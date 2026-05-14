"""Out-the-shutter potential evaluator — turns the structured scene
facts into (a) an internal 0-100 score we use for ranking / A/B, and
(b) the natural-language coach short sentences with emotion tags
that ``VoiceCoach`` (iOS) reads aloud.

Why no user-visible score?
--------------------------
The product spec is explicit: numbers feel like grading, the user said
"这位置只有 62 分" is psychologically punishing. So we keep the score
private and only surface natural-language coaching such as:

    『这角度挺有故事的，但你左边那棵树挡住了主光，往前两步会更通透』

Each cue carries an emotion tag (calm | encouraging | playful |
caution) so the TTS layer picks the right voice profile.

What goes into the score
------------------------
Five axes, normalised so each contributes its weight to a 0..100 total:

    light       30  — main_light elevation + hardness + clipping
    background  25  — busiest-vs-cleanest contrast, foreground richness
    subject     20  — pose/face presence + framing room
    layering    15  — depth_layers near% + landmark graph stereo flag
    uniqueness  10  — bonus for landmark count / stereo opportunity

Each axis returns a ``(0..1, why_dropped_zh)`` pair. ``why_dropped_zh``
is the bullet list that becomes the user-visible coaching after we drop
the numeric framing.

This module reads from ``SceneAggregate`` (already built by
``scene_aggregate.aggregate``) plus ``LandmarkGraph`` and
``LightingProAggregate``. It performs no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from . import light_pro as light_pro_service
from . import scene_aggregate as scene_aggregate_service
from . import landmark_graph as landmark_graph_service


Emotion = Literal["calm", "encouraging", "playful", "caution"]


@dataclass(frozen=True, slots=True)
class CoachLine:
    """One natural-language coaching cue with prosody hints."""
    text_zh: str
    emotion: Emotion
    priority: int     # 1 = primary (must read), 2 = supplementary

    def to_dict(self) -> dict:
        return {
            "text_zh": self.text_zh,
            "emotion": self.emotion,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class PotentialBreakdown:
    """Per-axis score + free-form notes that drove the score."""
    light: float
    background: float
    subject: float
    layering: float
    uniqueness: float
    light_notes_zh: list[str]
    background_notes_zh: list[str]
    subject_notes_zh: list[str]
    layering_notes_zh: list[str]
    uniqueness_notes_zh: list[str]

    def weighted_total(self) -> float:
        return round(
            self.light * 30
            + self.background * 25
            + self.subject * 20
            + self.layering * 15
            + self.uniqueness * 10,
            1,
        )


@dataclass(frozen=True, slots=True)
class PotentialEvaluation:
    breakdown: PotentialBreakdown
    coach_lines: list[CoachLine]

    @property
    def internal_score(self) -> float:
        """0..100 — never shown to the user, used internally only."""
        return self.breakdown.weighted_total()


# ---------------------------------------------------------------------------
# Axis evaluators
# ---------------------------------------------------------------------------
def _eval_light(
    scene: Optional[scene_aggregate_service.SceneAggregate],
    light_pro: Optional[light_pro_service.LightingProAggregate],
) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.6
    if light_pro is not None:
        # Hardness penalty when extreme.
        if light_pro.hardness == "hard" and light_pro.elevation == "overhead":
            score -= 0.30
            notes.append("正顶硬光会让眼窝发黑")
        if light_pro.hardness == "soft":
            score += 0.10
        if light_pro.elevation == "golden":
            score += 0.20
            notes.append("黄金时段的低位光最讨脸")
        if light_pro.elevation == "below_horizon":
            score -= 0.20
            notes.append("天色已暗，光线偏弱，需要稳定支撑")
        if light_pro.chiaroscuro_level == "extreme":
            score -= 0.10
            notes.append("反差太大，要么剪影要么补光")
    if scene is not None:
        if scene.highlight_clip_pct and scene.highlight_clip_pct > 0.05:
            score -= 0.10
            notes.append(f"高光已经溢出 {scene.highlight_clip_pct*100:.0f}%")
        if scene.shadow_clip_pct and scene.shadow_clip_pct > 0.05:
            score -= 0.10
            notes.append(f"暗部死黑 {scene.shadow_clip_pct*100:.0f}%")
        if scene.luma_contrast_ratio >= 1.6:
            score += 0.05
            notes.append("方向光明显，可以做侧光/逆光")
    return (max(0.0, min(1.0, score)), notes)


def _eval_background(
    scene: Optional[scene_aggregate_service.SceneAggregate],
) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.6
    if scene is None:
        return (score, notes)
    if scene.busiest_azimuth is not None and scene.cleanest_azimuth is not None:
        if scene.busiest_azimuth != scene.cleanest_azimuth:
            score += 0.15
        if scene.luma_contrast_ratio >= 1.4:
            score += 0.05
    if scene.foreground_facts:
        score += 0.15
        notes.append(f"前景候选 {len(scene.foreground_facts)} 个，可造层次")
    else:
        score -= 0.10
        notes.append("前景偏空，需要主动走到植物/栏杆边")
    if scene.dominant_quadrant == "center":
        notes.append("视觉重心居中，建议主体偏到三分点")
    return (max(0.0, min(1.0, score)), notes)


def _eval_subject(
    scene: Optional[scene_aggregate_service.SceneAggregate],
) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.6
    if scene is None:
        return (score, notes)
    if scene.largest_person_azimuth is not None:
        score += 0.15
    else:
        score -= 0.10
        notes.append("还没找到主体的最佳方位，让主体先站定")
    if scene.pose_facts_zh:
        # Each pose issue knocks 0.05 — they are correctable nudges.
        score -= 0.05 * len(scene.pose_facts_zh)
        notes.extend(scene.pose_facts_zh[:2])
    if scene.tilt_advice_zh and ("蹲" in scene.tilt_advice_zh or "举高" in scene.tilt_advice_zh):
        score -= 0.05
        notes.append("机位高度需要调整")
    return (max(0.0, min(1.0, score)), notes)


def _eval_layering(
    scene: Optional[scene_aggregate_service.SceneAggregate],
    graph: Optional[landmark_graph_service.LandmarkGraph],
) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.5
    if scene is not None and scene.near_depth_pct is not None:
        if scene.near_depth_pct >= 0.05:
            score += 0.20
            notes.append("有可用的前景层")
        else:
            score -= 0.10
            notes.append("前景层几乎为空，没层次感")
    if graph is not None and graph.has_stereo_opportunity:
        score += 0.20
        notes.append("存在立体高度差，可以玩多机位")
    return (max(0.0, min(1.0, score)), notes)


def _eval_uniqueness(
    graph: Optional[landmark_graph_service.LandmarkGraph],
) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0.5
    if graph is None or not graph.nodes:
        return (score, notes)
    if len(graph.nodes) >= 4:
        score += 0.20
        notes.append(f"场景元素丰富（{len(graph.nodes)} 个地标）")
    if graph.has_stereo_opportunity:
        score += 0.20
        notes.append("立体层次能拍出与众不同的角度")
    return (max(0.0, min(1.0, score)), notes)


# ---------------------------------------------------------------------------
# Coach line generation
# ---------------------------------------------------------------------------
def _build_coach_lines(b: PotentialBreakdown) -> list[CoachLine]:
    """Convert the lowest-scoring axis into a caring nudge, and pad
    with one or two encouraging observations from the strongest axes.

    Tone calibration:
      - "扣分项 → 改进方向"对应 caution（不刻意亲近，但带建议）
      - 强项的 acknowledgement 对应 encouraging
      - 中性事实陈述对应 calm
    """
    lines: list[CoachLine] = []

    axes = [
        ("light",       b.light,       b.light_notes_zh,       30),
        ("background",  b.background,  b.background_notes_zh,  25),
        ("subject",     b.subject,     b.subject_notes_zh,     20),
        ("layering",    b.layering,    b.layering_notes_zh,    15),
        ("uniqueness",  b.uniqueness,  b.uniqueness_notes_zh,  10),
    ]
    # Sort by weighted contribution to the deficit (lower score & higher
    # weight = bigger ROI to mention first).
    deficits = sorted(
        axes,
        key=lambda t: (1 - t[1]) * t[3],
        reverse=True,
    )

    # Top deficit → primary caution + actionable hint.
    name, score, notes, _w = deficits[0]
    if score < 0.6 and notes:
        primary = _to_action_zh(name, notes[0])
        lines.append(CoachLine(text_zh=primary, emotion="caution", priority=1))
    elif score >= 0.8:
        lines.append(CoachLine(
            text_zh=_strength_compliment_zh(name),
            emotion="encouraging",
            priority=1,
        ))
    else:
        lines.append(CoachLine(
            text_zh="环境基础不错，先让主体站好，我们慢慢调",
            emotion="calm",
            priority=1,
        ))

    # Second-tier hint when there's still meaningful improvement room.
    if len(deficits) > 1:
        name2, score2, notes2, _w2 = deficits[1]
        if score2 < 0.55 and notes2:
            lines.append(CoachLine(
                text_zh=_to_action_zh(name2, notes2[0]),
                emotion="caution",
                priority=2,
            ))

    # One strength acknowledgement when we have one.
    strongest = max(axes, key=lambda t: t[1])
    if strongest[1] >= 0.8 and strongest is not deficits[0]:
        lines.append(CoachLine(
            text_zh=_strength_compliment_zh(strongest[0]),
            emotion="encouraging",
            priority=2,
        ))

    return lines


_AXIS_NAME_ZH = {
    "light":      "光线",
    "background": "背景",
    "subject":    "主体",
    "layering":   "层次",
    "uniqueness": "独特性",
}


def _to_action_zh(axis: str, raw_note: str) -> str:
    """Turn a raw deficit note into an actionable, kind sentence."""
    # Light cues — convert tech terms into action verbs.
    if "硬光" in raw_note or "正顶" in raw_note:
        return f"现在是硬光，{raw_note[:8]}…试试往遮阴下半步，脸会立刻通透"
    if "前景偏空" in raw_note:
        return "前景太空了，往植物或栏杆边挪两步，把它放到画面下角能造层次"
    if "前景层几乎为空" in raw_note:
        return "这一面看出去比较开阔，先别急着拍，我们绕一下找个有前景的方向"
    if "暗" in raw_note or "夜间" in raw_note:
        return "光线偏弱，把手机贴稳一点，或者拉主体往灯下走几步"
    if "暗部死黑" in raw_note or "高光已经溢出" in raw_note:
        return f"现在反差有点大（{raw_note}），等会儿拍可能要做点曝光补偿"
    if "找到主体" in raw_note:
        return "先让主体定个位置，我再给你机位建议"
    if "重心居中" in raw_note:
        return "主体太居中了，往左或往右挪一点，构图会更有呼吸"
    if "高度需要调整" in raw_note:
        return "你蹲低一点或举高一点，主体的比例会更舒服"
    # Generic fallback
    return f"{_AXIS_NAME_ZH.get(axis, axis)}这边：{raw_note}"


def _strength_compliment_zh(axis: str) -> str:
    return {
        "light":      "光线状态很对，趁这十几分钟把心仪机位拍掉",
        "background": "背景很干净，主体放对位置就出片",
        "subject":    "主体的状态在线，可以多试几个表情",
        "layering":   "场景层次感不错，可以试试前景虚化",
        "uniqueness": "这个环境元素挺特别，能拍出辨识度",
    }.get(axis, "整体感觉很好，放松拍就行")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------
def evaluate(
    scene: Optional[scene_aggregate_service.SceneAggregate],
    graph: Optional[landmark_graph_service.LandmarkGraph],
    light_pro: Optional[light_pro_service.LightingProAggregate],
) -> Optional[PotentialEvaluation]:
    """Return a ``PotentialEvaluation``, or ``None`` if every input is empty."""
    if scene is None and graph is None and light_pro is None:
        return None
    l, ln = _eval_light(scene, light_pro)
    b, bn = _eval_background(scene)
    s, sn = _eval_subject(scene)
    la, lan = _eval_layering(scene, graph)
    u, un = _eval_uniqueness(graph)
    breakdown = PotentialBreakdown(
        light=l, background=b, subject=s, layering=la, uniqueness=u,
        light_notes_zh=ln, background_notes_zh=bn, subject_notes_zh=sn,
        layering_notes_zh=lan, uniqueness_notes_zh=un,
    )
    coach_lines = _build_coach_lines(breakdown)
    return PotentialEvaluation(breakdown=breakdown, coach_lines=coach_lines)


def to_prompt_block(ev: Optional[PotentialEvaluation]) -> str:
    """Render the coaching cues for the LLM as part of the prompt.

    We deliberately omit the numeric breakdown — the LLM doesn't need
    a score either, it needs to know which axes are weak and how the
    coach voice is about to address them so the rationale aligns.
    """
    if ev is None:
        return ""
    lines = ["── 现场教练已经准备好对用户说的话（声音侧请勿改动用词） ──"]
    for c in ev.coach_lines:
        lines.append(f"  · [{c.emotion}/p{c.priority}] {c.text_zh}")
    lines.append(
        "  POTENTIAL DOCTRINE：以上是基于客户端事实自动生成的现场教练台词。"
        "你产出的 rationale 必须与这些话术呼应（同一个改进方向用同一种说法），"
        "不要写出冲突的建议（例如教练说『往遮阴下挪』而你 rationale 让用户『走到正光下』）。"
    )
    return "\n".join(lines)
