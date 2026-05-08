"""Tests for capture-quality advisory (Phase 1) and 7-axis criteria
(Phase 2.3) — both shipped together because the LLM emits them in one
response object.

We exercise both:
  * The deterministic post-pass `_enforce_capture_advisory` that trims
    the shot list when the LLM judges the source video unusable.
  * The `_compute_overall_score` weighted ranking score so we can sort
    on the client without re-prompting the model.
  * Old 4-axis criteria_score payloads must still decode (subject_fit /
    background / theme default to 3) — important because cached responses
    in the wild were produced before the v6 schema.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models import (
    AnalyzeResponse,
    Angle,
    CameraSettings,
    CaptureQuality,
    CaptureQualityIssue,
    Composition,
    CompositionType,
    CriteriaScore,
    EnvironmentSnapshot,
    Layout,
    Lighting,
    PoseSuggestion,
    SceneSummary,
    ShotRecommendation,
    StyleInspiration,
)
from app.services.analyze_service import AnalyzeService


# ────────── helpers ──────────


def _make_shot(**overrides) -> ShotRecommendation:
    base = dict(
        id="s1",
        title="测试机位",
        angle=Angle(azimuth_deg=45.0, pitch_deg=-5.0, distance_m=2.5),
        composition=Composition(primary=CompositionType.rule_of_thirds),
        camera=CameraSettings(
            focal_length_mm=50,
            aperture="f/2.8",
            shutter="1/250",
            iso=200,
        ),
        poses=[PoseSuggestion(person_count=1, layout=Layout.single)],
        rationale="测试",
        confidence=0.8,
    )
    base.update(overrides)
    return ShotRecommendation(**base)


def _make_response(shots=None, scene=None, env=None) -> AnalyzeResponse:
    if shots is None:
        shots = [
            _make_shot(id="s1", title="a"),
            _make_shot(id="s2", title="b"),
            _make_shot(id="s3", title="c"),
        ]
    return AnalyzeResponse(
        scene=scene or _make_scene(),
        environment=env,
        shots=shots,
        style_inspiration=StyleInspiration(),
    )


def _make_scene(**overrides) -> SceneSummary:
    base = dict(
        type="urban_street",
        lighting=Lighting.golden_hour,
        background_summary="测试背景",
    )
    base.update(overrides)
    return SceneSummary(**base)


# ────────── _enforce_capture_advisory ──────────


def test_advisory_no_quality_keeps_all_shots():
    resp = _make_response()
    AnalyzeService._enforce_capture_advisory(resp)
    assert len(resp.shots) == 3


def test_advisory_high_quality_keeps_all_shots():
    resp = _make_response(
        scene=_make_scene(
            capture_quality=CaptureQuality(
                score=4, issues=[], summary_zh="证据充分", should_retake=False,
            ),
        )
    )
    AnalyzeService._enforce_capture_advisory(resp)
    assert len(resp.shots) == 3


def test_advisory_low_quality_with_retake_trims_to_one():
    resp = _make_response(
        scene=_make_scene(
            lighting=Lighting.overcast,
            capture_quality=CaptureQuality(
                score=2,
                issues=[CaptureQualityIssue.cluttered_bg],
                summary_zh="背景过于杂乱",
                should_retake=True,
            ),
        )
    )
    AnalyzeService._enforce_capture_advisory(resp)
    assert len(resp.shots) == 1, "低质 + retake 应只保留一个保底机位"


def test_advisory_low_score_without_retake_keeps_all():
    """Score <= 2 但 LLM 没要求 retake — 不该擅自裁剪用户预期."""
    resp = _make_response(
        scene=_make_scene(
            lighting=Lighting.overcast,
            capture_quality=CaptureQuality(
                score=2,
                issues=[CaptureQualityIssue.too_dark],
                summary_zh="偏暗但仍可用",
                should_retake=False,
            ),
        )
    )
    AnalyzeService._enforce_capture_advisory(resp)
    assert len(resp.shots) == 3


# ────────── _compute_overall_score ──────────


def test_overall_score_uses_seven_dim_avg():
    shot = _make_shot(
        criteria_score=CriteriaScore(
            composition=5, light=5, color=4, depth=4,
            subject_fit=5, background=4, theme=5,
        ),
        confidence=0.9,
    )
    score = AnalyzeService._compute_overall_score(shot, env=None)
    assert 3.0 <= score <= 5.0
    # crit_avg = 32/7 ≈ 4.57; conf = 4.5;
    # weighted = 0.5*4.57 + 0.3*4.5 + 0.2*0 ≈ 3.636
    assert abs(score - 3.64) < 0.05


def test_overall_score_no_criteria_uses_confidence():
    shot = _make_shot(criteria_score=None, confidence=0.6)
    score = AnalyzeService._compute_overall_score(shot, env=None)
    # crit_avg = 0.6*5 = 3; conf = 3; weighted = 0.5*3 + 0.3*3 = 2.4
    assert abs(score - 2.4) < 0.01


def test_overall_score_clamped_to_five():
    shot = _make_shot(
        criteria_score=CriteriaScore(
            composition=5, light=5, color=5, depth=5,
            subject_fit=5, background=5, theme=5,
        ),
        confidence=1.0,
    )
    # No env — but full marks shouldn't push past cap.
    score = AnalyzeService._compute_overall_score(shot, env=None)
    assert score <= 5.0


# ────────── 7D <-> 4D backward compat ──────────


def test_old_four_dim_criteria_decodes_with_defaults():
    """Old API responses had only 4 axes. Pydantic must default the new
    three to 3 (neutral) so we don't blow up on cached payloads."""
    payload = {
        "composition": 4,
        "light": 5,
        "color": 4,
        "depth": 3,
    }
    score = CriteriaScore.model_validate(payload)
    assert score.composition == 4
    assert score.subject_fit == 3
    assert score.background == 3
    assert score.theme == 3


def test_seven_dim_criteria_round_trips():
    payload = {
        "composition": 5, "light": 4, "color": 4, "depth": 3,
        "subject_fit": 5, "background": 4, "theme": 5,
    }
    score = CriteriaScore.model_validate(payload)
    assert score.subject_fit == 5
    assert score.theme == 5
    dumped = score.model_dump()
    for k in payload:
        assert dumped[k] == payload[k]


def test_criteria_score_values_clamped_by_schema():
    """Each axis must be 1..5; out-of-range should ValidationError."""
    with pytest.raises(ValidationError):
        CriteriaScore(composition=6, light=3, color=3, depth=3)
    with pytest.raises(ValidationError):
        CriteriaScore(composition=3, light=0, color=3, depth=3)


# ────────── CaptureQuality serialization ──────────


def test_capture_quality_serialises_to_camelish():
    q = CaptureQuality(
        score=3,
        issues=[CaptureQualityIssue.too_dark, CaptureQualityIssue.blurry],
        summary_zh="环境偏暗",
        should_retake=True,
    )
    d = q.model_dump(mode="json")
    assert d["score"] == 3
    assert "too_dark" in d["issues"]
    assert d["should_retake"] is True


def test_scene_summary_accepts_no_capture_quality():
    """capture_quality is optional — old responses still validate."""
    s = _make_scene()
    assert s.capture_quality is None
