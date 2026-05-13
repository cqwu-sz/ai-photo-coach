"""Per-subscription rolling-period usage quota with two-phase commit
(PR5 of subscription/auth rework).

Why two-phase?
    The user-facing rule is "出片方案出来才扣次数". A 5xx upstream
    error (Gemini timeout, OOM, etc.) MUST NOT consume the user's
    monthly budget. We do this by reserving a slot up front (atomic
    decrement in `usage_periods`) and only committing after
    `analyze_service.run()` returns successfully. Failure rolls the
    reservation back. A janitor coroutine sweeps abandoned
    reservations every 60 seconds in case the worker crashed before
    we could rollback explicitly.

Why anchor on Apple's purchase_date?
    PR4 already chose the active subscription. We tie the quota
    period to its `purchase_date` so renewing or upgrading creates a
    NEW period (and a brand-new 100/500/2000 budget). Old periods are
    not touched — Apple's `expires_at` invalidates the old sub, our
    `_evaluate_tier` now picks the new one, and the next reserve
    lands in a fresh row.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status

from . import user_repo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESERVATION_TTL_SEC = 5 * 60       # janitor reclaims pending after 5 min
ADMIN_RESERVATION_ID = "admin"     # sentinel — never written to DB
FREE_RESERVATION_PREFIX = "free:"  # reservation_id format: free:<uuid>
FREE_TIER_TOTAL = 5                # lifetime free shots per device fp
FREE_TIER_PLAN_NAME = "free"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PeriodSnapshot:
    plan: str
    period_start: datetime
    period_end: datetime
    total: int
    used: int

    @property
    def remaining(self) -> int:
        return max(self.total - self.used, 0)


@dataclass
class ReservationResult:
    reservation_id: Optional[str]   # None = unlimited (admin) or no quota
    snapshot: Optional[PeriodSnapshot]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _iap_plan_total(product_id: str) -> Optional[tuple[str, int]]:
    from ..config import get_settings
    return get_settings().iap_plan_map.get(product_id)


def _resolve_active_plan(user_id: str) -> Optional[tuple[str, int, user_repo.Subscription]]:
    """Re-run the PR4 selection rule, scoped to this module so we don't
    depend on the iap api layer (which would create an import cycle).
    Returns (plan, total, subscription) or None when there's no active
    pro sub."""
    pros = _pro_product_ids()
    active = user_repo.list_active_subscriptions(user_id)
    rank = {"yearly": 3, "quarterly": 2, "monthly": 1}
    best: Optional[tuple[int, str, int, user_repo.Subscription]] = None
    for s in active:
        if s.product_id not in pros:
            continue
        meta = _iap_plan_total(s.product_id)
        if meta is None:
            continue
        plan, total = meta
        r = rank.get(plan, 0)
        # Tie-break on purchase_date so a renewal (same plan, fresher
        # purchase_date) always wins over the previous period.
        if best is None or r > best[0] or (r == best[0]
                                            and s.purchase_date
                                                > best[3].purchase_date):
            best = (r, plan, total, s)
    if best is None:
        return None
    return best[1], best[2], best[3]


def _pro_product_ids() -> set[str]:
    from ..config import get_settings
    raw = (get_settings().apple_iap_pro_product_ids or "").strip()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _expiry_or_max(s: user_repo.Subscription) -> datetime:
    return s.expires_at or datetime.max.replace(tzinfo=timezone.utc)


def _ensure_period(con: sqlite3.Connection, *, user_id: str,
                    plan: str, total: int,
                    sub: user_repo.Subscription) -> PeriodSnapshot:
    """Upsert the current period row keyed by (user_id, sub.purchase_date).

    A renewal or upgrade lands on a different purchase_date, which
    creates a brand-new row with `used=0` — that's how "过期/续订
    重置" lands automatically."""
    anchor = _iso(sub.purchase_date)
    period_end = sub.expires_at or (sub.purchase_date + _default_period_for(plan))
    row = con.execute(
        "SELECT plan, period_start, period_end, total, used FROM usage_periods "
        "WHERE user_id = ? AND period_anchor = ?",
        (user_id, anchor),
    ).fetchone()
    now_iso = _iso(_now())
    if row is None:
        con.execute(
            "INSERT INTO usage_periods (user_id, period_anchor, plan, "
            "period_start, period_end, total, used, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (user_id, anchor, plan, _iso(sub.purchase_date),
             _iso(period_end), total, now_iso, now_iso),
        )
        return PeriodSnapshot(plan=plan, period_start=sub.purchase_date,
                               period_end=period_end, total=total, used=0)
    return PeriodSnapshot(
        plan=row[0],
        period_start=_parse(row[1]) or sub.purchase_date,
        period_end=_parse(row[2]) or period_end,
        total=int(row[3]),
        used=int(row[4]),
    )


