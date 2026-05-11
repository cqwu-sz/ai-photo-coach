"""A0-5 — confirm user A can't read user B's recon3d job."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client: TestClient, device_id: str) -> dict:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_recon3d_job_is_owner_scoped(client):
    a_headers = _bearer(client, "iso-A")
    b_headers = _bearer(client, "iso-B")
    # Submit a job as A (1 tiny image is fine — pycolmap will stub).
    img = base64.b64encode(b"\xff\xd8\xff\xd9" * 32).decode("ascii")
    r = client.post("/recon3d/start", json={"images_b64": [img]}, headers=a_headers)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # A can read it.
    assert client.get(f"/recon3d/{job_id}", headers=a_headers).status_code == 200
    # B sees 404.
    assert client.get(f"/recon3d/{job_id}", headers=b_headers).status_code == 404


def test_feedback_delete_only_own_rows(client):
    a_headers = _bearer(client, "iso-fb-A")
    b_headers = _bearer(client, "iso-fb-B")
    # Both write a row with device_id='shared'.
    payload = {"style_keywords": [], "device_id": "shared", "scene_kind": "portrait"}
    assert client.post("/feedback/", json=payload, headers=a_headers).status_code == 200
    assert client.post("/feedback/", json=payload, headers=b_headers).status_code == 200
    # B asks to delete by device_id 'shared' — should only kill their own row.
    r = client.delete("/feedback/by_device", params={"device_id": "shared"},
                       headers=b_headers)
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
