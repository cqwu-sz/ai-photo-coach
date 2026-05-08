import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.models import AnalyzeResponse


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _fake_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(128, 128, 128)).save(buf, format="JPEG")
    return buf.getvalue()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["mock_mode"] is True


def test_analyze_mock_single_person(client):
    meta = {
        "person_count": 1,
        "quality_mode": "fast",
        "style_keywords": ["clean"],
        "frame_meta": [
            {"index": i, "azimuth_deg": i * 45.0, "pitch_deg": 0, "timestamp_ms": i * 500}
            for i in range(8)
        ],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    r = client.post(
        "/analyze",
        data={"meta": json.dumps(meta)},
        files=files,
    )
    assert r.status_code == 200, r.text
    parsed = AnalyzeResponse.model_validate(r.json())
    assert len(parsed.shots) >= 1
    assert parsed.shots[0].poses[0].person_count == 1


def test_analyze_mock_three_person(client):
    meta = {
        "person_count": 3,
        "quality_mode": "fast",
        "frame_meta": [
            {"index": i, "azimuth_deg": i * 30.0}
            for i in range(8)
        ],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    r = client.post(
        "/analyze",
        data={"meta": json.dumps(meta)},
        files=files,
    )
    assert r.status_code == 200, r.text
    parsed = AnalyzeResponse.model_validate(r.json())
    assert parsed.shots[0].poses[0].person_count == 3


def test_analyze_rejects_too_few_frames(client):
    meta = {
        "person_count": 1,
        "frame_meta": [{"index": 0, "azimuth_deg": 0}],
    }
    files = [("frames", ("f0.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))]
    r = client.post("/analyze", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 400


def test_analyze_with_reference_thumbnails(client):
    meta = {
        "person_count": 2,
        "quality_mode": "fast",
        "style_keywords": ["moody"],
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    files += [
        ("reference_thumbnails", (f"ref{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(3)
    ]
    r = client.post("/analyze", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "personalization" in body.get("debug", {})


def test_analyze_rejects_too_many_references(client):
    meta = {
        "person_count": 2,
        "quality_mode": "fast",
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    files += [
        ("reference_thumbnails", (f"ref{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(20)
    ]
    r = client.post("/analyze", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 413


def test_analyze_rejects_meta_mismatch(client):
    meta = {
        "person_count": 1,
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(4)
    ]
    r = client.post("/analyze", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 400


def test_analyze_accepts_scenery_mode_with_zero_people(client):
    meta = {
        "person_count": 0,
        "scene_mode": "scenery",
        "quality_mode": "fast",
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    r = client.post("/analyze", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    for shot in body["shots"]:
        assert shot["poses"] == []


def test_analyze_rejects_zero_people_for_portrait(client):
    meta = {
        "person_count": 0,
        "scene_mode": "portrait",
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    r = client.post("/analyze", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 400


def test_analyze_passes_byok_fields(client, monkeypatch):
    """When the user supplies model_id + key + base_url they are routed
    into AnalyzeService.run; mock_mode short-circuits before any provider
    is built so we just verify the endpoint accepts the form fields."""
    meta = {
        "person_count": 1,
        "quality_mode": "fast",
        "scene_mode": "portrait",
        "frame_meta": [
            {"index": i, "azimuth_deg": i * 45.0} for i in range(8)
        ],
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    r = client.post(
        "/analyze",
        data={
            "meta": json.dumps(meta),
            "model_id": "glm-4.6v",
            "model_api_key": "user-byok-key",
            "model_base_url": "https://custom.example.com/v1",
        },
        files=files,
    )
    assert r.status_code == 200, r.text
    parsed = AnalyzeResponse.model_validate(r.json())
    assert parsed.shots


def test_models_endpoint_lists_glm_default(client):
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["enable_byok"] is True
    ids = [m["id"] for m in body["models"]]
    assert "gemini-2.5-flash" in ids
    assert "glm-4.6v" in ids
    assert "gpt-4o" in ids
    assert "qwen-vl-max" in ids
    assert "deepseek-vl2" in ids
    assert "moonshot-v1-128k-vision-preview" in ids


def test_models_test_endpoint_handles_missing_key(client):
    r = client.post(
        "/models/test",
        json={"model_id": "glm-4.6v"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "unauthorized" in (body.get("error") or "")
