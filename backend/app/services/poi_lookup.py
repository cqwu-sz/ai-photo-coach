"""Three-tier POI search used to seed ``absolute`` shot candidates.

Order:
    1. Local sqlite (``poi_kb.db``) — anything we've already seen.
    2. AMap (Gaode) Place Search — needs ``AMAP_KEY`` env var.
    3. OpenStreetMap Overpass — free fallback, no key.

Every successful upstream hit is **written back** to ``poi_kb.db`` with a
``source`` tag and ``fetched_at_utc`` timestamp so subsequent requests at
the same location skip the network entirely.

The whole pipeline is bounded by a single 1.5 s budget (matching the
weather lookup) so analyze never stalls. Any failure path returns the
local results that we already have (often an empty list) and we keep
going — POI is purely additive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import httpx

from . import circuit_breaker
from .poi_kb import DB_PATH, _haversine_m

log = logging.getLogger(__name__)

TIMEOUT_SEC = 1.5
AMAP_PLACE_URL = "https://restapi.amap.com/v3/place/around"
AMAP_TYPES = "110000|110200|130000|140100|140200"  # 风景名胜 | 风景名胜相关 | 公司企业(地标)|博物馆 | 文化场馆
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


@dataclass(frozen=True, slots=True)
class POICandidate:
    """A POI that could host a shot. The fusion layer turns this into a
    ``ShotPosition`` with ``source='poi_kb'`` or ``'poi_online'``."""
    name: str
    lat: float
    lon: float
    kind: str
    source: str                         # 'kb' | 'amap' | 'osm'
    distance_m: float                   # haversine to the user's GeoFix
    bearing_from_user_deg: float        # 0=N, 90=E ...
    recommended_facing_deg: Optional[float] = None
    """Optional camera facing direction (e.g. 'point camera AT this POI'
    is usually ``bearing_from_user_deg`` flipped by 180°). Filled when
    we have a strong opinion (museums-with-facade, viewpoints with a
    documented best angle); else ``None`` and the LLM picks."""


# ---------------------------------------------------------------------------
async def search_nearby(
    lat: float,
    lon: float,
    radius_m: int = 300,
    max_total: int = 8,
    amap_key: Optional[str] = None,
) -> list[POICandidate]:
    """Return up to ``max_total`` POIs within ``radius_m`` of (lat, lon).

    Always returns within ``TIMEOUT_SEC`` — falls back to whatever the
    local DB has if both online tiers time out.
    """
    deadline = time.monotonic() + TIMEOUT_SEC
    results: list[POICandidate] = list(_local(lat, lon, radius_m))
    if len(results) >= max_total:
        return _trim(results, max_total)

    amap_breaker = circuit_breaker.get("amap")
    osm_breaker = circuit_breaker.get("osm")

    # Tier 2 — AMap (Gaode). Skip when no key configured or breaker open.
    key = amap_key if amap_key is not None else os.getenv("AMAP_KEY", "").strip()
    if key and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            async with amap_breaker.guarded("place.around"):
                amap = await asyncio.wait_for(
                    _fetch_amap(lat, lon, radius_m, key), timeout=remaining,
                )
            for p in amap:
                if not _dup(p, results):
                    results.append(p)
                    _writeback(p)
        except circuit_breaker.CircuitOpen:
            log.info("amap circuit open, skipping")
        except Exception as e:                              # noqa: BLE001
            log.info("amap fetch failed: %s", e)

    if len(results) >= max_total:
        return _trim(results, max_total)

    # Tier 3 — OpenStreetMap Overpass. Always tried as last resort.
    if time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            async with osm_breaker.guarded("overpass"):
                osm = await asyncio.wait_for(
                    _fetch_osm(lat, lon, radius_m), timeout=remaining,
                )
            for p in osm:
                if not _dup(p, results):
                    results.append(p)
                    _writeback(p)
        except circuit_breaker.CircuitOpen:
            log.info("osm circuit open, skipping")
        except Exception as e:                              # noqa: BLE001
            log.info("osm fetch failed: %s", e)

    return _trim(results, max_total)


# ---------------------------------------------------------------------------
def _trim(results: list[POICandidate], max_total: int) -> list[POICandidate]:
    """Sort by distance ascending and cap. Stable so writeback order is
    preserved when distances tie."""
    results.sort(key=lambda p: p.distance_m)
    return results[:max_total]


def _dup(p: POICandidate, existing: list[POICandidate]) -> bool:
    """Two POIs are 'the same' when within 15 m AND same name prefix."""
    for e in existing:
        if _haversine_m(p.lat, p.lon, e.lat, e.lon) < 15:
            return True
        if p.name and e.name and (p.name in e.name or e.name in p.name):
            if _haversine_m(p.lat, p.lon, e.lat, e.lon) < 50:
                return True
    return False


def _bearing_from_user(user_lat: float, user_lon: float,
                       poi_lat: float, poi_lon: float) -> float:
    """Forward azimuth in degrees [0, 360), 0=N, 90=E."""
    phi1 = math.radians(user_lat)
    phi2 = math.radians(poi_lat)
    dl = math.radians(poi_lon - user_lon)
    y = math.sin(dl) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


# ---------------------------------------------------------------------------
def _local(lat: float, lon: float, radius_m: int) -> Iterable[POICandidate]:
    """Bounding-box scan against the seeded sqlite DB.

    P2-10.1: when a ``boost`` column exists (added by weekly_poi_boost),
    we knock distance_m down by ``boost * 5 m`` so high-conversion
    POIs sort to the top within the same ring.
    """
    deg = (radius_m / 111000) * 1.5
    try:
        with _connect_ro() as con:
            cols = {row[1] for row in con.execute("PRAGMA table_info(pois)").fetchall()}
            has_boost = "boost" in cols
            sql = (
                "SELECT name, lat, lon, kind, "
                + ("boost " if has_boost else "0 as boost ")
                + "FROM pois WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
            )
            rows = con.execute(
                sql, (lat - deg, lat + deg, lon - deg, lon + deg),
            ).fetchall()
    except sqlite3.DatabaseError as e:
        log.info("poi_kb read failed: %s", e)
        return []
    out: list[POICandidate] = []
    for name, plat, plon, kind, boost in rows:
        d = _haversine_m(lat, lon, plat, plon)
        if d > radius_m:
            continue
        # Promote popular POIs without lying about distance: subtract a
        # symbolic 5m per log-unit of conversions, but never below 1m.
        sort_d = max(1.0, d - float(boost or 0) * 5.0)
        out.append(POICandidate(
            name=name, lat=plat, lon=plon, kind=kind or "poi",
            source="kb", distance_m=round(sort_d, 1),
            bearing_from_user_deg=round(_bearing_from_user(lat, lon, plat, plon), 1),
        ))
    # UGC tier — user-validated spots with enough upvotes count as KB.
    out.extend(_local_user_spots(lat, lon, radius_m))
    return out


def _local_user_spots(lat: float, lon: float, radius_m: int,
                      min_upvotes: Optional[int] = None) -> list[POICandidate]:
    """Pull from ``user_spots`` table (UGC).

    A spot enters the KB tier when it satisfies *any* of:
      - ``upvotes >= min_upvotes`` (default from settings, dynamic), OR
      - ``is_curated = 1`` (admin / seed / "trusted from day 1") (P1-6.2).

    Threshold is dynamic (P1-6.1): when total user_spots count is small
    we relax to 1 vote so cold-start regions still surface results.
    """
    deg = (radius_m / 111000) * 1.5
    try:
        with _connect_ro() as con:
            _ensure_user_spots_schema(con)
            if min_upvotes is None:
                # P1-6.1 dynamic threshold based on global density.
                try:
                    total = con.execute(
                        "SELECT COUNT(*) FROM user_spots"
                    ).fetchone()[0]
                except sqlite3.DatabaseError:
                    total = 0
                min_upvotes = 1 if total < 100 else 3
            rows = con.execute(
                "SELECT lat, lon, scene_kind, derived_from, upvotes, is_curated "
                "FROM user_spots "
                "WHERE (upvotes >= ? OR is_curated = 1) "
                "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (min_upvotes, lat - deg, lat + deg, lon - deg, lon + deg),
            ).fetchall()
    except sqlite3.DatabaseError as e:
        log.info("user_spots read failed: %s", e)
        return []
    out: list[POICandidate] = []
    for plat, plon, scene_kind, derived, upvotes, curated in rows:
        d = _haversine_m(lat, lon, plat, plon)
        if d > radius_m:
            continue
        label = str(derived or "用户验证机位")
        if curated:
            label = f"{label} ✓"
        out.append(POICandidate(
            name=label,
            lat=plat, lon=plon, kind=str(scene_kind or "ugc"),
            source="poi_ugc",
            distance_m=round(d, 1),
            bearing_from_user_deg=round(_bearing_from_user(lat, lon, plat, plon), 1),
        ))
    return out


# ---------------------------------------------------------------------------
async def fetch_amap(lat: float, lon: float, radius_m: int, key: str) -> list[POICandidate]:
    """Public wrapper around the AMap fetch (used by bulk_seed_poi).
    Identical behaviour, just a stable public name."""
    return await _fetch_amap(lat, lon, radius_m, key)


async def fetch_osm(lat: float, lon: float, radius_m: int) -> list[POICandidate]:
    """Public wrapper around the OSM Overpass fetch (used by bulk_seed_poi)."""
    return await _fetch_osm(lat, lon, radius_m)


async def _fetch_amap(lat: float, lon: float, radius_m: int, key: str) -> list[POICandidate]:
    """AMap Place Search around. Filters to scenic / cultural categories
    so we don't drown in restaurants and convenience stores."""
    # v9 UX polish #18 — round before sending to third party. 110 m
    # accuracy is plenty for nearby-POI search and we don't want
    # AMap's logs to know your exact location.
    from app.config import round_geo_by_use
    tp_lat = round_geo_by_use(lat, "third_party")
    tp_lon = round_geo_by_use(lon, "third_party")
    params = {
        "key": key,
        "location": f"{tp_lon},{tp_lat}",   # AMap uses lon,lat
        "radius": str(min(radius_m, 50000)),
        "types": AMAP_TYPES,
        "extensions": "base",
        "offset": "20",
        "page": "1",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        r = await client.get(AMAP_PLACE_URL, params=params)
        r.raise_for_status()
        payload = r.json()
    if str(payload.get("status")) != "1":
        log.info("amap non-success: %s", payload.get("info"))
        return []
    out: list[POICandidate] = []
    for poi in payload.get("pois", []):
        loc = poi.get("location", "")
        if "," not in loc:
            continue
        try:
            plon, plat = (float(s) for s in loc.split(",")[:2])
        except ValueError:
            continue
        d = _haversine_m(lat, lon, plat, plon)
        out.append(POICandidate(
            name=str(poi.get("name") or "未命名地点"),
            lat=plat, lon=plon,
            kind=str(poi.get("type") or "poi").split(";")[0],
            source="amap",
            distance_m=round(d, 1),
            bearing_from_user_deg=round(_bearing_from_user(lat, lon, plat, plon), 1),
        ))
    return out


async def _fetch_osm(lat: float, lon: float, radius_m: int) -> list[POICandidate]:
    """OSM Overpass — viewpoints, attractions, monuments, museums."""
    # v9 UX polish #18 — round before sending to third party.
    from app.config import round_geo_by_use
    tp_lat = round_geo_by_use(lat, "third_party")
    tp_lon = round_geo_by_use(lon, "third_party")
    query = (
        "[out:json][timeout:1];("
        f"node[tourism=viewpoint](around:{radius_m},{tp_lat},{tp_lon});"
        f"node[tourism=attraction](around:{radius_m},{tp_lat},{tp_lon});"
        f"node[historic](around:{radius_m},{tp_lat},{tp_lon});"
        f"node[tourism=museum](around:{radius_m},{tp_lat},{tp_lon});"
        ");out body 20;"
    )
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        r = await client.post(OVERPASS_URL, data={"data": query})
        r.raise_for_status()
        payload = r.json()
    out: list[POICandidate] = []
    for el in payload.get("elements", []):
        plat = el.get("lat")
        plon = el.get("lon")
        if plat is None or plon is None:
            continue
        tags = el.get("tags") or {}
        name = tags.get("name:zh") or tags.get("name:en") or tags.get("name") or "viewpoint"
        kind = tags.get("tourism") or tags.get("historic") or "poi"
        d = _haversine_m(lat, lon, plat, plon)
        out.append(POICandidate(
            name=str(name), lat=plat, lon=plon, kind=str(kind),
            source="osm",
            distance_m=round(d, 1),
            bearing_from_user_deg=round(_bearing_from_user(lat, lon, plat, plon), 1),
        ))
    return out


# ---------------------------------------------------------------------------
def _writeback(p: POICandidate) -> None:
    """Insert into poi_kb so next request hits the local cache. We also
    stamp ``source`` and ``fetched_at_utc`` if those columns exist (they
    are added on demand below)."""
    try:
        with _connect_rw() as con:
            _ensure_writeback_schema(con)
            con.execute(
                "INSERT OR IGNORE INTO pois (name, lat, lon, kind, source, fetched_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p.name, p.lat, p.lon, p.kind, p.source,
                 datetime.now(timezone.utc).isoformat()),
            )
    except sqlite3.DatabaseError as e:
        log.info("poi_kb writeback failed: %s", e)


