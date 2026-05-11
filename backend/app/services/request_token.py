"""Analyze-request HMAC token (P0-1.2).

Issued by /analyze in the response, replayed by /feedback to prove the
client actually went through the analyze pipeline. Stops trivial
spam-the-feedback-endpoint attacks against the UGC table.

Token = base64( nonce(8) + ts_be(8) + hmac_sha256(secret, payload || nonce || ts)[:16] )

- TTL 30 min by default (override via ``request_token_ttl_sec``)
- Secret loaded from settings (``request_token_secret``); auto-generated
  ephemeral when blank — fine for local dev, MUST be set in prod env.
- ``payload`` is a stable string of (device_id || scene_mode); binds the
  token to one device + intent so a token issued for portrait can't be
  replayed against scenery from another device.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import struct
import time
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_TTL = 30 * 60

# Lazy ephemeral secret; only used when settings.request_token_secret
# is blank (local dev). Production MUST set the env var.
_EPHEMERAL_SECRET: Optional[bytes] = None


def _resolve_secret(secret: Optional[str]) -> bytes:
    global _EPHEMERAL_SECRET
    if secret:
        return secret.encode("utf-8")
    env = os.getenv("REQUEST_TOKEN_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        log.warning(
            "REQUEST_TOKEN_SECRET not set — using ephemeral secret. "
            "Tokens will not survive process restarts.",
        )
    return _EPHEMERAL_SECRET


def issue(payload: str, *, secret: Optional[str] = None) -> str:
    """Issue a fresh token bound to ``payload``."""
    key = _resolve_secret(secret)
    nonce = secrets.token_bytes(8)
    ts = int(time.time())
    ts_bytes = struct.pack(">Q", ts)
    msg = payload.encode("utf-8") + nonce + ts_bytes
    sig = hmac.new(key, msg, hashlib.sha256).digest()[:16]
    raw = nonce + ts_bytes + sig
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify(token: str, payload: str, *,
           secret: Optional[str] = None,
           ttl_sec: int = _DEFAULT_TTL) -> bool:
    """Constant-time verify. Returns False on any decode/HMAC/expiry failure."""
    if not token or not payload:
        return False
    try:
        pad = "=" * ((4 - len(token) % 4) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
    except Exception:                                          # noqa: BLE001
        return False
    if len(raw) != 8 + 8 + 16:
        return False
    nonce, ts_bytes, sig = raw[:8], raw[8:16], raw[16:32]
    ts = struct.unpack(">Q", ts_bytes)[0]
    now = int(time.time())
    if now - ts > ttl_sec or now + 60 < ts:
        return False
    key = _resolve_secret(secret)
    msg = payload.encode("utf-8") + nonce + ts_bytes
    expected = hmac.new(key, msg, hashlib.sha256).digest()[:16]
    return hmac.compare_digest(expected, sig)


def payload_for(device_id: Optional[str], scene_mode: Optional[str]) -> str:
    """Stable token-binding string."""
    return f"{device_id or '_'}|{scene_mode or '_'}"
