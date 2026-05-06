from fastapi.testclient import TestClient

from app.main import app


def test_manifest_lists_seeded_poses():
    client = TestClient(app)
    r = client.get("/pose-library/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 5
    ids = {p["id"] for p in body["poses"]}
    assert "pose_single_relaxed_001" in ids
    assert "pose_two_high_low_001" in ids
    assert "pose_three_triangle_001" in ids


def test_thumbnail_round_trip():
    client = TestClient(app)
    r = client.get("/pose-library/thumbnail/pose_single_relaxed_001.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert len(r.content) > 100


def test_thumbnail_404():
    client = TestClient(app)
    r = client.get("/pose-library/thumbnail/not_a_pose.png")
    assert r.status_code == 404
