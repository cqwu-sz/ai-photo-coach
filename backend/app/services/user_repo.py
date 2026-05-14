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
    # v17 — multi-channel auth + RBAC.
    # Added in schema_v2; old rows back-fill to defaults via _ensure_schema.
    phone: Optional[str] = None
    role: str = "user"   # 'user' | 'admin'
    status: str = "active"  # 'active' | 'locked'
    # v17b — sha256(device_id). Used by usage_quota to anchor the
    # free-tier 5-shot bucket on the physical device, not the account.
    device_fingerprint: Optional[str] = None


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
    _ensure_schema_v2(con)
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


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _ensure_schema_v2(con: sqlite3.Connection) -> None:
    """v17 schema migration — additive only, idempotent.

    Brings older sqlite files up to the multi-channel auth + quota +
    audit world without ever dropping data. Runs on every connection
    (cheap; PRAGMA + IF NOT EXISTS are no-ops once applied).
    """
    # ---- users.{phone, role, status, device_fingerprint} ----------------
    if not _column_exists(con, "users", "phone"):
        con.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if not _column_exists(con, "users", "role"):
        con.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if not _column_exists(con, "users", "status"):
        con.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    # v17b — device_fingerprint = sha256(device_id). Lets the free
    # quota be anchored on the device, not the account, so creating
    # a 2nd account on the same iPhone shares the same 5-shot bucket
    # (anti registration-farm; users can still use a 2nd device).
    if not _column_exists(con, "users", "device_fingerprint"):
        con.execute("ALTER TABLE users ADD COLUMN device_fingerprint TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_users_device_fp "
                "ON users(device_fingerprint)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone) "
                "WHERE phone IS NOT NULL AND deleted_at IS NULL")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) "
                "WHERE email IS NOT NULL AND deleted_at IS NULL")
    con.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")

    # ---- OTP codes (sms / email) — only HMAC stored ---------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS otp_codes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel      TEXT NOT NULL,           -- 'sms' | 'email'
            target       TEXT NOT NULL,           -- phone or email (lowercase)
            code_hash    TEXT NOT NULL,           -- HMAC-SHA256 of code
            expires_at   TEXT NOT NULL,
            attempts     INTEGER NOT NULL DEFAULT 0,
            consumed_at  TEXT,
            created_at   TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_otp_target ON otp_codes(target, channel)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_otp_expires ON otp_codes(expires_at)")

    # ---- auth attempt throttling ----------------------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_attempts (
            target        TEXT PRIMARY KEY,        -- phone/email/ip
            count         INTEGER NOT NULL DEFAULT 0,
            window_start  TEXT NOT NULL,
            locked_until  TEXT
        )
        """
    )

    # ---- usage periods (per-subscription rolling quota) -----------------
    # PRIMARY KEY = (user_id, period_anchor) so an upsert on renewal is
    # cheap and we never accidentally double-create a period.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_periods (
            user_id        TEXT NOT NULL,
            period_anchor  TEXT NOT NULL,          -- ISO8601 of subscription purchase_date
            plan           TEXT NOT NULL,          -- 'monthly' | 'quarterly' | 'yearly'
            period_start   TEXT NOT NULL,
            period_end     TEXT NOT NULL,
            total          INTEGER NOT NULL,
            used           INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (user_id, period_anchor)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_usage_periods_user "
                "ON usage_periods(user_id, period_end DESC)")

    # ---- usage reservations (two-phase commit for quota) ----------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_reservations (
            id              TEXT PRIMARY KEY,       -- uuid
            user_id         TEXT NOT NULL,
            period_anchor   TEXT NOT NULL,
            status          TEXT NOT NULL,          -- 'pending' | 'committed' | 'rolled_back'
            cost            REAL NOT NULL DEFAULT 1.0,
            request_id      TEXT,
            created_at      TEXT NOT NULL,
            expires_at      TEXT NOT NULL,
            settled_at      TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_reservations_status "
                "ON usage_reservations(status, expires_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_reservations_user "
                "ON usage_reservations(user_id, created_at DESC)")

    # ---- usage records (audit + user-visible history) -------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_records (
            id                  TEXT PRIMARY KEY,
            user_id             TEXT NOT NULL,
            request_id          TEXT NOT NULL,
            status              TEXT NOT NULL,      -- 'pending' | 'charged' | 'refunded' | 'failed'
            charge_at           TEXT,
            refund_at           TEXT,
            step_config         TEXT NOT NULL,      -- JSON
            proposals           TEXT NOT NULL,      -- JSON
            picked_proposal_id  TEXT,
            picked_at           TEXT,
            captured            INTEGER NOT NULL DEFAULT 0,
            captured_at         TEXT,
            model_id            TEXT,
            prompt_tokens       INTEGER,
            completion_tokens   INTEGER,
            cost_usd            REAL,
            error_code          TEXT,
            reservation_id      TEXT,
            created_at          TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_usage_records_user "
                "ON usage_records(user_id, created_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_usage_records_status "
                "ON usage_records(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_usage_records_request "
                "ON usage_records(request_id)")

    # ---- subscription events (admin audit + churn analysis) -------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_events (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                  TEXT NOT NULL,
            type                     TEXT NOT NULL,
            plan                     TEXT,
            product_id               TEXT,
            original_transaction_id  TEXT,
            amount_cny               REAL,
            occurred_at              TEXT NOT NULL,
            payload                  TEXT,
            created_at               TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_sub_events_time "
                "ON subscription_events(occurred_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sub_events_user "
                "ON subscription_events(user_id, occurred_at DESC)")

    # ---- revenue ledger -------------------------------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS revenue_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            plan            TEXT NOT NULL,
            amount_cny      REAL NOT NULL,
            apple_currency  TEXT,
            apple_amount    REAL,
            fx_rate         REAL,
            occurred_at     TEXT NOT NULL,
            source          TEXT NOT NULL,           -- 'asn' | 'verify' | 'manual'
            created_at      TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_revenue_time "
                "ON revenue_ledger(occurred_at DESC)")

    # ---- expense ledger (vendor cost) -----------------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS expense_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor          TEXT NOT NULL,           -- 'openai' | 'gemini' | 'aliyun_sms' | ...
            amount_usd      REAL NOT NULL,
            description     TEXT,
            occurred_at     TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_expense_time "
                "ON expense_ledger(occurred_at DESC)")

    # ---- model settings (single-row + history) --------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS model_settings (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            fast_model_id   TEXT NOT NULL,
            high_model_id   TEXT NOT NULL,
            updated_by      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS model_settings_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fast_model_id   TEXT NOT NULL,
            high_model_id   TEXT NOT NULL,
            changed_by      TEXT NOT NULL,
            changed_at      TEXT NOT NULL,
            reason          TEXT
        )
        """
    )

    # ---- admin audit log ------------------------------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    TEXT NOT NULL,
            action      TEXT NOT NULL,
            target      TEXT,
            payload     TEXT,
            occurred_at TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_audit_admin "
                "ON admin_audit_log(admin_id, occurred_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_audit_time "
                "ON admin_audit_log(occurred_at DESC)")

    # ---- free-tier quota anchored on device (v17b) ----------------------
    # One row per device fingerprint. All accounts on the same iPhone
    # share the same `total - used` budget; no account, no row → no
    # free shot. Anchored on device so a user can't reset by signing
    # up with another phone number. Resets only when admin manually
    # bumps `total` (or device is wiped & re-installed → new fp).
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_free_quota (
            device_fingerprint TEXT PRIMARY KEY,
            total              INTEGER NOT NULL,
            used               INTEGER NOT NULL DEFAULT 0,
            first_user_id      TEXT,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL
        )
        """
    )

    # ---- Per-IP throttle for OTP send (v17b anti-farm) ------------------
    # Distinct from auth_attempts (which is per-target). This counts
    # how many *distinct* targets a single IP has tried to send OTP
    # to within a rolling window.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS otp_ip_attempts (
            ip            TEXT NOT NULL,
            target        TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            PRIMARY KEY (ip, target, created_at)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_otp_ip_time "
                "ON otp_ip_attempts(ip, created_at DESC)")

    # ---- Endpoint config (v17b admin-driven server URL) -----------------
    # Single-row table holding the canonical baseURL all clients
    # should use. Admins update via PUT /admin/endpoint; clients
    # poll GET /api/config/endpoint every 5 min.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_config (
            id              INTEGER PRIMARY KEY,
            primary_url     TEXT NOT NULL,
            fallback_url    TEXT,
            min_app_version TEXT,
            note            TEXT,
            updated_by      TEXT,
            updated_at      TEXT NOT NULL,
            rollout_percentage INTEGER NOT NULL DEFAULT 100
        )
        """
    )
    # v17c — additive: existing rows lack rollout_percentage column
    if not _column_exists(con, "endpoint_config", "rollout_percentage"):
        con.execute("ALTER TABLE endpoint_config "
                    "ADD COLUMN rollout_percentage INTEGER NOT NULL DEFAULT 100")
    # ---- blocklist (v17c anti-abuse) ------------------------------------
    # Single source of truth for "should this request be denied
    # before it costs us money or burns SMS budget?". Scope:
    #   * 'ip'     — block all traffic from a CIDR-free IP
    #   * 'phone'  — block OTP send to + login from a phone number
    #   * 'email'  — same for email
    #   * 'user'   — kill switch for a specific user_id (escalation
    #                 from 'locked' status — locked users can still
    #                 read their data; blocked users get 403)
    # ``expires_at`` NULL = permanent block. Indexed for cheap reads
    # in the request middleware hot path.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS blocklist (
            scope       TEXT NOT NULL,           -- 'ip'|'phone'|'email'|'user'
            value       TEXT NOT NULL,
            reason      TEXT,
            created_by  TEXT,
            created_at  TEXT NOT NULL,
            expires_at  TEXT,
            dry_run     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, value)
        )
        """
    )
    if not _column_exists(con, "blocklist", "dry_run"):
        con.execute("ALTER TABLE blocklist ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0")
    con.execute("CREATE INDEX IF NOT EXISTS idx_blocklist_expires "
                "ON blocklist(expires_at)")

    # ---- runtime settings (v17d — admin-tunable knobs) ------------------
    # Tiny KV for things admin should be able to adjust without a
    # deploy: OTP daily caps, RPM ceilings, per-IP throttles.
    # Service code reads via runtime_settings.get_int(key, default).
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_by  TEXT,
            updated_at  TEXT NOT NULL
        )
        """
    )

    # ---- global rate-limit counters (v17c anti-DDoS) --------------------
    # Bucket = "service:scope:bucket_key:minute". Used by the middleware
    # token-bucket and OTP RPM ceiling. Cheap; rows expire after 24h.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_buckets (
            service      TEXT NOT NULL,
            scope        TEXT NOT NULL,
            bucket_key   TEXT NOT NULL,
            window_start TEXT NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (service, scope, bucket_key, window_start)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_rate_window "
                "ON rate_buckets(window_start)")

    # ---- endpoint telemetry (v17b) --------------------------------------
    # Lightweight: every poll appends one row tagged with the URL the
    # client is *currently* using + an opaque device_fp hash. Admin
    # queries roll it up to "what % of installs are on the new URL?".
    # Sweep older than 24h on every insert (cheap, keeps it bounded).
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_telemetry (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            active_url      TEXT NOT NULL,
            device_fp       TEXT,
            app_version     TEXT,
            reported_at     TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ep_tel_time "
                "ON endpoint_telemetry(reported_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ep_tel_url "
                "ON endpoint_telemetry(active_url, reported_at DESC)")

    # ---- endpoint override audit (v18) ----------------------------------
    # Separate table from endpoint_telemetry on purpose:
    #   - endpoint_telemetry: every poll, "what URL is this install on
    #     right now" -- high volume, GC'd at 24h.
    #   - endpoint_override_audit: only when an Internal-build user
    #     manually changes the override -- low volume, kept 90 days so
    #     support can answer "why can't this device connect?".
    # Source values: "internal_ui" (the only writer right now). Reserve
    # "auto_remote" / "deeplink" for future writers so the schema doesn't
    # need migrating again.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_override_audit (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            device_fp     TEXT,
            old_url       TEXT,
            new_url       TEXT,
            healthz_ok    INTEGER NOT NULL DEFAULT 0,
            source        TEXT NOT NULL DEFAULT 'internal_ui',
            app_version   TEXT,
            reported_at   TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ep_ovr_audit_time "
                "ON endpoint_override_audit(reported_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ep_ovr_audit_device "
                "ON endpoint_override_audit(device_fp, reported_at DESC)")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_config_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_url  TEXT NOT NULL,
            fallback_url TEXT,
            changed_by   TEXT,
            changed_at   TEXT NOT NULL,
            reason       TEXT
        )
        """
    )

    # ---- v18 satisfaction signal ----------------------------------------
    # Three columns on usage_records (additive, idempotent). `satisfied`
    # is INTEGER (0/1) so NULL keeps "no answer" as the default state.
    if not _column_exists(con, "usage_records", "satisfied"):
        con.execute("ALTER TABLE usage_records ADD COLUMN satisfied INTEGER")
    if not _column_exists(con, "usage_records", "satisfied_at"):
        con.execute("ALTER TABLE usage_records ADD COLUMN satisfied_at TEXT")
    if not _column_exists(con, "usage_records", "satisfied_note"):
        con.execute(
            "ALTER TABLE usage_records ADD COLUMN satisfied_note TEXT")
    # v18 s1 — 3-grade fidelity (love / ok / bad). The original
    # `satisfied INTEGER` column collapses love+ok into 1; the grade
    # column lets admin reports show "真爱比例" without re-deriving
    # from `note`.
    if not _column_exists(con, "usage_records", "satisfied_grade"):
        con.execute(
            "ALTER TABLE usage_records ADD COLUMN satisfied_grade TEXT")

    # ---- v18 user_preferences -------------------------------------------
    # Per-user, per-(scene, style) running tally of satisfied/dissatisfied
    # taps. Drives the "## USER_PREFERENCE" prompt slot.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id        TEXT NOT NULL,
            scene_mode     TEXT NOT NULL,
            style_id       TEXT NOT NULL,
            satisfied      INTEGER NOT NULL DEFAULT 0,
            dissatisfied   INTEGER NOT NULL DEFAULT 0,
            last_at        TEXT NOT NULL,
            PRIMARY KEY (user_id, scene_mode, style_id)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_user_pref_user "
                "ON user_preferences(user_id, scene_mode)")

    # ---- v18 satisfaction_aggregates ------------------------------------
    # Anonymous, k-anon-gated rollup. Drives the "## CROSS_USER_TREND"
    # prompt slot when admin flips `pref.global_hint.enabled = true`.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS satisfaction_aggregates (
            scene_mode         TEXT NOT NULL,
            style_id           TEXT NOT NULL,
            satisfied_count    INTEGER NOT NULL DEFAULT 0,
            dissatisfied_count INTEGER NOT NULL DEFAULT 0,
            distinct_users     INTEGER NOT NULL DEFAULT 0,
            updated_at         TEXT NOT NULL,
            PRIMARY KEY (scene_mode, style_id)
        )
        """
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_user(row: sqlite3.Row) -> User:
    keys = row.keys()
    return User(
        id=row["id"],
        apple_sub=row["apple_sub"],
        email=row["email"],
        is_anonymous=bool(row["is_anonymous"]),
        tier=row["tier"] or "free",
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
        deleted_at=_parse(row["deleted_at"]) if row["deleted_at"] else None,
        phone=row["phone"] if "phone" in keys else None,
        role=(row["role"] if "role" in keys else None) or "user",
        status=(row["status"] if "status" in keys else None) or "active",
        device_fingerprint=(row["device_fingerprint"]
                              if "device_fingerprint" in keys else None),
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
    import hashlib  # local import — avoid top-level cycle
    uid = str(uuid.uuid4())
    now = _now()
    fp = (hashlib.sha256(device_id.encode("utf-8")).hexdigest()
          if device_id else None)
    with _connect() as con:
        con.execute(
            "INSERT INTO users (id, is_anonymous, tier, device_fingerprint, "
            "created_at, updated_at) VALUES (?, 1, 'free', ?, ?, ?)",
            (uid, fp, now, now),
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


def set_role(user_id: str, role: str) -> None:
    """v17 — role-based admin. Tier is independent of role; admin
    accounts typically also carry tier='pro' for UI badge consistency,
    but authorisation MUST gate on role."""
    assert role in ("user", "admin"), role
    with _connect() as con:
        con.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
            (role, _now(), user_id),
        )


def get_by_phone(phone: str) -> Optional[User]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM users WHERE phone = ? AND deleted_at IS NULL",
            (phone,),
        ).fetchone()
    return _row_to_user(row) if row else None


