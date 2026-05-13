"""Centralised deny-list (v17c anti-abuse).

Single source of truth for "should this request be denied before it
costs us money or burns provider budget?". Read on the hot path by:

  * `middleware.ip_throttle` — refuse any request whose source IP is
    blocked (`scope='ip'`).
  * `otp.request_code` — refuse SMS/email send if target is blocked
    (`scope='phone'` / `'email'`) or source IP is blocked.
  * `auth.current_user` — refuse JWT validation when the user_id is
    blocked (`scope='user'`), so a stolen refresh token can't keep
    a banned user alive.

Cached in-process for 30s under an RLock so the middleware doesn't
re-query SQLite per request. Cache flushes on every admin write.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import user_repo

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30
_lock = threading.RLock()
_cache: dict[str, object] = {"value": None, "fetched_at": 0.0}


@dataclass
class BlockEntry:
    scope: str
    value: str
    reason: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    expires_at: Optional[datetime]
    # v17d — when True, hits are recorded to logs/metrics but do NOT
    # actually deny the request. Lets admin sanity-check a rule for
    # ~24h before promoting it to enforcement.
    dry_run: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[tuple[str, str], BlockEntry]:
    now_iso = _now_iso()
    out: dict[tuple[str, str], BlockEntry] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT scope, value, reason, created_by, created_at, expires_at, "
            "dry_run FROM blocklist WHERE expires_at IS NULL OR expires_at > ?",
            (now_iso,),
        ).fetchall()
    for r in rows:
        exp = datetime.fromisoformat(r[5]) if r[5] else None
        out[(r[0], r[1])] = BlockEntry(
            scope=r[0], value=r[1], reason=r[2], created_by=r[3],
            created_at=datetime.fromisoformat(r[4]), expires_at=exp,
            dry_run=bool(r[6]) if len(r) > 6 else False,
        )
    return out


def _get_all() -> dict[tuple[str, str], BlockEntry]:
    with _lock:
        cached = _cache.get("value")
        ts = float(_cache.get("fetched_at") or 0.0)
        if cached is not None and time.time() - ts < _CACHE_TTL_SEC:
            return cached  # type: ignore[return-value]
        v = _load()
        _cache["value"] = v
        _cache["fetched_at"] = time.time()
        return v


def _cidr_match(value: str, cidr: str) -> bool:
    """True if `value` (single IP) belongs to `cidr` network."""
    try:
        from ipaddress import ip_address, ip_network
        return ip_address(value) in ip_network(cidr, strict=False)
    except ValueError:
        return False


def is_blocked(scope: str, value: str) -> Optional[BlockEntry]:
    """Cheap O(1) exact lookup + O(N_cidr) CIDR scan for IP scope.

    Returns the entry if blocked AND enforcing (dry_run=False), else
    None. Dry-run hits are recorded via `record_dryrun_hit` so admin
    sees what *would* have been blocked without users feeling it."""
    if not value:
        return None
    cache = _get_all()
    entry = cache.get((scope, value))
    # v17d — CIDR fallback for IP scope. Linear scan is fine because
    # the count of CIDR rules is admin-curated (handfuls, not 1000s);
    # if it ever grows we'd switch to a radix tree.
    if entry is None and scope == "ip":
        for (s, v), e in cache.items():
            if s == "ip" and "/" in v and _cidr_match(value, v):
                entry = e
                break
    if entry is None:
        return None
    if entry.dry_run:
        record_dryrun_hit(entry)
        return None
    # v17d — count enforcing hits too so admin metrics page can show
    # "saved N requests in last hour by blocklist".
    try:
        from . import rate_buckets
        rate_buckets.hit("blocklist_enforce", entry.scope, entry.value, 3600)
    except Exception:                                               # noqa: BLE001
        pass
    return entry


def record_dryrun_hit(entry: BlockEntry) -> None:
    """Counter so admin can quantify "if I promoted this to enforce,
    how many requests would it block?". Cheap; no DB write."""
    try:
        from . import rate_buckets
        rate_buckets.hit("blocklist_dryrun", entry.scope, entry.value, 3600)
    except Exception:                                               # noqa: BLE001
        pass


def peek_dryrun_hits(scope: str, value: str) -> int:
    """Read the rolling-1h dry-run hit count for an entry."""
    try:
        from . import rate_buckets
        return rate_buckets.peek("blocklist_dryrun", scope, value, 3600)
    except Exception:                                               # noqa: BLE001
        return 0


def gc_expired(grace_days: int = 30) -> int:
    """Delete blocklist rows whose expires_at is more than `grace_days`
    in the past. Kept around for a while so admin can still see "we
    blocked X for Y reason last week" in audits, but eventually we
    don't want the table to grow forever.

    Returns rows deleted. Cheap; safe to call repeatedly. Wired into
    app startup as a fire-and-forget task."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=grace_days)).isoformat()
    with user_repo._connect() as con:                               # noqa: SLF001
        cur = con.execute(
            "DELETE FROM blocklist WHERE expires_at IS NOT NULL "
            "AND expires_at < ?",
            (cutoff,),
        )
        con.commit()
        n = cur.rowcount
    if n:
        log.info("blocklist: gc removed %d expired rows", n)
        _flush_cache()
    return n


def add(scope: str, value: str, *, reason: Optional[str] = None,
        created_by: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        dry_run: bool = False) -> BlockEntry:
    if scope not in {"ip", "phone", "email", "user"}:
        raise ValueError(f"unknown blocklist scope: {scope}")
    if not value or len(value) > 256:
        raise ValueError("invalid blocklist value")
    # v17d — accept either a single IP or a CIDR for ip scope.
    if scope == "ip":
        try:
            from ipaddress import ip_network
            ip_network(value, strict=False)
        except ValueError as e:
            raise ValueError(f"invalid IP/CIDR: {e}")
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT OR REPLACE INTO blocklist (scope, value, reason, "
            "created_by, created_at, expires_at, dry_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope, value, reason, created_by, _now_iso(),
             expires_at.isoformat() if expires_at else None,
             1 if dry_run else 0),
        )
        con.commit()
    _flush_cache()
    log.warning("blocklist: %s scope=%s value=%s by=%s",
                "dry-run" if dry_run else "blocked",
                scope, value, created_by)
    return BlockEntry(scope=scope, value=value, reason=reason,
                       created_by=created_by,
                       created_at=datetime.now(timezone.utc),
                       expires_at=expires_at, dry_run=dry_run)


def remove(scope: str, value: str) -> bool:
    with user_repo._connect() as con:                               # noqa: SLF001
        cur = con.execute(
            "DELETE FROM blocklist WHERE scope = ? AND value = ?",
            (scope, value),
        )
        con.commit()
        deleted = cur.rowcount > 0
    if deleted:
        _flush_cache()
        log.info("blocklist: removed scope=%s value=%s", scope, value)
    return deleted


def list_all(scope: Optional[str] = None) -> list[BlockEntry]:
    items = list(_get_all().values())
    if scope:
        items = [e for e in items if e.scope == scope]
    items.sort(key=lambda e: e.created_at, reverse=True)
    return items


def _flush_cache() -> None:
    with _lock:
        _cache["value"] = None
        _cache["fetched_at"] = 0.0


def reset_for_tests() -> None:
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute("DELETE FROM blocklist")
        con.commit()
    _flush_cache()


__all__ = ["BlockEntry", "is_blocked", "add", "remove",
            "list_all", "reset_for_tests"]
