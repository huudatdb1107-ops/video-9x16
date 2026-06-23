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
    if path == "tagline_footer":
        return (content.get("tagline_footer")
                or content.get("scenes", {}).get("s1", {}).get("byline")
                or "HÀNH TRÌNH KIÊN TRÌ CÙNG CON VƯỢT KHÓ")
    return resolve(path, scenes_data)

html = re.sub(r"\{\{\s*([\w\.]+)\s*\}\}", replace_placeholder, skeleton)
# Enforce BOOM BOOM case-insensitive replacement
html = re.sub(r'\bbom[- ]?bom\b', 'BOOM BOOM', html, flags=re.IGNORECASE)

# ============ P1.3: Word-stagger wrap ============
# Element có data-wrap-words="1" → split inner text thành span.word stagger reveal.
# KHÔNG đụng vào nested HTML (em.hl-orange...) — chỉ wrap plain text node ngoài cùng.
def _wrap_words_re(m):
    open_tag = m.group(1)
    inner = m.group(3)
    close_tag = m.group(4)
    if "<" in inner: return m.group(0)
    words = inner.strip().split()
    if len(words) < 2: return m.group(0)
    wrapped = " ".join(f'<span class="word">{w}</span>' for w in words)
    return f'{open_tag}{wrapped}{close_tag}'

html = re.sub(
    r'(<(\w+)[^>]*\bdata-wrap-words="1"[^>]*>)([^<]+)(</\2>)',
    _wrap_words_re,
    html,
)

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

# ============ STEP 2.5: AI tùy biến font combo (random theo topic) ============
try:
    import font_combos
    topic_seed = content.get("topic", "") or str(CONTENT_DIR.name)
    combo_name, combo = font_combos.pick_combo(topic_seed)
    fonts_dir = TPL_DIR.parent / "_fonts"
    font_css = font_combos.build_font_css(combo, fonts_dir, workspace_dir=CONTENT_DIR)
    if font_css:
        font_block = f'<style id="_font_combo" data-combo="{combo_name}">\n{font_css}\n</style>'
        html = html.replace("</head>", font_block + "\n</head>", 1)
        print(f"  ✓ Font combo: {combo_name} — {combo['label']}")
except Exception as e:
    print(f"  ⚠ Font combo skip: {e}")

OUTPUT.write_text(html, encoding="utf-8")

# Copy fonts vào workspace (skeleton dùng relative src './fontname.ttf')
for font_name in ["UTM-Cookies.ttf", "Inter-Regular.ttf", "Inter-Bold.ttf", "Inter-ExtraBold.ttf", "Inter-Black.ttf", "Inter-Italic-Variable.ttf"]:
    font_src = TPL_DIR.parent / "_fonts" / font_name
    font_dst = CONTENT_DIR / font_name
    if font_src.exists() and not font_dst.exists():
        font_dst.write_bytes(font_src.read_bytes())
        print(f"  ✓ Copied font: {font_name}")

print(f"✓ Composed: {OUTPUT}")
print(f"  Skeleton: {len(skeleton)} chars → Output: {len(html)} chars")
print(f"  Voice duration: {duration}s → data-duration={d if duration else 'unchanged'}")
