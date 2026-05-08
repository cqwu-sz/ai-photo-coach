"""Centralised settings loaded from env vars / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    app_port: int = 8000
    log_level: str = "INFO"

    mock_mode: bool = True

    # ---- model selection ------------------------------------------------
    default_model_id: str = "gemini-2.5-flash"
    enable_byok: bool = True
    """When False, the /analyze endpoint ignores per-request model_api_key
    overrides and always uses operator-side env keys."""

    # ---- per-vendor operator-side fallback keys -------------------------
    gemini_api_key: str = ""
    openai_api_key: str = ""
    zhipu_api_key: str = ""
    dashscope_api_key: str = ""
    deepseek_api_key: str = ""
    moonshot_api_key: str = ""

    # Legacy fields kept for backward compatibility with /healthz logging.
    gemini_model_fast: str = "gemini-2.5-flash"
    gemini_model_high: str = "gemini-2.5-pro"

    kb_poses_dir: str = "app/knowledge/poses"
    kb_camera_dir: str = "app/knowledge/camera_settings"
    kb_composition_dir: str = "app/knowledge/composition"
    kb_animations_path_str: str = "app/knowledge/animations/pose_to_mixamo.json"

    max_frames: int = 16
    max_frame_bytes: int = 2 * 1024 * 1024
    max_reference_thumbs: int = 8

    @property
    def kb_poses_path(self) -> Path:
        return BACKEND_ROOT / self.kb_poses_dir

    @property
    def kb_camera_path(self) -> Path:
        return BACKEND_ROOT / self.kb_camera_dir

    @property
    def kb_composition_path(self) -> Path:
        return BACKEND_ROOT / self.kb_composition_dir

    @property
    def kb_animations_path(self) -> Path:
        return BACKEND_ROOT / self.kb_animations_path_str

    @property
    def models_api_keys(self) -> dict[str, str]:
        """Vendor -> operator-side fallback key. BYOK overrides win over
        these on a per-request basis."""
        return {
            "google": self.gemini_api_key,
            "openai": self.openai_api_key,
            "zhipu": self.zhipu_api_key,
            "dashscope": self.dashscope_api_key,
            "deepseek": self.deepseek_api_key,
            "moonshot": self.moonshot_api_key,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
