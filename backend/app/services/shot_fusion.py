"""Three-source shot-position fusion.

Inputs come from three independent producers:

  1. **LLM relative shots** — the existing pipeline output, anchored at
     the user's standing point. Always present.
  2. **POI candidates** — from ``poi_lookup.search_nearby``; each is a
     known landmark with accurate lat/lon.
  3. **SfM/VIO candidates** — from ``walk_geometry.derive_candidates``;
     positions on the user's actual walked path.

Output is the same ``list[ShotRecommendation]`` we always return; we
just enrich each item with a ``ShotPosition`` (always) and may *add* up
to a few new shots that piggy-back on POI / SfM candidates with the
LLM's first shot used as a template (camera, composition, pose copied).

Ranking weights (kept in one place so they're easy to tune):
  - POI authority  : 0.25
  - distance fit   : 0.20  (walk_distance_m in [0, 150] is the sweet spot)
  - light fit      : 0.20  (camera-to-sun geometry; only when env.sun set)
  - source conf    : 0.15  (POI = 0.9, SfM iOS = 0.85, ...)
  - LLM intrinsic  : 0.20  (overall_score / 5 if filled, else confidence)

We always preserve at least one ``relative`` shot in the final list so
clients without map rendering still have something to draw.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Optional

from ..models import (
    EnvironmentSnapshot,
    GeoFix,
    ShotPosition,
    ShotPositionKind,
    ShotRecommendation,
)
from ..models import FarPoint
from .poi_lookup import POICandidate

WALK_SWEET_SPOT_MAX_M = 150.0   # beyond this, distance fit decays linearly to 0 at 400 m
SOURCE_INTRINSIC = {
    "poi_kb":       0.92,
    "poi_online":   0.88,
    "poi_ugc":      0.78,
    "poi_indoor":   0.86,
    "sfm_ios":      0.85,
    "sfm_web":      0.55,
    "triangulated": 0.6,
    "recon3d":      0.78,
    "llm_relative": 0.65,
}


# ---------------------------------------------------------------------------
def attach_relative_positions(
    shots: list[ShotRecommendation],
    user_geo: Optional[GeoFix],
) -> None:
    """Mirror each shot's legacy ``angle`` into a ``relative`` ShotPosition
    so old clients keep working and new clients get a uniform field. When
    a GeoFix is available we *also* compute the absolute lat/lon (kept on
    the relative position via name_zh + bearing only) just so the UI can
    render a fallback map dot near the user."""
    for s in shots:
        if s.position is not None:
            continue
        s.position = ShotPosition(
            kind=ShotPositionKind.relative,
            azimuth_deg=s.angle.azimuth_deg % 360,
            distance_m=s.angle.distance_m,
            pitch_deg=s.angle.pitch_deg,
            height_hint=s.angle.height_hint,
            source="llm_relative",
            confidence=s.confidence or 0.7,
            name_zh="原地附近机位",
        )


# ---------------------------------------------------------------------------
def fuse(
    llm_shots: list[ShotRecommendation],
    poi_candidates: list[POICandidate],
    sfm_candidates: list[ShotPosition],
    env: Optional[EnvironmentSnapshot],
    user_geo: Optional[GeoFix],
    max_total: int = 5,
    far_points: Optional[list[FarPoint]] = None,
    indoor_positions: Optional[list[ShotPosition]] = None,
) -> list[ShotRecommendation]:
    """Return the merged + ranked list."""
    attach_relative_positions(llm_shots, user_geo)
    if far_points:
        _upgrade_with_far_points(llm_shots, far_points)

    # Build candidate ShotRecommendations from POI + SfM by cloning the
    # best LLM shot as a template (same camera / composition / pose) and
    # swapping in the absolute position. This keeps the rest of the
    # response schema valid without requiring the LLM to know about
    # absolute positioning.
    template = _best_template(llm_shots)

    poi_shots: list[ShotRecommendation] = []
    if template is not None:
        for i, p in enumerate(poi_candidates, start=1):
            shot = _clone_with_position(
                template, _poi_to_position(p),
                id_suffix=f"poi-{i}",
                title=f"机位｜{p.name}",
                rationale_prefix=f"利用 POI「{p.name}」做远机位：",
            )
            poi_shots.append(shot)

    sfm_shots: list[ShotRecommendation] = []
    if template is not None:
        for i, sp in enumerate(sfm_candidates, start=1):
            shot = _clone_with_position(
                template, sp,
                id_suffix=f"sfm-{i}",
                title=sp.name_zh or f"漫游机位 #{i}",
                rationale_prefix="基于你的漫游轨迹推导出的远机位：",
            )
            sfm_shots.append(shot)

    indoor_shots: list[ShotRecommendation] = []
    if template is not None and indoor_positions:
        for i, ip in enumerate(indoor_positions, start=1):
            shot = _clone_with_position(
                template, ip,
                id_suffix=f"indoor-{i}",
                title=ip.name_zh or f"室内机位 #{i}",
                rationale_prefix=f"利用室内热点「{(ip.indoor.hotspot_label_zh if ip.indoor else '') or ''}」做机位：",
            )
            indoor_shots.append(shot)

    pool: list[ShotRecommendation] = list(llm_shots) + poi_shots + sfm_shots + indoor_shots

    # De-dupe absolute candidates that are within 5 m of each other
    # (POI > SfM > LLM by source priority).
    pool = _dedupe_absolute(pool)

    # Score everything.
    scored = [(_score(s, env), s) for s in pool]
    scored.sort(key=lambda t: t[0], reverse=True)

    selected: list[ShotRecommendation] = []
    saw_relative = False
    for score, s in scored:
        if len(selected) >= max_total:
            break
        selected.append(s)
        if s.position is not None and s.position.kind == ShotPositionKind.relative:
            saw_relative = True

    # Guarantee at least one relative shot in the output (fallback for
    # map-less clients). If the top-N is all absolute, swap in the
    # highest-scoring relative.
    if not saw_relative:
        for score, s in scored:
            if s.position is not None and s.position.kind == ShotPositionKind.relative:
                # Replace the lowest-scoring selected with this one.
                selected[-1] = s
                break

    return selected


# ---------------------------------------------------------------------------
def _best_template(shots: list[ShotRecommendation]) -> Optional[ShotRecommendation]:
    """Pick the highest-confidence LLM shot to use as the template for
    POI / SfM clones. If nothing's available, callers skip cloning."""
    if not shots:
        return None
    return max(shots, key=lambda s: (s.overall_score or 0.0, s.confidence or 0.0))


