import Foundation

enum APIConfig {
    /// Resolution order, evaluated **on every URLRequest build** so a
    /// switch propagates to all *future* requests without disturbing
    /// in-flight URLSession tasks (those keep the URL they were
    /// constructed with — that's exactly the "graceful drain" we
    /// want for v17b admin-driven endpoint switching).
    ///
    /// **Internal builds** (`#if INTERNAL_BUILD`):
    ///   1. `ServerEndpointStore.activeOverride` — user-set local
    ///      override from `ServerEndpointPublicView` (Internal only).
    ///   2. `ServerEndpointStore.activeRemote` — value most recently
    ///      received from the backend `GET /api/config/endpoint`
    ///      poll and validated against `/healthz`.
    ///   3. Info.plist `API_BASE_URL` — build-time bake-in. May be
    ///      empty for Internal (then we fall back to nil).
    ///
    /// **Production builds**:
    ///   1. `ServerEndpointStore.activeRemote` — admin-driven rollout.
    ///   2. Info.plist `API_BASE_URL` — burned in from
    ///      `Config/Production.xcconfig` (CI: secret;
    ///      developer: copy of `.template`). Guaranteed non-empty
    ///      by `postCompileScripts` in `project.yml`.
    ///
    /// The current best-effort base URL.
    ///
    /// Returns a sentinel "unreachable host" URL when nothing is
    /// configured (Internal cold start before the user sets an
    /// override). The sentinel intentionally points at a non-routable
    /// RFC 5737 documentation address (`192.0.2.1`) so any code path
    /// that slips past the `isConfigured` gate fails fast and locally
    /// instead of being routed to an unrelated host — protecting
    /// users from MITM if a malicious DNS answer fills the void.
    ///
    /// Call sites that can express "未配置" as a UX state (LoginView,
    /// SettingsView, EndpointSyncService) should check
    /// `isConfigured` first and route the user to the connection
    /// settings sheet, rather than relying on URLSession's eventual
    /// timeout.
    ///
    /// New code paths should prefer `requireBaseURL()` which throws
    /// `APIConfigError.endpointNotConfigured` — that's the right
    /// shape for surfacing the state up to a view layer.
    static var baseURL: URL {
        resolvedBaseURL() ?? Self.unreachableSentinel
    }

    static func resolvedBaseURL() -> URL? {
        #if INTERNAL_BUILD
        if let override = ServerEndpointStore.shared.activeOverrideURL {
            return override
        }
        #endif
        if let remote = ServerEndpointStore.shared.activeRemoteURL {
            return remote
        }
        if let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
           !raw.isEmpty,
           let url = URL(string: raw) {
            return url
        }
        return nil
    }

    /// Convenience: `true` when at least one source has resolved a
    /// usable URL. Cheap to call from views to decide whether to
    /// show the "未配置服务器" banner / sheet.
    static var isConfigured: Bool { resolvedBaseURL() != nil }

    /// RFC 5737 documentation address — guaranteed non-routable on
    /// the public internet, so requests fail closed instead of
    /// landing somewhere unexpected.
    private static let unreachableSentinel = URL(string: "https://192.0.2.1")!

    /// The URL hard-baked into the binary — used as the absolute last
    /// fallback when everything else is unreachable (so a bad remote
    /// config can't permanently brick the app). May be `nil` in
    /// Internal builds (where the user is expected to set the
    /// override manually); production builds are guaranteed to have
    /// a non-nil value here by the build-time CI gate.
    static var bundledFallbackURL: URL? {
        if let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
           !raw.isEmpty,
           let url = URL(string: raw) {
            return url
        }
        return nil
    }

    static let connectTimeout: TimeInterval = 15
    static let requestTimeout: TimeInterval = 60

    /// Throws-flavored accessor for call sites that can propagate the
    /// "未配置" error up to a UI layer. Prefer this in new code.
    static func requireBaseURL() throws -> URL {
        guard let u = resolvedBaseURL() else { throw APIConfigError.endpointNotConfigured }
        return u
    }
}

/// Thrown by network layers when `APIConfig.baseURL` is nil. Lets
/// UI distinguish "服务器未配置（请去连接设置）" from the generic
/// HTTP / decoding errors.
enum APIConfigError: LocalizedError {
    case endpointNotConfigured

    var errorDescription: String? {
        switch self {
        case .endpointNotConfigured:
            #if INTERNAL_BUILD
            return "尚未配置服务器地址，请在「连接设置」中填入后端 URL。"
            #else
            return "服务器配置异常，请稍后再试或联系客服。"
            #endif
        }
    }
}
