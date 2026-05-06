"""Pretty-print one Gemini analyze response so we can read what it actually
generated. Encodes Chinese as UTF-8 and dumps full content."""
from __future__ import annotations

import io
import json
import sys

import requests
from PIL import Image, ImageDraw

BASE = "http://localhost:8000"


def make_synthetic_scene(idx: int) -> bytes:
    palette = [
        (40, 40, 60), (80, 60, 100), (140, 110, 90), (200, 130, 60),
        (240, 180, 80), (200, 140, 60), (140, 100, 80), (80, 70, 90),
    ]
    img = Image.new("RGB", (640, 360), color=palette[idx % len(palette)])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 270, 640, 360), fill=(40, 100, 60))
    d.rectangle((220, 180, 420, 270), fill=(120, 90, 70))
    d.text((10, 10), f"frame {idx}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=78)
    return buf.getvalue()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    meta = {
        "person_count": 2,
        "quality_mode": "fast",
        "style_keywords": ["clean", "moody", "cinematic"],
        "frame_meta": [
            {"index": i, "azimuth_deg": (i * 30) % 360, "pitch_deg": 0, "roll_deg": 0, "timestamp_ms": i * 200}
            for i in range(8)
        ],
    }
    files = [
        ("frames", (f"f{i}.jpg", make_synthetic_scene(i), "image/jpeg"))
        for i in range(8)
    ]
    r = requests.post(f"{BASE}/analyze",
                      data={"meta": json.dumps(meta)},
                      files=files,
                      timeout=120)
    if r.status_code != 200:
        print(f"FAILED {r.status_code}\n{r.text}")
        return 1

    body = r.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
