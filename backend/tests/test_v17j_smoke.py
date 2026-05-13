"""v17j regression — cohort detail metadata, severity routing,
cohort cache bust endpoint."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services import (alert_mailer, cohort_recommender,
                            runtime_settings, user_repo)


@pytest.fixture(autouse=True)
def _reset():
    cohort_recommender.reset_for_tests()
    runtime_settings.reset_for_tests()
    yield


def _seed(user_id: str, scene: str, kws: list[str], pid: str,
            suffix: str) -> None:
    sc = {"scene_mode": scene, "style_keywords": kws}
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO usage_records (id, user_id, request_id, status, "
            "step_config, proposals, picked_proposal_id, captured, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"r-{user_id}-{suffix}", user_id, f"q-{user_id}-{suffix}",
                "charged", json.dumps(sc), json.dumps([{"id": pid}]),
                pid, 0, datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()


def test_recommend_detailed_returns_cohort_size_and_basis():
    for i in range(6):
        _seed(f"u{i}", "portrait", ["natural"], "PX", suffix=f"a{i}")
    res = cohort_recommender.recommend_detailed("portrait", ["natural"])
    assert res is not None
    assert res["proposal_id"] == "PX"
    assert res["cohort_size"] == 6
    assert res["cohort_basis"] == "scene+keyword:natural"


def test_recommend_detailed_falls_back_to_scene_only():
    # 6 distinct users, scene='scenery', kw none → only scene-level
    # bucket has quorum.
    for i in range(6):
        _seed(f"u{i}", "scenery", [], "PY", suffix=f"b{i}")
    res = cohort_recommender.recommend_detailed("scenery", [])
    assert res is not None
    assert res["cohort_basis"] == "scene:scenery"


def test_severity_inference_default_buckets():
    assert alert_mailer.severity_for("trend.anomaly") == "trend"
    assert alert_mailer.severity_for("asn.signature_invalid") == "critical"
    assert alert_mailer.severity_for("alert.webhook_failed") == "critical"
    assert alert_mailer.severity_for("iap.asn.refund") == "warning"
    assert alert_mailer.severity_for("otp.permanent_lock") == "warning"
    assert alert_mailer.severity_for("auth.admin_login_success") == "info"
    assert alert_mailer.severity_for("endpoint_config.save") == "info"


def test_severity_override_takes_precedence():
    runtime_settings.set_value("alert.severity.endpoint_config.save",
                                "critical", updated_by="t")
    assert alert_mailer.severity_for("endpoint_config.save") == "critical"


def test_recipients_routes_via_severity_when_no_exact_match():
    runtime_settings.set_value("alert.recipients.severity.warning",
                                "biz@example.com", updated_by="t")
    runtime_settings.set_value("alert.recipients.default",
                                "ops@example.com", updated_by="t")
    # iap.asn.refund → severity.warning → biz@
    assert alert_mailer.recipients_for("iap.asn.refund") == ["biz@example.com"]
    # auth.admin_login_success → severity.info (no rec) → default
    assert alert_mailer.recipients_for("auth.admin_login_success") == \
        ["ops@example.com"]


def test_recipients_exact_still_wins_over_severity():
    runtime_settings.set_value("alert.recipients.severity.warning",
                                "biz@example.com", updated_by="t")
    runtime_settings.set_value("alert.recipients.iap.asn.refund",
                                "billing@example.com", updated_by="t")
    assert alert_mailer.recipients_for("iap.asn.refund") == \
        ["billing@example.com"]


def test_trend_alerts_dont_leak_into_critical_inbox():
    # The whole point of the severity tier is keyword spikes never
    # share an inbox with security incidents.
    runtime_settings.set_value("alert.recipients.severity.critical",
                                "oncall@example.com", updated_by="t")
    runtime_settings.set_value("alert.recipients.severity.trend",
                                "growth@example.com", updated_by="t")
    assert alert_mailer.recipients_for("trend.anomaly") == \
        ["growth@example.com"]
    assert alert_mailer.recipients_for("asn.signature_invalid") == \
        ["oncall@example.com"]
