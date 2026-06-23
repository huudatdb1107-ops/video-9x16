"""font_combos.py — AI tùy biến font combo cho mỗi video.

Mỗi combo = 1 set font phối hợp theo nguyên tắc thẩm mỹ.
compose.py random pick 1 combo (seeded theo topic) + inject @font-face base64 + CSS override.
"""

# Mỗi combo: dict { selector: (font_family_css, file_in_fonts_dir) }
# Selectors target:
#   .s1-title  — title scene 1 (chữ to)
#   .s2-big    — big text scene 2 ("KHÁC BIỆT")
#   .s3-heading, .s5-heading — heading scene 3/5
#   .s4-quote  — quote scene 4
#   .s6-title  — title CTA
#   body       — body default (mọi text khác)

FONT_COMBOS = {
    "warm-playful": {
        "label": "Warm Playful — ấm áp, vui nhộn (parenting/family)",
        "selectors": {
            ".s1-title":   ("LilitaOne_Vi",    "LilitaOne-Regular.ttf"),
            ".s2-big":     ("LilitaOne_Vi",    "LilitaOne-Regular.ttf"),
            ".s3-heading": ("Nunito_Vi",       "Nunito-Variable.ttf"),
            ".s5-heading": ("Nunito_Vi",       "Nunito-Variable.ttf"),
            ".s4-quote":   ("PlayfairDisplay_Vi", "PlayfairDisplay-Variable.ttf"),
            ".s6-title":   ("LilitaOne_Vi",    "LilitaOne-Regular.ttf"),
        },
    },
    "bold-impact": {
        "label": "Bold Impact — mạnh mẽ, ấn tượng (news/announcement)",
        "selectors": {
            ".s1-title":   ("Anton_Vi",        "Anton-Regular.ttf"),
            ".s2-big":     ("Anton_Vi",        "Anton-Regular.ttf"),
            ".s3-heading": ("Anton_Vi",        "Anton-Regular.ttf"),
            ".s5-heading": ("Anton_Vi",        "Anton-Regular.ttf"),
            ".s4-quote":   ("CrimsonPro_Vi",   "CrimsonPro-Variable.ttf"),
            ".s6-title":   ("Anton_Vi",        "Anton-Regular.ttf"),
        },
    },
    "modern-clean": {
        "label": "Modern Clean — hiện đại, sạch sẽ (tech/business)",
        "selectors": {
            ".s2-big":     ("Anton_Vi",        "Anton-Regular.ttf"),
            ".s4-quote":   ("PlayfairDisplay_Vi", "PlayfairDisplay-Variable.ttf"),
        },
    },
    "kid-friendly": {
        "label": "Kid Friendly — dễ thương, thân thiện trẻ em",
        "selectors": {
            ".s1-title":   ("LilitaOne_Vi",    "LilitaOne-Regular.ttf"),
            ".s2-big":     ("LilitaOne_Vi",    "LilitaOne-Regular.ttf"),
            ".s3-heading": ("Nunito_Vi",       "Nunito-Variable.ttf"),
            ".s5-heading": ("Nunito_Vi",       "Nunito-Variable.ttf"),
            ".s4-quote":   ("Inter",           None),  # giữ Inter gốc
            ".s6-title":   ("LilitaOne_Vi",    "LilitaOne-Regular.ttf"),
        },
    },
    "elegant-serif": {
        "label": "Elegant Serif — sang trọng, cổ điển (story/quote-heavy)",
        "selectors": {
            ".s1-title":   ("PlayfairDisplay_Vi", "PlayfairDisplay-Variable.ttf"),
            ".s2-big":     ("Anton_Vi",        "Anton-Regular.ttf"),
            ".s3-heading": ("PlayfairDisplay_Vi", "PlayfairDisplay-Variable.ttf"),
            ".s5-heading": ("PlayfairDisplay_Vi", "PlayfairDisplay-Variable.ttf"),
            ".s4-quote":   ("CrimsonPro_Vi",   "CrimsonPro-Variable.ttf"),
            ".s6-title":   ("PlayfairDisplay_Vi", "PlayfairDisplay-Variable.ttf"),
        },
    },
}


def pick_combo(seed_text: str = None):
    """Pick 1 combo. Nếu seed_text cung cấp → deterministic theo hash → cùng topic luôn cùng font.
       Nếu không → random hoàn toàn."""
    import random, hashlib
    keys = list(FONT_COMBOS.keys())
    if seed_text:
        h = int(hashlib.md5(seed_text.encode("utf-8")).hexdigest(), 16)
        idx = h % len(keys)
    else:
        idx = random.randrange(len(keys))
    name = keys[idx]
    return name, FONT_COMBOS[name]


def build_font_css(combo: dict, fonts_dir, workspace_dir=None) -> str:
    """Build CSS block: @font-face dùng Base64 Data-URI + selector override.
       Returns CSS string ready để inject vào <head>."""
    import pathlib, base64

    fonts_dir = pathlib.Path(fonts_dir)
    workspace_dir = pathlib.Path(workspace_dir) if workspace_dir else None

    needed_files = {}
    for sel, (family, file) in combo["selectors"].items():
        if file:
            needed_files[family] = file

    css_parts = []
    for family, file in needed_files.items():
        fp = fonts_dir / file
        if not fp.exists():
            print(f"  ⚠ Font {file} missing in {fonts_dir}")
            continue
        data = fp.read_bytes()
        if data[:4].hex() != "00010000":
            print(f"  ⚠ Font {file} invalid TTF header")
            continue
        # Vẫn copy font vào workspace để editor_server phục vụ file tĩnh nếu cần
        if workspace_dir:
            dst = workspace_dir / file
            if not dst.exists():
                dst.write_bytes(data)
        
        # Base64 encoding
        encoded = base64.b64encode(data).decode('utf-8')
        css_parts.append(
            f"@font-face {{ font-family: '{family}'; font-style: normal; "
            f"font-weight: 100 900; src: url('data:font/ttf;charset=utf-8;base64,{encoded}') format('truetype'); }}"
        )

    for sel, (family, _) in combo["selectors"].items():
        css_parts.append(f"{sel} {{ font-family: '{family}', 'Inter', sans-serif !important; }}")

    return "\n".join(css_parts)
