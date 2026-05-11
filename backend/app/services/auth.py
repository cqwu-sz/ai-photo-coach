"""JWT issuance + SIWA verification + current_user dependency
(A0-2 / A0-4 of MULTI_USER_AUTH).

Why we roll our own JWT instead of relying on Apple's identity_token
directly:
- iOS keeps the SIWA token only for first login; we need a long-lived
  refresh story for our own app.
- Server-side checks (revocation, soft-delete, tier flips) need a token
  we issue ourselves so we can invalidate.

Token shape:
    access:  jwt(sub=user_id, type="access",  exp=now+15m, tier=...)
    refresh: jwt(sub=user_id, type="refresh", exp=now+30d, jti=uuid)

The refresh `jti` is allow-listed in `user_repo.refresh_tokens` so we
can revoke a single device without invalidating others (e.g. on logout
or "delete account").
"""
from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from ..config import get_settings
from . import user_repo

log = logging.getLogger(__name__)

_ALG = "HS256"
_EPHEMERAL_SECRET: Optional[str] = None


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def _secret() -> str:
    global _EPHEMERAL_SECRET
    cfg = get_settings().app_jwt_secret.strip()
    if cfg:
        return cfg
    env = os.getenv("APP_JWT_SECRET", "").strip()
    if env:
        return env
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
        log.warning(
            "APP_JWT_SECRET not set — using ephemeral secret. "
            "Tokens will not survive process restarts. Do NOT ship this to prod.",
        )
    return _EPHEMERAL_SECRET


# ---------------------------------------------------------------------------
# Issue / verify
# ---------------------------------------------------------------------------


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def issue_pair(user_id: str, *, tier: str = "free") -> TokenPair:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    access_exp = now + timedelta(seconds=settings.app_jwt_access_ttl_sec)
    refresh_exp = now + timedelta(seconds=settings.app_jwt_refresh_ttl_sec)
    jti = str(uuid.uuid4())
    access = jwt.encode(
        {"sub": user_id, "type": "access", "tier": tier,
         "iat": int(now.timestamp()), "exp": int(access_exp.timestamp())},
        _secret(), algorithm=_ALG,
    )
    refresh = jwt.encode(
        {"sub": user_id, "type": "refresh", "jti": jti,
         "iat": int(now.timestamp()), "exp": int(refresh_exp.timestamp())},
        _secret(), algorithm=_ALG,
    )
    user_repo.remember_refresh(jti, user_id, refresh_exp)
    return TokenPair(
        access_token=access, refresh_token=refresh,
        access_expires_at=access_exp, refresh_expires_at=refresh_exp,
    )


def decode(token: str, *, expected_type: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALG])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(401, {"error": {"code": "token_expired", "message": str(e)}})
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, {"error": {"code": "token_invalid", "message": str(e)}})
    if claims.get("type") != expected_type:
        raise HTTPException(401, {"error": {"code": "token_type_mismatch"}})
    return claims


def rotate_refresh(refresh_token: str) -> TokenPair:
    claims = decode(refresh_token, expected_type="refresh")
    jti = claims.get("jti")
    sub = claims.get("sub")
    if not jti or not sub or not user_repo.is_refresh_valid(jti):
        raise HTTPException(401, {"error": {"code": "refresh_revoked"}})
    user_repo.revoke_refresh(jti)
    user = user_repo.get_user(sub)
    if user is None:
        raise HTTPException(401, {"error": {"code": "user_gone"}})
    return issue_pair(user.id, tier=user.tier)


# ---------------------------------------------------------------------------
# Sign in with Apple verifier
# ---------------------------------------------------------------------------


_JWKS_CACHE: dict[str, Any] = {"fetched_at": 0.0, "keys": {}}


async def _fetch_apple_jwks() -> dict[str, Any]:
    """Cached JWKS fetch. Apple rotates rarely; 6h TTL is fine."""
    now = time.time()
    if _JWKS_CACHE["keys"] and (now - _JWKS_CACHE["fetched_at"]) < 6 * 3600:
        return _JWKS_CACHE["keys"]
    url = get_settings().apple_siwa_jwks_url
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    keys: dict[str, Any] = {}
    for k in data.get("keys", []):
        keys[k["kid"]] = k
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["fetched_at"] = now
    return keys


