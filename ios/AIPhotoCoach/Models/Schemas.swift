import Foundation

// Mirrors backend/app/models/schemas.py and shared/schema/analyze.openapi.yaml.
// Keep these enums/cases byte-for-byte equal to the backend.

enum QualityMode: String, Codable, CaseIterable, Sendable, Hashable {
    case fast, high
}

enum SceneMode: String, Codable, CaseIterable, Sendable, Hashable {
    case portrait
    case closeup
    case fullBody = "full_body"
    case documentary
    case scenery
    case lightShadow = "light_shadow"

    var displayName: String {
        switch self {
        case .portrait: return "人像"
        case .closeup: return "特写"
        case .fullBody: return "全身"
        case .documentary: return "人文"
        case .scenery: return "风景"
        case .lightShadow: return "光影"
        }
    }

    var blurb: String {
        switch self {
        case .portrait: return "半身或全身，人物为主"
        case .closeup: return "脸 / 上半身 / 神态特写"
        case .fullBody: return "完整人物 + 背景"
        case .documentary: return "抓拍质感 + 故事感"
        case .scenery: return "纯环境出片，可不出人"
        case .lightShadow: return "强对比光影 / 剪影 / 长影 / 光柱"
        }
    }

    var allowsZeroPeople: Bool { self == .scenery }

    /// Real-world sun + weather data powers the style feasibility check
    /// (e.g. "clean bright" needs >5000K + ev>=0; warn user up front if
    /// their environment can't pull off the chosen style). All scene modes
    /// benefit, so this is `true` everywhere now. Permission stays opt-in.
    var needsSunInfo: Bool { true }
}

enum Lighting: String, Codable, Sendable, Hashable {
    case goldenHour = "golden_hour"
    case blueHour = "blue_hour"
    case harshNoon = "harsh_noon"
    case overcast
    case shade
    case indoorWarm = "indoor_warm"
    case indoorCool = "indoor_cool"
    case lowLight = "low_light"
    case backlight
    case mixed
}

enum CompositionType: String, Codable, Sendable, Hashable {
    case ruleOfThirds = "rule_of_thirds"
    case leadingLine = "leading_line"
    case symmetry
    case frameWithinFrame = "frame_within_frame"
    case negativeSpace = "negative_space"
    case centered
    case diagonal
    case goldenRatio = "golden_ratio"
}

enum HeightHint: String, Codable, Sendable, Hashable {
    case low, eyeLevel = "eye_level", high, overhead
}

enum Layout: String, Codable, Sendable, Hashable {
    case single
    case sideBySide = "side_by_side"
    case highLowOffset = "high_low_offset"
    case triangle
    case line
    case cluster
    case diagonal
    case vFormation = "v_formation"
    case circle
    case custom
}

enum IphoneLens: String, Codable, Sendable, Hashable {
    case ultrawide = "ultrawide_0_5x"
    case wide = "wide_1x"
    case tele2x = "tele_2x"
    case tele3x = "tele_3x"
    case tele5x = "tele_5x"
}

enum Difficulty: String, Codable, Sendable, Hashable {
    case easy, medium, hard
}

struct FrameMeta: Codable, Sendable, Hashable {
    let index: Int
    let azimuthDeg: Double
    let pitchDeg: Double
    let rollDeg: Double
    let timestampMs: Int
    let ambientLux: Double?
    /// Client-side visual signals (v6): mean BT-601 luma in [0,1],
    /// horizontal-gradient sharpness proxy, Vision face-hit flag.
    let blurScore: Double?
    let meanLuma: Double?
    let faceHit: Bool?

    /// v8 semantic signals (Phase 2 — A 路线). All optional. iOS fills
    /// these from Vision (`VNDetectHumanRectangles` / `VNGenerateAttention
    /// BasedSaliency` / `VNDetectHorizon`); web from MediaPipe Tasks +
    /// canvas. Backend treats nil as "no signal" not "negative signal".
    let personBox: [Double]?           // [x, y, w, h] in 0..1
    let saliencyQuadrant: String?      // top_left | top_right | bottom_left | bottom_right | center
    let horizonTiltDeg: Double?        // -90..90, + = right side higher
    /// v9 Phase 3 — three-layer composition signals. Drive
    /// FOREGROUND DOCTRINE in the prompt builder. Both nullable per
    /// frame; backend treats absence as "no client signal".
    let foregroundCandidates: [ForegroundCandidate]?
    let depthLayers: DepthLayers?
    /// v10 — subject pose anchor points used by scene_aggregate to
    /// recommend lens (distance-based) and tilt (crouch/lift hints).
    let poseNoseY: Double?
    let poseAnkleY: Double?
    /// v10.1 — face bbox height / frame height. Sharper distance
    /// estimate than body ratio for tight portraits.
    let faceHeightRatio: Double?
    /// v10.1 — horizon midpoint y in [0,1] top-left coords.
    let horizonY: Double?
    /// v10.2 — multi-person disambiguation: how many people detected
    /// in this frame, and which one the client chose as the subject.
    let personCount: Int?
    let subjectBox: [Double]?
    /// v11 — color science / lighting stats per frame.
    let rgbMean: [Double]?
    let lumaP05: Double?
    let lumaP95: Double?
    let highlightClipPct: Double?
    let shadowClipPct: Double?
    let saturationMean: Double?
    /// v12 — EXIF-derived camera intrinsics for accurate distance/lens
    /// reasoning. Read from the captured CGImage's metadata when
    /// available; nil on Web (no EXIF for canvas captures).
    let focalLengthMm: Double?
    let focalLength35mmEq: Double?
    let sensorWidthMm: Double?
    /// v12 — horizon triangulation + fine-grained pose.
    let horizonYVision: Double?
    let horizonYGravity: Double?
    let skyMaskTopPct: Double?
    let shoulderTiltDeg: Double?
    let hipOffsetX: Double?
    let chinForward: Double?
    let spineCurve: Double?

