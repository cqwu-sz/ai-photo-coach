"""Provider abstraction for vision LLMs.

`VisionProvider` is the small surface AnalyzeService talks to:
  - analyze(...) takes the user inputs and returns a parsed-but-not-yet-
    Pydantic-validated dict that AnalyzeResponse.model_validate(...) can
    ingest.
  - repair(...) is a second-chance call that feeds back the previous raw
    output + Pydantic validation errors so the model can fix structure.
  - ping(...) does a 1-token request used by /models/test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ...models import CaptureMeta


@dataclass(frozen=True)
class ProviderConfig:
    """Static description of a built-in (or user-overridden) vision model."""

    id: str
    """Stable string id used in the API & UI (e.g. ``gemini-2.5-flash``)."""

    display_name: str
    vendor: str
    """``google`` / ``openai`` / ``zhipu`` / ``dashscope`` / ``deepseek``
    / ``moonshot`` / ``custom``."""

    kind: str
    """``gemini`` (native) or ``openai_compat`` (any /v1/chat/completions)."""

    base_url: Optional[str] = None
    """Required for ``openai_compat``; ignored for ``gemini``."""

    model_id: Optional[str] = None
    """Vendor-side model name. Defaults to ``id`` when None."""

    supports_native_video: bool = False
    """If True the provider receives all keyframes natively (Gemini).
    Otherwise we subsample down to ``max_images`` images per request."""

    max_images: int = 8
    """Max image parts per request for image-only providers."""

    json_schema_mode: str = "schema"
    """JSON enforcement strategy:
        - ``schema`` -> response_format json_schema (OpenAI / Gemini)
        - ``object`` -> response_format json_object (most others)
        - ``none``   -> rely on prompt + repair pass only
    """

    api_key_env: Optional[str] = None
    """Env var the backend reads for its own (operator) fallback key."""

    notes: str = ""
    requires_key: bool = True


class ProviderError(RuntimeError):
    """Generic provider failure -> maps to 502/503 for the client."""


class ProviderUnauthorized(ProviderError):
    """API key missing / invalid -> 401."""


class ProviderQuotaExceeded(ProviderError):
    """Rate limited / quota exhausted -> 429."""


class VisionProvider(Protocol):
    """Runtime protocol both GeminiProvider and OpenAICompatProvider satisfy."""

    config: ProviderConfig

    async def analyze(
        self,
        meta: CaptureMeta,
        frames: list[bytes],
        references: list[bytes],
        pose_summary: str,
        camera_summary: str,
        scene_mode: str,
        panorama_jpeg: bytes | None = None,
        video_mp4: bytes | None = None,
    ) -> dict[str, Any]: ...

    async def repair(
        self,
        meta: CaptureMeta,
        prev_output: str,
        validation_errors: list[dict],
        scene_mode: str,
    ) -> dict[str, Any]: ...

    async def ping(self) -> dict[str, Any]: ...
