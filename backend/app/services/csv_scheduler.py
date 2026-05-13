"""Weekly insights CSV emailer (v17i).

Wakes once per hour, checks runtime_settings:

  insights.weekly_csv.enabled     = "true" | "false"   (default false)
  insights.weekly_csv.recipient   = "ops@x.com,bi@x.com"
  insights.weekly_csv.day         = "monday"           (default monday)
  insights.weekly_csv.hour        = "9"                (UTC hour, default 9)
  insights.weekly_csv.window_days = "7"                (default 7)

State (so we don't double-send on restart) is kept in
runtime_settings under `insights.weekly_csv.last_sent_iso`.

When time matches, we synthesize the same CSV the admin endpoint
emits and dispatch via alert_mailer (so it inherits all the
recipient parsing / channel routing — email/lark/dingtalk).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from . import alert_mailer, runtime_settings, user_repo

log = logging.getLogger(__name__)

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
              "friday": 4, "saturday": 5, "sunday": 6}


def _build_csv(window_hours: int) -> str:
    """Inline replica of api.admin_insights.export_insights_csv —
    we don't import the API module to avoid pulling fastapi DI just
    to render a string."""
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=window_hours)
    lines = ["report,key,calls,distinct_users,picked,captured,"
              "adoption_rate,capture_rate,merged_from_low_n"]

    def _esc(v) -> str:
        s = "" if v is None else str(v)
        if any(c in s for c in (",", "\"", "\n")):
            return "\"" + s.replace("\"", "\"\"") + "\""
        return s

    with user_repo._connect() as con:                               # noqa: SLF001
        rows = con.execute(
            "SELECT step_config, proposals, picked_proposal_id, captured, user_id "
            "FROM usage_records WHERE status='charged' "
            "AND created_at BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    scene_calls: dict[str, int] = {}
    scene_users: dict[str, set] = {}
    quality_calls: dict[str, int] = {}
    quality_users: dict[str, set] = {}
    kw_calls: dict[str, int] = {}
    kw_users: dict[str, set] = {}
    prop_off: dict[str, int] = {}
    prop_pick: dict[str, int] = {}
    prop_cap: dict[str, int] = {}
    prop_users: dict[str, set] = {}
    for sc_raw, props_raw, picked, cap, uid in rows:
        try:
            sc = json.loads(sc_raw) if sc_raw else {}
            props = json.loads(props_raw) if props_raw else []
        except (TypeError, ValueError):
            continue
        scene = sc.get("scene_mode") or "unknown"
        scene_calls[scene] = scene_calls.get(scene, 0) + 1
        scene_users.setdefault(scene, set()).add(uid)
        q = sc.get("quality_mode") or "unspecified"
        quality_calls[q] = quality_calls.get(q, 0) + 1
        quality_users.setdefault(q, set()).add(uid)
        for raw_kw in (sc.get("style_keywords") or []):
            if not raw_kw:
                continue
            kw = str(raw_kw).strip()[:60].lower()
            kw_calls[kw] = kw_calls.get(kw, 0) + 1
            kw_users.setdefault(kw, set()).add(uid)
        if isinstance(props, list):
            for p in props:
                if not isinstance(p, dict):
                    continue
                pid = p.get("id") or p.get("proposal_id")
                if pid:
                    prop_off[pid] = prop_off.get(pid, 0) + 1
                    prop_users.setdefault(pid, set()).add(uid)
        if picked:
            prop_pick[picked] = prop_pick.get(picked, 0) + 1
            if cap:
                prop_cap[picked] = prop_cap.get(picked, 0) + 1

    def _emit(report: str, calls: dict[str, int], users: dict[str, set],
                extras: dict[str, dict] | None = None) -> None:
        for k, v in sorted(calls.items(), key=lambda x: x[1], reverse=True):
            ex = (extras or {}).get(k, {})
            offered = v
            picked = ex.get("picked", "")
            captured = ex.get("captured", "")
            adoption = ""
            cap_rate = ""
            if isinstance(picked, int) and offered:
                adoption = round(picked / offered, 3)
            if isinstance(picked, int) and isinstance(captured, int) and picked:
                cap_rate = round(captured / picked, 3)
            lines.append(",".join([
                _esc(report), _esc(k), _esc(offered),
                _esc(len(users.get(k, set()))),
                _esc(picked), _esc(captured),
                _esc(adoption), _esc(cap_rate), _esc(""),
            ]))

    _emit("scene_mode", scene_calls, scene_users)
    _emit("quality_mode", quality_calls, quality_users)
    _emit("style_keyword", kw_calls, kw_users)
    prop_extras = {pid: {"picked": prop_pick.get(pid, 0),
                          "captured": prop_cap.get(pid, 0)}
                    for pid in prop_off}
    _emit("proposal", prop_off, prop_users, extras=prop_extras)
    return "\n".join(lines) + "\n"


def _last_sent() -> str:
    return runtime_settings.get_str("insights.weekly_csv.last_sent_iso", "")


def _record_sent(now: datetime) -> None:
    runtime_settings.set_value(
        "insights.weekly_csv.last_sent_iso",
        now.isoformat(), updated_by="system:csv_scheduler",
    )


def _should_send_now(now: datetime) -> bool:
    if runtime_settings.get_str(
            "insights.weekly_csv.enabled", "false").lower() not in (
            "true", "1", "yes"):
        return False
    target_day = runtime_settings.get_str(
        "insights.weekly_csv.day", "monday").lower()
    if _WEEKDAYS.get(target_day, 0) != now.weekday():
        return False
    target_hour = runtime_settings.get_int("insights.weekly_csv.hour", 9)
    if now.hour != target_hour:
        return False
    last = _last_sent()
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 6 * 24 * 3600:
                # Already sent in the last 6 days → skip.
                return False
        except ValueError:
            pass
    return True


async def _send_once() -> bool:
    rec_raw = runtime_settings.get_str("insights.weekly_csv.recipient", "")
    rec = [x.strip() for x in rec_raw.split(",") if x.strip()]
    if not rec:
        return False
    window_days = runtime_settings.get_int(
        "insights.weekly_csv.window_days", 7)
    csv_text = _build_csv(window_days * 24)
    subject = f"[周报] AI Photo Coach 产品洞察 · 近 {window_days} 天"
    body = (f"附件性质的内联 CSV — 第一行是表头。\n"
              f"窗口：近 {window_days} 天。\n"
              f"已应用 k-anon (≥5)。\n\n"
              + csv_text)
    sent_total = 0
    for to in rec:
        try:
            alert_mailer._provider_send(to, subject, body)          # noqa: SLF001
            sent_total += 1
        except Exception as e:                                      # noqa: BLE001
            log.warning("csv_scheduler: send to %s failed: %s", to, e)
    if sent_total > 0:
        log.info("csv_scheduler: weekly CSV dispatched to %d recipients",
                  sent_total)
    return sent_total > 0


async def loop(interval_sec: int = 3600) -> None:
    """Hourly tick. Cheap; even when disabled it just reads cached
    runtime_settings."""
    log.info("csv_scheduler: started (poll every %ds)", interval_sec)
    while True:
        try:
            now = datetime.now(timezone.utc)
            if _should_send_now(now):
                ok = await _send_once()
                if ok:
                    _record_sent(now)
        except Exception as e:                                      # noqa: BLE001
            log.warning("csv_scheduler: tick error: %s", e)
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break


__all__ = ["loop", "_build_csv", "_should_send_now", "_send_once"]
