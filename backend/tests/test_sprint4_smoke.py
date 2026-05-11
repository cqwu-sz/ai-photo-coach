"""Sprint 4 smoke tests: composition + POI knowledge base."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.models import FrameMeta
from app.services.scene_aggregate import _build_composition
from app.services import poi_kb


def _frame(idx: int, sx: float, sy: float) -> FrameMeta:
    """A frame with a 0.20×0.40 subject_box centred at (sx, sy)."""
    bx = max(0.0, min(0.80, sx - 0.10))
    by = max(0.0, min(0.60, sy - 0.20))
    return FrameMeta(
        index=idx, azimuth_deg=idx * 36, pitch_deg=0,
        mean_luma=130, blur_score=6,
        subject_box=[bx, by, 0.20, 0.40],
    )


def test_composition_centred_subject_recommends_thirds():
    fms = [_frame(i, 0.5, 0.5) for i in range(5)]
    rot, sym, facts = _build_composition(fms)
    assert sym == 1.0
    assert rot > 0.15
    assert any("三分点" in f for f in facts)


def test_composition_already_on_thirds_quiet():
    fms = [_frame(i, 1/3, 1/3) for i in range(5)]
    rot, sym, facts = _build_composition(fms)
    assert rot < 0.05
    # No advice fires when subject is already well placed.
    assert facts == []


def test_composition_returns_none_without_subject():
    fms = [
        FrameMeta(index=i, azimuth_deg=i*36, pitch_deg=0,
                  mean_luma=130, blur_score=6)
        for i in range(5)
    ]
    rot, sym, facts = _build_composition(fms)
    assert rot is None and sym is None and facts == []


def test_poi_kb_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(poi_kb, "DB_PATH", tmp_path / "poi.db")
    # Seed one POI + a few peer shots.
    with poi_kb._connect() as con:
        cur = con.execute(
            "INSERT INTO pois (name, lat, lon, kind) VALUES (?, ?, ?, ?)",
            ("West Lake Broken Bridge", 30.2580, 120.1480, "scenic"),
        )
        pid = cur.lastrowid
        for f, k in [(35, 5500), (50, 5400), (35, 5600), (24, 5500)]:
            con.execute(
                "INSERT INTO peer_shots (poi_id, focal_eq, white_balance_k) VALUES (?, ?, ?)",
                (pid, f, k),
            )

    # Within 200 m → match.
    near = poi_kb.nearest_poi(30.2581, 120.1481, radius_m=200)
    assert near is not None and near.name.startswith("West Lake")
    exif = poi_kb.median_exif_for_poi(near.id)
    assert exif is not None
    assert exif.focal_eq_mm in (35, 24, 50)
    assert 5400 <= exif.white_balance_k <= 5600
    assert exif.sample_size == 4

    # 5 km away → miss.
    far = poi_kb.nearest_poi(30.30, 120.20)
    assert far is None
