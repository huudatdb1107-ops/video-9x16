# Pipeline diagram — VIDEO-9X16-Br

## Luồng đầy đủ

```
┌──────────────────────────────────────────────────────────────┐
│  INPUT từ Boss                                               │
│  • topic: "Trẻ em hiếu động vs tăng động"                    │
│  • script-file: kịch bản .txt (~280-320 từ Việt)             │
│  • voice (optional): "TT_06" (default)                        │
│  • output-dir: workspace cho video mới                        │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 1 — match_template.py                                  │
│  Heuristic keyword matching:                                  │
│  • Topic warm (con cái/giáo dục) → 01_Text                   │
│  • Topic cold (tech/AI/privacy) → 02_TechCold                 │
│  Output: <template_name>                                      │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 2 — fill_content.py (LLM via agy_wrapper)              │
│  Prompt LLM:                                                  │
│  • Read template's content.schema.json                        │
│  • Parse script → fill scenes theo schema                     │
│  • Tô màu keyword bằng <em class="hl-*">                      │
│  • Tránh số Ả-Rập ≥2, acronym ALL-CAPS                        │
│  Output: <output_dir>/content.json                            │
│  Fallback nếu LLM fail: dump prompt cho Boss fill manual      │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 3 — gen_voice.py (OMNI bridge)                         │
│  bridge_omni.py với utf-8 stdin (đã fix)                      │
│  TTS audit: check acronym/số/câu dài KHÔNG comma              │
│  Output: <output_dir>/narration.wav                           │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 4 — transcribe (faster-whisper base int8)              │
│  Output: <output_dir>/transcript.json (duration + segments)   │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 5 — update_timeline                                    │
│  Chia voice duration đều cho N scenes                         │
│  Update content.json["timeline"] + content.json["voice"]      │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 6 — compose (template/compose.py)                      │
│  skeleton.html + style-tokens.json + content.json             │
│  → index.html render-ready (placeholders replaced, CSS inj.)  │
└──────────────────────────────────────────────────────────────┘
                          ↓ (optional check assets)
┌──────────────────────────────────────────────────────────────┐
│  STEP 6.5 — gen_assets.py (chỉ template có ảnh: 02_TechCold) │
│  Check assets/ có đủ PNG không                                │
│  Mode CHECK: liệt kê missing + gợi ý PIC_Br prompt            │
│  Mode AUTO (TODO): gọi PIC_Br skill auto-gen                  │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 7 — render mp4 (hyperframes)                           │
│  npx hyperframes render . --quality draft --fps 30            │
│  Output: <output_dir>/out_<timestamp>.mp4                     │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  STEP 8 — DONE 🎬                                            │
│  Boss có:                                                     │
│  • out_<ts>.mp4 (final video)                                 │
│  • index.html (render-ready, có thể tinh chỉnh + render lại)  │
│  • content.json (text + timeline — edit để re-render nhanh)   │
│  • narration.wav (voice)                                       │
│  • transcript.json (audit trail)                              │
└──────────────────────────────────────────────────────────────┘
```

## Tinh chỉnh sau pipeline

Sau khi pipeline xong, Boss có thể:

1. **Edit text qua editor** (đã build ở session trước):
   - Chạy `editor_server.py` trong `<output_dir>/` (nếu đã copy)
   - Click chữ inline để sửa, đổi màu/font/line-height
   - Save → Gen Video lại

2. **Edit content.json trực tiếp**:
   - Mở file bằng text editor
   - Sửa text scene → chạy lại `template/compose.py <output_dir>` → render lại

3. **Đổi template**:
   - Copy `content.json` sang template khác (vd 01_Text → 02_TechCold)
   - Adjust fields theo schema mới
   - Compose + render

## Fallback graceful

| Step | Fail mode | Fallback |
|---|---|---|
| 1 Match | Không có template phù hợp | Default `01_Text` |
| 2 LLM fill | Antigravity unavailable | Dump prompt → Boss tự fill |
| 3 Voice | OMNI bridge fail | Boss tự đưa narration.wav vào |
| 4 Transcribe | Whisper fail | Bỏ step 5, dùng timeline mặc định trong content.json |
| 6 Compose | Schema mismatch | Print missing fields, dừng |
| 7 Render | hyperframes fail | Giữ index.html, Boss tự `npx hyperframes render` |
