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
TEMPLATES_DIR = ROOT / ".agent" / "skills" / "video-9x16" / "_templates"
AGY_WRAPPER = ROOT / "agy_wrapper.py"
PY = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"

PROMPT_TPL = """Bạn là content designer cho video TikTok/Reels tiếng Việt dọc 9:16.

Nhiệm vụ: phân tích SCRIPT narration dưới đây + chia thành các scene theo SCHEMA của template "{template_name}".

QUY TẮC bắt buộc:
- KHÔNG dùng số Ả-Rập ≥ 2 chữ số (vd "36" → "ba mươi sáu", "1/36" → "một trên ba mươi sáu")
- KHÔNG dùng acronym ALL-CAPS Latin trong title/text (vd "ADHD" → "rối loạn tăng động giảm chú ý")
- Highlight & Ngắt dòng cho Tiêu đề S1 (title) và S6 (title): KHÔNG được lấy nguyên văn câu thoại dài của script làm tiêu đề. Tiêu đề trên màn hình bắt buộc phải cực kỳ ngắn gọn và giật tít (S1 chỉ từ 5-7 từ, S6 chỉ từ 4-6 từ). Bắt buộc phải sử dụng thẻ <br> để chia tiêu đề thành 2 dòng cân đối để tránh chữ bị to quá tràn ra ngoài khung hình (font size 130px cực kỳ lớn).
- QUY TẮC HIGHLIGHT: Luôn sử dụng thẻ <em>...</em> trơn (KHÔNG có class, KHÔNG có thuộc tính khác) để bọc từ khóa trong TẤT CẢ các scene (S1, S3, S4, S5, S6) để tạo điểm nhấn. Hệ thống sẽ tự động chuyển màu thích hợp.
- BẮT BUỘC HIGHLIGHT:
  - S1 title và S6 title BẮT BUỘC PHẢI CHỨA CHÍNH XÁC 2 THẺ <em>...</em>. S4 quote BẮT BUỘC PHẢI CHỨA CHÍNH XÁC 2 THẺ <em>...</em> (Không được thiếu, không được quên!).
  - Ở S1 title: Bọc 1 cụm ở dòng 1 và 1 cụm ở dòng 2. Ví dụ: "Con chỉ<br><em>hiếu động</em> hay <em>tăng động</em>?"
  - Ở S6 title: Bọc 2 cụm từ quan trọng. Ví dụ: "Đồng hành cùng con,<br><em>yêu thương</em> và <em>kiên nhẫn</em>"
  - Trong mỗi Card ở S3, mỗi câu Quote ở S4, và mỗi Item ở S5: BẮT BUỘC PHẢI CHỨA CHÍNH XÁC 2 THẺ <em>...</em> trơn để tạo 2 màu sắc (xanh ngọc và cam) xen kẽ độc lập trong từng block card/item/quote, giúp giao diện sinh động.
- QUY TẮC NGỮ NGHĨA highlight: Chỉ bọc thẻ <em> cho các từ/cụm từ mang ý nghĩa TÍCH CỰC, THẤU CẢM, GIẢI PHÁP (vd: "thấu hiểu", "yêu thương", "kiên nhẫn", "kết nối", "tự lập"). Tuyệt đối KHÔNG highlight các từ mang nghĩa tiêu cực, phủ định hoặc các lỗi hành vi (vd: "đứa trẻ hư", "chống đối", "nghịch ngợm", "mất kiểm soát", "ăn vạ").
- Quy tắc ngắt dòng Heading (S3/S5) và Quote S4 (quote_html):
  - Heading S3/S5: Phải chèn 1 thẻ <br> chia làm 2 dòng rất ngắn gọn và cân đối.
  - Quote S4 (quote_html): BẮT BUỘC chèn từ 2 đến 3 thẻ <br> để chia câu quote thành đúng 3 hoặc 4 dòng cực kỳ cân đối. Mỗi dòng CHỈ ĐƯỢC CHỨA TỐI ĐA 5 ĐẾN 6 TỪ (không dòng nào được vượt quá 6 từ để tránh bị trình duyệt tự ngắt làm gãy chữ). Bắt buộc ngắt dòng đúng sau các cụm từ có nghĩa trọn vẹn, tuyệt đối CẤM ngắt dòng ở giữa một từ ghép (ví dụ các từ "tinh tế", "cha mẹ", "chìa khóa", "hạnh phúc", "phát triển" phải nằm nguyên vẹn trên cùng một dòng). TUYỆT ĐỐI CẤM để dòng cuối cùng chỉ chứa 1 hoặc 2 từ lơ lửng đơn độc.
- Quy tắc ngắt dòng trong Cards/Items (S3/S5): CẤM TUYỆT ĐỐI việc tự ý chèn bất kỳ thẻ <br> nào trong nội dung của cards (S3) và items (S5). Toàn bộ nội dung của mỗi card/item phải là một chuỗi văn bản liền mạch chạy dài (chỉ chứa thẻ <em>...</em> để highlight). Trình duyệt sẽ tự động xuống dòng tự nhiên theo box layout để tránh trống trải hoặc gãy vụn.
- Tiếng Việt rõ ràng, ngắn gọn, không sáo rỗng.
- Câu > 20 từ phải có dấu phẩy ngắt.
- Tuyệt đối KHÔNG dùng chữ "Bom Bom" hay "BomBom" trong kịch bản và các phần text, bắt buộc phải thay thế hoàn toàn bằng chữ "BOOM BOOM" viết hoa (ví dụ: "nhấn theo dõi BOOM BOOM để...", "PK NHI BOOM BOOM").
- Nếu kịch bản/chủ đề có nhiều ý, hãy tự động gom nhóm khoa học thành đúng cấu trúc 3 cards ở s3 và 3 items ở s5 để đảm bảo tính thẩm mỹ layout của template.
- Với scene 2 (s2), phần "big_text" (ví dụ: từ khóa chủ đề ngắn) bắt buộc phải là một từ viết tắt/từ khóa vô cùng ấn tượng ngắn gọn hoặc con số viết chữ hoa (ví dụ: "KHÁC BIỆT", "SỰ THẬT", "10 PHẦN TRĂM"). Tuyệt đối KHÔNG trích xuất các từ chung chung vô nghĩa như "ba", "hai", "một", "bốn".
- QUY TẮC PHÒNG TRÁNH LỖI JSON: Tuyệt đối KHÔNG sử dụng dấu ngoặc kép thẳng (") bên trong nội dung các chuỗi text tiếng Việt. Hãy sử dụng dấu ngoặc đơn (') hoặc dấu ngoặc kép kiểu Pháp (« và ») để nhấn mạnh từ nhằm tránh gây lỗi cú pháp JSON.
- Khi viết mã HTML bên trong chuỗi JSON (như thẻ em, span), bắt buộc phải escape toàn bộ dấu ngoặc kép của thuộc tính thành \". Ví dụ: <em class=\"hl-orange\"> hoặc <span style=\"color:#15C8C1\">. Không được viết class="hl-orange" vì sẽ gây lỗi cú pháp JSON.

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
    # Auto-escape unescaped double quotes in class/style/id HTML attributes
    text = re.sub(r'(\bclass|\bstyle)="([^"]*?)"', r'\1=\\"\2\\"', text)
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

HIGHLIGHT_KEYWORDS = [
    'thấu hiểu', 'thấu cảm', 'yêu thương', 'kiên nhẫn', 'kết nối', 
    'tự lập', 'đồng hành', 'trí tuệ', 'tự tin', 'an toàn', 
    'phát triển', 'lắng nghe', 'chia sẻ', 'giúp đỡ', 'tôn trọng',
    'học cách', 'chăm sóc', 'khích lệ', 'động viên', 'tin tưởng'
]

def auto_wrap_missing_ems(text: str) -> str:
    """Tự động bọc thêm thẻ em nếu câu thiếu highlight (cần đúng 2 thẻ em)."""
    import re
    # Đếm số thẻ em hiện có
    ems = re.findall(r'<(?:em|em[^>]*)>(.*?)</em[^>]*>', text, flags=re.IGNORECASE)
    needed = 2 - len(ems)
    if needed <= 0:
        return text

    # Tạm thời thay thế các thẻ em hiện có bằng placeholder để tránh bọc đè
    placeholders = []
    def repl(m):
        placeholders.append(m.group(0))
        return f"__EM_PLACEHOLDER_{len(placeholders)-1}__"
    
    temp_text = re.sub(r'<(?:em|em[^>]*)>.*?</em[^>]*>', repl, text, flags=re.IGNORECASE)
    
    # Thử tìm các từ khóa tích cực phổ biến trong phần text còn lại
    for kw in HIGHLIGHT_KEYWORDS:
        if needed <= 0:
            break
        # Tìm từ khóa đứng độc lập (không nằm trong placeholder)
        pattern = rf'(?<!\w)({re.escape(kw)})(?!\w)'
        matches = list(re.finditer(pattern, temp_text, flags=re.IGNORECASE))
        if matches:
            # Bọc match đầu tiên tìm được
            m = matches[0]
            temp_text = temp_text[:m.start()] + f"<em>{m.group(1)}</em>" + temp_text[m.end():]
            needed -= 1

    # Nếu vẫn thiếu, ta chọn một cụm từ dài 2-3 từ ở phần text dài nhất để bọc làm từ khóa thứ 2
    if needed > 0:
        # Split theo các placeholders và các thẻ em mới bọc
        parts = re.split(r'(__EM_PLACEHOLDER_\d+__|<(?:em|em[^>]*)>.*?</em[^>]*>)', temp_text)
        longest_part_idx = -1
        longest_len = 0
        for idx, part in enumerate(parts):
            if not part.startswith('__EM_PLACEHOLDER_') and not part.startswith('<em'):
                clean_part = re.sub(r'<[^>]+>', '', part).strip()
                words = clean_part.split()
                if len(words) >= 3 and len(clean_part) > longest_len:
                    longest_len = len(clean_part)
                    longest_part_idx = idx
        
        if longest_part_idx != -1:
            part = parts[longest_part_idx]
            # Split clean_part để tránh dính thẻ HTML trong kw_text
            clean_part = re.sub(r'<[^>]+>', ' ', part).strip()
            clean_words = clean_part.split()
            if len(clean_words) >= 4:
                start_w = len(clean_words) - 3
                kw_text = " ".join(clean_words[start_w:start_w+2])
            else:
                kw_text = " ".join(clean_words[-2:]) if len(clean_words) >= 2 else clean_words[0]
            
            # Tạo pattern cho phép có thẻ HTML xen kẽ giữa các từ của kw_text
            words_pattern = r'\s*(?:<[^>]+>\s*)*'.join(re.escape(w) for w in kw_text.split())
            pattern = rf'(?<!\w)({words_pattern})(?!\w)'
            part_replaced = re.sub(pattern, rf'<em>\1</em>', part, count=1, flags=re.IGNORECASE)
            parts[longest_part_idx] = part_replaced
            temp_text = "".join(parts)
            needed -= 1

    # Restore placeholders
    for idx, placeholder_val in enumerate(placeholders):
        temp_text = temp_text.replace(f"__EM_PLACEHOLDER_{idx}__", placeholder_val)
        
    return temp_text

def process_highlights(data: dict) -> dict:
    """Post-processes data to format em tags correctly for templates."""
    import re
    
    # Process S3 cards
    if "scenes" in data and "s3" in data["scenes"]:
        s3 = data["scenes"]["s3"]
        fields = s3.get("fields", s3)
        if isinstance(fields, dict) and "cards" in fields and isinstance(fields["cards"], list):
            for card in fields["cards"]:
                if isinstance(card, dict) and "text" in card and isinstance(card["text"], str):
                    # Tự động bọc thêm nếu thiếu
                    card["text"] = auto_wrap_missing_ems(card["text"])
                    
                    text = card["text"]
                    parts = re.split(r'<(?:em|em[^>]*)>(.*?)</em[^>]*>', text, flags=re.IGNORECASE)
                    new_text = []
                    color_idx = 0
                    for i, part in enumerate(parts):
                        if i % 2 == 0:
                            new_text.append(part)
                        else:
                            cls = "teal" if color_idx % 2 == 0 else "orange"
                            new_text.append(f'<em class="hl-{cls}">{part}</em>')
                            color_idx += 1
                    card["text"] = "".join(new_text)

    # Process S4 quote
    if "scenes" in data and "s4" in data["scenes"]:
        s4 = data["scenes"]["s4"]
        fields = s4.get("fields", s4)
        if isinstance(fields, dict) and "quote_html" in fields and isinstance(fields["quote_html"], str):
            # Tự động bọc thêm nếu thiếu
            fields["quote_html"] = auto_wrap_missing_ems(fields["quote_html"])
            
            text = fields["quote_html"]
            parts = re.split(r'<(?:em|em[^>]*)>(.*?)</em[^>]*>', text, flags=re.IGNORECASE)
            new_text = []
            color_idx = 0
            for i, part in enumerate(parts):
                if i % 2 == 0:
                    new_text.append(part)
                else:
                    cls = "teal" if color_idx % 2 == 0 else "orange"
                    new_text.append(f'<em class="hl-{cls}">{part}</em>')
                    color_idx += 1
            fields["quote_html"] = "".join(new_text)

    # Process S5 items
    if "scenes" in data and "s5" in data["scenes"]:
        s5 = data["scenes"]["s5"]
        fields = s5.get("fields", s5)
        if isinstance(fields, dict) and "items" in fields and isinstance(fields["items"], list):
            for item in fields["items"]:
                if isinstance(item, dict) and "text" in item and isinstance(item["text"], str):
                    # Tự động bọc thêm nếu thiếu
                    item["text"] = auto_wrap_missing_ems(item["text"])
                    
                    text = item["text"]
                    parts = re.split(r'<(?:em|em[^>]*)>(.*?)</em[^>]*>', text, flags=re.IGNORECASE)
                    new_text = []
                    color_idx = 0
                    for i, part in enumerate(parts):
                        if i % 2 == 0:
                            new_text.append(part)
                        else:
                            cls = "teal" if color_idx % 2 == 0 else "orange"
                            new_text.append(f'<em class="hl-{cls}">{part}</em>')
                            color_idx += 1
                    item["text"] = "".join(new_text)

    # For S1, S6, make sure we use plain <em> tags (no class)
    for sid in ["s1", "s6"]:
        if "scenes" in data and sid in data["scenes"]:
            scene = data["scenes"][sid]
            fields = scene.get("fields", scene)
            if isinstance(fields, dict):
                key = "title" if sid in ["s1", "s6"] else "quote_html"
                if key in fields and isinstance(fields[key], str):
                    val = fields[key]
                    val = re.sub(r'<(?:em|em[^>]*)>', '<em>', val, flags=re.IGNORECASE)
                    val = re.sub(r'</em[^>]*>', '</em>', val, flags=re.IGNORECASE)
                    fields[key] = val

    return data


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

    data = process_highlights(data)
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ content.json saved: {out_file}")
