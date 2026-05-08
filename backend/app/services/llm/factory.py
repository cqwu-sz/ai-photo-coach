"""Construct a VisionProvider from a model id + optional BYOK overrides.

Resolution order for the API key (first hit wins):
  1. ``api_key_override`` (BYOK from request)
  2. operator-side env var (settings.models_api_keys[vendor])
  3. None  -> provider raises ProviderUnauthorized at first call
"""
from __future__ import annotations

from typing import Optional

from ...config import Settings
from .base import ProviderConfig, VisionProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider
from .registry import find_model


def get_provider(
    settings: Settings,
    model_id: Optional[str],
    api_key_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
) -> VisionProvider:
    target_id = (model_id or settings.default_model_id).strip()
    cfg = find_model(target_id)
    if cfg is None:
        # Unknown id -> treat as a custom OpenAI-compat model. Requires
        # the user to bring their own key + base_url; otherwise the
        # provider will fail loudly at call time.
        cfg = ProviderConfig(
            id=target_id,
            display_name=target_id,
            vendor="custom",
            kind="openai_compat",
            base_url=base_url_override,
            model_id=target_id,
            supports_native_video=False,
            max_images=8,
            json_schema_mode="object",
            api_key_env=None,
            requires_key=True,
            notes="Custom user-configured model (no built-in preset).",
        )

    api_key = api_key_override or _resolve_api_key(settings, cfg)

    if cfg.kind == "gemini":
        return GeminiProvider(cfg, api_key=api_key)
    return OpenAICompatProvider(
        cfg,
        api_key=api_key,
        base_url=base_url_override or cfg.base_url,
    )


def _resolve_api_key(settings: Settings, cfg: ProviderConfig) -> Optional[str]:
    """Find the operator-side fallback key for this provider's vendor."""
    keys = settings.models_api_keys
    val = keys.get(cfg.vendor)
    return val or None
