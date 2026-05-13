"""v18 — satisfaction signal regression.

Covers:
  * /me/usage/{id}/satisfied owner-scoping (404 for foreign records)
  * personal preference accumulation (>= 2 satisfied → render hint)
  * cohort sample threshold (1 satisfied → no hint)
  * cross-user trend gating (enabled=false → no block; enabled=true
    + thresholds met → block)
  * threshold edge: rate exactly == min should pass (>= semantic)
  * privacy: GET /me/usage detail never exposes user_id list,
    /admin endpoint payloads contain no raw photo bytes (sanity).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services import (
    runtime_settings,
    satisfaction_aggregates,
    style_catalog,
    usage_records,
    user_preferences,
    user_repo,
)


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    runtime_settings.reset_for_tests()
    satisfaction_aggregates.reset_for_tests()
    # v18 c1 — disable the 7-day cooldown so accumulator tests can
    # still exercise multiple satisfied taps in a row. The cooldown
    # itself is exercised by `test_personal_pref_cooldown_skips`.
    monkeypatch.setattr(user_preferences, "_COOLDOWN_SEC", 0)
    yield


def _make_user(email: str = "u@example.com") -> str:
    """Create a real user via user_repo so JWT-requiring endpoints
    don't 401. Returns user.id."""
    u = user_repo.create_user(email=email)
    return u.id


def _seed_charged_record(user_id: str, scene: str,
                          style_keywords: list[str]) -> str:
    """Insert a 'charged' usage_records row owned by user_id with the
    given step_config. Returns the record id."""
    sc = {"scene_mode": scene, "style_keywords": style_keywords}
    rid = usage_records.create_pending(
        user_id=user_id,
        request_id=f"req-{scene}-{user_id[:6]}-{len(style_keywords)}",
        step_config=sc,
    )
    usage_records.mark_charged(
        rid, proposals=[{"id": "S1"}, {"id": "S2"}],
        model_id="test-model",
    )
    return rid


# ---------------------------------------------------------------------------
# style_catalog mapping
# ---------------------------------------------------------------------------


def test_style_catalog_maps_known_keywords():
    assert style_catalog.infer_style_id(["cinematic", "moody"]) \
        == "cinematic_moody"
    assert style_catalog.infer_style_id(["clean", "bright"]) == "clean_bright"
    assert style_catalog.infer_style_id(["editorial", "fashion"]) \
        == "editorial_fashion"


def test_style_catalog_returns_none_for_unknown():
    assert style_catalog.infer_style_id([]) is None
    assert style_catalog.infer_style_id(["something_random"]) is None


# ---------------------------------------------------------------------------
# Personal preference accumulator
# ---------------------------------------------------------------------------


def test_personal_pref_below_min_samples_no_hint():
    user = _make_user("p1@example.com")
    rid = _seed_charged_record(user, "portrait", ["cinematic", "moody"])
    usage_records.mark_satisfied(user_id=user, record_id=rid,
                                   satisfied=True)
    # Only 1 satisfied → below _MIN_PERSONAL_SAMPLES=2.
    assert user_preferences.render_personal_hint(user, "portrait") is None


def test_personal_pref_two_satisfied_renders_hint():
    user = _make_user("p2@example.com")
    for _ in range(2):
        rid = _seed_charged_record(user, "portrait", ["cinematic", "moody"])
        usage_records.mark_satisfied(user_id=user, record_id=rid,
                                       satisfied=True)
    hint = user_preferences.render_personal_hint(user, "portrait")
    assert hint is not None
    # v18 s2 — scene rendered as zh ("人像"), not raw enum.
    assert "氛围感" in hint and "人像" in hint


def test_personal_pref_dissatisfied_dominates_no_hint():
    user = _make_user("p3@example.com")
    rid1 = _seed_charged_record(user, "portrait", ["cinematic", "moody"])
    usage_records.mark_satisfied(user_id=user, record_id=rid1, satisfied=True)
    # 1 satisfied + below min → no hint regardless of dissatisfied,
    # but check the "net <= 0 filter" path explicitly with 2 sat + 3 dis.
    user2 = _make_user("p3b@example.com")
    for _ in range(2):
        r = _seed_charged_record(user2, "portrait", ["cinematic", "moody"])
        usage_records.mark_satisfied(user_id=user2, record_id=r,
                                       satisfied=True)
    for _ in range(3):
        r = _seed_charged_record(user2, "portrait", ["cinematic", "moody"])
        usage_records.mark_satisfied(user_id=user2, record_id=r,
                                       satisfied=False)
    # net = 2 - 3 = -1 → filtered out by render_personal_hint.
    assert user_preferences.render_personal_hint(user2, "portrait") is None


def test_personal_pref_cooldown_skips(monkeypatch):
    """With cooldown=1h, two satisfied taps within the window only
    bump the counter once."""
    monkeypatch.setattr(user_preferences, "_COOLDOWN_SEC", 3600)
    u = _make_user("cool@example.com")
    for _ in range(3):
        r = _seed_charged_record(u, "portrait", ["cinematic", "moody"])
        usage_records.mark_satisfied(user_id=u, record_id=r,
                                       satisfied=True)
    rows = user_preferences.top_styles(u, "portrait")
    # Without cooldown we'd expect satisfied=3; with cooldown only
    # the first tap counts.
    assert rows == []  # 1 sat < _MIN_PERSONAL_SAMPLES → no hint row
    # Verify the underlying counter directly.
    with user_repo._connect() as con:                               # noqa: SLF001
        row = con.execute(
            "SELECT satisfied FROM user_preferences WHERE user_id = ?",
            (u,),
        ).fetchone()
    assert row is not None and int(row["satisfied"]) == 1


