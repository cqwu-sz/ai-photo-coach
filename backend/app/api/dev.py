"""Dev / demo endpoints.

These exist so a user without a webcam (e.g. on a Windows desktop) can
still drive the entire end-to-end pipeline. They serve a deterministic,
hand-tuned set of synthetic environment frames plus a few reference
photos. The generated images are rich enough that Gemini will produce
a meaningful scene description (lighting, foreground/background, where
to place subjects) instead of saying "blank rectangle".

Frames simulate a 360° scan of an outdoor park around golden hour:
azimuth 0  -> looking at the sunset (warm sky, silhouette tree, path)
azimuth 90 -> side view (cooler sky, building, lamppost)
azimuth 180 -> behind the user (deep blue sky, fountain)
azimuth 270 -> opposite side (mixed sky, bench, tree group)
We sample 8 frames at 45° intervals, each rendered slightly
differently so the LLM can compose multi-shot diversity.
"""
from __future__ import annotations

import io
import math
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Response

router = APIRouter(prefix="/dev", tags=["dev"])

SAMPLE_FRAME_COUNT = 8
SAMPLE_REFERENCE_COUNT = 3
FRAME_SIZE = (960, 540)
REF_SIZE = (640, 800)

# Per-scene-mode demo presets. Each tuple is (person_count, style_keywords,
# label) — the manifest layer pre-fills CaptureMeta hints so the frontend
# can pick them up without a manual second screen.
_SCENE_DEFAULTS: dict[str, dict] = {
    "portrait": {
        "person_count": 1,
        "style_keywords": ["clean", "bright"],
        "blurb": "黄昏公园人像，半身或全身都能出。",
    },
    "closeup": {
        "person_count": 1,
        "style_keywords": ["mood", "tele"],
        "blurb": "侧逆光下的脸部 / 上半身特写。",
    },
    "full_body": {
        "person_count": 1,
        "style_keywords": ["clean", "wide"],
        "blurb": "拉远到 35mm，全身入画带环境。",
    },
    "documentary": {
        "person_count": 2,
        "style_keywords": ["candid", "story"],
        "blurb": "街拍人文，自然走动 + 互动。",
    },
    "scenery": {
        "person_count": 0,
        "style_keywords": ["wide", "leading line"],
        "blurb": "纯环境出片，无人。",
    },
    "light_shadow": {
        "person_count": 1,
        "style_keywords": ["chiaroscuro", "rim light"],
        "blurb": "用强对比光影做戏剧画面，剪影 / 长影 / 光柱。",
    },
}


def _sample_manifest(scene_mode: str = "portrait") -> dict:
    cfg = _SCENE_DEFAULTS.get(scene_mode, _SCENE_DEFAULTS["portrait"])
    return {
        "scene_mode": scene_mode,
        "person_count_default": cfg["person_count"],
        "style_keywords_default": cfg["style_keywords"],
        "blurb": cfg["blurb"],
        "frames": [
            {
                "index": i,
                "azimuth_deg": (i * 45) % 360,
                "pitch_deg": 0.0,
                "roll_deg": 0.0,
                "timestamp_ms": i * 220,
                "url": f"/dev/sample-frame/{i}.jpg",
            }
            for i in range(SAMPLE_FRAME_COUNT)
        ],
        "references": (
            [
                {"index": i, "url": f"/dev/sample-reference/{i}.jpg"}
                for i in range(SAMPLE_REFERENCE_COUNT)
            ]
            if scene_mode != "scenery"
            else []
        ),
        "panorama_url": "/dev/panorama-demo.jpg",
    }


@router.get("/sample-manifest")
def sample_manifest(
    scene_mode: str = Query("portrait", description="portrait/closeup/full_body/documentary/scenery"),
) -> dict:
    if scene_mode not in _SCENE_DEFAULTS:
        scene_mode = "portrait"
    return _sample_manifest(scene_mode)


@router.get(
    "/sample-frame/{idx}.jpg",
    responses={200: {"content": {"image/jpeg": {}}}},
)
def sample_frame(idx: int) -> Response:
    if not 0 <= idx < SAMPLE_FRAME_COUNT:
        raise HTTPException(404, "frame out of range")
    return Response(content=_make_frame_bytes(idx), media_type="image/jpeg")


