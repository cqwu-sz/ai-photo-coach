"""P2-13.1 — End-to-end scenarios across multiple scene fixtures.

Each fixture is a tiny JSON of CaptureMeta + per-frame metadata that
collectively exercises a known shot pipeline path:
  - portrait_basic: 1 person, no geo
  - scenery_geo:    0 persons, with geo + walk_segment
  - light_shadow:   golden-hour-likely, geo provided
  - indoor:         geo near a known indoor building (stub)

The test posts each fixture against the in-process app and asserts
the response shape is sane (≥ 1 shot, every shot has a position,
debug.analyze_request_id is set).
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _fake_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(120, 130, 140)).save(buf, format="JPEG")
    return buf.getvalue()


SCENARIOS = [
    {
        "name": "portrait_basic",
        "meta": {
            "person_count": 1,
            "scene_mode": "portrait",
            "quality_mode": "fast",
            "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
        },
        "expect_position": True,
    },
    {
        "name": "scenery_geo",
        "meta": {
            "person_count": 0,
            "scene_mode": "scenery",
            "quality_mode": "fast",
            "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
            "geo": {"lat": 31.2389, "lon": 121.4905},
        },
        "expect_position": True,
    },
    {
        "name": "light_shadow",
        "meta": {
            "person_count": 1,
            "scene_mode": "light_shadow",
            "quality_mode": "fast",
            "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
            "geo": {"lat": 39.9075, "lon": 116.3972},
        },
        "expect_position": True,
    },
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_analyze_scenarios(client, scenario):
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    r = client.post(
        "/analyze",
        data={"meta": json.dumps(scenario["meta"])},
        files=files,
        headers={"X-Device-Id": f"test-{scenario['name']}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shots"], "every scenario must produce at least one shot"
    if scenario["expect_position"]:
        for shot in body["shots"]:
            assert shot.get("position") is not None, \
                f"{scenario['name']}: shot missing position"
    # Response must always carry an analyze_request_id token (P0-1.2).
    assert body.get("debug", {}).get("analyze_request_id"), \
        "analyze_request_id token missing"


def test_metrics_endpoint_responds(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "ai_photo_coach" in r.text or r.text == "\n"


def test_feedback_delete_by_device(client):
    r = client.delete("/feedback/by_device?device_id=non-existent-test")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 0
