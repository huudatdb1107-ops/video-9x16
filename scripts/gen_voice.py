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
    voice_wav = TOOL / "voice_mau" / "wavs" / f"{voice}.wav"
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
        print(f"✗ Voice gen failed (exit {proc.returncode})")
        print(out_str[-1500:])
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--script", help="Inline script text")
    src.add_argument("--script-file", help="Path to .txt file")
    ap.add_argument("--voice",  default="TT_06", help="Voice sample name in voice_mau/wavs/ (no .wav extension)")
    ap.add_argument("--output", default="narration.wav", help="Output wav path")
    args = ap.parse_args()

    text = pathlib.Path(args.script_file).read_text(encoding="utf-8").strip() if args.script_file else args.script
    out  = pathlib.Path(args.output).resolve()
    result = gen(text, args.voice, out)
    sys.exit(0 if result else 1)
