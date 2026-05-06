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

    gemini_api_key: str = ""
    gemini_model_fast: str = "gemini-2.5-flash"
    gemini_model_high: str = "gemini-2.5-pro"

    kb_poses_dir: str = "app/knowledge/poses"
    kb_camera_dir: str = "app/knowledge/camera_settings"
    kb_composition_dir: str = "app/knowledge/composition"

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
