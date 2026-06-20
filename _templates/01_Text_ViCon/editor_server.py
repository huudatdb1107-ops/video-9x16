"""Editor server — Boss edit text trong index.html qua web UI + 1 click Gen Video.

Chạy: python editor_server.py → mở browser http://localhost:5050/
Workflow:
  1. LEFT panel: gallery grid 6 scene thu nhỏ (live update khi gõ)
  2. RIGHT panel: form textarea cho từng text element
  3. Boss gõ → preview cập nhật ngay
  4. Save → ghi index.html
  5. Gen Video → render mp4 → ra `out_latest.mp4`
"""
import json, re, subprocess, threading, sys, urllib.parse, time, os, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def get_path_from_query(url_str):
    query = urllib.parse.urlparse(url_str).query
    params = urllib.parse.parse_qs(query)
    path_str = params.get("path", [None])[0]
    if path_str:
        return Path(path_str).resolve()
    return WORK

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
<html lang="vi"><head><meta charset="UTF-8"><title>B.SIMPLE Workspace Editor — Multi-Project Trạm Biên Tập</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; }
body { margin: 0; background: #0d0d0d; color: #eee; font-family: "Outfit", sans-serif; height: 100vh; overflow: hidden; }
#app { display: grid; grid-template-rows: 64px auto 1fr; height: 100vh; }
#topbar { display: flex; align-items: center; padding: 0 20px; background: #0d0d0d; border-bottom: 1px solid #443311; gap: 16px; }
#topbar h2 { margin: 0; font-size: 16px; color: #D4AF37; min-width: 0; font-weight: 800; letter-spacing: 1.5px; display: flex; align-items: center; gap: 8px; text-transform: uppercase; }
#topbar button { flex-shrink: 0; }
#main { padding: 20px; overflow-y: auto; background: #0d0d0d; }
#stylebar { background: #050505; border-bottom: 1px solid #443311; padding: 12px 20px; display: flex; gap: 8px; align-items: center; flex-wrap: nowrap; display: none; }
#stylebar.show { display: flex; }
#stylebar .target { font-size: 12px; color: #D4AF37; font-weight: 700; min-width: 90px; }
#stylebar .grp { display: flex; gap: 4px; align-items: center; background: #050505; padding: 3px 8px; border-radius: 4px; border: 1px solid #443311; }
#stylebar .grp label { font-size: 9px; color: #D4AF37; text-transform: uppercase; }
#stylebar input { background: transparent; color: #fff; border: none; padding: 3px 0 3px 2px; margin: 0; font-family: monospace; font-size: 12px; text-align: left; }
#stylebar input[type="text"] { min-width: 45px; }
#stylebar .bump { cursor: pointer; user-select: none; touch-action: manipulation; }
#stylebar .bump:active { transform: scale(0.95); }
#stylebar input::-webkit-outer-spin-button, #stylebar input::-webkit-inner-spin-button { display: none; }
#stylebar input:focus { outline: none; }
.swatch { width: 18px; height: 18px; border-radius: 3px; border: 1.5px solid #443311; cursor: pointer; padding: 0; transition: transform 0.1s, border-color 0.1s; }
.swatch:hover { transform: scale(1.2); border-color: #D4AF37; }
.bump { width: 20px; height: 20px; background: transparent; color: #D4AF37; border: 1px solid #443311; border-radius: 3px; cursor: pointer; font-size: 13px; font-weight: 700; line-height: 1; padding: 0; margin-left: 4px; }
.bump:hover { background-color: rgba(212, 175, 55, 0.5); color: #000; border-color: #FFD700; }

/* Golden Obsidian Buttons */
button { background: transparent; color: #D4AF37; border: 1px solid #D4AF37; padding: 8px 16px; border-radius: 4px; font-weight: 700; cursor: pointer; font-size: 12px; letter-spacing: 0.5px; transition: all 0.2s ease-in-out; text-transform: uppercase; }
button:hover { background-color: rgba(212, 175, 55, 0.5); color: #000; border: 1px solid #FFD700; }
button.secondary { background: transparent; color: #888; border: 1px solid #443311; text-transform: none; }
button.secondary:hover { background-color: rgba(255, 255, 255, 0.05); color: #fff; border-color: #888; }
button.secondary.active { background-color: rgba(212, 175, 55, 0.8) !important; color: #000 !important; border-color: #FFD700 !important; }
button:disabled { opacity: 0.5; cursor: wait; }

button.btn-visual { background: transparent; color: #ff9900; border-color: #ff9900; }
button.btn-visual:hover { background: rgba(255, 153, 0, 0.5); color: #000; border-color: #ffaa00; }
button.btn-voice { background: transparent; color: #00ccff; border-color: #00ccff; }
button.btn-voice:hover { background: rgba(0, 204, 255, 0.5); color: #000; border-color: #33ddff; }
button.btn-video { background: transparent; color: #00ff99; border-color: #00ff99; }
button.btn-video:hover { background: rgba(0, 255, 153, 0.5); color: #000; border-color: #33ffaa; }

/* Progress */
#batchProgressContainer { display: none; align-items: center; gap: 12px; background: #050505; border: 1px solid #443311; padding: 6px 12px; border-radius: 4px; font-size: 12px; color: #ffcc00; margin-left: auto; max-width: 450px; }
.progress-bar-bg { width: 100px; height: 8px; background: #221100; border-radius: 4px; overflow: hidden; border: 1px solid #443311; }
.progress-bar-fg { height: 100%; background: #D4AF37; width: 0%; transition: width 0.3s; }

/* Table styles */
table { width: 100%; border-collapse: collapse; background: #0d0d0d; border: 1px solid #443311; font-size: 12px; }
th, td { border: 1px solid #443311; padding: 8px 10px; text-align: left; vertical-align: middle; }
th { background: #050505; color: #D4AF37; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }
tr:hover { background: #121212; }
tr.published-row { opacity: 0.65; }

/* Custom Checkbox */
input[type="checkbox"] { accent-color: #D4AF37; cursor: pointer; width: 16px; height: 16px; }

/* Scene frame */
.scene-col { width: 98px; text-align: center; }
.iframe-container { position: relative; width: 90px; height: 160px; background: #050505; border: 1px solid #443311; border-radius: 4px; overflow: hidden; margin: 0 auto; }
.iframe-container iframe { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; transform: scale(0.083333); transform-origin: top left; border: none; }
.iframe-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: transparent; cursor: pointer; z-index: 5; }
.iframe-container.editing { border-color: #D4AF37; box-shadow: 0 0 10px rgba(212, 175, 55, 0.4); }
.iframe-container.editing .iframe-overlay { display: none; }

/* Mini Player */
.audio-mini { width: 100%; max-width: 140px; height: 28px; background: transparent; outline: none; }
.video-mini { width: 90px; height: 160px; object-fit: cover; border-radius: 4px; border: 1px solid #443311; background: #000; cursor: pointer; }

/* Warning pulse */
@keyframes pulse-red {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}
.warning-badge { display: flex; align-items: center; gap: 4px; color: #ff3333; font-size: 9px; font-weight: 800; animation: pulse-red 1.2s infinite; margin-top: 6px; }
.btn-quick { background: transparent; color: #D4AF37; border: 1px solid #D4AF37; font-size: 9px; padding: 2px 6px; border-radius: 3px; cursor: pointer; font-weight: 700; margin-top: 4px; display: inline-block; text-transform: uppercase; }
.btn-quick:hover { background: #D4AF37; color: #000; }

.status-badge { display: inline-block; padding: 3px 8px; border-radius: 99px; font-size: 10px; font-weight: 700; text-transform: uppercase; }
.status-badge.draft { background: #222; color: #aaa; border: 1px solid #444; }
.status-badge.approved { background: #221100; color: #ff9900; border: 1px solid #663300; }
.status-badge.published { background: #002211; color: #00ff99; border: 1px solid #006633; }

#renderLog { background: #050505; padding: 12px; border-radius: 4px; font-family: monospace; font-size: 11px; white-space: pre-wrap; color: #aaa; max-height: 180px; overflow-y: auto; margin-top: 15px; border: 1px solid #443311; }
</style>
</head><body>
<div id="app">
  <div id="topbar">
    <h2><span style="font-size: 20px;">📁</span> Multi-Project Workspace: <span id="workspaceTitle">Loading...</span></h2>
    
    <!-- Progress bar chạy ngầm -->
    <div id="batchProgressContainer">
      <span id="batchProgressText">⚙️ Đang xử lý...</span>
      <div class="progress-bar-bg">
        <div class="progress-bar-fg" id="batchProgressFg"></div>
      </div>
    </div>
    
    <div style="display: flex; gap: 10px; align-items: center; margin-left: auto;">
      <label style="font-size: 11px; color: #D4AF37; display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none;">
        <input type="checkbox" id="hidePublishedCheckbox" checked onchange="toggleHidePublished()"> Ẩn bài đã Post
      </label>
      <button class="secondary" onclick="reload()">🔄 Reload</button>
      <button class="btn-visual" onclick="triggerBatch('visual')">🎨 Gen Visual</button>
      <button class="btn-voice" onclick="triggerBatch('voice')">🎙️ Gen Voice</button>
      <button class="btn-video" onclick="triggerBatch('video')">🎬 Gen Video</button>
    </div>
  </div>

  <div id="stylebar">
    <div class="target" style="display: flex; align-items: center; gap: 12px; min-width: 150px; flex-shrink: 0;">
      <span id="styleTarget">Click vào chữ để chỉnh font/cỡ/giãn dòng</span>
    </div>
    <div class="grp" style="margin-left: auto;"><label>font</label><input type="text" id="styFs" placeholder="130px"><button type="button" class="bump" data-target="styFs" data-delta="-2">−</button><button type="button" class="bump" data-target="styFs" data-delta="2">+</button></div>
    <div class="grp"><label>line</label><input type="text" id="styLh" placeholder="1.25"><button type="button" class="bump" data-target="styLh" data-delta="-0.05">−</button><button type="button" class="bump" data-target="styLh" data-delta="0.05">+</button></div>
    <div class="grp"><label>spacing</label><input type="text" id="styLs" placeholder="-1px"><button type="button" class="bump" data-target="styLs" data-delta="-1">−</button><button type="button" class="bump" data-target="styLs" data-delta="1">+</button></div>
    <div class="grp"><label>gap</label><input type="text" id="styGap" placeholder="48px" style="width:60px"><button type="button" class="bump" data-target="styGap" data-delta="-4">−</button><button type="button" class="bump" data-target="styGap" data-delta="4">+</button></div>
    <div class="grp" id="grpTargetSelect" style="display: flex; gap: 6px; padding: 2px 6px; border-color: #443311; align-items: center; height: 32px;">
      <button type="button" class="secondary" id="btnSelectGeneral" style="padding: 2px 8px; font-size: 10px; border-radius: 4px; text-transform: none;">General</button>
      <button type="button" class="secondary" id="btnSelectHighlight" style="padding: 2px 8px; font-size: 10px; border-radius: 4px; text-transform: none;">Highlight</button>
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
    <table id="projectTable">
      <thead>
        <tr>
          <th style="width: 40px; text-align: center;"><input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll()"></th>
          <th style="width: 40px; text-align: center;">ID</th>
          <th style="width: 200px;">Thư mục</th>
          <th class="scene-col">S1 (HOOK)</th>
          <th class="scene-col">S2 (STAT)</th>
          <th class="scene-col">S3 (CARDS)</th>
          <th class="scene-col">S4 (QUOTE)</th>
          <th class="scene-col">S5 (LIST)</th>
          <th class="scene-col">S6 (CTA)</th>
          <th style="width: 150px;">Voice</th>
          <th style="width: 110px; text-align: center;">Video Preview</th>
          <th style="width: 100px; text-align: center;">Trạng thái</th>
        </tr>
      </thead>
      <tbody id="projectTableBody">
        <!-- Load dynamically -->
      </tbody>
    </table>
    
    <div id="renderLog">Workspace logs ready...</div>
  </div>
</div>

<script>
let PROJECTS = [];
let CURRENT_FOLDER = null;
let CURRENT_SEL = null;
let FOCUS_IFRAME = null;

// --- UNDO/REDO & Styles support ---
function rgbToHex(rgb) {
  if (!rgb) return '#ffffff';
  if (rgb.startsWith('#')) return rgb.length === 7 ? rgb : '#ffffff';
  const m = String(rgb).match(/\d+/g);
  if (!m || m.length < 3) return '#ffffff';
  return '#' + m.slice(0,3).map(n => parseInt(n).toString(16).padStart(2,'0')).join('');
}

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

import queue
import shutil

ROOT = Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
SKILL_DIR = Path(__file__).parent.parent.parent
SCRIPT_DIR = SKILL_DIR / "scripts"
TEMPLATES = SKILL_DIR / "_templates"
PY = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"

PAGE_PROFILES = {}
try:
    _profile_path = SKILL_DIR / "video_brand_profiles.json"
    if _profile_path.exists():
        PAGE_PROFILES = json.loads(_profile_path.read_text(encoding="utf-8"))
except Exception as _e:
    print(f"  ⚠ Lỗi load video_brand_profiles.json: {_e}")

BATCH_QUEUE = queue.Queue()
BATCH_STATUS = {
    "running": False,
    "current_path": "",
    "current_action": "",
    "current_index": 0,
    "total": 0,
    "log": [],
    "progress_pct": 0
}

def ensure_vbs(out_dir: Path, template: str):
    vbs_path = out_dir / "MO_EDITOR.vbs"
    editor_py = str((TEMPLATES / template / "editor_server.py").resolve())
    vbs_content = (
        "' Mo Editor cho workspace nay\r\n"
        "Set objFSO = CreateObject(\"Scripting.FileSystemObject\")\r\n"
        "strDir = objFSO.GetParentFolderName(WScript.ScriptFullName)\r\n"
        "Set objShell = CreateObject(\"WScript.Shell\")\r\n"
        "objShell.CurrentDirectory = strDir\r\n\r\n"
        f"objShell.Run \"\"\"{PY}\"\" \"\"{editor_py}\"\" --workspace \"\"\" & strDir & \"\"\"\", 0, False\r\n"
    )
    vbs_path.write_text(vbs_content, encoding="utf-16")

def transcribe(wav_file: Path, out_file: Path):
    code = f"""
import sys, json, pathlib
sys.stdout.reconfigure(encoding='utf-8')
from faster_whisper import WhisperModel
try:
    model = WhisperModel("base", device="cuda", compute_type="float16")
    segments, info = model.transcribe(r"{wav_file}", language="vi", beam_size=5, word_timestamps=False)
    segments = list(segments)
    print("✓ Sử dụng GPU (cuda) để transcribe")
except Exception as e:
    print(f"⚠ Lỗi GPU (cuda) hoặc OOM: {{e}}. Fallback về CPU.")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(r"{wav_file}", language="vi", beam_size=5, word_timestamps=False)
    segments = list(segments)
out = []
for seg in segments:
    out.append({{"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()}})
result = {{"duration": round(info.duration, 3), "segments": out}}
pathlib.Path(r"{out_file}").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
"""
    return subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8")

def update_timeline_from_transcript(out_dir: Path, wav_filename: str = "narration.wav"):
    import re
    from difflib import SequenceMatcher
    
    def clean_text(text):
        if not text: return ""
        return re.sub(r'[^\w\s]', '', str(text).lower()).strip()
        
    def clean_html(text):
        if not text: return ""
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&nbsp;', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'&amp;', '&', text, flags=re.IGNORECASE)
        return ' '.join(text.split())

    tr_file = out_dir / "transcript.json"
    co_file = out_dir / "content.json"
    sc_file = out_dir / "script.txt"
    
    if not tr_file.exists() or not co_file.exists():
        return
        
    tr = json.loads(tr_file.read_text(encoding="utf-8"))
    co = json.loads(co_file.read_text(encoding="utf-8"))
    duration = tr.get("duration", 60.0)
    segments = tr.get("segments", [])
    sids_sorted = sorted(co.get("scenes", {}).keys())
    n_scenes = len(sids_sorted)
    if n_scenes < 2: return

    lines = []
    if sc_file.exists():
        lines = [l.strip() for l in sc_file.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        
    if len(lines) != n_scenes:
        lines = []
        for sid in sids_sorted:
            scene = co["scenes"][sid]
            scene_text = ""
            if sid == "s1":
                scene_text = scene.get("title", "")
            elif sid == "s2":
                scene_text = scene.get("note", "") or scene.get("label", "")
            elif sid == "s3":
                scene_text = scene.get("heading", "") + " " + " ".join(c.get("text", "") for c in scene.get("cards", []))
            elif sid == "s4":
                scene_text = scene.get("quote_html", "")
            elif sid == "s5":
                scene_text = scene.get("heading", "") + " " + " ".join(i.get("text", "") for i in scene.get("items", []))
            elif sid == "s6":
                scene_text = scene.get("sub", "")
            lines.append(clean_html(scene_text))

    char_map = []
    full_transcript = ""
    for seg in segments:
        text = seg["text"].strip().lower()
        if not text: continue
        s_us = float(seg["start"])
        e_us = float(seg["end"])
        c_dur = (e_us - s_us) / len(text)
        for i, char in enumerate(text):
            char_map.append((char, s_us + i * c_dur, s_us + (i + 1) * c_dur))
            full_transcript += char
        char_map.append((' ', e_us, e_us))
        full_transcript += ' '

    anchors = {}
    ptr = 0
    for idx, sid in enumerate(sids_sorted):
        target = clean_text(lines[idx])
        if not target:
            anchors[sid] = None
            continue
            
        window = full_transcript[ptr : ptr + 1000]
        matcher = SequenceMatcher(None, window, target)
        m = matcher.find_longest_match(0, len(window), 0, len(target))
        
        if m.size > 10 or (len(target) > 0 and m.size / len(target) > 0.25):
            gs = max(0, min(len(char_map)-1, (ptr + m.a) - m.b))
            ge = min(len(char_map)-1, gs + len(target))
            anchors[sid] = (char_map[gs][1], char_map[ge][2])
            ptr = gs + m.size
        else:
            anchors[sid] = None

    boundaries = {}
    last_end = 0.0
    for i, sid in enumerate(sids_sorted):
        if anchors[sid] is not None:
            s, e = anchors[sid]
        else:
            next_anc_sid = next((sids_sorted[j] for j in range(i+1, len(sids_sorted)) if anchors[sids_sorted[j]] is not None), None)
            g_start = last_end
            g_end = anchors[next_anc_sid][0] if next_anc_sid else duration
            
            next_idx = sids_sorted.index(next_anc_sid) if next_anc_sid else len(sids_sorted)
            orphans = sids_sorted[i : next_idx]
            total_chars = sum(len(clean_text(lines[sids_sorted.index(o)])) for o in orphans)
            g_dur = max(0.0, g_end - g_start)
            curr_s = g_start
            for o_sid in orphans:
                o_text = clean_text(lines[sids_sorted.index(o_sid)])
                o_dur = g_dur * (len(o_text) / total_chars) if total_chars > 0 else (g_dur / len(orphans))
                o_e = min(g_end, curr_s + o_dur)
                if o_sid == sids_sorted[-1]:
                    o_e = duration
                anchors[o_sid] = (curr_s, o_e)
                curr_s = o_e
            s, e = anchors[sid]

        def snap_t(t, fps=30):
            f = 1.0 / fps
            return round(round(t / f) * f, 2)
            
        boundaries[sid] = snap_t(last_end)
        last_end = snap_t(e if i < len(sids_sorted) - 1 else duration)

    timeline = {}
    for i, sid in enumerate(sids_sorted):
        out_t = boundaries[sids_sorted[i+1]] if i+1 < len(sids_sorted) else duration
        timeline[sid] = {"in": round(boundaries[sid], 2), "out": round(out_t, 2)}
    co["timeline"] = timeline

    def find_segment_start(keywords, after_t, before_t):
        for seg in segments:
            if seg["start"] < after_t or seg["start"] >= before_t: continue
            text = clean_text(seg["text"])
            for kw in keywords:
                if text.startswith(kw):
                    return seg["start"]
        return None

    kw_groups = [
        (["mot", "một", "1"], "c1", "i1"),
        (["hai", "2"], "c2", "i2"),
        (["ba", "3"], "c3", "i3"),
    ]
    for sid in ("s3", "s5"):
        scene = co["scenes"].get(sid)
        tl = co["timeline"].get(sid)
        if not scene or not tl: continue
        has_cards = "cards" in scene
        has_items = "items" in scene
        if not (has_cards or has_items): continue
        et = {}
        for kws, c_key, i_key in kw_groups:
            t = find_segment_start(kws, tl["in"], tl["out"])
            if t is None: continue
            et[c_key if has_cards else i_key] = round(t, 2)
        if et:
            scene["element_times"] = et

    if "voice" not in co: co["voice"] = {}
    co["voice"]["file"] = wav_filename
    co["voice"]["duration"] = duration
    co_file.write_text(json.dumps(co, ensure_ascii=False, indent=2), encoding="utf-8")

def get_project_status(folder_name, db_items):
    match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
    slug = match.group(1) if match else folder_name
    slug = slug.strip().lower()
    
    for item in db_items:
        item_topic = item.get("topic", "")
        if not item_topic:
            continue
        item_slug = slugify_vietnamese(item_topic)
        if item_slug == slug or item_slug in slug or slug in item_slug:
            return item.get("status", "Draft")
    return "Draft"

def get_project_files_info(proj_dir: Path, db_items):
    script_file = proj_dir / "script.txt"
    content_file = proj_dir / "content.json"
    
    script_mtime = script_file.stat().st_mtime if script_file.exists() else 0
    vbs_mtime = content_file.stat().st_mtime if content_file.exists() else 0
    
    voice_file = None
    wavs = [p for p in proj_dir.glob("*.wav") if p.name != "narration.wav" and not p.name.endswith(".tmp")]
    if wavs:
        wavs = sorted(wavs, key=lambda p: p.stat().st_mtime, reverse=True)
        voice_file = wavs[0]
    else:
        n_wav = proj_dir / "narration.wav"
        if n_wav.exists():
            voice_file = n_wav
            
    voice_mtime = voice_file.stat().st_mtime if voice_file else 0
    voice_name = voice_file.name if voice_file else ""
    
    voice_duration = 0.0
    if voice_file and voice_file.exists():
        try:
            out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_file)], capture_output=True, text=True, timeout=5).stdout.strip()
            voice_duration = float(out) if out else 0.0
        except Exception: pass
    
    video_file = None
    mp4s = [p for p in proj_dir.glob("*.mp4") if not p.name.endswith(".tmp")]
    if mp4s:
        mp4s = sorted(mp4s, key=lambda p: p.stat().st_mtime, reverse=True)
        video_file = mp4s[0]
        
    video_mtime = video_file.stat().st_mtime if video_file else 0
    video_name = video_file.name if video_file else ""
    
    status = get_project_status(proj_dir.name, db_items)
    
    warnings = []
    if script_mtime > 0 and (vbs_mtime == 0 or script_mtime > vbs_mtime + 2):
        warnings.append("kịch bản mới hơn vbs")
    if script_mtime > 0 and (voice_mtime == 0 or script_mtime > voice_mtime + 2):
        warnings.append("kịch bản mới hơn voice")
    if voice_mtime > 0 and (video_mtime == 0 or voice_mtime > video_mtime + 2):
        warnings.append("voice mới hơn video")
        
    return {
        "name": proj_dir.name,
        "path": str(proj_dir.resolve()).replace("\\", "/"),
        "script_mtime": script_mtime,
        "vbs_mtime": vbs_mtime,
        "voice_name": voice_name,
        "voice_mtime": voice_mtime,
        "voice_duration": round(voice_duration, 1),
        "video_name": video_name,
        "video_mtime": video_mtime,
        "status": status,
        "warnings": warnings
    }

def list_projects():
    parent_dir = WORK.parent
    projects = []
    
    db_items = []
    db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
    if db_path.exists():
        try:
            db_items = json.loads(db_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[SYSTEM] Lỗi load UP database: {e}")
            
    if parent_dir.exists():
        subdirs = [d for d in parent_dir.iterdir() if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")]
        subdirs = sorted(subdirs, key=lambda d: d.stat().st_mtime, reverse=True)
        
        for d in subdirs:
            if (d / "index.html").exists() or (d / "content.json").exists() or (d / "script.txt").exists():
                info = get_project_files_info(d, db_items)
                projects.append(info)
    return projects

def batch_worker():
    global BATCH_STATUS
    while True:
        task = BATCH_QUEUE.get()
        if task is None:
            break
            
        path_str, action = task
        proj_path = Path(path_str)
        
        BATCH_STATUS["running"] = True
        BATCH_STATUS["current_path"] = proj_path.name
        BATCH_STATUS["current_action"] = action
        BATCH_STATUS["current_index"] += 1
        pct = int((BATCH_STATUS["current_index"] - 1) / BATCH_STATUS["total"] * 100)
        BATCH_STATUS["progress_pct"] = pct
        
        log_msg = f"[SYSTEM] Bắt đầu {action.upper()} cho {proj_path.name}"
        print(log_msg)
        BATCH_STATUS["log"].append(log_msg)
        
        try:
            if action == "visual":
                script_txt = proj_path / "script.txt"
                if not script_txt.exists():
                    raise FileNotFoundError(f"Không tìm thấy script.txt trong {proj_path.name}")
                    
                fill_script = SCRIPT_DIR / "fill_content.py"
                cmd = [PY, str(fill_script), "--template", "01_Text_ViCon", "--script-file", str(script_txt), "--output-dir", str(proj_path)]
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if p.returncode != 0:
                    raise RuntimeError(f"fill_content.py fail: {p.stderr}")
                    
                tpl_dir = TEMPLATES / "01_Text_ViCon"
                workdir_in_tpl = tpl_dir / f"_pipeline_{proj_path.name}"
                workdir_in_tpl.mkdir(exist_ok=True)
                
                content_json_src = proj_path / "content.json"
                if content_json_src.exists():
                    (workdir_in_tpl / "content.json").write_text(content_json_src.read_text(encoding="utf-8"), encoding="utf-8")
                
                wavs = [p for p in proj_path.glob("*.wav") if p.name != "narration.wav" and not p.name.endswith(".tmp")]
                voice_filename = "narration.wav"
                if wavs:
                    wavs = sorted(wavs, key=lambda p: p.stat().st_mtime, reverse=True)
                    shutil.copy(wavs[0], workdir_in_tpl / wavs[0].name)
                    voice_filename = wavs[0].name
                else:
                    n_wav = proj_path / "narration.wav"
                    if n_wav.exists():
                        shutil.copy(n_wav, workdir_in_tpl / "narration.wav")
                        
                compose_script = tpl_dir / "compose.py"
                cmd_compose = [PY, str(compose_script), workdir_in_tpl.name]
                p_compose = subprocess.run(cmd_compose, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(tpl_dir))
                if p_compose.returncode != 0:
                    raise RuntimeError(f"compose.py fail: {p_compose.stderr}")
                    
                composed_html = workdir_in_tpl / "index.html"
                if composed_html.exists():
                    html_content = composed_html.read_text(encoding="utf-8")
                    html_content = re.sub(r'src="narration\.wav"', f'src="{voice_filename}"', html_content)
                    composed_html.write_text(html_content, encoding="utf-8")
                    shutil.copy(composed_html, proj_path / "index.html")
                
                ensure_vbs(proj_path, "01_Text_ViCon")
                msg = f"[SYSTEM] Thành công Visual cho {proj_path.name}"
                print(msg)
                BATCH_STATUS["log"].append(msg)
                
            elif action == "voice":
                script_txt = proj_path / "script.txt"
                if not script_txt.exists():
                    raise FileNotFoundError(f"Không tìm thấy script.txt trong {proj_path.name}")
                
                voice_name = "TT_06"
                profile = PAGE_PROFILES.get("vicon")
                if profile and "default_voice" in profile:
                    voice_name = profile["default_voice"]
                    
                timestamp = time.strftime("T%m.%d_%Hh%M")
                voice_filename = f"TT_{timestamp}.wav"
                out_wav = proj_path / voice_filename
                
                gen_voice_script = SCRIPT_DIR / "gen_voice.py"
                cmd = [PY, str(gen_voice_script), "--script-file", str(script_txt), "--voice", voice_name, "--output", str(out_wav)]
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if p.returncode != 0 or not out_wav.exists():
                    raise RuntimeError(f"gen_voice.py fail: {p.stderr}")
                    
                sync_latest_voice(proj_path)
                
                tr_file = proj_path / "transcript.json"
                if tr_file.exists():
                    tr_file.unlink()
                tr_result = transcribe(out_wav, tr_file)
                if tr_result.returncode != 0 or not tr_file.exists():
                    raise RuntimeError(f"whisper transcribe fail: {tr_result.stderr}")
                    
                update_timeline_from_transcript(proj_path, voice_filename)
                
                tpl_dir = TEMPLATES / "01_Text_ViCon"
                workdir_in_tpl = tpl_dir / f"_pipeline_{proj_path.name}"
                workdir_in_tpl.mkdir(exist_ok=True)
                
                content_json_src = proj_path / "content.json"
                if content_json_src.exists():
                    (workdir_in_tpl / "content.json").write_text(content_json_src.read_text(encoding="utf-8"), encoding="utf-8")
                shutil.copy(out_wav, workdir_in_tpl / voice_filename)
                
                compose_script = tpl_dir / "compose.py"
                cmd_compose = [PY, str(compose_script), workdir_in_tpl.name]
                p_compose = subprocess.run(cmd_compose, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(tpl_dir))
                
                composed_html = workdir_in_tpl / "index.html"
                if composed_html.exists():
                    html_content = composed_html.read_text(encoding="utf-8")
                    html_content = re.sub(r'src="narration\.wav"', f'src="{voice_filename}"', html_content)
                    composed_html.write_text(html_content, encoding="utf-8")
                    shutil.copy(composed_html, proj_path / "index.html")
                
                msg = f"[SYSTEM] Thành công Voice cho {proj_path.name}"
                print(msg)
                BATCH_STATUS["log"].append(msg)
                
            elif action == "video":
                wavs = [p for p in proj_path.glob("*.wav") if p.name != "narration.wav" and not p.name.endswith(".tmp")]
                if wavs:
                    wavs = sorted(wavs, key=lambda p: p.stat().st_mtime, reverse=True)
                    if not (proj_path / wavs[0].name).exists():
                        shutil.copy(wavs[0], proj_path / wavs[0].name)
                
                ts_part = time.strftime("T%m.%d_%Hh%M")
                match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", proj_path.name)
                slug_part = match.group(1) if match else proj_path.name
                slug_clean = slugify_vietnamese(slug_part, max_len=60)
                out_name = f"{slug_clean}_{ts_part}.mp4"
                
                cmd = f'npx -y -p hyperframes hyperframes render . --output "{out_name}" --fps 30 --quality draft'
                p = subprocess.run(cmd, shell=True, cwd=str(proj_path), capture_output=True, text=True, encoding="utf-8", errors="replace")
                if p.returncode != 0 or not (proj_path / out_name).exists():
                    raise RuntimeError(f"hyperframes render fail: {p.stderr}")
                    
                out_mp4 = proj_path / out_name
                co_file = proj_path / "content.json"
                if co_file.exists() and out_mp4.exists():
                    try:
                        co_data = json.loads(co_file.read_text(encoding="utf-8"))
                        voice_dur = float(co_data.get("voice", {}).get("duration", 0))
                        if voice_dur > 0.5:
                            locked = proj_path / f"{out_mp4.stem}_locked.mp4"
                            lock_cmd = (
                                f'ffmpeg -y -i "{out_mp4.name}" -vf "tpad=stop_mode=clone:stop_duration={voice_dur}" '
                                f'-t {voice_dur} -c:v libx264 -preset ultrafast -crf 23 -c:a copy "{locked.name}"'
                            )
                            p_lock = subprocess.run(lock_cmd, shell=True, cwd=str(proj_path), capture_output=True, text=True, encoding="utf-8", errors="replace")
                            if p_lock.returncode == 0 and locked.exists():
                                os.remove(out_mp4)
                                os.rename(locked, out_mp4)
                    except Exception as e_dur:
                        print(f"[SYSTEM] Lỗi khóa thời lượng: {e_dur}")
                
                msg = f"[SYSTEM] Thành công Render Video cho {proj_path.name} -> {out_name}"
                print(msg)
                BATCH_STATUS["log"].append(msg)
                
        except Exception as ex:
            err_msg = f"[SYSTEM] LỖI khi xử lý {proj_path.name} action {action}: {ex}"
            print(err_msg)
            BATCH_STATUS["log"].append(err_msg)
            
        BATCH_STATUS["progress_pct"] = int(BATCH_STATUS["current_index"] / BATCH_STATUS["total"] * 100)
        BATCH_QUEUE.task_done()
        
    BATCH_STATUS["running"] = False
    BATCH_STATUS["current_path"] = ""
    BATCH_STATUS["current_action"] = ""

threading.Thread(target=batch_worker, daemon=True).start()


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
            self._send(200, "text/html; charset=utf-8", EDITOR_HTML)
        elif path == "/api/projects":
            try:
                projects = list_projects()
                self._send(200, "application/json; charset=utf-8", json.dumps(projects, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/api/video-9x16/batch-status":
            self._send(200, "application/json; charset=utf-8", json.dumps(BATCH_STATUS, ensure_ascii=False))
        elif path.startswith("/workspace-file/"):
            fname = path[len("/workspace-file/"):]
            if "/" in fname or "\\" in fname or ".." in fname:
                self._send(400, "text/plain", "Bad name"); return
            folder_path = get_path_from_query(self.path)
            fp = folder_path / fname
            if fp.exists():
                ext = fp.suffix.lower()
                ctype = {".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".mp4": "video/mp4"}.get(ext, "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
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
            folder_path = get_path_from_query(self.path)
            sync_latest_voice(folder_path)
            index_file = folder_path / "index.html"
            if not index_file.exists():
                self._send(200, "application/json; charset=utf-8", json.dumps({"fields": FIELDS, "data": {}, "styleFields": STYLE_FIELDS, "styles": {}}, ensure_ascii=False))
                return
            html = index_file.read_text(encoding="utf-8")
            data = {f["id"]: clean_boom_boom(get_inner(html, f["id"])) for f in FIELDS}
            styles = {sf["sel"]: {p: get_css(html, sf["sel"], p) for p in sf["props"]} for sf in STYLE_FIELDS}
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"fields": FIELDS, "data": data, "styleFields": STYLE_FIELDS, "styles": styles}, ensure_ascii=False))
        elif path == "/workspace-info":
            folder_path = get_path_from_query(self.path)
            self._send(200, "application/json", json.dumps({"workspace": str(folder_path.resolve())}))
        elif path == "/heartbeat":
            global LAST_HEARTBEAT
            LAST_HEARTBEAT = time.time()
            self._send(200, "application/json", '{"ok":true}')
        elif path.startswith("/preview/"):
            folder_path = get_path_from_query(self.path)
            sync_latest_voice(folder_path)
            try:
                n = int(path.rsplit("/", 1)[-1].split("?")[0])
            except Exception:
                self._send(400, "text/plain", "Bad scene number"); return
            
            index_file = folder_path / "index.html"
            if not index_file.exists():
                self._send(404, "text/plain", "Project index.html not found"); return
            
            html = index_file.read_text(encoding="utf-8")
            
            try:
                style_blocks = re.findall(r'<style[^>]*>(.*?)</style>', html, re.DOTALL)
                workspace_css = "\n".join(style_blocks)
                workspace_css = re.sub(
                    r"url\(['\"]\./([^'\"]+\.(?:ttf|otf|woff2?))['\"]\)",
                    rf"url('/workspace-file/\1?path={urllib.parse.quote(str(folder_path.resolve()))}')",
                    workspace_css, flags=re.IGNORECASE
                )
                workspace_css = re.sub(r'#root\b', '.thumb-inner', workspace_css)
                scoped = re.sub(
                    r'(^|\})\s*(\.[\w][\w\-,\s\.]*\{)',
                    lambda m: m.group(1) + " .thumb-inner " + m.group(2),
                    workspace_css,
                    flags=re.MULTILINE
                )
                override = '.thumb-inner .scene{opacity:1!important;position:relative!important;}'
                inject_style_str = f'<style id="_from_workspace">{scoped}\n{override}</style>'
            except Exception as e_css:
                print(f"[preview] css extraction fail: {e_css}")
                inject_style_str = ""
            
            edit_ids = [f["id"] for f in FIELDS]
            inject_css = (
                "<style id=\"_preview_override\">"
                "body{background:transparent!important;overflow:hidden!important;}"
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
                f"const FOLDER_PATH={json.dumps(str(folder_path.resolve()).replace('\\\\', '/'))};"
                "function setupEdit(){"
                "  EDIT_IDS.forEach(id=>{"
                "    const el=document.getElementById(id); if(!el || el._wired) return;"
                "    el._wired=true;"
                "    el.setAttribute('contenteditable','true');"
                "    el.addEventListener('mousedown',e=>e.stopPropagation(),true);"
                "    el.addEventListener('focus',()=>{"
                "      const sel='.'+ (el.className.match(/\\bs\\d-[\\w-]+/)||[''])[0];"
                "      parent.postMessage({t:'focus',scene:SCENE_NUM,id,sel,html:el.innerHTML,path:FOLDER_PATH},'*');"
                "    });"
                "    el.addEventListener('input',()=>{ parent.postMessage({t:'changed',id,html:el.innerHTML,path:FOLDER_PATH},'*'); });"
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
            
            html = re.sub(
                r"url\(['\"]\./([^'\"]+\.(?:ttf|otf|woff2?))['\"]\)",
                rf"url('/workspace-file/\1?path={urllib.parse.quote(str(folder_path.resolve()))}')",
                html, flags=re.IGNORECASE
            )
            
            html = re.sub(
                r'(<audio[^>]*\bsrc=")([^"]+)(")',
                rf'\g<1>/workspace-file/\2?path={urllib.parse.quote(str(folder_path.resolve()))}\g<3>',
                html, flags=re.IGNORECASE
            )
            
            if inject_style_str:
                html = html.replace("</head>", inject_style_str + inject_css + "</head>", 1)
            else:
                html = html.replace("</head>", inject_css + "</head>", 1)
                
            if "</body>" in html:
                html = html.replace("</body>", inject_js + "</body>", 1)
            else:
                html = html + inject_js
            self._send(200, "text/html; charset=utf-8", html)
        elif path == "/narration.wav":
            folder_path = get_path_from_query(self.path)
            wav = _find_voice_wav(folder_path)
            if wav and wav.exists():
                self._send(200, "audio/wav", wav.read_bytes())
            else:
                self._send(404, "text/plain", "no audio")
        elif path == "/voice-info":
            folder_path = get_path_from_query(self.path)
            sync_latest_voice(folder_path)
            wav = _find_voice_wav(folder_path)
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
