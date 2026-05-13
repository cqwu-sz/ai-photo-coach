"""Admin-tunable runtime knobs (v17d).

For values you'd otherwise hard-code as a Python constant but want
to be able to adjust during an incident without a redeploy:
  * OTP daily/global RPM ceilings
  * Per-IP rate-limit thresholds
  * Anything else admin might need to tweak under fire

Cached 30s in-process to keep the hot path cheap.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import user_repo

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30
_lock = threading.RLock()
_cache: dict[str, object] = {"value": None, "fetched_at": 0.0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, str]:
    out: dict[str, str] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT key, value FROM runtime_settings"
        ).fetchall()
    for r in rows:
        out[r[0]] = r[1]
    return out


def _all() -> dict[str, str]:
    with _lock:
        cached = _cache.get("value")
        ts = float(_cache.get("fetched_at") or 0.0)
        if cached is not None and time.time() - ts < _CACHE_TTL_SEC:
            return cached  # type: ignore[return-value]
        v = _load()
        _cache["value"] = v
        _cache["fetched_at"] = time.time()
        return v


def get_str(key: str, default: str) -> str:
    return _all().get(key, default)


def get_int(key: str, default: int) -> int:
    raw = _all().get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning("runtime_settings: %r is not an int (%r), using default",
                     key, raw)
        return default


def set_value(key: str, value: Any, *, updated_by: Optional[str] = None) -> str:
    if not key or len(key) > 128:
        raise ValueError("invalid runtime_settings key")
    str_value = str(value)
    if len(str_value) > 1024:
        raise ValueError("value too long")
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT OR REPLACE INTO runtime_settings (key, value, "
            "updated_by, updated_at) VALUES (?, ?, ?, ?)",
            (key, str_value, updated_by, _now_iso()),
        )
        con.commit()
    _flush_cache()
    log.info("runtime_settings: set %s=%s by=%s", key, str_value, updated_by)
    return str_value


def list_all() -> list[dict]:
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT key, value, updated_by, updated_at "
            "FROM runtime_settings ORDER BY key"
        ).fetchall()
    return [{"key": r[0], "value": r[1], "updated_by": r[2],
              "updated_at": r[3]} for r in rows]


def _flush_cache() -> None:
    with _lock:
        _cache["value"] = None
        _cache["fetched_at"] = 0.0


def reset_for_tests() -> None:
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute("DELETE FROM runtime_settings")
        con.commit()
    _flush_cache()


__all__ = ["get_str", "get_int", "set_value", "list_all",
            "reset_for_tests"]
