"""Bulk-seed the local POI database by gridding city bounding boxes and
calling AMap + OSM at each grid centre.

Usage:
    python -m scripts.bulk_seed_poi --region all
    python -m scripts.bulk_seed_poi --region shanghai_core
    python -m scripts.bulk_seed_poi --region all --providers osm

Reads ``backend/data/seed_regions.json``. Each region defines a bbox
[lat_min, lon_min, lat_max, lon_max]. We walk the bbox at the configured
grid step (default 0.005 deg ≈ 550 m) and call ``poi_lookup._fetch_amap``
+ ``_fetch_osm`` at each cell centre. Hits are de-duped against existing
rows (lat/lon within 15 m and same name) and inserted via the same
write-back path used at request time.

This script is **idempotent** — re-running it just upserts. It honours
the AMap rate limit (~5 rps) by sleeping 0.25s between cells when AMap
is enabled. Total wall-clock for all 8 regions: 30-60 minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

# Make the backend package importable when called as a script.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import poi_lookup           # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bulk_seed_poi")

REGIONS_FILE = ROOT / "data" / "seed_regions.json"


def _grid_cells(bbox: list[float], step_deg: float) -> Iterable[tuple[float, float]]:
    lat_min, lon_min, lat_max, lon_max = bbox
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            yield (round(lat, 6), round(lon, 6))
            lon += step_deg
        lat += step_deg


async def _seed_region(region: dict, providers: set[str], step_deg: float,
                       radius_m: int, amap_key: str, sleep_s: float) -> dict:
    """Walk one region's grid, call providers at each cell, return stats."""
    name = region["name"]
    bbox = region["bbox"]
    cells = list(_grid_cells(bbox, step_deg))
    log.info("region %s: %d cells (step %s deg, radius %d m)",
             name, len(cells), step_deg, radius_m)

    inserted = 0
    skipped = 0
    failed = 0
    consecutive_failures = 0
    backoff_until = 0.0
    for i, (lat, lon) in enumerate(cells):
        # P0-1.8 exponential backoff on AMap rate-limit codes.
        now = time.monotonic()
        if now < backoff_until:
            await asyncio.sleep(backoff_until - now)
        before = _count_pois()
        try:
            results = await poi_lookup.search_nearby(
                lat, lon,
                radius_m=radius_m,
                amap_key=amap_key if "amap" in providers else None,
            )
            after = _count_pois()
            inserted += max(0, after - before)
            if not results:
                skipped += 1
            consecutive_failures = 0
        except Exception as e:                              # noqa: BLE001
            log.warning("cell (%s,%s) failed: %s", lat, lon, e)
            failed += 1
            consecutive_failures += 1
            # P0-1.8 — back off if upstream rejects us repeatedly.
            backoff_s = min(60.0, 2.0 ** consecutive_failures)
            backoff_until = time.monotonic() + backoff_s
            if "amap" in providers and "10003" in str(e) or "10001" in str(e):
                log.warning("amap rate-limit detected, sleeping 60s")
                backoff_until = time.monotonic() + 60.0
        if (i + 1) % 25 == 0:
            log.info("  %d/%d cells, +%d new POIs", i + 1, len(cells), inserted)
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)
    log.info("region %s done: +%d POIs (%d empty cells, %d failures)",
             name, inserted, skipped, failed)
    return {"region": name, "inserted": inserted,
            "empty_cells": skipped, "failed_cells": failed,
            "total_cells": len(cells)}


def _count_pois() -> int:
    """Quick row count — used as a delta probe between cells. Avoids
    writing brittle de-dupe logic here; ``_writeback`` already does it."""
    import sqlite3
    try:
        with sqlite3.connect(str(poi_lookup.DB_PATH)) as con:
            return con.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
    except sqlite3.DatabaseError:
        return 0


async def _amain(args: argparse.Namespace) -> int:
    payload = json.loads(REGIONS_FILE.read_text(encoding="utf-8"))
    all_regions = payload["regions"]
    step_deg = args.step or payload.get("default_grid_step_deg", 0.005)
    radius_m = args.radius or payload.get("default_radius_m", 400)

    if args.region == "all":
        selected = all_regions
    else:
        selected = [r for r in all_regions if r["name"] == args.region]
        if not selected:
            log.error("unknown region '%s'. Known: %s", args.region,
                      ", ".join(r["name"] for r in all_regions))
            return 2

    providers = set((args.providers or "amap,osm").split(","))
    amap_key = os.getenv("AMAP_KEY", "").strip()
    if "amap" in providers and not amap_key:
        log.warning("AMAP_KEY not set in env; AMap tier will silently skip "
                    "and only OSM will populate the DB")
    sleep_s = 0.25 if "amap" in providers and amap_key else 0.05

    t0 = time.monotonic()
    summary = []
    for r in selected:
        summary.append(await _seed_region(
            r, providers=providers, step_deg=step_deg, radius_m=radius_m,
            amap_key=amap_key, sleep_s=sleep_s,
        ))
    log.info("ALL DONE in %.1fs", time.monotonic() - t0)
    log.info("summary: %s", json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--region", default="all",
                   help="Region name from seed_regions.json, or 'all'.")
    p.add_argument("--providers", default="amap,osm",
                   help="Comma-separated subset of {amap,osm}.")
    p.add_argument("--step", type=float, default=None,
                   help="Grid step in degrees (default from JSON).")
    p.add_argument("--radius", type=int, default=None,
                   help="Per-cell search radius in metres (default from JSON).")
    args = p.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
