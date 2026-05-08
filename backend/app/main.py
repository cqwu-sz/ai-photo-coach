"""FastAPI entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import analyze, avatars, dev, models as models_api, panorama, pose_library, sun as sun_api
from .config import get_settings
from .logging_setup import setup_logging

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
        },
    )
    yield


app = FastAPI(
    title="AI Photo Coach API",
    version="0.1.0",
    description="Backend for the iOS AI Photo Coach app.",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> JSONResponse:
    s = get_settings()
    return JSONResponse({"status": "ok", "mock_mode": s.mock_mode})


app.include_router(analyze.router)
app.include_router(pose_library.router)
app.include_router(avatars.router)
app.include_router(dev.router)
app.include_router(panorama.router)
app.include_router(models_api.router)
app.include_router(sun_api.router)


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
