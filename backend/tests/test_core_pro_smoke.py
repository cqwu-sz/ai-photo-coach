"""Smoke tests for the core-capability pro upgrade modules:
landmark_graph, light_pro, potential_evaluator, shot_hypothesis.

These tests build synthetic inputs to verify the deterministic
geometry / scoring logic does what the modules promise. They do not
hit any network, LLM, or filesystem.
"""
from __future__ import annotations

from app.models import FrameMeta, LandmarkCandidate
from app.services import (
    landmark_graph,
    light_pro,
    potential_evaluator,
    scene_aggregate,
    shot_hypothesis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _frame(idx: int, az: float, landmarks: list[LandmarkCandidate] | None = None,
           **overrides) -> FrameMeta:
    base = dict(
        index=idx,
        azimuth_deg=az,
        mean_luma=120.0,
        blur_score=80.0,
        luma_p05=20.0,
        luma_p95=220.0,
        highlight_clip_pct=0.01,
        shadow_clip_pct=0.01,
        rgb_mean=[120.0, 120.0, 120.0],
        sky_mask_top_pct=0.10,
        person_box=[0.4, 0.2, 0.2, 0.6],
        subject_box=[0.4, 0.2, 0.2, 0.6],
    )
    base.update(overrides)
    fm = FrameMeta(**base)
    fm.landmark_candidates = landmarks
    return fm


def _ld(label: str, x: float, y: float, z: float, *, sid: str | None = None,
        size=None, conf=0.8) -> LandmarkCandidate:
    return LandmarkCandidate(
        label=label,
        world_xyz=[x, y, z],
        size_m=size,
        confidence=conf,
        stable_id=sid,
    )


# ---------------------------------------------------------------------------
# landmark_graph
# ---------------------------------------------------------------------------
def test_landmark_graph_empty_when_no_landmarks():
    frames = [_frame(0, 0), _frame(1, 90)]
    assert landmark_graph.aggregate(frames) is None


def test_landmark_graph_detects_stereo_opportunity():
    # Ground stair at (1, 0, 2). Balcony at same azimuth, +3.2 m up.
    landmarks_per_frame = [
        [_ld("stair", 1.0, 0.0, -2.0, sid="s1"),
         _ld("balcony", 1.0, 3.2, -2.0, sid="b1")],
        [_ld("stair", 1.0, 0.0, -2.0, sid="s1"),
         _ld("balcony", 1.0, 3.2, -2.0, sid="b1")],
    ]
    frames = [_frame(0, 0, landmarks_per_frame[0]),
              _frame(1, 30, landmarks_per_frame[1])]
    graph = landmark_graph.aggregate(frames)
    assert graph is not None
    assert len(graph.nodes) == 2
    assert graph.has_stereo_opportunity is True

    # The lower node should sit on the ground bucket and have h≈0.
    low = next(n for n in graph.nodes if n.label == "stair")
    high = next(n for n in graph.nodes if n.label == "balcony")
    assert low.height_above_ground_m is not None and abs(low.height_above_ground_m) < 0.1
    assert high.height_above_ground_m is not None and high.height_above_ground_m > 3.0
    assert high.height_bucket == "first_floor"

    # Should emit at least one is_above edge from stair → balcony.
    assert any(e.relation == "is_above" and e.src_id == low.node_id
               and e.dst_id == high.node_id for e in graph.edges)


def test_landmark_graph_dedup_across_frames_without_stable_id():
    """Same point observed twice without stable_id collapses to one node."""
    frames = [
        _frame(0, 0, [_ld("doorway", 0.5, 1.0, -3.0)]),
        _frame(1, 5, [_ld("doorway", 0.55, 1.0, -3.02)]),  # same physical place
    ]
    graph = landmark_graph.aggregate(frames)
    assert graph is not None
    assert len(graph.nodes) == 1


def test_landmark_graph_prompt_block_non_empty_for_stereo():
    frames = [
        _frame(0, 0, [_ld("stair", 1.0, 0.0, -2.0, sid="s1"),
                      _ld("balcony", 1.0, 3.2, -2.0, sid="b1")]),
        _frame(1, 30, [_ld("stair", 1.0, 0.0, -2.0, sid="s1"),
                       _ld("balcony", 1.0, 3.2, -2.0, sid="b1")]),
    ]
    block = landmark_graph.to_prompt_block(landmark_graph.aggregate(frames))
    assert "LANDMARK GRAPH" in block
    assert "立体" in block  # 立体空间机会 line
    assert "LANDMARK DOCTRINE" in block


# ---------------------------------------------------------------------------
# light_pro
# ---------------------------------------------------------------------------
def test_light_pro_golden_hour_soft():
    frames = [_frame(i, i * 30) for i in range(4)]
    agg = light_pro.aggregate(
        frames,
        sun_altitude_deg=12.0,
        cct_k=3800,
        highlight_clip_pct=0.005,
        shadow_clip_pct=0.005,
        light_direction="side",
    )
    assert agg is not None
    assert agg.elevation == "golden"
    # Hardness can be soft or medium depending on score; key fact is it's not "hard".
    assert agg.hardness in ("soft", "medium")
    assert "黄金" in agg.summary_zh or "低位" in agg.summary_zh


def test_light_pro_overhead_hard():
    frames = [_frame(i, i * 30, highlight_clip_pct=0.10, shadow_clip_pct=0.08,
                     luma_p05=5.0, luma_p95=250.0) for i in range(4)]
    agg = light_pro.aggregate(
        frames,
        sun_altitude_deg=75.0,
        cct_k=5500,
        highlight_clip_pct=0.10,
        shadow_clip_pct=0.08,
        light_direction="front",
    )
    assert agg is not None
    assert agg.elevation == "overhead"
    assert agg.hardness == "hard"


def test_light_pro_indoor_returns_none_when_no_signal():
    frames = []
    assert light_pro.aggregate(frames) is None


# ---------------------------------------------------------------------------
# shot_hypothesis
# ---------------------------------------------------------------------------
def test_shot_hypothesis_generates_when_stereo_available():
    frames = [
        _frame(0, 0, [_ld("stair", 1.0, 0.0, -2.0, sid="s1"),
                      _ld("balcony", 1.0, 3.2, -2.0, sid="b1")]),
        _frame(1, 30, [_ld("stair", 1.0, 0.0, -2.0, sid="s1"),
                       _ld("balcony", 1.0, 3.2, -2.0, sid="b1")]),
    ]
    graph = landmark_graph.aggregate(frames)
    hyps = shot_hypothesis.generate(graph)
    assert hyps, "should generate at least one stereo shot hypothesis"
    # The first one should put the subject up and the camera below
    # (largest dh, looking up).
    h = hyps[0]
    assert h.relation in ("subject_above_camera", "camera_above_subject")
    assert -90 <= h.pitch_deg <= 90
    assert h.distance_m >= 0.0
    # rationale_prefix should reference both node ids.
    assert h.subject_node_id in h.rationale_prefix_zh
    assert h.photographer_node_id in h.rationale_prefix_zh


def test_shot_hypothesis_empty_when_flat_scene():
    frames = [
        _frame(0, 0, [_ld("bench", 1.0, 0.0, -2.0),
                      _ld("tree", 2.0, 0.05, -3.0)]),
        _frame(1, 30, [_ld("bench", 1.0, 0.0, -2.0),
                       _ld("tree", 2.0, 0.05, -3.0)]),
    ]
    graph = landmark_graph.aggregate(frames)
    assert shot_hypothesis.generate(graph) == []


# ---------------------------------------------------------------------------
# potential_evaluator
# ---------------------------------------------------------------------------
def test_potential_evaluator_emits_coach_lines():
    frames = [_frame(i, i * 30) for i in range(6)]
    scene = scene_aggregate.aggregate(frames)
    light = light_pro.aggregate(
        frames, sun_altitude_deg=12.0, cct_k=3800,
        highlight_clip_pct=0.005, shadow_clip_pct=0.005,
        light_direction="side",
    )
    ev = potential_evaluator.evaluate(scene, None, light)
    assert ev is not None
    assert ev.coach_lines
    # Internal score must be in [0, 100].
    assert 0.0 <= ev.internal_score <= 100.0
    # Every coach line should carry a recognised emotion.
    for c in ev.coach_lines:
        assert c.emotion in ("calm", "encouraging", "playful", "caution")
        assert c.priority in (1, 2, 3)
        assert c.text_zh


def test_potential_evaluator_handles_minimal_scene():
    """With sparse signals we should still get *some* output, not crash."""
    frames = [_frame(i, i * 30) for i in range(3)]
    scene = scene_aggregate.aggregate(frames)
    ev = potential_evaluator.evaluate(scene, None, None)
    # Either we get back a non-empty eval, or None — both acceptable.
    if ev is not None:
        assert isinstance(ev.coach_lines, list)
