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
    """Load every JSON file in ``path``. Each file may be:
      - a single dict   -> appended as one entry, or
      - a list of dicts -> extended into the result list.

    The list-of-dicts form is what we use for the v6 composition KB
    seed batches (~80 entries per file), since one-file-per-rule scales
    poorly to 200+ entries.
    """
    if not path.exists():
        log.warning("knowledge dir missing", extra={"path": str(path)})
        return []
    items: list[dict[str, Any]] = []
    for f in sorted(path.glob("*.json")):
        try:
            with f.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
            if isinstance(payload, list):
                items.extend(p for p in payload if isinstance(p, dict))
            elif isinstance(payload, dict):
                items.append(payload)
            else:
                log.warning("ignoring %s: unexpected JSON top-level type %r",
                            f, type(payload).__name__)
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


@lru_cache(maxsize=4)
def load_pose_to_mixamo(path_str: str) -> dict[str, str]:
    """Load the pose-id → Mixamo-animation-id mapping (v7).

    Returns a flat dict (single + two_person + three_person + four_person
    sections all merged). Callers that want per-count fallbacks should
    consult the original JSON directly via :func:`load_pose_to_mixamo_raw`.
    """
    raw = load_pose_to_mixamo_raw(path_str)
    flat: dict[str, str] = {}
    for section in ("single", "two_person", "three_person", "four_person"):
        flat.update(raw.get(section, {}) or {})
    return flat


@lru_cache(maxsize=4)
def load_pose_to_mixamo_raw(path_str: str) -> dict[str, Any]:
    """Return the full mapping JSON including ``_meta`` and
    ``fallback_by_count``. Used by API endpoints that want to expose
    fallbacks to the client (so the web/iOS loaders can fall back when
    a brand-new pose KB id has no mapping yet)."""
    p = Path(path_str)
    if not p.exists():
        log.warning("pose-to-mixamo mapping missing", extra={"path": str(p)})
        return {}
    try:
        with p.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception as exc:
        log.exception("failed to load pose_to_mixamo: %s", exc)
        return {}


def lookup_mixamo_for_pose(
    pose_id: str | None,
    person_count: int,
    mapping: dict[str, str] | None = None,
    fallback_by_count: dict[str, str] | None = None,
) -> str:
    """Resolve a pose KB id to a Mixamo animation id.

    Strategy:
      1. Direct lookup in the flat mapping
      2. Fall back to ``fallback_by_count[str(person_count)]``
      3. Fall back to a hardcoded ``idle_relaxed``

    All three layers exist so a brand-new pose KB id never breaks the
    web 3D preview / iOS AR — it just animates with a generic idle.
    """
    if mapping is None:
        mapping = {}
    if fallback_by_count is None:
        fallback_by_count = {}
    if pose_id and pose_id in mapping:
        return mapping[pose_id]
    return fallback_by_count.get(str(person_count), "idle_relaxed")


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


# ────────────────────────────────────────────────────────────────────────────
# Composition KB summarizer — dynamic top-N injection.
#
# The KB has ~210 entries; throwing them all into every prompt would burn
# 6-8k tokens on rules the current scene can't even use. So we filter by
# scene_mode + person_count, sort by axes-overlap + priority, and yield
# only the top-N (default 30 ≈ 800-900 tokens).
#
# The output format is what rule 14 in SYSTEM_INSTRUCTION expects:
#     [rule_id] name_zh — summary（when_to_use 简写）
# A few counter-examples are mixed in so the LLM learns when *not* to
# apply a rule too — important for not turning every shot into a thirds
# grid recital.
# ────────────────────────────────────────────────────────────────────────────


_AXIS_NAMES = {
    "composition", "light", "color", "depth",
    "subject_fit", "background", "theme",
}


def _entry_score(entry: dict[str, Any], scene_mode: str, axes_focus: list[str]) -> float:
    """Higher score = more relevant. Combines priority + axis overlap +
    scene_mode match. Entries that don't list our scene_mode get a hard
    -inf so they're excluded (filtered out before sort, but we belt-and-
    suspender it here in case the caller forgets)."""
    sm = entry.get("scene_modes") or []
    if sm and scene_mode not in sm:
        return float("-inf")
    priority = float(entry.get("priority", 3))
    axes = set(entry.get("axes") or [])
    overlap = len(axes & set(axes_focus)) if axes_focus else 0
    # Light bonus when the entry covers a wider range of axes (more useful
    # globally), but nothing dramatic.
    breadth = min(len(axes), 3) * 0.1
    return priority * 2.0 + overlap * 1.5 + breadth


def summarize_composition_kb(
    kb: list[dict[str, Any]],
    scene_mode: str = "portrait",
    person_count: int = 1,
    axes_focus: list[str] | None = None,
    top_n: int = 30,
) -> str:
    """Produce the ── 专业摄影评判字典 ── block to be injected into the
    user prompt. Output is plain text; one line per rule.

    Empty KB or unknown scene_mode -> returns a tight placeholder so the
    prompt still validates downstream.
    """
    if not kb:
        return "(专业评判字典暂未加载，请使用通用规则)"
    axes_focus = axes_focus or list(_AXIS_NAMES)

    candidates: list[tuple[float, dict[str, Any]]] = []
    for entry in kb:
        score = _entry_score(entry, scene_mode, axes_focus)
        if score == float("-inf"):
            continue
        person_range = entry.get("person_count_range")
        if person_range and isinstance(person_range, list) and len(person_range) == 2:
            lo, hi = person_range
            if not (lo <= person_count <= hi):
                continue
        candidates.append((score, entry))

    candidates.sort(key=lambda t: t[0], reverse=True)
    chosen = [e for _, e in candidates[:top_n]]

    lines: list[str] = []
    for entry in chosen:
        when = ", ".join((entry.get("when_to_use") or [])[:2]) or "通用"
        lines.append(
            f"[{entry.get('id')}] {entry.get('name_zh')} — "
            f"{entry.get('summary', '')}（{when}）"
        )
    # Append up to 2 counter-examples so the LLM internalises "don't
    # blindly apply the rule".
    counters = [
        e for _, e in candidates if e.get("counter_example")
    ][:2]
    for entry in counters:
        if entry in chosen:
            lines.append(
                f"  反例 ↪ [{entry.get('id')}] {entry.get('counter_example')}"
            )
    return "\n".join(lines) if lines else "(没有匹配当前场景的规则，请用通用判断)"
