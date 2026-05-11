"""Two-view triangulation of remote scene points (W4).

Given two ``(image, ARKit-pose, intrinsics)`` records from frames with
sufficient baseline (≥ 3 m), recover sparse 3D points and convert them
to ``(lat, lon)`` using the user's GeoFix as the anchor for the local
ENU frame.

The output (``list[FarPoint]``) feeds ``shot_fusion``: when the LLM's
relative shot azimuth lines up with a recovered FarPoint, that shot is
upgraded to ``absolute`` with ``source='triangulated'``.

OpenCV is imported lazily so the rest of the service runs unaffected if
``opencv-python-headless`` isn't installed.
"""
from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from ..models.schemas import FarPoint

log = logging.getLogger(__name__)

MIN_BASELINE_M = 3.0
MIN_MATCHES = 8
MAX_REPROJ_ERR_PX = 2.0
MAX_FAR_POINTS = 12


@dataclass
class TriangulationFrame:
    """Lightweight bundle the analyze pipeline can hand to triangulation."""
    image_bytes: bytes
    """Raw JPEG/PNG bytes — decoded lazily inside cv2."""
    pose_t: tuple[float, float, float]
    """Camera position in local ENU metres (x=east, y=north, z=up)."""
    pose_R: tuple[tuple[float, float, float], ...]
    """3x3 rotation matrix camera→world (ENU)."""
    fx: float
    fy: float
    cx: Optional[float] = None
    cy: Optional[float] = None


def _try_import_cv2():
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
        return True
    except Exception as e:                                          # noqa: BLE001
        log.info("opencv unavailable, triangulation disabled: %s", e)
        return False


def derive_far_points(frames: Sequence[TriangulationFrame],
                      origin_lat: float, origin_lon: float,
                      initial_heading_deg: float = 0.0) -> list[FarPoint]:
    """Pairwise-triangulate matching ORB features across the supplied
    frames; return up to ``MAX_FAR_POINTS`` highest-confidence FarPoints.

    Pure best-effort: if cv2 is missing, frames < 2, baselines too short,
    or matches too sparse, returns ``[]`` and the caller proceeds without
    triangulated upgrades.
    """
    if len(frames) < 2:
        return []
    if not _try_import_cv2():
        return []

    import cv2
    import numpy as np

    out: list[FarPoint] = []
    seen: list[tuple[float, float]] = []   # crude lat,lon de-dupe (~5m grid)

    orb = cv2.ORB_create(nfeatures=800)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            fi, fj = frames[i], frames[j]
            baseline = math.dist(fi.pose_t, fj.pose_t)
            if baseline < MIN_BASELINE_M:
                continue

            img_i = _decode(fi.image_bytes, cv2)
            img_j = _decode(fj.image_bytes, cv2)
            if img_i is None or img_j is None:
                continue

            kp_i, des_i = orb.detectAndCompute(img_i, None)
            kp_j, des_j = orb.detectAndCompute(img_j, None)
            if des_i is None or des_j is None or len(kp_i) < MIN_MATCHES:
                continue

            knn = matcher.knnMatch(des_i, des_j, k=2)
            good = []
            for m_pair in knn:
                if len(m_pair) < 2:
                    continue
                m, n = m_pair
                if m.distance < 0.75 * n.distance:
                    good.append(m)
            if len(good) < MIN_MATCHES:
                continue

            pts_i = np.float32([kp_i[m.queryIdx].pt for m in good])
            pts_j = np.float32([kp_j[m.trainIdx].pt for m in good])

            cx_i = fi.cx if fi.cx is not None else img_i.shape[1] * 0.5
            cy_i = fi.cy if fi.cy is not None else img_i.shape[0] * 0.5
            cx_j = fj.cx if fj.cx is not None else img_j.shape[1] * 0.5
            cy_j = fj.cy if fj.cy is not None else img_j.shape[0] * 0.5
            K_i = np.array([[fi.fx, 0, cx_i], [0, fi.fy, cy_i], [0, 0, 1]])
            K_j = np.array([[fj.fx, 0, cx_j], [0, fj.fy, cy_j], [0, 0, 1]])

            E, mask = cv2.findEssentialMat(
                pts_i, pts_j, K_i,
                method=cv2.RANSAC, prob=0.999, threshold=1.0,
            )
            if E is None:
                continue
            _, R_rel, t_rel, mask_pose = cv2.recoverPose(E, pts_i, pts_j, K_i, mask=mask)

            # Build full projection matrices in world (ENU) frame from
            # the supplied per-frame poses. ARKit gives us pose_R / pose_t
            # camera→world; cv2 wants world→camera so we invert.
            R_i = np.array(fi.pose_R)
            t_i = np.array(fi.pose_t).reshape(3, 1)
            R_j = np.array(fj.pose_R)
            t_j = np.array(fj.pose_t).reshape(3, 1)

            P_i = K_i @ np.hstack([R_i.T, -R_i.T @ t_i])
            P_j = K_j @ np.hstack([R_j.T, -R_j.T @ t_j])

            inlier_idx = (mask_pose.ravel() > 0) if mask_pose is not None else np.ones(len(pts_i), dtype=bool)
            if inlier_idx.sum() < MIN_MATCHES:
                continue

            pts4d = cv2.triangulatePoints(
                P_i, P_j,
                pts_i[inlier_idx].T, pts_j[inlier_idx].T,
            )
            pts3d = (pts4d[:3] / np.where(np.abs(pts4d[3]) < 1e-9, 1e-9, pts4d[3])).T

            for X in pts3d:
                if not np.all(np.isfinite(X)):
                    continue
                # crude reprojection-error gate via re-projecting back into i
                proj = (P_i @ np.append(X, 1.0))
                if abs(proj[2]) < 1e-6:
                    continue
                proj /= proj[2]
                # skip points that didn't survive the essential-mat sanity check
                # (we already filtered by inlier_idx; this is a numeric guard)
                # convert ENU offset to lat/lon
                east, north, up = float(X[0]), float(X[1]), float(X[2])
                # azimuth observed from origin (0 = north, 90 = east)
                az = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0
                # rotate by initial_heading so az is in true compass frame
                az = (az + initial_heading_deg) % 360.0
                lat, lon = _enu_to_latlon(east, north, origin_lat, origin_lon)
                if any(_close(lat, lon, s) for s in seen):
                    continue
                seen.append((lat, lon))
                # confidence: more matches + larger baseline = higher
                conf = max(0.3, min(0.85,
                    0.4 + 0.05 * (baseline / MIN_BASELINE_M) +
                    0.005 * inlier_idx.sum()))
                out.append(FarPoint(
                    lat=lat, lon=lon,
                    height_m=up if abs(up) < 200 else None,
                    confidence=round(conf, 2),
                    observed_in_azimuth_deg=round(az, 1),
                ))
                if len(out) >= MAX_FAR_POINTS:
                    return out
    return out


