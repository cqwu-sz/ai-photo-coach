// LocationProvider.swift
//
// One-shot geolocation helper for analyze requests. Used only when the
// active scene mode benefits from sun/weather context (today: .lightShadow).
// We never start continuous updates — a single fix is enough for the LLM
// to decide rim-light direction and golden-hour countdowns.
//
// The fix is cached for 6h in UserDefaults so we don't pester the user
// every time they re-analyze with the same location. Nothing leaves the
// device until the analyze request itself.

import Foundation
@preconcurrency import CoreLocation

/// 6-hour staleness window — same as the web side.
private let GEO_MAX_AGE: TimeInterval = 6 * 60 * 60

@MainActor
final class LocationProvider: NSObject {
    static let shared = LocationProvider()

    private let manager: CLLocationManager
    private var pending: CheckedContinuation<GeoFix?, Never>?
    private var resolved = false

    override init() {
        self.manager = CLLocationManager()
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    // MARK: - Public API

    /// Returns a cached fix if it's < 6h old, otherwise asks the OS for a
    /// single fresh fix. Returns ``nil`` if the user denies permission, the
    /// device has no GPS, or the request times out (12s).
    func ensureGeoFix(forceFresh: Bool = false) async -> GeoFix? {
        if !forceFresh, let cached = readCache(), Date().timeIntervalSince(cached.timestamp ?? .distantPast) < GEO_MAX_AGE {
            return cached
        }

        // Request authorization synchronously if needed; we use when-in-use
        // because the camera is also in-foreground when this runs.
        let status = manager.authorizationStatus
        if status == .notDetermined {
            manager.requestWhenInUseAuthorization()
        }
        if status == .denied || status == .restricted {
            return nil
        }

        return await withCheckedContinuation { (cont: CheckedContinuation<GeoFix?, Never>) in
            self.pending = cont
            self.resolved = false
            manager.requestLocation()

            // Hard-cap the wait at 12s so a slow GPS doesn't stall analyze.
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 12_000_000_000)
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    if !self.resolved {
                        self.resolved = true
                        self.pending?.resume(returning: nil)
                        self.pending = nil
                    }
                }
            }
        }
    }

    func clearCache() {
        UserDefaults.standard.removeObject(forKey: "aphc.geofix")
    }

    // MARK: - Cache (UserDefaults JSON blob)

    private func readCache() -> GeoFix? {
        guard let data = UserDefaults.standard.data(forKey: "aphc.geofix") else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(GeoFix.self, from: data)
    }

    private func writeCache(_ fix: GeoFix) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(fix) {
            UserDefaults.standard.set(data, forKey: "aphc.geofix")
        }
    }
}

// MARK: - CLLocationManagerDelegate

extension LocationProvider: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let fix = GeoFix(
            lat: round4(loc.coordinate.latitude),
            lon: round4(loc.coordinate.longitude),
            accuracyM: loc.horizontalAccuracy >= 0 ? loc.horizontalAccuracy : nil,
            timestamp: loc.timestamp
        )
        Task { @MainActor [weak self] in
            guard let self else { return }
            if !self.resolved {
                self.resolved = true
                self.writeCache(fix)
                self.pending?.resume(returning: fix)
                self.pending = nil
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didFailWithError error: Error) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            if !self.resolved {
                self.resolved = true
                self.pending?.resume(returning: nil)
                self.pending = nil
            }
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        // No-op: ensureGeoFix() drives requests. We just need the delegate
        // hooked up so the manager doesn't drop the authorization callback.
    }
}

// Local helper — keeps lat/lon at ~11m precision before persisting.
private func round4(_ x: Double) -> Double {
    (x * 10_000).rounded() / 10_000
}
