"""Per-scene-mode prompt + camera + pose specialisations."""
from __future__ import annotations

import pytest

from app.models import (
    CaptureMeta,
    CompositionType,
    FrameMeta,
    Lighting,
    SceneMode,
)
from app.services import camera_params, pose_engine
from app.services.mock_provider import make_mock_response
from app.services.prompts import build_user_prompt


def _meta(scene_mode: SceneMode, person_count: int) -> CaptureMeta:
    return CaptureMeta(
        person_count=person_count,
        scene_mode=scene_mode,
        frame_meta=[FrameMeta(index=i, azimuth_deg=i * 45) for i in range(8)],
    )


@pytest.mark.parametrize(
    "scene_mode,must_contain",
    [
        (SceneMode.portrait, "人像"),
        (SceneMode.closeup, "特写"),
        (SceneMode.full_body, "全身"),
        (SceneMode.documentary, "人文"),
    ],
)
def test_prompt_includes_scene_branch(scene_mode, must_contain):
    meta = _meta(scene_mode, 1)
    prompt = build_user_prompt(
        meta=meta,
        pose_library_summary="",
        camera_kb_summary="",
        has_references=False,
        scene_mode=scene_mode.value,
    )
    assert must_contain in prompt


def test_prompt_scenery_with_zero_people_demands_empty_poses():
    meta = _meta(SceneMode.scenery, 0)
    prompt = build_user_prompt(
        meta=meta,
        pose_library_summary="",
        camera_kb_summary="",
        has_references=False,
        scene_mode="scenery",
    )
    assert "风景" in prompt
    assert "poses" in prompt and "[]" in prompt


def test_pose_engine_scenery_zero_returns_empty_persons():
    pose = pose_engine.fallback_pose(0, scene_mode="scenery")
    assert pose.person_count == 0
    assert pose.persons == []


def test_camera_params_closeup_pulls_long_lens():
    cam = camera_params.synthesize_camera_settings(
        Lighting.golden_hour, person_count=1, scene_mode="closeup"
    )
    assert cam.focal_length_mm >= 85


def test_camera_params_scenery_uses_small_aperture():
    cam = camera_params.synthesize_camera_settings(
        Lighting.overcast, person_count=0, scene_mode="scenery"
    )
    # Scenery preset is f/8 with focal length in 14-35 zone.
    assert cam.aperture == "f/8"
    assert 14 <= cam.focal_length_mm <= 35


def test_camera_params_full_body_uses_standard_lens():
    cam = camera_params.synthesize_camera_settings(
        Lighting.golden_hour, person_count=2, scene_mode="full_body"
    )
    assert 35 <= cam.focal_length_mm <= 50


def test_capture_meta_rejects_zero_people_for_non_scenery():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CaptureMeta(
            person_count=0,
            scene_mode=SceneMode.portrait,
            frame_meta=[FrameMeta(index=i, azimuth_deg=0) for i in range(8)],
        )


def test_capture_meta_allows_zero_people_for_scenery():
    meta = CaptureMeta(
        person_count=0,
        scene_mode=SceneMode.scenery,
        frame_meta=[FrameMeta(index=i, azimuth_deg=0) for i in range(8)],
    )
    assert meta.person_count == 0


def test_mock_response_scenery_has_empty_poses():
    meta = _meta(SceneMode.scenery, 0)
    resp = make_mock_response(meta)
    for shot in resp.shots:
        assert shot.poses == []
        # Scenery composition vocabulary
        assert shot.composition.primary in (
            CompositionType.leading_line,
            CompositionType.negative_space,
            CompositionType.symmetry,
        )


def test_mock_response_closeup_keeps_poses_and_uses_long_lens():
    meta = _meta(SceneMode.closeup, 1)
    resp = make_mock_response(meta)
    for shot in resp.shots:
        assert shot.poses, "closeup should still have at least one pose"
        assert shot.camera.focal_length_mm >= 85