@router.get(
    "/sample-reference/{idx}.jpg",
    responses={200: {"content": {"image/jpeg": {}}}},
)
def sample_reference(idx: int) -> Response:
    if not 0 <= idx < SAMPLE_REFERENCE_COUNT:
        raise HTTPException(404, "reference out of range")
    return Response(content=_make_reference_bytes(idx), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Image generation – pure Pillow, no external assets.
# Cached by index so repeated requests don't re-render.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=SAMPLE_FRAME_COUNT)
def _make_frame_bytes(idx: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    azimuth = (idx * 45) % 360
    w, h = FRAME_SIZE

    img = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Sky gradient: shifts hue with azimuth – sunset on west, blue on east.
    sky_top, sky_bottom = _sky_colors(azimuth)
    for y in range(int(h * 0.65)):
        t = y / (h * 0.65)
        c = _lerp_rgb(sky_top, sky_bottom, t)
        draw.line([(0, y), (w, y)], fill=c)

    # Sun disc near the horizon for west-facing frames.
    if 300 <= azimuth or azimuth <= 60:
        sun_x = w // 2 + int(math.sin(math.radians(azimuth)) * w * 0.3)
        sun_y = int(h * 0.6)
        for r, alpha in [(140, 60), (90, 120), (55, 220)]:
            draw.ellipse(
                [sun_x - r, sun_y - r, sun_x + r, sun_y + r],
                fill=_blend_color((255, 220, 140), sky_bottom, alpha / 255),
            )

    # Ground band – warm grass + path
    ground_top = int(h * 0.65)
    ground_color = _ground_color(azimuth)
    draw.rectangle([0, ground_top, w, h], fill=ground_color)

    # Stone path (perspective triangle) toward the camera
    path_color = (120, 110, 95) if azimuth < 90 or azimuth > 270 else (130, 130, 130)
    draw.polygon(
        [
            (w // 2 - 30, ground_top + 5),
            (w // 2 + 30, ground_top + 5),
            (int(w * 0.85), h - 10),
            (int(w * 0.15), h - 10),
        ],
        fill=path_color,
    )

    # Per-azimuth foreground decoration
    _draw_decor(draw, azimuth, w, h, ground_top)

    # Subtle vignette
    overlay = Image.new("RGB", (w, h), (0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([-w // 4, -h // 4, w + w // 4, h + h // 4], fill=(60, 60, 60))
    img = Image.blend(img, overlay, 0.06)

    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))

    # Stamp azimuth in the corner for debugging
    try:
        draw2 = ImageDraw.Draw(img)
        draw2.text((12, 12), f"azimuth {azimuth:>3}°", fill=(255, 255, 255))
    except Exception:
        pass

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return buf.getvalue()


def _draw_decor(draw, azimuth, w, h, ground_top):
    if azimuth in (0, 315):
        # Tall silhouette tree on the right – sunset side
        x = int(w * 0.75)
        draw.rectangle([x - 6, ground_top - 130, x + 6, ground_top], fill=(40, 35, 30))
        draw.ellipse([x - 60, ground_top - 200, x + 60, ground_top - 90], fill=(50, 45, 35))
    elif azimuth == 45:
        # Lone bench
        bx = int(w * 0.55)
        draw.rectangle([bx - 70, ground_top + 10, bx + 70, ground_top + 22], fill=(85, 60, 40))
        draw.rectangle([bx - 65, ground_top + 22, bx - 55, ground_top + 60], fill=(85, 60, 40))
        draw.rectangle([bx + 55, ground_top + 22, bx + 65, ground_top + 60], fill=(85, 60, 40))
    elif azimuth == 90:
        # Modern building block on the left
        draw.rectangle([20, ground_top - 220, 220, ground_top], fill=(110, 110, 120))
        for r in range(3):
            for c in range(4):
                draw.rectangle(
                    [40 + c * 45, ground_top - 200 + r * 60,
                     40 + c * 45 + 25, ground_top - 200 + r * 60 + 30],
                    fill=(180, 170, 110),
                )
        # Lamppost on the right
        lx = int(w * 0.82)
        draw.rectangle([lx - 3, ground_top - 180, lx + 3, ground_top], fill=(50, 50, 50))
        draw.ellipse([lx - 18, ground_top - 200, lx + 18, ground_top - 165], fill=(255, 230, 160))
    elif azimuth == 135:
        # Two short trees clustered
        for tx in (int(w * 0.3), int(w * 0.45)):
            draw.rectangle([tx - 5, ground_top - 80, tx + 5, ground_top], fill=(55, 40, 30))
            draw.ellipse([tx - 35, ground_top - 130, tx + 35, ground_top - 60], fill=(60, 95, 55))
    elif azimuth == 180:
        # Fountain – round basin
        cx = w // 2
        cy = ground_top + 60
        draw.ellipse([cx - 110, cy - 18, cx + 110, cy + 18], fill=(140, 145, 150))
        draw.ellipse([cx - 90, cy - 12, cx + 90, cy + 12], fill=(80, 110, 140))
        # Spray
        for i in range(-2, 3):
            draw.line([(cx + i * 14, cy - 10), (cx + i * 18, cy - 80)], fill=(220, 230, 240), width=2)
    elif azimuth == 225:
        # Bench cluster + small dog statue
        bx = int(w * 0.45)
        draw.rectangle([bx - 90, ground_top + 5, bx + 90, ground_top + 18], fill=(85, 60, 40))
        draw.rectangle([bx - 4, ground_top + 18, bx + 4, ground_top + 60], fill=(85, 60, 40))
        # Dog
        dx = int(w * 0.6)
        draw.rectangle([dx - 18, ground_top, dx + 18, ground_top + 25], fill=(120, 80, 50))
        draw.rectangle([dx - 22, ground_top - 10, dx - 8, ground_top + 4], fill=(120, 80, 50))
    elif azimuth == 270:
        # Distant skyline
        for i, x in enumerate([0.05, 0.18, 0.30, 0.45, 0.60, 0.75, 0.88]):
            bx = int(w * x)
            bh = 60 + (i * 23) % 80
            draw.rectangle(
                [bx - 22, ground_top - bh, bx + 22, ground_top],
                fill=(60 + i * 8, 70 + i * 6, 90),
            )


def _sky_colors(azimuth: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    # Returns (top, near-horizon)
    if azimuth in (0, 315):
        return ((35, 30, 70), (255, 130, 80))   # warm sunset west
    if azimuth == 45:
        return ((50, 60, 110), (240, 170, 110))
    if azimuth == 90:
        return ((40, 70, 130), (170, 190, 220))
    if azimuth == 135:
        return ((30, 50, 95), (110, 150, 200))
    if azimuth == 180:
        return ((20, 30, 70), (60, 90, 150))    # deep blue east
    if azimuth == 225:
        return ((30, 40, 80), (90, 120, 180))
    if azimuth == 270:
        return ((35, 35, 85), (180, 150, 180))
    return ((40, 40, 90), (200, 160, 180))


def _ground_color(azimuth: int) -> tuple[int, int, int]:
    base = (60, 95, 60)
    if azimuth in (0, 315, 45):
        # Warm cast on grass
        return (95, 110, 60)
    if 90 <= azimuth <= 180:
        return base
    return (70, 100, 70)


def _lerp_rgb(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _blend_color(fg, bg, alpha):
    return tuple(int(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))


# ---------------------------------------------------------------------------
# Reference styles – three distinct moods that look like sample inspiration
# photos rather than environment frames.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=SAMPLE_REFERENCE_COUNT)
def _make_reference_bytes(idx: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    w, h = REF_SIZE
    img = Image.new("RGB", (w, h), (10, 10, 14))
    draw = ImageDraw.Draw(img)

    if idx == 0:
        # "moody cinematic" – portrait silhouette in front of warm window
        for y in range(h):
            t = y / h
            c = _lerp_rgb((35, 25, 50), (110, 65, 70), t)
            draw.line([(0, y), (w, y)], fill=c)
        # Window frame
        wx0, wy0, wx1, wy1 = int(w * 0.12), int(h * 0.18), int(w * 0.88), int(h * 0.78)
        draw.rectangle([wx0, wy0, wx1, wy1], fill=(180, 110, 70))
        for col in range(3):
            x = wx0 + (wx1 - wx0) * (col + 1) // 4
            draw.line([(x, wy0), (x, wy1)], fill=(60, 30, 25), width=4)
        # Subject silhouette
        sx, sy = w // 2, int(h * 0.55)
        draw.ellipse([sx - 50, sy - 130, sx + 50, sy - 30], fill=(20, 15, 15))
        draw.rectangle([sx - 70, sy - 30, sx + 70, sy + 180], fill=(20, 15, 15))
        title = "MOODY CINEMATIC"
    elif idx == 1:
        # "clean bright" – flat lay over a beige wall, two figures with
        # geometric shadows
        for y in range(h):
            c = _lerp_rgb((220, 210, 195), (240, 230, 215), y / h)
            draw.line([(0, y), (w, y)], fill=c)
        # Two figure shadows
        for sx in (int(w * 0.32), int(w * 0.58)):
            draw.ellipse([sx - 35, int(h * 0.28), sx + 35, int(h * 0.32) + 60], fill=(180, 165, 140))
            draw.rectangle([sx - 55, int(h * 0.32) + 60, sx + 55, int(h * 0.78)], fill=(180, 165, 140))
        # Sun strip
        draw.rectangle([0, int(h * 0.18), w, int(h * 0.22)], fill=(255, 240, 210))
        title = "CLEAN BRIGHT"
    else:
        # "film warm" – overhead grass with two outline figures
        for y in range(h):
            c = _lerp_rgb((85, 130, 70), (60, 95, 50), y / h)
            draw.line([(0, y), (w, y)], fill=c)
        # Figures lying-down outlines
        for sx, sy in [(int(w * 0.35), int(h * 0.45)), (int(w * 0.62), int(h * 0.55))]:
            draw.ellipse([sx - 26, sy - 60, sx + 26, sy + 60], outline=(230, 220, 180), width=4)
            draw.ellipse([sx - 14, sy - 80, sx + 14, sy - 50], outline=(230, 220, 180), width=4)
        title = "FILM WARM"

    img = img.filter(ImageFilter.GaussianBlur(radius=1.2))
    draw = ImageDraw.Draw(img)
    draw.text((20, h - 30), title, fill=(245, 240, 230))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
