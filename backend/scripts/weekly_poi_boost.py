"""P2-10.1 — weekly job: compute per-POI "recommend → shoot conversion"
and write the rate back to ``pois.boost`` so search_nearby can rank
high-conversion POIs first.

Algorithm:
  - For each POI in ``pois`` table, count: how many shot_results
    landed within 50 m of it in the past N days.
  - Compute boost = log1p(landed) so 0 stays 0 and big numbers don't
    swamp distance ranking.
  - Persist into a new column ``boost REAL DEFAULT 0`` on ``pois``.

Run weekly (cron):
    0 4 * * 0 cd /opt/ai-photo-coach/backend && \
        python -m scripts.weekly_poi_boost --days 14
"""
from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.poi_kb import DB_PATH as POI_DB, _haversine_m  # noqa: E402

SHOT_DB = ROOT / "data" / "shot_results.db"

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("weekly_poi_boost")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--radius", type=float, default=50.0)
    args = p.parse_args()

    if not POI_DB.exists() or not SHOT_DB.exists():
        log.warning("required DBs missing; nothing to boost")
        return 0
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=args.days)).isoformat()

    with sqlite3.connect(str(SHOT_DB)) as scon:
        shot_rows = scon.execute(
            "SELECT geo_lat, geo_lon FROM shot_results "
            "WHERE received_at_utc >= ? AND geo_lat IS NOT NULL "
            "AND geo_lon IS NOT NULL",
            (cutoff,),
        ).fetchall()
    log.info("shot_results in window: %d", len(shot_rows))
    if not shot_rows:
        return 0

    with sqlite3.connect(str(POI_DB)) as pcon:
        cols = {row[1] for row in pcon.execute("PRAGMA table_info(pois)").fetchall()}
        if "boost" not in cols:
            pcon.execute("ALTER TABLE pois ADD COLUMN boost REAL NOT NULL DEFAULT 0")
        pois = pcon.execute("SELECT id, lat, lon FROM pois").fetchall()
        log.info("scanning %d pois", len(pois))
        updates = []
        for pid, plat, plon in pois:
            n = sum(
                1 for slat, slon in shot_rows
                if _haversine_m(plat, plon, slat, slon) <= args.radius
            )
            if n > 0:
                updates.append((math.log1p(n), pid))
        log.info("updating boost on %d pois", len(updates))
        pcon.executemany("UPDATE pois SET boost = ? WHERE id = ?", updates)
        pcon.commit()
    log.info("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
