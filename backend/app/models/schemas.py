"""Pydantic models that mirror shared/schema/analyze.openapi.yaml.

Source of truth for the wire format. iOS has matching Codable types in
ios/AIPhotoCoach/Models/Schemas.swift.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class QualityMode(str, Enum):
    fast = "fast"
    high = "high"


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


class CaptureMeta(BaseModel):
    person_count: int = Field(ge=1, le=4)
    quality_mode: QualityMode = QualityMode.fast
    style_keywords: list[str] = Field(default_factory=list)
    frame_meta: list[FrameMeta]

    @field_validator("frame_meta")
    @classmethod
    def _frames_len(cls, v: list[FrameMeta]) -> list[FrameMeta]:
        if not 4 <= len(v) <= 16:
            raise ValueError("frame_meta length must be 4..16")
        return v


class SceneSummary(BaseModel):
    type: str
    lighting: Lighting
    background_summary: str
    cautions: list[str] = Field(default_factory=list)


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


class CameraSettings(BaseModel):
    focal_length_mm: float = Field(ge=14, le=200)
    aperture: str
    shutter: str
    iso: int = Field(ge=50, le=12800)
    white_balance_k: Optional[int] = Field(default=None, ge=2500, le=10000)
    ev_compensation: Optional[float] = Field(default=None, ge=-3, le=3)
    rationale: Optional[str] = None
    device_hints: Optional[DeviceHints] = None


class PersonPose(BaseModel):
    role: str
    stance: Optional[str] = None
    upper_body: Optional[str] = None
    hands: Optional[str] = None
    gaze: Optional[str] = None
    expression: Optional[str] = None
    position_hint: Optional[str] = None


class PoseSuggestion(BaseModel):
    person_count: int = Field(ge=1, le=4)
    layout: Layout
    persons: list[PersonPose]
    interaction: Optional[str] = None
    reference_thumbnail_id: Optional[str] = None
    difficulty: Optional[Difficulty] = None


class ShotRecommendation(BaseModel):
    id: str
    title: Optional[str] = None
    angle: Angle
    composition: Composition
    camera: CameraSettings
    poses: list[PoseSuggestion]
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


class StyleInspiration(BaseModel):
    """How the user-provided reference photos were absorbed."""
    used_count: int = 0
    summary: Optional[str] = None
    """One sentence in Chinese, e.g. 借鉴了你图1的低饱和暖调与图2的高低错位站位。"""
    inherited_traits: list[str] = Field(default_factory=list)
    """Short tags like ["暖调", "高低错位", "三分线"]."""


class AnalyzeResponse(BaseModel):
    scene: SceneSummary
    shots: list[ShotRecommendation]
    style_inspiration: Optional[StyleInspiration] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model: str = ""
    debug: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
