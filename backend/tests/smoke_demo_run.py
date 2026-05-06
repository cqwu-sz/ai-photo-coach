"""End-to-end smoke for the no-camera demo path.

Replicates exactly what the browser does when the user clicks
"用示例数据跑一次真分析（无需摄像头）":

  1. GET /dev/sample-manifest
  2. For each frame in the manifest, GET /dev/sample-frame/<i>.jpg
  3. For each reference, GET /dev/sample-reference/<i>.jpg
  4. POST /analyze with all of those + a 4-person meta JSON
  5. Pretty-print the AI response so you can eyeball it

Run while the backend is up:
    python tests/smoke_demo_run.py
"""
from __future__ import annotations

import json
import sys

import requests

BASE = "http://localhost:8000"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("== /dev/sample-manifest")
    manifest = requests.get(f"{BASE}/dev/sample-manifest", timeout=10).json()
    print(f"   frames: {len(manifest['frames'])}, refs: {len(manifest['references'])}")

    frames = []
    for f in manifest["frames"]:
        r = requests.get(f"{BASE}{f['url']}", timeout=10)
        r.raise_for_status()
        frames.append((f, r.content))
    print(f"== fetched {len(frames)} sample frames"
          f" (avg {sum(len(b) for _, b in frames) // len(frames)} bytes)")

    refs = []
    for f in manifest["references"]:
        r = requests.get(f"{BASE}{f['url']}", timeout=10)
        r.raise_for_status()
        refs.append((f, r.content))
    print(f"== fetched {len(refs)} sample references")

    meta = {
        "person_count": 2,
        "quality_mode": "fast",
        "style_keywords": ["cinematic", "moody"],
        "frame_meta": [
            {
                "index": fm["index"],
                "azimuth_deg": fm["azimuth_deg"],
                "pitch_deg": fm.get("pitch_deg", 0),
                "roll_deg": fm.get("roll_deg", 0),
                "timestamp_ms": fm.get("timestamp_ms", 0),
            }
            for fm, _ in frames
        ],
    }

    files = []
    for fm, blob in frames:
        files.append(("frames", (f"frame_{fm['index']}.jpg", blob, "image/jpeg")))
    for fm, blob in refs:
        files.append(
            ("reference_thumbnails", (f"ref_{fm['index']}.jpg", blob, "image/jpeg"))
        )

    print("== POST /analyze ...")
    r = requests.post(
        f"{BASE}/analyze",
        data={"meta": json.dumps(meta)},
        files=files,
        timeout=180,
    )
    if r.status_code != 200:
        print(f"FAILED {r.status_code}\n{r.text[:1500]}")
        return 1

    body = r.json()
    print(f"   model: {body.get('model')}")
    scene = body.get("scene", {})
    print(f"   scene type: {scene.get('type')}, lighting: {scene.get('lighting')}")
    print(f"   scene summary: {scene.get('background_summary', '')[:140]}…")
    for i, shot in enumerate(body.get("shots", [])):
        ang = shot["angle"]
        cam = shot["camera"]
        pose = shot["poses"][0] if shot["poses"] else {}
        print(
            f"   #{i+1} {shot.get('title')} | az={ang['azimuth_deg']:.0f}° "
            f"d={ang['distance_m']:.1f}m | "
            f"{cam['focal_length_mm']:.0f}mm {cam['aperture']} {cam['shutter']} "
            f"ISO{cam['iso']} | pose={pose.get('layout')}/{pose.get('person_count')}p "
            f"thumb={pose.get('reference_thumbnail_id')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
