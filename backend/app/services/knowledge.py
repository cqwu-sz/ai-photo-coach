"""Loads the small JSON knowledge base files (poses, camera settings, composition).

Used both as RAG context for the LLM and as ground truth for the
camera-params engine.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _load_dir(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        log.warning("knowledge dir missing", extra={"path": str(path)})
        return []
    items: list[dict[str, Any]] = []
    for f in sorted(path.glob("*.json")):
        try:
            with f.open("r", encoding="utf-8") as fp:
                items.append(json.load(fp))
        except Exception as exc:
            log.exception("failed to load %s: %s", f, exc)
    return items


@lru_cache(maxsize=8)
def load_poses(path_str: str) -> list[dict[str, Any]]:
    return _load_dir(Path(path_str))


@lru_cache(maxsize=8)
def load_camera_kb(path_str: str) -> list[dict[str, Any]]:
    return _load_dir(Path(path_str))


@lru_cache(maxsize=8)
def load_composition_kb(path_str: str) -> list[dict[str, Any]]:
    return _load_dir(Path(path_str))


def summarize_poses(poses: list[dict[str, Any]], person_count: int) -> str:
    """Produce a tight digest the LLM can read without exploding tokens."""
    if not poses:
        return "(empty pose library)"

    relevant = [p for p in poses if p.get("person_count") == person_count]
    if not relevant:
        relevant = poses

    lines = []
    for p in relevant[:20]:
        lines.append(
            f"- id={p.get('id')} layout={p.get('layout')} "
            f"persons={p.get('person_count')} "
            f"summary={p.get('summary', '')!r}"
        )
    return "\n".join(lines)


def summarize_camera_kb(kb: list[dict[str, Any]]) -> str:
    if not kb:
        return "(empty camera kb)"
    lines = []
    for entry in kb:
        lines.append(
            f"- {entry.get('scenario')}: focal={entry.get('focal_length_mm')} "
            f"ap={entry.get('aperture')} sh={entry.get('shutter')} "
            f"iso~={entry.get('iso')} note={entry.get('note', '')!r}"
        )
    return "\n".join(lines)
