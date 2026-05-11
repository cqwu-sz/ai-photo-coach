"""P2-12.3 — Pydantic ↔ iOS Codable contract test.

We can't compile Swift inside pytest but we *can* verify the JSON the
iOS encoder is supposed to produce decodes cleanly into the backend
Pydantic models. The fixture below mirrors the shape JSONEncoder
(snake_case + ISO8601) emits given the iOS structs in
``ios/AIPhotoCoach/Models/Schemas.swift``.

Update both sides together — when iOS adds a field to WalkSegment,
update this fixture in the same PR so the contract stays honest.
"""
from __future__ import annotations

from app.models.schemas import WalkSegment, GpsSample, CaptureMeta


def _ios_walk_segment_json() -> dict:
    return {
        "source": "arkit",
        "initial_heading_deg": 35.0,
        "poses": [
            {"t_ms": 0, "x": 0.0, "y": 0.0, "z": 0.0,
             "qx": 0, "qy": 0, "qz": 0, "qw": 1},
            {"t_ms": 5000, "x": 4.2, "y": -1.1, "z": 0.0,
             "qx": 0, "qy": 0, "qz": 0, "qw": 1},
        ],
        "sparse_points": None,
        "gps_track": [
            {"t_ms": 0, "lat": 31.2389, "lon": 121.4905, "accuracy_m": 4.5},
            {"t_ms": 5000, "lat": 31.23895, "lon": 121.49055, "accuracy_m": 4.0},
        ],
        "keyframes_b64": [
            {"t_ms": 0, "dataUrl": "data:image/jpeg;base64,/9j/4AAQ"},
        ],
    }


def test_ios_walk_segment_decodes():
    seg = WalkSegment.model_validate(_ios_walk_segment_json())
    assert seg.source == "arkit"
    assert len(seg.poses) == 2
    assert seg.gps_track and len(seg.gps_track) == 2
    assert isinstance(seg.gps_track[0], GpsSample)


def test_ios_capture_meta_with_walk():
    payload = {
        "person_count": 1,
        "scene_mode": "portrait",
        "quality_mode": "fast",
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
        "geo": {"lat": 31.2389, "lon": 121.4905},
        "walk_segment": _ios_walk_segment_json(),
    }
    meta = CaptureMeta.model_validate(payload)
    assert meta.walk_segment is not None
    assert meta.walk_segment.gps_track[0].lat == 31.2389
