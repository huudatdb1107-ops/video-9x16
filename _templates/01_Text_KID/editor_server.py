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
    if not isinstance(text, str): return text
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
# Tăng timeout lên 24 giờ để giữ server luôn sống ổn định cho Sếp làm việc, tránh bị tắt ngầm khi ẩn tab
LAST_HEARTBEAT = time.time()
HEARTBEAT_TIMEOUT = 86400  # 24 giờ (86400 giây)
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
_ap.add_argument("--port", type=int, default=None, help="HTTP port (mặc định 5050 hoặc đọc từ .editor_port)")
_args, _ = _ap.parse_known_args()
WORK = Path(_args.workspace).resolve() if _args.workspace else Path.cwd()
WORKSPACE = WORK
INDEX = WORK / "index.html"
PORT = _args.port if _args.port is not None else 5050
print(f"[editor_server] WORK = {WORK}")

IS_SINGLE_MODE = False
if (WORK / "index.html").exists() or (WORK / "content.json").exists() or (WORK / "script.txt").exists():
    IS_SINGLE_MODE = True

EDITOR_SINGLE_HTML = ""
_single_editor_path = Path(__file__).parent / "single_editor.html"
if _single_editor_path.exists():
    try:
        EDITOR_SINGLE_HTML = _single_editor_path.read_text(encoding="utf-8")
    except Exception as e_read:
        print(f"[editor_server] Lỗi đọc single_editor.html: {e_read}")

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
<html lang="vi"><head><meta charset="UTF-8"><meta name="color-scheme" content="dark"><title>B.SIMPLE Workspace Editor — Multi-Project Trạm Biên Tập</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #0d0d0d; color: #eee; font-family: "Outfit", sans-serif; height: 100vh; overflow: hidden; }
#app { display: grid; grid-template-rows: 64px auto 1fr; height: 100vh; }
#topbar { display: flex; align-items: center; padding: 0 20px; background: #0d0d0d; border-bottom: 1px solid #443311; gap: 16px; }
#topbar h2 { margin: 0; font-size: 16px; color: #D4AF37; min-width: 0; font-weight: 800; letter-spacing: 1.5px; display: flex; align-items: center; gap: 8px; text-transform: uppercase; }
#topbar button { flex-shrink: 0; }
#main { padding: 20px; overflow-y: auto; background: #0d0d0d; }
#stylebar { background: #050505; border-bottom: 1px solid #443311; padding: 12px 20px; display: flex; gap: 8px; align-items: center; flex-wrap: nowrap; }
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
th, td { border: 1px solid #443311; padding: 4px 8px; text-align: left; vertical-align: middle; }
#projectTable td.scene-col { padding: 2px !important; }
th { background: #050505; color: #D4AF37; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }
tr:hover { background: #121212; }
tr.published-row { opacity: 0.65; }

/* Custom Checkbox */
input[type="checkbox"] { accent-color: #D4AF37; cursor: pointer; width: 16px; height: 16px; }

/* Scene frame */
#projectTable th.scene-col, #projectTable td.scene-col {
  width: 140px;
  text-align: center;
  border-left: none !important;
  border-right: none !important;
  padding-left: 2px !important;
  padding-right: 2px !important;
}
#projectTable th.scene-col:nth-child(4), #projectTable td.scene-col:nth-child(4) {
  border-left: 1px solid #443311 !important;
}
#projectTable th.scene-col:nth-child(9), #projectTable td.scene-col:nth-child(9) {
  border-right: 1px solid #443311 !important;
}
.id-col { width: 20px !important; padding-left: 2px !important; padding-right: 2px !important; text-align: center; }
.iframe-container { position: relative; width: 135px; height: 240px; background: #050505; border: 1px solid #443311; border-radius: 4px; overflow: hidden; margin: 0 auto; }
.iframe-container iframe { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; transform: scale(0.125); transform-origin: top left; border: none; }
.iframe-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: transparent; cursor: pointer; z-index: 5; }
.iframe-container.editing { border-color: #D4AF37; box-shadow: 0 0 10px rgba(212, 175, 55, 0.4); }
.iframe-container.editing .iframe-overlay { display: none; }

/* Mini Player */
.audio-mini { width: 100%; max-width: 140px; height: 28px; background: transparent; outline: none; }
.video-mini { width: 135px; height: 240px; object-fit: cover; border-radius: 4px; border: 1px solid #443311; background: #000; cursor: pointer; }

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
.status-badge.script { background: #008080; color: #fff; border: 1px solid #00a3a3; }

#renderLog { background: #050505; padding: 12px; border-radius: 4px; font-family: monospace; font-size: 11px; white-space: pre-wrap; color: #aaa; max-height: 180px; overflow-y: auto; margin-top: 15px; border: 1px solid #443311; }
</style>
</head><body>
<div id="app">
  <div id="topbar">
    <h2><span style="font-size: 20px;">📁</span> Workspace: <select id="workspaceSelect" onchange="switchWorkspace(this.value)" style="background: #050505; color: #D4AF37; border: 1px solid #443311; padding: 4px 8px; border-radius: 4px; font-weight: 700; font-family: inherit; font-size: 13px; cursor: pointer; outline: none;"></select></h2>
    
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
      <span id="styleTarget">Click vào chữ trong ô preview để chỉnh styles</span>
    </div>
    <div class="grp" style="margin-left: auto;"><label>font</label><input type="text" id="styFs" placeholder="130px"><button type="button" class="bump" data-target="styFs" data-delta="-2">−</button><button type="button" class="bump" data-target="styFs" data-delta="2">+</button></div>
    <div class="grp"><label>line</label><input type="text" id="styLh" placeholder="1.25"><button type="button" class="bump" data-target="styLh" data-delta="-0.05">−</button><button type="button" class="bump" data-target="styLh" data-delta="0.05">+</button></div>
    <div class="grp"><label>spacing</label><input type="text" id="styLs" placeholder="-1px"><button type="button" class="bump" data-target="styLs" data-delta="-1">−</button><button type="button" class="bump" data-target="styLs" data-delta="1">+</button></div>
    <div class="grp"><label>gap</label><input type="text" id="styGap" placeholder="48px" style="width:60px"><button type="button" class="bump" data-target="styGap" data-delta="-4">−</button><button type="button" class="bump" data-target="styGap" data-delta="4">+</button></div>
    <div class="grp"><label>color</label><input type="text" id="styColor" placeholder="#ffffff" style="width:70px"><input type="color" id="styColorPick" style="width:24px; height:20px; border:none; background:transparent; cursor:pointer; padding:0; margin-left:4px;"></div>
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
    <button class="secondary" onclick="showTemplateModal()" style="padding: 4px 10px; font-size: 11px; border-radius: 4px; text-transform: none; margin-left: 10px;">💾 Save Template</button>
  </div>

  <div id="main">
    <!-- Diagnostics Board -->
    <div id="diagnosticsBoard" class="diagnostics-container" style="display:none; margin-bottom: 20px; padding: 12px; background: #0c0803; border: 1px solid #443311; border-radius: 6px;">
      <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #443311; padding-bottom: 6px; margin-bottom: 10px;">
        <span style="color: #D4AF37; font-weight: bold; font-size: 13px; display: flex; align-items: center; gap: 6px;">🔍 BẢNG TỔNG QUÁT KIỂM ĐỊNH (DIAGNOSTICS BOARD)</span>
        <button class="secondary" onclick="runDiagnostics()" style="padding: 2px 8px; font-size: 10px; border-radius: 4px; text-transform: none; height: 24px; line-height: 20px; border-color: #D4AF37; color: #D4AF37;">Chạy Lại Kiểm Định</button>
      </div>
      <div id="diagnosticsStats" style="font-size: 11px; color: #fff; margin-bottom: 10px; display:flex; gap:16px;"></div>
      <div id="diagnosticsList" style="max-height: 150px; overflow-y: auto; font-size: 11px; display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 8px; padding-right: 4px;"></div>
    </div>

    <table id="projectTable">
      <thead>
        <tr>
          <th style="width: 40px; text-align: center;"><input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll()"></th>
          <th class="id-col">ID</th>
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
let PROJECT_STATES = {}; // cache lưu state load được: {folderPath: {fields, data, styleFields, styles}}
let CURRENT_FOLDER = null;
let CURRENT_SEL = null;
let CURRENT_ELEMENT_ID = null;
let ACTIVE_EM = null;
let LAST_COMPUTED_STYLES = null;

let PROJECT_UNDO_STACKS = {};
const MAX_STACK_SIZE = 50;

function saveProjectToUndoStack(folderPath) {
  if (!folderPath || !PROJECT_STATES[folderPath]) return;
  const state = PROJECT_STATES[folderPath];
  const stateStr = JSON.stringify({ data: state.data, styles: state.styles });
  if (!PROJECT_UNDO_STACKS[folderPath]) {
    PROJECT_UNDO_STACKS[folderPath] = [];
  }
  const stack = PROJECT_UNDO_STACKS[folderPath];
  if (stack.length > 0 && stack[stack.length - 1] === stateStr) {
    return;
  }
  stack.push(stateStr);
  if (stack.length > MAX_STACK_SIZE) {
    stack.shift();
  }
}

function performProjectUndo(folderPath) {
  if (!folderPath || !PROJECT_UNDO_STACKS[folderPath] || PROJECT_UNDO_STACKS[folderPath].length <= 1) {
    console.log('Không có gì để Undo');
    return;
  }
  const stack = PROJECT_UNDO_STACKS[folderPath];
  stack.pop(); // Bỏ trạng thái hiện tại
  const previousStateStr = stack[stack.length - 1];
  const previousState = JSON.parse(previousStateStr);
  
  const state = PROJECT_STATES[folderPath];
  state.data = previousState.data;
  state.styles = previousState.styles;
  
  // Lưu ngay lập tức
  saveProject(folderPath);
  
  // Reload các iframe preview trong hàng
  const pName = folderPath.split('/').pop().split('\\').pop();
  const tr = document.getElementById('row-' + pName);
  if (tr) {
    tr.querySelectorAll('iframe').forEach(iframe => {
      if (iframe.src !== 'about:blank' && iframe.src) {
        try {
          const url = new URL(iframe.src, window.location.href);
          url.searchParams.set('t', Date.now());
          iframe.src = url.toString();
        } catch(err){
          iframe.src = iframe.dataset.src + '&t=' + Date.now();
        }
      }
    });
  }
}

window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    if (CURRENT_FOLDER) {
      e.preventDefault();
      performProjectUndo(CURRENT_FOLDER);
    }
  }
});

// Helper chuyển đổi màu rgb sang hex
function rgbToHex(rgb) {
  if (!rgb) return '#ffffff';
  if (rgb.startsWith('#')) return rgb.length === 7 ? rgb : '#ffffff';
  const m = String(rgb).match(/\d+/g);
  if (!m || m.length < 3) return '#ffffff';
  return '#' + m.slice(0,3).map(n => parseInt(n).toString(16).padStart(2,'0')).join('');
}

// Set status log hiển thị
function setStatus(text, type='ok') {
  const icon = type === 'busy' ? '⚙️' : type === 'err' ? '❌' : '✅';
  console.log(`[SYSTEM] [status] ${icon} ${text}`);
  const logEl = document.getElementById('renderLog');
  if (logEl) {
    logEl.textContent = `[${new Date().toLocaleTimeString()}] ${icon} ${text}\n` + logEl.textContent;
  }
}

// Auto-size input width
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

function padDecimal(s) {
  s = String(s).trim();
  if (s.includes('.')) return s;
  if (/^-?\d+$/.test(s)) return s + '.0';
  return s;
}

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

function formatForApply(prop, value) {
  if (!value) return value;
  if (prop === 'color' || prop === 'line-height') return value;
  const t = String(value).trim();
  if (/^-?\d+(\.\d+)?$/.test(t)) return t + 'px';
  return t;
}

// Load danh sách projects
async function reload() {
  setStatus('Loading projects…', 'busy');
  try {
    const r = await fetch('/api/projects');
    PROJECTS = await r.json();
    
    renderTable();
    await loadWorkspaces();
    setStatus('Đã tải danh sách dự án!', 'ok');
  } catch (e) {
    console.error('Lỗi load projects:', e);
    setStatus('Lỗi tải danh sách dự án con: ' + e.message, 'err');
  }
}

// Chẩn đoán lỗi toàn bộ dự án
function runDiagnostics() {
  const board = document.getElementById('diagnosticsBoard');
  const statsEl = document.getElementById('diagnosticsStats');
  const listEl = document.getElementById('diagnosticsList');
  
  if (!board || !statsEl || !listEl) return;
  
  let totalProjects = PROJECTS.length;
  let errorProjectsCount = 0;
  let totalErrors = 0;
  
  listEl.innerHTML = '';
  
  PROJECTS.forEach(p => {
    const pErrors = p.errors || [];
    
    if (pErrors.length > 0) {
      errorProjectsCount++;
      totalErrors += pErrors.length;
      
      const item = document.createElement('div');
      item.style.padding = '8px';
      item.style.background = '#150f08';
      item.style.border = '1px solid #ff5544';
      item.style.borderRadius = '4px';
      item.style.display = 'flex';
      item.style.flexDirection = 'column';
      item.style.gap = '4px';
      
      const header = document.createElement('div');
      header.style.display = 'flex';
      header.style.justifyContent = 'space-between';
      header.style.alignItems = 'center';
      header.style.fontWeight = 'bold';
      header.style.color = '#ff9988';
      
      // Lấy lỗi đầu tiên để nhảy tới sửa
      const firstErr = pErrors[0];
      header.innerHTML = `
        <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 250px;" title="${p.name}">${p.name}</span>
        <button class="secondary" style="padding: 1px 6px; font-size: 9px; border-radius: 3px; text-transform: none; border-color: #ff9988; color: #ff9988; background: transparent; cursor: pointer;" onclick="jumpToProjectFirstError('${p.name}', '${firstErr.replace(/'/g, "\\'")}')">Sửa Lỗi</button>
      `;
      item.appendChild(header);
      
      const errList = document.createElement('ul');
      errList.style.margin = '0';
      errList.style.paddingLeft = '16px';
      errList.style.color = '#ff5544';
      pErrors.forEach(err => {
        const li = document.createElement('li');
        li.textContent = err;
        errList.appendChild(li);
      });
      item.appendChild(errList);
      listEl.appendChild(item);
    }
  });
  
  if (errorProjectsCount > 0) {
    board.style.display = 'block';
    statsEl.innerHTML = `
      <span>Tổng số dự án: <strong style="color:#D4AF37">${totalProjects}</strong></span>
      <span style="margin-left: 15px;">Số dự án có lỗi: <strong style="color:#ff5544">${errorProjectsCount}</strong></span>
      <span style="margin-left: 15px;">Tổng số lỗi phát hiện: <strong style="color:#ff5544">${totalErrors}</strong></span>
    `;
  } else {
    board.style.display = 'none';
  }
}

// Cuộn mượt và kích hoạt chế độ sửa lỗi cho dự án bị lỗi
window.jumpToProjectFirstError = function(projName, errorMsg) {
  let sceneNum = 1;
  const match = errorMsg.match(/Scene\s+(\d+)/);
  if (match) {
    sceneNum = parseInt(match[1]);
  } else if (errorMsg.includes("index.html") || errorMsg.includes("kịch bản")) {
    sceneNum = 1;
  }
  
  const container = document.getElementById(`container-${projName}-${sceneNum}`);
  if (container) {
    container.scrollIntoView({ behavior: 'smooth', block: 'center' });
    
    // Highlight nhấp nháy
    container.style.outline = '3px solid #ff5544';
    container.style.boxShadow = '0 0 15px rgba(255, 85, 68, 0.8)';
    setTimeout(() => {
      container.style.outline = 'none';
      container.style.boxShadow = 'none';
    }, 2500);
    
    // Kích hoạt click mở Stylebar
    setTimeout(() => {
      container.click();
    }, 300);
  }
};

// Sử dụng một IntersectionObserver duy nhất để lazy-load và tự động giải phóng RAM các iframe preview khi cuộn
let globalIframeObserver = null;
function getIframeObserver() {
  if (!globalIframeObserver) {
    const rootEl = document.getElementById('main');
    globalIframeObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const container = entry.target;
        const iframe = container.querySelector('iframe');
        if (!iframe) return;
        
        if (entry.isIntersecting) {
          // Khi cuộn vào tầm mắt, nạp iframe
          if (iframe.src === 'about:blank' || !iframe.src) {
            iframe.src = iframe.dataset.src;
          }
        } else {
          // Khi cuộn ra ngoài tầm mắt, giải phóng iframe để trả lại RAM/CPU
          // Chống giải phóng iframe đang được chỉnh sửa (.editing) để tránh mất dấu soạn thảo
          if (iframe.src !== 'about:blank' && !container.classList.contains('editing')) {
            iframe.src = 'about:blank';
          }
        }
      });
    }, { 
      root: rootEl || null, 
      rootMargin: '450px' // Nạp trước 450px để cuộn mượt
    });
  }
  return globalIframeObserver;
}

