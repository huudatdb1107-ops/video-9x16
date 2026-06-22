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
def _find_rule(html, sel):
    """Find a top-level CSS rule body for selector `sel`. Returns (start, end, body) or None."""
    pat = rf'(?<![\w\.\-]){re.escape(sel)}\s*\{{'
    for m in re.finditer(pat, html):
        depth = 1
        i = m.end()
        while i < len(html) and depth > 0:
            c = html[i]
            if c == '{': depth += 1
            elif c == '}': depth -= 1
            i += 1
        if depth == 0:
            return (m.start(), i, html[m.end():i-1])
    return None

def set_css(html, sel, prop, value):
    r = _find_rule(html, sel)
    if not r: return html
    start, end, body = r
    prop_pat = rf'((?:^|[;\s])){re.escape(prop)}\s*:\s*[^;]+?\s*(;|$)'
    if re.search(prop_pat, body, re.MULTILINE):
        new_body = re.sub(prop_pat, rf'\1{prop}: {value}\2', body, count=1, flags=re.MULTILINE)
    else:
        new_body = body.rstrip() + f"\n  {prop}: {value};\n"
    return html[:start] + html[start:end].replace(body, new_body, 1) + html[end:]

def resolve(path: str, data: dict):
    """Resolve dotted path like 's3.cards.0.num' against data dict, supporting both flat and nested schemas."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return ""
        elif isinstance(cur, dict):
            # Fallback: if part is not in dict but 'fields' is a dict containing part
            if part not in cur and "fields" in cur and isinstance(cur["fields"], dict):
                cur = cur["fields"]
            cur = cur.get(part)
            if cur is None: return ""
        else:
            return ""
    if isinstance(cur, dict):
        if "value" in cur:
            return str(cur["value"])
        if "default" in cur:
            return str(cur["default"])
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
        return content.get("brand_watermark") or "PK NHI BOOM BOOM"
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
    # Loại bỏ khoảng trắng thừa trước dấu câu nếu có để dính liền
    inner_clean = re.sub(r'\s+([? !:;,])', r'\1', inner)
    words = inner_clean.strip().split()
    if len(words) < 2: return m.group(0)
    
    # Gộp các dấu câu đứng riêng biệt (nếu vẫn còn) vào từ trước nó
    new_words = []
    for w in words:
        if w in ["?", "!", ":", ";", ",", "."] and new_words:
            new_words[-1] = new_words[-1] + w
        else:
            new_words.append(w)
            
    wrapped = " ".join(f'<span class="word">{w}</span>' for w in new_words)
    return f'{open_tag}{wrapped}{close_tag}'

html = re.sub(
    r'(<(\w+)[^>]*\bdata-wrap-words="1"[^>]*>)([^<]+)(</\2>)',
    _wrap_words_re,
    html,
)

# ============ STEP 2: Apply custom styles from content.json ============
custom_styles = content.get("styles", {})
for sel, props in custom_styles.items():
    for prop, value in props.items():
        if value and value.strip():
            html = set_css(html, sel, prop, value.strip())

# ============ STEP 3: Update audio duration from voice ============
voice = content.get("voice", {})
duration = voice.get("duration")
if duration:
    # update data-duration on root + audio elements
    d = int(round(float(duration) + 0.5))  # round up
    html = re.sub(r'(data-composition-id="root"[^>]*data-duration=")\d+(")', rf'\g<1>{d}\g<2>', html)
    html = re.sub(r'(id="narration"[^>]*data-duration=")\d+(")', rf'\g<1>{d}\g<2>', html)

# Copy/Sync voice file to narration.wav for hyperframes rendering compatibility
voice_file = voice.get("file")
if voice_file:
    src_voice = CONTENT_DIR / voice_file
    dst_voice = CONTENT_DIR / "narration.wav"
    if src_voice.exists():
        import shutil
        try:
            if not dst_voice.exists() or dst_voice.stat().st_size != src_voice.stat().st_size:
                shutil.copy2(src_voice, dst_voice)
                print(f"  ✓ Synced voice file to narration.wav for rendering compatibility")
        except Exception as e:
            print(f"  ⚠ Failed to sync voice file: {e}")

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

# Base64 hóa font UTM-Cookies.ttf để đảm bảo render watermark chuẩn trong Puppeteer
try:
    import base64
    font_cookies_path = TPL_DIR.parent / "_fonts" / "UTM-Cookies.ttf"
    if font_cookies_path.exists():
        cookies_data = font_cookies_path.read_bytes()
        cookies_b64 = base64.b64encode(cookies_data).decode('utf-8')
        cookies_css = (
            f"@font-face {{\n"
            f"  font-family: \"UTM Cookies\";\n"
            f"  font-style: normal;\n"
            f"  font-weight: 400;\n"
            f"  src: url('data:font/ttf;charset=utf-8;base64,{cookies_b64}') format('truetype');\n"
            f"}}"
        )
        html = re.sub(
            r'@font-face\s*\{\s*font-family:\s*["\']UTM Cookies["\'];.*?src:\s*url\([^\)]+\)[^;]*;?\s*\}',
            cookies_css,
            html,
            flags=re.DOTALL | re.IGNORECASE
        )
        print("  ✓ Base64 encoded UTM Cookies font")
except Exception as e:
    print(f"  ⚠ UTM Cookies base64 encoding failed: {e}")

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
