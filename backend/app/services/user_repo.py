"""Users / subscriptions persistence (A0-1 / A0-7 of MULTI_USER_AUTH).

Single sqlite db at ``data/users.db``. Designed to be swappable for
Postgres later — every call goes through this module, never raw SQL
elsewhere.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "users.db"


@dataclass
class User:
    id: str
    apple_sub: Optional[str]
    email: Optional[str]
    is_anonymous: bool
    tier: str           # 'free' | 'pro'
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]


@dataclass
class Subscription:
    id: int
    user_id: str
    product_id: str
    original_transaction_id: str
    latest_transaction_id: str
    environment: str
    purchase_date: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    auto_renew: bool


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con)
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            apple_sub     TEXT UNIQUE,
            email         TEXT,
            is_anonymous  INTEGER NOT NULL DEFAULT 1,
            tier          TEXT NOT NULL DEFAULT 'free',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            deleted_at    TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_users_apple_sub ON users(apple_sub)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_users_deleted ON users(deleted_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS device_bindings (
            device_id   TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at  TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_bindings_user ON device_bindings(user_id)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                  TEXT NOT NULL,
            product_id               TEXT NOT NULL,
            original_transaction_id  TEXT NOT NULL UNIQUE,
            latest_transaction_id    TEXT NOT NULL,
            environment              TEXT NOT NULL,
            purchase_date            TEXT NOT NULL,
            expires_at               TEXT,
            revoked_at               TEXT,
            auto_renew               INTEGER NOT NULL DEFAULT 1,
            raw_jws                  TEXT NOT NULL,
            received_at              TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscriptions(expires_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            jti         TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            issued_at   TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            revoked_at  TEXT
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id)"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        apple_sub=row["apple_sub"],
        email=row["email"],
        is_anonymous=bool(row["is_anonymous"]),
        tier=row["tier"] or "free",
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
        deleted_at=_parse(row["deleted_at"]) if row["deleted_at"] else None,
    )


def _parse(s: Optional[str]) -> datetime:
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


def get_user(user_id: str) -> Optional[User]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
    return _row_to_user(row) if row else None


def get_by_apple_sub(apple_sub: str) -> Optional[User]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM users WHERE apple_sub = ? AND deleted_at IS NULL",
            (apple_sub,),
        ).fetchone()
    return _row_to_user(row) if row else None


def get_by_device_id(device_id: str) -> Optional[User]:
    with _connect() as con:
        row = con.execute(
            """
            SELECT u.* FROM users u
            JOIN device_bindings d ON d.user_id = u.id
            WHERE d.device_id = ? AND u.deleted_at IS NULL
            """,
            (device_id,),
        ).fetchone()
    return _row_to_user(row) if row else None


def create_anonymous(device_id: Optional[str] = None) -> User:
    """Create a fresh anonymous user, optionally bound to a device id."""
    uid = str(uuid.uuid4())
    now = _now()
    with _connect() as con:
        con.execute(
            "INSERT INTO users (id, is_anonymous, tier, created_at, updated_at) "
            "VALUES (?, 1, 'free', ?, ?)",
            (uid, now, now),
        )
        if device_id:
            con.execute(
                "INSERT OR REPLACE INTO device_bindings (device_id, user_id, created_at) "
                "VALUES (?, ?, ?)",
                (device_id, uid, now),
            )
    log.info("user_repo: anonymous user created id=%s device_id_bound=%s", uid, bool(device_id))
    return get_user(uid)  # type: ignore[return-value]


def upgrade_to_siwa(user_id: str, apple_sub: str, email: Optional[str]) -> User:
    """Convert an anonymous user into a SIWA-backed account."""
    now = _now()
    with _connect() as con:
        con.execute(
            "UPDATE users SET apple_sub = ?, email = COALESCE(email, ?), "
            "is_anonymous = 0, updated_at = ? WHERE id = ?",
            (apple_sub, email, now, user_id),
        )
    return get_user(user_id)  # type: ignore[return-value]


def bind_device(user_id: str, device_id: str) -> None:
    if not device_id:
        return
    with _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO device_bindings (device_id, user_id, created_at) "
            "VALUES (?, ?, ?)",
            (device_id, user_id, _now()),
        )


def set_tier(user_id: str, tier: str) -> None:
    assert tier in ("free", "pro"), tier
    with _connect() as con:
        con.execute(
            "UPDATE users SET tier = ?, updated_at = ? WHERE id = ?",
            (tier, _now(), user_id),
        )


def soft_delete(user_id: str) -> None:
    """Mark deleted + cascade-erase user-owned rows across other dbs.

    The 24h hard-delete sweeper handles the final removal of the row
    itself; we wipe content immediately so nothing user-owned survives
    past the API call.
    """
    now = _now()
    with _connect() as con:
        con.execute(
            "UPDATE users SET deleted_at = ?, updated_at = ?, apple_sub = NULL, "
            "email = NULL WHERE id = ?",
            (now, now, user_id),
        )
        con.execute("DELETE FROM device_bindings WHERE user_id = ?", (user_id,))
        con.execute(
            "UPDATE subscriptions SET revoked_at = COALESCE(revoked_at, ?) "
            "WHERE user_id = ?",
            (now, user_id),
        )
        con.execute(
            "UPDATE refresh_tokens SET revoked_at = COALESCE(revoked_at, ?) "
            "WHERE user_id = ?",
            (now, user_id),
        )

    # Cascade into feature tables (best-effort; swallow errors so a
    # missing table never blocks the deletion API).
    _cascade_delete(user_id)


def _cascade_delete(user_id: str) -> None:
    feedback_db = Path(__file__).resolve().parent.parent.parent / "data" / "shot_results.db"
    recon_db   = Path(__file__).resolve().parent.parent.parent / "data" / "recon3d_jobs.db"
    poi_db     = Path(__file__).resolve().parent.parent.parent / "data" / "poi_kb.db"
    attest_db  = Path(__file__).resolve().parent.parent.parent / "data" / "attested_devices.db"
    for db in (feedback_db, recon_db, poi_db, attest_db):
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(str(db))
            try:
                cols_by_table = {}
                for (tname,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall():
                    cols = [r[1] for r in con.execute(f"PRAGMA table_info({tname})").fetchall()]
                    if "user_id" in cols:
                        cols_by_table[tname] = cols
                for tname in cols_by_table:
                    con.execute(f"DELETE FROM {tname} WHERE user_id = ?", (user_id,))
                con.commit()
            finally:
                con.close()
        except sqlite3.DatabaseError as e:        # noqa: BLE001
            log.info("user_repo cascade delete on %s failed: %s", db.name, e)


def touch(user_id: str) -> None:
    """Update `updated_at` so the inactivity sweeper sees this user as
    alive. Best-effort + cheap; called from `current_user`."""
    try:
        with _connect() as con:
            con.execute(
                "UPDATE users SET updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (_now(), user_id),
            )
    except sqlite3.DatabaseError:
        pass


def purge_inactive_anonymous(older_than_days: int) -> int:
    """A1-4 — soft-delete anonymous users whose `updated_at` is older
    than the cutoff, then hard-delete via `_cascade_delete`. Returns
    the number of users purged."""
    if older_than_days <= 0:
        return 0
    cutoff_ts = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
    victims: list[str] = []
    with _connect() as con:
        rows = con.execute(
            "SELECT id, updated_at FROM users "
            "WHERE is_anonymous = 1 AND deleted_at IS NULL"
        ).fetchall()
        for r in rows:
            try:
                if datetime.fromisoformat(r["updated_at"]).timestamp() < cutoff_ts:
                    victims.append(r["id"])
            except (TypeError, ValueError):
                continue
    for uid in victims:
        soft_delete(uid)
    # Immediately hard-delete (no 24h grace for anon — they never
    # logged in, so there's no recovery story).
    if victims:
        with _connect() as con:
            con.executemany("DELETE FROM users WHERE id = ?",
                             [(uid,) for uid in victims])
    return len(victims)


def hard_delete_old(older_than_hours: int = 24) -> int:
    """Cron sweeper: physically remove users soft-deleted > N hours ago."""
    cutoff = (datetime.now(timezone.utc).timestamp() - older_than_hours * 3600)
    with _connect() as con:
        rows = con.execute(
            "SELECT id, deleted_at FROM users WHERE deleted_at IS NOT NULL"
        ).fetchall()
        victims = []
        for r in rows:
            try:
                if datetime.fromisoformat(r["deleted_at"]).timestamp() < cutoff:
                    victims.append(r["id"])
            except (TypeError, ValueError):
                continue
        for uid in victims:
            con.execute("DELETE FROM users WHERE id = ?", (uid,))
    return len(victims)


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------


def upsert_subscription(
    *, user_id: str, product_id: str,
    original_transaction_id: str, latest_transaction_id: str,
    environment: str, purchase_date: datetime,
    expires_at: Optional[datetime], revoked_at: Optional[datetime],
    auto_renew: bool, raw_jws: str,
) -> None:
    with _connect() as con:
        con.execute(
            """
            INSERT INTO subscriptions (
                user_id, product_id, original_transaction_id,
                latest_transaction_id, environment, purchase_date,
                expires_at, revoked_at, auto_renew, raw_jws, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(original_transaction_id) DO UPDATE SET
                latest_transaction_id = excluded.latest_transaction_id,
                expires_at            = excluded.expires_at,
                revoked_at            = excluded.revoked_at,
                auto_renew            = excluded.auto_renew,
                raw_jws               = excluded.raw_jws,
                received_at           = excluded.received_at
            """,
            (
                user_id, product_id, original_transaction_id,
                latest_transaction_id, environment, purchase_date.isoformat(),
                expires_at.isoformat() if expires_at else None,
                revoked_at.isoformat() if revoked_at else None,
                1 if auto_renew else 0, raw_jws, _now(),
            ),
        )


def list_active_subscriptions(user_id: str) -> list[Subscription]:
    now_iso = _now()
    with _connect() as con:
        rows = con.execute(
            """
            SELECT * FROM subscriptions
            WHERE user_id = ?
              AND (revoked_at IS NULL)
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY expires_at DESC NULLS LAST
            """,
            (user_id, now_iso),
        ).fetchall()
    return [_row_to_sub(r) for r in rows]


def find_subscription_by_original_id(original_transaction_id: str) -> Optional[Subscription]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM subscriptions WHERE original_transaction_id = ?",
            (original_transaction_id,),
        ).fetchone()
    return _row_to_sub(row) if row else None


def _row_to_sub(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        user_id=row["user_id"],
        product_id=row["product_id"],
        original_transaction_id=row["original_transaction_id"],
        latest_transaction_id=row["latest_transaction_id"],
        environment=row["environment"],
        purchase_date=_parse(row["purchase_date"]),
        expires_at=_parse(row["expires_at"]) if row["expires_at"] else None,
        revoked_at=_parse(row["revoked_at"]) if row["revoked_at"] else None,
        auto_renew=bool(row["auto_renew"]),
    )


# ---------------------------------------------------------------------------
# Refresh-token allow-list
# ---------------------------------------------------------------------------


def remember_refresh(jti: str, user_id: str, expires_at: datetime) -> None:
    with _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO refresh_tokens (jti, user_id, issued_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (jti, user_id, _now(), expires_at.isoformat()),
        )


def is_refresh_valid(jti: str) -> bool:
    with _connect() as con:
        row = con.execute(
            "SELECT revoked_at, expires_at FROM refresh_tokens WHERE jti = ?",
            (jti,),
        ).fetchone()
    if not row:
        return False
    if row["revoked_at"]:
        return False
    try:
        return datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
    except ValueError:
        return False


def revoke_refresh(jti: str) -> None:
    with _connect() as con:
        con.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE jti = ?",
            (_now(), jti),
        )


def reset_for_tests() -> None:
    """Wipe the users db between tests."""
    if DB_PATH.exists():
        DB_PATH.unlink()
