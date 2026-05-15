"""POST /feedback — capture-time → reality reconciliation.

After the user actually takes a photo, the iOS app reads the most
recent PHAsset's EXIF (within ~5 minutes of the analyze response) and
posts the realised camera params here. We persist the (recommendation,
result) pair into a sqlite ``shot_results`` table that scripts/
calibrate_distance.py and scripts/calibrate_palette.py can later
mine for K_face / K_body / STYLE_PALETTE refinement.

This is the closed loop that turns the app from "rule-based recommender"
into "self-improving recommender".
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models.schemas import ShotPosition, ShotPositionKind
from app.services import auth as auth_svc
from app.services import content_filter, poi_lookup, rate_limit, request_token
from app.api import metrics as metrics_api

from fastapi import Depends

log = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])

# Sqlite path — co-located with other backend state. Created lazily on
# first POST so unit tests don't need a real DB on import.
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "shot_results.db"


class ShotResultIn(BaseModel):
    """What the client tells us about the shot they actually took."""
    analyze_request_id: Optional[str] = Field(
        default=None,
        description="The id of the analyze response this realised. Used to join recommendation ↔ reality."
    )
    style_keywords: list[str] = Field(default_factory=list)
    geo_lat: Optional[float] = None
    geo_lon: Optional[float] = None
    captured_at_utc: Optional[datetime] = None
    # EXIF as read from PHAsset.
    focal_length_mm: Optional[float] = None
    focal_length_35mm_eq: Optional[float] = None
    aperture: Optional[float] = None
    exposure_time_s: Optional[float] = None
    iso: Optional[int] = None
    white_balance_k: Optional[int] = None
    # Free-form: the entire recommendation JSON the user picked from,
    # so the calibrator can replay scoring with new K values.
    recommendation_snapshot: Optional[dict[str, Any]] = None
    # ---- W2: UGC reinforcement loop -------------------------------
    chosen_position: Optional[ShotPosition] = None
    """Which ShotPosition the user actually used. When kind=absolute and
    rating >= 4 with no nearby POI hit, we promote it into user_spots."""
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    scene_kind: Optional[str] = None
    device_id: Optional[str] = Field(default=None, max_length=128)
    """Stable client-side device id; used for UGC dedup so a single
    device can't farm upvotes on the same spot."""


class FeedbackResponse(BaseModel):
    stored: bool
    row_id: Optional[int] = None
    ugc_action: Optional[str] = None
    """One of insert | merge | noop | skipped — describes what we did with
    the user's chosen_position in the user_spots UGC table (W2)."""


