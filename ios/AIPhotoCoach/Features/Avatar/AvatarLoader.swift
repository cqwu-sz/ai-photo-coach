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

    enum CodingKeys: String, CodingKey {
        case single
        case twoPerson = "two_person"
        case threePerson = "three_person"
        case fourPerson = "four_person"
        case fallbackByCount = "fallback_by_count"
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
            self.payload = parsed
            return parsed
        } catch {
            lastError = error.localizedDescription
            print("[AvatarManifest] load failed:", error)
            return nil
        }
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
            let entity = try await Entity(named: candidate)
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
            let entity = try await Entity(named: candidate)
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
        let rotation = ["female_casual_22", "male_casual_25",
                        "female_elegant_30", "child_girl_8"]
        let available = rotation.filter { id in presets.contains { $0.id == id } }
        if available.isEmpty {
            return presets[personIndex % presets.count].id
        }
        return available[personIndex % available.count]
    }
}
