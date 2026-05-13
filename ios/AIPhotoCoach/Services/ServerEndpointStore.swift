import Foundation
import Combine
import CryptoKit

/// Source of truth for which baseURL the app should use.
///
/// Layered design (highest precedence first):
///   * `activeOverrideURL` — admin-set local override, persisted in
///     UserDefaults. Visible only when the signed-in user has the
///     `admin` role (enforced by `AdminDashboardView`). Survives app
///     restarts so an admin can keep pointing at staging.
///   * `activeRemoteURL` — last *validated* URL received from the
///     backend at `GET /api/config/endpoint`. "Validated" = a HEAD
///     `/healthz` on the new URL returned 2xx within 5s. Anything
///     else is rejected and we keep the previous remote URL.
///   * Bundle `API_BASE_URL` (read by `APIConfig.baseURL`).
///
/// Every URLRequest reads `APIConfig.baseURL` afresh, so a change
/// here propagates to subsequent requests immediately while in-flight
/// `URLSessionDataTask`s keep the URL they were built with.
final class ServerEndpointStore: ObservableObject {
    static let shared = ServerEndpointStore()

    private let defaults = UserDefaults.standard
    private let kOverride = "v17b.endpoint.override"
    private let kRemote = "v17b.endpoint.remote"
    private let kRemoteFetchedAt = "v17b.endpoint.remote_fetched_at"

    @Published private(set) var activeOverrideRaw: String?
    @Published private(set) var activeRemoteRaw: String?
    @Published private(set) var lastSyncedAt: Date?

    private init() {
        activeOverrideRaw = defaults.string(forKey: kOverride)
        activeRemoteRaw = defaults.string(forKey: kRemote)
        if let ts = defaults.object(forKey: kRemoteFetchedAt) as? Date {
            lastSyncedAt = ts
        }
    }

    var activeOverrideURL: URL? {
        guard let raw = activeOverrideRaw, let url = URL(string: raw) else { return nil }
        return url
    }

    var activeRemoteURL: URL? {
        guard let raw = activeRemoteRaw, let url = URL(string: raw) else { return nil }
        return url
    }

    /// Set or clear the admin local override. Pass `nil` to remove.
    ///
    /// Privilege gate: silently no-ops for non-admin users. We use a
    /// silent failure (rather than `assert` / `fatalError`) so a
    /// future caller from a non-admin code path — e.g. URL-scheme
    /// deeplinks, debug menus, or accidental wiring from another
    /// view — can't accidentally point a regular user's app at a
    /// rogue server. The AdminEndpointSettingsView is itself gated
    /// behind `auth.role == "admin"` so the legitimate path still
    /// works; this is defense-in-depth.
    @discardableResult
    func setOverride(_ raw: String?) -> Bool {
        guard AuthManager.shared.role == "admin" else {
            #if DEBUG
            print("[ServerEndpointStore] setOverride denied: role=\(AuthManager.shared.role)")
            #endif
            return false
        }
        let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let t = trimmed, !t.isEmpty, URL(string: t) != nil {
            defaults.set(t, forKey: kOverride)
            activeOverrideRaw = t
        } else {
            defaults.removeObject(forKey: kOverride)
            activeOverrideRaw = nil
        }
        return true
    }

    /// sha256 of the persistent Keychain device id. Used as an
    /// opaque cardinality key when reporting endpoint telemetry to
    /// the backend so admin can compute "% of installs on new URL"
    /// without us ever transmitting the raw id.
    static func deviceFingerprint() -> String? {
        let raw = AuthManager.shared.deviceId
        guard !raw.isEmpty else { return nil }
        let digest = SHA256.hash(data: Data(raw.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    /// Called by `EndpointSyncService` after `/healthz` validation.
    func setRemote(_ raw: String) {
        defaults.set(raw, forKey: kRemote)
        defaults.set(Date(), forKey: kRemoteFetchedAt)
        activeRemoteRaw = raw
        lastSyncedAt = Date()
    }
}
