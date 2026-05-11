"""Rate-limit (P0-1.4 + A1-1).

Two backends, transparently selected by `settings.redis_url`:

  - **In-process token bucket** (default). Single-worker only; multi-
    worker / multi-host deployments will under-count by `N×` (each
    worker holds its own bucket). Fine for dev + small staging.

  - **Redis token bucket** (set REDIS_URL). Atomic Lua eval so
    horizontal scale-out is safe. The per-call cost is ~1 round trip
    to Redis (~0.3-0.8 ms on the same VPC), which we trade for
    correctness once you scale beyond one process.

Both backends key on (route, identity) where identity is `user_id`
(set by callers in analyze/feedback/recon3d).

Pro tier multiplier: when callers pass `tier="pro"` we scale capacity
+ refill by `settings.rate_limit_pro_multiplier` so paying users get
N× the headroom. Implementation lives here so each route doesn't
duplicate the math.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-process backend
# ---------------------------------------------------------------------------


@dataclass
class Bucket:
    capacity: float
    tokens: float
    refill_per_sec: float
    last_refill: float


_buckets: dict[tuple[str, str], Bucket] = {}
_lock = asyncio.Lock()


def reset_for_tests() -> None:
    _buckets.clear()
    _redis_client["v"] = None     # type: ignore[index]


async def _consume_local(route: str, identity: str, *,
                          capacity: float, refill_per_sec: float,
                          cost: float = 1.0) -> bool:
    key = (route, identity)
    now = time.monotonic()
    async with _lock:
        b = _buckets.get(key)
        if b is None:
            b = Bucket(capacity=capacity, tokens=capacity,
                       refill_per_sec=refill_per_sec, last_refill=now)
            _buckets[key] = b
        elapsed = now - b.last_refill
        b.tokens = min(b.capacity, b.tokens + elapsed * b.refill_per_sec)
        b.last_refill = now
        if b.tokens < cost:
            return False
        b.tokens -= cost
        return True


# ---------------------------------------------------------------------------
# Redis backend (lazy init — only if REDIS_URL is set)
# ---------------------------------------------------------------------------

_redis_client: dict[str, object] = {"v": None}

_LUA_TOKEN_BUCKET = """
-- KEYS[1] = bucket key
-- ARGV: capacity, refill_per_sec, now_ms, cost
local key = KEYS[1]
local cap = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
    tokens = cap
    ts = now
end
local elapsed_s = math.max(0, (now - ts) / 1000.0)
tokens = math.min(cap, tokens + elapsed_s * rate)
local allowed = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
-- Bucket auto-expires after twice its drain time so cold keys don't pile up.
local ttl = math.ceil(cap / math.max(rate, 0.0001) * 2)
redis.call('EXPIRE', key, ttl)
return allowed
"""


async def _get_redis():
    cli = _redis_client["v"]
    if cli is not None:
        return cli
    from ..config import get_settings
    url = (get_settings().redis_url or "").strip()
    if not url:
        return None
    try:
        # Prefer redis>=5 async client. We import lazily so the
        # dependency is optional.
        import redis.asyncio as redis_async  # type: ignore
    except Exception as e:                                       # noqa: BLE001
        log.warning("REDIS_URL set but `redis` package unavailable: %s", e)
        return None
    cli = redis_async.from_url(url, decode_responses=True)
    _redis_client["v"] = cli
    log.info("rate_limit: Redis backend enabled url=%s", url)
    return cli


async def _consume_redis(route: str, identity: str, *,
                          capacity: float, refill_per_sec: float,
                          cost: float = 1.0) -> bool:
    cli = await _get_redis()
    if cli is None:
        return await _consume_local(route, identity,
                                      capacity=capacity,
                                      refill_per_sec=refill_per_sec,
                                      cost=cost)
    key = f"rl:{route}:{identity}"
    now_ms = int(time.time() * 1000)
    try:
        result = await cli.eval(
            _LUA_TOKEN_BUCKET, 1, key,
            str(capacity), str(refill_per_sec), str(now_ms), str(cost),
        )
        return int(result) == 1
    except Exception as e:                                       # noqa: BLE001
        log.warning("rate_limit redis eval failed, falling back to local: %s", e)
        return await _consume_local(route, identity,
                                      capacity=capacity,
                                      refill_per_sec=refill_per_sec,
                                      cost=cost)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def consume(route: str, identity: str, *,
                   capacity: float, refill_per_sec: float,
                   cost: float = 1.0) -> bool:
    """Try to consume `cost` tokens. Returns False if the bucket is dry."""
    return await _consume_redis(route, identity,
                                  capacity=capacity,
                                  refill_per_sec=refill_per_sec,
                                  cost=cost)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if fwd:
        return fwd
    if request.client:
        return request.client.host or "_unknown"
    return "_unknown"


def _scale_for_tier(tier: Optional[str], capacity: float,
                     refill_per_sec: float) -> tuple[float, float]:
    """Apply A1-5 tier multiplier. Pro users get N× headroom."""
    if tier == "pro":
        from ..config import get_settings
        m = max(1.0, float(get_settings().rate_limit_pro_multiplier))
        return capacity * m, refill_per_sec * m
    return capacity, refill_per_sec


async def enforce(request: Request, route: str, *,
                   capacity: float, refill_per_sec: float,
                   identity: str | None = None,
                   tier: str | None = None,
                   cost: float = 1.0) -> None:
    """Raise 429 when the bucket is exhausted."""
    cap, rate = _scale_for_tier(tier, capacity, refill_per_sec)
    ident = identity or client_ip(request)
    ok = await consume(route, ident, capacity=cap,
                        refill_per_sec=rate, cost=cost)
    if not ok:
        log.info("rate_limit hit route=%s identity=%s tier=%s",
                 route, ident, tier)
        # Best-effort metric — late import to dodge cycles.
        try:
            from ..api import metrics as metrics_api
            metrics_api.inc("ai_photo_coach_rate_limit_total",
                             route=route, tier=tier or "free")
        except Exception:                                        # noqa: BLE001
            pass
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"code": "rate_limited", "message": "too many requests"}},
        )
