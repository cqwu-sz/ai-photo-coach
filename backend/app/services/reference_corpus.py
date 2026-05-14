"""Per-user reference inspiration library — the private side of the
works/ corpus.

Data flow
---------
When a user records the screen while scrolling Xiaohongshu / Pinterest
/ etc. (see ``ios/AIPhotoCoach/Features/ReferenceLearn/``), the client:

1. Captures CMSampleBuffer frames via ReplayKit (user-initiated).
2. Runs the four-level local triage (blur / human / "is-photo" / dedup).
3. Lets the user review which surviving frames to keep
   (``ReviewView.swift``).
4. Optionally OCRs a creator handle and asks the user to confirm
   bucket-by-creator.
5. Uploads only the **kept** frames + recipe drafts + creator id to
   this corpus.

Storage layout
--------------
SQLite table ``reference_items`` (sibling to ``users.db``):

    user_id           TEXT NOT NULL              indexed
    item_id           TEXT PRIMARY KEY           uuid
    creator_handle    TEXT NULL                  e.g. "@xxx" — bucket key
    creator_platform  TEXT NULL                  "xhs" | "pinterest" | "manual"
    image_thumb_uri   TEXT NULL                  small jpeg blob hash on disk
    scene_tags        JSON                       list[str]
    light_tags        JSON                       list[str]
    composition_tags  JSON                       list[str]
    recipe            JSON                       reusable_recipe dict
    embedding         BLOB NULL                  CLIP vector (float32, L2-normalised)
    added_at          INTEGER NOT NULL           unix ms
    deleted_at        INTEGER NULL               soft delete

Bucketing-by-creator is purely a UI affordance ("show me the @xxx
collection"). Search / retrieval ignores it — the only thing that
matters at recipe-recall time is the embedding similarity to the
current environment fingerprint.

This module is intentionally **storage + retrieval only**. No image
upload handling here (that goes through the analyze API surface or a
dedicated /reference endpoint) and no CLIP model loading (the backend
either receives pre-computed embeddings from the iOS CLIPEmbedder or
lazily fills them via a background task — see ``scripts/build_index``).

Privacy notes
-------------
- Items belong to ``user_id`` only; the recall API is keyed by user.
- We never serve thumbnails cross-user.
- Soft-delete (``deleted_at``) is used for "user removed this from
  their library"; a nightly job purges items past 30 days.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reference_corpus.db"


@dataclass
class ReferenceItem:
    user_id: str
    item_id: str
    creator_handle: Optional[str]
    creator_platform: Optional[str]
    image_thumb_uri: Optional[str]
    scene_tags: list[str]
    light_tags: list[str]
    composition_tags: list[str]
    recipe: dict
    embedding: Optional[list[float]]
    added_at: int
    deleted_at: Optional[int]

    def to_dict(self) -> dict:
        return {
            "user_id":          self.user_id,
            "item_id":          self.item_id,
            "creator_handle":   self.creator_handle,
            "creator_platform": self.creator_platform,
            "image_thumb_uri":  self.image_thumb_uri,
            "scene_tags":       self.scene_tags,
            "light_tags":       self.light_tags,
            "composition_tags": self.composition_tags,
            "recipe":           self.recipe,
            "added_at":         self.added_at,
        }


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------
@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con)
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS reference_items (
            user_id           TEXT NOT NULL,
            item_id           TEXT PRIMARY KEY,
            creator_handle    TEXT,
            creator_platform  TEXT,
            image_thumb_uri   TEXT,
            scene_tags        TEXT NOT NULL,    -- JSON list
            light_tags        TEXT NOT NULL,
            composition_tags  TEXT NOT NULL,
            recipe            TEXT NOT NULL,    -- JSON dict
            embedding         BLOB,
            added_at          INTEGER NOT NULL,
            deleted_at        INTEGER
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ref_user "
        "ON reference_items(user_id, deleted_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ref_creator "
        "ON reference_items(user_id, creator_handle)"
    )


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------
def add_item(
    user_id: str,
    *,
    creator_handle: Optional[str] = None,
    creator_platform: Optional[str] = None,
    image_thumb_uri: Optional[str] = None,
    scene_tags: Optional[Iterable[str]] = None,
    light_tags: Optional[Iterable[str]] = None,
    composition_tags: Optional[Iterable[str]] = None,
    recipe: Optional[dict] = None,
    embedding: Optional[list[float]] = None,
    item_id: Optional[str] = None,
) -> ReferenceItem:
    """Insert one reference item for ``user_id``. Returns the persisted
    record. Tags / recipe default to empty when unknown so older
    clients without a triage pipeline can still drop in raw frames."""
    item_id = item_id or uuid.uuid4().hex
    scene_tags = list(scene_tags or [])
    light_tags = list(light_tags or [])
    composition_tags = list(composition_tags or [])
    recipe = recipe or {}
    added_at = int(time.time() * 1000)
    emb_blob = _embedding_to_blob(embedding)
    with _connect() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO reference_items
                (user_id, item_id, creator_handle, creator_platform,
                 image_thumb_uri, scene_tags, light_tags,
                 composition_tags, recipe, embedding, added_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                user_id, item_id, creator_handle, creator_platform,
                image_thumb_uri,
                json.dumps(scene_tags, ensure_ascii=False),
                json.dumps(light_tags, ensure_ascii=False),
                json.dumps(composition_tags, ensure_ascii=False),
                json.dumps(recipe, ensure_ascii=False),
                emb_blob, added_at,
            ),
        )
    return ReferenceItem(
        user_id=user_id, item_id=item_id,
        creator_handle=creator_handle, creator_platform=creator_platform,
        image_thumb_uri=image_thumb_uri,
        scene_tags=scene_tags, light_tags=light_tags,
        composition_tags=composition_tags,
        recipe=recipe, embedding=embedding,
        added_at=added_at, deleted_at=None,
    )


def soft_delete(user_id: str, item_id: str) -> bool:
    """Mark an item as deleted. Returns True if a row was actually
    affected (idempotent)."""
    with _connect() as con:
        cur = con.execute(
            "UPDATE reference_items SET deleted_at = ? "
            "WHERE user_id = ? AND item_id = ? AND deleted_at IS NULL",
            (int(time.time() * 1000), user_id, item_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def list_for_user(
    user_id: str,
    *,
    creator_handle: Optional[str] = None,
    limit: int = 100,
) -> list[ReferenceItem]:
    """List a user's live items, optionally filtered by creator. Sorted
    by ``added_at`` DESC so the UI shows freshest inspirations first."""
    sql = (
        "SELECT * FROM reference_items "
        "WHERE user_id = ? AND deleted_at IS NULL"
    )
    args: list = [user_id]
    if creator_handle:
        sql += " AND creator_handle = ?"
        args.append(creator_handle)
    sql += " ORDER BY added_at DESC LIMIT ?"
    args.append(limit)
    with _connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [_row_to_item(r) for r in rows]


def list_creators(user_id: str) -> list[dict]:
    """Returns the user's creators with item counts.

    Output: ``[{"creator_handle": "@x", "creator_platform": "xhs", "count": 17}, ...]``
    """
    with _connect() as con:
        rows = con.execute(
            """
            SELECT creator_handle, creator_platform, COUNT(*) AS n
            FROM reference_items
            WHERE user_id = ? AND deleted_at IS NULL
              AND creator_handle IS NOT NULL
            GROUP BY creator_handle, creator_platform
            ORDER BY n DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "creator_handle":   r["creator_handle"],
            "creator_platform": r["creator_platform"],
            "count":            int(r["n"]),
        }
        for r in rows
    ]


