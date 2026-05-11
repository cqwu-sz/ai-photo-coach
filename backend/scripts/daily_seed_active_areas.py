"""P1-6.3 — daily background seeding around yesterday's active GeoFixes.

Reads ``shot_results`` for the past 24h, clusters geo points to ~1km
buckets, and re-runs ``poi_lookup.search_nearby`` for each centre with
a configurable radius. This warms the local poi_kb.db with rows for
the regions users actually visit, without paying AMap/OSM at request
time.

Usage:
    python -m scripts.daily_seed_active_areas
    python -m scripts.daily_seed_active_areas --since-hours 48 --radius 1000

Run nightly via cron:
    0 3 * * * cd /opt/ai-photo-coach/backend && \
        python -m scripts.daily_seed_active_areas
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import poi_lookup  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("daily_seed")

SHOT_DB = ROOT / "data" / "shot_results.db"


def _bucket(lat: float, lon: float, precision: int = 3) -> tuple[float, float]:
    """Round to N decimals — precision=3 ≈ 110 m bucket."""
    return (round(lat, precision), round(lon, precision))


def _active_centres(since_hours: int) -> list[tuple[float, float]]:
    if not SHOT_DB.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    try:
        with sqlite3.connect(str(SHOT_DB)) as con:
            rows = con.execute(
                "SELECT geo_lat, geo_lon FROM shot_results "
                "WHERE received_at_utc >= ? AND geo_lat IS NOT NULL AND geo_lon IS NOT NULL",
                (cutoff,),
            ).fetchall()
    except sqlite3.DatabaseError as e:
        log.warning("shot_results read failed: %s", e)
        return []
    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for lat, lon in rows:
        b = _bucket(lat, lon)
        if b in seen:
            continue
        seen.add(b)
        out.append((float(lat), float(lon)))
    return out


async def _amain(args: argparse.Namespace) -> int:
    centres = _active_centres(args.since_hours)
    log.info("active centres: %d (since %dh)", len(centres), args.since_hours)
    if not centres:
        log.info("nothing to seed; exiting cleanly")
        return 0
    amap_key = os.getenv("AMAP_KEY", "").strip()
    inserted = 0
    t0 = time.monotonic()
    for i, (lat, lon) in enumerate(centres):
        try:
            results = await poi_lookup.search_nearby(
                lat, lon, radius_m=args.radius,
                amap_key=amap_key or None,
            )
            inserted += sum(1 for r in results if r.source != "kb")
        except Exception as e:                                  # noqa: BLE001
            log.warning("centre (%s,%s) failed: %s", lat, lon, e)
        if (i + 1) % 25 == 0:
            log.info("  %d/%d centres, +%d new POIs",
                     i + 1, len(centres), inserted)
        await asyncio.sleep(0.25)
    log.info("DONE in %.1fs, +%d POIs across %d centres",
             time.monotonic() - t0, inserted, len(centres))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since-hours", type=int, default=24)
    p.add_argument("--radius", type=int, default=500)
    args = p.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
