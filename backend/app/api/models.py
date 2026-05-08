"""GET /models — list built-in vision models exposed to the UI.
POST /models/test — sanity-check a (model_id, api_key, base_url?) tuple
without burning a real analysis call.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..config import get_settings
from ..services.llm import (
    BUILTIN_MODELS,
    DEFAULT_MODEL_ID,
    ProviderQuotaExceeded,
    ProviderUnauthorized,
    get_provider,
)

router = APIRouter(prefix="/models", tags=["models"])
log = logging.getLogger(__name__)


class ModelPresetOut(BaseModel):
    id: str
    display_name: str
    vendor: str
    kind: str
    base_url: Optional[str]
    supports_native_video: bool
    json_schema_mode: str
    api_key_env: Optional[str]
    notes: str
    requires_key: bool
    has_operator_key: bool
    """True when the backend has a usable env-var fallback for this vendor."""


class ModelsResponse(BaseModel):
    default_model_id: str
    enable_byok: bool
    models: list[ModelPresetOut]


class ModelsTestRequest(BaseModel):
    model_id: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ModelsTestResponse(BaseModel):
    ok: bool
    snippet: Optional[str] = None
    error: Optional[str] = None


@router.get("", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    s = get_settings()
    op_keys = s.models_api_keys
    items = [
        ModelPresetOut(
            id=cfg.id,
            display_name=cfg.display_name,
            vendor=cfg.vendor,
            kind=cfg.kind,
            base_url=cfg.base_url,
            supports_native_video=cfg.supports_native_video,
            json_schema_mode=cfg.json_schema_mode,
            api_key_env=cfg.api_key_env,
            notes=cfg.notes,
            requires_key=cfg.requires_key,
            has_operator_key=bool(op_keys.get(cfg.vendor)),
        )
        for cfg in BUILTIN_MODELS
    ]
    return ModelsResponse(
        default_model_id=s.default_model_id or DEFAULT_MODEL_ID,
        enable_byok=s.enable_byok,
        models=items,
    )


@router.post("/test", response_model=ModelsTestResponse)
async def test_model(req: ModelsTestRequest) -> ModelsTestResponse:
    settings = get_settings()
    if not settings.enable_byok and req.api_key:
        # Still allow testing, just route through operator key if user
        # tries to bring their own while BYOK is disabled.
        api_key_override = None
    else:
        api_key_override = req.api_key

    try:
        provider = get_provider(
            settings,
            model_id=req.model_id,
            api_key_override=api_key_override,
            base_url_override=req.base_url,
        )
        result = await provider.ping()
        return ModelsTestResponse(ok=True, snippet=result.get("snippet"))
    except ProviderUnauthorized as exc:
        log.info("models/test unauthorized for %s", req.model_id)
        return ModelsTestResponse(ok=False, error=f"unauthorized: {exc}")
    except ProviderQuotaExceeded as exc:
        return ModelsTestResponse(ok=False, error=f"quota: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.warning("models/test error for %s: %s", req.model_id, exc)
        return ModelsTestResponse(ok=False, error=str(exc))
