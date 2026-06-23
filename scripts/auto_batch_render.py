"""auto_batch_render.py — Tự động quét và render hàng loạt video cho các dự án.
Hỗ trợ cả dự án mới tinh chưa có video và dự án đã sửa đổi nhưng chưa render lại.

Usage:
  python auto_batch_render.py --page vicon
  python auto_batch_render.py --dir E:/HuuDat/VIDEO/03_KID
  python auto_batch_render.py --page vicon --dry-run (chỉ quét xem danh sách, không render thật)
"""
import sys, argparse, pathlib, json, time, subprocess, re

sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = pathlib.Path(__file__).parent
ROOT       = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
TEMPLATES  = ROOT / ".agent" / "skills" / "video-9x16" / "_templates"
PY = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"

PAGE_PROFILES = {
    "kid": {
        "name": "👶 Vì Con không thể đợi",
        "output_dir": r"E:\HuuDat\VIDEO\03_KID",
    },
    "book": {
        "name": "📚 Góc nhỏ - Sách & Đời",
        "output_dir": r"E:\HuuDat\VIDEO\02_BOOK",
    },
    "bsimple": {
        "name": "🚀 B.Simple",
        "output_dir": r"E:\HuuDat\VIDEO\01_B.Simple",
    }
}

# Tải cấu hình thực tế nếu có
try:
    _profile_path = SCRIPT_DIR.parent / "video_brand_profiles.json"
    if _profile_path.exists():
        PAGE_PROFILES = json.loads(_profile_path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"[SYSTEM] Warning: Không load được video_brand_profiles.json: {e}")


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


def run_cmd(cmd, **kwargs):
    print(f"  $ {' '.join(str(c) for c in cmd[:6])}...")
    return subprocess.run(cmd, **kwargs)


def ensure_vbs(out_dir: pathlib.Path, template: str):
    """Đảm bảo file MO_EDITOR.vbs tồn tại an toàn."""
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


