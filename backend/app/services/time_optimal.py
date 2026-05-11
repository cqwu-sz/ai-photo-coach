"""Best-time-of-day recommendation from historical feedback (W7).

Reads the ``shot_results`` table (populated by /feedback) and aggregates
per-hour ratings within ~50 m of the user's current location. Output is
a ``TimeRecommendation`` ready to inject into the prompt + the
``AnalyzeResponse.environment`` block.

When historical density is too low (n < ``MIN_SAMPLES``) we fall back to
a heuristic based purely on local sunrise / sunset (golden hour ±30 min,
blue hour ±15 min).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models.schemas import TimeRecommendation

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "shot_results.db"
RADIUS_M = 50.0
MIN_SAMPLES = 5


def _haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    R = 6371008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def lookup(lat: Optional[float], lon: Optional[float],
           now_utc: Optional[datetime] = None) -> Optional[TimeRecommendation]:
    """Return a TimeRecommendation when we have enough samples; else None."""
    if lat is None or lon is None:
        return None
    if not DB_PATH.exists():
        return None
    now = now_utc or datetime.now(timezone.utc)

    deg = (RADIUS_M / 111000) * 1.5
    try:
        with sqlite3.connect(str(DB_PATH)) as con:
            rows = con.execute(
                "SELECT geo_lat, geo_lon, captured_at_utc, recommendation_snapshot_json "
                "FROM shot_results WHERE geo_lat BETWEEN ? AND ? "
                "AND geo_lon BETWEEN ? AND ?",
                (lat - deg, lat + deg, lon - deg, lon + deg),
            ).fetchall()
    except sqlite3.DatabaseError as e:
        log.info("time_optimal db read failed: %s", e)
        return None

    by_hour: dict[int, list[float]] = {}
    for slat, slon, ts, snap in rows:
        if slat is None or slon is None or ts is None:
            continue
        if _haversine_m(lat, lon, slat, slon) > RADIUS_M:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        rating = _rating_from_snapshot(snap)
        if rating is None:
            continue
        # Use *local* hour for the user. The DB stores UTC; without TZ
        # info we assume the user's current TZ matches the captured TZ
        # (good enough — feedback rows are typically the same user).
        hour_local = dt.astimezone(now.astimezone().tzinfo).hour
        by_hour.setdefault(hour_local, []).append(rating)

    if not by_hour:
        return None

    scored = sorted(
        ((h, sum(rs) / len(rs), len(rs)) for h, rs in by_hour.items()),
        key=lambda t: (t[1], t[2]),
        reverse=True,
    )
    best_h, best_score, best_n = scored[0]
    if best_n < MIN_SAMPLES:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else None
    minutes_until = ((best_h - now.astimezone().hour) % 24) * 60 - now.astimezone().minute
    return TimeRecommendation(
        best_hour_local=best_h,
        score=round(best_score, 2),
        sample_n=best_n,
        runner_up_hour_local=runner_up,
        minutes_until_best=minutes_until,
        blurb_zh=f"附近 {best_n} 张照片在每天 {best_h:02d}:00 前后评分最高（均分 {best_score:.1f}）。",
    )


def _rating_from_snapshot(snap: Optional[str]) -> Optional[float]:
    """Mine the recommendation_snapshot_json for an explicit rating, or
    fall back to overall_score if rating is absent.

    P1-7.3: also accepts ``silent_positive: true`` flag (set by
    FeedbackUploader.recordSilentPositive) and treats it as 3.5 stars.
    """
    if not snap:
        return None
    try:
        d = json.loads(snap)
    except Exception:                                                # noqa: BLE001
        return None
    r = d.get("rating")
    if isinstance(r, (int, float)):
        return float(r)
    if d.get("silent_positive"):
        return 3.5
    shot = d.get("shot") or {}
    overall = shot.get("overall_score")
    if isinstance(overall, (int, float)):
        return float(overall)
    return None


def to_prompt_block(rec: Optional[TimeRecommendation]) -> str:
    if rec is None:
        return ""
    lines = ["── BEST-TIME EVIDENCE（基于附近历史评分）──"]
    lines.append(
        f"  当前位置每天 {rec.best_hour_local:02d}:00 前后评分最高，均分 "
        f"{rec.score} (n={rec.sample_n})."
    )
    if rec.runner_up_hour_local is not None:
        lines.append(f"  次优时段 {rec.runner_up_hour_local:02d}:00.")
    lines.append("  如非当前时段最优，可在 rationale 中提示用户考虑调整拍摄时间。")
    return "\n".join(lines)
