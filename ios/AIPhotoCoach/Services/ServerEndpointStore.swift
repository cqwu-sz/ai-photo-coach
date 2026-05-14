import Foundation
import Combine
import CryptoKit

/// Source of truth for which baseURL the app should use.
///
/// Layered design (highest precedence first):
///   * `activeOverrideURL` — local manual override, only available in
///     **Internal builds** (`#if INTERNAL_BUILD`). Persisted in
///     UserDefaults. Set from `ServerEndpointPublicView` after a
///     successful `/healthz` probe. Survives app restarts.
///   * `activeRemoteURL` — last *validated* URL received from the
///     backend at `GET /api/config/endpoint`. "Validated" = a HEAD
///     `/healthz` on the new URL returned 2xx within 5s. Anything
///     else is rejected and we keep the previous remote URL.
///   * Bundle `API_BASE_URL` (read by `APIConfig.baseURL`).
///
/// Every URLRequest reads `APIConfig.baseURL` afresh, so a change
/// here propagates to subsequent requests immediately while in-flight
/// `URLSessionDataTask`s keep the URL they were built with.
///
/// **Security model**: the override is gated by *input validation*,
/// not by role. Reason: the override only affects the device the
/// human is physically holding, and only after they tapped through
/// our `/healthz` test. Treating it as an admin-only feature created
/// the chicken-and-egg "you must log in first, but to log in you
/// must reach the server" problem. Production builds remove the
/// entire override surface at compile time, so there is no attack
/// surface to defend in App Store binaries.
final class ServerEndpointStore: ObservableObject {
    static let shared = ServerEndpointStore()

    private let defaults = UserDefaults.standard
    private let kOverride = "v17b.endpoint.override"
    private let kRemote = "v17b.endpoint.remote"
    private let kRemoteFetchedAt = "v17b.endpoint.remote_fetched_at"
    private let kHistory = "v18.endpoint.override_history"

    @Published private(set) var activeOverrideRaw: String?
    @Published private(set) var activeRemoteRaw: String?
    @Published private(set) var lastSyncedAt: Date?
    @Published private(set) var overrideHistory: [OverrideHistoryEntry] = []

    private init() {
        activeOverrideRaw = defaults.string(forKey: kOverride)
        activeRemoteRaw = defaults.string(forKey: kRemote)
        if let ts = defaults.object(forKey: kRemoteFetchedAt) as? Date {
            lastSyncedAt = ts
        }
        overrideHistory = Self.loadHistory(from: defaults, key: kHistory)
    }

    var activeOverrideURL: URL? {
        guard let raw = activeOverrideRaw, let url = URL(string: raw) else { return nil }
        return url
    }

    var activeRemoteURL: URL? {
        guard let raw = activeRemoteRaw, let url = URL(string: raw) else { return nil }
        return url
    }

    /// sha256 of the persistent Keychain device id. Used as an
    /// opaque cardinality key when reporting endpoint telemetry to
    /// the backend so admin can compute "% of installs on new URL"
    /// without us ever transmitting the raw id.
    @MainActor
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

    // MARK: - History (always present so views can compile; only written from Internal)

    fileprivate static func loadHistory(from d: UserDefaults, key: String) -> [OverrideHistoryEntry] {
        guard let data = d.data(forKey: key),
              let decoded = try? JSONDecoder().decode([OverrideHistoryEntry].self, from: data) else {
            return []
        }
        return decoded
    }

    fileprivate func saveHistory() {
        if let data = try? JSONEncoder().encode(overrideHistory) {
            defaults.set(data, forKey: kHistory)
        }
    }

    fileprivate func clearOverrideStorage() {
        defaults.removeObject(forKey: kOverride)
        activeOverrideRaw = nil
    }

    fileprivate func writeOverrideStorage(_ url: String) {
        defaults.set(url, forKey: kOverride)
        activeOverrideRaw = url
    }
}

/// Persisted entry recording every override change. Kept as a 5-item
/// ring buffer so an Internal-build user can recover from "I pointed
/// at a dead host and now I can't reach login".
struct OverrideHistoryEntry: Codable, Identifiable, Equatable {
    let id: UUID
    let url: String?       // nil = cleared
    let appliedAt: Date
    let healthzOk: Bool

    init(url: String?, appliedAt: Date = Date(), healthzOk: Bool) {
        self.id = UUID()
        self.url = url
        self.appliedAt = appliedAt
        self.healthzOk = healthzOk
    }
}

// MARK: - INTERNAL_BUILD-only override API
//
// Everything below is removed from Production binaries at compile
// time. Even the symbols disappear (the postCompileScripts in
// project.yml will fail the Release build if any of these names
// show up in the linked binary).

#if INTERNAL_BUILD

enum OverrideValidationError: LocalizedError {
    case empty
    case malformed
    case schemeNotAllowed(String)
    case hostMissing
    case hostBlocklisted(String)
    case insecureHostNotAllowed(String)

    var errorDescription: String? {
        switch self {
        case .empty:                       return "请输入服务器地址"
        case .malformed:                   return "URL 格式不合法"
        case .schemeNotAllowed(let s):     return "不允许的协议：\(s) (仅支持 https，或本机网络下的 http)"
        case .hostMissing:                 return "URL 缺少主机名"
        case .hostBlocklisted(let h):      return "主机 \(h) 在禁用名单中（云元数据端点）"
        case .insecureHostNotAllowed(let h): return "HTTP 仅允许指向本机/局域网；\(h) 不符合"
        }
    }
}

