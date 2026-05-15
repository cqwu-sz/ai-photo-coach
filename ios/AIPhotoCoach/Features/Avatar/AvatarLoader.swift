// v7 Phase B — RealityKit avatar + animation loader.
//
// Replaces AvatarBuilderSCN's hand-built SCNBox geometry with real
// USDZ models converted from ReadyPlayerMe glb files.
//
// Public API:
//
//   let m = try await AvatarManifest.shared.load()
//   let entity = try await AvatarLoader.shared.load(presetId: "male_casual_25")
//   try await AvatarLoader.shared.playAnimation("idle_relaxed", on: entity)
//
// All async; loads are cached in-memory so subsequent calls are
// instant. Falls back to nil + a console warning when the .usdz file
// isn't bundled — caller should detect and fall back to the legacy
// SceneKit avatar.

import Foundation
import RealityKit

/// One preset avatar's metadata, mirrors the backend manifest entry.
public struct AvatarPresetEntry: Codable, Hashable, Sendable {
    public let id: String
    public let nameZh: String
    public let gender: String
    public let age: Int
    public let style: String
    public let tags: [String]
    public let glb: String       // web URL — used by Web only
    public let usdz: String      // bundle-relative path
    public let thumbnail: String

    enum CodingKeys: String, CodingKey {
        case id, gender, age, style, tags, glb, usdz, thumbnail
        case nameZh = "name_zh"
    }
}

/// A single Mixamo animation reference. We store the path relative to
/// the bundle's resource directory so RealityKit can load it directly.
public struct AvatarAnimationManifest: Codable, Sendable {
    public let single: [String: String]
    public let twoPerson: [String: String]
    public let threePerson: [String: String]
    public let fourPerson: [String: String]
    public let fallbackByCount: [String: String]
    /// B-pose-for-height — map from HeightHint enum string ("low",
    /// "eye_level", "high", "overhead") to a mixamo-id whose stance
    /// makes sense at that altitude. When the manifest omits this map
    /// we fall back to ``resolve(poseId:personCount:)`` so older
    /// backend versions keep working without any null checks here.
    public let poseForHeight: [String: String]?

    enum CodingKeys: String, CodingKey {
        case single
        case twoPerson = "two_person"
        case threePerson = "three_person"
        case fourPerson = "four_person"
        case fallbackByCount = "fallback_by_count"
        case poseForHeight = "pose_for_height"
    }

    /// Flatten all sections into a single pose-id → mixamo-id map.
    public var flatPoseMap: [String: String] {
        var combined: [String: String] = [:]
        for section in [single, twoPerson, threePerson, fourPerson] {
            combined.merge(section) { _, new in new }
        }
        return combined
    }

    public func resolve(poseId: String?, personCount: Int) -> String {
        if let id = poseId, let direct = flatPoseMap[id] {
            return direct
        }
        return fallbackByCount[String(personCount)] ?? "idle_relaxed"
    }

    /// Pick a pose specifically tailored to the subject's vertical
    /// position. ``heightHint`` strings match the backend HeightHint
    /// enum. Returns a sensible built-in fallback when the manifest
    /// doesn't carry ``pose_for_height``.
    public func resolveForHeight(_ heightHint: String?) -> String? {
        guard let h = heightHint else { return nil }
        if let mapped = poseForHeight?[h] { return mapped }
        // Built-in defaults so "overhead" doesn't fall back to a
        // standing idle, which would look wrong floating 3 m in the air.
        switch h {
        case "overhead":  return "lean_look_down"
        case "high":      return "stand_look_down_slight"
        case "low":       return "look_up_curious"
        default:          return nil
        }
    }
}

/// Container for the /avatars/manifest payload.
public struct AvatarManifestPayload: Codable, Sendable {
    public let presets: [AvatarPresetEntry]
    public let poseToMixamo: AvatarAnimationManifest

    enum CodingKeys: String, CodingKey {
        case presets
        case poseToMixamo = "pose_to_mixamo"
    }
}

/// Caches the manifest fetched from the backend. The avatar gallery
/// + AR view both subscribe to this to learn which presets are
/// available.
@MainActor
public final class AvatarManifest: ObservableObject {
    public static let shared = AvatarManifest()

    @Published public private(set) var payload: AvatarManifestPayload?
    @Published public private(set) var isLoading: Bool = false
    @Published public private(set) var lastError: String?

    private init() {}

