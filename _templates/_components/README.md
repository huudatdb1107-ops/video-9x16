# Component Library — Video 9:16

Khối lego dùng chung cho tất cả template. Mỗi file = 1 component self-contained (HTML + CSS + GSAP hint).

## 10 components hiện có

| # | File | Khi dùng | Props chính |
|---|---|---|---|
| 1 | `eyebrow-tag.html` | Label nhỏ uppercase trên đầu scene title | text, color (cyan/teal/orange) |
| 2 | `title-multicolor.html` | Heading lớn 2-4 dòng, có thể tô màu keyword bằng `<em class="hl-*">` | size (hero/big/mid), highlights |
| 3 | `card-grid-2.html` | 2 card ngang để compare/list 2 ý | cards × 2 |
| 4 | `card-grid-3-vertical.html` | 3 card xếp dọc có số 01/02/03 | items × 3 |
| 5 | `card-grid-4.html` | 4 card thu nhỏ ngang (vd Face/Search/Share/Upload) | cards × 4 |
| 6 | `bullet-list-colored.html` | List 3-5 bullets có chấm tròn màu khác nhau | items × N |
| 7 | `stat-block.html` | Số/text BIG + label uppercase + body (vd "10GB / FREE STORAGE") | number, label, body |
| 8 | `device-mockup.html` | Ảnh device composite (phone+laptop+tablet) | src, caption, sublabel |
| 9 | `illustration.html` | 3D character / mascot / icon-illustration | src, caption, sublabel |
| 10 | `quote-block.html` | Trích dẫn ngắn 1-2 câu có mark ✦ | text, mark, color |

## 2 component chrome (frame video)

| File | Khi dùng |
|---|---|
| `background-tech.html` | Layer nền navy gradient + grid lines + spotlight (style cold tech) |
| `frame-chrome.html` | 4 góc brackets viewfinder + footer label (chrome bao quanh scene) |

## Cách lắp ráp 1 scene

```html
<div class="scene scene-N" id="sN">
  <!-- Optional: background tech layer -->
  <!-- (chỉ cần 1 lần ở root, không lặp mỗi scene) -->

  <!-- Eyebrow -->
  <div class="hf-eyebrow" data-color="cyan">PRIVATE GOOGLE PHOTOS?</div>

  <!-- Title -->
  <h1 class="hf-title" data-size="hero">
    Muốn kiểu <em class="hl-cyan">Google Photos</em> nhưng
    ảnh của bạn vẫn <em class="hl-teal">mã hóa đầu cuối</em>?
  </h1>

  <!-- Body block (chọn 1): card-grid-2 / card-grid-4 / bullet-list / stat-block / device-mockup / illustration / quote-block -->
  <div class="hf-card-grid hf-grid-2">
    ...
  </div>
</div>
```

## Quy tắc Vietnamese

Tất cả component đã apply:
- `line-height` ≥ 1.5 cho body, ≥ 1.55 cho title (diacritics fit)
- Không có acronym ALL-CAPS Latin trong title
- Không số Ả-Rập ≥ 2 chữ số (dùng "ba mươi sáu" thay "36")

## Tokens

Mỗi template (vd `01_Text/`) có `style-tokens.json` override màu/size/spacing cho components. Component dùng giá trị mặc định nếu không override.

## Next: P1

P1 = Refactor `01_Text` template để dùng các components này. Skeleton hiện tại có CSS inline + content fixed → tách CSS ra tokens, content ra placeholder.
