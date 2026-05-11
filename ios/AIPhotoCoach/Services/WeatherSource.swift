// WeatherSource.swift
//
// Pluggable weather backend for the iOS client. Mirrors the
// `weather.WeatherProvider` protocol on the backend so we can:
//   - Default to Open-Meteo (free, no key, no Apple Dev account).
//   - Swap in Apple WeatherKit later without touching call sites,
//     once the team has a paid Developer Program membership.
//
// We call this at capture-start to stamp `meta.weather` directly into
// the analyze request, so the backend doesn't have to round-trip
// Open-Meteo a second time when the iOS app already had the data.

import Foundation
#if canImport(WeatherKit)
import WeatherKit
import CoreLocation
#endif

struct WeatherFacts: Sendable, Codable, Hashable {
    let cloudCoverPct: Int?
    let visibilityM: Int?
    let uvIndex: Double?
    let temperatureC: Double?
    let weatherCode: Int?
    let source: String   // "open-meteo" / "weatherkit"
}

protocol WeatherSource: Sendable {
    func fetchCurrent(lat: Double, lon: Double) async -> WeatherFacts?
}

/// Default. Free, no API key required, ~200ms typical latency.
struct OpenMeteoSource: WeatherSource {
    private static let endpoint = "https://api.open-meteo.com/v1/forecast"

    func fetchCurrent(lat: Double, lon: Double) async -> WeatherFacts? {
        var c = URLComponents(string: Self.endpoint)!
        c.queryItems = [
            URLQueryItem(name: "latitude",  value: String(lat)),
            URLQueryItem(name: "longitude", value: String(lon)),
            URLQueryItem(name: "current",
                         value: "cloud_cover,visibility,uv_index,temperature_2m,weather_code"),
        ]
        guard let url = c.url else { return nil }
        var req = URLRequest(url: url, timeoutInterval: 1.5)
        req.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let cur = payload?["current"] as? [String: Any]
            return WeatherFacts(
                cloudCoverPct: (cur?["cloud_cover"]    as? NSNumber)?.intValue,
                visibilityM:   (cur?["visibility"]     as? NSNumber)?.intValue,
                uvIndex:       (cur?["uv_index"]       as? NSNumber)?.doubleValue,
                temperatureC:  (cur?["temperature_2m"] as? NSNumber)?.doubleValue,
                weatherCode:   (cur?["weather_code"]   as? NSNumber)?.intValue,
                source:        "open-meteo",
            )
        } catch {
            return nil
        }
    }
}

#if canImport(WeatherKit)
/// Apple WeatherKit — higher accuracy, sub-100m geo, requires a paid
/// Developer Program account at compile/sign time. Identical surface
/// to `OpenMeteoSource` so callers can swap freely.
@available(iOS 16.0, *)
struct WeatherKitSource: WeatherSource {
    func fetchCurrent(lat: Double, lon: Double) async -> WeatherFacts? {
        do {
            let weather = try await WeatherService.shared.weather(
                for: CLLocation(latitude: lat, longitude: lon),
            )
            let cur = weather.currentWeather
            return WeatherFacts(
                cloudCoverPct: Int((cur.cloudCover * 100).rounded()),
                visibilityM:   Int(cur.visibility.value),
                uvIndex:       Double(cur.uvIndex.value),
                temperatureC:  cur.temperature.value,
                weatherCode:   nil,            // WeatherKit uses condition strings instead
                source:        "weatherkit",
            )
        } catch {
            return nil
        }
    }
}
#endif

/// Process-wide default. Tests inject a mock by reassigning this var.
enum WeatherSourceProvider {
    static var current: WeatherSource = OpenMeteoSource()
}
