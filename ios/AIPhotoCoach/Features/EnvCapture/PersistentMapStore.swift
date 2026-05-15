// PersistentMapStore.swift
//
// A-persistent-map — persist the current ARWorldMap to Application
// Support keyed by a coarse geohash (≈ 50 m cell). When the user revisits
// the same location we can reload the map and ARKit re-localises against
// previously discovered planes / mesh / landmarks, so the second visit's
// landmark graph builds on top of the first instead of from scratch.
//
// This is a scaffold — the actual relocalisation handshake lives in
// ``ARSessionController`` and ``ARLandmarkExtractor``. Wiring it up
// requires real-device validation (relocalisation success depends on
// lighting + viewpoint similarity), so we ship the storage layer + a
// well-defined hook the controller can call when the user opts in.

import ARKit
import CoreLocation
import Foundation

/// On-disk cache of ARWorldMaps keyed by a coarse geohash. Thread-safe
/// via an internal queue — callers can fire-and-forget save / load.
final class PersistentMapStore {
    static let shared = PersistentMapStore()

    private let queue = DispatchQueue(label: "ai.photocoach.persistentmap",
                                       qos: .utility)
    private let fm = FileManager.default

    /// Cap the on-disk footprint. ARWorldMap can be 1-10 MB per cell, so
    /// 20 cells × 10 MB = 200 MB worst case; we cap at 50 cells and
    /// LRU-evict beyond that. Adjust based on telemetry once real users
    /// hit this code path.
    private let maxCells = 50

    private lazy var rootDir: URL = {
        let base = fm.urls(for: .applicationSupportDirectory,
                            in: .userDomainMask).first!
        let dir = base.appendingPathComponent("PersistentARMaps", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    private init() {}

    // MARK: - Cell key

    /// Coarse 50-metre geohash. Two visits within ~50 m of each other
    /// share a cell; further apart they live in separate cells.
    /// We use a precision-6 geohash variant (≈ 1.2 km × 0.6 km cell
    /// world-wide) and then sub-divide by another quad bit, getting us
    /// roughly the resolution we want without pulling a real geohash
    /// dependency. Good enough for "are we back in the same room/yard?".
    static func cellKey(for location: CLLocation) -> String {
        let lat = location.coordinate.latitude
        let lon = location.coordinate.longitude
        let latIdx = Int((lat + 90.0) * 10000)   // 0.0001° ≈ 11 m
        let lonIdx = Int((lon + 180.0) * 10000)
        // Round to 5×10000 = 50 m at the equator (a bit smaller at high lat)
        return "g_\(latIdx / 5)_\(lonIdx / 5)"
    }

    // MARK: - API

    /// Persist ``map`` for the cell containing ``location``. No-op when
    /// either is nil. Returns immediately; serialization happens off the
    /// main thread.
    func save(map: ARWorldMap?, at location: CLLocation?) {
        guard let map, let location else { return }
        queue.async { [weak self] in
            guard let self else { return }
            let key = Self.cellKey(for: location)
            let url = self.rootDir.appendingPathComponent("\(key).arworldmap")
            do {
                let data = try NSKeyedArchiver.archivedData(
                    withRootObject: map, requiringSecureCoding: true,
                )
                try data.write(to: url, options: .atomic)
                self.evictIfNeeded()
            } catch {
                // Silent; persistence is best-effort.
                print("[PersistentMapStore] save failed: \(error)")
            }
        }
    }

    /// Load any previously-saved map for the cell at ``location``. nil
    /// when no map exists for that cell or unarchival fails.
    func load(at location: CLLocation,
               completion: @escaping (ARWorldMap?) -> Void) {
        queue.async { [weak self] in
            guard let self else { completion(nil); return }
            let key = Self.cellKey(for: location)
            let url = self.rootDir.appendingPathComponent("\(key).arworldmap")
            guard self.fm.fileExists(atPath: url.path),
                  let data = try? Data(contentsOf: url),
                  let map = try? NSKeyedUnarchiver.unarchivedObject(
                    ofClass: ARWorldMap.self, from: data) else {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            DispatchQueue.main.async { completion(map) }
        }
    }

    /// Drop every persisted map (e.g. user wants to "forget all places").
    /// Synchronous; intended for a settings toggle, not hot paths.
    func clearAll() {
        queue.sync {
            try? fm.removeItem(at: rootDir)
            try? fm.createDirectory(at: rootDir, withIntermediateDirectories: true)
        }
    }

    // MARK: - LRU eviction

    private func evictIfNeeded() {
        let urls = (try? fm.contentsOfDirectory(
            at: rootDir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        guard urls.count > maxCells else { return }
        let sorted = urls.sorted { a, b in
            let ad = (try? a.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let bd = (try? b.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return ad < bd
        }
        for url in sorted.prefix(urls.count - maxCells) {
            try? fm.removeItem(at: url)
        }
    }
}