// Render bảng danh sách các dự án con
function renderTable() {
  const tbody = document.getElementById('projectTableBody');
  tbody.innerHTML = '';
  
  const hidePublished = document.getElementById('hidePublishedCheckbox').checked;
  
  let index = 0;
  PROJECTS.forEach(p => {
    // Ẩn các bài đã đăng nếu checkbox hidePublished được chọn
    const isPublished = p.status.toLowerCase() === 'published' || p.status.toLowerCase() === 'posted';
    if (hidePublished && isPublished) {
      return;
    }
    
    index++;
    const tr = document.createElement('tr');
    tr.id = `row-${p.name}`;
    if (isPublished) {
      tr.classList.add('published-row');
    }
    if (p.errors && p.errors.length > 0) {
      tr.style.background = 'rgba(255, 85, 68, 0.04)';
      tr.style.borderLeft = '4px solid #ff5544';
    }
    
    // --- Tạo ô checkbox chọn dự án ---
    const tdCheck = document.createElement('td');
    tdCheck.style.textAlign = 'center';
    tdCheck.innerHTML = `<input type="checkbox" class="project-checkbox" data-path="${p.path}" onchange="updateSelectAllState()">`;
    tr.appendChild(tdCheck);
    
    // --- Tạo ô ID ---
    const tdId = document.createElement('td');
    tdId.className = 'id-col';
    tdId.textContent = index;
    tr.appendChild(tdId);
    
    // --- Tạo ô Tên thư mục (Folder) ---
    const tdFolder = document.createElement('td');
    tdFolder.style.fontWeight = '500';
    tdFolder.innerHTML = `
      <div style="word-break: break-all; max-width: 220px;">${p.name}</div>
      <div style="margin-top: 6px; display: flex; gap: 6px;">
        <button class="secondary" style="padding: 2px 8px; font-size: 10px; border-radius: 4px; text-transform: capitalize;" onclick="openProjectFolder('${p.path}')">📂 Open</button>
      </div>
    `;
    
    // Check và hiển thị cảnh báo VBS/Visual nếu kịch bản mới hơn index.html
    const hasVisualWarning = p.warnings.includes('kịch bản mới hơn vbs');
    if (hasVisualWarning) {
      const badge = document.createElement('div');
      badge.className = 'warning-badge';
      badge.innerHTML = `⚠️ KỊCH BẢN MỚI HƠN VISUAL`;
      const btnQuick = document.createElement('button');
      btnQuick.className = 'btn-quick';
      btnQuick.textContent = 'Gen Visual';
      btnQuick.onclick = () => runQuickAction(p.path, 'visual');
      tdFolder.appendChild(badge);
      tdFolder.appendChild(btnQuick);
    }
    tr.appendChild(tdFolder);
    
    // --- Tạo 6 ô iframe preview ---
    for (let scene = 1; scene <= 6; scene++) {
      const tdScene = document.createElement('td');
      tdScene.className = 'scene-col';
      
      const container = document.createElement('div');
      container.className = 'iframe-container';
      container.id = `container-${p.name}-${scene}`;
      
      // Iframe preview trỏ tới /preview/{scene}?path={p.path}
      const iframe = document.createElement('iframe');
      iframe.dataset.src = `/preview/${scene}?path=${encodeURIComponent(p.path)}`;
      iframe.src = 'about:blank'; // Lazy loading
      
      const overlay = document.createElement('div');
      overlay.className = 'iframe-overlay';
      overlay.onclick = () => startInlineEdit(container, p.name, scene, p.path);
      
      container.appendChild(overlay);
      container.appendChild(iframe);
      tdScene.appendChild(container);
      tr.appendChild(tdScene);
    }
    
    // --- Tạo ô Voice player ---
    const tdVoice = document.createElement('td');
    if (p.voice_name) {
      const voiceSrc = `/workspace-file/${p.voice_name}?path=${encodeURIComponent(p.path)}`;
      tdVoice.innerHTML = `
        <audio class="audio-mini" controls src="${voiceSrc}"></audio>
        <div style="font-size: 10px; color: #aaa; margin-top: 4px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          ${p.voice_name} (${p.voice_duration}s)
        </div>
      `;
    } else {
      tdVoice.innerHTML = `<span style="color: #666; font-style: italic;">Chưa có voice</span>`;
    }
    
    const hasVoiceWarning = p.warnings.includes('kịch bản mới hơn voice');
    if (hasVoiceWarning) {
      const badge = document.createElement('div');
      badge.className = 'warning-badge';
      badge.innerHTML = `⚠️ KỊCH BẢN MỚI HƠN VOICE`;
      const btnQuick = document.createElement('button');
      btnQuick.className = 'btn-quick';
      btnQuick.textContent = 'Gen Voice';
      btnQuick.onclick = () => runQuickAction(p.path, 'voice');
      tdVoice.appendChild(badge);
      tdVoice.appendChild(btnQuick);
    }
    tr.appendChild(tdVoice);
    
    // --- Tạo ô Video player ---
    const tdVideo = document.createElement('td');
    tdVideo.style.textAlign = 'center';
    if (p.video_name) {
      const videoSrc = `/workspace-file/${p.video_name}?path=${encodeURIComponent(p.path)}`;
      tdVideo.innerHTML = `
        <video class="video-mini" src="${videoSrc}" loop muted playsinline preload="metadata" onloadeddata="this.currentTime=0.1" onclick="this.paused ? this.play() : this.pause()"></video>
        <div style="font-size: 9px; color: #888; margin-top: 4px; max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          ${p.video_name}
        </div>
      `;
    } else {
      tdVideo.innerHTML = `<span style="color: #666; font-style: italic;">Chưa có video</span>`;
    }
    
    const hasVideoWarning = p.warnings.includes('voice mới hơn video');
    if (hasVideoWarning) {
      const badge = document.createElement('div');
      badge.className = 'warning-badge';
      badge.innerHTML = `⚠️ VOICE MỚI HƠN VIDEO`;
      const btnQuick = document.createElement('button');
      btnQuick.className = 'btn-quick';
      btnQuick.textContent = 'Render';
      btnQuick.onclick = () => runQuickAction(p.path, 'video');
      tdVideo.appendChild(badge);
      tdVideo.appendChild(btnQuick);
    }
    tr.appendChild(tdVideo);
    
    // --- Tạo ô Trạng thái ---
    const tdStatus = document.createElement('td');
    tdStatus.style.textAlign = 'center';
    const stClass = p.status.toLowerCase() === 'published' || p.status.toLowerCase() === 'posted' ? 'published' : p.status.toLowerCase() === 'approved' ? 'approved' : 'draft';
    tdStatus.innerHTML = `<span class="status-badge ${stClass}">${p.status}</span>`;
    tr.appendChild(tdStatus);
    
    // Tự động nạp/giải phóng iframe preview khi hover vào dòng
    tr.addEventListener('mouseenter', () => {
      tr.querySelectorAll('iframe').forEach(iframe => {
        if (iframe.src === 'about:blank' || !iframe.src) {
          iframe.src = iframe.dataset.src;
        }
      });
    });
    tr.addEventListener('mouseleave', () => {
      if (!tr.classList.contains('editing-row')) {
        tr.querySelectorAll('iframe').forEach(iframe => {
          if (iframe.src !== 'about:blank') {
            iframe.src = 'about:blank';
          }
        });
      }
    });

    tbody.appendChild(tr);
  });
  
  updateSelectAllState();
  runDiagnostics();
  
  // Tự động kích hoạt hàng đầu tiên (nếu có dự án) để nạp sẵn preview khi load trang
  const firstRow = tbody.querySelector('tr');
  if (firstRow) {
    firstRow.classList.add('editing-row');
    firstRow.querySelectorAll('iframe').forEach(iframe => {
      if (iframe.src === 'about:blank' || !iframe.src) {
        iframe.src = iframe.dataset.src;
      }
    });
  }
}

// Bắt đầu chỉnh sửa trực tiếp trên iframe
async function startInlineEdit(container, projName, sceneNum, folderPath) {
  document.querySelectorAll('.iframe-container.editing').forEach(el => {
    el.classList.remove('editing');
  });
  
  // Xóa class editing-row trên toàn bộ tr cũ và giải phóng iframe của chúng
  document.querySelectorAll('tr.editing-row').forEach(row => {
    row.classList.remove('editing-row');
    row.querySelectorAll('iframe').forEach(iframe => {
      if (iframe.src !== 'about:blank') {
        iframe.src = 'about:blank';
      }
    });
  });
  
  // Thêm class editing-row cho tr cha của container hiện tại
  const tr = container.closest('tr');
  if (tr) {
    tr.classList.add('editing-row');
    // Đảm bảo toàn bộ 6 iframe của hàng này được nạp
    tr.querySelectorAll('iframe').forEach(iframe => {
      if (iframe.src === 'about:blank' || !iframe.src) {
        iframe.src = iframe.dataset.src;
      }
    });
  }
  
  container.classList.add('editing');
  CURRENT_FOLDER = folderPath;
  
  // Load state của dự án
  await loadProjectState(folderPath);
  
  // Gửi tin nhắn xuống iframe để tự động focus vào chữ
  const iframe = container.querySelector('iframe');
  if (iframe && iframe.contentWindow) {
    setTimeout(() => {
      iframe.contentWindow.postMessage({ t: 'trigger-focus' }, '*');
    }, 100);
  }
}

// Tải state dự án (chứa texts và styles) và cache
async function loadProjectState(folderPath) {
  try {
    const r = await fetch('/load?path=' + encodeURIComponent(folderPath));
    const j = await r.json();
    PROJECT_STATES[folderPath] = j;
    
    if (CURRENT_SEL) {
      updateStyleBarInputs();
    }
  } catch (e) {
    console.error('Lỗi load project state:', e);
    setStatus('Lỗi tải dữ liệu dự án: ' + e.message, 'err');
  }
}

