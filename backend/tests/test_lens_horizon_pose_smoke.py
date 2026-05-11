"""Sprint 2 smoke tests: lens / horizon / pose facts."""
from __future__ import annotations

from app.models import FrameMeta
from app.services.scene_aggregate import aggregate, _vote_horizon, _build_pose_facts


def _frame(idx: int, **kw) -> FrameMeta:
    return FrameMeta(
        index=idx, azimuth_deg=kw.get("az", idx * 36),
        pitch_deg=kw.get("pitch", 0),
        mean_luma=130, blur_score=6,
        face_height_ratio=kw.get("face"),
        focal_length_35mm_eq=kw.get("focal"),
        horizon_y=kw.get("hy"),
        horizon_y_vision=kw.get("hyv"),
        sky_mask_top_pct=kw.get("sky"),
        shoulder_tilt_deg=kw.get("sh"),
        hip_offset_x=kw.get("hip"),
        chin_forward=kw.get("chin"),
        spine_curve=kw.get("spine"),
    )


def test_lens_with_exif_returns_close_distance():
    """Subject face fills 18% of frame at 26mm-eq lens → ~1m."""
    fms = [_frame(i, face=0.18 if i == 4 else None, focal=26.0) for i in range(10)]
    agg = aggregate(fms)
    assert agg is not None
    assert 0.7 < agg.subject_distance_m < 1.5, agg.subject_distance_m


def test_lens_without_exif_falls_back_to_K():
    fms = [_frame(i, face=0.18 if i == 4 else None) for i in range(10)]
    agg = aggregate(fms)
    assert agg.subject_distance_m == 1.0


def test_horizon_consensus_high_when_two_sources_agree():
    fms = [_frame(i, hy=0.50, hyv=0.52, sky=0.30) for i in range(5)]
    y, conf, ok = _vote_horizon(fms)
    assert ok is True
    assert conf == "high"
    assert 0.49 < y < 0.53


def test_horizon_suppressed_when_no_sky():
    fms = [_frame(i, hy=0.50, hyv=0.52, sky=0.01) for i in range(5)]
    y, conf, ok = _vote_horizon(fms)
    assert ok is False
    assert conf == "none"
    assert y is None


def test_pose_facts_fire_on_meaningful_drift():
    fms = [_frame(i, sh=8.5, hip=0.18, chin=0.13, spine=0.07) for i in range(5)]
    facts = _build_pose_facts(fms)
    assert any("肩线" in f for f in facts)
    assert any("重心" in f for f in facts)
    assert any("下颌" in f or "下巴" in f for f in facts)
    assert any("脊柱" in f for f in facts)


def test_pose_facts_silent_when_below_threshold():
    fms = [_frame(i, sh=2, hip=0.05, chin=0.04, spine=0.02) for i in range(5)]
    assert _build_pose_facts(fms) == []
