"""Editor server — Boss edit text trong index.html qua web UI + 1 click Gen Video.

Chạy: python editor_server.py → mở browser http://localhost:5050/
Workflow:
  1. LEFT panel: gallery grid 6 scene thu nhỏ (live update khi gõ)
  2. RIGHT panel: form textarea cho từng text element
  3. Boss gõ → preview cập nhật ngay
  4. Save → ghi index.html
  5. Gen Video → render mp4 → ra `out_latest.mp4`
"""
import json, re, subprocess, threading, sys, urllib.parse, time, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Logging to file để debug crash
import logging
_log_path = Path(__file__).parent / "_editor_debug.log"
logging.basicConfig(
    filename=str(_log_path), filemode="a",
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO, encoding="utf-8"
)
logging.info("=" * 60)
logging.info(f"Editor server starting, PID={os.getpid()}")

# Catch unhandled exceptions
def _excepthook(exc_type, exc_value, exc_tb):
    logging.error("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)
sys.excepthook = _excepthook
import threading as _th
_th.excepthook = lambda args: logging.error(f"THREAD EXCEPTION: {args.exc_type.__name__}: {args.exc_value}", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

def slugify_vietnamese(text: str, max_len: int = 60) -> str:
    import re
    patterns = {
        '[àáảãạăằắẳẵặâầấẩẫậ]': 'a',
        '[ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬ]': 'A',
        '[èéẻẽẹêềếểễệ]': 'e',
        '[ÈÉẺẼẸÊỀẾỂỄỆ]': 'E',
        '[ìíỉĩị]': 'i',
        '[ÌÍỈĨỊ]': 'I',
        '[òóỏõọôồốổỗộơờớởỡợ]': 'o',
        '[ÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ]': 'O',
        '[ùúủũụưừứửữự]': 'u',
        '[ÙÚỦŨỤƯỪỨỬỮỰ]': 'U',
        '[ỳýỷỹỵ]': 'y',
        '[ỲÝỶỸỴ]': 'Y',
        '[đ]': 'd',
        '[Đ]': 'D'
    }
    s = text
    for pattern, repl in patterns.items():
        s = re.sub(pattern, repl, s)
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9\s_]', '', s)
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'_+', '_', s)
    
    if len(s) > max_len:
        truncated = s[:max_len]
        last_underscore = truncated.rfind('_')
        if last_underscore > 0:
            s = truncated[:last_underscore]
        else:
            s = truncated
    return s

def clean_boom_boom(text: str) -> str:
    if not text: return text
    return re.sub(r'\bbom[- ]?bom\b', 'BOOM BOOM', text, flags=re.IGNORECASE)

