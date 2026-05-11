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

    # ---- v13 — three-source position fusion -----------------------------
    amap_key: str = ""
    """AMap (Gaode) Place Search API key. When empty the POI lookup
    skips AMap and falls straight back to OpenStreetMap Overpass.
    Read from ``AMAP_KEY`` env var (also recognised as ``amap_key``)."""
    enable_poi_lookup: bool = True
    """Master kill-switch for the POI lookup path. Disable to force
    every analyze run back to the legacy 'relative-only' shape."""
    enable_walk_segment: bool = True
    """Master kill-switch for SfM/VIO derivation from walk_segment."""
    poi_lookup_timeout_sec: float = 1.5
    poi_lookup_radius_m: int = 300

    # ---- v14 — B/C upgrades (W1-W11) ------------------------------------
    enable_indoor_poi: bool = True
    indoor_provider: str = "amap"  # amap | mapbox | none
    amap_indoor_key: str = ""
    mapbox_token: str = ""
    enable_ugc_spots: bool = True
    ugc_min_upvotes: int = 3
    enable_route_planner: bool = True
    route_planner_distance_threshold_m: int = 50
    enable_triangulation: bool = True
    enable_time_optimal: bool = True
    enable_style_extract: bool = True
    enable_recon3d: bool = False  # off by default; explicit user trigger
    recon3d_max_concurrent_jobs: int = 1

    # ---- v15 — productization (P0/P1/P2 backlog) ------------------------
    cors_allow_origins: str = ""
    """Comma-separated allow-list. Blank → restrictive default
    (localhost + 127.0.0.1 + the bundled web demo). Set in prod env to
    your real frontend domain(s)."""
    request_token_secret: str = ""
    """HMAC secret for analyze_request_id. Blank → ephemeral per-process
    secret (fine for dev, MUST be set in prod)."""
    request_token_ttl_sec: int = 30 * 60
    enable_app_attest: bool = False
    """When True, /analyze rejects iOS requests without a valid App
    Attest assertion (P0-1.3). Off by default until iOS ships it."""
    enable_rate_limit: bool = True
    rate_limit_analyze_per_min: int = 6
    rate_limit_default_per_min: int = 30
    rate_limit_recon3d_per_min: int = 1
    enable_ugc_content_filter: bool = True
    ugc_dedup_window_hours: int = 24
    ugc_dedup_radius_m: float = 5.0
    recon3d_max_images: int = 30
    recon3d_max_image_bytes: int = 2 * 1024 * 1024
    enable_circuit_breaker: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_sec: float = 60.0
    enable_metrics: bool = True
    geo_round_decimals: int = 4
    """How many decimals to keep when persisting user coordinates.
    4 ≈ 11 m grid — used for POI dedupe where street-level precision
    actually matters. Other use sites should pick from the tier consts
    below or call ``round_geo_by_use`` so we never log raw GPS."""

    # v9 UX polish #18 — tier the geo rounding by use case. Anything
    # that leaves the request scope (persisted UGC, log lines, sent to
    # third-party APIs) should round to a coarser grid than the
    # transient values we use internally.
    geo_round_decimals_log: int = 3
    """~110 m grid. Use for diagnostic logs, analytics, anonymous UGC
    aggregates — anywhere a row could later be exported."""

    geo_round_decimals_third_party: int = 3
    """~110 m grid. Use when forwarding lat/lon to weather, geocoding,
    or any external API. Weather doesn't need < 100 m and POI
    providers shouldn't see your exact coordinates."""

    geo_round_decimals_poi: int = 4
    """~11 m grid. Use only for POI lookup / dedupe where street-level
    precision is the whole point."""
    enable_ddtrace: bool = False
    """When True (and ddtrace is installed) the analyze_service.run()
    spans are instrumented for Datadog APM."""
    weekly_seed_active_radius_km: float = 5.0
    enable_post_process_telemetry: bool = True
    enable_ar_nav_telemetry: bool = True

    # ---- v16 — multi-user / auth / IAP (Phase 0 of MULTI_USER_AUTH) -----
    app_jwt_secret: str = ""
    """HMAC secret for our own JWT (access + refresh). Blank → ephemeral
    per-process secret (dev only). MUST be set in prod env."""
    app_jwt_access_ttl_sec: int = 15 * 60
    app_jwt_refresh_ttl_sec: int = 30 * 24 * 3600

    apple_siwa_bundle_id: str = ""
    """Bundle id used as the expected `aud` when verifying SIWA identity
    tokens. Blank → /auth/siwa returns 503 (compliance failsafe)."""
    apple_siwa_team_id: str = ""
    apple_siwa_jwks_url: str = "https://appleid.apple.com/auth/keys"

    apple_iap_bundle_id: str = ""
    """Bundle id used to validate the StoreKit 2 JWS `bundleId` claim."""
    apple_iap_environment: str = "Production"
    """Production | Sandbox. Sandbox-only when set to 'Sandbox'."""
    apple_iap_pro_product_ids: str = "ai_photo_coach.pro.monthly"
    """Comma-separated list of product ids that grant tier=pro."""
    apple_iap_grace_period_days: int = 16

    enable_legacy_device_id_auth: bool = True
    """When True, requests without Authorization but with X-Device-Id are
    auto-promoted to an anonymous user (compat layer for unupgraded
    clients). Flip to False after iOS v1.1 rollout."""

    # ---- A1 — Phase 1 productization (rate-limit / db / tiers) ---------
    redis_url: str = ""
    """When set (e.g. redis://localhost:6379/0), rate-limit + future
    cross-worker state moves to Redis. Blank → in-process token bucket
    (single-worker only)."""
    rate_limit_pro_multiplier: float = 5.0
    """Pro users get N× the free quota for analyze/feedback/recon3d."""

    db_backend: str = "sqlite"
    """sqlite (default) | postgres. Postgres path is gated behind the
    `psycopg` dependency and only switches `users.db` for now; feature
    tables migrate in Phase 1.2."""
    postgres_dsn: str = ""

    privacy_policy_url: str = ""
    """Public URL of the hosted privacy policy. Defaults to /web/privacy.html
    when blank — the bundled page suffices for first submission but you
    SHOULD swap in a marketing-domain URL before scaling."""
    eula_url: str = ""
    """Optional. Apple's standard EULA is fine for most cases — leave
    blank to use https://www.apple.com/legal/internet-services/itunes/dev/stdeula/"""

    anonymous_account_ttl_days: int = 30
    """Cron sweeper: anonymous accounts inactive for > N days get hard
    deleted along with their data. Set 0 to disable."""

    enforce_required_secrets: bool = False
    """When True, app refuses to start unless all production-critical
    env vars are set. CI / staging should keep it False; prod should
    flip to True so a misconfigured deploy fails loudly."""

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


# v9 UX polish #18 — single chokepoint for "round before this leaves the
# process". Use ``"log"`` / ``"third_party"`` / ``"poi"`` to pick the
# right tier without callers having to remember decimal counts.
_GEO_TIERS = {
    "log": "geo_round_decimals_log",
    "third_party": "geo_round_decimals_third_party",
    "poi": "geo_round_decimals_poi",
    # Back-compat alias for old call sites still using the flat setting.
    "default": "geo_round_decimals",
}


def round_geo_by_use(value: float | None, use: str = "log") -> float | None:
    """Round a single lat/lon by intended use case.

    >>> round_geo_by_use(31.230871, "log")        # ~110 m
    31.231
    >>> round_geo_by_use(31.230871, "poi")        # ~11 m
    31.2309
    """
    if value is None:
        return None
    s = get_settings()
    attr = _GEO_TIERS.get(use, _GEO_TIERS["log"])
    decimals = int(getattr(s, attr))
    return round(float(value), decimals)