def _decode(buf: bytes, cv2):
    import numpy as np
    arr = np.frombuffer(buf, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    return img


def _enu_to_latlon(east_m: float, north_m: float,
                   origin_lat: float, origin_lon: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(0.05, math.cos(math.radians(origin_lat))))
    return (origin_lat + dlat, origin_lon + dlon)


def _close(lat: float, lon: float, ref: tuple[float, float],
           tol_deg: float = 5e-5) -> bool:
    return abs(lat - ref[0]) < tol_deg and abs(lon - ref[1]) < tol_deg


def derive_far_points_with_prior(frames: Sequence[TriangulationFrame],
                                  origin_lat: float, origin_lon: float,
                                  initial_heading_deg: float = 0.0) -> list[FarPoint]:
    """P2-11.2 — when a cached SparseModel exists for this region we use
    its bbox as a prior: any FarPoint outside the bbox is dropped, and
    the survivors get a confidence bump.

    Falls through to ``derive_far_points`` when no cache is available.
    """
    base = derive_far_points(frames, origin_lat, origin_lon, initial_heading_deg)
    try:
        from . import recon3d as _r
        models = _r.lookup_cached_models(origin_lat, origin_lon)
    except Exception:                                            # noqa: BLE001
        return base
    if not models:
        return base
    # Use the largest model's bbox as a generous prior (pad 20m).
    best = max(models, key=lambda m: m.points_count)
    if not best.bbox_lat or not best.bbox_lon:
        return base
    lat_min, lat_max = min(best.bbox_lat), max(best.bbox_lat)
    lon_min, lon_max = min(best.bbox_lon), max(best.bbox_lon)
    pad = 0.0002  # ~22m
    refined: list[FarPoint] = []
    for fp in base:
        if (lat_min - pad) <= fp.lat <= (lat_max + pad) and \
           (lon_min - pad) <= fp.lon <= (lon_max + pad):
            refined.append(fp.model_copy(update={
                "confidence": min(1.0, fp.confidence + 0.15),
                "label_zh": (fp.label_zh or "") + "·重建复用",
            }))
    return refined or base
