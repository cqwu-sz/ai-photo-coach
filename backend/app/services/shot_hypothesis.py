"""Stereo shot hypothesis generator — searches the landmark graph for
viable (subject_landmark, photographer_landmark, relation) triples and
formats them as prompt-ready candidate shots.

This is the heart of the "二楼阳台模特 + 楼下机位仰拍" plan that
``scene_aggregate``-alone cannot express. We don't replace the LLM
shot generator — we *prime* it with concrete, geometrically-valid
hypotheses extracted from the 3D graph, then let the LLM choose which
to use and refine. That way the LLM still controls poses, framing
intent, and rationale text, but it can no longer hallucinate
""模特站在一个不存在的高度上"".

Search space
------------
For each ``is_above`` edge ``(low → high)`` in the graph, generate up
to 3 hypotheses by varying the **photographer landmark**:

    Hypothesis 1: subject at HIGH, photographer at LOW (look up, classic
                  hero shot — "model on balcony, you below")
    Hypothesis 2: subject at HIGH, photographer at a near LOW node at
                  the same azimuth (centered framing with foreground)
    Hypothesis 3: subject at LOW, photographer at HIGH (look down, GoT-
                  raven-cam — works for dramatic detail shots)

Each hypothesis returns ``ShotHypothesis`` with:
  - subject_node_id / photographer_node_id
  - distance, azimuth, pitch (computed geometrically)
  - recommended ``height_hint`` enum
  - one-line rationale prefix the LLM can adopt

The module is deterministic: same graph → same hypotheses → same order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

from . import landmark_graph as landmark_graph_service


HeightHintBucket = Literal["low", "eye_level", "high", "overhead"]


@dataclass(frozen=True, slots=True)
class ShotHypothesis:
    """One geometrically-valid stereo shot candidate."""
    hypothesis_id: str
    subject_node_id: str
    photographer_node_id: str
    relation: str               # "subject_above_camera" | "camera_above_subject" | ...
    azimuth_deg: float
    pitch_deg: float
    distance_m: float
    height_hint: HeightHintBucket
    rationale_prefix_zh: str

    def to_dict(self) -> dict:
        return {
            "hypothesis_id":         self.hypothesis_id,
            "subject_node_id":       self.subject_node_id,
            "photographer_node_id":  self.photographer_node_id,
            "relation":              self.relation,
            "azimuth_deg":           self.azimuth_deg,
            "pitch_deg":             self.pitch_deg,
            "distance_m":            self.distance_m,
            "height_hint":           self.height_hint,
            "rationale_prefix_zh":   self.rationale_prefix_zh,
        }


def _node_by_id(graph: landmark_graph_service.LandmarkGraph, node_id: str
                 ) -> Optional[landmark_graph_service.LandmarkNode]:
    for n in graph.nodes:
        if n.node_id == node_id:
            return n
    return None


def _pitch_for_height_diff(dh: float, horizontal_d: float) -> float:
    """Pitch in degrees for camera at one node looking at another.
    Positive pitch = look down; negative = look up. Matches the
    convention used elsewhere in the codebase."""
    if horizontal_d <= 0.01:
        # Subject is directly above/below — extreme case, clamp.
        return 80.0 if dh > 0 else -80.0
    return round(math.degrees(math.atan2(dh, horizontal_d)), 1)


def _height_hint_for_pitch(pitch_deg: float) -> HeightHintBucket:
    """Map pitch to the HeightHint enum the LLM consumes."""
    if pitch_deg < -25:   return "low"          # crouched, looking up
    if pitch_deg > 25:    return "high"         # standing, looking down
    if pitch_deg > 45:    return "overhead"     # bird's-eye-ish
    return "eye_level"


def _distance_between(a: landmark_graph_service.LandmarkNode,
                       b: landmark_graph_service.LandmarkNode) -> float:
    dx = a.world_xyz[0] - b.world_xyz[0]
    dz = a.world_xyz[2] - b.world_xyz[2]
    return math.sqrt(dx * dx + dz * dz)


def _azimuth_from_to(src: landmark_graph_service.LandmarkNode,
                      dst: landmark_graph_service.LandmarkNode) -> float:
    """Azimuth (0..360, N=0, clockwise) from src towards dst."""
    dx = dst.world_xyz[0] - src.world_xyz[0]
    dz = dst.world_xyz[2] - src.world_xyz[2]
    return round(math.degrees(math.atan2(dx, -dz)) % 360, 1)


def _hypothesis_subject_above(
    low: landmark_graph_service.LandmarkNode,
    high: landmark_graph_service.LandmarkNode,
) -> ShotHypothesis:
    """Photographer at LOW, subject at HIGH — looking up."""
    horizontal = _distance_between(low, high)
    if high.height_above_ground_m is None or low.height_above_ground_m is None:
        dh = 0.0
    else:
        dh = high.height_above_ground_m - low.height_above_ground_m
    pitch = _pitch_for_height_diff(dh, horizontal)  # positive dh → look up → negative pitch
    pitch = -pitch
    az = _azimuth_from_to(low, high)
    return ShotHypothesis(
        hypothesis_id=f"sh_{low.node_id}_to_{high.node_id}",
        subject_node_id=high.node_id,
        photographer_node_id=low.node_id,
        relation="subject_above_camera",
        azimuth_deg=az,
        pitch_deg=pitch,
        distance_m=round(horizontal, 2),
        height_hint=_height_hint_for_pitch(pitch),
        rationale_prefix_zh=(
            f"让主体上到 {high.node_id}（{high.label}，比地面高 "
            f"{high.height_above_ground_m:+.1f}m），你蹲到 {low.node_id} 的位置仰拍："
        ),
    )


def _hypothesis_camera_above(
    high: landmark_graph_service.LandmarkNode,
    low: landmark_graph_service.LandmarkNode,
) -> ShotHypothesis:
    """Photographer at HIGH, subject at LOW — looking down."""
    horizontal = _distance_between(high, low)
    if high.height_above_ground_m is None or low.height_above_ground_m is None:
        dh = 0.0
    else:
        dh = high.height_above_ground_m - low.height_above_ground_m
    pitch = _pitch_for_height_diff(dh, horizontal)  # high → low → positive pitch
    az = _azimuth_from_to(high, low)
    return ShotHypothesis(
        hypothesis_id=f"sh_{high.node_id}_down_to_{low.node_id}",
        subject_node_id=low.node_id,
        photographer_node_id=high.node_id,
        relation="camera_above_subject",
        azimuth_deg=az,
        pitch_deg=pitch,
        distance_m=round(horizontal, 2),
        height_hint=_height_hint_for_pitch(pitch),
        rationale_prefix_zh=(
            f"你站上去 {high.node_id}（{high.label}），让主体留在 "
            f"{low.node_id} 附近，俯拍："
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate(
    graph: Optional[landmark_graph_service.LandmarkGraph],
    *,
    max_per_pair: int = 2,
    max_total: int = 6,
) -> list[ShotHypothesis]:
    """Generate up to ``max_total`` shot hypotheses from the landmark
    graph. Returns ``[]`` when the graph lacks stereo opportunities.

    Pair ordering: largest absolute Δh first (most dramatic stereo
    shots first), then by closer distance (less walking for the user).
    """
    if graph is None or not graph.has_stereo_opportunity:
        return []
    pairs = []
    nodes = list(graph.nodes)
    for i, a in enumerate(nodes):
        if a.height_above_ground_m is None or a.horizontal_distance_m > 12:
            continue
        for b in nodes[i + 1:]:
            if b.height_above_ground_m is None or b.horizontal_distance_m > 12:
                continue
            dh = b.height_above_ground_m - a.height_above_ground_m
            if abs(dh) < 0.3:
                continue
            pairs.append((a, b, dh))
    pairs.sort(key=lambda t: (-abs(t[2]), _distance_between(t[0], t[1])))

    out: list[ShotHypothesis] = []
    for a, b, dh in pairs:
        added = 0
        if dh > 0:
            # b is higher — subject up, camera down
            out.append(_hypothesis_subject_above(a, b))
            added += 1
            if max_per_pair >= 2:
                out.append(_hypothesis_camera_above(b, a))
                added += 1
        else:
            out.append(_hypothesis_subject_above(b, a))
            added += 1
            if max_per_pair >= 2:
                out.append(_hypothesis_camera_above(a, b))
                added += 1
        if len(out) >= max_total:
            break
    return out[:max_total]


def to_prompt_block(hyps: list[ShotHypothesis]) -> str:
    """Render the SHOT HYPOTHESES block. Empty when ``hyps`` is empty."""
    if not hyps:
        return ""
    lines = [
        f"── SHOT HYPOTHESES（基于地标图谱的 {len(hyps)} 个立体机位候选） ──",
        "  你被强烈鼓励在 ``shots`` 中至少包含一个利用下列候选的机位。"
        "选择某条候选后，请在 rationale 起首沿用所附『前缀』，并明确说出"
        "起飞的 node_id 和落点 node_id：",
    ]
    for h in hyps:
        lines.append(
            f"  · [{h.hypothesis_id}] {h.relation}: "
            f"主体={h.subject_node_id}, 摄影师={h.photographer_node_id}, "
            f"az={h.azimuth_deg}°/pitch={h.pitch_deg}°/dist={h.distance_m}m, "
            f"height_hint={h.height_hint}"
        )
        lines.append(f"      前缀：{h.rationale_prefix_zh}")
    lines.append(
        "  HYPOTHESIS DOCTRINE：本列表是几何上已经验证可行的机位（不是发散建议）。"
        "若你采纳某条，``shot.angle`` 的 azimuth/pitch/distance 必须沿用，"
        "``shot.angle.height_hint`` 沿用本条的取值；如果你认为某条不合适，"
        "rationale 必须说出原因（例：『ld_03 节点正对路人通道，不安全』）。"
    )
    return "\n".join(lines)
