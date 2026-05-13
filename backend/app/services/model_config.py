"""Centralised vision-model configuration (PR8 of subscription/auth rework).

Single-row `model_settings` table holds the currently active fast +
high-quality model ids. Every analyze request reads from here (with a
short in-process cache so we don't slam sqlite). Admin updates flip
the cache instantly so all subsequent user requests pick up the new
choice without redeploy.

User clients no longer pick models — the iOS app has the model_id /
BYOK fields removed in the same PR. The 'fast' vs 'high' axis is the
only knob users see, and it maps to whichever vendor models the
admin currently selected.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import user_repo

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30.0
_lock = threading.RLock()       # reentrant: get_current → _seed → save → cache reset
_cache: dict[str, object] = {"value": None, "fetched_at": 0.0}


@dataclass
class ModelChoice:
    fast_model_id: str
    high_model_id: str
    updated_by: Optional[str]
    updated_at: Optional[datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _read_from_db() -> Optional[ModelChoice]:
    with user_repo._connect() as con:                               # noqa: SLF001
        row = con.execute(
            "SELECT fast_model_id, high_model_id, updated_by, updated_at "
            "FROM model_settings WHERE id = 1",
        ).fetchone()
    if row is None:
        return None
    return ModelChoice(
        fast_model_id=row[0],
        high_model_id=row[1],
        updated_by=row[2],
        updated_at=_parse(row[3]),
    )


def _seed_from_settings() -> ModelChoice:
    """First run: pull defaults from settings.* so the API still works
    on a fresh sqlite db. Persisted so admins can audit the first state."""
    from ..config import get_settings
    s = get_settings()
    fast = (s.gemini_model_fast or s.default_model_id or "gemini-2.5-flash").strip()
    high = (s.gemini_model_high or s.default_model_id or "gemini-2.5-pro").strip()
    save(fast_model_id=fast, high_model_id=high, admin_id="system",
         reason="bootstrap from env")
    return ModelChoice(fast_model_id=fast, high_model_id=high,
                        updated_by="system", updated_at=_now())


def get_current() -> ModelChoice:
    """Returns the active fast/high model choice, refreshing every
    `_CACHE_TTL_SEC` seconds. Thread-safe."""
    with _lock:
        cached = _cache.get("value")
        fetched_at = float(_cache.get("fetched_at") or 0.0)
        if cached is not None and (time.time() - fetched_at) < _CACHE_TTL_SEC:
            return cached  # type: ignore[return-value]
        choice = _read_from_db() or _seed_from_settings()
        _cache["value"] = choice
        _cache["fetched_at"] = time.time()
        return choice


def save(*, fast_model_id: str, high_model_id: str,
         admin_id: str, reason: Optional[str] = None) -> ModelChoice:
    """Persist a new fast/high pair, append to history, and bust cache.

    The `id = 1` sentinel keeps this strictly single-row. We use
    `INSERT OR REPLACE` so the first save bootstraps the row too.
    """
    fast_model_id = (fast_model_id or "").strip()
    high_model_id = (high_model_id or "").strip()
    if not fast_model_id or not high_model_id:
        raise ValueError("fast_model_id and high_model_id required")
    now_iso = _iso(_now())
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT OR REPLACE INTO model_settings "
            "(id, fast_model_id, high_model_id, updated_by, updated_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (fast_model_id, high_model_id, admin_id, now_iso),
        )
        con.execute(
            "INSERT INTO model_settings_history "
            "(fast_model_id, high_model_id, changed_by, changed_at, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (fast_model_id, high_model_id, admin_id, now_iso, reason),
        )
    with _lock:
        _cache["value"] = None
        _cache["fetched_at"] = 0.0
    log.info("model_config: saved fast=%s high=%s by=%s reason=%s",
             fast_model_id, high_model_id, admin_id, reason)
    return get_current()


def list_history(limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 200))
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT fast_model_id, high_model_id, changed_by, changed_at, reason "
            "FROM model_settings_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"fast_model_id": r[0], "high_model_id": r[1], "changed_by": r[2],
         "changed_at": r[3], "reason": r[4]} for r in rows
    ]


def reset_for_tests() -> None:
    with _lock:
        _cache["value"] = None
        _cache["fetched_at"] = 0.0


__all__ = ["ModelChoice", "get_current", "save", "list_history", "reset_for_tests"]
