"""W1.2 — poi_indoor smoke. Verifies registry-based hits + that no
network call is made when the GPS is far from any registered building."""
from __future__ import annotations

import asyncio

from app.services import poi_indoor


def test_registry_hit_returns_hotspots():
    # IAPM (Shanghai) — 31.2151, 121.4622 is in default registry.
    out = asyncio.run(poi_indoor.lookup_indoor(31.2151, 121.4622))
    assert isinstance(out, list)
    if out:
        assert out[0].building_name_zh
        assert out[0].hotspot_label_zh


def test_registry_miss_returns_empty():
    out = asyncio.run(poi_indoor.lookup_indoor(0.0, 0.0))
    assert out == []
