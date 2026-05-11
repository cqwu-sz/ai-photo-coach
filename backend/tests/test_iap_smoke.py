"""Smoke tests for /iap/verify + /me/entitlements + /apple/asn (A0-7/A0-8).

Apple's real JWS verification needs the root CA on disk; in unit tests
we run in unverified-decode mode and forge JWTs with `jwt.encode(..., key='x', algorithm='HS256')`
which is fine because our service falls back to `verify_signature=False`
when no root CA is present.
"""
from __future__ import annotations

import json

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client: TestClient, device_id: str = "iap-dev") -> dict:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def _forge_storekit_jws(*, product_id: str = "ai_photo_coach.pro.monthly",
                         expires_ms: int = 9_999_999_999_000,
                         original_id: str = "TXN-1",
                         environment: str = "Sandbox") -> str:
    payload = {
        "productId": product_id,
        "originalTransactionId": original_id,
        "transactionId": original_id,
        "purchaseDate": 1_700_000_000_000,
        "expiresDate": expires_ms,
        "environment": environment,
        "bundleId": "",
        "autoRenewStatus": 1,
    }
    return jwt.encode(payload, "test", algorithm="HS256")


def test_iap_verify_grants_pro(client):
    headers = _bearer(client, "iap-A")
    jws = _forge_storekit_jws()
    r = client.post("/iap/verify", json={"jws_representation": jws},
                     headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "pro"
    assert body["product_id"] == "ai_photo_coach.pro.monthly"

    r2 = client.get("/me/entitlements", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["tier"] == "pro"


def test_iap_expired_subscription_is_free(client):
    headers = _bearer(client, "iap-EXP")
    jws = _forge_storekit_jws(expires_ms=1)
    r = client.post("/iap/verify", json={"jws_representation": jws},
                     headers=headers)
    assert r.status_code == 200
    assert r.json()["tier"] == "free"


def test_apple_asn_refund_revokes(client):
    headers = _bearer(client, "iap-REF")
    # First grant pro.
    jws = _forge_storekit_jws(original_id="TXN-REF")
    r = client.post("/iap/verify", json={"jws_representation": jws}, headers=headers)
    assert r.json()["tier"] == "pro"

    # Now Apple webhook posts a REFUND.
    inner = _forge_storekit_jws(
        original_id="TXN-REF", expires_ms=1,
    )
    outer = jwt.encode({
        "notificationType": "REFUND",
        "data": {"signedTransactionInfo": inner},
    }, "test", algorithm="HS256")
    r = client.post("/apple/asn", json={"signedPayload": outer})
    assert r.status_code == 200, r.text
    assert r.json()["matched_user"] is True

    r2 = client.get("/me/entitlements", headers=headers)
    assert r2.json()["tier"] == "free"
