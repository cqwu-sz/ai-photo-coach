"""PR10 — /me/data/export user data download."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import usage_records


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client, device_id: str) -> tuple[dict, str]:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user_id"]


def test_export_returns_disclosure_and_records(client):
    headers, uid = _bearer(client, "exp-A")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-exp",
        step_config={"scene_mode": "portrait"},
    )
    usage_records.mark_charged(rid, proposals=[{"id": "p1"}],
                                  prompt_tokens=10, completion_tokens=20)
    r = client.get("/me/data/export", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    body = json.loads(r.content)
    assert body["user_id"] == uid
    assert "what_we_store_for_you" in body["disclosure"]
    assert "what_we_do_not_store" in body["disclosure"]
    assert any(rec["request_id"] == "req-exp" for rec in body["usage_records"])
    assert "OTP" not in json.dumps(body["users"])  # No raw secrets leaked


def test_other_users_records_not_included(client):
    headers_a, uid_a = _bearer(client, "exp-A2")
    _, uid_b = _bearer(client, "exp-B2")
    usage_records.create_pending(
        user_id=uid_b, request_id="req-other",
        step_config={"scene_mode": "portrait"},
    )
    body = json.loads(client.get("/me/data/export", headers=headers_a).content)
    for rec in body["usage_records"]:
        assert rec["request_id"] != "req-other"
