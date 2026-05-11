"""W4 — triangulation smoke. Tolerates absence of opencv (returns [])."""
from __future__ import annotations

from app.services import triangulation


def test_zero_frames_returns_empty():
    assert triangulation.derive_far_points([], 0.0, 0.0) == []


def test_single_frame_returns_empty():
    fake = triangulation.TriangulationFrame(
        image_bytes=b"", pose_t=(0, 0, 0),
        pose_R=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        fx=500, fy=500,
    )
    assert triangulation.derive_far_points([fake], 0.0, 0.0) == []
