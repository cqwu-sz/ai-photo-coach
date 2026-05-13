"""Generic windowed counter for v17c anti-DDoS.

Distinct from `rate_limit.py` (token-bucket per (route, identity)
used by /analyze etc.). This one is a simple "how many X happened
in the current N-second window?" counter, used by:

  * `otp.request_code` — daily-per-target / daily-per-IP / global RPM
    ceilings on top of the existing rolling cooldown.
  * `middleware._global_security_gate` — per-IP RPM/RPH ceilings on
    every public endpoint to absorb naive scrape / DDoS.

Backends (selected automatically):

  * **SQLite** (default) — single-row INSERT-OR-UPDATE keyed on
    (service, scope, bucket_key, window_start). Cheap, durable, no
    extra dependency. Perfectly fine up to a few hundred QPS on
    one process.
  * **Redis** (when ``settings.redis_url`` is set AND ``redis`` is
    installed) — atomic INCR + EXPIRE. Required for multi-instance
    deployments where the per-IP cap must be global, otherwise N
    instances multiply the effective cap by N.

The interface is sync to keep the OTP send path simple. Redis path
runs the (very fast) command via ``asyncio.run_coroutine_threadsafe``
on a dedicated background loop so we don't taint sync callers with
async colour. If that loop isn't ready (cold start) we fall back
to SQLite for the call — fail safe, never drop requests on the
floor of the limiter itself.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import user_repo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------


def _floor(period_sec: int) -> int:
    """Window key: epoch second floored to the period boundary."""
    now = int(time.time())
    return now - (now % max(period_sec, 1))


def _window_iso(period_sec: int) -> str:
    return datetime.fromtimestamp(_floor(period_sec), tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Redis backend (optional)
# ---------------------------------------------------------------------------


_redis_client: dict[str, object] = {"v": None, "loop": None, "thread": None}
_redis_lock = threading.Lock()


def _ensure_redis_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Lazily start a daemon thread running an asyncio loop dedicated
    to Redis I/O. Sync callers pump coroutines onto it via
    ``run_coroutine_threadsafe``."""
    with _redis_lock:
        loop = _redis_client.get("loop")
        if loop is not None:
            return loop  # type: ignore[return-value]
        try:
            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_run, name="rate_buckets-redis",
                                  daemon=True)
            t.start()
            _redis_client["loop"] = loop
            _redis_client["thread"] = t
            return loop
        except Exception as e:                                      # noqa: BLE001
            log.warning("rate_buckets: failed to start redis loop: %s", e)
            return None


def _get_redis_client():
    cli = _redis_client.get("v")
    if cli is not None:
        return cli
    from ..config import get_settings
    url = (getattr(get_settings(), "redis_url", "") or "").strip()
    if not url:
        return None
    try:
        import redis.asyncio as redis_async  # type: ignore
    except Exception as e:                                          # noqa: BLE001
        log.warning("rate_buckets: REDIS_URL set but redis pkg missing: %s", e)
        return None
    cli = redis_async.from_url(url, decode_responses=True)
    _redis_client["v"] = cli
    log.info("rate_buckets: Redis backend enabled")
    return cli


def _hit_redis(service: str, scope: str, bucket_key: str,
                period_sec: int) -> Optional[int]:
    cli = _get_redis_client()
    if cli is None:
        return None
    loop = _ensure_redis_loop()
    if loop is None:
        return None
    window = _floor(period_sec)
    key = f"rb:{service}:{scope}:{bucket_key}:{window}"

    async def _run() -> int:
        # INCR + first-time EXPIRE. TTL = period * 2 so window ends
        # well before key expiry, no risk of bucket disappearing
        # mid-window.
        pipe = cli.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, period_sec * 2)
        res = await pipe.execute()
        return int(res[0])

    try:
        fut = asyncio.run_coroutine_threadsafe(_run(), loop)
        return fut.result(timeout=1.0)
    except Exception as e:                                          # noqa: BLE001
        log.debug("rate_buckets: Redis hit failed, falling back: %s", e)
        return None


def _peek_redis(service: str, scope: str, bucket_key: str,
                  period_sec: int) -> Optional[int]:
    cli = _get_redis_client()
    if cli is None:
        return None
    loop = _ensure_redis_loop()
    if loop is None:
        return None
    window = _floor(period_sec)
    key = f"rb:{service}:{scope}:{bucket_key}:{window}"

    async def _run() -> int:
        v = await cli.get(key)
        return int(v or 0)

    try:
        fut = asyncio.run_coroutine_threadsafe(_run(), loop)
        return fut.result(timeout=1.0)
    except Exception:                                               # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# SQLite backend (default)
# ---------------------------------------------------------------------------


def _hit_sqlite(service: str, scope: str, bucket_key: str,
                  period_sec: int) -> int:
    window = _window_iso(period_sec)
    with user_repo._connect() as con:                               # noqa: SLF001
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass
        con.execute(
            "INSERT INTO rate_buckets (service, scope, bucket_key, "
            "window_start, count) VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(service, scope, bucket_key, window_start) "
            "DO UPDATE SET count = count + 1",
            (service, scope, bucket_key, window),
        )
        cur = con.execute(
            "SELECT count FROM rate_buckets WHERE service = ? AND scope = ? "
            "AND bucket_key = ? AND window_start = ?",
            (service, scope, bucket_key, window),
        ).fetchone()
        con.commit()
    return int(cur[0]) if cur else 0


def _peek_sqlite(service: str, scope: str, bucket_key: str,
                   period_sec: int) -> int:
    window = _window_iso(period_sec)
    with user_repo._connect() as con:                               # noqa: SLF001
        cur = con.execute(
            "SELECT count FROM rate_buckets WHERE service = ? AND scope = ? "
            "AND bucket_key = ? AND window_start = ?",
            (service, scope, bucket_key, window),
        ).fetchone()
    return int(cur[0]) if cur else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def hit(service: str, scope: str, bucket_key: str,
        period_sec: int) -> int:
    """Increment & return the new count for the current window.

    Tries Redis first when configured; falls back to SQLite on
    miss/error so a flaky Redis can never take down OTP."""
    n = _hit_redis(service, scope, bucket_key, period_sec)
    if n is not None:
        return n
    return _hit_sqlite(service, scope, bucket_key, period_sec)


def peek(service: str, scope: str, bucket_key: str,
         period_sec: int) -> int:
    n = _peek_redis(service, scope, bucket_key, period_sec)
    if n is not None:
        return n
    return _peek_sqlite(service, scope, bucket_key, period_sec)


def gc(older_than_sec: int = 24 * 3600) -> int:
    """Trim SQLite rows older than the longest reasonable window.
    No-op for Redis (TTL handles it)."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=older_than_sec)).isoformat()
    with user_repo._connect() as con:                               # noqa: SLF001
        cur = con.execute("DELETE FROM rate_buckets WHERE window_start < ?",
                           (cutoff,))
        con.commit()
        return cur.rowcount


def reset_for_tests() -> None:
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute("DELETE FROM rate_buckets")
        con.commit()
    # Best-effort flush for Redis test runs.
    cli = _redis_client.get("v")
    if cli is not None:
        loop = _redis_client.get("loop")
        if loop is not None:
            try:
                async def _flush():
                    keys = [k async for k in cli.scan_iter("rb:*")]
                    if keys:
                        await cli.delete(*keys)
                asyncio.run_coroutine_threadsafe(_flush(), loop).result(timeout=2)
            except Exception:                                       # noqa: BLE001
                pass


__all__ = ["hit", "peek", "gc", "reset_for_tests"]
