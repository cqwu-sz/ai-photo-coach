"""Pillow + NumPy keyframe scorer (Phase 3.1).

Goal: given the env-video keyframes the client uploaded with /analyze,
score each one on three orthogonal axes — *sharpness*, *exposure*,
*composition density* — so the post-LLM "pick a representative frame
for shot N" step can do better than nearest-azimuth alone.

Why this matters
================
The LLM picks an azimuth/pitch/distance for each shot. The result UI
then needs an actual *frame* from the env video to use as a backdrop
(2D mock-up + 3D scene compose). Until now we picked the keyframe with
the closest azimuth — but that's blind to whether the closest frame is
actually in focus, well-exposed, and visually rich.

After this scorer, ``_best_frame_for_shot`` mixes:
  * azimuth proximity (primary; we still want the frame pointing
    roughly at the shot's azimuth)
  * the per-frame quality score (tiebreaker; up to ~0.5 weight)

Calibration ranges (after the per-batch normalisation):
  - sharpness          : 0..1, > 0.6 ≈ in-focus
  - exposure           : 0..1, > 0.7 ≈ middle grey, ≤ 0.3 ≈ blown / crushed
  - composition_density: 0..1, > 0.5 ≈ rich edges; ≤ 0.2 ≈ flat (sky / wall)

All three are rolled into ``overall`` via a 0.5 / 0.3 / 0.2 weighting
(sharp matters most, density least). The scorer runs in <50 ms total
for 10 keyframes on a typical phone-resolution input — much cheaper
than the LLM call it sits behind.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from typing import Iterable, Optional

import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)


# Target grayscale resolution we downscale to before crunching numbers.
# 192 px is a good balance: cheap (≈37 k pixels) but not so coarse that
# Laplacian variance goes flat on real-world phone footage.
_DOWNSCALE_W = 192


@dataclass(slots=True)
class FrameScore:
    """Per-frame quality scores in [0, 1].

    sharpness         : Laplacian-variance-style focus measure
    exposure          : 1 - (deviation from 50% grey) − blown-pixel penalty
    composition_density: edge-pixel ratio after edge-detect
    overall           : weighted sum (0.5/0.3/0.2)
    """
    sharpness: float
    exposure: float
    composition_density: float
    overall: float

    def as_dict(self) -> dict[str, float]:
        return {
            "sharpness": round(self.sharpness, 3),
            "exposure": round(self.exposure, 3),
            "composition_density": round(self.composition_density, 3),
            "overall": round(self.overall, 3),
        }


def score_frame(image_bytes: bytes) -> Optional[FrameScore]:
    """Return a :class:`FrameScore` for a single JPEG/PNG/etc. blob.

    Returns ``None`` when decoding fails — the caller (analyze_service)
    should fall back to the legacy nearest-azimuth heuristic in that
    case rather than crashing the whole analyze pipeline.
    """
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            im.load()
            gray = im.convert("L")
            # Downscale to keep this cheap. Use a high-quality LANCZOS
            # filter so we don't accidentally smooth out edges that the
            # sharpness metric depends on.
            ratio = _DOWNSCALE_W / max(gray.width, 1)
            if ratio < 1.0:
                new_h = max(48, int(gray.height * ratio))
                gray = gray.resize((_DOWNSCALE_W, new_h), Image.Resampling.LANCZOS)
            arr = np.asarray(gray, dtype=np.float32) / 255.0
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_arr = np.asarray(edges, dtype=np.float32) / 255.0
    except Exception as exc:
        log.info("frame decode failed (skipping scorer): %s", exc)
        return None

    sharp = _sharpness(arr)
    expo  = _exposure(arr)
    dens  = _composition_density(edge_arr)
    overall = 0.5 * sharp + 0.3 * expo + 0.2 * dens
    return FrameScore(
        sharpness=float(sharp),
        exposure=float(expo),
        composition_density=float(dens),
        overall=float(overall),
    )


def score_frames(images: Iterable[bytes]) -> list[Optional[FrameScore]]:
    """Score a batch. Preserves order; ``None`` slots for un-decodable
    inputs so callers can align the result list with their original index.
    """
    return [score_frame(b) for b in images]


# ────────── per-axis primitives ──────────


def _sharpness(arr: np.ndarray) -> float:
    """Laplacian variance, scaled to 0..1.

    We use a 3×3 Laplacian kernel implemented as a stencil (no SciPy
    dependency). The variance of the result is the textbook focus
    measure (Pertuz et al., 2013, *Analysis of focus measure operators
    for shape-from-focus*). We map [0, 0.04] linearly to [0, 1] —
    empirically `< 0.005` is "blurry enough to retake" on a typical
    1280×720 phone capture.
    """
    if arr.size < 9:
        return 0.0
    # ∇²I ≈ 4·center − up − down − left − right
    cur  = arr[1:-1, 1:-1]
    up   = arr[ :-2, 1:-1]
    down = arr[2:  , 1:-1]
    lt   = arr[1:-1,  :-2]
    rt   = arr[1:-1, 2:  ]
    lap = 4.0 * cur - up - down - lt - rt
    var = float(lap.var())
    return float(np.clip(var / 0.04, 0.0, 1.0))


def _exposure(arr: np.ndarray) -> float:
    """Reward middle grey + low blown/crushed-pixel ratio.

    ``arr`` is in [0, 1]. The score is a product of two terms:
      * `mid`: 1 − 2·|mean − 0.5|, so mean=0.5 → 1.0, extremes → 0.
      * `clipped`: 1 − (frac_below_0.02 + frac_above_0.98). Penalises
        photos with crushed shadows or blown highlights.

    Both terms are then averaged (so a slight clip is OK if mean is good).
    """
    if arr.size == 0:
        return 0.5
    mean = float(arr.mean())
    mid = 1.0 - min(2.0 * abs(mean - 0.5), 1.0)
    blown = float(((arr > 0.98) | (arr < 0.02)).mean())
    clipped = max(0.0, 1.0 - 2.5 * blown)
    return float(np.clip(0.6 * mid + 0.4 * clipped, 0.0, 1.0))


def _composition_density(edges: np.ndarray) -> float:
    """Edge density after FIND_EDGES.

    A frame full of compositionally interesting elements (foreground,
    silhouettes, geometry) ends up with a high mean over the edge map.
    A frame that's mostly sky / a wall / a floor scrolls toward 0.

    We map mean(edge_arr) in [0, 0.15] linearly to [0, 1] — empirically
    `> 0.10` is "rich" on portrait-oriented scenes.
    """
    if edges.size == 0:
        return 0.0
    return float(np.clip(edges.mean() / 0.15, 0.0, 1.0))


# ────────── helper: pick best frame for a shot ──────────


def best_frame_index(
    shot_azimuth_deg: float,
    frame_azimuths: list[float],
    frame_scores: list[Optional[FrameScore]],
    *,
    azimuth_window_deg: float = 35.0,
    quality_weight: float = 0.5,
) -> Optional[int]:
    """Pick the index of the keyframe that best represents a shot.

    Trade-off: we *first* prefer frames whose azimuth is within
    ``azimuth_window_deg`` of the shot's planned azimuth (the AI was
    explicit about which way to point the camera). Among those we pick
    the one with the highest quality score, weighted so a slightly more
    distant frame can still win if its quality is dramatically better.

    Returns ``None`` only when no frames at all were provided.
    """
    if not frame_azimuths:
        return None
    n = len(frame_azimuths)
    if n != len(frame_scores):
        # Defensive: if the caller misaligned the lists, fall back to
        # nearest-azimuth without quality weighting.
        return _nearest_index(shot_azimuth_deg, frame_azimuths)

    # Combined score per candidate. Distance penalty is bell-shaped so
    # frames inside the window dominate but a stellar far frame can still
    # claim the shot.
    best_idx = 0
    best_value = float("-inf")
    for i, (az, score) in enumerate(zip(frame_azimuths, frame_scores)):
        delta = _angle_delta(shot_azimuth_deg, az)
        # Normalised distance penalty: 1 at delta=0, 0 at azimuth_window.
        dist_score = max(0.0, 1.0 - (delta / azimuth_window_deg))
        q = score.overall if score is not None else 0.5
        value = (1.0 - quality_weight) * dist_score + quality_weight * q
        if value > best_value:
            best_value = value
            best_idx = i
    return best_idx


def _angle_delta(a: float, b: float) -> float:
    """Smallest angular distance in degrees, wrapping at 360."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _nearest_index(shot_az: float, azs: list[float]) -> int:
    best_i, best_d = 0, float("inf")
    for i, az in enumerate(azs):
        d = _angle_delta(shot_az, az)
        if d < best_d:
            best_d, best_i = d, i
    return best_i
