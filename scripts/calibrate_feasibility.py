"""Calibrate style_feasibility scoring against real sample ratings.

Given a CSV of human-rated photo samples taken at known (geo, time,
style), this script:

  1. Re-runs ``style_feasibility.score_styles`` for each row to get the
     model-predicted score of the style the photographer actually chose.
  2. Computes correlation between predicted score and human rating
     (Pearson + Spearman) per style and overall.
  3. Buckets predictions into recommended / marginal / discouraged tiers
     and prints the average human rating per bucket — if our "discouraged"
     samples actually rate just as well as "recommended", thresholds need
     to move.
  4. Suggests adjustments: which style's threshold band looks too strict
     vs too loose.

Sample CSV schema (header required):

    lat,lon,timestamp_utc,style_id,human_rating
    31.2304,121.4737,2026-05-09T10:30:00Z,film_warm,4.5
    35.6762,139.6503,2026-05-09T03:00:00Z,clean_bright,3.0
    ...

human_rating is 0.0-5.0; treat 4+ as "the photo achieved the style".

Usage:
    python scripts/calibrate_feasibility.py samples.csv
    python scripts/calibrate_feasibility.py samples.csv --no-weather
        # Skip Open-Meteo lookup (faster, deterministic for unit-style runs).

When the file doesn't exist yet we generate a SYNTHETIC dataset that
exercises the scoring in known-good ways — useful as a sanity check
that the pipeline is wired up before you collect real samples.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout so the ✓ / ⚠ / ρ / Δ characters render on Windows
# consoles where the default code page is still GBK.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Resolve backend imports regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import style_feasibility as sf       # noqa: E402
from app.services import sun as sun_service             # noqa: E402
from app.services import weather as weather_service     # noqa: E402


@dataclass
class Row:
    lat: float
    lon: float
    timestamp_utc: datetime
    style_id: str
    human_rating: float


def _read_csv(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            t = datetime.fromisoformat(raw["timestamp_utc"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            rows.append(Row(
                lat=float(raw["lat"]),
                lon=float(raw["lon"]),
                timestamp_utc=t,
                style_id=raw["style_id"].strip(),
                human_rating=float(raw["human_rating"]),
            ))
    return rows


def _synthetic_dataset() -> list[Row]:
    """Hand-curated rows where we KNOW the verdict. Used as a self-test
    to verify the regression pipeline works before any real samples
    exist. If correlations here are not strongly positive, the scoring
    or this script itself is broken."""
    # Shanghai various times of day. Ratings are what a human would give
    # if they tried to shoot the named style at that hour.
    L_LAT, L_LON = 31.2304, 121.4737
    rows = [
        # film_warm — needs golden hour. Noon=bad, dusk=great.
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),  "film_warm", 1.5),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 7, 0, tzinfo=timezone.utc),  "film_warm", 2.5),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 9, 30, tzinfo=timezone.utc), "film_warm", 4.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 10, 30, tzinfo=timezone.utc),"film_warm", 5.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc), "film_warm", 1.0),
        # clean_bright — needs bright neutral. Noon=great, dusk=fail.
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),  "clean_bright", 4.5),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 7, 0, tzinfo=timezone.utc),  "clean_bright", 4.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 10, 30, tzinfo=timezone.utc),"clean_bright", 1.5),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc), "clean_bright", 0.5),
        # cinematic_moody — likes low light. Noon=meh, dusk/night=great.
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),  "cinematic_moody", 2.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 10, 30, tzinfo=timezone.utc),"cinematic_moody", 4.5),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), "cinematic_moody", 4.0),
        # street_candid — almost always works.
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),  "street_candid", 4.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc), "street_candid", 4.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), "street_candid", 2.5),
        # editorial_fashion — daytime good, deep night bad.
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),  "editorial_fashion", 4.0),
        Row(L_LAT, L_LON, datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), "editorial_fashion", 1.5),
    ]
    return rows


# ---------------------------------------------------------------------------
# Stats helpers (keep zero deps — numpy/scipy not in requirements.txt)
# ---------------------------------------------------------------------------
def pearson(xs, ys) -> float:
    if len(xs) < 2:
        return 0.0
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def spearman(xs, ys) -> float:
    def rank(seq):
        s = sorted((v, i) for i, v in enumerate(seq))
        ranks = [0.0] * len(seq)
        # Assign average ranks to ties.
        i = 0
        while i < len(s):
            j = i
            while j + 1 < len(s) and s[j + 1][0] == s[i][0]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[s[k][1]] = avg
            i = j + 1
        return ranks
    return pearson(rank(xs), rank(ys))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def _score_row(row: Row, use_weather: bool) -> float:
    sun_info = sun_service.compute(row.lat, row.lon, row.timestamp_utc)
    weather = await weather_service.fetch_current(row.lat, row.lon) if use_weather else None
    scores = sf.score_styles(sun_info, weather)
    match = next((s for s in scores if s.style_id == row.style_id), None)
    return match.score if match else 0.0


async def main_async(rows: list[Row], use_weather: bool) -> None:
    print(f"Scoring {len(rows)} samples (weather={'on' if use_weather else 'off'})...")
    predicted = []
    for row in rows:
        p = await _score_row(row, use_weather)
        predicted.append(p)

    print("\n=== Per-row predictions vs human rating ===")
    print(f'{"style_id":20s} {"time_local":17s} {"pred":6s} {"human":6s} {"tier":12s}')
    for row, p in zip(rows, predicted):
        # Approximate local time = UTC + 8 for Shanghai-ish; this is just
        # for display so we don't bother with full tz lookup.
        local = row.timestamp_utc.astimezone(timezone.utc).replace(tzinfo=None)
        tier = "recommended" if p >= 0.7 else "marginal" if p >= 0.45 else "discouraged"
        print(f"  {row.style_id:18s} {local.strftime('%m-%d %H:%M UTC'):17s} "
              f"{p:.2f}  {row.human_rating:.2f}  {tier:12s}")

    print("\n=== Overall correlation ===")
    print(f"  Pearson  r = {pearson(predicted, [r.human_rating for r in rows]):+.3f}")
    print(f"  Spearman ρ = {spearman(predicted, [r.human_rating for r in rows]):+.3f}")

    print("\n=== Per-style correlation ===")
    by_style: dict[str, list[tuple[float, float]]] = {}
    for row, p in zip(rows, predicted):
        by_style.setdefault(row.style_id, []).append((p, row.human_rating))
    for sid, pairs in sorted(by_style.items()):
        if len(pairs) < 2:
            print(f"  {sid:20s} n={len(pairs)} (need ≥2 to correlate)")
            continue
        ps = [p for p, _ in pairs]
        hs = [h for _, h in pairs]
        r = pearson(ps, hs)
        rho = spearman(ps, hs)
        print(f"  {sid:20s} n={len(pairs)}  r={r:+.3f}  ρ={rho:+.3f}")

    print("\n=== Average human rating per predicted tier ===")
    by_tier: dict[str, list[float]] = {"recommended": [], "marginal": [], "discouraged": []}
    for row, p in zip(rows, predicted):
        tier = "recommended" if p >= 0.7 else "marginal" if p >= 0.45 else "discouraged"
        by_tier[tier].append(row.human_rating)
    for tier in ("recommended", "marginal", "discouraged"):
        ratings = by_tier[tier]
        if not ratings:
            print(f"  {tier:12s} n=0")
            continue
        avg = statistics.mean(ratings)
        print(f"  {tier:12s} n={len(ratings):2d}  avg_rating={avg:.2f}  "
              f"min={min(ratings):.1f}  max={max(ratings):.1f}")

    print("\n=== Suggestions ===")
    rec = by_tier["recommended"]
    dis = by_tier["discouraged"]
    if rec and dis and statistics.mean(rec) - statistics.mean(dis) < 1.0:
        print("  ⚠ recommended vs discouraged spread < 1.0 stars — thresholds may be"
              " too aggressive (we're calling things 'discouraged' that humans liked).")
    if rec and statistics.mean(rec) < 3.5:
        print("  ⚠ 'recommended' average is below 3.5 — model is over-promising;"
              " consider raising the 0.7 threshold or tightening the per-style sub-scores.")
    if not rec and not dis:
        print("  (Not enough samples in the extreme tiers to draw a conclusion.)")
    if rec and dis and statistics.mean(rec) - statistics.mean(dis) >= 1.5:
        print(f"  ✓ Healthy spread: recommended avg {statistics.mean(rec):.2f} vs"
              f" discouraged avg {statistics.mean(dis):.2f} (Δ={statistics.mean(rec)-statistics.mean(dis):+.2f})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", nargs="?", default=None)
    parser.add_argument("--no-weather", action="store_true",
                        help="Skip Open-Meteo lookup (faster, fully deterministic)")
    args = parser.parse_args()

    if args.csv_path:
        rows = _read_csv(Path(args.csv_path))
        if not rows:
            print(f"No rows in {args.csv_path}", file=sys.stderr)
            sys.exit(1)
    else:
        print("(no csv supplied — running synthetic self-test dataset)")
        rows = _synthetic_dataset()

    asyncio.run(main_async(rows, use_weather=not args.no_weather))


if __name__ == "__main__":
    main()
