import Foundation
import UIKit

/// Persists the most recent panorama capture so the user can change tone
/// settings (scene mode / style keywords / quality) on launch and re-run
/// `/analyze` without recording a fresh sweep.
///
/// Layout (all under `Documents/captured-frames/`):
///   meta.json           — { capturedAt, sceneMode, count, frames: [...] }
///   frame-000.jpg       — keyframe 0
///   frame-001.jpg       — keyframe 1
///   ...
///
/// Mirrors the web PWA's IndexedDB cache (`web/js/frames_db.js`).
@MainActor
enum CapturedFramesStore {

    // MARK: - Types --------------------------------------------------------

    struct FrameRecord: Codable {
        let index: Int
        let azimuthDeg: Double
        let pitchDeg: Double
        let rollDeg: Double
        let timestampMs: Int
        let filename: String
    }

    struct Meta: Codable {
        let capturedAt: Date
        let sceneMode: String
        let count: Int
        let frames: [FrameRecord]
    }

    struct LoadedCapture {
        let meta: Meta
        let frames: [Data]
    }

    // MARK: - Public API ---------------------------------------------------

    /// 24h freshness window. Older caches are still readable but the UI
    /// hides the reuse chip past this age.
    static let maxAge: TimeInterval = 24 * 60 * 60

    /// Persist the latest sweep, replacing any previous one. Best-effort
    /// (never throws — caller should not block on this).
    static func save(frames: [Data],
                     frameMeta: [FrameMeta],
                     sceneMode: SceneMode) {
        guard frames.count == frameMeta.count, !frames.isEmpty else { return }
        do {
            let dir = try ensureDir()
            try clearDir(dir)

            var records: [FrameRecord] = []
            for (i, blob) in frames.enumerated() {
                let name = String(format: "frame-%03d.jpg", i)
                let url = dir.appendingPathComponent(name)
                try blob.write(to: url, options: .atomic)
                let m = frameMeta[i]
                records.append(FrameRecord(
                    index: i,
                    azimuthDeg: m.azimuthDeg,
                    pitchDeg: m.pitchDeg,
                    rollDeg: m.rollDeg,
                    timestampMs: m.timestampMs,
                    filename: name
                ))
            }

            let meta = Meta(
                capturedAt: Date(),
                sceneMode: sceneMode.rawValue,
                count: frames.count,
                frames: records
            )
            let metaURL = dir.appendingPathComponent("meta.json")
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            try encoder.encode(meta).write(to: metaURL, options: .atomic)
        } catch {
            print("[CapturedFramesStore] save failed:", error)
        }
    }

    /// Cheap probe used by the wizard's review screen to decide whether to
    /// surface the "reuse" chip. Returns `nil` if no cache exists.
    static func peekMeta() -> Meta? {
        guard let dir = try? ensureDir() else { return nil }
        let metaURL = dir.appendingPathComponent("meta.json")
        guard let data = try? Data(contentsOf: metaURL) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(Meta.self, from: data)
    }

    /// Return how stale the cache is in seconds. `nil` if no cache.
    static func ageSeconds() -> TimeInterval? {
        guard let m = peekMeta() else { return nil }
        return Date().timeIntervalSince(m.capturedAt)
    }

    /// Load the cached frames + meta into memory ready for an /analyze call.
    static func load() -> LoadedCapture? {
        guard let dir = try? ensureDir(), let meta = peekMeta() else { return nil }
        var datas: [Data] = []
        datas.reserveCapacity(meta.frames.count)
        for rec in meta.frames {
            let url = dir.appendingPathComponent(rec.filename)
            guard let data = try? Data(contentsOf: url) else { return nil }
            datas.append(data)
        }
        return LoadedCapture(meta: meta, frames: datas)
    }

    static func clear() {
        guard let dir = try? ensureDir() else { return }
        try? clearDir(dir)
    }

    /// e.g. "5 分钟前" / "2 小时前" / "1 天前".
    static func relativeAge(_ ageSec: TimeInterval) -> String {
        let s = Int(ageSec)
        if s < 60 { return "刚刚" }
        if s < 3600 { return "\(s / 60) 分钟前" }
        if s < 86400 { return "\(s / 3600) 小时前" }
        return "\(s / 86400) 天前"
    }

    // MARK: - Private ------------------------------------------------------

    private static func ensureDir() throws -> URL {
        let docs = try FileManager.default.url(
            for: .documentDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true)
        let dir = docs.appendingPathComponent("captured-frames", isDirectory: true)
        if !FileManager.default.fileExists(atPath: dir.path) {
            try FileManager.default.createDirectory(
                at: dir, withIntermediateDirectories: true)
        }
        return dir
    }

    private static func clearDir(_ dir: URL) throws {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil) else { return }
        for item in items {
            try? FileManager.default.removeItem(at: item)
        }
    }
}
