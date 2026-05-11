"""Device attestation endpoint (P0-1.3).

POST /devices/attest
    body: { key_id: str, attestation_b64: str, challenge: str }
    -> { ok: bool, enforce_mode: bool }

iOS calls this once per install (or after key rotation). Subsequent
analyze requests pass an assertion header instead.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import app_attest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


class AttestIn(BaseModel):
    key_id: str = Field(min_length=4, max_length=128)
    attestation_b64: str = Field(min_length=4)
    challenge: str = Field(min_length=4, max_length=128)


class AttestOut(BaseModel):
    ok: bool
    enforce_mode: bool
    note: Optional[str] = None


@router.post("/attest", response_model=AttestOut)
async def attest(payload: AttestIn) -> AttestOut:
    challenge_bytes = app_attest.fingerprint_challenge(payload.challenge)
    ok = app_attest.register_attestation(
        payload.key_id, payload.attestation_b64, challenge_bytes,
    )
    enforce = app_attest.is_enforcing()
    note = None if enforce else "running in shadow mode (no Apple root CA found)"
    return AttestOut(ok=ok, enforce_mode=enforce, note=note)
