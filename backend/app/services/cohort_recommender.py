"""Lightweight cohort-based proposal recommender (v17i).

The wizard / analyze response uses this to suggest "users like you
usually pick proposal X". It's NOT a full ML model — it's a cheap
SQL aggregation over the last 30 days of `usage_records`, identical
math to `/admin/insights/cooccurrence` but inlined into the request
path with a 5-minute in-process cache so we don't hammer SQLite on
every analyze call.

Privacy: respects the same k-anonymity floor (≥5 distinct users in
the cohort) so no individual's pick can be reverse-engineered. If
the cohort is too sparse we return None and the client falls back
to the LLM's natural ordering.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import user_repo

log = logging.getLogger(__name__)

_MIN_N = 5
_CACHE_TTL_SEC = 5 * 60
_LOOKBACK_DAYS = 30

_lock = threading.RLock()
_cache: dict[str, tuple[float, dict[tuple[str, str], str]]] = {}


def _now() -> float:
    return time.time()


def _build_index() -> tuple[dict[tuple[str, str], str],
                              dict[tuple[str, str], int]]:
    """One-pass scan over `usage_records`, producing:
      * idx   = {(scene, kw): best_proposal_id}
      * sizes = {(scene, kw): distinct_user_count}

    Both are derived from the same row enumeration so we never pay
    the IO cost twice."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    buckets: dict[tuple[str, str], dict[str, dict[str, int]]] = {}
    bucket_users: dict[tuple[str, str], set] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT user_id, step_config, proposals, picked_proposal_id "
            "FROM usage_records WHERE status = 'charged' "
            "AND created_at >= ?",
            (cutoff,),
        ).fetchall()
    for user_id, raw_sc, raw_props, picked in rows:
        try:
            sc = json.loads(raw_sc) if raw_sc else {}
            props = json.loads(raw_props) if raw_props else []
        except (TypeError, ValueError):
            continue
        scene = sc.get("scene_mode") or "unknown"
        kws = [str(k).strip()[:60].lower()
               for k in (sc.get("style_keywords") or [])
               if isinstance(k, str) and k.strip()]
        keys = [(scene, "*")] + [(scene, kw) for kw in kws]
        for k in keys:
            slot = buckets.setdefault(k, {})
            bucket_users.setdefault(k, set()).add(user_id)
            if not isinstance(props, list):
                continue
            for p in props:
                if not isinstance(p, dict):
                    continue
                pid = p.get("id") or p.get("proposal_id")
                if not pid:
                    continue
                d = slot.setdefault(pid, {"offered": 0, "picked": 0})
                d["offered"] += 1
            if picked:
                d = slot.setdefault(picked, {"offered": 0, "picked": 0})
                d["picked"] += 1

    idx: dict[tuple[str, str], str] = {}
    sizes: dict[tuple[str, str], int] = {}
    for key, pmap in buckets.items():
        n_users = len(bucket_users.get(key, set()))
        if n_users < _MIN_N:
            continue
        sizes[key] = n_users
        best_pid: Optional[str] = None
        best_score = -1.0
        for pid, d in pmap.items():
            if d["offered"] < _MIN_N:
                continue
            score = d["picked"] / d["offered"]
            if score > best_score:
                best_score = score
                best_pid = pid
        if best_pid:
            idx[key] = best_pid
    return idx, sizes


def _index_pair() -> tuple[dict[tuple[str, str], str],
                             dict[tuple[str, str], int]]:
    with _lock:
        cached = _cache.get("v")
        if cached and _now() - cached[0] < _CACHE_TTL_SEC:
            return cached[1]                                        # type: ignore[return-value]
        try:
            idx, sizes = _build_index()
        except Exception as e:                                      # noqa: BLE001
            log.warning("cohort_recommender: build failed: %s", e)
            idx, sizes = {}, {}
        _cache["v"] = (_now(), (idx, sizes))                        # type: ignore[assignment]
        return idx, sizes


def _index() -> dict[tuple[str, str], str]:
    """Backwards-compat shim — old callers only need the idx half."""
    return _index_pair()[0]


def recommend(scene_mode: Optional[str],
              style_keywords: Optional[list[str]]) -> Optional[str]:
    res = recommend_detailed(scene_mode, style_keywords)
    return res["proposal_id"] if res else None


def recommend_detailed(scene_mode: Optional[str],
                       style_keywords: Optional[list[str]]
                       ) -> Optional[dict]:
    """Like ``recommend`` but also returns the cohort metadata the
    UI needs to render an explainer chip:

      {"proposal_id": "...", "cohort_size": 12,
       "cohort_basis": "scene+keyword:natural"}

    cohort_size is the count of distinct users whose pick we
    aggregated. cohort_basis tells the user *why* we picked this
    proposal (which slice of the population matched them)."""
    if not scene_mode:
        return None
    idx, sizes = _index_pair()
    for raw in (style_keywords or []):
        kw = str(raw).strip()[:60].lower()
        if not kw:
            continue
        pid = idx.get((scene_mode, kw))
        if pid:
            return {"proposal_id": pid,
                    "cohort_size": sizes.get((scene_mode, kw), 0),
                    "cohort_basis": f"scene+keyword:{kw}"}
    pid = idx.get((scene_mode, "*"))
    if pid:
        return {"proposal_id": pid,
                "cohort_size": sizes.get((scene_mode, "*"), 0),
                "cohort_basis": f"scene:{scene_mode}"}
    return None


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()


__all__ = ["recommend", "reset_for_tests"]
