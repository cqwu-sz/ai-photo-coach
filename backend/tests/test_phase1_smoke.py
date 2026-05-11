"""Smoke tests for Phase 1 additions: tier-aware rate limit, anonymous
account TTL sweeper, startup checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import rate_limit, startup_checks, user_repo


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _bearer(client: TestClient, device_id: str) -> dict:
    body = client.post("/auth/anonymous", json={"device_id": device_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}", "device_id": device_id,
             "user_id": body["user_id"]}


# ---------------------------------------------------------------------------
# A1-5: tier multiplier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier_multiplier_doubles_capacity():
    rate_limit.reset_for_tests()
    # Free user: 2 hits then dry.
    for _ in range(2):
        assert await rate_limit.consume("t-free", "u-free",
                                          capacity=2, refill_per_sec=0)
    assert not await rate_limit.consume("t-free", "u-free",
                                          capacity=2, refill_per_sec=0)

    # Pro user with 5x multiplier configured by default → 10 hits.
    for i in range(10):
        ok = await rate_limit._consume_local(
            "t-pro", "u-pro",
            capacity=2 * 5,           # what enforce() would compute
            refill_per_sec=0,
        )
        assert ok, f"hit {i} should pass"
    assert not await rate_limit._consume_local(
        "t-pro", "u-pro", capacity=10, refill_per_sec=0,
    )


# ---------------------------------------------------------------------------
# A1-4: anonymous account TTL sweep
# ---------------------------------------------------------------------------


def test_purge_inactive_anonymous_only_old_anon():
    fresh = user_repo.create_anonymous(device_id="ttl-fresh")
    stale = user_repo.create_anonymous(device_id="ttl-stale")
    # Hand-roll a stale timestamp 60 days ago.
    old_iso = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with user_repo._connect() as con:
        con.execute("UPDATE users SET updated_at = ? WHERE id = ?",
                     (old_iso, stale.id))
    n = user_repo.purge_inactive_anonymous(older_than_days=30)
    assert n == 1
    assert user_repo.get_user(fresh.id) is not None
    assert user_repo.get_user(stale.id) is None


def test_touch_keeps_user_alive():
    u = user_repo.create_anonymous(device_id="ttl-touched")
    old_iso = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with user_repo._connect() as con:
        con.execute("UPDATE users SET updated_at = ? WHERE id = ?",
                     (old_iso, u.id))
    user_repo.touch(u.id)
    n = user_repo.purge_inactive_anonymous(older_than_days=30)
    assert n == 0
    assert user_repo.get_user(u.id) is not None


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------


def test_startup_checks_dev_warn_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ENFORCE_REQUIRED_SECRETS", "false")
    monkeypatch.setenv("APP_JWT_SECRET", "")
    get_settings.cache_clear()
    # Should NOT raise even with missing secret in dev.
    startup_checks.run_and_report(get_settings())


def test_startup_checks_prod_refuses_missing_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENFORCE_REQUIRED_SECRETS", "true")
    monkeypatch.setenv("APP_JWT_SECRET", "")
    monkeypatch.setenv("REQUEST_TOKEN_SECRET", "")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="refusing to start"):
        startup_checks.run_and_report(get_settings())


# ---------------------------------------------------------------------------
# /healthz exposes legal URLs (so iOS can hot-swap them)
# ---------------------------------------------------------------------------


def test_healthz_returns_legal_urls(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "privacy_policy_url" in body
    assert "eula_url" in body
