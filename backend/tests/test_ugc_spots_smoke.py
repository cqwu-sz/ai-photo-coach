"""W2 — UGC spots reinforcement smoke."""
from __future__ import annotations

from app.services import poi_lookup


def test_record_user_spot_inserts_then_merges(tmp_path, monkeypatch):
    db = tmp_path / "poi_kb.db"
    monkeypatch.setattr(poi_lookup, "DB_PATH", db)

    r1 = poi_lookup.record_user_spot(31.2389, 121.4905, rating=5,
                                     derived_from="外滩观景台")
    assert r1["action"] == "insert"
    r2 = poi_lookup.record_user_spot(31.23890, 121.49050, rating=4,
                                     derived_from="外滩观景台",
                                     merge_radius_m=10.0)
    assert r2["action"] == "merge"
    assert r2["upvotes"] >= 2
