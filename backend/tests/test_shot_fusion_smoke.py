"""Smoke tests for the three-source shot fusion."""
from __future__ import annotations

from app.models import (
    Angle,
    CameraSettings,
    Composition,
    CompositionType,
    GeoFix,
    PoseSuggestion,
    Layout,
    ShotPosition,
    ShotPositionKind,
    ShotRecommendation,
)
from app.services import shot_fusion
from app.services.poi_lookup import POICandidate


def _llm_shot(id_: str, az: float, distance: float = 3.0,
              overall: float = 4.0) -> ShotRecommendation:
    return ShotRecommendation(
        id=id_,
        angle=Angle(azimuth_deg=az, pitch_deg=0, distance_m=distance),
        composition=Composition(primary=CompositionType.rule_of_thirds),
        camera=CameraSettings(focal_length_mm=35, aperture="f/2.8",
                              shutter="1/250", iso=200),
        poses=[PoseSuggestion(person_count=1, layout=Layout.single)],
        rationale="rationale text",
        confidence=0.8,
        overall_score=overall,
    )


def test_fuse_attaches_relative_position():
    shots = [_llm_shot("s1", az=90), _llm_shot("s2", az=270)]
    out = shot_fusion.fuse(shots, [], [], env=None,
                           user_geo=GeoFix(lat=0, lon=0))
    assert all(s.position is not None for s in out)
    assert all(s.position.kind == ShotPositionKind.relative for s in out)


def test_fuse_includes_poi_and_sfm():
    template = _llm_shot("base", az=180)
    pois = [POICandidate(
        name="陈毅广场", lat=31.2401, lon=121.4912, kind="viewpoint",
        source="kb", distance_m=140.0, bearing_from_user_deg=15.0,
    )]
    sfm = [ShotPosition(
        kind=ShotPositionKind.absolute,
        lat=31.2392, lon=121.4906,
        walk_distance_m=25.0, bearing_from_user_deg=350.0,
        est_walk_minutes=0.4, source="sfm_ios",
        confidence=0.85, name_zh="漫游机位 #1",
    )]
    out = shot_fusion.fuse([template], pois, sfm, env=None,
                           user_geo=GeoFix(lat=31.2389, lon=121.4905))
    sources = {s.position.source for s in out if s.position}
    assert "poi_kb" in sources
    assert "sfm_ios" in sources
    # Always at least one relative survives for fallback.
    kinds = {s.position.kind for s in out if s.position}
    assert ShotPositionKind.relative in kinds


def test_dedupe_drops_close_absolute():
    template = _llm_shot("base", az=180)
    pois = [POICandidate(
        name="A", lat=31.2400, lon=121.4900, kind="viewpoint",
        source="kb", distance_m=120.0, bearing_from_user_deg=15.0,
    )]
    sfm = [ShotPosition(
        kind=ShotPositionKind.absolute,
        lat=31.24001, lon=121.49001,   # < 5 m from POI
        walk_distance_m=120.5, bearing_from_user_deg=15.5,
        est_walk_minutes=1.7, source="sfm_ios",
        confidence=0.85, name_zh="漫游机位 #1",
    )]
    out = shot_fusion.fuse([template], pois, sfm, env=None,
                           user_geo=GeoFix(lat=31.2389, lon=121.4905))
    abs_sources = [s.position.source for s in out
                   if s.position and s.position.kind == ShotPositionKind.absolute]
    # POI wins on tie.
    assert "poi_kb" in abs_sources
    assert "sfm_ios" not in abs_sources


def test_top_n_caps_results():
    template = _llm_shot("base", az=180, overall=3.5)
    pois = [POICandidate(
        name=f"poi-{i}", lat=31.24 + i * 0.001, lon=121.49,
        kind="viewpoint", source="kb",
        distance_m=100.0 + i * 30, bearing_from_user_deg=10.0 * i,
    ) for i in range(8)]
    out = shot_fusion.fuse([template], pois, [], env=None,
                           user_geo=GeoFix(lat=31.2389, lon=121.49),
                           max_total=5)
    assert len(out) == 5