// Cập nhật các input trên Stylebar
function updateStyleBarInputs() {
  if (!CURRENT_FOLDER || !CURRENT_SEL || !PROJECT_STATES[CURRENT_FOLDER]) return;
  
  const state = PROJECT_STATES[CURRENT_FOLDER];
  const styles = state.styles[CURRENT_SEL] || {};
  
  document.getElementById('styFs').value = formatForDisplay('font-size', styles['font-size']);
  document.getElementById('styLh').value = formatForDisplay('line-height', styles['line-height']);
  document.getElementById('styLs').value = formatForDisplay('letter-spacing', styles['letter-spacing']);
  
  // Gap
  const gapVal = state.styles['.s3-cards']?.['gap'] || state.styles['.s5-list']?.['gap'] || '';
  document.getElementById('styGap').value = formatForDisplay('gap', gapVal);
  
  // Color
  const colorVal = rgbToHex(styles['color'] || '#ffffff');
  document.getElementById('styColor').value = colorVal;
  document.getElementById('styColorPick').value = colorVal;
  
  // Cập nhật General/Highlight buttons state
  const btnGeneral = document.getElementById('btnSelectGeneral');
  const btnHighlight = document.getElementById('btnSelectHighlight');
  
  if (CURRENT_SEL.includes(' em')) {
    btnGeneral.classList.remove('active');
    btnHighlight.classList.add('active');
  } else {
    btnGeneral.classList.add('active');
    btnHighlight.classList.remove('active');
  }
  
  // Kiểm tra xem selector gốc có hỗ trợ Highlight hay không
  const rawSel = CURRENT_SEL.replace(' em', '');
  const supportsHighlight = rawSel.includes('title') || rawSel.includes('quote') || rawSel.includes('card-text') || rawSel.includes('text') || rawSel.includes('sub');
  
  if (supportsHighlight) {
    btnHighlight.disabled = false;
    btnHighlight.style.opacity = "1";
    btnHighlight.style.cursor = "pointer";
  } else {
    btnHighlight.disabled = true;
    btnHighlight.style.opacity = "0.3";
    btnHighlight.style.cursor = "not-allowed";
    btnHighlight.classList.remove('active');
  }
  
  document.querySelectorAll('#stylebar input').forEach(autosizeInput);
}

// Cập nhật style cho selector hiện tại
function applyStyleProp(prop, value) {
  if (!CURRENT_FOLDER || !CURRENT_SEL || !PROJECT_STATES[CURRENT_FOLDER]) return;
  
  saveProjectToUndoStack(CURRENT_FOLDER);
  const v = formatForApply(prop, value);
  const state = PROJECT_STATES[CURRENT_FOLDER];
  
  state.styles[CURRENT_SEL] = state.styles[CURRENT_SEL] || {};
  state.styles[CURRENT_SEL][prop] = v;
  
  // Gửi postMessage xuống iframe đang edit
  const iframeContainer = document.querySelector('.iframe-container.editing');
  if (iframeContainer) {
    const iframe = iframeContainer.querySelector('iframe');
    if (iframe && iframe.contentWindow) {
      iframe.contentWindow.postMessage({t: 'set-style', sel: CURRENT_SEL, prop: prop, val: v}, '*');
    }
  }
  
  queueAutoSave(CURRENT_FOLDER);
  saveProjectToUndoStack(CURRENT_FOLDER);
}

// Auto-save debounce
let saveTimeout = null;
function queueAutoSave(folderPath) {
  if (saveTimeout) clearTimeout(saveTimeout);
  saveTimeout = setTimeout(() => {
    saveProject(folderPath);
  }, 1500);
}

async function saveProject(folderPath) {
  if (!PROJECT_STATES[folderPath]) return;
  const state = PROJECT_STATES[folderPath];
  try {
    const payload = JSON.stringify({ data: state.data, styles: state.styles });
    const r = await fetch('/save?path=' + encodeURIComponent(folderPath), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: payload
    });
    const j = await r.json();
    if (j.ok) {
      setStatus(`Đã tự động lưu thành công: ${folderPath.split('/').pop()}`);
    } else {
      setStatus(`Lưu tự động thất bại: ${j.msg || ''}`, 'err');
    }
  } catch (e) {
    console.error('Auto save error:', e);
    setStatus('Lỗi lưu tự động: ' + e.message, 'err');
  }
}

// Thiết lập Stylebar
function wireStyleBar() {
  const map = { styFs: 'font-size', styLh: 'line-height', styLs: 'letter-spacing', styColor: 'color' };
  for (const id in map) {
    const inp = document.getElementById(id);
    inp.addEventListener('input', () => { 
      applyStyleProp(map[id], inp.value); 
      autosizeInput(inp); 
    });
  }
  
  // group-gap
  const gapInput = document.getElementById('styGap');
  if (gapInput) {
    gapInput.addEventListener('input', () => {
      const v = formatForApply('gap', gapInput.value);
      if (CURRENT_FOLDER && PROJECT_STATES[CURRENT_FOLDER]) {
        const state = PROJECT_STATES[CURRENT_FOLDER];
        ['.s3-cards', '.s5-list'].forEach(sel => {
          state.styles[sel] = state.styles[sel] || {};
          state.styles[sel]['gap'] = v;
        });
        
        const iframeContainer = document.querySelector('.iframe-container.editing');
        if (iframeContainer) {
          const iframe = iframeContainer.querySelector('iframe');
          if (iframe && iframe.contentWindow) {
            ['.s3-cards', '.s5-list'].forEach(sel => {
              iframe.contentWindow.postMessage({t: 'set-style', sel: sel, prop: 'gap', val: v}, '*');
            });
          }
        }
        queueAutoSave(CURRENT_FOLDER);
      }
      autosizeInput(gapInput);
    });
  }
  
  // Color Picker & Input color
  const pick = document.getElementById('styColorPick');
  pick.addEventListener('input', () => {
    document.getElementById('styColor').value = pick.value;
    applyStyleProp('color', pick.value);
  });
  
  // Color Swatches
  document.querySelectorAll('.swatch').forEach(btn => {
    btn.addEventListener('click', () => {
      const c = btn.dataset.color;
      document.getElementById('styColor').value = c;
      document.getElementById('styColorPick').value = c;
      applyStyleProp('color', c);
    });
  });
  
  // Bump buttons (+/-)
  const BUMP_PROP_MAP = { styFs: 'font-size', styLh: 'line-height', styLs: 'letter-spacing', styGap: 'gap' };
  function bumpHandler(btn) {
    const targetId = btn.dataset.target;
    const inp = document.getElementById(targetId);
    if (!inp) return;
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
    
    if (prop === 'gap') {
      const cssVal = formatForApply('gap', inp.value);
      if (CURRENT_FOLDER && PROJECT_STATES[CURRENT_FOLDER]) {
        const state = PROJECT_STATES[CURRENT_FOLDER];
        ['.s3-cards', '.s5-list'].forEach(sel => {
          state.styles[sel] = state.styles[sel] || {};
          state.styles[sel]['gap'] = cssVal;
        });
        
        const iframeContainer = document.querySelector('.iframe-container.editing');
        if (iframeContainer) {
          const iframe = iframeContainer.querySelector('iframe');
          if (iframe && iframe.contentWindow) {
            ['.s3-cards', '.s5-list'].forEach(sel => {
              iframe.contentWindow.postMessage({t: 'set-style', sel: sel, prop: 'gap', val: cssVal}, '*');
            });
          }
        }
        queueAutoSave(CURRENT_FOLDER);
      }
    } else if (prop) {
      applyStyleProp(prop, inp.value);
    }
    autosizeInput(inp);
  }
  
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
  
  // Stylebar target select general/highlight
  const btnGeneral = document.getElementById('btnSelectGeneral');
  const btnHighlight = document.getElementById('btnSelectHighlight');
  
  btnGeneral.onmousedown = (e) => {
    e.preventDefault();
    if (CURRENT_SEL && CURRENT_SEL.includes(' em')) {
      CURRENT_SEL = CURRENT_SEL.split(' ')[0];
      updateStyleBarInputs();
    }
  };
  
  btnHighlight.onmousedown = (e) => {
    e.preventDefault();
    if (CURRENT_SEL && !CURRENT_SEL.includes(' em')) {
      if (CURRENT_FOLDER) {
        saveProjectToUndoStack(CURRENT_FOLDER);
      }
      CURRENT_SEL = CURRENT_SEL + ' em';
      updateStyleBarInputs();
      
      // Gửi lệnh wrap-highlight xuống iframe đang edit
      const iframeContainer = document.querySelector('.iframe-container.editing');
      if (iframeContainer) {
        const iframe = iframeContainer.querySelector('iframe');
        if (iframe && iframe.contentWindow) {
          iframe.contentWindow.postMessage({ t: 'wrap-highlight' }, '*');
        }
      }
    }
  };
}

// Window Message Listener từ iframe
window.addEventListener('message', async (e) => {
  const m = e.data || {};
  if (m.t === 'focus') {
    CURRENT_FOLDER = m.path;
    CURRENT_ELEMENT_ID = m.id;
    CURRENT_SEL = m.sel;
    
    // Hiển thị Stylebar
    document.getElementById('stylebar').classList.add('show');
    document.getElementById('styleTarget').innerHTML = `Đang chỉnh: <code style="color:#D4AF37;">${m.id}</code>`;
    
    // Đọc state
    await loadProjectState(CURRENT_FOLDER);
    
    // Khởi tạo Undo Stack cho project này nếu chưa có
    if (CURRENT_FOLDER && (!PROJECT_UNDO_STACKS[CURRENT_FOLDER] || PROJECT_UNDO_STACKS[CURRENT_FOLDER].length === 0)) {
      saveProjectToUndoStack(CURRENT_FOLDER);
    }
  } else if (m.t === 'changed') {
    if (PROJECT_STATES[m.path]) {
      // Lưu undo stack trước và sau khi thay đổi
      saveProjectToUndoStack(m.path);
      PROJECT_STATES[m.path].data[m.id] = m.html;
      queueAutoSave(m.path);
      saveProjectToUndoStack(m.path);
    }
  } else if (m.t === 'undo') {
    performProjectUndo(m.path);
  }
});

// Click outside to clear stylebar selection
document.addEventListener('mousedown', (e) => {
  if (!e.target.closest('#stylebar') && !e.target.closest('.iframe-container') && !e.target.closest('.modal')) {
    document.getElementById('stylebar').classList.remove('show');
    document.getElementById('styleTarget').textContent = 'Click vào chữ trong ô preview để chỉnh styles';
    document.querySelectorAll('.iframe-container.editing').forEach(el => {
      el.classList.remove('editing');
    });
    CURRENT_SEL = null;
    ACTIVE_EM = null;
  }
});

// Xử lý Checkbox Chọn ALL
function toggleSelectAll() {
  const allChecked = document.getElementById('selectAllCheckbox').checked;
  const checkboxes = document.querySelectorAll('.project-checkbox');
  checkboxes.forEach(cb => {
    const tr = cb.closest('tr');
    if (tr && tr.style.display !== 'none') {
      cb.checked = allChecked;
    }
  });
}

function updateSelectAllState() {
  const selectAll = document.getElementById('selectAllCheckbox');
  const visibleCheckboxes = Array.from(document.querySelectorAll('.project-checkbox')).filter(cb => {
    const tr = cb.closest('tr');
    return tr && tr.style.display !== 'none';
  });
  
  if (visibleCheckboxes.length === 0) {
    selectAll.checked = false;
    return;
  }
  
  const allChecked = visibleCheckboxes.every(cb => cb.checked);
  selectAll.checked = allChecked;
}

function toggleHidePublished() {
  renderTable();
}

// Mở thư mục dự án qua explorer
async function openProjectFolder(folderPath) {
  try {
    await fetch('/open-folder?path=' + encodeURIComponent(folderPath), { method: 'POST' });
  } catch (e) {
    console.warn('Lỗi mở folder:', e);
  }
}

// Gửi lệnh batch run cho nhiều thư mục
let batchPollInterval = null;

async function triggerBatch(action) {
  const checkedBoxes = document.querySelectorAll('.project-checkbox:checked');
  if (checkedBoxes.length === 0) {
    alert('Vui lòng tích chọn ít nhất một thư mục dự án!');
    return;
  }
  
  const paths = Array.from(checkedBoxes).map(cb => cb.dataset.path);
  setStatus(`Bắt đầu chạy batch ${action.toUpperCase()} cho ${paths.length} thư mục...`, 'busy');
  
  try {
    const r = await fetch('/api/video-9x16/batch-run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ paths: paths, action: action })
    });
    const j = await r.json();
    if (j.ok) {
      document.getElementById('batchProgressContainer').style.display = 'flex';
      startBatchStatusPolling();
    } else {
      setStatus(`Không thể khởi chạy hàng loạt: ${j.msg || ''}`, 'err');
    }
  } catch (e) {
    setStatus(`Lỗi kết nối batch: ${e.message}`, 'err');
  }
}

