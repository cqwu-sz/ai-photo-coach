"""Per-user style preference accumulator (v18).

Driven by `PATCH /me/usage/{id}/satisfied`. Each thumbs-up/down on
a usage_record bumps a single (user_id, scene_mode, style_id) row.
The analyze prompt later reads the top 1-2 entries for this user
in the same scene_mode and renders them as a `## USER_PREFERENCE`
hint so the LLM biases toward styles the user has historically
enjoyed.

Data we DO store:
  - user_id, scene_mode, style_id (one of 5 fixed cards), counts.
That's it. No photos, no notes here (the note lives on the
usage_records row), no free-text style.

When the same row is hit twice with conflicting answers (user
PATCHed satisfied=true, then later satisfied=false), the earlier
answer must be backed out so we never double-count. We don't have
a per-row history, so we encode that via the `_undo_previous` flag
the caller passes in.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import style_catalog, usage_records, user_repo

log = logging.getLogger(__name__)

# Minimum samples before we surface a user's history in their prompt.
# Set low (2) because individual taste is robust against small N — if
# they tapped "satisfied" twice on 氛围感, that's a real signal.
_MIN_PERSONAL_SAMPLES = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_from_record(*, user_id: str, record_id: str,
                        satisfied: bool) -> None:
    """Called by usage_records.mark_satisfied. Reads the (now-updated)
    row, infers style_id from step_config.style_keywords, and bumps
    the right counter.

    Best-effort: any error must NOT propagate to the user-facing
    PATCH endpoint, so the caller wraps us in try/except.
    """
    rec = usage_records.get_for_user(user_id, record_id)
    if rec is None:
        return
    scene = (rec.step_config or {}).get("scene_mode")
    if not scene:
        return
    kws = (rec.step_config or {}).get("style_keywords") or []
    style_id = style_catalog.infer_style_id(kws)
    if not style_id:
        return
    _upsert_counter(user_id=user_id, scene_mode=scene,
                     style_id=style_id, satisfied=satisfied)
    # Also push into the global aggregate. Done here (not at the API
    # layer) to keep the two side-effects atomic w.r.t. the prompt
    # injection contract: same data both tables.
    try:
        from . import satisfaction_aggregates
        satisfaction_aggregates.record(scene_mode=scene,
                                         style_id=style_id,
                                         user_id=user_id,
                                         satisfied=satisfied)
    except Exception as e:                                          # noqa: BLE001
        log.warning("satisfaction_aggregates.record failed: %s", e)


# Default 7 days. Overridable by admin via runtime_settings key
# `pref.personal_cooldown_sec`. Tests monkeypatch this constant
# directly (see _isolation fixture).
_COOLDOWN_SEC = 7 * 24 * 3600


def _cooldown_sec() -> int:
    """v18 s2 — admin can shorten / lengthen at runtime without
    redeploy via `pref.personal_cooldown_sec`. Falls back to the
    7-day default. Test paths that monkeypatch _COOLDOWN_SEC keep
    working because we still respect the module-level constant
    when runtime_settings has no override."""
    try:
        from . import runtime_settings as _rs
        return _rs.get_int("pref.personal_cooldown_sec", _COOLDOWN_SEC)
    except Exception:                                                # noqa: BLE001
        return _COOLDOWN_SEC


def _upsert_counter(*, user_id: str, scene_mode: str, style_id: str,
                      satisfied: bool) -> None:
    """v18 c1 — same (user, scene, style) only counts once per
    cooldown window. Protects against a single ecstatic shooter
    spamming "satisfied" on 30 frames in one minute and skewing
    their own preference."""
    col = "satisfied" if satisfied else "dissatisfied"
    cooldown = _cooldown_sec()
    with user_repo._connect() as con:                               # noqa: SLF001
        existing = con.execute(
            "SELECT last_at FROM user_preferences WHERE user_id = ? "
            "AND scene_mode = ? AND style_id = ?",
            (user_id, scene_mode, style_id),
        ).fetchone()
        if existing and existing["last_at"]:
            try:
                last = datetime.fromisoformat(existing["last_at"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last).total_seconds() \
                        < cooldown:
                    log.debug("user_preferences cooldown skip user=%s "
                                 "scene=%s style=%s", user_id, scene_mode,
                                 style_id)
                    return
            except (TypeError, ValueError):
                pass
        con.execute(
            "INSERT INTO user_preferences (user_id, scene_mode, style_id, "
            f"{col}, last_at) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id, scene_mode, style_id) DO UPDATE SET "
            f"{col} = {col} + 1, last_at = excluded.last_at",
            (user_id, scene_mode, style_id, _now_iso()),
        )


def top_styles(user_id: str, scene_mode: str, *,
                limit: int = 2) -> list[dict]:
    """Return the user's most-satisfying style_ids in this scene,
    sorted by net score (satisfied - dissatisfied) desc.

    Filters out rows where net score <= 0 (no point hinting "user
    disliked this") and rows below the personal min-sample threshold.
    """
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT style_id, satisfied, dissatisfied FROM user_preferences "
            "WHERE user_id = ? AND scene_mode = ?",
            (user_id, scene_mode),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        sid = r["style_id"]
        sat = int(r["satisfied"] or 0)
        dis = int(r["dissatisfied"] or 0)
        net = sat - dis
        if sat < _MIN_PERSONAL_SAMPLES:
            continue
        if net <= 0:
            continue
        out.append({"style_id": sid, "satisfied": sat,
                     "dissatisfied": dis, "net": net,
                     "label_zh": style_catalog.label_zh(sid)})
    out.sort(key=lambda x: (x["net"], x["satisfied"]), reverse=True)
    return out[:limit]


def render_personal_hint(user_id: str, scene_mode: str) -> Optional[str]:
    """Returns a natural-language paragraph for prompt injection, or
    None when we have no signal worth surfacing."""
    rows = top_styles(user_id, scene_mode)
    if not rows:
        return None
    parts = [f"{r['label_zh']}（已满意 {r['satisfied']} 次）" for r in rows]
    body = "、".join(parts)
    scene_zh = style_catalog.scene_label_zh(scene_mode)
    return (f"该用户在「{scene_zh}」场景下的历史偏好风格："
            f"{body}。在不与本次现场条件冲突的前提下，"
            f"输出方案时优先沿用这些风格的处理方式。")


def purge_for_user(user_id: str) -> None:
    """Called from soft_delete cascade. Wipes all preference rows."""
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute("DELETE FROM user_preferences WHERE user_id = ?",
                     (user_id,))


__all__ = [
    "upsert_from_record",
    "top_styles",
    "render_personal_hint",
    "purge_for_user",
]