@dataclass
class SiwaClaims:
    sub: str
    email: Optional[str]
    aud: str
    iss: str


async def verify_siwa_identity_token(identity_token: str) -> SiwaClaims:
    """Verify Apple's identity_token JWT.

    Raises HTTPException(401) on any failure. SIWA bundle id MUST be
    configured (`apple_siwa_bundle_id`) — otherwise we 503 to fail loud
    rather than accept anything in shadow mode.
    """
    settings = get_settings()
    expected_aud = settings.apple_siwa_bundle_id.strip()
    if not expected_aud:
        raise HTTPException(503, {"error": {"code": "siwa_not_configured"}})

    try:
        unverified = jwt.get_unverified_header(identity_token)
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, {"error": {"code": "siwa_token_invalid", "message": str(e)}})

    kid = unverified.get("kid")
    if not kid:
        raise HTTPException(401, {"error": {"code": "siwa_missing_kid"}})

    jwks = await _fetch_apple_jwks()
    jwk = jwks.get(kid)
    if not jwk:
        # Try refreshing once in case Apple rotated.
        _JWKS_CACHE["fetched_at"] = 0.0
        jwks = await _fetch_apple_jwks()
        jwk = jwks.get(kid)
    if not jwk:
        raise HTTPException(401, {"error": {"code": "siwa_unknown_kid", "kid": kid}})

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    try:
        claims = jwt.decode(
            identity_token, public_key,
            algorithms=["RS256"],
            audience=expected_aud,
            issuer="https://appleid.apple.com",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, {"error": {"code": "siwa_token_invalid", "message": str(e)}})

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(401, {"error": {"code": "siwa_missing_sub"}})
    return SiwaClaims(
        sub=str(sub),
        email=claims.get("email"),
        aud=claims.get("aud", expected_aud),
        iss=claims.get("iss", "https://appleid.apple.com"),
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


@dataclass
class CurrentUser:
    id: str
    tier: str
    is_anonymous: bool


def _bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip() or None


async def current_user(
    request: Request,
    x_device_id: Optional[str] = Header(default=None),
) -> CurrentUser:
    """Resolve the authenticated user.

    Lookup order:
      1. Authorization: Bearer <access_jwt>  (the future-proof path)
      2. X-Device-Id   (compat: auto-promote to anonymous user when
         `enable_legacy_device_id_auth` is True)

    When neither works, 401.
    """
    settings = get_settings()
    token = _bearer(request)
    if token:
        claims = decode(token, expected_type="access")
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(401, {"error": {"code": "token_missing_sub"}})
        user = user_repo.get_user(str(sub))
        if user is None:
            raise HTTPException(401, {"error": {"code": "user_gone"}})
        user_repo.touch(user.id)
        try:
            from ..api import metrics as metrics_api
            metrics_api.inc("ai_photo_coach_auth_total", method="bearer")
        except Exception:                                       # noqa: BLE001
            pass
        # Claims-cached tier may lag the DB after a webhook flips it;
        # always trust the DB so a refunded user loses Pro instantly.
        return CurrentUser(id=user.id, tier=user.tier, is_anonymous=user.is_anonymous)

    if settings.enable_legacy_device_id_auth and x_device_id:
        user = user_repo.get_by_device_id(x_device_id)
        if user is None:
            user = user_repo.create_anonymous(device_id=x_device_id)
        else:
            user_repo.touch(user.id)
        try:
            from ..api import metrics as metrics_api
            metrics_api.inc("ai_photo_coach_auth_total", method="device_id_legacy")
        except Exception:                                       # noqa: BLE001
            pass
        return CurrentUser(id=user.id, tier=user.tier, is_anonymous=user.is_anonymous)

    raise HTTPException(401, {"error": {"code": "auth_required",
                                         "message": "Bearer token required"}})


async def optional_user(
    request: Request,
    x_device_id: Optional[str] = Header(default=None),
) -> Optional[CurrentUser]:
    """Same as `current_user` but returns None instead of 401.

    Use on read-only endpoints that should still serve unauthenticated
    callers (e.g. `/healthz`, `/models`)."""
    try:
        return await current_user(request, x_device_id=x_device_id)
    except HTTPException:
        return None


def require_pro(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.tier != "pro":
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            {"error": {"code": "pro_required",
                       "message": "This feature requires AI Photo Coach Pro."}},
        )
    return user
