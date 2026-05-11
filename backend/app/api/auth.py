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

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..services import auth as auth_svc
from ..services import user_repo

log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


class TokenPairOut(BaseModel):
    user_id: str
    is_anonymous: bool
    tier: str
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
    user = None
    if payload.device_id:
        user = user_repo.get_by_device_id(payload.device_id)
    if user is None:
        user = user_repo.create_anonymous(device_id=payload.device_id)
    pair = auth_svc.issue_pair(user.id, tier=user.tier)
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous, tier=user.tier,
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
async def auth_siwa(payload: SiwaIn) -> TokenPairOut:
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
    pair = auth_svc.issue_pair(user.id, tier=user.tier)
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous, tier=user.tier,
        access_token=pair.access_token, refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/auth/refresh", response_model=TokenPairOut)
async def auth_refresh(payload: RefreshIn) -> TokenPairOut:
    pair = auth_svc.rotate_refresh(payload.refresh_token)
    user = user_repo.get_user(auth_svc.decode(pair.access_token, expected_type="access")["sub"])
    if user is None:
        raise HTTPException(401, {"error": {"code": "user_gone"}})
    return TokenPairOut(
        user_id=user.id, is_anonymous=user.is_anonymous, tier=user.tier,
        access_token=pair.access_token, refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


class LogoutIn(BaseModel):
    refresh_token: str


@router.post("/auth/logout")
async def auth_logout(payload: LogoutIn) -> dict:
    try:
        claims = auth_svc.decode(payload.refresh_token, expected_type="refresh")
        jti = claims.get("jti")
        if jti:
            user_repo.revoke_refresh(jti)
    except HTTPException:
        # Idempotent: revoking an invalid/expired token is still success.
        pass
    return {"ok": True}


class MeOut(BaseModel):
    user_id: str
    is_anonymous: bool
    tier: str
    apple_sub: Optional[str]
    email: Optional[str]


@router.get("/me", response_model=MeOut)
async def get_me(user: auth_svc.CurrentUser = Depends(auth_svc.current_user)) -> MeOut:
    full = user_repo.get_user(user.id)
    if full is None:
        raise HTTPException(401, {"error": {"code": "user_gone"}})
    return MeOut(
        user_id=full.id, is_anonymous=full.is_anonymous, tier=full.tier,
        apple_sub=full.apple_sub, email=full.email,
    )


@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user: auth_svc.CurrentUser = Depends(auth_svc.current_user)) -> None:
    """Apple 5.1.1(v) — must wipe the account when the user asks."""
    user_repo.soft_delete(user.id)
    log.info("user soft-deleted (cascade) id=%s", user.id)
