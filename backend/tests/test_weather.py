"""Tests for the Open-Meteo weather client + analyze integration.

We mock the Open-Meteo HTTP call with respx for the weather-client unit
tests so the suite stays offline. The recapture-decision tests inject
synthetic responses directly into ``AnalyzeService._decide_recapture_hint``
since that's a pure function — no HTTP needed.

What we verify:
  1. fetch_current parses cloud_cover, visibility, uv_index, temp,
     weather_code into a WeatherSnapshot, with the right softness label.
  2. softness flips correctly for clear / overcast / fog edge cases.
  3. fetch_current returns None on network errors / non-200 / bad JSON
     so analyze never blocks on weather.
  4. The 5-minute cache short-circuits a second call to the same lat/lon.
  5. The prompt block formatter folds softness into Chinese text.
  6. _decide_recapture_hint fires only for light_shadow + weak evidence.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.services import weather as weather_service


def _payload(cloud: int, code: int, *, vis: int = 18000, uv: float = 4.0, temp: float = 22.0) -> dict:
    return {
        "current": {
            "cloud_cover":   cloud,
            "visibility":    vis,
            "uv_index":      uv,
            "temperature_2m": temp,
            "weather_code":  code,
        },
    }


# ---------------------------------------------------------------------------
# fetch_current happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_clear_sky_is_hard_light() -> None:
    weather_service._cache.clear()
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=_payload(cloud=10, code=0)),
    )
    snap = await weather_service.fetch_current(31.23, 121.47)
    assert snap is not None
    assert snap.cloud_cover_pct == 10
    assert snap.softness == "hard"
    assert snap.code_label_zh == "晴"
    assert snap.visibility_m == 18000


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_overcast_is_soft_light() -> None:
    weather_service._cache.clear()
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=_payload(cloud=92, code=3)),
    )
    snap = await weather_service.fetch_current(31.30, 121.50)
    assert snap is not None
    assert snap.softness == "soft"
    assert snap.code_label_zh == "阴"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_fog_is_soft_regardless_of_cloud() -> None:
    weather_service._cache.clear()
    # Cloud cover 40 (which would normally be "mixed") but weather code
    # 45 = fog — photographer-relevant softness is still "soft".
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=_payload(cloud=40, code=45)),
    )
    snap = await weather_service.fetch_current(31.40, 121.60)
    assert snap is not None
    assert snap.softness == "soft"
    assert snap.code_label_zh == "雾"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_partly_cloudy_is_mixed() -> None:
    weather_service._cache.clear()
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=_payload(cloud=50, code=2)),
    )
    snap = await weather_service.fetch_current(31.50, 121.70)
    assert snap is not None
    assert snap.softness == "mixed"


# ---------------------------------------------------------------------------
# Failure modes — analyze must never crash on weather
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_returns_none_on_network_error() -> None:
    weather_service._cache.clear()
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        side_effect=httpx.ConnectError("offline"),
    )
    snap = await weather_service.fetch_current(31.99, 121.99)
    assert snap is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_returns_none_on_non_200() -> None:
    weather_service._cache.clear()
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(503, text="service unavailable"),
    )
    snap = await weather_service.fetch_current(31.98, 121.98)
    assert snap is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_returns_none_on_bad_json() -> None:
    weather_service._cache.clear()
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, text="not json at all"),
    )
    snap = await weather_service.fetch_current(31.97, 121.97)
    assert snap is None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_current_caches_per_rounded_coords() -> None:
    weather_service._cache.clear()
    route = respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=_payload(cloud=20, code=1)),
    )
    snap1 = await weather_service.fetch_current(31.231, 121.474)
    # Second call within 5 min, same rounded key (2 decimals -> 31.23, 121.47).
    snap2 = await weather_service.fetch_current(31.234, 121.473)
    assert snap1 is not None and snap2 is not None
    assert route.call_count == 1
    assert snap1.softness == snap2.softness


# ---------------------------------------------------------------------------
# Prompt block formatter
# ---------------------------------------------------------------------------


def test_to_prompt_block_includes_softness_chinese() -> None:
    snap = weather_service.WeatherSnapshot(
        cloud_cover_pct=85,
        visibility_m=8500,
        uv_index=7.2,
        temperature_c=17.5,
        weather_code=3,
        softness="soft",
        code_label_zh="阴",
    )
    block = weather_service.to_prompt_block(snap)
    assert "阴" in block
    assert "软" in block
    assert "云量：85%" in block
    assert "8.5 km" in block
    assert "UV" in block


# ---------------------------------------------------------------------------
# AnalyzeService recapture decision (pure-function tests)
# ---------------------------------------------------------------------------


def _frames(n: int = 6) -> list:
    from app.models import FrameMeta
    return [FrameMeta(index=i, azimuth_deg=float(i * 45), pitch_deg=0.0) for i in range(n)]


@pytest.mark.asyncio
async def test_recapture_hint_silent_for_non_light_shadow_modes() -> None:
    from app.config import Settings
    from app.models import CaptureMeta, IphoneLens, QualityMode, SceneMode
    from app.services.analyze_service import AnalyzeService

    settings = Settings(mock_mode=True)
    service = AnalyzeService(settings)

    for mode in (SceneMode.portrait, SceneMode.documentary, SceneMode.scenery):
        meta = CaptureMeta(
            person_count=1 if mode != SceneMode.scenery else 0,
            quality_mode=QualityMode.fast,
            scene_mode=mode,
            device_lens=IphoneLens.wide_1x,
            frame_meta=_frames(),
        )
        response = await service.run(meta=meta, frames=[b""], references=[])
        assert response.light_recapture_hint is None, f"mode={mode}"


def test_recapture_hint_fires_when_light_shadow_no_geo_low_confidence() -> None:
    """light_shadow + no geo + LLM unsure -> backend nudges user."""
    from app.models import (
        CaptureMeta, IphoneLens, QualityMode, SceneMode,
        AnalyzeResponse, SceneSummary, Lighting, VisionLightHint,
    )
    from app.services.analyze_service import AnalyzeService

    meta = CaptureMeta(
        person_count=1,
        quality_mode=QualityMode.fast,
        scene_mode=SceneMode.light_shadow,
        device_lens=IphoneLens.wide_1x,
        frame_meta=_frames(),
    )
    # Synthesize a response with weak vision_light evidence.
    scene = SceneSummary(
        type="outdoor",
        lighting=Lighting.golden_hour,
        background_summary="x",
        cautions=[],
        vision_light=VisionLightHint(
            direction_deg=None, quality="unknown", confidence=0.05, notes="低信心",
        ),
    )
    resp = AnalyzeResponse(scene=scene, shots=[])
    hint = AnalyzeService._decide_recapture_hint(meta, resp, "light_shadow")
    assert hint is not None and hint.enabled is True
    assert "光" in hint.title


def test_recapture_hint_silent_when_high_vision_confidence() -> None:
    """Strong LLM-derived light evidence is enough — no nudge."""
    from app.models import (
        CaptureMeta, IphoneLens, QualityMode, SceneMode,
        AnalyzeResponse, SceneSummary, Lighting, VisionLightHint,
    )
    from app.services.analyze_service import AnalyzeService

    meta = CaptureMeta(
        person_count=1,
        quality_mode=QualityMode.fast,
        scene_mode=SceneMode.light_shadow,
        device_lens=IphoneLens.wide_1x,
        frame_meta=_frames(),
    )
    scene = SceneSummary(
        type="outdoor",
        lighting=Lighting.golden_hour,
        background_summary="x",
        cautions=[],
        vision_light=VisionLightHint(
            direction_deg=240.0, quality="hard", confidence=0.8, notes="高信心",
        ),
    )
    resp = AnalyzeResponse(scene=scene, shots=[])
    hint = AnalyzeService._decide_recapture_hint(meta, resp, "light_shadow")
    assert hint is None


def test_recapture_hint_silent_when_geo_present() -> None:
    """Geo gives us a real sun calculation — never nudge."""
    from app.models import (
        CaptureMeta, GeoFix, IphoneLens, QualityMode, SceneMode,
        AnalyzeResponse, SceneSummary, Lighting,
    )
    from app.services.analyze_service import AnalyzeService

    meta = CaptureMeta(
        person_count=1,
        quality_mode=QualityMode.fast,
        scene_mode=SceneMode.light_shadow,
        device_lens=IphoneLens.wide_1x,
        frame_meta=_frames(),
        geo=GeoFix(lat=31.23, lon=121.47),
    )
    scene = SceneSummary(
        type="outdoor",
        lighting=Lighting.golden_hour,
        background_summary="x",
        cautions=[],
        vision_light=None,
    )
    resp = AnalyzeResponse(scene=scene, shots=[])
    hint = AnalyzeService._decide_recapture_hint(meta, resp, "light_shadow")
    assert hint is None
