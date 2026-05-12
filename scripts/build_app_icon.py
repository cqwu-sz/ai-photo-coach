"""
Build the iOS AppIcon set + alternate icon set from the two direction masters.

Default app icon  → AppIcon.appiconset       sourced from icon-direction2-*.png
Alternate icon    → AppIcon-Sunset.appiconset sourced from icon-direction1-*.png

For the *default* icon set we ship full iOS 18+ appearance variants (light, dark,
tinted). For *alternate* icons, iOS does not support per-appearance variants via
the asset catalog at this time, so we only emit the light variant — switching the
icon at runtime via `UIApplication.setAlternateIconName` will display the light
version under all system appearances. (This matches Apple's own apps such as
Phone and Calendar that have alternate icons.)

Usage:
  python scripts/build_app_icon.py

Environment:
  DEFAULT_DIRECTION  — override which direction is the default (1 or 2). Default: 2
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
XCASSETS = ROOT / "ios" / "AIPhotoCoach" / "Resources" / "Assets.xcassets"

DEFAULT_DIRECTION = int(os.environ.get("DEFAULT_DIRECTION", "2"))
ALT_DIRECTION = 1 if DEFAULT_DIRECTION == 2 else 2

# (idiom, pt size, scale, base filename without -dark/-tinted suffix)
SLOTS = [
    ("iphone", 20, 2, "Icon-20@2x.png"),
    ("iphone", 20, 3, "Icon-20@3x.png"),
    ("iphone", 29, 2, "Icon-29@2x.png"),
    ("iphone", 29, 3, "Icon-29@3x.png"),
    ("iphone", 40, 2, "Icon-40@2x.png"),
    ("iphone", 40, 3, "Icon-40@3x.png"),
    ("iphone", 60, 2, "Icon-60@2x.png"),
    ("iphone", 60, 3, "Icon-60@3x.png"),
    ("ios-marketing", 1024, 1, "AppIcon-1024.png"),
]


def load_square(src: Path, target: int = 1024) -> Image.Image:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    if w == h:
        return im if w == target else im.resize((target, target), Image.LANCZOS)
    side = min(w, h)
    left = (w - side) // 2 if w > h else 0
    top = (h - side) // 2 if h > w else 0
    return im.crop((left, top, left + side, top + side)).resize((target, target), Image.LANCZOS)


def filename_with_suffix(base: str, suffix: str) -> str:
    if not suffix:
        return base
    stem, ext = base.rsplit(".", 1)
    return f"{stem}{suffix}.{ext}"


def build_iconset(out_dir: Path, masters: dict[str, Image.Image], include_appearances: bool) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Wipe any stale PNGs from a previous build so renamed slots don't linger.
    for old in out_dir.glob("*.png"):
        old.unlink()

    appearances_spec = [("", None)]
    if include_appearances:
        appearances_spec += [
            ("-dark", [{"appearance": "luminosity", "value": "dark"}]),
            ("-tinted", [{"appearance": "luminosity", "value": "tinted"}]),
        ]

    images: list[dict] = []
    for idiom, pt, scale, base_fname in SLOTS:
        px = pt * scale
        for suffix, appearances in appearances_spec:
            master = masters[suffix or "light"]
            fname = filename_with_suffix(base_fname, suffix)
            if fname.startswith("AppIcon-1024"):
                im = master
            else:
                im = master.resize((px, px), Image.LANCZOS)
            im.save(out_dir / fname, "PNG")
            entry: dict = {
                "idiom": idiom,
                "size": f"{pt}x{pt}",
                "scale": f"{scale}x",
                "filename": fname,
            }
            if appearances:
                entry["appearances"] = appearances
            images.append(entry)

    contents = {"images": images, "info": {"version": 1, "author": "xcode"}}
    (out_dir / "Contents.json").write_text(json.dumps(contents, indent=2))
    return len(images)


def load_direction_masters(direction: int, *, with_variants: bool) -> dict[str, Image.Image]:
    light_path = ASSETS / f"icon-direction{direction}-light.png"
    if not light_path.exists():
        raise SystemExit(
            f"Light master for direction {direction} not found: {light_path}\n"
            "Run scripts/build_icon_variants.py first."
        )
    masters: dict[str, Image.Image] = {"light": load_square(light_path)}
    if with_variants:
        dark_path = ASSETS / f"icon-direction{direction}-dark.png"
        tinted_path = ASSETS / f"icon-direction{direction}-tinted.png"
        for p in (dark_path, tinted_path):
            if not p.exists():
                raise SystemExit(f"Variant master not found: {p}")
        masters["-dark"] = load_square(dark_path)
        masters["-tinted"] = load_square(tinted_path)
    return masters


def main() -> None:
    print(f"Default direction = {DEFAULT_DIRECTION}, alternate direction = {ALT_DIRECTION}")

    default_masters = load_direction_masters(DEFAULT_DIRECTION, with_variants=True)
    default_set = XCASSETS / "AppIcon.appiconset"
    n_default = build_iconset(default_set, default_masters, include_appearances=True)
    print(f"  wrote {n_default} images → {default_set.relative_to(ROOT)}")

    alt_label = "Sunset" if ALT_DIRECTION == 1 else "Beam"
    alt_masters = load_direction_masters(ALT_DIRECTION, with_variants=False)
    alt_set = XCASSETS / f"AppIcon-{alt_label}.appiconset"
    n_alt = build_iconset(alt_set, alt_masters, include_appearances=False)
    print(f"  wrote {n_alt} images → {alt_set.relative_to(ROOT)}")

    (XCASSETS / "Contents.json").write_text(
        json.dumps({"info": {"version": 1, "author": "xcode"}}, indent=2)
    )
    print("Done.")


if __name__ == "__main__":
    main()
