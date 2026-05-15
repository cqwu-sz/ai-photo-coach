"""EXIF-driven camera-parameter calibration.

Reads the ``feedback`` table (rated shots that include EXIF facts) and,
for each scene_kind / lighting bucket, prints the median + P75 of the
parameters users *actually* end up with on their highly-rated photos.
Engineers use this to tighten the default recommendation ranges the
LLM emits (e.g. discover that "indoor_warm cafe" users almost always
land on ISO 800-1600, never 200, and then adjust the prompt's example
values).

This is a *human-facing* diagnostic — no auto-commit. Run weekly,
eyeball the medians, update prompt examples by hand.

Run::

    python scripts/reports/exif_calibration.py [--days 30] [--min-rating 4]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median


def percentile(values: list[float], p: float) -> float:
    if not values: return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/feedback.sqlite")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-rating", type=int, default=4,
                     help="Only consider shots rated this many stars or higher.")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr); return 2

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    con = sqlite3.connect(db)
    # The schema of ``feedback`` differs between major versions — adapt
    # the column list defensively.
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(feedback)")}
    except sqlite3.OperationalError:
        print("no 'feedback' table — nothing to calibrate.", file=sys.stderr); return 3

    select_cols = ["focal_length_35mm_eq", "aperture", "exposure_time_s",
                   "iso", "white_balance_k", "scene_kind", "rating"]
    available = [c for c in select_cols if c in cols]
    if "rating" not in available:
        print("feedback rows lack a rating column", file=sys.stderr); return 3
    sql = (f"SELECT {', '.join(available)} FROM feedback "
            f"WHERE received_at_utc >= ? AND rating >= ?")
    try:
        rows = con.execute(sql, (cutoff, args.min_rating)).fetchall()
    finally:
        con.close()

    if not rows:
        print(f"# EXIF calibration (last {args.days}d, rating ≥ {args.min_rating})\n"
              f"\n_no qualifying samples — try widening --days or lowering --min-rating._")
        return 0

    by_scene: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        rec = dict(zip(available, row))
        scene = rec.get("scene_kind") or "unknown"
        for key in ("focal_length_35mm_eq", "aperture", "exposure_time_s",
                    "iso", "white_balance_k"):
            v = rec.get(key)
            if v is None: continue
            try:
                by_scene[scene][key].append(float(v))
            except (TypeError, ValueError):
                pass

    print(f"# EXIF calibration (last {args.days}d, rating ≥ {args.min_rating}, "
           f"n={len(rows)})\n")
    for scene in sorted(by_scene.keys()):
        bucket = by_scene[scene]
        n = max(len(v) for v in bucket.values()) if bucket else 0
        if n < 5:
            continue  # skip noisy scenes
        print(f"## {scene}  (n≈{n})\n")
        print("| param | median | P25 | P75 |")
        print("|---|---:|---:|---:|")
        for key in ("focal_length_35mm_eq", "aperture", "exposure_time_s",
                    "iso", "white_balance_k"):
            vals = bucket.get(key, [])
            if not vals: continue
            print(f"| {key} | {median(vals):.2f} | "
                  f"{percentile(vals, 0.25):.2f} | {percentile(vals, 0.75):.2f} |")
        print()

    print("_Use these P25..P75 ranges to tighten the camera examples in "
          "SYSTEM_INSTRUCTION / FEW_SHOT_EXAMPLE so the LLM emits values "
          "users actually keep._")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
