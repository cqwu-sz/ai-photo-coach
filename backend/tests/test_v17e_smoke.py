"""v17e — gc_expired, audit on permanent OTP lock, distribution series."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (blocklist, endpoint_config,
                            rate_buckets, runtime_settings, user_repo)


@pytest.fixture()
def client() -> TestClient:
    rate_buckets.reset_for_tests()
    blocklist.reset_for_tests()
    runtime_settings.reset_for_tests()
    endpoint_config.reset_cache_for_tests()
    return TestClient(app)


def test_blocklist_gc_drops_old_expired(client: TestClient):
    """Add an entry that expired 60 days ago — gc_expired should
    delete it (grace=30d)."""
    long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO blocklist (scope, value, reason, created_by, "
            "created_at, expires_at, dry_run) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            ("ip", "203.0.113.99", "old", "test",
             (datetime.now(timezone.utc) - timedelta(days=120)).isoformat(),
             long_ago),
        )
        con.commit()
    n = blocklist.gc_expired(grace_days=30)
    assert n >= 1
    assert blocklist.is_blocked("ip", "203.0.113.99") is None


def test_blocklist_gc_keeps_recent_expired(client: TestClient):
    """Entry expired 5 days ago, grace=30d → should KEEP for audit."""
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO blocklist (scope, value, reason, created_by, "
            "created_at, expires_at, dry_run) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            ("ip", "203.0.113.50", "recent", "test",
             (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
             recent),
        )
        con.commit()
    blocklist.gc_expired(grace_days=30)
    # Entry still exists in DB even though is_blocked returns None
    # (because expires_at is past).
    with user_repo._connect() as con:
        row = con.execute(
            "SELECT 1 FROM blocklist WHERE scope=? AND value=?",
            ("ip", "203.0.113.50")).fetchone()
    assert row is not None


def test_otp_permanent_lock_writes_audit(client: TestClient):
    """After 12 _record_failure calls, an admin_audit_log row with
    action='otp.permanent_lock' should exist. We bypass the public
    verify path because the 1h-tier lock would short-circuit it
    before we reach the 12-fail permanent threshold; this directly
    exercises the escalation logic."""
    from app.services import otp as otp_svc
    target = "permalock@example.com"
    # Each call opens & closes its own connection so the nested
    # blocklist.add (separate connection) doesn't deadlock SQLite.
    for _ in range(12):
        with user_repo._connect() as con:
            otp_svc._record_failure(con, target)                    # noqa: SLF001
            con.commit()
    with user_repo._connect() as con:
        row = con.execute(
            "SELECT admin_id, action, target FROM admin_audit_log "
            "WHERE action = 'otp.permanent_lock' "
            "ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == "system"
    # target stored as redacted form (not the raw email).
    assert "@" not in (row[2] or "") or "***" in (row[2] or "")


def test_distribution_series_buckets_telemetry(client: TestClient):
    endpoint_config.save(primary_url="https://api-canon.example.com",
                          fallback_url="https://api-old.example.com",
                          rollout_percentage=80)
    now = datetime.now(timezone.utc)
    samples = [
        # bucket A — 2 devices on canonical
        (now - timedelta(minutes=50), "https://api-canon.example.com", "fp-1"),
        (now - timedelta(minutes=48), "https://api-canon.example.com", "fp-2"),
        # bucket A — 1 device on old
        (now - timedelta(minutes=49), "https://api-old.example.com", "fp-3"),
        # bucket B (more recent) — all 3 on canonical
        (now - timedelta(minutes=5), "https://api-canon.example.com", "fp-1"),
        (now - timedelta(minutes=4), "https://api-canon.example.com", "fp-2"),
        (now - timedelta(minutes=3), "https://api-canon.example.com", "fp-3"),
    ]
    with user_repo._connect() as con:
        for ts, url, fp in samples:
            con.execute(
                "INSERT INTO endpoint_telemetry "
                "(active_url, device_fp, app_version, reported_at) "
                "VALUES (?, ?, ?, ?)",
                (url, fp, "1.0.0", ts.isoformat()),
            )
        con.commit()

    # Need an admin token to call /admin/* — borrow the test seed.
    from app.services import admin_seed
    admin_seed.ensure_admins("permaadmin@example.com")
    # Just hit the function directly to skip auth machinery.
    from app.api.admin import get_endpoint_distribution_series
    import asyncio

    class _Stub:
        id = "admin-test"; role = "admin"

    res = asyncio.run(get_endpoint_distribution_series(
        user=_Stub(), hours=2, bucket_minutes=15))                  # type: ignore[arg-type]
    assert res["canonical_url"] == "https://api-canon.example.com"
    assert res["target_pct"] == 80
    assert isinstance(res["buckets"], list)
    assert len(res["buckets"]) >= 1
    last = res["buckets"][-1]
    assert last["pct"] == 100.0
    # 3 distinct fps, but bucket boundary may split them across two
    # adjacent 15min slots — accept any non-zero count, just check
    # all of them landed somewhere.
    total_seen = sum(b["total"] for b in res["buckets"][-2:])
    assert total_seen >= 3
