"""Admin-only endpoints (PR8/PR9 of subscription/auth rework).

All routes here are gated by `auth_svc.require_admin`. Audit log
entries are written by the dependency-style helper `_audit` so a
forgetful caller can't bypass the trail.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import auth as auth_svc
from ..services import blocklist as blocklist_svc
from ..services import endpoint_config as ep_svc
from ..services import model_config, runtime_settings as rs_svc, user_repo

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


def _redact_ip(ip: str) -> Optional[str]:
    """v17d — PIPL-friendly redaction. Audit logs are useful for
    "an admin from /24 X did Y" but we don't need the last octet.
    IPv4 → keep first 3 octets, mask last as 0; IPv6 → keep /48
    prefix. Falls back to None for malformed input."""
    if not ip:
        return None
    try:
        from ipaddress import ip_address
        addr = ip_address(ip)
    except ValueError:
        return None
    if addr.version == 4:
        parts = ip.split(".")
        if len(parts) != 4:
            return None
        return ".".join(parts[:3] + ["0"]) + "/24"
    # IPv6 — keep first three hextets (/48 is RIR allocation boundary).
    parts = addr.exploded.split(":")
    return ":".join(parts[:3] + ["0"] * 5) + "/48"


def _request_meta(request: Request) -> dict:
    """Capture caller IP + UA for high-sensitivity admin actions
    (endpoint switch, role grant). Trust X-Forwarded-For when set;
    we control the LB at the edge. v17d: IP is redacted to /24
    (IPv4) or /48 (IPv6) before persistence to comply with PIPL."""
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host or ""
    return {
        "client_ip": _redact_ip(ip),
        "user_agent": request.headers.get("user-agent"),
    }


def _audit(admin_id: str, action: str, *,
           target: Optional[str] = None,
           payload: Optional[dict] = None) -> None:
    """Thin wrapper. Real implementation moved to
    `services.admin_audit.write` in v17e so non-admin paths
    (auth/IAP/data export) can audit without importing the API
    layer. Keep this shim so existing callers don't churn."""
    from ..services import admin_audit
    admin_audit.write(admin_id, action, target=target, payload=payload)


# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------


class ModelChoiceOut(BaseModel):
    fast_model_id: str
    high_model_id: str
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class ModelChoiceIn(BaseModel):
    fast_model_id: str = Field(min_length=1, max_length=128)
    high_model_id: str = Field(min_length=1, max_length=128)
    reason: Optional[str] = Field(default=None, max_length=500)