def _ensure_user_spots_schema(con: sqlite3.Connection) -> None:
    """Create the UGC ``user_spots`` table on demand. Idempotent."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS user_spots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "lat REAL NOT NULL, lon REAL NOT NULL, "
        "derived_from TEXT, rating INTEGER, "
        "upvotes INTEGER NOT NULL DEFAULT 1, "
        "scene_kind TEXT, "
        "is_curated INTEGER NOT NULL DEFAULT 0, "
        "created_at_utc TEXT NOT NULL)"
    )
    # Lazy migration: add is_curated column to existing DBs (P1-6.2).
    try:
        cols = {row[1] for row in con.execute(
            "PRAGMA table_info(user_spots)").fetchall()}
        if "is_curated" not in cols:
            con.execute("ALTER TABLE user_spots ADD COLUMN "
                        "is_curated INTEGER NOT NULL DEFAULT 0")
        # A0-5: track owning user so /users/me delete can cascade.
        if "user_id" not in cols:
            con.execute("ALTER TABLE user_spots ADD COLUMN user_id TEXT")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_spots_user ON user_spots(user_id)"
            )
    except sqlite3.DatabaseError:
        pass
    con.execute(
        "CREATE TABLE IF NOT EXISTS user_spot_votes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "spot_id INTEGER NOT NULL, "
        "device_id TEXT, "
        "created_at_utc TEXT NOT NULL)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_spot_votes_device "
        "ON user_spot_votes(device_id, created_at_utc)"
    )


def record_user_spot(lat: float, lon: float, *, rating: int,
                     derived_from: Optional[str] = None,
                     scene_kind: Optional[str] = None,
                     merge_radius_m: float = 5.0,
                     device_id: Optional[str] = None,
                     dedup_window_hours: int = 24,
                     is_curated: bool = False,
                     user_id: Optional[str] = None) -> dict:
    """Insert a user-confirmed spot, OR merge into an existing nearby
    record (within ``merge_radius_m``) by incrementing ``upvotes``.

    Returns ``{"action": "insert"|"merge", "id": int, "upvotes": int}``.
    Safe to call from /feedback regardless of POI density — caller is
    expected to first confirm there is no nearby existing POI hit.
    """
    try:
        with _connect_rw() as con:
            _ensure_user_spots_schema(con)
            deg = (merge_radius_m / 111000) * 1.5
            rows = con.execute(
                "SELECT id, lat, lon, upvotes FROM user_spots "
                "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (lat - deg, lat + deg, lon - deg, lon + deg),
            ).fetchall()
            now_iso = datetime.now(timezone.utc).isoformat()
            for sid, slat, slon, votes in rows:
                if _haversine_m(lat, lon, slat, slon) <= merge_radius_m:
                    # P0-1.6 anti-farming dedup: if same device_id has
                    # already voted on this spot inside the window, no-op.
                    if device_id and dedup_window_hours > 0:
                        cutoff = (datetime.now(timezone.utc).timestamp()
                                   - dedup_window_hours * 3600)
                        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
                        prev = con.execute(
                            "SELECT id FROM user_spot_votes "
                            "WHERE spot_id = ? AND device_id = ? "
                            "AND created_at_utc >= ?",
                            (sid, device_id, cutoff_iso),
                        ).fetchone()
                        if prev:
                            return {"action": "dedup", "id": int(sid),
                                    "upvotes": int(votes or 1)}
                    new_votes = int(votes or 1) + 1
                    con.execute(
                        "UPDATE user_spots SET upvotes = ?, rating = "
                        "MAX(COALESCE(rating, 0), ?) WHERE id = ?",
                        (new_votes, int(rating), sid),
                    )
                    con.execute(
                        "INSERT INTO user_spot_votes (spot_id, device_id, created_at_utc) "
                        "VALUES (?, ?, ?)",
                        (sid, device_id, now_iso),
                    )
                    return {"action": "merge", "id": int(sid),
                            "upvotes": new_votes}
            cur = con.execute(
                "INSERT INTO user_spots "
                "(lat, lon, derived_from, rating, upvotes, scene_kind, "
                "is_curated, created_at_utc, user_id) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (lat, lon, derived_from, int(rating), scene_kind,
                 1 if is_curated else 0, now_iso, user_id),
            )
            new_id = int(cur.lastrowid or 0)
            con.execute(
                "INSERT INTO user_spot_votes (spot_id, device_id, created_at_utc) "
                "VALUES (?, ?, ?)",
                (new_id, device_id, now_iso),
            )
            return {"action": "insert", "id": new_id, "upvotes": 1}
    except sqlite3.DatabaseError as e:
        log.info("user_spots writeback failed: %s", e)
        return {"action": "noop", "id": 0, "upvotes": 0}


def _ensure_writeback_schema(con: sqlite3.Connection) -> None:
    """Lazy migrate the legacy ``pois`` table to add ``source`` /
    ``fetched_at_utc`` columns (idempotent)."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(pois)").fetchall()}
    if "source" not in cols:
        con.execute("ALTER TABLE pois ADD COLUMN source TEXT")
    if "fetched_at_utc" not in cols:
        con.execute("ALTER TABLE pois ADD COLUMN fetched_at_utc TEXT")


