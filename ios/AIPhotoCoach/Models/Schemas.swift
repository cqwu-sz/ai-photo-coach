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

    /// Light-shadow mode wants real-world sun data so we can plan rim-light /
    /// silhouette shots. UI uses this to ask for location permission upfront.
    var needsSunInfo: Bool { self == .lightShadow }
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

    init(
        index: Int,
        azimuthDeg: Double,
        pitchDeg: Double,
        rollDeg: Double,
        timestampMs: Int,
        ambientLux: Double? = nil,
        blurScore: Double? = nil,
        meanLuma: Double? = nil,
        faceHit: Bool? = nil
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

struct CaptureMeta: Codable, Sendable, Hashable {
    let personCount: Int
    let qualityMode: QualityMode
    let sceneMode: SceneMode
    let styleKeywords: [String]
    let frameMeta: [FrameMeta]
    let geo: GeoFix?

    init(
        personCount: Int,
        qualityMode: QualityMode,
        sceneMode: SceneMode = .portrait,
        styleKeywords: [String],
        frameMeta: [FrameMeta],
        geo: GeoFix? = nil
    ) {
        self.personCount = personCount
        self.qualityMode = qualityMode
        self.sceneMode = sceneMode
        self.styleKeywords = styleKeywords
        self.frameMeta = frameMeta
        self.geo = geo
    }

    enum CodingKeys: String, CodingKey {
        case personCount = "person_count"
        case qualityMode = "quality_mode"
        case sceneMode = "scene_mode"
        case styleKeywords = "style_keywords"
        case frameMeta = "frame_meta"
        case geo
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.personCount = try c.decode(Int.self, forKey: .personCount)
        self.qualityMode = try c.decode(QualityMode.self, forKey: .qualityMode)
        self.sceneMode = try c.decodeIfPresent(SceneMode.self, forKey: .sceneMode) ?? .portrait
        self.styleKeywords = try c.decodeIfPresent([String].self, forKey: .styleKeywords) ?? []
        self.frameMeta = try c.decode([FrameMeta].self, forKey: .frameMeta)
        self.geo = try c.decodeIfPresent(GeoFix.self, forKey: .geo)
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

    enum CodingKeys: String, CodingKey {
        case scene
        case shots
        case generatedAt = "generated_at"
        case model
        case styleInspiration = "style_inspiration"
        case environment
        case lightRecaptureHint = "light_recapture_hint"
    }
}
