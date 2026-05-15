"""Weekly recipe-hit-rate report.

Queries the ``post_process_events`` table (populated by
``/feedback/post_process``) and prints a per-recipe-key summary of:

  - total samples
  - "hit"     = user kept the AI recipe verbatim (recipe_applied=true at submit)
  - "tweak"   = user kept the AI preset but moved beauty / LUT
  - "swap"    = user picked a *different* preset entirely
  - p50 swap-count, p90 swap-count

Run::

    python scripts/reports/recipe_hit_rate.py [--db data/feedback.sqlite] [--days 7]

Output: stdout markdown table; intended to be pasted into the weekly
review doc or wired into a Slack webhook later. No DB writes; safe to
run as often as you want.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/feedback.sqlite",
                     help="Path to the feedback sqlite file.")
    ap.add_argument("--days", type=int, default=7,
                     help="Lookback window in days.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT payload_json FROM post_process_events WHERE received_at_utc >= ?",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"query failed: {e}", file=sys.stderr)
        return 3
    finally:
        con.close()

    by_recipe: dict[str, list[dict]] = defaultdict(list)
    for (raw,) in rows:
        try:
            p = json.loads(raw)
        except Exception:
            continue
        rec = p.get("recipe_filter_preset")
        if not rec:
            continue
        by_recipe[rec].append(p)

    if not by_recipe:
        print(f"# Recipe hit-rate (last {args.days}d)\n\n_no samples_")
        return 0

    print(f"# Recipe hit-rate (last {args.days}d)\n")
    print("| recipe | n | hit | tweak | swap | hit% | p50 swaps | p90 swaps |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")

    for recipe, samples in sorted(by_recipe.items(), key=lambda kv: -len(kv[1])):
        n = len(samples)
        hit = sum(1 for s in samples if s.get("recipe_applied") is True)
        swap = sum(1 for s in samples if s.get("recipe_user_override") is True)
        tweak = n - hit - swap
        swaps = [int(s.get("preset_swap_count") or 0) for s in samples]
        swaps_sorted = sorted(swaps)
        def pct(p: float) -> int:
            if not swaps_sorted: return 0
            i = max(0, min(len(swaps_sorted) - 1, int(round(p * (len(swaps_sorted) - 1)))))
            return swaps_sorted[i]
        hit_pct = (hit / n) * 100 if n else 0.0
        print(f"| {recipe} | {n} | {hit} | {tweak} | {swap} | "
              f"{hit_pct:.1f}% | {pct(0.50)} | {pct(0.90)} |")

    print()
    if swaps:
        mean_swap = statistics.mean(int(s.get("preset_swap_count") or 0)
                                     for s in (x for lst in by_recipe.values() for x in lst))
        print(f"_overall mean preset_swap_count = {mean_swap:.2f}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
