"""run_batch.py — Script chạy batch sản xuất video ngắn hàng loạt cho skill video-9x16.

Chế độ hoạt động:
  1. --mode generate: Sinh danh sách kịch bản tự động bằng Gemini, tránh trùng các chủ đề cũ.
  2. --mode run: Chạy vòng lặp qua các kịch bản đã duyệt để sinh thư mục dự án qua run_pipeline.py.
"""
import sys
import os
import argparse
import json
import pathlib
import time
import subprocess

# Thiết lập mã hoá UTF-8 cho stdout/stderr
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
SCRIPT_DIR = pathlib.Path(__file__).parent
RUN_PIPELINE = SCRIPT_DIR / "run_pipeline.py"
PY = r"C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"
AGY_WRAPPER = ROOT / "agy_wrapper.py"

PAGE_PROFILES = {}
try:
    _profile_path = SCRIPT_DIR.parent / "video_brand_profiles.json"
    if _profile_path.exists():
        PAGE_PROFILES = json.loads(_profile_path.read_text(encoding="utf-8"))
except Exception as _e:
    print(f"[SYSTEM] Lỗi load video_brand_profiles.json: {_e}")

# Fallback
if not PAGE_PROFILES:
    PAGE_PROFILES = {
        "kid": {
            "name": "👶 Vì Con không thể đợi",
            "output_dir": r"E:\HuuDat\VIDEO\03_KID",
            "default_voice": "TT_06",
            "default_template": "01_Text_KID"
        }
    }


def log_system(msg):
    print(f"[SYSTEM] {msg}")

def log_user(msg):
    print(f"[USER] {msg}")

def call_gemini(prompt: str) -> str:
    """Gọi Gemini CLI thông qua agy_wrapper.py"""
    # Thay thế dấu ngoặc kép bằng ngoặc đơn để tránh lỗi cú pháp CMD shell
    sanitized_prompt = prompt.replace('"', "'")
    cmd = [PY, str(AGY_WRAPPER), sanitized_prompt]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    
    if result.returncode != 0:
        raise Exception(f"Gọi Gemini thất bại (exit {result.returncode}): {result.stderr}")
    return result.stdout.strip()

def load_history(history_file: pathlib.Path) -> list:
    """Tải lịch sử chủ đề cũ"""
    if not history_file.exists():
        return []
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # Trích xuất trường topic hoặc string trực tiếp
            return [item["topic"] if isinstance(item, dict) and "topic" in item else item for item in data]
    except Exception as e:
        log_system(f"Không thể đọc file lịch sử: {e}. Sử dụng danh sách rỗng.")
    return []

