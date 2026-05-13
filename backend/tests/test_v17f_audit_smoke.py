"""v17f — audit visibility expansion: login mirror, IAP mirror,
data export audit, soft delete audit, rollback flag, summary
endpoints."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (admin_audit, blocklist, endpoint_config,
                            rate_buckets, runtime_settings, user_repo)


@pytest.fixture()
def client() -> TestClient:
    rate_buckets.reset_for_tests()
    blocklist.reset_for_tests()
    runtime_settings.reset_for_tests()
    endpoint_config.reset_cache_for_tests()
    return TestClient(app)


def _login_otp(client: TestClient, email: str) -> str:
    """Helper: request + verify OTP and return access_token."""
    from app.services import otp as otp_svc
    client.post("/auth/otp/request",
                  json={"channel": "email", "target": email},
                  headers={"X-Forwarded-For": "203.0.113.55"})
    # mock provider stores last issued code in-memory; pull via repo.
    with user_repo._connect() as con:
        row = con.execute(
            "SELECT code_hash FROM otp_codes WHERE target = ? "
            "ORDER BY id DESC LIMIT 1", (email.lower(),)
        ).fetchone()
    assert row is not None, "OTP code wasn't created"
    # Brute-force find the code (mock provider issues 6-digit numbers).
    # Easier: hit the test seam — otp_svc exposes hash_code.
    from app.services.otp import hash_code
    for n in range(1_000_000):
        c = f"{n:06d}"
        if hash_code(c) == row[0]:
            code = c
            break
    else:
        raise AssertionError("could not recover OTP code in test")
    r = client.post("/auth/otp/verify",
                      json={"channel": "email", "target": email,
                            "code": code},
                      headers={"X-Forwarded-For": "203.0.113.55",
                                "X-Device-Id": "test-device-1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _audit_rows(action: str | None = None) -> list:
    where = "" if action is None else "WHERE action = ?"
    args = () if action is None else (action,)
    with user_repo._connect() as con:
        return con.execute(
            f"SELECT admin_id, action, target, payload, occurred_at "
            f"FROM admin_audit_log {where} ORDER BY id DESC", args
        ).fetchall()


# ---------------------------------------------------------------------------
# Login + soft delete + data export audit
# ---------------------------------------------------------------------------


def test_login_writes_audit_with_ip(client: TestClient):
    _login_otp(client, "auditme@example.com")
    rows = _audit_rows("auth.login_success")
    assert rows, "auth.login_success not written"
    actor, _, target, payload, _ = rows[0]
    assert actor.startswith("user:")
    assert "203.0.113.55" in (payload or "")
    assert "email" in (payload or "")


def test_data_export_writes_audit(client: TestClient):
    token = _login_otp(client, "exporter@example.com")
    r = client.get("/me/data/export",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    rows = _audit_rows("user.data_export")
    assert rows, "user.data_export not written"
    assert "bytes" in (rows[0][3] or "")


def test_soft_delete_writes_audit(client: TestClient):
    token = _login_otp(client, "leaving@example.com")
    r = client.delete("/users/me",
                        headers={"Authorization": f"Bearer {token}",
                                  "X-Forwarded-For": "203.0.113.99"})
    assert r.status_code == 204
    rows = _audit_rows("user.soft_delete")
    assert rows, "user.soft_delete not written"


# ---------------------------------------------------------------------------
# Endpoint rollback detection
# ---------------------------------------------------------------------------


def test_endpoint_save_marks_rollback(client: TestClient):
    """If admin sets primary back to the previous fallback URL, the
    audit payload should include is_rollback=True."""
    # Initial state — A primary, B fallback.
    endpoint_config.save(primary_url="https://a.example.com",
                          fallback_url="https://b.example.com",
                          rollout_percentage=100)
    # Now simulate rollback: new primary = old fallback.
    endpoint_config.save(primary_url="https://b.example.com",
                          fallback_url="https://a.example.com",
                          rollout_percentage=100,
                          updated_by="admin-test",
                          reason="rollback for testing")
    # We didn't go through the API path, so audit isn't written here.
    # Call the API path with a stub admin to exercise put_endpoint:
    from app.api.admin import EndpointAdminIn, put_endpoint
    from starlette.requests import Request

    class _Stub:
        id = "admin-test"; role = "admin"

    scope = {"type": "http", "headers": [], "client": None}
    req = Request(scope)
    payload = EndpointAdminIn(primary_url="https://a.example.com",
                                fallback_url="https://b.example.com",
                                rollout_percentage=100,
                                reason="going back to A")
    asyncio.run(put_endpoint(payload, req, user=_Stub()))           # type: ignore[arg-type]

    rows = _audit_rows("endpoint_config.save")
    # Latest entry — admin-test action — should have _previous filled
    # (because at the time we saved, prev was b/a/100).
    assert rows, "no endpoint_config.save audit"
    last_payload = rows[0][3] or ""
    assert "_previous" in last_payload
    # Going from b→a where prev fallback was a → is_rollback True.
    assert "\"is_rollback\": true" in last_payload


# ---------------------------------------------------------------------------
# Summary / recent_logins / anomaly_summary endpoints
# ---------------------------------------------------------------------------


def test_audit_summary_groups_by_action(client: TestClient):
    admin_audit.write("user:u1", "auth.login_success", target="u1",
                       payload={"channel": "email"})
    admin_audit.write("user:u2", "auth.login_success", target="u2",
                       payload={"channel": "phone"})
    admin_audit.write("system", "iap.asn.refund", target="u1",
                       payload={"product_id": "monthly"})

    from app.api.admin import audit_summary

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(audit_summary(since_hours=24, user=_Stub()))  # type: ignore[arg-type]
    by_action = {i["action"]: i["count"] for i in res["items"]}
    assert by_action.get("auth.login_success", 0) >= 2
    assert by_action.get("iap.asn.refund", 0) >= 1


def test_anomaly_summary_counts_high_value_events(client: TestClient):
    admin_audit.write("system", "iap.asn.refund", target="u1",
                       payload={"product_id": "monthly"})
    admin_audit.write("system", "otp.permanent_lock", target="phone:1",
                       payload={"fails": 12})
    admin_audit.write("admin1", "endpoint_config.save",
                       target="https://a", payload={"is_rollback": True,
                                                      "_previous": {"primary_url": "https://b"}})

    from app.api.admin import anomaly_summary

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(anomaly_summary(hours=24, user=_Stub()))      # type: ignore[arg-type]
    assert res["counts"].get("iap.asn.refund", 0) >= 1
    assert res["counts"].get("otp.permanent_lock", 0) >= 1
    assert res["counts"].get("endpoint.rollback", 0) >= 1
    assert res["recent_rollbacks"]


def test_audit_log_filters_by_action_prefix(client: TestClient):
    admin_audit.write("system", "iap.asn.did_renew", target="u1")
    admin_audit.write("system", "iap.asn.refund", target="u1")
    admin_audit.write("user:u1", "auth.login_success", target="u1")

    from app.api.admin import audit_log

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(audit_log(action="iap.asn.*", user=_Stub()))  # type: ignore[arg-type,call-arg]
    actions = {i["action"] for i in res["items"]}
    # Should include both iap.asn.* but NOT auth.login_success.
    assert all(a.startswith("iap.asn.") for a in actions)
    assert "iap.asn.refund" in actions
    assert "iap.asn.did_renew" in actions