def render_project(project_dir: pathlib.Path):
    """Tiến hành render một dự án cụ thể."""
    print(f"\n[SYSTEM] >>> BẮT ĐẦU RENDER DỰ ÁN: {project_dir.name}")
    content_file = project_dir / "content.json"
    
    # 1. Đọc tên template và thông tin topic từ content.json
    try:
        co_data = json.loads(content_file.read_text(encoding="utf-8"))
        template = co_data.get("template", "01_Text_KID")
        topic = co_data.get("topic", project_dir.name)
    except Exception as e:
        template = "01_Text_KID"
        topic = project_dir.name
        print(f"  ⚠ Không đọc được template từ content.json, dùng mặc định: {template}")

    # 2. Tìm file voice wav mới nhất trong thư mục
    wavs = sorted(list(project_dir.glob("TT_*.wav")), key=lambda p: p.stat().st_mtime, reverse=True)
    if wavs:
        wav = wavs[0]
    else:
        # Fallback nếu không có TT_*.wav thì tìm narration.wav
        fallback_wav = project_dir / "narration.wav"
        if fallback_wav.exists():
            wav = fallback_wav
        else:
            print(f"  ⚠ LỖI: Không tìm thấy file voice (.wav) nào trong {project_dir.name}. Bỏ qua render.")
            return False

    # 3. Đảm bảo file wav được copy thành narration.wav để hyperframes render tương thích
    shutil_dest = project_dir / "narration.wav"
    if wav != shutil_dest:
        import shutil
        shutil.copy(wav, shutil_dest)

    # 4. Chạy render bằng Hyperframes CLI
    ts = time.strftime("T%m.%d_%Hh%M")
    out_mp4 = project_dir / f"{slugify_vietnamese(topic)}_{ts}.mp4"
    
    print(f"  [SYSTEM] Đang gọi hyperframes render...")
    result = run_cmd(
        f'npx -y -p hyperframes hyperframes render . --output {out_mp4.name} --fps 30 --quality draft',
        shell=True, cwd=str(project_dir), capture_output=True, text=True, encoding="utf-8"
    )
    
    if not out_mp4.exists():
        print(f"  ⚠ LỖI RENDER THẤT BẠI cho dự án: {project_dir.name}")
        print(f"  Chi tiết lỗi:\n{result.stderr[-500:]}")
        return False

    print(f"  ✓ Render thành công: {out_mp4.name} ({out_mp4.stat().st_size / 1024 / 1024:.2f} MB)")

    # 5. Khóa thời lượng khớp voice (Duration Lock)
    try:
        voice_dur = float(co_data.get("voice", {}).get("duration", 0))
        if voice_dur > 0.5:
            probe = run_cmd(
                f'ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "{out_mp4.name}"',
                shell=True, cwd=str(project_dir), capture_output=True, text=True, encoding="utf-8"
            )
            actual = float((probe.stdout or "0").strip() or 0)
            drift = abs(actual - voice_dur)
            if drift > 0.05:
                locked = project_dir / f"{out_mp4.stem}_locked.mp4"
                lock_cmd = (
                    f'ffmpeg -y -i "{out_mp4.name}" -vf "tpad=stop_mode=clone:stop_duration={voice_dur}" '
                    f'-t {voice_dur} -c:v libx264 -preset ultrafast -crf 23 -c:a copy "{locked.name}"'
                )
                lock_result = run_cmd(lock_cmd, shell=True, cwd=str(project_dir), capture_output=True, text=True, encoding="utf-8")
                if locked.exists() and locked.stat().st_size > 1024:
                    out_mp4.unlink()
                    locked.rename(out_mp4)
                    print(f"  ✓ Duration locked: {actual:.2f}s → {voice_dur:.2f}s (drift {drift:.2f}s)")
                else:
                    print(f"  ⚠ Không khóa được duration, giữ nguyên video gốc.")
    except Exception as e:
        print(f"  ⚠ Duration lock skipped: {e}")

    # 6. Cắt khoảng lặng đầu (Lead-in Trim 120ms)
    try:
        tr_file = project_dir / "transcript.json"
        if tr_file.exists():
            tr = json.loads(tr_file.read_text(encoding="utf-8"))
            segs = tr.get("segments", [])
            if segs:
                first_start = float(segs[0].get("start", 0))
                if first_start > 0.2:
                    seek_sec = round((first_start * 1000 - 120) / 1000, 3)
                    if seek_sec > 0.02:
                        trimmed = project_dir / f"{out_mp4.stem}_trim.mp4"
                        trim_cmd = (
                            f'ffmpeg -y -ss {seek_sec} -i "{out_mp4.name}" '
                            f'-c:v libx264 -preset ultrafast -crf 23 -c:a aac "{trimmed.name}"'
                        )
                        trim_result = run_cmd(trim_cmd, shell=True, cwd=str(project_dir), capture_output=True, text=True, encoding="utf-8")
                        if trimmed.exists() and trimmed.stat().st_size > 1024:
                            out_mp4.unlink()
                            trimmed.rename(out_mp4)
                            print(f"  ✓ Lead-in trimmed seek={seek_sec}s (first speech {first_start:.2f}s − 120ms backoff)")
    except Exception as e:
        print(f"  ⚠ Lead-in trim skipped: {e}")

    # 7. Khôi phục VBS
    ensure_vbs(project_dir, template)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", help="Chọn tên page cấu hình sẵn (vicon, gocnho, bsimple)")
    ap.add_argument("--dir", help="Đường dẫn tuyệt đối thư mục cần quét dự án")
    ap.add_argument("--dirs", help="Danh sách tên thư mục con cụ thể cần render (cách nhau bằng dấu phẩy)")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ quét in danh sách, không render thật")
    args = ap.parse_args()

    if not args.page and not args.dir:
        ap.error("Phải truyền ít nhất --page hoặc --dir")

    if args.dir:
        scan_dir = pathlib.Path(args.dir).resolve()
    else:
        page_key = args.page.lower().strip()
        if page_key not in PAGE_PROFILES:
            ap.error(f"Page '{args.page}' chưa được cấu hình. Các page hiện tại: {list(PAGE_PROFILES.keys())}")
        scan_dir = pathlib.Path(PAGE_PROFILES[page_key]["output_dir"]).resolve()

    if not scan_dir.exists():
        print(f"[SYSTEM] LỖI: Thư mục quét không tồn tại: {scan_dir}")
        sys.exit(1)

    print(f"[SYSTEM] Bắt đầu quét thư mục dự án tại: {scan_dir}")
    if args.dry_run:
        print("[SYSTEM] *** ĐANG CHẠY CHẾ ĐỘ THỬ NGHIỆM (DRY-RUN) ***")

    # Lọc danh sách thư mục con nếu có chỉ định
    target_dirs = []
    if args.dirs:
        target_dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
        print(f"[SYSTEM] Chỉ quét các thư mục được chỉ định: {target_dirs}")

    queue = []
    
    # Duyệt qua các thư mục con
    for p in sorted(scan_dir.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or p.name.startswith("work-") or p.name.startswith("."):
            continue
            
        if target_dirs and p.name not in target_dirs:
            continue
        
        content_file = p / "content.json"
        index_file = p / "index.html"
        
        # Chỉ quét các thư mục đã được cấu hình (phải có content.json và index.html)
        if not content_file.exists() or not index_file.exists():
            continue

        # Tìm các file video .mp4 hiện có trong thư mục
        mp4s = list(p.glob("*.mp4"))
        
        need_render = False
        reason = ""

        # Trường hợp 1: Dự án chưa có video .mp4 nào
        if not mp4s:
            need_render = True
            reason = "Dự án mới tinh (Chưa có video .mp4)"
        else:
            # Trường hợp 2: Có video nhưng video cũ hơn file content.json hoặc script.txt
            latest_mp4 = max(mp4s, key=lambda f: f.stat().st_mtime)
            
            # Đối chiếu thời gian lưu
            ref_files = [content_file]
            script_file = p / "script.txt"
            if script_file.exists():
                ref_files.append(script_file)
                
            # Đối chiếu với file voice wav mới nhất
            wavs = list(p.glob("TT_*.wav"))
            if wavs:
                latest_wav = max(wavs, key=lambda f: f.stat().st_mtime)
                ref_files.append(latest_wav)

            for ref in ref_files:
                if ref.stat().st_mtime > latest_mp4.stat().st_mtime:
                    need_render = True
                    reason = f"Dữ liệu thay đổi (file '{ref.name}' mới hơn video cũ)"
                    break

        if need_render:
            queue.append((p, reason))
            print(f"  + [QUEUE] {p.name} -> Lý do: {reason}")

    print(f"\n[SYSTEM] Tổng số dự án cần render trong hàng đợi: {len(queue)}")
    
    if args.dry_run or not queue:
        print("[SYSTEM] Done. Không có dự án nào thực thi render.")
        return

    # Thực thi render hàng loạt
    success_count = 0
    for idx, (project_dir, reason) in enumerate(queue):
        print(f"\n{'='*70}\n[Tiến trình {idx+1}/{len(queue)}] Render: {project_dir.name}\nLý do: {reason}\n{'='*70}")
        success = render_project(project_dir)
        if success:
            success_count += 1
            
    print(f"\n[SYSTEM] HOÀN THÀNH BATCH RENDER.")
    print(f"  - Thành công: {success_count}/{len(queue)} dự án.")
    if success_count < len(queue):
        print(f"  - Thất bại: {len(queue) - success_count} dự án. Vui lòng check log ở trên.")


if __name__ == "__main__":
    main()