def _clone_with_position(
    template: ShotRecommendation,
    position: ShotPosition,
    id_suffix: str,
    title: str,
    rationale_prefix: str,
) -> ShotRecommendation:
    s = copy.deepcopy(template)
    s.id = f"{template.id}-{id_suffix}" if template.id else id_suffix
    s.title = title
    s.position = position
    # Soften: this is an extrapolated clone, not vouched-for by the LLM.
    s.confidence = max(0.4, min(0.85, position.confidence))
    s.rationale = f"{rationale_prefix}{template.rationale}"
    return s


def _poi_to_position(p: POICandidate) -> ShotPosition:
    """Build a ShotPosition from a POI candidate. Source is normalised
    to ``poi_kb`` vs ``poi_online`` for downstream weighting."""
    src = "poi_kb" if p.source == "kb" else "poi_online"
    walk = p.distance_m
    return ShotPosition(
        kind=ShotPositionKind.absolute,
        lat=p.lat, lon=p.lon,
        facing_deg=p.recommended_facing_deg,
        walk_distance_m=round(walk, 1),
        bearing_from_user_deg=p.bearing_from_user_deg,
        est_walk_minutes=round(walk / 70.0, 1),
        source=src,
        confidence=SOURCE_INTRINSIC.get(src, 0.7),
        name_zh=p.name,
        walkability_note_zh=f"{p.kind} · 来自 {p.source.upper()}",
    )


# ---------------------------------------------------------------------------
def _dedupe_absolute(pool: list[ShotRecommendation]) -> list[ShotRecommendation]:
    """Drop absolute shots whose (lat, lon) is within 5 m of another
    higher-priority absolute shot. Source priority:
    poi_kb > poi_online > sfm_ios > sfm_web > llm_relative.
    """
    PRIORITY = {"poi_kb": 7, "poi_indoor": 6, "poi_online": 5,
                "poi_ugc": 4, "sfm_ios": 3, "recon3d": 3,
                "triangulated": 2, "sfm_web": 2, "llm_relative": 1}
    abs_shots = [s for s in pool if s.position and s.position.kind == ShotPositionKind.absolute]
    abs_shots.sort(key=lambda s: PRIORITY.get(s.position.source, 0), reverse=True)
    kept_abs: list[ShotRecommendation] = []
    for s in abs_shots:
        dup = False
        for k in kept_abs:
            if _meters_between(s.position, k.position) < 5.0:
                dup = True
                break
        if not dup:
            kept_abs.append(s)
    rel_shots = [s for s in pool if not (s.position and s.position.kind == ShotPositionKind.absolute)]
    return rel_shots + kept_abs


