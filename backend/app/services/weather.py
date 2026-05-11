"""Open-Meteo weather lookup — free, no API key required.

Endpoint:
    https://api.open-meteo.com/v1/forecast
        ?latitude=...&longitude=...
        &current=cloud_cover,visibility,uv_index,temperature_2m,weather_code

Why we add it:
    Cloud cover decides whether the sun's golden-hour color temperature
    actually reaches the subject (overcast = soft, hazy, lower contrast),
    which is critical input for the "光影" scene mode. Visibility + UV
    let the LLM judge atmospheric clarity (mountain shots vs city haze).

Usage:
    snap = await fetch_current(lat, lon)
    snap.cloud_cover_pct  # 0..100, None on failure
    snap.softness         # "soft" | "hard" | "mixed"

Resilience:
    - 1.5s timeout — analyze must not stall waiting for weather.
    - Network errors / non-200 / parse errors all return ``None``; the
      analyze pipeline falls back gracefully without weather context.
    - 5 min in-memory TTL keyed on rounded (lat, lon) so a single user
      revisiting twice in five minutes never hits the upstream twice.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SEC = 1.5
CACHE_TTL_SEC = 5 * 60


# Open-Meteo "weather_code" reference (WMO):
#   0 clear, 1-3 mainly clear / partly / overcast,
#   45/48 fog, 51-57 drizzle, 61-67 rain, 71-77 snow,
#   80-82 rain showers, 85-86 snow showers, 95-99 thunderstorms.
_CODE_LABELS_ZH = {
    0:  "晴",
    1:  "多云转晴",
    2:  "局部多云",
    3:  "阴",
    45: "雾",
    48: "雾凇",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "霰",
    80: "阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷阵雨夹冰雹",
    99: "强雷暴",
}


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    """Photographer-friendly weather summary for the prompt + UI."""

    cloud_cover_pct: Optional[int]            # 0..100
    visibility_m: Optional[int]               # meters; > 10000 typically "unlimited"
    uv_index: Optional[float]                 # 0..11+
    temperature_c: Optional[float]
    weather_code: Optional[int]               # WMO
    softness: str                             # "soft" | "hard" | "mixed" | "unknown"
    code_label_zh: Optional[str]

    def to_dict(self) -> dict:
        return {
            "cloud_cover_pct": self.cloud_cover_pct,
            "visibility_m":    self.visibility_m,
            "uv_index":        self.uv_index,
            "temperature_c":   self.temperature_c,
            "weather_code":    self.weather_code,
            "softness":        self.softness,
            "code_label_zh":   self.code_label_zh,
        }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


_cache: dict[tuple[float, float], tuple[float, WeatherSnapshot]] = {}
_cache_lock = asyncio.Lock()


def _round(lat: float, lon: float) -> tuple[float, float]:
    """Round to 2 decimal places (~1.1 km) for cache locality without
    accidentally collapsing distinct cities."""
    return round(lat, 2), round(lon, 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_current(lat: float, lon: float, *, client: Optional[httpx.AsyncClient] = None) -> Optional[WeatherSnapshot]:
    """Fetch current weather. Returns ``None`` on any failure so callers
    can degrade gracefully. Caches for 5 minutes per (lat, lon)."""
    key = _round(lat, lon)
    now = time.monotonic()

    async with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SEC:
            return cached[1]

    own_client = client is None
    cli = client or httpx.AsyncClient(timeout=TIMEOUT_SEC)
    try:
        params = {
            "latitude":  lat,
            "longitude": lon,
            "current":   "cloud_cover,visibility,uv_index,temperature_2m,weather_code",
        }
        try:
            resp = await cli.get(OPEN_METEO_URL, params=params)
        except httpx.HTTPError as e:
            log.info("weather lookup failed (network): %s", e)
            return None
        if resp.status_code != 200:
            log.info("weather lookup non-200: %s", resp.status_code)
            return None
        try:
            payload = resp.json()
        except ValueError:
            log.info("weather lookup payload not JSON")
            return None
    finally:
        if own_client:
            await cli.aclose()

    snap = _from_payload(payload)
    if snap is not None:
        async with _cache_lock:
            _cache[key] = (now, snap)
    return snap


def _from_payload(payload: dict) -> Optional[WeatherSnapshot]:
    cur = payload.get("current") or {}
    try:
        cloud = cur.get("cloud_cover")
        vis   = cur.get("visibility")
        uv    = cur.get("uv_index")
        temp  = cur.get("temperature_2m")
        code  = cur.get("weather_code")
    except AttributeError:
        return None

    cloud_int = int(cloud) if cloud is not None else None
    vis_int   = int(vis)   if vis   is not None else None
    code_int  = int(code)  if code  is not None else None

    return WeatherSnapshot(
        cloud_cover_pct = cloud_int,
        visibility_m    = vis_int,
        uv_index        = float(uv) if uv is not None else None,
        temperature_c   = float(temp) if temp is not None else None,
        weather_code    = code_int,
        softness        = _classify_softness(cloud_int, code_int),
        code_label_zh   = _CODE_LABELS_ZH.get(code_int) if code_int is not None else None,
    )


def _classify_softness(cloud_pct: Optional[int], weather_code: Optional[int]) -> str:
    """Photographer's rule of thumb: high cloud_cover + drizzle/fog =>
    soft, diffused light (great for skin / portraits, kills rim light).
    Clear sky + low cloud => hard light with sharp shadows."""
    # Fog / mist / drizzle / rain / snow are all soft, regardless of cloud %.
    if weather_code in {45, 48, 51, 53, 55, 61, 63, 65, 71, 73, 75, 77}:
        return "soft"
    if cloud_pct is None:
        return "unknown"
    if cloud_pct >= 75:
        return "soft"
    if cloud_pct >= 35:
        return "mixed"
    return "hard"


# ---------------------------------------------------------------------------
# Prompt formatter
# ---------------------------------------------------------------------------


def to_prompt_block(snap: WeatherSnapshot) -> str:
    """Format weather as Chinese bullet list for the prompt."""
    lines: list[str] = []
    if snap.code_label_zh:
        lines.append(f"  · 天气：{snap.code_label_zh}")
    if snap.cloud_cover_pct is not None:
        lines.append(f"  · 云量：{snap.cloud_cover_pct}% ({_softness_zh(snap.softness)}光)")
    if snap.visibility_m is not None:
        # Clamp display: > 10 km treat as "通透"
        vis_km = snap.visibility_m / 1000
        if vis_km >= 10:
            lines.append(f"  · 能见度：>10 km（通透，远景细节保留）")
        else:
            lines.append(f"  · 能见度：约 {vis_km:.1f} km（注意远景灰雾感）")
    if snap.uv_index is not None and snap.uv_index >= 6:
        lines.append(f"  · UV 指数：{snap.uv_index:.0f}（强紫外线，注意高光过曝）")
    if snap.temperature_c is not None:
        lines.append(f"  · 气温：{snap.temperature_c:.0f}°C（影响模特舒适度，户外久站避免）")
    return "\n".join(lines)


def _softness_zh(softness: str) -> str:
    return {"soft": "软", "hard": "硬", "mixed": "半软半硬", "unknown": "未知"}.get(softness, "未知")


# ---------------------------------------------------------------------------
# v12 — Provider protocol + Open-Meteo / Mock implementations
# ---------------------------------------------------------------------------
#
# This indirection lets us swap weather sources without touching the
# scene_aggregate / prompts call sites:
#   - OpenMeteoProvider: production default (free, no key).
#   - MockProvider: used by pytest, returns whatever you preload.
#   - WeatherKitProvider: stub for when the iOS client provides cached
#     WeatherKit snapshots over the wire (no backend Apple Dev needed).

from typing import Protocol, runtime_checkable


@runtime_checkable
class WeatherProvider(Protocol):
    async def fetch_current(self, lat: float, lon: float) -> Optional[WeatherSnapshot]: ...
    async def fetch_minutely_15(self, lat: float, lon: float, hours: int = 1) -> Optional[list[dict]]: ...


class OpenMeteoProvider:
    """Default provider that wraps the existing module-level functions."""
    async def fetch_current(self, lat: float, lon: float) -> Optional[WeatherSnapshot]:
        return await fetch_current(lat, lon)

    async def fetch_minutely_15(self, lat: float, lon: float, hours: int = 1) -> Optional[list[dict]]:
        """Return up to `hours` of 15-minute forecast points for the
        next hour: cloud_cover, visibility, weather_code. Used to
        predict golden_hour_countdown / cloud_in_30min.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as cli:
                resp = await cli.get(
                    OPEN_METEO_URL,
                    params={
                        "latitude": lat, "longitude": lon,
                        "minutely_15": "cloud_cover,visibility,weather_code",
                        "forecast_minutely_15": str(min(hours * 4, 24)),
                    },
                )
                if resp.status_code != 200:
                    return None
                payload = resp.json()
        except Exception as e:                     # pragma: no cover
            log.info("minutely forecast failed: %s", e)
            return None
        m15 = payload.get("minutely_15") or {}
        times = m15.get("time") or []
        if not times:
            return None
        out: list[dict] = []
        for i, t in enumerate(times):
            out.append({
                "time":         t,
                "cloud_cover":  (m15.get("cloud_cover") or [None])[i],
                "visibility":   (m15.get("visibility") or [None])[i],
                "weather_code": (m15.get("weather_code") or [None])[i],
            })
        return out


