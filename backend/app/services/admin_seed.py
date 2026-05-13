"""Admin account bootstrap (PR2 of subscription/auth rework).

Reads ``settings.admin_bootstrap`` at startup and ensures every listed
account exists with ``role='admin'``. Idempotent — existing rows are
upgraded but never demoted, so removing an entry does NOT revoke admin
(operators must demote via a future ``/admin/users/{id}/role`` API).

Format::

    ADMIN_BOOTSTRAP="13800000000:sms,admin@yourdomain.com:email"

Each entry is ``<phone_or_email>:<channel>`` where channel ∈ {sms, email}.
The channel is currently advisory — it controls which column the row
is keyed on (``users.phone`` or ``users.email``). The login flow itself
still uses OTP/SIWA, so an admin logs in exactly the same way as a
regular user; we just flag the row.

Why no static admin password?
-----------------------------
Hard-coded master credentials are the #1 way startup admin accounts
get pwned. Forcing OTP keeps the secret in the SMS/email channel
(rotatable) and lets us revoke a leaked admin instantly via channel
takeover (change phone) without redeploying.
"""
from __future__ import annotations

import logging
from typing import Iterable

from . import user_repo

log = logging.getLogger(__name__)


def _parse(raw: str) -> list[tuple[str, str]]:
    """Parse the ``ADMIN_BOOTSTRAP`` env value into ``[(target, channel)]``."""
    out: list[tuple[str, str]] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            log.warning("admin_seed: ignoring malformed entry %r (need target:channel)",
                        chunk)
            continue
        target, channel = chunk.rsplit(":", 1)
        target = target.strip()
        channel = channel.strip().lower()
        if channel not in ("sms", "email"):
            log.warning("admin_seed: ignoring unknown channel %r in %r",
                        channel, chunk)
            continue
        if not target:
            continue
        out.append((target, channel))
    return out


def ensure_admins(raw_bootstrap: str) -> list[str]:
    """Make sure every entry in ``raw_bootstrap`` is an active admin user.

    Returns the list of user ids touched (created or upgraded). Safe
    to call repeatedly; only writes when state would actually change.
    """
    entries = _parse(raw_bootstrap)
    if not entries:
        return []
    touched: list[str] = []
    for target, channel in entries:
        try:
            user = _get_or_create(target, channel)
        except Exception as e:                                  # noqa: BLE001
            log.exception("admin_seed: failed to bootstrap %s: %s", _redact(target), e)
            continue
        if user.role != "admin":
            user_repo.set_role(user.id, "admin")
            log.info("admin_seed: promoted user_id=%s target=%s channel=%s",
                     user.id, _redact(target), channel)
        else:
            log.info("admin_seed: confirmed user_id=%s target=%s channel=%s",
                     user.id, _redact(target), channel)
        touched.append(user.id)
    return touched


def _get_or_create(target: str, channel: str) -> user_repo.User:
    if channel == "sms":
        existing = user_repo.get_by_phone(target)
        if existing is not None:
            return existing
        return user_repo.create_user(phone=target, role="admin")
    # email
    norm = target.lower()
    existing = user_repo.get_by_email(norm)
    if existing is not None:
        return existing
    return user_repo.create_user(email=norm, role="admin")


def _redact(target: str) -> str:
    """Mask phone/email so audit logs don't leak admin contacts in clear."""
    if "@" in target:
        name, _, domain = target.partition("@")
        if len(name) <= 2:
            shown = name[:1] + "*"
        else:
            shown = name[0] + "***" + name[-1]
        return f"{shown}@{domain}"
    if len(target) >= 7:
        return target[:3] + "****" + target[-4:]
    return target[:1] + "***"


__all__ = ["ensure_admins"]