    init(
        index: Int,
        azimuthDeg: Double,
        pitchDeg: Double,
        rollDeg: Double,
        timestampMs: Int,
        ambientLux: Double? = nil,
        blurScore: Double? = nil,
        meanLuma: Double? = nil,
        faceHit: Bool? = nil,
        personBox: [Double]? = nil,
        saliencyQuadrant: String? = nil,
        horizonTiltDeg: Double? = nil,
        foregroundCandidates: [ForegroundCandidate]? = nil,
        depthLayers: DepthLayers? = nil,
        poseNoseY: Double? = nil,
        poseAnkleY: Double? = nil,
        faceHeightRatio: Double? = nil,
        horizonY: Double? = nil,
        personCount: Int? = nil,
        subjectBox: [Double]? = nil,
        rgbMean: [Double]? = nil,
        lumaP05: Double? = nil,
        lumaP95: Double? = nil,
        highlightClipPct: Double? = nil,
        shadowClipPct: Double? = nil,
        saturationMean: Double? = nil,
        focalLengthMm: Double? = nil,
        focalLength35mmEq: Double? = nil,
        sensorWidthMm: Double? = nil,
        horizonYVision: Double? = nil,
        horizonYGravity: Double? = nil,
        skyMaskTopPct: Double? = nil,
        shoulderTiltDeg: Double? = nil,
        hipOffsetX: Double? = nil,
        chinForward: Double? = nil,
        spineCurve: Double? = nil
    ) {
        self.index = index
        self.azimuthDeg = azimuthDeg
        self.pitchDeg = pitchDeg
        self.rollDeg = rollDeg
        self.timestampMs = timestampMs
        self.ambientLux = ambientLux
        self.blurScore = blurScore
        self.meanLuma = meanLuma
        self.faceHit = faceHit
        self.personBox = personBox
        self.saliencyQuadrant = saliencyQuadrant
        self.horizonTiltDeg = horizonTiltDeg
        self.foregroundCandidates = foregroundCandidates
        self.depthLayers = depthLayers
        self.poseNoseY = poseNoseY
        self.poseAnkleY = poseAnkleY
        self.faceHeightRatio = faceHeightRatio
        self.horizonY = horizonY
        self.personCount = personCount
        self.subjectBox = subjectBox
        self.rgbMean = rgbMean
        self.lumaP05 = lumaP05
        self.lumaP95 = lumaP95
        self.highlightClipPct = highlightClipPct
        self.shadowClipPct = shadowClipPct
        self.saturationMean = saturationMean
        self.focalLengthMm = focalLengthMm
        self.focalLength35mmEq = focalLength35mmEq
        self.sensorWidthMm = sensorWidthMm
        self.horizonYVision = horizonYVision
        self.horizonYGravity = horizonYGravity
        self.skyMaskTopPct = skyMaskTopPct
        self.shoulderTiltDeg = shoulderTiltDeg
        self.hipOffsetX = hipOffsetX
        self.chinForward = chinForward
        self.spineCurve = spineCurve
    }

    enum CodingKeys: String, CodingKey {
        case index
        case azimuthDeg = "azimuth_deg"
        case pitchDeg = "pitch_deg"
        case rollDeg = "roll_deg"
        case timestampMs = "timestamp_ms"
        case ambientLux = "ambient_lux"
        case blurScore = "blur_score"
        case meanLuma = "mean_luma"
        case faceHit = "face_hit"
        case personBox = "person_box"
        case saliencyQuadrant = "saliency_quadrant"
        case horizonTiltDeg = "horizon_tilt_deg"
        case foregroundCandidates = "foreground_candidates"
        case depthLayers = "depth_layers"
        case poseNoseY  = "pose_nose_y"
        case poseAnkleY = "pose_ankle_y"
        case faceHeightRatio = "face_height_ratio"
        case horizonY = "horizon_y"
        case personCount = "person_count"
        case subjectBox  = "subject_box"
        case rgbMean = "rgb_mean"
        case lumaP05 = "luma_p05"
        case lumaP95 = "luma_p95"
        case highlightClipPct = "highlight_clip_pct"
        case shadowClipPct    = "shadow_clip_pct"
        case saturationMean   = "saturation_mean"
        case focalLengthMm    = "focal_length_mm"
        case focalLength35mmEq = "focal_length_35mm_eq"
        case sensorWidthMm    = "sensor_width_mm"
        case horizonYVision   = "horizon_y_vision"
        case horizonYGravity  = "horizon_y_gravity"
        case skyMaskTopPct    = "sky_mask_top_pct"
        case shoulderTiltDeg  = "shoulder_tilt_deg"
        case hipOffsetX       = "hip_offset_x"
        case chinForward      = "chin_forward"
        case spineCurve       = "spine_curve"
    }
}

/// Mirrors backend ``ForegroundCandidate``. iOS clients fill from
/// VNGenerateObjectnessBasedSaliency + VNClassifyImage (or LiDAR-aware
/// AVDepthData → see ``DepthFusion``).
struct ForegroundCandidate: Codable, Sendable, Hashable {
    let label: String                 // potted_plant / tree / fence / ...
    let box: [Double]                 // [x, y, w, h] in 0..1, top-left origin
    let confidence: Double?
    let estimatedDistanceM: Double?

    enum CodingKeys: String, CodingKey {
        case label, box, confidence
        case estimatedDistanceM = "estimated_distance_m"
    }
}

/// Mirrors backend ``DepthLayers``. iOS clients fill from MiDaS CoreML
/// (``midas_ios``) or ``AVCaptureDepthDataOutput`` (``avdepth_lidar`` /
/// ``avdepth_dual``).
struct DepthLayers: Codable, Sendable, Hashable {
    let nearPct: Double               // < ~1.5m: true foreground territory
    let midPct: Double                // ~1.5-5m: subject zone
    let farPct: Double                // > ~5m: environment / sky
    let source: String                // midas_ios | avdepth_lidar | avdepth_dual

    enum CodingKeys: String, CodingKey {
        case nearPct = "near_pct"
        case midPct  = "mid_pct"
        case farPct  = "far_pct"
        case source
    }
}

/// Optional location attached when the user explicitly opts in. Used by
/// the analyze pipeline to inject ENVIRONMENT FACTS (sun azimuth /
/// altitude / phase) into the LLM prompt — invaluable for `light_shadow`
/// scene mode but harmless to include for any mode.
struct GeoFix: Codable, Sendable, Hashable {
    let lat: Double
    let lon: Double
    let accuracyM: Double?
    let timestamp: Date?

    enum CodingKeys: String, CodingKey {
        case lat
        case lon
        case accuracyM = "accuracy_m"
        case timestamp
    }
}

/// Mirrors backend ``WalkPose``. One sample from the optional walk
/// segment, in local ENU metres relative to the user's initial GeoFix.
struct WalkPose: Codable, Sendable, Hashable {
    let tMs: Int
    let x: Double
    let y: Double
    let z: Double
    let qx: Double
    let qy: Double
    let qz: Double
    let qw: Double

    enum CodingKeys: String, CodingKey {
        case tMs = "t_ms"
        case x, y, z, qx, qy, qz, qw
    }

    init(tMs: Int, x: Double, y: Double, z: Double,
         qx: Double = 0, qy: Double = 0, qz: Double = 0, qw: Double = 1) {
        self.tMs = tMs; self.x = x; self.y = y; self.z = z
        self.qx = qx; self.qy = qy; self.qz = qz; self.qw = qw
    }
}

