"""Admin alert dispatcher (v17g/v17h).

Subscribers list: per-action mapping in `runtime_settings`. Each
entry is a comma-separated list of *channel-typed targets*:

  alert.recipients.<action> = "a@x.com,lark://https://open.feishu.cn/.../xxx,
                                 dingtalk://https://oapi.dingtalk.com/.../yyy"

Channel inference:
  * looks like email ("@" present)        → email   (Aliyun DirectMail)
  * starts with "lark://"  / "feishu://"  → lark    (custom robot webhook)
  * starts with "dingtalk://" / "ding://" → dingtalk (custom robot webhook)
  * starts with "webhook://"              → generic JSON POST
  * anything else is dropped (logged warning)

Email is good for "I want a record"; webhook is good for "wake me
up in 30 seconds". Use both simultaneously by listing both.

Throttle key still per-action so listing 5 channels doesn't fire
5×; one alert event = one send pass per channel.

Format goal: subject is one line, body is human-readable Markdown-ish
plain text that a phone preview shows correctly. NO json dumps.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from . import rate_buckets, runtime_settings, user_repo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------


def _split(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def severity_for(action: str) -> str:
    """v17j — coarse 4-tier severity used for channel routing.

    Tiers (high → low):
      critical : security breach / financial loss / abuse — page on-call now
      warning  : something the business owner cares about within the day
      info     : audit-trail noise that's nice to keep, not nice to be
                 woken up by (admin login, runtime knob tweak)
      trend    : product-analytics-grade signals (keyword spike) — should
                 NEVER share an inbox with critical alerts

    Admin can override with `alert.severity.<action>` in runtime_settings.
    """
    override = runtime_settings.get_str(f"alert.severity.{action}", "")
    if override:
        return override.lower()
    if action.startswith("trend."):
        return "trend"
    if action in ("asn.signature_invalid", "alert.webhook_failed",
                   "iap.local.error"):
        return "critical"
    if (action in ("iap.asn.refund", "iap.asn.revoke", "asn.unmatched",
                    "otp.permanent_lock", "user.soft_delete")
            or action.startswith("iap.asn.")):
        return "warning"
    return "info"


def recipients_for(action: str) -> list[str]:
    """Resolution order:
      1. exact action override                  alert.recipients.<action>
      2. dotted-prefix override                 alert.recipients.iap.asn.*
      3. severity tier (v17j)                   alert.recipients.severity.warning
      4. global default                         alert.recipients.default
    """
    rs = runtime_settings
    if rs.get_str("alert.enabled", "true").lower() not in ("1", "true", "yes"):
        return []
    exact = _split(rs.get_str(f"alert.recipients.{action}", ""))
    if exact:
        return exact
    parts = action.split(".")
    for i in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:i]) + ".*"
        rec = _split(rs.get_str(f"alert.recipients.{prefix}", ""))
        if rec:
            return rec
    sev = severity_for(action)
    sev_rec = _split(rs.get_str(f"alert.recipients.severity.{sev}", ""))
    if sev_rec:
        return sev_rec
    return _split(rs.get_str("alert.recipients.default", ""))


def cooldown_sec_for(action: str) -> int:
    """Per-action throttle to prevent storms when something explodes."""
    return runtime_settings.get_int(f"alert.cooldown_sec.{action}",
                                      runtime_settings.get_int(
                                          "alert.cooldown_sec.default", 300))


# ---------------------------------------------------------------------------
# Formatting — keep it readable; avoid json walls.
# ---------------------------------------------------------------------------


_SEVERITY: dict[str, tuple[str, str]] = {
    # action            (icon, severity label)
    "iap.asn.refund":   ("[退款]", "需关注"),
    "iap.asn.revoke":   ("[撤销]", "需关注"),
    "asn.signature_invalid":   ("[安全]", "高危"),
    "asn.unmatched":           ("[孤儿订阅]", "需关注"),
    "otp.permanent_lock":      ("[永久封号]", "异常"),
    "auth.admin_login_success":("[管理员登录]", "提醒"),
    "endpoint_config.save":    ("[配置变更]", "提醒"),
    "user.data_export":        ("[数据导出]", "提醒"),
}


def _icon_label(action: str) -> tuple[str, str]:
    return _SEVERITY.get(action, ("[告警]", "提醒"))


def format_subject(action: str, target: Optional[str]) -> str:
    icon, sev = _icon_label(action)
    short = action.split(".")[-1] if "." in action else action
    tail = f" — {target}" if target else ""
    return f"{icon} AI Photo Coach · {short}{tail}"


def _fmt_value(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def format_body(action: str, *, admin_id: str, target: Optional[str],
                  payload: Optional[dict], occurred_at: str) -> str:
    icon, sev = _icon_label(action)
    lines: list[str] = []
    lines.append(f"{icon} {action}")
    lines.append("")
    lines.append(f"严重级别：{sev}")
    lines.append(f"发生时间：{occurred_at}")
    lines.append(f"操作主体：{admin_id}")
    if target:
        lines.append(f"作用对象：{target}")
    lines.append("")
    if payload:
        lines.append("详情：")
        # Pretty print top-level keys only; nested dicts go one level
        # in. Skip _previous etc. unless small.
        for k, v in payload.items():
            if isinstance(v, dict):
                inner = ", ".join(f"{ik}={_fmt_value(iv)}"
                                    for ik, iv in list(v.items())[:6])
                lines.append(f"  · {k}: {{ {inner} }}")
            elif isinstance(v, (list, tuple)):
                lines.append(f"  · {k}: [{len(v)} 项]")
            else:
                lines.append(f"  · {k}: {_fmt_value(v)}")
    lines.append("")
    lines.append("— 该邮件由 AI Photo Coach 后端自动发送。"
                  "如需调整接收人，请进入 admin app → 运行时阈值，"
                  "修改 alert.recipients.* / alert.enabled。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Channel detection & senders
# ---------------------------------------------------------------------------


def _parse_target(raw: str) -> tuple[str, str] | None:
    """Returns (channel, payload) or None if unparseable."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith(("lark://", "feishu://")):
        return ("lark", raw.split("://", 1)[1])
    if low.startswith(("dingtalk://", "ding://")):
        return ("dingtalk", raw.split("://", 1)[1])
    if low.startswith("webhook://"):
        return ("webhook", raw.split("://", 1)[1])
    if "@" in raw and len(raw) <= 256:
        return ("email", raw)
    log.warning("alert_mailer: unrecognised target %r", raw[:60])
    return None


