"""PR9 — admin audit summary / series / users / log endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import usage_records, user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client, device_id: str) -> tuple[dict, str]:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user_id"]


def _admin(client, device_id: str) -> dict:
    headers, uid = _bearer(client, device_id)
    user_repo.set_role(uid, "admin")
    return headers


def _grant_yearly(client, headers, original_id: str) -> None:
    payload = {
        "productId": "ai_photo_coach.pro.yearly",
        "originalTransactionId": original_id,
        "transactionId": original_id,
        "purchaseDate": int(datetime.now(timezone.utc).timestamp() * 1000),
        "expiresDate": 9_999_999_999_000,
        "environment": "Sandbox",
        "bundleId": "",
        "autoRenewStatus": 1,
    }
    jws = jwt.encode(payload, "test", algorithm="HS256")
    client.post("/iap/verify", json={"jws_representation": jws}, headers=headers)


def test_summary_aggregates_subs_and_records(client):
    admin_headers = _admin(client, "au-A")
    headers_user, uid = _bearer(client, "au-USR")
    _grant_yearly(client, headers_user, "TXN-AU-1")

    rid = usage_records.create_pending(
        user_id=uid, request_id="req-AU-1",
        step_config={"scene_mode": "portrait"},
    )
    usage_records.mark_charged(rid, proposals=[{"id": "p1"}],
                                  model_id="gemini-2.5-flash",
                                  prompt_tokens=300, completion_tokens=400,
                                  cost_usd=0.0012)
    usage_records.mark_failed(
        usage_records.create_pending(
            user_id=uid, request_id="req-AU-2",
            step_config={"scene_mode": "scenery"},
        ),
        error_code="boom",
    )

    r = client.get("/admin/audit/summary?since="
                   + (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                   headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_subscriptions"] == 1
    assert body["new_subscriptions_by_plan"]["yearly"] == 1
    assert body["revenue_cny_gross"] == 412.0
    assert body["analyze_total"] == 2
    assert body["analyze_charged"] == 1
    assert body["analyze_failed"] == 1
    assert body["prompt_tokens"] == 300
    assert body["completion_tokens"] == 400
    assert body["active_users"] == 1


def test_series_buckets_by_hour(client):
    admin_headers = _admin(client, "au-S")
    _, uid = _bearer(client, "au-S-USR")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-AU-S",
        step_config={"scene_mode": "portrait"},
    )
    usage_records.mark_charged(rid, proposals=[],
                                  prompt_tokens=10, completion_tokens=20)
    r = client.get("/admin/audit/series?bucket=hour", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bucket"] == "hour"
    assert any(p["analyze_charged"] >= 1 for p in body["points"])


def test_users_endpoint_returns_top_spenders(client):
    admin_headers = _admin(client, "au-U")
    _, uid = _bearer(client, "au-U-USR")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-AU-U",
        step_config={"scene_mode": "portrait"},
    )
    usage_records.mark_charged(rid, proposals=[],
                                  cost_usd=0.5,
                                  prompt_tokens=100, completion_tokens=100)
    r = client.get("/admin/audit/users?limit=5", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert any(item["user_id"] == uid and item["cost_usd"] >= 0.5
                for item in body["items"])


def test_audit_log_returns_recent_admin_actions(client):
    admin_headers = _admin(client, "au-L")
    _, target = _bearer(client, "au-L-USR")
    client.post(f"/admin/users/{target}/grant_pro",
                headers=admin_headers, json={"reason": "test"})
    r = client.get("/admin/audit/log?limit=10", headers=admin_headers)
    body = r.json()
    actions = [item["action"] for item in body["items"]]
    assert "user.grant_pro" in actions


def test_user_blocked_from_audit(client):
    headers, _ = _bearer(client, "au-PUB")
    r = client.get("/admin/audit/summary", headers=headers)
    assert r.status_code == 403
