"""v17i regression smoke — cohort recommend, alert webhook failure
audit, scene compare, weekly CSV scheduler gating, trend z-score."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services import (
    alert_mailer,
    cohort_recommender,
    csv_scheduler,
    runtime_settings,
    trend_anomaly,
    user_repo,
)


def _seed_record(user_id: str, scene: str, kws: list[str],
                  proposals: list[dict], picked: str | None = None,
                  captured: bool = False, suffix: str = "") -> None:
    sc = {"scene_mode": scene, "style_keywords": kws}
    with user_repo._connect() as con:
        con.execute(
            "INSERT INTO usage_records (id, user_id, request_id, status, "
            "step_config, proposals, picked_proposal_id, captured, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"r-{user_id}-{scene}-{picked or 'n'}-{suffix}",
                user_id, f"req-{user_id}-{suffix}", "charged",
                json.dumps(sc), json.dumps(proposals), picked,
                1 if captured else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()


@pytest.fixture(autouse=True)
def _isolation():
    # _force_mock (autouse) runs first and re-points user_repo.DB_PATH
    # to a fresh tmp dir, so we don't need to DELETE rows here. We just
    # need to wipe the in-process caches that survive across tests.
    cohort_recommender.reset_for_tests()
    runtime_settings.reset_for_tests()
    yield


def test_cohort_recommender_picks_best_when_quorum_met():
    for i in range(6):
        _seed_record(f"u{i}", "portrait", ["natural"],
                      [{"id": "P1"}, {"id": "P2"}], picked="P2",
                      suffix=f"a{i}")
    cohort_recommender.reset_for_tests()
    assert cohort_recommender.recommend("portrait", ["natural"]) == "P2"


def test_cohort_recommender_returns_none_below_k_anon():
    for i in range(2):
        _seed_record(f"u{i}", "scenery", ["sunset"],
                      [{"id": "P9"}], picked="P9", suffix=f"b{i}")
    assert cohort_recommender.recommend("scenery", ["sunset"]) is None


def test_alert_webhook_failure_writes_audit(monkeypatch):
    runtime_settings.set_value("alert.recipients.default",
                                "webhook://https://example.invalid/h",
                                updated_by="t")
    runtime_settings.set_value("alert.cooldown_sec.default", "0",
                                updated_by="t")
    # Bypass alert_mailer's "skip in dev/mock_mode" short-circuit so we
    # actually exercise the webhook send path → failure → audit write.
    monkeypatch.setattr(alert_mailer, "_provider_send",
                          lambda *a, **kw: (_ for _ in ()).throw(
                              RuntimeError("HTTP 503")))
    alert_mailer.maybe_send_for_audit(
        "iap.refund", admin_id="x", target="t",
        payload={"k": "v"},
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )
    with user_repo._connect() as con:
        rows = con.execute(
            "SELECT action, payload FROM admin_audit_log "
            "WHERE action = 'alert.webhook_failed'"
        ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["failures"][0]["channel"] == "webhook"
    assert "target_hash" in payload["failures"][0]


def test_csv_scheduler_gating():
    runtime_settings.set_value("insights.weekly_csv.enabled", "false",
                                updated_by="t")
    now = datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc)  # Monday 9 UTC
    assert not csv_scheduler._should_send_now(now)
    runtime_settings.set_value("insights.weekly_csv.enabled", "true",
                                updated_by="t")
    runtime_settings.set_value("insights.weekly_csv.day", "monday",
                                updated_by="t")
    runtime_settings.set_value("insights.weekly_csv.hour", "9",
                                updated_by="t")
    assert csv_scheduler._should_send_now(now)
    # Already-sent dedup
    runtime_settings.set_value(
        "insights.weekly_csv.last_sent_iso",
        (now - timedelta(hours=1)).isoformat(), updated_by="t")
    assert not csv_scheduler._should_send_now(now)


def test_trend_zscore_flags_spike():
    base = [1] * 167  # 167 hours of baseline = 1 call/h
    series = base + [50]  # last hour = 50 calls
    flagged = trend_anomaly._detect(
        {"sunset": series}, min_z=3.0, min_calls_in_hour=5)
    assert flagged and flagged[0]["keyword"] == "sunset"


def test_trend_zscore_ignores_quiet_keyword():
    series = [0] * 167 + [2]  # last hour only 2 calls → below noise floor
    assert trend_anomaly._detect(
        {"quiet": series}, min_z=3.0, min_calls_in_hour=5) == []
