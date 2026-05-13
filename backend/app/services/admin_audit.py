"""Admin audit-log writer (v17e).

Centralises writes to `admin_audit_log` so every part of the
service can emit events without duplicating SQL. Originally lived
inline in `api/admin.py::_audit` — pulled out so OTP/auth/IAP/data
export paths can record events without an admin actor.

Convention for `admin_id` when the actor isn't an admin user:
  * "system"        — automatic (e.g. permanent OTP lock, ASN renewal)
  * "user:<uid>"    — user-initiated sensitive op (e.g. data export)
  * "<admin uuid>"  — actual admin acting in dashboard

Convention for `action`:
  * `<domain>.<verb>` lowercase snake (e.g. `auth.login_success`,
    `iap.refund`, `user.soft_delete`).

Payload is JSON; truncated to 8 KB to keep the log table cheap.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import user_repo

log = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 8000


def write(admin_id: str, action: str, *,
          target: Optional[str] = None,
          payload: Optional[dict[str, Any]] = None) -> None:
    """Best-effort: never raises. Audit failure must not fail a
    business request — we log a warning and move on."""
    occurred_at = datetime.now(timezone.utc).isoformat()
    try:
        body = (json.dumps(payload, ensure_ascii=False, default=str)
                if payload else None)
        if body and len(body) > _MAX_PAYLOAD_BYTES:
            body = body[:_MAX_PAYLOAD_BYTES] + "...[truncated]"
        with user_repo._connect() as con:                           # noqa: SLF001
            con.execute(
                "INSERT INTO admin_audit_log (admin_id, action, target, "
                "payload, occurred_at) VALUES (?, ?, ?, ?, ?)",
                (admin_id, action, target, body, occurred_at),
            )
            con.commit()
    except Exception as e:                                          # noqa: BLE001
        log.warning("admin_audit.write failed action=%s err=%s", action, e)
        return

    # v17g — fan out to email subscribers for high-value actions.
    # Best-effort, throttled inside the mailer; failure must not
    # affect the calling business request.
    try:
        from . import alert_mailer
        alert_mailer.maybe_send_for_audit(
            action, admin_id=admin_id, target=target,
            payload=payload, occurred_at=occurred_at,
        )
    except Exception as e:                                          # noqa: BLE001
        log.debug("admin_audit: alert_mailer skipped: %s", e)


__all__ = ["write"]