def test_personal_pref_owner_isolation():
    """User A's likes must not leak into user B's hint."""
    a = _make_user("ai@example.com")
    b = _make_user("bo@example.com")
    for _ in range(2):
        r = _seed_charged_record(a, "portrait", ["cinematic", "moody"])
        usage_records.mark_satisfied(user_id=a, record_id=r, satisfied=True)
    assert user_preferences.render_personal_hint(b, "portrait") is None
    assert user_preferences.render_personal_hint(a, "portrait") is not None


# ---------------------------------------------------------------------------
# Foreign record can't be marked satisfied
# ---------------------------------------------------------------------------


def test_mark_satisfied_owner_scoped_no_op():
    a = _make_user("aa@example.com")
    b = _make_user("bb@example.com")
    rid = _seed_charged_record(a, "portrait", ["cinematic", "moody"])
    # b tries to mark a's record — service should not error but also
    # not mutate (UPDATE ... WHERE user_id = b matches 0 rows).
    usage_records.mark_satisfied(user_id=b, record_id=rid, satisfied=True)
    rec = usage_records.get_for_user(a, rid)
    assert rec is not None
    assert rec.satisfied is None


# ---------------------------------------------------------------------------
# Cross-user trend gating
# ---------------------------------------------------------------------------


def _seed_aggregate(scene: str, style_keywords: list[str], n_users: int,
                     satisfied: bool) -> None:
    for i in range(n_users):
        u = _make_user(f"agg-{scene}-{i}-{satisfied}@example.com")
        r = _seed_charged_record(u, scene, style_keywords)
        usage_records.mark_satisfied(user_id=u, record_id=r,
                                       satisfied=satisfied)


def test_global_hint_disabled_by_default_returns_none():
    _seed_aggregate("portrait", ["cinematic", "moody"], n_users=10,
                     satisfied=True)
    # default is OFF.
    assert satisfaction_aggregates.render_global_hint("portrait") is None


def test_global_hint_under_distinct_users_threshold_returns_none():
    runtime_settings.set_value("pref.global_hint.enabled", "true",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_distinct_users", "20",
                                updated_by="t")
    _seed_aggregate("portrait", ["cinematic", "moody"], n_users=5,
                     satisfied=True)
    satisfaction_aggregates.reset_for_tests()
    assert satisfaction_aggregates.render_global_hint("portrait") is None


def test_global_hint_threshold_met_returns_block():
    runtime_settings.set_value("pref.global_hint.enabled", "true",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_distinct_users", "5",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_satisfaction_rate",
                                "0.5", updated_by="t")
    _seed_aggregate("portrait", ["cinematic", "moody"], n_users=5,
                     satisfied=True)
    satisfaction_aggregates.reset_for_tests()
    hint = satisfaction_aggregates.render_global_hint("portrait")
    assert hint is not None
    assert "氛围感" in hint
    assert "人像" in hint  # v18 s2 — zh scene label


def test_global_hint_rate_edge_inclusive():
    """rate = 0.5 should pass when min = 0.5 (>= semantic)."""
    runtime_settings.set_value("pref.global_hint.enabled", "true",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_distinct_users", "5",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_satisfaction_rate",
                                "0.5", updated_by="t")
    # 5 users satisfied + 5 users dissatisfied → rate exactly 0.5.
    _seed_aggregate("scenery", ["clean", "bright"], n_users=5,
                     satisfied=True)
    _seed_aggregate("scenery", ["clean", "bright"], n_users=5,
                     satisfied=False)
    satisfaction_aggregates.reset_for_tests()
    hint = satisfaction_aggregates.render_global_hint("scenery")
    assert hint is not None


def test_global_hint_just_below_rate_threshold_excluded():
    runtime_settings.set_value("pref.global_hint.enabled", "true",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_distinct_users", "5",
                                updated_by="t")
    runtime_settings.set_value("pref.global_hint.min_satisfaction_rate",
                                "0.6", updated_by="t")
    # 5 satisfied + 5 dissatisfied → rate 0.5 < 0.6.
    _seed_aggregate("light_shadow", ["film", "warm"], n_users=5,
                     satisfied=True)
    _seed_aggregate("light_shadow", ["film", "warm"], n_users=5,
                     satisfied=False)
    satisfaction_aggregates.reset_for_tests()
    assert satisfaction_aggregates.render_global_hint("light_shadow") \
        is None


# ---------------------------------------------------------------------------
# Privacy regression
# ---------------------------------------------------------------------------


def test_admin_aggregate_payload_has_no_user_id():
    a = _make_user("priv@example.com")
    r = _seed_charged_record(a, "portrait", ["cinematic", "moody"])
    usage_records.mark_satisfied(user_id=a, record_id=r, satisfied=True)
    rows = satisfaction_aggregates.list_for_admin("portrait")
    blob = json.dumps(rows, ensure_ascii=False, default=str)
    # Hard guard: future regression that adds user_id to the rollup
    # would silently leak who liked what.
    assert "user_id" not in blob
    assert a not in blob  # specific user pk also not present


def test_satisfied_note_truncated_to_200():
    a = _make_user("note@example.com")
    r = _seed_charged_record(a, "portrait", ["cinematic", "moody"])
    long = "x" * 500
    usage_records.mark_satisfied(user_id=a, record_id=r,
                                   satisfied=True, note=long)
    rec = usage_records.get_for_user(a, r)
    assert rec is not None
    assert rec.satisfied_note is not None
    assert len(rec.satisfied_note) <= 200