def recall(
    user_id: str,
    *,
    query_embedding: Optional[list[float]] = None,
    query_scene_tags: Optional[Iterable[str]] = None,
    query_light_tags: Optional[Iterable[str]] = None,
    top_k: int = 5,
) -> list[tuple[ReferenceItem, float]]:
    """Recall a user's most-similar items for use in a prompt.

    Scoring strategy (tunable):
      - If ``query_embedding`` is provided AND items carry embeddings,
        cosine similarity wins (heaviest weight).
      - Otherwise (or in addition), Jaccard overlap on scene_tags +
        light_tags gives a cheap text-only fallback.

    Returns ``[(item, score)]`` sorted by descending score, capped at
    ``top_k``. Empty list when the user has no items at all.
    """
    items = list_for_user(user_id, limit=500)
    if not items:
        return []
    qe = query_embedding
    q_scene = set(query_scene_tags or [])
    q_light = set(query_light_tags or [])
    scored: list[tuple[ReferenceItem, float]] = []
    for item in items:
        emb_score = 0.0
        if qe is not None and item.embedding is not None:
            emb_score = _cosine(qe, item.embedding)
        tag_overlap = (
            _jaccard(set(item.scene_tags), q_scene) * 0.6
            + _jaccard(set(item.light_tags), q_light) * 0.4
        )
        # 0.7 embedding + 0.3 tags when we have an embedding; pure tags
        # otherwise. (Embeddings are the meaningful signal; tags are
        # the safety net.)
        if qe is not None and item.embedding is not None:
            score = 0.7 * emb_score + 0.3 * tag_overlap
        else:
            score = tag_overlap
        scored.append((item, score))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_to_item(r: sqlite3.Row) -> ReferenceItem:
    return ReferenceItem(
        user_id=r["user_id"],
        item_id=r["item_id"],
        creator_handle=r["creator_handle"],
        creator_platform=r["creator_platform"],
        image_thumb_uri=r["image_thumb_uri"],
        scene_tags=json.loads(r["scene_tags"]) if r["scene_tags"] else [],
        light_tags=json.loads(r["light_tags"]) if r["light_tags"] else [],
        composition_tags=json.loads(r["composition_tags"]) if r["composition_tags"] else [],
        recipe=json.loads(r["recipe"]) if r["recipe"] else {},
        embedding=_blob_to_embedding(r["embedding"]),
        added_at=int(r["added_at"]),
        deleted_at=int(r["deleted_at"]) if r["deleted_at"] is not None else None,
    )


def _embedding_to_blob(emb: Optional[list[float]]) -> Optional[bytes]:
    if emb is None:
        return None
    import struct
    return struct.pack(f"{len(emb)}f", *emb)


def _blob_to_embedding(blob: Optional[bytes]) -> Optional[list[float]]:
    if not blob:
        return None
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
