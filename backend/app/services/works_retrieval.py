"""Retrieve relevant works/ recipes for a given analyze request and
render them as a few-shot prompt block.

The job: given the user's current environment fingerprint (landmark
graph + light_pro + scene_aggregate), pick the 3-6 most similar
deconstructed works from ``backend/app/knowledge/works/`` plus the
user's private ``reference_corpus`` items, and produce a Markdown-ish
prompt block that the LLM can read as concrete few-shot examples.

This is the "I have seen 10000 great photos" half of the AI's
expertise. Without this block, the LLM can only quote theory; with it,
the LLM can quote concrete recipes.

Scoring strategy
----------------
For each candidate work:

  score = 0.55 × cosine(query_embedding, work.embedding)
        + 0.20 × jaccard(query_scene_tags, work.scene_tags)
        + 0.15 × jaccard(query_light_tags, work.light_tags)
        + 0.10 × applicability_match(work.applicable_to, query_context)

The embedding side is skipped (and the weights renormalised) when
either the query or the work lacks an embedding — older works added
manually before ``build_index.py`` ran still work, they just rank
purely on tags.

Two pools are scored together:
  - Public corpus (``knowledge/works/``): served at full thumbnail.
  - User's private corpus (``reference_corpus``): served as recipe-only
    when ``image_thumb_uri`` is empty.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Optional

from . import clip_service
from . import reference_corpus as reference_corpus_service

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkSearchContext:
    """Compact summary of the user's current shoot context."""
    scene_tags: tuple[str, ...]
    light_tags: tuple[str, ...]
    scene_mode: str = "portrait"
    needs_stereo: bool = False
    needs_leading_line: bool = False
    query_text: str = ""              # optional natural-language hint
    query_embedding: Optional[tuple[float, ...]] = None


@dataclass(frozen=True, slots=True)
class WorkHit:
    work: dict
    score: float
    source: str           # "public" | "user_private"


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _cosine(a: Iterable[float], b: Iterable[float]) -> float:
    a = list(a or [])
    b = list(b or [])
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _applicability_match(applicable_to: dict, ctx: WorkSearchContext) -> float:
    """Penalty / bonus signal — 0..1. Modes overlap = match."""
    score = 0.0
    if not isinstance(applicable_to, dict):
        return score
    modes = applicable_to.get("scene_modes") or []
    if ctx.scene_mode in modes:
        score += 0.6
    if ctx.needs_stereo and applicable_to.get("needs_stereo"):
        score += 0.25
    elif (not ctx.needs_stereo) and not applicable_to.get("needs_stereo"):
        score += 0.05
    if ctx.needs_leading_line and applicable_to.get("needs_leading_line"):
        score += 0.15
    return min(1.0, score)


def _score(work_or_item: dict, ctx: WorkSearchContext) -> float:
    has_qe = bool(ctx.query_embedding)
    has_we = bool(work_or_item.get("embedding"))
    weights = {"emb": 0.55, "scene": 0.20, "light": 0.15, "appl": 0.10}
    if not (has_qe and has_we):
        # Renormalise: pull weight off the embedding axis onto scene + light.
        weights = {"emb": 0.0, "scene": 0.45, "light": 0.35, "appl": 0.20}
    s = 0.0
    if has_qe and has_we:
        s += weights["emb"] * _cosine(ctx.query_embedding or (),
                                       work_or_item.get("embedding") or [])
    s += weights["scene"] * _jaccard(ctx.scene_tags, work_or_item.get("scene_tags") or [])
    s += weights["light"] * _jaccard(ctx.light_tags, work_or_item.get("light_tags") or [])
    recipe = work_or_item.get("reusable_recipe") or {}
    s += weights["appl"] * _applicability_match(recipe.get("applicable_to") or {}, ctx)
    return round(s, 3)


def recall(
    public_corpus: list[dict],
    *,
    user_id: Optional[str] = None,
    ctx: WorkSearchContext,
    top_k: int = 5,
) -> list[WorkHit]:
    """Pull the top-K hits across public + user-private pools."""
    qe = list(ctx.query_embedding) if ctx.query_embedding else None
    # When query_text is supplied and no explicit embedding, lazily
    # embed via the clip_service backend (NoopBackend keeps it None).
    if qe is None and ctx.query_text:
        be = clip_service.get_backend()
        qe = be.embed_text(ctx.query_text)
        if qe:
            ctx = WorkSearchContext(
                scene_tags=ctx.scene_tags, light_tags=ctx.light_tags,
                scene_mode=ctx.scene_mode,
                needs_stereo=ctx.needs_stereo,
                needs_leading_line=ctx.needs_leading_line,
                query_text=ctx.query_text,
                query_embedding=tuple(qe),
            )

    hits: list[WorkHit] = []
    for w in public_corpus or []:
        hits.append(WorkHit(work=w, score=_score(w, ctx), source="public"))

    if user_id:
        try:
            user_items = reference_corpus_service.list_for_user(user_id, limit=200)
        except Exception as exc:                          # noqa: BLE001
            log.info("user_private recall skipped: %s", exc)
            user_items = []
        for it in user_items:
            # Mirror ReferenceItem into the work-shaped dict for scoring.
            shadow = {
                "id":               it.item_id,
                "source":           {"platform": it.creator_platform or "user_private",
                                      "url": "", "author": it.creator_handle,
                                      "license": "user"},
                "image_uri":        it.image_thumb_uri or "",
                "thumbnail_uri":    it.image_thumb_uri or "",
                "scene_tags":       it.scene_tags,
                "light_tags":       it.light_tags,
                "composition_tags": it.composition_tags,
                "reusable_recipe":  it.recipe,
                "embedding":        it.embedding,
            }
            hits.append(WorkHit(work=shadow, score=_score(shadow, ctx),
                                 source="user_private"))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def to_prompt_block(hits: list[WorkHit]) -> str:
    """Render hits as a few-shot block the LLM can chew on."""
    if not hits:
        return ""
    lines = [
        f"── REFERENCE WORKS（基于环境指纹召回的 {len(hits)} 个真实优秀作品配方，"
        "用作 few-shot 参考） ──",
    ]
    for i, h in enumerate(hits, 1):
        w = h.work
        src = w.get("source") or {}
        platform = src.get("platform") or "?"
        author = src.get("author") or "(unknown)"
        scenes = "/".join(w.get("scene_tags") or [])
        lights = "/".join(w.get("light_tags") or [])
        comps = "/".join(w.get("composition_tags") or [])
        recipe = w.get("reusable_recipe") or {}
        lines.append(
            f"  · 范例 {i} [{h.source}/{platform}, score={h.score}]"
            f" {author} · {scenes} / {lights} / {comps}"
        )
        if w.get("why_good"):
            lines.append(f"      为什么好：{'；'.join(w['why_good'][:3])}")
        recipe_bits = []
        for k in ("subject_pose", "camera_position", "framing", "focal_length",
                  "aperture", "post_style"):
            v = recipe.get(k)
            if v:
                recipe_bits.append(f"{k}={v}")
        if recipe_bits:
            lines.append(f"      可复现配方：{' | '.join(recipe_bits)}")
    lines.append(
        "  WORKS DOCTRINE：把上面这些范例当成「在类似环境里别人已经验证过的"
        "执行路径」。你产出的 shot 里至少要有一个明显借鉴某条范例的 rationale，"
        "并在 rationale 里直接说『参考 范例 X 的 camera_position + framing』。"
        "不要照搬，但请把范例的核心动作迁移到当前真实环境上。"
    )
    return "\n".join(lines)
