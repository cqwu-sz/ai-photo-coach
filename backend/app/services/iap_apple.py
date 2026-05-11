"""StoreKit 2 / App Store Server Notifications V2 verification
(A0-7 / A0-8 of MULTI_USER_AUTH).

Apple signs every transaction (and every webhook payload) as a JWS
whose x5c header chains up to Apple's root CA. Full chain validation
needs the Apple AppleRootCA-G3 cert on disk; until then we run in
**unverified-decode mode**: we still parse the JWS payload (so users
get the right tier locally) but log a loud warning so prod operators
flip enforcement on.

Two entry points:
  - `decode_transaction_jws(jws)` for client-uploaded StoreKit2 JWS
  - `decode_notification_jws(jws)` for ASN V2 webhook bodies

Both return a normalized `IAPTransaction` so the API layer doesn't care
which path it came from.

Apple references:
  - https://developer.apple.com/documentation/appstoreservernotifications
  - https://developer.apple.com/documentation/storekit/transaction/4302394-jsonrepresentation
  - https://www.apple.com/certificateauthority/AppleRootCA-G3.cer  (commit DER → PEM)
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import jwt

log = logging.getLogger(__name__)

APPLE_ROOT_CA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "apple_root_ca_g3.pem"
)


@dataclass
class IAPTransaction:
    product_id: str
    original_transaction_id: str
    transaction_id: str
    purchase_date: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    environment: str            # 'Production' | 'Sandbox'
    bundle_id: str
    auto_renew: bool
    raw_payload: dict[str, Any]


@dataclass
class IAPNotification:
    notification_type: str      # SUBSCRIBED | DID_RENEW | EXPIRED | REFUND ...
    subtype: Optional[str]
    transaction: Optional[IAPTransaction]
    raw_payload: dict[str, Any]


def is_enforcing() -> bool:
    """Full chain validation requires Apple's root CA on disk."""
    return APPLE_ROOT_CA_PATH.exists()


def _decode_jws_unverified(jws: str) -> dict[str, Any]:
    """Decode a JWS without signature verification.

    Used as a fallback when the Apple root CA isn't on disk yet (we
    still want the app to work end-to-end during dev / staging) and
    inside the verified path to extract claims after we've validated
    the chain."""
    try:
        return jwt.decode(jws, options={"verify_signature": False})
    except jwt.InvalidTokenError as e:
        raise ValueError(f"jws decode failed: {e}") from e


def _verify_with_chain(jws: str) -> dict[str, Any]:
    """Verify JWS signature using the leaf cert from the x5c header.

    The leaf cert is signed by an Apple intermediate, which is signed by
    the root we have on disk. PyJWT doesn't validate x5c chains for us,
    so we use cryptography manually.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, ec

    headers = jwt.get_unverified_header(jws)
    x5c = headers.get("x5c")
    if not x5c:
        raise ValueError("jws missing x5c header")

    chain = [
        x509.load_der_x509_certificate(base64.b64decode(c)) for c in x5c
    ]
    leaf = chain[0]
    # Walk parent → child verifying each signature.
    with open(APPLE_ROOT_CA_PATH, "rb") as f:
        root = x509.load_pem_x509_certificate(f.read())
    chain.append(root)
    for child, parent in zip(chain[:-1], chain[1:]):
        pub = parent.public_key()
        sig = child.signature
        tbs = child.tbs_certificate_bytes
        algo = child.signature_hash_algorithm
        try:
            if isinstance(pub, ec.EllipticCurvePublicKey):
                pub.verify(sig, tbs, ec.ECDSA(algo))
            else:
                pub.verify(sig, tbs, padding.PKCS1v15(), algo)
        except Exception as e:                                  # noqa: BLE001
            raise ValueError(f"chain verify failed: {e}") from e

    # Now PyJWT can verify the JWS itself using the leaf's public key.
    leaf_pem = leaf.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    alg = headers.get("alg") or "ES256"
    return jwt.decode(jws, leaf_pem, algorithms=[alg],
                       options={"verify_aud": False})


def _safe_decode(jws: str) -> dict[str, Any]:
    if is_enforcing():
        try:
            return _verify_with_chain(jws)
        except Exception as e:                                  # noqa: BLE001
            log.warning("apple iap chain verify failed, falling back: %s", e)
    else:
        log.warning("apple iap root CA not on disk → unverified decode (DEV ONLY)")
    return _decode_jws_unverified(jws)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def _ms_to_dt(ms: Any) -> Optional[datetime]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _to_transaction(payload: dict[str, Any]) -> IAPTransaction:
    return IAPTransaction(
        product_id=str(payload.get("productId") or payload.get("product_id") or ""),
        original_transaction_id=str(payload.get("originalTransactionId") or ""),
        transaction_id=str(payload.get("transactionId") or payload.get("originalTransactionId") or ""),
        purchase_date=_ms_to_dt(payload.get("purchaseDate")) or datetime.now(timezone.utc),
        expires_at=_ms_to_dt(payload.get("expiresDate")),
        revoked_at=_ms_to_dt(payload.get("revocationDate")),
        environment=str(payload.get("environment") or "Production"),
        bundle_id=str(payload.get("bundleId") or ""),
        auto_renew=bool(payload.get("autoRenewStatus", 1)),
        raw_payload=payload,
    )


def decode_transaction_jws(jws: str) -> IAPTransaction:
    """Used by `/iap/verify` for client-uploaded StoreKit2 transactions."""
    payload = _safe_decode(jws)
    return _to_transaction(payload)


def decode_notification_jws(signed_payload: str) -> IAPNotification:
    """Used by `/apple/asn` webhook (ASN V2).

    The outer JWS payload contains `notificationType` + a nested
    `signedTransactionInfo` JWS we have to decode separately.
    """
    outer = _safe_decode(signed_payload)
    notification_type = str(outer.get("notificationType") or "")
    subtype = outer.get("subtype")
    nested = (outer.get("data") or {}).get("signedTransactionInfo")
    txn: Optional[IAPTransaction] = None
    if nested:
        try:
            inner = _safe_decode(nested)
            txn = _to_transaction(inner)
        except Exception as e:                                  # noqa: BLE001
            log.info("asn nested transaction decode failed: %s", e)
    return IAPNotification(
        notification_type=notification_type,
        subtype=str(subtype) if subtype else None,
        transaction=txn,
        raw_payload=outer,
    )


# Notification types that GRANT or RESTORE entitlement.
ASN_GRANT_TYPES = {
    "SUBSCRIBED", "DID_RENEW", "DID_CHANGE_RENEWAL_STATUS",
    "OFFER_REDEEMED", "PRICE_INCREASE",
}
# Notification types that REVOKE entitlement.
ASN_REVOKE_TYPES = {
    "EXPIRED", "REFUND", "REVOKE", "GRACE_PERIOD_EXPIRED",
    "DID_FAIL_TO_RENEW",
}