@router.get("/admin/model", response_model=ModelChoiceOut)
async def get_model(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> ModelChoiceOut:
    choice = model_config.get_current()
    return ModelChoiceOut(
        fast_model_id=choice.fast_model_id,
        high_model_id=choice.high_model_id,
        updated_by=choice.updated_by,
        updated_at=choice.updated_at,
    )


@router.put("/admin/model", response_model=ModelChoiceOut)
async def put_model(
    payload: ModelChoiceIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> ModelChoiceOut:
    choice = model_config.save(
        fast_model_id=payload.fast_model_id,
        high_model_id=payload.high_model_id,
        admin_id=user.id,
        reason=payload.reason,
    )
    audit_payload = payload.model_dump()
    audit_payload.update(_request_meta(request))
    _audit(user.id, "model_config.save", payload=audit_payload)
    return ModelChoiceOut(
        fast_model_id=choice.fast_model_id,
        high_model_id=choice.high_model_id,
        updated_by=choice.updated_by,
        updated_at=choice.updated_at,
    )


@router.get("/admin/model/history")
async def get_model_history(
    limit: int = 50,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    return {"items": model_config.list_history(limit=limit)}


# ---------------------------------------------------------------------------
# Users (lightweight — full table comes in PR9)
# ---------------------------------------------------------------------------


class GrantProIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/admin/users/{user_id}/grant_pro")
async def grant_pro(
    user_id: str,
    payload: GrantProIn,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    target = user_repo.get_user(user_id)
    if target is None:
        raise HTTPException(404, {"error": {"code": "user_not_found"}})
    user_repo.set_tier(user_id, "pro")
    _audit(user.id, "user.grant_pro", target=user_id,
            payload={"reason": payload.reason})
    return {"ok": True}


class SetRoleIn(BaseModel):
    role: str = Field(pattern="^(user|admin)$")
    reason: Optional[str] = Field(default=None, max_length=500)


@router.put("/admin/users/{user_id}/role")
async def set_role(
    user_id: str,
    payload: SetRoleIn,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    target = user_repo.get_user(user_id)
    if target is None:
        raise HTTPException(404, {"error": {"code": "user_not_found"}})
    user_repo.set_role(user_id, payload.role)
    _audit(user.id, "user.set_role", target=user_id,
            payload=payload.model_dump())
    return {"ok": True, "role": payload.role}


# ---------------------------------------------------------------------------
# Audit & metrics
# ---------------------------------------------------------------------------


def _resolve_window(since: Optional[str], until: Optional[str],
                     default_hours: int = 24) -> tuple[datetime, datetime]:
    """Parse ISO-8601 since/until or default to a rolling window."""
    now = datetime.now(timezone.utc)
    end = _parse_iso(until) or now
    start = _parse_iso(since) or (end - _timedelta(hours=default_hours))
    if start > end:
        start, end = end, start
    return start, end


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _timedelta(*, hours: int = 0) -> Any:
    from datetime import timedelta as _td
    return _td(hours=hours)


class AuditSummaryOut(BaseModel):
    since: datetime
    until: datetime
    new_subscriptions: int
    new_subscriptions_by_plan: dict[str, int]
    revenue_cny_gross: float
    revenue_cny_net: float
    analyze_total: int
    analyze_failed: int
    analyze_charged: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    active_users: int


@router.get("/admin/audit/summary", response_model=AuditSummaryOut)
async def audit_summary(
    since: Optional[str] = None,
    until: Optional[str] = None,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> AuditSummaryOut:
    start, end = _resolve_window(since, until)
    s_iso, e_iso = start.isoformat(), end.isoformat()
    settings = get_settings()
    price_map = settings.iap_price_map
    plan_map = settings.iap_plan_map
    commission = settings.apple_iap_commission_rate

    with user_repo._connect() as con:                               # noqa: SLF001
        subs_rows = con.execute(
            "SELECT product_id, COUNT(*) FROM subscriptions "
            "WHERE purchase_date >= ? AND purchase_date < ? "
            "GROUP BY product_id",
            (s_iso, e_iso),
        ).fetchall()

        analyze_rows = con.execute(
            "SELECT status, COUNT(*), "
            "COALESCE(SUM(prompt_tokens),0), "
            "COALESCE(SUM(completion_tokens),0), "
            "COALESCE(SUM(cost_usd),0.0) "
            "FROM usage_records WHERE created_at >= ? AND created_at < ? "
            "GROUP BY status",
            (s_iso, e_iso),
        ).fetchall()

        active_users = con.execute(
            "SELECT COUNT(DISTINCT user_id) FROM usage_records "
            "WHERE created_at >= ? AND created_at < ?",
            (s_iso, e_iso),
        ).fetchone()[0]

    new_subscriptions = sum(int(r[1]) for r in subs_rows)
    by_plan: dict[str, int] = {}
    revenue_gross = 0.0
    for product_id, cnt in subs_rows:
        plan = plan_map.get(product_id, ("unknown", 0))[0]
        by_plan[plan] = by_plan.get(plan, 0) + int(cnt)
        revenue_gross += float(cnt) * price_map.get(product_id, 0.0)
    revenue_net = revenue_gross * (1.0 - commission)

    analyze_total = analyze_charged = analyze_failed = 0
    prompt_tokens = completion_tokens = 0
    cost_usd = 0.0
    for status_, cnt, p, c, cost in analyze_rows:
        analyze_total += int(cnt)
        if status_ == "charged":
            analyze_charged += int(cnt)
        elif status_ == "failed":
            analyze_failed += int(cnt)
        prompt_tokens += int(p)
        completion_tokens += int(c)
        cost_usd += float(cost)

    return AuditSummaryOut(
        since=start, until=end,
        new_subscriptions=new_subscriptions,
        new_subscriptions_by_plan=by_plan,
        revenue_cny_gross=round(revenue_gross, 2),
        revenue_cny_net=round(revenue_net, 2),
        analyze_total=analyze_total,
        analyze_failed=analyze_failed,
        analyze_charged=analyze_charged,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=round(cost_usd, 4),
        active_users=int(active_users or 0),
    )


class AuditSeriesPoint(BaseModel):
    bucket_start: datetime
    new_subscriptions: int
    revenue_cny_gross: float
    analyze_charged: int
    analyze_failed: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class AuditSeriesOut(BaseModel):
    bucket: str
    points: list[AuditSeriesPoint]


_BUCKET_FORMATS = {
    "hour": "%Y-%m-%dT%H:00:00+00:00",
    "day":  "%Y-%m-%dT00:00:00+00:00",
}


@router.get("/admin/audit/series", response_model=AuditSeriesOut)
async def audit_series(
    since: Optional[str] = None,
    until: Optional[str] = None,
    bucket: str = "hour",
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> AuditSeriesOut:
    if bucket not in _BUCKET_FORMATS:
        raise HTTPException(400, {"error": {"code": "bad_bucket",
                                              "message": "bucket must be hour|day"}})
    start, end = _resolve_window(since, until,
                                   default_hours=168 if bucket == "day" else 24)
    s_iso, e_iso = start.isoformat(), end.isoformat()
    settings = get_settings()
    price_map = settings.iap_price_map

    fmt = _BUCKET_FORMATS[bucket]
    points: dict[str, dict] = {}

    with user_repo._connect() as con:                               # noqa: SLF001
        for product_id, purchase_at in con.execute(
            "SELECT product_id, purchase_date FROM subscriptions "
            "WHERE purchase_date >= ? AND purchase_date < ?",
            (s_iso, e_iso),
        ):
            key = _truncate_iso(purchase_at, bucket)
            p = points.setdefault(key, _empty_point())
            p["new_subscriptions"] += 1
            p["revenue_cny_gross"] += price_map.get(product_id, 0.0)

        for status_, created_at, pt, ct, cost in con.execute(
            "SELECT status, created_at, "
            "COALESCE(prompt_tokens,0), COALESCE(completion_tokens,0), "
            "COALESCE(cost_usd, 0.0) FROM usage_records "
            "WHERE created_at >= ? AND created_at < ?",
            (s_iso, e_iso),
        ):
            key = _truncate_iso(created_at, bucket)
            p = points.setdefault(key, _empty_point())
            if status_ == "charged":
                p["analyze_charged"] += 1
            elif status_ == "failed":
                p["analyze_failed"] += 1
            p["prompt_tokens"] += int(pt)
            p["completion_tokens"] += int(ct)
            p["cost_usd"] += float(cost)

    series = []
    for key in sorted(points.keys()):
        bucket_start = datetime.fromisoformat(key)
        p = points[key]
        series.append(AuditSeriesPoint(
            bucket_start=bucket_start,
            new_subscriptions=p["new_subscriptions"],
            revenue_cny_gross=round(p["revenue_cny_gross"], 2),
            analyze_charged=p["analyze_charged"],
            analyze_failed=p["analyze_failed"],
            prompt_tokens=p["prompt_tokens"],
            completion_tokens=p["completion_tokens"],
            cost_usd=round(p["cost_usd"], 4),
        ))
    return AuditSeriesOut(bucket=bucket, points=series)


def _truncate_iso(s: str, bucket: str) -> str:
    dt = _parse_iso(s) or datetime.now(timezone.utc)
    if bucket == "hour":
        dt = dt.replace(minute=0, second=0, microsecond=0)
    else:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.isoformat()


def _empty_point() -> dict:
    return {"new_subscriptions": 0, "revenue_cny_gross": 0.0,
            "analyze_charged": 0, "analyze_failed": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}


class AuditUserRow(BaseModel):
    user_id: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    last_at: Optional[datetime] = None


@router.get("/admin/audit/users")
async def audit_users(
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    start, end = _resolve_window(since, until)
    s_iso, e_iso = start.isoformat(), end.isoformat()
    limit = max(1, min(limit, 500))
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT user_id, COUNT(*), "
            "COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            "COALESCE(SUM(cost_usd),0.0), MAX(created_at) "
            "FROM usage_records "
            "WHERE created_at >= ? AND created_at < ? "
            "GROUP BY user_id ORDER BY SUM(cost_usd) DESC LIMIT ?",
            (s_iso, e_iso, limit),
        ).fetchall()
    items = [AuditUserRow(
        user_id=r[0], requests=int(r[1]),
        prompt_tokens=int(r[2]), completion_tokens=int(r[3]),
        cost_usd=round(float(r[4]), 4),
        last_at=_parse_iso(r[5]),
    ) for r in rows]
    return {"items": [i.model_dump() for i in items],
             "since": start.isoformat(), "until": end.isoformat()}


@router.get("/admin/audit/log")
async def audit_log(
    limit: int = 100,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    target: Optional[str] = None,
    since_hours: Optional[int] = None,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17e — filterable audit search.
    `action` accepts an exact match or a `prefix.*` pattern (e.g.
    `iap.asn.*` to see all Apple webhooks)."""
    limit = max(1, min(limit, 500))
    where: list[str] = []
    args: list = []
    if action:
        if action.endswith(".*"):
            where.append("action LIKE ?"); args.append(action[:-1] + "%")
        else:
            where.append("action = ?"); args.append(action)
    if actor:
        where.append("admin_id = ?"); args.append(actor)
    if target:
        where.append("target = ?"); args.append(target)
    if since_hours and since_hours > 0:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=min(since_hours, 24 * 90))).isoformat()
        where.append("occurred_at >= ?"); args.append(cutoff)
    sql = ("SELECT id, admin_id, action, target, payload, occurred_at "
           "FROM admin_audit_log")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r[4]) if r[4] else None
        except ValueError:
            payload = r[4]
        out.append({
            "id": r[0], "admin_id": r[1], "action": r[2],
            "target": r[3], "payload": payload, "occurred_at": r[5],
        })
    return {"items": out}


@router.get("/admin/audit/summary")
async def audit_summary(
    since_hours: int = 24,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17e — at-a-glance count by action over the window. Lets
    admin spot abnormal spikes (e.g. 50× iap.asn.refund in 1h =
    customer-service hit fraud)."""
    since_hours = max(1, min(since_hours, 24 * 90))
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=since_hours)).isoformat()
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT action, COUNT(*), MIN(occurred_at), MAX(occurred_at) "
            "FROM admin_audit_log WHERE occurred_at >= ? "
            "GROUP BY action ORDER BY COUNT(*) DESC",
            (cutoff,),
        ).fetchall()
    return {
        "since_hours": since_hours,
        "items": [{"action": r[0], "count": int(r[1]),
                    "first_at": r[2], "last_at": r[3]} for r in rows],
    }


@router.get("/admin/audit/recent_logins")
async def audit_recent_logins(
    limit: int = 50,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17e — most recent successful logins (admin + user). Quick
    sanity scan: any admin login from an unfamiliar IP/UA?"""
    limit = max(1, min(limit, 200))
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT id, admin_id, action, target, payload, occurred_at "
            "FROM admin_audit_log "
            "WHERE action IN ('auth.login_success', "
            "'auth.admin_login_success') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r[4]) if r[4] else {}
        except ValueError:
            p = {}
        out.append({
            "id": r[0], "user_id": r[3],
            "channel": p.get("channel"),
            "client_ip": p.get("client_ip"),
            "user_agent": p.get("user_agent"),
            "is_admin": r[2] == "auth.admin_login_success",
            "occurred_at": r[5],
        })
    return {"items": out}


@router.get("/admin/active_devices")
async def active_devices(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    hours: int = 24,
) -> dict:
    """v17e — count of distinct devices that polled
    /api/config/endpoint in the window. Cheap proxy for "how many
    apps are alive right now?". Doesn't need user login."""
    hours = max(1, min(hours, 24 * 7))
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=hours)).isoformat()
    with user_repo._connect() as con:                               # noqa: SLF001
        total = con.execute(
            "SELECT COUNT(DISTINCT device_fp) FROM endpoint_telemetry "
            "WHERE reported_at >= ?", (cutoff,),
        ).fetchone()[0]
        by_app = con.execute(
            "SELECT app_version, COUNT(DISTINCT device_fp) "
            "FROM endpoint_telemetry WHERE reported_at >= ? "
            "GROUP BY app_version ORDER BY 2 DESC",
            (cutoff,),
        ).fetchall()
    return {
        "window_hours": hours,
        "total_devices": int(total or 0),
        "by_app_version": [{"app_version": r[0] or "unknown",
                              "devices": int(r[1])} for r in by_app],
    }


@router.get("/admin/anomaly_summary")
async def anomaly_summary(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    hours: int = 24,
) -> dict:
    """v17e — single dashboard call that rolls up everything an
    on-call admin wants to glance at after an incident: refunds,
    permanent locks, blocklist enforce hits, OTP throttle hits,
    rollback events."""
    hours = max(1, min(hours, 24 * 30))
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=hours)).isoformat()
    actions_of_interest = [
        "iap.asn.refund",
        "iap.asn.revoke",
        "iap.asn.expired",
        "otp.permanent_lock",
        "endpoint_config.save",   # filtered later for is_rollback
        "user.soft_delete",
        "user.data_export",
    ]
    with user_repo._connect() as con:                               # noqa: SLF001
        placeholders = ",".join(["?"] * len(actions_of_interest))
        rows = con.execute(
            f"SELECT action, payload, occurred_at FROM admin_audit_log "
            f"WHERE action IN ({placeholders}) AND occurred_at >= ? "
            f"ORDER BY occurred_at DESC",
            (*actions_of_interest, cutoff),
        ).fetchall()
        # blocklist enforce hits (rolling 1h windows summed).
        bl_rows = con.execute(
            "SELECT scope, SUM(count) FROM rate_buckets "
            "WHERE service = 'blocklist_enforce' AND window_start >= ? "
            "GROUP BY scope",
            (cutoff,),
        ).fetchall()
    counters: dict[str, int] = {}
    rollbacks: list[dict] = []
    for action, payload, occurred_at in rows:
        if action == "endpoint_config.save":
            try:
                p = json.loads(payload) if payload else {}
            except ValueError:
                p = {}
            if p.get("is_rollback"):
                counters["endpoint.rollback"] = counters.get("endpoint.rollback", 0) + 1
                rollbacks.append({"occurred_at": occurred_at,
                                    "primary_url": p.get("primary_url"),
                                    "previous": p.get("_previous")})
        else:
            counters[action] = counters.get(action, 0) + 1
    return {
        "window_hours": hours,
        "counts": counters,
        "blocklist_enforce_by_scope": {r[0]: int(r[1] or 0) for r in bl_rows},
        "recent_rollbacks": rollbacks[:10],
    }


# ---------------------------------------------------------------------------
# Endpoint config (v17b — admin-driven server URL switch)
# ---------------------------------------------------------------------------


class EndpointAdminOut(BaseModel):
    primary_url: str
    fallback_url: Optional[str] = None
    min_app_version: Optional[str] = None
    note: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: datetime
    rollout_percentage: int = 100


class EndpointAdminIn(BaseModel):
    primary_url: str = Field(min_length=8, max_length=512)
    fallback_url: Optional[str] = Field(default=None, max_length=512)
    min_app_version: Optional[str] = Field(default=None, max_length=32)
    note: Optional[str] = Field(default=None, max_length=500)
    reason: Optional[str] = Field(default=None, max_length=500)
    rollout_percentage: int = Field(default=100, ge=0, le=100)


@router.get("/admin/endpoint", response_model=EndpointAdminOut)
async def get_endpoint(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> EndpointAdminOut:
    cfg = ep_svc.get_current()
    return EndpointAdminOut(
        primary_url=cfg.primary_url, fallback_url=cfg.fallback_url,
        min_app_version=cfg.min_app_version, note=cfg.note,
        updated_by=cfg.updated_by, updated_at=cfg.updated_at,
        rollout_percentage=cfg.rollout_percentage,
    )


@router.put("/admin/endpoint", response_model=EndpointAdminOut)
async def put_endpoint(
    payload: EndpointAdminIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> EndpointAdminOut:
    """Switch the canonical server URL.

    Clients pick this up via /api/config/endpoint within ~5 minutes;
    in-flight requests are NEVER cancelled. iOS additionally probes
    /healthz on the new URL before accepting it (see EndpointSync).
    """
    # v17e — capture old config BEFORE save so audit records a real
    # before→after diff (admin can see "this was a rollback to last
    # week's URL" instead of guessing).
    try:
        prev = ep_svc.get_current()
    except Exception:                                               # noqa: BLE001
        prev = None
    try:
        cfg = ep_svc.save(
            primary_url=payload.primary_url,
            fallback_url=payload.fallback_url,
            min_app_version=payload.min_app_version,
            note=payload.note,
            updated_by=user.id,
            reason=payload.reason,
            rollout_percentage=payload.rollout_percentage,
        )
    except ValueError as e:
        raise HTTPException(400, {"error": {"code": "endpoint_invalid",
                                              "message": str(e)}})
    audit_payload = payload.model_dump()
    audit_payload.update(_request_meta(request))
    if prev is not None:
        audit_payload["_previous"] = {
            "primary_url": prev.primary_url,
            "fallback_url": prev.fallback_url,
            "rollout_percentage": prev.rollout_percentage,
        }
        # Heuristic: if new primary == prev fallback, this is almost
        # certainly a rollback — flag for admin attention.
        audit_payload["is_rollback"] = (
            prev.fallback_url is not None
            and cfg.primary_url == prev.fallback_url
        )
    _audit(user.id, "endpoint_config.save", target=cfg.primary_url,
           payload=audit_payload)
    return EndpointAdminOut(
        primary_url=cfg.primary_url, fallback_url=cfg.fallback_url,
        min_app_version=cfg.min_app_version, note=cfg.note,
        updated_by=cfg.updated_by, updated_at=cfg.updated_at,
        rollout_percentage=cfg.rollout_percentage,
    )


@router.get("/admin/endpoint/history")
async def get_endpoint_history(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    limit: int = 20,
) -> dict:
    return {"items": ep_svc.history(limit=max(1, min(limit, 200)))}


@router.get("/admin/endpoint/override_audit")
async def get_endpoint_override_audit(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    hours: int = 24,
    device_fp: Optional[str] = None,
    limit: int = 100,
    format: str = "json",
):
    """v18 — query the Internal-build override audit trail.

    Support can answer "why can't device X connect?" by filtering on
    its sha256 device fingerprint (visible in the iOS Internal build's
    Connection Settings page). Without `device_fp` returns the most
    recent overrides across all devices for triage / smoke-detection.
    """
    hours = max(1, min(hours, 24 * 30))   # cap at 30 days
    limit = max(1, min(limit, 500))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    sql = ("SELECT id, device_fp, old_url, new_url, healthz_ok, source, "
            "app_version, reported_at "
            "FROM endpoint_override_audit WHERE reported_at >= ?")
    params: list = [cutoff]
    if device_fp:
        sql += " AND device_fp = ?"
        params.append(device_fp[:128])
    sql += " ORDER BY reported_at DESC LIMIT ?"
    params.append(limit)

    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(sql, params).fetchall()
        total_24h = con.execute(
            "SELECT COUNT(*) FROM endpoint_override_audit "
            "WHERE reported_at >= ?", (cutoff,),
        ).fetchone()[0]
        distinct_devices = con.execute(
            "SELECT COUNT(DISTINCT device_fp) FROM endpoint_override_audit "
            "WHERE reported_at >= ?", (cutoff,),
        ).fetchone()[0]

    items = [
        {
            "id": int(r[0]),
            "device_fp": r[1],
            "old_url": r[2],
            "new_url": r[3],
            "healthz_ok": bool(r[4]),
            "source": r[5],
            "app_version": r[6],
            "reported_at": r[7],
        }
        for r in rows
    ]
    if (format or "").lower() == "csv":
        # Style-match other admin CSVs (RFC 4180, CRLF, quoted fields).
        import csv
        import io
        from fastapi.responses import StreamingResponse
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["id", "reported_at", "device_fp", "old_url", "new_url",
                     "healthz_ok", "source", "app_version"])
        for it in items:
            w.writerow([it["id"], it["reported_at"], it["device_fp"] or "",
                         it["old_url"] or "", it["new_url"] or "",
                         "1" if it["healthz_ok"] else "0",
                         it["source"], it["app_version"] or ""])
        buf.seek(0)
        fname = f"endpoint_override_audit_{hours}h.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    return {
        "window_hours": hours,
        "total_events": int(total_24h or 0),
        "distinct_devices": int(distinct_devices or 0),
        "items": items,
    }


@router.get("/admin/endpoint/distribution")
async def get_endpoint_distribution(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    hours: int = 1,
) -> dict:
    """Roll up `endpoint_telemetry` so admin can see how many distinct
    devices are currently pointing at each URL.

    Use this right after a switch to confirm rollout — if the new URL
    isn't trending toward 100% within ~10min, something is wrong
    (clients can't reach the new healthz, app version too old, etc.)."""
    hours = max(1, min(hours, 24))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    canonical = ep_svc.get_current().primary_url
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT active_url, COUNT(DISTINCT device_fp), COUNT(*) "
            "FROM endpoint_telemetry WHERE reported_at >= ? "
            "GROUP BY active_url ORDER BY 2 DESC",
            (cutoff,),
        ).fetchall()
        total_devices = con.execute(
            "SELECT COUNT(DISTINCT device_fp) FROM endpoint_telemetry "
            "WHERE reported_at >= ?", (cutoff,),
        ).fetchone()[0]
    items = [{"active_url": r[0], "devices": int(r[1]), "polls": int(r[2]),
              "is_canonical": r[0] == canonical} for r in rows]
    canonical_devices = sum(i["devices"] for i in items if i["is_canonical"])
    rollout_pct = (canonical_devices / total_devices * 100.0
                   if total_devices else 0.0)
    cfg = ep_svc.get_current()
    target_pct = cfg.rollout_percentage
    # Below the configured target by ≥10pp = warning, ≥30pp = critical.
    delta = rollout_pct - target_pct
    if total_devices < 5:
        alert = "insufficient_data"
    elif delta <= -30:
        alert = "critical"
    elif delta <= -10:
        alert = "warning"
    else:
        alert = "ok"
    return {
        "canonical_url": canonical,
        "window_hours": hours,
        "total_devices": int(total_devices or 0),
        "canonical_devices": canonical_devices,
        "rollout_pct": round(rollout_pct, 2),
        "target_pct": target_pct,
        "alert": alert,
        "alert_message": {
            "ok": None,
            "warning": f"采用率 {round(rollout_pct,1)}% 低于目标 {target_pct}%，请确认新地址 /healthz 是否健康。",
            "critical": f"采用率 {round(rollout_pct,1)}% 严重低于目标 {target_pct}%，建议立即回滚或排查新地址。",
            "insufficient_data": "样本不足（<5 台设备），暂无法判断。",
        }[alert],
        "items": items,
    }


@router.get("/admin/endpoint/distribution/series")
async def get_endpoint_distribution_series(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    hours: int = 6,
    bucket_minutes: int = 15,
) -> dict:
    """v17d — time-series of "% devices on canonical URL".

    Lets admin watch a rollout climb (or stall) instead of just
    looking at the current snapshot. Returns one point per
    `bucket_minutes` over `hours` window.

    Response shape:
      { "buckets": [{"t": iso, "pct": 0..100, "total": N,
                       "canonical": M}, ...],
        "canonical_url": "...", "target_pct": N }
    """
    hours = max(1, min(hours, 48))
    bucket_minutes = max(5, min(bucket_minutes, 60))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    canonical = ep_svc.get_current().primary_url
    target_pct = ep_svc.get_current().rollout_percentage

    # Pull all telemetry in one shot, bucket in Python — DB bucketing
    # in SQLite needs strftime gymnastics and the row count is small
    # (a few thousand at most for 48h).
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT reported_at, active_url, device_fp "
            "FROM endpoint_telemetry WHERE reported_at >= ? "
            "ORDER BY reported_at ASC",
            (cutoff.isoformat(),),
        ).fetchall()

    bucket_sec = bucket_minutes * 60
    # bucket_key -> (set(device_fp_canonical), set(device_fp_total))
    buckets: dict[int, tuple[set, set]] = {}
    for reported_at, active_url, device_fp in rows:
        try:
            ts = datetime.fromisoformat(reported_at)
        except (TypeError, ValueError):
            continue
        epoch = int(ts.timestamp())
        key = epoch - (epoch % bucket_sec)
        canon_set, total_set = buckets.setdefault(key, (set(), set()))
        if device_fp:
            total_set.add(device_fp)
            if active_url == canonical:
                canon_set.add(device_fp)

    out = []
    for key in sorted(buckets):
        canon_set, total_set = buckets[key]
        total = len(total_set)
        canon = len(canon_set)
        pct = round((canon / total) * 100.0, 2) if total else 0.0
        out.append({
            "t": datetime.fromtimestamp(key, tz=timezone.utc).isoformat(),
            "pct": pct,
            "total": total,
            "canonical": canon,
        })

    return {
        "canonical_url": canonical,
        "target_pct": target_pct,
        "window_hours": hours,
        "bucket_minutes": bucket_minutes,
        "buckets": out,
    }


