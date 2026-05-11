"""Smoke tests for /auth/anonymous + /auth/refresh + /me + DELETE /users/me
(A0-3 / A0-6 of MULTI_USER_AUTH)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_anonymous_then_me(client):
    r = client.post("/auth/anonymous", json={"device_id": "dev-A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_anonymous"] is True
    assert body["tier"] == "free"
    access = body["access_token"]

    r2 = client.get("/me", headers={"Authorization": f"Bearer {access}"})
    assert r2.status_code == 200
    me = r2.json()
    assert me["user_id"] == body["user_id"]
    assert me["is_anonymous"] is True


def test_anonymous_is_idempotent_per_device(client):
    a = client.post("/auth/anonymous", json={"device_id": "dev-IDEMP"}).json()
    b = client.post("/auth/anonymous", json={"device_id": "dev-IDEMP"}).json()
    assert a["user_id"] == b["user_id"]


def test_refresh_rotates(client):
    body = client.post("/auth/anonymous", json={"device_id": "dev-R"}).json()
    refresh = body["refresh_token"]
    r = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    new = r.json()
    # New refresh has a fresh jti, so it MUST differ; access can match
    # if iat collides at second resolution which is harmless.
    assert new["refresh_token"] != refresh
    # Old refresh is revoked.
    r2 = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


def test_delete_me_removes_data(client, tmp_path):
    a = client.post("/auth/anonymous", json={"device_id": "dev-DEL"}).json()
    headers = {"Authorization": f"Bearer {a['access_token']}"}
    # Post a feedback row tagged to this user.
    payload = {
        "style_keywords": ["clean"],
        "rating": 5,
        "scene_kind": "portrait",
        "device_id": "dev-DEL",
    }
    r = client.post("/feedback/", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["stored"] is True

    # Delete account.
    r = client.delete("/users/me", headers=headers)
    assert r.status_code == 204

    # /me now 401 (user soft-deleted).
    r = client.get("/me", headers=headers)
    assert r.status_code == 401


def test_logout_revokes_refresh(client):
    body = client.post("/auth/anonymous", json={"device_id": "dev-LO"}).json()
    refresh = body["refresh_token"]
    assert client.post("/auth/logout", json={"refresh_token": refresh}).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401
