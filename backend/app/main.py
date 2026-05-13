"""FastAPI entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    admin as admin_api,
    admin_insights as admin_insights_api,
    analyze,
    auth as auth_api,
    avatars,
    data_export as data_export_api,
    dev,
    endpoint_config_api,
    devices as devices_api,
    feedback as feedback_api,
    iap as iap_api,
    metrics as metrics_api,
    models as models_api,
    panorama,
    pose_library,
    recon3d as recon3d_api,
    style_feasibility as style_feasibility_api,
    sun as sun_api,
    usage as usage_api,
)
from .config import get_settings
from .logging_setup import setup_logging
from .services import recon3d as recon3d_service
from .services import admin_seed, startup_checks, usage_quota, user_repo

settings = get_settings()
setup_logging(settings.log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    log.info(
        "Starting AI Photo Coach backend",
        extra={
            "app_env": settings.app_env,
            "mock_mode": settings.mock_mode,
            "model_fast": settings.gemini_model_fast,
            "cors_allow_origins": _resolve_cors_origins(),
            "request_token_secret_set": bool(settings.request_token_secret),
        },
    )
    # Phase 0 must — fail loud when prod env is misconfigured.
    startup_checks.run_and_report(settings)

    # v17 — run schema migration once at startup so request paths
    # don't pay the PRAGMA / IF NOT EXISTS cost on every connection.
    # `_ensure_schema_v2` is still idempotent; this just warms the file.
    try:
        user_repo.get_user("__schema_warmup__")
    except Exception as e:                                       # noqa: BLE001
        log.warning("schema warmup failed: %s", e)

    # Bootstrap admin accounts from env (idempotent, never demotes).
    try:
        admin_seed.ensure_admins(settings.admin_bootstrap)
    except Exception as e:                                       # noqa: BLE001
        log.warning("admin_seed.ensure_admins failed: %s", e)

    # v17d — opportunistic GC for expired blocklist & rate_buckets rows.
    # Both tables grow forever otherwise (rate_buckets writes one row
    # per (bucket, minute)). Runs once per startup; in steady-state
    # this is enough because pods restart on every deploy.
    try:
        from .services import (audit_retention as _ar,
                                  blocklist as _bl,
                                  rate_buckets as _rb)
        _bl.gc_expired(grace_days=30)
        _rb.gc(older_than_sec=24 * 3600)
        _ar.gc()
    except Exception as e:                                       # noqa: BLE001
        log.warning("v17d/g gc failed: %s", e)

    cleanup_task = asyncio.create_task(recon3d_service.cleanup_loop())
    anon_task = asyncio.create_task(_anonymous_account_sweeper())
    quota_task = asyncio.create_task(_usage_quota_sweeper())
    # v17i — weekly insights CSV emailer. Cheap polling loop; gated
    # behind `insights.weekly_csv.enabled` runtime setting so it
    # stays a no-op until admin explicitly opts in.
    from .services import csv_scheduler, trend_anomaly
    csv_task = asyncio.create_task(csv_scheduler.loop())
    # v17i — z-score keyword spike detector. Runs hourly. Off-by-runtime
    # via `trend.enabled` setting.
    trend_task = asyncio.create_task(trend_anomaly.loop())
    try:
        yield
    finally:
        for t in (cleanup_task, anon_task, quota_task, csv_task, trend_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def _usage_quota_sweeper() -> None:
    """v17 — every 60s, roll back reservations whose 5-min TTL has
    elapsed without a commit/rollback. Catches worker crashes between
    `reserve()` and the result handler."""
    while True:
        try:
            await asyncio.sleep(60)
            usage_quota.sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception as e:                                       # noqa: BLE001
            log.info("usage_quota sweeper tick failed: %s", e)


async def _anonymous_account_sweeper() -> None:
    """A1-4 — periodically purge anonymous accounts that haven't been
    touched in `anonymous_account_ttl_days` days. Cheap: one query
    per day."""
    if settings.anonymous_account_ttl_days <= 0:
        return
    while True:
        try:
            await asyncio.sleep(24 * 3600)
            removed = user_repo.hard_delete_old(
                older_than_hours=24,    # soft-deleted users, real wipe
            )
            inactive = user_repo.purge_inactive_anonymous(
                older_than_days=settings.anonymous_account_ttl_days,
            )
            if removed or inactive:
                log.info(
                    "anon sweeper: hard_deleted=%d inactive_purged=%d",
                    removed, inactive,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:    # noqa: BLE001
            log.info("anon sweeper tick failed: %s", e)


def _resolve_cors_origins() -> list[str]:
    """Resolve the CORS allow-list. Empty setting → restrictive default."""
    raw = (settings.cors_allow_origins or "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "capacitor://localhost",
        "ionic://localhost",
    ]


def _is_prod() -> bool:
    return (settings.app_env or "").lower() in ("production", "prod")


# v17 / PR11 — strip docs from prod so the openapi schema isn't a
# free recon for attackers looking for new admin endpoints.
_docs_kwargs: dict = {}
if _is_prod() and settings.disable_web_routes_in_prod:
    _docs_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(
    title="AI Photo Coach API",
    version="0.1.0",
    description="Backend for the iOS AI Photo Coach app.",
    lifespan=_lifespan,
    **_docs_kwargs,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS", "PUT", "PATCH"],
    allow_headers=["*"],
)


# v17 / PR11 — IP allowlist for /admin/*. RBAC catches stolen
# tokens but a leaked admin JWT is still useful from anywhere on
# the internet; pin it to known networks. Empty allowlist = no IP gate.
_ADMIN_ALLOWLIST = [c.strip() for c in (settings.admin_ip_allowlist or "").split(",") if c.strip()]


def _request_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else ""


# Tunables — kept here so they're easy to spot during incident response.
# `/healthz` and `/api/config/endpoint` are exempt (they MUST stay
# reachable even under attack so clients can self-heal).
_GLOBAL_IP_RPM = 120              # per-IP requests per minute
_GLOBAL_IP_RPH = 1500             # per-IP requests per hour
_AUTH_PATH_RPM = 20               # tighter cap on /auth/* (login attempts)
_RATE_LIMIT_EXEMPT_PREFIXES = ("/healthz", "/api/config/endpoint",
                                 "/apple/asn", "/static/")


@app.middleware("http")
async def _global_security_gate(request, call_next):
    """v17c — three-layer gate that runs on EVERY request:
      1. IP blocklist  (admin-curated, cached 30s)
      2. Per-IP rate limits (RPM/RPH; tighter for /auth/*)
      3. Admin-IP allowlist (legacy, retained)

    Exempt paths: healthz + endpoint poll + Apple webhook (these
    must keep working even when the rest is throttled, otherwise
    clients can't self-heal). Static assets are exempt because
    they're typically served by a CDN anyway.
    """
    from .services import blocklist as blocklist_svc
    from .services import rate_buckets as rl
    from .services import runtime_settings as rs

    path = request.url.path or ""
    ip = _request_ip(request)

    # 1) Hard blocklist — earliest possible exit.
    if ip and blocklist_svc.is_blocked("ip", ip):
        return JSONResponse(
            {"error": {"code": "ip_blocked",
                        "message": "您的网络已被封禁，如有疑问请联系客服。"}},
            status_code=403,
        )

    # 2) Per-IP rate limits, but only on hot user-facing paths.
    if ip and not any(path.startswith(p) for p in _RATE_LIMIT_EXEMPT_PREFIXES):
        try:
            rpm = rl.hit("http", "ip_minute", ip, 60)
            rph = rl.hit("http", "ip_hour", ip, 3600)
        except Exception:
            rpm = rph = 0  # Never let the limiter itself drop traffic.
        rpm_cap = rs.get_int("http.ip_rpm", _GLOBAL_IP_RPM)
        rph_cap = rs.get_int("http.ip_rph", _GLOBAL_IP_RPH)
        if rpm > rpm_cap or rph > rph_cap:
            return JSONResponse(
                {"error": {"code": "rate_limited",
                            "message": "请求过于频繁，请稍后再试。",
                            "retry_after_sec": 60}},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        if path.startswith("/auth/"):
            auth_rpm = rl.hit("http", "ip_auth_minute", ip, 60)
            auth_cap = rs.get_int("http.auth_rpm", _AUTH_PATH_RPM)
            if auth_rpm > auth_cap:
                return JSONResponse(
                    {"error": {"code": "auth_rate_limited",
                                "message": "登录请求过于频繁，请稍后再试。",
                                "retry_after_sec": 60}},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )

    # 3) Admin-only IP allowlist (unchanged from v17).
    if path.startswith("/admin/") and _ADMIN_ALLOWLIST:
        if not _ip_allowed(ip, _ADMIN_ALLOWLIST):
            return JSONResponse(
                {"error": {"code": "admin_ip_denied",
                            "message": "Administrator endpoints are restricted to allowlisted networks."}},
                status_code=403,
            )
    return await call_next(request)


def _ip_allowed(ip: str, allowlist: list[str]) -> bool:
    """Cheap CIDR membership check. Tolerates plain IPs (treated as /32)."""
    try:
        from ipaddress import ip_address, ip_network
        addr = ip_address(ip)
    except ValueError:
        return False
    for rule in allowlist:
        try:
            net = ip_network(rule, strict=False) if "/" in rule else ip_network(rule + "/32")
            if addr in net:
                return True
        except ValueError:
            continue
    return False

# Optional Datadog APM. We only enable it when ddtrace is installed AND
# settings.enable_ddtrace is True so dev environments stay clean.
if settings.enable_ddtrace:
    try:
        from ddtrace import patch_all                          # type: ignore
        patch_all()
        log.info("ddtrace patched (Datadog APM enabled)")
    except Exception as _e:                                    # noqa: BLE001
        log.info("ddtrace not enabled: %s", _e)


@app.get("/healthz")
def healthz() -> JSONResponse:
    s = get_settings()
    return JSONResponse({
        "status": "ok",
        "mock_mode": s.mock_mode,
        "app_env": s.app_env,
        "privacy_policy_url": s.privacy_policy_url or "/web/privacy.html",
        "eula_url": s.eula_url or "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/",
    })


@app.get("/health/catalog")
def health_catalog() -> JSONResponse:
    """v18 c3 — single source of truth for the (style_id, scene_mode)
    Chinese labels. iOS pulls this once on launch and caches in
    UserDefaults; avoids forking the dictionary across iOS, web,
    and backend (where it lives in services/style_catalog).

    Public on purpose — no PII, no admin secrets. Add `Cache-Control`
    so the iOS HTTP cache can keep it for an hour without a roundtrip.
    """
    from .services import style_catalog as _sc
    body = {
        "version": 1,
        "styles": [{"id": sid,
                      "label_zh": _sc.LABEL_ZH.get(sid, sid)}
                     for sid in _sc.ALL_STYLE_IDS],
        "scene_modes": [{"id": sid, "label_zh": label}
                          for sid, label in _sc.SCENE_LABEL_ZH.items()],
    }
    return JSONResponse(body, headers={
        "Cache-Control": "public, max-age=3600",
    })


app.include_router(analyze.router)
app.include_router(pose_library.router)
app.include_router(avatars.router)
app.include_router(dev.router)
app.include_router(panorama.router)
app.include_router(models_api.router)
app.include_router(sun_api.router)
app.include_router(style_feasibility_api.router)
app.include_router(feedback_api.router)
app.include_router(recon3d_api.router)
app.include_router(devices_api.router)
app.include_router(auth_api.router)
app.include_router(iap_api.router)
app.include_router(usage_api.router)
app.include_router(admin_api.router)
app.include_router(admin_insights_api.router)
app.include_router(data_export_api.router)
app.include_router(endpoint_config_api.router)
if settings.enable_metrics:
    app.include_router(metrics_api.router)


# Mount the PWA web demo at /web. Same origin as the API, so no CORS pain
# and no second process to run. http://localhost:8000/web/ -> index page.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WEB_DIR = _REPO_ROOT / "web"


# v9 UX polish #11 — `preview.html` is a dev-only device-frame tool.
# In production it can confuse first-time visitors who Google it and
# think it's the product. We hide it behind app_debug / mock_mode so
# devs can still hit it locally.
@app.get("/web/preview.html", include_in_schema=False)
def _maybe_serve_preview():
    from fastapi import HTTPException
    dev = settings.mock_mode or (settings.app_env or "").lower() in ("local", "dev", "development")
    if not dev:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(_WEB_DIR / "preview.html")


_mount_web = (_WEB_DIR.exists()
               and not (_is_prod() and settings.disable_web_routes_in_prod))

if _mount_web:
    app.mount("/web", StaticFiles(directory=_WEB_DIR, html=True), name="web")

    @app.get("/")
    def _root_redirect() -> FileResponse:
        return FileResponse(_WEB_DIR / "index.html")

    # Splash / welcome screen — also reachable as /welcome so we can deep
    # link from elsewhere (and so iOS can mirror the URL).
    @app.get("/welcome")
    def _welcome() -> FileResponse:
        return FileResponse(_WEB_DIR / "welcome.html")

    # Convenience shortcut: http://localhost:8000/preview shows the PWA wrapped
    # inside an iPhone 15 Pro mock-up. Lets users on Windows/Linux preview the
    # app without a Mac/iPhone.
    @app.get("/preview")
    def _preview() -> FileResponse:
        return FileResponse(_WEB_DIR / "preview.html")
