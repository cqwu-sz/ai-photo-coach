"""Admin product-insight endpoints (v17g).

Aggregated, anonymized usage analytics for product iteration:
  * which scene modes / quality modes do users actually pick
  * which style keywords are popular
  * which proposals get adopted vs ignored

PIPL-friendly contract:
  * never returns user_id, never returns raw frames or contact info
  * applies a min-N=5 floor on every grouping — buckets with fewer
    distinct users are merged into "(其它)" so individual behaviour
    can't be reverse-engineered
  * caller must be admin (enforced by router dep)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Response

from ..services import auth as auth_svc
from ..services import user_repo

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin", "insights"])

_MIN_N = 5  # k-anonymity floor


def _window(hours: int) -> tuple[str, str]:
    h = max(1, min(hours, 24 * 90))
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=h)
    return start.isoformat(), end.isoformat()


def _bucket_with_floor(counts: dict[str, int],
                         user_counts: dict[str, set],
                         *, sort_by: str = "calls") -> list[dict]:
    """Merge buckets where distinct user count < _MIN_N into one
    "其它" row. `sort_by` ∈ {'calls', 'distinct_users'}.

    `calls` view answers "what gets used most"; `distinct_users`
    view answers "what does the userbase like" — high-frequency
    power users can't bias it. Both views are useful; default is
    calls but the iOS / API caller can flip."""
    out: list[dict] = []
    other_calls = 0
    other_users: set = set()
    for k, v in counts.items():
        users = user_counts.get(k, set())
        if len(users) < _MIN_N:
            other_calls += v
            other_users |= users
        else:
            out.append({"key": k, "calls": v, "distinct_users": len(users)})
    key_fn = (lambda x: x["distinct_users"]) if sort_by == "distinct_users" \
        else (lambda x: x["calls"])
    out.sort(key=key_fn, reverse=True)
    if other_calls > 0:
        out.append({"key": "(其它)", "calls": other_calls,
                     "distinct_users": len(other_users),
                     "merged_from_low_n": True})
    return out


@router.post("/admin/insights/cohort/refresh")
async def cohort_refresh(
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17j — manually bust the cohort recommender's 5-min cache.

    Useful when admin just imported / corrected data and wants to
    verify the next analyze call sees the new cohort distribution
    immediately, instead of waiting up to 5 minutes."""
    from ..services import cohort_recommender
    cohort_recommender.reset_for_tests()
    return {"ok": True, "cleared_at": datetime.now(timezone.utc).isoformat()}


