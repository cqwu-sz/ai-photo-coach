"""Pydantic models that mirror shared/schema/analyze.openapi.yaml.

Source of truth for the wire format. iOS has matching Codable types in
ios/AIPhotoCoach/Models/Schemas.swift.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

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
    # ---- v8 semantic signals (Phase 2 — A路线) ----------------------
    # Three lightweight per-frame semantic features the client can fill
    # in cheaply (iOS: Vision; Web: MediaPipe Tasks Vision + canvas).
    # Each is independent & optional — backend treats missing values as
    # "no information" and falls back to the LLM's own inspection.
    person_box: Optional[list[float]] = Field(
        default=None,
        description=(
            "Largest detected person rectangle as [x, y, w, h] in 0..1 frame "
            "coordinates. None when no person detected (or detector unavailable)."
        ),
    )
    saliency_quadrant: Optional[str] = Field(
        default=None,
        description=(
            "Which quadrant of the frame holds the visual centre of mass: "
            "'top_left'|'top_right'|'bottom_left'|'bottom_right'|'center'. "
            "Lets the LLM reason about 'where is the busy part of this view'."
        ),
    )
    horizon_tilt_deg: Optional[float] = Field(
        default=None, ge=-90, le=90,
        description=(
            "Detected horizon tilt in degrees, positive = right side higher. "
            "When the device's roll matches the visible horizon (small "
            "magnitude), the frame is well-levelled."
        ),
    )
    # ---- v10 lens / tilt signals (Phase 4 — 焦段+俯仰) -------------
    # Two normalised y coordinates from the subject's pose keypoints
    # (nose & midpoint of ankles). Combined with pitch_deg they let
    # scene_aggregate decide whether the user should crouch / lift.
    pose_nose_y: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Detected y of subject's nose in [0,1] frame coords (top-left "
            "origin). Lets the prompt builder reason about head placement "
            "vs camera pitch. Source: MediaPipe Pose / Apple Vision."
        ),
    )
    pose_ankle_y: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Detected y of midpoint between left+right ankle keypoints. "
            "pose_ankle_y - pose_nose_y is the on-screen body height "
            "fraction → distance estimate (K/heightRatio model)."
        ),
    )
    # ---- v10.1 face + horizon refinements ---------------------------
    # face_height_ratio gives a much sharper distance estimate when the
    # subject is framed tighter than half-body (the pose-based ratio
    # collapses once ankles leave the frame). horizon_y cross-validates
    # the pitch/pose-center based tilt advice.
    face_height_ratio: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Detected face bounding-box height / frame height in [0,1]. "
            "When non-null, scene_aggregate prefers this over the body "
            "height ratio for distance estimation (K_face ≈ 0.18 m)."
        ),
    )
    horizon_y: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Vertical position of the detected horizon midpoint in [0,1] "
            "top-left frame coords. <0.45 ≈ camera looking down (a lot "
            "of ground); >0.55 ≈ camera looking up (a lot of sky). "
            "Used as a cross-check for the gyro pitch_deg signal."
        ),
    )
    # ---- v10.2 multi-person disambiguation -------------------------
    person_count: Optional[int] = Field(
        default=None, ge=0,
        description=(
            "Number of people detected in this frame (face or body). "
            "Used by scene_aggregate to decide whether to trust the "
            "single-subject pose / face metrics blindly. >1 means the "
            "client picked one as 'subject' via the consistency rule."
        ),
    )
    subject_box: Optional[list[float]] = Field(
        default=None,
        description=(
            "Chosen subject bounding box [x,y,w,h] in [0,1] top-left "
            "coords. When multi-person, this is the box the client "
            "voted as the most-likely intended subject (largest, "
            "most central, and consistent across nearby frames). "
            "person_box / pose_*_y / face_height_ratio above all refer "
            "to this same subject."
        ),
    )
    # ---- v11 color science / lighting --------------------------------
    rgb_mean: Optional[list[float]] = Field(
        default=None, min_length=3, max_length=3,
        description=(
            "Mean linear-ish R,G,B (0..255) of the frame, sampled by "
            "the client. Feeds color_science.estimate_cct_k for "
            "color-temperature estimation. May exclude saturated "
            "highlights & deep shadows for accuracy."
        ),
    )
    luma_p05: Optional[float] = Field(
        default=None, ge=0, le=255,
        description="5th percentile of luma (0..255) — shadow floor."
    )
    luma_p95: Optional[float] = Field(
        default=None, ge=0, le=255,
        description="95th percentile of luma — highlight ceiling."
    )
    highlight_clip_pct: Optional[float] = Field(
        default=None, ge=0, le=1,
        description="Fraction of pixels with luma >= 250 (clipped highlights)."
    )
    shadow_clip_pct: Optional[float] = Field(
        default=None, ge=0, le=1,
        description="Fraction of pixels with luma <= 5 (crushed shadows)."
    )
    saturation_mean: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Mean HSV saturation across the frame in [0,1]. Used by "
            "style_palette to compare to per-style targets."
        ),
    )
    # ---- v12 EXIF / camera intrinsics --------------------------------
    focal_length_mm: Optional[float] = Field(
        default=None, ge=0,
        description=(
            "Physical focal length in millimetres from EXIF. Combined "
            "with sensor_width_mm gives the true horizontal FOV which "
            "calibrates K_face/K_body for distance estimation."
        ),
    )
    focal_length_35mm_eq: Optional[float] = Field(
        default=None, ge=0,
        description="35mm-equivalent focal length from EXIF, when present."
    )
    sensor_width_mm: Optional[float] = Field(
        default=None, ge=0,
        description=(
            "Physical sensor width in millimetres. iPhone main cameras "
            "are typically 6-7mm; ultrawide ~3.5mm; tele ~5mm."
        ),
    )
    # ---- v12 horizon triangulation -----------------------------------
    horizon_y_vision: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Horizon midpoint y from device-pose (Vision) inference. "
            "Independent of horizon_y above which uses image gradients; "
            "scene_aggregate triangulates the two."
        ),
    )
    horizon_y_gravity: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Horizon midpoint y derived purely from device gravity "
            "(ARKit camera.transform). Acts as the third vote in "
            "scene_aggregate._vote_horizon for a 2-of-3 majority."
        ),
    )
    sky_mask_top_pct: Optional[float] = Field(
        default=None, ge=0, le=1,
        description=(
            "Fraction of pixels in the top half of the frame classified "
            "as sky (high luma + slightly blue). When this ratio is "
            "high, horizon_y is trusted; when zero (indoor / no sky) "
            "horizon facts are suppressed."
        ),
    )
    # ---- v12 fine-grained pose ---------------------------------------
    shoulder_tilt_deg: Optional[float] = Field(
        default=None, ge=-90, le=90,
        description="Subject's shoulder line tilt vs horizontal (+ = right shoulder higher)."
    )
    hip_offset_x: Optional[float] = Field(
        default=None, ge=-1, le=1,
        description="Hip-midpoint x offset from frame centre in [-1, +1] (- = left)."
    )
    chin_forward: Optional[float] = Field(
        default=None, ge=-1, le=1,
        description=(
            "Chin protrusion vs neck axis in normalised units. > 0.10 "
            "= 探头 / 下颌前伸 (camera-friendly but unflattering side-on)."
        ),
    )
    spine_curve: Optional[float] = Field(
        default=None, ge=-1, le=1,
        description=(
            "Spine curvature: triangle area of (head, mid-back, hip) "
            "normalised by body height. > 0.05 = noticeably bent / "
            "slouching."
        ),
    )
    # ---- v9 foreground / depth signals (Phase 3 — 三层构图) ---------
    # Per-frame inputs that feed FOREGROUND DOCTRINE in the prompt
    # builder. All optional. Backends downstream:
    #   - object detector (MediaPipe / Apple Vision) → foreground_candidates
    #   - monocular depth (MiDaS) or LiDAR (AVDepthData) → depth_layers
    foreground_candidates: Optional[list["ForegroundCandidate"]] = Field(
        default=None,
        description=(
            "Detected objects in this frame that could plausibly serve as "
            "a depth-layer foreground (plants, fences, doorways, leading "
            "lines, etc.). Capped at the top 3 by area; nil when nothing "
            "useful was found or detector unavailable."
        ),
    )
    depth_layers: Optional["DepthLayers"] = Field(
        default=None,
        description=(
            "What fraction of this frame's pixels fall into near (< ~1.5m, "
            "true foreground territory), mid (~1.5-5m, subject zone), and "
            "far (> ~5m, environment) buckets. Source: MiDaS monocular "
            "depth on web/iOS, AVDepthData on iOS Pro. Used by the LLM "
            "to decide whether the scene actually has a usable foreground "
            "layer (need near_pct >= ~5% to virtualise into bokeh)."
        ),
    )


class ForegroundCandidate(BaseModel):
    """A single object detected in a keyframe that *could* be staged
    as a near-foreground element (the FOREGROUND DOCTRINE rule in the
    prompt builder picks from these instead of guessing)."""
    label: str = Field(
        description=(
            "Object label — typically a normalised COCO/Vision class "
            "('plant', 'tree', 'fence', 'doorway', 'bench', 'railing', "
            "'flower', 'window', etc.). Coarse is fine — the LLM only "
            "needs to know roughly what's there."
        ),
    )
    box: list[float] = Field(
        description=(
            "[x, y, w, h] in 0..1 frame coords (top-left origin) — same "
            "convention as person_box."
        ),
    )
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    estimated_distance_m: Optional[float] = Field(
        default=None, ge=0.05, le=50,
        description=(
            "Optional depth estimate when ``DepthLayers`` is also "
            "available — lets the prompt builder filter to '<1.5m, "
            "actually virtualisable' candidates only."
        ),
    )


class DepthLayers(BaseModel):
    """Coarse 3-bucket histogram of monocular / sensor depth in a frame.

    Numbers are area fractions in [0, 1] and should sum to ~1 (small
    rounding tolerated). Used by the LLM to decide whether the scene
    *physically supports* a foreground layer at all — if near_pct < 5%
    the scene is open and rationale should say so honestly.
    """
    near_pct: float = Field(ge=0, le=1, description="< ~1.5m: true foreground territory")
    mid_pct: float = Field(ge=0, le=1, description="~1.5-5m: subject zone")
    far_pct: float = Field(ge=0, le=1, description="> ~5m: environment / sky")
    source: str = Field(
        description=(
            "How the depth was computed: 'midas_web' | 'midas_ios' | "
            "'avdepth_lidar' | 'avdepth_dual'. Lets the LLM weight the "
            "evidence (LiDAR > monocular)."
        ),
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
    heading_source: Literal["sensor", "fake", "unknown"] = "unknown"
    """v9 UX polish #16 — Where azimuth values came from.

    - ``sensor``: real device gyroscope / compass. Trust direction-based
      reranking and the ``best_direction`` rationale.
    - ``fake``: client fell back to mouse / synthetic input (desktop demo,
      iOS without orientation permission). Direction-dependent reasoning
      should be suppressed or caveated by the LLM.
    - ``unknown``: pre-v9 clients that didn't send the field.
    """
    walk_segment: Optional["WalkSegment"] = None
    """Optional opt-in 10-20 s walk after the standing pan. Enables the
    SfM/VIO branch of the shot-position fusion (see
    ``services.walk_geometry``)."""

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


class ShotPositionKind(str, Enum):
    """Two coordinate systems for a recommended shot position.

    - ``relative``: the legacy / default — polar coordinates anchored at
      the user's current standing point. Distance is bounded to 20 m.
    - ``absolute``: world coordinates (lat/lon). Used by POI-derived and
      SfM/VIO-derived candidates that may be 50-200 m from the user.
    """
    relative = "relative"
    absolute = "absolute"
    indoor = "indoor"


PositionSource = Literal[
    "llm_relative",   # synthesised from the LLM's relative angle
    "poi_kb",         # POI from the local seeded knowledge base
    "poi_online",     # POI fetched live from AMap / OSM (cached after)
    "poi_ugc",        # user-confirmed spot from feedback (W2)
    "poi_indoor",     # indoor POI from AMap Indoor / Mapbox Indoor (W1.2)
    "sfm_ios",        # ARKit/VIO trajectory candidate (high precision)
    "sfm_web",        # WebXR / DeviceMotion candidate (lower precision)
    "triangulated",   # remote 3D point recovered via two-view triangulation (W4)
    "recon3d",        # full SfM model output (W9)
]


class IndoorContext(BaseModel):
    """Where in a building this shot lives (W1.2). Replaces the geo map
    rendering with a floor-plan thumbnail + hotspot on the client."""
    building_id: str
    building_name_zh: Optional[str] = None
    floor: Optional[str] = None
    """Floor label, e.g. 'L2', 'B1', '一楼大堂'."""
    hotspot_label_zh: Optional[str] = None
    image_ref: Optional[str] = None
    """URL or asset key for the floor-plan thumbnail."""
    x_floor: Optional[float] = Field(default=None, ge=0, le=1)
    y_floor: Optional[float] = Field(default=None, ge=0, le=1)
    """Normalised position on the floor plan, top-left origin."""


class WalkRouteStep(BaseModel):
    """One leg of a walking-route narration (W3)."""
    instruction_zh: str
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    polyline: Optional[str] = None


class WalkRoute(BaseModel):
    """Walking directions to an absolute shot position (W3)."""
    distance_m: float = Field(ge=0)
    duration_min: float = Field(ge=0)
    polyline: str = ""
    """Encoded polyline (AMap / Google polyline-style)."""
    steps: list[WalkRouteStep] = Field(default_factory=list)
    provider: str = "amap"


class ShotPosition(BaseModel):
    """Unified shot-position descriptor that supports both the legacy
    relative polar form and the new absolute world-coords form.

    The client renders compass arrows for ``relative`` and a map pin
    with walking distance for ``absolute``. ``source`` and ``confidence``
    drive ranking inside ``shot_fusion`` and let the UI badge each
    recommendation honestly ("权威机位" vs "估算机位").
    """
    kind: ShotPositionKind
    # ---- relative subset ----
    azimuth_deg: Optional[float] = Field(default=None, ge=0, lt=360)
    distance_m: Optional[float] = Field(default=None, ge=0.3, le=200)
    pitch_deg: Optional[float] = None
    height_hint: Optional[HeightHint] = None
    # ---- absolute subset ----
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    height_above_ground_m: Optional[float] = Field(default=None, ge=-50, le=200)
    facing_deg: Optional[float] = Field(default=None, ge=0, lt=360)
    walk_distance_m: Optional[float] = Field(default=None, ge=0, le=2000)
    bearing_from_user_deg: Optional[float] = Field(default=None, ge=0, lt=360)
    est_walk_minutes: Optional[float] = Field(default=None, ge=0, le=60)
    # ---- common ----
    source: PositionSource = "llm_relative"
    confidence: float = Field(ge=0, le=1, default=0.5)
    walkability_note_zh: Optional[str] = None
    name_zh: Optional[str] = None
    """Human-readable label, e.g. POI name or '漫游机位 #2'."""
    indoor: Optional["IndoorContext"] = None
    """Populated when ``kind == indoor`` (W1.2)."""
    walk_route: Optional["WalkRoute"] = None
    """Populated by route_planner when distance > threshold (W3)."""

    @model_validator(mode="after")
    def _kind_subset(self) -> "ShotPosition":
        if self.kind == ShotPositionKind.relative:
            if self.azimuth_deg is None or self.distance_m is None:
                raise ValueError("relative ShotPosition needs azimuth_deg and distance_m")
            if self.distance_m > 20:
                raise ValueError("relative ShotPosition.distance_m must be <= 20")
        elif self.kind == ShotPositionKind.indoor:
            if self.indoor is None:
                raise ValueError("indoor ShotPosition needs indoor context")
        else:
            if self.lat is None or self.lon is None:
                raise ValueError("absolute ShotPosition needs lat and lon")
        return self


class WalkPose(BaseModel):
    """One sample from the optional walk-segment trajectory.

    Origin is the user's initial GeoFix at the start of the walk.
    Coordinates are in the local ENU frame (x=east, y=north, z=up,
    metres). Quaternion is the device camera orientation.
    """
    t_ms: int = Field(ge=0, description="Milliseconds since walk segment start.")
    x: float
    y: float
    z: float
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0


class WalkSegment(BaseModel):
    """User-opt-in 10-20 s walk after the standing pan, used by the
    backend to derive far-away ``absolute`` shot candidates.

    iOS supplies this from ``ARFrame.camera.transform`` (true VIO,
    centimetre-grade); Web supplies it from WebXR if available, else
    from DeviceMotion double-integration plus keyframe matching
    (lower precision, ``confidence`` is downgraded by walk_geometry).
    """
    source: Literal["arkit", "webxr", "devicemotion"]
    initial_heading_deg: Optional[float] = Field(
        default=None, ge=0, lt=360,
        description=(
            "Compass heading at walk start so the local ENU frame can be "
            "rotated into true world coordinates. Required to convert "
            "(x,y) into (lat,lon)."
        ),
    )
    poses: list[WalkPose] = Field(default_factory=list)
    sparse_points: Optional[list[list[float]]] = Field(
        default=None,
        description=(
            "Optional SfM sparse point cloud as [[x,y,z], ...] in the "
            "same ENU frame as poses. Currently only iOS / WebXR clients "
            "provide this."
        ),
    )
    gps_track: Optional[list["GpsSample"]] = Field(
        default=None,
        description=(
            "Optional GPS samples taken during the walk (Web only — iOS "
            "uses ARKit fused poses). Backend uses these to fit + nudge "
            "the IMU-derived path. (W5.1)"
        ),
    )
    keyframes_b64: Optional[list[dict]] = Field(
        default=None,
        description=(
            "Optional small JPEGs sampled at 1 Hz during the walk for ORB "
            "correction (W5.2). Each item is "
            "``{t_ms: int, dataUrl: str}`` — the dataUrl is a "
            "data:image/jpeg;base64,... payload kept tiny by the client."
        ),
    )


class GpsSample(BaseModel):
    """One GPS reading taken during a Web walk segment (W5)."""
    t_ms: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: Optional[float] = Field(default=None, ge=0)


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

    # ----- Unified position (v13 — three-source fusion) ---------------
    position: Optional["ShotPosition"] = None
    """Unified shot-position descriptor. ``relative`` mirrors ``angle``
    and is always set so old clients keep working; ``absolute`` is set
    when the shot was sourced from POI knowledge or SfM/VIO. The result
    UI uses ``position.kind`` to pick compass-vs-map rendering."""

    # ----- Foreground / depth-layer strategy (v9) ---------------------
    foreground: Optional["ShotForeground"] = None
    """Three-layer composition strategy: which kind of foreground to
    use, where to find it (cited by azimuth + canvas quadrant), how to
    physically nudge the user to bring it into frame. Filled by the
    LLM under FOREGROUND DOCTRINE. None = scene has no usable foreground
    (e.g. open beach with nothing within 1.5m), in which case rationale
    must explicitly say so."""

    # ----- Style intent (filled by backend, not LLM) ------------------
    style_match: Optional["ShotStyleMatch"] = None
    """Which user-picked style this shot was tuned toward, plus the
    recommended camera ranges for that style. Populated by
    ``style_compliance_service`` after validate_and_clamp; lets the
    result UI show a "风格 X · 推荐 Y · 实际 Z ✓" panel per shot.
    Only present when the user actually picked a style on Step 3."""

    # ----- iPhone-specific photo tips ---------------------------------
    iphone_tips: list[str] = Field(default_factory=list)
    """2-3 short Chinese sentences specific to shooting this on an
    iPhone. Examples:
      - "切到 2x 长焦镜头拍人像，避免主摄数码裁剪降低画质"
      - "iPhone 物理光圈固定 f/1.78，要 f/4 的深景深建议拍后用人像模式"
      - "ISO 800 噪点可见，可以靠近主体让 ISO 自然降到 200"
    Filled by the LLM (prompt-driven) so it stays scene-aware. Always
    rendered alongside the iphone_apply_plan in both Web and iOS UIs."""


class ShotStyleMatch(BaseModel):
    """Per-shot style intent + compliance result.

    Surfaces three things to the result UI:
      1. Which of the user's picked styles this shot was tuned for
         (style_id + Chinese label).
      2. The recommended numeric ranges that drove tuning.
      3. Whether the LLM's original output was already in those ranges,
         or whether the backend had to clamp values.

    Empty (None on ShotRecommendation) when the user didn't pick any
    recognised style on Step 3 — in that case there's nothing to compare
    against and the UI should hide the badge.
    """

    style_id: str
    label_zh: str
    white_balance_k_range: tuple[int, int]
    focal_length_mm_range: tuple[float, float]
    ev_range: tuple[float, float]
    in_range: bool
    """True iff the LLM's original (pre-clamp) values were all already
    inside the recommended ranges. False when at least one knob was
    auto-corrected — the rationale will also have a ``（已按...风格自动
    校准...）`` suffix in that case so the user understands the change."""
    fixes: list[dict] = Field(default_factory=list)
    """When ``in_range`` is False, list of ``{knob, from, to}`` records
    describing each clamp. Empty otherwise."""


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


class ReferenceFingerprint(BaseModel):
    """Style fingerprint extracted from a single user-provided reference
    image (W6). Drives prompt injection + per-shot palette compliance."""
    index: int = Field(ge=0, description="Position in the user's reference list, 0-based.")
    palette: list[str] = Field(
        default_factory=list,
        description="Top 5 hex colours, e.g. ['#2a1f1a','#c08a55',...].",
    )
    palette_weights: list[float] = Field(
        default_factory=list,
        description="Per-colour weight in [0,1], same length as palette.",
    )
    contrast_band: str = "mid"
    """One of low | mid | high — derived from luma p5/p95 spread."""
    saturation_band: str = "mid"
    """One of low | mid | high — derived from mean HSV saturation."""
    mood_keywords: list[str] = Field(default_factory=list)
    embedding_dims: Optional[int] = Field(
        default=None, ge=0,
        description="Dimensionality of the embedding stored server-side.",
    )
    thumbnail_ref: Optional[str] = None


class FarPoint(BaseModel):
    """A 3D point recovered from two-view triangulation (W4)."""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    height_m: Optional[float] = Field(default=None, ge=-50, le=2000)
    confidence: float = Field(ge=0, le=1, default=0.5)
    observed_in_azimuth_deg: float = Field(ge=0, lt=360)
    label_zh: Optional[str] = None


class TimeRecommendation(BaseModel):
    """Best historical time-of-day for shooting at a given location (W7)."""
    best_hour_local: int = Field(ge=0, le=23)
    score: float = Field(ge=0, le=5)
    sample_n: int = Field(ge=0)
    blurb_zh: str = ""
    runner_up_hour_local: Optional[int] = Field(default=None, ge=0, le=23)
    minutes_until_best: Optional[float] = Field(default=None, ge=-1440, le=1440)


class SparseModel(BaseModel):
    """Output of recon3d worker (W9). Lightweight summary suitable for
    embedding in /analyze response or polled separately."""
    job_id: str
    points_count: int = Field(ge=0)
    cameras_count: int = Field(ge=0)
    scale_m_per_unit: float = 1.0
    bbox_lat: Optional[list[float]] = None
    bbox_lon: Optional[list[float]] = None
    thumbnail_ref: Optional[str] = None


class AnalyzeResponse(BaseModel):
    scene: SceneSummary
    shots: list[ShotRecommendation]
    style_inspiration: Optional[StyleInspiration] = None
    environment: Optional[EnvironmentSnapshot] = None
    """Echoed back so the result UI can draw a sun compass / golden-hour
    badge. Only populated when the request supplied a geo fix."""
    time_recommendation: Optional[TimeRecommendation] = None
    """Populated by services.time_optimal when geo + history available (W7)."""
    reference_fingerprints: list[ReferenceFingerprint] = Field(default_factory=list)
    """Populated by services.style_extract when user uploaded refs (W6)."""
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


# Resolve the forward reference inside ShotRecommendation.style_match —
# ShotStyleMatch is defined after ShotRecommendation so Pydantic needs an
# explicit rebuild to bind the string annotation to the actual class.
class ShotForeground(BaseModel):
    """Foreground / three-layer composition plan for a single shot.

    Photographers stage three depth layers — close foreground (0.2-1.5m,
    the "bokeh layer" or "framing"), midground (the subject), background
    (environment / mood). Strong photos almost always have an intentional
    foreground; weak amateur photos lack one. We make the LLM commit to
    one of four strategies per shot, plus give the user a *physical*
    nudge ("step left, lower the phone, place these branches in the
    bottom-left quadrant") so it's actionable.
    """
    layer: Literal[
        "bokeh_plant",     # 树叶 / 花 / 草 → 大光圈虚化成色块
        "natural_frame",   # 门洞 / 树枝 / 栏杆 → 框住主体
        "leading_line",    # 栏杆 / 台阶 / 地砖 → 引导视线到主体
        "none",            # 场景没有可用前景；坦白告诉用户即可
    ]
    suggestion_zh: str = Field(
        description=(
            "1-2 sentence Chinese, second-person, physical nudge: "
            "\"侧身半步，把这棵树的枝叶放到画面左下角，让前景虚成绿色色块\". "
            "Must mention an actionable body/phone movement, not "
            "abstract advice like '加点前景'."
        ),
    )
    source_azimuth_deg: Optional[float] = Field(
        default=None, ge=0, lt=360,
        description=(
            "Which azimuth's keyframe the LLM saw this foreground in. "
            "The result UI uses this to highlight the right thumbnail."
        ),
    )
    canvas_quadrant: Optional[str] = Field(
        default=None,
        description=(
            "Where in the resulting frame the foreground should sit: "
            "top_left | top_right | bottom_left | bottom_right | "
            "left_edge | right_edge | bottom_edge | top_edge. Lets the "
            "result card draw a hint overlay on the mock-up."
        ),
    )
    estimated_distance_m: Optional[float] = Field(
        default=None, ge=0.1, le=10,
        description=(
            "LLM's estimate of how far the foreground element is. "
            "< 1.5m means it'll actually blur on a phone main lens; "
            ">= 1.5m means it'll just be a sharp distraction — should "
            "trigger a 'step closer to the foreground' nudge."
        ),
    )


ShotRecommendation.model_rebuild()
FrameMeta.model_rebuild()
WalkSegment.model_rebuild()
CaptureMeta.model_rebuild()
