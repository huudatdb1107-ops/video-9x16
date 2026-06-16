"""fill_content.py — dùng LLM (via agy_wrapper) để fill content.json từ script + template schema.

Workflow:
  1. Đọc template/content.schema.json
  2. Đọc script narration
  3. Gọi Antigravity với prompt: "Phân tích script này, chia thành N scene theo schema, trả về JSON"
  4. Parse JSON output → save vào <output_dir>/content.json

Usage:
  python fill_content.py --template 01_Text --script-file script.txt --output-dir my_video/

LLM call dùng agy_wrapper.py (E:\HuuDat\BrianD\TOOL_BrianD\agy_wrapper.py).
Fallback nếu LLM fail: print prompt ra console để Boss tự fill manually.
"""
import sys, json, argparse, pathlib, subprocess, re
sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
TEMPLATES_DIR = ROOT / "TEST" / "_templates"
AGY_WRAPPER = ROOT / "agy_wrapper.py"
PY = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"

PROMPT_TPL = """Bạn là content designer cho video TikTok/Reels tiếng Việt dọc 9:16.

Nhiệm vụ: phân tích SCRIPT narration dưới đây + chia thành các scene theo SCHEMA của template "{template_name}".

QUY TẮC bắt buộc:
- KHÔNG dùng số Ả-Rập ≥ 2 chữ số (vd "36" → "ba mươi sáu", "1/36" → "một trên ba mươi sáu")
- KHÔNG dùng acronym ALL-CAPS Latin trong title/text (vd "ADHD" → "rối loạn tăng động giảm chú ý")
- Tô màu keyword: dùng <em class="hl-orange">, <em class="hl-teal">, hoặc <em class="hl-cyan"> cho từ quan trọng
- Tiếng Việt rõ ràng, ngắn gọn, không sáo rỗng
- Câu > 20 từ phải có dấu phẩy ngắt
- Nếu kịch bản/chủ đề có nhiều ý (ví dụ: 5, 10 phương pháp), hãy tự động gom nhóm khoa học thành đúng cấu trúc 3 cards ở s3 và 3 items ở s5 để đảm bảo tính thẩm mỹ layout của template.

SCHEMA template "{template_name}":
{schema_json}

SCRIPT NARRATION:
{script_text}

OUTPUT YÊU CẦU: trả lời CHỈ JSON object đúng schema, KHÔNG markdown fence, KHÔNG giải thích. Bắt đầu bằng `{{` và kết thúc bằng `}}`. JSON phải có:
- scenes: dict với keys s1, s2, ... theo schema
- voice: {{file: "narration.wav", duration: <ước lượng giây>}}
- timeline: dict {{s1: {{in, out}}, ...}} chia đều theo voice duration

JSON output:
"""

def call_agy(prompt: str) -> str:
    """Gọi Antigravity via agy_wrapper.py. Return raw text response."""
    if not AGY_WRAPPER.exists():
        return ""
    proc = subprocess.run(
        [PY, str(AGY_WRAPPER), prompt],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    return proc.stdout.strip()

def extract_json(text: str) -> dict | None:
    """Tìm largest valid JSON object trong text."""
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    # Find JSON object
    start = text.find("{")
    if start < 0: return None
    # Try parse từ start tới end
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--script-file", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    tpl_dir = TEMPLATES_DIR / args.template
    schema_file = tpl_dir / "content.schema.json"
    assert schema_file.exists(), f"Schema missing: {schema_file}"
    schema = schema_file.read_text(encoding="utf-8")
    script = pathlib.Path(args.script_file).read_text(encoding="utf-8").strip()

    prompt = PROMPT_TPL.format(template_name=args.template, schema_json=schema, script_text=script)

    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "content.json"

    print("Calling Antigravity LLM via agy_wrapper...")
    response = call_agy(prompt)
    if not response:
        print("⚠ LLM call failed (agy_wrapper unavailable). Dumping prompt to manual_prompt.txt")
        (out_dir / "manual_prompt.txt").write_text(prompt, encoding="utf-8")
        print(f"→ Boss tự gọi LLM với prompt trong: {out_dir/'manual_prompt.txt'}")
        print(f"→ Sau khi có JSON, save vào: {out_file}")
        sys.exit(1)

    data = extract_json(response)
    if not data:
        print("⚠ LLM response không phải JSON hợp lệ:")
        print(response[:500])
        (out_dir / "llm_raw_response.txt").write_text(response, encoding="utf-8")
        sys.exit(1)

    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ content.json saved: {out_file}")