/// Mirrors backend ``WalkSegment``. Filled when the user opts into the
/// 10-20 s walk after the standing pan; iOS uses ARKit ``ARFrame.camera.transform``
/// for true VIO, so ``source`` is always ``.arkit`` here.
struct WalkSegment: Codable, Sendable, Hashable {
    enum Source: String, Codable, Sendable, Hashable {
        case arkit, webxr, devicemotion
    }
    let source: Source
    let initialHeadingDeg: Double?
    let poses: [WalkPose]
    let sparsePoints: [[Double]]?
    /// P2-12 — symmetric with the Web client: even on iOS we can
    /// optionally ship an extra GPS-track + 1Hz keyframes that the
    /// backend uses to rectify VIO drift over very long walks.
    let gpsTrack: [GpsSample]?
    let keyframesB64: [WalkKeyframe]?

    enum CodingKeys: String, CodingKey {
        case source
        case initialHeadingDeg = "initial_heading_deg"
        case poses
        case sparsePoints = "sparse_points"
        case gpsTrack = "gps_track"
        case keyframesB64 = "keyframes_b64"
    }
}

struct GpsSample: Codable, Sendable, Hashable {
    let tMs: Int
    let lat: Double
    let lon: Double
    let accuracyM: Double?

    enum CodingKeys: String, CodingKey {
        case tMs = "t_ms"
        case lat, lon
        case accuracyM = "accuracy_m"
    }
}

struct WalkKeyframe: Codable, Sendable, Hashable {
    let tMs: Int
    /// data:image/jpeg;base64,... payload kept tiny by sampling at 1 Hz.
    let dataUrl: String

    enum CodingKeys: String, CodingKey {
        case tMs = "t_ms"
        case dataUrl
    }
}

struct CaptureMeta: Codable, Sendable, Hashable {
    let personCount: Int
    let qualityMode: QualityMode
    let sceneMode: SceneMode
    let styleKeywords: [String]
    let frameMeta: [FrameMeta]
    let geo: GeoFix?
    let walkSegment: WalkSegment?

    init(
        personCount: Int,
        qualityMode: QualityMode,
        sceneMode: SceneMode = .portrait,
        styleKeywords: [String],
        frameMeta: [FrameMeta],
        geo: GeoFix? = nil,
        walkSegment: WalkSegment? = nil
    ) {
        self.personCount = personCount
        self.qualityMode = qualityMode
        self.sceneMode = sceneMode
        self.styleKeywords = styleKeywords
        self.frameMeta = frameMeta
        self.geo = geo
        self.walkSegment = walkSegment
    }

    enum CodingKeys: String, CodingKey {
        case personCount = "person_count"
        case qualityMode = "quality_mode"
        case sceneMode = "scene_mode"
        case styleKeywords = "style_keywords"
        case frameMeta = "frame_meta"
        case geo
        case walkSegment = "walk_segment"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.personCount = try c.decode(Int.self, forKey: .personCount)
        self.qualityMode = try c.decode(QualityMode.self, forKey: .qualityMode)
        self.sceneMode = try c.decodeIfPresent(SceneMode.self, forKey: .sceneMode) ?? .portrait
        self.styleKeywords = try c.decodeIfPresent([String].self, forKey: .styleKeywords) ?? []
        self.frameMeta = try c.decode([FrameMeta].self, forKey: .frameMeta)
        self.geo = try c.decodeIfPresent(GeoFix.self, forKey: .geo)
        self.walkSegment = try c.decodeIfPresent(WalkSegment.self, forKey: .walkSegment)
    }
}

struct SceneSummary: Codable, Sendable, Hashable {
    let type: String
    let lighting: Lighting
    let backgroundSummary: String
    let cautions: [String]
    /// LLM-derived light direction inferred from the video frames; populated
    /// even when the user hasn't shared their location, so the result UI can
    /// always render a (lower-confidence) light indicator.
    let visionLight: VisionLightHint?
    /// LLM self-assessment of whether the env video is even good enough to
    /// analyze. Drives the red CaptureAdvisoryBanner above the shot list.
    let captureQuality: CaptureQuality?

    init(
        type: String,
        lighting: Lighting,
        backgroundSummary: String,
        cautions: [String] = [],
        visionLight: VisionLightHint? = nil,
        captureQuality: CaptureQuality? = nil
    ) {
        self.type = type
        self.lighting = lighting
        self.backgroundSummary = backgroundSummary
        self.cautions = cautions
        self.visionLight = visionLight
        self.captureQuality = captureQuality
    }

    enum CodingKeys: String, CodingKey {
        case type
        case lighting
        case backgroundSummary = "background_summary"
        case cautions
        case visionLight = "vision_light"
        case captureQuality = "capture_quality"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.type              = try c.decode(String.self, forKey: .type)
        self.lighting          = try c.decode(Lighting.self, forKey: .lighting)
        self.backgroundSummary = try c.decode(String.self, forKey: .backgroundSummary)
        self.cautions          = try c.decodeIfPresent([String].self, forKey: .cautions) ?? []
        self.visionLight       = try c.decodeIfPresent(VisionLightHint.self, forKey: .visionLight)
        // ``capture_quality`` arrived in v6. Old responses won't have it.
        self.captureQuality    = try c.decodeIfPresent(CaptureQuality.self, forKey: .captureQuality)
    }
}

/// LLM-judged reasons the captured environment video may not be a good
/// basis for shot recommendations. Strings match backend enum values.
enum CaptureQualityIssue: String, Codable, Sendable, Hashable, CaseIterable {
    case clutteredBg        = "cluttered_bg"
    case noSubject          = "no_subject"
    case groundOnly         = "ground_only"
    case tooDark            = "too_dark"
    case tooManyPassersby   = "too_many_passersby"
    case blurry             = "blurry"
    case narrowPan          = "narrow_pan"

    var labelZh: String {
        switch self {
        case .clutteredBg:      return "背景太杂"
        case .noSubject:        return "没有可识别的主体"
        case .groundOnly:       return "镜头主要对着地面"
        case .tooDark:          return "环境太暗"
        case .tooManyPassersby: return "路人过多"
        case .blurry:           return "画面糊（设备晃动 / 失焦）"
        case .narrowPan:        return "环视范围太窄"
        }
    }
}

/// LLM self-assessment of the environment-video usability. Drives the
/// red advisory banner on the recommendation screen.
struct CaptureQuality: Codable, Sendable, Hashable {
    /// 1–5; <= 2 means "really shouldn't analyze".
    let score: Int
    let issues: [CaptureQualityIssue]
    let summaryZh: String?
    let shouldRetake: Bool

    enum CodingKeys: String, CodingKey {
        case score
        case issues
        case summaryZh = "summary_zh"
        case shouldRetake = "should_retake"
    }

