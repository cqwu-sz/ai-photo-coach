import os
import pytest


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ENABLE_RATE_LIMIT", "false")
    monkeypatch.setenv("ENABLE_LEGACY_DEVICE_ID_AUTH", "true")
    # v17 — anonymous signup is disabled in prod, but our existing test
    # corpus relies on /auth/anonymous + X-Device-Id legacy. Turn it
    # back on for the test process so PR2's gate doesn't block fixtures.
    monkeypatch.setenv("ENABLE_ANONYMOUS_AUTH", "true")
    monkeypatch.setenv("APP_JWT_SECRET", "test-secret-please-rotate-this-is-32-bytes-min")
    monkeypatch.setenv("APPLE_SIWA_BUNDLE_ID", "com.example.aiphotocoach.test")
    monkeypatch.setenv("APPLE_IAP_BUNDLE_ID", "")  # disable bundle check in tests
    from app.config import get_settings
    from app.services import model_config, otp, rate_limit, user_repo

    # Re-route the users db into a per-test tmp dir so test runs don't
    # cross-pollinate (and don't need disk cleanup).
    user_repo.DB_PATH = tmp_path / "users.db"
    get_settings.cache_clear()
    rate_limit.reset_for_tests()
    otp.reset_for_tests()
    model_config.reset_for_tests()

    # Inject a default X-Device-Id on requests that don't already carry
    # one (or an Authorization header). Registered once, idempotent.
    from app.main import app as _app
    if not getattr(_app.state, "_test_device_mw_installed", False):
        @_app.middleware("http")
        async def _test_default_device(request, call_next):
            keys = {k.lower() for k in request.headers.keys()}
            if "x-device-id" not in keys and "authorization" not in keys:
                request.scope["headers"] = list(request.scope["headers"]) + [
                    (b"x-device-id", b"_pytest_default_device"),
                ]
            return await call_next(request)
        _app.state._test_device_mw_installed = True

    yield
    get_settings.cache_clear()
    rate_limit.reset_for_tests()
    otp.reset_for_tests()
    model_config.reset_for_tests()
