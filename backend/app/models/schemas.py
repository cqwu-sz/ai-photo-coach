"""Pydantic models that mirror shared/schema/analyze.openapi.yaml.

Source of truth for the wire format. iOS has matching Codable types in
ios/AIPhotoCoach/Models/Schemas.swift.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class QualityMode(str, Enum):
    fast = "fast"
    high = "high"


class SceneMode(str, Enum):
    """High-level intent for what the user wants to shoot.

    Drives prompt branching, camera-parameter sweet spots, and (in the
    case of ``scenery``) whether poses are required at all.
    """
    portrait = "portrait"
    closeup = "closeup"
    full_body = "full_body"
    documentary = "documentary"
    scenery = "scenery"
    light_shadow = "light_shadow"
    """Light & shadow mode — uses sun position + visual brightness peak to
    plan rim-light / silhouette / chiaroscuro shots. Time-sensitive: AI may
    return shots in a recommended order with a 'shoot before X minutes' tag."""


class Lighting(str, Enum):
    golden_hour = "golden_hour"
    blue_hour = "blue_hour"
    harsh_noon = "harsh_noon"
    overcast = "overcast"
    shade = "shade"
    indoor_warm = "indoor_warm"
    indoor_cool = "indoor_cool"
    low_light = "low_light"
    backlight = "backlight"
    mixed = "mixed"


class CompositionType(str, Enum):
    rule_of_thirds = "rule_of_thirds"
    leading_line = "leading_line"
    symmetry = "symmetry"
    frame_within_frame = "frame_within_frame"
    negative_space = "negative_space"
    centered = "centered"
    diagonal = "diagonal"
    golden_ratio = "golden_ratio"


class HeightHint(str, Enum):
    low = "low"
    eye_level = "eye_level"
    high = "high"
    overhead = "overhead"


class Layout(str, Enum):
    single = "single"
    side_by_side = "side_by_side"
    high_low_offset = "high_low_offset"
    triangle = "triangle"
    line = "line"
    cluster = "cluster"
    diagonal = "diagonal"
    v_formation = "v_formation"
    circle = "circle"
    custom = "custom"


class IphoneLens(str, Enum):
    ultrawide_0_5x = "ultrawide_0_5x"
    wide_1x = "wide_1x"
    tele_2x = "tele_2x"
    tele_3x = "tele_3x"
    tele_5x = "tele_5x"


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class FrameMeta(BaseModel):
    index: int = Field(ge=0)
    azimuth_deg: float
    pitch_deg: float = 0
    roll_deg: float = 0
    timestamp_ms: int = 0
    ambient_lux: Optional[float] = None
    # ---- v6 capture-quality signals (Phase 1) -----------------------
    # Filled by the client when it can compute these cheaply on-device
    # (iOS: CIFilter / Vision; Web: Canvas + mediapipe-tasks-vision).
    # All optional — old clients still work, the backend treats absence
    # as "no signal" rather than "bad signal".
    blur_score: Optional[float] = Field(
        default=None, ge=0,
        description="Laplacian variance — higher is sharper. < 50 hints blur.",
    )
    mean_luma: Optional[float] = Field(
        default=None, ge=0, le=255,
        description="Average grayscale luma 0-255. < 30 hints too-dark.",
    )
    face_hit: Optional[bool] = Field(
        default=None,
        description="Did the client detect at least one face/subject in this frame?",
    )


class GeoFix(BaseModel):
    """Optional location attached by the client. Only sent when the user
    has explicitly opted in (web Geolocation API / iOS CoreLocation when-in-use).

    Used by the analyze pipeline to inject ENVIRONMENT FACTS into the LLM
    prompt (sun azimuth/altitude, golden-hour countdown, etc.) — extremely
    valuable for ``light_shadow`` mode but harmless to include for any mode.
    """
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: Optional[float] = Field(default=None, ge=0)
    timestamp: Optional[datetime] = None
    """If absent the analyze service uses ``datetime.now(UTC)``."""


class CaptureMeta(BaseModel):
    person_count: int = Field(ge=0, le=4)
    """0 is allowed only for ``scene_mode == scenery`` (validated below)."""
    quality_mode: QualityMode = QualityMode.fast
    scene_mode: SceneMode = SceneMode.portrait
    style_keywords: list[str] = Field(default_factory=list)
    frame_meta: list[FrameMeta]
    geo: Optional[GeoFix] = None
    """Optional. When present, the prompt injects sun + time-of-day facts."""

    @field_validator("frame_meta")
    @classmethod
    def _frames_len(cls, v: list[FrameMeta]) -> list[FrameMeta]:
        if not 4 <= len(v) <= 16:
            raise ValueError("frame_meta length must be 4..16")
        return v

    @model_validator(mode="after")
    def _person_count_for_mode(self) -> "CaptureMeta":
        if self.person_count == 0 and self.scene_mode != SceneMode.scenery:
            raise ValueError(
                "person_count=0 is only valid when scene_mode='scenery'"
            )
        return self


class CaptureQualityIssue(str, Enum):
    """LLM-judged reasons the captured environment video may not be a
    suitable basis for shot recommendations. Surfaced verbatim in the UI
    advisory banner."""
    cluttered_bg = "cluttered_bg"
    no_subject = "no_subject"
    ground_only = "ground_only"
    too_dark = "too_dark"
    too_many_passersby = "too_many_passersby"
    blurry = "blurry"
    narrow_pan = "narrow_pan"


class CaptureQuality(BaseModel):
    """LLM self-assessment of how usable the user's environment video is
    as evidence for the analyze. The LLM is instructed (rule 13) to fill
    this honestly rather than fabricate confident shots when the footage
    is poor.

    score 1-5; 1-2 means "really shouldn't analyze, ask user to retake";
    3 is fine but with caveats; 4-5 is solid. ``should_retake`` is the
    explicit flag the result UI uses to render an Advisory Banner.
    """
    score: int = Field(ge=1, le=5)
    issues: list[CaptureQualityIssue] = Field(default_factory=list)
    summary_zh: str = Field(default="", description="One-sentence Chinese explanation surfaced as the banner subtitle.")
    should_retake: bool = False


class SceneSummary(BaseModel):
    type: str
    lighting: Lighting
    background_summary: str
    cautions: list[str] = Field(default_factory=list)
    vision_light: Optional[VisionLightHint] = None
    """LLM-derived dominant light direction inferred from the frames,
    independent of any geo fix. Always populated for ``light_shadow``
    scene mode so the result UI can draw a light indicator on the compass
    even when location permission is denied."""
    capture_quality: Optional[CaptureQuality] = None
    """LLM self-assessment of whether the env video is even good enough to
    analyze. Filled when prompt rule 13 is honored (real LLM); mock
    provider also fills a benign value so UI doesn't crash on absence."""


