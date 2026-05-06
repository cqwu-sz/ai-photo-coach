"""Generate placeholder PNG thumbnails for the v0 pose library.

These are simple coloured tiles with the pose ID and a stick-figure-ish
silhouette so the iOS UI has something to display before we get real photos
of the poses. Each pose JSON has a `thumbnail` field pointing at one of
these PNGs.

Usage:
    python scripts/generate_pose_thumbnails.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
POSES_DIR = ROOT / "backend" / "app" / "knowledge" / "poses"
OUT_DIR = POSES_DIR  # write thumbnails alongside the JSON files


_LAYOUT_COLORS = {
    "single": (220, 200, 180),
    "side_by_side": (180, 210, 220),
    "high_low_offset": (220, 180, 200),
    "triangle": (200, 220, 180),
    "diagonal": (200, 200, 230),
    "cluster": (230, 210, 180),
    "v_formation": (180, 220, 210),
    "line": (210, 200, 220),
    "circle": (200, 210, 200),
    "custom": (210, 210, 210),
}


def _draw_silhouettes(draw: ImageDraw.ImageDraw, layout: str, count: int, size=(512, 512)):
    w, h = size
    cx, cy = w // 2, h // 2 + 30

    head_r = 28
    body_h = 110

    def figure(x: int, y: int, color=(80, 80, 80)):
        draw.ellipse((x - head_r, y - head_r, x + head_r, y + head_r), fill=color)
        draw.rectangle((x - 18, y + head_r, x + 18, y + head_r + body_h), fill=color)
        draw.line((x - 30, y + head_r + 30, x - 70, y + head_r + 80), fill=color, width=12)
        draw.line((x + 30, y + head_r + 30, x + 70, y + head_r + 80), fill=color, width=12)
        draw.line((x - 12, y + head_r + body_h, x - 18, y + head_r + body_h + 90), fill=color, width=14)
        draw.line((x + 12, y + head_r + body_h, x + 18, y + head_r + body_h + 90), fill=color, width=14)

    if layout == "single" or count == 1:
        figure(cx, cy - 30)
    elif layout in ("side_by_side", "line"):
        spacing = 110
        start = cx - (count - 1) * spacing // 2
        for i in range(count):
            figure(start + i * spacing, cy - 30)
    elif layout == "high_low_offset":
        figure(cx - 60, cy - 60)
        figure(cx + 60, cy + 30)
    elif layout == "triangle":
        figure(cx, cy - 80)
        figure(cx - 90, cy + 10)
        figure(cx + 90, cy + 10)
    elif layout == "diagonal":
        for i in range(count):
            figure(cx - 100 + i * 80, cy - 60 + i * 30)
    elif layout == "v_formation":
        figure(cx, cy - 80)
        figure(cx - 70, cy - 30)
        figure(cx + 70, cy - 30)
        figure(cx - 130, cy + 30)
        figure(cx + 130, cy + 30)
    else:  # cluster, circle, custom
        offsets = [(-90, -20), (-30, -50), (30, -40), (90, -10), (0, 30)]
        for dx, dy in offsets[:count]:
            figure(cx + dx, cy + dy)


def render(pose: dict) -> Path:
    layout = pose["layout"]
    count = pose["person_count"]
    name = pose["thumbnail"]
    out_path = OUT_DIR / name

    bg = _LAYOUT_COLORS.get(layout, (220, 220, 220))
    img = Image.new("RGB", (512, 512), color=bg)
    draw = ImageDraw.Draw(img)

    draw.rectangle((20, 20, 492, 492), outline=(60, 60, 60), width=4)

    _draw_silhouettes(draw, layout, count)

    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    draw.text((30, 30), pose["id"], fill=(40, 40, 40), font=font)
    draw.text(
        (30, 460),
        f"{layout}  /  {count}p",
        fill=(40, 40, 40),
        font=font,
    )

    img.save(out_path, "PNG", optimize=True)
    return out_path


def main() -> None:
    json_files = sorted(POSES_DIR.glob("pose_*.json"))
    if not json_files:
        print("No pose JSON files found")
        return
    for jf in json_files:
        with jf.open("r", encoding="utf-8") as f:
            pose = json.load(f)
        out = render(pose)
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