    /// True when the banner should be loud / red, not amber.
    var isCritical: Bool { shouldRetake && score <= 2 }
}

/// LLM-derived dominant light direction read off the video frames. Lower
/// confidence than a real sun calculation but always available — backed by
/// the prompt's "ENVIRONMENT FACTS" hard requirement to fill it.
struct VisionLightHint: Codable, Sendable, Hashable {
    let directionDeg: Double?
    let quality: String?
    let confidence: Double?
    let notes: String?

    enum CodingKeys: String, CodingKey {
        case directionDeg = "direction_deg"
        case quality
        case confidence
        case notes
    }

    var qualityZh: String {
        switch quality {
        case "hard":  return "硬光"
        case "soft":  return "软光"
        case "mixed": return "半软半硬"
        default:      return "未知"
        }
    }

    var confidencePct: Int { Int(round((confidence ?? 0) * 100)) }
}

struct Angle: Codable, Sendable, Hashable {
    let azimuthDeg: Double
    let pitchDeg: Double
    let distanceM: Double
    let heightHint: HeightHint?

    enum CodingKeys: String, CodingKey {
        case azimuthDeg = "azimuth_deg"
        case pitchDeg = "pitch_deg"
        case distanceM = "distance_m"
        case heightHint = "height_hint"
    }
}

/// Mirrors backend ``ShotPosition``. ``kind == .relative`` carries the
/// legacy polar fields (azimuth + distance) anchored at the user; the
/// shot card renders a compass arrow. ``kind == .absolute`` carries
/// world coordinates plus walk distance — the card renders a MapKit
/// pin instead.
struct ShotPosition: Codable, Sendable, Hashable {
    enum Kind: String, Codable, Sendable, Hashable {
        case relative, absolute, indoor
    }
    enum Source: String, Codable, Sendable, Hashable {
        case llmRelative = "llm_relative"
        case poiKb       = "poi_kb"
        case poiOnline   = "poi_online"
        case poiUgc      = "poi_ugc"
        case poiIndoor   = "poi_indoor"
        case sfmIos      = "sfm_ios"
        case sfmWeb      = "sfm_web"
        case triangulated = "triangulated"
        case recon3d     = "recon3d"
    }
    let kind: Kind
    // relative
    let azimuthDeg: Double?
    let distanceM: Double?
    let pitchDeg: Double?
    let heightHint: HeightHint?
    // absolute
    let lat: Double?
    let lon: Double?
    let heightAboveGroundM: Double?
    let facingDeg: Double?
    let walkDistanceM: Double?
    let bearingFromUserDeg: Double?
    let estWalkMinutes: Double?
    // common
    let source: Source
    let confidence: Double
    let walkabilityNoteZh: String?
    let nameZh: String?
    let indoor: IndoorContext?
    let walkRoute: WalkRoute?

    enum CodingKeys: String, CodingKey {
        case kind
        case azimuthDeg            = "azimuth_deg"
        case distanceM             = "distance_m"
        case pitchDeg              = "pitch_deg"
        case heightHint            = "height_hint"
        case lat, lon
        case heightAboveGroundM    = "height_above_ground_m"
        case facingDeg             = "facing_deg"
        case walkDistanceM         = "walk_distance_m"
        case bearingFromUserDeg    = "bearing_from_user_deg"
        case estWalkMinutes        = "est_walk_minutes"
        case source, confidence
        case walkabilityNoteZh     = "walkability_note_zh"
        case nameZh                = "name_zh"
        case indoor
        case walkRoute             = "walk_route"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.kind                = try c.decode(Kind.self, forKey: .kind)
        self.azimuthDeg          = try c.decodeIfPresent(Double.self, forKey: .azimuthDeg)
        self.distanceM           = try c.decodeIfPresent(Double.self, forKey: .distanceM)
        self.pitchDeg            = try c.decodeIfPresent(Double.self, forKey: .pitchDeg)
        self.heightHint          = try c.decodeIfPresent(HeightHint.self, forKey: .heightHint)
        self.lat                 = try c.decodeIfPresent(Double.self, forKey: .lat)
        self.lon                 = try c.decodeIfPresent(Double.self, forKey: .lon)
        self.heightAboveGroundM  = try c.decodeIfPresent(Double.self, forKey: .heightAboveGroundM)
        self.facingDeg           = try c.decodeIfPresent(Double.self, forKey: .facingDeg)
        self.walkDistanceM       = try c.decodeIfPresent(Double.self, forKey: .walkDistanceM)
        self.bearingFromUserDeg  = try c.decodeIfPresent(Double.self, forKey: .bearingFromUserDeg)
        self.estWalkMinutes      = try c.decodeIfPresent(Double.self, forKey: .estWalkMinutes)
        self.source              = (try? c.decode(Source.self, forKey: .source)) ?? .llmRelative
        self.confidence          = try c.decodeIfPresent(Double.self, forKey: .confidence) ?? 0.5
        self.walkabilityNoteZh   = try c.decodeIfPresent(String.self, forKey: .walkabilityNoteZh)
        self.nameZh              = try c.decodeIfPresent(String.self, forKey: .nameZh)
        self.indoor              = try c.decodeIfPresent(IndoorContext.self, forKey: .indoor)
        self.walkRoute           = try c.decodeIfPresent(WalkRoute.self, forKey: .walkRoute)
    }

    /// Convenience: a one-line label for the shot card.
    var summaryZh: String {
        switch kind {
        case .relative:
            let d = distanceM.map { String(format: "%.1f m", $0) } ?? "—"
            return "原地附近 · \(d)"
        case .absolute:
            if let walk = walkDistanceM {
                let mins = estWalkMinutes ?? (walk / 80.0)
                return "走 \(Int(walk.rounded())) m · ≈ \(Int(mins.rounded())) 分钟"
            }
            return nameZh ?? "外部机位"
        case .indoor:
            if let s = indoor {
                let parts = [s.buildingNameZh, s.floor, s.hotspotLabelZh].compactMap { $0 }
                return parts.joined(separator: " · ")
            }
            return nameZh ?? "室内热点"
        }
    }
}

struct Composition: Codable, Sendable, Hashable {
    let primary: CompositionType
    let secondary: [String]
    let notes: String?
}

struct DeviceHints: Codable, Sendable, Hashable {
    let iphoneLens: IphoneLens?
    let thirdPartyApp: String?

    enum CodingKeys: String, CodingKey {
        case iphoneLens = "iphone_lens"
        case thirdPartyApp = "third_party_app"
    }
}

struct CameraSettings: Codable, Sendable, Hashable {
    let focalLengthMm: Double
    let aperture: String
    let shutter: String
    let iso: Int
    let whiteBalanceK: Int?
    let evCompensation: Double?
    let rationale: String?
    let deviceHints: DeviceHints?
    /// New in v6 — backend always populates this for iOS shoot screen.
    let iphoneApplyPlan: IphoneApplyPlan?

