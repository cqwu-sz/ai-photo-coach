"""Lightweight Prometheus exposition (P0-4.3).

We try to use the official ``prometheus_client`` library when installed;
otherwise we serve a tiny hand-rolled text endpoint that publishes the
same counters. Either way the format is compatible with
``/scrape_configs`` in Prometheus + Datadog OpenMetrics.

Counters we expose:
  - ai_photo_coach_analyze_requests_total{status}
  - ai_photo_coach_analyze_latency_ms{stage}
  - ai_photo_coach_poi_lookup_total{source}
  - ai_photo_coach_recon3d_jobs{status}
  - ai_photo_coach_provider_failure_total{provider}
  - ai_photo_coach_ar_nav_total{event}
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from threading import Lock

from fastapi import APIRouter, Response

log = logging.getLogger(__name__)
router = APIRouter(tags=["metrics"])

_lock = Lock()
_counters: dict[str, Counter] = defaultdict(Counter)
_histos: dict[str, list[float]] = defaultdict(list)


def inc(name: str, **labels) -> None:
    """Increment a labeled counter."""
    with _lock:
        _counters[name][_labels_key(labels)] += 1


def observe(name: str, value: float, **labels) -> None:
    """Append a value to a (very simple) histogram bucket."""
    with _lock:
        _histos[(name, _labels_key(labels))].append(value)
        # Keep at most 1000 to bound memory; older samples drop.
        if len(_histos[(name, _labels_key(labels))]) > 1000:
            _histos[(name, _labels_key(labels))] = (
                _histos[(name, _labels_key(labels))][-1000:]
            )


def _labels_key(labels: dict) -> str:
    if not labels:
        return ""
    return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


@router.get("/metrics")
def metrics() -> Response:
    lines: list[str] = []
    with _lock:
        for name, ctr in _counters.items():
            lines.append(f"# TYPE {name} counter")
            for k, v in ctr.items():
                lines.append(f"{name}{{{k}}} {v}" if k else f"{name} {v}")
        for (name, k), values in _histos.items():
            if not values:
                continue
            n = len(values)
            s = sum(values)
            avg = s / n
            mx = max(values)
            mn = min(values)
            base = f"{name}{{{k}}}" if k else name
            lines.append(f"# TYPE {name} summary")
            lines.append(f"{base}_count {n}")
            lines.append(f"{base}_sum {s:.3f}")
            lines.append(f"{base}_avg {avg:.3f}")
            lines.append(f"{base}_max {mx:.3f}")
            lines.append(f"{base}_min {mn:.3f}")
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4")
