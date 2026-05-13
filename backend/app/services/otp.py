"""OTP (one-time-password) issuance & verification (PR3 of subscription/auth rework).

Flow::

    POST /auth/otp/request {channel, target}
        → generate 6-digit code, store HMAC, hand to provider for delivery
    POST /auth/otp/verify {channel, target, code}
        → verify against latest unconsumed hash, mark consumed,
          create-or-fetch the user, hand back our JWT pair

Storage:
  - We store ONLY ``HMAC-SHA256(code, OTP_HASH_SECRET)``. The plain
    code never touches the DB.
  - Codes are single-use; ``consumed_at`` is set on the first matching
    verify to prevent replay.
  - 5-minute TTL; max 5 verify attempts per code; throttle 60s between
    requests for the same target; lock target for 15min after 5 wrong
    codes within a 30min window.

Providers:
  - ``AliyunSmsProvider`` — Alibaba Cloud Dysmsapi (国内首选)
  - ``AliyunEmailProvider`` — Alibaba Cloud DirectMail
  - ``MockProvider`` — writes the code to a process-local list so
    tests + local dev can verify without sending real messages.

Provider selection is keyed off ``settings.app_env`` and the presence
of Aliyun credentials. When credentials are missing we fall back to
the mock provider in non-prod, and refuse to start in prod.
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional

from fastapi import HTTPException, status

from ..config import get_settings
from . import blocklist as blocklist_svc
from . import rate_buckets as rl
from . import user_repo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exceptions (mapped to HTTP codes by the API layer)
# ---------------------------------------------------------------------------


class OtpError(HTTPException):
    def __init__(self, code: str, message: str, *, http: int = 400,
                 extra: Optional[dict] = None) -> None:
        body = {"error": {"code": code, "message": message}}
        if extra:
            body["error"].update(extra)
        super().__init__(status_code=http, detail=body)


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


@dataclass
class OtpProvider:
    """Abstract sender. Subclasses MUST override `send`."""
    name: str = "abstract"

    def send(self, *, target: str, code: str, channel: str) -> None:
        raise NotImplementedError


@dataclass
class MockProvider(OtpProvider):
    name: str = "mock"
    sent: list[tuple[str, str, str]] = field(default_factory=list)
    """Process-local list of (channel, target, code) tuples. Tests
    pop from here to know what was 'sent' without touching the network."""

    def send(self, *, target: str, code: str, channel: str) -> None:
        self.sent.append((channel, target, code))
        log.info("otp.mock send channel=%s target=%s code=%s",
                 channel, _redact(target), code)


@dataclass
class AliyunSmsProvider(OtpProvider):
    """Calls Alibaba Cloud Dysmsapi SendSms.

    Config (via env / settings):
      - ``aliyun_sms_access_key`` / ``aliyun_sms_access_secret``
      - ``aliyun_sms_sign_name``  (e.g. "AI Photo Coach")
      - ``aliyun_sms_template_code`` (e.g. "SMS_465xxxxxxx")

    The template should contain ``${code}`` placeholder.
    """
    name: str = "aliyun_sms"

    def send(self, *, target: str, code: str, channel: str) -> None:
        s = get_settings()
        ak = os.getenv("ALIYUN_SMS_ACCESS_KEY", s.aliyun_sms_access_key)
        sk = os.getenv("ALIYUN_SMS_ACCESS_SECRET", s.aliyun_sms_access_secret)
        sign = os.getenv("ALIYUN_SMS_SIGN_NAME", s.aliyun_sms_sign_name)
        tpl = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", s.aliyun_sms_template_code)
        if not (ak and sk and sign and tpl):
            raise OtpError("otp_provider_unconfigured",
                           "Aliyun SMS provider missing credentials.",
                           http=503)
        try:
            from aliyunsdkcore.client import AcsClient                # type: ignore
            from aliyunsdkcore.request import CommonRequest           # type: ignore
        except ImportError as e:
            raise OtpError("otp_provider_unavailable",
                           f"aliyun-python-sdk-core not installed: {e}",
                           http=503)

        client = AcsClient(ak, sk, "cn-hangzhou")
        req = CommonRequest()
        req.set_accept_format("json")
        req.set_domain("dysmsapi.aliyuncs.com")
        req.set_method("POST")
        req.set_protocol_type("https")
        req.set_version("2017-05-25")
        req.set_action_name("SendSms")
        req.add_query_param("PhoneNumbers", target)
        req.add_query_param("SignName", sign)
        req.add_query_param("TemplateCode", tpl)
        req.add_query_param("TemplateParam", '{"code":"%s"}' % code)
        try:
            resp = client.do_action_with_exception(req)
        except Exception as e:                                       # noqa: BLE001
            raise OtpError("otp_send_failed", str(e), http=502)
        log.info("otp.aliyun_sms sent target=%s resp=%s",
                 _redact(target), str(resp)[:200])


@dataclass
class AliyunEmailProvider(OtpProvider):
    """Calls Alibaba Cloud DirectMail SingleSendMail."""
    name: str = "aliyun_email"

    def send(self, *, target: str, code: str, channel: str) -> None:
        s = get_settings()
        ak = os.getenv("ALIYUN_DM_ACCESS_KEY", s.aliyun_dm_access_key)
        sk = os.getenv("ALIYUN_DM_ACCESS_SECRET", s.aliyun_dm_access_secret)
        sender = os.getenv("ALIYUN_DM_SENDER", s.aliyun_dm_sender)
        sender_name = os.getenv("ALIYUN_DM_SENDER_NAME", s.aliyun_dm_sender_name)
        if not (ak and sk and sender):
            raise OtpError("otp_provider_unconfigured",
                           "Aliyun DirectMail provider missing credentials.",
                           http=503)
        try:
            from aliyunsdkcore.client import AcsClient                # type: ignore
            from aliyunsdkcore.request import CommonRequest           # type: ignore
        except ImportError as e:
            raise OtpError("otp_provider_unavailable",
                           f"aliyun-python-sdk-core not installed: {e}",
                           http=503)
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
        req.add_query_param("ToAddress", target)
        req.add_query_param("Subject", "AI Photo Coach 验证码")
        body = (
            f"您的验证码是 {code}，5 分钟内有效。"
            "如非本人操作请忽略本邮件。AI Photo Coach"
        )
        req.add_query_param("TextBody", body)
        try:
            resp = client.do_action_with_exception(req)
        except Exception as e:                                       # noqa: BLE001
            raise OtpError("otp_send_failed", str(e), http=502)
        log.info("otp.aliyun_email sent target=%s resp=%s",
                 _redact(target), str(resp)[:200])


# Singleton-ish provider registry. Tests inject MockProvider via
# `set_providers_for_tests` so we don't need DI plumbing in routes.
_provider_lock = threading.Lock()
_providers: dict[str, OtpProvider] = {}


def _default_providers() -> dict[str, OtpProvider]:
    s = get_settings()
    env = (s.app_env or "").lower()
    if env in ("local", "dev", "development", "test") or s.mock_mode:
        mp = MockProvider()
        return {"sms": mp, "email": mp}
    return {"sms": AliyunSmsProvider(), "email": AliyunEmailProvider()}


def get_provider(channel: str) -> OtpProvider:
    with _provider_lock:
        if not _providers:
            _providers.update(_default_providers())
        provider = _providers.get(channel)
    if provider is None:
        raise OtpError("otp_channel_unsupported",
                       f"Unsupported channel: {channel}")
    return provider


def set_providers_for_tests(sms: OtpProvider, email: OtpProvider) -> None:
    with _provider_lock:
        _providers.clear()
        _providers["sms"] = sms
        _providers["email"] = email


def reset_for_tests() -> None:
    with _provider_lock:
        _providers.clear()


# ---------------------------------------------------------------------------
# Validation + normalisation
# ---------------------------------------------------------------------------


_CN_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_INTL_PHONE_RE = re.compile(r"^\+\d{8,15}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_target(channel: str, target: str) -> str:
    target = (target or "").strip()
    if channel == "sms":
        if _CN_PHONE_RE.match(target):
            return target
        if _INTL_PHONE_RE.match(target):
            return target
        raise OtpError("otp_target_invalid", "手机号格式不正确")
    if channel == "email":
        target = target.lower()
        if _EMAIL_RE.match(target):
            return target
        raise OtpError("otp_target_invalid", "邮箱格式不正确")
    raise OtpError("otp_channel_unsupported", f"未知通道: {channel}")


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _hash_secret() -> bytes:
    s = get_settings()
    secret = (s.otp_hash_secret or s.app_jwt_secret or
              os.getenv("OTP_HASH_SECRET", "") or
              os.getenv("APP_JWT_SECRET", ""))
    if not secret:
        # Last-resort ephemeral so tests don't crash; logged loudly.
        log.warning("otp: no OTP_HASH_SECRET / APP_JWT_SECRET, using ephemeral")
        return secrets.token_bytes(32)
    return secret.encode("utf-8")


def hash_code(code: str) -> str:
    return hmac.new(_hash_secret(), code.encode("utf-8"), sha256).hexdigest()


# ---------------------------------------------------------------------------
# Throttling helpers
# ---------------------------------------------------------------------------


_REQUEST_COOLDOWN_SEC = 60          # one OTP send per target per 60s
_CODE_TTL_SEC = 5 * 60
_MAX_VERIFY_ATTEMPTS = 3            # tightened from 5 — 3 wrong codes burns the OTP
_LOCK_WINDOW_SEC = 30 * 60          # rolling fail-window scope
# v17d — three-tier escalating lock:
#   * 3 fails  → 1h lock
#   * 6 fails  → 24h lock
#   * 12 fails → permanent lock (admin must unlock via blocklist remove)
# Counts reset after `_LOCK_WINDOW_SEC` of inactivity (30 min).
# Rationale: the previous flat 3-fails-then-3h was too punishing for
# legitimate fat-finger users while too lenient on actual brute-force
# attackers (who don't care about a 3h pause).
_MAX_FAILS_PER_WINDOW = 3
_LOCK_TIERS_SEC: tuple[tuple[int, int], ...] = (
    (3, 60 * 60),            # ≥3 fails → 1h
    (6, 24 * 60 * 60),       # ≥6 fails → 24h
    (12, 0),                 # ≥12 fails → permanent (0 sentinel)
)

# Hard ceiling: a single target can request at most this many codes per
# day, regardless of cooldown gaps. Stops "bot fires once per minute
# for 24h to drain SMS budget".
_DAILY_MAX_PER_TARGET = 8

# Hard ceiling per IP per day (anti-farm second layer; the rolling-1h
# distinct-target throttle catches bursts, this catches slow & low).
_DAILY_MAX_PER_IP = 30

# Global per-minute send budget across the whole service. 50/min ≈
# 72k/day which is roughly Aliyun's default SMS budget. Above this
# we shed load to protect the SMS bill.
_GLOBAL_RPM = 50


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().isoformat()


def _check_cooldown(con: sqlite3.Connection, channel: str, target: str) -> None:
    cutoff = (_now_dt() - timedelta(seconds=_REQUEST_COOLDOWN_SEC)).isoformat()
    row = con.execute(
        "SELECT created_at FROM otp_codes WHERE channel = ? AND target = ? "
        "AND created_at > ? ORDER BY id DESC LIMIT 1",
        (channel, target, cutoff),
    ).fetchone()
    if row:
        try:
            recent = datetime.fromisoformat(row[0])
            wait = _REQUEST_COOLDOWN_SEC - int((_now_dt() - recent).total_seconds())
            wait = max(wait, 1)
        except ValueError:
            wait = _REQUEST_COOLDOWN_SEC
        raise OtpError("otp_too_frequent",
                       f"请稍候 {wait} 秒后再获取验证码",
                       http=429, extra={"retry_after": wait})


def _check_lock(con: sqlite3.Connection, target: str) -> None:
    row = con.execute(
        "SELECT locked_until FROM auth_attempts WHERE target = ?",
        (target,),
    ).fetchone()
    if not row or not row[0]:
        return
    try:
        until = datetime.fromisoformat(row[0])
    except ValueError:
        return
    if until > _now_dt():
        secs = int((until - _now_dt()).total_seconds())
        raise OtpError("otp_target_locked",
                       f"验证失败次数过多，请 {secs} 秒后再试",
                       http=429, extra={"retry_after": secs})


def _record_failure(con: sqlite3.Connection, target: str) -> None:
    row = con.execute(
        "SELECT count, window_start FROM auth_attempts WHERE target = ?",
        (target,),
    ).fetchone()
    now_dt = _now_dt()
    if row is None:
        con.execute(
            "INSERT INTO auth_attempts (target, count, window_start) "
            "VALUES (?, 1, ?)",
            (target, now_dt.isoformat()),
        )
        return
    count = int(row[0]) + 1
    try:
        window_start = datetime.fromisoformat(row[1])
    except (ValueError, TypeError):
        window_start = now_dt
    if (now_dt - window_start).total_seconds() > _LOCK_WINDOW_SEC:
        count = 1
        window_start = now_dt
    locked_until = None
    # Walk tiers DESC so the strictest matching threshold wins.
    for threshold, dur_sec in reversed(_LOCK_TIERS_SEC):
        if count >= threshold:
            if dur_sec <= 0:
                # Permanent lock = blocklist add. Use a sentinel
                # "9999-12-31" expiry on auth_attempts so existing
                # cooldown logic also refuses; blocklist gives admin
                # the kill-switch to unlock. Both writes piggy-back
                # on the caller's `con` so SQLite doesn't deadlock on
                # the open transaction.
                locked_until = "9999-12-31T23:59:59+00:00"
                try:
                    import json as _json
                    scope = "phone" if "@" not in target else "email"
                    con.execute(
                        "INSERT OR REPLACE INTO blocklist "
                        "(scope, value, reason, created_by, created_at, "
                        "expires_at, dry_run) VALUES (?,?,?,?,?,?,0)",
                        (scope, target,
                         f"auto: {count} OTP fails",
                         "system:otp_lock",
                         now_dt.isoformat(),
                         None),
                    )
                    con.execute(
                        "INSERT INTO admin_audit_log "
                        "(admin_id, action, target, payload, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        ("system", "otp.permanent_lock",
                         _redact(target),
                         _json.dumps({"fails": count, "scope": scope,
                                       "reason": "auto"}),
                         now_dt.isoformat()),
                    )
                    # Best-effort cache flush so the next request sees
                    # the new blocklist row immediately.
                    try:
                        from . import blocklist as _bl
                        _bl._flush_cache()                          # noqa: SLF001
                    except Exception:                               # noqa: BLE001
                        pass
                except Exception as e:                              # noqa: BLE001
                    log.warning("otp: permanent-lock writes failed: %s", e)
            else:
                locked_until = (now_dt + timedelta(seconds=dur_sec)).isoformat()
            log.warning("otp: locking target=%s until=%s after %d fails (tier %ds)",
                        _redact(target), locked_until, count, dur_sec)
            break
    con.execute(
        "UPDATE auth_attempts SET count = ?, window_start = ?, locked_until = ? "
        "WHERE target = ?",
        (count, window_start.isoformat(), locked_until, target),
    )


def _clear_failures(con: sqlite3.Connection, target: str) -> None:
    con.execute("DELETE FROM auth_attempts WHERE target = ?", (target,))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class IssuedCode:
    channel: str
    target: str
    expires_at: datetime


_IP_WINDOW_SEC = 60 * 60         # rolling 1h window
_IP_MAX_DISTINCT_TARGETS = 8     # ≤8 distinct phones/emails per IP per hour


def _check_ip_throttle(con: sqlite3.Connection, ip: Optional[str],
                       target: str) -> None:
    """Refuse if this client IP has already pinged ≥N *distinct* targets
    in the last hour. Same target replays don't count (those are caught
    by the per-target cooldown). Bypassed if IP is None (proxy issue or
    misconfig — fail open rather than locking everyone out)."""
    if not ip:
        return
    cutoff = (_now_dt() - timedelta(seconds=_IP_WINDOW_SEC)).isoformat()
    # Purge old rows opportunistically so the table doesn't grow forever.
    con.execute("DELETE FROM otp_ip_attempts WHERE created_at < ?", (cutoff,))
    distinct = con.execute(
        "SELECT COUNT(DISTINCT target) FROM otp_ip_attempts "
        "WHERE ip = ? AND created_at >= ? AND target != ?",
        (ip, cutoff, target),
    ).fetchone()[0]
    if int(distinct) >= _IP_MAX_DISTINCT_TARGETS:
        raise OtpError(
            "otp_ip_throttled",
            "当前网络环境短时间内尝试了过多账号，请稍后再试。",
            http=429,
            extra={"retry_after_sec": _IP_WINDOW_SEC},
        )


def _check_blocklist(channel: str, target: str,
                      client_ip: Optional[str]) -> None:
    scope = "phone" if channel == "sms" else "email"
    if blocklist_svc.is_blocked(scope, target):
        raise OtpError("otp_target_blocked",
                        "该号码/邮箱已被封禁，如有疑问请联系客服。",
                        http=403)
    if client_ip and blocklist_svc.is_blocked("ip", client_ip):
        raise OtpError("otp_ip_blocked",
                        "当前 IP 已被封禁。", http=403)


def _check_daily_caps(target: str, client_ip: Optional[str]) -> None:
    """Hard daily ceilings on top of the rolling cooldown.

    Thresholds resolve from runtime_settings → constants → defaults
    so admin can dial them down during incidents without a deploy."""
    from . import runtime_settings as rs
    target_max = rs.get_int("otp.daily_max_per_target", _DAILY_MAX_PER_TARGET)
    ip_max = rs.get_int("otp.daily_max_per_ip", _DAILY_MAX_PER_IP)
    target_count = rl.hit("otp", "target_day", target, 86400)
    if target_count > target_max:
        raise OtpError("otp_daily_target_exhausted",
                        "今日该号码请求验证码次数过多，请明天再试。",
                        http=429,
                        extra={"retry_after_sec": 86400})
    if client_ip:
        ip_count = rl.hit("otp", "ip_day", client_ip, 86400)
        if ip_count > ip_max:
            raise OtpError("otp_daily_ip_exhausted",
                            "今日当前网络环境请求验证码次数过多。",
                            http=429,
                            extra={"retry_after_sec": 86400})


def _check_global_rpm() -> None:
    """Service-wide RPM ceiling — protects the SMS bill from abuse spikes."""
    from . import runtime_settings as rs
    rpm_cap = rs.get_int("otp.global_rpm", _GLOBAL_RPM)
    n = rl.hit("otp", "global_minute", "all", 60)
    if n > rpm_cap:
        raise OtpError("otp_service_busy",
                        "短信服务繁忙，请稍后再试。", http=503,
                        extra={"retry_after_sec": 60})


def request_code(channel: str, target: str,
                 *, client_ip: Optional[str] = None) -> IssuedCode:
    """Generate a fresh OTP, persist its HMAC, and dispatch via provider.

    Defense layers (cheap → expensive):
      1. blocklist (target / IP)        — admin-curated kill switch
      2. account lock                    — 3 wrong codes / 30min → 3h lock
      3. per-target 60s cooldown         — burst control
      4. per-IP 1h distinct-target cap   — anti farm-burst
      5. per-target / per-IP daily cap   — anti slow-drip drain
      6. global RPM ceiling              — protects SMS bill
    """
    target = normalize_target(channel, target)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = _now_dt() + timedelta(seconds=_CODE_TTL_SEC)
    code_hash = hash_code(code)
    _check_blocklist(channel, target, client_ip)
    with user_repo._connect() as con:                               # noqa: SLF001
        _check_lock(con, target)
        _check_cooldown(con, channel, target)
        _check_ip_throttle(con, client_ip, target)
    # Outside the SQLite tx — these don't need to be atomic with insert.
    _check_daily_caps(target, client_ip)
    _check_global_rpm()
    with user_repo._connect() as con:                               # noqa: SLF001
        con.execute(
            "INSERT INTO otp_codes (channel, target, code_hash, expires_at, "
            "attempts, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (channel, target, code_hash, expires.isoformat(), _now_iso()),
        )
        if client_ip:
            con.execute(
                "INSERT OR IGNORE INTO otp_ip_attempts (ip, target, created_at) "
                "VALUES (?, ?, ?)",
                (client_ip, target, _now_iso()),
            )
    # Send AFTER persisting so a network blip doesn't leave a "code on
    # the user's phone but no row in DB" state.
    provider = get_provider(channel)
    provider.send(target=target, code=code, channel=channel)
    return IssuedCode(channel=channel, target=target, expires_at=expires)


@dataclass
class VerifyResult:
    user: user_repo.User
    created: bool


def verify_code(channel: str, target: str, code: str,
                *, device_fingerprint: Optional[str] = None) -> VerifyResult:
    """Validate a submitted code; on success create-or-fetch the user.

    `device_fingerprint` is sha256 of the iOS Keychain device_id and
    is stored on the user row so the free-quota bucket (PR13) can
    anchor on the device, not the account. Missing fp = legacy
    client; we still let them in but the free quota will degrade
    to per-user."""
    target = normalize_target(channel, target)
    code = (code or "").strip()
    if not re.fullmatch(r"\d{4,8}", code):
        raise OtpError("otp_code_invalid", "验证码格式不正确")

    submitted_hash = hash_code(code)

    with user_repo._connect() as con:                               # noqa: SLF001
        _check_lock(con, target)
        row = con.execute(
            "SELECT id, code_hash, expires_at, attempts, consumed_at "
            "FROM otp_codes WHERE channel = ? AND target = ? "
            "ORDER BY id DESC LIMIT 1",
            (channel, target),
        ).fetchone()
        if row is None:
            raise OtpError("otp_code_missing", "请先获取验证码")
        rid, stored_hash, expires_at, attempts, consumed_at = row
        if consumed_at:
            raise OtpError("otp_code_used", "验证码已使用，请重新获取")
        try:
            if datetime.fromisoformat(expires_at) <= _now_dt():
                raise OtpError("otp_code_expired", "验证码已过期，请重新获取")
        except ValueError:
            raise OtpError("otp_code_expired", "验证码已过期，请重新获取")
        if attempts >= _MAX_VERIFY_ATTEMPTS:
            raise OtpError("otp_attempts_exhausted",
                           "尝试次数过多，请重新获取验证码")
        # Constant-time compare so we don't leak hash prefix on timing.
        ok = hmac.compare_digest(stored_hash, submitted_hash)
        if not ok:
            con.execute(
                "UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?",
                (rid,),
            )
            _record_failure(con, target)
            # `_connect` only commits on clean exit; raising here would
            # roll back the attempt counter and the lockout, defeating
            # the throttle. Commit explicitly before raising.
            con.commit()
            raise OtpError("otp_code_mismatch", "验证码错误")
        # Success — mark consumed, clear lockout state, upsert user.
        con.execute(
            "UPDATE otp_codes SET consumed_at = ? WHERE id = ?",
            (_now_iso(), rid),
        )
        _clear_failures(con, target)

    if channel == "sms":
        existing = user_repo.get_by_phone(target)
        if existing is None:
            user = user_repo.create_user(phone=target,
                                          device_fingerprint=device_fingerprint)
            return VerifyResult(user=user, created=True)
        # Backfill fp on subsequent login so a legacy account picks
        # up the device anchor without requiring the user to wipe.
        if device_fingerprint and not existing.device_fingerprint:
            user_repo.set_device_fingerprint(existing.id, device_fingerprint)
        return VerifyResult(user=existing, created=False)
    # email
    existing = user_repo.get_by_email(target)
    if existing is None:
        user = user_repo.create_user(email=target,
                                      device_fingerprint=device_fingerprint)
        return VerifyResult(user=user, created=True)
    if device_fingerprint and not existing.device_fingerprint:
        user_repo.set_device_fingerprint(existing.id, device_fingerprint)
    return VerifyResult(user=existing, created=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact(target: str) -> str:
    if "@" in target:
        name, _, domain = target.partition("@")
        if len(name) <= 2:
            return name[:1] + "*@" + domain
        return name[0] + "***" + name[-1] + "@" + domain
    if len(target) >= 7:
        return target[:3] + "****" + target[-4:]
    return target[:1] + "***"


__all__ = [
    "OtpError", "OtpProvider", "MockProvider",
    "AliyunSmsProvider", "AliyunEmailProvider",
    "request_code", "verify_code",
    "set_providers_for_tests", "reset_for_tests",
    "get_provider", "normalize_target", "hash_code",
]
