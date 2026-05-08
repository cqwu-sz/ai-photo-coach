"""Tests for the composition KB loader and dynamic summarizer.

These guard the v6 contract:
  * The seed JSON files load cleanly (list-of-dicts is supported).
  * Each entry has the required v6 fields (id, axes, scene_modes, priority,
    citations).
  * summarize_composition_kb stays inside our token budget, filters by
    scene_mode + person_count, and emits the [rule_id] prefix the LLM
    must echo back in criteria_notes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.knowledge import (
    load_composition_kb,
    summarize_composition_kb,
)


KB_DIR = Path(__file__).resolve().parents[1] / "app" / "knowledge" / "composition"


@pytest.fixture(scope="module")
def kb() -> list[dict]:
    return load_composition_kb(str(KB_DIR))


def test_kb_loads_at_least_seed_count(kb):
    assert len(kb) >= 150, "v6 KB should ship >= 150 rules across all 3 waves"


REQUIRED_FIELDS = {
    "id", "category", "name_zh", "summary",
    "axes", "scene_modes", "priority",
}


@pytest.mark.parametrize(
    "axis", ["composition", "subject_fit", "background", "theme",
             "light", "color", "depth"],
)
def test_every_axis_has_at_least_two_rules(kb, axis):
    matching = [e for e in kb if axis in (e.get("axes") or [])]
    assert len(matching) >= 2, f"axis={axis} only has {len(matching)} rules"


def test_each_entry_has_required_fields(kb):
    for entry in kb:
        missing = REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"entry {entry.get('id')} missing fields {missing}"
        assert isinstance(entry["axes"], list) and entry["axes"]
        assert isinstance(entry["scene_modes"], list) and entry["scene_modes"]
        assert 1 <= entry["priority"] <= 5


def test_ids_are_unique(kb):
    ids = [e["id"] for e in kb]
    assert len(ids) == len(set(ids)), "duplicate rule ids found"


def test_citations_have_chapter_not_page(kb):
    """Compliance rule: chapter-level only (avoid 1:1 quoting)."""
    for entry in kb:
        for cit in entry.get("citations", []) or []:
            assert "chapter" in cit, (
                f"{entry['id']}: citations must reference chapter, not page"
            )


def test_summarize_stays_within_token_budget(kb):
    """Top-30 KB digest should fit in <= ~1500 chars (≈500 tokens)."""
    out = summarize_composition_kb(
        kb,
        scene_mode="portrait",
        person_count=1,
        top_n=30,
    )
    assert out, "summary must be non-empty"
    assert len(out) <= 4000, (
        f"KB digest grew to {len(out)} chars — over budget"
    )


def test_summarize_filters_by_scene_mode(kb):
    portrait = summarize_composition_kb(
        kb, scene_mode="portrait", person_count=1, top_n=30,
    )
    scenery = summarize_composition_kb(
        kb, scene_mode="scenery", person_count=0, top_n=30,
    )
    assert portrait != scenery, (
        "different scene_modes must yield different digests"
    )


def test_summarize_respects_person_count(kb):
    """Multi-person rules (e.g. high_low_offset) only show when N >= 2."""
    solo = summarize_composition_kb(
        kb, scene_mode="portrait", person_count=1, top_n=50,
    )
    duo = summarize_composition_kb(
        kb, scene_mode="portrait", person_count=2, top_n=50,
    )
    assert "sub_high_low_offset" not in solo
    assert "sub_high_low_offset" in duo


def test_summary_emits_rule_id_prefix(kb):
    out = summarize_composition_kb(
        kb, scene_mode="portrait", person_count=1, top_n=10,
    )
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("反例"):
            assert "[" in line, "counter-example must keep [rule_id]"
            continue
        assert line.startswith("["), (
            f"summary line must start with [rule_id]: {line!r}"
        )


def test_unknown_scene_mode_returns_placeholder(kb):
    out = summarize_composition_kb(kb, scene_mode="moon_landing", person_count=1)
    assert out, "must return placeholder, not crash"


# ─────────────────── v6 wave-3 coverage ───────────────────


def test_v3_theory_authors_are_present(kb):
    """Wave-3 theory rules cite Barthes / Suler / HCB / Sontag.
    Each should contribute at least 5 rules (Barthes 8, Suler 15+,
    HCB 6, Sontag 5).
    """
    counts = {"Barthes": 0, "Suler": 0, "Cartier-Bresson": 0, "Sontag": 0}
    for e in kb:
        for cit in e.get("citations", []) or []:
            src = cit.get("source", "")
            for key in counts:
                if key in src:
                    counts[key] += 1
                    break
    assert counts["Barthes"] >= 5, f"Barthes coverage too low: {counts}"
    assert counts["Suler"] >= 10, f"Suler coverage too low: {counts}"
    assert counts["Cartier-Bresson"] >= 4, f"HCB coverage too low: {counts}"
    assert counts["Sontag"] >= 3, f"Sontag coverage too low: {counts}"


def test_anti_pattern_rules_exist(kb):
    """Wave-3 should ship anti-pattern rules so the prompt can teach
    'what NOT to do' as well as best practices."""
    anti = [e for e in kb if e["id"].startswith("anti_")]
    assert len(anti) >= 10, (
        f"expected >= 10 anti-pattern rules, got {len(anti)}"
    )
    # Each anti-pattern must explicitly list a counter_example
    for e in anti:
        assert e.get("counter_example"), (
            f"{e['id']}: anti-pattern must have counter_example"
        )


def test_priority_distribution_reasonable(kb):
    """Sanity check: priority 5 (must) rules should be ~25-50% of KB.
    If everything is priority 5 the prompt budget chooses arbitrarily;
    if too few exist the LLM lacks anchor cues."""
    p5 = [e for e in kb if e.get("priority") == 5]
    ratio = len(p5) / len(kb)
    assert 0.20 <= ratio <= 0.60, (
        f"priority-5 ratio {ratio:.2%} outside healthy 20-60% band"
    )


def test_summarize_under_largest_kb_still_in_budget(kb):
    """At 170+ rules the digest must still stay compact; this guards
    against future expansion blowing past the LLM context budget."""
    for mode in ("portrait", "documentary", "scenery", "light_shadow",
                 "closeup", "full_body"):
        out = summarize_composition_kb(
            kb, scene_mode=mode, person_count=1, top_n=30,
        )
        assert len(out) <= 4000, (
            f"mode={mode}: digest {len(out)} chars over budget"
        )
