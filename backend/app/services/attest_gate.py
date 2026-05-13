"""Helper to enforce App Attest assertion on selected endpoints (v17c).

We don't want to litter the route handlers with the same try/except
block; this consolidates the policy in one place. Each call site:

    attest_gate.require(request, payload_for_challenge="<some str>")

Behaviour:
  * If neither config flag enables enforcement → no-op.
  * If headers missing → 403 `attest_required`.
  * If verifier rejects → 403 `attest_invalid`.

Headers expected (set by iOS):
  X-Attest-KeyId       — the App Attest key id (base64-url-safe)
  X-Attest-Assertion   — base64 of CBOR assertion blob
  X-Attest-Challenge   — opaque per-request string the server
                          asked the client to sign. We accept any
                          string and hash it; clients pick a fresh
                          UUID per request.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request

from . import app_attest

log = logging.getLogger(__name__)


def _get_required(request: Request, kind: str) -> bool:
    from ..config import get_settings
    s = get_settings()
    if kind == "otp":
        return bool(getattr(s, "require_app_attest_on_otp", False))
    if kind == "analyze":
        return bool(getattr(s, "require_app_attest_on_analyze", False))
    return False


def require(request: Request, *, kind: str,
            payload_for_challenge: Optional[str] = None) -> None:
    if not _get_required(request, kind):
        return
    key_id = request.headers.get("x-attest-keyid")
    assertion = request.headers.get("x-attest-assertion")
    challenge_raw = request.headers.get("x-attest-challenge") or (payload_for_challenge or "")
    if not key_id or not assertion:
        raise HTTPException(403, {"error": {"code": "attest_required",
                                              "message": "请升级 App 至最新版本。"}})
    challenge_bytes = app_attest.fingerprint_challenge(challenge_raw)
    ok = app_attest.verify_assertion(key_id, assertion, challenge_bytes)
    if not ok:
        log.warning("attest_gate: rejected key_id=%s kind=%s", key_id, kind)
        raise HTTPException(403, {"error": {"code": "attest_invalid",
                                              "message": "设备校验失败。"}})


__all__ = ["require"]
