"""Vision-LLM provider abstraction.

Small surface that AnalyzeService talks to, with two concrete backends:
  - GeminiProvider   (native video via google-genai)
  - OpenAICompatProvider (anything OpenAI-compat: GLM, Qwen, DeepSeek-VL2,
    Moonshot, OpenAI itself, plus custom user endpoints)

The registry lists all built-in vision presets exposed via /models.
"""
from .base import (
    ProviderConfig,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderUnauthorized,
    VisionProvider,
)
from .factory import get_provider
from .registry import BUILTIN_MODELS, DEFAULT_MODEL_ID, MODELS_BY_ID, find_model

__all__ = [
    "BUILTIN_MODELS",
    "DEFAULT_MODEL_ID",
    "MODELS_BY_ID",
    "ProviderConfig",
    "ProviderError",
    "ProviderQuotaExceeded",
    "ProviderUnauthorized",
    "VisionProvider",
    "find_model",
    "get_provider",
]
