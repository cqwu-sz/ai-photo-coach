"""v7 Phase E — pose-id → Mixamo-animation mapping tests.

Catches the most common shipping bug: someone adds a new pose KB entry
but forgets to wire it into ``backend/app/knowledge/animations/pose_to_mixamo.json``,
so the web 3D preview / iOS AR shows a default idle for that pose
instead of the intended motion.

Tests:
    1. Every pose KB id has a Mixamo mapping
    2. Mapping is loaded into a flat dict cleanly
    3. lookup_mixamo_for_pose returns the correct id with proper
       fallback when the id is unknown
    4. fallback_by_count covers all the person counts we ever ship
       (1, 2, 3, 4)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.services.knowledge import (
    load_pose_to_mixamo,
    load_pose_to_mixamo_raw,
    load_poses,
    lookup_mixamo_for_pose,
)


@pytest.fixture(scope="module")
def settings():
    return get_settings()


@pytest.fixture(scope="module")
def all_pose_ids(settings) -> set[str]:
    poses = load_poses(str(settings.kb_poses_path))
    ids = {p.get("id") for p in poses if p.get("id")}
    return ids


@pytest.fixture(scope="module")
def mixamo_map(settings):
    return load_pose_to_mixamo(str(settings.kb_animations_path))


@pytest.fixture(scope="module")
def mixamo_raw(settings):
    return load_pose_to_mixamo_raw(str(settings.kb_animations_path))


def test_all_pose_kb_ids_have_mixamo_mapping(all_pose_ids, mixamo_map):
    """The killer test — adding a pose KB entry without a mapping fails CI."""
    missing = sorted(all_pose_ids - set(mixamo_map.keys()))
    assert not missing, (
        f"{len(missing)} pose KB ids missing a Mixamo mapping. "
        f"Add them to backend/app/knowledge/animations/pose_to_mixamo.json. "
        f"Missing: {missing}"
    )


def test_mapping_is_non_empty(mixamo_map):
    assert mixamo_map, "Mixamo map loaded as empty — mapping file may be missing or unreadable"


def test_fallback_by_count_covers_1_to_4(mixamo_raw):
    fb = mixamo_raw.get("fallback_by_count", {})
    for n in ("1", "2", "3", "4"):
        assert n in fb, f"fallback_by_count missing entry for {n} persons"
        assert fb[n], f"fallback_by_count[{n}] is empty"


def test_meta_section_present(mixamo_raw):
    meta = mixamo_raw.get("_meta", {})
    assert meta.get("version"), "manifest missing _meta.version"
    assert meta.get("fallback"), "manifest missing _meta.fallback (used by lookup helper)"


@pytest.mark.parametrize(
    "pose_id,expected_animation",
    [
        ("pose_single_relaxed_001", "idle_relaxed"),
        ("pose_single_hand_in_hair_001", "pose_hand_in_hair"),
        ("pose_two_high_low_001", "couple_high_low"),
        ("pose_three_triangle_001", "group_triangle_pose"),
        ("pose_four_diamond_001", "group_diamond_pose"),
    ],
)
def test_lookup_returns_direct_mapping(pose_id, expected_animation, mixamo_map, mixamo_raw):
    fb = mixamo_raw.get("fallback_by_count", {})
    got = lookup_mixamo_for_pose(pose_id, 1, mixamo_map, fb)
    assert got == expected_animation


@pytest.mark.parametrize("person_count", [1, 2, 3, 4])
def test_lookup_falls_back_when_id_unknown(person_count, mixamo_map, mixamo_raw):
    fb = mixamo_raw.get("fallback_by_count", {})
    got = lookup_mixamo_for_pose(
        "pose_does_not_exist_999", person_count, mixamo_map, fb,
    )
    assert got == fb[str(person_count)], (
        f"fallback for person_count={person_count} mismatch: "
        f"got {got!r}, expected {fb[str(person_count)]!r}"
    )


def test_lookup_uses_idle_relaxed_when_no_pose_id(mixamo_map, mixamo_raw):
    fb = mixamo_raw.get("fallback_by_count", {})
    got = lookup_mixamo_for_pose(None, 1, mixamo_map, fb)
    assert got == fb["1"]


def test_no_animation_id_appears_in_more_than_5_mappings(mixamo_map):
    """Sanity — if every pose maps to ``idle_relaxed`` we mis-wired."""
    from collections import Counter
    counts = Counter(mixamo_map.values())
    most_common_id, most_common_count = counts.most_common(1)[0]
    # 28 poses across ~4 layout classes; the most common animation
    # should still cover < 6 ids. If this trips we probably copy-pasted.
    assert most_common_count < 6, (
        f"animation {most_common_id!r} is mapped to "
        f"{most_common_count} pose ids — likely a copy-paste mistake"
    )


def test_animation_ids_are_consistent_with_count_section(mixamo_raw):
    """The mapping is split into single / two_person / three_person /
    four_person sections to keep diffs reviewable. Verify each pose id
    actually starts with the expected prefix."""
    section_prefix = {
        "single": "pose_single_",
        "two_person": "pose_two_",
        "three_person": "pose_three_",
        "four_person": "pose_four_",
    }
    for section, prefix in section_prefix.items():
        for pose_id in (mixamo_raw.get(section) or {}).keys():
            assert pose_id.startswith(prefix), (
                f"{pose_id!r} is in {section!r} section but has wrong "
                f"prefix (expected {prefix!r})"
            )
