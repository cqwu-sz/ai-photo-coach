"""3D landmark graph aggregation — the geometric backbone of stereo
('multi-height') shot planning.

Why this exists
---------------
``scene_aggregate`` already produces a rich 2.5D summary of the scan
(brightest azimuth, foreground candidates with bbox + estimated metres,
subject pose). That is enough for "stand here, point that way, frame
the doorway behind the model" type plans, but it fundamentally cannot
express **vertical separation**: e.g. "model leans over the +3m
balcony railing, photographer crouches at ground level, looks up
through the tree branches".

A 3D landmark graph adds the missing axis. Each node is a real-world
3D point with a coarse class label; edges encode the relationships the
LLM (or a deterministic ``shot_hypothesis`` searcher) needs to reason
about: ``A is_above B by Δh``, ``A frames B``, ``A blocks_light_to B``.

Data flow
---------
1. iOS client emits ``LandmarkCandidate`` per keyframe — anchored on
   ARKit world coords via ``ARKitDepthSource`` + raycast.
2. ``LandmarkGraph.from_frames`` here dedups across frames (when the
   client supplies ``stable_id``) or via 0.3 m clustering, then
   classifies a "ground plane" anchor by majority vote on the lowest
   nodes' Y.
3. Edges are inferred geometrically — no LLM in this module.
4. ``to_prompt_block`` renders a tight Markdown summary fed into the
   ``LANDMARK GRAPH`` section of the analyze prompt; the LLM then
   picks (subject_landmark_id, photographer_landmark_id, relation)
   triples instead of fabricating angles in a 2D vacuum.

Determinism + cheapness
-----------------------
No I/O, no LLM. Pure Python + ``math``. Designed to run inside
``analyze_service`` synchronously alongside the existing
``scene_aggregate.aggregate`` call. Empty / missing input yields
``None`` so callers can splice without leaving a stranded heading.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import FrameMeta, LandmarkCandidate


# Cluster radius for landmark dedup when the client did not provide a
# ``stable_id``. 0.3 m means two candidates within ~30 cm of each other
# in ARKit world space are treated as the same physical landmark.
_DEDUP_RADIUS_M = 0.30

# Δh threshold (metres) below which two landmarks are treated as
# "roughly co-planar in height" rather than one being above the other.
# 0.3 m matches one stair-tread riser ≈ comfortable visual separation.
_STEREO_DELTA_H_M = 0.30

# Vertical bucket boundaries for "where is this landmark in space"
# coarse labels we use in the prompt block.
_HEIGHT_BUCKETS = [
    (-0.30,  "below_ground"),    # pit / descending stair
    ( 0.30,  "ground"),          # walkable surface
    ( 1.30,  "knee_to_chest"),   # bench / low wall
    ( 2.10,  "head"),            # doorway top / counter
    ( 4.00,  "first_floor"),     # balcony / mezzanine
    (12.00,  "upper_floor"),     # second-floor and above
    (math.inf, "tall_structure"),# pillar top / canopy
]


@dataclass(frozen=True, slots=True)
class LandmarkNode:
    """One physical landmark in the scene graph."""
    node_id: str          # short stable label like "ld_03"
    label: str
    world_xyz: tuple[float, float, float]
    size_m: Optional[tuple[float, float, float]]
    height_above_ground_m: Optional[float]
    height_bucket: str
    material_label: Optional[str]
    light_exposure: Optional[str]
    confidence: float
    seen_in_frames: tuple[int, ...]
    # Azimuth of this landmark from the user's standing point (origin),
    # for cross-referencing the scene_aggregate's azimuth-based facts.
    azimuth_from_origin_deg: float
    horizontal_distance_m: float

    def short(self) -> str:
        """One-line prompt-friendly description of this landmark."""
        h = self.height_above_ground_m
        h_txt = (f"+{h:.1f}m" if h is not None and h >= 0
                 else f"{h:.1f}m" if h is not None
                 else "?")
        return (
            f"{self.node_id}={self.label}@"
            f"az{self.azimuth_from_origin_deg:.0f}°/"
            f"dist{self.horizontal_distance_m:.1f}m/"
            f"h{h_txt}"
        )


@dataclass(frozen=True, slots=True)
class LandmarkEdge:
    """A geometric relationship between two landmarks."""
    src_id: str
    dst_id: str
    relation: str
    # One-line, optional human note (e.g. "Δh=+3.1m" / "behind src in view").
    note: Optional[str] = None


@dataclass(frozen=True, slots=True)
class LandmarkGraph:
    """Aggregated 3D landmark graph for a single analyze request."""
    nodes: tuple[LandmarkNode, ...]
    edges: tuple[LandmarkEdge, ...]
    ground_y: Optional[float]
    has_stereo_opportunity: bool
    """True when at least one pair of nodes has |Δh| ≥ ``_STEREO_DELTA_H_M``
    AND lies within usable photographic distance (< 12m). This is the
    flag that gates stereo / multi-height shot hypotheses downstream."""

    @classmethod
    def empty(cls) -> "LandmarkGraph":
        return cls(nodes=(), edges=(), ground_y=None, has_stereo_opportunity=False)


# ---------------------------------------------------------------------------
# Aggregation entry-point
# ---------------------------------------------------------------------------
def aggregate(frames: Iterable[FrameMeta]) -> Optional[LandmarkGraph]:
    """Build a ``LandmarkGraph`` from all frames' landmark_candidates.

    Returns ``None`` if no frame has any landmarks (older clients) so
    callers know to skip the prompt block. The graph itself may still
    have an empty edges tuple if e.g. there's only one usable node.
    """
    frames = list(frames)
    if not frames:
        return None
    raw: list[tuple[int, LandmarkCandidate]] = []
    for f in frames:
        if not f.landmark_candidates:
            continue
        for cand in f.landmark_candidates:
            raw.append((f.index, cand))
    if not raw:
        return None

    deduped = _dedup(raw)
    ground_y = _infer_ground_y(deduped)
    nodes = _build_nodes(deduped, ground_y)
    if not nodes:
        return LandmarkGraph.empty()
    edges = _build_edges(nodes)
    has_stereo = _check_stereo_opportunity(nodes)
    return LandmarkGraph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        ground_y=ground_y,
        has_stereo_opportunity=has_stereo,
    )


# ---------------------------------------------------------------------------
# Dedup across frames
# ---------------------------------------------------------------------------
def _dedup(raw: list[tuple[int, LandmarkCandidate]]) -> list[dict]:
    """Collapse multi-frame observations of the same landmark.

    Strategy:
      1. If the client supplied a ``stable_id``, group by it.
      2. Otherwise, greedy cluster by 3D distance ≤ ``_DEDUP_RADIUS_M``.

    Output is a list of dicts (mutable so subsequent passes can attach
    derived fields like ``height_above_ground_m``).
    """
    groups: dict[str, list[tuple[int, LandmarkCandidate]]] = defaultdict(list)
    anonymous: list[tuple[int, LandmarkCandidate]] = []
    for fi, c in raw:
        if c.stable_id:
            groups[c.stable_id].append((fi, c))
        else:
            anonymous.append((fi, c))

    # Greedy cluster the anonymous ones.
    clusters: list[list[tuple[int, LandmarkCandidate]]] = []
    centroids: list[tuple[float, float, float]] = []
    for fi, c in anonymous:
        cx, cy, cz = c.world_xyz
        joined = False
        for k, (px, py, pz) in enumerate(centroids):
            d = math.sqrt((cx - px) ** 2 + (cy - py) ** 2 + (cz - pz) ** 2)
            if d <= _DEDUP_RADIUS_M:
                clusters[k].append((fi, c))
                n = len(clusters[k])
                centroids[k] = (
                    (px * (n - 1) + cx) / n,
                    (py * (n - 1) + cy) / n,
                    (pz * (n - 1) + cz) / n,
                )
                joined = True
                break
        if not joined:
            clusters.append([(fi, c)])
            centroids.append((cx, cy, cz))

    out: list[dict] = []
    for sid, members in groups.items():
        out.append(_collapse_cluster(members, hint_id=sid))
    for cluster in clusters:
        out.append(_collapse_cluster(cluster))
    return out


def _collapse_cluster(members: list[tuple[int, LandmarkCandidate]],
                      hint_id: Optional[str] = None) -> dict:
    """Reduce N observations of one landmark to a single record."""
    xs = [m[1].world_xyz[0] for m in members]
    ys = [m[1].world_xyz[1] for m in members]
    zs = [m[1].world_xyz[2] for m in members]
    cx = statistics.fmean(xs)
    cy = statistics.fmean(ys)
    cz = statistics.fmean(zs)
    # Take the most common label across observations — robust against
    # one frame mis-classifying a balcony as a "wall".
    labels = [m[1].label for m in members]
    label = max(set(labels), key=labels.count)
    confs = [m[1].confidence for m in members if m[1].confidence is not None]
    conf = float(statistics.fmean(confs)) if confs else 0.5
    sizes = [m[1].size_m for m in members if m[1].size_m]
    size = None
    if sizes:
        size = (
            statistics.fmean(s[0] for s in sizes),
            statistics.fmean(s[1] for s in sizes),
            statistics.fmean(s[2] for s in sizes),
        )
    material = next((m[1].material_label for m in members if m[1].material_label), None)
    lighting = next((m[1].light_exposure for m in members if m[1].light_exposure), None)
    seen = tuple(sorted({fi for fi, _ in members}))
    return {
        "hint_id": hint_id,
        "label": label,
        "xyz": (cx, cy, cz),
        "size": size,
        "material": material,
        "lighting": lighting,
        "confidence": conf,
        "seen_in_frames": seen,
    }


# ---------------------------------------------------------------------------
# Ground plane + node materialisation
# ---------------------------------------------------------------------------
def _infer_ground_y(records: list[dict]) -> Optional[float]:
    """Take the lowest 25 % of Y-values and call their median ground Y.

    Why not just min(): one outlier (a stray detection inside a basement
    stairwell, or a misidentified water reflection) would yank the
    ground plane way down and turn every other landmark into a
    'balcony'. The 25 %-trimmed median is robust to a few outliers and
    matches how iOS ARKit eventually settles on the dominant horizontal
    plane.
    """
    ys = sorted(r["xyz"][1] for r in records)
    if not ys:
        return None
    cutoff = max(1, len(ys) // 4)
    return statistics.median(ys[:cutoff])


def _height_bucket(dh: Optional[float]) -> str:
    if dh is None:
        return "unknown"
    for ceiling, name in _HEIGHT_BUCKETS:
        if dh < ceiling:
            return name
    return "tall_structure"


def _build_nodes(records: list[dict], ground_y: Optional[float]) -> list[LandmarkNode]:
    """Materialise dict records into ``LandmarkNode`` value objects.

    Sorts by horizontal distance from origin so node ids ``ld_01``,
    ``ld_02``... grow outward — gives the LLM a friendly mental layout.
    """
    enriched = []
    for r in records:
        x, y, z = r["xyz"]
        # ARKit world: -Z is forward of the camera at session start.
        # Azimuth from origin: 0° = north (= -Z), clockwise (+X = east).
        az = math.degrees(math.atan2(x, -z)) % 360
        hd = math.sqrt(x * x + z * z)
        dh = (y - ground_y) if ground_y is not None else None
        enriched.append((hd, az, r, dh))
    enriched.sort(key=lambda t: t[0])
    nodes: list[LandmarkNode] = []
    for i, (hd, az, r, dh) in enumerate(enriched, start=1):
        node_id = r["hint_id"] or f"ld_{i:02d}"
        nodes.append(LandmarkNode(
            node_id=node_id,
            label=r["label"],
            world_xyz=tuple(r["xyz"]),
            size_m=tuple(r["size"]) if r["size"] else None,
            height_above_ground_m=round(dh, 2) if dh is not None else None,
            height_bucket=_height_bucket(dh),
            material_label=r["material"],
            light_exposure=r["lighting"],
            confidence=round(r["confidence"], 2),
            seen_in_frames=r["seen_in_frames"],
            azimuth_from_origin_deg=round(az, 1),
            horizontal_distance_m=round(hd, 2),
        ))
    return nodes


# ---------------------------------------------------------------------------
# Edge inference
# ---------------------------------------------------------------------------
def _build_edges(nodes: list[LandmarkNode]) -> list[LandmarkEdge]:
    """Infer geometric relationships between landmarks.

    Three families of edges currently emitted:

    - ``is_above``: dst sits ≥ ``_STEREO_DELTA_H_M`` higher than src.
      Always emit the lower→higher direction so a hypothesis engine can
      do `for src in nodes: for high in is_above(src):` to find stereo
      candidates.
    - ``frames``: src has a "framing" shape (doorway / window / pillar)
      and dst is within a 30° angular window behind src as seen from
      the origin. Distance dst >= distance src + 0.5m so the framer is
      actually in front of the framed.
    - ``blocks_light_to``: src is closer to the brightest direction than
      dst, AND src is taller than dst by ≥ 0.5 m. Cheap shadow-occluder
      heuristic; will produce false positives but they cost nothing to
      surface (the LLM just ignores them).
    """
    edges: list[LandmarkEdge] = []
    if not nodes:
        return edges

    FRAMING_LABELS = {"doorway", "window", "pillar", "tree", "wall_corner"}

    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if i == j:
                continue
            # is_above (only emit lower → higher)
            if (a.height_above_ground_m is not None
                    and b.height_above_ground_m is not None):
                dh = b.height_above_ground_m - a.height_above_ground_m
                if dh >= _STEREO_DELTA_H_M:
                    edges.append(LandmarkEdge(
                        src_id=a.node_id,
                        dst_id=b.node_id,
                        relation="is_above",
                        note=f"Δh=+{dh:.2f}m",
                    ))
            # frames (only when a is a framing type & b is further out
            # in roughly the same azimuth)
            if a.label in FRAMING_LABELS:
                d_az = abs(((a.azimuth_from_origin_deg - b.azimuth_from_origin_deg
                             + 540) % 360) - 180)
                if (d_az <= 15
                        and b.horizontal_distance_m >= a.horizontal_distance_m + 0.5):
                    edges.append(LandmarkEdge(
                        src_id=a.node_id,
                        dst_id=b.node_id,
                        relation="frames",
                        note=f"Δaz={d_az:.0f}°",
                    ))
    return edges


def _check_stereo_opportunity(nodes: list[LandmarkNode]) -> bool:
    """Any pair where |Δh| ≥ ``_STEREO_DELTA_H_M`` AND both within 12 m?"""
    if len(nodes) < 2:
        return False
    for i, a in enumerate(nodes):
        if a.height_above_ground_m is None or a.horizontal_distance_m > 12:
            continue
        for b in nodes[i + 1:]:
            if b.height_above_ground_m is None or b.horizontal_distance_m > 12:
                continue
            if abs(b.height_above_ground_m - a.height_above_ground_m) >= _STEREO_DELTA_H_M:
                return True
    return False


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------
_HEIGHT_BUCKET_ZH = {
    "below_ground":   "地面以下",
    "ground":         "地面层",
    "knee_to_chest":  "膝到胸高",
    "head":           "齐头高",
    "first_floor":    "约一层楼高",
    "upper_floor":    "二层及以上",
    "tall_structure": "高大结构顶部",
    "unknown":        "高度未知",
}


def to_prompt_block(graph: Optional[LandmarkGraph]) -> str:
    """Render the ``LANDMARK GRAPH`` block for the user prompt.

    Empty string when the graph is None / empty so callers can splice
    without leaving a heading. Format mirrors the other
    ``to_prompt_block`` helpers — Markdown-ish, with explicit
    DOCTRINE lines that tell the LLM how to consume the facts.
    """
    if graph is None or not graph.nodes:
        return ""

    lines = [
        f"── LANDMARK GRAPH（客户端 ARKit 3D 地标聚合，共 {len(graph.nodes)} 个节点） ──",
    ]
    if graph.ground_y is not None:
        lines.append(
            f"  · 地面层 y ≈ {graph.ground_y:+.2f} m（所有 height_above_ground 都基于此基准）"
        )
    if graph.has_stereo_opportunity:
        lines.append(
            "  · **检测到立体空间机会**：场景中至少有一对节点的"
            "高度差 ≥ 0.3 m 且都在可拍摄距离内（< 12 m）。"
            "你被强烈鼓励产出至少一个利用高度差的方案"
            "（例：模特在高节点上，摄影师在低节点位置仰拍）。"
        )

    lines.append("  · 节点列表（按距离用户由近到远）：")
    for n in graph.nodes[:20]:
        bucket_zh = _HEIGHT_BUCKET_ZH.get(n.height_bucket, n.height_bucket)
        extras = []
        if n.material_label:
            extras.append(n.material_label)
        if n.light_exposure and n.light_exposure != "unknown":
            extras.append(n.light_exposure)
        extra_txt = f"，{'/'.join(extras)}" if extras else ""
        lines.append(f"    · {n.short()}（{bucket_zh}{extra_txt}）")

    if graph.edges:
        lines.append("  · 节点关系（自动推断的几何关系）：")
        for e in graph.edges[:24]:
            note = f"（{e.note}）" if e.note else ""
            lines.append(f"    · {e.src_id} —{e.relation}→ {e.dst_id}{note}")

    lines.append(
        "  LANDMARK DOCTRINE：填 shot 时，**若节点列表里存在合适的高低组合**，"
        "你的 shot.rationale 必须显式引用所选地标的 node_id（例：「让模特坐 "
        "ld_03 的台阶顶端，你蹲到 ld_01 的位置仰拍」），不要凭空给方位角。"
        "若选择了 is_above 边作为依据，相应 shot.angle.height_hint 应为 "
        "low 或 high 以匹配高度差；coach_brief 直接用「上去」「下来」喊出。"
    )
    return "\n".join(lines)
