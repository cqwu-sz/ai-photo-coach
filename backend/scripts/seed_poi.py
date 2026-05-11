"""Seed `data/poi_kb.db` from OpenStreetMap Overpass API.

Pulls landmarks / tourist attractions inside a bounding box and writes
them into the `pois` table for `poi_kb.nearest_poi` lookups.

Why Overpass instead of bundled GeoJSON:
    - Free (no API key), CC-BY-SA data, no licensing footgun for App
      Store review (we don't redistribute the dump, only query at
      seed time).
    - We can refresh per-region on demand (run nightly with cron for
      cities the user actually visits, instead of shipping 4 GB of
      planet data).

Usage:
    python scripts/seed_poi.py --bbox 30.20,120.10,30.30,120.20
    python scripts/seed_poi.py --city Hangzhou --radius 15

`--bbox` takes (min_lat, min_lon, max_lat, max_lon).
`--city` is a Nominatim search string; `--radius` is km (default 10).

Idempotent: running twice on the same area dedupes by (name, lat, lon).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Iterable, Optional

# Re-use the production schema/connector — never roll our own here.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from app.services import poi_kb  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ai-ios-photo-coach/0.1 (poi seeder)"

# OSM tags that map to scenic/landmark POIs worth photographing.
# Map each tag → kind label we store in `pois.kind`.
_TAG_KINDS = [
    ("tourism=attraction",  "attraction"),
    ("tourism=viewpoint",   "viewpoint"),
    ("tourism=museum",      "museum"),
    ("historic=monument",   "monument"),
    ("historic=memorial",   "monument"),
    ("historic=castle",     "landmark"),
    ("leisure=park",        "park"),
    ("natural=peak",        "peak"),
    ("natural=beach",       "beach"),
    ("waterway=waterfall",  "waterfall"),
    ("amenity=place_of_worship", "temple"),
]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", help="min_lat,min_lon,max_lat,max_lon")
    p.add_argument("--city", help="Nominatim city name (alternative to --bbox)")
    p.add_argument("--radius", type=float, default=10.0,
                   help="km radius around city centre when --city is used")
    p.add_argument("--dry-run", action="store_true",
                   help="Print parsed POIs without writing to DB.")
    args = p.parse_args(argv[1:])

    if args.bbox:
        bbox = _parse_bbox(args.bbox)
    elif args.city:
        bbox = _bbox_from_city(args.city, args.radius)
    else:
        p.error("must give --bbox or --city")
        return 2

    print(f"querying Overpass for bbox={bbox} ...")
    pois = list(_query_overpass(bbox))
    print(f"  → {len(pois)} POIs returned")
    if args.dry_run:
        for poi in pois[:20]:
            print(f"  · {poi['name']:40s} ({poi['kind']:12s}) {poi['lat']:.5f},{poi['lon']:.5f}")
        if len(pois) > 20:
            print(f"  ... and {len(pois)-20} more")
        return 0

    inserted = _insert(pois)
    print(f"inserted {inserted} new POIs (skipped duplicates)")
    return 0


def _parse_bbox(s: str) -> tuple[float, float, float, float]:
    a, b, c, d = (float(x) for x in s.split(","))
    return (a, b, c, d)


def _bbox_from_city(city: str, radius_km: float) -> tuple[float, float, float, float]:
    """Use Nominatim to geocode the city name, then derive a bbox of
    side ``2 * radius_km`` km around it. Includes the legal user-agent
    Nominatim requires for reliable service.
    """
    qs = urllib.parse.urlencode({"q": city, "format": "json", "limit": "1"})
    req = urllib.request.Request(NOMINATIM_URL + "?" + qs, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read())
    if not rows:
        raise RuntimeError(f"no Nominatim hit for city: {city}")
    lat = float(rows[0]["lat"])
    lon = float(rows[0]["lon"])
    # 1 deg lat ≈ 111 km; 1 deg lon ≈ 111 × cos(lat) km.
    import math
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def _query_overpass(bbox: tuple[float, float, float, float]) -> Iterable[dict]:
    """Build one Overpass QL union query for all our tag kinds, send
    it, and yield parsed POIs. We intentionally cap to nodes (skip
    way/relation centroids) to keep results to "single point" features
    a photographer can stand at.
    """
    lat1, lon1, lat2, lon2 = bbox
    parts = []
    for tag, _ in _TAG_KINDS:
        k, v = tag.split("=")
        parts.append(f'  node["{k}"="{v}"]({lat1},{lon1},{lat2},{lon2});')
    ql = "[out:json][timeout:40];\n(\n" + "\n".join(parts) + "\n);\nout body 800;"
    data = urllib.parse.urlencode({"data": ql}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    for el in payload.get("elements", []):
        if el.get("type") != "node":
            continue
        tags = el.get("tags") or {}
        name = tags.get("name:en") or tags.get("name") or tags.get("name:zh")
        if not name:
            continue
        kind = _kind_for(tags)
        if kind is None:
            continue
        yield {
            "name": name,
            "lat":  el["lat"],
            "lon":  el["lon"],
            "kind": kind,
        }


def _kind_for(tags: dict) -> Optional[str]:
    for tag, kind in _TAG_KINDS:
        k, v = tag.split("=")
        if tags.get(k) == v:
            return kind
    return None


def _insert(pois: list[dict]) -> int:
    inserted = 0
    with poi_kb._connect() as con:
        for poi in pois:
            existing = con.execute(
                "SELECT id FROM pois WHERE name = ? AND ABS(lat - ?) < 1e-5 AND ABS(lon - ?) < 1e-5",
                (poi["name"], poi["lat"], poi["lon"]),
            ).fetchone()
            if existing:
                continue
            con.execute(
                "INSERT INTO pois (name, lat, lon, kind) VALUES (?, ?, ?, ?)",
                (poi["name"], poi["lat"], poi["lon"], poi["kind"]),
            )
            inserted += 1
    return inserted


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
