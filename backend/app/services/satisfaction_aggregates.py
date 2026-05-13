"""Cross-user satisfaction rollup (v18).

Powers the optional `## CROSS_USER_TREND` block in the analyze prompt.
A separate service from `user_preferences` because:
  * counts must be aggregated across users without ever surfacing
    individual identities;
  * the `distinct_users` k-anonymity floor is enforced at READ time
    (not at write) so we don't have to maintain a cumulative HLL.

Storage layout (see user_repo._ensure_schema_v2):
  satisfaction_aggregates(scene_mode, style_id, satisfied_count,
                          dissatisfied_count, distinct_users, updated_at)

`distinct_users` is recomputed lazily by `_refresh_distinct_users`,
NOT incremented per write. It uses
  SELECT COUNT(DISTINCT user_id)
  FROM user_preferences WHERE scene_mode=? AND style_id=?;
which leverages the (user_id, scene_mode) index already on that table.
A 5-minute in-process cache wraps `render_global_hint` so we don't
re-run COUNT DISTINCT on every analyze call.

Admin gates this whole layer via runtime_settings:
  pref.global_hint.enabled              (default false)
  pref.global_hint.min_distinct_users   (default 30)
  pref.global_hint.min_satisfaction_rate (default 0.6)
  pref.global_hint.cooldown_sec         (default 300)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from . import runtime_settings, style_catalog, user_repo

log = logging.getLogger(__name__)


# Default knob values; admin can override via runtime_settings.
_DEFAULTS = {
    "enabled":                "false",
    "min_distinct_users":     30,
    "min_satisfaction_rate":  0.6,
    "cooldown_sec":           300,
}


_lock = threading.RLock()
# v18 c4 — value tuple: (built_at_monotonic, max_updated_at_seen, rows).
# Any other process bumping satisfaction_aggregates writes a newer
# updated_at; we re-read MAX(updated_at) on each cache check (cheap,
# indexed by (scene_mode) implicitly via the small row count) and
# bypass cooldown when it advances. This gives multi-worker
# correctness without needing Redis pub/sub.
_cache: dict[str, tuple[float, str, list[dict]]] = {}


def _now() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _knob_str(key: str) -> str:
    return runtime_settings.get_str(
        f"pref.global_hint.{key}", str(_DEFAULTS[key]))


def _knob_int(key: str) -> int:
    return runtime_settings.get_int(
        f"pref.global_hint.{key}", int(_DEFAULTS[key]))  # type: ignore[arg-type]


def _knob_float(key: str) -> float:
    raw = runtime_settings.get_str(
        f"pref.global_hint.{key}", str(_DEFAULTS[key]))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULTS[key])  # type: ignore[arg-type]


def is_enabled() -> bool:
    return _knob_str("enabled").strip().lower() in ("1", "true", "yes")


def record(*, scene_mode: str, style_id: str, user_id: str,
            satisfied: bool) -> None:
    """Bump satisfied / dissatisfied counts for this (scene, style).

    distinct_users column is not touched here — it's recomputed
    lazily on read. user_id is accepted for future-proofing (e.g.
    if we later want a per-write HLL) but currently not stored.
    """
    col = "satisfied_count" if satisfied else "dissatisfied_count"
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT INTO satisfaction_aggregates (scene_mode, style_id, "
            f"{col}, updated_at) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(scene_mode, style_id) DO UPDATE SET "
            f"{col} = {col} + 1, updated_at = excluded.updated_at",
            (scene_mode, style_id, _now_iso()),
        )
    # Bust read-cache so admins viewing the dashboard see fresh
    # numbers rather than waiting for cooldown_sec.
    with _lock:
        _cache.pop("rows", None)


def _build_rows(scene_mode: str) -> list[dict]:
    """Pull the aggregate rows for a scene, recompute distinct_users,
    and apply the k-anon + satisfaction-rate filters."""
    min_dist = _knob_int("min_distinct_users")
    min_rate = _knob_float("min_satisfaction_rate")
    out: list[dict] = []
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT style_id, satisfied_count, dissatisfied_count "
            "FROM satisfaction_aggregates WHERE scene_mode = ?",
            (scene_mode,),
        ).fetchall()
        for r in rows:
            sat = int(r["satisfied_count"] or 0)
            dis = int(r["dissatisfied_count"] or 0)
            total = sat + dis
            if total == 0:
                continue
            distinct = con.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_preferences "
                "WHERE scene_mode = ? AND style_id = ?",
                (scene_mode, r["style_id"]),
            ).fetchone()[0]
            if distinct < min_dist:
                continue
            rate = sat / total
            if rate < min_rate:
                continue
            out.append({
                "style_id": r["style_id"],
                "label_zh": style_catalog.label_zh(r["style_id"]),
                "satisfied": sat,
                "dissatisfied": dis,
                "distinct_users": int(distinct),
                "satisfaction_rate": round(rate, 3),
            })
    out.sort(key=lambda x: (x["satisfaction_rate"], x["satisfied"]),
              reverse=True)
    # Persist back distinct_users + updated_at so admin views can read
    # them cheaply without re-running COUNT DISTINCT.
    with user_repo._connect() as con:                               # noqa: SLF001
        for r in out:
            con.execute(
                "UPDATE satisfaction_aggregates SET distinct_users = ?, "
                "updated_at = ? WHERE scene_mode = ? AND style_id = ?",
                (r["distinct_users"], _now_iso(), scene_mode, r["style_id"]),
            )
    return out


def _max_updated_at(scene_mode: str) -> str:
    try:
        with user_repo._connect() as con:                           # noqa: SLF001
            row = con.execute(
                "SELECT MAX(updated_at) FROM satisfaction_aggregates "
                "WHERE scene_mode = ?",
                (scene_mode,),
            ).fetchone()
        return (row[0] or "") if row else ""
    except Exception:                                                # noqa: BLE001
        return ""


def _scene_rows_cached(scene_mode: str) -> list[dict]:
    cooldown = _knob_int("cooldown_sec")
    cache_key = f"rows:{scene_mode}"
    current_token = _max_updated_at(scene_mode)
    with _lock:
        cached = _cache.get(cache_key)
        if cached:
            built_at, token_seen, rows = cached
            fresh_window = (_now() - built_at) < cooldown
            unchanged = (token_seen == current_token)
            if fresh_window and unchanged:
                return rows
    try:
        rows = _build_rows(scene_mode)
    except Exception as e:                                          # noqa: BLE001
        log.warning("satisfaction_aggregates build failed: %s", e)
        rows = []
    with _lock:
        _cache[cache_key] = (_now(), current_token, rows)
    return rows


def render_global_hint(scene_mode: str) -> Optional[str]:
    """Return a soft-suggestion paragraph for the prompt, or None."""
    if not is_enabled():
        return None
    rows = _scene_rows_cached(scene_mode)
    if not rows:
        return None
    head = rows[0]
    scene_zh = style_catalog.scene_label_zh(scene_mode)
    return (
        f"在「{scene_zh}」场景下，{head['distinct_users']} 位用户中"
        f" {int(head['satisfaction_rate']*100)}% 对「{head['label_zh']}」"
        f"风格的成片表示满意。可作为弱参考；如本次现场条件不适合，"
        f"请优先尊重现场。"
    )


def list_for_admin(scene_mode: Optional[str] = None,
                    sort_by: str = "rate") -> list[dict]:
    """Admin dashboard view — returns ALL rows (does not apply
    enabled/threshold gates), so the operator can see what would
    light up if they relaxed the knobs.

    sort_by:
      * "rate" (default) — satisfaction_rate desc
      * "distinct_users" — distinct_users desc (find buckets close to
        k-anon threshold)
      * "satisfied"      — total satisfied count desc
      * "updated_at"     — most-recently-touched first
    """
    out: list[dict] = []
    with user_repo._connect() as con:                               # noqa: SLF001
        if scene_mode:
            rows = con.execute(
                "SELECT scene_mode, style_id, satisfied_count, "
                "dissatisfied_count, distinct_users, updated_at "
                "FROM satisfaction_aggregates WHERE scene_mode = ?",
                (scene_mode,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT scene_mode, style_id, satisfied_count, "
                "dissatisfied_count, distinct_users, updated_at "
                "FROM satisfaction_aggregates"
            ).fetchall()
    for r in rows:
        sat = int(r["satisfied_count"] or 0)
        dis = int(r["dissatisfied_count"] or 0)
        total = sat + dis
        out.append({
            "scene_mode": r["scene_mode"],
            "style_id": r["style_id"],
            "label_zh": style_catalog.label_zh(r["style_id"]),
            "satisfied": sat,
            "dissatisfied": dis,
            "distinct_users": int(r["distinct_users"] or 0),
            "satisfaction_rate": (round(sat / total, 3) if total else None),
            "updated_at": r["updated_at"],
        })
    sort_keys = {
        "rate":           lambda x: (x["satisfaction_rate"] or 0.0,
                                       x["satisfied"]),
        "distinct_users": lambda x: (x["distinct_users"], x["satisfied"]),
        "satisfied":      lambda x: (x["satisfied"], x["distinct_users"]),
        "updated_at":     lambda x: (x["updated_at"] or ""),
    }
    out.sort(key=sort_keys.get(sort_by, sort_keys["rate"]),
              reverse=True)
    return out


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()


__all__ = [
    "is_enabled",
    "record",
    "render_global_hint",
    "list_for_admin",
    "reset_for_tests",
]
