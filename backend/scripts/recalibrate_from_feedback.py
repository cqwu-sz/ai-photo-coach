"""Recompute K_face / K_body / STYLE_PALETTE bands from feedback DB.

Reads `data/shot_results.db` (populated by /feedback) and prints the
suggested constants. Designed to be run nightly via cron.

Usage:
    python scripts/recalibrate_from_feedback.py [--apply]

Without --apply, prints proposed values.
With --apply, also writes them into ``data/calibration.json`` which
``scene_aggregate`` reads at startup (override of the in-source
defaults).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import median

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "shot_results.db"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "calibration.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write the new calibration.json (otherwise dry-run).")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"no DB at {DB_PATH} — has /feedback been called yet?")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute(
        "SELECT focal_length_35mm_eq, white_balance_k, recommendation_snapshot_json "
        "FROM shot_results WHERE focal_length_35mm_eq IS NOT NULL"
    ).fetchall()
    con.close()

    if not rows:
        print("no rows with focal length yet")
        return 1

    # Group white_balance by style to refine STYLE_PALETTE warmth bands.
    wb_by_style: dict[str, list[int]] = defaultdict(list)
    focal_dist: list[float] = []
    for focal_eq, wb_k, snapshot_json in rows:
        focal_dist.append(focal_eq)
        if wb_k and snapshot_json:
            try:
                snap = json.loads(snapshot_json)
                for shot in snap.get("shots", []):
                    sm = shot.get("style_match") or {}
                    sid = sm.get("style_id")
                    if sid:
                        wb_by_style[sid].append(int(wb_k))
            except Exception:
                continue

    print(f"=== focal-length distribution (n={len(focal_dist)}) ===")
    print(f"  median: {median(focal_dist):.1f}mm-eq")
    bins = {"ultrawide(<20)": 0, "wide(20-35)": 0, "tele(35-100)": 0, "supertele(>100)": 0}
    for f in focal_dist:
        if f < 20: bins["ultrawide(<20)"] += 1
        elif f < 35: bins["wide(20-35)"] += 1
        elif f < 100: bins["tele(35-100)"] += 1
        else: bins["supertele(>100)"] += 1
    for k, v in bins.items():
        print(f"  {k:18s}: {v} ({100*v/len(focal_dist):.0f}%)")

    if wb_by_style:
        print("\n=== style → realised WB (Kelvin) ===")
        proposed_wb_centres: dict[str, int] = {}
        for sid, ks in sorted(wb_by_style.items()):
            if len(ks) < 5: continue
            m = int(median(ks))
            proposed_wb_centres[sid] = m
            print(f"  {sid:15s}: median={m}K  n={len(ks)}")

        if args.apply:
            OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            current: dict = {}
            if OUT_PATH.exists():
                current = json.loads(OUT_PATH.read_text())
            current["style_wb_centres"] = proposed_wb_centres
            OUT_PATH.write_text(json.dumps(current, indent=2))
            print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
