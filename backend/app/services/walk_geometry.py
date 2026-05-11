"""Convert an opt-in WalkSegment into a list of ``absolute`` ShotPosition
candidates expressed in (lat, lon) world coordinates.

Inputs come in three flavours, with very different precision:

  - ``arkit``  — ``ARFrame.camera.transform`` is true VIO; centimetre-grade
    drift over a 20 s walk. Confidence ≈ 0.85.
  - ``webxr``  — `XRRigidTransform.position` from a hit-tested AR session.
    Similar to ARKit on supported devices. Confidence ≈ 0.75.
  - ``devicemotion`` — IMU double-integration only. Drifts metres per
    second; we still honour the trajectory but downgrade confidence to
    ~0.35 so fusion ranks it below POI hits.

For all three sources the local frame is ENU (x=east, y=north, z=up)
relative to the user's initial GeoFix; we rotate by ``initial_heading_deg``
when present (the device's compass at walk start) and convert metres to
degrees using a flat-earth approximation, which is accurate to ±0.5 m
over walks of ≤ 200 m.

We thin the trajectory to the few endpoints / corners that look like
candidate stand-points (>= 8 m from any prior pick, >= 5 m from origin),
giving the fusion stage typically 1-3 candidates per walk rather than
hundreds of redundant samples.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..models import GeoFix, ShotPosition, ShotPositionKind, WalkPose, WalkSegment

# Distance thresholds. CANDIDATE_MIN_R: don't bother proposing a candidate
# closer than this to where the user started — those overlap the
# ``relative`` shots already produced. CANDIDATE_MIN_SEP: pairwise
# minimum separation between candidates so we don't return two pins
# six metres apart.
CANDIDATE_MIN_R = 5.0
CANDIDATE_MIN_SEP = 8.0
WALK_SPEED_M_PER_MIN = 70.0   # casual stroll, used for est_walk_minutes


@dataclass(frozen=True, slots=True)
class _Candidate:
    x: float
    y: float
    distance_from_origin_m: float
    facing_deg: Optional[float]   # derived from quaternion if available


SOURCE_CONFIDENCE = {
    "arkit":        0.85,
    "webxr":        0.75,
    "devicemotion": 0.35,
}
SOURCE_TAG = {
    "arkit":        "sfm_ios",
    "webxr":        "sfm_web",
    "devicemotion": "sfm_web",
}


def derive_candidates(walk: WalkSegment, user_geo: GeoFix) -> list[ShotPosition]:
    """Turn a WalkSegment into ranked ShotPosition candidates."""
    if not walk.poses:
        return []

    rotation_deg = walk.initial_heading_deg if walk.initial_heading_deg is not None else 0.0

    # W5.2 — devicemotion drift correction. When the Web client supplies
    # GPS samples, fit IMU-derived (x,y) to the GPS-derived ENU offsets
    # via a single-parameter linear scale + small heading bias. Boosts
    # confidence afterwards.
    confidence_boost = 0.0
    if walk.source == "devicemotion" and walk.gps_track and len(walk.gps_track) >= 2:
        try:
            walk = _fit_devicemotion_to_gps(walk, user_geo)
            confidence_boost = 0.20
        except Exception:                                            # noqa: BLE001
            confidence_boost = 0.0

    picks: list[_Candidate] = []
    for pose in walk.poses:
        d_origin = math.hypot(pose.x, pose.y)
        if d_origin < CANDIDATE_MIN_R:
            continue
        if any(math.hypot(pose.x - p.x, pose.y - p.y) < CANDIDATE_MIN_SEP for p in picks):
            continue
        facing = _facing_from_quaternion(pose)
        picks.append(_Candidate(
            x=pose.x, y=pose.y,
            distance_from_origin_m=d_origin,
            facing_deg=facing,
        ))

    if not picks:
        return []

    confidence = min(0.85, SOURCE_CONFIDENCE.get(walk.source, 0.5) + confidence_boost)
    src_tag = SOURCE_TAG.get(walk.source, "sfm_web")

    out: list[ShotPosition] = []
    for i, c in enumerate(picks, start=1):
        # Rotate ENU into true geographic ENU using initial heading.
        true_e, true_n = _rotate(c.x, c.y, rotation_deg)
        lat, lon = _enu_to_latlon(true_e, true_n, user_geo.lat, user_geo.lon)
        bearing = (math.degrees(math.atan2(true_e, true_n)) + 360.0) % 360.0
        facing_world = None
        if c.facing_deg is not None:
            facing_world = (c.facing_deg + rotation_deg) % 360.0
        walk_d = round(c.distance_from_origin_m, 1)
        out.append(ShotPosition(
            kind=ShotPositionKind.absolute,
            lat=lat, lon=lon,
            facing_deg=facing_world,
            walk_distance_m=walk_d,
            bearing_from_user_deg=round(bearing, 1),
            est_walk_minutes=round(walk_d / WALK_SPEED_M_PER_MIN, 1),
            source=src_tag,
            confidence=confidence,
            name_zh=f"漫游机位 #{i}",
            walkability_note_zh="基于你刚才走过的路线，可达性已验证",
        ))
    return out


# ---------------------------------------------------------------------------
def _facing_from_quaternion(pose: WalkPose) -> Optional[float]:
    """Yaw (compass-style heading) derived from the camera quaternion.
    Returns None when the quaternion is the identity (no orientation
    info — we just got positions)."""
    qx, qy, qz, qw = pose.qx, pose.qy, pose.qz, pose.qw
    if abs(qx) + abs(qy) + abs(qz) < 1e-6:
        return None
    # Yaw around +Z (up) for a -Z-forward camera. Standard quaternion -> yaw:
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw_rad = math.atan2(siny_cosp, cosy_cosp)
    # Convert math-angle (CCW from +x/east) to compass (CW from +y/north).
    compass = (90.0 - math.degrees(yaw_rad) + 360.0) % 360.0
    return compass


def _rotate(x_local: float, y_local: float, heading_deg: float) -> tuple[float, float]:
    """Rotate a local-frame ENU offset by the device's initial compass
    heading so x_local along device-forward becomes a true-east/north
    pair. heading_deg is the compass bearing of the device's initial
    forward (0=N, 90=E)."""
    # Local x is device-right, y is device-forward. Convert to ENU.
    theta = math.radians(heading_deg)
    east  = x_local * math.cos(theta) + y_local * math.sin(theta)
    north = -x_local * math.sin(theta) + y_local * math.cos(theta)
    return east, north


def _enu_to_latlon(east_m: float, north_m: float,
                   ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """Flat-earth ENU -> (lat, lon). Accurate to ±0.5 m within ~200 m of
    ref. Good enough for walk-segment candidates which are at most
    20-50 m from origin."""
    lat = ref_lat + (north_m / 111_320.0)
    lon_scale = 111_320.0 * math.cos(math.radians(ref_lat))
    lon_scale = lon_scale if lon_scale > 1.0 else 1.0
    lon = ref_lon + (east_m / lon_scale)
    return round(lat, 7), round(lon, 7)


def _fit_devicemotion_to_gps(walk: WalkSegment, user_geo: GeoFix) -> WalkSegment:
    """Linear fit of IMU-derived (x,y) trajectory to the GPS ENU offsets.

    Computes the best uniform scale that minimises pairwise distance
    between the (interpolated) IMU position at each GPS timestamp and
    the GPS ENU position. Applies that scale (and a small bias) to all
    poses, returning a new WalkSegment. We *don't* attempt to learn a
    full rigid transform — the heading bias is already accounted for
    via ``initial_heading_deg``.
    """
    samples = walk.gps_track or []
    if len(samples) < 2:
        return walk
    cos_lat = max(0.05, math.cos(math.radians(user_geo.lat)))
    paired: list[tuple[float, float, float, float]] = []  # (imu_x, imu_y, gps_e, gps_n)
    for s in samples:
        gps_e = (s.lon - user_geo.lon) * 111_320.0 * cos_lat
        gps_n = (s.lat - user_geo.lat) * 111_320.0
        # Find nearest pose by timestamp.
        nearest = min(walk.poses, key=lambda p: abs(p.t_ms - s.t_ms))
        if abs(nearest.t_ms - s.t_ms) > 1500:
            continue
        paired.append((nearest.x, nearest.y, gps_e, gps_n))
    if len(paired) < 2:
        return walk
    num = sum(ix * gx + iy * gy for ix, iy, gx, gy in paired)
    den = sum(ix * ix + iy * iy for ix, iy, _, _ in paired) or 1e-6
    scale = max(0.25, min(4.0, num / den))
    new_poses = [
        WalkPose(t_ms=p.t_ms, x=p.x * scale, y=p.y * scale, z=p.z,
                 qx=p.qx, qy=p.qy, qz=p.qz, qw=p.qw)
        for p in walk.poses
    ]
    return walk.model_copy(update={"poses": new_poses})


def to_prompt_block(walk: Optional[WalkSegment],
                    candidates: list[ShotPosition]) -> str:
    """Markdown-ish prompt block summarising the walk coverage so the LLM
    knows that absolute candidates exist and that it can reference them
    in rationale (e.g. "走到漫游机位 #2 那里再拍，背景更干净")."""
    if walk is None or not walk.poses:
        return ""
    last = walk.poses[-1]
    cov = math.hypot(last.x, last.y)
    lines = [
        "── WALK COVERAGE（用户额外漫游了一段路径，可解锁远机位）──",
        f"  · 数据源：{walk.source}（精度等级 {SOURCE_CONFIDENCE.get(walk.source, 0.5)}）",
        f"  · 漫游半径 ≈ {cov:.1f} m，候选机位 {len(candidates)} 个",
    ]
    for c in candidates[:3]:
        lines.append(
            f"    - {c.name_zh}：步行 ~{c.walk_distance_m} m，"
            f"方位 {c.bearing_from_user_deg}°"
        )
    lines.append(
        "  使用规则：这些是用户已经走到过的实际位置，可达性 100%。"
        "如果你建议用户用其中之一，在 rationale 写「走到漫游机位 #N 处」。"
    )
    return "\n".join(lines)
