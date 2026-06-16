"""match_template.py — chọn template phù hợp dựa trên topic + script keywords.

Heuristic:
  - Topic chứa "trẻ em", "con cái", "cha mẹ", "giáo dục", "sức khỏe", "tâm lý" → 01_Text (warm)
  - Topic chứa "tool", "app", "review", "tech", "privacy", "security", "AI", "platform" → 02_TechCold
  - Script có stat/% và "%" hoặc "GB" → 02_TechCold (có stat-block)
  - Mặc định fallback 01_Text

Usage:
  python match_template.py --topic "Ente Photos privacy" --script-file script.txt
"""
import sys, re, argparse, pathlib, json
sys.stdout.reconfigure(encoding="utf-8")

TEMPLATES_DIR = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD\TEST\_templates")

WARM_KEYWORDS = [
    "trẻ em", "con cái", "con bạn", "cha mẹ", "ba mẹ", "phụ huynh",
    "giáo dục", "sức khỏe", "tâm lý", "tự kỷ", "tăng động", "hiếu động",
    "yêu thương", "chăm sóc", "đồng hành", "lắng nghe", "kiên nhẫn",
    "câu chuyện", "thầy cô", "học sinh"
]
COLD_KEYWORDS = [
    "tool", "app", "review", "tech", "công nghệ", "phần mềm",
    "privacy", "security", "bảo mật", "mã hóa", "encrypt",
    "AI", "machine learning", "monorepo", "platform", "self-host",
    "GB", "TB", "MB", "%", "cloud", "server", "API", "framework"
]

def score(text: str, keywords: list) -> int:
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)

def match(topic: str, script: str = "") -> str:
    """Return template name. Default 01_Text if tie."""
    combined = f"{topic} {script}"
    warm_score = score(combined, WARM_KEYWORDS)
    cold_score = score(combined, COLD_KEYWORDS)
    print(f"  Warm score: {warm_score}, Cold score: {cold_score}")
    return "02_TechCold" if cold_score > warm_score else "01_Text"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--script-file", help="Optional script text file")
    args = ap.parse_args()
    script = pathlib.Path(args.script_file).read_text(encoding="utf-8") if args.script_file else ""
    chosen = match(args.topic, script)
    print(f"✓ Matched template: {chosen}")
    meta = TEMPLATES_DIR / chosen / "meta.json"
    if meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        print(f"  Title: {m.get('title', '?')}")
        print(f"  When to use: {', '.join(m.get('when_to_use', [])[:2])}")
    print(chosen)  # last line = bare template name for piping
