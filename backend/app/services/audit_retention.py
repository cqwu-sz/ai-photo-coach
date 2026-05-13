"""Audit-log retention (v17g).

`admin_audit_log` grows forever otherwise. We split events by
business value:

  * KEEP_FOREVER — money & lifecycle. These are the "you'd want
    these in court" events: every IAP transaction, every account
    deletion, every admin role grant, every endpoint switch,
    every permanent OTP lock, every blocklist add/remove.
  * RETAIN_365D — config knobs. runtime_settings, model_config —
    a year is enough to answer "when did we change this?".
  * RETAIN_90D — high-volume, low-stakes. auth.login_success,
    auth.logout, auth.refresh_failed, user.data_export. These
    are mostly forensic-on-demand; older than 90d we don't care.
  * RETAIN_30D — defaults: anything else we don't explicitly classify.

Run from a daily cron / lifespan-on-startup. Idempotent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from . import user_repo

log = logging.getLogger(__name__)


# --- Classification ---------------------------------------------------------


_KEEP_FOREVER: frozenset[str] = frozenset({
    # Money — Apple receipts.
    "iap.verify",
    # All asn.* events are financial too; matched by prefix below.
    # User lifecycle.
    "user.soft_delete", "user.hard_delete",
    "user.grant_pro", "user.set_role",
    # Security-critical.
    "otp.permanent_lock",
    "blocklist.add", "blocklist.remove",
    # Infra changes that affect everyone.
    "endpoint_config.save",
})

_FOREVER_PREFIXES: tuple[str, ...] = ("iap.asn.",)

_RETAIN_365D: frozenset[str] = frozenset({
    "model_config.save",
    "runtime_settings.set",
    "free_quota.set",
})

_RETAIN_90D: frozenset[str] = frozenset({
    "auth.login_success",
    "auth.admin_login_success",
    "auth.logout",
    "auth.refresh_failed",
    "user.data_export",
    "iap.local.error",
    "asn.unmatched",
    "asn.signature_invalid",
})


def _classify(action: str) -> int | None:
    """Return retention days, or None for KEEP_FOREVER."""
    if action in _KEEP_FOREVER:
        return None
    if any(action.startswith(p) for p in _FOREVER_PREFIXES):
        return None
    if action in _RETAIN_365D:
        return 365
    if action in _RETAIN_90D:
        return 90
    return 30


def gc(now: datetime | None = None) -> dict[str, int]:
    """Delete rows past their retention. Returns counts per bucket
    so the caller can log/metric them."""
    now = now or datetime.now(timezone.utc)
    deleted: dict[str, int] = {"d30": 0, "d90": 0, "d365": 0}

    plans: Iterable[tuple[str, int, frozenset[str]]] = (
        ("d30", 30, frozenset()),  # default catch-all handled separately
        ("d90", 90, _RETAIN_90D),
        ("d365", 365, _RETAIN_365D),
    )

    with user_repo._connect() as con:                               # noqa: SLF001
        # Targeted deletes for the known retention buckets.
        for label, days, actions in plans:
            if not actions:
                continue
            cutoff = (now - timedelta(days=days)).isoformat()
            placeholders = ",".join(["?"] * len(actions))
            cur = con.execute(
                f"DELETE FROM admin_audit_log "
                f"WHERE occurred_at < ? AND action IN ({placeholders})",
                (cutoff, *actions),
            )
            deleted[label] = cur.rowcount

        # Default 30d for everything NOT in any retention list and NOT
        # forever-kept.
        forever_actions = list(_KEEP_FOREVER)
        cutoff_30 = (now - timedelta(days=30)).isoformat()
        retained_actions = list(_KEEP_FOREVER | _RETAIN_365D | _RETAIN_90D)
        place_retain = ",".join(["?"] * len(retained_actions))
        # Forever prefixes excluded via NOT LIKE chain.
        not_like_clauses = " AND ".join(
            ["action NOT LIKE ?"] * len(_FOREVER_PREFIXES))
        not_like_args = [f"{p}%" for p in _FOREVER_PREFIXES]
        sql = (
            f"DELETE FROM admin_audit_log "
            f"WHERE occurred_at < ? "
            f"AND action NOT IN ({place_retain}) "
            f"AND {not_like_clauses}"
        )
        cur = con.execute(sql, (cutoff_30, *retained_actions, *not_like_args))
        deleted["d30"] = cur.rowcount
        con.commit()

    if any(deleted.values()):
        log.info("audit_retention.gc deleted %s", deleted)
    return deleted


__all__ = ["gc"]