# ---------------------------------------------------------------------------
# Blocklist (v17c — kill switch for IPs / phones / emails / users)
# ---------------------------------------------------------------------------


class BlockIn(BaseModel):
    scope: str = Field(pattern="^(ip|phone|email|user)$")
    value: str = Field(min_length=1, max_length=256)
    reason: Optional[str] = Field(default=None, max_length=500)
    expires_in_hours: Optional[int] = Field(default=None, ge=1, le=24 * 365)
    # v17d — when True, log "would-have-blocked" hits without
    # denying the request. Promote to enforce after observing.
    dry_run: bool = False


@router.get("/admin/blocklist")
async def list_blocklist(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    scope: Optional[str] = None,
) -> dict:
    items = blocklist_svc.list_all(scope=scope)
    return {"items": [
        {"scope": e.scope, "value": e.value, "reason": e.reason,
         "created_by": e.created_by, "created_at": e.created_at,
         "expires_at": e.expires_at, "dry_run": e.dry_run,
         "dryrun_hits_1h": (blocklist_svc.peek_dryrun_hits(e.scope, e.value)
                              if e.dry_run else 0)}
        for e in items
    ]}


@router.post("/admin/blocklist")
async def add_blocklist(
    payload: BlockIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    expires = None
    if payload.expires_in_hours:
        from datetime import timedelta
        expires = (datetime.now(timezone.utc)
                    + timedelta(hours=payload.expires_in_hours))
    try:
        e = blocklist_svc.add(payload.scope, payload.value,
                                reason=payload.reason,
                                created_by=user.id, expires_at=expires,
                                dry_run=payload.dry_run)
    except ValueError as ex:
        raise HTTPException(400, {"error": {"code": "blocklist_invalid",
                                              "message": str(ex)}})
    audit_payload = payload.model_dump()
    audit_payload.update(_request_meta(request))
    _audit(user.id, "blocklist.add", target=f"{e.scope}:{e.value}",
           payload=audit_payload)
    return {"scope": e.scope, "value": e.value, "expires_at": e.expires_at,
             "dry_run": e.dry_run}


@router.delete("/admin/blocklist")
async def remove_blocklist(
    scope: str,
    value: str,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    deleted = blocklist_svc.remove(scope, value)
    _audit(user.id, "blocklist.remove", target=f"{scope}:{value}",
           payload={**_request_meta(request), "deleted": deleted})
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Free-tier quota override (v17c — customer support tool)
# ---------------------------------------------------------------------------


class FreeQuotaIn(BaseModel):
    device_fingerprint: str = Field(min_length=8, max_length=128)
    total: int = Field(ge=0, le=1000)


@router.get("/admin/users/{user_id}/free_quota")
async def get_user_free_quota(
    user_id: str,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    u = user_repo.get_user(user_id)
    if u is None or not u.device_fingerprint:
        raise HTTPException(404, {"error": {"code": "no_device_fp"}})
    with user_repo._connect() as con:                               # noqa: SLF001
        row = con.execute(
            "SELECT total, used, created_at, updated_at FROM usage_free_quota "
            "WHERE device_fingerprint = ?", (u.device_fingerprint,),
        ).fetchone()
    return {"device_fingerprint": u.device_fingerprint,
             "total": int(row[0]) if row else 0,
             "used": int(row[1]) if row else 0,
             "created_at": row[2] if row else None,
             "updated_at": row[3] if row else None}


@router.put("/admin/free_quota")
async def set_free_quota(
    payload: FreeQuotaIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """Customer-support tool: bump (or reset) the per-device free
    bucket. Lowering `total` below current `used` is allowed and
    will result in remaining = 0 (we never refund used shots)."""
    now = datetime.now(timezone.utc).isoformat()
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT INTO usage_free_quota (device_fingerprint, total, used, "
            "created_at, updated_at) VALUES (?, ?, 0, ?, ?) "
            "ON CONFLICT(device_fingerprint) DO UPDATE SET "
            "total = excluded.total, updated_at = excluded.updated_at",
            (payload.device_fingerprint, payload.total, now, now),
        )
        con.commit()
    audit_payload = payload.model_dump()
    audit_payload.update(_request_meta(request))
    _audit(user.id, "free_quota.set",
           target=payload.device_fingerprint, payload=audit_payload)
    return {"device_fingerprint": payload.device_fingerprint,
             "total": payload.total}


# ---------------------------------------------------------------------------
# Runtime settings (v17d — admin-tunable knobs without a deploy)
# ---------------------------------------------------------------------------


class RuntimeSettingIn(BaseModel):
    key: str = Field(min_length=1, max_length=128,
                       pattern=r"^[a-z][a-z0-9_.]*$")
    value: str = Field(max_length=1024)


@router.get("/admin/metrics/security")
async def get_security_metrics(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """Roll-up of the v17c/v17d defense layers. All counts are for
    the *current* hour-window of the underlying rate_buckets — not
    a strict rolling 60-min, but cheap & good enough for a glance."""
    with user_repo._connect() as con:                               # noqa: SLF001
        # blocklist enforce + dry-run hit totals by scope.
        rows = con.execute(
            "SELECT service, scope, SUM(count) "
            "FROM rate_buckets WHERE service IN "
            "('blocklist_enforce','blocklist_dryrun','otp','http') "
            "AND window_start >= ? "
            "GROUP BY service, scope",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
        ).fetchall()
    out: dict[str, dict] = {}
    for service, scope, total in rows:
        out.setdefault(service, {})[scope] = int(total or 0)
    return {
        "window_hours": 1,
        "blocklist_enforce_by_scope": out.get("blocklist_enforce", {}),
        "blocklist_dryrun_by_scope": out.get("blocklist_dryrun", {}),
        "otp_counters": out.get("otp", {}),
        "http_counters": out.get("http", {}),
    }


@router.get("/admin/alerts/recipients")
async def list_alert_recipients(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17g — recipients live in runtime_settings under
    `alert.recipients.<action>`. This endpoint pre-extracts them
    so the iOS UI doesn't have to filter the full settings list."""
    items = rs_svc.list_all()
    out = []
    for it in items:
        key = it["key"]
        if not key.startswith("alert.recipients."):
            continue
        out.append({
            "action": key[len("alert.recipients."):],
            "recipients": [x.strip() for x in (it["value"] or "").split(",")
                            if x.strip()],
            "updated_by": it["updated_by"],
            "updated_at": it["updated_at"],
        })
    enabled = rs_svc.get_str("alert.enabled", "true").lower() in ("1", "true", "yes")
    default_cooldown = rs_svc.get_int("alert.cooldown_sec.default", 300)
    return {"enabled": enabled, "default_cooldown_sec": default_cooldown,
             "items": out}


class AlertRecipientsIn(BaseModel):
    action: str = Field(min_length=1, max_length=128,
                          pattern=r"^[a-z][a-z0-9_.*]*$")
    recipients: list[str] = Field(default_factory=list, max_length=20)


@router.put("/admin/alerts/recipients")
async def set_alert_recipients(
    payload: AlertRecipientsIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    # Lightly sanitise — must look like an email and not exceed total
    # length budget for the runtime_settings value column (1024).
    # v17h — accept email OR lark://... OR dingtalk://... OR webhook://...
    # alert_mailer auto-detects channel from the prefix.
    cleaned: list[str] = []
    for raw in payload.recipients:
        v = (raw or "").strip()
        if not v or len(v) > 512:
            continue
        low = v.lower()
        ok = ("@" in v
              or low.startswith(("lark://", "feishu://",
                                   "dingtalk://", "ding://",
                                   "webhook://")))
        if not ok:
            continue
        cleaned.append(v)
    value = ",".join(cleaned)
    if len(value) > 1000:
        raise HTTPException(400, {"error": {"code": "alert_too_many",
                                              "message": "收件人总长度过大"}})
    key = f"alert.recipients.{payload.action}"
    if cleaned:
        rs_svc.set_value(key, value, updated_by=user.id)
    else:
        # Empty list = remove the entry entirely so the resolver
        # falls back to default.
        with user_repo._connect() as con:                           # noqa: SLF001
            con.execute("DELETE FROM runtime_settings WHERE key = ?", (key,))
            con.commit()
        rs_svc._flush_cache()                                       # noqa: SLF001
    audit_payload = {"action": payload.action, "count": len(cleaned)}
    audit_payload.update(_request_meta(request))
    _audit(user.id, "alerts.recipients.set", target=payload.action,
           payload=audit_payload)
    return {"action": payload.action, "recipients": cleaned}


@router.get("/admin/alerts/preview")
async def preview_alert(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    action: str = "iap.asn.refund",
) -> dict:
    """v17h — render the subject + body for an action without
    actually sending. Lets admin sanity-check the format before
    pointing alerts at production inboxes."""
    from ..services import alert_mailer
    fake_payload = {
        "product_id": "com.aiphotocoach.monthly",
        "expires_at": "2026-06-01T00:00:00Z",
        "client_ip": "203.0.113.0/24",
        "user_agent": "AIPhotoCoach/1.0 iOS/17.5",
        "channel": "email",
    }
    subject = alert_mailer.format_subject(action, "user-preview-demo")
    body = alert_mailer.format_body(
        action, admin_id=f"user:{user.id}", target="user-preview-demo",
        payload=fake_payload, occurred_at="2026-05-12T10:00:00Z",
    )
    return {"action": action, "subject": subject, "body": body,
             "would_send_to": alert_mailer.recipients_for(action)}


@router.post("/admin/alerts/test")
async def send_test_alert(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
    action: str = "test",
) -> dict:
    """v17g — fire a synthetic alert through the full pipeline so admin
    can validate inbox setup without waiting for a real refund."""
    from ..services import alert_mailer
    sent = alert_mailer.maybe_send_for_audit(
        action, admin_id=f"user:{user.id}", target=user.id,
        payload={"note": "manual test from iOS admin"},
    )
    return {"sent": sent, "action": action,
             "recipients": alert_mailer.recipients_for(action)}


@router.get("/admin/runtime_settings")
async def list_runtime_settings(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    return {"items": rs_svc.list_all()}


@router.put("/admin/runtime_settings")
async def set_runtime_setting(
    payload: RuntimeSettingIn,
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    try:
        v = rs_svc.set_value(payload.key, payload.value, updated_by=user.id)
    except ValueError as e:
        raise HTTPException(400, {"error": {"code": "runtime_invalid",
                                              "message": str(e)}})
    # v17h — PII guard: certain keys (alert recipients, anything that
    # might contain emails / phones / webhook tokens) are recorded
    # WITHOUT their literal value. We log a hash + length instead so
    # forensic search still works ("did anyone change this between
    # T1 and T2?") without leaking the value to other admins viewing
    # the audit log.
    sensitive_prefixes = ("alert.recipients.",)
    audit_payload = payload.model_dump()
    if payload.key.startswith(sensitive_prefixes):
        import hashlib as _h
        digest = _h.sha256((payload.value or "").encode("utf-8")).hexdigest()
        audit_payload["value"] = (
            f"[redacted len={len(payload.value)} "
            f"sha256={digest[:12]}]"
        )
    audit_payload.update(_request_meta(request))
    _audit(user.id, "runtime_settings.set", target=payload.key,
           payload=audit_payload)
    return {"key": payload.key, "value": v}


# ---------------------------------------------------------------------------
# v18 — satisfaction aggregates view + global-hint kill switch
# ---------------------------------------------------------------------------


@router.get("/admin/satisfaction/aggregates")
async def list_satisfaction_aggregates(
    scene_mode: Optional[str] = None,
    sort_by: str = "rate",
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v18 — admin can see ALL aggregate rows (including ones below
    the live thresholds), so they can decide whether to relax
    `pref.global_hint.min_distinct_users` or
    `min_satisfaction_rate`.

    `sort_by` ∈ {rate, distinct_users, satisfied, updated_at}.
    """
    from ..services import satisfaction_aggregates as sa_svc
    rows = sa_svc.list_for_admin(scene_mode, sort_by=sort_by)
    # v18 s1 — also surface the love/ok/bad split so the operator
    # can see "of the satisfied votes, how many are 真爱".
    with user_repo._connect() as con:
        if scene_mode:
            grade_rows = con.execute(
                "SELECT (step_config) AS sc, satisfied_grade, COUNT(*) AS n "
                "FROM usage_records "
                "WHERE satisfied_grade IS NOT NULL "
                "GROUP BY satisfied_grade"
            ).fetchall()
        else:
            grade_rows = con.execute(
                "SELECT satisfied_grade, COUNT(*) AS n FROM usage_records "
                "WHERE satisfied_grade IS NOT NULL "
                "GROUP BY satisfied_grade"
            ).fetchall()
    grade_dist = {"love": 0, "ok": 0, "bad": 0}
    for r in grade_rows:
        g = r["satisfied_grade"]
        if g in grade_dist:
            grade_dist[g] = int(r["n"] or 0)
    return {
        "enabled": sa_svc.is_enabled(),
        "grade_distribution": grade_dist,
        "thresholds": {
            "min_distinct_users":
                rs_svc.get_int("pref.global_hint.min_distinct_users", 30),
            "min_satisfaction_rate":
                rs_svc.get_str(
                    "pref.global_hint.min_satisfaction_rate", "0.6"),
            "cooldown_sec":
                rs_svc.get_int("pref.global_hint.cooldown_sec", 300),
        },
        "items": rows,
    }


@router.post("/admin/satisfaction/global_hint/kill")
async def kill_global_hint(
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v18 — single-tap kill switch. Writes
    `pref.global_hint.enabled = false` and audits with
    `satisfaction.global_hint.killed`. Recovers via the normal
    runtime_settings PUT once admin verifies the data is healthy."""
    rs_svc.set_value("pref.global_hint.enabled", "false",
                      updated_by=user.id)
    from ..services import satisfaction_aggregates as sa_svc
    sa_svc.reset_for_tests()  # bust read-cache so other instances
    audit_payload = _request_meta(request)
    _audit(user.id, "satisfaction.global_hint.killed",
           target="pref.global_hint.enabled", payload=audit_payload)
    return {"ok": True, "enabled": False}
