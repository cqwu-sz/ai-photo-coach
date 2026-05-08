"""Phase 3.2 — pose embedding + similarity search.

Why a custom embedder
=====================
The pose KB has ~30 entries. Bringing in `sentence-transformers` would
add 100 MB of model weights at boot, just to embed strings the LLM is
already paraphrasing. Instead we use a deterministic **character-bigram
+ trigram bag-of-words**, IDF-weighted across the KB, and compute cosine
similarity in pure NumPy. On 30 entries this is:

  * fully offline (no ``transformers`` / ``torch``),
  * deterministic across runs,
  * 0 wall-clock cost (≈1 ms to embed + rank),
  * good enough for the recall@5 ≈ 100% target on our hand-built tests.

The public surface is intentionally minimal:

  * :class:`PoseEmbeddingIndex` — built once from a list of pose dicts,
    then queried by free-text strings.
  * :func:`rank_pose_ids` — convenience wrapper used by ``pose_engine``.

If we later want a real embedding model, swap the implementation of
``_text_vector`` for sentence-transformers and call sites stay identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import math
from typing import Iterable, Optional

import numpy as np


# Character n-gram lengths used as tokens. 2-3 char windows match the
# granularity of Chinese morphemes well without needing a tokenizer.
_NGRAM_SIZES: tuple[int, ...] = (2, 3)
# Lowercased ASCII tokens are also extracted so layouts like "high_low_offset"
# remain matchable against the LLM's English layout values.
_ASCII_TOKEN_RE = None  # set lazily; see _ascii_tokens


def _char_ngrams(text: str) -> list[str]:
    """Generate character bi/tri-grams from a raw string after stripping
    whitespace. We keep punctuation — it's noise on Chinese text but
    cheap and self-cancelling under TF-IDF.
    """
    s = "".join(text.split())
    if not s:
        return []
    out: list[str] = []
    for n in _NGRAM_SIZES:
        if len(s) >= n:
            for i in range(len(s) - n + 1):
                out.append(s[i:i + n])
    return out


def _ascii_tokens(text: str) -> list[str]:
    """Cheap lowercase ASCII word tokenizer for layout / tag matching."""
    global _ASCII_TOKEN_RE
    import re
    if _ASCII_TOKEN_RE is None:
        _ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
    return [m.group(0).lower() for m in _ASCII_TOKEN_RE.finditer(text)]


def _tokenize(text: str) -> list[str]:
    return _char_ngrams(text) + _ascii_tokens(text)


# ────────── pose document construction ──────────


def pose_document(pose: dict) -> str:
    """Build the searchable text representation of a pose KB entry.

    We deliberately concatenate every human-readable field — the LLM
    might mention a tag (`casual`), a layout (`high_low_offset`), or a
    fragment of the stance description, and we want all of those to
    score the right entry.

    The Chinese alias fields ``summary_zh`` / ``tags_zh`` are repeated
    twice so they dominate the n-gram weight against the English
    ``summary`` / ``tags`` (the LLM's output is overwhelmingly Chinese).
    """
    parts = [
        pose.get("summary", ""),
        pose.get("summary_zh", ""),
        pose.get("summary_zh", ""),  # double-weight Chinese summary
        pose.get("layout", ""),
        " ".join(pose.get("tags", []) or []),
        " ".join(pose.get("tags_zh", []) or []),
        " ".join(pose.get("tags_zh", []) or []),  # double-weight Chinese tags
        " ".join(pose.get("best_for", []) or []),
    ]
    # Some KB entries also carry verbose person fields — fold them in
    # if present without making them mandatory.
    persons = pose.get("persons") or []
    for p in persons:
        for key in ("stance", "upper_body", "hands", "gaze", "expression",
                    "interaction", "position_hint"):
            v = p.get(key) if isinstance(p, dict) else None
            if v: parts.append(v)
    return " ".join(s for s in parts if s)


# ────────── index ──────────


@dataclass(slots=True)
class _Vec:
    """Sparse-style record: token -> tf-idf weight, plus the precomputed
    L2 norm for fast cosine."""
    weights: dict[str, float]
    norm: float


class PoseEmbeddingIndex:
    """Build an IDF-weighted character-n-gram index over a pose library.

    Usage::

        idx = PoseEmbeddingIndex.build(load_poses(path))
        ranked = idx.rank("放松站立 一手插袋 街头", top_k=5,
                          person_count=1)
        # -> [("pose_single_relaxed_001", 0.42), ...]

    The index is immutable and cheap (~10 KB for 30 poses); rebuild it
    on every analyze call if the KB ever changes at runtime.
    """

    def __init__(
        self,
        poses: list[dict],
        idf: dict[str, float],
        vectors: list[_Vec],
    ) -> None:
        self._poses = poses
        self._idf = idf
        self._vecs = vectors

    @classmethod
    def build(cls, poses: list[dict]) -> "PoseEmbeddingIndex":
        # Build per-doc TF + DF in one pass.
        docs: list[Counter] = []
        df: Counter = Counter()
        for pose in poses:
            tokens = _tokenize(pose_document(pose))
            tf = Counter(tokens)
            docs.append(tf)
            for tok in tf:
                df[tok] += 1
        n_docs = max(1, len(docs))
        idf = {
            tok: math.log((n_docs + 1) / (cnt + 1)) + 1.0
            for tok, cnt in df.items()
        }
        vectors: list[_Vec] = []
        for tf in docs:
            w: dict[str, float] = {}
            for tok, count in tf.items():
                w[tok] = float(count) * idf.get(tok, 1.0)
            norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
            vectors.append(_Vec(weights=w, norm=norm))
        return cls(poses=poses, idf=idf, vectors=vectors)

    # ────────── querying ──────────

    def rank(
        self,
        query: str,
        *,
        top_k: int = 5,
        person_count: Optional[int] = None,
        prefer_layout: Optional[str] = None,
    ) -> list[tuple[str, float]]:
        """Return ``[(pose_id, similarity), …]`` sorted by descending
        cosine similarity.

        Soft filters:
          * ``person_count`` — only entries with this exact count are
            considered (drop everything else outright).
          * ``prefer_layout`` — entries with a matching layout receive
            a +0.10 similarity bonus (additive, capped at 1.0).
        """
        if not query.strip():
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        q_tf = Counter(q_tokens)
        q_w: dict[str, float] = {}
        for tok, count in q_tf.items():
            q_w[tok] = float(count) * self._idf.get(tok, 1.0)
        q_norm = math.sqrt(sum(v * v for v in q_w.values())) or 1.0

        scores: list[tuple[str, float]] = []
        for pose, vec in zip(self._poses, self._vecs):
            if person_count is not None and pose.get("person_count") != person_count:
                continue
            sim = _cosine(q_w, q_norm, vec)
            if prefer_layout and pose.get("layout") == prefer_layout:
                sim = min(1.0, sim + 0.10)
            scores.append((pose.get("id"), sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def best_match(
        self,
        query: str,
        *,
        person_count: Optional[int] = None,
        prefer_layout: Optional[str] = None,
        min_similarity: float = 0.05,
    ) -> Optional[str]:
        """Best ``pose_id`` above the similarity floor, or ``None``."""
        ranked = self.rank(
            query, top_k=1,
            person_count=person_count, prefer_layout=prefer_layout,
        )
        if not ranked:
            return None
        pid, score = ranked[0]
        return pid if score >= min_similarity else None


def _cosine(qw: dict[str, float], qn: float, dv: _Vec) -> float:
    """Sparse cosine — iterate the smaller dict to keep this O(min(|q|,|d|))."""
    if len(qw) > len(dv.weights):
        small, large = dv.weights, qw
    else:
        small, large = qw, dv.weights
    dot = 0.0
    for tok, w in small.items():
        if tok in large:
            dot += w * large[tok]
    return dot / (qn * dv.norm)


# ────────── high-level convenience for pose_engine ──────────


def rank_pose_ids(
    query: str,
    poses: list[dict],
    *,
    top_k: int = 5,
    person_count: Optional[int] = None,
    prefer_layout: Optional[str] = None,
) -> list[tuple[str, float]]:
    """One-shot helper used by ``pose_engine.map_to_library`` when the
    LLM didn't specify a ``reference_thumbnail_id``. Builds the index
    inline — for our 30-entry KB this costs <1 ms.
    """
    idx = PoseEmbeddingIndex.build(poses)
    return idx.rank(
        query, top_k=top_k,
        person_count=person_count, prefer_layout=prefer_layout,
    )


def query_text_for(pose) -> str:
    """Build a query string from a ``PoseSuggestion`` (Pydantic model
    or plain dict). Used by pose_engine to match LLM output against KB.
    """
    if isinstance(pose, dict):
        layout = pose.get("layout", "")
        interaction = pose.get("interaction") or ""
        persons = pose.get("persons") or []
    else:
        layout = getattr(pose.layout, "value", str(pose.layout))
        interaction = getattr(pose, "interaction", None) or ""
        persons = getattr(pose, "persons", []) or []

    parts: list[str] = [layout, interaction]
    for p in persons:
        if isinstance(p, dict):
            for key in ("stance", "upper_body", "hands", "gaze",
                        "expression", "position_hint"):
                v = p.get(key)
                if v: parts.append(v)
        else:
            for key in ("stance", "upper_body", "hands", "gaze",
                        "expression", "position_hint"):
                v = getattr(p, key, None)
                if v: parts.append(v)
    return " ".join(parts)


__all__ = [
    "PoseEmbeddingIndex",
    "pose_document",
    "query_text_for",
    "rank_pose_ids",
]
