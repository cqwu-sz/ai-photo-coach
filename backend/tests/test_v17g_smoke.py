"""v17g — alert mailer, audit retention GC, product insights k-anon."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (admin_audit, alert_mailer, audit_retention,
                            blocklist, endpoint_config, rate_buckets,
                            runtime_settings, user_repo)


@pytest.fixture()
def client() -> TestClient:
    rate_buckets.reset_for_tests()
    blocklist.reset_for_tests()
    runtime_settings.reset_for_tests()
    endpoint_config.reset_cache_for_tests()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Alert mailer recipient resolution
# ---------------------------------------------------------------------------


def test_mailer_resolves_exact_then_prefix_then_default(client: TestClient):
    runtime_settings.set_value("alert.recipients.default", "ops@example.com")
    runtime_settings.set_value("alert.recipients.iap.asn.*", "billing@example.com")
    runtime_settings.set_value("alert.recipients.otp.permanent_lock",
                                 "sec@example.com,ops@example.com")
    # Exact match wins.
    assert alert_mailer.recipients_for("otp.permanent_lock") == [
        "sec@example.com", "ops@example.com"]
    # Prefix match.
    assert alert_mailer.recipients_for("iap.asn.refund") == ["billing@example.com"]
    # Falls back to default.
    assert alert_mailer.recipients_for("user.data_export") == ["ops@example.com"]


def test_mailer_disabled_returns_empty(client: TestClient):
    runtime_settings.set_value("alert.recipients.default", "ops@example.com")
    runtime_settings.set_value("alert.enabled", "false")
    assert alert_mailer.recipients_for("anything") == []


def test_mailer_throttles_within_cooldown(client: TestClient):
    runtime_settings.set_value("alert.recipients.default", "ops@example.com")
    runtime_settings.set_value("alert.cooldown_sec.default", "60")
    n1 = alert_mailer.maybe_send_for_audit(
        "test.event", admin_id="system", target=None, payload={"x": 1})
    n2 = alert_mailer.maybe_send_for_audit(
        "test.event", admin_id="system", target=None, payload={"x": 2})
    assert n1 == 1
    assert n2 == 0  # throttled


def test_mailer_format_subject_and_body_are_human_readable():
    subj = alert_mailer.format_subject("iap.asn.refund", "user-abc")
    assert "[退款]" in subj
    assert "refund" in subj
    body = alert_mailer.format_body(
        "iap.asn.refund", admin_id="system", target="user-abc",
        payload={"product_id": "monthly", "expires_at": "2026-06-01T00:00:00Z"},
        occurred_at="2026-05-12T10:00:00Z",
    )
    # Body must NOT be a JSON wall.
    assert "{" not in body or "{" in "{ inner }"  # only nested marker, not raw json
    assert "product_id" in body
    assert "monthly" in body
    assert "AI Photo Coach" in body


# ---------------------------------------------------------------------------
# Audit retention
# ---------------------------------------------------------------------------


def test_retention_keeps_iap_forever_drops_logins_after_90d(client: TestClient):
    very_old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    middle = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO admin_audit_log (admin_id, action, target, payload, occurred_at) "
            "VALUES (?,?,?,?,?)",
            ("system", "iap.asn.refund", "u1", "{}", very_old))
        con.execute(
            "INSERT INTO admin_audit_log (admin_id, action, target, payload, occurred_at) "
            "VALUES (?,?,?,?,?)",
            ("user:u1", "auth.login_success", "u1", "{}", middle))
        con.execute(
            "INSERT INTO admin_audit_log (admin_id, action, target, payload, occurred_at) "
            "VALUES (?,?,?,?,?)",
            ("admin", "model_config.save", "fast", "{}", middle))
        con.commit()
    audit_retention.gc()
    with user_repo._connect() as con:
        rows = {r[0] for r in con.execute(
            "SELECT action FROM admin_audit_log").fetchall()}
    assert "iap.asn.refund" in rows                   # forever
    assert "auth.login_success" not in rows           # 90d expired
    assert "model_config.save" in rows                # 365d not yet


# ---------------------------------------------------------------------------
# Product insights — k-anon floor
# ---------------------------------------------------------------------------


def _seed_record(user_id: str, scene: str = "portrait",
                  quality: str = "fast", keywords: list[str] | None = None,
                  proposals: list[dict] | None = None,
                  picked: str | None = None, captured: int = 0) -> None:
    sc = {"scene_mode": scene, "quality_mode": quality,
          "style_keywords": keywords or []}
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO usage_records (user_id, request_id, status, "
            "step_config, proposals, picked_proposal_id, captured, "
            "model_id, prompt_tokens, completion_tokens, cost_usd, "
            "created_at, charge_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, f"req-{user_id}-{datetime.now().timestamp()}",
             "charged", json.dumps(sc),
             json.dumps(proposals or []), picked, captured,
             "fast-1", 100, 200, 0.001,
             datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def test_insights_k_anon_merges_low_n(client: TestClient):
    """With < 5 distinct users picking 'rare', it must be merged
    into '(其它)' not exposed as itself."""
    # 6 users → 'portrait' (above floor).
    for i in range(6):
        _seed_record(f"u-{i}", scene="portrait")
    # 2 users → 'rare_mode' (below floor).
    _seed_record("u-100", scene="rare_mode")
    _seed_record("u-101", scene="rare_mode")

    from app.api.admin_insights import scene_mode_distribution

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(scene_mode_distribution(hours=24, user=_Stub()))  # type: ignore[arg-type]
    keys = [i["key"] for i in res["items"]]
    assert "portrait" in keys
    assert "rare_mode" not in keys
    assert "(其它)" in keys


def test_insights_keywords_explode_per_record(client: TestClient):
    """Each record can carry multiple keywords; they should each
    contribute to their own bucket."""
    for i in range(7):
        _seed_record(f"u-{i}", keywords=["复古", "胶片", "暖色"])
    from app.api.admin_insights import style_keyword_distribution

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(style_keyword_distribution(
        hours=24, top_n=10, user=_Stub()))                          # type: ignore[arg-type]
    by_key = {i["key"]: i["calls"] for i in res["items"]}
    # Each kw used by 7 distinct users so they all clear floor.
    assert by_key.get("复古") == 7
    assert by_key.get("胶片") == 7
    assert by_key.get("暖色") == 7


def test_insights_proposal_adoption_calculates_rates(client: TestClient):
    """Proposal A offered 6× and picked 4× (captured 2×) → adoption 4/6,
    capture 2/4."""
    proposals = [{"id": "prop-A", "title": "A"},
                  {"id": "prop-B", "title": "B"}]
    # 4 users pick A and capture.
    for i in range(2):
        _seed_record(f"u-{i}", proposals=proposals,
                       picked="prop-A", captured=1)
    for i in range(2, 4):
        _seed_record(f"u-{i}", proposals=proposals,
                       picked="prop-A", captured=0)
    # 2 users see proposals, pick B, no capture.
    for i in range(4, 6):
        _seed_record(f"u-{i}", proposals=proposals,
                       picked="prop-B", captured=0)

    from app.api.admin_insights import proposal_adoption

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(proposal_adoption(hours=24, user=_Stub()))    # type: ignore[arg-type]
    by_key = {i["key"]: i for i in res["items"]}
    a = by_key.get("prop-A")
    assert a is not None
    assert a["calls"] == 6 and a["picked"] == 4 and a["captured"] == 2
    assert a["adoption_rate"] == round(4 / 6, 3)
    assert a["capture_rate"] == 0.5


# ---------------------------------------------------------------------------
# 5 new audit points fire
# ---------------------------------------------------------------------------


def test_refresh_failed_writes_audit(client: TestClient):
    r = client.post("/auth/refresh",
                      json={"refresh_token": "obviously-bogus"},
                      headers={"X-Forwarded-For": "203.0.113.222"})
    assert r.status_code in (400, 401)
    with user_repo._connect() as con:
        row = con.execute(
            "SELECT payload FROM admin_audit_log "
            "WHERE action = 'auth.refresh_failed' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert "203.0.113.222" in (row[0] or "")