    enum CodingKeys: String, CodingKey {
        case focalLengthMm = "focal_length_mm"
        case aperture
        case shutter
        case iso
        case whiteBalanceK = "white_balance_k"
        case evCompensation = "ev_compensation"
        case rationale
        case deviceHints = "device_hints"
        case iphoneApplyPlan = "iphone_apply_plan"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.focalLengthMm  = try c.decode(Double.self, forKey: .focalLengthMm)
        self.aperture       = try c.decode(String.self, forKey: .aperture)
        self.shutter        = try c.decode(String.self, forKey: .shutter)
        self.iso            = try c.decode(Int.self, forKey: .iso)
        self.whiteBalanceK  = try c.decodeIfPresent(Int.self, forKey: .whiteBalanceK)
        self.evCompensation = try c.decodeIfPresent(Double.self, forKey: .evCompensation)
        self.rationale      = try c.decodeIfPresent(String.self, forKey: .rationale)
        self.deviceHints    = try c.decodeIfPresent(DeviceHints.self, forKey: .deviceHints)
        // Tolerant of older cached responses that pre-date v6.
        self.iphoneApplyPlan = try c.decodeIfPresent(IphoneApplyPlan.self, forKey: .iphoneApplyPlan)
    }
}

/// Machine-applicable iPhone parameters computed by the backend. Plug
/// these straight into ``AVCaptureDevice`` from ``ShootingCameraController``.
struct IphoneApplyPlan: Codable, Sendable, Hashable {
    let zoomFactor: Double
    let iso: Int
    let shutterSeconds: Double
    let evCompensation: Double
    let whiteBalanceK: Int
    let apertureNote: String
    let canApply: Bool

    enum CodingKeys: String, CodingKey {
        case zoomFactor = "zoom_factor"
        case iso
        case shutterSeconds = "shutter_seconds"
        case evCompensation = "ev_compensation"
        case whiteBalanceK = "white_balance_k"
        case apertureNote = "aperture_note"
        case canApply = "can_apply"
    }

    /// Reciprocal — UI displays "1/250 s" rather than "0.004 s" so it
    /// matches photographer expectations.
    var shutterDisplay: String {
        if shutterSeconds <= 0 { return "—" }
        let denom = 1.0 / shutterSeconds
        if denom >= 2 {
            return "1/\(Int(denom.rounded()))"
        }
        return String(format: "%.1fs", shutterSeconds)
    }

    /// Friendly equivalent focal length, given a 26 mm-equivalent main lens.
    var equivalentFocalMm: Int {
        Int((zoomFactor * 26.0).rounded())
    }
}

struct PersonPose: Codable, Sendable, Identifiable, Hashable {
    let role: String
    let stance: String?
    let upperBody: String?
    let hands: String?
    let gaze: String?
    let expression: String?
    let positionHint: String?

    var id: String { role }

    enum CodingKeys: String, CodingKey {
        case role
        case stance
        case upperBody = "upper_body"
        case hands
        case gaze
        case expression
        case positionHint = "position_hint"
    }
}

struct PoseSuggestion: Codable, Sendable, Hashable {
    let personCount: Int
    let layout: Layout
    let persons: [PersonPose]
    let interaction: String?
    let referenceThumbnailId: String?
    let difficulty: Difficulty?

    enum CodingKeys: String, CodingKey {
        case personCount = "person_count"
        case layout
        case persons
        case interaction
        case referenceThumbnailId = "reference_thumbnail_id"
        case difficulty
    }
}

/// 7-dimension quality score the LLM is forced to fill so the result UI
/// can show *why* a shot is recommended. Mirrors backend `CriteriaScore`.
///
/// v6 added subject_fit / background / theme on top of the original 4 axes
/// (composition × light × color × depth). The decoder defaults the new
/// three to a neutral 3 so old cached payloads still parse.
struct CriteriaScore: Codable, Sendable, Hashable {
    let composition: Int
    let light: Int
    let color: Int
    let depth: Int
    let subjectFit: Int
    let background: Int
    let theme: Int

    enum CodingKeys: String, CodingKey {
        case composition, light, color, depth
        case subjectFit = "subject_fit"
        case background
        case theme
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.composition = try c.decode(Int.self, forKey: .composition)
        self.light       = try c.decode(Int.self, forKey: .light)
        self.color       = try c.decode(Int.self, forKey: .color)
        self.depth       = try c.decode(Int.self, forKey: .depth)
        self.subjectFit  = try c.decodeIfPresent(Int.self, forKey: .subjectFit) ?? 3
        self.background  = try c.decodeIfPresent(Int.self, forKey: .background) ?? 3
        self.theme       = try c.decodeIfPresent(Int.self, forKey: .theme) ?? 3
    }

    init(composition: Int, light: Int, color: Int, depth: Int,
         subjectFit: Int = 3, background: Int = 3, theme: Int = 3) {
        self.composition = composition
        self.light = light
        self.color = color
        self.depth = depth
        self.subjectFit = subjectFit
        self.background = background
        self.theme = theme
    }

    /// Ordered for SwiftUI ScrollView — composition first (most important),
    /// theme last (most subjective). Matches the order in render.js.
    var asArray: [(label: String, key: String, value: Int)] {
        [
            ("构图",   "composition",  composition),
            ("主体感", "subject_fit",  subjectFit),
            ("背景",   "background",   background),
            ("主题",   "theme",        theme),
            ("光线",   "light",        light),
            ("色彩",   "color",        color),
            ("景深",   "depth",        depth),
        ]
    }
}

struct CriteriaNotes: Codable, Sendable, Hashable {
    let composition: String?
    let light: String?
    let color: String?
    let depth: String?
    let subjectFit: String?
    let background: String?
    let theme: String?

    enum CodingKeys: String, CodingKey {
        case composition, light, color, depth
        case subjectFit = "subject_fit"
        case background
        case theme
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.composition = try c.decodeIfPresent(String.self, forKey: .composition)
        self.light       = try c.decodeIfPresent(String.self, forKey: .light)
        self.color       = try c.decodeIfPresent(String.self, forKey: .color)
        self.depth       = try c.decodeIfPresent(String.self, forKey: .depth)
        self.subjectFit  = try c.decodeIfPresent(String.self, forKey: .subjectFit)
        self.background  = try c.decodeIfPresent(String.self, forKey: .background)
        self.theme       = try c.decodeIfPresent(String.self, forKey: .theme)
    }

    func note(for axis: String) -> String? {
        switch axis {
        case "composition": return composition
        case "light":       return light
        case "color":       return color
        case "depth":       return depth
        case "subject_fit": return subjectFit
        case "background":  return background
        case "theme":       return theme
        default:            return nil
        }
    }
}

