"""Z-score anomaly detection for keyword trends (v17i).

Runs hourly. For each top-N style keyword, looks at its hourly call
counts in the past 7 days. If the most recent hour is > Z standard
deviations above the mean of the trailing window, fires an audit
event `trend.anomaly` (which also routes through the alert mailer
to whichever inbox/webhook is configured for `trend.*`).

This is deliberately a tiny, explainable detector — no Prophet,
no Holt-Winters. Stakeholders can read the formula in two lines.

Tunables (runtime_settings):
  trend.enabled               = "true"|"false"  (default true)
  trend.min_z                 = "3"             (z-score threshold)
  trend.min_calls_in_hour     = "5"             (suppress noise)
  trend.window_hours          = "168"           (7d trailing window)
  trend.cooldown_sec          = "21600"         (6h dedup per kw)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone

from . import rate_buckets, runtime_settings, user_repo

log = logging.getLogger(__name__)

_DEDUP_BUCKET = "trend_anomaly"


def _hourly_counts(window_hours: int) -> dict[str, list[int]]:
    """Returns {keyword: [count_h0, count_h1, ..., count_h(N-1)]}
    where index 0 = oldest hour."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    hours = window_hours
    by_kw: dict[str, list[int]] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT created_at, step_config FROM usage_records "
            "WHERE status = 'charged' AND created_at >= ?",
            (start.isoformat(),),
        ).fetchall()
    for created_at, sc_raw in rows:
        try:
            sc = json.loads(sc_raw) if sc_raw else {}
            ts = datetime.fromisoformat(created_at)
        except (TypeError, ValueError):
            continue
        bucket = int((ts - start).total_seconds() // 3600)
        if bucket < 0 or bucket >= hours:
            continue
        for raw_kw in (sc.get("style_keywords") or []):
            if not raw_kw:
                continue
            kw = str(raw_kw).strip()[:60].lower()
            if kw not in by_kw:
                by_kw[kw] = [0] * hours
            by_kw[kw][bucket] += 1
    return by_kw


def _detect(by_kw: dict[str, list[int]], *,
              min_z: float, min_calls_in_hour: int) -> list[dict]:
    out: list[dict] = []
    for kw, series in by_kw.items():
        if len(series) < 24:
            continue
        latest = series[-1]
        if latest < min_calls_in_hour:
            continue
        baseline = series[:-1]
        n = len(baseline)
        if n == 0:
            continue
        mean = sum(baseline) / n
        var = sum((x - mean) ** 2 for x in baseline) / n
        std = math.sqrt(var)
        if std == 0:
            # Constant baseline; only flag if latest is clearly above it.
            if latest > mean * 3 and latest >= min_calls_in_hour * 2:
                out.append({"keyword": kw, "latest": latest,
                              "mean": round(mean, 2), "std": 0.0,
                              "z": float("inf")})
            continue
        z = (latest - mean) / std
        if z >= min_z:
            out.append({"keyword": kw, "latest": latest,
                          "mean": round(mean, 2),
                          "std": round(std, 2),
                          "z": round(z, 2)})
    out.sort(key=lambda x: x["z"] if x["z"] != float("inf") else 1e9,
              reverse=True)
    return out


def _emit(anomaly: dict) -> None:
    cooldown = runtime_settings.get_int("trend.cooldown_sec", 21600)
    n = rate_buckets.hit(_DEDUP_BUCKET, "kw", anomaly["keyword"], cooldown)
    if n > 1:
        return  # already fired in cooldown window
    # Write audit + auto-route through alert mailer.
    from . import admin_audit
    admin_audit.write(
        "system", "trend.anomaly", target=anomaly["keyword"],
        payload=anomaly,
    )
    log.info("trend.anomaly fired kw=%s z=%s",
              anomaly["keyword"], anomaly["z"])


def scan_once() -> int:
    """Returns number of anomalies emitted."""
    if runtime_settings.get_str("trend.enabled", "true").lower() not in (
            "true", "1", "yes"):
        return 0
    window = runtime_settings.get_int("trend.window_hours", 168)
    min_z = float(runtime_settings.get_str("trend.min_z", "3"))
    min_calls = runtime_settings.get_int("trend.min_calls_in_hour", 5)
    by_kw = _hourly_counts(window)
    anomalies = _detect(by_kw, min_z=min_z, min_calls_in_hour=min_calls)
    for a in anomalies:
        _emit(a)
    return len(anomalies)


async def loop(interval_sec: int = 3600) -> None:
    log.info("trend_anomaly: started (poll every %ds)", interval_sec)
    while True:
        try:
            n = scan_once()
            if n:
                log.info("trend_anomaly: scan emitted %d alerts", n)
        except Exception as e:                                      # noqa: BLE001
            log.warning("trend_anomaly tick failed: %s", e)
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break


__all__ = ["scan_once", "loop", "_detect"]
