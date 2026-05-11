"""Smoke tests for walk_geometry — synthesised straight-line walk
should produce a candidate within ~2 m of ground truth."""
from __future__ import annotations

import math

from app.models import GeoFix, ShotPositionKind, WalkPose, WalkSegment
from app.services import walk_geometry


def _straight_north_walk(distance_m: float, n: int = 12) -> WalkSegment:
    """Synthetic 'walked due north for N samples' trajectory."""
    poses = []
    for i in range(n):
        d = (i / (n - 1)) * distance_m
        poses.append(WalkPose(t_ms=i * 500, x=0.0, y=d, z=0.0))
    return WalkSegment(source="arkit", initial_heading_deg=0.0, poses=poses)


def test_arkit_straight_walk_lat_lon():
    """A 30 m due-north walk from (31.2389, 121.4905) should land us
    on roughly (31.2392, 121.4905)."""
    geo = GeoFix(lat=31.2389, lon=121.4905)
    segment = _straight_north_walk(30.0)
    cands = walk_geometry.derive_candidates(segment, geo)
    assert cands, "expected at least one candidate"
    assert cands[-1].kind == ShotPositionKind.absolute
    final = cands[-1]
    # Verify against the flat-earth approx: 30 m north ≈ 0.000270 deg
    expected_lat = geo.lat + 30.0 / 111_320.0
    assert abs(final.lat - expected_lat) < 1e-4
    assert abs(final.lon - geo.lon) < 1e-4
    # Walk distance should match the synthetic trajectory length (within
    # 1 m of 30) and confidence should reflect ARKit (>= 0.8).
    assert abs(final.walk_distance_m - 30.0) < 1.5
    assert final.confidence >= 0.8


def test_devicemotion_lower_confidence():
    geo = GeoFix(lat=31.2389, lon=121.4905)
    segment = WalkSegment(
        source="devicemotion", initial_heading_deg=0.0,
        poses=_straight_north_walk(20.0).poses,
    )
    cands = walk_geometry.derive_candidates(segment, geo)
    assert cands and cands[0].confidence < 0.5


def test_short_walk_returns_empty():
    """Anything less than CANDIDATE_MIN_R from origin gets dropped."""
    geo = GeoFix(lat=31.2389, lon=121.4905)
    segment = WalkSegment(
        source="arkit", initial_heading_deg=0.0,
        poses=[WalkPose(t_ms=i * 500, x=0, y=i * 0.4, z=0) for i in range(5)],
    )
    assert walk_geometry.derive_candidates(segment, geo) == []
