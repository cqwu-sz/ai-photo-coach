"""PR6 — usage_records lifecycle + /me/usage endpoints."""
from __future__ import annotations

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


def test_create_pending_then_charged(client):
    _, uid = _bearer(client, "ur-A")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-1",
        step_config={"scene_mode": "portrait", "person_count": 1},
    )
    rec = usage_records.get_for_user(uid, rid)
    assert rec.status == "pending"
    assert rec.step_config["scene_mode"] == "portrait"

    usage_records.mark_charged(rid, proposals=[{"id": "s0", "summary": "shot"}],
                                  model_id="gemini-2.5-flash",
                                  prompt_tokens=120, completion_tokens=240,
                                  cost_usd=0.0007)
    rec2 = usage_records.get_for_user(uid, rid)
    assert rec2.status == "charged"
    assert rec2.proposals[0]["id"] == "s0"
    assert rec2.prompt_tokens == 120


def test_mark_failed_keeps_record(client):
    _, uid = _bearer(client, "ur-B")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-2",
        step_config={"scene_mode": "scenery"},
    )
    usage_records.mark_failed(rid, error_code="upstream_timeout")
    rec = usage_records.get_for_user(uid, rid)
    assert rec.status == "failed"
    assert rec.error_code == "upstream_timeout"


def test_list_endpoint_returns_recent_first(client):
    headers, uid = _bearer(client, "ur-C")
    for i in range(3):
        usage_records.create_pending(
            user_id=uid, request_id=f"req-{i}",
            step_config={"scene_mode": "portrait", "person_count": i + 1},
        )
    r = client.get("/me/usage", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 3
    # Most recent first (request_id req-2 has the highest person_count).
    assert items[0]["person_count"] == 3
    assert items[2]["person_count"] == 1


def test_list_pagination_cursor(client):
    headers, uid = _bearer(client, "ur-D")
    for i in range(5):
        usage_records.create_pending(
            user_id=uid, request_id=f"req-D-{i}",
            step_config={"scene_mode": "portrait"},
        )
    page = client.get("/me/usage?limit=2", headers=headers).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"] is not None
    nxt = client.get(f"/me/usage?limit=2&before={page['next_cursor']}",
                      headers=headers).json()
    assert len(nxt["items"]) == 2


def test_get_detail_returns_step_config(client):
    headers, uid = _bearer(client, "ur-E")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-detail",
        step_config={"scene_mode": "portrait", "person_count": 2,
                     "style_keywords": ["clean"]},
    )
    usage_records.mark_charged(rid, proposals=[{"id": "p1"}])
    r = client.get(f"/me/usage/{rid}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step_config"]["style_keywords"] == ["clean"]
    assert body["proposals"][0]["id"] == "p1"


def test_pick_and_captured_endpoints(client):
    headers, uid = _bearer(client, "ur-F")
    rid = usage_records.create_pending(
        user_id=uid, request_id="req-F",
        step_config={"scene_mode": "portrait"},
    )
    usage_records.mark_charged(rid, proposals=[{"id": "p1"}, {"id": "p2"}])

    r = client.patch(f"/me/usage/{rid}/pick",
                     json={"proposal_id": "p2"}, headers=headers)
    assert r.status_code == 200

    r = client.patch(f"/me/usage/{rid}/captured", headers=headers)
    assert r.status_code == 200

    detail = client.get(f"/me/usage/{rid}", headers=headers).json()
    assert detail["picked_proposal_id"] == "p2"
    assert detail["captured"] is True


def test_other_user_cannot_read(client):
    _, owner = _bearer(client, "ur-OWN")
    headers_other, _ = _bearer(client, "ur-OTH")
    rid = usage_records.create_pending(
        user_id=owner, request_id="req-private",
        step_config={"scene_mode": "portrait"},
    )
    r = client.get(f"/me/usage/{rid}", headers=headers_other)
    assert r.status_code == 404
