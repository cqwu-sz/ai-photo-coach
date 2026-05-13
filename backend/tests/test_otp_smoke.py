"""PR3 — OTP issuance/verification smoke tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import otp as otp_svc, user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def mock_provider() -> otp_svc.MockProvider:
    """Install a single mock provider for both channels and yield it."""
    mp = otp_svc.MockProvider()
    otp_svc.set_providers_for_tests(sms=mp, email=mp)
    return mp


def _send_and_get_code(mock_provider: otp_svc.MockProvider) -> str:
    assert mock_provider.sent, "no OTP delivered"
    return mock_provider.sent[-1][2]


def test_request_and_verify_sms_creates_user(client, mock_provider):
    r = client.post("/auth/otp/request",
                    json={"channel": "sms", "target": "13800000001"})
    assert r.status_code == 200, r.text
    code = _send_and_get_code(mock_provider)

    r2 = client.post("/auth/otp/verify",
                     json={"channel": "sms", "target": "13800000001",
                           "code": code})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["is_anonymous"] is False
    assert body["role"] == "user"
    assert body["tier"] == "free"
    user = user_repo.get_by_phone("13800000001")
    assert user is not None
    assert user.id == body["user_id"]


def test_request_and_verify_email_lowercases(client, mock_provider):
    r = client.post("/auth/otp/request",
                    json={"channel": "email", "target": "User@Example.COM"})
    assert r.status_code == 200, r.text
    target_in_log = mock_provider.sent[-1][1]
    assert target_in_log == "user@example.com"
    code = _send_and_get_code(mock_provider)

    r2 = client.post("/auth/otp/verify",
                     json={"channel": "email", "target": "user@example.com",
                           "code": code})
    assert r2.status_code == 200, r2.text
    assert user_repo.get_by_email("user@example.com") is not None


def test_invalid_phone_format_rejected(client, mock_provider):
    r = client.post("/auth/otp/request",
                    json={"channel": "sms", "target": "12345"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "otp_target_invalid"


def test_cooldown_blocks_immediate_resend(client, mock_provider):
    payload = {"channel": "sms", "target": "13800000002"}
    r1 = client.post("/auth/otp/request", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/auth/otp/request", json=payload)
    assert r2.status_code == 429
    assert r2.json()["detail"]["error"]["code"] == "otp_too_frequent"


def test_wrong_code_increments_attempts_and_locks(client, mock_provider):
    target = "13800000003"
    client.post("/auth/otp/request", json={"channel": "sms", "target": target})
    # 5 wrong attempts → locked. The 5th call may surface as either
    # mismatch (recorded then locked) or exhausted-code; both are acceptable.
    for _ in range(5):
        client.post("/auth/otp/verify",
                    json={"channel": "sms", "target": target, "code": "000000"})
    r = client.post("/auth/otp/verify",
                    json={"channel": "sms", "target": target, "code": "000000"})
    assert r.status_code == 429
    assert r.json()["detail"]["error"]["code"] == "otp_target_locked"


def test_correct_code_only_works_once(client, mock_provider):
    target = "13800000004"
    client.post("/auth/otp/request", json={"channel": "sms", "target": target})
    code = _send_and_get_code(mock_provider)
    r1 = client.post("/auth/otp/verify",
                     json={"channel": "sms", "target": target, "code": code})
    assert r1.status_code == 200
    r2 = client.post("/auth/otp/verify",
                     json={"channel": "sms", "target": target, "code": code})
    assert r2.status_code == 400
    assert r2.json()["detail"]["error"]["code"] == "otp_code_used"


def test_existing_phone_user_reuses_id(client, mock_provider):
    target = "13800000005"
    pre = user_repo.create_user(phone=target)
    client.post("/auth/otp/request", json={"channel": "sms", "target": target})
    code = _send_and_get_code(mock_provider)
    body = client.post(
        "/auth/otp/verify",
        json={"channel": "sms", "target": target, "code": code},
    ).json()
    assert body["user_id"] == pre.id


def test_admin_login_returns_admin_role(client, mock_provider):
    target = "13899990002"
    user_repo.create_user(phone=target, role="admin")
    client.post("/auth/otp/request", json={"channel": "sms", "target": target})
    code = _send_and_get_code(mock_provider)
    body = client.post(
        "/auth/otp/verify",
        json={"channel": "sms", "target": target, "code": code},
    ).json()
    assert body["role"] == "admin"
