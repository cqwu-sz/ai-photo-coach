"""POST /panorama — build an equirectangular panorama from keyframes.

Same input shape as /analyze (frames + meta), but only produces the
360° backdrop the Three.js scene needs. Kept independent so the front
end can request it lazily after analyze (the panorama is ~200KB and not
needed until the user opens the 3D view).

Also supports a "from-demo" GET form that builds the panorama out of
the synthetic /dev/sample-frame/* set, so the UI's demo runner doesn't
need to upload anything.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import ValidationError

from ..config import get_settings
from ..models import CaptureMeta
from ..services.panorama import PanoramaConfig, make_panorama

router = APIRouter(tags=["panorama"])
log = logging.getLogger(__name__)


@router.post(
    "/panorama",
    responses={
        200: {"content": {"image/jpeg": {}}},
        400: {"description": "bad input"},
    },
)
async def panorama(
    meta: Annotated[str, Form(description="JSON-encoded CaptureMeta")],
    frames: Annotated[list[UploadFile], File()],
) -> Response:
    settings = get_settings()
    try:
        capture_meta = CaptureMeta.model_validate(json.loads(meta))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid meta: {exc}")

    if not 4 <= len(frames) <= settings.max_frames:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"need 4..{settings.max_frames} frames, got {len(frames)}",
        )
    if len(frames) != len(capture_meta.frame_meta):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "frames count != frame_meta count",
        )

    frame_bytes: list[bytes] = []
    for f in frames:
        data = await f.read()
        if len(data) > settings.max_frame_bytes:
            raise HTTPException(413, f"frame {f.filename} too large")
        frame_bytes.append(data)

    pano_bytes = make_panorama(frame_bytes, capture_meta.frame_meta)
    return Response(content=pano_bytes, media_type="image/jpeg")


@router.get(
    "/dev/panorama-demo.jpg",
    responses={200: {"content": {"image/jpeg": {}}}},
    tags=["dev"],
)
def panorama_demo() -> Response:
    """Build a panorama from the synthetic dev sample frames."""
    return Response(content=_demo_panorama_bytes(), media_type="image/jpeg")


@lru_cache(maxsize=1)
def _demo_panorama_bytes() -> bytes:
    from .dev import SAMPLE_FRAME_COUNT, _make_frame_bytes
    from ..models import FrameMeta

    frames: list[bytes] = []
    metas: list[FrameMeta] = []
    for i in range(SAMPLE_FRAME_COUNT):
        frames.append(_make_frame_bytes(i))
        metas.append(
            FrameMeta(
                index=i,
                azimuth_deg=(i * 45) % 360,
                pitch_deg=0.0,
                roll_deg=0.0,
                timestamp_ms=i * 220,
            )
        )
    return make_panorama(frames, metas, PanoramaConfig())
