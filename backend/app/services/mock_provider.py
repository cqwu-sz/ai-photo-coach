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
    CaptureQuality,
    Composition,
    CompositionType,
    CriteriaNotes,
    CriteriaScore,
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
    VisionLightHint,
)
from . import camera_apply, camera_params, pose_engine


def _scene_for(meta: CaptureMeta) -> SceneSummary:
    style = " / ".join(meta.style_keywords) if meta.style_keywords else "neutral"
    # Mock vision_light is keyed off scene_mode so the result-page light
    # indicator demos correctly without an LLM round-trip. light_shadow
    # gets a moderate-confidence west-southwest reading; other modes get
    # a soft "unknown" so the recapture hint logic can be exercised.
    if meta.scene_mode.value == "light_shadow":
        vl = VisionLightHint(
            direction_deg=248.0,
            quality="hard",
            confidence=0.7,
            notes="模拟视觉判断：第 5 帧 azimuth 248° 高光最强，地面长影指向 68°。",
        )
    else:
        vl = VisionLightHint(
            direction_deg=None,
            quality="unknown",
            confidence=0.0,
            notes="模拟模式：非光影场景未做视觉光向估算。",
        )
    return SceneSummary(
        type="outdoor_urban" if meta.person_count <= 2 else "outdoor_park",
        lighting=Lighting.golden_hour,
        background_summary=f"模拟场景：{style} 风格，远景为城市天际线，中景有树木遮挡",
        cautions=["逆光下注意人物面部曝光", "背景中广告牌可能干扰，请避开"],
        vision_light=vl,
        # Mock mode always reports a healthy capture; tests that need to
        # exercise the advisory banner inject a custom CaptureQuality
        # via _decide_capture_advisory directly.
        capture_quality=CaptureQuality(
            score=4, issues=[], summary_zh="画面证据充分（模拟模式）", should_retake=False,
        ),
    )


