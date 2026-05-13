"""In-App Purchase verify + Apple Server Notifications V2 webhook
(A0-7 / A0-8 of MULTI_USER_AUTH).

POST /iap/verify          — client uploads StoreKit2 JWS, we update
                             subscriptions + tier
POST /apple/asn           — Apple → us webhook (renew / refund / expire)
GET  /me/entitlements     — what tier the client should unlock right now
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import auth as auth_svc
from ..services import iap_apple, usage_quota, user_repo
from ..api import metrics as metrics_api

log = logging.getLogger(__name__)
router = APIRouter(tags=["iap"])


def _pro_product_ids() -> set[str]:
    raw = (get_settings().apple_iap_pro_product_ids or "").strip()
    return {p.strip() for p in raw.split(",") if p.strip()}


# Plan ranking — when a user holds overlapping subscriptions, the
# longest-period plan wins so they aren't accidentally downgraded to
# the shorter-quota tier mid-period (PR4 of subscription rework).
_PLAN_RANK = {"yearly": 3, "quarterly": 2, "monthly": 1}


def _plan_for(product_id: str) -> Optional[str]:
    return get_settings().iap_plan_map.get(product_id, (None, 0))[0]


def _quota_total_for(product_id: str) -> int:
    return get_settings().iap_plan_map.get(product_id, (None, 0))[1]


def _evaluate_tier(
    user_id: str,
) -> tuple[str, Optional[str], Optional[user_repo.Subscription]]:
    """Walk active subscriptions and decide tier + plan.

    Selection rule when multiple subs are active:
      1. plan rank (yearly > quarterly > monthly) — protects the user's
         quota budget from being clipped by a stale shorter sub
      2. tie-breaker = latest expires_at
    Returns ``(tier, plan, subscription)`` where plan is None when no
    pro sub is active.
    """
    pros = _pro_product_ids()
    active = user_repo.list_active_subscriptions(user_id)
    best: Optional[user_repo.Subscription] = None
    best_rank = -1
    for s in active:
        if s.product_id not in pros:
            continue
        rank = _PLAN_RANK.get(_plan_for(s.product_id) or "", 0)
        if rank > best_rank:
            best, best_rank = s, rank
        elif rank == best_rank and best is not None:
            far_future = datetime.max.replace(tzinfo=timezone.utc)
            if (s.expires_at or far_future) > (best.expires_at or far_future):
                best = s
    plan = _plan_for(best.product_id) if best else None
    return ("pro" if best else "free"), plan, best


def _apply_subscription(*, user_id: str, txn: iap_apple.IAPTransaction,
                         raw_jws: str) -> None:
    settings = get_settings()
    if settings.apple_iap_bundle_id and txn.bundle_id and \
       txn.bundle_id != settings.apple_iap_bundle_id:
        raise HTTPException(400, {"error": {"code": "iap_bundle_mismatch",
                                              "got": txn.bundle_id}})
    metrics_api.inc("ai_photo_coach_iap_apply_total",
                     environment=txn.environment, product=txn.product_id)
    user_repo.upsert_subscription(
        user_id=user_id,
        product_id=txn.product_id,
        original_transaction_id=txn.original_transaction_id,
        latest_transaction_id=txn.transaction_id,
        environment=txn.environment,
        purchase_date=txn.purchase_date,
        expires_at=txn.expires_at,
        revoked_at=txn.revoked_at,
        auto_renew=txn.auto_renew,
        raw_jws=raw_jws,
    )
    tier, _plan, _sub = _evaluate_tier(user_id)
    user_repo.set_tier(user_id, tier)


# ---------------------------------------------------------------------------
# /iap/verify
# ---------------------------------------------------------------------------


class VerifyIn(BaseModel):
    jws_representation: str = Field(min_length=20,
                                     description="Transaction.jsonRepresentation from StoreKit2")


class EntitlementOut(BaseModel):
    tier: str
    plan: Optional[str] = None              # 'monthly' | 'quarterly' | 'yearly' | 'admin'
    product_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    auto_renew: Optional[bool] = None
    in_grace_period: Optional[bool] = None
    environment: Optional[str] = None
    quota_total: Optional[int] = None       # null = unlimited (admin)
    quota_used: Optional[int] = None
    quota_remaining: Optional[int] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


@router.post("/iap/verify", response_model=EntitlementOut)
async def iap_verify(payload: VerifyIn,
                      user: auth_svc.CurrentUser = Depends(auth_svc.current_user)
                      ) -> EntitlementOut:
    try:
        txn = iap_apple.decode_transaction_jws(payload.jws_representation)
    except ValueError as e:
        # v17g — JWS reject = potentially forged / replay attack.
        # Audit so admin can spot a single user_id repeatedly failing.
        from ..services import admin_audit
        admin_audit.write(
            f"user:{user.id}", "iap.local.error", target=user.id,
            payload={"reason": str(e)[:500]},
        )
        raise HTTPException(400, {"error": {"code": "iap_jws_invalid", "message": str(e)}})
    _apply_subscription(user_id=user.id, txn=txn,
                         raw_jws=payload.jws_representation)
    # v17e — every paid event must be recoverable from audit alone.
    # `iap_verify` = client-driven (purchase, restore). ASN webhook
    # uses `iap.asn` action below.
    from ..services import admin_audit
    admin_audit.write(
        f"user:{user.id}", "iap.verify", target=user.id,
        payload={"product_id": txn.product_id,
                  "environment": getattr(txn, "environment", None),
                  "expires_at": txn.expires_at,
                  "original_transaction_id":
                      getattr(txn, "original_transaction_id", None)},
    )
    return await get_entitlements(user)  # type: ignore[arg-type]


@router.get("/me/entitlements", response_model=EntitlementOut)
async def get_entitlements(
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> EntitlementOut:
    # Admin: unlimited, surfaces a friendly badge in the iOS UI.
    if user.role == "admin":
        return EntitlementOut(tier="pro", plan="admin",
                               quota_total=None, quota_used=0,
                               quota_remaining=None)

    tier, plan, sub = _evaluate_tier(user.id)
    if not sub:
        return EntitlementOut(tier=tier)
    grace_days = get_settings().apple_iap_grace_period_days
    in_grace = False
    if sub.expires_at is not None:
        delta = (datetime.now(timezone.utc) - sub.expires_at).total_seconds()
        in_grace = -grace_days * 86400 <= delta < 0
    quota_total = _quota_total_for(sub.product_id) if plan else None
    snapshot = usage_quota.get_period(user.id, role=user.role)
    return EntitlementOut(
        tier=tier,
        plan=plan,
        product_id=sub.product_id,
        expires_at=sub.expires_at,
        auto_renew=sub.auto_renew,
        in_grace_period=in_grace,
        environment=sub.environment,
        quota_total=quota_total,
        quota_used=snapshot.used if snapshot else None,
        quota_remaining=snapshot.remaining if snapshot else None,
        period_start=snapshot.period_start if snapshot else sub.purchase_date,
        period_end=snapshot.period_end if snapshot else sub.expires_at,
    )


class QuotaOut(BaseModel):
    plan: Optional[str] = None
    total: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    is_unlimited: bool = False


@router.get("/me/quota", response_model=QuotaOut)
async def get_quota(
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> QuotaOut:
    """v17 — exposes the current period budget for the iOS hero pill
    & 使用记录 page. Free users get all-None; admin gets is_unlimited."""
    if user.role == "admin":
        return QuotaOut(plan="admin", is_unlimited=True)
    snapshot = usage_quota.get_period(user.id, role=user.role)
    if snapshot is None:
        return QuotaOut()
    return QuotaOut(
        plan=snapshot.plan,
        total=snapshot.total,
        used=snapshot.used,
        remaining=snapshot.remaining,
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
    )


# ---------------------------------------------------------------------------
# /apple/asn webhook
# ---------------------------------------------------------------------------


class ASNIn(BaseModel):
    signedPayload: str


@router.post("/apple/asn")
async def apple_asn(request: Request) -> dict:
    """Apple Server Notifications V2 endpoint.

    Apple POSTs:
        { "signedPayload": "<JWS>" }
    """
    try:
        body = await request.json()
    except Exception:                                            # noqa: BLE001
        raise HTTPException(400, {"error": {"code": "asn_body_invalid"}})

    signed = body.get("signedPayload") or ""
    if not signed:
        raise HTTPException(400, {"error": {"code": "asn_missing_payload"}})

    try:
        notif = iap_apple.decode_notification_jws(signed)
    except ValueError as e:
        # v17g — JWS verify failure on Apple webhook. Either Apple
        # rotated keys (unlikely w/o notice) or someone is probing
        # our /apple/asn endpoint with forged payloads.
        from ..services import admin_audit
        admin_audit.write(
            "system", "asn.signature_invalid", target=None,
            payload={"reason": str(e)[:500]},
        )
        raise HTTPException(400, {"error": {"code": "asn_jws_invalid", "message": str(e)}})

    metrics_api.inc("ai_photo_coach_asn_total",
                     type=notif.notification_type or "unknown")

    if notif.transaction is None:
        log.info("asn webhook: type=%s without transaction (data-less notice)",
                 notif.notification_type)
        return {"ok": True, "type": notif.notification_type}

    sub = user_repo.find_subscription_by_original_id(
        notif.transaction.original_transaction_id,
    )
    if sub is None:
        log.warning(
            "asn webhook: unknown originalTransactionId=%s type=%s — "
            "ignoring (likely user not registered yet, /iap/verify will catch up)",
            notif.transaction.original_transaction_id, notif.notification_type,
        )
        # v17g — this is usually benign, but rare enough that a sudden
        # spike means our /iap/verify path broke. Audit to detect that.
        from ..services import admin_audit
        admin_audit.write(
            "system", "asn.unmatched",
            target=notif.transaction.original_transaction_id,
            payload={"type": notif.notification_type,
                      "product_id": notif.transaction.product_id},
        )
        return {"ok": True, "type": notif.notification_type, "matched_user": False}

    _apply_subscription(user_id=sub.user_id, txn=notif.transaction,
                         raw_jws=signed)
    log.info(
        "asn webhook: applied type=%s user_id=%s product=%s expires=%s",
        notif.notification_type, sub.user_id,
        notif.transaction.product_id, notif.transaction.expires_at,
    )
    # v17e — Apple Server Notification mirror. `notification_type`
    # carries the semantics admin actually wants to see (DID_RENEW,
    # REFUND, EXPIRED, REVOKE, GRACE_PERIOD_EXPIRED, etc.).
    from ..services import admin_audit
    admin_audit.write(
        "system", f"iap.asn.{(notif.notification_type or 'unknown').lower()}",
        target=sub.user_id,
        payload={"product_id": notif.transaction.product_id,
                  "expires_at": notif.transaction.expires_at,
                  "original_transaction_id":
                      notif.transaction.original_transaction_id,
                  "subtype": getattr(notif, "subtype", None)},
    )
    return {"ok": True, "type": notif.notification_type, "matched_user": True}
