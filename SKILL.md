---
name: video-9x16
description: Tạo video dọc 9:16 TikTok/Reels có voice + animation tự động từ script. Trigger - Boss gõ "/video-9x16", "/video-tiktok", "tạo video 9:16", "video reels".
---

# video-9x16 — Skill tạo video 9:16 tự động

> **META**: Skill chuyên biệt cho video dọc 1080×1920 TikTok/Reels có narration voice + GSAP animation timeline. Khác với `/html-anything-Br` (chỉ HTML tĩnh).

| Thành phần | Đường dẫn |
|---|---|
| **Template library** | `E:\HuuDat\BrianD\TOOL_BrianD\TEST\_templates\` |
| **Components** | `_templates/_components/` (12 block reusable) |
| **Templates** | `_templates/01_Text/` (warm parenting), `_templates/02_TechCold/` (cold tech) |
| **Pipeline orchestrator** | `scripts/run_pipeline.py` |
| **Voice gen** | `scripts/gen_voice.py` (OMNI bridge) |
| **Render engine** | `hyperframes` CLI (npm) |

---

## ⚡ COMMANDS

- **Slash**: `/video-9x16`
- **Trigger phrases**: "tạo video 9:16", "video TikTok", "video reels", "video dọc"

---

## 🏗️ AI BEHAVIOR

1. Khi Boss gõ trigger → hỏi 3 thông tin nếu chưa có:
   - **Topic** (chủ đề video)
   - **Script** (kịch bản narration đầy đủ, viết sẵn hoặc gen mới)
   - **Template** (`01_Text` hay `02_TechCold` — em suggest dựa trên topic)

   > **🚫 SCRIPT FORMAT — BẮT BUỘC:**
   > Script là **văn đọc thẳng cho TTS** — KHÔNG có label "Cảnh 1:", "Bộ cảnh:", "Kết thúc:", "Scene 1:", hay bất kỳ tiêu đề nào.
   > TTS đọc nguyên xi mọi ký tự trong script — label thừa = giọng đọc sai.
   > ✅ ĐÚNG: "Em bé 7 tuổi năng động, nhưng khi đến trường lại gặp khó khăn..."
   > ❌ SAI: "Cảnh 1: Em bé 7 tuổi..." / "Bộ cảnh: Một em bé..."

2. Workflow auto (bước 1→6, **LUÔN dùng `--skip-render`**):
   ```
   Script → parse → match_template → fill_content.json → gen_voice (OMNI) →
   transcribe → update_timeline → compose → index.html + MO_EDITOR.vbs
   ```
   > Render mp4 (bước 7-8) = Boss tự làm: double-click `MO_EDITOR.vbs` → click **🎬 Gen Video**.
   > **KHÔNG chạy bước 7-8, áp dụng mọi agent, không có ngoại lệ.**

3. Mỗi bước log progress, dừng nếu Boss reject.

4. **Output**: folder chứa `index.html` + `narration.wav` + `MO_EDITOR.vbs` — Boss double-click VBS để preview/chỉnh/render.

## 🚫 HARD RULES — PIPELINE EXECUTION

> **VI PHẠM CÁC RULE NÀY = LỖI NGHIÊM TRỌNG**

1. **LUÔN gọi `run_pipeline.py` 1 lệnh duy nhất** — KHÔNG tự tạo script.txt, content.json, hay bất kỳ file nào thủ công trước khi chạy pipeline. Pipeline tự lo hết.

2. **KHÔNG dump file ra root của page folder** — KHÔNG tạo `content.json`, `script.txt`, `llm_raw_response.txt` ở `F:\VIDEO\09_POST\FACEBOOK\01__Vi_Con\` trực tiếp. Mọi file phải nằm trong SUBFOLDER output.

3. **`--output-dir` PHẢI là subfolder trong page folder** — ví dụ `F:\VIDEO\09_POST\FACEBOOK\01__Vi_Con\` (để `run_pipeline.py` tự tạo subfolder với timestamp). KHÔNG dùng `--output-dir` trỏ thẳng vào root page folder.

4. **Tên folder tự động** — `run_pipeline.py` tự gen timestamp prefix `T[MM].[DD]_[HH]h[mm]_`. KHÔNG đặt tên thủ công.

5. **Chỉ 1 lần chạy pipeline per video** — KHÔNG retry nhiều lần tạo nhiều folder rác.

---

## 📋 INPUT / OUTPUT

**CLI gọi pipeline (Cách 1: Khuyên dùng - Sử dụng Profile Page):**
```bash
python scripts/run_pipeline.py \
  --topic "Trẻ em hiếu động vs tăng động" \
  --script-file "script.txt" \
  --page vicon
```
*(Hệ thống tự động ánh xạ thư mục Vì Con `F:\VIDEO\09_POST\FACEBOOK\01__Vi_Con`, áp dụng giọng đọc `TT_06`, watermark `PK NHI BOOM BOOM`, template `01_Text` và hashtag `#NuoiDayCon`)*