struct ShotRecommendation: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let title: String?
    let angle: Angle
    let composition: Composition
    let camera: CameraSettings
    let poses: [PoseSuggestion]
    let rationale: String
    let coachBrief: String?
    let representativeFrameIndex: Int?
    let confidence: Double
    let criteriaScore: CriteriaScore?
    let criteriaNotes: CriteriaNotes?
    let strongestAxis: String?
    let weakestAxis: String?
    /// Backend-computed weighted score in [0, 5]. Used for client-side
    /// ranking when the user toggles "按综合分排序".
    let overallScore: Double?
    let iphoneTips: [String]
    /// Per-shot style intent + compliance summary (filled by backend
    /// after the LLM returns). Nil when user didn't pick a style on
    /// Step 3. UI renders a "风格 X · 推荐 Y · 实际 Z" badge when set.
    let styleMatch: ShotStyleMatch?
    /// Three-layer composition strategy (前景/中景/背景). Tells the user
    /// what kind of foreground to use, where to find it (azimuth +
    /// quadrant), how to physically nudge themselves to bring it into
    /// frame. Mirrors backend ``ShotForeground``. Nil when the scene
    /// has no usable foreground (e.g. open beach with nothing within
    /// 1.5m); rationale must explicitly say so in that case.
    let foreground: ShotForeground?
    /// v13 — unified shot-position descriptor. ``relative`` keeps parity
    /// with ``angle``; ``absolute`` carries POI / SfM derived world coords
    /// so the result UI can render a map pin + walk distance.
    let position: ShotPosition?

    enum CodingKeys: String, CodingKey {
        case id, title, angle, composition, camera, poses, rationale, confidence
        case coachBrief = "coach_brief"
        case representativeFrameIndex = "representative_frame_index"
        case criteriaScore = "criteria_score"
        case criteriaNotes = "criteria_notes"
        case strongestAxis = "strongest_axis"
        case weakestAxis   = "weakest_axis"
        case overallScore  = "overall_score"
        case iphoneTips    = "iphone_tips"
        case styleMatch    = "style_match"
        case foreground
        case position
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id           = try c.decode(String.self, forKey: .id)
        self.title        = try c.decodeIfPresent(String.self, forKey: .title)
        self.angle        = try c.decode(Angle.self, forKey: .angle)
        self.composition  = try c.decode(Composition.self, forKey: .composition)
        self.camera       = try c.decode(CameraSettings.self, forKey: .camera)
        self.poses        = try c.decode([PoseSuggestion].self, forKey: .poses)
        self.rationale    = try c.decode(String.self, forKey: .rationale)
        self.coachBrief   = try c.decodeIfPresent(String.self, forKey: .coachBrief)
        self.representativeFrameIndex = try c.decodeIfPresent(Int.self, forKey: .representativeFrameIndex)
        self.confidence   = try c.decodeIfPresent(Double.self, forKey: .confidence) ?? 0.7
        self.criteriaScore = try c.decodeIfPresent(CriteriaScore.self, forKey: .criteriaScore)
        self.criteriaNotes = try c.decodeIfPresent(CriteriaNotes.self, forKey: .criteriaNotes)
        self.strongestAxis = try c.decodeIfPresent(String.self, forKey: .strongestAxis)
        self.weakestAxis   = try c.decodeIfPresent(String.self, forKey: .weakestAxis)
        self.overallScore  = try c.decodeIfPresent(Double.self, forKey: .overallScore)
        self.iphoneTips    = try c.decodeIfPresent([String].self, forKey: .iphoneTips) ?? []
        self.styleMatch    = try c.decodeIfPresent(ShotStyleMatch.self, forKey: .styleMatch)
        self.foreground    = try c.decodeIfPresent(ShotForeground.self, forKey: .foreground)
        self.position      = try c.decodeIfPresent(ShotPosition.self, forKey: .position)
    }
}

/// Mirrors backend ``ShotForeground``. The result UI uses ``layer`` as
/// a localized chip ("散景前景"/"自然画框"/"引导线"/"无可用前景") and
/// ``suggestionZh`` as a 1-2 line actionable nudge. ``sourceAzimuthDeg``
/// can be used to highlight the matching keyframe thumbnail.
struct ShotForeground: Codable, Sendable, Hashable {
    let layer: String
    let suggestionZh: String
    let sourceAzimuthDeg: Double?
    let canvasQuadrant: String?
    let estimatedDistanceM: Double?

    enum CodingKeys: String, CodingKey {
        case layer
        case suggestionZh       = "suggestion_zh"
        case sourceAzimuthDeg   = "source_azimuth_deg"
        case canvasQuadrant     = "canvas_quadrant"
        case estimatedDistanceM = "estimated_distance_m"
    }

    /// Localized chip label for the result card.
    var layerLabelZh: String {
        switch layer {
        case "bokeh_plant":    return "散景前景"
        case "natural_frame":  return "自然画框"
        case "leading_line":   return "引导线"
        case "none":           return "无可用前景"
        default:               return layer
        }
    }
}

/// Mirrors backend ``ShotStyleMatch``. Surfaces in the result UI as a
/// "风格 X · 推荐 Y · 实际 Z ✓/⚠" panel so the user can verify the AI
/// actually tuned each shot toward their chosen style.
struct ShotStyleMatch: Codable, Sendable, Hashable {
    let styleId: String
    let labelZh: String
    let whiteBalanceKRange: [Int]            // [lo, hi]
    let focalLengthMmRange: [Double]
    let evRange: [Double]
    let inRange: Bool
    /// When ``inRange`` is false, list of {knob, from, to} dicts. Decoded
    /// loosely as ``[String: AnyCodable]``-shaped dictionaries — we just
    /// pass them through to the UI's "what we tweaked" detail row.
    let fixes: [[String: StyleMatchFixValue]]

    enum CodingKeys: String, CodingKey {
        case styleId            = "style_id"
        case labelZh            = "label_zh"
        case whiteBalanceKRange = "white_balance_k_range"
        case focalLengthMmRange = "focal_length_mm_range"
        case evRange            = "ev_range"
        case inRange            = "in_range"
        case fixes
    }
}

/// One value in a ShotStyleMatch.fixes record. Backend may emit ints,
/// doubles, strings, or nulls in the same column (knob/from/to), so we
/// box into an enum that knows how to lossily Stringify itself for UI.
enum StyleMatchFixValue: Codable, Sendable, Hashable {
    case string(String)
    case number(Double)
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let d = try? c.decode(Double.self) { self = .number(d); return }
        if let i = try? c.decode(Int.self) { self = .number(Double(i)); return }
        self = .null
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let d): try c.encode(d)
        case .null: try c.encodeNil()
        }
    }

    var displayString: String {
        switch self {
        case .string(let s): return s
        case .number(let d): return d.truncatingRemainder(dividingBy: 1) == 0
            ? String(Int(d)) : String(format: "%.1f", d)
        case .null: return "—"
        }
    }
}

