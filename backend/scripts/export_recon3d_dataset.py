"""P2-11.3 — export every cached SparseModel as a flat dataset suitable
for training a future lightweight visual-localization model.

Output layout (newline-delimited JSON):
    data/recon3d_dataset.jsonl
        {"job_id": "...", "geohash": "...", "points_count": N,
         "bbox_lat": [...], "bbox_lon": [...]}

Real training would also dump per-image (pose, intrinsics, descriptor)
records — those need to be persisted from the recon3d worker first.
This script is the data pipeline plumbing; the heavy lifting upstream
is tracked separately.

Run monthly:
    python -m scripts.export_recon3d_dataset
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "recon3d_models"
OUT = ROOT / "data" / "recon3d_dataset.jsonl"

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("export_recon3d_dataset")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    args = p.parse_args()
    if not CACHE.exists():
        log.info("no recon3d_models cache; nothing to export")
        OUT.write_text("", encoding="utf-8")
        return 0
    n = 0
    with OUT.open("w", encoding="utf-8") as out:
        for gh_dir in CACHE.iterdir():
            if not gh_dir.is_dir():
                continue
            for f in gh_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except Exception as e:                            # noqa: BLE001
                    log.info("skip %s: %s", f, e)
                    continue
                data["geohash"] = gh_dir.name
                out.write(json.dumps(data, ensure_ascii=False) + "\n")
                n += 1
    log.info("wrote %d rows -> %s", n, OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
