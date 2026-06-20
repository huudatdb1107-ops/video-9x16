"""run_pipeline.py — orchestrator chính cho skill VIDEO-9X16-Br.

Pipeline 8 bước:
  1. Match template (heuristic)
  2. Fill content.json (LLM)
  3. Gen voice (OMNI bridge)
  4. Transcribe voice (faster-whisper)
  5. Update timeline trong content.json từ transcript
  6. Compose: skeleton + tokens + content → index.html
  7. Render mp4 (hyperframes)
  8. Done — báo path mp4

Usage:
  python run_pipeline.py \
    --topic "Trẻ em hiếu động vs tăng động" \
    --script-file script.txt \
    --voice TT_06 \
    --output-dir E:/HuuDat/BrianD/TOOL_BrianD/TEST/_videos/my_video/

Optional flags:
  --template <name>     bỏ qua match, dùng template chỉ định
  --skip-voice          dùng narration.wav có sẵn trong output-dir
  --skip-llm            dùng content.json có sẵn (Boss fill manual)
  --skip-render         dừng sau compose, chỉ ra index.html
"""
import sys, argparse, pathlib, subprocess, json, time
sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = pathlib.Path(__file__).parent
ROOT       = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
TEMPLATES  = ROOT / ".agent" / "skills" / "video-9x16" / "_templates"
PY = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"

PAGE_PROFILES = {}
try:
    _profile_path = SCRIPT_DIR.parent / "video_brand_profiles.json"
    if _profile_path.exists():
        PAGE_PROFILES = json.loads(_profile_path.read_text(encoding="utf-8"))
except Exception as _e:
    print(f"  ⚠ Lỗi load video_brand_profiles.json: {_e}")

# Fallback
if not PAGE_PROFILES:
    PAGE_PROFILES = {
        "vicon": {
            "name": "👶 Vì Con không thể đợi",
            "output_dir": r"E:\HuuDat\VIDEO\FACEBOOK\01__Vi_Con",
            "default_voice": "TT_06",
            "default_template": "01_Text_ViCon",
            "brand_watermark": "PK NHI BOOM BOOM",
            "default_hashtag": "#NuoiDayCon"
        }
    }


def step(num, name):
    print(f"\n{'='*60}\n[{num}/8] {name}\n{'='*60}")

def run(cmd, **kwargs):
    print(f"  $ {' '.join(str(c) for c in cmd[:6])}...")
    return subprocess.run(cmd, **kwargs)

