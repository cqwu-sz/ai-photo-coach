"""Bake the PWA icon PNGs from a procedural design (aperture motif).

Run from repo root:

    python scripts/generate_pwa_icons.py

Outputs:
    web/img/icon-192.png      ~ Android Chrome install
    web/img/icon-512.png      ~ Android Chrome splash / large
    web/img/icon-maskable.png ~ Android adaptive icon (safe zone 80%)
    web/img/icon-180.png      ~ iOS apple-touch-icon

Why a script instead of static PNG assets:
    * Reproducible — if the brand palette shifts we re-bake instead of
      hand-editing 4 PNGs in Photoshop.
    * No image binaries in git diffs.
    * Pillow is already in the backend's transitive deps, so the
      script runs without adding new dependencies.

Design mirrors ``web/img/icon.svg``:
    * rounded-square dark background, warm-cool diagonal gradient,
    * radial glow top-left,
    * 6 aperture blades stroked in accent gold,
    * filled centre dot.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "img"

# Brand palette (matches welcome.css / shot CTAs).
BG_TOP = (26, 26, 46)        # #1a1a2e
BG_MID = (14, 16, 36)        # #0e1024
BG_BOT = (4, 5, 11)          # #04050b
ACCENT_WARM = (244, 184, 96) # #f4b860
ACCENT_HOT = (255, 122, 108) # #ff7a6c
INK = (245, 244, 238)


def _vertical_gradient(size: int) -> Image.Image:
    """3-stop diagonal-ish gradient via vertical interpolation."""
    grad = Image.new("RGB", (size, size), BG_TOP)
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        if t < 0.5:
            u = t / 0.5
            r = int(BG_TOP[0] + (BG_MID[0] - BG_TOP[0]) * u)
            g = int(BG_TOP[1] + (BG_MID[1] - BG_TOP[1]) * u)
            b = int(BG_TOP[2] + (BG_MID[2] - BG_TOP[2]) * u)
        else:
            u = (t - 0.5) / 0.5
            r = int(BG_MID[0] + (BG_BOT[0] - BG_MID[0]) * u)
            g = int(BG_MID[1] + (BG_BOT[1] - BG_MID[1]) * u)
            b = int(BG_BOT[2] + (BG_BOT[2] - BG_MID[2]) * u)
        for x in range(size):
            px[x, y] = (r, g, b)
    return grad


def _warm_glow(size: int) -> Image.Image:
    """Soft radial highlight, top-left, blended on top of the gradient."""
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx, cy = int(size * 0.32), int(size * 0.28)
    r_max = int(size * 0.65)
    px = glow.load()
    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            d = math.hypot(dx, dy)
            if d >= r_max:
                continue
            t = 1.0 - (d / r_max)
            # Two-stop warm glow → fade.
            if t > 0.45:
                u = (t - 0.45) / 0.55
                a = int(0.55 * 255 * u)
                px[x, y] = (*ACCENT_WARM, a)
            else:
                u = t / 0.45
                a = int(0.18 * 255 * u)
                px[x, y] = (*ACCENT_HOT, a)
    # Big blur to make it feel light, not pixelly.
    return glow.filter(ImageFilter.GaussianBlur(radius=size * 0.04))


def _rounded_mask(size: int, radius_ratio: float) -> Image.Image:
    """Alpha mask for the rounded-square shell."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return mask


def _draw_aperture(canvas: Image.Image, scale: float = 1.0):
    """Paint the 6-blade aperture motif onto ``canvas`` in-place.

    scale=1.0 fills the canvas as if it were a non-maskable icon
    (logo edge close to background edge); scale=0.7 leaves the
    Android maskable safe zone.
    """
    size = canvas.size[0]
    draw = ImageDraw.Draw(canvas, "RGBA")
    cx, cy = size / 2, size / 2
    R = (size * 0.35) * scale
    stroke = max(2, int(size * 0.027 * scale))

    # Outer ring (gradient simulated with two overlaid arcs).
    # Pillow doesn't do gradient strokes, so we approximate by stroking
    # in warm then nudging the right half with hot.
    bb = (cx - R, cy - R, cx + R, cy + R)
    draw.ellipse(bb, outline=ACCENT_WARM, width=stroke)
    # Right-half tint
    for ang in range(-90, 90, 2):
        x1 = cx + R * math.cos(math.radians(ang))
        y1 = cy + R * math.sin(math.radians(ang))
        x2 = cx + R * math.cos(math.radians(ang + 2))
        y2 = cy + R * math.sin(math.radians(ang + 2))
        draw.line((x1, y1, x2, y2), fill=ACCENT_HOT, width=stroke)

    # Blades — 6 short radial segments from inner ring to outer.
    inner = R * 0.25
    outer = R * 0.85
    for i in range(6):
        ang = math.radians(-60 + i * 60)
        x1 = cx + inner * math.cos(ang)
        y1 = cy + inner * math.sin(ang)
        x2 = cx + outer * math.cos(ang)
        y2 = cy + outer * math.sin(ang)
        draw.line((x1, y1, x2, y2), fill=(*ACCENT_WARM, 220), width=stroke)

    # Centre dot.
    dot_r = R * 0.20
    draw.ellipse(
        (cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r),
        fill=ACCENT_WARM,
    )


def render_icon(size: int, *, maskable: bool = False) -> Image.Image:
    """Render a single icon at ``size`` px square."""
    # 2x supersample for AA.
    work_size = size * 2
    bg = _vertical_gradient(work_size).convert("RGBA")
    glow = _warm_glow(work_size)
    bg.alpha_composite(glow)

    # Maskable icons must keep the logo inside the inner 80% safe zone;
    # Android can crop the outer 10% as a circle/squircle. Non-maskable
    # icons get full-bleed.
    _draw_aperture(bg, scale=0.62 if maskable else 1.0)

    if not maskable:
        # Round the corners so the icon looks correct on iOS / desktop
        # PWA without the OS having to clip it.
        mask = _rounded_mask(work_size, radius_ratio=0.22)
        bg.putalpha(mask)

    return bg.resize((size, size), Image.LANCZOS)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-180.png", 180, False),
        ("icon-maskable.png", 512, True),
    ]
    for name, size, maskable in targets:
        img = render_icon(size, maskable=maskable)
        out = OUT_DIR / name
        img.save(out, "PNG", optimize=True)
        print(f"  wrote {out.relative_to(OUT_DIR.parent.parent)}  ({size}x{size}, maskable={maskable})")


if __name__ == "__main__":
    main()
