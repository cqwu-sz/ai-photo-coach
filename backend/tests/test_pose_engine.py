from app.models import Layout, PersonPose, PoseSuggestion
from app.services.pose_engine import fallback_pose, map_to_library


def test_fallback_pose_single():
    p = fallback_pose(1)
    assert p.person_count == 1
    assert p.layout == Layout.single
    assert len(p.persons) == 1


def test_fallback_pose_two():
    p = fallback_pose(2)
    assert p.person_count == 2
    assert p.layout == Layout.high_low_offset
    assert len(p.persons) == 2


def test_fallback_pose_three():
    p = fallback_pose(3)
    assert p.person_count == 3
    assert p.layout == Layout.triangle
    assert len(p.persons) == 3


def test_fallback_pose_four():
    p = fallback_pose(4)
    assert p.person_count == 4
    assert p.layout == Layout.cluster
    assert len(p.persons) == 4


def test_map_to_library_keeps_valid_id():
    library = [
        {"id": "pose_two_high_low_001", "person_count": 2, "layout": "high_low_offset"},
        {"id": "pose_two_side_001", "person_count": 2, "layout": "side_by_side"},
    ]
    p = PoseSuggestion(
        person_count=2,
        layout=Layout.high_low_offset,
        persons=[PersonPose(role="person_a"), PersonPose(role="person_b")],
        reference_thumbnail_id="pose_two_high_low_001",
    )
    out = map_to_library(p, library)
    assert out.reference_thumbnail_id == "pose_two_high_low_001"


def test_map_to_library_replaces_invalid_id():
    library = [
        {"id": "pose_two_high_low_001", "person_count": 2, "layout": "high_low_offset"},
    ]
    p = PoseSuggestion(
        person_count=2,
        layout=Layout.high_low_offset,
        persons=[PersonPose(role="person_a"), PersonPose(role="person_b")],
        reference_thumbnail_id="bogus",
    )
    out = map_to_library(p, library)
    assert out.reference_thumbnail_id == "pose_two_high_low_001"


def test_map_to_library_returns_none_when_empty():
    p = PoseSuggestion(
        person_count=2,
        layout=Layout.high_low_offset,
        persons=[PersonPose(role="person_a"), PersonPose(role="person_b")],
    )
    out = map_to_library(p, [])
    assert out.reference_thumbnail_id is None
