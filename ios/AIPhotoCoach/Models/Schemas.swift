import Foundation

// Mirrors backend/app/models/schemas.py and shared/schema/analyze.openapi.yaml.
// Keep these enums/cases byte-for-byte equal to the backend.

enum QualityMode: String, Codable, CaseIterable, Sendable {
    case fast, high
}

enum Lighting: String, Codable, Sendable {
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

enum CompositionType: String, Codable, Sendable {
    case ruleOfThirds = "rule_of_thirds"
    case leadingLine = "leading_line"
    case symmetry
    case frameWithinFrame = "frame_within_frame"
    case negativeSpace = "negative_space"
    case centered
    case diagonal
    case goldenRatio = "golden_ratio"
}

enum HeightHint: String, Codable, Sendable {
    case low, eyeLevel = "eye_level", high, overhead
}

enum Layout: String, Codable, Sendable {
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

enum IphoneLens: String, Codable, Sendable {
    case ultrawide = "ultrawide_0_5x"
    case wide = "wide_1x"
    case tele2x = "tele_2x"
    case tele3x = "tele_3x"
    case tele5x = "tele_5x"
}

enum Difficulty: String, Codable, Sendable {
    case easy, medium, hard
}

struct FrameMeta: Codable, Sendable {
    let index: Int
    let azimuthDeg: Double
    let pitchDeg: Double
    let rollDeg: Double
    let timestampMs: Int
    let ambientLux: Double?

    enum CodingKeys: String, CodingKey {
        case index
        case azimuthDeg = "azimuth_deg"
        case pitchDeg = "pitch_deg"
        case rollDeg = "roll_deg"
        case timestampMs = "timestamp_ms"
        case ambientLux = "ambient_lux"
    }
}

struct CaptureMeta: Codable, Sendable {
    let personCount: Int
    let qualityMode: QualityMode
    let styleKeywords: [String]
    let frameMeta: [FrameMeta]

    enum CodingKeys: String, CodingKey {
        case personCount = "person_count"
        case qualityMode = "quality_mode"
        case styleKeywords = "style_keywords"
        case frameMeta = "frame_meta"
    }
}

struct SceneSummary: Codable, Sendable {
    let type: String
    let lighting: Lighting
    let backgroundSummary: String
    let cautions: [String]

    enum CodingKeys: String, CodingKey {
        case type
        case lighting
        case backgroundSummary = "background_summary"
        case cautions
    }
}

struct Angle: Codable, Sendable {
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

struct Composition: Codable, Sendable {
    let primary: CompositionType
    let secondary: [String]
    let notes: String?
}

struct DeviceHints: Codable, Sendable {
    let iphoneLens: IphoneLens?
    let thirdPartyApp: String?

    enum CodingKeys: String, CodingKey {
        case iphoneLens = "iphone_lens"
        case thirdPartyApp = "third_party_app"
    }
}

struct CameraSettings: Codable, Sendable {
    let focalLengthMm: Double
    let aperture: String
    let shutter: String
    let iso: Int
    let whiteBalanceK: Int?
    let evCompensation: Double?
    let rationale: String?
    let deviceHints: DeviceHints?

    enum CodingKeys: String, CodingKey {
        case focalLengthMm = "focal_length_mm"
        case aperture
        case shutter
        case iso
        case whiteBalanceK = "white_balance_k"
        case evCompensation = "ev_compensation"
        case rationale
        case deviceHints = "device_hints"
    }
}

struct PersonPose: Codable, Sendable, Identifiable {
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

struct PoseSuggestion: Codable, Sendable {
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

struct ShotRecommendation: Codable, Sendable, Identifiable {
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

    enum CodingKeys: String, CodingKey {
        case id, title, angle, composition, camera, poses, rationale, confidence
        case coachBrief = "coach_brief"
        case representativeFrameIndex = "representative_frame_index"
    }
}

struct StyleInspiration: Codable, Sendable {
    let usedCount: Int
    let summary: String?
    let inheritedTraits: [String]

    enum CodingKeys: String, CodingKey {
        case usedCount = "used_count"
        case summary
        case inheritedTraits = "inherited_traits"
    }
}

struct AnalyzeResponse: Codable, Sendable {
    let scene: SceneSummary
    let shots: [ShotRecommendation]
    let generatedAt: Date
    let model: String
    let styleInspiration: StyleInspiration?

    enum CodingKeys: String, CodingKey {
        case scene
        case shots
        case generatedAt = "generated_at"
        case model
        case styleInspiration = "style_inspiration"
    }
}