class Angle(BaseModel):
    azimuth_deg: float
    pitch_deg: float
    distance_m: float = Field(ge=0.3, le=20)
    height_hint: Optional[HeightHint] = None


class Composition(BaseModel):
    primary: CompositionType
    secondary: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class DeviceHints(BaseModel):
    iphone_lens: Optional[IphoneLens] = None
    third_party_app: Optional[str] = None


class IphoneApplyPlan(BaseModel):
    """Machine-applicable parameters for iPhone's AVCaptureDevice. The
    LLM only fills the photographer-facing semantic intent (aperture
    "f/2.0", shutter "1/250") — *this* plan is computed by
    ``services.camera_apply.build_plan`` so the iOS shoot screen can
    plug it straight into AVFoundation without re-parsing strings.

    Schema goal: every field is either directly settable on
    ``AVCaptureDevice`` or a passive note for the UI.
    """
    zoom_factor: float = Field(ge=0.5, le=15.0)
    """``device.videoZoomFactor`` target on ``builtInTripleCamera``.
    0.5 = ultra-wide, 1.0 = main 1x, 2.0 = 2x telephoto, 5.0 = 5x.
    Computed from focal_length_mm assuming a 26mm-equivalent main lens."""
    iso: int = Field(ge=25, le=12800)
    shutter_seconds: float = Field(gt=0.0, le=1.0)
    """For ``setExposureModeCustom(duration:iso:)``."""
    ev_compensation: float = Field(ge=-3.0, le=3.0, default=0.0)
    white_balance_k: int = Field(ge=2000, le=10000, default=5500)
    aperture_note: str = ""
    """One-sentence honest note about iPhone's fixed-aperture lens. The
    UI shows it under the aperture chip so the user knows whether the
    requested f-stop is achievable in-camera or only in post."""
    can_apply: bool = True
    """False when current device can't satisfy the plan (e.g. requested
    zoom_factor exceeds the active format). UI shows a degraded mode."""


class CameraSettings(BaseModel):
    focal_length_mm: float = Field(ge=14, le=200)
    aperture: str
    shutter: str
    iso: int = Field(ge=50, le=12800)
    white_balance_k: Optional[int] = Field(default=None, ge=2500, le=10000)
    ev_compensation: Optional[float] = Field(default=None, ge=-3, le=3)
    rationale: Optional[str] = None
    device_hints: Optional[DeviceHints] = None
    iphone_apply_plan: Optional[IphoneApplyPlan] = None
    """Always populated by the backend after analyze; the LLM never sets
    this directly. Lets iOS apply parameters via AVCaptureDevice without
    re-parsing the photographer-facing ``aperture`` / ``shutter`` strings."""


