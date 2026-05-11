"""Hot-reloadable calibration overrides for distance K's and style WB.

Production loop:
    1. `scripts/recalibrate_from_feedback.py --apply` writes
       `backend/data/calibration.json` nightly from /feedback DB.
    2. This module mtime-polls that file and re-loads when it changes,
       so a fresh calibration takes effect without a service restart.
    3. `scene_aggregate._pick_lens` and `style_compliance` read the
       overrides via `current()`.

The file format is a flat dict (all keys optional):

    {
      "K_face": 0.20,
      "K_body": 1.30,
      "style_wb_centres": {
        "japanese": 5500,
        "ambient":  4200
      }
    }

Any missing key falls back to the in-source default; the defaults
themselves never change at runtime.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CALIB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "calibration.json"


@dataclass(frozen=True)
class CalibrationSnapshot:
    k_face: Optional[float] = None
    k_body: Optional[float] = None
    style_wb_centres: dict[str, int] = field(default_factory=dict)
    mtime: float = 0.0


_lock = threading.Lock()
_cached: CalibrationSnapshot = CalibrationSnapshot()


def current() -> CalibrationSnapshot:
    """Return the latest calibration. Cheap mtime check (one stat per
    call) means it's safe to call from hot paths."""
    global _cached
    try:
        if not CALIB_PATH.exists():
            if _cached.mtime != 0.0:
                with _lock:
                    _cached = CalibrationSnapshot()
            return _cached
        mtime = CALIB_PATH.stat().st_mtime
        if mtime == _cached.mtime:
            return _cached
        with _lock:
            if mtime == _cached.mtime:    # double-check under lock
                return _cached
            try:
                payload = json.loads(CALIB_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("calibration.json unreadable, keeping previous: %s", e)
                return _cached
            _cached = CalibrationSnapshot(
                k_face=_maybe_float(payload.get("K_face")),
                k_body=_maybe_float(payload.get("K_body")),
                style_wb_centres={
                    str(k): int(v)
                    for k, v in (payload.get("style_wb_centres") or {}).items()
                    if isinstance(v, (int, float))
                },
                mtime=mtime,
            )
            log.info("calibration reloaded: K_face=%s K_body=%s wb_centres=%d",
                     _cached.k_face, _cached.k_body, len(_cached.style_wb_centres))
            return _cached
    except Exception as e:                          # pragma: no cover
        log.warning("calibration lookup failed: %s", e)
        return _cached


def _maybe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