def _send_lark(url: str, subject: str, body: str) -> None:
    """Lark/Feishu custom robot — text message format.
    https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
    """
    payload = {"msg_type": "text",
                "content": {"text": f"{subject}\n\n{body}"}}
    _post_json(url, payload)


def _send_dingtalk(url: str, subject: str, body: str) -> None:
    """DingTalk custom robot — text message.
    https://open.dingtalk.com/document/orgapp/custom-robot-access
    """
    payload = {"msgtype": "text",
                "text": {"content": f"{subject}\n\n{body}"}}
    _post_json(url, payload)


def _send_generic_webhook(url: str, subject: str, body: str) -> None:
    """Generic JSON POST: {subject, body, ts}. Use for Slack/custom."""
    payload = {"subject": subject, "body": body,
                "ts": datetime.now(timezone.utc).isoformat()}
    _post_json(url, payload)


def _post_json(url: str, payload: dict) -> None:
    """Stdlib POST — no extra dep. 5s timeout so a hung webhook
    never blocks the audit write path. Errors raise to the caller
    so it can write a `alert.webhook_failed` audit row — losing a
    webhook silently is the worst possible failure mode for an
    on-call alerting channel."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"webhook returned HTTP {resp.status}")


# ---------------------------------------------------------------------------
# Email sending (Aliyun DirectMail)
# ---------------------------------------------------------------------------


_LAST_SEND_KEY = "alert_email"


def _provider_send(to: str, subject: str, body: str) -> None:
    """Dispatch a single target. Channel is auto-detected from the
    raw value (email vs lark vs dingtalk vs generic webhook)."""
    parsed = _parse_target(to)
    if parsed is None:
        return
    channel, payload = parsed
    from ..config import get_settings
    s = get_settings()
    env = (s.app_env or "").lower()
    if env in ("local", "dev", "development", "test") or s.mock_mode:
        log.info("alert_mailer.MOCK channel=%s to=%s subject=%r\n%s",
                  channel, payload[:60], subject, body)
        return
    if channel == "lark":
        _send_lark(payload, subject, body)
        return
    if channel == "dingtalk":
        _send_dingtalk(payload, subject, body)
        return
    if channel == "webhook":
        _send_generic_webhook(payload, subject, body)
        return
    # Email path (Aliyun DirectMail).
    to = payload
    ak = os.getenv("ALIYUN_DM_ACCESS_KEY", s.aliyun_dm_access_key)
    sk = os.getenv("ALIYUN_DM_ACCESS_SECRET", s.aliyun_dm_access_secret)
    sender = os.getenv("ALIYUN_DM_SENDER", s.aliyun_dm_sender)
    sender_name = os.getenv("ALIYUN_DM_SENDER_NAME", s.aliyun_dm_sender_name)
    if not (ak and sk and sender):
        log.warning("alert_mailer: DM not configured, dropping alert to=%s", to)
        return
    try:
        from aliyunsdkcore.client import AcsClient                  # type: ignore
        from aliyunsdkcore.request import CommonRequest             # type: ignore
    except ImportError:
        log.warning("alert_mailer: aliyun sdk missing, dropping alert")
        return
    client = AcsClient(ak, sk, "cn-hangzhou")
    req = CommonRequest()
    req.set_accept_format("json")
    req.set_domain("dm.aliyuncs.com")
    req.set_method("POST")
    req.set_protocol_type("https")
    req.set_version("2015-11-23")
    req.set_action_name("SingleSendMail")
    req.add_query_param("AccountName", sender)
    if sender_name:
        req.add_query_param("FromAlias", sender_name)
    req.add_query_param("AddressType", "1")
    req.add_query_param("ReplyToAddress", "false")
    req.add_query_param("ToAddress", to)
    req.add_query_param("Subject", subject)
    req.add_query_param("TextBody", body)
    try:
        client.do_action_with_exception(req)
    except Exception as e:                                          # noqa: BLE001
        log.warning("alert_mailer.send failed to=%s err=%s", to, e)


# v18 c1 — actions that are useful to audit but useless (or actively
# harmful, by inbox flooding) to email per-event. They still hit
# admin_audit_log; csv_scheduler / weekly digest can summarise them.
# Admin override: set runtime_settings `alert.digest_only.<action>`
# to "false" to opt back into immediate dispatch.
_DEFAULT_DIGEST_ONLY = {
    "usage.satisfied",
}


def _is_digest_only(action: str) -> bool:
    override = runtime_settings.get_str(
        f"alert.digest_only.{action}", "")
    if override:
        return override.strip().lower() in ("1", "true", "yes")
    return action in _DEFAULT_DIGEST_ONLY


def maybe_send_for_audit(action: str, *, admin_id: str,
                            target: Optional[str],
                            payload: Optional[dict],
                            occurred_at: Optional[str] = None) -> int:
    """Resolve recipients, throttle, send. Returns # emails dispatched.

    Cheap to call from every audit write; the cost is two
    runtime_settings reads (cached 30s) and one rate_buckets check.
    """
    if _is_digest_only(action):
        return 0
    rec = recipients_for(action)
    if not rec:
        return 0
    cooldown = max(60, cooldown_sec_for(action))
    # `hit` returns the new count for the current window. >1 means
    # we already sent for this action in this cooldown window.
    n = rate_buckets.hit(_LAST_SEND_KEY, "action", action, cooldown)
    if n > 1:
        log.debug("alert_mailer: throttled action=%s n=%d cooldown=%ds",
                    action, n, cooldown)
        return 0
    occurred_at = occurred_at or datetime.now(timezone.utc).isoformat()
    subject = format_subject(action, target)
    body = format_body(action, admin_id=admin_id, target=target,
                         payload=payload, occurred_at=occurred_at)
    sent = 0
    failures: list[dict] = []
    for to in rec:
        try:
            _provider_send(to, subject, body)
            sent += 1
        except Exception as e:                                      # noqa: BLE001
            log.warning("alert_mailer: send failed to=%s err=%s", to, e)
            # Don't store the literal target (could be PII / webhook
            # token). Hash it so admin can correlate failures across
            # the same recipient over time.
            import hashlib as _h
            digest = _h.sha256(to.encode("utf-8")).hexdigest()[:12]
            channel = (_parse_target(to) or ("unknown", ""))[0]
            failures.append({"channel": channel,
                              "target_hash": digest,
                              "error": str(e)[:200]})
    if sent:
        log.info("alert_mailer: sent action=%s to=%d recipients", action, sent)
    if failures:
        # v17i — surface in admin audit so a silently-broken webhook
        # gets caught at the next dashboard refresh instead of during
        # the next real incident.
        try:
            # Lazy import to avoid cycle (admin_audit imports us).
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            with user_repo._connect() as _con:                      # noqa: SLF001
                _con.execute(
                    "INSERT INTO admin_audit_log (admin_id, action, "
                    "target, payload, occurred_at) VALUES (?,?,?,?,?)",
                    ("system", "alert.webhook_failed", action,
                     _json.dumps({"action": action,
                                    "failures": failures,
                                    "succeeded": sent}),
                     _dt.now(_tz.utc).isoformat()),
                )
                _con.commit()
        except Exception as e:                                      # noqa: BLE001
            log.warning("alert_mailer: failure audit write failed: %s", e)
    return sent


__all__ = ["recipients_for", "cooldown_sec_for", "maybe_send_for_audit",
            "format_subject", "format_body"]