def safe_write_file(file_path: Path, content, is_binary: bool = False):
    """Ghi đè an toàn xuống đĩa bằng file tạm và os.fsync."""
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        if is_binary:
            with open(tmp_path, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        if file_path.exists():
            os.remove(file_path)
        os.rename(tmp_path, file_path)
        print(f"[SYSTEM] Đã ghi đè an toàn file: {file_path.name}")
    except Exception as e:
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e

# Watchdog: nếu browser ngừng gửi heartbeat → server tự shutdown
# Timeout tăng lên 5 phút để chịu được render (CPU starvation + tab background throttle)
LAST_HEARTBEAT = time.time()
HEARTBEAT_TIMEOUT = 300  # 5 phút — đủ cho render xong
RENDER_IS_RUNNING = lambda: False  # set bởi Handler khi /render đang chạy

def watchdog():
    while True:
        time.sleep(10)
        if RENDER_IS_RUNNING():
            continue
        delta = time.time() - LAST_HEARTBEAT
        if delta > HEARTBEAT_TIMEOUT:
            logging.warning(f"WATCHDOG SHUTDOWN: no heartbeat for {delta:.1f}s (timeout={HEARTBEAT_TIMEOUT}s)")
            os._exit(0)

import argparse as _ap_mod
_ap = _ap_mod.ArgumentParser()
_ap.add_argument("--workspace", default=None, help="Folder chứa index.html + narration.wav")
_args, _ = _ap.parse_known_args()
WORK = Path(_args.workspace).resolve() if _args.workspace else Path.cwd()
INDEX = WORK / "index.html"
PORT = 5050
print(f"[editor_server] WORK = {WORK}")

# Fields có id="..." trong index.html — Boss edit innerHTML, có thể chứa <em>, <br>
FIELDS = [
    {"scene": 1, "id": "s1-tag",     "label": "Tag (pill)",      "rows": 1},
    {"scene": 1, "id": "s1-title",   "label": "Title (HTML)",    "rows": 2},
    {"scene": 1, "id": "s1-byline",  "label": "Byline",          "rows": 1},
    {"scene": 2, "id": "s2-num",     "label": "Big text",        "rows": 1},
    {"scene": 2, "id": "s2-label",   "label": "Label",           "rows": 2},
    {"scene": 2, "id": "s2-note",    "label": "Note",            "rows": 2},
    {"scene": 3, "id": "s3-heading", "label": "Heading",         "rows": 1},
    {"scene": 3, "id": "s3-card-1",  "label": "Card 01 text",    "rows": 2},
    {"scene": 3, "id": "s3-card-2",  "label": "Card 02 text",    "rows": 2},
    {"scene": 3, "id": "s3-card-3",  "label": "Card 03 text",    "rows": 2},
    {"scene": 4, "id": "s4-quote",   "label": "Quote (HTML)",    "rows": 3},
    {"scene": 5, "id": "s5-heading", "label": "Heading",         "rows": 1},
    {"scene": 5, "id": "s5-text-1",  "label": "Item 1 text",     "rows": 2},
    {"scene": 5, "id": "s5-text-2",  "label": "Item 2 text",     "rows": 2},
    {"scene": 5, "id": "s5-text-3",  "label": "Item 3 text",     "rows": 2},
    {"scene": 6, "id": "s6-title",   "label": "Title (HTML)",    "rows": 2},
    {"scene": 6, "id": "s6-sub",     "label": "Sub",             "rows": 2},
    {"scene": 6, "id": "s6-hashtag", "label": "Hashtag",         "rows": 1},
]

def get_inner(html, elem_id):
    # Capture tag name from opening, match closing tag of SAME name → tránh dừng ở <em> lồng nhau
    pat = rf'<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)\b[^>]*\bid="{re.escape(elem_id)}"[^>]*>(?P<inner>.*?)</(?P=tag)>'
    m = re.search(pat, html, re.DOTALL)
    return m.group("inner").strip() if m else ""

def _find_voice_wav(work_dir: Path):
    """Tìm file voice .wav trong workspace. Ưu tiên content.json voice.file → fallback latest .wav theo mtime."""
    co_file = work_dir / "content.json"
    if co_file.exists():
        try:
            co = json.loads(co_file.read_text(encoding="utf-8"))
            fn = co.get("voice", {}).get("file")
            if fn:
                p = work_dir / fn
                if p.exists(): return p
        except Exception: pass
    wavs = sorted(work_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    return wavs[0] if wavs else None


def set_inner(html, elem_id, new_inner):
    # Cleanup contenteditable junk: Chrome inject <div> khi Enter + &nbsp; + <br> thừa trong <em>
    # Replace <div>X</div> → <br>X (flatten block to inline break)
    new_inner = re.sub(r'<div\b[^>]*>', '<br>', new_inner, flags=re.IGNORECASE)
    new_inner = re.sub(r'</div>', '', new_inner, flags=re.IGNORECASE)
    # &nbsp; → regular space
    new_inner = new_inner.replace('&nbsp;', ' ').replace('\xa0', ' ')
    # Bỏ <br> ngay sau <em ...> hoặc ngay trước </em>
    new_inner = re.sub(r'(<em\b[^>]*>)(?:\s*<br\s*/?>)+', r'\1', new_inner, flags=re.IGNORECASE)
    new_inner = re.sub(r'(?:\s*<br\s*/?>)+\s*(</em>)', r'\1', new_inner, flags=re.IGNORECASE)
    # GIỮ NGUYÊN multiple <br> (Boss có thể chủ ý enter nhiều lần để tạo khoảng cách)
    # CHỈ trim whitespace 2 đầu, KHÔNG strip <br>
    new_inner = new_inner.strip()
    pat = rf'(<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)\b[^>]*\bid="{re.escape(elem_id)}"[^>]*>)(.*?)(</(?P=tag)>)'
    return re.sub(pat, lambda m: m.group(1) + new_inner + m.group(4), html, count=1, flags=re.DOTALL)

# ============ CSS property read/write (text-based, đủ cho selectors phẳng) ============
_BASE_PROPS = ["font-size","line-height","letter-spacing","color"]
STYLE_FIELDS = [
    {"scene": 1, "sel": ".s1-tag",      "label": "Tag pill",   "props": _BASE_PROPS},
    {"scene": 1, "sel": ".s1-title",    "label": "Title",      "props": _BASE_PROPS},
    {"scene": 1, "sel": ".s1-byline",   "label": "Byline",     "props": _BASE_PROPS},
    {"scene": 2, "sel": ".s2-big",      "label": "Big text",   "props": _BASE_PROPS},
    {"scene": 2, "sel": ".s2-label",    "label": "Label",      "props": _BASE_PROPS},
    {"scene": 2, "sel": ".s2-note",     "label": "Note",       "props": _BASE_PROPS},
    {"scene": 3, "sel": ".s3-heading",  "label": "Heading",    "props": _BASE_PROPS},
    {"scene": 3, "sel": ".s3-card-text","label": "Card text",  "props": _BASE_PROPS},
    {"scene": 3, "sel": ".s3-card-num", "label": "Card #",     "props": _BASE_PROPS},
    {"scene": 4, "sel": ".s4-quote",    "label": "Quote",      "props": _BASE_PROPS},
    {"scene": 5, "sel": ".s5-heading",  "label": "Heading",    "props": _BASE_PROPS},
    {"scene": 5, "sel": ".s5-text",     "label": "Item text",  "props": _BASE_PROPS},
    {"scene": 6, "sel": ".s6-title",    "label": "Title",      "props": _BASE_PROPS},
    {"scene": 6, "sel": ".s6-sub",      "label": "Sub",        "props": _BASE_PROPS},
    {"scene": 6, "sel": ".s6-hashtag",  "label": "Hashtag",    "props": _BASE_PROPS},
]

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

def get_css(html, sel, prop):
    r = _find_rule(html, sel)
    if not r: return ""
    body = r[2]
    m = re.search(rf'(?:^|[;\s]){re.escape(prop)}\s*:\s*([^;]+?)\s*(?:;|$)', body, re.MULTILINE)
    return m.group(1).strip() if m else ""

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


EDITOR_HTML = r"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><title>Editor — tre_em_hieu_dong_vs_tang_dong</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; }
body { margin: 0; background: #0d0d0d; color: #eee; font-family: "Inter", sans-serif; height: 100vh; overflow: hidden; }
#app { display: grid; grid-template-rows: 56px auto 1fr; height: 100vh; }
#topbar { display: flex; align-items: center; padding: 0 20px; background: #0d0d0d; border-bottom: 1px solid #443311; gap: 12px; }
#topbar h2 { margin: 0; font-size: 16px; color: #D4AF37; min-width: 0; font-weight: 800; letter-spacing: 1px; display: flex; align-items: flex-start; gap: 6px; }
#topbar button, #topbar #status { flex-shrink: 0; }
#main { padding: 18px; overflow-y: auto; background: #0d0d0d; }
#stylebar { background: #050505; border-bottom: 1px solid #443311; padding: 12px 20px; display: none; }
#stylebar.show { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
#stylebar .target { font-size: 12px; color: #D4AF37; font-weight: 700; min-width: 90px; }
#stylebar .grp { display: flex; gap: 4px; align-items: center; background: #050505; padding: 3px 8px; border-radius: 4px; border: 1px solid #443311; }
#stylebar .grp label { font-size: 9px; color: #D4AF37; text-transform: uppercase; }
#stylebar input { background: transparent; color: #fff; border: none; padding: 3px 0 3px 2px; margin: 0; font-family: monospace; font-size: 12px; text-align: left; }
#stylebar .bump { cursor: pointer; user-select: none; touch-action: manipulation; }
#stylebar .bump:active { transform: scale(0.95); }
/* Width sẽ được JS autosizeInput set theo content thật — không hardcode */
#stylebar input::-webkit-outer-spin-button, #stylebar input::-webkit-inner-spin-button { display: none; }
#stylebar input:focus { outline: none; }
.swatch { width: 18px; height: 18px; border-radius: 3px; border: 1.5px solid #443311; cursor: pointer; padding: 0; transition: transform 0.1s, border-color 0.1s; }
.swatch:hover { transform: scale(1.2); border-color: #D4AF37; }
.bump { width: 20px; height: 20px; background: transparent; color: #D4AF37; border: 1px solid #443311; border-radius: 3px; cursor: pointer; font-size: 13px; font-weight: 700; line-height: 1; padding: 0; margin-left: 4px; }
.bump:hover { background-color: rgba(212, 175, 55, 0.5); color: #000; border-color: #FFD700; }
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; align-items: center; }
.toolbar h2 { margin: 0; font-size: 15px; color: #D4AF37; flex: 1; }
[contenteditable="true"] { cursor: text; transition: outline 0.1s; }
[contenteditable="true"]:hover { outline: 2px dashed rgba(212, 175, 55, 0.6); outline-offset: 4px; }
[contenteditable="true"]:focus { outline: 2px solid #D4AF37; outline-offset: 4px; }

/* Golden Obsidian Buttons */
button { background: transparent; color: #D4AF37; border: 1px solid #D4AF37; padding: 9px 10px; border-radius: 6px; font-weight: 700; cursor: pointer; font-size: 13px; letter-spacing: 0.5px; transition: all 0.2s ease-in-out; }
button:hover { background-color: rgba(212, 175, 55, 0.5); color: #000; border: 1px solid #FFD700; }
button.secondary { background: transparent; color: #D4AF37; border: 1px solid #443311; }
button.secondary:hover { background-color: rgba(212, 175, 55, 0.2); color: #fff; border-color: #D4AF37; }
button:disabled { opacity: 0.5; cursor: wait; }
button.btn-gen, html body #app #topbar button.btn-gen { background: #3399ff !important; color: #000000 !important; border: 1px solid #3399ff !important; }
button.btn-gen:hover, html body #app #topbar button.btn-gen:hover { background: #0080ff !important; border-color: #0080ff !important; color: #000000 !important; box-shadow: 0 0 15px rgba(0, 128, 255, 0.4) !important; }
button.btn-save, html body #app #topbar button.btn-save { background: #c8b3f7 !important; color: #000000 !important; border: 1px solid #9d85d9 !important; }
button.btn-save:hover, html body #app #topbar button.btn-save:hover { background: #9d85d9 !important; color: #000000 !important; border-color: #c8b3f7 !important; box-shadow: 0 0 15px rgba(157, 133, 217, 0.4) !important; }
button.btn-template, html body #app #topbar button.btn-template { background: #b37d4e !important; color: #000000 !important; border: 1px solid #b37d4e !important; }
button.btn-template:hover, html body #app #topbar button.btn-template:hover { background: #966436 !important; color: #000000 !important; border-color: #dcb38a !important; box-shadow: 0 0 15px rgba(179, 125, 78, 0.4) !important; }

/* Status */
#status { font-size: 12px; color: #D4AF37; padding: 6px 10px; background: #050505; border-radius: 4px; min-width: 150px; text-align: center; border: 1px solid #443311; margin-left: auto; }
#status.ok { color: #00ffcc; }
#status.err { color: #ff0000; }
#status.busy { color: #ffcc00; }

.gallery { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.thumb { aspect-ratio: 9 / 16; position: relative; overflow: hidden; border-radius: 10px; border: 2px solid #2a2a30; background: #0a1820; transition: border-color 0.2s; }
.thumb iframe { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; transform-origin: top left; border: none; pointer-events: auto; }
.thumb.active { border-color: #ff7a2a; }
.thumb-label { position: absolute; top: 6px; left: 6px; z-index: 100; background: rgba(255,122,42,0.95); color: #000; font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 3px; letter-spacing: 0.5px; }
.thumb-inner { width: 1080px; height: 1920px; position: absolute; top: 0; left: 0; transform-origin: top left; }
@font-face { font-family: 'UTM Cookies'; src: url('/fonts/UTM-Cookies.ttf') format('truetype'); font-display: swap; }
.thumb-inner .brand-watermark { position: absolute; top: 50px; right: 50px; z-index: 100; font-family: 'UTM Cookies', cursive; font-size: 60px; font-weight: 400; letter-spacing: 1px; color: #ff7a2a; padding: 14px 30px; background: rgba(255,122,42,0.12); border: 2px solid rgba(255,122,42,0.65); border-radius: 999px; text-shadow: 0 0 18px rgba(255,122,42,0.55), 0 2px 0 rgba(0,0,0,0.25); text-transform: uppercase; pointer-events: none; }
.thumb-inner .tagline-footer { position: absolute; bottom: 80px; left: 50%; transform: translateX(-50%); z-index: 100; font-family: 'Inter', sans-serif; font-size: 26px; font-weight: 600; letter-spacing: 4px; color: #6db8b8; text-transform: uppercase; white-space: nowrap; pointer-events: none; text-shadow: 0 0 14px rgba(77,204,204,0.4); }
.thumb-inner::before { content: ""; position: absolute; inset: 0; background: radial-gradient(circle at 85% 75%, rgba(58,188,188,0.45) 0%, transparent 50%), radial-gradient(circle at 12% 18%, rgba(58,188,188,0.28) 0%, transparent 42%), radial-gradient(circle at 92% 8%, rgba(255,138,58,0.22) 0%, transparent 30%), linear-gradient(180deg, #0a1a26 0%, #0a1820 50%, #061018 100%); z-index: 0; }

/* Scene styles — copy từ index.html final state */
.scene { position: absolute; inset: 0; display: flex; flex-direction: column; padding: 140px 90px; z-index: 2; }
.scene-3, .scene-5 { padding: 140px 50px; }
.scene-1, .scene-2, .scene-3, .scene-4, .scene-5, .scene-6 { justify-content: center; align-items: center; }
.scene-1 { justify-content: space-between; }
.scene-3, .scene-5 { justify-content: center; }
.thumb-inner em { font-style: normal; font-weight: 700; color: #4dcccc !important; text-shadow: 0 0 20px rgba(77,204,204,0.6) !important; }
.thumb-inner em.hl-teal { color: #4dcccc !important; font-style: normal; font-weight: 700; text-shadow: 0 0 20px rgba(77,204,204,0.6) !important; }
.thumb-inner em.hl-orange { color: #ff7a2a !important; font-style: normal; font-weight: 700; text-shadow: 0 0 20px rgba(255,122,42,0.6) !important; }
.thumb-inner .s6-title em { color: #ff7a2a !important; font-style: normal; font-weight: 900; text-shadow: 0 0 25px rgba(255,122,42,0.75) !important; }
.s1-top { display: flex; flex-direction: column; align-items: center; gap: 24px; padding-top: 80px; }
.s1-tag { font-size: 24px; font-weight: 600; color: #ff7a2a; letter-spacing: 3px; text-transform: uppercase; padding: 10px 28px; border: 1.5px solid rgba(255,122,42,0.45); border-radius: 999px; background: rgba(10,24,32,0.4); }
.s1-tag::before { content: "● "; color: #ff7a2a; margin-right: 6px; }
.s1-title { font-size: 130px; font-weight: 900; color: #ffffff; line-height: 1.7; text-align: center; max-width: 960px; margin: 0; letter-spacing: -1px; text-shadow: 0 0 30px rgba(255,122,42,0.6); text-transform: uppercase; }
.s1-title em { font-style: normal; color: #ff7a2a; font-weight: 900; text-shadow: 0 0 25px rgba(255,122,42,0.7); }
.s1-bottom { display: flex; flex-direction: column; align-items: center; gap: 32px; padding-bottom: 60px; }
.s1-dot { width: 80px; height: 3px; background: linear-gradient(90deg, transparent, #ff7a2a, transparent); border-radius: 2px; }
.s1-byline { font-size: 22px; color: #6db8b8; letter-spacing: 2px; text-transform: uppercase; font-weight: 500; }
.s2-stat-wrap { display: flex; flex-direction: column; align-items: center; gap: 36px; }
.s2-big { font-size: 360px; font-weight: 900; color: #ff7a2a; line-height: 0.9; letter-spacing: -8px; text-shadow: 0 0 50px rgba(255,122,42,0.55); }
.s2-label { font-size: 58px; color: #ffffff; text-align: center; font-weight: 700; line-height: 2; max-width: 880px; }
.s2-note { font-size: 30px; color: #a0c4c4; text-align: center; max-width: 760px; line-height: 1.5; margin-top: 24px; }
.s3-heading { font-size: 72px; font-weight: 800; color: #ff7a2a; text-align: center; margin: 0 0 60px; max-width: 980px; line-height: 1.50; letter-spacing: -1px; }
.s3-cards { display: flex; flex-direction: column; gap: 48px; width: 100%; max-width: 900px; }
.s3-card { background: rgba(10,24,32,0.55); border: 1.5px solid rgba(58,188,188,0.35); border-radius: 20px; padding: 36px 44px; display: flex; align-items: center; gap: 56px; box-shadow: 0 0 32px rgba(77,204,204,0.25), inset 0 0 24px rgba(58,188,188,0.08); }
.s3-card-num { font-size: 80px; font-weight: 900; color: #ff7a2a; min-width: 90px; line-height: 1; text-shadow: 0 0 20px rgba(255,122,42,0.5); }
.s3-card-text { font-size: 40px; color: #ffffff; font-weight: 600; line-height: 1.5; }
.s4-quote-mark { font-size: 180px; color: #ff7a2a; line-height: 0.5; margin-bottom: 80px; text-shadow: 0 0 40px rgba(255,122,42,0.55); }
.s4-quote { font-size: 64px; font-weight: 700; color: #ffebd2; text-align: center; line-height: 2; max-width: 920px; letter-spacing: -0.3px; }
.s4-quote em { color: #4dcccc; font-weight: 800; font-style: normal; text-shadow: 0 0 24px rgba(77,204,204,0.45); }
.s5-heading { font-size: 68px; font-weight: 800; color: #ffa726; text-align: center; margin: 0 0 60px; max-width: 980px; line-height: 1.50; letter-spacing: -1px;}
.s5-list { display: flex; flex-direction: column; gap: 48px; width: 100%; max-width: 880px; }
.s5-item { display: flex; align-items: center; gap: 28px; background: rgba(10,24,32,0.55); border: 1.5px solid rgba(58,188,188,0.35); border-radius: 16px; padding: 32px 40px; box-shadow: 0 0 28px rgba(77,204,204,0.25); }
.s5-dot { width: 16px; height: 16px; border-radius: 50%; background: #4dcccc; flex-shrink: 0; box-shadow: 0 0 20px rgba(77,204,204,0.7); }
.s5-text { font-size: 40px; color: #ffffff; font-weight: 600; line-height: 1.45; }
.s6-title { font-size: 130px; font-weight: 900; color: #4dcccc; text-align: center; line-height: 1.65; max-width: 940px; margin: 0 0 40px 0; letter-spacing: -1px; text-shadow: 0 0 35px rgba(255,122,42,0.6); text-transform: uppercase; }
.s6-title em { font-style: normal; color: #ff7a2a; font-weight: 900; text-shadow: 0 0 25px rgba(255,122,42,0.75); }
.s6-sub { font-size: 36px; color: #cfd9d9; text-align: center; max-width: 820px; line-height: 1.5; }
.s6-hashtag { font-size: 34px; color: #4dcccc; letter-spacing: 3px; font-weight: 700; margin-top: 40px; }

.scene-section { margin-bottom: 22px; padding-bottom: 14px; border-bottom: 1px solid #2a2a30; }
.scene-section h3 { margin: 0 0 10px; color: #ff7a2a; font-size: 14px; letter-spacing: 1px; }
.field { margin-bottom: 10px; }
.field label { display: block; font-size: 11px; color: #888; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
.field textarea { width: 100%; background: #0a0a0a; color: #fff; border: 1px solid #2a2a30; border-radius: 4px; padding: 8px 10px; font-family: "Inter", monospace; font-size: 13px; resize: vertical; }
.field textarea:focus { outline: none; border-color: #ff7a2a; }
.hint { font-size: 10px; color: #555; margin-top: 4px; }
.style-panel { background: #0d0d0f; border: 1px solid #1f1f24; border-radius: 4px; padding: 8px 10px; margin-bottom: 10px; }
.style-panel summary { font-size: 11px; color: #4dcccc; cursor: pointer; padding: 2px 0; letter-spacing: 0.5px; text-transform: uppercase; outline: none; }
.style-row { display: grid; grid-template-columns: 110px 1fr; gap: 6px; margin-top: 6px; align-items: center; }
.style-row label { font-size: 10px; color: #888; text-transform: uppercase; }
.style-row input { width: 100%; background: #0a0a0a; color: #fff; border: 1px solid #2a2a30; border-radius: 3px; padding: 4px 8px; font-family: monospace; font-size: 12px; }
.style-row input:focus { outline: none; border-color: #4dcccc; }
#renderLog { background: #0a0a0a; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 11px; white-space: pre-wrap; color: #aaa; max-height: 200px; overflow-y: auto; margin-top: 12px; }
</style>
</head><body>
<div id="app">
  <div id="topbar">
    <h2 id="openFolderBtn" style="cursor:pointer; user-select:none;" title="Mở thư mục workspace" onclick="openFolder()">📁 FOLDER</h2>
    <button class="secondary" id="voiceBtn" onclick="pickVoice()" title="Click để chọn file voice (.wav)">🎤 Voice: ...</button>
    <input type="file" id="voiceFile" accept="audio/wav,.wav" style="display:none">
    <div id="status">Loading…</div>
    <button class="secondary" onclick="reload()">Reload</button>
    <button class="btn-save" onclick="save()">💾 SAVE</button>
    <button class="btn-template" onclick="showTemplateModal()">💾 TEMPLATE</button>
    <button class="btn-gen" onclick="render()">🎬 GEN VIDEO</button>
  </div>
  <div id="stylebar">
    <div class="target" id="styleTarget">Click vào chữ để chỉnh font/cỡ/giãn dòng</div>
    <div class="grp"><label>font</label><input type="text" id="styFs" placeholder="130px"><button type="button" class="bump" data-target="styFs" data-delta="-2">−</button><button type="button" class="bump" data-target="styFs" data-delta="2">+</button></div>
    <div class="grp"><label>line</label><input type="text" id="styLh" placeholder="1.5"><button type="button" class="bump" data-target="styLh" data-delta="-0.05">−</button><button type="button" class="bump" data-target="styLh" data-delta="0.05">+</button></div>
    <div class="grp"><label>spacing</label><input type="text" id="styLs" placeholder="-1px"><button type="button" class="bump" data-target="styLs" data-delta="-1">−</button><button type="button" class="bump" data-target="styLs" data-delta="1">+</button></div>
    <div class="grp" style="border-color:#ff7a2a"><label style="color:#ff7a2a">group-gap</label><input type="text" id="styGap" placeholder="48px" style="width:60px"><button type="button" class="bump" data-target="styGap" data-delta="-4">−</button><button type="button" class="bump" data-target="styGap" data-delta="4">+</button></div>
    <div class="grp" style="padding: 2px 6px; gap: 4px;">
      <label>color</label>
      <input type="color" id="styColorPick" style="width: 28px; height: 24px; border: none; background: transparent; cursor: pointer; padding: 0;">
      <input type="text" id="styColor" placeholder="#ffffff" style="width: 70px;">
      <span style="color:#444;margin:0 2px">|</span>
      <button type="button" data-color="#ffffff" class="swatch" style="background:#ffffff"></button>
      <button type="button" data-color="#000000" class="swatch" style="background:#000000"></button>
      <button type="button" data-color="#ff7a2a" class="swatch" style="background:#ff7a2a"></button>
      <button type="button" data-color="#ffa726" class="swatch" style="background:#ffa726"></button>
      <button type="button" data-color="#ffd166" class="swatch" style="background:#ffd166"></button>
      <button type="button" data-color="#ff5544" class="swatch" style="background:#ff5544"></button>
      <button type="button" data-color="#4dcccc" class="swatch" style="background:#4dcccc"></button>
      <button type="button" data-color="#7adcdc" class="swatch" style="background:#7adcdc"></button>
      <button type="button" data-color="#a0c4c4" class="swatch" style="background:#a0c4c4"></button>
      <button type="button" data-color="#cfd9d9" class="swatch" style="background:#cfd9d9"></button>
    </div>
  </div>
  <div id="main">
    <div class="gallery" id="gallery"></div>
    <div id="renderLog"></div>
  </div>
</div>

<script>
// Templates với contenteditable + data-bind + data-sel (selector cho style bar)
const ED = (id, sel) => `contenteditable="true" data-bind="${id}" data-sel="${sel}"`;
const SCENE_TEMPLATES = {
  1: (d) => `<div class="scene scene-1">
    <div class="s1-top"><div class="s1-tag" ${ED('s1-tag','.s1-tag')}>${d['s1-tag']||''}</div></div>
    <h1 class="s1-title" ${ED('s1-title','.s1-title')}>${d['s1-title']||''}</h1>
    <div class="s1-bottom"><div class="s1-dot"></div><div class="s1-byline" ${ED('s1-byline','.s1-byline')}>${d['s1-byline']||''}</div></div>
  </div>`,
  2: (d) => `<div class="scene scene-2"><div class="s2-stat-wrap">
    <div class="s2-big" ${ED('s2-num','.s2-big')}>${d['s2-num']||''}</div>
    <div class="s2-label" ${ED('s2-label','.s2-label')}>${d['s2-label']||''}</div>
    <div class="s2-note" ${ED('s2-note','.s2-note')}>${d['s2-note']||''}</div>
  </div></div>`,
  3: (d) => `<div class="scene scene-3">
    <h2 class="s3-heading" ${ED('s3-heading','.s3-heading')}>${d['s3-heading']||''}</h2>
    <div class="s3-cards">
      <div class="s3-card s3-c1"><div class="s3-card-num">01</div><div class="s3-card-text" ${ED('s3-card-1','.s3-card-text')}>${d['s3-card-1']||''}</div></div>
      <div class="s3-card s3-c2"><div class="s3-card-num">02</div><div class="s3-card-text" ${ED('s3-card-2','.s3-card-text')}>${d['s3-card-2']||''}</div></div>
      <div class="s3-card s3-c3"><div class="s3-card-num">03</div><div class="s3-card-text" ${ED('s3-card-3','.s3-card-text')}>${d['s3-card-3']||''}</div></div>
    </div></div>`,
  4: (d) => `<div class="scene scene-4">
    <div class="s4-quote-mark">✦</div>
    <div class="s4-quote" ${ED('s4-quote','.s4-quote')}>${d['s4-quote']||''}</div>
  </div>`,
  5: (d) => `<div class="scene scene-5">
    <h2 class="s5-heading" ${ED('s5-heading','.s5-heading')}>${d['s5-heading']||''}</h2>
    <div class="s5-list">
      <div class="s5-item s5-i1"><div class="s5-dot"></div><div class="s5-text" ${ED('s5-text-1','.s5-text')}>${d['s5-text-1']||''}</div></div>
      <div class="s5-item s5-i2"><div class="s5-dot"></div><div class="s5-text" ${ED('s5-text-2','.s5-text')}>${d['s5-text-2']||''}</div></div>
      <div class="s5-item s5-i3"><div class="s5-dot"></div><div class="s5-text" ${ED('s5-text-3','.s5-text')}>${d['s5-text-3']||''}</div></div>
    </div></div>`,
  6: (d) => `<div class="scene scene-6">
    <h1 class="s6-title" ${ED('s6-title','.s6-title')}>${d['s6-title']||''}</h1>
    <div class="s6-sub" ${ED('s6-sub','.s6-sub')}>${d['s6-sub']||''}</div>
    <div class="s6-hashtag" ${ED('s6-hashtag','.s6-hashtag')}>${d['s6-hashtag']||''}</div>
  </div>`,
};

const SCENE_NAMES = { 1: 'HOOK', 2: 'KHÁI NIỆM', 3: '3 DẤU HIỆU', 4: 'QUOTE', 5: 'CHA MẸ', 6: 'CTA' };

let STATE = { fields: [], data: {} };

// Enter trong textarea → tự convert thành <br> khi preview/save.
// Load từ server thì <br> → \n để textarea hiển thị xuống dòng tự nhiên.
const newlineToBr = (s) => (s || '').replace(/\r?\n/g, '<br>');
const brToNewline = (s) => (s || '').replace(/<br\s*\/?>/gi, '\n');

function setStatus(msg, cls = '') {
  const s = document.getElementById('status');
  s.textContent = msg;
  s.className = cls;
}

function renderGallery() {
  const g = document.getElementById('gallery');
  g.innerHTML = '';
  for (let i = 1; i <= 6; i++) {
    const thumb = document.createElement('div');
    thumb.className = 'thumb';
    thumb.innerHTML = `<div class="thumb-label">S${i} · ${SCENE_NAMES[i]}</div><div class="thumb-inner" id="thumb-${i}"><div class="brand-watermark">PK NHI BOOM BOOM</div></div>`;
    g.appendChild(thumb);
  }
  requestAnimationFrame(() => {
    document.querySelectorAll('.thumb').forEach(t => {
      const w = t.clientWidth;
      const inner = t.querySelector('.thumb-inner');
      inner.style.transform = `scale(${w / 1080})`;
    });
    updateAllThumbs();
    wireEditables();
  });
}

function wireEditables() {
  document.querySelectorAll('[data-bind]').forEach(el => {
    if (el._wired) return;
    el._wired = true;
    // input: cập nhật STATE.data (preserve raw HTML including <br>, <em>)
    el.addEventListener('input', () => {
      const id = el.dataset.bind;
      STATE.data[id] = el.innerHTML;
    });
  });
  document.querySelectorAll('[data-sel]').forEach(el => {
    if (el._wiredFocus) return;
    el._wiredFocus = true;
    el.addEventListener('focus', () => focusStyleBar(el));
  });
}

let CURRENT_SEL = null;

function rgbToHex(rgb) {
  if (!rgb) return '#ffffff';
  if (rgb.startsWith('#')) return rgb.length === 7 ? rgb : '#ffffff';
  const m = String(rgb).match(/\d+/g);
  if (!m || m.length < 3) return '#ffffff';
  return '#' + m.slice(0,3).map(n => parseInt(n).toString(16).padStart(2,'0')).join('');
}

function focusStyleBar(el) {
  const sel = el.dataset.sel;
  if (!sel) return;
  CURRENT_SEL = sel;
  document.getElementById('stylebar').classList.add('show');
  document.getElementById('styleTarget').textContent = '🎨 ' + sel;
  const cur = STATE.styles[sel] || {};
  const cs = getComputedStyle(el);
  document.getElementById('styFs').value = formatForDisplay('font-size',     cur['font-size']     || cs.fontSize);
  document.getElementById('styLh').value = formatForDisplay('line-height',   cur['line-height']   || cs.lineHeight);
  document.getElementById('styLs').value = formatForDisplay('letter-spacing',cur['letter-spacing']|| cs.letterSpacing);
  ['styFs','styLh','styLs'].forEach(id => autosizeInput(document.getElementById(id)));
  const colorVal = cur['color'] || cs.color;
  document.getElementById('styColor').value = colorVal;
  document.getElementById('styColorPick').value = rgbToHex(colorVal);
  ['styFs','styLh','styLs','styColor','styGap'].forEach(id => { const e = document.getElementById(id); if (e) autosizeInput(e); });
  // group-gap: luôn đọc từ .s3-cards (sync với .s5-list)
  const gapTargets = document.querySelectorAll('.s3-cards, .s5-list');
  if (gapTargets.length) {
    const gapVal = (STATE.styles['.s3-cards'] && STATE.styles['.s3-cards']['gap'])
                || getComputedStyle(gapTargets[0]).gap;
    document.getElementById('styGap').value = formatForDisplay('gap', gapVal);
    autosizeInput(document.getElementById('styGap'));
  }
  autosizeInput(document.getElementById('styColor'));
}

function applyStyleProp(prop, value) {
  if (!CURRENT_SEL) return;
  const v = formatForApply(prop, value);
  STATE.styles[CURRENT_SEL] = STATE.styles[CURRENT_SEL] || {};
  STATE.styles[CURRENT_SEL][prop] = v;
  document.querySelectorAll(CURRENT_SEL).forEach(el => el.style.setProperty(prop, v));
}

// Auto-size: đo content thật bằng span ẩn → set width input chính xác (zero trắng cuối).
function autosizeInput(inp) {
  if (!inp) return;
  const cs = getComputedStyle(inp);
  const span = document.createElement('span');
  span.style.cssText = `font:${cs.font};letter-spacing:${cs.letterSpacing};visibility:hidden;position:absolute;white-space:pre;`;
  span.textContent = inp.value || inp.placeholder || '0';
  document.body.appendChild(span);
  const w = span.offsetWidth;
  span.remove();
  inp.style.width = Math.max(8, w + 3) + 'px';  // tăng thêm 2px buffer để hiển thị đủ số
}

// Pad ".0" cho số nguyên (giữ precision nếu đã có decimal — không round)
function padDecimal(s) {
  s = String(s).trim();
  if (s.includes('.')) return s;
  if (/^-?\d+$/.test(s)) return s + '.0';
  return s;
}
// Strip "px" suffix khi hiển thị; line-height + letter-spacing luôn dạng x.x
function formatForDisplay(prop, value) {
  if (!value || prop === 'color') return value || '';
  if (prop === 'line-height') {
    const num = parseFloat(value);
    return isNaN(num) ? value : num.toFixed(2);
  }
  const stripped = String(value).replace(/px$/, '').trim();
  if (prop === 'letter-spacing') return padDecimal(stripped);
  return stripped;
}
// Thêm "px" lại khi apply (chỉ với giá trị thuần số)
function formatForApply(prop, value) {
  if (!value) return value;
  if (prop === 'color' || prop === 'line-height') return value;
  const t = String(value).trim();
  if (/^-?\d+(\.\d+)?$/.test(t)) return t + 'px';
  return t;
}
function wireStyleBar() {
  const map = { styFs: 'font-size', styLh: 'line-height', styLs: 'letter-spacing', styColor: 'color' };
  for (const id in map) {
    const inp = document.getElementById(id);
    inp.addEventListener('input', () => { applyStyleProp(map[id], inp.value); autosizeInput(inp); });
  }
  // group-gap: apply cho .s3-cards + .s5-list (lưu state vào cả 2 selector)
  const gapInput = document.getElementById('styGap');
  if (gapInput) {
    gapInput.addEventListener('input', () => {
      const v = formatForApply('gap', gapInput.value);
      ['.s3-cards', '.s5-list'].forEach(sel => {
        STATE.styles[sel] = STATE.styles[sel] || {};
        STATE.styles[sel]['gap'] = v;
        document.querySelectorAll(sel).forEach(el => el.style.setProperty('gap', v));
      });
      autosizeInput(gapInput);
    });
  }
  // Native color picker → sync với text input + apply
  const pick = document.getElementById('styColorPick');
  pick.addEventListener('input', () => {
    document.getElementById('styColor').value = pick.value;
    applyStyleProp('color', pick.value);
  });
  // Swatch buttons
  document.querySelectorAll('.swatch').forEach(btn => {
    btn.addEventListener('click', () => {
      const c = btn.dataset.color;
      document.getElementById('styColor').value = c;
      document.getElementById('styColorPick').value = c;
      applyStyleProp('color', c);
    });
  });
  // Bump (+/−): preserve decimal places + apply CSS trực tiếp + dispatch input để keep handlers sync
  const BUMP_PROP_MAP = { styFs: 'font-size', styLh: 'line-height', styLs: 'letter-spacing', styGap: 'gap' };
  function bumpHandler(btn) {
    const targetId = btn.dataset.target;
    const inp = document.getElementById(targetId);
    if (!inp) { console.warn('[bump] no input for', targetId); return; }
    const delta = parseFloat(btn.dataset.delta);
    const v = (inp.value || '').trim();
    const m = v.match(/^(-?\d*\.?\d+)(.*)$/);
    const numStr = m ? m[1] : '0';
    const decimalsCur = numStr.includes('.') ? numStr.split('.')[1].length : 0;
    const decimalsDelta = String(Math.abs(delta)).includes('.') ? String(Math.abs(delta)).split('.')[1].length : 0;
    let decimals = Math.max(decimalsCur, decimalsDelta);
    const prop = BUMP_PROP_MAP[targetId];
    if (prop === 'line-height') decimals = 2;
    const num = parseFloat(numStr) + delta;
    const unit = m ? m[2].trim() : '';
    inp.value = num.toFixed(decimals) + unit;
    console.log('[bump]', targetId, '→', inp.value, 'prop=', prop, 'CURRENT_SEL=', CURRENT_SEL);
    if (prop === 'gap') {
      const cssVal = formatForApply('gap', inp.value);
      ['.s3-cards', '.s5-list'].forEach(sel => {
        STATE.styles[sel] = STATE.styles[sel] || {};
        STATE.styles[sel]['gap'] = cssVal;
        document.querySelectorAll(sel).forEach(el => el.style.setProperty('gap', cssVal));
      });
    } else if (prop) {
      applyStyleProp(prop, inp.value);
    }
    autosizeInput(inp);
  }
  // Bump: fire NGAY khi pointerdown + hold-to-repeat. Không giới hạn số lần click.
  document.querySelectorAll('.bump').forEach(btn => {
    let holdTimer = null;
    let repeatTimer = null;
    const trigger = () => {
      btn.style.background = '#ff7a2a';
      setTimeout(() => { btn.style.background = ''; }, 80);
      bumpHandler(btn);
    };
    const onDown = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      trigger();
      // Hold > 400ms → auto-repeat 8/s
      holdTimer = setTimeout(() => {
        repeatTimer = setInterval(trigger, 120);
      }, 400);
    };
    const onUp = () => {
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
      if (repeatTimer) { clearInterval(repeatTimer); repeatTimer = null; }
    };
    btn.addEventListener('pointerdown', onDown);
    btn.addEventListener('pointerup', onUp);
    btn.addEventListener('pointerleave', onUp);
    btn.addEventListener('pointercancel', onUp);
  });
}

function applyStylesToThumb(rootEl) {
  if (!STATE.styles) return;
  for (const sel in STATE.styles) {
    const props = STATE.styles[sel];
    rootEl.querySelectorAll(sel).forEach(el => {
      for (const p in props) {
        const v = props[p];
        if (v && String(v).trim()) el.style.setProperty(p, v);
      }
    });
  }
}

function updateAllThumbs() {
  const renderData = {};
  for (const k in STATE.data) renderData[k] = newlineToBr(STATE.data[k]);
  const watermark = '<div class="brand-watermark">PK NHI BOOM BOOM</div>';
  const taglineText = (STATE.data['s1-byline'] || 'HÀNH TRÌNH KIÊN TRÌ CÙNG CON VƯỢT KHÓ').replace(/<[^>]+>/g, '');
  const tagline = `<div class="tagline-footer">${taglineText}</div>`;
  for (let i = 1; i <= 6; i++) {
    const tpl = SCENE_TEMPLATES[i];
    const el = document.getElementById('thumb-' + i);
    el.innerHTML = tpl(renderData) + watermark + tagline;  // append watermark + tagline mỗi thumb
    applyStylesToThumb(el);
  }
}

function renderForm() { return; /* DEPRECATED — inline edit thay thế */ }
function _renderForm_old() {
  const f = document.getElementById('form');
  f.innerHTML = '';
  const grouped = {};
  STATE.fields.forEach(fld => {
    (grouped[fld.scene] = grouped[fld.scene] || []).push(fld);
  });
  const styleGrouped = {};
  (STATE.styleFields || []).forEach(sf => {
    (styleGrouped[sf.scene] = styleGrouped[sf.scene] || []).push(sf);
  });

  for (let s = 1; s <= 6; s++) {
    const sec = document.createElement('div');
    sec.className = 'scene-section';
    sec.innerHTML = `<h3>Scene ${s} · ${SCENE_NAMES[s]}</h3>`;
    (grouped[s] || []).forEach(fld => {
      const div = document.createElement('div');
      div.className = 'field';
      const val = (STATE.data[fld.id] || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      div.innerHTML = `<label>${fld.label}</label><textarea rows="${fld.rows}" data-id="${fld.id}">${val}</textarea>`;
      sec.appendChild(div);
    });
    (styleGrouped[s] || []).forEach(sf => {
      const panel = document.createElement('details');
      panel.className = 'style-panel';
      panel.open = false;
      const rows = sf.props.map(p => {
        const v = ((STATE.styles[sf.sel]||{})[p] || '').replace(/"/g,'&quot;');
        return `<div class="style-row"><label>${p}</label><input type="text" data-sel="${sf.sel}" data-prop="${p}" value="${v}"></div>`;
      }).join('');
      panel.innerHTML = `<summary>🎨 Style: ${sf.label} <code style="opacity:0.5">${sf.sel}</code></summary>${rows}`;
      sec.appendChild(panel);
    });
    f.appendChild(sec);
  }
  // Wire text live update
  document.querySelectorAll('textarea[data-id]').forEach(t => {
    t.addEventListener('input', () => {
      STATE.data[t.dataset.id] = t.value;
      updateAllThumbs();
    });
  });
  // Wire style live update
  document.querySelectorAll('input[data-sel]').forEach(inp => {
    inp.addEventListener('input', () => {
      const sel = inp.dataset.sel, prop = inp.dataset.prop;
      STATE.styles[sel] = STATE.styles[sel] || {};
      STATE.styles[sel][prop] = inp.value;
      updateAllThumbs();
    });
  });
}

async function reload() {
  setStatus('Loading…');
  const r = await fetch('/load');
  const j = await r.json();
  STATE = j;
  // Convert <br> → \n cho textarea hiển thị xuống dòng đẹp
  for (const k in STATE.data) STATE.data[k] = brToNewline(STATE.data[k]);
  renderForm();
  renderGallery();
  setStatus('Ready', 'ok');
}

function savePayload() {
  // Convert \n → <br> trước khi gửi server (HTML cần <br>)
  const data = {};
  for (const k in STATE.data) data[k] = newlineToBr(STATE.data[k]);
  return JSON.stringify({ data: data, styles: STATE.styles });
}

let _saving = false;
async function save() {
  if (_saving) { console.warn('Save in progress — ignored'); return; }
  _saving = true;
  setStatus('Saving…', 'busy');
  const t0 = performance.now();
  try {
    const r = await fetch('/save', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: savePayload() });
    const j = await r.json();
    const dt = (performance.now() - t0).toFixed(0);
    console.log(`[save] ${dt}ms`, j);
    setStatus(j.ok ? `Saved ✓ (${dt}ms)` : 'Save failed', j.ok ? 'ok' : 'err');
  } catch (e) {
    console.error('[save] EXCEPTION', e);
    setStatus('Save error: ' + e.message, 'err');
  } finally {
    _saving = false;
  }
}

async function render() {
  setStatus('Rendering…', 'busy');
  document.getElementById('renderLog').textContent = '⏳ Auto-save → render mp4… (3–5 phút, draft quality)';
  // Auto-save first
  await fetch('/save', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: savePayload() });
  const r = await fetch('/render', { method: 'POST' });
  const j = await r.json();
  if (!j.ok) { setStatus('Render rejected: ' + (j.msg||''), 'err'); return; }
  // Poll status
  const poll = setInterval(async () => {
    const s = await fetch('/render-status').then(r => r.json());
    if (!s.running) {
      clearInterval(poll);
      setStatus('Render done ✓', 'ok');
      document.getElementById('renderLog').textContent = s.last_out || '';
    }
  }, 5000);
}

wireStyleBar();
reload();
refreshVoiceInfo();

async function openFolder() {
  try {
    await fetch('/open-folder', { method: 'POST' });
  } catch (e) { console.warn('openFolder fail', e); }
}

async function refreshVoiceInfo() {
  try {
    const r = await fetch('/voice-info'); const j = await r.json();
    const btn = document.getElementById('voiceBtn');
    if (j.exists) {
      btn.textContent = `🎤 ${j.filename} · ${j.duration.toFixed(1)}s`;
    } else {
      btn.textContent = '🎤 Chưa có voice — click để chọn';
    }
  } catch(e) {}
}

function pickVoice() {
  document.getElementById('voiceFile').click();
}

document.getElementById('voiceFile').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setStatus(`Uploading ${file.name}…`, 'busy');
  const buf = await file.arrayBuffer();
  const r = await fetch('/upload-voice?name=' + encodeURIComponent(file.name), { method: 'POST', body: buf, headers: {'Content-Type': 'audio/wav'} });
  const j = await r.json();
  if (j.ok) {
    setStatus(`Voice updated · ${j.duration.toFixed(1)}s`, 'ok');
    refreshVoiceInfo();
  } else {
    setStatus('Upload failed: ' + (j.msg||''), 'err');
  }
  e.target.value = '';
});

// Heartbeat: gửi mỗi 5s để server biết tab còn mở
setInterval(() => { fetch('/heartbeat').catch(()=>{}); }, 5000);
// Đã loại bỏ listener pagehide tự động shutdown để tránh lỗi ERR_CONNECTION_REFUSED khi refresh F5.
// Server sẽ tự động tắt thông qua cơ chế Watchdog sau 5 phút không có heartbeat.

function showTemplateModal() {
  const modal = document.getElementById('templateModal');
  modal.style.display = 'flex';
  loadTemplateList();
}

function closeTemplateModal() {
  const modal = document.getElementById('templateModal');
  modal.style.display = 'none';
}

async function loadTemplateList() {
  const listDiv = document.getElementById('templateList');
  listDiv.innerHTML = '<div style="color: #ffcc00; font-size: 13px;">Đang tải danh sách...</div>';
  try {
    const r = await fetch('/list-templates');
    const j = await r.json();
    if (j.ok && j.templates) {
      listDiv.innerHTML = '';
      if (j.templates.length === 0) {
        listDiv.innerHTML = '<div style="color: #ffcc00; font-size: 13px;">Không tìm thấy template nào</div>';
        return;
      }
      j.templates.forEach(tpl => {
        const btn = document.createElement('button');
        btn.style.width = '100%';
        btn.style.textAlign = 'left';
        btn.style.textTransform = 'none';
        btn.style.fontWeight = '500';
        btn.textContent = tpl;
        btn.onclick = () => confirmSaveToTemplate(tpl);
        listDiv.appendChild(btn);
      });
    } else {
      listDiv.innerHTML = '<div style="color: #ff0000; font-size: 13px;">Lỗi tải danh sách</div>';
    }
  } catch (e) {
    listDiv.innerHTML = '<div style="color: #ff0000; font-size: 13px;">Lỗi kết nối: ' + e.message + '</div>';
  }
}

async function confirmSaveToTemplate(templateName) {
  if (!confirm(`Bạn có chắc chắn muốn ghi đè styles hiện tại vào template "${templateName}"?`)) return;
  closeTemplateModal();
  setStatus(`Saving to template ${templateName}…`, 'busy');
  const t0 = performance.now();
  try {
    const r = await fetch(`/save-to-template?template=${encodeURIComponent(templateName)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: savePayload()
    });
    const j = await r.json();
    const dt = (performance.now() - t0).toFixed(0);
    console.log(`[save-to-template] ${dt}ms`, j);
    if (j.ok) {
      setStatus(`Saved to template ✓ (${dt}ms)`, 'ok');
    } else {
      setStatus(`Save to template failed: ${j.msg || ''}`, 'err');
    }
  } catch (e) {
    console.error('[save-to-template] EXCEPTION', e);
    setStatus('Save to template error: ' + e.message, 'err');
  }
}
</script>

<!-- Template Selector Modal -->
<div id="templateModal" class="modal" style="display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(0,0,0,0.8); align-items: center; justify-content: center;">
  <div class="modal-content" style="background-color: #0d0d0d; margin: auto; padding: 24px; border: 2px solid #443311; border-radius: 12px; width: 450px; box-shadow: 0 4px 20px rgba(212, 175, 55, 0.15); display: flex; flex-direction: column; gap: 18px;">
    <h3 style="margin: 0; color: #D4AF37; font-size: 18px; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #443311; padding-bottom: 12px; font-weight: 800;">Chọn Template để lưu</h3>
    <div id="templateList" style="display: flex; flex-direction: column; gap: 8px; max-height: 250px; overflow-y: auto; padding-right: 4px;">
      <!-- Templates will be loaded here dynamically -->
    </div>
    <div style="display: flex; justify-content: flex-end; gap: 10px; border-top: 1px solid #443311; padding-top: 12px; margin-top: 6px;">
      <button class="secondary" onclick="closeTemplateModal()" style="text-transform: capitalize;">Hủy</button>
    </div>
  </div>
</div>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    render_status = {"running": False, "last_out": ""}

    def log_message(self, fmt, *args): pass  # silence access log

    def _send(self, code, ctype, body):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            # Inject CSS từ workspace index.html → editor preview render = MP4 render
            html_serve = EDITOR_HTML
            try:
                idx_html = INDEX.read_text(encoding="utf-8")
                # Extract toàn bộ <style> block của index.html (skeleton CSS + Boss save override)
                style_blocks = re.findall(r'<style[^>]*>(.*?)</style>', idx_html, re.DOTALL)
                workspace_css = "\n".join(style_blocks)
                # Rewrite font URL './fontname.ttf' → '/workspace-file/fontname.ttf' (endpoint serve from workspace)
                workspace_css = re.sub(
                    r"url\(['\"]\./([^'\"]+\.(?:ttf|otf|woff2?))['\"]\)",
                    r"url('/workspace-file/\1')",
                    workspace_css, flags=re.IGNORECASE
                )
                # Scope CSS vào .thumb-inner để không leak UI editor
                # (chỉ wrap selectors bắt đầu bằng dấu chấm, bỏ qua @font-face/@import/etc)
                scoped = re.sub(
                    r'(^|\})\s*(\.[\w][\w\-,\s\.]*\{)',
                    lambda m: m.group(1) + " .thumb-inner " + m.group(2),
                    workspace_css,
                    flags=re.MULTILINE
                )
                # Override: force scene visible (skeleton có opacity:0 cho GSAP, editor preview phải show)
                override = '.thumb-inner .scene{opacity:1!important;position:relative!important;}'
                inject = f'<style id="_from_workspace">{scoped}\n{override}</style>'
                html_serve = html_serve.replace('</head>', inject + '</head>', 1)
                # Thay thế FOLDER title thành icon + tên thư mục dạng chữ thường, nhỏ, xuống dòng tự nhiên, đẩy lên trên
                html_serve = html_serve.replace(
                    '📁 FOLDER',
                    f'<span style="font-size: 16px; line-height: 1;">📁</span><span style="word-break: break-all; text-transform: none; font-size: 12px; font-weight: 600; line-height: 1.2; margin-top: 2px; letter-spacing: 0.5px;">{WORK.name}</span>'
                )
            except Exception as e:
                print(f"[/] inject workspace CSS fail: {e}")
            self._send(200, "text/html; charset=utf-8", html_serve)
        elif path.startswith("/workspace-file/"):
            # Serve file từ workspace (font, audio...) cho editor preview
            fname = path[len("/workspace-file/"):]
            if "/" in fname or "\\" in fname or ".." in fname:
                self._send(400, "text/plain", "Bad name"); return
            fp = WORK / fname
            if fp.exists():
                ext = fp.suffix.lower()
                ctype = {".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2", ".wav": "audio/wav"}.get(ext, "application/octet-stream")
                self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
                self.wfile.write(fp.read_bytes())
            else:
                self._send(404, "text/plain", "Not found")
        elif path == "/fonts/UTM-Cookies.ttf":
            font_path = Path(__file__).parent.parent / "_fonts" / "UTM-Cookies.ttf"
            if font_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "font/ttf")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(font_path.read_bytes())
            else:
                self._send(404, "text/plain", "Font not found")
        elif path == "/load":
            html = INDEX.read_text(encoding="utf-8")
            data = {f["id"]: clean_boom_boom(get_inner(html, f["id"])) for f in FIELDS}
            styles = {sf["sel"]: {p: get_css(html, sf["sel"], p) for p in sf["props"]} for sf in STYLE_FIELDS}
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"fields": FIELDS, "data": data, "styleFields": STYLE_FIELDS, "styles": styles}, ensure_ascii=False))
        elif path == "/list-templates":
            try:
                templates_dir = Path(__file__).parent.parent
                subdirs = [d.name for d in templates_dir.iterdir() if d.is_dir() and not d.name.startswith("_")]
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "templates": sorted(subdirs)}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/render-status":
            self._send(200, "application/json", json.dumps(Handler.render_status))
        elif path == "/workspace-info":
            self._send(200, "application/json", json.dumps({"workspace": str(WORK.resolve())}))
        elif path == "/heartbeat":
            global LAST_HEARTBEAT
            LAST_HEARTBEAT = time.time()
            self._send(200, "application/json", '{"ok":true}')
        elif path.startswith("/preview/"):
            # Serve index.html nhưng inject CSS+JS để chỉ show 1 scene + contenteditable + postMessage sync
            try:
                n = int(path.rsplit("/", 1)[-1].split("?")[0])
            except Exception:
                self._send(400, "text/plain", "Bad scene number"); return
            html = INDEX.read_text(encoding="utf-8")
            edit_ids = [f["id"] for f in FIELDS]
            inject_css = (
                "<style id=\"_preview_override\">"
                ".scene{opacity:0!important;transition:none!important;}"
                f"#s{n}{{opacity:1!important;}}"
                "[contenteditable=\"true\"]{cursor:text;}"
                "[contenteditable=\"true\"]:hover{outline:2px dashed rgba(77,204,204,0.6);outline-offset:4px;}"
                "[contenteditable=\"true\"]:focus{outline:2px solid #ff7a2a;outline-offset:4px;}"
                "</style>"
            )
            inject_js = (
                "<script>(function(){"
                f"const SCENE_NUM={n};"
                f"const EDIT_IDS={json.dumps(edit_ids)};"
                "function setupEdit(){"
                "  EDIT_IDS.forEach(id=>{"
                "    const el=document.getElementById(id); if(!el || el._wired) return;"
                "    el._wired=true;"
                "    el.setAttribute('contenteditable','true');"
                "    el.addEventListener('mousedown',e=>e.stopPropagation(),true);"
                "    el.addEventListener('focus',()=>{"
                "      const sel='.'+ (el.className.match(/\\bs\\d-[\\w-]+/)||[''])[0];"
                "      parent.postMessage({t:'focus',scene:SCENE_NUM,id,sel,html:el.innerHTML},'*');"
                "    });"
                "    el.addEventListener('input',()=>{ parent.postMessage({t:'changed',id,html:el.innerHTML},'*'); });"
                "  });"
                "}"
                "function trySeek(){"
                "  if(window.__timelines && window.__timelines['root']){"
                "    try{"
                "      const t = window.__TL ? window.__TL['s' + SCENE_NUM] : null;"
                "      const PEAK = t ? (t.in + (t.out - t.in) * 0.8) : 5;"
                "      window.__timelines['root'].seek(PEAK).pause();"
                "    }catch(e){}"
                "    return true;"
                "  } return false;"
                "}"
                "function init(){"
                "  setupEdit();"
                "  if(!trySeek()){ let n=0; const iv=setInterval(()=>{ if(trySeek()||++n>30) clearInterval(iv); },200); }"
                "  window.addEventListener('message',e=>{"
                "    const m=e.data||{};"
                "    if(m.t==='set-style'){ document.querySelectorAll(m.sel).forEach(el=>el.style.setProperty(m.prop,m.val)); }"
                "    else if(m.t==='set-text'){ const el=document.getElementById(m.id); if(el && document.activeElement!==el) el.innerHTML=m.html; }"
                "  });"
                "  parent.postMessage({t:'ready',scene:SCENE_NUM},'*');"
                "}"
                "if(document.readyState==='complete')init(); else window.addEventListener('load',init);"
                "})();</script>"
            )
            html = html.replace("</head>", inject_css + "</head>", 1)
            if "</body>" in html:
                html = html.replace("</body>", inject_js + "</body>", 1)
            else:
                html = html + inject_js
            self._send(200, "text/html; charset=utf-8", html)
        elif path == "/narration.wav":
            # Phục vụ latest .wav trong workspace (ưu tiên content.json voice.file)
            wav = _find_voice_wav(WORK)
            if wav and wav.exists():
                self._send(200, "audio/wav", wav.read_bytes())
            else:
                self._send(404, "text/plain", "no audio")
        elif path == "/voice-info":
            wav = _find_voice_wav(WORK)
            info = {"exists": False, "filename": "", "size": 0, "duration": 0}
            if wav and wav.exists():
                info = {"exists": True, "filename": wav.name, "size": wav.stat().st_size, "duration": 0}
                try:
                    out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(wav)], capture_output=True, text=True, timeout=10).stdout.strip()
                    info["duration"] = float(out) if out else 0
                except Exception: pass
            self._send(200, "application/json", json.dumps(info))
        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        body = raw_body.decode("utf-8", errors="replace") if raw_body else ""
        if path == "/save":
            t0 = time.perf_counter()
            payload = json.loads(body)
            data = payload.get("data") or payload
            styles = payload.get("styles") or {}
            html = INDEX.read_text(encoding="utf-8")
            t1 = time.perf_counter()
            for elem_id, new_inner in data.items():
                html = set_inner(html, elem_id, clean_boom_boom(new_inner))
            t2 = time.perf_counter()
            for sel, props in styles.items():
                for prop, value in props.items():
                    if value and value.strip():
                        html = set_css(html, sel, prop, value.strip())
            t3 = time.perf_counter()
            safe_write_file(INDEX, html)
            t4 = time.perf_counter()
            print(f"[USER] [save] read={t1-t0:.2f}s inner={t2-t1:.2f}s css={t3-t2:.2f}s write={t4-t3:.2f}s total={t4-t0:.2f}s")
            
            # --- CẬP NHẬT CONTENT.JSON ĐỒNG BỘ ---
            co_file = WORK / "content.json"
            if co_file.exists():
                try:
                    co = json.loads(co_file.read_text(encoding="utf-8"))
                    if "scenes" not in co: co["scenes"] = {}
                    
                    # s1
                    if "s1" not in co["scenes"]: co["scenes"]["s1"] = {}
                    if "s1-tag" in data: co["scenes"]["s1"]["eyebrow"] = clean_boom_boom(data["s1-tag"])
                    if "s1-title" in data: co["scenes"]["s1"]["title"] = clean_boom_boom(data["s1-title"])
                    if "s1-byline" in data: co["scenes"]["s1"]["byline"] = clean_boom_boom(data["s1-byline"])
                    
                    # s2
                    if "s2" not in co["scenes"]: co["scenes"]["s2"] = {}
                    if "s2-num" in data: co["scenes"]["s2"]["big_text"] = clean_boom_boom(data["s2-num"])
                    if "s2-label" in data: co["scenes"]["s2"]["label"] = clean_boom_boom(data["s2-label"])
                    if "s2-note" in data: co["scenes"]["s2"]["note"] = clean_boom_boom(data["s2-note"])
                    
                    # s3
                    if "s3" not in co["scenes"]: co["scenes"]["s3"] = {}
                    if "s3-heading" in data: co["scenes"]["s3"]["heading"] = clean_boom_boom(data["s3-heading"])
                    if "cards" not in co["scenes"]["s3"]: co["scenes"]["s3"]["cards"] = [{}, {}, {}]
                    while len(co["scenes"]["s3"]["cards"]) < 3:
                        co["scenes"]["s3"]["cards"].append({})
                    if "s3-card-1" in data: co["scenes"]["s3"]["cards"][0]["text"] = clean_boom_boom(data["s3-card-1"])
                    if "s3-card-2" in data: co["scenes"]["s3"]["cards"][1]["text"] = clean_boom_boom(data["s3-card-2"])
                    if "s3-card-3" in data: co["scenes"]["s3"]["cards"][2]["text"] = clean_boom_boom(data["s3-card-3"])
                    
                    # s4
                    if "s4" not in co["scenes"]: co["scenes"]["s4"] = {}
                    if "s4-quote" in data: co["scenes"]["s4"]["quote_html"] = clean_boom_boom(data["s4-quote"])
                    
                    # s5
                    if "s5" not in co["scenes"]: co["scenes"]["s5"] = {}
                    if "s5-heading" in data: co["scenes"]["s5"]["heading"] = clean_boom_boom(data["s5-heading"])
                    if "items" not in co["scenes"]["s5"]: co["scenes"]["s5"]["items"] = [{}, {}, {}]
                    while len(co["scenes"]["s5"]["items"]) < 3:
                        co["scenes"]["s5"]["items"].append({})
                    if "s5-text-1" in data: co["scenes"]["s5"]["items"][0]["text"] = clean_boom_boom(data["s5-text-1"])
                    if "s5-text-2" in data: co["scenes"]["s5"]["items"][1]["text"] = clean_boom_boom(data["s5-text-2"])
                    if "s5-text-3" in data: co["scenes"]["s5"]["items"][2]["text"] = clean_boom_boom(data["s5-text-3"])
                    
                    # s6
                    if "s6" not in co["scenes"]: co["scenes"]["s6"] = {}
                    if "s6-title" in data: co["scenes"]["s6"]["title"] = clean_boom_boom(data["s6-title"])
                    if "s6-sub" in data: co["scenes"]["s6"]["sub"] = clean_boom_boom(data["s6-sub"])
                    if "s6-hashtag" in data: co["scenes"]["s6"]["hashtag"] = clean_boom_boom(data["s6-hashtag"])
                    
                    safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
                    print(f"[SYSTEM] [save] Đồng bộ thành công content.json cho {WORK.name}")
                except Exception as e:
                    print(f"[SYSTEM] [save] Lỗi đồng bộ content.json: {e}")
            
            self._send(200, "application/json", '{"ok":true}')
        elif path == "/save-to-template":
            try:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                template_name = params.get("template", [None])[0]
                if not template_name or "/" in template_name or "\\" in template_name or template_name.startswith("_"):
                    self._send(400, "application/json", '{"ok":false,"msg":"Bad template name"}')
                    return
                
                templates_dir = Path(__file__).parent.parent
                tpl_dir = templates_dir / template_name
                if not tpl_dir.exists() or not tpl_dir.is_dir():
                    self._send(404, "application/json", '{"ok":false,"msg":"Template not found"}')
                    return
                
                payload = json.loads(body)
                styles = payload.get("styles") or {}
                
                # 1. Update skeleton.html of the template
                skel_path = tpl_dir / "skeleton.html"
                if skel_path.exists():
                    skel_html = skel_path.read_text(encoding="utf-8")
                    for sel, props in styles.items():
                        for prop, value in props.items():
                            if value and value.strip():
                                skel_html = set_css(skel_html, sel, prop, value.strip())
                    safe_write_file(skel_path, skel_html)
                    print(f"[SYSTEM] [save-to-template] Updated skeleton.html of {template_name}")
                
                # 2. Update style-tokens.json of the template
                tokens_path = tpl_dir / "style-tokens.json"
                if tokens_path.exists():
                    try:
                        tokens_data = json.loads(tokens_path.read_text(encoding="utf-8"))
                    except Exception:
                        tokens_data = {}
                    
                    if "tokens" not in tokens_data:
                        tokens_data["tokens"] = {}
                    if "rules" not in tokens_data["tokens"]:
                        tokens_data["tokens"]["rules"] = {}
                    
                    for sel, props in styles.items():
                        if sel not in tokens_data["tokens"]["rules"]:
                            tokens_data["tokens"]["rules"][sel] = {}
                        for prop, value in props.items():
                            if value and value.strip():
                                tokens_data["tokens"]["rules"][sel][prop] = value.strip()
                    
                    safe_write_file(tokens_path, json.dumps(tokens_data, ensure_ascii=False, indent=2))
                    print(f"[SYSTEM] [save-to-template] Updated style-tokens.json of {template_name}")
                
                self._send(200, "application/json", '{"ok":true}')
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/render":
            if Handler.render_status["running"]:
                self._send(409, "application/json", '{"ok":false,"msg":"already running"}')
                return
            Handler.render_status["running"] = True
            globals()["RENDER_IS_RUNNING"] = lambda: Handler.render_status["running"]
            Handler.render_status["last_out"] = "Starting render…"

            def do_render():
                import re
                ts_part = time.strftime("T%m.%d_%Hh%M")
                match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", WORK.name)
                if match:
                    slug_part = match.group(1)
                else:
                    slug_part = WORK.name
                
                slug_clean = slugify_vietnamese(slug_part, max_len=60)
                out_name = f"{slug_clean}_{ts_part}.mp4"
                logging.info(f"RENDER START: out={out_name} cwd={WORK}")
                t0 = time.time()
                try:
                    proc = subprocess.run(
                        f'npx -y -p hyperframes hyperframes render . --output {out_name} --fps 30 --quality draft --workers 2',
                        cwd=str(WORK), capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        shell=True,
                    )
                    out = (proc.stdout or "") + (proc.stderr or "")
                    tail = out[-1200:]
                    elapsed = time.time() - t0
                    Handler.render_status["last_out"] = f"Exit {proc.returncode}\n→ {out_name}\n{tail}"
                    logging.info(f"RENDER END: exit={proc.returncode} elapsed={elapsed:.1f}s out={out_name}")
                    if proc.returncode != 0:
                        logging.error(f"RENDER FAILED stderr tail:\n{(proc.stderr or '')[-2000:]}")
                except Exception as e:
                    logging.exception(f"RENDER EXCEPTION: {e}")
                    Handler.render_status["last_out"] = f"ERROR: {e}"
                finally:
                    Handler.render_status["running"] = False

            threading.Thread(target=do_render, daemon=True).start()
            self._send(202, "application/json", '{"ok":true,"msg":"render started"}')
        elif path == "/shutdown":
            self._send(200, "application/json", '{"ok":true}')
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
        elif path == "/open-folder":
            try:
                # Dùng explorer.exe trực tiếp (os.startfile có thể fail trong HTTP thread context)
                subprocess.Popen(["explorer.exe", str(WORK)], shell=False)
                self._send(200, "application/json", '{"ok":true}')
            except Exception as e:
                # Fallback os.startfile
                try:
                    os.startfile(str(WORK))
                    self._send(200, "application/json", '{"ok":true,"fallback":"startfile"}')
                except Exception as e2:
                    self._send(500, "application/json", json.dumps({"ok": False, "msg": f"explorer: {e}; startfile: {e2}"}))
        elif path.startswith("/upload-voice"):
            # Save raw .wav bytes to narration.wav and original name
            try:
                if not raw_body:
                    self._send(400, "application/json", '{"ok":false,"msg":"empty"}'); return
                
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                orig_name = params.get("name", ["narration.wav"])[0]
                orig_name = os.path.basename(orig_name)
                
                safe_write_file(WORK / orig_name, raw_body, is_binary=True)
                safe_write_file(WORK / "narration.wav", raw_body, is_binary=True)
                
                # Probe new duration
                dur = 0
                try:
                    out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(WORK / orig_name)], capture_output=True, text=True, timeout=10).stdout.strip()
                    dur = float(out) if out else 0
                except Exception: pass
                
                # Cập nhật content.json
                co_file = WORK / "content.json"
                old_wav_name = None
                if co_file.exists():
                    try:
                        co = json.loads(co_file.read_text(encoding="utf-8"))
                        old_wav_name = co.get("voice", {}).get("file")
                        if "voice" not in co: co["voice"] = {}
                        co["voice"]["file"] = orig_name
                        co["voice"]["duration"] = dur
                        safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
                        print(f"[SYSTEM] [upload-voice] Updated content.json: voice.file = {orig_name}")
                    except Exception as e:
                        print(f"[SYSTEM] [upload-voice] Update content.json error: {e}")
                
                # Cập nhật index.html
                if INDEX.exists():
                    try:
                        html = INDEX.read_text(encoding="utf-8")
                        if old_wav_name:
                            html = html.replace(f'src="{old_wav_name}"', f'src="{orig_name}"')
                        else:
                            html = re.sub(r'(<audio[^>]*\bsrc=")([^"]+)"', rf'\1{orig_name}"', html, count=1)
                        safe_write_file(INDEX, html)
                        print(f"[SYSTEM] [upload-voice] Updated index.html audio src to {orig_name}")
                    except Exception as e:
                        print(f"[upload-voice] Update index.html error: {e}")
                
                self._send(200, "application/json", json.dumps({"ok": True, "duration": dur, "size": len(raw_body)}))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))
        else:
            self._send(404, "text/plain", "Not found")


if __name__ == "__main__":
    # Thử tắt server cũ đang chạy ngầm trên cổng 5050
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/shutdown", method="POST")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            print(f"[SYSTEM] Đã gửi lệnh shutdown tới server cũ ở cổng {PORT}")
            time.sleep(0.6)  # Đợi 0.6s để socket được giải phóng hoàn toàn
    except Exception as e:
        # Không có server cũ đang chạy, bỏ qua
        pass

    print(f"\nEditor: http://localhost:{PORT}/\nWorkspace: {WORK}\nPress Ctrl+C to stop.\n")
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        # Ghi cổng vào file khi socket lắng nghe thành công
        try:
            (WORK / ".editor_port").write_text(str(PORT), encoding="utf-8")
            print(f"[SYSTEM] Đã ghi cổng {PORT} vào file .editor_port")
        except Exception as e:
            print(f"[SYSTEM] Lỗi ghi file .editor_port: {e}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
