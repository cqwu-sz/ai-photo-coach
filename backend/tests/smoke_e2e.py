"""Stand-alone E2E smoke script (not a pytest).

Hits the actually-running backend at http://localhost:8000 with a request
shaped like what the web frontend builds, then prints a digest of what the
result page would render. Use to verify the end-to-end loop after starting
uvicorn manually.

    python backend/tests/smoke_e2e.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import requests
from PIL import Image


BASE = "http://localhost:8000"


def make_jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color=color).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def main() -> int:
    print(f"== healthz")
    h = requests.get(f"{BASE}/healthz", timeout=5).json()
    print(json.dumps(h, indent=2))

    print(f"\n== /pose-library/manifest")
    m = requests.get(f"{BASE}/pose-library/manifest", timeout=5).json()
    print(f"poses: {m['count']}")

    print(f"\n== /analyze (mock unless GEMINI key set)")
    meta = {
        "person_count": 2,
        "quality_mode": "fast",
        "style_keywords": ["clean", "moody"],
        "frame_meta": [
            {
                "index": i,
                "azimuth_deg": (i * 30) % 360,
                "pitch_deg": 0,
                "roll_deg": 0,
                "timestamp_ms": i * 200,
            }
            for i in range(8)
        ],
    }
    palette = [
        (40, 40, 60),
        (80, 60, 100),
        (140, 110, 90),
        (200, 130, 60),
        (240, 180, 80),
        (200, 140, 60),
        (140, 100, 80),
        (80, 70, 90),
    ]
    files = [
        ("frames", (f"frame_{i}.jpg", make_jpeg(palette[i]), "image/jpeg"))
        for i in range(8)
    ]
    r = requests.post(
        f"{BASE}/analyze",
        data={"meta": json.dumps(meta)},
        files=files,
        timeout=60,
    )
    if r.status_code != 200:
        print(f"FAILED {r.status_code}: {r.text}")
        return 1
    body = r.json()
    print(f"model: {body.get('model')}")
    print(f"scene: {body['scene']['type']} / {body['scene']['lighting']}")
    print(f"shots: {len(body['shots'])}")
    for i, s in enumerate(body["shots"]):
        cam = s["camera"]
        pose = s["poses"][0]
        print(
            f"  #{i+1} {s.get('title','')} - "
            f"{cam['focal_length_mm']:.0f}mm {cam['aperture']} {cam['shutter']} ISO{cam['iso']}, "
            f"pose={pose['layout']}/{pose['person_count']}p, "
            f"thumb={pose.get('reference_thumbnail_id','-')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
