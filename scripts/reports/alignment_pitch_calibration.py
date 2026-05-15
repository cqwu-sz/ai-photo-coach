"""Weekly recalibration job for AlignmentMachine.Tolerances.pitchNear /
pitchFar.

Reads ``alignment_pitch_events`` (written by /feedback/alignment_pitch),
computes the P50 / P75 / P90 / P95 of ``abs_delta_deg`` across the
lookback window, and prints a markdown summary plus a suggested
``Tolerances`` literal that engineering can paste into
``AlignmentMachine.swift``.

Heuristic:

  - ``pitchOk``   → P50 (median user already considers themselves "aligned")
  - ``pitchNear`` → P75
  - ``pitchWarn`` → P85
  - ``pitchFar``  → P95

We **don't** auto-commit changes — recalibration moves UX language, and
we want a human to eyeball the new numbers (and rough-side cap them so a
runaway sample can't widen tolerances to 30°+) before they land. Future
work: emit a PR via a GitHub workflow when the deltas exceed a threshold.

Run::

    python scripts/reports/alignment_pitch_calibration.py [--db ...] [--days 14]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Engineering-imposed hard caps. We never recommend opening the tolerances
# beyond these even if telemetry says so — a wider band means a sloppier
# green light and we'd rather collect more data than ship a regression.
HARD_CAPS = {"pitchOk": 7.0, "pitchNear": 12.0, "pitchWarn": 16.0, "pitchFar": 28.0}


def percentile(values: list[float], p: float) -> float:
    if not values: return 0.0
    s = sorted(values)
    if len(s) == 1: return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/feedback.sqlite",
                     help="Feedback sqlite path.")
    ap.add_argument("--days", type=int, default=14,
                     help="Lookback in days. Two weeks is the sweet spot: "
                          "wide enough for stable P95, narrow enough that "
                          "we react to user-base shifts.")
    ap.add_argument("--min-samples", type=int, default=50,
                     help="Skip recommending changes below this sample count.")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr); return 2

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT abs_delta_deg, tier, shot_id FROM alignment_pitch_events "
            "WHERE received_at_utc >= ?",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"query failed: {e}", file=sys.stderr); return 3
    finally:
        con.close()

    if not rows:
        print(f"# Pitch calibration (last {args.days}d)\n\n_no samples — collect more data._")
        return 0

    all_deltas = [float(r[0]) for r in rows if r[0] is not None]
    by_tier: dict[str, list[float]] = defaultdict(list)
    for d, t, _ in rows:
        by_tier[str(t)].append(float(d))

    print(f"# AlignmentMachine pitch calibration (last {args.days}d, n={len(all_deltas)})\n")
    print("## Distribution of |pitchDelta| at green-light edge\n")
    print("| metric | value (°) |")
    print("|---|---:|")
    for p, label in [(0.50, "P50"), (0.75, "P75"), (0.85, "P85"),
                     (0.90, "P90"), (0.95, "P95")]:
        print(f"| {label} | {percentile(all_deltas, p):.2f} |")

    print("\n## Tier breakdown\n")
    print("| tier | n | P50 | P90 |")
    print("|---|---:|---:|---:|")
    for tier in ("onTarget", "slight", "noticeable", "severe", "unknown"):
        vals = by_tier.get(tier, [])
        if not vals: continue
        print(f"| {tier} | {len(vals)} | "
              f"{percentile(vals, 0.5):.2f} | {percentile(vals, 0.9):.2f} |")

    if len(all_deltas) < args.min_samples:
        print(f"\n_only {len(all_deltas)} samples — below the {args.min_samples} "
              "recommendation threshold; thresholds left untouched._")
        return 0

    p50 = percentile(all_deltas, 0.50)
    p75 = percentile(all_deltas, 0.75)
    p85 = percentile(all_deltas, 0.85)
    p95 = percentile(all_deltas, 0.95)
    rec = {
        "pitchOk":   min(round(p50, 1), HARD_CAPS["pitchOk"]),
        "pitchNear": min(round(p75, 1), HARD_CAPS["pitchNear"]),
        "pitchWarn": min(round(p85, 1), HARD_CAPS["pitchWarn"]),
        "pitchFar":  min(round(p95, 1), HARD_CAPS["pitchFar"]),
    }
    print("\n## Recommended `Tolerances` (eyeball before merging)\n")
    print("```swift")
    print("static let `default` = Tolerances(")
    print("    headingOk: 4.0, headingWarn: 12.0,")
    print(f"    pitchOk: {rec['pitchOk']}, pitchWarn: {rec['pitchWarn']},")
    print(f"    pitchNear: {rec['pitchNear']}, pitchFar: {rec['pitchFar']},")
    print("    distanceOkM: 0.25, distanceWarnM: 0.6,")
    print("    holdTime: 0.7")
    print(")")
    print("```")
    print(f"\n_hard caps applied: {HARD_CAPS}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
