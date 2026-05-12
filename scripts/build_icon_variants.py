"""
Derive the dark + tinted appearance variants for each direction master.

Inputs (square-cropped 1024x1024 light masters are produced inside this script):
  assets/icon-direction1.png  (light, default direction 1 — saturated late golden hour)
  assets/icon-direction2.png  (light, default direction 2 — light beam from above)

Outputs (one set per direction):
  assets/icon-direction{N}-light.png      — square 1024 light master (center-cropped)
  assets/icon-direction{N}-dark.png       — square 1024 dark variant
  assets/icon-direction{N}-tinted.png     — square 1024 tinted luminance mask

Why no manual color tweaks per direction?
  Both direction masters share the same visual language: deep wine-plum silhouettes
  against a warm-jewel-toned glow with a bright focal light source. The same
  luminance-driven re-color works for both. If a future direction needs bespoke
  treatment, override the per-direction palette in DIRECTION_PALETTES below.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

DIRECTIONS = ["direction1", "direction2"]

# Dark sky palette — applied per-direction so we can fine-tune later if a direction
# benefits from a different "night" mood. Currently both use the same twilight
# palette because both already share a moody warm-jewel aesthetic.
DIRECTION_PALETTES: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    "direction1": [
        (0.0, (8, 14, 30)),
        (0.35, (32, 22, 52)),
        (0.62, (96, 38, 70)),
        (0.78, (182, 92, 78)),
        (1.0, (212, 138, 92)),
    ],
    "direction2": [
        (0.0, (6, 10, 22)),
        (0.45, (28, 14, 46)),
        (0.75, (74, 24, 60)),
        (1.0, (140, 56, 60)),
    ],
}


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def gradient_color(t: float, stops: list[tuple[float, tuple[int, int, int]]]) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t0 <= t <= t1:
            return lerp(c0, c1, (t - t0) / max(1e-6, t1 - t0))
    return stops[-1][1]


def square_crop(src: Path, target: int = 1024) -> Image.Image:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    if w == h:
        return im if w == target else im.resize((target, target), Image.LANCZOS)
    side = min(w, h)
    left = (w - side) // 2 if w > h else 0
    top = (h - side) // 2 if h > w else 0
    return im.crop((left, top, left + side, top + side)).resize((target, target), Image.LANCZOS)


def make_dark(light: Image.Image, sky_stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    w, h = light.size
    out = Image.new("RGB", (w, h))
    src = light.load()
    dst = out.load()
    silhouette_color = (62, 50, 56)
    sun_color = (255, 230, 170)

    for y in range(h):
        v = y / (h - 1)
        for x in range(w):
            r, g, b = src[x, y]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum < 100:
                t = lum / 100
                warm = (r - b) / 255
                base = silhouette_color
                rim = (95, 65, 60)
                c = lerp(base, rim, max(0.0, min(1.0, warm * 2.5)))
                c = lerp((35, 25, 32), c, t)
                dst[x, y] = c
            elif lum > 230:
                dst[x, y] = sun_color
            else:
                c = gradient_color(v, sky_stops)
                mix = 0.10 * ((lum - 100) / 130)
                c = (
                    int(c[0] * (1 - mix) + r * mix),
                    int(c[1] * (1 - mix) + g * mix),
                    int(c[2] * (1 - mix) + b * mix),
                )
                dst[x, y] = c
    return out


def fill_dark_holes(mask: Image.Image, threshold: int = 80, radius: int = 6) -> Image.Image:
    """Fill small dark blobs that are surrounded by bright pixels.

    The tinted mask treats the camera-lens center as "mid-luminance" which
    drops it to near-black. At small icon sizes that black dot reads as
    a manufacturing speck on an otherwise tinted silhouette, so we patch
    isolated dark blobs whose `radius`-neighbourhood is mostly bright.
    Implemented with PIL's MaxFilter (bright-dilation), which is O(width *
    height * radius) but trivially fast for a 1024² icon.
    """
    from PIL import ImageFilter

    # Dilate brightness by `radius` so bright regions swallow nearby holes.
    dilated = mask.filter(ImageFilter.MaxFilter(size=radius * 2 + 1))
    src = mask.load()
    dil = dilated.load()
    out = mask.copy()
    dst = out.load()
    w, h = mask.size
    for y in range(h):
        for x in range(w):
            sr = src[x, y][0]
            if sr < threshold and dil[x, y][0] > 200:
                # Dark pixel surrounded by bright neighbours → fill in.
                v = dil[x, y][0]
                dst[x, y] = (v, v, v)
    return out


def make_tinted(light: Image.Image) -> Image.Image:
    """Tinted mask: silhouettes → bright (gets tinted), sky → dark, sun/lens → brightest."""
    w, h = light.size
    out = Image.new("RGB", (w, h), (0, 0, 0))
    src = light.load()
    dst = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = src[x, y]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            # Tinted policy uses a V-shaped luminance curve:
            #   - very dark pixels (deep silhouettes) → bright (tint paints them)
            #   - very bright pixels (sun, lens hotspot) → bright (focal accents)
            #   - mid-luminance pixels (sky, glow, gradients) → dark
            # This guarantees the figure + camera read as a tinted silhouette
            # against a near-black background, while preserving the bright sun
            # / lens highlights as crisp tinted accents.
            if lum < 70:
                # Deep silhouette → near white
                v = 240
            elif lum < 110:
                # Silhouette edge → blend out
                t = (lum - 70) / 40
                v = int(240 - t * 180)  # 240 → 60
            elif lum > 235:
                # Focal hotspot (sun, lens glow, beam core) → white
                v = 255
            elif lum > 200:
                # Bright halo around hotspot → bright
                t = (lum - 200) / 35
                v = int(60 + t * 195)  # 60 → 255
            else:
                # Sky / mid-tones → very dark
                v = 25
            dst[x, y] = (v, v, v)
    return out


def main() -> None:
    for name in DIRECTIONS:
        raw = ASSETS / f"icon-{name}.png"
        if not raw.exists():
            print(f"skip {name}: source not found ({raw})")
            continue
        light = square_crop(raw)
        light_out = ASSETS / f"icon-{name}-light.png"
        dark_out = ASSETS / f"icon-{name}-dark.png"
        tinted_out = ASSETS / f"icon-{name}-tinted.png"
        light.save(light_out, "PNG")
        print("wrote", light_out)
        make_dark(light, DIRECTION_PALETTES[name]).save(dark_out, "PNG")
        print("wrote", dark_out)
        tinted = make_tinted(light)
        tinted = fill_dark_holes(tinted)
        tinted.save(tinted_out, "PNG")
        print("wrote", tinted_out)


if __name__ == "__main__":
    main()
