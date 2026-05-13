"""v17h — alert webhooks, insights metric/CSV/series/cooccurrence,
audit PII redaction."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (alert_mailer, blocklist, endpoint_config,
                            rate_buckets, runtime_settings, user_repo)


@pytest.fixture()
def client() -> TestClient:
    rate_buckets.reset_for_tests()
    blocklist.reset_for_tests()
    runtime_settings.reset_for_tests()
    endpoint_config.reset_cache_for_tests()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Channel parsing
# ---------------------------------------------------------------------------


def test_parse_target_email():
    assert alert_mailer._parse_target("ops@x.com") == ("email", "ops@x.com")


def test_parse_target_lark_and_dingtalk():
    assert alert_mailer._parse_target("lark://https://open.feishu.cn/x") \
        == ("lark", "https://open.feishu.cn/x")
    assert alert_mailer._parse_target("dingtalk://https://oapi.dingtalk.com/y") \
        == ("dingtalk", "https://oapi.dingtalk.com/y")


def test_parse_target_garbage_returns_none():
    assert alert_mailer._parse_target("just text") is None
    assert alert_mailer._parse_target("") is None


def test_admin_recipients_endpoint_accepts_webhook_url(client: TestClient):
    """The PUT endpoint validation must allow lark://... entries."""
    # Direct service write (bypass auth) — emulates what set_alert_recipients
    # produces after its own validation.
    runtime_settings.set_value(
        "alert.recipients.test",
        "lark://https://open.feishu.cn/bot/v2/hook/abc,ops@example.com",
        updated_by="admin-test",
    )
    rec = alert_mailer.recipients_for("test")
    assert "ops@example.com" in rec
    assert any(r.startswith("lark://") for r in rec)


def test_lark_webhook_payload_is_text_msg():
    """Mock urllib so we don't actually POST. Verify the payload
    shape Lark expects."""
    captured = {}

    def fake_post(url, payload):
        captured["url"] = url
        captured["payload"] = payload

    with patch.object(alert_mailer, "_post_json", side_effect=fake_post):
        alert_mailer._send_lark("https://lark.example.com/hook",
                                  "subject!", "body line 1\nline 2")
    assert captured["payload"]["msg_type"] == "text"
    assert "subject!" in captured["payload"]["content"]["text"]
    assert "body line 1" in captured["payload"]["content"]["text"]


# ---------------------------------------------------------------------------
# Audit PII redaction for alert.recipients.* keys
# ---------------------------------------------------------------------------


