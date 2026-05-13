"""Auth + account APIs (A0-3 / A0-6).

Endpoints:
  POST /auth/anonymous   — bootstrap a new anonymous user from device id
  POST /auth/siwa        — verify Apple identity_token, merge or upsert
  POST /auth/refresh     — rotate refresh token
  POST /auth/logout      — revoke current refresh
  GET  /me               — return current user profile
  DELETE /users/me       — soft delete + cascade erase
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import admin_audit
from ..services import auth as auth_svc
from ..services import otp as otp_svc
from ..services import user_repo

log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


class TokenPairOut(BaseModel):
    user_id: str
    is_anonymous: bool
    tier: str
    role: str = "user"
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class AnonymousIn(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=128,
                                      description="Stable Keychain UUID. "
                                                   "When supplied, the user is bound so future calls reuse the same id.")


@router.post("/auth/anonymous", response_model=TokenPairOut)
async def auth_anonymous(payload: AnonymousIn) -> TokenPairOut:
    # v17 — anonymous signup is being phased out in favour of OTP/SIWA.
    # Gated behind `enable_anonymous_auth` so test fixtures still work,
    # but production deploys MUST set this to False so the endpoint
    # returns 410 Gone (and the iOS LoginView is the only entry point).
    if not get_settings().enable_anonymous_auth:
        raise HTTPException(
            status.HTTP_410_GONE,
            {"error": {"code": "anonymous_disabled",
                       "message": "Anonymous signup is no longer supported. "
                                  "Use /auth/otp/* or /auth/siwa."}},
        )
    user = None
    if payload.device_id:
        user = user_repo.get_by_device_id(payload.device_id)
    if user is None:
        user = user_repo.create_anonymous(device_id=payload.device_id)
    pair = auth_svc.issue_pair(user.id, tier=user.tier, role=user.role)
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous,
        tier=user.tier, role=user.role,
        access_token=pair.access_token, refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


class OtpRequestIn(BaseModel):
    channel: str = Field(pattern="^(sms|email)$")
    target: str = Field(min_length=4, max_length=128)


class OtpRequestOut(BaseModel):
    channel: str
    target: str
    expires_at: datetime
    cooldown_sec: int = 60


def _client_ip(request: Request) -> Optional[str]:
    """Best-effort IP. Honours X-Forwarded-For when present (we trust
    the LB at edge); otherwise the socket peer. Used only for soft
    throttling — we deliberately fail open if anything looks weird."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return None


@router.post("/auth/otp/request", response_model=OtpRequestOut)
async def auth_otp_request(payload: OtpRequestIn,
                            request: Request) -> OtpRequestOut:
    from ..services import attest_gate
    attest_gate.require(request, kind="otp",
                          payload_for_challenge=payload.target)
    """v17 — issue an OTP via the configured provider.

    Throttling lives in `otp_svc.request_code`:
      - 60s cooldown per (channel, target)
      - 5 wrong codes within 30min locks the target for 15min
      - v17b: ≤8 distinct targets per IP per hour (anti-farm)
    """
    issued = otp_svc.request_code(payload.channel, payload.target,
                                   client_ip=_client_ip(request))
    return OtpRequestOut(
        channel=issued.channel, target=issued.target,
        expires_at=issued.expires_at,
    )


class OtpVerifyIn(BaseModel):
    channel: str = Field(pattern="^(sms|email)$")
    target: str = Field(min_length=4, max_length=128)
    code: str = Field(min_length=4, max_length=8)