// Chạy tác vụ quick-fix cho 1 thư mục dự án
async function runQuickAction(folderPath, action) {
  setStatus(`Khởi chạy tác vụ ${action.toUpperCase()} cho dự án...`, 'busy');
  try {
    const r = await fetch('/api/video-9x16/batch-run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ paths: [folderPath], action: action })
    });
    const j = await r.json();
    if (j.ok) {
      document.getElementById('batchProgressContainer').style.display = 'flex';
      startBatchStatusPolling();
    } else {
      setStatus(`Lỗi quick action: ${j.msg || ''}`, 'err');
    }
  } catch (e) {
    setStatus(`Lỗi kết nối quick action: ${e.message}`, 'err');
  }
}

// Polling cập nhật tiến trình chạy batch ngầm
function startBatchStatusPolling() {
  if (batchPollInterval) clearInterval(batchPollInterval);
  
  batchPollInterval = setInterval(async () => {
    try {
      const r = await fetch('/api/video-9x16/batch-status');
      const s = await r.json();
      
      const progressContainer = document.getElementById('batchProgressContainer');
      const progressText = document.getElementById('batchProgressText');
      const progressFg = document.getElementById('batchProgressFg');
      const logEl = document.getElementById('renderLog');
      
      if (s.running) {
        progressContainer.style.display = 'flex';
        progressText.textContent = `⚙️ ${s.current_action.toUpperCase()}: ${s.current_index}/${s.total} (${s.current_path})`;
        progressFg.style.width = `${s.progress_pct}%`;
        
        if (s.log && s.log.length > 0) {
          logEl.textContent = s.log.join('\n');
          logEl.scrollTop = logEl.scrollHeight;
        }
      } else {
        clearInterval(batchPollInterval);
        batchPollInterval = null;
        progressContainer.style.display = 'none';
        setStatus('Hoàn thành hàng loạt các tác vụ! Đang cập nhật bảng...');
        await reload();
      }
    } catch (e) {
      console.warn('Lỗi polling batch status:', e);
    }
  }, 2000);
}

// Heartbeat
setInterval(() => { fetch('/heartbeat').catch(()=>{}); }, 5000);

// Template selectors logic
function showTemplateModal() {
  if (!CURRENT_FOLDER) {
    alert('Vui lòng click vào text của một dự án để active trước khi lưu template!');
    return;
  }
  document.getElementById('templateModal').style.display = 'flex';
  loadTemplateList();
}

function closeTemplateModal() {
  document.getElementById('templateModal').style.display = 'none';
}

async function loadTemplateList() {
  const listDiv = document.getElementById('templateList');
  listDiv.innerHTML = '<div style="color: #ffcc00; font-size: 13px;">Đang tải danh sách...</div>';
  try {
    const r = await fetch('/list-templates');
    // Vì list-templates API đã bị xóa ở GET handler mới, ta sẽ fallback hoặc list tĩnh,
    // hoặc ta phục vụ trực tiếp bằng API list-templates.
    // Chờ đã! Trong do_GET của chúng ta ở trên, route list-templates có không?
    // À, trong do_GET mới, route /list-templates đã bị xóa!
    // Chúng ta cần khôi phục lại route /list-templates trong do_GET để Sếp có thể load danh sách template.
    // Em sẽ viết list template tĩnh hoặc gọi API. Phải khôi phục API /list-templates.
    const j = await r.json();
    if (j.ok && j.templates) {
      listDiv.innerHTML = '';
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
      listDiv.innerHTML = '<div style="color: #ff0000; font-size: 13px;">Lỗi tải danh sách template</div>';
    }
  } catch (e) {
    // List tĩnh fallback đề phòng API lỗi
    listDiv.innerHTML = `
      <button class="secondary" style="width:100%; text-align:left; text-transform:none; margin-bottom:6px;" onclick="confirmSaveToTemplate('01_Text_KID')">01_Text_KID (Default)</button>
    `;
  }
}

async function confirmSaveToTemplate(templateName) {
  if (!CURRENT_FOLDER || !PROJECT_STATES[CURRENT_FOLDER]) return;
  if (!confirm(`Bạn có chắc chắn muốn ghi đè styles của dự án đang chọn vào template gốc "${templateName}"?`)) return;
  closeTemplateModal();
  setStatus(`Đang ghi đè styles vào template ${templateName}…`, 'busy');
  const t0 = performance.now();
  try {
    const payload = JSON.stringify({ styles: PROJECT_STATES[CURRENT_FOLDER].styles });
    const r = await fetch(`/save-to-template?template=${encodeURIComponent(templateName)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: payload
    });
    const j = await r.json();
    const dt = (performance.now() - t0).toFixed(0);
    if (j.ok) {
      setStatus(`Ghi đè styles vào template thành công! (${dt}ms)`, 'ok');
    } else {
      setStatus(`Lưu template thất bại: ${j.msg || ''}`, 'err');
    }
  } catch (e) {
    console.error('Lỗi lưu template:', e);
    setStatus('Lỗi lưu template: ' + e.message, 'err');
  }
}

async function loadWorkspaces() {
  try {
    const r = await fetch('/api/workspaces');
    const j = await r.json();
    if (j.ok && j.workspaces) {
      const select = document.getElementById('workspaceSelect');
      select.innerHTML = '';
      j.workspaces.forEach(ws => {
        const opt = document.createElement('option');
        opt.value = ws.path;
        opt.textContent = ws.name;
        opt.selected = ws.current;
        select.appendChild(opt);
      });
    }
  } catch (e) {
    console.error('Lỗi load workspaces:', e);
  }
}

async function switchWorkspace(path) {
  setStatus('Đang chuyển đổi workspace...', 'busy');
  try {
    const r = await fetch('/api/launch-workspace', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ path: path })
    });
    const j = await r.json();
    if (j.ok && j.port) {
      setStatus('Chuyển đổi thành công! Đang chuyển hướng...', 'ok');
      window.location.href = `http://localhost:${j.port}/`;
    } else {
      setStatus('Lỗi chuyển đổi workspace: ' + (j.msg || ''), 'err');
      alert('Không thể chuyển đổi sang workspace được chọn: ' + (j.msg || ''));
    }
  } catch (e) {
    setStatus('Lỗi kết nối chuyển đổi: ' + e.message, 'err');
    alert('Lỗi kết nối chuyển đổi: ' + e.message);
  }
}

// Khởi chạy
wireStyleBar();
reload();
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
    vbs_path.write_text(vbs_content, encoding="ascii", errors="replace")

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

    # Chuẩn hóa boundaries để đảm bảo tăng dần và không vượt quá duration
    last_val = 0.0
    for sid in sids_sorted:
        val = boundaries[sid]
        if val < last_val:
            val = last_val
        if val > duration:
            val = duration
        boundaries[sid] = round(val, 2)
        last_val = val

    timeline = {}
    for i, sid in enumerate(sids_sorted):
        in_t = boundaries[sid]
        out_t = boundaries[sids_sorted[i+1]] if i+1 < len(sids_sorted) else duration
        # Đảm bảo in_t <= out_t <= duration
        if in_t > duration: in_t = duration
        if out_t > duration: out_t = duration
        if out_t < in_t:
            out_t = in_t
        timeline[sid] = {"in": round(in_t, 2), "out": round(out_t, 2)}
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

def diagnose_and_fix_timeline(workspace_path: Path):
    co_file = workspace_path / "content.json"
    tr_file = workspace_path / "transcript.json"
    if not co_file.exists() or not tr_file.exists():
        return False, "Thiếu content.json hoặc transcript.json để tự động chẩn đoán."
        
    try:
        co = json.loads(co_file.read_text(encoding="utf-8"))
        tr = json.loads(tr_file.read_text(encoding="utf-8"))
        
        duration = float(tr.get("duration", 0.0))
        segments = tr.get("segments", [])
        
        timeline = co.get("timeline", {})
        sids = sorted(timeline.keys())
        
        # 1. Chuẩn hóa timeline tổng thể s1 -> s6 đảm bảo tăng dần tuyến tính
        last_t = 0.0
        for sid in sids:
            t_in = float(timeline[sid].get("in", 0.0))
            t_out = float(timeline[sid].get("out", 0.0))
            
            if t_in < last_t:
                timeline[sid]["in"] = round(last_t, 2)
                t_in = last_t
            if t_out < t_in:
                timeline[sid]["out"] = round(t_in, 2)
                t_out = t_in
            last_t = t_out
            
        # Đảm bảo out của scene cuối cùng khớp với duration thực tế
        if sids:
            last_sid = sids[-1]
            if duration > 0:
                timeline[last_sid]["out"] = round(duration, 2)
                if timeline[last_sid]["in"] > duration:
                    timeline[last_sid]["in"] = round(duration, 2)
                    
        # 2. Sửa lỗi trùng keyword gây thụt lùi/đè timeline trong s3 & s5
        import re
        def clean_text(text):
            return re.sub(r'[^\w\s]', '', str(text).lower()).strip()
            
        def find_segment_start(keywords, after_t, before_t):
            for seg in segments:
                # Bắt buộc bắt đầu từ after_t (mốc của card trước)
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
            tl = timeline.get(sid)
            if not scene or not tl: continue
            
            has_cards = "cards" in scene
            has_items = "items" in scene
            if not (has_cards or has_items): continue
            
            et = {}
            current_after = tl["in"]
            
            for kws, c_key, i_key in kw_groups:
                key = c_key if has_cards else i_key
                t = find_segment_start(kws, current_after, tl["out"])
                if t is not None:
                    et[key] = round(t, 2)
                    current_after = t
            
            # Kiểm tra xem các mốc có được lấy tuần tự hay bị lỗi đảo ngược không
            keys_present = [k for k in ["c1", "c2", "c3"] if k in et] if has_cards else [k for k in ["i1", "i2", "i3"] if k in et]
            is_valid = True
            for idx in range(len(keys_present) - 1):
                if et[keys_present[idx+1]] <= et[keys_present[idx]]:
                    is_valid = False
                    break
                    
            # Nếu mốc lỗi hoặc thiếu mốc, ta phân bổ tuyến tính đều (Linear Distribution) để tránh lỗi đen/mất card
            if not is_valid or len(keys_present) < 3:
                total_duration = tl["out"] - tl["in"]
                step = total_duration / 4.0
                prefix = "c" if has_cards else "i"
                et[prefix + "1"] = round(tl["in"] + step, 2)
                et[prefix + "2"] = round(tl["in"] + step * 2, 2)
                et[prefix + "3"] = round(tl["in"] + step * 3, 2)
                print(f"[SYSTEM] [fix-bug] Tự động phân bổ tuyến tính element_times cho {sid}: {et}")
                
            scene["element_times"] = et

        if "voice" not in co: co["voice"] = {}
        co["voice"]["duration"] = duration
        co["timeline"] = timeline
        
        safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
        return True, "Chẩn đoán & sửa lỗi timeline thành công!"
    except Exception as e:
        return False, f"Lỗi xử lý chẩn đoán: {str(e)}"

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
            # Đọc từ content.json trước để tránh gọi ffprobe liên tục gây đơ
            co_file = proj_dir / "content.json"
            co_dur = 0.0
            if co_file.exists():
                co = json.loads(co_file.read_text(encoding="utf-8"))
                co_dur = co.get("voice", {}).get("duration", 0.0)
            voice_duration = co_dur
            if voice_duration <= 0.0:
                out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_file)], capture_output=True, text=True, timeout=5).stdout.strip()
                voice_duration = float(out) if out else 0.0
                # Ghi ngược vào content.json để cache
                if co_file.exists() and voice_duration > 0.0:
                    co["voice"]["duration"] = voice_duration
                    safe_write_file(co_file, json.dumps(co, indent=2, ensure_ascii=False))
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
        
    # Thực hiện chẩn đoán lỗi dự án
    errors = []
    index_file = proj_dir / "index.html"
    if not index_file.exists():
        errors.append("Thiếu file index.html (Chưa render Visual)")
    else:
        try:
            html_content = index_file.read_text(encoding="utf-8")
            # 1. Kiểm tra chữ rác JSON / cấu hình chưa điền
            if "ALLOW_EM" in html_content or "{'type':" in html_content or "{'TYPE':" in html_content or "max_len" in html_content:
                errors.append("Lỗi điền kịch bản (Còn rác JSON/cấu hình)")
            
            # 2. Kiểm tra timeline scene
            tl_match = re.search(r'const __TL\s*=\s*(\{.*?\});', html_content)
            if tl_match:
                tl_data = json.loads(tl_match.group(1))
                for s_name, s_val in tl_data.items():
                    s_in = float(s_val.get("in", 0.0))
                    s_out = float(s_val.get("out", 0.0))
                    if s_out < s_in:
                        errors.append(f"Lỗi timeline Scene {s_name[1:]} (Thời gian out < in)")
                    elif s_name != "s6" and s_out == s_in:
                        errors.append(f"Lỗi timeline Scene {s_name[1:]} (Thời lượng bằng 0)")
        except Exception as e_diag:
            errors.append(f"Lỗi đọc/phân tích index.html: {str(e_diag)}")

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
        "warnings": warnings,
        "errors": errors
    }