def _default_period_for(plan: str) -> timedelta:
    if plan == "yearly":
        return timedelta(days=365)
    if plan == "quarterly":
        return timedelta(days=90)
    return timedelta(days=30)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _device_fp_for(user_id: str) -> Optional[str]:
    u = user_repo.get_user(user_id)
    return u.device_fingerprint if u else None


def _free_snapshot(con: sqlite3.Connection, fp: str) -> PeriodSnapshot:
    """Read-or-init the per-device free bucket. Period_start = first
    sighting of the device; period_end = +100y (effectively lifetime)."""
    row = con.execute(
        "SELECT total, used, created_at FROM usage_free_quota "
        "WHERE device_fingerprint = ?",
        (fp,),
    ).fetchone()
    if row is None:
        return PeriodSnapshot(
            plan=FREE_TIER_PLAN_NAME,
            period_start=_now(), period_end=_now() + timedelta(days=365 * 100),
            total=FREE_TIER_TOTAL, used=0,
        )
    created = _parse(row[2]) or _now()
    return PeriodSnapshot(
        plan=FREE_TIER_PLAN_NAME,
        period_start=created,
        period_end=created + timedelta(days=365 * 100),
        total=int(row[0]), used=int(row[1]),
    )


def get_period(user_id: str, *, role: str = "user") -> Optional[PeriodSnapshot]:
    """Cheap read for /me/quota — does not reserve anything."""
    if role == "admin":
        return PeriodSnapshot(plan="admin",
                               period_start=_now(),
                               period_end=_now() + timedelta(days=365),
                               total=10**9, used=0)
    resolved = _resolve_active_plan(user_id)
    if resolved is not None:
        plan, total, sub = resolved
        with user_repo._connect() as con:                           # noqa: SLF001
            return _ensure_period(con, user_id=user_id, plan=plan,
                                    total=total, sub=sub)
    # Free tier — show the lifetime device bucket if we have an fp.
    fp = _device_fp_for(user_id)
    if not fp:
        return None
    with user_repo._connect() as con:                               # noqa: SLF001
        return _free_snapshot(con, fp)