def _meters_between(a: ShotPosition, b: ShotPosition) -> float:
    if a.lat is None or b.lat is None:
        return float("inf")
    R = 6_371_000.0
    p1 = math.radians(a.lat); p2 = math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians((b.lon or 0) - (a.lon or 0))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
def _upgrade_with_far_points(shots: list[ShotRecommendation],
                             far_points: list[FarPoint],
                             azimuth_tol_deg: float = 8.0) -> None:
    """When a relative LLM shot points at roughly the same azimuth as a
    triangulated FarPoint, copy that point's lat/lon into a NEW absolute
    ShotPosition, attach it to the shot (replacing the relative one),
    and stamp source='triangulated'."""
    for s in shots:
        pos = s.position
        if pos is None or pos.kind != ShotPositionKind.relative:
            continue
        if pos.azimuth_deg is None:
            continue
        for fp in far_points:
            delta = abs(((fp.observed_in_azimuth_deg - pos.azimuth_deg) + 540) % 360 - 180)
            if delta <= azimuth_tol_deg:
                s.position = ShotPosition(
                    kind=ShotPositionKind.absolute,
                    lat=fp.lat, lon=fp.lon,
                    height_above_ground_m=fp.height_m,
                    facing_deg=(fp.observed_in_azimuth_deg + 180) % 360,
                    bearing_from_user_deg=fp.observed_in_azimuth_deg,
                    source="triangulated",
                    confidence=fp.confidence,
                    name_zh=fp.label_zh or "三角化机位",
                )
                break


def _score(shot: ShotRecommendation, env: Optional[EnvironmentSnapshot]) -> float:
    """Weighted score in [0, 1]."""
    pos = shot.position
    if pos is None:
        return 0.0

    poi_authority = 1.0 if pos.source in (
        "poi_kb", "poi_online", "poi_ugc", "poi_indoor"
    ) else 0.4

    if pos.kind == ShotPositionKind.absolute and pos.walk_distance_m is not None:
        d = pos.walk_distance_m
        if d <= WALK_SWEET_SPOT_MAX_M:
            distance_fit = 1.0
        else:
            distance_fit = max(0.0, 1.0 - (d - WALK_SWEET_SPOT_MAX_M) / 250.0)
    else:
        # Relative shots: prefer ones not crammed within 1 m of the user.
        d = pos.distance_m or 2.0
        distance_fit = 1.0 if 1.5 <= d <= 8.0 else 0.6

    light_fit = _light_fit(pos, env)
    source_conf = SOURCE_INTRINSIC.get(pos.source, 0.5) * pos.confidence
    llm_intrinsic = (shot.overall_score or 0.0) / 5.0 if shot.overall_score else (shot.confidence or 0.5)

    return round(
        0.25 * poi_authority
        + 0.20 * distance_fit
        + 0.20 * light_fit
        + 0.15 * source_conf
        + 0.20 * llm_intrinsic,
        4,
    )


def _light_fit(pos: ShotPosition, env: Optional[EnvironmentSnapshot]) -> float:
    """Crude camera-to-sun geometry score. Side-light (camera ⊥ sun) is
    universally flattering and gets the highest score; backlight is good
    too (rim light); harsh straight-on noon front-light scores lowest."""
    if env is None or env.sun is None:
        return 0.5
    facing = pos.facing_deg
    if facing is None and pos.kind == ShotPositionKind.relative:
        # Use azimuth as a proxy: assume the user points the camera
        # toward the named azimuth, so facing == azimuth.
        facing = pos.azimuth_deg
    if facing is None:
        return 0.5
    delta = abs(((env.sun.azimuth_deg - facing) + 540) % 360 - 180)
    if 60 <= delta <= 120:
        return 1.0           # side light
    if delta >= 150:
        return 0.85          # back / rim
    if delta <= 30:
        return 0.45          # straight-on, often too flat
    return 0.7
