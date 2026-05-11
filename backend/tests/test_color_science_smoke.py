"""Smoke tests for Sprint 1 color/lighting science.

Each test exercises one canonical scene and asserts the aggregated
LIGHTING FACTS land in the right bucket. Synthetic FrameMeta only —
no real images required.
"""
from __future__ import annotations

from app.models import FrameMeta
from app.services.scene_aggregate import aggregate
from app.services.color_science import check_style_palette


def _frame(idx: int, az: float, **kw) -> FrameMeta:
    return FrameMeta(
        index=idx, azimuth_deg=az, pitch_deg=0,
        mean_luma=kw.get("luma_mean", 130),
        blur_score=6,
        rgb_mean=kw.get("rgb_mean"),
        luma_p05=kw.get("luma_p05", 30),
        luma_p95=kw.get("luma_p95", 220),
        highlight_clip_pct=kw.get("hi", 0.0),
        shadow_clip_pct=kw.get("lo", 0.0),
        saturation_mean=kw.get("sat", 0.4),
    )


def test_golden_hour_warm_with_clipping():
    """Sunset: warm RGB, sharp highlight clipping, high DR."""
    fms = [_frame(i, i * 36, rgb_mean=[245, 180, 90],
                  luma_p05=1, luma_p95=255, hi=0.08, lo=0.02, sat=0.65)
           for i in range(10)]
    agg = aggregate(fms, sun_azimuth_deg=270.0)
    assert agg is not None
    assert 2500 <= agg.cct_k <= 3800, agg.cct_k
    assert agg.dynamic_range in ("high", "extreme")
    assert agg.highlight_clip_pct == 0.08
    assert any("高光" in n for n in agg.lighting_notes)


def test_overcast_neutral_low_contrast():
    """Cloudy: cool-neutral RGB, low DR, no clipping."""
    fms = [_frame(i, i * 36, rgb_mean=[200, 210, 235],
                  luma_p05=70, luma_p95=180, hi=0.0, lo=0.0, sat=0.18)
           for i in range(10)]
    agg = aggregate(fms)
    assert agg is not None
    assert agg.cct_k > 6500
    assert agg.dynamic_range == "low"
    assert agg.lighting_notes == ()


def test_indoor_tungsten_warm_no_clipping():
    """Indoor incandescent: very warm, standard DR."""
    fms = [_frame(i, i * 36, rgb_mean=[230, 165, 95],
                  luma_p05=40, luma_p95=200, hi=0.01, lo=0.04, sat=0.45)
           for i in range(10)]
    agg = aggregate(fms)
    assert agg.cct_k < 4200
    assert agg.dynamic_range in ("low", "standard")


def test_palette_drift_japanese_rejects_warm_scene():
    """Picking 'japanese_clean' with a 3000K scene should fail palette."""
    diffs = check_style_palette("japanese_clean", 3000, 0.30, 0.40)
    axes = [a for a, _ in diffs]
    assert "cct_k" in axes


def test_palette_drift_golden_hour_accepts_warm_scene():
    """Same warm scene against 'golden_hour' should be in band."""
    diffs = check_style_palette("golden_hour", 3200, 0.65, 0.65)
    assert diffs == []


def test_light_direction_front_when_sun_behind_camera():
    """Subject faces north (cam_az=0), sun in south (180): back-light."""
    fms = [_frame(i, az=0.0, rgb_mean=[200, 200, 200])
           for i in range(5)]
    # Add subject_box so consensus filter keeps frames.
    for f in fms:
        object.__setattr__(f, "subject_box", [0.4, 0.3, 0.2, 0.5])
    agg = aggregate(fms, sun_azimuth_deg=180.0)
    assert agg.light_direction == "back"


def test_light_direction_side_when_sun_perpendicular():
    fms = [_frame(i, az=0.0, rgb_mean=[200, 200, 200])
           for i in range(5)]
    for f in fms:
        object.__setattr__(f, "subject_box", [0.4, 0.3, 0.2, 0.5])
    agg = aggregate(fms, sun_azimuth_deg=90.0)
    assert agg.light_direction == "side"