@router.post("/", response_model=FeedbackResponse)
async def post_feedback(
    request: Request,
    payload: ShotResultIn,
    x_device_id: Optional[str] = Header(default=None),
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> FeedbackResponse:
    settings = get_settings()
    if settings.enable_rate_limit:
        await rate_limit.enforce(
            request, "feedback",
            capacity=float(settings.rate_limit_default_per_min),
            refill_per_sec=settings.rate_limit_default_per_min / 60.0,
            identity=user.id,
            tier=user.tier,
        )
    # ---- P0-1.2 verify analyze_request_id token --------------------
    if payload.analyze_request_id and settings.request_token_secret:
        ok = request_token.verify(
            payload.analyze_request_id,
            request_token.payload_for(
                x_device_id or payload.device_id,
                payload.scene_kind,
            ),
            secret=settings.request_token_secret,
            ttl_sec=settings.request_token_ttl_sec,
        )
        if not ok:
            log.info("feedback token verify failed; accepting in degraded mode")
    # ---- P0-3.1 round geo to N decimals before persisting ----------
    geo_lat = _round_geo(payload.geo_lat, settings.geo_round_decimals)
    geo_lon = _round_geo(payload.geo_lon, settings.geo_round_decimals)
    try:
        with _connect() as con:
            cur = con.execute(
                """
                INSERT INTO shot_results (
                    received_at_utc, analyze_request_id, style_keywords_json,
                    geo_lat, geo_lon, captured_at_utc,
                    focal_length_mm, focal_length_35mm_eq, aperture,
                    exposure_time_s, iso, white_balance_k,
                    recommendation_snapshot_json, device_id, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    payload.analyze_request_id,
                    json.dumps(payload.style_keywords),
                    geo_lat, geo_lon,
                    payload.captured_at_utc.isoformat() if payload.captured_at_utc else None,
                    payload.focal_length_mm, payload.focal_length_35mm_eq,
                    payload.aperture, payload.exposure_time_s,
                    payload.iso, payload.white_balance_k,
                    json.dumps(payload.recommendation_snapshot) if payload.recommendation_snapshot else None,
                    x_device_id or payload.device_id,
                    user.id,
                ),
            )
            row_id = cur.lastrowid
        ugc_action = _maybe_record_ugc(payload, settings, x_device_id or payload.device_id, user_id=user.id)
        metrics_api.inc("ai_photo_coach_feedback_total", action=ugc_action or "noop")
        return FeedbackResponse(stored=True, row_id=row_id, ugc_action=ugc_action)
    except Exception as e:                         # pragma: no cover
        log.exception("feedback insert failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/post_process")
async def post_process_telemetry(
    payload: dict,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    """P1-7.1 — record post-process choices so the data flywheel can mine
    which presets/beauty knobs actually correlate with high ratings."""
    settings = get_settings()
    if not settings.enable_post_process_telemetry:
        return {"stored": False, "reason": "disabled"}
    try:
        with _connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS post_process_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "received_at_utc TEXT NOT NULL, payload_json TEXT NOT NULL)"
            )
            con.execute(
                "INSERT INTO post_process_events (received_at_utc, payload_json) VALUES (?, ?)",
                (datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)),
            )
        metrics_api.inc("ai_photo_coach_post_process_total")
        return {"stored": True}
    except Exception as e:                                       # noqa: BLE001
        log.info("post_process telemetry failed: %s", e)
        return {"stored": False, "error": str(e)}


@router.post("/alignment_pitch")
async def alignment_pitch_telemetry(
    payload: dict,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    """P3-strong-3 — sample |pitchDelta| at the green-light edge.
    Persisted to a dedicated table so the calibration job can SELECT
    abs_delta_deg, PERCENTILE_DISC(0.9) and rotate the
    ``pitchNear``/``pitchFar`` thresholds without touching app code."""
    settings = get_settings()
    if not settings.enable_alignment_pitch_telemetry:
        return {"stored": False, "reason": "disabled"}
    try:
        abs_delta = float(payload.get("abs_delta_deg") or 0.0)
        tier = str(payload.get("tier") or "unknown")
        shot_id = payload.get("shot_id")
        shot_id_str = str(shot_id) if shot_id else None
        with _connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS alignment_pitch_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "received_at_utc TEXT NOT NULL, "
                "shot_id TEXT, "
                "abs_delta_deg REAL NOT NULL, "
                "tier TEXT NOT NULL, "
                "payload_json TEXT NOT NULL)"
            )
            # Idempotent migration: older rows lacked shot_id; ALTER if missing.
            cols = {r[1] for r in con.execute("PRAGMA table_info(alignment_pitch_events)")}
            if "shot_id" not in cols:
                con.execute("ALTER TABLE alignment_pitch_events ADD COLUMN shot_id TEXT")
            con.execute(
                "INSERT INTO alignment_pitch_events "
                "(received_at_utc, shot_id, abs_delta_deg, tier, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), shot_id_str, abs_delta, tier,
                 json.dumps(payload, ensure_ascii=False)),
            )
        metrics_api.inc("ai_photo_coach_alignment_pitch_total", tier=tier)
        return {"stored": True}
    except Exception as e:                                       # noqa: BLE001
        log.info("alignment_pitch telemetry failed: %s", e)
        return {"stored": False, "error": str(e)}


@router.post("/ar_nav")
async def ar_nav_telemetry(
    payload: dict,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    """P2-10.3 — AR navigation funnel: attempted / arrived / shot_taken."""
    settings = get_settings()
    if not settings.enable_ar_nav_telemetry:
        return {"stored": False, "reason": "disabled"}
    event = str(payload.get("event") or "unknown")
    metrics_api.inc("ai_photo_coach_ar_nav_total", event=event)
    try:
        with _connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS ar_nav_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "received_at_utc TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT NOT NULL)"
            )
            con.execute(
                "INSERT INTO ar_nav_events (received_at_utc, event, payload_json) VALUES (?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), event,
                 json.dumps(payload, ensure_ascii=False)),
            )
        return {"stored": True}
    except Exception as e:                                       # noqa: BLE001
        log.info("ar_nav telemetry failed: %s", e)
        return {"stored": False, "error": str(e)}


@router.delete("/by_device")
async def delete_by_device(
    device_id: str,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> dict:
    """P0-3.4 GDPR/PIPL self-service deletion. Deletes every shot_result
    row recorded under ``device_id``.

    A0-5 hardening: only deletes rows belonging to the authenticated
    user — otherwise anyone could DELETE someone else's data by guessing
    their device_id.
    """
    if not device_id or len(device_id) > 128:
        raise HTTPException(status_code=400, detail="invalid device_id")
    try:
        with _connect() as con:
            cur = con.execute(
                "DELETE FROM shot_results WHERE device_id = ? AND "
                "(user_id IS NULL OR user_id = ?)",
                (device_id, user.id),
            )
            deleted = cur.rowcount
            con.execute("CREATE TABLE IF NOT EXISTS post_process_events ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "received_at_utc TEXT NOT NULL, payload_json TEXT NOT NULL)")
            # post_process_events are not keyed by device_id; we leave them
            # untouched (anonymous already).
        return {"deleted": deleted, "device_id": device_id}
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


def _round_geo(v: Optional[float], decimals: int) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), decimals)
    except (TypeError, ValueError):
        return None


def _maybe_record_ugc(payload: ShotResultIn, settings, device_id: Optional[str],
                       *, user_id: Optional[str] = None) -> Optional[str]:
    """If the user picked an absolute ShotPosition and rated >= 4 stars,
    promote it into the UGC ``user_spots`` table — but only if:
      - it's not already covered by a nearby POI (within 50 m), AND
      - the same device hasn't upvoted the same spot in the last 24 h
        (P0-1.6 anti-farming dedup).
    Best-effort; silently skips on any error so feedback never fails."""
    try:
        pos = payload.chosen_position
        rating = payload.rating
        if not pos or rating is None:
            return "skipped"
        if rating < 4 or pos.kind != ShotPositionKind.absolute:
            return "skipped"
        if pos.lat is None or pos.lon is None:
            return "skipped"
        # P0-3.2 round to N decimals before persisting.
        plat = round(pos.lat, settings.geo_round_decimals)
        plon = round(pos.lon, settings.geo_round_decimals)
        # Already-known POI within 50 m? Don't pollute user_spots.
        try:
            existing = poi_lookup._local(plat, plon, 50)
            if any(e.source == "kb" for e in existing):
                return "skipped"
        except Exception:                                       # noqa: BLE001
            pass
        # P0-1.5 sanitize derived_from.
        derived = pos.name_zh or "用户机位"
        if settings.enable_ugc_content_filter:
            derived = content_filter.sanitise(derived) or "用户机位"
        result = poi_lookup.record_user_spot(
            plat, plon,
            rating=rating,
            derived_from=derived,
            scene_kind=payload.scene_kind,
            device_id=device_id,
            dedup_window_hours=settings.ugc_dedup_window_hours,
            merge_radius_m=settings.ugc_dedup_radius_m,
            user_id=user_id,
        )
        return str(result.get("action", "noop"))
    except Exception as e:                                       # noqa: BLE001
        log.info("ugc record failed (best-effort): %s", e)
        return "noop"


@contextmanager
def _connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH))
    try:
        _ensure_schema(con)
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS shot_results (
            id                            INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at_utc               TEXT NOT NULL,
            analyze_request_id            TEXT,
            style_keywords_json           TEXT,
            geo_lat                       REAL,
            geo_lon                       REAL,
            captured_at_utc               TEXT,
            focal_length_mm               REAL,
            focal_length_35mm_eq          REAL,
            aperture                      REAL,
            exposure_time_s               REAL,
            iso                           INTEGER,
            white_balance_k               INTEGER,
            recommendation_snapshot_json  TEXT
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_shot_results_geo ON shot_results(geo_lat, geo_lon)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_shot_results_when ON shot_results(captured_at_utc)"
    )
    # Lazy migration: device_id (added P0-1.6 / P0-3.4).
    cols = {row[1] for row in con.execute("PRAGMA table_info(shot_results)").fetchall()}
    if "device_id" not in cols:
        con.execute("ALTER TABLE shot_results ADD COLUMN device_id TEXT")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_shot_results_device ON shot_results(device_id)"
        )
    # Lazy migration: user_id (A0-5 multi-user isolation).
    if "user_id" not in cols:
        con.execute("ALTER TABLE shot_results ADD COLUMN user_id TEXT")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_shot_results_user ON shot_results(user_id)"
        )
