"""POST /recon3d/start + GET /recon3d/{job_id} (W9.2).

Async recon worker — accepts multipart-style image batches (already
base64-decoded by the client) and returns a tracked job. The actual
SfM is run by ``services.recon3d`` on a single-slot semaphore so the
process never thrashes.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import auth as auth_svc
from ..services import rate_limit, recon3d as recon_svc, usage_quota

log = logging.getLogger(__name__)
router = APIRouter(prefix="/recon3d", tags=["recon3d"])


class Recon3DStartIn(BaseModel):
    images_b64: list[str] = Field(default_factory=list,
                                   description="JPEG bytes per image, base64-encoded.")
    priors: Optional[list[dict]] = Field(
        default=None,
        description="Optional [{'image_name': str, 't': [x,y,z]}, ...] camera priors.",
    )
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None


class Recon3DJobOut(BaseModel):
    job_id: str
    status: str
    progress: float
    error: Optional[str] = None
    model: Optional[dict] = None


def _to_out(job: recon_svc.Recon3DJob) -> Recon3DJobOut:
    return Recon3DJobOut(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        error=job.error,
        model=job.model.model_dump() if job.model else None,
    )


@router.post("/start", response_model=Recon3DJobOut)
async def start_recon(
    request: Request,
    payload: Recon3DStartIn,
    x_device_id: Optional[str] = Header(default=None),
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> Recon3DJobOut:
    settings = get_settings()
    # ---- P0-1.7 per-user rate limit (1 / 5min default, A1-5 tiered) ----
    if settings.enable_rate_limit:
        await rate_limit.enforce(
            request, "recon3d_start",
            capacity=float(settings.rate_limit_recon3d_per_min),
            refill_per_sec=settings.rate_limit_recon3d_per_min / 300.0,
            identity=user.id,
            tier=user.tier,
        )
    if not payload.images_b64:
        raise HTTPException(status_code=400, detail="images_b64 must not be empty")
    if len(payload.images_b64) > settings.recon3d_max_images:
        raise HTTPException(
            status_code=413,
            detail=f"too many images: {len(payload.images_b64)} > {settings.recon3d_max_images}",
        )
    blobs: list[bytes] = []
    for s in payload.images_b64:
        try:
            data = base64.b64decode(s)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad base64: {e}")
        if len(data) > settings.recon3d_max_image_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"image too large: {len(data)} > {settings.recon3d_max_image_bytes}",
            )
        blobs.append(data)
    # v17 / opt-recon3d-quota: 3D reconstruction also burns model time.
    # Reserve a slot before scheduling; rollback if submit_job throws
    # synchronously, otherwise commit so async worker failures still
    # count (the GPU was busy regardless).
    quota = usage_quota.reserve(user.id, role=user.role)
    try:
        job = recon_svc.submit_job(
            blobs, priors=payload.priors,
            origin_lat=payload.origin_lat, origin_lon=payload.origin_lon,
            user_id=user.id,
        )
    except Exception:
        usage_quota.rollback(quota.reservation_id)
        raise
    usage_quota.commit(quota.reservation_id)
    return _to_out(job)


@router.get("/{job_id}", response_model=Recon3DJobOut)
async def get_recon(
    job_id: str,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> Recon3DJobOut:
    # A0-5: only the owner can see their job. We accept jobs with
    # NULL user_id (legacy rows from before this migration) for the
    # current user too, so the rollout window doesn't break anyone.
    job = recon_svc.get_job(job_id, user_id=user.id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return _to_out(job)
