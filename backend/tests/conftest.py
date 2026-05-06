import os
import pytest


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
