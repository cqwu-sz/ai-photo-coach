"""Smoke tests for v12 follow-ups: 3-way horizon, calibration hot
reload, POI seeder parsing, calibration → compliance feedback."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.models import FrameMeta
from app.services import calibration as calibration_service
from app.services import poi_kb
from app.services.scene_aggregate import _vote_horizon


def _frame(idx: int, **kw) -> FrameMeta:
    return FrameMeta(
        index=idx, azimuth_deg=idx * 36, pitch_deg=0,
        mean_luma=130, blur_score=6,
        horizon_y=kw.get("hy"),
        horizon_y_vision=kw.get("hyv"),
        horizon_y_gravity=kw.get("hyg"),
        sky_mask_top_pct=kw.get("sky", 0.30),
    )


def test_horizon_3way_high_confidence_when_all_agree():
    fms = [_frame(i, hy=0.50, hyv=0.51, hyg=0.49) for i in range(5)]
    y, conf, ok = _vote_horizon(fms)
    assert ok is True
    assert conf == "high"
    assert 0.49 <= y <= 0.51


def test_horizon_3way_medium_when_two_of_three_agree():
    # gravity is the outlier (~0.20 below).
    fms = [_frame(i, hy=0.50, hyv=0.51, hyg=0.30) for i in range(5)]
    y, conf, _ = _vote_horizon(fms)
    assert conf == "medium"
    # Should pick the agreeing pair (image+vision), not the outlier.
    assert 0.49 <= y <= 0.51


def test_horizon_3way_low_when_all_disagree():
    fms = [_frame(i, hy=0.30, hyv=0.55, hyg=0.80) for i in range(5)]
    _, conf, _ = _vote_horizon(fms)
    assert conf == "low"


def test_calibration_hot_reload(tmp_path, monkeypatch):
    p = tmp_path / "calibration.json"
    monkeypatch.setattr(calibration_service, "CALIB_PATH", p)
    # Reset the module cache so the test starts from a clean slate.
    monkeypatch.setattr(calibration_service, "_cached", calibration_service.CalibrationSnapshot())

    # No file yet → all defaults None.
    snap = calibration_service.current()
    assert snap.k_face is None
    assert snap.style_wb_centres == {}

    # Write v1.
    p.write_text(json.dumps({
        "K_face": 0.20,
        "style_wb_centres": {"japanese": 5500},
    }))
    snap1 = calibration_service.current()
    assert snap1.k_face == 0.20
    assert snap1.style_wb_centres == {"japanese": 5500}

    # Re-read with no change → same instance (cache hit).
    snap1b = calibration_service.current()
    assert snap1b is snap1

    # Bump mtime + write v2 → reload picks up new value.
    time.sleep(0.05)
    p.write_text(json.dumps({"K_face": 0.22}))
    # Force mtime forward in case fs resolution is coarse.
    new_mtime = p.stat().st_mtime + 1
    import os
    os.utime(p, (new_mtime, new_mtime))
    snap2 = calibration_service.current()
    assert snap2.k_face == 0.22
    assert snap2.style_wb_centres == {}


def test_seed_poi_parses_overpass_payload(monkeypatch, tmp_path):
    """Don't actually hit the network — feed a canned Overpass payload
    into the parser and assert the right rows land in poi_kb."""
    monkeypatch.setattr(poi_kb, "DB_PATH", tmp_path / "poi.db")

    import urllib.request, io, json as _json

    canned = {
        "elements": [
            {"type": "node", "lat": 30.258, "lon": 120.148,
             "tags": {"tourism": "viewpoint", "name": "Broken Bridge"}},
            {"type": "node", "lat": 30.259, "lon": 120.149,
             "tags": {"natural": "peak", "name": "Lone Hill"}},
            # Missing name → skipped.
            {"type": "node", "lat": 30.260, "lon": 120.150,
             "tags": {"tourism": "attraction"}},
            # No matching tag → skipped.
            {"type": "node", "lat": 30.261, "lon": 120.151,
             "tags": {"shop": "convenience", "name": "FamilyMart"}},
        ]
    }

    def fake_urlopen(req, timeout=0):
        body = _json.dumps(canned).encode()
        class _R:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Re-import the seeder so it picks up the patched urllib.
    import importlib, sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    seed_poi = importlib.import_module("seed_poi")
    pois = list(seed_poi._query_overpass((30.20, 120.10, 30.30, 120.20)))
    assert {p["name"] for p in pois} == {"Broken Bridge", "Lone Hill"}
    inserted = seed_poi._insert(pois)
    assert inserted == 2
    # Re-insert is a no-op.
    assert seed_poi._insert(pois) == 0


def test_compliance_uses_calibrated_wb_centre(tmp_path, monkeypatch):
    """When calibration.json overrides 'japanese' WB centre to 5800K,
    a shot at 5800K should be in-range even though the default preset
    centres around 5200K."""
    from app.services import style_compliance, style_feasibility as sf

    # Override calibration to recentre japanese WB at 5800.
    p = tmp_path / "calibration.json"
    monkeypatch.setattr(calibration_service, "CALIB_PATH", p)
    monkeypatch.setattr(calibration_service, "_cached", calibration_service.CalibrationSnapshot())
    p.write_text(json.dumps({"style_wb_centres": {"japanese": 5800}}))

    # Build a fake shot with WB=5800, focal=35, EV=0.
    class _Cam:
        white_balance_k = 5800
        focal_length_mm = 35
        ev_compensation = 0.0
    class _Shot:
        camera = _Cam()
        rationale = ""
        id = "shot_1"
        style_match = None
    shots = [_Shot()]
    rep = style_compliance.validate_and_clamp(shots, ["japanese"])
    # If the override widened the centre to 5800, our 5800 should sit
    # in range and rate should be 1.0.
    assert rep.rate == 1.0
    assert rep.clamped_count == 0
