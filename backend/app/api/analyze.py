"""POST /analyze endpoint."""
from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..config import get_settings
from ..models import AnalyzeResponse, CaptureMeta, ErrorBody, ErrorResponse
from ..services.analyze_service import AnalyzeService

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
    meta: Annotated[str, Form(description="JSON-encoded CaptureMeta")],
    frames: Annotated[list[UploadFile], File(description="8-12 keyframes")],
    reference_thumbnails: Annotated[
        list[UploadFile] | None, File(description="Optional reference thumbnails")
    ] = None,
) -> AnalyzeResponse:
    settings = get_settings()

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

    service = AnalyzeService(settings)
    try:
        return await service.run(capture_meta, frame_bytes, ref_bytes)
    except Exception as exc:
        log.exception("analyze failed")
        raise _error(
            "analyze_failed",
            str(exc),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