**Các Page Profile đã cấu hình sẵn:**
- `vicon` (Vì Con): `F:\VIDEO\09_POST\FACEBOOK\01__Vi_Con` (Giọng `TT_06`, Watermark `PK NHI BOOM BOOM`, Hashtag `#NuoiDayCon`)
- `gocnho` (Góc Nhỏ): `F:\VIDEO\09_POST\FACEBOOK\02_Goc_Nho` (Giọng `TT_01`, Watermark `GÓC NHỎ`, Hashtag `#Stoic`)
- `bsimple` (B.Simple): `F:\VIDEO\09_POST\FACEBOOK\03_B_Simple` (Giọng `TT_02`, Watermark `B.SIMPLE`, Hashtag `#AI`)

**CLI gọi pipeline (Cách 2: Tự định nghĩa đường dẫn tự do):**
```bash
python scripts/run_pipeline.py \
  --topic "Trẻ em hiếu động vs tăng động" \
  --script-file "script.txt" \
  --voice "TT_06" \
  --template "01_Text" \
  --output-dir "E:\path\to\workspace\my_video\" \
  --skip-render
```

**Output structure & Quy tắc đặt tên chuẩn BSIMPLE:**
- **Thư mục đầu ra**: Khi được tạo mới, hệ thống tự động đưa đuôi thời gian theo chuẩn BSIMPLE lên đầu thư mục: `TTháng.Ngày_GiờhPhút_[Tên_Thư_Mục]` (Ví dụ: `T06.16_16h30_my_video`).
- **Tệp Voice**: `TT_TTháng.Ngày_GiờhPhút.wav` (Ví dụ: `TT_T06.16_16h30.wav`).
- **Tệp Video**: `[Tên_chủ_đề_viết_liền_không_dấu]_TTháng.Ngày_GiờhPhút.mp4` (Ví dụ: `tre_em_hieu_dong_vs_tang_dong_T06.16_16h30.mp4`).

Cấu trúc thư mục thành phẩm:
```
T06.16_16h30_my_video/
├── content.json                         (AI fill từ script, voice.file trỏ đúng file wav mới)
├── TT_T06.16_16h30.wav                  (Voice OMNI gen theo thời gian thực)
├── transcript.json                      (faster-whisper transcribe)
├── index.html                           (compose output, audio src tự động replace sang TT_T06.16_16h30.wav)
├── tre_em_hieu_dong_vs_tang_dong_T06.16_16h30.mp4 ★ FINAL VIDEO
└── pipeline.log                         (audit trail mỗi step)
```

---

## 🤖 LLM CALL

Skill dùng `agy_wrapper.py` (root project) để gọi Antigravity/Gemini cho 2 việc:
1. **Parse script → scene segments**: chia kịch bản thành 5-7 đoạn theo cấu trúc template
2. **Fill content.json**: từ scene segments → JSON theo schema của template

**Fallback**: nếu LLM fail → Boss tự fill content.json thủ công + chạy compose riêng.

---

## 🔴 HARD RULES

### Pitfalls
- **LLM fill step may fail**: If step 2 (`fill_content.json`) cannot reach the LLM, the pipeline aborts with a warning. Use the `--skip-llm` flag and provide a manually created `content.json` (see `references/llm_fill_fallback.md`).
- **Missing script file**: Ensure the script path is correct; absolute Windows paths must be prefixed with `/c/` for MSYS compatibility.
- **Port 5050 conflict**: Kill any existing process listening on port 5050 before launching the editor VBS.


1. **Template = single source of truth cho style**. Skill chỉ ráp content vào template, KHÔNG tự sinh CSS/HTML mới (trừ qua editor thủ công).
2. **Voice tiếng Việt**: bridge_omni.py đã có UTF-8 fix (memory `lesson_omni_bridge_pythonioencoding`).
3. **Vietnamese typography rule**: line-height heading ≥1.6, no acronym ALL-CAPS, no số Ả-Rập ≥2 chữ số (rule trong template `vietnamese_rules`).
4. **KHÔNG tự chỉnh template hiện có** — muốn style mới → tạo template mới (vd `03_*`).
5. **Render fail**: dump log đầy đủ + giữ index.html + content.json để debug.
6. **VOICE-FIRST CONTENT** (rule sống còn — memory `lesson_video_sync_voice_first`):
   - Content text scenes PHẢI khớp với câu voice ở thời điểm scene đó visible
   - S4 quote = câu trong voice (không độc lập)
   - Element reveal time (cards S3, items S5) = `element_times` absolute từ transcript, KHÔNG scale uniform
   - `run_pipeline.py` step 5 tự detect element_times từ keyword segments ("Một/Hai/Ba" hoặc "1./2./3.")
