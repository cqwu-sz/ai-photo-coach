"""User-visible request audit log (PR6 of subscription/auth rework).

Each /analyze invocation gets one row in `usage_records`. The row is:
  - INSERTed in `pending` state at the start of the request
  - UPDATEd to `charged` (with token / cost / proposals) on success
  - UPDATEd to `failed` (with error_code) when the analyze pipeline
    blew up; in that case usage_quota.rollback already refunded the
    slot, so the user sees "本次未消耗次数" in the iOS history page

Two later mutations are user-driven:
  - `mark_picked` when the user taps a proposal in the iOS picker
  - `mark_captured` when the user actually shoots the photo

The file lives in the same sqlite db as `users`/`usage_periods` so
cross-table joins (admin audit, /me/usage) stay cheap.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from . import user_repo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class UsageRecord:
    id: str
    user_id: str
    request_id: str
    status: str
    charge_at: Optional[datetime]
    refund_at: Optional[datetime]
    step_config: dict
    proposals: list[dict]
    picked_proposal_id: Optional[str]
    picked_at: Optional[datetime]
    captured: bool
    captured_at: Optional[datetime]
    model_id: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    cost_usd: Optional[float]
    error_code: Optional[str]
    reservation_id: Optional[str]
    created_at: datetime
    # v18 — user-driven satisfaction signal. None = no answer (most
    # rows). True/False = the user explicitly tapped the chip.
    satisfied: Optional[bool] = None
    satisfied_at: Optional[datetime] = None
    satisfied_note: Optional[str] = None
    # v18 s1 — one of "love" / "ok" / "bad" / None.
    satisfied_grade: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _row(row: sqlite3.Row) -> UsageRecord:
    # v18 — `satisfied`/`satisfied_at`/`satisfied_note` are added via
    # additive migration. Rows from older deploys won't have them as
    # row-keys; sqlite3.Row raises IndexError on missing keys, so
    # probe with `keys()` to stay back-compat.
    keys = row.keys()
    raw_sat = row["satisfied"] if "satisfied" in keys else None
    return UsageRecord(
        id=row["id"],
        user_id=row["user_id"],
        request_id=row["request_id"],
        status=row["status"],
        charge_at=_parse(row["charge_at"]),
        refund_at=_parse(row["refund_at"]),
        step_config=_safe_json(row["step_config"]) or {},
        proposals=_safe_json(row["proposals"]) or [],
        picked_proposal_id=row["picked_proposal_id"],
        picked_at=_parse(row["picked_at"]),
        captured=bool(row["captured"]),
        captured_at=_parse(row["captured_at"]),
        model_id=row["model_id"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        cost_usd=row["cost_usd"],
        error_code=row["error_code"],
        reservation_id=row["reservation_id"],
        created_at=_parse(row["created_at"]) or _now(),
        satisfied=(None if raw_sat is None else bool(raw_sat)),
        satisfied_at=(_parse(row["satisfied_at"])
                       if "satisfied_at" in keys else None),
        satisfied_note=(row["satisfied_note"]
                          if "satisfied_note" in keys else None),
        satisfied_grade=(row["satisfied_grade"]
                           if "satisfied_grade" in keys else None),
    )


def _safe_json(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_pending(*, user_id: str, request_id: str,
                    step_config: dict,
                    reservation_id: Optional[str] = None) -> str:
    """INSERT a fresh pending record. Returns its id (uuid)."""
    rid = str(uuid.uuid4())
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT INTO usage_records (id, user_id, request_id, status, "
            "step_config, proposals, captured, reservation_id, created_at) "
            "VALUES (?, ?, ?, 'pending', ?, '[]', 0, ?, ?)",
            (rid, user_id, request_id,
             json.dumps(step_config, ensure_ascii=False, default=str),
             reservation_id, _iso(_now())),
        )
    return rid


def mark_charged(record_id: str, *,
                  proposals: list[dict],
                  model_id: Optional[str] = None,
                  prompt_tokens: Optional[int] = None,
                  completion_tokens: Optional[int] = None,
                  cost_usd: Optional[float] = None) -> None:
    """Move a record from pending → charged. Stores token/cost too."""
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_records SET status='charged', charge_at=?, "
            "proposals=?, model_id=?, prompt_tokens=?, completion_tokens=?, "
            "cost_usd=? WHERE id=?",
            (_iso(_now()),
             json.dumps(proposals, ensure_ascii=False, default=str),
             model_id, prompt_tokens, completion_tokens, cost_usd,
             record_id),
        )


def mark_failed(record_id: str, *, error_code: str) -> None:
    """Move a record to failed. usage_quota.rollback() must be called
    separately to refund the slot."""
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_records SET status='failed', refund_at=?, error_code=? "
            "WHERE id=?",
            (_iso(_now()), error_code, record_id),
        )


def mark_picked(*, user_id: str, record_id: str,
                 proposal_id: str) -> None:
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_records SET picked_proposal_id=?, picked_at=? "
            "WHERE id=? AND user_id=?",
            (proposal_id, _iso(_now()), record_id, user_id),
        )


def mark_captured(*, user_id: str, record_id: str) -> None:
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_records SET captured=1, captured_at=? "
            "WHERE id=? AND user_id=?",
            (_iso(_now()), record_id, user_id),
        )


_VALID_GRADES = {"love", "ok", "bad"}


def mark_satisfied(*, user_id: str, record_id: str,
                    satisfied: bool,
                    note: Optional[str] = None,
                    grade: Optional[str] = None) -> None:
    """v18 — record the user's thumbs reaction on the proposal that
    they (presumably) shot. Note is truncated to 200 chars to keep
    the column small and bound the audit footprint.

    `grade` is the v18-s1 3-way enum ("love" / "ok" / "bad"). When
    the client doesn't send one we fall back to deriving it from
    `satisfied`: True → "ok", False → "bad" (we can't distinguish
    "love" from "ok" without explicit signal).

    Idempotent in the sense that the same user can flip their answer
    later (UPDATE overwrites). We do *not* version the note.
    """
    note_safe = (note or "").strip()[:200] or None
    g = (grade or "").strip().lower() or None
    if g not in _VALID_GRADES:
        g = ("ok" if satisfied else "bad")
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_records SET satisfied=?, satisfied_at=?, "
            "satisfied_note=?, satisfied_grade=? "
            "WHERE id=? AND user_id=?",
            (1 if satisfied else 0, _iso(_now()), note_safe, g,
             record_id, user_id),
        )
    # Side-effects: bump per-user preference + global aggregate. Both
    # are best-effort; they must never raise back to the user-facing
    # PATCH, hence the broad except.
    try:
        from . import user_preferences
        user_preferences.upsert_from_record(user_id=user_id,
                                              record_id=record_id,
                                              satisfied=satisfied)
    except Exception as e:                                           # noqa: BLE001
        log.warning("user_preferences upsert failed (non-fatal): %s", e)


def list_for_user(user_id: str, *, limit: int = 30,
                  before_id: Optional[str] = None) -> list[UsageRecord]:
    """Reverse-chronological list. ``before_id`` is a cursor — pass the
    last item's id to fetch the next page."""
    limit = max(1, min(limit, 100))
    with user_repo._connect() as con:                               # noqa: SLF001
        if before_id:
            cursor = con.execute(
                "SELECT created_at FROM usage_records WHERE id=? AND user_id=?",
                (before_id, user_id),
            ).fetchone()
            if cursor is None:
                return []
            rows = con.execute(
                "SELECT * FROM usage_records WHERE user_id=? AND created_at < ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, cursor[0], limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM usage_records WHERE user_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    return [_row(r) for r in rows]


def get_for_user(user_id: str, record_id: str) -> Optional[UsageRecord]:
    with user_repo._connect() as con:                               # noqa: SLF001
        row = con.execute(
            "SELECT * FROM usage_records WHERE id=? AND user_id=?",
            (record_id, user_id),
        ).fetchone()
    return _row(row) if row else None


__all__ = [
    "UsageRecord",
    "create_pending", "mark_charged", "mark_failed",
    "mark_picked", "mark_captured", "mark_satisfied",
    "list_for_user", "get_for_user",
]
