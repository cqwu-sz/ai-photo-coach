"""PR11 — admin IP allowlist & web-route disable in prod."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as appmain
from app.services import user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(appmain.app)


def _admin(client, device_id: str) -> dict:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    user_repo.set_role(body["user_id"], "admin")
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_admin_ip_allowlist_denies_unknown(monkeypatch, client):
    """When the allowlist is set, requests not matching it return 403."""
    monkeypatch.setattr(appmain, "_ADMIN_ALLOWLIST", ["10.20.30.0/24"])
    headers = _admin(client, "ip-A")
    r = client.get("/admin/audit/summary", headers=headers)
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "admin_ip_denied"


def test_admin_ip_allowlist_empty_lets_everything_through(monkeypatch, client):
    monkeypatch.setattr(appmain, "_ADMIN_ALLOWLIST", [])
    headers = _admin(client, "ip-C")
    r = client.get("/admin/audit/summary", headers=headers)
    assert r.status_code == 200
