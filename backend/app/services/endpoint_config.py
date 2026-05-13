"""Admin-driven server endpoint configuration (v17b).

Lets admins switch the URL all clients should use without an app
update. iOS polls ``GET /api/config/endpoint`` every ~5 minutes
(plus once on cold start) and persists the result locally.

Design constraints:
  * **No bricking**: clients keep using the previous URL until
    they successfully fetch a new one. iOS additionally probes
    ``/healthz`` on the new URL before accepting it.
  * **Graceful drain**: in-flight requests target the URL they
    were built with — we never cancel sessions on switch.
  * **Audit trail**: every change records a row in
    ``endpoint_config_history`` with the admin id and reason.
  * **Caching**: 30s in-process cache (RLock) so the public
    poll endpoint doesn't hammer SQLite.
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

_CACHE_TTL_SEC = 30
_lock = threading.RLock()
_cache: dict[str, object] = {"value": None, "fetched_at": 0.0}


@dataclass
class EndpointConfig:
    primary_url: str
    fallback_url: Optional[str]
    min_app_version: Optional[str]
    note: Optional[str]
    updated_by: Optional[str]
    updated_at: datetime
    # v17c — % of distinct devices that should adopt primary_url.
    # Anything <100 means the rest stays on fallback_url. iOS picks
    # bucket = int(sha256(device_fp)[0:8], 16) % 100 — deterministic
    # per device, so an individual user gets a stable assignment
    # across polls (no flapping).
    rollout_percentage: int = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_cfg(row) -> EndpointConfig:
    return EndpointConfig(
        primary_url=row[0],
        fallback_url=row[1],
        min_app_version=row[2],
        note=row[3],
        updated_by=row[4],
        updated_at=datetime.fromisoformat(row[5]),
        rollout_percentage=int(row[6]) if len(row) > 6 and row[6] is not None else 100,
    )


def _load_from_db() -> Optional[EndpointConfig]:
    with user_repo._connect() as con:                               # noqa: SLF001
        row = con.execute(
            "SELECT primary_url, fallback_url, min_app_version, note, "
            "updated_by, updated_at, rollout_percentage "
            "FROM endpoint_config WHERE id = 1"
        ).fetchone()
    return _row_to_cfg(row) if row else None


def _seed_from_settings() -> EndpointConfig:
    """First-run seed: read the current production URL out of settings
    so the table is never empty in fresh installs."""
    from ..config import get_settings
    s = get_settings()
    primary = (getattr(s, "default_public_base_url", None)
               or "https://api.example.com").strip()
    cfg = EndpointConfig(
        primary_url=primary, fallback_url=None,
        min_app_version=None, note="seeded from settings",
        updated_by=None, updated_at=datetime.now(timezone.utc),
    )
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT OR IGNORE INTO endpoint_config (id, primary_url, "
            "fallback_url, min_app_version, note, updated_by, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            (cfg.primary_url, cfg.fallback_url, cfg.min_app_version,
             cfg.note, cfg.updated_by, _now_iso()),
        )
        con.commit()
    return _load_from_db() or cfg


def get_current() -> EndpointConfig:
    """Cached read. Hot path for the public poll endpoint."""
    with _lock:
        cached = _cache.get("value")
        fetched_at = float(_cache.get("fetched_at") or 0.0)
        if cached and time.time() - fetched_at < _CACHE_TTL_SEC:
            return cached  # type: ignore[return-value]
        cfg = _load_from_db() or _seed_from_settings()
        _cache["value"] = cfg
        _cache["fetched_at"] = time.time()
        return cfg


def save(*, primary_url: str, fallback_url: Optional[str] = None,
         min_app_version: Optional[str] = None,
         note: Optional[str] = None,
         updated_by: Optional[str] = None,
         reason: Optional[str] = None,
         rollout_percentage: int = 100) -> EndpointConfig:
    """Update the singleton row + append history. Cache-busts."""
    primary_url = primary_url.strip().rstrip("/")
    if not primary_url.startswith(("http://", "https://")):
        raise ValueError("primary_url must start with http(s)://")
    fallback_url = (fallback_url or "").strip().rstrip("/") or None
    if fallback_url and not fallback_url.startswith(("http://", "https://")):
        raise ValueError("fallback_url must start with http(s)://")
    rollout_percentage = max(0, min(int(rollout_percentage), 100))
    if rollout_percentage < 100 and not fallback_url:
        raise ValueError("rollout_percentage<100 requires fallback_url")
    now_iso = _now_iso()
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT INTO endpoint_config (id, primary_url, fallback_url, "
            "min_app_version, note, updated_by, updated_at, rollout_percentage) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "primary_url=excluded.primary_url, "
            "fallback_url=excluded.fallback_url, "
            "min_app_version=excluded.min_app_version, "
            "note=excluded.note, "
            "updated_by=excluded.updated_by, "
            "updated_at=excluded.updated_at, "
            "rollout_percentage=excluded.rollout_percentage",
            (primary_url, fallback_url, min_app_version, note,
             updated_by, now_iso, rollout_percentage),
        )
        con.execute(
            "INSERT INTO endpoint_config_history (primary_url, fallback_url, "
            "changed_by, changed_at, reason) VALUES (?, ?, ?, ?, ?)",
            (primary_url, fallback_url, updated_by, now_iso, reason),
        )
        con.commit()
    with _lock:
        _cache["value"] = None
        _cache["fetched_at"] = 0.0
    log.info("endpoint_config: updated primary=%s by=%s", primary_url,
             updated_by)
    return get_current()


def history(limit: int = 20) -> list[dict]:
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT primary_url, fallback_url, changed_by, changed_at, reason "
            "FROM endpoint_config_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"primary_url": r[0], "fallback_url": r[1],
             "changed_by": r[2], "changed_at": r[3], "reason": r[4]}
            for r in rows]


def reset_cache_for_tests() -> None:
    with _lock:
        _cache["value"] = None
        _cache["fetched_at"] = 0.0


__all__ = ["EndpointConfig", "get_current", "save", "history",
            "reset_cache_for_tests"]