@contextmanager
def _connect_ro():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    try:
        # Make sure base tables exist so first-ever query doesn't crash.
        con.execute(
            "CREATE TABLE IF NOT EXISTS pois ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, kind TEXT)"
        )
        yield con
    finally:
        con.close()


@contextmanager
def _connect_rw():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS pois ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, kind TEXT)"
        )
        yield con
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
def to_prompt_block(pois: list[POICandidate]) -> str:
    """Render up to 5 nearby POIs as a plain-text block for the LLM. The
    LLM is told these are *candidate* shot locations and that it may
    reference them in ``rationale``; final inclusion is decided by
    ``shot_fusion`` regardless of LLM output."""
    if not pois:
        return ""
    lines = [
        "── NEARBY POIS（用户 GPS 周围 {n} 个候选机位 / 地标，已自带准确经纬度，"
        "可作为远机位的来源）──".format(n=len(pois)),
    ]
    for p in pois[:5]:
        lines.append(
            "  · {name}（{kind}, {src}） — 距你约 {d} m, 方位 {br}°".format(
                name=p.name, kind=p.kind, src=p.source,
                d=int(p.distance_m), br=int(p.bearing_from_user_deg),
            )
        )
    lines.append(
        "  使用规则：如果你认为某个 POI 是更好的拍摄机位，可以在 rationale 中"
        "推荐用户走过去。**最终是否纳入推荐由后端融合决定**，你不要硬塞 angle.distance > 20。"
    )
    return "\n".join(lines)
