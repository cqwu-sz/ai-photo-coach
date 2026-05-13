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
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel

from ..services import endpoint_config as ep_svc
from ..services import user_repo

router = APIRouter(tags=["config"])


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
