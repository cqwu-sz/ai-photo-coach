"""Sprint 4 — light POI / peer-shots knowledge base.

Tiny sqlite-backed lookup of "what other people shot here" so the LLM
can borrow community-validated EXIF medians (focal length, white
balance) and quick visual references for the user's GPS area.

Schema (created lazily):
    pois            — id, name, lat, lon, kind (landmark/scenic/...)
    peer_shots      — id, poi_id, focal_eq, white_balance_k, taken_at_utc

Seeding: this file ships empty. Operators populate it via
``scripts/seed_poi.py`` (POIs from OSM dumps) and via the /feedback
endpoint (peer_shots derived from real users' captures).

API:
    nearest_poi(lat, lon, radius_m=200) -> Optional[POI]
    median_exif_for_poi(poi_id) -> Optional[ExifMedian]
    to_prompt_block(poi, exif) -> str
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "poi_kb.db"
# v12.2 — CC0 reference manifests (one per POI × style) live next to
# the web bundle so the static server already vends them. The POI
# prompt block links to a couple of these so the LLM sees real
# photographer compositions for the user's location.
REFS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "web" / "img" / "poi"


@dataclass(frozen=True, slots=True)
class POI:
    id: int
    name: str
    lat: float
    lon: float
    kind: str


@dataclass(frozen=True, slots=True)
class ExifMedian:
    focal_eq_mm: Optional[int]
    white_balance_k: Optional[int]
    sample_size: int


def nearest_poi(lat: float, lon: float, radius_m: int = 200) -> Optional[POI]:
    """Find the closest POI within ``radius_m``. Returns None when the
    DB is empty or nothing is close enough.
    """
    with _connect() as con:
        # 0.001 deg ≈ 111 m at the equator; we widen the bounding box
        # then haversine-filter in Python for accuracy without PostGIS.
        deg = (radius_m / 111000) * 1.5
        rows = con.execute(
            "SELECT id, name, lat, lon, kind FROM pois "
            "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - deg, lat + deg, lon - deg, lon + deg),
        ).fetchall()
    best: Optional[tuple[float, POI]] = None
    for rid, name, plat, plon, kind in rows:
        d = _haversine_m(lat, lon, plat, plon)
        if d <= radius_m and (best is None or d < best[0]):
            best = (d, POI(rid, name, plat, plon, kind))
    return best[1] if best else None


def median_exif_for_poi(poi_id: int) -> Optional[ExifMedian]:
    with _connect() as con:
        rows = con.execute(
            "SELECT focal_eq, white_balance_k FROM peer_shots WHERE poi_id = ?",
            (poi_id,),
        ).fetchall()
    if not rows:
        return None
    focals = [r[0] for r in rows if r[0] is not None]
    wbs    = [r[1] for r in rows if r[1] is not None]
    return ExifMedian(
        focal_eq_mm=int(median(focals)) if focals else None,
        white_balance_k=int(median(wbs)) if wbs else None,
        sample_size=len(rows),
    )


def to_prompt_block(poi: POI, exif: Optional[ExifMedian],
                    style_keywords: Optional[list[str]] = None) -> str:
    lines = [
        "  ── PEER SHOTS — 同地点其他用户的真实数据 ──",
        f"    · 地点：{poi.name}（{poi.kind}）",
    ]
    if exif and exif.sample_size:
        if exif.focal_eq_mm:
            lines.append(f"    · 历史焦段中位数：{exif.focal_eq_mm}mm（n={exif.sample_size}）")
        if exif.white_balance_k:
            lines.append(f"    · 历史白平衡中位数：{exif.white_balance_k}K")
        lines.append(
            "    使用规则：这些是真实用户在该地点出片的中位 EXIF；如果你"
            "推荐的焦段 / 白平衡偏离 ±25%，必须在 rationale 解释为何。"
        )
    refs = nearby_reference_photos(poi, style_keywords or [], limit=3)
    if refs:
        lines.append("    · CC0 参考照片（Unsplash）：")
        for r in refs:
            lines.append(f"      - {r['author_name']}: {r['permalink']}")
        lines.append(
            "    使用规则：这些参考是社区公开的同地点照片；可以从中借鉴构图"
            "/ 光位 / 色调，但不能照抄；必须在 rationale 简述借鉴了哪个元素。"
        )
    return "\n".join(lines)


def nearby_reference_photos(poi: POI, style_keywords: list[str], limit: int = 3) -> list[dict]:
    """Look up CC0 references built by ``scripts/build_poi_refs.py``.
    Picks the first style keyword whose manifest exists for this POI;
    falls back to any available style. Returns up to ``limit`` items
    each shaped like manifest.json[items][i].
    """
    if not REFS_ROOT.exists():
        return []
    slug = _slugify(poi.name)
    base = REFS_ROOT / slug
    if not base.exists():
        return []
    candidates = list(style_keywords) + [d.name for d in base.iterdir() if d.is_dir()]
    seen: set[str] = set()
    for style in candidates:
        if not style or style in seen:
            continue
        seen.add(style)
        manifest = base / style / "manifest.json"
        if not manifest.exists():
            continue
        try:
            import json
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = payload.get("items") or []
        if items:
            return items[:limit]
    return []


def _slugify(name: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", name.lower())
    return s.strip("-") or "poi"


# ---------------------------------------------------------------------------
@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_schema(con)
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS pois (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT NOT NULL,
            lat   REAL NOT NULL,
            lon   REAL NOT NULL,
            kind  TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_pois_geo ON pois(lat, lon)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS peer_shots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            poi_id          INTEGER NOT NULL,
            focal_eq        INTEGER,
            white_balance_k INTEGER,
            taken_at_utc    TEXT,
            FOREIGN KEY (poi_id) REFERENCES pois(id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_peer_shots_poi ON peer_shots(poi_id)")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
