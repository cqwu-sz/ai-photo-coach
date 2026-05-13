"""PR8 — central model config + /admin/model + admin guards."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import model_config, user_repo


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


def test_get_model_seeds_from_settings(client):
    headers = _admin(client, "mc-A")
    r = client.get("/admin/model", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_model_id"]
    assert body["high_model_id"]


def test_user_cannot_read_model(client):
    headers, _ = _bearer(client, "mc-USER")
    r = client.get("/admin/model", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "admin_required"


def test_put_model_persists_and_busts_cache(client):
    headers = _admin(client, "mc-PUT")
    # Warm cache.
    _ = client.get("/admin/model", headers=headers).json()
    r = client.put("/admin/model", headers=headers,
                   json={"fast_model_id": "new-fast",
                         "high_model_id": "new-high",
                         "reason": "smoke"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_model_id"] == "new-fast"
    # Cache MUST be invalidated — read again returns the new value.
    r2 = client.get("/admin/model", headers=headers)
    assert r2.json()["high_model_id"] == "new-high"
    # And history records the change.
    hist = client.get("/admin/model/history", headers=headers).json()
    assert any(h["fast_model_id"] == "new-fast" for h in hist["items"])


def test_grant_pro_and_set_role(client):
    headers_admin = _admin(client, "mc-ADM2")
    _, target_uid = _bearer(client, "mc-TARG")

    r = client.post(f"/admin/users/{target_uid}/grant_pro",
                    headers=headers_admin, json={"reason": "vip"})
    assert r.status_code == 200
    assert user_repo.get_user(target_uid).tier == "pro"

    r = client.put(f"/admin/users/{target_uid}/role",
                   headers=headers_admin,
                   json={"role": "admin", "reason": "promote"})
    assert r.status_code == 200
    assert user_repo.get_user(target_uid).role == "admin"

    # Audit rows actually written.
    import sqlite3
    con = sqlite3.connect(str(user_repo.DB_PATH))
    rows = con.execute(
        "SELECT action, target FROM admin_audit_log ORDER BY id DESC",
    ).fetchall()
    actions = [r[0] for r in rows]
    assert "user.grant_pro" in actions
    assert "user.set_role" in actions
