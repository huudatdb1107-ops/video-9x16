"""gen_assets.py — gen ảnh PNG (device-mockup, illustration) qua PIC_Br skill.

Workflow:
  1. Đọc content.json → liệt kê asset paths cần (vd assets/devices-ente.png)
  2. Check existing assets
  3. Cho mỗi missing asset:
     - Lấy prompt từ content.json["asset_prompts"][filename] (nếu có)
     - Hoặc auto-gen prompt từ context (LLM)
     - Gọi PIC_Br CLI → save PNG vào assets/

P4 — MVP version:
  - Mode CHECK: chỉ check assets có sẵn không, in danh sách missing
  - Mode AUTO (TODO): gọi PIC_Br thực tế

Usage:
  python gen_assets.py --content my_video/content.json --mode check
  python gen_assets.py --content my_video/content.json --mode auto
"""
import sys, json, argparse, pathlib, re
sys.stdout.reconfigure(encoding="utf-8")

ROOT  = pathlib.Path(r"E:\HuuDat\BrianD\TOOL_BrianD")
PIC_BR = ROOT / "B-Go" / "3_PIC_Br"

def extract_asset_refs(content: dict) -> list:
    """Tìm mọi field có giá trị ending .png, .jpg, .webp trong content."""
    refs = []
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}.{i}")
        elif isinstance(obj, str):
            if re.search(r"\.(png|jpg|webp)$", obj, re.IGNORECASE):
                refs.append({"path": path, "value": obj})
    walk(content)
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True, help="Path to content.json")
    ap.add_argument("--mode", choices=["check", "auto"], default="check")
    args = ap.parse_args()

    content_file = pathlib.Path(args.content).resolve()
    content = json.loads(content_file.read_text(encoding="utf-8"))
    assets_dir = content_file.parent / "assets"
    assets_dir.mkdir(exist_ok=True)

    refs = extract_asset_refs(content)
    print(f"Found {len(refs)} asset references in content.json:")
    missing = []
    for r in refs:
        target = assets_dir / pathlib.Path(r["value"]).name
        exists = target.exists()
        mark = "✓" if exists else "✗"
        print(f"  {mark} {r['path']} → {r['value']}  ({'exists' if exists else 'MISSING'})")
        if not exists:
            missing.append({**r, "target": target})

    if not missing:
        print(f"\n✓ All assets present in {assets_dir}")
        return

    print(f"\n⚠ {len(missing)} asset missing")

    if args.mode == "check":
        print("\nMode CHECK — chỉ liệt kê. Boss tự gen ảnh + copy vào assets/")
        print("Gợi ý PIC_Br prompts để gen:")
        prompts = content.get("asset_prompts", {})
        for m in missing:
            name = pathlib.Path(m["value"]).name
            suggestion = prompts.get(name, f"<chưa có prompt — Boss tự gen ảnh '{name}'>")
            print(f"  • {name}: {suggestion}")
        return

    if args.mode == "auto":
        print("\nMode AUTO — TODO: integrate PIC_Br CLI call.")
        print("Tạm thời em chưa đủ context PIC_Br bridge spec. Boss cần:")
        print("  1. Cung cấp prompt cho mỗi asset trong content.json['asset_prompts']")
        print("  2. Hoặc gen manual qua /PIC skill rồi copy ảnh vào assets/")
        print("\nĐể em bổ sung sau khi Boss đưa spec của PIC_Br bridge_pic.py.")


if __name__ == "__main__":
    main()
