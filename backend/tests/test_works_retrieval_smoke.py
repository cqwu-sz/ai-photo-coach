"""Smoke tests for works_retrieval — few-shot recall + scoring."""
from __future__ import annotations

import pytest

from app.services import reference_corpus, works_retrieval


@pytest.fixture
def isolated_ref_db(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_corpus, "DB_PATH", tmp_path / "reference_corpus.db")
    yield


PUBLIC_CORPUS = [
    {
        "id": "w_alley",
        "source": {"platform": "unsplash", "url": "u1", "author": "@a", "license": "unsplash"},
        "scene_tags": ["urban", "alleyway"],
        "light_tags": ["golden_hour", "side_light", "rim"],
        "composition_tags": ["leading_line"],
        "why_good": ["边缘光勾轮廓"],
        "reusable_recipe": {
            "subject_pose": "侧身",
            "camera_position": "蹲低",
            "framing": "右1/3",
            "focal_length": "50mm",
            "applicable_to": {"scene_modes": ["portrait", "documentary"],
                               "needs_leading_line": True},
        },
    },
    {
        "id": "w_beach",
        "source": {"platform": "unsplash", "url": "u2", "author": "@b", "license": "unsplash"},
        "scene_tags": ["beach"],
        "light_tags": ["overcast"],
        "composition_tags": ["negative_space"],
        "why_good": ["留白"],
        "reusable_recipe": {
            "subject_pose": "站立",
            "camera_position": "远景",
            "framing": "底部1/3",
            "applicable_to": {"scene_modes": ["scenery"]},
        },
    },
]


def test_recall_prefers_matching_tags():
    ctx = works_retrieval.WorkSearchContext(
        scene_tags=("urban", "alleyway"),
        light_tags=("golden_hour",),
        scene_mode="portrait",
        needs_leading_line=True,
    )
    hits = works_retrieval.recall(PUBLIC_CORPUS, ctx=ctx, top_k=2)
    assert hits[0].work["id"] == "w_alley"
    assert hits[0].score > hits[1].score


def test_recall_zero_corpus_empty():
    ctx = works_retrieval.WorkSearchContext(scene_tags=(), light_tags=())
    assert works_retrieval.recall([], ctx=ctx) == []


def test_recall_includes_user_private(isolated_ref_db):
    reference_corpus.add_item(
        "u1",
        creator_handle="@me",
        creator_platform="xhs",
        scene_tags=["urban", "alleyway"],
        light_tags=["golden_hour"],
        recipe={"camera_position": "私人配方"},
    )
    ctx = works_retrieval.WorkSearchContext(
        scene_tags=("urban", "alleyway"),
        light_tags=("golden_hour",),
    )
    hits = works_retrieval.recall(PUBLIC_CORPUS, user_id="u1", ctx=ctx, top_k=3)
    # Both public + private sources represented.
    sources = {h.source for h in hits}
    assert "public" in sources
    assert "user_private" in sources


def test_prompt_block_renders_doctrine():
    ctx = works_retrieval.WorkSearchContext(scene_tags=("urban",), light_tags=("golden_hour",))
    hits = works_retrieval.recall(PUBLIC_CORPUS, ctx=ctx, top_k=1)
    block = works_retrieval.to_prompt_block(hits)
    assert "REFERENCE WORKS" in block
    assert "WORKS DOCTRINE" in block
    assert "范例 1" in block
