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
from ..services import iap_apple, user_repo
from ..api import metrics as metrics_api

log = logging.getLogger(__name__)
router = APIRouter(tags=["iap"])


def _pro_product_ids() -> set[str]:
    raw = (get_settings().apple_iap_pro_product_ids or "").strip()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _evaluate_tier(user_id: str) -> tuple[str, Optional[user_repo.Subscription]]:
    """Walk active subscriptions and decide tier.

    Newest non-revoked, non-expired subscription wins. When multiple
    products exist (e.g. monthly + yearly), the latest expiry counts.
    """
    pros = _pro_product_ids()
    active = user_repo.list_active_subscriptions(user_id)
    best: Optional[user_repo.Subscription] = None
    for s in active:
        if s.product_id not in pros:
            continue
        if best is None or (s.expires_at or datetime.max.replace(tzinfo=timezone.utc)) > \
                            (best.expires_at or datetime.max.replace(tzinfo=timezone.utc)):
            best = s
    return ("pro" if best else "free"), best


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
    tier, _ = _evaluate_tier(user_id)
    user_repo.set_tier(user_id, tier)


# ---------------------------------------------------------------------------
# /iap/verify
# ---------------------------------------------------------------------------


class VerifyIn(BaseModel):
    jws_representation: str = Field(min_length=20,
                                     description="Transaction.jsonRepresentation from StoreKit2")


class EntitlementOut(BaseModel):
    tier: str
    product_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    auto_renew: Optional[bool] = None
    in_grace_period: Optional[bool] = None
    environment: Optional[str] = None


@router.post("/iap/verify", response_model=EntitlementOut)
async def iap_verify(payload: VerifyIn,
                      user: auth_svc.CurrentUser = Depends(auth_svc.current_user)
                      ) -> EntitlementOut:
    try:
        txn = iap_apple.decode_transaction_jws(payload.jws_representation)
    except ValueError as e:
        raise HTTPException(400, {"error": {"code": "iap_jws_invalid", "message": str(e)}})
    _apply_subscription(user_id=user.id, txn=txn,
                         raw_jws=payload.jws_representation)
    return await get_entitlements(user)  # type: ignore[arg-type]


@router.get("/me/entitlements", response_model=EntitlementOut)
async def get_entitlements(
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> EntitlementOut:
    tier, sub = _evaluate_tier(user.id)
    if not sub:
        return EntitlementOut(tier=tier)
    grace_days = get_settings().apple_iap_grace_period_days
    in_grace = False
    if sub.expires_at is not None:
        delta = (datetime.now(timezone.utc) - sub.expires_at).total_seconds()
        in_grace = -grace_days * 86400 <= delta < 0
    return EntitlementOut(
        tier=tier,
        product_id=sub.product_id,
        expires_at=sub.expires_at,
        auto_renew=sub.auto_renew,
        in_grace_period=in_grace,
        environment=sub.environment,
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
        return {"ok": True, "type": notif.notification_type, "matched_user": False}

    _apply_subscription(user_id=sub.user_id, txn=notif.transaction,
                         raw_jws=signed)
    log.info(
        "asn webhook: applied type=%s user_id=%s product=%s expires=%s",
        notif.notification_type, sub.user_id,
        notif.transaction.product_id, notif.transaction.expires_at,
    )
    return {"ok": True, "type": notif.notification_type, "matched_user": True}
