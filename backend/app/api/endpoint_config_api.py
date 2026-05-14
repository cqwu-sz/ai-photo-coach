"""Public-readable endpoint configuration (v17b).

The iOS app polls this on cold-start and every ~5 minutes to learn
which baseURL it should be using. Anonymous read is intentional —
the response contains nothing sensitive (just a URL the binary will
fall back on anyway), and gating it behind auth would create a
chicken-and-egg problem if the auth path itself is what's being
migrated.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from ..services import endpoint_config as ep_svc
from ..services import rate_buckets, user_repo

router = APIRouter(tags=["config"])
log = logging.getLogger(__name__)


class EndpointOut(BaseModel):
    primary_url: str
    fallback_url: Optional[str] = None
    min_app_version: Optional[str] = None
    note: Optional[str] = None
    updated_at: datetime
    # v17c — gradual rollout. Client computes its own bucket and
    # picks primary_url iff bucket < rollout_percentage.
    rollout_percentage: int = 100


def _record_telemetry(active_url: Optional[str],
                       device_fp: Optional[str],
                       app_version: Optional[str]) -> None:
    """Best-effort: never let a telemetry hiccup fail the poll."""
    if not active_url:
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with user_repo._connect() as con:                           # noqa: SLF001
            con.execute(
                "INSERT INTO endpoint_telemetry (active_url, device_fp, "
                "app_version, reported_at) VALUES (?, ?, ?, ?)",
                (active_url[:512], (device_fp or "")[:128] or None,
                 (app_version or "")[:32] or None, now),
            )
            # Opportunistic GC; cheap because of the time index.
            con.execute("DELETE FROM endpoint_telemetry WHERE reported_at < ?",
                         (cutoff,))
            con.commit()
    except Exception:
        pass


@router.get("/api/config/endpoint", response_model=EndpointOut)
async def get_endpoint(
    response: Response,
    x_active_endpoint: Optional[str] = Header(default=None, alias="X-Active-Endpoint"),
    x_device_fp: Optional[str] = Header(default=None, alias="X-Device-Fp"),
    x_app_version: Optional[str] = Header(default=None, alias="X-App-Version"),
) -> EndpointOut:
    _record_telemetry(x_active_endpoint, x_device_fp, x_app_version)
    cfg = ep_svc.get_current()
    # CDN cache: with N backend instances, every iOS poll otherwise
    # fans out to N. 60s public cache lets the LB/CDN absorb the
    # bulk of traffic. ETag lets clients short-circuit on 304.
    # `stale-while-revalidate` keeps the response warm even if
    # the backend is briefly unhealthy — exactly what we want here
    # (a stale endpoint URL is fine; an unreachable config is not).
    response.headers["Cache-Control"] = (
        "public, max-age=60, stale-while-revalidate=300"
    )
    # ETag must be stable across processes & restarts so CDN cache
    # is actually useful. Built-in hash() randomises per interpreter
    # — sha1 of the canonical state is the right thing.
    etag_src = "|".join([
        cfg.primary_url,
        cfg.fallback_url or "",
        str(cfg.rollout_percentage),
        cfg.updated_at.isoformat(),
    ]).encode("utf-8")
    etag = hashlib.sha1(etag_src).hexdigest()[:16]
    response.headers["ETag"] = f'W/"{etag}"'
    return EndpointOut(
        primary_url=cfg.primary_url,
        fallback_url=cfg.fallback_url,
        min_app_version=cfg.min_app_version,
        note=cfg.note,
        updated_at=cfg.updated_at,
        rollout_percentage=cfg.rollout_percentage,
    )


# ---------------------------------------------------------------------------
# Override audit (v18 — Internal-build only path)
# ---------------------------------------------------------------------------
#
# Internal builds expose a "Connection settings" UI that lets the user point
# the app at any reachable URL (LAN dev box, staging, etc). When that user
# applies/clears an override, the client fires this endpoint best-effort so
# support can trace "device X switched at time Y, then couldn't reach Z".
#
# Anonymous on purpose:
#   - Endpoint is hit *before* login completes (that's the whole reason it
#     exists — the user is configuring how to reach the auth backend).
#   - Body is bounded; we cap field lengths server-side.
#   - Failure to record is silent (never blocks the user's actual config
#     action). Severe spam would show up as the table growing; the 90-day
#     retention sweep below keeps it bounded.
#
# Production builds will never hit this endpoint because the override UI
# is compiled out (#if INTERNAL_BUILD). The endpoint is still safe to leave
# enabled on production servers — worst case a curious user POSTs noise,
# which is no worse than them POSTing to any other anonymous endpoint.


class OverrideAuditIn(BaseModel):
    device_fp: Optional[str] = Field(default=None, max_length=128)
    old_url: Optional[str] = Field(default=None, max_length=512)
    new_url: Optional[str] = Field(default=None, max_length=512)
    healthz_ok: bool = False
    source: str = Field(default="internal_ui", max_length=32)
    app_version: Optional[str] = Field(default=None, max_length=32)


class OverrideAuditOut(BaseModel):
    ok: bool


@router.post("/api/telemetry/endpoint_override", response_model=OverrideAuditOut)
async def record_override(body: OverrideAuditIn,
                            request: Request,
                            x_app_version: Optional[str] = Header(
                                default=None, alias="X-App-Version",
                            )) -> OverrideAuditOut:
    # ---- per-IP throttle ------------------------------------------------
    # Endpoint is anonymous-writeable by design (it has to fire before
    # the user is logged in), which makes it a soft DoS target. Cap at
    # 10 hits per IP per minute — the legitimate "I changed my mind 3x
    # in a row" path stays comfortable; a script blasting 1000/s gets
    # silently dropped after the cap, and the actual override still
    # works locally for the user (this is fire-and-forget anyway).
    client_ip = ((request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
                  or (request.client.host if request.client else "unknown"))
    n = rate_buckets.hit("endpoint_override_audit", "ip", client_ip[:64], 60)
    if n > 10:
        log.info("endpoint_override audit dropped (ip=%s count=%d)", client_ip, n)
        return OverrideAuditOut(ok=False)
    try:
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        version = (body.app_version or x_app_version or "")[:32] or None
        with user_repo._connect() as con:                           # noqa: SLF001
            con.execute(
                "INSERT INTO endpoint_override_audit "
                "(device_fp, old_url, new_url, healthz_ok, source, "
                " app_version, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (body.device_fp or "")[:128] or None,
                    (body.old_url or "")[:512] or None,
                    (body.new_url or "")[:512] or None,
                    1 if body.healthz_ok else 0,
                    (body.source or "internal_ui")[:32],
                    version,
                    now,
                ),
            )
            # Opportunistic 90-day sweep so the table stays bounded
            # without a separate cron job.
            con.execute(
                "DELETE FROM endpoint_override_audit WHERE reported_at < ?",
                (cutoff,),
            )
            con.commit()
    except Exception as e:                                          # noqa: BLE001
        # Don't surface the error — fire-and-forget by contract. Log
        # so the SRE can spot disk/sqlite issues.
        log.warning("endpoint_override audit insert failed: %s", e)
    return OverrideAuditOut(ok=True)
