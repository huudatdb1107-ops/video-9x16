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

# Logging to file se duoc cau hinh sau khi xac dinhh WORK

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

def safe_write_file(file_path: Path, content, is_binary: bool = False, encoding: str = "utf-8"):
    """Ghi đè an toàn xuống đĩa bằng file tạm và os.fsync."""
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        if is_binary:
            with open(tmp_path, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(tmp_path, "w", encoding=encoding) as f:
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
_ap.add_argument("--open-browser", action="store_true", help="Tự động mở trình duyệt sau khi start")
_args, _ = _ap.parse_known_args()
WORK = Path(_args.workspace).resolve() if _args.workspace else Path.cwd()
INDEX = WORK / "index.html"
PORT = 5050
print(f"[editor_server] WORK = {WORK}")

# Logging to file để debug crash (ghi vào workspace dự án để tránh tranh chấp file lock)
import logging
_log_path = WORK / "_editor_debug.log"
logging.basicConfig(
    filename=str(_log_path), filemode="a",
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO, encoding="utf-8"
)
logging.info("=" * 60)
logging.info(f"Editor server starting, PID={os.getpid()}")

# Fields có id="..." trong index.html — Boss edit innerHTML, có thể chứa <em>, <br>
FIELDS = [
    {"scene": 1, "id": "s1-tag",     "label": "Tag (pill)",      "rows": 1},
    {"scene": 1, "id": "s1-title",   "label": "Title (HTML)",    "rows": 2},
    {"scene": 1, "id": "s1-byline",  "label": "Byline",          "rows": 1},
    {"scene": 2, "id": "s2-num",     "label": "Big text",        "rows": 1},
    {"scene": 2, "id": "s2-label",   "label": "Label",           "rows": 2},
    {"scene": 2, "id": "s2-note",    "label": "Note",            "rows": 2},
    {"scene": 3, "id": "s3-heading", "label": "Heading",         "rows": 1},
    {"scene": 3, "id": "s3-card-num-1", "label": "Card 01 #",     "rows": 1},
    {"scene": 3, "id": "s3-card-1",  "label": "Card 01 text",    "rows": 2},
    {"scene": 3, "id": "s3-card-num-2", "label": "Card 02 #",     "rows": 1},
    {"scene": 3, "id": "s3-card-2",  "label": "Card 02 text",    "rows": 2},
    {"scene": 3, "id": "s3-card-num-3", "label": "Card 03 #",     "rows": 1},
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
    """Tìm file voice .wav trong workspace. Ưu tiên latest .wav theo mtime trên đĩa (bỏ qua narration.wav),
    nếu không có thì fallback theo content.json hoặc file bất kỳ."""
    wavs = [p for p in work_dir.glob("*.wav") if p.name != "narration.wav" and not p.name.endswith(".tmp")]
    if wavs:
        # Sắp xếp mtime giảm dần (mới nhất lên đầu)
        wavs = sorted(wavs, key=lambda p: p.stat().st_mtime, reverse=True)
        return wavs[0]
        
    co_file = work_dir / "content.json"
    if co_file.exists():
        try:
            co = json.loads(co_file.read_text(encoding="utf-8"))
            fn = co.get("voice", {}).get("file")
            if fn:
                p = work_dir / fn
                if p.exists(): return p
        except Exception: pass
    all_wavs = sorted(work_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    return all_wavs[0] if all_wavs else None


def _get_absolute_latest_wav(work_dir: Path):
    """Tìm file .wav có mtime mới nhất trên đĩa (bỏ qua narration.wav và các file tmp)."""
    wavs = [p for p in work_dir.glob("*.wav") if p.name != "narration.wav" and not p.name.endswith(".tmp")]
    if not wavs:
        return None
    wavs = sorted(wavs, key=lambda p: p.stat().st_mtime, reverse=True)
    return wavs[0]


def sync_latest_voice(work_dir: Path):
    """Tự động đồng bộ file voice mới nhất trên đĩa vào content.json và index.html."""
    try:
        latest_wav = _get_absolute_latest_wav(work_dir)
        if not latest_wav:
            return
        
        vname = latest_wav.name
        
        # 1. Cập nhật content.json
        co_file = work_dir / "content.json"
        if co_file.exists():
            try:
                co = json.loads(co_file.read_text(encoding="utf-8"))
                if "voice" not in co:
                    co["voice"] = {}
                
                old_file = co["voice"].get("file")
                if old_file != vname:
                    co["voice"]["file"] = vname
                    # Probe duration bằng ffprobe
                    try:
                        out = subprocess.run(
                            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(latest_wav)],
                            capture_output=True, text=True, timeout=5
                        ).stdout.strip()
                        if out:
                            co["voice"]["duration"] = round(float(out), 3)
                    except Exception as ex:
                        print(f"[SYSTEM] [sync] ffprobe duration fail: {ex}")
                    
                    safe_write_file(co_file, json.dumps(co, indent=2, ensure_ascii=False))
                    print(f"[SYSTEM] [sync] Tự động cập nhật content.json voice -> {vname}")
            except Exception as ex_json:
                print(f"[SYSTEM] [sync] Đọc/ghi content.json fail: {ex_json}")
        
        # 2. Cập nhật index.html tag <audio> src
        index_file = work_dir / "index.html"
        if index_file.exists():
            html = index_file.read_text(encoding="utf-8")
            match_aud = re.search(r'(<audio[^>]*\bid="narration"[^>]*\bsrc=")([^"]+)(")', html)
            if match_aud:
                old_src = match_aud.group(2)
                if old_src != vname:
                    html = re.sub(
                        r'(<audio[^>]*\bid="narration"[^>]*\bsrc=")([^"]+)(")',
                        rf'\g<1>{vname}\g<3>',
                        html
                    )
                    safe_write_file(index_file, html)
                    print(f"[SYSTEM] [sync] Tự động cập nhật index.html audio src -> {vname}")
            else:
                match_any_aud = re.search(r'(<audio[^>]*\bsrc=")([^"]+)(")', html)
                if match_any_aud and match_any_aud.group(2) != vname:
                    html = re.sub(
                        r'(<audio[^>]*\bsrc=")([^"]+)(")',
                        rf'\g<1>{vname}\g<3>',
                        html,
                        count=1
                    )
                    safe_write_file(index_file, html)
                    print(f"[SYSTEM] [sync] Tự động cập nhật index.html (any) audio src -> {vname}")
    except Exception as e:
        print(f"[SYSTEM] [sync] sync_latest_voice error: {e}")


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
    # Tự động loại bỏ khoảng trắng thừa trước các dấu câu để tránh bị rớt dòng
    new_inner = re.sub(r'\s+([? !:;,])', r'\1', new_inner)
    pat = rf'(<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)\b[^>]*\bid="{re.escape(elem_id)}"[^>]*>)(.*?)(</(?P=tag)>)'
    return re.sub(pat, lambda m: m.group(1) + new_inner + m.group(4), html, count=1, flags=re.DOTALL)

# ============ CSS property read/write (text-based, đủ cho selectors phẳng) ============
_BASE_PROPS = ["font-size","line-height","letter-spacing","color"]
STYLE_FIELDS = [
    {"scene": 1, "sel": ".s1-tag",      "label": "Tag pill",   "props": _BASE_PROPS},
    {"scene": 1, "sel": ".s1-title",    "label": "Title",      "props": _BASE_PROPS},
    {"scene": 1, "sel": ".s1-title em", "label": "Title Highlight", "props": _BASE_PROPS},
    {"scene": 1, "sel": ".s1-byline",   "label": "Byline",     "props": _BASE_PROPS},
    {"scene": 2, "sel": ".s2-big",      "label": "Big text",   "props": _BASE_PROPS},
    {"scene": 2, "sel": ".s2-label",    "label": "Label",      "props": _BASE_PROPS},
    {"scene": 2, "sel": ".s2-note",     "label": "Note",       "props": _BASE_PROPS},
    {"scene": 3, "sel": ".s3-heading",  "label": "Heading",    "props": _BASE_PROPS},
    {"scene": 3, "sel": ".s3-card-text","label": "Card text",  "props": _BASE_PROPS},
    {"scene": 3, "sel": "em.hl-teal",   "label": "Highlight Teal", "props": _BASE_PROPS},
    {"scene": 3, "sel": "em.hl-orange", "label": "Highlight Orange", "props": _BASE_PROPS},
    {"scene": 3, "sel": ".s3-card-num", "label": "Card #",     "props": _BASE_PROPS},
    {"scene": 4, "sel": ".s4-quote",    "label": "Quote",      "props": _BASE_PROPS},
    {"scene": 4, "sel": ".s4-quote em", "label": "Quote Highlight", "props": _BASE_PROPS},
    {"scene": 5, "sel": ".s5-heading",  "label": "Heading",    "props": _BASE_PROPS},
    {"scene": 5, "sel": ".s5-text",     "label": "Item text",  "props": _BASE_PROPS},
    {"scene": 6, "sel": ".s6-title",    "label": "Title",      "props": _BASE_PROPS},
    {"scene": 6, "sel": ".s6-title em", "label": "Title Highlight", "props": _BASE_PROPS},
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
    if r:
        start, end, body = r
        prop_pat = rf'((?:^|[;\s])){re.escape(prop)}\s*:\s*[^;]+?\s*(;|$)'
        if re.search(prop_pat, body, re.MULTILINE):
            new_body = re.sub(prop_pat, rf'\1{prop}: {value}\2', body, count=1, flags=re.MULTILINE)
        else:
            new_body = body.rstrip() + f"\n  {prop}: {value};\n"
        return html[:start] + html[start:end].replace(body, new_body, 1) + html[end:]
    else:
        # Nếu không tìm thấy rule riêng biệt cho selector này, ta append thêm rule mới vào style block đầu tiên của index.html
        style_match = re.search(r'<style[^>]*>(.*?)</style>', html, re.DOTALL)
        if style_match:
            style_content = style_match.group(1)
            new_rule = f"\n{sel} {{\n  {prop}: {value};\n}}\n"
            updated_style = style_content.rstrip() + new_rule
            start_style = style_match.start(1)
            end_style = style_match.end(1)
            return html[:start_style] + updated_style + html[end_style:]
        return html


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
#stylebar { background: #050505; border-bottom: 1px solid #443311; padding: 12px 20px; display: flex; gap: 8px; align-items: center; flex-wrap: nowrap; }
#stylebar .target { font-size: 12px; color: #D4AF37; font-weight: 700; min-width: 90px; }
#stylebar .grp { display: flex; gap: 4px; align-items: center; background: #050505; padding: 3px 8px; border-radius: 4px; border: 1px solid #443311; }
#stylebar .grp label { font-size: 9px; color: #D4AF37; text-transform: uppercase; }
#stylebar input { background: transparent; color: #fff; border: none; padding: 3px 0 3px 2px; margin: 0; font-family: monospace; font-size: 12px; text-align: left; }
#stylebar input[type="text"] { min-width: 45px; }
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
button.secondary.active {
  background-color: rgba(212, 175, 55, 0.8) !important;
  color: #000 !important;
  border-color: #FFD700 !important;
}
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

.gallery { display: grid; grid-template-columns: repeat(5, 1fr) !important; gap: 12px; align-items: start; }
.thumb { box-sizing: content-box; aspect-ratio: 9 / 16; position: relative; overflow: hidden; border-radius: 10px; border: 2px solid #2a2a30; background: #0a1820; transition: border-color 0.2s; min-width: 0; }
.thumb iframe { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; transform-origin: top left; border: none; pointer-events: auto; }
.thumb.active { border-color: #ff7a2a; }
.thumb-label { position: absolute; top: 6px; left: 6px; z-index: 100; background: rgba(255,122,42,0.95); color: #000; font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 3px; letter-spacing: 0.5px; }
.thumb-inner { width: 1080px; height: 1920px; position: absolute; top: 0; left: 0; transform-origin: top left; overflow: hidden; }
@font-face { font-family: 'UTM Cookies'; src: url('/fonts/UTM-Cookies.ttf') format('truetype'); font-display: swap; }
.thumb-inner .brand-watermark { position: absolute; top: 50px; right: 50px; z-index: 100; font-family: 'UTM Cookies', cursive; font-size: 60px; font-weight: 400; letter-spacing: 1px; color: #ff7a2a; padding: 14px 30px; background: rgba(255,122,42,0.12); border: 2px solid rgba(255,122,42,0.65); border-radius: 999px; text-shadow: 0 0 18px rgba(255,122,42,0.55), 0 2px 0 rgba(0,0,0,0.25); text-transform: uppercase; pointer-events: none; }
.thumb-inner .tagline-footer { position: absolute; bottom: 80px; left: 50%; transform: translateX(-50%); z-index: 100; font-family: 'Inter', sans-serif; font-size: 26px; font-weight: 600; letter-spacing: 4px; color: #6db8b8; text-transform: uppercase; white-space: nowrap; pointer-events: none; text-shadow: 0 0 14px rgba(77,204,204,0.4); }

.thumb-inner em { font-style: normal; font-weight: 700; color: #4dcccc !important; text-shadow: 0 0 20px rgba(77,204,204,0.6) !important; }
.thumb-inner em.hl-teal { color: #4dcccc !important; font-style: normal; font-weight: 700; text-shadow: 0 0 20px rgba(77,204,204,0.6) !important; }
.thumb-inner em.hl-orange { color: #ff7a2a !important; font-style: normal; font-weight: 700; text-shadow: 0 0 20px rgba(255,122,42,0.6) !important; }
.thumb-inner .s1-title em { color: #ff7a2a !important; font-style: normal; font-weight: 900; text-shadow: 0 0 25px rgba(255,122,42,0.75) !important; }
.thumb-inner .s6-title em { color: #ff7a2a !important; font-style: normal; font-weight: 900; text-shadow: 0 0 25px rgba(255,122,42,0.75) !important; }

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
#stylebar:not(.show) .grp,
#stylebar:not(.show) .swatch,
#stylebar:not(.show) button {
  opacity: 0.3;
  pointer-events: none;
  user-select: none;
}
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
    <div class="target" style="display: flex; align-items: center; gap: 12px; min-width: 150px; flex-shrink: 0;">
      <span id="styleTarget">Click vào chữ để chỉnh font/cỡ/giãn dòng</span>
    </div>
    <div class="grp" style="margin-left: auto;"><label>font</label><input type="text" id="styFs" placeholder="130px"><button type="button" class="bump" data-target="styFs" data-delta="-2">−</button><button type="button" class="bump" data-target="styFs" data-delta="2">+</button></div>
    <div class="grp"><label>line</label><input type="text" id="styLh" placeholder="1.5"><button type="button" class="bump" data-target="styLh" data-delta="-0.05">−</button><button type="button" class="bump" data-target="styLh" data-delta="0.05">+</button></div>
    <div class="grp"><label>spacing</label><input type="text" id="styLs" placeholder="-1px"><button type="button" class="bump" data-target="styLs" data-delta="-1">−</button><button type="button" class="bump" data-target="styLs" data-delta="1">+</button></div>
    <div class="grp"><label>gap</label><input type="text" id="styGap" placeholder="48px" style="width:60px"><button type="button" class="bump" data-target="styGap" data-delta="-4">−</button><button type="button" class="bump" data-target="styGap" data-delta="4">+</button></div>
    <div class="grp" id="grpTargetSelect" style="display: flex; gap: 6px; padding: 2px 6px; border-color: #443311; align-items: center; height: 32px;">
      <button type="button" class="secondary" id="btnSelectGeneral" style="padding: 2px 8px; font-size: 10px; border-radius: 4px; text-transform: none;">General</button>
      <button type="button" class="secondary" id="btnSelectHighlight" style="padding: 2px 8px; font-size: 10px; border-radius: 4px; text-transform: none;">Highlight</button>
    </div>
    <div class="grp" style="padding: 2px 6px; gap: 4px; height: 32px; display: flex; align-items: center;">
      <label>color</label>
      <input type="color" id="styColorPick" style="width: 28px; height: 24px; border: none; background: transparent; cursor: pointer; padding: 0;">
      <input type="text" id="styColor" placeholder="#ffffff" style="width: 70px; display: none;">
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
      <div class="s3-card s3-c1"><div class="s3-card-num" ${ED('s3-card-num-1','.s3-card-num')}>${d['s3-card-num-1']||'01'}</div><div class="s3-card-text" ${ED('s3-card-1','.s3-card-text')}>${d['s3-card-1']||''}</div></div>
      <div class="s3-card s3-c2"><div class="s3-card-num" ${ED('s3-card-num-2','.s3-card-num')}>${d['s3-card-num-2']||'02'}</div><div class="s3-card-text" ${ED('s3-card-2','.s3-card-text')}>${d['s3-card-2']||''}</div></div>
      <div class="s3-card s3-c3"><div class="s3-card-num" ${ED('s3-card-num-3','.s3-card-num')}>${d['s3-card-num-3']||'03'}</div><div class="s3-card-text" ${ED('s3-card-3','.s3-card-text')}>${d['s3-card-3']||''}</div></div>
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

// --- UNDO/REDO SYSTEM ---
let UNDO_STACK = [];
let REDO_STACK = [];
const MAX_STACK = 100;
let isTyping = false;
let typingTimer = null;

function snapshotState() {
  return JSON.parse(JSON.stringify({
    data: STATE.data,
    styles: STATE.styles
  }));
}

function pushUndo() {
  const snap = snapshotState();
  if (UNDO_STACK.length > 0) {
    const last = UNDO_STACK[UNDO_STACK.length - 1];
    if (JSON.stringify(last) === JSON.stringify(snap)) {
      return;
    }
  }
  UNDO_STACK.push(snap);
  if (UNDO_STACK.length > MAX_STACK) {
    UNDO_STACK.shift();
  }
  REDO_STACK = [];
  console.log("[USER] Đã lưu snapshot trạng thái vào Undo Stack.");
}

function handleTypingChange() {
  if (!isTyping) {
    pushUndo();
    isTyping = true;
  }
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => {
    isTyping = false;
  }, 800);
}

function undo() {
  if (UNDO_STACK.length === 0) {
    console.log("[SYSTEM] Undo stack rỗng.");
    return;
  }
  const current = snapshotState();
  REDO_STACK.push(current);
  const prev = UNDO_STACK.pop();
  applyState(prev);
  console.log("[SYSTEM] Thực hiện hoàn tác (Undo).");
}

function redo() {
  if (REDO_STACK.length === 0) {
    console.log("[SYSTEM] Redo stack rỗng.");
    return;
  }
  const next = REDO_STACK.pop();
  UNDO_STACK.push(snapshotState());
  applyState(next);
  console.log("[SYSTEM] Thực hiện làm lại (Redo).");
}

function applyState(state) {
  STATE.data = state.data;
  STATE.styles = state.styles;
  
  // Đồng bộ text
  document.querySelectorAll('[data-bind]').forEach(el => {
    const id = el.dataset.bind;
    if (STATE.data[id] !== undefined) {
      const htmlVal = newlineToBr(STATE.data[id]);
      if (el.innerHTML !== htmlVal) {
        el.innerHTML = htmlVal;
      }
    }
  });
  
  // Đồng bộ style
  for (let i = 1; i <= 6; i++) {
    const el = document.getElementById('thumb-' + i);
    if (el) applyStylesToThumb(el);
  }
  
  // Cập nhật StyleBar
  if (CURRENT_SEL) {
    const el = document.querySelector(CURRENT_SEL);
    if (el) {
      const label = CURRENT_SEL.includes('em') ? 'Highlight' : null;
      focusStyleBar(el, CURRENT_SEL, label);
    }
  }
  setStatus('Đã khôi phục trạng thái', 'ok');
}

// Bắt phím nóng toàn cục
window.addEventListener('keydown', (e) => {
  const isZ = e.key.toLowerCase() === 'z';
  const isY = e.key.toLowerCase() === 'y';
  const isH = e.key.toLowerCase() === 'h';
  if ((e.ctrlKey || e.metaKey) && isZ) {
    e.preventDefault();
    undo();
  }
  if ((e.ctrlKey || e.metaKey) && (isY || (e.shiftKey && isZ))) {
    e.preventDefault();
    redo();
  }
  if ((e.ctrlKey || e.metaKey) && isH) {
    const activeEl = document.activeElement;
    if (activeEl && activeEl.hasAttribute('contenteditable')) {
      e.preventDefault();
      const parentSel = activeEl.dataset.sel;
      if (parentSel) {
        let highlightSel = parentSel + " em";
        let hlLabel = parentSel.replace(/^\./, '').toUpperCase() + " Highlight";
        let hasHighlightSupport = true;
        
        if (parentSel === ".s3-card-text") {
          highlightSel = "em.hl-teal";
          hlLabel = "Highlight (Teal)";
          const em = activeEl.querySelector('em');
          if (em && em.classList.contains('hl-orange')) {
            highlightSel = "em.hl-orange";
            hlLabel = "Highlight (Orange)";
          }
        } else if (parentSel === ".s1-title") {
          highlightSel = ".s1-title em";
          hlLabel = "S1 Title Highlight";
        } else if (parentSel === ".s6-title") {
          highlightSel = ".s6-title em";
          hlLabel = "S6 Title Highlight";
        } else if (parentSel === ".s4-quote") {
          highlightSel = ".s4-quote em";
          hlLabel = "S4 Quote Highlight";
        }
        
        if (hasHighlightSupport) {
          const sel = window.getSelection();
          if (sel.rangeCount > 0 && !sel.getRangeAt(0).collapsed) {
            pushUndo();
            let className = '';
            if (highlightSel.includes('.hl-teal')) className = 'hl-teal';
            else if (highlightSel.includes('.hl-orange')) className = 'hl-orange';
            const newEm = makeSelectionHighlight(activeEl, className);
            if (newEm) {
              focusStyleBar(newEm, highlightSel, hlLabel);
            }
          }
        }
      }
    }
  }
});
// ------------------------

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
  const ro = new ResizeObserver(entries => {
    for (let entry of entries) {
      const t = entry.target;
      const w = t.clientWidth;
      const inner = t.querySelector('.thumb-inner');
      if (inner) {
        inner.style.transform = `scale(${w / 1080})`;
      }
    }
  });
  for (let i = 1; i <= 6; i++) {
    const thumb = document.createElement('div');
    thumb.className = 'thumb';
    thumb.innerHTML = `<div class="thumb-label">S${i} · ${SCENE_NAMES[i]}</div><div class="thumb-inner" id="thumb-${i}"><div class="brand-watermark">PK NHI BOOM BOOM</div></div>`;
    g.appendChild(thumb);
    ro.observe(thumb);
  }
  requestAnimationFrame(() => {
    updateAllThumbs();
    wireEditables();
  });
}

function wireEditables() {
  document.querySelectorAll('[data-bind]').forEach(el => {
    if (el._wired) return;
    el._wired = true;
    el.addEventListener('input', () => {
      const id = el.dataset.bind;
      handleTypingChange();
      STATE.data[id] = el.innerHTML;
    });
  });
  let isHighlightClick = false;
  document.querySelectorAll('[data-sel]').forEach(el => {
    if (el._wiredFocus) return;
    el._wiredFocus = true;

    const checkSelection = () => {
      const sel = window.getSelection();
      if (sel.rangeCount > 0) {
        const node = sel.anchorNode;
        if (node) {
          const parent = node.nodeType === 3 ? node.parentElement : node;
          const em = parent.closest('em');
          const parentSel = el.dataset.sel;
          
          if (em && el.contains(em)) {
            let highlightSel = "";
            let label = "";
            if (em.classList.contains('hl-teal')) {
              highlightSel = "em.hl-teal";
              label = `${parentSel} > highlight (Teal)`;
            } else if (em.classList.contains('hl-orange')) {
              highlightSel = "em.hl-orange";
              label = `${parentSel} > highlight (Orange)`;
            } else {
              if (parentSel === ".s1-title") {
                highlightSel = ".s1-title em";
                label = "S1 Title Highlight";
              } else if (parentSel === ".s6-title") {
                highlightSel = ".s6-title em";
                label = "S6 Title Highlight";
              } else if (parentSel === ".s4-quote") {
                highlightSel = ".s4-quote em";
                label = "S4 Quote Highlight";
              } else {
                highlightSel = "em";
                label = `${parentSel} > highlight`;
              }
            }
            focusStyleBar(em, highlightSel, label);
          } else {
            focusStyleBar(el);
          }
        }
      }
    };

    el.addEventListener('mouseup', () => {
      setTimeout(checkSelection, 10);
    });
    el.addEventListener('keyup', () => {
      setTimeout(checkSelection, 10);
    });
    el.addEventListener('focus', () => {
      setTimeout(checkSelection, 50);
    });
  });
}

let CURRENT_SEL = null;
let ACTIVE_EM = null;

function rgbToHex(rgb) {
  if (!rgb) return '#ffffff';
  if (rgb.startsWith('#')) return rgb.length === 7 ? rgb : '#ffffff';
  const m = String(rgb).match(/\d+/g);
  if (!m || m.length < 3) return '#ffffff';
  return '#' + m.slice(0,3).map(n => parseInt(n).toString(16).padStart(2,'0')).join('');
}

function makeSelectionHighlight(parentEl, className = '') {
  const sel = window.getSelection();
  if (!sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  if (range.collapsed) return null;
  if (!parentEl.contains(range.commonAncestorContainer)) return null;

  let container = range.commonAncestorContainer;
  if (container.nodeType === 3) container = container.parentNode;
  const em = container.closest('em');
  if (em) return em;

  const emNode = document.createElement('em');
  if (className && className.trim() && !className.includes(' ')) {
    emNode.classList.add(className.trim());
  }
  try {
    range.surroundContents(emNode);
  } catch (err) {
    const html = range.cloneContents();
    const div = document.createElement('div');
    div.appendChild(html);
    const text = div.textContent;
    const emHTML = className ? `<em class="${className}">${text}</em>` : `<em>${text}</em>`;
    document.execCommand('insertHTML', false, emHTML);
    const ems = parentEl.querySelectorAll('em');
    return ems[ems.length - 1] || null;
  }
  
  parentEl.dispatchEvent(new Event('input'));
  sel.removeAllRanges();
  const newRange = document.createRange();
  newRange.selectNodeContents(emNode);
  sel.addRange(newRange);
  return emNode;
}

function makeSelectionGeneral(parentEl) {
  const sel = window.getSelection();
  if (!sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  if (range.collapsed) return;
  if (!parentEl.contains(range.commonAncestorContainer)) return;

  let container = range.commonAncestorContainer;
  if (container.nodeType === 3) container = container.parentNode;
  const em = container.closest('em');
  if (em) {
    const textNode = document.createTextNode(em.textContent);
    const parent = em.parentNode;
    parent.replaceChild(textNode, em);

    parentEl.dispatchEvent(new Event('input'));
    sel.removeAllRanges();
    const newRange = document.createRange();
    newRange.selectNodeContents(textNode);
    sel.addRange(newRange);
  }
}

function focusStyleBar(el, customSel = null, customLabel = null) {
  const sel = customSel || el.dataset.sel;
  if (!sel) return;
  CURRENT_SEL = sel;
  ACTIVE_EM = (el && el.tagName === 'EM') ? el : null;
  document.getElementById('stylebar').classList.add('show');
  document.getElementById('styleTarget').textContent = '🎨 ' + (customLabel || sel);
  const cur = STATE.styles[sel] || {};
  const cs = getComputedStyle(el);
  const getPropVal = (prop, styleProp) => {
    if (ACTIVE_EM) {
      return ACTIVE_EM.style.getPropertyValue(prop) || cs[styleProp];
    }
    return cur[prop] || cs[styleProp];
  };
  document.getElementById('styFs').value = formatForDisplay('font-size',     getPropVal('font-size', 'fontSize'));
  document.getElementById('styLh').value = formatForDisplay('line-height',   getPropVal('line-height', 'lineHeight'));
  document.getElementById('styLs').value = formatForDisplay('letter-spacing',getPropVal('letter-spacing', 'letterSpacing'));
  ['styFs','styLh','styLs'].forEach(id => autosizeInput(document.getElementById(id)));
  const colorVal = ACTIVE_EM ? (ACTIVE_EM.style.getPropertyValue('color') || cs.color) : (cur['color'] || cs.color);
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

  // Cập nhật active/disabled state cho group chọn nhanh General/Highlight sát bên trái Khung color
  const grpSelect = document.getElementById('grpTargetSelect');
  if (grpSelect) {
    const parentEl = el.closest('[data-sel]');
    if (parentEl) {
      const parentSel = parentEl.dataset.sel;
      const btnGeneral = document.getElementById('btnSelectGeneral');
      const btnHighlight = document.getElementById('btnSelectHighlight');
      
      // Tất cả mọi phần tử gõ chữ đều hỗ trợ Highlight!
      let hasHighlightSupport = true;
      let highlightSel = parentSel + " em";
      let hlLabel = parentSel.replace(/^\./, '').toUpperCase() + " Highlight";
      
      if (parentSel === ".s3-card-text") {
        highlightSel = "em.hl-teal";
        hlLabel = "Highlight (Teal)";
        const em = parentEl.querySelector('em');
        if (em && em.classList.contains('hl-orange')) {
          highlightSel = "em.hl-orange";
          hlLabel = "Highlight (Orange)";
        }
      } else if (parentSel === ".s1-title") {
        highlightSel = ".s1-title em";
        hlLabel = "S1 Title Highlight";
      } else if (parentSel === ".s6-title") {
        highlightSel = ".s6-title em";
        hlLabel = "S6 Title Highlight";
      } else if (parentSel === ".s4-quote") {
        highlightSel = ".s4-quote em";
        hlLabel = "S4 Quote Highlight";
      }

      if (hasHighlightSupport) {
        btnGeneral.disabled = false;
        btnGeneral.style.opacity = "1";
        btnGeneral.style.cursor = "pointer";
        btnHighlight.disabled = false;
        btnHighlight.style.opacity = "1";
        btnHighlight.style.cursor = "pointer";
        
        btnGeneral.onmousedown = (e) => {
          e.stopPropagation();
          e.preventDefault();
          const sel = window.getSelection();
          if (sel.rangeCount > 0 && !sel.getRangeAt(0).collapsed) {
            pushUndo();
            makeSelectionGeneral(parentEl);
          }
          focusStyleBar(parentEl);
        };
        btnHighlight.onmousedown = (e) => {
          e.stopPropagation();
          e.preventDefault();
          const sel = window.getSelection();
          let targetEl = parentEl.querySelector('em') || parentEl;
          if (sel.rangeCount > 0 && !sel.getRangeAt(0).collapsed) {
            pushUndo();
            let className = '';
            if (highlightSel.includes('.hl-teal')) className = 'hl-teal';
            else if (highlightSel.includes('.hl-orange')) className = 'hl-orange';
            const newEm = makeSelectionHighlight(parentEl, className);
            if (newEm) targetEl = newEm;
          }
          focusStyleBar(targetEl, highlightSel, hlLabel);
        };
        
        if (CURRENT_SEL === parentSel) {
          btnGeneral.classList.add('active');
          btnHighlight.classList.remove('active');
        } else {
          btnGeneral.classList.remove('active');
          btnHighlight.classList.add('active');
        }
      } else {
        // Không hỗ trợ Highlight -> Disable nút Highlight, chọn General làm active
        btnGeneral.disabled = false;
        btnGeneral.style.opacity = "1";
        btnGeneral.style.cursor = "pointer";
        btnGeneral.classList.add('active');
        
        btnHighlight.disabled = true;
        btnHighlight.style.opacity = "0.3";
        btnHighlight.style.cursor = "not-allowed";
        btnHighlight.classList.remove('active');
        
        btnGeneral.onmousedown = (e) => {
          e.stopPropagation();
          e.preventDefault();
          focusStyleBar(parentEl);
        };
        btnHighlight.onmousedown = null;
      }
    }
  }
}

function applyStyleProp(prop, value) {
  if (!CURRENT_SEL) return;
  const v = formatForApply(prop, value);
  if (ACTIVE_EM) {
    ACTIVE_EM.style.setProperty(prop, v, 'important');
    const parentEl = ACTIVE_EM.closest('[data-bind]');
    if (parentEl) {
      STATE.data[parentEl.dataset.bind] = parentEl.innerHTML;
    }
  } else {
    STATE.styles[CURRENT_SEL] = STATE.styles[CURRENT_SEL] || {};
    STATE.styles[CURRENT_SEL][prop] = v;
    document.querySelectorAll(CURRENT_SEL).forEach(el => {
      const priority = CURRENT_SEL.includes('em') ? 'important' : '';
      el.style.setProperty(prop, v, priority);
    });
  }
}

// Auto-size: đo content thật bằng span ẩn → set width input chính xác (zero trắng cuối).
function autosizeInput(inp) {
  if (!inp) return;
  const cs = getComputedStyle(inp);
  const span = document.createElement('span');
  span.style.fontSize = cs.fontSize;
  span.style.fontFamily = cs.fontFamily;
  span.style.fontWeight = cs.fontWeight;
  span.style.fontStyle = cs.fontStyle;
  span.style.letterSpacing = cs.letterSpacing;
  span.style.visibility = 'hidden';
  span.style.position = 'absolute';
  span.style.whiteSpace = 'pre';
  
  span.textContent = inp.value || inp.placeholder || '0';
  document.body.appendChild(span);
  const w = span.offsetWidth;
  span.remove();
  inp.style.width = Math.max(25, w + 4) + 'px';
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
    inp.addEventListener('input', () => { 
      handleTypingChange();
      applyStyleProp(map[id], inp.value); 
      autosizeInput(inp); 
    });
  }
  // group-gap: apply cho .s3-cards + .s5-list (lưu state vào cả 2 selector)
  const gapInput = document.getElementById('styGap');
  if (gapInput) {
    gapInput.addEventListener('input', () => {
      handleTypingChange();
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
    handleTypingChange();
    document.getElementById('styColor').value = pick.value;
    applyStyleProp('color', pick.value);
  });
  // Swatch buttons
  document.querySelectorAll('.swatch').forEach(btn => {
    btn.addEventListener('click', () => {
      pushUndo();
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
      pushUndo();
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
        if (v && String(v).trim()) {
          // Bỏ qua ghi đè màu trắng mặc định lên thẻ em highlight để tránh mất màu Teal/Orange trên Editor
          if (sel.includes('em') && p === 'color' && (v === '#ffffff' || v === 'rgb(255, 255, 255)')) {
            continue;
          }
          if (el.style.getPropertyValue(p)) {
            continue;
          }
          const priority = sel.includes('em') ? 'important' : '';
          el.style.setProperty(p, v, priority);
        }
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
  try {
    const r = await fetch('/load');
    const j = await r.json();
    STATE = j;
    // Convert <br> → \n cho textarea hiển thị xuống dòng đẹp
    for (const k in STATE.data) STATE.data[k] = brToNewline(STATE.data[k]);
    renderForm();
    renderGallery();
    await refreshVoiceInfo();
    setStatus('Ready', 'ok');
  } catch (e) {
    console.error('[reload] fail:', e);
    setStatus('Load failed', 'err');
  }
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

  // Click outside to clear stylebar selection
  document.addEventListener('mousedown', (e) => {
    if (!e.target.closest('#stylebar') && !e.target.closest('[contenteditable="true"]')) {
      document.getElementById('stylebar').classList.remove('show');
      document.getElementById('styleTarget').textContent = 'Click vào chữ để chỉnh font/cỡ/giãn dòng';
      CURRENT_SEL = null;
      ACTIVE_EM = null;
    }
  });

wireStyleBar();
reload();

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
  } catch(e) {
    console.error('[refreshVoiceInfo] fail:', e);
  }
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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            sync_latest_voice(WORK)
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
                # Map #root thành .thumb-inner để background gradient và kích thước được áp dụng chính xác cho preview
                workspace_css = re.sub(r'#root\b', '.thumb-inner', workspace_css)
                # Scope CSS vào .thumb-inner để không leak UI editor
                # (wrap selectors bắt đầu bằng dấu chấm hoặc #root đã map, bỏ qua @font-face/@import/etc)
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
            sync_latest_voice(WORK)
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
            sync_latest_voice(WORK)
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
            sync_latest_voice(WORK)
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
            
            # Hàm viết hoa thông minh cho title HTML (giữ nguyên tag <br>, <em>)
            def uppercase_html(text):
                if not text: return ""
                parts = re.split(r'(<[^>]+>)', text)
                for i in range(len(parts)):
                    if not parts[i].startswith('<'):
                        parts[i] = parts[i].upper()
                res = "".join(parts)
                # Restore all HTML entities (e.g., &NBSP;, &AMP;, &QUOT;) to lowercase to prevent rendering as raw text
                res = re.sub(r'&[A-Z0-9]+;', lambda m: m.group(0).lower(), res)
                return res
            
            # Tự động viết hoa title Scene 1 & 6
            if "s1-title" in data:
                data["s1-title"] = uppercase_html(data["s1-title"])
            if "s6-title" in data:
                data["s6-title"] = uppercase_html(data["s6-title"])
                
            # Ép mặc định line-height của title Scene 1 & 6 khít hơn để không bị giãn
            if ".s1-title" not in styles: styles[".s1-title"] = {}
            if "line-height" not in styles[".s1-title"] or styles[".s1-title"]["line-height"] == "1.7" or not styles[".s1-title"]["line-height"]:
                styles[".s1-title"]["line-height"] = "1.25"
                
            if ".s6-title" not in styles: styles[".s6-title"] = {}
            if "line-height" not in styles[".s6-title"] or styles[".s6-title"]["line-height"] == "1.65" or not styles[".s6-title"]["line-height"]:
                styles[".s6-title"]["line-height"] = "1.25"

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
                    if "s3-card-num-1" in data: co["scenes"]["s3"]["cards"][0]["num"] = clean_boom_boom(data["s3-card-num-1"])
                    if "s3-card-1" in data: co["scenes"]["s3"]["cards"][0]["text"] = clean_boom_boom(data["s3-card-1"])
                    if "s3-card-num-2" in data: co["scenes"]["s3"]["cards"][1]["num"] = clean_boom_boom(data["s3-card-num-2"])
                    if "s3-card-2" in data: co["scenes"]["s3"]["cards"][1]["text"] = clean_boom_boom(data["s3-card-2"])
                    if "s3-card-num-3" in data: co["scenes"]["s3"]["cards"][2]["num"] = clean_boom_boom(data["s3-card-num-3"])
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
                    
                    co["styles"] = styles
                    safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
                    print(f"[SYSTEM] [save] Đồng bộ thành công content.json cho {WORK.name}")
                except Exception as e:
                    print(f"[SYSTEM] [save] Lỗi đồng bộ content.json: {e}")
            
            self._send(200, "application/json", '{"ok":true}')
        elif path == "/save-duration":
            # P2.7 — Đổi voice.duration trong content.json (pipeline sẽ ép MP4 khớp số này)
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body) if body else {}
                new_dur = float(payload.get("duration", 0))
                if new_dur < 1 or new_dur > 600:
                    self._send(400, "application/json", json.dumps({"ok": False, "error": "duration phải 1-600s"}))
                    return
                co_file = WORK / "content.json"
                if not co_file.exists():
                    self._send(404, "application/json", json.dumps({"ok": False, "error": "content.json không tồn tại"}))
                    return
                co = json.loads(co_file.read_text(encoding="utf-8"))
                if "voice" not in co: co["voice"] = {}
                co["voice"]["duration"] = round(new_dur, 2)
                co_file.write_text(json.dumps(co, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[USER] [save-duration] {WORK.name} → voice.duration = {new_dur}s")
                self._send(200, "application/json", json.dumps({"ok": True, "duration": new_dur}))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))
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
            
            # Tự động dò tìm file voice gốc và đồng bộ cấu hình trong index.html + content.json (giữ nguyên tên file của Sếp)
            try:
                wav_file = _find_voice_wav(WORK)
                if wav_file and wav_file.exists():
                    vname = wav_file.name
                    # 1. Đồng bộ content.json
                    co_file = WORK / "content.json"
                    if co_file.exists():
                        co = json.loads(co_file.read_text(encoding="utf-8"))
                        if co.get("voice", {}).get("file") != vname:
                            if "voice" not in co: co["voice"] = {}
                            co["voice"]["file"] = vname
                            safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
                            print(f"[SYSTEM] [render] Auto sync content.json voice.file = {vname}")
                    
                    # 2. Đồng bộ index.html tag <audio> src
                    if INDEX.exists():
                        html = INDEX.read_text(encoding="utf-8")
                        # Tìm src hiện tại của audio tag narration
                        match_aud = re.search(r'(<audio[^>]*\bid="narration"[^>]*\bsrc=")([^"]+)(")', html)
                        if match_aud and match_aud.group(2) != vname:
                            html = re.sub(
                                r'(<audio[^>]*\bid="narration"[^>]*\bsrc=")([^"]+)(")',
                                rf'\g<1>{vname}\g<3>',
                                html
                            )
                            safe_write_file(INDEX, html)
                            print(f"[SYSTEM] [render] Auto sync index.html audio src = {vname}")
            except Exception as e:
                print(f"[SYSTEM] [render] Auto sync voice file failed: {e}")

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
            if Handler.render_status["running"]:
                self._send(409, "application/json", '{"ok":false,"msg":"rendering"}')
                return
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
    # Tự động kill tiến trình cha wscript.exe (nếu khởi chạy từ VBScript) để tránh VBScript mở tab trùng lặp
    try:
        import psutil
        import os
        parent = psutil.Process(os.getpid()).parent()
        if parent and "wscript" in parent.name().lower():
            print(f"[SYSTEM] Phát hiện tiến trình cha là VBScript ({parent.name()}), thực hiện kill để tránh mở 2 tab.")
            parent.kill()
    except Exception as e_kill:
        print(f"[SYSTEM] Lỗi kill tiến trình cha: {e_kill}")

    import socket
    import urllib.request
    import json
    import webbrowser
    
    def is_port_in_use(p):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', p)) == 0

    def update_vbs_file(vbs_path: Path, p_num: int):
        if not vbs_path.exists():
            return
        try:
            content = vbs_path.read_text(encoding="utf-8-sig")
            run_line_match = re.search(r'(objShell\.Run\s+""?.*editor_server\.py.*workspace.*)', content, re.IGNORECASE)
            if not run_line_match:
                return
            run_line = run_line_match.group(1).strip()
            # Loại bỏ hoàn toàn flag --open-browser
            run_line = run_line.replace(" --open-browser", "")
            # Đổi comment sang không dấu để tránh lỗi encoding trên Windows Script Host
            new_vbs = f"""' Mo Editor cho workspace nay
Set objFSO = CreateObject("Scripting.FileSystemObject")
strDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = strDir
{run_line}
"""
            safe_write_file(vbs_path, new_vbs, encoding="utf-16")
            print(f"[SYSTEM] Đã nâng cấp MO_EDITOR.vbs thành phiên bản tinh gọn không mở trình duyệt")
        except Exception as e_vbs:
            print(f"[SYSTEM] Lỗi nâng cấp MO_EDITOR.vbs: {e_vbs}")

    # Đọc cổng đã lưu trước đó của dự án
    port_file = WORK / ".editor_port"
    target_port = 5050
    if port_file.exists():
        try:
            target_port = int(port_file.read_text(encoding="utf-8").strip())
        except Exception:
            target_port = 5050

    # Nếu cổng này đang bị chiếm dụng
    if is_port_in_use(target_port):
        try:
            # Kiểm tra xem có phải đúng là server của dự án này đang chạy hay không
            with urllib.request.urlopen(f"http://127.0.0.1:{target_port}/workspace-info", timeout=1.0) as response:
                info = json.loads(response.read().decode("utf-8"))
                if Path(info.get("workspace")).resolve() == WORK.resolve():
                    print(f"[SYSTEM] Dự án này đã có server chạy ở cổng {target_port}. Tái sử dụng server cũ.")
                    webbrowser.open(f"http://localhost:{target_port}/")
                    sys.exit(0)
        except Exception:
            pass
        
        # Nếu là dự án khác đang chạy chiếm cổng đó, ta tự tìm cổng rỗi tiếp theo starting from 5050
        target_port = 5050
        while is_port_in_use(target_port):
            target_port += 1

    PORT = target_port
    
    # Ghi cổng vào file vĩnh viễn
    try:
        safe_write_file(port_file, str(PORT))
        print(f"[SYSTEM] Đã ghi cổng {PORT} vào file .editor_port")
    except Exception as e_port:
        print(f"[SYSTEM] Lỗi ghi file .editor_port: {e_port}")

    # Nâng cấp file VBScript trong thư mục làm việc của dự án
    update_vbs_file(WORK / "MO_EDITOR.vbs", PORT)

    print(f"\nEditor: http://localhost:{PORT}/\nWorkspace: {WORK}\nPress Ctrl+C to stop.\n")
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        
        # Tự động mở trình duyệt khi khởi chạy chủ động
        webbrowser.open(f"http://localhost:{PORT}/")
                
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
