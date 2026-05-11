"""Color & lighting science helpers.

Up to v10.x the backend treated lighting as a single number ``mean_luma``.
That collapses every interesting axis (color temperature, dynamic range,
clipping, light direction) into one scalar — far too coarse to actually
distinguish a "Japanese clean" palette from "HK neon" or "film grain".

This module is the *canonical* place where we turn raw per-frame pixel
statistics into the structured lighting/color facts that the prompt
builder folds into ``LIGHTING FACTS`` and that ``style_compliance`` uses
to tell the LLM "the scene is 6500K, your style targets 5200K, please
warm up the white-balance suggestion".

Inputs are deliberately platform-neutral: the client (Web canvas, iOS
CoreImage) computes per-frame numeric stats and ships them in
``FrameMeta`` — *no raw bitmap ever leaves the device*. This module only
ever consumes the stats & aggregates them across the keyframe set.

Public surface:

    estimate_cct_k(rgb_avg)              -> int kelvin (1500..15000)
    estimate_tint(rgb_avg)               -> float in [-1, +1] (green/magenta)
    classify_light_ratio(luma_q)         -> Literal["front","side","back","top","mixed"]
    classify_dynamic_range(stats)        -> Literal["low","standard","high","extreme"]
    aggregate_lighting(frames)           -> LightingAggregate

All functions are *deterministic*, no I/O, no LLM calls — safe to unit
test with synthetic data.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal, Optional, Sequence


# ---------------------------------------------------------------------------
# Color temperature estimation
# ---------------------------------------------------------------------------
#
# We use the gray-world assumption: average R/G/B over a "neutral" subset
# of pixels approximates the scene illuminant. The client passes us the
# raw 0..255 averages (possibly already trimmed of saturated highlights
# & black shadows). We convert to chromaticity, then to CCT via McCamy's
# 1992 cubic — fast, no table lookup, accurate to ~100 K within the
# 2500-12000 K range we actually care about.

def estimate_cct_k(rgb_avg: Sequence[float]) -> Optional[int]:
    """Return correlated colour temperature in Kelvin from a mean
    sRGB triplet (each in 0..255). Returns None when the input is too
    dim or too monochrome to give a meaningful estimate.

    Implementation notes:
      * Linearise sRGB → linear RGB → CIE XYZ (Rec. 709 primaries).
      * Compute chromaticity (x, y), then McCamy's cubic in n=(x-x_e)/(y-y_e),
        with reference point (0.3320, 0.1858).
      * Clamp result to [1500, 15000] — anything outside is almost
        certainly noise.
    """
    if not rgb_avg or len(rgb_avg) < 3:
        return None
    r, g, b = (max(0.0, min(255.0, float(c))) for c in rgb_avg[:3])
    if r + g + b < 30:           # too dark
        return None
    # sRGB → linear (gamma 2.2 approx is fine for an *averaged* triplet)
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl, gl, bl = lin(r), lin(g), lin(b)
    # Rec. 709 / sRGB → XYZ (D65)
    X = 0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl
    Y = 0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl
    Z = 0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl
    s = X + Y + Z
    if s <= 1e-9:
        return None
    x, y = X / s, Y / s
    if y < 1e-6:
        return None
    n = (x - 0.3320) / (0.1858 - y)
    cct = 449.0 * n**3 + 3525.0 * n**2 + 6823.3 * n + 5520.33
    if not math.isfinite(cct):
        return None
    return int(max(1500, min(15000, round(cct))))


def estimate_tint(rgb_avg: Sequence[float]) -> Optional[float]:
    """Return a green/magenta tint in [-1, +1].

    Negative = green tint (typical of fluorescent / forest understory).
    Positive = magenta tint (sunset / indoor incandescent + bounce).
    Computed as the deviation of the green channel from the
    luminance-weighted average of red+blue, normalised by the mean.
    """
    if not rgb_avg or len(rgb_avg) < 3:
        return None
    r, g, b = (float(c) for c in rgb_avg[:3])
    avg_rb = (r + b) / 2
    mean = (r + g + b) / 3
    if mean < 5:
        return None
    # Positive number means green > avg(R,B). Flip sign so positive ==
    # magenta (matching Lightroom's WB tint convention).
    raw = (avg_rb - g) / mean
    return max(-1.0, min(1.0, round(raw, 3)))


# ---------------------------------------------------------------------------
# Dynamic range / clipping classification
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FrameLightingStats:
    """What we expect each FrameMeta lighting field to provide."""
    rgb_mean: Optional[Sequence[float]]
    luma_mean: Optional[float]
    luma_p05: Optional[float]
    luma_p95: Optional[float]
    highlight_clip_pct: Optional[float]
    shadow_clip_pct: Optional[float]


def classify_dynamic_range(stats: FrameLightingStats) -> Optional[str]:
    """Bucket the dynamic range into low / standard / high / extreme.

    EV span = log2(p95/p05) on linear luma. Practical thresholds:
      * < 4 EV   → low contrast (overcast / fog / studio fill)
      * 4-7 EV   → standard
      * 7-10 EV  → high (sun + shadow, sunset)
      * > 10 EV  → extreme (sun in frame, deep cave + window)
    Returns ``None`` when stats are missing.
    """
    p05, p95 = stats.luma_p05, stats.luma_p95
    if p05 is None or p95 is None or p05 <= 0:
        return None
    ev = math.log2(max(1.0, p95) / max(0.5, p05))
    if ev < 4:
        return "low"
    if ev < 7:
        return "standard"
    if ev < 10:
        return "high"
    return "extreme"


def classify_light_ratio(
    front_luma: Optional[float],
    back_luma: Optional[float],
    side_luma: Optional[float],
) -> Optional[str]:
    """Decide the lighting *direction* relative to the subject.

    Inputs are the average luma of a small window centred on the
    subject's face (front), the area opposite the camera-to-subject
    axis (back, i.e. the background behind the subject), and the
    perpendicular side. We pick whichever is brightest by a meaningful
    margin (>20% over the second-brightest); otherwise return "mixed".
    """
    parts = [
        ("front", front_luma),
        ("back",  back_luma),
        ("side",  side_luma),
    ]
    parts = [(k, v) for k, v in parts if v is not None and v > 0]
    if len(parts) < 2:
        return None
    parts.sort(key=lambda p: p[1], reverse=True)
    if parts[0][1] / max(1.0, parts[1][1]) < 1.20:
        return "mixed"
    return parts[0][0]


# ---------------------------------------------------------------------------
# Cross-frame aggregation
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LightingAggregate:
    """Scene-level lighting fact bundle. Consumed by scene_aggregate
    and rendered into the LIGHTING FACTS block."""
    cct_k: Optional[int]
    tint: Optional[float]
    dynamic_range: Optional[str]
    light_direction: Optional[str]
    highlight_clip_pct: Optional[float]   # max across frames
    shadow_clip_pct: Optional[float]
    luma_mean: Optional[float]
    notes: list[str]                      # human-readable warnings


def aggregate_lighting(frame_stats: Sequence[FrameLightingStats]) -> LightingAggregate:
    """Reduce per-frame lighting stats into one scene-level bundle.

    Strategy:
      * CCT / tint: median across frames (robust to one bad frame).
      * Dynamic range: take the *worst* (highest-contrast) frame —
        that's the one that constrains the LLM's exposure decision.
      * Clipping: take the maximum % across frames (a single clipped
        frame is still a problem the user needs to know about).
      * Notes: append plain-language warnings when thresholds breach.
    """
    if not frame_stats:
        return LightingAggregate(None, None, None, None, None, None, None, [])

    ccts: list[int] = []
    tints: list[float] = []
    for f in frame_stats:
        if f.rgb_mean:
            k = estimate_cct_k(f.rgb_mean)
            if k is not None:
                ccts.append(k)
            t = estimate_tint(f.rgb_mean)
            if t is not None:
                tints.append(t)

    cct_k = int(statistics.median(ccts)) if ccts else None
    tint = round(statistics.median(tints), 3) if tints else None

    # Dynamic range: pick the most contrasty frame.
    drs = [classify_dynamic_range(f) for f in frame_stats]
    drs = [d for d in drs if d]
    rank = {"low": 0, "standard": 1, "high": 2, "extreme": 3}
    dynamic_range = max(drs, key=lambda d: rank[d]) if drs else None

    hi_clips = [f.highlight_clip_pct for f in frame_stats if f.highlight_clip_pct is not None]
    sh_clips = [f.shadow_clip_pct    for f in frame_stats if f.shadow_clip_pct    is not None]
    luma_means = [f.luma_mean        for f in frame_stats if f.luma_mean        is not None]

    hi_max = round(max(hi_clips), 3) if hi_clips else None
    sh_max = round(max(sh_clips), 3) if sh_clips else None
    luma_mean = round(statistics.mean(luma_means), 1) if luma_means else None

    notes: list[str] = []
    if hi_max is not None and hi_max > 0.05:
        notes.append(
            f"高光裁剪 {int(hi_max*100)}%（白衣 / 天空 / 反光面已经过曝），"
            "建议降 0.7-1.3 EV 或换正面光"
        )
    if sh_max is not None and sh_max > 0.10:
        notes.append(
            f"暗部死黑 {int(sh_max*100)}%（阴影细节丢失），"
            "建议加补光（手机闪 / 反光板）或转向半逆光"
        )
    if dynamic_range == "extreme":
        notes.append("画面动态范围超出手机宽容度，强烈建议 HDR 或包围曝光")

    return LightingAggregate(
        cct_k=cct_k,
        tint=tint,
        dynamic_range=dynamic_range,
        light_direction=None,   # filled in by scene_aggregate using subject_box
        highlight_clip_pct=hi_max,
        shadow_clip_pct=sh_max,
        luma_mean=luma_mean,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Style palette matching
# ---------------------------------------------------------------------------
# Each style keyword has a target CCT range, target saturation, and
# tolerance bands. ``check_style_palette`` returns a list of (axis,
# diff, suggestion) tuples that style_compliance can fold into the
# LLM's per-shot ``style_match.fixes`` array.

# Calibrated by eyeballing reference image medians from
# web/img/style/CREDITS.md — refine via Sprint 3 feedback loop.
STYLE_PALETTE: dict[str, dict[str, tuple[float, float]]] = {
    # (low, high) tolerance bands.
    "japanese_clean": {
        "cct_k":      (5800, 6800),
        "saturation": (0.18, 0.42),
        "contrast":   (0.30, 0.55),
    },
    "ambient_mood": {
        "cct_k":      (4200, 5400),
        "saturation": (0.45, 0.75),
        "contrast":   (0.55, 0.85),
    },
    "hk_neon": {
        "cct_k":      (3000, 4000),
        "saturation": (0.65, 0.95),
        "contrast":   (0.65, 0.95),
    },
    "film_grain": {
        "cct_k":      (4800, 5600),
        "saturation": (0.30, 0.55),
        "contrast":   (0.55, 0.80),
    },
    "high_key": {
        "cct_k":      (5600, 6600),
        "saturation": (0.20, 0.45),
        "contrast":   (0.20, 0.45),
    },
    "golden_hour": {
        "cct_k":      (2800, 3800),
        "saturation": (0.55, 0.85),
        "contrast":   (0.50, 0.80),
    },
}


def check_style_palette(
    style_keyword: str,
    cct_k: Optional[int],
    saturation: Optional[float],
    contrast: Optional[float],
) -> list[tuple[str, str]]:
    """Return a list of (axis, suggestion) tuples for any axis that
    drifts outside the style's target band. Returns [] when everything
    is in band or the style is unknown.
    """
    palette = STYLE_PALETTE.get(style_keyword)
    if not palette:
        return []
    out: list[tuple[str, str]] = []
    if cct_k is not None and "cct_k" in palette:
        lo, hi = palette["cct_k"]
        if cct_k < lo:
            out.append(("cct_k",
                        f"画面色温 {cct_k} K 偏冷，{style_keyword} 目标 ≈ {int((lo+hi)/2)} K，"
                        "建议把 white_balance 调到 cloudy / shade 或后期 +200 K"))
        elif cct_k > hi:
            out.append(("cct_k",
                        f"画面色温 {cct_k} K 偏暖，{style_keyword} 目标 ≈ {int((lo+hi)/2)} K，"
                        "建议把 white_balance 调到 daylight 或后期 -200 K"))
    if saturation is not None and "saturation" in palette:
        lo, hi = palette["saturation"]
        if saturation < lo:
            out.append(("saturation",
                        f"饱和度 {saturation:.2f} 偏低，{style_keyword} 目标 ≈ {((lo+hi)/2):.2f}，"
                        "建议后期 +0.10 或加偏振 / 降低环境反光"))
        elif saturation > hi:
            out.append(("saturation",
                        f"饱和度 {saturation:.2f} 偏高，{style_keyword} 目标 ≈ {((lo+hi)/2):.2f}，"
                        "建议后期 -0.10 或换柔和侧光"))
    if contrast is not None and "contrast" in palette:
        lo, hi = palette["contrast"]
        if contrast < lo:
            out.append(("contrast",
                        f"对比度 {contrast:.2f} 偏平，{style_keyword} 目标 ≈ {((lo+hi)/2):.2f}，"
                        "建议找有强光阴影的角度或后期 +clarity"))
        elif contrast > hi:
            out.append(("contrast",
                        f"对比度 {contrast:.2f} 偏高，{style_keyword} 目标 ≈ {((lo+hi)/2):.2f}，"
                        "建议加补光柔化阴影或后期 -clarity"))
    return out