def _device_fingerprint(raw: Optional[str]) -> Optional[str]:
    """sha256(device_id) — never persist the raw value, only the digest.

    Keeps the fp stable across re-installs *if* iOS restored the
    Keychain item; if user wiped the device it'll be a new fp and
    the free-quota bucket resets — that's intentional & fair."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/auth/otp/verify", response_model=TokenPairOut)
async def auth_otp_verify(
    payload: OtpVerifyIn,
    request: Request,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-Id"),
) -> TokenPairOut:
    result = otp_svc.verify_code(
        payload.channel, payload.target, payload.code,
        device_fingerprint=_device_fingerprint(x_device_id),
    )
    user = result.user
    pair = auth_svc.issue_pair(user.id, tier=user.tier, role=user.role)
    # v17e — audit every successful login. Critical for forensics
    # ("when did account X start being abused?") and for spotting
    # admin login from unexpected IPs. Action prefix doubles up:
    # `auth.admin_login_success` lights up dashboards instantly.
    action = ("auth.admin_login_success" if user.role == "admin"
              else "auth.login_success")
    admin_audit.write(
        f"user:{user.id}", action,
        target=user.id,
        payload={"channel": payload.channel,
                  "client_ip": _client_ip(request),
                  "user_agent": request.headers.get("user-agent"),
                  "is_first_login": result.is_new_user
                                      if hasattr(result, "is_new_user") else None},
    )
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous,
        tier=user.tier, role=user.role,
        access_token=pair.access_token, refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


class SiwaIn(BaseModel):
    identity_token: str = Field(min_length=20)
    authorization_code: Optional[str] = None
    device_id: Optional[str] = Field(default=None, max_length=128)
    email_hint: Optional[str] = None


@router.post("/auth/siwa", response_model=TokenPairOut)
async def auth_siwa(payload: SiwaIn, request: Request) -> TokenPairOut:
    claims = await auth_svc.verify_siwa_identity_token(payload.identity_token)
    existing = user_repo.get_by_apple_sub(claims.sub)
    if existing is not None:
        user = existing
    else:
        # Try to merge into the anonymous user that's been writing data
        # under this device_id, so the user doesn't lose their history
        # the moment they sign in.
        merge_target = (
            user_repo.get_by_device_id(payload.device_id) if payload.device_id else None
        )
        if merge_target is not None and merge_target.is_anonymous:
            user = user_repo.upgrade_to_siwa(
                merge_target.id, claims.sub, claims.email or payload.email_hint,
            )
        else:
            user = user_repo.create_anonymous(device_id=payload.device_id)
            user = user_repo.upgrade_to_siwa(
                user.id, claims.sub, claims.email or payload.email_hint,
            )
    if payload.device_id:
        user_repo.bind_device(user.id, payload.device_id)
        # Anchor the free-quota bucket on the device fp so a 2nd SIWA
        # account on the same iPhone shares the same 5-shot budget.
        fp = _device_fingerprint(payload.device_id)
        if fp and not user.device_fingerprint:
            user_repo.set_device_fingerprint(user.id, fp)
            user = user_repo.get_user(user.id) or user
    pair = auth_svc.issue_pair(user.id, tier=user.tier, role=user.role)
    action = ("auth.admin_login_success" if user.role == "admin"
              else "auth.login_success")
    admin_audit.write(
        f"user:{user.id}", action,
        target=user.id,
        payload={"channel": "siwa",
                  "client_ip": _client_ip(request),
                  "user_agent": request.headers.get("user-agent")},
    )
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous,
        tier=user.tier, role=user.role,
        access_token=pair.access_token, refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/auth/refresh", response_model=TokenPairOut)
async def auth_refresh(payload: RefreshIn, request: Request) -> TokenPairOut:
    try:
        pair = auth_svc.rotate_refresh(payload.refresh_token)
    except HTTPException as e:
        # v17g — refresh failures = stolen-token probing or replay attempts.
        # Log with IP/UA so admin can spot a single IP burning many tokens.
        admin_audit.write(
            "system", "auth.refresh_failed", target=None,
            payload={"client_ip": _client_ip(request),
                      "user_agent": request.headers.get("user-agent"),
                      "code": (e.detail or {}).get("error", {}).get("code")
                                if isinstance(e.detail, dict) else None},
        )
        raise
    user = user_repo.get_user(auth_svc.decode(pair.access_token, expected_type="access")["sub"])
    if user is None:
        raise HTTPException(401, {"error": {"code": "user_gone"}})
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous,
        tier=user.tier, role=user.role,
        access_token=pair.access_token, refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


class LogoutIn(BaseModel):
    refresh_token: str


@router.post("/auth/logout")
async def auth_logout(payload: LogoutIn, request: Request) -> dict:
    user_id: Optional[str] = None
    try:
        claims = auth_svc.decode(payload.refresh_token, expected_type="refresh")
        jti = claims.get("jti")
        user_id = claims.get("sub")
        if jti:
            user_repo.revoke_refresh(jti)
    except HTTPException:
        # Idempotent: revoking an invalid/expired token is still success.
        pass
    if user_id:
        admin_audit.write(
            f"user:{user_id}", "auth.logout", target=user_id,
            payload={"client_ip": _client_ip(request)},
        )
    return {"ok": True}


class MeOut(BaseModel):
    user_id: str
    is_anonymous: bool
    tier: str
    role: str = "user"
    apple_sub: Optional[str]
    email: Optional[str]
    phone: Optional[str] = None


@router.get("/me", response_model=MeOut)
async def get_me(user: auth_svc.CurrentUser = Depends(auth_svc.current_user)) -> MeOut:
    full = user_repo.get_user(user.id)
    if full is None:
        raise HTTPException(401, {"error": {"code": "user_gone"}})
    return MeOut(
        user_id=full.id, is_anonymous=full.is_anonymous,
        tier=full.tier, role=full.role,
        apple_sub=full.apple_sub, email=full.email, phone=full.phone,
    )


@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    request: Request,
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> None:
    """Apple 5.1.1(v) — must wipe the account when the user asks."""
    user_repo.soft_delete(user.id)
    log.info("user soft-deleted (cascade) id=%s", user.id)
    admin_audit.write(
        f"user:{user.id}", "user.soft_delete", target=user.id,
        payload={"client_ip": _client_ip(request),
                  "user_agent": request.headers.get("user-agent")},
    )
