"""Panorama generation from environment keyframes.

Why not OpenCV stitcher? Because the user's "rotate in place" capture
gives essentially zero parallax between frames, which is the textbook
worst case for feature-based stitching — the matcher fails on plain
sky / out-of-focus background, and even when it succeeds it produces
huge translation drift.

So we use a much simpler and *always-works* approach: an azimuth-based
equirectangular projection. Each frame already has a known azimuth
captured by the gyroscope on the client; we just paste each frame into
the equirectangular canvas at the corresponding longitude with a
horizontal FOV cover and a soft alpha falloff so neighbours blend
smoothly. The result isn't a metric reconstruction, but it gives a
fully usable 360° backdrop for the Three.js panorama sphere — which is
all the UI needs.

Output: JPEG bytes, 2048×1024 equirectangular by default.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

from ..models import FrameMeta


@dataclass(frozen=True)
class PanoramaConfig:
    width: int = 2048
    height: int = 1024
    horizontal_fov_deg: float = 75.0
    vertical_coverage: float = 0.65
    edge_falloff_frac: float = 0.12  # fraction of frame width on each side that fades


def make_panorama(
    frames: list[bytes],
    frame_meta: list[FrameMeta],
    cfg: PanoramaConfig | None = None,
) -> bytes:
    """Build an equirectangular panorama JPG from a list of frame
    bytes + their azimuths."""
    cfg = cfg or PanoramaConfig()

    if not frames:
        raise ValueError("no frames")
    if len(frames) != len(frame_meta):
        raise ValueError("frames/meta length mismatch")

    pano = Image.new("RGB", (cfg.width, cfg.height), (10, 12, 18))

    for raw, meta in zip(frames, frame_meta):
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:  # noqa: BLE001 — never fail panorama on a single bad frame
            continue
        _paste_frame(pano, img, meta.azimuth_deg, cfg)

    # Sky / ground fade so the top/bottom edges aren't hard cuts when
    # a frame doesn't cover the full vertical band.
    _vertical_vignette(pano, cfg)

    out = io.BytesIO()
    pano.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue()


def _paste_frame(
    pano: Image.Image,
    frame: Image.Image,
    azimuth_deg: float,
    cfg: PanoramaConfig,
) -> None:
    # Map [0..360) → [0..width). 0° is center of pano (longitude 0).
    az = ((azimuth_deg % 360) + 360) % 360
    center_x = (az / 360.0) * cfg.width

    # Each frame covers `horizontal_fov_deg` of longitude.
    fov_px = (cfg.horizontal_fov_deg / 360.0) * cfg.width
    target_w = max(64, int(fov_px))
    aspect = frame.height / max(1, frame.width)
    target_h = int(target_w * aspect)
    if target_h > cfg.height * cfg.vertical_coverage:
        target_h = int(cfg.height * cfg.vertical_coverage)
        target_w = int(target_h / aspect)

    thumb = frame.resize((target_w, target_h), Image.LANCZOS)
    mask = _falloff_mask(target_w, target_h, cfg.edge_falloff_frac)

    x0 = int(round(center_x - target_w / 2))
    y0 = int((cfg.height - target_h) / 2)

    # Paste with horizontal wrap (so frames near 0° / 360° don't get clipped).
    for dx in (-cfg.width, 0, cfg.width):
        pano.paste(thumb, (x0 + dx, y0), mask)


def _falloff_mask(w: int, h: int, edge_frac: float) -> Image.Image:
    """An L-mode mask that's 255 in the middle and ramps to 0 at the
    horizontal edges, so neighbouring frames blend instead of butt-jointing."""
    mask = Image.new("L", (w, h), 255)
    edge = max(1, int(w * edge_frac))
    px = mask.load()
    for x in range(edge):
        a = int(255 * (x / edge))
        for y in range(h):
            px[x, y] = a
            px[w - 1 - x, y] = a
    # A subtle vertical falloff so frames don't show their hard top/bottom edges.
    v_edge = max(1, int(h * 0.08))
    for y in range(v_edge):
        b = int(255 * (y / v_edge))
        for x in range(w):
            cur = px[x, y]
            px[x, y] = min(cur, b)
            cur2 = px[x, h - 1 - y]
            px[x, h - 1 - y] = min(cur2, b)
    # Slight blur to smooth the falloff edges.
    return mask.filter(ImageFilter.GaussianBlur(radius=2))


def _vertical_vignette(pano: Image.Image, cfg: PanoramaConfig) -> None:
    """Soft sky/ground gradient so the unfilled bands above/below the
    paste zone don't look like a sharp letterbox."""
    overlay = Image.new("RGB", (cfg.width, cfg.height))
    draw = ImageDraw.Draw(overlay)
    band = int(cfg.height * (1.0 - cfg.vertical_coverage) * 0.5)
    for y in range(band):
        a = 1.0 - (y / band)
        col = (int(40 * a + 10), int(50 * a + 12), int(70 * a + 18))
        draw.line([(0, y), (cfg.width, y)], fill=col, width=1)
        draw.line(
            [(0, cfg.height - 1 - y), (cfg.width, cfg.height - 1 - y)],
            fill=col,
            width=1,
        )
    # Mask to only paint the unfilled bands.
    mask = Image.new("L", (cfg.width, cfg.height), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rectangle([(0, 0), (cfg.width, band)], fill=140)
    mdraw.rectangle(
        [(0, cfg.height - band), (cfg.width, cfg.height)],
        fill=140,
    )
    pano.paste(overlay, (0, 0), mask)
