"""iOS App Attest verifier stub (P0-1.3).

Apple's App Attest gives every iPhone a unique, hardware-backed key. The
flow:

1. iOS calls ``DCAppAttestService.attestKey(...)`` once on first launch
   and POSTs the attestation object + key id to ``/devices/attest``.
2. Backend verifies the attestation chain rooted at Apple's CA, stores
   ``(key_id, public_key)`` in ``data/attested_devices.db``.
3. Every subsequent /analyze request includes a fresh assertion + the
   key_id; backend looks up the public key, verifies signature over
   ``(client_data || nonce)``, ratchets the counter, and accepts.

Full verification needs ``cryptography`` + Apple's root CA (DER) on
disk. Until that lands the verifier runs in **shadow mode**: it logs
whether the client supplied a key_id but doesn't reject anything.
Switch to enforcing once you've shipped the iOS side and confirmed >95%
of analyze requests carry a key.

This module is intentionally dependency-light — it imports
``cryptography`` only when full verification is requested.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "attested_devices.db"

APPLE_APP_ATTEST_ROOT_CA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "apple_app_attest_root_ca.pem"
)
"""TODO: download from
https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem
and commit. Verifier falls back to shadow-mode if missing."""


@dataclass
class AttestRecord:
    key_id: str
    public_key_der: bytes
    counter: int
    created_at: datetime
    last_seen_at: datetime


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS devices ("
            "key_id TEXT PRIMARY KEY, "
            "public_key_der BLOB NOT NULL, "
            "counter INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL, "
            "last_seen_at TEXT NOT NULL)"
        )
        yield con
        con.commit()
    finally:
        con.close()


def is_enforcing() -> bool:
    """Enforce mode requires the Apple root CA on disk."""
    return APPLE_APP_ATTEST_ROOT_CA_PATH.exists()


def _bundle_id() -> str:
    """Resolve the expected bundle id (used as RP id hash)."""
    from ..config import get_settings
    s = get_settings()
    return (s.apple_iap_bundle_id or s.apple_siwa_bundle_id or "").strip()


def _team_id() -> str:
    from ..config import get_settings
    return (get_settings().apple_siwa_team_id or "").strip()


def register_attestation(key_id: str, attestation_b64: str,
                          challenge: bytes) -> bool:
    """Verify Apple's attestation object and persist the device key.

    Returns True iff verified. In shadow mode (no root CA available)
    we accept any attestation, log a warning, and store an opaque
    placeholder so subsequent /analyze calls can still ratchet.

    Reference: https://developer.apple.com/documentation/devicecheck/validating_apps_that_connect_to_your_server
    """
    if not is_enforcing():
        log.warning("app_attest shadow-mode: accepting key_id %s without verification", key_id)
        _persist(key_id, b"<shadow>", counter=0)
        return True
    try:
        import base64 as _b64
        try:
            import cbor2  # type: ignore
        except Exception:                                            # noqa: BLE001
            log.error("app_attest enforce: cbor2 not installed; refusing")
            return False
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding

        attestation = _b64.b64decode(attestation_b64)
        obj = cbor2.loads(attestation)
        fmt = obj.get("fmt")
        if fmt != "apple-appattest":
            log.warning("app_attest fmt mismatch: %s", fmt)
            return False
        att_stmt = obj.get("attStmt") or {}
        auth_data = obj.get("authData") or b""
        x5c = att_stmt.get("x5c") or []
        if not x5c or not auth_data:
            return False

        # 1. Verify x5c chain up to Apple's root.
        certs = [x509.load_der_x509_certificate(c) for c in x5c]
        with open(APPLE_APP_ATTEST_ROOT_CA_PATH, "rb") as f:
            root = x509.load_pem_x509_certificate(f.read())
        chain = certs + [root]
        for child, parent in zip(chain[:-1], chain[1:]):
            pub = parent.public_key()
            try:
                if isinstance(pub, ec.EllipticCurvePublicKey):
                    pub.verify(child.signature, child.tbs_certificate_bytes,
                                ec.ECDSA(child.signature_hash_algorithm))
                else:
                    pub.verify(child.signature, child.tbs_certificate_bytes,
                                padding.PKCS1v15(), child.signature_hash_algorithm)
            except Exception as e:                                   # noqa: BLE001
                log.warning("app_attest chain verify failed: %s", e)
                return False

        # 2. Verify nonce: sha256(authData || clientDataHash) appears in
        #    leaf cert's 1.2.840.113635.100.8.2 extension.
        client_data_hash = hashes.Hash(hashes.SHA256())
        client_data_hash.update(challenge)
        cdh = client_data_hash.finalize()
        nonce_h = hashes.Hash(hashes.SHA256())
        nonce_h.update(auth_data + cdh)
        expected_nonce = nonce_h.finalize()
        leaf = certs[0]
        try:
            ext = leaf.extensions.get_extension_for_oid(
                x509.ObjectIdentifier("1.2.840.113635.100.8.2")
            )
            # Apple wraps the nonce in an ASN.1 sequence; the last 32 bytes
            # are the SHA-256.
            ext_bytes = ext.value.value if hasattr(ext.value, "value") else bytes(ext.value)
            if expected_nonce not in ext_bytes:
                log.warning("app_attest nonce mismatch")
                return False
        except x509.ExtensionNotFound:
            log.warning("app_attest leaf missing nonce extension")
            return False

        # 3. RP ID hash = sha256(team_id + "." + bundle_id) — first 32 bytes
        #    of authData.
        bundle = _bundle_id()
        team = _team_id()
        if bundle and team:
            rp_h = hashes.Hash(hashes.SHA256())
            rp_h.update(f"{team}.{bundle}".encode("utf-8"))
            rp_hash = rp_h.finalize()
            if auth_data[:32] != rp_hash:
                log.warning("app_attest rp id hash mismatch")
                return False

        # 4. Extract attested credential data → public key (DER), persist.
        from cryptography.hazmat.primitives import serialization
        leaf_pub = leaf.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # Counter is bytes [33:37] big-endian.
        counter = int.from_bytes(auth_data[33:37], "big") if len(auth_data) >= 37 else 0
        _persist(key_id, leaf_pub, counter=counter)
        return True
    except Exception as e:                                       # noqa: BLE001
        log.warning("app_attest verification failed: %s", e)
        return False


def verify_assertion(key_id: str, assertion_b64: str,
                      client_data: bytes) -> bool:
    """Verify a per-request assertion. Shadow mode accepts anything."""
    rec = _lookup(key_id)
    if not rec:
        if not is_enforcing():
            log.info("app_attest shadow: unknown key_id %s, allowing", key_id)
            return True
        return False
    if not is_enforcing():
        _ratchet(key_id, rec.counter + 1)
        return True
    try:
        import base64 as _b64
        try:
            import cbor2  # type: ignore
        except Exception:                                            # noqa: BLE001
            log.error("app_attest enforce: cbor2 missing for assertion")
            return False
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        obj = cbor2.loads(_b64.b64decode(assertion_b64))
        sig = obj.get("signature")
        auth_data = obj.get("authenticatorData") or b""
        if not sig or not auth_data:
            return False

        cdh = hashes.Hash(hashes.SHA256())
        cdh.update(client_data)
        client_hash = cdh.finalize()
        signed = auth_data + client_hash

        public_key = serialization.load_der_public_key(rec.public_key_der)
        try:
            public_key.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
        except Exception as e:                                       # noqa: BLE001
            log.info("app_attest assertion sig verify failed: %s", e)
            return False

        new_counter = int.from_bytes(auth_data[33:37], "big") if len(auth_data) >= 37 else 0
        if new_counter <= rec.counter:
            log.warning("app_attest counter not monotonic: old=%d new=%d",
                        rec.counter, new_counter)
            return False
        _ratchet(key_id, new_counter)
        return True
    except Exception as e:                                       # noqa: BLE001
        log.warning("app_attest assertion verify failed: %s", e)
        return False


def _persist(key_id: str, pubkey_der: bytes, counter: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO devices "
                "(key_id, public_key_der, counter, created_at, last_seen_at) "
                "VALUES (?, ?, ?, COALESCE((SELECT created_at FROM devices "
                "WHERE key_id = ?), ?), ?)",
                (key_id, pubkey_der, counter, key_id, now, now),
            )
    except sqlite3.DatabaseError as e:
        log.info("app_attest persist failed: %s", e)


def _lookup(key_id: str) -> Optional[AttestRecord]:
    try:
        with _connect() as con:
            row = con.execute(
                "SELECT key_id, public_key_der, counter, created_at, last_seen_at "
                "FROM devices WHERE key_id = ?",
                (key_id,),
            ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if not row:
        return None
    return AttestRecord(
        key_id=row[0], public_key_der=row[1], counter=int(row[2] or 0),
        created_at=datetime.fromisoformat(row[3]),
        last_seen_at=datetime.fromisoformat(row[4]),
    )


def _ratchet(key_id: str, new_counter: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as con:
            con.execute(
                "UPDATE devices SET counter = ?, last_seen_at = ? WHERE key_id = ?",
                (new_counter, now, key_id),
            )
    except sqlite3.DatabaseError:
        pass


def fingerprint_challenge(payload: str) -> bytes:
    """Build the per-request challenge bytes from a stable payload."""
    return hashlib.sha256(payload.encode("utf-8")).digest()
