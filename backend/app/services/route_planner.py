"""Walking-route planner (W3).

Given a user GeoFix and an absolute ShotPosition (lat, lon), call
AMap Direction/walking v5 to produce a ``WalkRoute``. Falls back to a
straight-line crow's-flight estimate if the API is unreachable, so the
shot card always shows *something*.

A small in-process LRU cache keyed on rounded coordinates (so repeated
analyze calls in the same area share results) keeps load light. Each
external call is bounded by ``TIMEOUT_SEC``.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Optional

import httpx

from ..models.schemas import WalkRoute, WalkRouteStep

log = logging.getLogger(__name__)

TIMEOUT_SEC = 1.5
AMAP_WALK_URL = "https://restapi.amap.com/v5/direction/walking"
WALKING_SPEED_M_S = 1.25
CACHE_TTL_S = 300

_cache: dict[tuple, tuple[float, WalkRoute]] = {}


def _cache_key(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> tuple:
    """Round to ~10 m so neighbouring calls share results."""
    return (round(o_lat, 4), round(o_lon, 4), round(d_lat, 4), round(d_lon, 4))


def _haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    R = 6371008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _straight_line_route(o_lat: float, o_lon: float,
                         d_lat: float, d_lon: float) -> WalkRoute:
    dist = _haversine_m(o_lat, o_lon, d_lat, d_lon)
    return WalkRoute(
        distance_m=round(dist, 1),
        duration_min=round(dist / WALKING_SPEED_M_S / 60, 1),
        polyline=f"{o_lon},{o_lat};{d_lon},{d_lat}",
        steps=[WalkRouteStep(
            instruction_zh=f"沿直线方向步行约 {int(dist)} 米",
            distance_m=round(dist, 1),
            duration_s=round(dist / WALKING_SPEED_M_S, 0),
        )],
        provider="straight_line",
    )


async def plan_route(o_lat: float, o_lon: float,
                     d_lat: float, d_lon: float,
                     amap_key: Optional[str] = None) -> WalkRoute:
    """Return a WalkRoute. Always succeeds — falls back to straight-line."""
    key = amap_key if amap_key is not None else os.getenv("AMAP_KEY", "").strip()
    cache_k = _cache_key(o_lat, o_lon, d_lat, d_lon)
    now = time.monotonic()
    cached = _cache.get(cache_k)
    if cached and (now - cached[0]) < CACHE_TTL_S:
        return cached[1]

    if not key:
        route = _straight_line_route(o_lat, o_lon, d_lat, d_lon)
        _cache[cache_k] = (now, route)
        return route

    try:
        route = await asyncio.wait_for(
            _fetch_amap(o_lat, o_lon, d_lat, d_lon, key),
            timeout=TIMEOUT_SEC,
        )
    except Exception as e:                                          # noqa: BLE001
        log.info("amap walking route failed: %s", e)
        route = _straight_line_route(o_lat, o_lon, d_lat, d_lon)

    _cache[cache_k] = (now, route)
    return route


async def _fetch_amap(o_lat: float, o_lon: float,
                      d_lat: float, d_lon: float,
                      key: str) -> WalkRoute:
    params = {
        "key": key,
        "origin": f"{o_lon},{o_lat}",
        "destination": f"{d_lon},{d_lat}",
        "show_fields": "polyline,navi",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        r = await client.get(AMAP_WALK_URL, params=params)
        r.raise_for_status()
        payload = r.json()
    if str(payload.get("status", "")) != "1" or not payload.get("route"):
        raise RuntimeError(f"amap non-success: {payload.get('info')}")
    paths = payload["route"].get("paths") or []
    if not paths:
        raise RuntimeError("amap: empty paths")
    p0 = paths[0]
    distance = float(p0.get("distance", 0))
    duration = float(p0.get("cost", {}).get("duration", distance / WALKING_SPEED_M_S))
    raw_steps = p0.get("steps") or []
    steps: list[WalkRouteStep] = []
    polylines: list[str] = []
    for s in raw_steps:
        steps.append(WalkRouteStep(
            instruction_zh=str(s.get("instruction") or "继续前行"),
            distance_m=float(s.get("step_distance", 0) or 0),
            duration_s=float(s.get("cost", {}).get("duration", 0) or 0),
            polyline=str(s.get("polyline") or "") or None,
        ))
        if s.get("polyline"):
            polylines.append(str(s["polyline"]))
    return WalkRoute(
        distance_m=round(distance, 1),
        duration_min=round(duration / 60, 1),
        polyline=";".join(polylines) if polylines else f"{o_lon},{o_lat};{d_lon},{d_lat}",
        steps=steps,
        provider="amap",
    )
