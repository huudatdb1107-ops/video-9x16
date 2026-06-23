"""Transcribe narration.wav using faster-whisper base int8 vi."""
import sys, json, pathlib
sys.stdout.reconfigure(encoding="utf-8")
from faster_whisper import WhisperModel

WORK = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD\TEST\tre_em_hieu_dong_vs_tang_dong")
AUDIO = WORK / "narration.wav"

print(f"[1/3] Loading faster-whisper base (int8)...")
model = WhisperModel("base", device="cpu", compute_type="int8")

print(f"[2/3] Transcribing {AUDIO.name}...")
segments, info = model.transcribe(str(AUDIO), language="vi", beam_size=5, word_timestamps=True)

out_segments = []
for seg in segments:
    out_segments.append({
        "start": round(seg.start, 3),
        "end":   round(seg.end, 3),
        "text":  seg.text.strip(),
        "words": [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)} for w in (seg.words or [])],
    })

print(f"[3/3] Got {len(out_segments)} segments, duration {info.duration:.2f}s")
result = {"duration": round(info.duration, 3), "segments": out_segments}
(WORK / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
for s in out_segments:
    print(f"  [{s['start']:6.2f} - {s['end']:6.2f}s]  {s['text']}")