def reserve(user_id: str, *, role: str = "user",
            cost: float = 1.0,
            request_id: Optional[str] = None) -> ReservationResult:
    """Atomically reserve ``cost`` units from the user's current period.

    Returns:
      - admin → ReservationResult(reservation_id="admin", snapshot=None)
      - free  → ReservationResult(None, None)  (no quota gate)
      - pro   → ReservationResult(reservation_id=<uuid>, snapshot=...)

    Raises HTTP 402 ``quota_exhausted`` when the bucket is empty.
    """
    if role == "admin":
        return ReservationResult(reservation_id=ADMIN_RESERVATION_ID,
                                  snapshot=None)
    resolved = _resolve_active_plan(user_id)
    if resolved is None:
        # Free user — gated by the per-device 5-shot bucket (PR13).
        # Anchored on device_fingerprint, NOT user_id, so a 2nd
        # account on the same iPhone shares the same budget.
        return _reserve_free(user_id, cost=cost, request_id=request_id)

    plan, total, sub = resolved
    res_id = str(uuid.uuid4())
    now_iso = _iso(_now())
    expires_iso = _iso(_now() + timedelta(seconds=RESERVATION_TTL_SEC))
    snapshot: Optional[PeriodSnapshot] = None

    with user_repo._connect() as con:                               # noqa: SLF001
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            # Already in a transaction (older sqlite); fall through.
            pass
        snapshot = _ensure_period(con, user_id=user_id, plan=plan,
                                    total=total, sub=sub)
        if snapshot.used + cost > snapshot.total:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"error": {"code": "quota_exhausted",
                                   "message": "本周期次数已用尽，请等待重置或升级套餐。",
                                   "plan": snapshot.plan,
                                   "total": snapshot.total,
                                   "used": snapshot.used,
                                   "period_end": _iso(snapshot.period_end)}},
            )
        new_used = int(snapshot.used + cost)
        con.execute(
            "UPDATE usage_periods SET used = ?, updated_at = ? "
            "WHERE user_id = ? AND period_anchor = ?",
            (new_used, now_iso, user_id, _iso(sub.purchase_date)),
        )
        snapshot = PeriodSnapshot(
            plan=snapshot.plan, period_start=snapshot.period_start,
            period_end=snapshot.period_end, total=snapshot.total,
            used=new_used,
        )
        con.execute(
            "INSERT INTO usage_reservations (id, user_id, period_anchor, "
            "status, cost, request_id, created_at, expires_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
            (res_id, user_id, _iso(sub.purchase_date), float(cost),
             request_id, now_iso, expires_iso),
        )
        con.commit()
    return ReservationResult(reservation_id=res_id, snapshot=snapshot)


def _reserve_free(user_id: str, *, cost: float,
                  request_id: Optional[str]) -> ReservationResult:
    """Free-tier reserve. Anchored on the device_fingerprint, so all
    accounts on the same physical iPhone share the same 5-shot bucket.

    If the user has no fp on file (legacy iOS build, or anonymous
    flow before PR13), we can't safely anchor — refuse rather than
    silently giving a free shot. The iOS app must send X-Device-Id
    on /auth/otp/verify (and SIWA must include device_id) for a
    fresh account to be usable."""
    fp = _device_fp_for(user_id)
    if not fp:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"error": {"code": "free_quota_no_device",
                              "message": "请升级 App 到最新版本或重新登录后再试。",
                              "plan": FREE_TIER_PLAN_NAME, "total": 0, "used": 0}},
        )
    res_id = FREE_RESERVATION_PREFIX + str(uuid.uuid4())
    now_iso = _iso(_now())
    expires_iso = _iso(_now() + timedelta(seconds=RESERVATION_TTL_SEC))
    with user_repo._connect() as con:                               # noqa: SLF001
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass
        # Upsert with current totals if first sighting.
        existing = con.execute(
            "SELECT total, used FROM usage_free_quota WHERE device_fingerprint = ?",
            (fp,),
        ).fetchone()
        if existing is None:
            con.execute(
                "INSERT INTO usage_free_quota (device_fingerprint, total, used, "
                "first_user_id, created_at, updated_at) "
                "VALUES (?, ?, 0, ?, ?, ?)",
                (fp, FREE_TIER_TOTAL, user_id, now_iso, now_iso),
            )
            total, used = FREE_TIER_TOTAL, 0
        else:
            total, used = int(existing[0]), int(existing[1])
        if used + cost > total:
            con.commit()  # release the BEGIN IMMEDIATE before raising
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"error": {"code": "free_quota_exhausted",
                                  "message": "免费体验次数已用完，订阅后可继续使用。",
                                  "plan": FREE_TIER_PLAN_NAME,
                                  "total": total, "used": used}},
            )
        new_used = int(used + cost)
        con.execute(
            "UPDATE usage_free_quota SET used = ?, updated_at = ? "
            "WHERE device_fingerprint = ?",
            (new_used, now_iso, fp),
        )
        con.execute(
            "INSERT INTO usage_reservations (id, user_id, period_anchor, status, "
            "cost, request_id, created_at, expires_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
            (res_id, user_id, FREE_RESERVATION_PREFIX + fp, float(cost),
             request_id, now_iso, expires_iso),
        )
        con.commit()
    snap = PeriodSnapshot(
        plan=FREE_TIER_PLAN_NAME, period_start=_now(),
        period_end=_now() + timedelta(days=365 * 100),
        total=total, used=new_used,
    )
    return ReservationResult(reservation_id=res_id, snapshot=snap)