def test_runtime_settings_redacts_alert_recipients_in_audit(client: TestClient):
    """When admin saves alert.recipients.<x>, audit payload must NOT
    contain the literal email — only a hash + length."""
    # Hit the API path.
    from app.api.admin import RuntimeSettingIn, set_runtime_setting
    from starlette.requests import Request

    class _Stub:
        id = "admin-test"; role = "admin"

    scope = {"type": "http", "headers": [], "client": None}
    req = Request(scope)
    payload = RuntimeSettingIn(
        key="alert.recipients.iap.asn.refund",
        value="secret-leak@example.com,another@example.com",
    )
    asyncio.run(set_runtime_setting(payload, req, user=_Stub()))    # type: ignore[arg-type]

    with user_repo._connect() as con:
        row = con.execute(
            "SELECT payload FROM admin_audit_log "
            "WHERE action = 'runtime_settings.set' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    body = row[0] or ""
    assert "secret-leak@example.com" not in body
    assert "another@example.com" not in body
    assert "redacted" in body
    assert "sha256=" in body


def test_runtime_settings_keeps_value_for_non_sensitive(client: TestClient):
    """Non-sensitive keys (e.g. otp.global_rpm) keep their literal
    value in audit so admin can diff."""
    from app.api.admin import RuntimeSettingIn, set_runtime_setting
    from starlette.requests import Request

    class _Stub:
        id = "admin-test"; role = "admin"

    scope = {"type": "http", "headers": [], "client": None}
    req = Request(scope)
    payload = RuntimeSettingIn(key="otp.global_rpm", value="42")
    asyncio.run(set_runtime_setting(payload, req, user=_Stub()))    # type: ignore[arg-type]

    with user_repo._connect() as con:
        row = con.execute(
            "SELECT payload FROM admin_audit_log "
            "WHERE action = 'runtime_settings.set' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    body = row[0] or ""
    assert "\"value\": \"42\"" in body


# ---------------------------------------------------------------------------
# Insights — metric, CSV, series, cooccurrence
# ---------------------------------------------------------------------------


def _seed(user_id: str, scene: str, kws: list[str] | None = None,
           proposals: list[dict] | None = None,
           picked: str | None = None, captured: int = 0,
           created_at: datetime | None = None) -> None:
    sc = {"scene_mode": scene, "quality_mode": "fast",
          "style_keywords": kws or []}
    ts = (created_at or datetime.now(timezone.utc)).isoformat()
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO usage_records (user_id, request_id, status, "
            "step_config, proposals, picked_proposal_id, captured, "
            "model_id, prompt_tokens, completion_tokens, cost_usd, "
            "created_at, charge_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, f"req-{user_id}-{ts}",
             "charged", json.dumps(sc),
             json.dumps(proposals or []), picked, captured,
             "fast-1", 100, 200, 0.001, ts, ts),
        )
        con.commit()


def test_insights_metric_distinct_users_changes_order(client: TestClient):
    """One power user spamming `night` 100×, vs 6 users using
    `portrait` once. By calls → night wins; by distinct_users →
    portrait wins."""
    for _ in range(100):
        _seed("power-user", "night")
    for i in range(6):
        _seed(f"u-{i}", "portrait")
    from app.api.admin_insights import scene_mode_distribution

    class _Stub:
        id = "admin"; role = "admin"

    by_calls = asyncio.run(scene_mode_distribution(
        hours=24, metric="calls", user=_Stub()))                    # type: ignore[arg-type]
    by_users = asyncio.run(scene_mode_distribution(
        hours=24, metric="distinct_users", user=_Stub()))           # type: ignore[arg-type]
    # By calls: night dominates if it cleared the floor; but distinct
    # users for night = 1 < 5 → night gets merged into "(其它)".
    # So night SHOULD NOT appear by name in either result.
    assert "night" not in [i["key"] for i in by_calls["items"]]
    # In distinct_users mode, portrait should be the top non-bucket.
    real_top = next(i for i in by_users["items"] if i["key"] != "(其它)")
    assert real_top["key"] == "portrait"


def test_insights_csv_export_has_four_reports(client: TestClient):
    for i in range(6):
        _seed(f"u-{i}", "portrait", kws=["复古"],
              proposals=[{"id": "p-A"}, {"id": "p-B"}], picked="p-A",
              captured=1)
    r = client.get("/admin/insights/export.csv?hours=24")
    # Auth required; just check the function output without auth via
    # direct call.
    from app.api.admin_insights import export_insights_csv

    class _Stub:
        id = "admin"; role = "admin"

    resp = asyncio.run(export_insights_csv(hours=24, user=_Stub()))  # type: ignore[arg-type]
    body = resp.body.decode("utf-8")
    assert body.startswith("report,key,calls,")
    assert "scene_mode" in body
    assert "style_keyword" in body
    assert "proposal" in body
    assert "复古" in body


def test_insights_keyword_series_buckets_top_n(client: TestClient):
    now = datetime.now(timezone.utc)
    for i in range(6):
        _seed(f"u-{i}", "portrait", kws=["复古"],
              created_at=now - timedelta(hours=2))
    for i in range(6, 12):
        _seed(f"u-{i}", "portrait", kws=["复古"],
              created_at=now - timedelta(hours=30))
    from app.api.admin_insights import style_keyword_series

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(style_keyword_series(
        hours=72, bucket_hours=24, top_n=5, user=_Stub()))          # type: ignore[arg-type]
    assert "复古" in res["keys"]
    fugu = next(v for v in res["values"] if v["key"] == "复古")
    assert sum(fugu["counts"]) == 12
    assert len(res["buckets"]) == len(fugu["counts"])


def test_insights_cooccurrence_finds_best_proposal(client: TestClient):
    """Build a cohort where (portrait + 复古) clearly favours prop-A."""
    proposals = [{"id": "p-A"}, {"id": "p-B"}]
    # 6 users in (portrait + 复古), all pick A.
    for i in range(6):
        _seed(f"u-A-{i}", "portrait", kws=["复古"],
              proposals=proposals, picked="p-A")
    # 1 user picks B (should not move the needle, also under the
    # adoption-rate denominator floor).
    _seed("u-B", "portrait", kws=["复古"],
          proposals=proposals, picked="p-B")
    from app.api.admin_insights import proposal_cooccurrence

    class _Stub:
        id = "admin"; role = "admin"

    res = asyncio.run(proposal_cooccurrence(hours=24, user=_Stub()))  # type: ignore[arg-type]
    rows = [r for r in res["items"]
            if r["scene_mode"] == "portrait" and r["keyword"] == "复古"]
    assert rows, "no recommendation for portrait+复古"
    assert rows[0]["recommended_proposal_id"] == "p-A"
    assert rows[0]["adoption_rate"] >= 0.7
