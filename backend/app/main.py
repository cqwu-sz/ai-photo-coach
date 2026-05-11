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
    analyze,
    auth as auth_api,
    avatars,
    dev,
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
)
from .config import get_settings
from .logging_setup import setup_logging
from .services import recon3d as recon3d_service
from .services import startup_checks, user_repo

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

    cleanup_task = asyncio.create_task(recon3d_service.cleanup_loop())
    anon_task = asyncio.create_task(_anonymous_account_sweeper())
    try:
        yield
    finally:
        for t in (cleanup_task, anon_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


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


app = FastAPI(
    title="AI Photo Coach API",
    version="0.1.0",
    description="Backend for the iOS AI Photo Coach app.",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

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
if settings.enable_metrics:
    app.include_router(metrics_api.router)


# Mount the PWA web demo at /web. Same origin as the API, so no CORS pain
# and no second process to run. http://localhost:8000/web/ -> index page.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WEB_DIR = _REPO_ROOT / "web"
if _WEB_DIR.exists():
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