def list_projects():
    # Nhận diện WORK là dự án con hay workspace cha
    is_single_project = False
    if (WORK / "index.html").exists() or (WORK / "content.json").exists() or (WORK / "script.txt").exists():
        is_single_project = True
        parent_dir = WORK.parent
    else:
        parent_dir = WORK
    projects = []
    
    db_items = []
    db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
    if db_path.exists():
        try:
            db_items = json.loads(db_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[SYSTEM] Lỗi load UP database: {e}")
            
    if is_single_project:
        # Chế độ Single-Project: chỉ hiển thị duy nhất dự án WORK hiện tại
        if WORK.exists():
            info = get_project_files_info(WORK, db_items)
            projects.append(info)
    else:
        # Chế độ Multi-Project: quét hiển thị toàn bộ các dự án con
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
                cmd = [PY, str(fill_script), "--template", "01_Text_KID", "--script-file", str(script_txt), "--output-dir", str(proj_path)]
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if p.returncode != 0:
                    raise RuntimeError(f"fill_content.py fail: {p.stderr}")
                    
                tpl_dir = TEMPLATES / "01_Text_KID"
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
                
                ensure_vbs(proj_path, "01_Text_KID")
                msg = f"[SYSTEM] Thành công Visual cho {proj_path.name}"
                print(msg)
                BATCH_STATUS["log"].append(msg)
                
            elif action == "voice":
                script_txt = proj_path / "script.txt"
                if not script_txt.exists():
                    raise FileNotFoundError(f"Không tìm thấy script.txt trong {proj_path.name}")
                
                voice_name = "TT_06"
                profile = PAGE_PROFILES.get("kid")
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
                
                tpl_dir = TEMPLATES / "01_Text_KID"
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
                
                reset_video_approval(proj_path)
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

def uppercase_html(text):
    if not text: return ""
    import re
    parts = re.split(r'(<[^>]+>)', text)
    for i in range(len(parts)):
        if not parts[i].startswith('<'):
            parts[i] = parts[i].upper()
    res = "".join(parts)
    res = re.sub(r'&[A-Z0-9]+;', lambda m: m.group(0).lower(), res)
    return res

def reset_video_approval(folder_path):
    db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
    if not db_path.exists():
        return
    try:
        db_items = json.loads(db_path.read_text(encoding="utf-8"))
        import re
        folder_name = folder_path.name
        match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
        slug = match.group(1) if match else folder_name
        slug = slug.strip().lower()
        
        db_changed = False
        for item in db_items:
            is_match = False
            url_val = item.get("url", "")
            if url_val:
                normalized_url = os.path.abspath(os.path.dirname(url_val) if url_val.lower().endswith(".mp4") else url_val).lower()
                normalized_folder = os.path.abspath(str(folder_path)).lower()
                if normalized_url == normalized_folder:
                    is_match = True
            if not is_match:
                item_topic = item.get("topic", "")
                if item_topic:
                    item_slug = slugify_vietnamese(item_topic)
                    if item_slug == slug or item_slug in slug or slug in item_slug:
                        is_match = True
            if is_match:
                if item.get("is_video_approved") or item.get("video_approved"):
                    item["is_video_approved"] = False
                    item["video_approved"] = False
                    db_changed = True
                    print(f"[SYSTEM] [reset-video-approval] Reset approval cho: {folder_name}")
        if db_changed:
            with open(db_path, 'w', encoding='utf-8') as f:
                json.dump(db_items, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
    except Exception as e:
        print(f"[SYSTEM] reset_video_approval error: {e}")

class Handler(BaseHTTPRequestHandler):

    render_status = {"running": False, "last_out": ""}

    def log_message(self, fmt, *args): pass  # silence access log

    def _send(self, code, ctype, body):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            if IS_SINGLE_MODE:
                # Đọc động từ đĩa mỗi request — Sếp chỉ cần F5 là thấy thay đổi ngay
                _spath = Path(__file__).parent / "single_editor.html"
                if _spath.exists():
                    try:
                        _html = _spath.read_text(encoding="utf-8")
                        self._send(200, "text/html; charset=utf-8", _html)
                        return
                    except Exception:
                        pass
                # fallback sang cache nếu đọc đĩa lỗi
                if EDITOR_SINGLE_HTML:
                    self._send(200, "text/html; charset=utf-8", EDITOR_SINGLE_HTML)
                    return
            self._send(200, "text/html; charset=utf-8", EDITOR_HTML)
        elif path == "/api/projects":
            try:
                projects = list_projects()
                self._send(200, "application/json; charset=utf-8", json.dumps(projects, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/api/video-profiles":
            try:
                vp_file = Path(__file__).parent / "video_profiles.json"
                if vp_file.exists():
                    self._send(200, "application/json; charset=utf-8", vp_file.read_text(encoding="utf-8"))
                else:
                    self._send(200, "application/json; charset=utf-8", json.dumps({"profiles":[],"templates":[],"brands":[]}, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path.startswith("/api/template-preview/"):
            try:
                name = path.split("/api/template-preview/", 1)[1]
                safe_name = re.sub(r'[^a-zA-Z0-9_.]', '', name)
                mp4 = Path(__file__).resolve().parents[5] / "B-Go" / "_Building" / "html_Video" / f"{safe_name}.mp4"
                if mp4.exists():
                    data = mp4.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(404, "text/plain; charset=utf-8", f"Not found: {mp4}")
            except Exception as e:
                self._send(500, "text/plain; charset=utf-8", str(e))
        elif path == "/api/pick-folder":
            try:
                import tkinter as _tk
                from tkinter import filedialog as _fd
                _root_tk = _tk.Tk(); _root_tk.withdraw(); _root_tk.attributes("-topmost", True)
                _initial = Path(__file__).parent / ".video_root"
                _init_dir = _initial.read_text(encoding="utf-8").strip() if _initial.exists() else "E:/HuuDat/VIDEO"
                picked = _fd.askdirectory(title="Chon thu muc tong chua cac brand", initialdir=_init_dir)
                _root_tk.destroy()
                if picked:
                    self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "path": picked.replace("\\", "/")}))
                else:
                    self._send(200, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": "cancelled"}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/api/video-root":
            try:
                _rf = Path(__file__).parent / ".video_root"
                _root = _rf.read_text(encoding="utf-8").strip() if _rf.exists() else "E:/HuuDat/VIDEO"
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "root": _root}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/api/workspace-tree":
            try:
                _rf = Path(__file__).parent / ".video_root"
                fb_root = Path(_rf.read_text(encoding="utf-8").strip() if _rf.exists() else "E:/HuuDat/VIDEO")
                # Clone UP logic line 2699-2731: load DB to get is_visual_approved per folder
                approved_map = {}
                try:
                    db_path_a = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
                    if db_path_a.exists():
                        db_items_a = json.loads(db_path_a.read_text(encoding="utf-8"))
                        if isinstance(db_items_a, list):
                            for item_a in db_items_a:
                                fn_a = item_a.get("folder_name") or item_a.get("folder")
                                if fn_a:
                                    approved_map[fn_a] = item_a.get("is_visual_approved") in (True, 1, "true") or item_a.get("visual_approved") in (True, 1, "true")
                except Exception:
                    pass
                tree = {"brands": []}
                if fb_root.exists():
                    for brand_dir in sorted(fb_root.iterdir()):
                        if not brand_dir.is_dir() or brand_dir.name.startswith("_") or brand_dir.name.startswith("."):
                            continue
                        if "__" in brand_dir.name:
                            continue
                        workspaces = []
                        for ws in sorted(brand_dir.iterdir()):
                            if not ws.is_dir() or ws.name.startswith("."):
                                continue
                            try:
                                files = [f.name for f in ws.iterdir() if f.is_file()]
                            except Exception:
                                continue
                            if "posted_info.json" in files:
                                continue
                            has_script = "script.txt" in files
                            mp4_files = [f for f in files if f.lower().endswith('.mp4')]
                            voice_files = [f for f in files if f.lower().endswith(('.mp3', '.wav')) and not f.endswith('.bak')]
                            has_voice = len(voice_files) > 0
                            has_video = len(mp4_files) > 0
                            is_voice_outdated = False
                            is_video_outdated = False
                            if has_script:
                                try:
                                    script_mtime = (ws / "script.txt").stat().st_mtime
                                    if has_voice:
                                        latest_voice_mtime = max((ws / f).stat().st_mtime for f in voice_files)
                                        if script_mtime > latest_voice_mtime + 2:
                                            is_voice_outdated = True
                                    if has_video:
                                        latest_mp4_mtime = max((ws / f).stat().st_mtime for f in mp4_files)
                                        if script_mtime > latest_mp4_mtime + 2:
                                            is_video_outdated = True
                                except Exception:
                                    pass
                            voice_approved = (ws / ".voice_approved").exists()
                            video_approved = (ws / ".video_approved").exists()
                            voice_state = "approved" if voice_approved else ("missing" if not has_voice else ("stale" if is_voice_outdated else "ready"))
                            video_state = "approved" if video_approved else ("missing" if not has_video else ("stale" if is_video_outdated else "ready"))
                            script_state = "missing" if not has_script else ("approved" if approved_map.get(ws.name, False) else "pending")
                            is_current = False
                            try:
                                is_current = ws.resolve() == WORK.resolve()
                            except Exception:
                                pass
                            workspaces.append({
                                "name": ws.name,
                                "path": str(ws.resolve()).replace("\\", "/"),
                                "script_state": script_state,
                                "voice_state": voice_state,
                                "video_state": video_state,
                                "is_current": is_current,
                                "mtime": ws.stat().st_mtime
                            })
                        # Sort theo tên (descending) — tên đã encode timestamp T<MM>.<DD>_<HH>h<mm>_*
                        # KHÔNG dùng folder mtime vì sẽ thay đổi khi có file ghi vào (e.g. .editor_port)
                        workspaces.sort(key=lambda x: x["name"], reverse=True)
                        tree["brands"].append({
                            "id": brand_dir.name,
                            "name": brand_dir.name,
                            "path": str(brand_dir.resolve()).replace("\\", "/"),
                            "workspaces": workspaces,
                            "count": len(workspaces)
                        })
                self._send(200, "application/json; charset=utf-8", json.dumps(tree, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/api/video-9x16/batch-status":
            self._send(200, "application/json; charset=utf-8", json.dumps(BATCH_STATUS, ensure_ascii=False))
        elif path == "/api/workspaces":
            try:
                # Nhận diện thư mục chứa các workspace (workspace_root)
                if (WORK / "index.html").exists() or (WORK / "content.json").exists() or (WORK / "script.txt").exists():
                    workspace_root = WORK.parent.parent
                else:
                    workspace_root = WORK.parent
                
                workspaces = []
                if workspace_root.exists():
                    for d in workspace_root.iterdir():
                        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                            port = None
                            port_file = d / ".editor_port"
                            if port_file.exists():
                                try:
                                    port = int(port_file.read_text(encoding="utf-8").strip())
                                except Exception: pass
                            
                            # Đánh dấu current nếu d trùng với workspace cha của WORK hiện tại
                            is_current = False
                            if (WORK / "index.html").exists() or (WORK / "content.json").exists() or (WORK / "script.txt").exists():
                                is_current = d.resolve() == WORK.parent.resolve()
                            else:
                                is_current = d.resolve() == WORK.resolve()
                                
                            workspaces.append({
                                "name": d.name,
                                "path": str(d.resolve()).replace("\\", "/"),
                                "port": port,
                                "current": is_current
                            })
                workspaces = sorted(workspaces, key=lambda x: x["name"])
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "workspaces": workspaces}, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
                
        elif path == "/video-info":
            try:
                folder_path = get_path_from_query(self.path)
                mp4_files = []
                if folder_path.exists():
                    for p in folder_path.iterdir():
                        if p.is_file() and p.suffix.lower() == ".mp4" and not p.name.startswith("._"):
                            mp4_files.append(p)
                
                script_file = folder_path / "script.txt"
                script_mtime = script_file.stat().st_mtime if script_file.exists() else 0
                
                # Đọc voice_mtime của file voice đang hoạt động (được trỏ trong content.json)
                voice_mtime = 0
                co_file = folder_path / "content.json"
                if co_file.exists():
                    try:
                        co = json.loads(co_file.read_text(encoding="utf-8"))
                        v_file = co.get("voice", {}).get("file")
                        if v_file:
                            v_path = folder_path / v_file
                            if v_path.exists():
                                voice_mtime = v_path.stat().st_mtime
                    except Exception: pass
                
                if mp4_files:
                    # Lấy file video mp4 có thời gian chỉnh sửa mới nhất
                    mp4_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                    target_file = mp4_files[0]
                    self._send(200, "application/json; charset=utf-8", json.dumps({
                        "exists": True,
                        "filename": target_file.name,
                        "mtime": target_file.stat().st_mtime,
                        "script_mtime": script_mtime,
                        "voice_mtime": voice_mtime
                    }, ensure_ascii=False))
                else:
                    self._send(200, "application/json; charset=utf-8", json.dumps({
                        "exists": False,
                        "script_mtime": script_mtime,
                        "voice_mtime": voice_mtime
                    }, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
                
        elif path == "/api/get-sibling":
            try:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                direction = int(params.get("dir", ["1"])[0])
                
                current_ws = Path(WORKSPACE).resolve()
                parent_dir = current_ws.parent
                
                siblings = []
                for p in parent_dir.iterdir():
                    if p.is_dir() and not p.name.startswith('.'):
                        if (p / "index.html").exists() or (p / "content.json").exists():
                            siblings.append(p)
                
                siblings.sort(key=lambda x: x.name.lower())
                
                if not siblings:
                    self._send(404, "application/json", '{"ok":false,"msg":"No sibling projects found"}')
                    return
                    
                try:
                    curr_idx = siblings.index(current_ws)
                except ValueError:
                    curr_idx = 0
                    
                new_idx = (curr_idx + direction) % len(siblings)
                target_ws = siblings[new_idx]
                
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "path": str(target_ws.resolve())}, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/list-templates":
            try:
                templates_dir = Path(__file__).parent.parent
                subdirs = [d.name for d in templates_dir.iterdir() if d.is_dir() and not d.name.startswith("_")]
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "templates": sorted(subdirs)}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
        elif path == "/render-status":
            self._send(200, "application/json", json.dumps(Handler.render_status))
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
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                if ext in [".ttf", ".otf", ".woff", ".woff2"]:
                    self.send_header("Cache-Control", "public, max-age=31536000")
                elif ext in [".wav", ".mp3", ".mp4"]:
                    self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(fp.read_bytes())
            else:
                self._send(404, "text/plain", "Not found")
        elif path == "/fonts/UTM-Cookies.ttf":
            font_path = Path(__file__).parent.parent / "_fonts" / "UTM-Cookies.ttf"
            if font_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "font/ttf")
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=31536000")
                self.end_headers()
                self.wfile.write(font_path.read_bytes())
            else:
                self._send(404, "text/plain", "Font not found")
        elif path == "/load":
            folder_path = get_path_from_query(self.path)
            sync_latest_voice(folder_path)
            
            # Đọc trạng thái is_visual_approved, is_video_approved, is_voice_approved từ bsimple_content_data.json thưa Sếp!
            is_visual_approved = False
            is_video_approved = False
            is_voice_approved = False
            db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
            if db_path.exists():
                try:
                    db_items = json.loads(db_path.read_text(encoding="utf-8"))
                    folder_name = folder_path.name
                    match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
                    slug = match.group(1) if match else folder_name
                    slug = slug.strip().lower()
                    for item in db_items:
                        is_match = False
                        url_val = item.get("url", "")
                        if url_val:
                            normalized_url = os.path.abspath(os.path.dirname(url_val) if url_val.lower().endswith(".mp4") else url_val).lower()
                            normalized_folder = os.path.abspath(str(folder_path)).lower()
                            if normalized_url == normalized_folder:
                                is_match = True
                        if not is_match:
                            item_topic = item.get("topic", "")
                            if item_topic:
                                item_slug = slugify_vietnamese(item_topic)
                                if item_slug == slug or item_slug in slug or slug in item_slug:
                                    is_match = True
                        if is_match:
                            is_visual_approved = item.get("is_visual_approved") in (True, 1, "true") or item.get("visual_approved") in (True, 1, "true")
                            is_video_approved = item.get("is_video_approved") in (True, 1, "true") or item.get("video_approved") in (True, 1, "true")
                            is_voice_approved = item.get("is_voice_approved") in (True, 1, "true")
                            break
                except Exception as e_db_load:
                    print(f"[SYSTEM] Lỗi đọc is_visual_approved/is_video_approved trong /load: {e_db_load}")

            index_file = folder_path / "index.html"
            if not index_file.exists():
                self._send(200, "application/json; charset=utf-8", json.dumps({"fields": FIELDS, "data": {}, "styleFields": STYLE_FIELDS, "styles": {}, "is_visual_approved": is_visual_approved, "is_video_approved": is_video_approved, "is_voice_approved": is_voice_approved}, ensure_ascii=False))
                return
            html = index_file.read_text(encoding="utf-8")
            data = {f["id"]: clean_boom_boom(get_inner(html, f["id"])) for f in FIELDS}
            styles = {sf["sel"]: {p: get_css(html, sf["sel"], p) for p in sf["props"]} for sf in STYLE_FIELDS}
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"fields": FIELDS, "data": data, "styleFields": STYLE_FIELDS, "styles": styles, "is_visual_approved": is_visual_approved, "is_video_approved": is_video_approved, "is_voice_approved": is_voice_approved}, ensure_ascii=False))
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
                f"const FOLDER_PATH={json.dumps(folder_path.resolve().as_posix())};"
                "let ACTIVE_EM = null; let LAST_RANGE = null; let LAST_SENT_ID = null; let LAST_SENT_SEL = null;"
                "function setupEdit(){"
                "  EDIT_IDS.forEach(id=>{"
                "    const el=document.getElementById(id); if(!el || el._wired) return;"
                "    el._wired=true;"
                "    el.setAttribute('contenteditable','true');"
                "    el.addEventListener('mousedown',e=>e.stopPropagation(),true);"
                "    el.addEventListener('focus',()=>{"
                "      const sel='.'+ (el.className.match(/\\bs\\d-[\\w-]+/)||[''])[0];"
                "      LAST_SENT_ID=id; LAST_SENT_SEL=sel;"
                "      parent.postMessage({t:'focus',scene:SCENE_NUM,id,sel,html:el.innerHTML,path:FOLDER_PATH},'*');"
                "    });"
                "    el.addEventListener('input',()=>{ parent.postMessage({t:'changed',id,html:el.innerHTML,path:FOLDER_PATH},'*'); });"
                "  });"
                "}"
                "function trySeek(){"
                "  if(!window.__TL && typeof __TL !== 'undefined') window.__TL = __TL;"
                "  if(window.__timelines && window.__timelines['root']){"
                "    try{"
                "      const t = window.__TL ? window.__TL['s' + SCENE_NUM] : null;"
                "      let seekTime = 1.5;"
                "      if (t) {"
                "        let t_in = parseFloat(t.in) || 0;"
                "        let t_out = parseFloat(t.out) || 0;"
                "        if (t_out < t_in) t_out = t_in + 5.0;"
                "        const dur = t_out - t_in;"
                "        if (dur <= 2.0) {"
                "          seekTime = t_in + dur * 0.5;"
                "        } else {"
                "          seekTime = t_in + 1.5;"
                "        }"
                "      }"
                "      if(window.gsap) window.gsap.globalTimeline.play();"
                "      window.__timelines['root'].seek(seekTime).pause();"
                "    }catch(e){}"
                "    return true;"
                "  } return false;"
                "}"
                "function init(){"
                "  setupEdit();"
                "  if(!trySeek()){ let n=0; const iv=setInterval(()=>{ if(trySeek()||++n>30) clearInterval(iv); },200); }"
                "  document.addEventListener('selectionchange', () => {"
                "    const sel = window.getSelection();"
                "    if (sel && sel.rangeCount > 0) {"
                "      const range = sel.getRangeAt(0);"
                "      if (!range.collapsed && sel.toString().trim().length > 0) {"
                "        LAST_RANGE = range.cloneRange();"
                "      }"
                "      try {"
                "        let parent = range.commonAncestorContainer;"
                "        if (parent.nodeType === 3) parent = parent.parentNode;"
                "        let em = parent;"
                "        while (em && em.tagName !== 'EM' && em.getAttribute && em.getAttribute('contenteditable') !== 'true') {"
                "          em = em.parentNode;"
                "        }"
                "        const isWithinEm = (em && em.tagName === 'EM');"
                "        let editable = parent;"
                "        while (editable && editable.getAttribute && editable.getAttribute('contenteditable') !== 'true') {"
                "          editable = editable.parentNode;"
                "        }"
                "        if (editable) {"
                "          const baseSel = '.' + (editable.className.match(/\\bs\\d-[\\w-]+/)||[''])[0];"
                "          let targetSel = baseSel;"
                "          if (isWithinEm) {"
                "            ACTIVE_EM = em;"
                "            targetSel = baseSel + ' em';"
                "          } else {"
                "            ACTIVE_EM = null;"
                "          }"
                "          if (editable.id === LAST_SENT_ID && targetSel === LAST_SENT_SEL) return;"
                "          LAST_SENT_ID = editable.id;"
                "          LAST_SENT_SEL = targetSel;"
                "          window.parent.postMessage({t:'focus',scene:SCENE_NUM,id:editable.id,sel:targetSel,html:editable.innerHTML,path:FOLDER_PATH},'*');"
                "          return;"
                "        }"
                "      } catch(err){}"
                "    }"
                "    ACTIVE_EM = null;"
                "  });"
                "  window.addEventListener('keydown', e => {"
                "    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {"
                "      e.preventDefault();"
                "      window.parent.postMessage({t: 'undo', path: FOLDER_PATH}, '*');"
                "    }"
                "  });"
                "  window.addEventListener('message',e=>{"
                "    const m=e.data||{};"
                "    if(m.t==='set-style'){"
                "      if (m.sel.includes(' em') && ACTIVE_EM && document.body.contains(ACTIVE_EM)) {"
                "        ACTIVE_EM.style.setProperty(m.prop, m.val);"
                "        let pNode = ACTIVE_EM;"
                "        while (pNode && pNode.getAttribute && pNode.getAttribute('contenteditable') !== 'true') {"
                "          pNode = pNode.parentNode;"
                "        }"
                "        if (pNode) {"
                "          window.parent.postMessage({t:'changed', id:pNode.id, html:pNode.innerHTML, path:FOLDER_PATH}, '*');"
                "        }"
                "      } else {"
                "        document.querySelectorAll(m.sel).forEach(el=>el.style.setProperty(m.prop,m.val));"
                "      }"
                "    }"
                "    else if(m.t==='set-text'){ const el=document.getElementById(m.id); if(el && document.activeElement!==el) el.innerHTML=m.html; }"
                "    else if(m.t==='wrap-highlight'){"
                "      let range = null;"
                "      const sel = window.getSelection();"
                "      if(sel && sel.rangeCount > 0 && !sel.getRangeAt(0).collapsed && sel.toString().trim().length > 0){"
                "        range = sel.getRangeAt(0);"
                "      } else if (LAST_RANGE) {"
                "        range = LAST_RANGE;"
                "      }"
                "      if(range){"
                "        const em = document.createElement('em');"
                "        em.className = 'hl-custom temp-focus-target';"
                "        try { range.surroundContents(em); } catch(err) {"
                "          const frag = range.extractContents();"
                "          em.appendChild(frag); range.insertNode(em);"
                "        }"
                "        LAST_RANGE = null;"
                "        let editable = em.parentNode;"
                "        while (editable && editable.getAttribute && editable.getAttribute('contenteditable') !== 'true') {"
                "          editable = editable.parentNode;"
                "        }"
                "        if(editable){"
                "          const activeId = editable.id;"
                "          window.parent.postMessage({t:'changed',id:activeId,html:editable.innerHTML,path:FOLDER_PATH},'*');"
                "          setTimeout(() => {"
                "            const targetEm = editable.querySelector('.temp-focus-target');"
                "            if (targetEm) {"
                "              editable.focus();"
                "              const newRange = document.createRange();"
                "              newRange.selectNodeContents(targetEm);"
                "              const newSel = window.getSelection();"
                "              newSel.removeAllRanges();"
                "              newSel.addRange(newRange);"
                "              targetEm.classList.remove('temp-focus-target');"
                "              ACTIVE_EM = targetEm;"
                "              window.parent.postMessage({t:'changed',id:activeId,html:editable.innerHTML,path:FOLDER_PATH},'*');"
                "            }"
                "          }, 30);"
                "        }"
                "      }"
                "    }"
                "    else if(m.t==='trigger-focus'){"
                "      const el=document.querySelector('#s' + SCENE_NUM + ' [contenteditable=\"true\"]') || document.querySelector('[contenteditable=\"true\"]');"
                "      if(el){ el.focus(); try{ const r=document.createRange(); const s=window.getSelection(); r.selectNodeContents(el); r.collapse(false); s.removeAllRanges(); s.addRange(r); }catch(err){} }"
                "    }"
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
            
            # Vô hiệu hóa việc load file audio nặng trong các iframe preview để tránh nghẽn connection mạng của trình duyệt
            html = re.sub(
                r'(<audio[^>]*\bsrc=")([^"]+)(")',
                r'\g<1>#\g<3> preload="none"',
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
            
            script_file = folder_path / "script.txt"
            script_mtime = script_file.stat().st_mtime if script_file.exists() else 0
            
            info = {"exists": False, "filename": "", "size": 0, "duration": 0, "script_mtime": script_mtime}
            if wav and wav.exists():
                info = {
                    "exists": True, 
                    "filename": wav.name, 
                    "size": wav.stat().st_size, 
                    "duration": 0,
                    "mtime": wav.stat().st_mtime,
                    "script_mtime": script_mtime
                }
                try:
                    # Đọc từ content.json trước
                    co_file = folder_path / "content.json"
                    co_dur = 0.0
                    if co_file.exists():
                        co = json.loads(co_file.read_text(encoding="utf-8"))
                        co_dur = co.get("voice", {}).get("duration", 0.0)
                    info["duration"] = co_dur
                    if info["duration"] <= 0:
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

        if path == "/api/video-profiles":
            try:
                data = json.loads(body)
                vp_file = Path(__file__).parent / "video_profiles.json"
                if vp_file.exists():
                    bak = vp_file.with_suffix(".json.bak")
                    bak.write_bytes(vp_file.read_bytes())
                vp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
            return

        if path == "/api/set-root":
            try:
                data = json.loads(body)
                new_root = Path(data.get("root", "")).resolve()
                if not new_root.exists() or not new_root.is_dir():
                    self._send(404, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": f"Not a folder: {new_root}"}))
                    return
                root_file = Path(__file__).parent / ".video_root"
                root_file.write_text(str(new_root).replace("\\", "/"), encoding="utf-8")
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "root": str(new_root).replace("\\", "/")}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
            return

        if path == "/api/switch-workspace":
            try:
                data = json.loads(body)
                new_path = Path(data.get("path", "")).resolve()
                fb_root = Path("E:/HuuDat/VIDEO").resolve()
                if not new_path.exists() or not new_path.is_dir():
                    self._send(404, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": f"Path not found: {new_path}"}))
                    return
                if not str(new_path).startswith(str(fb_root)):
                    self._send(403, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": "Path outside VIDEO root"}))
                    return
                global WORK, WORKSPACE
                WORK = new_path
                WORKSPACE = new_path
                # KHÔNG ghi .editor_port vào workspace mới — sẽ touch folder mtime + làm thay đổi sort order
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True, "workspace": str(new_path).replace("\\", "/")}))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"ok": False, "msg": str(e)}))
            return

        if path == "/save":
            t0 = time.perf_counter()
            folder_path = get_path_from_query(self.path)
            payload = json.loads(body)
            data = payload.get("data") or payload
            styles = payload.get("styles") or {}
            
            # Tự động viết hoa title Scene 1 & 6 (dùng hàm uppercase_html toàn cục)
            if "s1-title" in data and isinstance(data["s1-title"], str):
                data["s1-title"] = uppercase_html(data["s1-title"])
            if "s6-title" in data and isinstance(data["s6-title"], str):
                data["s6-title"] = uppercase_html(data["s6-title"])
                
            # Ép mặc định line-height của title Scene 1 & 6 khít hơn để không bị giãn
            if ".s1-title" not in styles: styles[".s1-title"] = {}
            if "line-height" not in styles[".s1-title"] or styles[".s1-title"]["line-height"] == "1.7" or not styles[".s1-title"]["line-height"]:
                styles[".s1-title"]["line-height"] = "1.25"
                
            if ".s6-title" not in styles: styles[".s6-title"] = {}
            if "line-height" not in styles[".s6-title"] or styles[".s6-title"]["line-height"] == "1.65" or not styles[".s6-title"]["line-height"]:
                styles[".s6-title"]["line-height"] = "1.25"

            index_file = folder_path / "index.html"
            if not index_file.exists():
                self._send(404, "application/json", json.dumps({"ok": False, "msg": f"Không tìm thấy index.html ở {folder_path.name}"}))
                return
                
            html = index_file.read_text(encoding="utf-8")
            t1 = time.perf_counter()
            for elem_id, new_inner in data.items():
                if not isinstance(new_inner, str) or elem_id == "styles":
                    continue
                html = set_inner(html, elem_id, clean_boom_boom(new_inner))
            t2 = time.perf_counter()
            for sel, props in styles.items():
                for prop, value in props.items():
                    if isinstance(value, str) and value.strip():
                        html = set_css(html, sel, prop, value.strip())
            t3 = time.perf_counter()
            safe_write_file(index_file, html)
            t4 = time.perf_counter()
            print(f"[USER] [save] read={t1-t0:.2f}s inner={t2-t1:.2f}s css={t3-t2:.2f}s write={t4-t3:.2f}s total={t4-t0:.2f}s")
            
            # --- CẬP NHẬT CONTENT.JSON ĐỒNG BỘ ---
            co_file = folder_path / "content.json"
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
                    print(f"[SYSTEM] [save] Đồng bộ thành công content.json cho {folder_path.name}")
                except Exception as e:
                    print(f"[SYSTEM] [save] Lỗi đồng bộ content.json: {e}")
            
            # --- CẬP NHẬT TỰ ĐỘNG TRẠNG THÁI IS_VISUAL_APPROVED KHI SẾP BẤM SAVE ---
            db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
            if db_path.exists():
                try:
                    db_items = json.loads(db_path.read_text(encoding="utf-8"))
                    folder_name = folder_path.name
                    match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
                    slug = match.group(1) if match else folder_name
                    slug = slug.strip().lower()
                    
                    db_changed = False
                    for item in db_items:
                        is_match = False
                        url_val = item.get("url", "")
                        if url_val:
                            normalized_url = os.path.abspath(os.path.dirname(url_val) if url_val.lower().endswith(".mp4") else url_val).lower()
                            normalized_folder = os.path.abspath(str(folder_path)).lower()
                            if normalized_url == normalized_folder:
                                is_match = True
                        if not is_match:
                            item_topic = item.get("topic", "")
                            if item_topic:
                                item_slug = slugify_vietnamese(item_topic)
                                if item_slug == slug or item_slug in slug or slug in item_slug:
                                    is_match = True
                        if is_match:
                            item["is_visual_approved"] = True
                            db_changed = True
                            print(f"[SYSTEM] [save] Tự động set is_visual_approved = True cho bài đăng ID {item.get('id')} ({folder_name})")
                    
                    if db_changed:
                        with open(db_path, 'w', encoding='utf-8') as f:
                            json.dump(db_items, f, ensure_ascii=False, indent=2)
                            f.flush()
                            os.fsync(f.fileno())
                        print("[SYSTEM] [save] Đã ghi nhận Đã Duyệt vào database Bảng UP thưa Sếp!")
                except Exception as e_db_save:
                    print(f"[SYSTEM] [save] Lỗi tự động cập nhật database duyệt bài: {e_db_save}")

            self._send(200, "application/json", '{"ok":true}')
            
        elif path == "/api/set-video-approval":
            try:
                folder_path = get_path_from_query(self.path)
                payload = json.loads(body)
                approved = payload.get("approved") in (True, 1, "true")
                
                db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
                if db_path.exists():
                    db_items = json.loads(db_path.read_text(encoding="utf-8"))
                    folder_name = folder_path.name
                    match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
                    slug = match.group(1) if match else folder_name
                    slug = slug.strip().lower()
                    
                    db_changed = False
                    for item in db_items:
                        is_match = False
                        url_val = item.get("url", "")
                        if url_val:
                            normalized_url = os.path.abspath(os.path.dirname(url_val) if url_val.lower().endswith(".mp4") else url_val).lower()
                            normalized_folder = os.path.abspath(str(folder_path)).lower()
                            if normalized_url == normalized_folder:
                                is_match = True
                        if not is_match:
                            item_topic = item.get("topic", "")
                            if item_topic:
                                item_slug = slugify_vietnamese(item_topic)
                                if item_slug == slug or item_slug in slug or slug in item_slug:
                                    is_match = True
                        if is_match:
                            item["is_video_approved"] = approved
                            item["video_approved"] = approved
                            db_changed = True
                            print(f"[SYSTEM] [set-video-approval] Set is_video_approved = {approved} & video_approved = {approved} cho bài đăng ID {item.get('id')} ({folder_name})")
                    
                    if db_changed:
                        with open(db_path, 'w', encoding='utf-8') as f:
                            json.dump(db_items, f, ensure_ascii=False, indent=2)
                            f.flush()
                            os.fsync(f.fileno())
                        print("[SYSTEM] [set-video-approval] Đã ghi nhận Duyệt Video vào database Bảng UP thưa Sếp!")
                            
                self._send(200, "application/json", '{"ok":true}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))
                
        elif path == "/api/set-voice-approval":
            try:
                import re
                folder_path = get_path_from_query(self.path)
                payload = json.loads(body)
                approved = payload.get("approved") in (True, 1, "true")

                db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
                if db_path.exists():
                    db_items = json.loads(db_path.read_text(encoding="utf-8"))
                    folder_name = folder_path.name
                    match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
                    slug = match.group(1) if match else folder_name
                    slug = slug.strip().lower()

                    db_changed = False
                    for item in db_items:
                        is_match = False
                        url_val = item.get("url", "")
                        if url_val:
                            normalized_url = os.path.abspath(os.path.dirname(url_val) if url_val.lower().endswith(".mp4") else url_val).lower()
                            normalized_folder = os.path.abspath(str(folder_path)).lower()
                            if normalized_url == normalized_folder:
                                is_match = True
                        if not is_match:
                            item_topic = item.get("topic", "")
                            if item_topic:
                                item_slug = slugify_vietnamese(item_topic)
                                if item_slug == slug or item_slug in slug or slug in item_slug:
                                    is_match = True
                        if is_match:
                            item["is_voice_approved"] = approved
                            db_changed = True
                            print(f"[SYSTEM] [set-voice-approval] Set is_voice_approved = {approved} cho bài đăng ID {item.get('id')} ({folder_name})")

                    if db_changed:
                        with open(db_path, 'w', encoding='utf-8') as f:
                            json.dump(db_items, f, ensure_ascii=False, indent=2)
                            f.flush()
                            os.fsync(f.fileno())
                        print("[SYSTEM] [set-voice-approval] Đã ghi nhận Duyệt Voice vào database thưa Sếp!")

                self._send(200, "application/json", '{"ok":true}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))

        elif path == "/save-duration":
            try:
                folder_path = get_path_from_query(self.path)
                payload = json.loads(body) if body else {}
                new_dur = float(payload.get("duration", 0))
                if new_dur < 1 or new_dur > 600:
                    self._send(400, "application/json", json.dumps({"ok": False, "error": "duration phải 1-600s"}))
                    return
                co_file = folder_path / "content.json"
                if not co_file.exists():
                    self._send(404, "application/json", json.dumps({"ok": False, "error": "content.json không tồn tại"}))
                    return
                co = json.loads(co_file.read_text(encoding="utf-8"))
                if "voice" not in co: co["voice"] = {}
                co["voice"]["duration"] = round(new_dur, 2)
                safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
                print(f"[USER] [save-duration] {folder_path.name} → voice.duration = {new_dur}s")
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
            
            folder_path = get_path_from_query(self.path)
            # Tự động dò tìm file voice gốc và đồng bộ cấu hình trong index.html + content.json (giữ nguyên tên file của Sếp)
            try:
                wav_file = _find_voice_wav(folder_path)
                if wav_file and wav_file.exists():
                    vname = wav_file.name
                    # 1. Đồng bộ content.json
                    co_file = folder_path / "content.json"
                    if co_file.exists():
                        co = json.loads(co_file.read_text(encoding="utf-8"))
                        if co.get("voice", {}).get("file") != vname:
                            if "voice" not in co: co["voice"] = {}
                            co["voice"]["file"] = vname
                            safe_write_file(co_file, json.dumps(co, ensure_ascii=False, indent=2))
                            print(f"[SYSTEM] [render] Auto sync content.json voice.file = {vname}")
                    
                    # 2. Đồng bộ index.html tag <audio> src
                    index_file = folder_path / "index.html"
                    if index_file.exists():
                        html = index_file.read_text(encoding="utf-8")
                        # Tìm src hiện tại của audio tag narration
                        match_aud = re.search(r'(<audio[^>]*\bid="narration"[^>]*\bsrc=")([^"]+)(")', html)
                        if match_aud and match_aud.group(2) != vname:
                            html = re.sub(
                                r'(<audio[^>]*\bid="narration"[^>]*\bsrc=")([^"]+)(")',
                                rf'\g<1>{vname}\g<3>',
                                html
                            )
                            safe_write_file(index_file, html)
                            print(f"[SYSTEM] [render] Auto sync index.html audio src = {vname}")
            except Exception as e:
                print(f"[SYSTEM] [render] Auto sync voice file failed: {e}")

            Handler.render_status["running"] = True
            globals()["RENDER_IS_RUNNING"] = lambda: Handler.render_status["running"]
            Handler.render_status["last_out"] = "Starting render…"

            def do_render(proj_path):
                import re
                ts_part = time.strftime("T%m.%d_%Hh%M")
                match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", proj_path.name)
                if match:
                    slug_part = match.group(1)
                else:
                    slug_part = proj_path.name
                
                slug_clean = slugify_vietnamese(slug_part, max_len=60)
                out_name = f"{slug_clean}_{ts_part}.mp4"
                logging.info(f"RENDER START: out={out_name} cwd={proj_path}")
                t0 = time.time()
                try:
                    proc = subprocess.run(
                        f'npx -y -p hyperframes hyperframes render . --output "{out_name}" --fps 30 --quality draft --workers 2',
                        cwd=str(proj_path), capture_output=True, text=True,
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
                    
                    # Khóa thời lượng khớp với content.json voice.duration nếu có
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
                                    print(f"[SYSTEM] [render] Khóa thời lượng thành công cho {proj_path.name} -> {out_name} ({voice_dur}s)")
                        except Exception as e_dur:
                            print(f"[SYSTEM] Lỗi khóa thời lượng: {e_dur}")
                except Exception as e:
                    logging.exception(f"RENDER EXCEPTION: {e}")
                    Handler.render_status["last_out"] = f"ERROR: {e}"
                finally:
                    Handler.render_status["running"] = False

            threading.Thread(target=do_render, args=(folder_path,), daemon=True).start()
            self._send(202, "application/json", '{"ok":true,"msg":"render started"}')
            
        elif path == "/shutdown":
            if Handler.render_status["running"]:
                self._send(409, "application/json", '{"ok":false,"msg":"rendering"}')
                return
            self._send(200, "application/json", '{"ok":true}')
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
            
        elif path == "/open-folder":
            try:
                folder_path = get_path_from_query(self.path)
                subprocess.Popen(["explorer.exe", str(folder_path)], shell=False)
                self._send(200, "application/json", '{"ok":true}')
            except Exception as e:
                try:
                    os.startfile(str(folder_path))
                    self._send(200, "application/json", '{"ok":true,"fallback":"startfile"}')
                except Exception as e2:
                    self._send(500, "application/json", json.dumps({"ok": False, "msg": f"explorer: {e}; startfile: {e2}"}))
                    
        elif path.startswith("/upload-voice"):
            try:
                if not raw_body:
                    self._send(400, "application/json", '{"ok":false,"msg":"empty"}')
                    return
                
                folder_path = get_path_from_query(self.path)
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                orig_name = params.get("name", ["narration.wav"])[0]
                orig_name = os.path.basename(orig_name)
                
                safe_write_file(folder_path / orig_name, raw_body, is_binary=True)
                safe_write_file(folder_path / "narration.wav", raw_body, is_binary=True)
                
                # Probe new duration
                dur = 0
                try:
                    out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(folder_path / orig_name)], capture_output=True, text=True, timeout=10).stdout.strip()
                    dur = float(out) if out else 0
                except Exception: pass
                
                # Cập nhật content.json
                co_file = folder_path / "content.json"
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
                index_file = folder_path / "index.html"
                if index_file.exists():
                    try:
                        html = index_file.read_text(encoding="utf-8")
                        if old_wav_name:
                            html = html.replace(f'src="{old_wav_name}"', f'src="{orig_name}"')
                        else:
                            html = re.sub(r'(<audio[^>]*\bsrc=")([^"]+)"', rf'\1{orig_name}"', html, count=1)
                        safe_write_file(index_file, html)
                        print(f"[SYSTEM] [upload-voice] Updated index.html audio src to {orig_name}")
                    except Exception as e:
                        print(f"[upload-voice] Update index.html error: {e}")
                
                self._send(200, "application/json", json.dumps({"ok": True, "duration": dur, "size": len(raw_body)}))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))
                
        elif path == "/api/video-9x16/batch-run":
            try:
                payload = json.loads(body)
                paths = payload.get("paths", [])
                action = payload.get("action", "")
                if not paths or not action:
                    self._send(400, "application/json", json.dumps({"ok": False, "msg": "Thiếu paths hoặc action"}))
                    return
                
                # Cập nhật status
                BATCH_STATUS["total"] = len(paths)
                BATCH_STATUS["current_index"] = 0
                BATCH_STATUS["progress_pct"] = 0
                BATCH_STATUS["log"] = [f"[SYSTEM] Bắt đầu hàng đợi {action.upper()} cho {len(paths)} thư mục."]
                
                for p in paths:
                    BATCH_QUEUE.put((p, action))
                    
                self._send(200, "application/json", json.dumps({"ok": True, "msg": f"Đã đưa {len(paths)} thư mục vào hàng đợi {action}"}))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))
                
        elif path == "/api/fix-bug":
            if Handler.render_status["running"]:
                self._send(409, "application/json", '{"ok":false,"msg":"already running render"}')
                return
            
            try:
                import re
                folder_path = get_path_from_query(self.path)
                print(f"[USER] [fix-bug] Bắt đầu tự động chẩn đoán và fix bug cho {folder_path.name}")
                
                # 1. Chạy chẩn đoán và sửa content.json
                ok, msg = diagnose_and_fix_timeline(folder_path)
                if not ok:
                    self._send(500, "application/json", json.dumps({"ok": False, "msg": msg}))
                    return
                
                # 2. Gọi compose.py để dựng lại index.html
                # Tìm voice mới nhất
                wav_file = _find_voice_wav(folder_path)
                voice_filename = wav_file.name if wav_file else "narration.wav"
                
                tpl_dir = TEMPLATES / "01_Text_KID"
                workdir_in_tpl = tpl_dir / f"_pipeline_{folder_path.name}"
                workdir_in_tpl.mkdir(exist_ok=True)
                
                content_json_src = folder_path / "content.json"
                if content_json_src.exists():
                    (workdir_in_tpl / "content.json").write_text(content_json_src.read_text(encoding="utf-8"), encoding="utf-8")
                
                if wav_file and wav_file.exists():
                    shutil.copy(wav_file, workdir_in_tpl / voice_filename)
                
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
                    shutil.copy(composed_html, folder_path / "index.html")
                
                print(f"[SYSTEM] [fix-bug] Re-composed index.html successfully")
            except Exception as e_comp:
                import traceback
                err_tb = traceback.format_exc()
                print(f"[SYSTEM] [fix-bug] Lỗi thực thi: {err_tb}")
                self._send(500, "application/json", json.dumps({"ok": False, "msg": f"Lỗi thực thi fix-bug: {str(e_comp)}\n{err_tb}"}))
                return
            
            # 3. Kích hoạt render video
            Handler.render_status["running"] = True
            globals()["RENDER_IS_RUNNING"] = lambda: Handler.render_status["running"]
            Handler.render_status["last_out"] = "Bug fixed! Starting re-render video..."
            
            def do_render(proj_path):
                import re
                ts_part = time.strftime("T%m.%d_%Hh%M")
                match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", proj_path.name)
                slug_part = match.group(1) if match else proj_path.name
                slug_clean = slugify_vietnamese(slug_part, max_len=60)
                out_name = f"{slug_clean}_{ts_part}.mp4"
                logging.info(f"RENDER START (FIX BUG): out={out_name} cwd={proj_path}")
                t0 = time.time()
                try:
                    proc = subprocess.run(
                        f'npx -y -p hyperframes hyperframes render . --output "{out_name}" --fps 30 --quality draft --workers 2',
                        cwd=str(proj_path), capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        shell=True,
                    )
                    out = (proc.stdout or "") + (proc.stderr or "")
                    tail = out[-1200:]
                    elapsed = time.time() - t0
                    Handler.render_status["last_out"] = f"Fix done ✓ Exit {proc.returncode}\n→ {out_name}\n{tail}"
                    logging.info(f"RENDER END (FIX BUG): exit={proc.returncode} elapsed={elapsed:.1f}s out={out_name}")
                    
                    # Khóa thời lượng khớp với voice duration
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
                except Exception as e:
                    logging.exception(f"RENDER EXCEPTION (FIX BUG): {e}")
                    Handler.render_status["last_out"] = f"ERROR: {e}"
                finally:
                    Handler.render_status["running"] = False

            threading.Thread(target=do_render, args=(folder_path,), daemon=True).start()
            self._send(202, "application/json", json.dumps({"ok": True, "msg": "Đã sửa xong bug timeline và đang tiến hành render lại video thưa Sếp!"}))

        elif path == "/api/launch-workspace":
            try:
                payload = json.loads(body)
                target_path_str = payload.get("path")
                if not target_path_str:
                    self._send(400, "application/json", '{"ok":false,"msg":"Missing path"}')
                    return
                
                target_path = Path(target_path_str).resolve()
                if not target_path.exists():
                    self._send(404, "application/json", '{"ok":false,"msg":"Workspace path not found"}')
                    return
                
                port_file = target_path / ".editor_port"
                port = None
                if port_file.exists():
                    try:
                        port = int(port_file.read_text(encoding="utf-8").strip())
                    except Exception: pass
                
                import socket
                def check_port(p):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.5)
                        return s.connect_ex(('127.0.0.1', p)) == 0
                
                if port and check_port(port):
                    self._send(200, "application/json", json.dumps({"ok": True, "port": port}))
                    return
                
                vbs_file = target_path / "MO_EDITOR.vbs"
                if vbs_file.exists():
                    print(f"[SYSTEM] Khởi chạy workspace mới qua VBS: {vbs_file}")
                    subprocess.Popen(["wscript.exe", vbs_file.name], cwd=str(target_path), shell=False)
                    
                    time.sleep(1.0)
                    for _ in range(10):
                        if port_file.exists():
                            try:
                                port = int(port_file.read_text(encoding="utf-8").strip())
                                if port and check_port(port):
                                    self._send(200, "application/json", json.dumps({"ok": True, "port": port}))
                                    return
                            except Exception: pass
                        time.sleep(0.3)
                        
                if not port:
                    port = 5050
                    while check_port(port):
                        port += 1
                
                print(f"[SYSTEM] Khởi chạy workspace mới bằng python: {target_path} tại port {port}")
                editor_py = __file__
                subprocess.Popen([PY, editor_py, "--workspace", str(target_path)], shell=False)
                
                time.sleep(1.5)
                self._send(200, "application/json", json.dumps({"ok": True, "port": port}))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))
                
        elif path == "/api/set-approval":
            try:
                folder_path = get_path_from_query(self.path)
                payload = json.loads(body)
                approved = payload.get("approved", False)
                
                db_path = Path(r"E:\HuuDat\BrianD\TOOL_BrianD\FB-Tools\up-data\bsimple_content_data.json")
                db_changed = False
                if db_path.exists():
                    try:
                        db_items = json.loads(db_path.read_text(encoding="utf-8"))
                        import re
                        folder_name = folder_path.name
                        match = re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_(.+)$", folder_name)
                        slug = match.group(1) if match else folder_name
                        slug = slug.strip().lower()
                        for item in db_items:
                            is_match = False
                            url_val = item.get("url", "")
                            if url_val:
                                normalized_url = os.path.abspath(os.path.dirname(url_val) if url_val.lower().endswith(".mp4") else url_val).lower()
                                normalized_folder = os.path.abspath(str(folder_path)).lower()
                                if normalized_url == normalized_folder:
                                    is_match = True
                            if not is_match:
                                item_topic = item.get("topic", "")
                                if item_topic:
                                    item_slug = slugify_vietnamese(item_topic)
                                    if item_slug == slug or item_slug in slug or slug in item_slug:
                                        is_match = True
                            if is_match:
                                item["is_visual_approved"] = approved
                                db_changed = True
                        if db_changed:
                            with open(db_path, 'w', encoding='utf-8') as f:
                                json.dump(db_items, f, ensure_ascii=False, indent=2)
                                f.flush()
                                os.fsync(f.fileno())
                    except Exception as e_db:
                        self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e_db)}))
                        return
                self._send(200, "application/json", '{"ok":true}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "msg": str(e)}))

        elif path == "/gen-voice":
            try:
                folder_path = get_path_from_query(self.path)
                script_txt = folder_path / "script.txt"
                if not script_txt.exists():
                    self._send(400, "application/json", json.dumps({"ok": False, "msg": "Không tìm thấy script.txt"}))
                    return
                
                # Chạy Gemini TTS sinh giọng
                voice_name = "TT_06"
                profile = PAGE_PROFILES.get("kid")
                if profile and "default_voice" in profile:
                    voice_name = profile["default_voice"]
                    
                timestamp = time.strftime("T%m.%d_%Hh%M")
                voice_filename = f"TT_{timestamp}.wav"
                out_wav = folder_path / voice_filename
                
                gen_voice_script = SCRIPT_DIR / "gen_voice.py"
                cmd = [PY, str(gen_voice_script), "--script-file", str(script_txt), "--voice", voice_name, "--output", str(out_wav)]
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if p.returncode != 0 or not out_wav.exists():
                    self._send(500, "application/json", json.dumps({"ok": False, "msg": f"Lỗi Gemini TTS: {p.stderr}"}))
                    return
                    
                sync_latest_voice(folder_path)
                
                # Chạy Whisper dịch timeline
                tr_file = folder_path / "transcript.json"
                if tr_file.exists():
                    tr_file.unlink()
                tr_result = transcribe(out_wav, tr_file)
                if tr_result.returncode != 0 or not tr_file.exists():
                    self._send(500, "application/json", json.dumps({"ok": False, "msg": f"Lỗi Whisper Transcribe: {tr_result.stderr}"}))
                    return
                    
                # Cập nhật timeline
                update_timeline_from_transcript(folder_path, voice_filename)
                
                # Copy và chạy compose.py để sinh lại HTML
                tpl_dir = TEMPLATES / "01_Text_KID"
                workdir_in_tpl = tpl_dir / f"_pipeline_{folder_path.name}"
                workdir_in_tpl.mkdir(exist_ok=True)
                
                content_json_src = folder_path / "content.json"
                if content_json_src.exists():
                    (workdir_in_tpl / "content.json").write_text(content_json_src.read_text(encoding="utf-8"), encoding="utf-8")
                shutil.copy(out_wav, workdir_in_tpl / voice_filename)
                
                compose_script = tpl_dir / "compose.py"
                cmd_compose = [PY, str(compose_script), workdir_in_tpl.name]
                p_compose = subprocess.run(cmd_compose, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(tpl_dir))
                
                composed_html = workdir_in_tpl / "index.html"
                if composed_html.exists():
                    shutil.copy(composed_html, folder_path / "index.html")
                    print(f"[SYSTEM] [gen-voice] Đồng bộ index.html về dự án thành công: {folder_path.name}")
                
                reset_video_approval(folder_path)
                self._send(200, "application/json", json.dumps({"ok": True, "voice": voice_filename}))
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
            # Đảm bảo có flag --open-browser trong VBS để khi Sếp click chủ động mở Editor trình duyệt tự động bật lên
            if "--open-browser" not in run_line:
                run_line += " --open-browser"
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

    # Đọc cổng đã lưu trước đó của dự án (user --port có priority cao nhất)
    port_file = WORK / ".editor_port"
    if _args.port is not None:
        target_port = _args.port
    else:
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
                    # webbrowser.open() has been completely disabled to prevent duplicate tabs and profile mismatch
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
        
        # Tự động mở trình duyệt khi khởi chạy chủ động (đã vô hiệu hóa hoàn toàn)
        # if _args.open_browser:
        #     webbrowser.open(f"http://localhost:{PORT}/")
                
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