extension ServerEndpointStore {
    /// Validate + persist a new local override (Internal-only path).
    /// Pass `nil` to clear.
    ///
    /// Returns the parsed canonical string on success so the UI can
    /// echo back exactly what got stored.
    @MainActor
    @discardableResult
    func setOverrideForInternalBuild(_ raw: String?, healthzOk: Bool) -> Result<String?, OverrideValidationError> {
        let oldRaw = activeOverrideRaw
        let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines)

        if trimmed == nil || trimmed?.isEmpty == true {
            // Clear path.
            clearOverrideStorage()
            appendHistory(.init(url: nil, healthzOk: false))
            reportOverrideAudit(oldURL: oldRaw, newURL: nil, healthzOk: false)
            return .success(nil)
        }

        switch Self.validate(trimmed!) {
        case .failure(let err):
            return .failure(err)
        case .success(let canonical):
            writeOverrideStorage(canonical)
            appendHistory(.init(url: canonical, healthzOk: healthzOk))
            reportOverrideAudit(oldURL: oldRaw, newURL: canonical, healthzOk: healthzOk)
            return .success(canonical)
        }
    }

    /// Fire-and-forget audit ping so backend support can trace
    /// "device X switched its endpoint at time Y". POSTs to whichever
    /// baseURL is *currently* resolvable — typically the new override
    /// itself (which already passed /healthz), so the ping has a real
    /// chance of landing. Failures are completely silent.
    @MainActor
    private func reportOverrideAudit(oldURL: String?, newURL: String?, healthzOk: Bool) {
        guard let base = APIConfig.resolvedBaseURL() else { return }
        let url = base.appendingPathComponent("api/telemetry/endpoint_override")
        let fp = Self.deviceFingerprint()
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        var body: [String: Any] = [
            "healthz_ok": healthzOk,
            "source": "internal_ui",
        ]
        if let fp { body["device_fp"] = fp }
        if let oldURL { body["old_url"] = oldURL }
        if let newURL { body["new_url"] = newURL }
        if let version { body["app_version"] = version }
        guard let payload = try? JSONSerialization.data(withJSONObject: body) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 4
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let version { req.setValue(version, forHTTPHeaderField: "X-App-Version") }
        req.httpBody = payload
        Task.detached {
            _ = try? await URLSession.shared.data(for: req)
        }
    }

    /// Pure-input validator. Exposed for unit tests and the "Test
    /// Connection" button (which validates form before probing).
    static func validate(_ raw: String) -> Result<String, OverrideValidationError> {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .failure(.empty) }
        guard let comps = URLComponents(string: trimmed),
              let scheme = comps.scheme?.lowercased() else {
            return .failure(.malformed)
        }
        guard scheme == "http" || scheme == "https" else {
            return .failure(.schemeNotAllowed(scheme))
        }
        guard let host = comps.host, !host.isEmpty else {
            return .failure(.hostMissing)
        }
        let lowerHost = host.lowercased()
        // Cloud metadata endpoints. SSRF defense even though the only
        // person who can set this is the user themselves — protects
        // them from a phishing link that pre-fills the field.
        let blocked = [
            "169.254.169.254",
            "metadata.google.internal",
            "metadata.azure.com",
            "100.100.100.200",        // Aliyun ECS metadata
        ]
        if blocked.contains(lowerHost) {
            return .failure(.hostBlocklisted(lowerHost))
        }
        if scheme == "http" && !isPrivateOrLoopback(lowerHost) {
            return .failure(.insecureHostNotAllowed(lowerHost))
        }
        // Canonicalize: strip trailing slash so equality checks behave.
        var canonical = trimmed
        while canonical.hasSuffix("/") {
            canonical.removeLast()
        }
        return .success(canonical)
    }

    private static func isPrivateOrLoopback(_ host: String) -> Bool {
        if host == "localhost" || host.hasSuffix(".local") { return true }
        // IPv4 RFC1918 + loopback. Coarse string match is fine for our
        // narrow use; we're not building a generic firewall.
        let parts = host.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4, parts.allSatisfy({ (0...255).contains($0) }) else {
            return false
        }
        if parts[0] == 127 { return true }
        if parts[0] == 10 { return true }
        if parts[0] == 192 && parts[1] == 168 { return true }
        if parts[0] == 172 && (16...31).contains(parts[1]) { return true }
        return false
    }

    @MainActor
    private func appendHistory(_ entry: OverrideHistoryEntry) {
        var next = overrideHistory
        next.insert(entry, at: 0)
        if next.count > 5 { next = Array(next.prefix(5)) }
        overrideHistory = next
        saveHistory()
    }

    /// Probe `/healthz` to confirm the URL is reachable before we
    /// adopt it. 5s timeout; 2xx → ok. Surfaced from
    /// `ServerEndpointPublicView`'s "Test connection" button.
    func probeHealthz(_ raw: String, timeout: TimeInterval = 5) async -> Bool {
        guard case .success(let canonical) = Self.validate(raw),
              let url = URL(string: canonical + "/healthz") else {
            return false
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = timeout
        req.httpMethod = "GET"
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else { return false }
            return (200..<300).contains(http.statusCode)
        } catch {
            return false
        }
    }
}

#endif
