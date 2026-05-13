"""v17c — anti-DDoS, blocklist, OTP tightening, rollout %, App Attest gate."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import (blocklist, endpoint_config, otp,
                            rate_buckets, user_repo)


@pytest.fixture()
def client() -> TestClient:
    rate_buckets.reset_for_tests()
    blocklist.reset_for_tests()
    return TestClient(app)


# ---------------------------------------------------------------------------
# OTP — tightened lock duration (3h) and lock count (3)
# ---------------------------------------------------------------------------


def test_otp_locks_after_three_wrong_codes(client: TestClient):
    """v17c — 3 (was 5) wrong codes → 3h lock."""
    target = "test3wrong@example.com"
    # 1st request issues a code we'll never guess.
    r = client.post("/auth/otp/request",
                     json={"channel": "email", "target": target},
                     headers={"X-Forwarded-For": "198.51.100.1"})
    assert r.status_code == 200, r.text
    # Submit 3 wrong guesses → 4th attempt should already be locked.
    seen_lock = False
    for _ in range(4):
        r = client.post("/auth/otp/verify",
                         json={"channel": "email", "target": target,
                               "code": "000000"})
        if r.status_code == 429:
            seen_lock = True
            break
    assert seen_lock, "expected lock after ≤3 wrong codes"


# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------


def test_blocklist_blocks_otp_target(client: TestClient):
    blocklist.add("email", "blocked@example.com", reason="abuse",
                   created_by="admin-test")
    r = client.post("/auth/otp/request",
                     json={"channel": "email", "target": "blocked@example.com"},
                     headers={"X-Forwarded-For": "198.51.100.2"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "otp_target_blocked"


def test_blocklist_blocks_ip_globally(client: TestClient):
    blocklist.add("ip", "203.0.113.99", reason="abuse",
                   created_by="admin-test")
    r = client.post("/auth/otp/request",
                     json={"channel": "email", "target": "ok@example.com"},
                     headers={"X-Forwarded-For": "203.0.113.99"})
    assert r.status_code == 403
    body = r.json()
    # Middleware returns {"error":...}; HTTPException wraps in {"detail":{...}}.
    err = body.get("error") or body.get("detail", {}).get("error", {})
    assert err.get("code") in ("ip_blocked", "otp_ip_blocked")


# ---------------------------------------------------------------------------
# Endpoint rollout_percentage
# ---------------------------------------------------------------------------


def test_endpoint_rollout_requires_fallback():
    endpoint_config.reset_cache_for_tests()
    with pytest.raises(ValueError):
        endpoint_config.save(primary_url="https://new.example.com",
                              rollout_percentage=50)  # no fallback → bad


def test_endpoint_rollout_accepts_partial():
    endpoint_config.reset_cache_for_tests()
    cfg = endpoint_config.save(primary_url="https://new.example.com",
                                 fallback_url="https://old.example.com",
                                 rollout_percentage=10)
    assert cfg.rollout_percentage == 10
    assert cfg.fallback_url == "https://old.example.com"


def test_endpoint_public_response_includes_rollout(client: TestClient):
    endpoint_config.reset_cache_for_tests()
    endpoint_config.save(primary_url="https://api.example.com",
                          fallback_url="https://api-old.example.com",
                          rollout_percentage=33)
    r = client.get("/api/config/endpoint")
    assert r.status_code == 200
    body = r.json()
    assert body["rollout_percentage"] == 33
    assert "Cache-Control" in r.headers


# ---------------------------------------------------------------------------
# Global IP rate-limit middleware
# ---------------------------------------------------------------------------


def test_global_ip_rpm_blocks_above_threshold(client: TestClient):
    # 121 hits in <60s should trip the 120 RPM cap on a non-exempt path.
    headers = {"X-Forwarded-For": "203.0.113.50"}
    last = None
    saw_429 = False
    for _ in range(125):
        r = client.get("/api/me", headers=headers)
        last = r
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, f"expected 429 at some point, last={last.status_code if last else 'n/a'}"
    assert last.json()["error"]["code"] in ("rate_limited", "auth_rate_limited")


def test_healthz_exempt_from_rate_limit(client: TestClient):
    headers = {"X-Forwarded-For": "203.0.113.51"}
    for _ in range(200):
        r = client.get("/healthz", headers=headers)
        assert r.status_code == 200
