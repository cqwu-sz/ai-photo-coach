"""Indoor POI lookup (W1.2).

Returns ``IndoorContext`` records describing nice indoor shooting hotspots
inside known buildings (mall atriums, museum halls, hotel lobbies, etc.).

Provider routing is configurable: AMap Indoor first (preferred for CN),
Mapbox Indoor as overseas fallback. Both are wrapped in a small known-
building registry — without an entry we skip the network entirely so we
don't burn quota guessing whether a random GPS fix is indoor.

A real production deployment would replace the registry with a polygon
test against a building footprint table. The registry is intentionally a
plain JSON file so designers / ops can extend it without code changes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from ..models.schemas import IndoorContext

log = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "indoor_buildings.json"
TIMEOUT_SEC = 1.5
AMAP_INDOOR_URL = "https://restapi.amap.com/v3/place/text"   # placeholder
MAPBOX_INDOOR_URL = "https://api.mapbox.com/v4/indoor/features.json"  # placeholder


@dataclass(frozen=True, slots=True)
class IndoorHotspot:
    name_zh: str
    floor: str
    x_floor: float
    y_floor: float


def _load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8")).get("buildings", [])
    except Exception as e:                                          # noqa: BLE001
        log.info("indoor_buildings.json load failed: %s", e)
        return []


def _haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    R = 6371008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


async def lookup_indoor(lat: float, lon: float,
                        provider: str = "amap",
                        amap_key: Optional[str] = None,
                        mapbox_token: Optional[str] = None,
                        ) -> list[IndoorContext]:
    """Return ``IndoorContext`` candidates for a (lat, lon).

    - Always seeds from the local registry (radius 80 m). When a building
      is hit, every hotspot in that building turns into one IndoorContext.
    - Optionally enriches with the configured external provider; failures
      are logged and silently ignored.
    """
    out: list[IndoorContext] = list(_from_registry(lat, lon))
    if not out:
        # No known building → don't bother the external API. We don't
        # have arbitrary-building floor plans and guessing is worse than
        # silence.
        return []
    try:
        if provider == "amap" and amap_key:
            await asyncio.wait_for(
                _enrich_amap(lat, lon, amap_key, out), timeout=TIMEOUT_SEC,
            )
        elif provider == "mapbox" and mapbox_token:
            await asyncio.wait_for(
                _enrich_mapbox(lat, lon, mapbox_token, out), timeout=TIMEOUT_SEC,
            )
    except Exception as e:                                          # noqa: BLE001
        log.info("indoor enrich (%s) failed: %s", provider, e)
    return out


def _from_registry(lat: float, lon: float, radius_m: float = 80) -> list[IndoorContext]:
    out: list[IndoorContext] = []
    for b in _load_registry():
        b_lat = b.get("lat"); b_lon = b.get("lon")
        if b_lat is None or b_lon is None:
            continue
        if _haversine_m(lat, lon, b_lat, b_lon) > radius_m:
            continue
        b_id = str(b.get("id") or b.get("name_zh") or "indoor")
        b_name = b.get("name_zh")
        for h in b.get("hotspots", []):
            out.append(IndoorContext(
                building_id=b_id,
                building_name_zh=b_name,
                floor=h.get("floor"),
                hotspot_label_zh=h.get("name_zh"),
                image_ref=b.get("floorplan_ref"),
                x_floor=h.get("x_floor"),
                y_floor=h.get("y_floor"),
            ))
    return out


async def _enrich_amap(lat: float, lon: float, key: str,
                       results: list[IndoorContext]) -> None:
    """Best-effort enrichment hook. Real AMap Indoor needs the indoor
    licence and the call shape varies per app; we keep this as a stub
    that ops can wire to the actual endpoint when a key is provisioned."""
    # NOTE: Intentionally minimal; the registry already seeds usable data.
    return None


async def _enrich_mapbox(lat: float, lon: float, token: str,
                         results: list[IndoorContext]) -> None:
    """Same idea for Mapbox Indoor."""
    return None


def to_prompt_block(items: list[IndoorContext]) -> str:
    if not items:
        return ""
    lines = ["── INDOOR HOTSPOTS（用户疑似在已知室内场所）──"]
    for it in items[:6]:
        lines.append(
            "  · {b}{floor}{spot}".format(
                b=it.building_name_zh or it.building_id,
                floor=f" {it.floor}" if it.floor else "",
                spot=f" / {it.hotspot_label_zh}" if it.hotspot_label_zh else "",
            )
        )
    lines.append("  当用户处于室内时，优先推荐这些热点位置而不是户外构图。")
    return "\n".join(lines)