/// Mirrors backend ``SunSnapshot``. Photographer-ready view of the sun
/// at the time of the analyze request — used by the result UI to render
/// a sun compass and golden-hour countdown badge.
struct SunSnapshot: Codable, Sendable, Hashable {
    let azimuthDeg: Double
    let altitudeDeg: Double
    let phase: String
    let colorTempKEstimate: Int
    let minutesToGoldenEnd: Double?
    let minutesToBlueEnd: Double?
    let minutesToSunset: Double?
    let minutesToSunrise: Double?

    enum CodingKeys: String, CodingKey {
        case azimuthDeg = "azimuth_deg"
        case altitudeDeg = "altitude_deg"
        case phase
        case colorTempKEstimate   = "color_temp_k_estimate"
        case minutesToGoldenEnd   = "minutes_to_golden_end"
        case minutesToBlueEnd     = "minutes_to_blue_end"
        case minutesToSunset      = "minutes_to_sunset"
        case minutesToSunrise     = "minutes_to_sunrise"
    }

    var phaseDisplayName: String {
        switch phase {
        case "night":              return "夜间"
        case "blue_hour_dawn":     return "蓝调（清晨）"
        case "golden_hour_dawn":   return "黄金时刻（清晨）"
        case "day":                return "白天"
        case "golden_hour_dusk":   return "黄金时刻（傍晚）"
        case "blue_hour_dusk":     return "蓝调（傍晚）"
        default:                   return phase
        }
    }

    /// True when the most relevant photographic window is < 30 minutes
    /// away — UI uses this to flag the "shoot now" badge / pulse the chip.
    var isTimeTight: Bool {
        if let m = minutesToGoldenEnd, m <= 30 { return true }
        if let m = minutesToBlueEnd,   m <= 30 { return true }
        return false
    }
}

struct EnvironmentSnapshot: Codable, Sendable, Hashable {
    let sun: SunSnapshot?
    let weather: WeatherSnapshot?
    let visionLight: VisionLightHint?
    let timestamp: Date?

    init(
        sun: SunSnapshot? = nil,
        weather: WeatherSnapshot? = nil,
        visionLight: VisionLightHint? = nil,
        timestamp: Date? = nil
    ) {
        self.sun = sun
        self.weather = weather
        self.visionLight = visionLight
        self.timestamp = timestamp
    }

    enum CodingKeys: String, CodingKey {
        case sun
        case weather
        case visionLight = "vision_light"
        case timestamp
    }
}

/// Photographer-friendly current-weather snapshot, sourced from
/// Open-Meteo (no API key). Optional — analyze never blocks on weather.
struct WeatherSnapshot: Codable, Sendable, Hashable {
    let cloudCoverPct: Int?
    let visibilityM: Int?
    let uvIndex: Double?
    let temperatureC: Double?
    let weatherCode: Int?
    let softness: String
    let codeLabelZh: String?

    enum CodingKeys: String, CodingKey {
        case cloudCoverPct = "cloud_cover_pct"
        case visibilityM   = "visibility_m"
        case uvIndex       = "uv_index"
        case temperatureC  = "temperature_c"
        case weatherCode   = "weather_code"
        case softness
        case codeLabelZh   = "code_label_zh"
    }

    var softnessLabelZh: String {
        switch softness {
        case "soft":  return "软光"
        case "hard":  return "硬光"
        case "mixed": return "半软半硬"
        default:      return "未判定"
        }
    }
    var softnessGlyph: String {
        switch softness {
        case "soft":  return "cloud.fill"
        case "hard":  return "sun.max.fill"
        case "mixed": return "cloud.sun.fill"
        default:      return "circle.dotted"
        }
    }
}

/// Backend-issued nudge: 'shoot a 10-second light pass'. Fires only in
/// light_shadow mode when there isn't enough light evidence to plan
/// reliably (no geo + low vision_light confidence).
struct LightRecaptureHint: Codable, Sendable, Hashable {
    let enabled: Bool
    let title: String
    let detail: String
    let suggestedAzimuthDeg: Double?

    enum CodingKeys: String, CodingKey {
        case enabled
        case title
        case detail
        case suggestedAzimuthDeg = "suggested_azimuth_deg"
    }
}

struct StyleInspiration: Codable, Sendable, Hashable {
    let usedCount: Int
    let summary: String?
    let inheritedTraits: [String]

    enum CodingKeys: String, CodingKey {
        case usedCount = "used_count"
        case summary
        case inheritedTraits = "inherited_traits"
    }
}

struct AnalyzeResponse: Codable, Sendable, Hashable {
    let scene: SceneSummary
    let shots: [ShotRecommendation]
    let generatedAt: Date
    let model: String
    let styleInspiration: StyleInspiration?
    let environment: EnvironmentSnapshot?
    let lightRecaptureHint: LightRecaptureHint?
    let debug: AnalyzeDebug?
    let timeRecommendation: TimeRecommendation?
    let referenceFingerprints: [ReferenceFingerprint]?

    enum CodingKeys: String, CodingKey {
        case scene
        case shots
        case generatedAt = "generated_at"
        case model
        case styleInspiration = "style_inspiration"
        case environment
        case lightRecaptureHint = "light_recapture_hint"
        case debug
        case timeRecommendation = "time_recommendation"
        case referenceFingerprints = "reference_fingerprints"
    }
}

/// Subset of backend `response.debug` that the UI actually renders.
/// Decoded loosely so adding new debug keys server-side never breaks
/// the iOS client.
struct AnalyzeDebug: Codable, Sendable, Hashable {
    let lighting: LightingDebug?
    let styleCompliance: StyleComplianceDebug?
    let poseHorizon: PoseHorizonDebug?
    let composition: CompositionDebug?
    let lightForecast: LightForecastDebug?

    enum CodingKeys: String, CodingKey {
        case lighting
        case styleCompliance = "style_compliance"
        case poseHorizon = "pose_horizon"
        case composition
        case lightForecast = "light_forecast"
    }
}

struct CompositionDebug: Codable, Sendable, Hashable {
    let ruleOfThirdsDist: Double?
    let symmetry: Double?
    let facts: [String]?

    enum CodingKeys: String, CodingKey {
        case ruleOfThirdsDist = "rule_of_thirds_dist"
        case symmetry
        case facts
    }
}

struct LightForecastDebug: Codable, Sendable, Hashable {
    let cloudIn30Min: Double?
    let goldenHourCountdownMin: Int?