7. **Skeleton GSAP** dùng `__TL[sN].element_times[X]` nếu có, fallback scale-based. Đã wire trong `01_Text/skeleton.html`.
8. **LUÔN tạo `MO_EDITOR.vbs`** trong mỗi workspace — không có lựa chọn skip. Pipeline tự gen ngay sau compose (step 6.5). Workflow ngoài pipeline phải gọi `ensure_vbs(out_dir, template)`.
9. **Tối ưu hóa GPU (CUDA) Transcribe**: Ưu tiên tự động chạy faster-whisper trên GPU (CUDA, float16) để tăng tốc độ. Nếu GPU lỗi hoặc thiếu CUDA driver, tự động fallback về CPU (int8).
10. **Gỡ lỗi kẹt Hyperframes**: Khi hyperframes render bị treo kẹt (Node CPU thấp, không spawn chrome.exe), bắt buộc kill sạch tiến trình node chạy hyperframes cũ đang giữ lock file của Puppeteer/Chromium cache.

---

## 🪟 EDITOR + VBS (workspace preview)

**Mỗi workspace output BẮT BUỘC có `MO_EDITOR.vbs`** — file double-click để mở Editor xem/chỉnh content và style trực tiếp trong browser.

**Cấu trúc:**
- Editor server: `_templates/01_Text/editor_server.py` (centralized — KHÔNG copy vào từng workspace)
- VBS launcher: `<workspace>/MO_EDITOR.vbs` — chạy Python ẩn + mở `http://localhost:5050/` với param `--workspace <path>`
- Port: **5050** (cố định)

**Khi nào VBS được gen:**
1. Pipeline `run_pipeline.py` LUÔN tạo VBS ngay sau **step 6 (compose)**, idempotent overwrite ở step 7-8 → mọi workspace ĐỀU có VBS dù render fail / skip
2. Nếu render thủ công bằng `npx hyperframes render` ngoài pipeline → gọi `ensure_vbs(out_dir, template)` (helper trong `run_pipeline.py`) hoặc paste template VBS dưới đây

**Template VBS chuẩn** (paste vào workspace):
```vbs
Set objFSO = CreateObject("Scripting.FileSystemObject")
strDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = strDir
strEditor = "E:\HuuDat\BrianD\TOOL_BrianD\TEST\_templates\01_Text\editor_server.py"
strPython = "C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe"
objShell.Run """" & strPython & """ """ & strEditor & """ --workspace """ & strDir & """", 0, False
WScript.Sleep 1800
objShell.Run "http://localhost:5050/", 1, False
```

**Editor capability:**
- Sửa text từng scene (contenteditable inline)
- Chỉnh CSS per selector: `font-size`, `line-height`, `letter-spacing`, `color`, `gap` (cho `.s3-cards`/`.s5-list`)
- Save → ghi vào `<workspace>/index.html` (CSS rule trong `<style>` block) — KHÔNG ghi vào skeleton template
- Gen Video → trigger render `hyperframes` từ workspace luôn

**Rule editor:**
- 1 instance/port → phải kill process port 5050 trước khi reload code Python mới
- Save mode: editor ghi CSS thẳng vào `index.html` workspace. Muốn lưu thành **default template** → patch `skeleton.html` riêng (em viết script diff CSS rules)
- `compose.py` KHÔNG inject tokens override (fix 2026-06-16) → skeleton.html = single source of truth → editor preview KHỚP MP4 render

**Trouble:**
- Editor không thấy thay đổi code Python → kill port 5050 (`Get-NetTCPConnection -LocalPort 5050 -State Listen | Stop-Process`) → reload VBS
- Port 5050 đang in use lúc start → editor cũ chưa close — kill trước khi mở mới

---

## 📚 Template chọn theo topic

| Topic loại | Template suggest | Lý do |
|---|---|---|
| Parenting / giáo dục con / sức khỏe gia đình | `01_Text` | Tone ấm, 6 scene fix, layout chữ to |
| Review tool / sản phẩm tech / privacy / security | `02_TechCold` | Tone lạnh, có mockup device, 4-card features |
| Stats / data heavy | `02_TechCold` | Có stat-block + card-grid-4 |
| Story narrative ≥120s | (TODO) `03_Narrative` | Chưa build, fallback `01_Text` |

## 📁 Sample Scripts

- `references/sample_script_trẻ_em_tăng_động.md` — example 60‑second script for the topic “Trẻ em tăng động”. Nội dung gồm 6 scene, ngắn gọn, tone ấm, phù hợp với template `01_Text`.


| Topic loại | Template suggest | Lý do |
|---|---|---|
| Parenting / giáo dục con / sức khỏe gia đình | `01_Text` | Tone ấm, 6 scene fix, layout chữ to |
| Review tool / sản phẩm tech / privacy / security | `02_TechCold` | Tone lạnh, có mockup device, 4-card features |
| Stats / data heavy | `02_TechCold` | Có stat-block + card-grid-4 |
| Story narrative ≥120s | (TODO) `03_Narrative` | Chưa build, fallback `01_Text` |

---

## 🎯 Roadmap mở rộng

**User preference**: see `references/user_preference.md` for Boss's request to always compare this skill with Claude's approach.

- `03_Narrative` — story telling dài >120s
- `04_Quote_Inspire` — single quote big text
- `05_Tutorial_Step` — step-by-step có ảnh
