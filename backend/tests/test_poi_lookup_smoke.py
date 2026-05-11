"""Smoke tests for the three-tier POI lookup.

We avoid real network calls by mocking ``httpx.AsyncClient`` for the
AMap and Overpass paths and by running each test against a temp DB
path so writebacks don't pollute the dev poi_kb.db.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.services import poi_lookup
from app.services.poi_lookup import POICandidate


def _patch_db(tmp_path: Path):
    """Re-bind ``DB_PATH`` so the test uses a private sqlite file."""
    db = tmp_path / "poi_kb_test.db"
    return patch.object(poi_lookup, "DB_PATH", db, create=False)


def test_local_only_when_no_network(tmp_path):
    """When AMAP_KEY is missing and OSM fails, we still return whatever
    the local DB has — never raise."""
    with _patch_db(tmp_path):
        # Pre-seed one POI within range.
        with sqlite3.connect(str(poi_lookup.DB_PATH)) as con:
            con.execute(
                "CREATE TABLE pois ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, kind TEXT)"
            )
            con.execute(
                "INSERT INTO pois (name, lat, lon, kind) VALUES (?, ?, ?, ?)",
                ("外滩观景平台", 31.2389, 121.4905, "viewpoint"),
            )

        async def fake_osm(*a, **kw):
            raise RuntimeError("no network")

        with patch.object(poi_lookup, "_fetch_osm", new=AsyncMock(side_effect=fake_osm)):
            results = asyncio.run(poi_lookup.search_nearby(31.2389, 121.4905))
        assert any("外滩" in p.name for p in results)
        assert all(p.distance_m < 5 for p in results if "外滩" in p.name)


def test_amap_writeback(tmp_path):
    """A successful AMap response is appended to the local DB so the
    next call hits the cache."""
    with _patch_db(tmp_path):
        amap_payload = {
            "status": "1",
            "pois": [{
                "name": "陈毅广场",
                "location": "121.4912,31.2401",
                "type": "风景名胜;观景平台",
            }],
        }

        async def fake_amap(lat, lon, radius, key):
            return [POICandidate(
                name="陈毅广场", lat=31.2401, lon=121.4912, kind="风景名胜",
                source="amap", distance_m=120.0, bearing_from_user_deg=15.0,
            )]

        with patch.object(poi_lookup, "_fetch_amap", new=AsyncMock(side_effect=fake_amap)), \
             patch.object(poi_lookup, "_fetch_osm", new=AsyncMock(return_value=[])):
            results = asyncio.run(poi_lookup.search_nearby(
                31.2389, 121.4905, amap_key="fake-key"))
        assert any(p.name == "陈毅广场" for p in results)
        # Writeback created the row.
        with sqlite3.connect(str(poi_lookup.DB_PATH)) as con:
            rows = con.execute(
                "SELECT name, source FROM pois WHERE name=?", ("陈毅广场",),
            ).fetchall()
        assert rows and rows[0][1] == "amap"


def test_dedupe_within_15m(tmp_path):
    """Two POIs sharing a name and within 15 m are de-duplicated."""
    a = POICandidate(name="X", lat=31.2389, lon=121.4905, kind="viewpoint",
                     source="kb", distance_m=0, bearing_from_user_deg=0)
    b = POICandidate(name="X", lat=31.2389, lon=121.4906, kind="viewpoint",
                     source="amap", distance_m=10, bearing_from_user_deg=90)
    assert poi_lookup._dup(b, [a])


def test_to_prompt_block_omits_when_empty():
    assert poi_lookup.to_prompt_block([]) == ""
    block = poi_lookup.to_prompt_block([POICandidate(
        name="外滩", lat=31.2389, lon=121.4905, kind="viewpoint",
        source="kb", distance_m=2.0, bearing_from_user_deg=180.0,
    )])
    assert "外滩" in block
    assert "NEARBY POIS" in block
