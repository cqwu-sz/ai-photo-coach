"""User-facing /me/usage endpoints (PR6 of subscription/auth rework).

GET  /me/usage           — paginated list (most recent first)
GET  /me/usage/{id}      — full record with the four-step config
PATCH /me/usage/{id}/pick       — record which proposal the user chose
PATCH /me/usage/{id}/captured   — record that the user actually shot
PATCH /me/usage/{id}/satisfied  — v18 thumbs up/down on the result

The 'pick' / 'captured' / 'satisfied' patches do NOT affect quota —
that has already been settled at /analyze time. They exist only so
the user (and admin audits) can see the full lifecycle, and so the
analyze prompt can grow a per-user style preference signal.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..services import admin_audit
from ..services import auth as auth_svc
from ..services import usage_records

log = logging.getLogger(__name__)
router = APIRouter(tags=["usage"])


class UsageRecordSummary(BaseModel):
    id: str
    request_id: str
    status: str
    created_at: datetime
    charge_at: Optional[datetime] = None
    refund_at: Optional[datetime] = None
    error_code: Optional[str] = None
    captured: bool = False
    picked_proposal_id: Optional[str] = None
    scene_mode: Optional[str] = None
    person_count: Optional[int] = None


class UsageRecordDetail(UsageRecordSummary):
    step_config: dict
    proposals: list[dict]
    picked_at: Optional[datetime] = None
    captured_at: Optional[datetime] = None
    model_id: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None


class UsageListOut(BaseModel):
    items: list[UsageRecordSummary]
    next_cursor: Optional[str] = None


def _summary(rec: usage_records.UsageRecord) -> UsageRecordSummary:
    return UsageRecordSummary(
        id=rec.id,
        request_id=rec.request_id,
        status=rec.status,
        created_at=rec.created_at,
        charge_at=rec.charge_at,
        refund_at=rec.refund_at,
        error_code=rec.error_code,
        captured=rec.captured,
        picked_proposal_id=rec.picked_proposal_id,
        scene_mode=rec.step_config.get("scene_mode"),
        person_count=rec.step_config.get("person_count"),
    )


@router.get("/me/usage", response_model=UsageListOut)
async def list_my_usage(
    limit: int = Query(20, ge=1, le=100),
    before: Optional[str] = Query(None, description="cursor: previous page's last id"),
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> UsageListOut:
    rows = usage_records.list_for_user(user.id, limit=limit, before_id=before)
    next_cursor = rows[-1].id if len(rows) == limit else None
    return UsageListOut(items=[_summary(r) for r in rows], next_cursor=next_cursor)


@router.get("/me/usage/{record_id}", response_model=UsageRecordDetail)
async def get_my_usage(
    record_id: str,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> UsageRecordDetail:
    rec = usage_records.get_for_user(user.id, record_id)
    if rec is None:
        raise HTTPException(404, {"error": {"code": "usage_record_not_found"}})
    base = _summary(rec)
    return UsageRecordDetail(
        **base.model_dump(),
        step_config=rec.step_config,
        proposals=rec.proposals,
        picked_at=rec.picked_at,
        captured_at=rec.captured_at,
        model_id=rec.model_id,
        prompt_tokens=rec.prompt_tokens,
        completion_tokens=rec.completion_tokens,
        cost_usd=rec.cost_usd,
    )


class PickIn(BaseModel):
    proposal_id: str


@router.patch("/me/usage/{record_id}/pick")
async def pick_proposal(
    record_id: str,
    payload: PickIn,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    rec = usage_records.get_for_user(user.id, record_id)
    if rec is None:
        raise HTTPException(404, {"error": {"code": "usage_record_not_found"}})
    usage_records.mark_picked(user_id=user.id, record_id=record_id,
                                proposal_id=payload.proposal_id)
    return {"ok": True}


@router.patch("/me/usage/{record_id}/captured")
async def mark_captured(
    record_id: str,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    rec = usage_records.get_for_user(user.id, record_id)
    if rec is None:
        raise HTTPException(404, {"error": {"code": "usage_record_not_found"}})
    usage_records.mark_captured(user_id=user.id, record_id=record_id)
    return {"ok": True}


class SatisfiedIn(BaseModel):
    satisfied: bool
    note: Optional[str] = None  # truncated server-side to 200 chars
    # v18 s1 — preferred client signal. One of "love" / "ok" / "bad".
    # Backwards-compat: if missing, server derives "ok"/"bad" from
    # the bool above.
    grade: Optional[str] = None


@router.patch("/me/usage/{record_id}/satisfied")
async def mark_satisfied(
    record_id: str,
    payload: SatisfiedIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    """v18 — record the user's thumbs-up/down on the proposal they
    actually shot. Anchors the per-user preference + global aggregate
    used by analyze prompt injection.

    Owner-scoped (404 if the record belongs to someone else, no leak).
    Idempotent: re-PATCHing overwrites the previous answer.
    """
    rec = usage_records.get_for_user(user.id, record_id)
    if rec is None:
        raise HTTPException(404, {"error": {"code": "usage_record_not_found"}})
    usage_records.mark_satisfied(user_id=user.id, record_id=record_id,
                                   satisfied=payload.satisfied,
                                   note=payload.note,
                                   grade=payload.grade)
    # Audit: log the bool + note LENGTH (never the note content) so
    # we can incident-respond to "did this user really click thumbs
    # down 50 times?" without persisting their words a second time.
    note_len = len(payload.note or "")
    client_ip = (request.client.host if request.client else None)
    admin_audit.write(
        user.id, "usage.satisfied",
        target=record_id,
        payload={
            "satisfied": payload.satisfied,
            "grade": payload.grade,
            "note_len": note_len,
            "scene_mode": (rec.step_config or {}).get("scene_mode"),
            "client_ip": client_ip,
            "user_agent": request.headers.get("user-agent"),
        },
    )
    return {"ok": True}


@router.delete("/me/preferences")
async def reset_my_preferences(
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    """v18 c2 — wipe THIS user's style-preference snapshots.

    Lower-friction than 'delete account'. Anonymous global aggregates
    are NOT touched (they no longer reference the user). The
    `usage_records.satisfied*` columns are preserved so the user's
    own history page still shows what they answered.
    """
    from ..services import user_preferences
    user_preferences.purge_for_user(user.id)
    admin_audit.write(
        user.id, "user.preferences_reset",
        target=user.id,
        payload={
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        },
    )
    return {"ok": True}
