"""compose.py — 02_TechCold template. Ráp skeleton + tokens + content → output index.html.

Cách dùng:
  python compose.py <content_dir>
  python compose.py example_ente_privacy/

Đọc:
  - ./skeleton.html              (HTML structure có placeholder {{ s1.eyebrow }} + {{ timeline_json }})
  - ./style-tokens.json          (CSS rules override)
  - <content_dir>/content.json   (text data + voice + timeline)

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
TOKENS   = TPL_DIR / "style-tokens.json"
CONTENT  = CONTENT_DIR / "content.json"
OUTPUT   = CONTENT_DIR / "index.html"

assert SKELETON.exists(), f"Missing: {SKELETON}"
assert CONTENT.exists(),  f"Missing: {CONTENT}"

skeleton = SKELETON.read_text(encoding="utf-8")
content  = json.loads(CONTENT.read_text(encoding="utf-8"))
tokens   = json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}

# Build flat lookup: scenes data + global fields
scenes_data = content.get("scenes", {})
global_data = {
    "topic":        content.get("topic", ""),
    "footer_brand": content.get("footer_brand", ""),
}

def resolve(path: str):
    """Resolve 's3.cards.0.heading' → traverse content."""
    # Try global first (no dot)
    if "." not in path and path in global_data:
        return global_data[path]
    # Try scenes
    cur = scenes_data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)] if part.isdigit() and int(part) < len(cur) else ""
        elif isinstance(cur, dict):
            cur = cur.get(part, "")
        else:
            return ""
    return str(cur) if cur is not None else ""

# Special placeholder: {{ timeline_json }} → JSON.stringify(content.timeline)
def replace_placeholder(match):
    path = match.group(1).strip()
    if path == "timeline_json":
        return json.dumps(content.get("timeline", {}), ensure_ascii=False)
    return resolve(path)

html = re.sub(r"\{\{\s*([\w\.]+)\s*\}\}", replace_placeholder, skeleton)

# Inject CSS tokens
rules = tokens.get("tokens", {}).get("rules", {})
if rules:
    overrides = [f"{sel} {{ " + "; ".join(f"{k}: {v}" for k, v in props.items()) + "; }" for sel, props in rules.items()]
    style_block = '<style id="_tokens_override">\n' + "\n".join(overrides) + "\n</style>"
    html = html.replace("</head>", style_block + "\n</head>", 1)

# Update audio data-duration
duration = content.get("voice", {}).get("duration")
d = None
if duration:
    d = int(round(float(duration) + 0.5))
    html = re.sub(r'(data-composition-id="root"[^>]*data-duration=")\d+(")', rf'\g<1>{d}\g<2>', html)
    html = re.sub(r'(id="narration"[^>]*data-duration=")\d+(")', rf'\g<1>{d}\g<2>', html)

OUTPUT.write_text(html, encoding="utf-8")
print(f"✓ Composed: {OUTPUT}")
print(f"  Skeleton: {len(skeleton)} chars → Output: {len(html)} chars")
print(f"  Voice duration: {duration}s → data-duration={d if d else 'unchanged'}")
print(f"  Tokens injected: {len(rules)} CSS rules")
