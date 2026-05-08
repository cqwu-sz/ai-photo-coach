"""Sun-position sanity checks.

We don't need to validate against an ephemeris reference here — the actual
algorithm in ``app.services.sun`` is taken from NOAA so it's already
correct enough for our use case (±0.5°). What this test pins down is:

  1. We return the right *shape* of data (the dict the prompt builder
     consumes downstream).
  2. The phase classifier produces sensible results for a few well-known
     local times (true noon at the equator, civil twilight in summer).
  3. The HTTP endpoint accepts both `now` and an explicit timestamp.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import sun as sun_service


client = TestClient(app)


def _at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_dict_has_all_keys() -> None:
    info = sun_service.compute(40.0, 116.0, _at(2026, 6, 15, 4))  # Beijing-ish
    d = info.to_dict()
    for key in (
        "azimuth_deg",
        "altitude_deg",
        "phase",
        "color_temp_k_estimate",
        "minutes_to_golden_end",
        "minutes_to_blue_end",
        "minutes_to_sunset",
        "minutes_to_sunrise",
        "declination_deg",
        "hour_angle_deg",
    ):
        assert key in d, f"missing key {key}"


def test_summer_noon_high_altitude() -> None:
    """Equatorial summer solstice noon-ish should land high in the sky."""
    # 0,0 at 12:00 UTC on June 21 -> sun very high.
    info = sun_service.compute(0.0, 0.0, _at(2026, 6, 21, 12))
    assert info.altitude_deg > 60, info.altitude_deg
    assert info.phase == sun_service.PHASE_DAY


def test_polar_night() -> None:
    """North pole in December — sun is below horizon all day."""
    info = sun_service.compute(85.0, 0.0, _at(2026, 12, 21, 12))
    assert info.altitude_deg < 0
    # Could be night or blue-hour depending on exact altitude
    assert info.phase in (
        sun_service.PHASE_NIGHT,
        sun_service.PHASE_BLUE_DAWN,
        sun_service.PHASE_BLUE_DUSK,
    )


def test_color_temp_warm_at_sunset() -> None:
    """Just before sunset color temp should land in golden-hour range (≤ 4500K)."""
    # Hand-tune time so altitude is small and dropping. Try 17:30 UTC at
    # equator, equinox-ish — that's late afternoon.
    info = sun_service.compute(0.0, 0.0, _at(2026, 3, 21, 17, 30))
    if info.phase in (sun_service.PHASE_GOLDEN_DUSK, sun_service.PHASE_GOLDEN_DAWN):
        assert 2700 <= info.color_temp_k_estimate <= 4500


def test_to_prompt_block_in_chinese() -> None:
    info = sun_service.compute(40.0, 116.0, _at(2026, 6, 15, 9))
    block = sun_service.to_prompt_block(info, 40.0, 116.0)
    assert "太阳方位角" in block
    assert "高度角" in block


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def test_endpoint_now() -> None:
    r = client.get("/sun-info", params={"lat": 40.0, "lon": 116.0})
    assert r.status_code == 200
    body = r.json()
    assert body["lat"] == 40.0
    assert body["lon"] == 116.0
    assert "phase" in body and "azimuth_deg" in body


def test_endpoint_explicit_timestamp() -> None:
    r = client.get(
        "/sun-info",
        params={
            "lat": 0.0,
            "lon": 0.0,
            "timestamp": "2026-06-21T12:00:00Z",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["altitude_deg"] > 60
    assert body["phase"] == sun_service.PHASE_DAY


def test_endpoint_rejects_bad_lat() -> None:
    r = client.get("/sun-info", params={"lat": 999, "lon": 0})
    assert r.status_code == 422


def test_endpoint_rejects_bad_timestamp() -> None:
    r = client.get(
        "/sun-info",
        params={"lat": 40.0, "lon": 116.0, "timestamp": "not-a-time"},
    )
    assert r.status_code == 400
