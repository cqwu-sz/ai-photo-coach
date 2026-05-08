"""Tests for the Pillow + NumPy keyframe scorer (Phase 3.1).

These guard the scoring contract:
  * Each axis stays in [0, 1].
  * Sharp frames score higher than blurry ones.
  * Mid-grey exposure beats blown / crushed extremes.
  * High-edge frames beat flat (sky / wall) frames on density.
  * best_frame_index respects the azimuth window but lets a much-better
    frame win across small distance penalties.
"""
from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageFilter

from app.services import keyframe_score
from app.services.keyframe_score import (
    FrameScore,
    best_frame_index,
    score_frame,
    score_frames,
)


# ────────── fixture builders ──────────


def _to_jpeg(arr: np.ndarray) -> bytes:
    """Encode a uint8 HxW or HxWx3 ndarray to JPEG bytes."""
    if arr.ndim == 2:
        im = Image.fromarray(arr, mode="L")
    else:
        im = Image.fromarray(arr, mode="RGB")
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _flat_grey(value: int = 128, w: int = 320, h: int = 240) -> bytes:
    return _to_jpeg(np.full((h, w), value, dtype=np.uint8))


def _checkerboard(w: int = 320, h: int = 240, square: int = 16) -> bytes:
    """Sharp, edge-rich, mid-grey on average — the gold-standard input."""
    yy, xx = np.indices((h, w))
    pattern = ((yy // square) + (xx // square)) % 2
    arr = (pattern * 255).astype(np.uint8)
    return _to_jpeg(arr)


def _blurred_checkerboard(w: int = 320, h: int = 240, square: int = 16) -> bytes:
    yy, xx = np.indices((h, w))
    pattern = ((yy // square) + (xx // square)) % 2
    im = Image.fromarray((pattern * 255).astype(np.uint8), mode="L")
    blurred = im.filter(ImageFilter.GaussianBlur(radius=6))
    buf = BytesIO()
    blurred.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ────────── score_frame core ──────────


def test_score_frame_returns_scores_in_unit_interval():
    s = score_frame(_checkerboard())
    assert s is not None
    for axis in ["sharpness", "exposure", "composition_density", "overall"]:
        v = getattr(s, axis)
        assert 0.0 <= v <= 1.0, f"{axis}={v} out of [0,1]"


def test_score_frame_invalid_bytes_returns_none():
    assert score_frame(b"not a jpeg") is None


def test_sharp_beats_blurry():
    sharp = score_frame(_checkerboard())
    blur  = score_frame(_blurred_checkerboard())
    assert sharp is not None and blur is not None
    assert sharp.sharpness > blur.sharpness + 0.10, (
        f"sharp={sharp.sharpness} blur={blur.sharpness}"
    )


def test_midgrey_beats_blown_highlights():
    mid    = score_frame(_flat_grey(128))
    blown  = score_frame(_flat_grey(254))
    crushed = score_frame(_flat_grey(2))
    for s in (mid, blown, crushed):
        assert s is not None
    assert mid.exposure > blown.exposure
    assert mid.exposure > crushed.exposure


def test_edge_rich_beats_flat_density():
    rich = score_frame(_checkerboard())
    flat = score_frame(_flat_grey(128))
    assert rich is not None and flat is not None
    assert rich.composition_density > flat.composition_density + 0.15


def test_score_frames_preserves_order_and_handles_failures():
    out = score_frames([_checkerboard(), b"bad bytes", _flat_grey(128)])
    assert len(out) == 3
    assert out[0] is not None
    assert out[1] is None
    assert out[2] is not None


# ────────── best_frame_index ──────────


def _fs(overall: float) -> FrameScore:
    return FrameScore(
        sharpness=overall, exposure=overall,
        composition_density=overall, overall=overall,
    )


def test_best_frame_picks_nearest_azimuth_when_quality_equal():
    azs = [0.0, 45.0, 90.0, 135.0]
    scores = [_fs(0.5), _fs(0.5), _fs(0.5), _fs(0.5)]
    pick = best_frame_index(50.0, azs, scores)
    assert pick == 1, "expected the 45° frame when shot is at 50°"


def test_best_frame_quality_can_overrule_small_distance():
    """A noticeably better frame slightly further away should win."""
    azs = [50.0, 80.0]
    scores = [_fs(0.20), _fs(0.95)]
    pick = best_frame_index(50.0, azs, scores, azimuth_window_deg=60)
    assert pick == 1


def test_best_frame_far_low_quality_loses():
    """A bad frame outside the window must lose to a closer good frame."""
    azs = [50.0, 200.0]
    scores = [_fs(0.6), _fs(0.95)]
    pick = best_frame_index(50.0, azs, scores, azimuth_window_deg=35)
    assert pick == 0


def test_best_frame_handles_misaligned_lists_via_nearest():
    azs = [0.0, 90.0, 180.0]
    pick = best_frame_index(170.0, azs, [_fs(0.5), _fs(0.5)])
    assert pick == 2


def test_best_frame_returns_none_for_empty():
    assert best_frame_index(0.0, [], []) is None


def test_angle_delta_wraps_around_360():
    f = keyframe_score._angle_delta
    assert f(355.0, 5.0) == pytest.approx(10.0)
    assert f(10.0, 350.0) == pytest.approx(20.0)
    assert f(180.0, 0.0) == pytest.approx(180.0)
