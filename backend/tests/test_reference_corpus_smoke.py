"""Smoke tests for per-user reference inspiration corpus."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import reference_corpus


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_corpus, "DB_PATH", tmp_path / "reference_corpus.db")
    yield


def test_add_and_list(isolated_db):
    item = reference_corpus.add_item(
        user_id="u1",
        creator_handle="@alice",
        creator_platform="xhs",
        scene_tags=["urban", "alleyway"],
        light_tags=["golden_hour"],
        composition_tags=["leading_line"],
        recipe={"focal": "50mm"},
        embedding=[0.1, 0.2, 0.3, 0.4],
    )
    assert item.user_id == "u1"
    items = reference_corpus.list_for_user("u1")
    assert len(items) == 1
    assert items[0].creator_handle == "@alice"
    assert items[0].recipe == {"focal": "50mm"}
    assert items[0].embedding == pytest.approx([0.1, 0.2, 0.3, 0.4], abs=1e-6)


def test_list_creators(isolated_db):
    reference_corpus.add_item("u1", creator_handle="@a", creator_platform="xhs")
    reference_corpus.add_item("u1", creator_handle="@a", creator_platform="xhs")
    reference_corpus.add_item("u1", creator_handle="@b", creator_platform="xhs")
    creators = reference_corpus.list_creators("u1")
    by_handle = {c["creator_handle"]: c for c in creators}
    assert by_handle["@a"]["count"] == 2
    assert by_handle["@b"]["count"] == 1


def test_soft_delete(isolated_db):
    item = reference_corpus.add_item("u1", creator_handle="@a")
    assert reference_corpus.soft_delete("u1", item.item_id) is True
    assert reference_corpus.list_for_user("u1") == []
    # Idempotent
    assert reference_corpus.soft_delete("u1", item.item_id) is False


def test_recall_by_tags(isolated_db):
    reference_corpus.add_item(
        "u1", scene_tags=["urban", "alleyway"], light_tags=["golden_hour"],
    )
    reference_corpus.add_item(
        "u1", scene_tags=["beach"], light_tags=["overcast"],
    )
    hits = reference_corpus.recall(
        "u1",
        query_scene_tags=["urban", "alleyway"],
        query_light_tags=["golden_hour"],
        top_k=2,
    )
    assert len(hits) == 2
    # First hit must be the urban alleyway one.
    assert hits[0][1] > hits[1][1]
    assert "urban" in hits[0][0].scene_tags


def test_recall_by_embedding(isolated_db):
    a = reference_corpus.add_item("u1", embedding=[1.0, 0.0, 0.0])
    b = reference_corpus.add_item("u1", embedding=[0.0, 1.0, 0.0])
    hits = reference_corpus.recall("u1", query_embedding=[0.99, 0.01, 0.0], top_k=2)
    assert hits[0][0].item_id == a.item_id
    assert hits[0][1] > hits[1][1]


def test_recall_empty_user_returns_nothing(isolated_db):
    assert reference_corpus.recall("ghost", top_k=5) == []


def test_works_loader_drops_invalid(tmp_path):
    # Valid + invalid mixed
    (tmp_path / "ok.json").write_text(
        '[{"id":"w1","scene_tags":["a"],"reusable_recipe":{}},'
        ' {"id":"","scene_tags":[],"reusable_recipe":{}}]',
        encoding="utf-8",
    )
    from app.services.knowledge import load_works
    load_works.cache_clear()
    works = load_works(str(tmp_path))
    assert len(works) == 1
    assert works[0]["id"] == "w1"