class PersonPose(BaseModel):
    role: str
    stance: Optional[str] = None
    upper_body: Optional[str] = None
    hands: Optional[str] = None
    gaze: Optional[str] = None
    expression: Optional[str] = None
    position_hint: Optional[str] = None


class PoseSuggestion(BaseModel):
    person_count: int = Field(ge=0, le=4)
    layout: Layout
    persons: list[PersonPose] = Field(default_factory=list)
    interaction: Optional[str] = None
    reference_thumbnail_id: Optional[str] = None
    difficulty: Optional[Difficulty] = None


class CriteriaScore(BaseModel):
    """7-dimension quality breakdown for a shot. Each axis 1-5.

    Upgraded from the original 4 axes (composition/light/color/depth) to
    7 axes in v6 — adding **subject_fit** (人物在画面中的位置与比例是否合
    适)、**background** (背景是否纯净、有无穿头穿杆) 和 **theme** (这张
    照片要表达的主题是否被画面元素出卖). The added axes directly target
    the user feedback: "好看不只是构图光线，背景复不复杂、主体合不合适、
    主题表达对不对都很重要".

    Default values map to "中位数 3 分" so partial responses (older
    models or repair fallbacks) still validate. The schema accepts
    legacy 4-axis responses by defaulting the 3 new fields to 3.
    """
    composition: int = Field(ge=1, le=5, default=3)
    light: int       = Field(ge=1, le=5, default=3)
    color: int       = Field(ge=1, le=5, default=3)
    depth: int       = Field(ge=1, le=5, default=3)
    subject_fit: int = Field(ge=1, le=5, default=3)
    background: int  = Field(ge=1, le=5, default=3)
    theme: int       = Field(ge=1, le=5, default=3)


class CriteriaNotes(BaseModel):
    """One-sentence justification for each criterion. Optional but strongly
    encouraged — these turn the score bars into something a human can act on.

    Each note **should** start with a ``[rule_id]`` reference into the
    composition KB (Phase 2.2), e.g. ``[comp_rule_of_thirds] 主体压在右
    三分线，地面引导线把视线带向人物``. Notes that don't reference a KB
    rule get a ``[freeform]`` prefix instead — this is allowed but
    discouraged so we can spot when the KB needs more entries.
    """
    composition: Optional[str] = None
    light: Optional[str] = None
    color: Optional[str] = None
    depth: Optional[str] = None
    subject_fit: Optional[str] = None
    background: Optional[str] = None
    theme: Optional[str] = None


class ShotRecommendation(BaseModel):
    id: str
    title: Optional[str] = None
    angle: Angle
    composition: Composition
    camera: CameraSettings
    poses: list[PoseSuggestion] = Field(default_factory=list)
    """May be empty for ``scene_mode == scenery``."""
    rationale: str
    coach_brief: Optional[str] = None
    """Short first-person line, like a friend coaching at the scene.
    Example: "来，把这棵树留在你左边，往后退一步蹲下..." Optional; if missing
    the UI will fall back to rationale."""
    representative_frame_index: Optional[int] = Field(default=None, ge=0)
    """Index into CaptureMeta.frame_meta of the frame that best matches
    this shot's azimuth. Lets the UI use that frame as a backdrop when it
    composes the visual mock-up."""
    confidence: float = Field(ge=0, le=1, default=0.7)

    # ----- 7-dimension scoring (v6 — composition × light × color × depth
    # × subject_fit × background × theme) -------------------------------
    criteria_score: Optional[CriteriaScore] = None
    """1-5 scores on the seven quality axes."""
    criteria_notes: Optional[CriteriaNotes] = None
    """One-line rule citation per axis. Should start with ``[rule_id]``
    referring to the composition KB; ``[freeform]`` allowed but tracked."""
    strongest_axis: Optional[str] = Field(default=None, description="Best axis name from {composition, light, color, depth, subject_fit, background, theme}")
    weakest_axis: Optional[str] = Field(default=None, description="Worst axis — UI shows a 'watch out' tip from notes[weakest]")
    overall_score: Optional[float] = Field(
        default=None, ge=0, le=5,
        description="Backend-computed weighted average for ranking. Filled in _repair_shot, "
                    "formula: 0.5*avg(criteria) + 0.3*confidence*5 + 0.2*time_bonus.",
    )

    # ----- iPhone-specific photo tips ---------------------------------
    iphone_tips: list[str] = Field(default_factory=list)
    """2-3 short Chinese sentences specific to shooting this on an
    iPhone. Examples:
      - "切到 2x 长焦镜头拍人像，避免主摄数码裁剪降低画质"
      - "iPhone 物理光圈固定 f/1.78，要 f/4 的深景深建议拍后用人像模式"
      - "ISO 800 噪点可见，可以靠近主体让 ISO 自然降到 200"
    Filled by the LLM (prompt-driven) so it stays scene-aware. Always
    rendered alongside the iphone_apply_plan in both Web and iOS UIs."""


