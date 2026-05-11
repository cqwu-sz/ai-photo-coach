"""P2-10.2 — weekly job: cluster the ReferenceFingerprints stored from
analyze runs and emit per-cluster centroids for prompt template tuning.

Sources:
  - shot_results.recommendation_snapshot_json may carry a 'reference_fingerprints'
    array (added by AnalyzeService). We aggregate every palette and
    contrast/saturation band into rough "style-cluster" buckets.

Output:
  - data/style_centroids.json — list of {centroid_palette, contrast,
    saturation, mood_keywords, n}.

Run weekly:
    0 5 * * 0 cd /opt/ai-photo-coach/backend && \
        python -m scripts.weekly_style_cluster
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHOT_DB = ROOT / "data" / "shot_results.db"
OUT = ROOT / "data" / "style_centroids.json"

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("weekly_style_cluster")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--k", type=int, default=8)
    args = p.parse_args()

    if not SHOT_DB.exists():
        log.info("no shot_results.db; nothing to cluster")
        OUT.write_text("[]", encoding="utf-8")
        return 0

    with sqlite3.connect(str(SHOT_DB)) as con:
        rows = con.execute(
            "SELECT recommendation_snapshot_json FROM shot_results "
            "WHERE recommendation_snapshot_json IS NOT NULL"
        ).fetchall()
    samples: list[dict] = []
    for (snap,) in rows:
        try:
            d = json.loads(snap)
        except Exception:                                            # noqa: BLE001
            continue
        for fp in d.get("reference_fingerprints", []) or []:
            palette = fp.get("palette") or []
            if not palette:
                continue
            samples.append({
                "palette": palette,
                "contrast": fp.get("contrast_band", "mid"),
                "saturation": fp.get("saturation_band", "mid"),
                "mood": fp.get("mood_keywords", []),
            })
    log.info("collected %d ReferenceFingerprint samples", len(samples))
    if not samples:
        OUT.write_text("[]", encoding="utf-8")
        return 0

    # Coarse cluster: bucket by (contrast, saturation) and emit a
    # centroid palette per bucket as the average of the dominant
    # colour. Production code would use real k-means in CIELAB.
    buckets: dict[tuple[str, str], list[dict]] = {}
    for s in samples:
        k = (s["contrast"], s["saturation"])
        buckets.setdefault(k, []).append(s)
    centroids = []
    for (con_band, sat_band), items in buckets.items():
        rgbs = [_hex_to_rgb(item["palette"][0]) for item in items]
        cx = sum(r for r, _, _ in rgbs) / len(rgbs)
        cy = sum(g for _, g, _ in rgbs) / len(rgbs)
        cz = sum(b for _, _, b in rgbs) / len(rgbs)
        moods: dict[str, int] = {}
        for item in items:
            for m in item["mood"]:
                moods[m] = moods.get(m, 0) + 1
        top_moods = sorted(moods.items(), key=lambda kv: kv[1], reverse=True)[:5]
        centroids.append({
            "centroid_dominant_hex": _rgb_to_hex((cx, cy, cz)),
            "contrast_band": con_band,
            "saturation_band": sat_band,
            "mood_keywords": [m for m, _ in top_moods],
            "n": len(items),
        })
    centroids.sort(key=lambda c: c["n"], reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(centroids, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    log.info("wrote %d centroids -> %s", len(centroids), OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
