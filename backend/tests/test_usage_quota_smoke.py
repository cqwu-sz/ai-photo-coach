"""PR5 — usage quota reservation / commit / rollback / sweeper.

Backed by the same JWS forgery helper from test_iap_smoke so we can
plant a 'pro monthly' subscription without going through StoreKit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import usage_quota, user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client, device_id: str) -> tuple[dict, str]:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user_id"]


def _grant_pro(client, headers: dict, *, plan_product: str = "ai_photo_coach.pro.monthly",
               original_id: str = "TXN-Q1") -> None:
    payload = {
        "productId": plan_product,
        "originalTransactionId": original_id,
        "transactionId": original_id,
        "purchaseDate": int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        "expiresDate": 9_999_999_999_000,
        "environment": "Sandbox",
        "bundleId": "",
        "autoRenewStatus": 1,
    }
    jws = jwt.encode(payload, "test", algorithm="HS256")
    r = client.post("/iap/verify", json={"jws_representation": jws}, headers=headers)
    assert r.status_code == 200, r.text


def test_reserve_commit_pro_user(client):
    headers, uid = _bearer(client, "qt-A")
    _grant_pro(client, headers)
    res = usage_quota.reserve(uid)
    assert res.reservation_id is not None
    assert res.snapshot is not None
    assert res.snapshot.used == 1
    assert res.snapshot.total == 100
    usage_quota.commit(res.reservation_id)

    snap = usage_quota.get_period(uid)
    assert snap.used == 1


def test_rollback_returns_slot(client):
    headers, uid = _bearer(client, "qt-B")
    _grant_pro(client, headers, original_id="TXN-Q-ROLL")
    res = usage_quota.reserve(uid)
    assert res.snapshot.used == 1
    usage_quota.rollback(res.reservation_id)
    snap = usage_quota.get_period(uid)
    assert snap.used == 0


def test_quota_exhausted_returns_402(client):
    headers, uid = _bearer(client, "qt-C")
    _grant_pro(client, headers, original_id="TXN-Q-FULL")
    # Burn through 100 monthly slots.
    for _ in range(100):
        usage_quota.reserve(uid)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        usage_quota.reserve(uid)
    assert exc.value.status_code == 402
    assert exc.value.detail["error"]["code"] == "quota_exhausted"


def test_admin_unlimited(client):
    headers, uid = _bearer(client, "qt-AD")
    user_repo.set_role(uid, "admin")
    res = usage_quota.reserve(uid, role="admin")
    assert res.reservation_id == usage_quota.ADMIN_RESERVATION_ID
    assert res.snapshot is None


def test_free_user_gets_five_shots_per_device(client):
    """v17b — free users now get a 5-shot bucket anchored on the
    device fingerprint (was: ungated). Anchoring on the device, not
    the user, is what stops registration farms."""
    _, uid = _bearer(client, "qt-FREE")
    snaps = []
    for _ in range(5):
        r = usage_quota.reserve(uid)
        assert r.reservation_id and r.reservation_id.startswith("free:")
        snaps.append(r.snapshot)
    assert snaps[-1].used == 5 and snaps[-1].total == 5
    # 6th call should be refused.
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        usage_quota.reserve(uid)
    assert ei.value.status_code == 402
    assert ei.value.detail["error"]["code"] == "free_quota_exhausted"


def test_sweep_rolls_back_expired(client):
    headers, uid = _bearer(client, "qt-SW")
    _grant_pro(client, headers, original_id="TXN-Q-SW")
    res = usage_quota.reserve(uid)
    assert res.snapshot.used == 1
    # Force-expire the reservation directly in DB.
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_reservations SET expires_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
             res.reservation_id),
        )
        con.commit()
    rolled = usage_quota.sweep_expired()
    assert rolled >= 1
    snap = usage_quota.get_period(uid)
    assert snap.used == 0


def test_me_quota_endpoint(client):
    headers, uid = _bearer(client, "qt-EP")
    _grant_pro(client, headers, original_id="TXN-Q-EP")
    r = client.get("/me/quota", headers=headers)
    body = r.json()
    assert body["plan"] == "monthly"
    assert body["total"] == 100
    assert body["used"] == 0
    assert body["remaining"] == 100


def test_me_quota_admin_unlimited(client):
    headers, uid = _bearer(client, "qt-EPA")
    user_repo.set_role(uid, "admin")
    r = client.get("/me/quota", headers=headers)
    body = r.json()
    assert body["is_unlimited"] is True
    assert body["plan"] == "admin"


def test_renewal_creates_new_period(client):
    """A second IAP verify with a different purchase_date must reset
    the quota — that's what the spec calls 'renewal resets, no carry-over'."""
    headers, uid = _bearer(client, "qt-RNW")
    _grant_pro(client, headers, original_id="TXN-Q-RN1")
    usage_quota.reserve(uid)  # used=1
    usage_quota.commit(usage_quota.reserve(uid).reservation_id)  # used=2

    # Renew: new purchase_date + new originalTransactionId.
    payload = {
        "productId": "ai_photo_coach.pro.monthly",
        "originalTransactionId": "TXN-Q-RN2",
        "transactionId": "TXN-Q-RN2",
        "purchaseDate": int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        "expiresDate": 9_999_999_999_000,
        "environment": "Sandbox",
        "bundleId": "",
        "autoRenewStatus": 1,
    }
    jws = jwt.encode(payload, "test", algorithm="HS256")
    client.post("/iap/verify", json={"jws_representation": jws}, headers=headers)

    snap = usage_quota.get_period(uid)
    # New period anchor = new purchase_date → fresh row, used=0.
    assert snap.used == 0
    assert snap.total == 100