    /// Fetch + cache. Subsequent calls return the cached payload
    /// without going to the network.
    @discardableResult
    public func load(baseURL: URL? = nil) async -> AvatarManifestPayload? {
        if let payload { return payload }
        if isLoading { return nil }
        isLoading = true
        defer { isLoading = false }
        let base = baseURL ?? AvatarManifest.defaultBaseURL()
        guard let url = URL(string: "/avatars/manifest", relativeTo: base) else {
            lastError = "invalid base URL"
            return nil
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoder = JSONDecoder()
            let parsed = try decoder.decode(AvatarManifestPayload.self, from: data)
            let merged = AvatarManifestPayload(
                presets: Self.mergeBundledPresets(parsed.presets),
                poseToMixamo: parsed.poseToMixamo
            )
            self.payload = merged
            return merged
        } catch {
            lastError = error.localizedDescription
            print("[AvatarManifest] load failed:", error)
            let fallback = AvatarManifestPayload(
                presets: Self.mergeBundledPresets([]),
                poseToMixamo: AvatarAnimationManifest(
                    single: [:],
                    twoPerson: [:],
                    threePerson: [:],
                    fourPerson: [:],
                    fallbackByCount: ["1": "idle_relaxed"],
                    poseForHeight: nil
                )
            )
            self.payload = fallback
            return fallback
        }
    }

    private static func mergeBundledPresets(_ remote: [AvatarPresetEntry]) -> [AvatarPresetEntry] {
        var merged: [AvatarPresetEntry] = []
        var seen = Set<String>()
        for preset in remote + bundledPresetFallbacks() {
            guard !seen.contains(preset.id) else { continue }
            seen.insert(preset.id)
            merged.append(preset)
        }
        return merged
    }

    private static func bundledPresetFallbacks() -> [AvatarPresetEntry] {
        [
            AvatarPresetEntry(id: "female_youth_18", nameZh: "少女 · 18", gender: "female", age: 18, style: "youth", tags: ["youth", "summer", "sweet"], glb: "/web/avatars/preset/female_youth_18.glb", usdz: "Avatars/female_youth_18.usdz", thumbnail: "/web/avatars/preset/female_youth_18.png"),
            AvatarPresetEntry(id: "male_casual_25", nameZh: "休闲男 · 25", gender: "male", age: 25, style: "casual", tags: ["street", "everyday"], glb: "/web/avatars/preset/male_casual_25.glb", usdz: "Avatars/male_casual_25.usdz", thumbnail: "/web/avatars/preset/male_casual_25.png"),
            AvatarPresetEntry(id: "female_casual_22", nameZh: "休闲女 · 22", gender: "female", age: 22, style: "casual", tags: ["youth", "street"], glb: "/web/avatars/preset/female_casual_22.glb", usdz: "Avatars/female_casual_22.usdz", thumbnail: "/web/avatars/preset/female_casual_22.png"),
            AvatarPresetEntry(id: "female_elegant_30", nameZh: "优雅女 · 30", gender: "female", age: 30, style: "elegant", tags: ["formal", "fashion"], glb: "/web/avatars/preset/female_elegant_30.glb", usdz: "Avatars/female_elegant_30.usdz", thumbnail: "/web/avatars/preset/female_elegant_30.png"),
            AvatarPresetEntry(id: "female_artsy_25", nameZh: "文艺女 · 25", gender: "female", age: 25, style: "artsy", tags: ["bohemian", "softlight"], glb: "/web/avatars/preset/female_artsy_25.glb", usdz: "Avatars/female_artsy_25.usdz", thumbnail: "/web/avatars/preset/female_artsy_25.png"),
            AvatarPresetEntry(id: "male_business_35", nameZh: "商务男 · 35", gender: "male", age: 35, style: "business", tags: ["formal", "office"], glb: "/web/avatars/preset/male_business_35.glb", usdz: "Avatars/male_business_35.usdz", thumbnail: "/web/avatars/preset/male_business_35.png"),
            AvatarPresetEntry(id: "male_athletic_28", nameZh: "运动男 · 28", gender: "male", age: 28, style: "athletic", tags: ["outdoor", "fit"], glb: "/web/avatars/preset/male_athletic_28.glb", usdz: "Avatars/male_athletic_28.usdz", thumbnail: "/web/avatars/preset/male_athletic_28.png"),
            AvatarPresetEntry(id: "child_boy_8", nameZh: "男孩 · 8", gender: "male", age: 8, style: "child", tags: ["family", "kids"], glb: "/web/avatars/preset/child_boy_8.glb", usdz: "Avatars/child_boy_8.usdz", thumbnail: "/web/avatars/preset/child_boy_8.png"),
            AvatarPresetEntry(id: "child_girl_8", nameZh: "女孩 · 8", gender: "female", age: 8, style: "child", tags: ["family", "kids"], glb: "/web/avatars/preset/child_girl_8.glb", usdz: "Avatars/child_girl_8.usdz", thumbnail: "/web/avatars/preset/child_girl_8.png"),
        ]
    }

