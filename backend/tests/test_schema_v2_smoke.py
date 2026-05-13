"""Smoke tests for schema_v2 migration (PR1 of subscription/auth rework).

Covers:
  - new tables exist after first connection
  - users.{phone, role, status} columns added & defaulted
  - get_by_phone / get_by_email / create_user happy path
  - schema upgrade is idempotent on a pre-existing v1-shaped sqlite file
"""
from __future__ import annotations

import sqlite3

import pytest

from app.services import user_repo


EXPECTED_TABLES = {
    "users", "device_bindings", "subscriptions", "refresh_tokens",
    "otp_codes", "auth_attempts", "usage_periods", "usage_reservations",
    "usage_records", "subscription_events", "revenue_ledger",
    "expense_ledger", "model_settings", "model_settings_history",
    "admin_audit_log",
}


def _tables(db_path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_v2_tables_created_on_first_connect():
    # Trigger schema by issuing any read (creates db file too).
    user_repo.get_user("nope")
    tables = _tables(user_repo.DB_PATH)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables after migration: {missing}"


def test_users_v2_columns_present():
    user_repo.get_user("nope")
    con = sqlite3.connect(str(user_repo.DB_PATH))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        con.close()
    assert {"phone", "role", "status"}.issubset(cols)


def test_create_user_by_phone_and_role_defaults():
    u = user_repo.create_user(phone="+8613800001111", role="user")
    assert u.phone == "+8613800001111"
    assert u.role == "user"
    assert u.status == "active"
    assert u.is_anonymous is False
    assert u.tier == "free"

    again = user_repo.get_by_phone("+8613800001111")
    assert again is not None
    assert again.id == u.id


def test_create_user_by_email_lowercases():
    u = user_repo.create_user(email="Foo@Bar.COM")
    assert u.email == "foo@bar.com"
    fetched = user_repo.get_by_email("foo@bar.com")
    assert fetched is not None and fetched.id == u.id


def test_create_admin_user_role_persists():
    u = user_repo.create_user(phone="+8613900000000", role="admin")
    assert u.role == "admin"
    user_repo.set_role(u.id, "user")
    assert user_repo.get_user(u.id).role == "user"


def test_phone_unique_among_active_users():
    user_repo.create_user(phone="+8613800002222")
    with pytest.raises(sqlite3.IntegrityError):
        user_repo.create_user(phone="+8613800002222")


def test_legacy_v1_sqlite_upgrades_in_place(tmp_path, monkeypatch):
    """Simulate a pre-v17 db file (v1 schema) and ensure connect upgrades it."""
    legacy = tmp_path / "users_legacy.db"
    con = sqlite3.connect(str(legacy))
    con.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            apple_sub TEXT UNIQUE,
            email TEXT,
            is_anonymous INTEGER NOT NULL DEFAULT 1,
            tier TEXT NOT NULL DEFAULT 'free',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );
        INSERT INTO users (id, is_anonymous, tier, created_at, updated_at)
        VALUES ('legacy-user', 1, 'free', '2024-01-01T00:00:00+00:00',
                '2024-01-01T00:00:00+00:00');
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(user_repo, "DB_PATH", legacy)

    u = user_repo.get_user("legacy-user")
    assert u is not None
    assert u.role == "user"           # back-fill default
    assert u.status == "active"
    assert u.phone is None
    # And the new tables exist now too.
    assert EXPECTED_TABLES.issubset(_tables(legacy))
