"""Smoke tests for PR2: anonymous gate, role-in-JWT, require_admin,
admin_seed bootstrap."""
from __future__ import annotations

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import admin_seed, auth as auth_svc, user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_anonymous_gate_returns_410_when_disabled(monkeypatch, client):
    monkeypatch.setenv("ENABLE_ANONYMOUS_AUTH", "false")
    get_settings.cache_clear()
    try:
        r = client.post("/auth/anonymous", json={"device_id": "dev-PR2-X"})
        assert r.status_code == 410
        assert r.json()["detail"]["error"]["code"] == "anonymous_disabled"
    finally:
        monkeypatch.setenv("ENABLE_ANONYMOUS_AUTH", "true")
        get_settings.cache_clear()


def test_jwt_carries_role_user_by_default(client):
    body = client.post("/auth/anonymous", json={"device_id": "dev-PR2-A"}).json()
    secret = get_settings().app_jwt_secret
    decoded = pyjwt.decode(body["access_token"], secret, algorithms=["HS256"])
    assert decoded["role"] == "user"
    assert body["role"] == "user"


def test_role_promotion_visible_after_refresh(client):
    body = client.post("/auth/anonymous", json={"device_id": "dev-PR2-B"}).json()
    user_repo.set_role(body["user_id"], "admin")
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": body["refresh_token"]},
    ).json()
    secret = get_settings().app_jwt_secret
    decoded = pyjwt.decode(refreshed["access_token"], secret, algorithms=["HS256"])
    assert decoded["role"] == "admin"
    assert refreshed["role"] == "admin"


def test_require_admin_blocks_user_allows_admin(client):
    """Mount a tiny route guarded by require_admin and exercise both sides."""
    if not getattr(app.state, "_pr2_admin_route_installed", False):
        @app.get("/__pr2_admin_ping")
        def _ping(_: auth_svc.CurrentUser = Depends(auth_svc.require_admin)) -> dict:
            return {"ok": True}
        app.state._pr2_admin_route_installed = True

    user_body = client.post("/auth/anonymous", json={"device_id": "dev-PR2-U"}).json()
    headers = {"Authorization": f"Bearer {user_body['access_token']}"}
    r = client.get("/__pr2_admin_ping", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "admin_required"

    admin_body = client.post("/auth/anonymous", json={"device_id": "dev-PR2-AD"}).json()
    user_repo.set_role(admin_body["user_id"], "admin")
    # Existing access token still has role=user; current_user always
    # re-reads the DB, so it should now be allowed without a refresh.
    r2 = client.get(
        "/__pr2_admin_ping",
        headers={"Authorization": f"Bearer {admin_body['access_token']}"},
    )
    assert r2.status_code == 200, r2.text


def test_admin_seed_creates_and_promotes():
    out = admin_seed.ensure_admins("13899990001:sms,Boss@Example.COM:email")
    assert len(out) == 2
    by_phone = user_repo.get_by_phone("13899990001")
    assert by_phone is not None and by_phone.role == "admin"
    by_email = user_repo.get_by_email("boss@example.com")
    assert by_email is not None and by_email.role == "admin"

    # Idempotent — second call returns same ids, no duplicate rows.
    again = admin_seed.ensure_admins("13899990001:sms,Boss@Example.COM:email")
    assert sorted(again) == sorted(out)


def test_admin_seed_ignores_malformed_entries(caplog):
    out = admin_seed.ensure_admins("nochannel,15000000000:wechat,:sms,  ")
    assert out == []


def test_admin_seed_promotes_existing_user():
    # Pre-create as a regular user, then seed.
    u = user_repo.create_user(phone="13900000001", role="user")
    assert u.role == "user"
    admin_seed.ensure_admins("13900000001:sms")
    assert user_repo.get_user(u.id).role == "admin"