    /// Resolve the API base URL from UserDefaults (BYOK / dev override)
    /// or fall back to localhost.
    private static func defaultBaseURL() -> URL {
        if let saved = UserDefaults.standard.string(forKey: "apiBaseURL"),
           let u = URL(string: saved) {
            return u
        }
        return URL(string: "http://127.0.0.1:8000")!
    }
}

// MARK: - Loader

/// Loads + caches RealityKit Entities from bundled USDZ avatars.
@MainActor
public final class AvatarLoader {
    public static let shared = AvatarLoader()

    private var avatarCache: [String: Entity] = [:]
    private var animationCache: [String: Entity] = [:]

    private init() {}

    /// Load (or return cached) avatar template by preset id. The
    /// returned Entity is *cloned* so each call gives a fresh instance
    /// callers can mutate / animate independently.
    public func load(presetId: String) async throws -> Entity? {
        if let cached = avatarCache[presetId] {
            return cached.clone(recursive: true)
        }
        let candidate = "Avatars/\(presetId)"
        do {
            // Entity(named:in:) async API is iOS 18+. On iOS 17 the
            // synchronous loader can throw via the same name; this
            // gate makes the call site explicitly 18-only and lets
            // older devices fall through to the SCN fallback path
            // that ARGuideView already implements.
            let entity: Entity
            if #available(iOS 18.0, *) {
                entity = try await Entity(named: candidate, in: nil)
            } else {
                entity = try Entity.load(named: candidate)
            }
            avatarCache[presetId] = entity
            return entity.clone(recursive: true)
        } catch {
            print("[AvatarLoader] preset \(presetId) not bundled — falling back to legacy SCN. Reason:", error)
            return nil
        }
    }

    /// Load (or return cached) animation by id. Animations are USDZ
    /// files containing only the AnimationLibrary track — they're
    /// retargeted onto whichever avatar Entity is passed.
    public func loadAnimation(_ animId: String) async throws -> AnimationResource? {
        if let cached = animationCache[animId],
           let res = cached.availableAnimations.first {
            return res
        }
        let candidate = "Animations/\(animId)"
        do {
            let entity: Entity
            if #available(iOS 18.0, *) {
                entity = try await Entity(named: candidate, in: nil)
            } else {
                entity = try Entity.load(named: candidate)
            }
            animationCache[animId] = entity
            return entity.availableAnimations.first
        } catch {
            print("[AvatarLoader] animation \(animId) not bundled. Reason:", error)
            return nil
        }
    }

    /// Convenience — resolve LLM pose id → Mixamo id → load + bind.
    /// Returns the running animation controller if everything aligned.
    @discardableResult
    public func playPose(
        _ poseId: String?,
        personCount: Int,
        on entity: Entity,
        manifest: AvatarAnimationManifest?,
    ) async -> AnimationPlaybackController? {
        let animId = manifest?.resolve(poseId: poseId, personCount: personCount)
            ?? "idle_relaxed"
        guard let animation = try? await loadAnimation(animId) else { return nil }
        return entity.playAnimation(animation.repeat(), transitionDuration: 0.4)
    }
}

// MARK: - Pick helpers

public enum AvatarPicker {
    /// Map a person index in a multi-person shot to a preset id, using
    /// the user's persisted picks where available, then a sensible
    /// rotation through the pack so couples aren't twins.
    public static func pick(
        personIndex: Int,
        from presets: [AvatarPresetEntry],
        persisted: [String]? = nil,
    ) -> String? {
        let stored = persisted ?? UserDefaults.standard.stringArray(forKey: "avatarPicks") ?? []
        if personIndex < stored.count, !stored[personIndex].isEmpty {
            return stored[personIndex]
        }
        guard !presets.isEmpty else { return nil }
        let rotation = ["female_youth_18", "male_casual_25",
                        "female_casual_22",
                        "female_elegant_30", "child_girl_8"]
        let available = rotation.filter { id in presets.contains { $0.id == id } }
        if available.isEmpty {
            return presets[personIndex % presets.count].id
        }
        return available[personIndex % available.count]
    }
}