    enum CodingKeys: String, CodingKey {
        case cloudIn30Min = "cloud_in_30min"
        case goldenHourCountdownMin = "golden_hour_countdown_min"
    }
}

struct PoseHorizonDebug: Codable, Sendable, Hashable {
    let poseFacts: [String]?
    let horizonY: Double?
    let horizonConfidence: String?
    let skyPresent: Bool?

    enum CodingKeys: String, CodingKey {
        case poseFacts = "pose_facts"
        case horizonY  = "horizon_y"
        case horizonConfidence = "horizon_confidence"
        case skyPresent = "sky_present"
    }
}

struct LightingDebug: Codable, Sendable, Hashable {
    let cctK: Int?
    let tint: Double?
    let dynamicRange: String?
    let lightDirection: String?
    let highlightClipPct: Double?
    let shadowClipPct: Double?
    let notes: [String]?

    enum CodingKeys: String, CodingKey {
        case cctK = "cct_k"
        case tint
        case dynamicRange = "dynamic_range"
        case lightDirection = "light_direction"
        case highlightClipPct = "highlight_clip_pct"
        case shadowClipPct    = "shadow_clip_pct"
        case notes
    }
}

struct StyleComplianceDebug: Codable, Sendable, Hashable {
    let rate: Double?
    let total: Int?
    let clamped: Int?
    let paletteDrift: [PaletteDriftEntry]?

    enum CodingKeys: String, CodingKey {
        case rate, total, clamped
        case paletteDrift = "palette_drift"
    }
}

struct PaletteDriftEntry: Codable, Sendable, Hashable {
    let styleId: String?
    let axis: String?
    let message: String?

    enum CodingKeys: String, CodingKey {
        case styleId = "style_id"
        case axis
        case message
    }
}

// MARK: - W2/W3/W6/W7/W9 — additional schemas

struct IndoorContext: Codable, Sendable, Hashable {
    let buildingId: String
    let buildingNameZh: String?
    let floor: String?
    let hotspotLabelZh: String?
    let imageRef: String?
    let xFloor: Double?
    let yFloor: Double?

    enum CodingKeys: String, CodingKey {
        case buildingId = "building_id"
        case buildingNameZh = "building_name_zh"
        case floor
        case hotspotLabelZh = "hotspot_label_zh"
        case imageRef = "image_ref"
        case xFloor = "x_floor"
        case yFloor = "y_floor"
    }
}

struct WalkRouteStep: Codable, Sendable, Hashable {
    let instructionZh: String
    let distanceM: Double
    let durationS: Double
    let polyline: String?

    enum CodingKeys: String, CodingKey {
        case instructionZh = "instruction_zh"
        case distanceM = "distance_m"
        case durationS = "duration_s"
        case polyline
    }
}

struct WalkRoute: Codable, Sendable, Hashable {
    let distanceM: Double
    let durationMin: Double
    let polyline: String
    let steps: [WalkRouteStep]
    let provider: String?

    enum CodingKeys: String, CodingKey {
        case distanceM = "distance_m"
        case durationMin = "duration_min"
        case polyline
        case steps
        case provider
    }
}

struct ReferenceFingerprint: Codable, Sendable, Hashable, Identifiable {
    let index: Int
    let palette: [String]
    let paletteWeights: [Double]
    let contrastBand: String
    let saturationBand: String
    let moodKeywords: [String]
    let embeddingDims: Int?
    let thumbnailRef: String?

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index
        case palette
        case paletteWeights = "palette_weights"
        case contrastBand = "contrast_band"
        case saturationBand = "saturation_band"
        case moodKeywords = "mood_keywords"
        case embeddingDims = "embedding_dims"
        case thumbnailRef = "thumbnail_ref"
    }
}

struct TimeRecommendation: Codable, Sendable, Hashable {
    let bestHourLocal: Int
    let score: Double
    let sampleN: Int
    let blurbZh: String?
    let runnerUpHourLocal: Int?
    let minutesUntilBest: Double?

    enum CodingKeys: String, CodingKey {
        case bestHourLocal = "best_hour_local"
        case score
        case sampleN = "sample_n"
        case blurbZh = "blurb_zh"
        case runnerUpHourLocal = "runner_up_hour_local"
        case minutesUntilBest = "minutes_until_best"
    }
}

struct SparseModel: Codable, Sendable, Hashable {
    let jobId: String
    let pointsCount: Int
    let camerasCount: Int
    let scaleMPerUnit: Double
    let bboxLat: [Double]?
    let bboxLon: [Double]?
    let thumbnailRef: String?

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case pointsCount = "points_count"
        case camerasCount = "cameras_count"
        case scaleMPerUnit = "scale_m_per_unit"
        case bboxLat = "bbox_lat"
        case bboxLon = "bbox_lon"
        case thumbnailRef = "thumbnail_ref"
    }
}

struct ShotResultIn: Codable, Sendable {
    var analyzeRequestId: String?
    var styleKeywords: [String] = []
    var geoLat: Double?
    var geoLon: Double?
    var capturedAtUtc: Date?
    var focalLengthMm: Double?
    var focalLength35mmEq: Double?
    var aperture: Double?
    var exposureTimeS: Double?
    var iso: Int?
    var whiteBalanceK: Int?
    var recommendationSnapshot: [String: AnyCodable]?
    var chosenPosition: ShotPosition?
    var rating: Int?
    var sceneKind: String?

    enum CodingKeys: String, CodingKey {
        case analyzeRequestId = "analyze_request_id"
        case styleKeywords = "style_keywords"
        case geoLat = "geo_lat"
        case geoLon = "geo_lon"
        case capturedAtUtc = "captured_at_utc"
        case focalLengthMm = "focal_length_mm"
        case focalLength35mmEq = "focal_length_35mm_eq"
        case aperture
        case exposureTimeS = "exposure_time_s"
        case iso
        case whiteBalanceK = "white_balance_k"
        case recommendationSnapshot = "recommendation_snapshot"
        case chosenPosition = "chosen_position"
        case rating
        case sceneKind = "scene_kind"
    }
}

struct AnyCodable: Codable, Sendable {
    let value: Any
    init(_ value: Any) { self.value = value }
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(Bool.self) { self.value = v }
        else if let v = try? c.decode(Int.self) { self.value = v }
        else if let v = try? c.decode(Double.self) { self.value = v }
        else if let v = try? c.decode(String.self) { self.value = v }
        else { self.value = NSNull() }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch value {
        case let v as Bool: try c.encode(v)
        case let v as Int: try c.encode(v)
        case let v as Double: try c.encode(v)
        case let v as String: try c.encode(v)
        default: try c.encodeNil()
        }
    }
}

