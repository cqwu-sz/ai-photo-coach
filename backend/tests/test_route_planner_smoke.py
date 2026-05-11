"""W3 — route_planner smoke. Ensures we always return a WalkRoute even
without an AMap key (straight-line fallback)."""
from __future__ import annotations

import asyncio

from app.services import route_planner


def test_straight_line_fallback_when_no_key():
    out = asyncio.run(route_planner.plan_route(
        31.2389, 121.4905, 31.2400, 121.4920, amap_key=None,
    ))
    assert out.distance_m > 0
    assert out.duration_min > 0
    assert out.steps and out.steps[0].instruction_zh
    assert out.provider == "straight_line"
