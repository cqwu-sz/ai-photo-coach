"""PR4 — three subscription tiers (monthly/quarterly/yearly).

Reuses the JWS forgery helper from test_iap_smoke for parity."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client: TestClient, device_id: str) -> dict:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user_id"]


def _forge(product_id: str, *, original_id: str = "TXN-1",
           expires_ms: int = 9_999_999_999_000) -> str:
    return jwt.encode({
        "productId": product_id,
        "originalTransactionId": original_id,
        "transactionId": original_id,
        "purchaseDate": 1_700_000_000_000,
        "expiresDate": expires_ms,
        "environment": "Sandbox",
        "bundleId": "",
        "autoRenewStatus": 1,
    }, "test", algorithm="HS256")


def test_monthly_plan_carries_quota_total(client):
    headers, _ = _bearer(client, "iap-M")
    r = client.post(
        "/iap/verify",
        json={"jws_representation": _forge("ai_photo_coach.pro.monthly",
                                            original_id="TXN-M")},
        headers=headers,
    )
    body = r.json()
    assert body["plan"] == "monthly"
    assert body["quota_total"] == 100
    assert body["tier"] == "pro"


def test_quarterly_plan_500(client):
    headers, _ = _bearer(client, "iap-Q")
    r = client.post(
        "/iap/verify",
        json={"jws_representation": _forge("ai_photo_coach.pro.quarterly",
                                            original_id="TXN-Q")},
        headers=headers,
    )
    body = r.json()
    assert body["plan"] == "quarterly"
    assert body["quota_total"] == 500


def test_yearly_plan_2000(client):
    headers, _ = _bearer(client, "iap-Y")
    r = client.post(
        "/iap/verify",
        json={"jws_representation": _forge("ai_photo_coach.pro.yearly",
                                            original_id="TXN-Y")},
        headers=headers,
    )
    body = r.json()
    assert body["plan"] == "yearly"
    assert body["quota_total"] == 2000


def test_overlapping_plans_yearly_wins(client):
    """Yearly should beat monthly on plan rank even if monthly was newer."""
    headers, _ = _bearer(client, "iap-MIX")
    client.post("/iap/verify",
                json={"jws_representation": _forge(
                    "ai_photo_coach.pro.monthly", original_id="TXN-M2")},
                headers=headers)
    client.post("/iap/verify",
                json={"jws_representation": _forge(
                    "ai_photo_coach.pro.yearly", original_id="TXN-Y2")},
                headers=headers)
    r = client.get("/me/entitlements", headers=headers)
    body = r.json()
    assert body["plan"] == "yearly"
    assert body["quota_total"] == 2000


def test_admin_role_returns_unlimited(client):
    headers, uid = _bearer(client, "iap-ADM")
    user_repo.set_role(uid, "admin")
    r = client.get("/me/entitlements", headers=headers)
    body = r.json()
    assert body["tier"] == "pro"
    assert body["plan"] == "admin"
    assert body["quota_total"] is None
