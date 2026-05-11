"""P1-5.3 — micro-benchmark for /analyze prefetch parallelism.

Usage: python -m scripts.perf_analyze --runs 10

Generates a fixed mock CaptureMeta with geo + walk_segment + 5 ref
images, fires N concurrent /analyze calls against the in-process app
and reports p50 / p95 / p99 latency. Compare before/after a refactor.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image
from starlette.testclient import TestClient

from app.main import app


def _fake_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(120, 130, 140)).save(buf, format="JPEG")
    return buf.getvalue()


async def _run_one(client: TestClient) -> float:
    meta = {
        "person_count": 1,
        "scene_mode": "portrait",
        "quality_mode": "fast",
        "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
        "geo": {"lat": 31.2389, "lon": 121.4905},
    }
    files = [
        ("frames", (f"f{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(8)
    ]
    refs = [
        ("reference_thumbnails", (f"r{i}.jpg", io.BytesIO(_fake_jpeg()), "image/jpeg"))
        for i in range(5)
    ]
    t0 = time.monotonic()
    r = client.post("/analyze", data={"meta": json.dumps(meta)},
                     files=files + refs)
    elapsed = (time.monotonic() - t0) * 1000
    assert r.status_code == 200, r.text
    return elapsed


async def _amain(runs: int) -> int:
    client = TestClient(app)
    samples = []
    for i in range(runs):
        elapsed = await _run_one(client)
        samples.append(elapsed)
        print(f"  run {i+1}/{runs}: {elapsed:.0f} ms")
    samples.sort()
    n = len(samples)
    p50 = samples[n // 2]
    p95 = samples[min(n - 1, int(n * 0.95))]
    p99 = samples[min(n - 1, int(n * 0.99))]
    print(f"\nRESULT runs={n} p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms "
          f"min={min(samples):.0f}ms max={max(samples):.0f}ms "
          f"mean={statistics.mean(samples):.0f}ms")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=int, default=10)
    args = p.parse_args()
    return asyncio.run(_amain(args.runs))


if __name__ == "__main__":
    raise SystemExit(main())
