"""Sun-position lookup endpoint.

Exposed at ``GET /sun-info`` so both the iOS and web clients can ask
"where is the sun right now at lat/lon?" before opening the AI camera.
The reply is cheap to compute, deterministic, and never leaves the user's
device for any external service.

The response is also folded into the analyze prompt under
"ENVIRONMENT FACTS" when light_shadow scene mode is active — so the LLM
can produce time-sensitive shot ordering and rim-light direction advice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import sun as sun_service

router = APIRouter(prefix="/sun-info", tags=["env"])


@router.get("")
def sun_info(
    lat: float = Query(..., ge=-90, le=90, description="WGS-84 latitude"),
    lon: float = Query(..., ge=-180, le=180, description="WGS-84 longitude"),
    timestamp: Optional[str] = Query(
        None,
        description="Optional ISO-8601 timestamp (UTC). Defaults to now.",
    ),
) -> dict:
    t: datetime
    if timestamp:
        try:
            t = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise HTTPException(400, f"timestamp not ISO-8601: {e}")
    else:
        t = datetime.now(timezone.utc)

    info = sun_service.compute(lat, lon, t)
    return {
        "lat": lat,
        "lon": lon,
        "timestamp": t.isoformat(),
        **info.to_dict(),
    }