def update_timeline_from_transcript(out_dir: pathlib.Path, wav_filename: str = "narration.wav"):
    """Đọc transcript.json + content.json → chia scenes bằng SequenceMatcher (giống tool TIME) + detect element_times."""
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
        print("  ⚠ transcript.json hoặc content.json missing, giữ timeline cũ.")
        return
        
    tr = json.loads(tr_file.read_text(encoding="utf-8"))
    co = json.loads(co_file.read_text(encoding="utf-8"))
    duration = tr.get("duration", 60.0)
    segments = tr.get("segments", [])
    sids_sorted = sorted(co.get("scenes", {}).keys())
    n_scenes = len(sids_sorted)
    if n_scenes < 2: return

    # Đọc script.txt (mỗi dòng tương ứng 1 scene)
    lines = []
    if sc_file.exists():
        lines = [l.strip() for l in sc_file.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        
    # Fallback nếu số dòng kịch bản khác số scene hoặc không có script.txt
    if len(lines) != n_scenes:
        print(f"  ⚠ Số dòng script.txt ({len(lines)}) khác số scene ({n_scenes}). Fallback lấy text trong content.json.")
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

    # Xây dựng bản đồ ký tự từ transcript
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

    # Khớp SequenceMatcher tìm anchors động
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
        
        # Ngưỡng khớp: match size > 10 ký tự hoặc khớp > 25% độ dài câu kịch bản
        if m.size > 10 or (len(target) > 0 and m.size / len(target) > 0.25):
            gs = max(0, min(len(char_map)-1, (ptr + m.a) - m.b))
            ge = min(len(char_map)-1, gs + len(target))
            anchors[sid] = (char_map[gs][1], char_map[ge][2])
            ptr = gs + m.size
            print(f"  ✓ Dynamic anchor for {sid}: {anchors[sid][0]:.2f}s -> {anchors[sid][1]:.2f}s (match size: {m.size})")
        else:
            anchors[sid] = None

    # Dàn trải các đoạn lủng (nội suy thông minh theo số ký tự kịch bản - orphan dispersal)
    boundaries = {}
    last_end = 0.0
    for i, sid in enumerate(sids_sorted):
        if anchors[sid] is not None:
            s, e = anchors[sid]
        else:
            # Tìm anchor đã biết tiếp theo
            next_anc_sid = next((sids_sorted[j] for j in range(i+1, len(sids_sorted)) if anchors[sids_sorted[j]] is not None), None)
            g_start = last_end
            g_end = anchors[next_anc_sid][0] if next_anc_sid else duration
            
            # Gom nhóm các scene mồ côi
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

        # Snap to frame 30fps
        def snap_t(t, fps=30):
            f = 1.0 / fps
            return round(round(t / f) * f, 2)
            
        boundaries[sid] = snap_t(last_end)
        last_end = snap_t(e if i < len(sids_sorted) - 1 else duration)

    # Cập nhật timeline mới vào content.json
    timeline = {}
    for i, sid in enumerate(sids_sorted):
        out_t = boundaries[sids_sorted[i+1]] if i+1 < len(sids_sorted) else duration
        timeline[sid] = {"in": round(boundaries[sid], 2), "out": round(out_t, 2)}
    co["timeline"] = timeline
    print(f"  ✓ Scene boundaries: {timeline}")

    # Detect element_times: scan segments tìm mốc xuất hiện Card / Item
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
            print(f"  ✓ {sid}.element_times: {et}")

    if "voice" not in co: co["voice"] = {}
    co["voice"]["file"] = wav_filename
    co["voice"]["duration"] = duration
    co_file.write_text(json.dumps(co, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Saved new timeline data to content.json")


def ensure_vbs(out_dir: pathlib.Path, template: str):
    """Tạo (hoặc overwrite) MO_EDITOR.vbs trong workspace. Idempotent — gọi bao nhiêu lần cũng OK."""
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
    return vbs_path


def transcribe(wav_file: pathlib.Path, out_file: pathlib.Path):
    """Run faster-whisper transcribe → save transcript.json."""
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
print(f"Transcribed: {{len(out)}} segments, {{info.duration:.2f}}s")
"""
    return run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8")


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
    
    # Giới hạn tự động cho tên file (slug chiếm max 60 ký tự để an toàn đường dẫn tuyệt đối MAX_PATH)
    # Cắt thông minh theo dấu gạch dưới để đảm bảo đủ câu có nghĩa
    if len(s) > max_len:
        truncated = s[:max_len]
        last_underscore = truncated.rfind('_')
        if last_underscore > 0:
            s = truncated[:last_underscore]
        else:
            s = truncated
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--script-file", required=True)
    ap.add_argument("--voice", help="Tên giọng đọc (mặc định lấy theo Profile Page)")
    ap.add_argument("--output-dir", help="Đường dẫn tuyệt đối đầu ra (nếu không dùng --page)")
    ap.add_argument("--page", help="Tên kênh cấu hình sẵn (vicon, gocnho, bsimple)")
    ap.add_argument("--template", help="Override template choice")
    ap.add_argument("--skip-voice", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--skip-render", action="store_true")
    args = ap.parse_args()

    if not args.output_dir and not args.page:
        ap.error("Phải truyền ít nhất --page hoặc --output-dir")

    ts = time.strftime("T%m.%d_%Hh%M")
    
    profile = None
    if args.page:
        page_key = args.page.lower().strip()
        if page_key not in PAGE_PROFILES:
            ap.error(f"Page '{args.page}' chưa được cấu hình. Các page hiện tại: {list(PAGE_PROFILES.keys())}")
        profile = PAGE_PROFILES[page_key]
        
        # Áp dụng defaults từ profile
        if not args.voice and "default_voice" in profile:
            args.voice = profile["default_voice"]
        if not args.template and "default_template" in profile:
            args.template = profile["default_template"]
            
        slug_topic = slugify_vietnamese(args.topic, max_len=60)
        sub_dir_name = slug_topic
        raw_out_dir = pathlib.Path(profile["output_dir"]) / sub_dir_name
    else:
        raw_out_dir = pathlib.Path(args.output_dir).resolve()

    # Áp dụng mặc định hệ thống cho giọng đọc nếu không chỉ định
    if not args.voice:
        args.voice = "TT_06"
        
    # Tự động chèn TIME lên đầu tên thư mục dự án nếu chưa có
    import re
    if not re.match(r"^T\d{2}\.\d{2}_\d{2}h\d{2}_", raw_out_dir.name):
        # Kiểm tra xem trong thư mục cha đã có thư mục nào chứa/kết thúc bằng slug chủ đề chưa để tránh trùng lặp rác
        parent_dir = raw_out_dir.parent
        slug_name = raw_out_dir.name
        matched_dir = None
        if parent_dir.exists():
            for p in parent_dir.iterdir():
                if p.is_dir() and (p.name == slug_name or p.name.endswith(f"_{slug_name}")):
                    matched_dir = p
                    break
        if matched_dir:
            out_dir = matched_dir
            print(f"[SYSTEM] Phát hiện thư mục trùng chủ đề đã tồn tại: '{out_dir.name}'. Tái sử dụng để cập nhật trực tiếp, tránh sinh rác.")
        else:
            out_dir = raw_out_dir.parent / f"{ts}_{raw_out_dir.name}"
    else:
        out_dir = raw_out_dir

        
    out_dir.mkdir(parents=True, exist_ok=True)
    script_file = pathlib.Path(args.script_file).resolve()

    # Copy script file vào thư mục dự án mới để lưu trữ trọn vẹn
    target_script = out_dir / "script.txt"
    if script_file.exists() and script_file != target_script:
        import shutil
        shutil.copy(script_file, target_script)
        # Cập nhật script_file trỏ tới file mới copy
        script_file = target_script

    # ============ STEP 1: Match template ============
    if args.template:
        template = args.template
        step(1, f"Use template (manual): {template}")
    else:
        step(1, "Match template")
        result = run([PY, str(SCRIPT_DIR / "match_template.py"),
                      "--topic", args.topic, "--script-file", str(script_file)],
                     capture_output=True, text=True, encoding="utf-8")
        print(result.stdout)
        template = result.stdout.strip().split("\n")[-1].strip()

    tpl_dir = TEMPLATES / template
    assert tpl_dir.exists(), f"Template missing: {tpl_dir}"

    # ============ STEP 1.5: Ensure MO_EDITOR.vbs (Tạo sớm theo yêu cầu của Sếp) ============
    try:
        ensure_vbs(out_dir, template)
        print(f"  ✓ Khởi tạo sớm MO_EDITOR.vbs tại: {out_dir}")
    except Exception as e:
        print(f"  ⚠ Lỗi khởi tạo sớm VBS: {e}")

    # ============ STEP 2: Fill content.json (LLM) ============
    target_content = out_dir / "content.json"
    if target_content.exists():
        print(f"  [SYSTEM] Phát hiện content.json đã tồn tại ở: {target_content}. Tự động tái sử dụng và skip LLM.")
        args.skip_llm = True

    if args.skip_llm:
        step(2, "SKIP LLM (use existing content.json)")
        source_content = raw_out_dir / "content.json"
        if not target_content.exists() and source_content.exists():
            import shutil
            shutil.copy(source_content, target_content)
            print(f"  ✓ Tự động copy content.json từ thư mục cũ sang: {target_content}")
    else:
        step(2, "Fill content.json via LLM")
        result = run([PY, str(SCRIPT_DIR / "fill_content.py"),
                      "--template", template, "--script-file", str(script_file),
                      "--output-dir", str(out_dir)])
        if result.returncode != 0:
            print("⚠ LLM fill failed. Boss fill manual rồi chạy lại với --skip-llm")
            return

    # Cập nhật brand_watermark & hashtag từ Profile Page nếu có
    if profile:
        co_file = out_dir / "content.json"
        if co_file.exists():
            try:
                co_data = json.loads(co_file.read_text(encoding="utf-8"))
                
                # Ghi đè brand_watermark vào root
                if "brand_watermark" in profile:
                    co_data["brand_watermark"] = profile["brand_watermark"]
                    print(f"  ✓ Tự động gán brand_watermark: {profile['brand_watermark']}")
                
                # Ghi đè hashtag vào s6
                if "default_hashtag" in profile:
                    if "scenes" in co_data and "s6" in co_data["scenes"]:
                        co_data["scenes"]["s6"]["hashtag"] = profile["default_hashtag"]
                        print(f"  ✓ Tự động gán hashtag: {profile['default_hashtag']}")
                
                co_file.write_text(json.dumps(co_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"  ⚠ Lỗi cập nhật profile cho content.json: {e}")

    # ============ STEP 3: Gen voice ============
    existing_wavs = sorted(list(out_dir.glob("TT_*.wav")), key=lambda p: p.stat().st_mtime, reverse=True)
    
    script_changed = False
    if existing_wavs:
        script_file_path = out_dir / "script.txt"
        if script_file_path.exists():
            # Nếu script.txt được sửa đổi sau khi file voice mới nhất được sinh ra
            if script_file_path.stat().st_mtime > existing_wavs[0].stat().st_mtime:
                script_changed = True
                print("  [SYSTEM] Phát hiện script.txt mới được chỉnh sửa. Sẽ sinh file voice mới.")

    if existing_wavs and not script_changed:
        wav = existing_wavs[0]
        wav_filename = wav.name
        args.skip_voice = True
        print(f"  [SYSTEM] Phát hiện file voice đã có sẵn trong thư mục dự án và kịch bản không đổi: '{wav_filename}'. Tự động tái sử dụng.")
    else:
        # Sinh file voice mới với timestamp mới linh hoạt
        wav_filename = f"TT_{ts}.wav"
        wav = out_dir / wav_filename
        args.skip_voice = False

    if args.skip_voice and wav.exists():
        step(3, f"SKIP voice gen (use existing {wav.name})")
    else:
        step(3, f"Gen voice (OMNI, voice={args.voice})")
        result = run([PY, str(SCRIPT_DIR / "gen_voice.py"),
                      "--script-file", str(script_file),
                      "--voice", args.voice,
                      "--output", str(wav)])
        if result.returncode != 0 or not wav.exists():
            print("⚠ Voice gen failed.")
            return

    # ============ STEP 4: Transcribe ============
    step(4, "Transcribe voice (faster-whisper base)")
    tr_file = out_dir / "transcript.json"
    
    # Chỉ tái sử dụng transcript.json cũ nếu nó tồn tại VÀ mới hơn file voice wav
    skip_transcribe = False
    if tr_file.exists() and wav.exists():
        if tr_file.stat().st_mtime > wav.stat().st_mtime:
            skip_transcribe = True
            
    if skip_transcribe:
        print(f"  [SYSTEM] Phát hiện transcript.json đã tồn tại và mới hơn file voice: {tr_file.name}. Tự động tái sử dụng.")
        class MockResult:
            returncode = 0
            stdout = "Tái sử dụng transcript.json có sẵn"
            stderr = ""
        tr_result = MockResult()
    else:
        # Nếu có transcript.json cũ nhưng file voice mới hơn, xóa transcript cũ đi để bắt buộc chạy lại Whisper
        if tr_file.exists():
            tr_file.unlink()
        tr_result = transcribe(wav, tr_file)
    print(tr_result.stdout[-500:] if tr_result.stdout else "")
    if tr_result.returncode != 0:
        if (out_dir / "transcript.json").exists():
            print(f"  [SYSTEM] Cảnh báo: Tiến trình transcribe thoát với code {tr_result.returncode} nhưng đã tìm thấy transcript.json. Tiếp tục pipeline...")
        else:
            print(f"⚠ Transcribe failed (không tìm thấy transcript.json):\n{tr_result.stderr[-500:]}")
            return

    # ============ STEP 5: Update timeline ============
    step(5, "Update timeline từ transcript")
    update_timeline_from_transcript(out_dir, wav_filename)

    # ============ STEP 6: Compose ============
    step(6, f"Compose: {template}/skeleton + tokens + content → index.html")
    # Compose script đặt content_dir RELATIVE to template dir → ta dùng absolute path workaround
    # Copy content.json + narration.wav vào subdir under template/
    workdir_in_tpl = tpl_dir / f"_pipeline_{out_dir.name}"
    workdir_in_tpl.mkdir(exist_ok=True)
    (workdir_in_tpl / "content.json").write_text(
        (out_dir / "content.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    import shutil
    if wav.exists():
        shutil.copy(wav, workdir_in_tpl / wav.name)
    result = run([PY, str(tpl_dir / "compose.py"), workdir_in_tpl.name],
                 capture_output=True, text=True, encoding="utf-8", cwd=str(tpl_dir))
    print(result.stdout)
    composed_html = workdir_in_tpl / "index.html"
    if composed_html.exists():
        # Đọc index.html và replace src="narration.wav" thành src="TT_<time>.wav"
        html_content = composed_html.read_text(encoding="utf-8")
        html_content = re.sub(r'src="narration\.wav"', f'src="{wav_filename}"', html_content)
        composed_html.write_text(html_content, encoding="utf-8")
        
        shutil.copy(composed_html, out_dir / "index.html")
    else:
        print("⚠ Compose failed.")
        return

    # ============ STEP 6.5: Ensure MO_EDITOR.vbs ============
    # Tạo NGAY sau compose để Boss luôn có editor preview, ngay cả khi render fail / skip
    vbs_path = ensure_vbs(out_dir, template)
    print(f"  ✓ MO_EDITOR.vbs: {vbs_path}")

    # ============ STEP 7: Render mp4 ============
    if args.skip_render:
        step(7, "SKIP render (--skip-render)")
        print(f"\n✓ Done (no render). index.html: {out_dir / 'index.html'}")
        print(f"   Editor: double-click {vbs_path.name}")
        return

    step(7, "Render mp4 (hyperframes draft)")
    out_mp4 = out_dir / f"{slugify_vietnamese(args.topic)}_{ts}.mp4"
    # Copy file wav vào cùng folder index.html
    if wav.exists() and not (out_dir / wav.name).exists():
        shutil.copy(wav, out_dir / wav.name)
    result = run(
        f'npx -y -p hyperframes hyperframes render . --output {out_mp4.name} --fps 30 --quality draft',
        shell=True, cwd=str(out_dir), capture_output=True, text=True, encoding="utf-8",
    )
    print(result.stdout[-1000:] if result.stdout else "")
    # VBS đã tạo ở step 6.5 → đảm bảo còn (idempotent)
    ensure_vbs(out_dir, template)
    if out_mp4.exists():
        # ===== STEP 7.5: Explicit duration lock (ported from html-video PR #25) =====
        # Đọc voice duration từ content.json, ép MP4 khớp đúng bằng ffmpeg tpad + -t.
        # tpad=stop_mode=clone clone last frame nếu MP4 ngắn hơn voice; -t cắt nếu dài hơn.
        try:
            co_data = json.loads(co_file.read_text(encoding="utf-8"))
            voice_dur = float(co_data.get("voice", {}).get("duration", 0))
            if voice_dur > 0.5:
                probe = run(
                    f'ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "{out_mp4.name}"',
                    shell=True, cwd=str(out_dir), capture_output=True, text=True, encoding="utf-8",
                )
                actual = float((probe.stdout or "0").strip() or 0)
                drift = abs(actual - voice_dur)
                if drift > 0.05:
                    locked = out_dir / f"{out_mp4.stem}_locked.mp4"
                    # tpad clone last frame to voice_dur, then -t cap exact
                    lock_cmd = (
                        f'ffmpeg -y -i "{out_mp4.name}" -vf "tpad=stop_mode=clone:stop_duration={voice_dur}" '
                        f'-t {voice_dur} -c:v libx264 -preset ultrafast -crf 23 -c:a copy "{locked.name}"'
                    )
                    lock_result = run(lock_cmd, shell=True, cwd=str(out_dir), capture_output=True, text=True, encoding="utf-8")
                    if locked.exists() and locked.stat().st_size > 1024:
                        out_mp4.unlink()
                        locked.rename(out_mp4)
                        print(f"  ✓ Duration locked: {actual:.2f}s → {voice_dur:.2f}s (drift {drift:.2f}s)")
                    else:
                        print(f"  ⚠ Duration lock failed, keeping original ({actual:.2f}s vs voice {voice_dur:.2f}s)")
                        print(f"     stderr: {(lock_result.stderr or '')[-300:]}")
                else:
                    print(f"  ✓ Duration already aligned ({actual:.2f}s ≈ {voice_dur:.2f}s)")
        except Exception as e:
            print(f"  ⚠ Duration lock skipped: {e}")

        # ===== STEP 7.6: Lead-in trim backoff 120ms (P3.13, ported từ html-video render.ts L341) =====
        # Nếu voice yên lặng đầu > 0.2s, trim back 0.12s tránh clip frame đầu thật (recorder jitter)
        try:
            tr_file = out_dir / "transcript.json"
            if tr_file.exists():
                tr = json.loads(tr_file.read_text(encoding="utf-8"))
                segs = tr.get("segments", [])
                if segs:
                    first_start = float(segs[0].get("start", 0))
                    if first_start > 0.2:
                        seek_sec = round((first_start * 1000 - 120) / 1000, 3)
                        if seek_sec > 0.02:
                            trimmed = out_dir / f"{out_mp4.stem}_trim.mp4"
                            trim_cmd = (
                                f'ffmpeg -y -ss {seek_sec} -i "{out_mp4.name}" '
                                f'-c:v libx264 -preset ultrafast -crf 23 -c:a aac "{trimmed.name}"'
                            )
                            trim_result = run(trim_cmd, shell=True, cwd=str(out_dir), capture_output=True, text=True, encoding="utf-8")
                            if trimmed.exists() and trimmed.stat().st_size > 1024:
                                out_mp4.unlink()
                                trimmed.rename(out_mp4)
                                print(f"  ✓ Lead-in trimmed seek={seek_sec}s (first speech {first_start:.2f}s − 120ms backoff)")
                            else:
                                print(f"  ⚠ Trim failed: {(trim_result.stderr or '')[-200:]}")
        except Exception as e:
            print(f"  ⚠ Lead-in trim skipped: {e}")

        # ===== STEP 7.7: Animation probe — skip infinite repeats (P3.14) =====
        # Note: pipeline KHÔNG self-probe (hyperframes CLI tự lo). Hint cho template author:
        # phải dùng `animation-iteration-count: 1` thay vì `infinite` cho mọi keyframes
        # đóng vai trò "intro motion". `infinite` chỉ dành cho ambient (particles, blob drift).
        # Compose.py không strip infinite vì hyperframes detect được; doc này chỉ là pointer.

        step(8, "✓ DONE")
        print(f"\n🎬 VIDEO: {out_mp4}")
        print(f"   Size: {out_mp4.stat().st_size / 1024 / 1024:.2f} MB")
        print(f"   Editor: double-click MO_EDITOR.vbs để mở editor cho workspace này")
    else:
        print("⚠ Render failed. Check:")
        print(f"   index.html: {out_dir/'index.html'}")
        print(f"   stderr: {result.stderr[-500:]}")
        print(f"   Editor vẫn dùng được: double-click MO_EDITOR.vbs")


if __name__ == "__main__":
    main()
