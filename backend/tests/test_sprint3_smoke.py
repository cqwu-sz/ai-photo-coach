"""Sprint 3 smoke tests: weather provider + feedback DB + light prediction."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import weather as weather_service


def test_predict_cloud_in_30min_high():
    pts = [
        {"cloud_cover": 80}, {"cloud_cover": 90},
        {"cloud_cover": 50}, {"cloud_cover": 30},
    ]
    assert weather_service.predict_cloud_in_30min(pts) == 1.0


def test_predict_cloud_in_30min_clear():
    pts = [{"cloud_cover": 10}, {"cloud_cover": 20}]
    assert weather_service.predict_cloud_in_30min(pts) == 0.0


def test_golden_hour_countdown_within_window():
    # Sun at 5° now → already in window.
    assert weather_service.golden_hour_countdown(5.0, 4.0) == 0


def test_golden_hour_countdown_descending():
    # Sun at 12°, dropping at ~0.4°/min → ~15 min to reach 6°.
    out = weather_service.golden_hour_countdown(12.0, 6.0)
    assert out is not None and 14 <= out <= 16


def test_golden_hour_countdown_rising():
    # Sun rising → no countdown.
    assert weather_service.golden_hour_countdown(10.0, 12.0) is None


def test_mock_weather_provider_returns_preloaded_snapshot():
    snap = weather_service.WeatherSnapshot(
        cloud_cover_pct=50, visibility_m=10000, uv_index=5.0,
        temperature_c=22.0, weather_code=2, softness="mixed",
        code_label_zh="局部多云",
    )
    p = weather_service.MockProvider(snapshot=snap)
    assert asyncio.run(p.fetch_current(30, 120)) is snap


def test_feedback_endpoint_round_trip(tmp_path, monkeypatch):
    # Redirect the DB into tmp dir so we don't pollute the real one.
    from app.api import feedback as fb
    monkeypatch.setattr(fb, "_DB_PATH", tmp_path / "shot_results.db")

    client = TestClient(app)
    payload = {
        "analyze_request_id": "req-abc",
        "style_keywords": ["japanese", "clean"],
        "geo_lat": 30.25, "geo_lon": 120.20,
        "captured_at_utc": "2026-05-09T07:30:00+00:00",
        "focal_length_mm": 5.96, "focal_length_35mm_eq": 26.0,
        "aperture": 1.78, "exposure_time_s": 0.005, "iso": 200,
        "white_balance_k": 5500,
    }
    r = client.post("/feedback/", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is True
    assert isinstance(body["row_id"], int)
    # Re-post: row_id should increment.
    r2 = client.post("/feedback/", json=payload)
    assert r2.json()["row_id"] == body["row_id"] + 1
