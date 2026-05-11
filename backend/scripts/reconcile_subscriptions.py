"""Hourly cron — pull every active subscription's latest state from
Apple's App Store Server API as a safety net for missed ASN V2 webhooks.

Apple is generally reliable but their own docs admit notifications can
get dropped under load. Without this safety net a refunded user might
keep Pro until they next purchase. We accept the latency trade-off
(checks run hourly) for resilience.

Usage:
    python -m scripts.reconcile_subscriptions
    # or via cron:
    # 0 * * * *  /usr/bin/python -m scripts.reconcile_subscriptions

Setup:
    APPLE_IAP_ISSUER_ID=...
    APPLE_IAP_KEY_ID=...
    APPLE_IAP_PRIVATE_KEY_PATH=/etc/secrets/AuthKey_XXX.p8
    APPLE_IAP_BUNDLE_ID=com.example.aiphotocoach

When the env vars aren't set (e.g. local dev) the script logs a notice
and exits 0, so adding it to cron immediately is safe.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import jwt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.services import iap_apple, user_repo  # noqa: E402

setup_logging("INFO")
log = logging.getLogger(__name__)


def _make_apple_jwt(*, issuer_id: str, key_id: str, key_pem: bytes,
                     bundle_id: str) -> str:
    """App Store Server API uses an ES256 JWT signed with a .p8 key."""
    now = int(time.time())
    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + 60 * 60,
        "aud": "appstoreconnect-v1",
        "bid": bundle_id,
    }
    headers = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    return jwt.encode(payload, key_pem, algorithm="ES256", headers=headers)


def _api_base(environment: str) -> str:
    if (environment or "").lower() == "sandbox":
        return "https://api.storekit-sandbox.itunes.apple.com"
    return "https://api.storekit.itunes.apple.com"


def reconcile() -> int:
    settings = get_settings()
    issuer_id = os.getenv("APPLE_IAP_ISSUER_ID", "").strip()
    key_id = os.getenv("APPLE_IAP_KEY_ID", "").strip()
    key_path = os.getenv("APPLE_IAP_PRIVATE_KEY_PATH", "").strip()
    bundle_id = settings.apple_iap_bundle_id.strip()

    if not (issuer_id and key_id and key_path and bundle_id):
        log.info(
            "reconcile_subscriptions: missing env "
            "(APPLE_IAP_ISSUER_ID/KEY_ID/PRIVATE_KEY_PATH/BUNDLE_ID); "
            "skipping. Configure these to enable hourly safety-net "
            "reconciliation.",
        )
        return 0

    key_pem = Path(key_path).read_bytes()
    token = _make_apple_jwt(
        issuer_id=issuer_id, key_id=key_id, key_pem=key_pem,
        bundle_id=bundle_id,
    )
    headers = {"Authorization": f"Bearer {token}"}

    # Walk every subscription we know about. The API is per
    # originalTransactionId, so we batch one HTTP call per row.
    seen = updated = revoked = errors = 0
    with user_repo._connect() as con:
        rows = con.execute(
            "SELECT original_transaction_id, environment, user_id "
            "FROM subscriptions",
        ).fetchall()
    for r in rows:
        seen += 1
        original_id = r["original_transaction_id"]
        environment = r["environment"] or settings.apple_iap_environment
        user_id = r["user_id"]
        url = (
            f"{_api_base(environment)}/inApps/v1/subscriptions/{original_id}"
        )
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers)
            if resp.status_code == 404:
                log.info("apple sub %s not found upstream; revoking", original_id)
                revoked += 1
                _revoke_locally(original_id, user_id)
                continue
            resp.raise_for_status()
            data = resp.json()
            applied = _apply_state(user_id=user_id, data=data)
            if applied:
                updated += 1
        except Exception as e:                                  # noqa: BLE001
            log.warning("apple sub %s reconcile failed: %s", original_id, e)
            errors += 1

    log.info(
        "reconcile_subscriptions done: seen=%d updated=%d revoked=%d errors=%d",
        seen, updated, revoked, errors,
    )
    return 0


def _revoke_locally(original_id: str, user_id: Optional[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with user_repo._connect() as con:
        con.execute(
            "UPDATE subscriptions SET revoked_at = COALESCE(revoked_at, ?) "
            "WHERE original_transaction_id = ?",
            (now, original_id),
        )
    if user_id:
        active = user_repo.list_active_subscriptions(user_id)
        if not any(s.product_id for s in active):
            user_repo.set_tier(user_id, "free")


def _apply_state(*, user_id: str, data: dict) -> bool:
    """Apple returns the most recent transaction info as a JWS in
    `data.lastTransactions[0].signedTransactionInfo`. We decode it
    through the same path as `/iap/verify` and reuse `_apply_subscription`
    semantics (avoid the FastAPI dependency import by reimplementing
    inline).
    """
    txns = (data.get("data") or [{}])[0].get("lastTransactions") or []
    if not txns:
        return False
    inner = txns[0].get("signedTransactionInfo")
    if not inner:
        return False
    txn = iap_apple.decode_transaction_jws(inner)
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
        raw_jws=inner,
    )
    # Re-evaluate tier from the current set of active subs.
    active = user_repo.list_active_subscriptions(user_id)
    pros = {p.strip() for p in
            (get_settings().apple_iap_pro_product_ids or "").split(",")
            if p.strip()}
    user_repo.set_tier(user_id, "pro" if any(s.product_id in pros for s in active) else "free")
    return True


if __name__ == "__main__":
    sys.exit(reconcile())