def commit(reservation_id: Optional[str]) -> None:
    """Mark a reservation as final. No-op for admin / unknown."""
    if reservation_id is None or reservation_id == ADMIN_RESERVATION_ID:
        return
    now_iso = _iso(_now())
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_reservations SET status = 'committed', settled_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now_iso, reservation_id),
        )


def rollback(reservation_id: Optional[str]) -> None:
    """Refund a previously reserved slot back into the user's period."""
    if reservation_id is None or reservation_id == ADMIN_RESERVATION_ID:
        return
    now_iso = _iso(_now())
    with user_repo._connect() as con:                               # noqa: SLF001
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass
        row = con.execute(
            "SELECT user_id, period_anchor, cost, status "
            "FROM usage_reservations WHERE id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            return
        user_id, anchor, cost, status_ = row
        if status_ != "pending":
            return
        if reservation_id.startswith(FREE_RESERVATION_PREFIX):
            fp = anchor[len(FREE_RESERVATION_PREFIX):]
            con.execute(
                "UPDATE usage_free_quota SET used = MAX(used - ?, 0), "
                "updated_at = ? WHERE device_fingerprint = ?",
                (int(cost), now_iso, fp),
            )
        else:
            con.execute(
                "UPDATE usage_periods SET used = MAX(used - ?, 0), updated_at = ? "
                "WHERE user_id = ? AND period_anchor = ?",
                (int(cost), now_iso, user_id, anchor),
            )
        con.execute(
            "UPDATE usage_reservations SET status = 'rolled_back', settled_at = ? "
            "WHERE id = ?",
            (now_iso, reservation_id),
        )
        con.commit()


def attach_request(reservation_id: Optional[str], request_id: str) -> None:
    if not reservation_id or reservation_id == ADMIN_RESERVATION_ID:
        return
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "UPDATE usage_reservations SET request_id = ? WHERE id = ?",
            (request_id, reservation_id),
        )


def sweep_expired() -> int:
    """Reclaim slots whose reservation has been pending past the TTL.

    Called periodically by the lifespan-managed janitor. Returns the
    number of reservations rolled back."""
    cutoff = _iso(_now())
    victims: list[str] = []
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT id FROM usage_reservations "
            "WHERE status = 'pending' AND expires_at < ?",
            (cutoff,),
        ).fetchall()
        victims = [r[0] for r in rows]
    for rid in victims:
        try:
            rollback(rid)
        except Exception as e:                                       # noqa: BLE001
            log.warning("usage_quota.sweep_expired: rollback failed id=%s err=%s",
                        rid, e)
    if victims:
        log.info("usage_quota.sweep_expired: rolled back %d stale reservations",
                 len(victims))
    return len(victims)


def reset_for_tests() -> None:
    """Wipe all quota / reservation rows. Tests only."""
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute("DELETE FROM usage_reservations")
        con.execute("DELETE FROM usage_periods")
        con.execute("DELETE FROM usage_free_quota")
        con.commit()


__all__ = [
    "PeriodSnapshot", "ReservationResult",
    "get_period", "reserve", "commit", "rollback",
    "attach_request", "sweep_expired", "reset_for_tests",
    "RESERVATION_TTL_SEC", "ADMIN_RESERVATION_ID",
]
