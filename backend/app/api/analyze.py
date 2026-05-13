"""POST /analyze endpoint."""
from __future__ import annotations

import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import ValidationError

from ..config import get_settings
from ..models import AnalyzeResponse, CaptureMeta, ErrorBody, ErrorResponse
from ..services import app_attest, model_config, rate_limit, request_token, usage_quota, usage_records
from ..services import auth as auth_svc
from ..services.analyze_service import AnalyzeService
from ..api import metrics as metrics_api

router = APIRouter()
log = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, **details) -> HTTPException:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return HTTPException(status_code=http_status, detail=body.model_dump())


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def analyze(
    request: Request,
    meta: Annotated[str, Form(description="JSON-encoded CaptureMeta")],
    frames: Annotated[list[UploadFile], File(description="8-12 keyframes")],
    reference_thumbnails: Annotated[
        list[UploadFile] | None, File(description="Optional reference thumbnails")
    ] = None,
    model_id: Annotated[
        Optional[str],
        Form(description="Vision-model id (defaults to settings.default_model_id)"),
    ] = None,
    model_api_key: Annotated[
        Optional[str],
        Form(description="BYOK key for the chosen model. Never logged."),
    ] = None,
    model_base_url: Annotated[
        Optional[str],
        Form(description="Custom OpenAI-compatible base URL for the chosen model."),
    ] = None,
    video: Annotated[
        Optional[UploadFile],
        File(description=(
            "Optional ≤ 8s 720p H.264/WebM environment-scan clip. Sent only "
            "in high-quality mode so Gemini Pro can do cross-frame temporal "
            "reasoning. Truncated server-side if too large."
        )),
    ] = None,
    x_device_id: Annotated[Optional[str], Header()] = None,
    x_app_attest_key: Annotated[Optional[str], Header()] = None,
    x_app_attest_assertion: Annotated[Optional[str], Header()] = None,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> AnalyzeResponse:
    settings = get_settings()

    # v17c — App Attest gate. No-op unless require_app_attest_on_analyze.
    from ..services import attest_gate
    attest_gate.require(request, kind="analyze",
                          payload_for_challenge=user.id)

    # ---- P0-1.4 rate limit (A0-5/A1-5: per user_id, tier-aware) ----
    if settings.enable_rate_limit:
        await rate_limit.enforce(
            request, "analyze",
            capacity=float(settings.rate_limit_analyze_per_min),
            refill_per_sec=settings.rate_limit_analyze_per_min / 60.0,
            identity=user.id,
            tier=user.tier,
        )

    # ---- P0-1.3 App Attest (optional / shadow mode by default) ------
    if settings.enable_app_attest and x_app_attest_key:
        challenge = app_attest.fingerprint_challenge(
            f"analyze|{x_device_id or '_'}|{request.client.host if request.client else '_'}",
        )
        if not app_attest.verify_assertion(
            x_app_attest_key, x_app_attest_assertion or "", challenge,
        ):
            metrics_api.inc("ai_photo_coach_analyze_requests_total", status="attest_fail")
            raise _error("attest_failed", "App Attest assertion failed", 401)

    try:
        meta_dict = json.loads(meta)
        capture_meta = CaptureMeta.model_validate(meta_dict)
    except json.JSONDecodeError as exc:
        raise _error("invalid_meta_json", str(exc), status.HTTP_400_BAD_REQUEST)
    except ValidationError as exc:
        raise _error(
            "invalid_meta",
            "meta failed schema validation",
            status.HTTP_400_BAD_REQUEST,
            errors=exc.errors(include_url=False, include_context=False),
        )

    if not 4 <= len(frames) <= settings.max_frames:
        raise _error(
            "frame_count_out_of_range",
            f"need 4..{settings.max_frames} frames, got {len(frames)}",
            status.HTTP_400_BAD_REQUEST,
        )

    if len(frames) != len(capture_meta.frame_meta):
        raise _error(
            "frame_meta_mismatch",
            f"frames count ({len(frames)}) != frame_meta count ({len(capture_meta.frame_meta)})",
            status.HTTP_400_BAD_REQUEST,
        )

    refs = reference_thumbnails or []
    if len(refs) > settings.max_reference_thumbs:
        raise _error(
            "too_many_references",
            f"max {settings.max_reference_thumbs} reference thumbnails",
            413,
        )

    frame_bytes: list[bytes] = []
    for f in frames:
        data = await f.read()
        if len(data) > settings.max_frame_bytes:
            raise _error(
                "frame_too_large",
                f"frame {f.filename} exceeds {settings.max_frame_bytes} bytes",
                413,
            )
        frame_bytes.append(data)

    ref_bytes: list[bytes] = []
    for r in refs:
        ref_bytes.append(await r.read())

    # Read optional video. We cap at settings.max_video_bytes (default
    # 12 MB ≈ 8 s of 720p) — anything larger is silently dropped so a
    # rogue client can't blow the request budget.
    video_bytes: bytes | None = None
    if video is not None:
        raw = await video.read()
        max_video = getattr(settings, "max_video_bytes", 12 * 1024 * 1024)
        if 0 < len(raw) <= max_video:
            video_bytes = raw
        else:
            log.info("dropping oversized video (%d bytes > %d)", len(raw), max_video)

    # ---- v17 central model config ---------------------------------
    # Model choice is admin-controlled. Client `model_id` is ignored
    # in production unless `enable_byok` is on AND env allows it.
    central = model_config.get_current()
    quality_mode = (getattr(capture_meta, "quality_mode", None) or "fast").lower()
    chosen_model_id = (
        central.high_model_id if quality_mode in ("high", "pro")
        else central.fast_model_id
    )
    if not settings.enable_byok:
        # Hard-strip BYOK overrides so the centralised choice always wins.
        model_id = None
        model_api_key = None
        model_base_url = None
    else:
        model_id = model_id or chosen_model_id

    # IMPORTANT: never log the BYOK api key. Truncate / redact for safety.
    log.info(
        "analyze_request",
        extra={
            "scene_mode": capture_meta.scene_mode.value,
            "person_count": capture_meta.person_count,
            "model_id": (model_id or chosen_model_id),
            "central_fast": central.fast_model_id,
            "central_high": central.high_model_id,
            "quality_mode": quality_mode,
            "byok_key_supplied": bool(model_api_key),
            "byok_base_url_supplied": bool(model_base_url),
            "frames": len(frame_bytes),
            "references": len(ref_bytes),
        },
    )

    # ---- v17 usage quota: reserve a slot up-front; commit on success,
    # rollback on any failure so a 5xx never burns the user's budget.
    quota = usage_quota.reserve(user.id, role=user.role)

    # Snapshot the four-step config so the user (and admins) can later
    # see exactly what they ran. Keep it small — no raw frames. Sanitize
    # user-typed style keywords: cap each keyword at 60 chars and the
    # whole list at 12 entries so the audit row can't be weaponised
    # to leak large prompt-injection blobs (PR6 follow-up opt-records-frame-redact).
    raw_keywords = getattr(capture_meta, "style_keywords", []) or []
    if isinstance(raw_keywords, str):
        raw_keywords = [raw_keywords]
    safe_keywords = [str(k)[:60] for k in list(raw_keywords)[:12]]
    step_config_snapshot = {
        "scene_mode": capture_meta.scene_mode.value,
        "person_count": capture_meta.person_count,
        "quality_mode": getattr(capture_meta, "quality_mode", None),
        "style_keywords": safe_keywords,
        "frames_count": len(frame_bytes),
        "references_count": len(ref_bytes),
        "has_video": video_bytes is not None,
    }
    pre_token = request_token.payload_for(x_device_id, capture_meta.scene_mode.value)
    request_id_for_record = request_token.issue(
        pre_token, secret=settings.request_token_secret or None,
    )
    record_id = usage_records.create_pending(
        user_id=user.id,
        request_id=request_id_for_record,
        step_config=step_config_snapshot,
        reservation_id=quota.reservation_id,
    )

    service = AnalyzeService(settings)
    import time as _time
    t0 = _time.monotonic()
    # v18 — stash user_id in a ContextVar so the synchronous prompt
    # builder can render the personal preference block without us
    # threading user_id through every Provider implementation.
    from ..services import prompts as _prompts
    _prompts.set_request_user_id(user.id)
    try:
        result = await service.run(
            capture_meta,
            frame_bytes,
            ref_bytes,
            model_id=model_id,
            model_api_key=model_api_key if settings.enable_byok else None,
            model_base_url=model_base_url if settings.enable_byok else None,
            video_mp4=video_bytes,
        )
    except HTTPException:
        usage_quota.rollback(quota.reservation_id)
        usage_records.mark_failed(record_id, error_code="analyze_http_error")
        raise
    except Exception as exc:
        usage_quota.rollback(quota.reservation_id)
        usage_records.mark_failed(record_id, error_code="analyze_exception")
        metrics_api.inc("ai_photo_coach_analyze_requests_total", status="error")
        log.exception("analyze failed")
        raise _error(
            "analyze_failed",
            str(exc),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    usage_quota.commit(quota.reservation_id)
    # Record the proposals (compact form) and any token / cost telemetry
    # the analyze service surfaced via `result.debug`.
    proposals_compact = []
    for s in result.shots[:8]:
        try:
            d = s.model_dump()
        except AttributeError:
            d = dict(getattr(s, "__dict__", {}))
        proposals_compact.append({
            "id": d.get("id") or d.get("shot_id"),
            "summary": d.get("rationale") or d.get("coach_message"),
            "lens": d.get("lens"),
            "angle": d.get("angle"),
        })
    usage_records.mark_charged(
        record_id,
        proposals=proposals_compact,
        model_id=(model_id or chosen_model_id),
        prompt_tokens=result.debug.get("prompt_tokens"),
        completion_tokens=result.debug.get("completion_tokens"),
        cost_usd=result.debug.get("cost_usd"),
    )
    elapsed_ms = (_time.monotonic() - t0) * 1000
    metrics_api.observe("ai_photo_coach_analyze_latency_ms", elapsed_ms, stage="total")
    metrics_api.inc("ai_photo_coach_analyze_requests_total", status="ok")

    # ---- P0-1.2 stamp the response with a HMAC token --------------
    result.debug["analyze_request_id"] = request_id_for_record
    result.debug["usage_record_id"] = record_id

    # v17i — cohort hint: which proposal_id has the highest historical
    # adoption among users who picked the same scene + keyword. If
    # the cohort is too sparse (< 5 distinct users) this returns
    # None and the client uses the LLM's natural ordering. We expose
    # it via debug.* so old clients ignore it gracefully.
    try:
        from ..services import cohort_recommender
        rec = cohort_recommender.recommend_detailed(
            capture_meta.scene_mode.value, safe_keywords)
        if rec:
            result.debug["cohort_recommended_proposal_id"] = rec["proposal_id"]
            result.debug["cohort_size"] = rec["cohort_size"]
            result.debug["cohort_basis"] = rec["cohort_basis"]
            # v17k — ship the human-readable scene name so the iOS chip
            # doesn't need to maintain a parallel scene-mode→中文 map.
            # v18 s2 — moved to services/style_catalog so a single map
            # backs cohort label, USER_PREFERENCE, CROSS_USER_TREND.
            from ..services import style_catalog as _sc
            result.debug["cohort_scene_label"] = _sc.scene_label_zh(
                capture_meta.scene_mode.value)
    except Exception as e:                                          # noqa: BLE001
        log.debug("cohort recommend failed (non-fatal): %s", e)

    return result