def get_by_email(email: str) -> Optional[User]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM users WHERE email = ? AND deleted_at IS NULL",
            (email.lower(),),
        ).fetchone()
    return _row_to_user(row) if row else None


def create_user(*, phone: Optional[str] = None, email: Optional[str] = None,
                role: str = "user", tier: str = "free",
                device_fingerprint: Optional[str] = None) -> User:
    """Create a non-anonymous user keyed by phone OR email (one is required).

    `device_fingerprint` (sha256 of the iOS Keychain device_id) is
    optional but strongly recommended — without it the free-quota
    bucket falls back to per-user, defeating the anti-farm guarantee."""
    assert phone or email, "phone or email required"
    assert role in ("user", "admin")
    assert tier in ("free", "pro")
    uid = str(uuid.uuid4())
    now = _now()
    with _connect() as con:
        con.execute(
            "INSERT INTO users (id, phone, email, is_anonymous, tier, role, status, "
            "device_fingerprint, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?, 'active', ?, ?, ?)",
            (uid, phone, email.lower() if email else None, tier, role,
             device_fingerprint, now, now),
        )
    log.info("user_repo: created user id=%s role=%s has_phone=%s has_email=%s "
             "has_device_fp=%s",
             uid, role, bool(phone), bool(email), bool(device_fingerprint))
    return get_user(uid)  # type: ignore[return-value]


def set_device_fingerprint(user_id: str, fp: str) -> None:
    """Backfill device_fingerprint for an existing user (e.g. when an
    older account logs in from an iOS build that finally sends X-Device-Id)."""
    with _connect() as con:
        con.execute(
            "UPDATE users SET device_fingerprint = ?, updated_at = ? WHERE id = ?",
            (fp, _now(), user_id),
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
        # v18 — wipe per-user style preference rows. Aggregate counts
        # in `satisfaction_aggregates` are left untouched (they're
        # anonymous totals, not personal data).
        try:
            con.execute(
                "DELETE FROM user_preferences WHERE user_id = ?",
                (user_id,),
            )
        except sqlite3.OperationalError:
            # Table may not exist yet on a never-migrated db.
            pass

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