@router.get("/admin/insights/scene_modes")
async def scene_mode_distribution(
    hours: int = 24 * 7,
    metric: str = "calls",
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """Which scene_mode (portrait/scenery/light_shadow/etc.) do users pick.

    Counts only `charged` records — i.e. real successful 出片. Failed
    rows don't count, since the user didn't really 'use' that mode."""
    start, end = _window(hours)
    counts: dict[str, int] = {}
    user_counts: dict[str, set] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT step_config, user_id FROM usage_records "
            "WHERE status = 'charged' AND created_at BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    for r in rows:
        try:
            sc = json.loads(r[0]) if r[0] else {}
        except (TypeError, ValueError):
            continue
        mode = sc.get("scene_mode") or "unknown"
        counts[mode] = counts.get(mode, 0) + 1
        user_counts.setdefault(mode, set()).add(r[1])
    return {
        "since_hours": hours,
        "metric": metric,
        "total_calls": sum(counts.values()),
        "items": _bucket_with_floor(counts, user_counts, sort_by=metric),
    }


@router.get("/admin/insights/quality_modes")
async def quality_mode_distribution(
    hours: int = 24 * 7,
    metric: str = "calls",
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """Distribution of `quality_mode` (fast/high) the user requested."""
    start, end = _window(hours)
    counts: dict[str, int] = {}
    user_counts: dict[str, set] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT step_config, user_id FROM usage_records "
            "WHERE status = 'charged' AND created_at BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    for r in rows:
        try:
            sc = json.loads(r[0]) if r[0] else {}
        except (TypeError, ValueError):
            continue
        q = sc.get("quality_mode") or "unspecified"
        counts[q] = counts.get(q, 0) + 1
        user_counts.setdefault(q, set()).add(r[1])
    return {
        "since_hours": hours,
        "metric": metric,
        "total_calls": sum(counts.values()),
        "items": _bucket_with_floor(counts, user_counts, sort_by=metric),
    }


@router.get("/admin/insights/style_keywords")
async def style_keyword_distribution(
    hours: int = 24 * 7,
    top_n: int = 30,
    metric: str = "calls",
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """Top style keywords users typed in the wizard.

    Each record can have up to 12 keywords; we explode them. Floor
    still applies — niche / personal keywords used by < 5 distinct
    users get rolled into "(其它)" so we don't expose anyone's
    quirky one-off prompt."""
    start, end = _window(hours)
    top_n = max(5, min(top_n, 200))
    counts: dict[str, int] = {}
    user_counts: dict[str, set] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT step_config, user_id FROM usage_records "
            "WHERE status = 'charged' AND created_at BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    for r in rows:
        try:
            sc = json.loads(r[0]) if r[0] else {}
        except (TypeError, ValueError):
            continue
        kws = sc.get("style_keywords") or []
        if not isinstance(kws, list):
            continue
        for kw in kws:
            if not kw:
                continue
            kw_norm = str(kw).strip()[:60].lower()
            counts[kw_norm] = counts.get(kw_norm, 0) + 1
            user_counts.setdefault(kw_norm, set()).add(r[1])
    items = _bucket_with_floor(counts, user_counts, sort_by=metric)
    return {
        "since_hours": hours,
        "metric": metric,
        "distinct_keywords_seen": len(counts),
        "items": items[:top_n],
    }


@router.get("/admin/insights/proposal_adoption")
async def proposal_adoption(
    hours: int = 24 * 7,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """How often does each proposal_id get picked vs ignored.

    A "proposal" is an output scheme the LLM returned. We score:
      * offered  — how many times this id appeared as a candidate
      * picked   — how many times the user actually picked it
      * captured — how many of those resulted in a real shot

    Adoption rate = picked / offered. Capture rate = captured / picked.

    We bucket by `proposal_id` (LLM-issued, stable across users for a
    given preset) when present; otherwise by `slug` derived from the
    proposal title to keep the report meaningful."""
    start, end = _window(hours)
    offered: dict[str, int] = {}
    picked: dict[str, int] = {}
    captured: dict[str, int] = {}
    user_offered: dict[str, set] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT user_id, proposals, picked_proposal_id, captured "
            "FROM usage_records "
            "WHERE status = 'charged' AND created_at BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    for user_id, raw, picked_id, was_captured in rows:
        try:
            props = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            continue
        if not isinstance(props, list):
            continue
        for p in props:
            if not isinstance(p, dict):
                continue
            pid = (p.get("id") or p.get("proposal_id")
                    or _slug(p.get("title") or p.get("name") or ""))
            if not pid:
                continue
            offered[pid] = offered.get(pid, 0) + 1
            user_offered.setdefault(pid, set()).add(user_id)
        if picked_id:
            picked[picked_id] = picked.get(picked_id, 0) + 1
            if was_captured:
                captured[picked_id] = captured.get(picked_id, 0) + 1

    # Apply k-anon floor on offered side, then enrich with picked/cap.
    base = _bucket_with_floor(offered, user_offered)
    out: list[dict] = []
    other_picked = picked.copy()
    other_captured = captured.copy()
    for row in base:
        if row.get("merged_from_low_n"):
            # Sum picked/captured across whatever got merged. Cheap
            # over-approx: subtract everything we already attributed.
            row["picked"] = max(0, sum(other_picked.values())
                                  - sum(o.get("picked", 0) for o in out))
            row["captured"] = max(0, sum(other_captured.values())
                                    - sum(o.get("captured", 0) for o in out))
        else:
            row["picked"] = picked.get(row["key"], 0)
            row["captured"] = captured.get(row["key"], 0)
        row["adoption_rate"] = (round(row["picked"] / row["calls"], 3)
                                  if row["calls"] else 0.0)
        row["capture_rate"] = (round(row["captured"] / row["picked"], 3)
                                 if row["picked"] else 0.0)
        out.append(row)
    return {
        "since_hours": hours,
        "total_offered": sum(offered.values()),
        "total_picked": sum(picked.values()),
        "total_captured": sum(captured.values()),
        "items": out,
    }


# ---------------------------------------------------------------------------
# v17h — CSV export, time series, collaborative-filter hint
# ---------------------------------------------------------------------------


def _csv_escape(v) -> str:
    s = "" if v is None else str(v)
    if any(c in s for c in (",", "\"", "\n", "\r")):
        return "\"" + s.replace("\"", "\"\"") + "\""
    return s


@router.get("/admin/insights/export.csv")
async def export_insights_csv(
    hours: int = 24 * 7,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> Response:
    """v17h — single CSV with all four insight tables stacked.

    Operations / BI need flat tabular for quarterly review; we
    emit a denormalised long table:
      report,key,calls,distinct_users,extra_metric,extra_value
    The four reports identify themselves via the `report` column."""
    scene = await scene_mode_distribution(hours=hours, user=user)   # type: ignore[arg-type]
    quality = await quality_mode_distribution(hours=hours, user=user)  # type: ignore[arg-type]
    keywords = await style_keyword_distribution(hours=hours, user=user)  # type: ignore[arg-type]
    proposals = await proposal_adoption(hours=hours, user=user)     # type: ignore[arg-type]

    lines: list[str] = [
        "report,key,calls,distinct_users,picked,captured,"
        "adoption_rate,capture_rate,merged_from_low_n",
    ]
    def _emit(report: str, items: list[dict]) -> None:
        for it in items:
            lines.append(",".join([
                _csv_escape(report),
                _csv_escape(it.get("key")),
                _csv_escape(it.get("calls")),
                _csv_escape(it.get("distinct_users")),
                _csv_escape(it.get("picked", "")),
                _csv_escape(it.get("captured", "")),
                _csv_escape(it.get("adoption_rate", "")),
                _csv_escape(it.get("capture_rate", "")),
                _csv_escape("yes" if it.get("merged_from_low_n") else ""),
            ]))
    _emit("scene_mode", scene["items"])
    _emit("quality_mode", quality["items"])
    _emit("style_keyword", keywords["items"])
    _emit("proposal", proposals["items"])

    body = "\n".join(lines) + "\n"
    headers = {
        "Content-Disposition":
            f'attachment; filename="aiphoto-insights-{hours}h.csv"',
    }
    return Response(content=body, media_type="text/csv; charset=utf-8",
                     headers=headers)


@router.get("/admin/insights/keywords/series")
async def style_keyword_series(
    hours: int = 24 * 30,
    bucket_hours: int = 24,
    top_n: int = 8,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17h — heat over time for the top N style keywords. Lets
    admin spot a trend (e.g. "复古 trending up the past 2 weeks")
    instead of just current totals.

    Approach: pick top N keywords from the FULL window first, then
    bucket their usage in `bucket_hours` slots so the legend stays
    stable across the whole chart."""
    hours = max(24, min(hours, 24 * 90))
    bucket_hours = max(1, min(bucket_hours, 168))
    start, end = _window(hours)
    bucket_sec = bucket_hours * 3600
    # First pass — find top N across the whole window.
    totals: dict[str, int] = {}
    user_totals: dict[str, set] = {}
    rows: list[tuple[str, str, str]] = []
    with user_repo._connect() as con:                               # noqa: SLF001
        for r in con.execute(
            "SELECT created_at, step_config, user_id FROM usage_records "
            "WHERE status = 'charged' AND created_at BETWEEN ? AND ?",
            (start, end),
        ).fetchall():
            try:
                sc = json.loads(r[1]) if r[1] else {}
            except (TypeError, ValueError):
                continue
            kws = sc.get("style_keywords") or []
            if not isinstance(kws, list):
                continue
            for kw in kws:
                if not kw:
                    continue
                norm = str(kw).strip()[:60].lower()
                totals[norm] = totals.get(norm, 0) + 1
                user_totals.setdefault(norm, set()).add(r[2])
                rows.append((r[0], norm, r[2]))
    top = _bucket_with_floor(totals, user_totals)[:top_n]
    top_keys = [t["key"] for t in top if t["key"] != "(其它)"]

    # Second pass — fill buckets only for the top keys.
    series: dict[str, dict[int, int]] = {k: {} for k in top_keys}
    for created_at, kw_norm, _user_id in rows:
        if kw_norm not in series:
            continue
        try:
            ts = datetime.fromisoformat(created_at)
        except (TypeError, ValueError):
            continue
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % bucket_sec)
        series[kw_norm][bucket] = series[kw_norm].get(bucket, 0) + 1

    all_buckets = sorted({b for d in series.values() for b in d})
    return {
        "since_hours": hours,
        "bucket_hours": bucket_hours,
        "keys": top_keys,
        "buckets": [datetime.fromtimestamp(b, tz=timezone.utc).isoformat()
                    for b in all_buckets],
        "values": [
            {"key": k, "counts": [series[k].get(b, 0) for b in all_buckets]}
            for k in top_keys
        ],
    }


@router.get("/admin/insights/cooccurrence")
async def proposal_cooccurrence(
    hours: int = 24 * 30,
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17h — collaborative-filter hint: for each (scene, top
    keyword) pair, which proposal_id has the highest adoption rate?
    Use this as a default ranking signal in the next iteration of
    the wizard so users land on what their cohort actually picks.

    Not full CF — that needs proper SVD / ALS — but a simple
    bucketed lookup that's cheap to compute on every refresh and
    easy to explain to non-ML stakeholders."""
    start, end = _window(hours)
    # bucket: (scene, keyword_or_*) -> proposal_id -> {offered, picked}
    buckets: dict[tuple[str, str], dict[str, dict[str, int]]] = {}
    user_buckets: dict[tuple[str, str], dict[str, set]] = {}
    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT user_id, step_config, proposals, picked_proposal_id "
            "FROM usage_records WHERE status = 'charged' "
            "AND created_at BETWEEN ? AND ?",
            (start, end),
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
        # Each record contributes to one (scene, '*') bucket and one
        # (scene, kw) bucket per kw — gives both "what works for
        # portrait overall" and "what works for portrait+复古".
        keys = [(scene, "*")] + [(scene, kw) for kw in kws]
        for k in keys:
            slot = buckets.setdefault(k, {})
            uslot = user_buckets.setdefault(k, {})
            for p in props:
                if not isinstance(p, dict):
                    continue
                pid = (p.get("id") or p.get("proposal_id")
                        or _slug(p.get("title") or p.get("name") or ""))
                if not pid:
                    continue
                d = slot.setdefault(pid, {"offered": 0, "picked": 0})
                d["offered"] += 1
                uslot.setdefault(pid, set()).add(user_id)
            if picked:
                d = slot.setdefault(picked, {"offered": 0, "picked": 0})
                d["picked"] += 1

    out: list[dict] = []
    for (scene, keyword), pmap in buckets.items():
        # k-anon: skip buckets where < _MIN_N distinct users overall.
        u_all: set = set()
        for s in user_buckets.get((scene, keyword), {}).values():
            u_all |= s
        if len(u_all) < _MIN_N:
            continue
        # Pick the proposal with max adoption_rate (picked/offered),
        # min 5 offers so we don't recommend a rare lottery winner.
        best_pid = None; best_score = -1.0; best_d = None
        for pid, d in pmap.items():
            if d["offered"] < _MIN_N:
                continue
            score = d["picked"] / d["offered"]
            if score > best_score:
                best_score = score; best_pid = pid; best_d = d
        if best_pid is None:
            continue
        out.append({
            "scene_mode": scene,
            "keyword": keyword,
            "recommended_proposal_id": best_pid,
            "adoption_rate": round(best_score, 3),
            "offered": best_d["offered"],
            "picked": best_d["picked"],
            "distinct_users": len(u_all),
        })
    out.sort(key=lambda x: (x["scene_mode"], x["keyword"]))
    return {"since_hours": hours, "items": out}


@router.get("/admin/insights/compare")
async def compare_scenes(
    hours: int = 24 * 30,
    scenes: str = "portrait,scenery",
    dimension: str = "quality_modes",
    user: auth_svc.CurrentUser = Depends(auth_svc.require_admin),
) -> dict:
    """v17i — side-by-side comparison of two scene_modes along one
    dimension (quality_modes / style_keywords / proposal adoption).

    Use case: "how do portrait users differ from scenery users in
    quality preference?"
    """
    parts = [s.strip() for s in (scenes or "").split(",") if s.strip()]
    if len(parts) != 2:
        return {"error": "scenes must be a 2-element comma list"}
    if dimension not in ("quality_modes", "style_keywords"):
        return {"error": "dimension must be quality_modes or style_keywords"}
    start, end = _window(hours)

    def _collect(scene: str) -> dict:
        counts: dict[str, int] = {}
        users: dict[str, set] = {}
        with user_repo._connect() as con:                           # noqa: SLF001
            rows = con.execute(
                "SELECT step_config, user_id FROM usage_records "
                "WHERE status = 'charged' AND created_at BETWEEN ? AND ?",
                (start, end),
            ).fetchall()
        for sc_raw, uid in rows:
            try:
                sc = json.loads(sc_raw) if sc_raw else {}
            except (TypeError, ValueError):
                continue
            if sc.get("scene_mode") != scene:
                continue
            if dimension == "quality_modes":
                vals = [sc.get("quality_mode") or "unspecified"]
            else:
                vals = [str(k).strip()[:60].lower()
                        for k in (sc.get("style_keywords") or [])
                        if isinstance(k, str) and k.strip()]
            for v in vals:
                counts[v] = counts.get(v, 0) + 1
                users.setdefault(v, set()).add(uid)
        return {
            "scene": scene,
            "total": sum(counts.values()),
            "items": _bucket_with_floor(counts, users),
        }

    a = _collect(parts[0])
    b = _collect(parts[1])

    # Compute "preference shift": for each key present in either side,
    # what's the % share difference. Useful to spot "scenery users
    # use 'high' quality 3× more often than portrait users".
    keys = {it["key"] for side in (a, b) for it in side["items"]
            if it["key"] != "(其它)"}
    diff: list[dict] = []
    a_total = max(a["total"], 1)
    b_total = max(b["total"], 1)
    for k in keys:
        a_calls = next((it["calls"] for it in a["items"] if it["key"] == k), 0)
        b_calls = next((it["calls"] for it in b["items"] if it["key"] == k), 0)
        a_share = a_calls / a_total
        b_share = b_calls / b_total
        diff.append({
            "key": k,
            f"{parts[0]}_share": round(a_share, 4),
            f"{parts[1]}_share": round(b_share, 4),
            "delta_pp": round((b_share - a_share) * 100, 2),
        })
    diff.sort(key=lambda x: abs(x["delta_pp"]), reverse=True)

    return {
        "since_hours": hours,
        "dimension": dimension,
        "left": a, "right": b,
        "delta_top": diff[:20],
    }


def _slug(text: str) -> str:
    s = "".join(ch.lower() if ch.isalnum() else "-" for ch in text.strip())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:64]


__all__ = ["router"]