class MockProvider:
    def __init__(self, snapshot: Optional[WeatherSnapshot] = None,
                 minutely: Optional[list[dict]] = None):
        self.snapshot = snapshot
        self.minutely = minutely

    async def fetch_current(self, lat: float, lon: float) -> Optional[WeatherSnapshot]:
        return self.snapshot

    async def fetch_minutely_15(self, lat: float, lon: float, hours: int = 1) -> Optional[list[dict]]:
        return self.minutely


# Module-level singleton for now — swappable in tests via
# ``weather.PROVIDER = MockProvider(...)``.
PROVIDER: WeatherProvider = OpenMeteoProvider()


# ---------------------------------------------------------------------------
# v12 — light prediction helpers
# ---------------------------------------------------------------------------

def predict_cloud_in_30min(minutely: list[dict]) -> Optional[float]:
    """Probability that cloud cover will be > 70% within the next 30
    min. Heuristic: take the next two 15-min steps; return fraction
    that exceed the threshold.
    """
    if not minutely:
        return None
    window = minutely[:2]   # next 30 min
    high = sum(1 for p in window if (p.get("cloud_cover") or 0) > 70)
    return round(high / len(window), 2)


def golden_hour_countdown(altitude_now_deg: float, altitude_in_15_deg: float) -> Optional[int]:
    """Minutes until sun altitude drops into the golden range (0-6°
    above horizon). Returns None if already in range or sun is rising
    rather than setting.
    """
    if 0 <= altitude_now_deg <= 6:
        return 0
    if altitude_now_deg < 0 or altitude_in_15_deg >= altitude_now_deg:
        return None
    rate_per_min = (altitude_now_deg - altitude_in_15_deg) / 15.0
    if rate_per_min <= 0:
        return None
    minutes_to_6 = (altitude_now_deg - 6) / rate_per_min
    return max(0, int(minutes_to_6))
