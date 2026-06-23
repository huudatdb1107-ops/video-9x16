"""compose.py — ráp skeleton + content + tokens → output index.html render-ready.

Cách dùng:
  python compose.py <content_dir>
  python compose.py example_tre_em_tang_dong/

Đọc:
  - ./skeleton.html              (HTML structure có placeholder {{ s1.eyebrow }})
  - ./style-tokens.json          (CSS rules override)
  - <content_dir>/content.json   (text data)

Output:
  - <content_dir>/index.html     (render-ready, hyperframes render được)
"""
import json, re, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")

if len(sys.argv) < 2:
    print("Usage: python compose.py <content_dir>")
    sys.exit(1)

TPL_DIR = pathlib.Path(__file__).parent
CONTENT_DIR = (TPL_DIR / sys.argv[1]).resolve()
SKELETON = TPL_DIR / "skeleton.html"
TOKENS = TPL_DIR / "style-tokens.json"
CONTENT = CONTENT_DIR / "content.json"
OUTPUT = CONTENT_DIR / "index.html"

assert SKELETON.exists(), f"Missing skeleton: {SKELETON}"
assert CONTENT.exists(),  f"Missing content: {CONTENT}"

skeleton = SKELETON.read_text(encoding="utf-8")
content  = json.loads(CONTENT.read_text(encoding="utf-8"))
tokens   = json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}

# ============ STEP 1: Replace {{ path.to.value }} placeholders ============
def resolve(path: str, data: dict):
    """Resolve dotted path like 's3.cards.0.num' against data dict."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
            if cur is None: return ""
        else:
            return ""
    return str(cur)

# Scenes content is nested under "scenes" key in content.json
scenes_data = content.get("scenes", {})
def replace_placeholder(match):
    path = match.group(1).strip()
    if path == "timeline_json":
        tl = dict(content.get("timeline", {}))
        for sid, scene_data in content.get("scenes", {}).items():
            if isinstance(scene_data, dict) and "element_times" in scene_data:
                if sid not in tl: tl[sid] = {}
                tl[sid]["element_times"] = scene_data["element_times"]
        return json.dumps(tl, ensure_ascii=False)
    if path == "voice_duration":
        return str(content.get("voice", {}).get("duration", 90))
    if path == "brand_watermark":
        return content.get("brand_watermark", "")
    return resolve(path, scenes_data)

html = re.sub(r"\{\{\s*([\w\.]+)\s*\}\}", replace_placeholder, skeleton)

# ============ STEP 2: CSS tokens — KHÔNG inject override ============
# 1 source of truth = skeleton.html. Editor Save ghi thẳng vào skeleton →
# MP4 render khớp 100% với preview. style-tokens.json giữ làm initial defaults
# khi tạo template mới, KHÔNG dùng để override skeleton lúc compose.
_ = tokens  # giữ để không phá biến nếu dùng ở chỗ khác

# ============ STEP 3: Update audio duration from voice ============
voice = content.get("voice", {})
duration = voice.get("duration")
if duration:
    # update data-duration on root + audio elements
    d = int(round(float(duration) + 0.5))  # round up
    html = re.sub(r'(data-composition-id="root"[^>]*data-duration=")\d+(")', rf'\g<1>{d}\g<2>', html)
    html = re.sub(r'(id="narration"[^>]*data-duration=")\d+(")', rf'\g<1>{d}\g<2>', html)

OUTPUT.write_text(html, encoding="utf-8")
print(f"✓ Composed: {OUTPUT}")
print(f"  Skeleton: {len(skeleton)} chars → Output: {len(html)} chars")
print(f"  Voice duration: {duration}s → data-duration={d if duration else 'unchanged'}")
