"""Deterministic mock implementation for `MOCK_MODE=true`.

Lets the iOS team develop and test against a real network endpoint without
needing a Gemini key. The output it returns is schema-valid and varies a
little based on `person_count` so the iOS UI can be exercised.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import (
    Angle,
    AnalyzeResponse,
    CameraSettings,
    CaptureMeta,
    Composition,
    CompositionType,
    DeviceHints,
    HeightHint,
    IphoneLens,
    Layout,
    Lighting,
    PersonPose,
    PoseSuggestion,
    SceneSummary,
    ShotRecommendation,
    StyleInspiration,
)
from . import camera_params, pose_engine


def _scene_for(meta: CaptureMeta) -> SceneSummary:
    style = " / ".join(meta.style_keywords) if meta.style_keywords else "neutral"
    return SceneSummary(
        type="outdoor_urban" if meta.person_count <= 2 else "outdoor_park",
        lighting=Lighting.golden_hour,
        background_summary=f"模拟场景：{style} 风格，远景为城市天际线，中景有树木遮挡",
        cautions=["逆光下注意人物面部曝光", "背景中广告牌可能干扰，请避开"],
    )


def _shot_for(meta: CaptureMeta, idx: int, lighting: Lighting) -> ShotRecommendation:
    person_count = meta.person_count

    azimuths = [m.azimuth_deg for m in meta.frame_meta]
    base_az = azimuths[len(azimuths) // 2 if azimuths else 0] if azimuths else 0.0
    target_az = (base_az + idx * 60) % 360

    # Pick the frame whose azimuth is closest to the target so the UI can
    # use it as a backdrop for this shot's mock-up.
    rep_idx = 0
    if meta.frame_meta:
        rep_idx = min(
            range(len(meta.frame_meta)),
            key=lambda i: _az_delta(meta.frame_meta[i].azimuth_deg, target_az),
        )

    angle = Angle(
        azimuth_deg=target_az,
        pitch_deg=-5 if idx == 0 else 0,
        distance_m=2.0 + idx * 0.8,
        height_hint=HeightHint.eye_level if idx != 1 else HeightHint.low,
    )

    composition = Composition(
        primary=(
            CompositionType.rule_of_thirds
            if idx == 0
            else CompositionType.leading_line if idx == 1 else CompositionType.symmetry
        ),
        secondary=["negative_space"] if idx == 0 else [],
        notes="主体置于左三分线，视线方向留出空间" if idx == 0 else None,
    )

    cam = camera_params.synthesize_camera_settings(lighting, person_count)
    pose = pose_engine.fallback_pose(person_count)

    coach_lines = [
        "来，把那块石头留在你右边，蹲下来等我数三声",
        "往后退一步，让光从你左肩斜下来",
        "你站到三分线，看向远处别看我",
    ]
    rationale = (
        f"我建议你转到 {target_az:.0f}° 方向，距离主体大约 {angle.distance_m:.1f} 米，"
        f"用 {cam.focal_length_mm:.0f}mm {cam.aperture}。{lighting.value} 光线下"
        f"{composition.primary.value} 构图最稳，能把人和环境都收得干净。"
    )

    return ShotRecommendation(
        id=f"shot_{idx + 1}",
        title=f"{['首选机位', '备选机位', '特殊角度'][idx]}",
        representative_frame_index=rep_idx,
        angle=angle,
        composition=composition,
        camera=cam,
        poses=[pose],
        rationale=rationale,
        coach_brief=coach_lines[idx % len(coach_lines)],
        confidence=0.75 - idx * 0.1,
    )


def _az_delta(a: float, b: float) -> float:
    d = abs((a - b + 540) % 360 - 180)
    return d


def make_mock_response(meta: CaptureMeta) -> AnalyzeResponse:
    scene = _scene_for(meta)
    shots = [_shot_for(meta, i, scene.lighting) for i in range(2)]
    return AnalyzeResponse(
        scene=scene,
        shots=shots,
        style_inspiration=StyleInspiration(
            used_count=0,
            summary="（mock 模式）暂未使用真实参考图。",
            inherited_traits=[],
        ),
        generated_at=datetime.now(timezone.utc),
        model="mock-1",
        debug={"mode": "mock", "frames_received_meta": [m.index for m in meta.frame_meta]},
    )
