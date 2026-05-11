"""Tiny in-process circuit breaker for outbound provider calls (P0-4.4).

After ``failure_threshold`` consecutive failures the breaker opens and
short-circuits subsequent calls for ``cooldown_sec`` so we don't
hammer a flaky upstream (and don't burn the user's request budget on
guaranteed-to-fail calls).

Usage:
    breaker = get("amap")
    async def lookup():
        async with breaker.guarded("amap.place_search"):
            return await httpx.get(...)

The ``guarded`` async context raises ``CircuitOpen`` immediately when
the breaker is open. Callers convert that into their normal "no result"
fallback path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

log = logging.getLogger(__name__)


class CircuitOpen(RuntimeError):
    pass


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    def __init__(self, name: str, *, failure_threshold: int = 5,
                 cooldown_sec: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self._state = _State()
        self._lock = asyncio.Lock()

    def _now(self) -> float:
        return time.monotonic()

    async def _maybe_close(self) -> None:
        if self._state.opened_at is None:
            return
        if (self._now() - self._state.opened_at) >= self.cooldown_sec:
            log.info("circuit_breaker[%s] half-open: trial allowed", self.name)
            self._state.opened_at = None
            self._state.failures = 0

    async def record_success(self) -> None:
        async with self._lock:
            if self._state.failures or self._state.opened_at:
                log.info("circuit_breaker[%s] reset after success", self.name)
            self._state.failures = 0
            self._state.opened_at = None

    async def record_failure(self) -> None:
        async with self._lock:
            self._state.failures += 1
            if self._state.failures >= self.failure_threshold and self._state.opened_at is None:
                self._state.opened_at = self._now()
                log.warning("circuit_breaker[%s] OPEN after %d failures",
                            self.name, self._state.failures)

    async def is_open(self) -> bool:
        async with self._lock:
            await self._maybe_close()
            return self._state.opened_at is not None

    @asynccontextmanager
    async def guarded(self, op: str):
        if await self.is_open():
            raise CircuitOpen(f"{self.name} breaker open ({op})")
        try:
            yield
        except Exception:
            await self.record_failure()
            raise
        else:
            await self.record_success()


_breakers: dict[str, CircuitBreaker] = {}
_lock = asyncio.Lock()


def get(name: str, **kwargs) -> CircuitBreaker:
    """Module-level singleton accessor."""
    b = _breakers.get(name)
    if b is None:
        b = CircuitBreaker(name, **kwargs)
        _breakers[name] = b
    return b
