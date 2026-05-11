"""Style feasibility lookup endpoint.

Exposed at ``GET /style-feasibility?lat=&lon=`` so the wizard step-3
picker UI can show "this style works / doesn't work right now" badges
based on real sun + weather data.

The same scoring runs server-side at /analyze time too (via prompts.py
→ style_feasibility.score_styles), so this endpoint exists purely so
the picker can show verdicts BEFORE the user even hits "capture".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import style_feasibility as sf_service
from ..services import sun as sun_service
from ..services import weather as weather_service

router = APIRouter(prefix="/style-feasibility", tags=["env"])


@router.get("")
async def style_feasibility(
    lat: Optional[float] = Query(None, ge=-90, le=90, description="WGS-84 latitude"),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="WGS-84 longitude"),
    timestamp: Optional[str] = Query(
        None, description="ISO-8601 UTC; defaults to now",
    ),
    picks: Optional[str] = Query(
        None,
        description="Comma-separated style IDs the user is currently leaning"
                    " toward. When provided + lat/lon present, response also"
                    " includes a `better_time` suggestion if scanning the"
                    " next 24h finds a slot with materially higher score.",
    ),
) -> dict:
    """Return per-style feasibility scores for the given location/time.

    Both `lat` and `lon` are optional — if either is missing, scores fall
    back to "unknown" tier so the caller can still render the picker
    without warning badges.
    """
    sun_info = None
    weather_snap = None

    if lat is not None and lon is not None:
        # Sun is local math, never fails. Weather is best-effort.
        if timestamp:
            try:
                t = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise HTTPException(400, f"timestamp not ISO-8601: {e}")
        else:
            t = datetime.now(timezone.utc)
        sun_info = sun_service.compute(lat, lon, t)
        weather_snap = await weather_service.fetch_current(lat, lon)

    scores = sf_service.score_styles(sun_info, weather_snap)

    better_time = None
    if picks and sun_info is not None and lat is not None and lon is not None:
        pick_ids = [p.strip() for p in picks.split(",") if p.strip()]
        valid = [pid for pid in pick_ids if pid in sf_service.STYLE_IDS]
        if valid:
            now_t = datetime.fromisoformat(
                (timestamp or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
            )
            if now_t.tzinfo is None:
                now_t = now_t.replace(tzinfo=timezone.utc)
            better_time = sf_service.suggest_better_time(
                valid, lat, lon, now_t, weather=weather_snap,
            )

    return {
        "lat": lat,
        "lon": lon,
        "has_geo": sun_info is not None,
        "sun": sun_info.to_dict() if sun_info else None,
        "weather": weather_snap.to_dict() if weather_snap else None,
        "scores": [
            {
                "style_id": s.style_id,
                "label_zh": s.label_zh,
                "score": s.score,
                "tier": s.tier,
                "reason_zh": s.reason_zh,
            }
            for s in scores
        ],
        "better_time": better_time,
    }
