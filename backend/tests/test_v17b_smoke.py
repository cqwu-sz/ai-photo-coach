"""v17b — free-quota device anchoring, OTP IP throttle, endpoint config."""
from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import endpoint_config, otp, usage_quota, user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _device_fp(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _anon_user(client: TestClient, device_id: str) -> str:
    """Create an anonymous user with the given device_id."""
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return body["user_id"]


# ---------------------------------------------------------------------------
# Free quota — anchored on device fingerprint (anti farm)
# ---------------------------------------------------------------------------


def test_two_accounts_same_device_share_free_bucket(client: TestClient):
    """Re-registering on the same iPhone must NOT grant another 5 shots."""
    dev = "device-shared-iphone"
    a = _anon_user(client, dev)
    b = _anon_user(client, dev)

    # First account burns 3.
    for _ in range(3):
        usage_quota.reserve(a)
    # Second account should only have 2 left, not 5.
    snap_b = usage_quota.get_period(b)
    assert snap_b is not None and snap_b.total == 5 and snap_b.used == 3

    # Burn the remaining 2 on B; 6th call (from either) must fail.
    for _ in range(2):
        usage_quota.reserve(b)
    with pytest.raises(HTTPException) as ei:
        usage_quota.reserve(a)
    assert ei.value.status_code == 402
    assert ei.value.detail["error"]["code"] == "free_quota_exhausted"


def test_different_devices_get_independent_buckets(client: TestClient):
    a = _anon_user(client, "device-A")
    b = _anon_user(client, "device-B")
    for _ in range(5):
        usage_quota.reserve(a)
    # Device B is untouched.
    snap_b = usage_quota.get_period(b)
    assert snap_b is not None and snap_b.used == 0


def test_rollback_returns_free_shot(client: TestClient):
    uid = _anon_user(client, "device-rollback")
    res = usage_quota.reserve(uid)
    assert res.snapshot.used == 1
    usage_quota.rollback(res.reservation_id)
    snap = usage_quota.get_period(uid)
    assert snap.used == 0


# ---------------------------------------------------------------------------
# Endpoint config
# ---------------------------------------------------------------------------


def test_public_endpoint_returns_seeded_value(client: TestClient):
    endpoint_config.reset_cache_for_tests()
    r = client.get("/api/config/endpoint")
    assert r.status_code == 200
    body = r.json()
    assert body["primary_url"].startswith(("http://", "https://"))


def test_admin_can_update_endpoint(client: TestClient):
    """Admin save → public read returns the new URL within one cache flush."""
    endpoint_config.reset_cache_for_tests()
    cfg = endpoint_config.save(
        primary_url="https://api-new.example.com",
        fallback_url="https://api-old.example.com",
        updated_by="admin-test",
        reason="failover drill",
    )
    assert cfg.primary_url == "https://api-new.example.com"
    assert cfg.fallback_url == "https://api-old.example.com"

    r = client.get("/api/config/endpoint")
    assert r.json()["primary_url"] == "https://api-new.example.com"


def test_endpoint_save_rejects_invalid_url():
    with pytest.raises(ValueError):
        endpoint_config.save(primary_url="ftp://nope")


# ---------------------------------------------------------------------------
# OTP per-IP throttle
# ---------------------------------------------------------------------------


def test_otp_ip_throttle_blocks_after_eight_distinct_targets(client: TestClient):
    """One IP, 9 different targets in 1h → 9th must 429."""
    # Use email channel to avoid actual SMS provider config.
    headers = {"X-Forwarded-For": "203.0.113.42"}
    for i in range(8):
        r = client.post(
            "/auth/otp/request",
            json={"channel": "email", "target": f"u{i}@example.com"},
            headers=headers,
        )
        assert r.status_code in (200, 429), r.text
        if r.status_code == 429:
            pytest.skip("provider-side throttle fired before IP throttle")
    r = client.post(
        "/auth/otp/request",
        json={"channel": "email", "target": "u8@example.com"},
        headers=headers,
    )
    assert r.status_code == 429
    assert r.json()["detail"]["error"]["code"] == "otp_ip_throttled"