def _shot_for(meta: CaptureMeta, idx: int, lighting: Lighting) -> ShotRecommendation:
    person_count = meta.person_count
    scene_mode = meta.scene_mode.value

    azimuths = [m.azimuth_deg for m in meta.frame_meta]
    base_az = azimuths[len(azimuths) // 2 if azimuths else 0] if azimuths else 0.0
    target_az = (base_az + idx * 60) % 360

    rep_idx = 0
    if meta.frame_meta:
        rep_idx = min(
            range(len(meta.frame_meta)),
            key=lambda i: _az_delta(meta.frame_meta[i].azimuth_deg, target_az),
        )

    # Distance varies by scene mode: closeup pulls in tight, scenery pushes out.
    base_dist = {
        "closeup": 1.0,
        "full_body": 3.0,
        "documentary": 2.5,
        "scenery": 8.0,
        "light_shadow": 2.5,
    }.get(scene_mode, 2.0)
    angle = Angle(
        azimuth_deg=target_az,
        pitch_deg=-5 if idx == 0 else 0,
        distance_m=base_dist + idx * 0.8,
        height_hint=HeightHint.eye_level if idx != 1 else HeightHint.low,
    )

    if scene_mode == "scenery":
        primary = (
            CompositionType.leading_line
            if idx == 0
            else CompositionType.negative_space if idx == 1 else CompositionType.symmetry
        )
    else:
        primary = (
            CompositionType.rule_of_thirds
            if idx == 0
            else CompositionType.leading_line if idx == 1 else CompositionType.symmetry
        )
    composition = Composition(
        primary=primary,
        secondary=["negative_space"] if idx == 0 else [],
        notes="主体置于左三分线，视线方向留出空间" if idx == 0 and scene_mode != "scenery" else None,
    )

    cam = camera_params.synthesize_camera_settings(
        lighting, person_count, scene_mode=scene_mode
    )
    cam.iphone_apply_plan = camera_apply.build_plan(cam)
    pose = pose_engine.fallback_pose(person_count, scene_mode=scene_mode)

    if scene_mode == "scenery":
        coach_lines = [
            "把地平线压到下三分线",
            "蹲下来让前景石头当锚点",
            "等云移到画面左上再按",
        ]
    elif scene_mode == "light_shadow":
        coach_lines = [
            "背对太阳，往前两步看那道光",
            "贴着阴影边缘，让脸的一半在光里",
            "等光柱穿过那扇窗再按",
        ]
    else:
        coach_lines = [
            "来，把那块石头留在你右边，蹲下来等我数三声",
            "往后退一步，让光从你左肩斜下来",
            "你站到三分线，看向远处别看我",
        ]
    rationale = (
        f"我建议你转到 {target_az:.0f}° 方向，距离主体大约 {angle.distance_m:.1f} 米，"
        f"用 {cam.focal_length_mm:.0f}mm {cam.aperture}。{lighting.value} 光线下"
        f"{composition.primary.value} 构图最稳，能把"
        f"{'天空和前景' if scene_mode == 'scenery' else '人和环境'}都收得干净。"
    )

    poses_list = [] if (scene_mode == "scenery" and person_count == 0) else [pose]

    # Mock 7D scores so the UI can demo the panel without a real model.
    # Vary by index so the strongest/weakest axes change between shots.
    # Notes use [rule_id] prefix to mirror the v6 prompt convention; KB
    # ids referenced here exist in the seed batch.
    if idx == 0:
        score = CriteriaScore(
            composition=5, light=4, color=3, depth=4,
            subject_fit=4, background=3, theme=4,
        )
        notes = CriteriaNotes(
            composition="[comp_rule_of_thirds] 主体已在右三分线 → 站位别再后退，保持这个比例",
            light="[light_side_back_rim] 侧光勾出发丝 → 让模特再侧脸 15°，亮区更连贯",
            color="[color_60_30_10] 暖调主导但点缀色弱 → 加一个红色道具入画做 10% 点缀",
            depth="[depth_focal_character] 35mm f/2.8 距离 2.5m → 锁住焦段不变",
            subject_fit="[sub_eyeline_breathing] 左侧留 60% 呼吸感 → 提醒主体视线略偏左",
            background="[bg_subject_separation] 凉亭轮廓还抢一点视线 → 往左半步把它移出框",
            theme="[theme_golden_warmth] 黄昏写真主题对了 → 等再低 5° 抢黄金尾光",
        )
        strong, weak = "composition", "background"
    elif idx == 1:
        score = CriteriaScore(
            composition=4, light=3, color=4, depth=3,
            subject_fit=3, background=4, theme=3,
        )
        notes = CriteriaNotes(
            composition="[comp_symmetry] 对称稳但偏呆 → 让模特微侧身 30° 打破中线",
            light="[light_top_avoid] 顶光偏硬 → 靠近左侧墙体半步借反射光",
            color="[color_complementary] 蓝绿冷调与服装暖色对比强 → 保持这个色对",
            depth="[depth_focal_character] 50mm 略压缩 → 后退半步让前景叶子入画",
            subject_fit="[sub_motion_break] 居中正对缺动势 → 让模特把重心压到一条腿",
            background="[bg_clean] 背景干净不抢戏 → 直接拍，不用调",
            theme="[theme_interaction] 主题弱 → 让模特和环境互动（摸墙/扶帽檐）",
        )
        strong, weak = "color", "subject_fit"
    else:
        score = CriteriaScore(
            composition=3, light=5, color=3, depth=4,
            subject_fit=3, background=4, theme=5,
        )
        notes = CriteriaNotes(
            composition="[comp_negative_space] 留白多重心偏低 → 蹲一点把地平线压到下 1/4",
            light="[light_silhouette] 逆光剪影强 → 曝光锁高光，EV -1.2 保住天空层次",
            color="[color_tonal] 单色剪影依赖天空渐变 → 等再过 5 分钟天色更厚",
            depth="[depth_focal_character] 85mm 压缩天际线 → 保持长焦不变",
            subject_fit="[sub_silhouette_pose] 剪影看姿态 → 让模特张开手臂或抬头",
            background="[bg_clean] 纯天空无干扰 → 直接拍",
            theme="[theme_decisive_moment] 主题极强 → 等他们牵手回头那一瞬按下",
        )
        strong, weak = "theme", "composition"

    iphone_tips = _iphone_tips_for(scene_mode, cam.focal_length_mm, cam.iso, idx)

    return ShotRecommendation(
        id=f"shot_{idx + 1}",
        title=f"{['首选机位', '备选机位', '特殊角度'][idx]}",
        representative_frame_index=rep_idx,
        angle=angle,
        composition=composition,
        camera=cam,
        poses=poses_list,
        rationale=rationale,
        coach_brief=coach_lines[idx % len(coach_lines)],
        confidence=0.75 - idx * 0.1,
        criteria_score=score,
        criteria_notes=notes,
        strongest_axis=strong,
        weakest_axis=weak,
        iphone_tips=iphone_tips,
    )


def _iphone_tips_for(scene_mode: str, focal_mm: float, iso: int, idx: int) -> list[str]:
    """Stable, scene-aware iPhone tips for mock mode. Real LLM responses
    fill ``iphone_tips`` themselves via the prompt; this list only runs
    when mock_provider is used (no model key set / dev mode)."""
    tips: list[str] = []
    if focal_mm >= 50:
        tips.append("切到 2x 长焦镜头拍人像，避免主摄数码裁剪导致细节流失")
    elif focal_mm >= 28:
        tips.append("用主摄 1x 拍，离主体 1.5-2 米，保留环境信息")
    else:
        tips.append("切到 0.5x 超广角端，注意人物不要放在四角避免拉伸")

    if scene_mode == "light_shadow":
        tips.append("打开「保留设置 - 曝光」，按住屏幕主体处下拉一档锁定剪影曝光")
        tips.append("iPhone 主光圈 f/1.78，逆光剪影时让快门 1/500+ 防过曝")
    elif scene_mode == "scenery":
        tips.append("开 RAW 模式（设置 - 相机 - 格式 - ProRAW），后期空间更大")
        tips.append("iPhone 物理光圈固定，要更大景深可后退一两步增加超焦距")
    elif scene_mode == "closeup":
        tips.append("iPhone 物理光圈 f/1.78 已是最大，想加强虚化用人像模式或贴近主体")
        tips.append("ISO 200+ 时关闭夜间模式，避免长曝模糊")
    else:
        tips.append("iPhone 主光圈 f/1.78 固定，要 f/4 深景深建议拍后用人像模式调虚化半径")
        if iso >= 800:
            tips.append("ISO 偏高建议靠近主体或换大光圈环境，自然降到 200 以内")
        else:
            tips.append("白平衡偏暖色场景，拍前点屏幕主体上滑 EV 微调一点")
    return tips[: 3]


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