class StyleInspiration(BaseModel):
    """How the user-provided reference photos were absorbed."""
    used_count: int = 0
    summary: Optional[str] = None
    """One sentence in Chinese, e.g. 借鉴了你图1的低饱和暖调与图2的高低错位站位。"""
    inherited_traits: list[str] = Field(default_factory=list)
    """Short tags like ["暖调", "高低错位", "三分线"]."""


class SunSnapshot(BaseModel):
    """Photographer-ready sun-position summary, computed from the optional
    ``CaptureMeta.geo`` fix at request time. Mirrors backend
    ``services.sun.SunInfo``. Sent only when geo was provided.
    """
    azimuth_deg: float
    altitude_deg: float
    phase: str
    color_temp_k_estimate: int
    minutes_to_golden_end: Optional[float] = None
    minutes_to_blue_end: Optional[float] = None
    minutes_to_sunset: Optional[float] = None
    minutes_to_sunrise: Optional[float] = None


class WeatherSnapshot(BaseModel):
    """Photographer-friendly current weather. Sourced from Open-Meteo
    (free, no key). Optional — analyze never fails on weather lookup
    errors, just leaves this null."""
    cloud_cover_pct: Optional[int] = Field(default=None, ge=0, le=100)
    visibility_m: Optional[int] = Field(default=None, ge=0)
    uv_index: Optional[float] = Field(default=None, ge=0)
    temperature_c: Optional[float] = None
    weather_code: Optional[int] = Field(default=None, description="WMO weather code")
    softness: str = "unknown"
    """One of soft / hard / mixed / unknown — derived from cloud_cover +
    weather_code. UI uses this to choose a soft/hard glyph and the prompt
    uses it to bias rim-light vs wraparound advice."""
    code_label_zh: Optional[str] = None


class VisionLightHint(BaseModel):
    """LLM-derived dominant light direction inferred purely from the video
    frames — works even without a geo fix. Lower-confidence than a real
    sun calculation but always available, so the UI can always render a
    light indicator on the compass.
    """
    direction_deg: Optional[float] = Field(
        default=None, ge=0, lt=360,
        description="0=N, 90=E, 180=S, 270=W. Where the dominant light comes from.",
    )
    quality: Optional[str] = Field(
        default=None,
        description="hard / soft / mixed / unknown — judged from highlights + shadow edges.",
    )
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    notes: Optional[str] = None
    """One sentence in Chinese, e.g. '亮度峰值在第 5 帧 azimuth 245°，影子方向反推主光来自西北。'"""


class EnvironmentSnapshot(BaseModel):
    """Time-stamped environmental context used to plan the shoot."""
    sun: Optional[SunSnapshot] = None
    weather: Optional[WeatherSnapshot] = None
    vision_light: Optional[VisionLightHint] = None
    """Always-available, vision-derived light direction. Populated even
    when sun/weather are missing — enables the compass to show a (dashed)
    light indicator regardless of permission state."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LightRecaptureHint(BaseModel):
    """Backend-issued nudge: 'shoot a 10-second pass aimed at the brightest
    direction so we can lock in a clearer light-shadow plan'. Fires when:

      - scene_mode == light_shadow
      - vision_light.confidence is low (or missing) AND geo is missing
      - OR vision_light says quality is unknown

    The UI shows a banner above the shots and offers a one-tap return to
    the capture screen with a hint to face the brightest spot.
    """
    enabled: bool = False
    title: str = ""
    detail: str = ""
    suggested_azimuth_deg: Optional[float] = Field(default=None, ge=0, lt=360)
    """If we have any light direction guess, prefill the recapture page so
    the user can use it as the centre of the new pass."""


class AnalyzeResponse(BaseModel):
    scene: SceneSummary
    shots: list[ShotRecommendation]
    style_inspiration: Optional[StyleInspiration] = None
    environment: Optional[EnvironmentSnapshot] = None
    """Echoed back so the result UI can draw a sun compass / golden-hour
    badge. Only populated when the request supplied a geo fix."""
    light_recapture_hint: Optional[LightRecaptureHint] = None
    """Optional banner asking the user to shoot a 10-second light-pass
    when we can't reliably reason about light. Populated in
    ``light_shadow`` mode when vision-light confidence is too low and no
    geo fix is available."""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model: str = ""
    debug: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
