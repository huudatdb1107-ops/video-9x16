"""gen_voice.py — gen narration.wav từ script text qua OMNI bridge.

bridge_omni.py đã tự reconfigure sys.stdin utf-8 (memory lesson_omni_bridge_pythonioencoding)
→ KHÔNG cần PYTHONIOENCODING env nữa, an toàn cho text Việt.

Usage:
  python gen_voice.py --script "Con bạn nghịch suốt ngày..." --output narration.wav
  python gen_voice.py --script-file script.txt --voice TT_06 --output narration.wav
"""
import os, sys, json, subprocess, pathlib, argparse, re
sys.stdout.reconfigure(encoding="utf-8")

ROOT  = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
TOOL  = ROOT / "B-Go" / "5.2_OMNI_VOICE_Br" / "python_engine"
COMP  = ROOT / "B-Go" / "5.2_OMNI_VOICE_Br" / "python_engine_compiled"
PY    = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"

TTS_MAX_WORDS = 20

def audit_text(text: str):
    issues = []
    for m in re.finditer(r"\b[A-Z]{2,}\b", text):
        issues.append(f"Acronym '{m.group()}' — chuyển sang đọc Việt")
    for m in re.finditer(r"\b\d{2,}\b", text):
        issues.append(f"Số '{m.group()}' — viết bằng chữ Việt")
    for sent in re.split(r"[.!?]\s+", text):
        words = sent.split()
        if len(words) > TTS_MAX_WORDS and "," not in sent:
            issues.append(f"Câu {len(words)} từ không có dấu phẩy")
    return issues

def gen(text: str, voice: str, output: pathlib.Path, params: dict = None):
    # Tìm kiếm file wav mẫu thông minh
    voice_wav = TOOL / "voice_mau" / "wavs" / f"{voice}.wav"
    if not voice_wav.exists():
        # Thử tìm trong thư mục con saved_voices_goc_VinaVoice
        alt_wav = TOOL / "voice_mau" / "saved_voices_goc_VinaVoice" / f"{voice}.wav"
        if alt_wav.exists():
            voice_wav = alt_wav
            
    assert voice_wav.exists(), f"Voice sample missing: {voice_wav}"

    issues = audit_text(text)
    if issues:
        print("⚠ TTS audit warnings:")
        for i, msg in enumerate(issues, 1):
            print(f"  {i}. {msg}")
        print("→ Gen vẫn tiếp tục, output có thể không tối ưu.\n")

    args = {
        "mode": "TXT",
        "text": text,
        "voice_wav": str(voice_wav),
        "ref_transcript": "",
        "output_dir": str(output.parent),
        "language": "vi",
        "params": params or {"exag": 1.0, "temp": 0.7, "pause": 0.5, "guidance": 2.0, "steps": 32},
        "threads": 1,
    }

    env = os.environ.copy()
    env["BRIAND_ENGINE_DIR"] = str(COMP)
    env["BRIAND_CACHE_DIR"]  = str(output.parent)
    env["PYTHONPATH"] = f"{COMP};{TOOL}"
    # bridge_omni đã tự fix utf-8 stdin nhưng set thêm cho safety
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"Spawning OMNI bridge: voice={voice}.wav, {len(text)} chars, ~{len(text.split())} từ")
    proc = subprocess.Popen(
        [PY, str(TOOL / "bridge_omni.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=str(TOOL),
    )
    out, _ = proc.communicate(input=json.dumps(args, ensure_ascii=False).encode("utf-8"))
    out_str = out.decode("utf-8", errors="replace")

    # Find output filename from log
    m = re.search(r'"files":\s*\[\s*"([^"]+)"', out_str)
    actual_path = pathlib.Path(m.group(1)) if m else None
    if actual_path and actual_path.exists():
        # Rename to target output
        actual_path.rename(output)
        print(f"✓ Voice gen done: {output}")
        return output
    else:
        print(f"✗ Voice gen failed (exit {proc.returncode}). Thử lại bằng cách ép chạy trên CPU (CUDA_VISIBLE_DEVICES=\"\")...")
        env["CUDA_VISIBLE_DEVICES"] = ""
        proc2 = subprocess.Popen(
            [PY, str(TOOL / "bridge_omni.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=str(TOOL),
        )
        out2, _ = proc2.communicate(input=json.dumps(args, ensure_ascii=False).encode("utf-8"))
        out_str2 = out2.decode("utf-8", errors="replace")
        m2 = re.search(r'"files":\s*\[\s*"([^"]+)"', out_str2)
        actual_path2 = pathlib.Path(m2.group(1)) if m2 else None
        if actual_path2 and actual_path2.exists():
            actual_path2.rename(output)
            print(f"✓ Voice gen done on CPU: {output}")
            return output
        else:
            print(f"✗ Voice gen CPU also failed (exit {proc2.returncode})")
            print(out_str2[-1500:])
            return None


def clean_voice_text(text: str) -> str:
    # 0. Thay thế các từ chuyên môn/viết tắt tiếng Anh bằng từ ngữ thuần Việt gần gũi khi thu voice
    acronyms_map = {
        r"\bADHD\b": "tăng động giảm chú ý",
        r"\bADHDs\b": "tăng động giảm chú ý",
        r"\bAI\b": "ai i",
    }
    for pattern, replacement in acronyms_map.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        # 1. Loại bỏ các dòng chú thích/ghi chú ngoài lề (ví dụ: *(Lưu ý: ...)*)
        lower_line = stripped.lower()
        # Sử dụng regex nhận diện chính xác các dòng ghi chú/chú thích bắt đầu bằng từ khoá để tránh loại bỏ nhầm từ "giảm chú ý" trong kịch bản ADHD
        if re.search(r"^\s*\(?\s*(lưu ý|ghi chú|chú ý|note)\s*[:\-]", lower_line) or "trùng chủ đề" in lower_line:
            print(f"[SYSTEM] [clean-voice] Loại bỏ dòng chú thích/ghi chú: {stripped}")
            continue
            
        # 2. Loại bỏ dấu * hoặc ** định dạng Markdown
        clean_line = stripped.replace("*", "")
        
        # 3. Loại bỏ dấu ngoặc đơn bao bọc cả câu (nếu có)
        if clean_line.startswith("(") and clean_line.endswith(")"):
            clean_line = clean_line[1:-1].strip()
            
        # 4. Loại bỏ các khoảng trắng thừa
        clean_line = clean_line.strip()
        if clean_line:
            cleaned_lines.append(clean_line)
            
    return "\n".join(cleaned_lines)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--script", help="Inline script text")
    src.add_argument("--script-file", help="Path to .txt file")
    ap.add_argument("--voice",  default="TT_06", help="Voice sample name in voice_mau/wavs/ (no .wav extension)")
    ap.add_argument("--output", default="narration.wav", help="Output wav path")
    args = ap.parse_args()

    text = pathlib.Path(args.script_file).read_text(encoding="utf-8").strip() if args.script_file else args.script
    cleaned_text = clean_voice_text(text)
    out  = pathlib.Path(args.output).resolve()
    result = gen(cleaned_text, args.voice, out)
    sys.exit(0 if result else 1)
