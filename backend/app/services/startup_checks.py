"""Startup-time configuration sanity checks (P0-must of Phase 0 deploy).

Called from `main.lifespan`. Behaviour:
  - In every environment we WARN about anything that's missing.
  - When `settings.enforce_required_secrets` is True (prod) we
    additionally raise RuntimeError so the process refuses to start
    rather than booting a half-configured backend.

Goals: stop the ten most common "we forgot to set X in env" outages,
which historically all routed through the same on-call ping.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..config import Settings
from .app_attest import APPLE_APP_ATTEST_ROOT_CA_PATH
from .iap_apple import APPLE_ROOT_CA_PATH

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fatal: bool   # if True and enforce_required_secrets, refuses startup


def _check_secret(name: str, value: str, *, min_len: int = 16,
                   fatal: bool) -> CheckResult:
    if not value or not value.strip():
        return CheckResult(name, False, f"{name} not set", fatal)
    if len(value) < min_len:
        return CheckResult(name, False,
                            f"{name} too short ({len(value)} < {min_len})",
                            fatal)
    return CheckResult(name, True, "ok", fatal)


def _check_file(name: str, path: Path, *, fatal: bool) -> CheckResult:
    return CheckResult(
        name, path.exists(),
        f"{name}: {path}" if path.exists() else f"missing file {path}",
        fatal,
    )


def collect(settings: Settings) -> list[CheckResult]:
    checks: list[CheckResult] = []
    is_prod = (settings.app_env or "").lower() in ("prod", "production")

    # JWT / token secrets — fatal in prod.
    checks.append(_check_secret("APP_JWT_SECRET", settings.app_jwt_secret,
                                  min_len=32, fatal=is_prod))
    checks.append(_check_secret("REQUEST_TOKEN_SECRET",
                                  settings.request_token_secret,
                                  min_len=32, fatal=is_prod))

    # CORS — must be explicit in prod (default localhost is wrong).
    cors = (settings.cors_allow_origins or "").strip()
    has_local = any(x in cors for x in ("localhost", "127.0.0.1"))
    if is_prod and (not cors or has_local):
        checks.append(CheckResult(
            "CORS_ALLOW_ORIGINS", False,
            "must be set to your prod domain(s) in production",
            True,
        ))
    else:
        checks.append(CheckResult("CORS_ALLOW_ORIGINS", True,
                                    cors or "(dev defaults)", False))

    # SIWA — required if you offer SIWA at all.
    if settings.apple_siwa_bundle_id:
        checks.append(CheckResult("APPLE_SIWA_BUNDLE_ID", True,
                                    settings.apple_siwa_bundle_id, False))
    else:
        checks.append(CheckResult(
            "APPLE_SIWA_BUNDLE_ID", False,
            "blank → /auth/siwa returns 503 (set when SIWA goes live)",
            False,
        ))

    # IAP — required if you sell anything.
    if settings.apple_iap_bundle_id:
        checks.append(CheckResult("APPLE_IAP_BUNDLE_ID", True,
                                    settings.apple_iap_bundle_id, False))
    else:
        checks.append(CheckResult(
            "APPLE_IAP_BUNDLE_ID", False,
            "blank → JWS bundleId check is skipped (set before launch)",
            is_prod,
        ))

    # Apple Root CAs — needed for full enforce mode.
    checks.append(_check_file("apple_root_ca_g3.pem",
                                APPLE_ROOT_CA_PATH, fatal=False))
    checks.append(_check_file("apple_app_attest_root_ca.pem",
                                APPLE_APP_ATTEST_ROOT_CA_PATH, fatal=False))

    # Legacy device-id auth — fine in dev, dangerous in prod.
    if is_prod and settings.enable_legacy_device_id_auth:
        checks.append(CheckResult(
            "ENABLE_LEGACY_DEVICE_ID_AUTH", False,
            "should be False once iOS v1.1 with AuthManager is rolled out — "
            "leaving it True keeps a JWT-bypass path open",
            False,
        ))

    return checks


def run_and_report(settings: Settings) -> None:
    checks = collect(settings)
    bad = [c for c in checks if not c.ok]
    fatal = [c for c in bad if c.fatal and settings.enforce_required_secrets]
    for c in checks:
        level = logging.INFO if c.ok else (logging.ERROR if c.fatal else logging.WARNING)
        log.log(level, "startup_check: %s — %s", c.name, c.detail)
    if fatal:
        names = ", ".join(c.name for c in fatal)
        raise RuntimeError(
            f"refusing to start: {len(fatal)} required setting(s) missing "
            f"or invalid: {names}. Set them in env or flip "
            "ENFORCE_REQUIRED_SECRETS=false to bypass (NOT for prod)."
        )
