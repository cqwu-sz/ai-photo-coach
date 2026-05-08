"""Convert each preset's Tripo `_rendered.webp` preview into the
256x256 face-style PNG thumbnail used by the avatar gallery.

The Tripo SDK's `download_task_models` writes one `*_rendered.webp` per
task into `scripts/_tripo_logs/<preset_id>/`. We pick the newest one per
folder, center-crop to a square, resize to 256x256, save as PNG, and
overwrite `web/avatars/preset/<preset_id>.png`. The original procedural
PNG is preserved alongside the glb backup as `<preset_id>.png.placeholder`.

Run after `gen_avatars_tripo.py`:
    python scripts/refresh_avatar_thumbnails.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "scripts" / "_tripo_logs"
PRESET_DIR = REPO_ROOT / "web" / "avatars" / "preset"

PRESET_IDS = [
    "male_casual_25",
    "male_business_35",
    "male_athletic_28",
    "female_casual_22",
    "female_elegant_30",
    "female_artsy_25",
    "child_boy_8",
    "child_girl_8",
]


def newest_render(preset_id: str) -> Path | None:
    d = LOG_DIR / preset_id
    if not d.exists():
        return None
    cands = sorted(d.glob("*_rendered.webp"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return cands[0] if cands else None


def to_thumb(src: Path, dst: Path, size: int = 256) -> None:
    img = Image.open(src).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((size, size), Image.LANCZOS)
    bg = Image.new("RGB", (size, size), (245, 245, 247))
    bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    bg.save(dst, format="PNG", optimize=True)


def main() -> int:
    fail = 0
    for pid in PRESET_IDS:
        src = newest_render(pid)
        if not src:
            print(f"[thumb] {pid:<22}  no rendered.webp — skipped")
            fail += 1
            continue
        dst = PRESET_DIR / f"{pid}.png"
        backup = PRESET_DIR / f"{pid}.png.placeholder"
        if dst.exists() and not backup.exists():
            shutil.copy2(dst, backup)
        to_thumb(src, dst)
        print(f"[thumb] {pid:<22}  OK  ({src.name} -> {dst.name})")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
