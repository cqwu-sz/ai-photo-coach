import Foundation

enum APIConfig {
    /// Resolution order, evaluated **on every URLRequest build** so a
    /// switch propagates to all *future* requests without disturbing
    /// in-flight URLSession tasks (those keep the URL they were
    /// constructed with — that's exactly the "graceful drain" we
    /// want for v17b admin-driven endpoint switching).
    ///
    /// 1. `ServerEndpointStore.activeOverride` — admin's local override
    ///    (only settable from AdminDashboardView).
    /// 2. `ServerEndpointStore.activeRemote` — value most recently
    ///    received from the backend `GET /api/config/endpoint` poll
    ///    and validated against `/healthz`.
    /// 3. Info.plist `API_BASE_URL` — build-time bake-in (per-scheme).
    /// 4. Hard-coded localhost — debug last-resort.
    static var baseURL: URL {
        if let override = ServerEndpointStore.shared.activeOverrideURL {
            return override
        }
        if let remote = ServerEndpointStore.shared.activeRemoteURL {
            return remote
        }
        if let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }

    /// The URL hard-baked into the binary — used as the absolute last
    /// fallback when everything else is unreachable (so a bad remote
    /// config can't permanently brick the app).
    static var bundledFallbackURL: URL {
        if let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }

    static let connectTimeout: TimeInterval = 15
    static let requestTimeout: TimeInterval = 60
}