def save_history(history_file: pathlib.Path, topic: str):
    """Ghi nhận chủ đề mới vào lịch sử"""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    data = []
    if history_file.exists():
        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            data = []
    
    # Ghi nhận dưới dạng object kèm timestamp để dễ theo dõi
    data.append({
        "topic": topic,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    
    # Ghi đè và ép ghi xuống đĩa (os.fsync) bảo tồn cấu hình/dữ liệu theo rule
    temp_file = history_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
        
    if history_file.exists():
        os.remove(history_file)
    os.rename(temp_file, history_file)

def mode_generate(args, out_dir: pathlib.Path, history_file: pathlib.Path, batch_file: pathlib.Path):
    log_system("Bắt đầu chế độ sinh kịch bản (--mode generate)...")
    
    # 1. Đọc lịch sử chủ đề
    history_topics = load_history(history_file)
    log_system(f"Đã tải {len(history_topics)} chủ đề từ lịch sử chống lặp.")
    
    # 2. Lên danh sách chủ đề mới
    history_str = ", ".join([f"'{t}'" for t in history_topics[-50:]]) # Lấy 50 chủ đề gần nhất để tránh overload prompt
    
    prompt_topics = (
        f"Hãy gợi ý đúng {args.count} chủ đề ngắn gọn (dưới 15 từ mỗi chủ đề) cho kênh Fanpage 'Vì Con' chuyên về "
        f"nuôi dạy con, tâm lý trẻ đặc biệt (tăng động giảm chú ý ADHD, tự kỷ, chậm phát triển) và kỹ năng nuôi dạy con tự lập.\n"
        f"Danh sách các chủ đề đã làm (TUYỆT ĐỐI không trùng lặp hoặc lặp lại góc nhìn tương tự): [{history_str}].\n"
        f"Hãy trả về kết quả dưới dạng một mảng JSON các chuỗi (string) đơn giản, không chứa markdown (không có ```json), không giải thích gì thêm. "
        f"Ví dụ format bắt buộc: [\"Chủ đề 1\", \"Chủ đề 2\", ...]"
    )
    
    log_system("Đang gửi yêu cầu lên danh sách chủ đề tới Gemini...")
    raw_topics = call_gemini(prompt_topics)
    
    # Xử lý làm sạch chuỗi JSON từ LLM
    raw_topics = raw_topics.strip()
    if raw_topics.startswith("```"):
        # Cắt bỏ dòng ```json và ``` ở cuối nếu LLM không tuân thủ
        lines = raw_topics.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        raw_topics = "\n".join(lines).strip()
        
    try:
        topics = json.loads(raw_topics)
        if not isinstance(topics, list):
            raise ValueError("Đầu ra không phải là mảng JSON")
    except Exception as e:
        log_system(f"Lỗi phân tích cú pháp danh sách chủ đề: {e}")
        log_system(f"Raw output từ Gemini: {raw_topics}")
        sys.exit(1)
        
    log_system(f"Đã sinh thành công {len(topics)} chủ đề mới.")
    
    # 3. Sinh kịch bản cho từng chủ đề
    batch_data = []
    for i, topic in enumerate(topics, 1):
        log_system(f"[{i}/{len(topics)}] Đang sinh kịch bản cho chủ đề: '{topic}'...")
        prompt_script = (
            f"Hãy viết kịch bản video ngắn Reels/TikTok dài 80 giây cho chủ đề: '{topic}'.\n"
            f"Kênh: Vì Con (vicon).\n"
            f"Yêu cầu kịch bản bắt buộc:\n"
            f"1. Độ dài: từ 200 đến 220 từ tiếng Việt. Văn phong ấm áp, thấu hiểu, thực tế, nhắm vào các bậc cha mẹ.\n"
            f"2. Định dạng: Chỉ trả về nội dung văn đọc thuần túy (narration voice) cho AI đọc văn bản thành giọng nói (TTS). "
            f"Tuyệt đối KHÔNG chứa bất kỳ tiêu đề, nhãn phân cảnh nào như 'Cảnh 1:', 'Bộ cảnh:', 'Giới thiệu:', 'CTA:', 'Scene 1:', 'Hook:', 'Voiceover:'. "
            f"Không có ghi chú trong ngoặc đơn hay ngoặc vuông. AI sẽ đọc từng chữ bạn viết ra.\n"
            f"3. Tuyệt đối không chứa markdown (không có dấu **, không có dấu #, không có dấu gạch đầu dòng). Chỉ có các đoạn văn bình thường ngăn cách bằng dấu xuống dòng.\n"
            f"Trả về văn bản thuần túy của kịch bản, không giải thích gì thêm."
        )
        
        try:
            script_content = call_gemini(prompt_script)
            batch_data.append({
                "topic": topic,
                "script": script_content,
                "status": "pending"
            })
            # Delay nhẹ giữa các lượt gọi API để tránh rate limit
            time.sleep(2)
        except Exception as ex:
            log_system(f"Lỗi khi sinh kịch bản cho chủ đề '{topic}': {ex}")
            # Vẫn tiếp tục với các chủ đề tiếp theo
            continue
            
    # Ghi file batch_scripts.json
    batch_file.write_text(json.dumps(batch_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log_system(f"✓ Đã lưu {len(batch_data)} kịch bản vào file batch: {batch_file}")
    log_user("Sếp hãy mở file batch_scripts.json để kiểm tra và tinh chỉnh kịch bản nếu cần thiết, sau đó chạy lệnh tiếp theo với --mode run.")

def mode_run(args, out_dir: pathlib.Path, history_file: pathlib.Path, batch_file: pathlib.Path):
    log_system("Bắt đầu chế độ chạy batch tạo video (--mode run)...")
    if not batch_file.exists():
        log_system(f"Lỗi: Không tìm thấy file kịch bản batch tại {batch_file}. Vui lòng chạy --mode generate trước.")
        sys.exit(1)
        
    try:
        batch_data = json.loads(batch_file.read_text(encoding="utf-8"))
    except Exception as e:
        log_system(f"Lỗi đọc file kịch bản batch: {e}")
        sys.exit(1)
        
    pending_items = [item for item in batch_data if item.get("status") == "pending"]
    log_system(f"Tổng số kịch bản: {len(batch_data)}. Số kịch bản đang chờ xử lý: {len(pending_items)}.")
    
    if not pending_items:
        log_system("Không còn kịch bản nào ở trạng thái pending. Hoàn thành nhiệm vụ!")
        return
        
    temp_script_path = out_dir / "temp_script_batch.txt"
    
    success_count = 0
    for i, item in enumerate(pending_items, 1):
        topic = item["topic"]
        script = item["script"]
        
        log_system(f"[{i}/{len(pending_items)}] Đang xử lý video: '{topic}'...")
        
        # 1. Ghi kịch bản ra file tạm
        temp_script_path.write_text(script, encoding="utf-8")
        
        # 2. Gọi run_pipeline.py
        cmd = [
            PY,
            str(RUN_PIPELINE),
            "--topic", topic,
            "--script-file", str(temp_script_path),
            "--page", args.page,
            "--skip-render"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=False) # Để hiển thị trực tiếp log của pipeline ra console
            
            if result.returncode == 0:
                log_system(f"✓ Tạo video thành công cho chủ đề: '{topic}'")
                item["status"] = "success"
                success_count += 1
                
                # Cập nhật lịch sử chủ đề cũ để tránh lặp
                save_history(history_file, topic)
            else:
                log_system(f"⚠ Pipeline báo lỗi (exit {result.returncode}) cho chủ đề: '{topic}'")
                item["status"] = "failed"
                
        except Exception as ex:
            log_system(f"Lỗi nghiêm trọng khi thực thi pipeline cho chủ đề '{topic}': {ex}")
            item["status"] = "failed"
            
        # Lưu lại tiến trình ngay lập tức để nếu có lỗi ngắt quãng vẫn không bị mất dữ liệu
        batch_file.write_text(json.dumps(batch_data, ensure_ascii=False, indent=2), encoding="utf-8")
        
        # Xóa file tạm
        if temp_script_path.exists():
            temp_script_path.unlink()
            
        # Delay 20 giây giữa các video để tránh overload GPU/API OMNI
        if i < len(pending_items):
            log_system(f"Nghỉ 20 giây trước khi tiếp tục...")
            time.sleep(20)
            
    log_system(f"Hoàn thành lượt chạy batch. Thành công: {success_count}/{len(pending_items)}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["generate", "run"], help="generate (sinh kịch bản) hoặc run (chạy pipeline)")
    ap.add_argument("--page", default="kid", help="Kênh đăng để áp cấu hình mặc định (vicon)")
    ap.add_argument("--count", type=int, default=35, help="Số lượng kịch bản cần sinh (mặc định 35)")
    args = ap.parse_args()
    
    page_key = args.page.lower().strip()
    if page_key not in PAGE_PROFILES:
        log_system(f"Lỗi: Page '{args.page}' chưa được hỗ trợ. Các page hợp lệ: {list(PAGE_PROFILES.keys())}")
        sys.exit(1)
        
    profile = PAGE_PROFILES[page_key]
    out_dir = pathlib.Path(profile["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    
    history_file = out_dir / f"{page_key}_history.json"
    batch_file = out_dir / "batch_scripts.json"
    
    if args.mode == "generate":
        mode_generate(args, out_dir, history_file, batch_file)
    elif args.mode == "run":
        mode_run(args, out_dir, history_file, batch_file)

if __name__ == "__main__":
    main()
