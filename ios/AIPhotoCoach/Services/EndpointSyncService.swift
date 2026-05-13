import Foundation

/// Periodically polls the backend for the canonical baseURL and
/// updates `ServerEndpointStore` when it changes.
///
/// Safety rails (so a bad config can never permanently brick the app):
///   * **Health-probe before accept**: if the new URL doesn't return
///     2xx on `/healthz` within 5s, we keep the old one.
///   * **Stick to current URL when poll fails**: a transient network
///     blip will never wipe `activeRemoteURL`.
///   * **Bundled fallback** (`APIConfig.bundledFallbackURL`) is the
///     final layer; if even that fails the user sees normal offline
///     errors, which is the right UX.
///
/// Polled from the URL we *currently* believe — so a server that
/// switched URLs can tell us about the move on the next request,
/// not just at cold start.
@MainActor
final class EndpointSyncService {
    static let shared = EndpointSyncService()

    private let pollInterval: TimeInterval = 300   // 5 min
    private let healthTimeout: TimeInterval = 5
    private var timer: Timer?
    private var inFlight = false

    private init() {}

    func start() {
        guard timer == nil else { return }
        // Cold-start sync (don't block UI).
        Task { await self.syncOnce() }
        let t = Timer(timeInterval: pollInterval, repeats: true) { [weak self] _ in
            Task { await self?.syncOnce() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    @MainActor
    func syncOnce() async {
        if inFlight { return }
        inFlight = true
        defer { inFlight = false }
        do {
            let cfg = try await fetchConfig()
            // Skip when remote == active — saves an unnecessary probe.
            if cfg.primaryURL == ServerEndpointStore.shared.activeRemoteRaw {
                return
            }
            // Probe the new URL. Reject silently on failure.
            guard let probeURL = URL(string: cfg.primaryURL)?
                    .appendingPathComponent("healthz") else { return }
            var req = URLRequest(url: probeURL)
            req.timeoutInterval = healthTimeout
            req.httpMethod = "GET"
            let (_, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return
            }
            ServerEndpointStore.shared.setRemote(cfg.primaryURL)
        } catch {
            // Silent — keep previous URL.
        }
    }

    private struct EndpointDTO: Decodable {
        let primary_url: String
        let fallback_url: String?
        let rollout_percentage: Int?
    }

    private struct EndpointResolved {
        let primaryURL: String
    }

    /// Deterministic device bucket [0, 99]. Same fp → same bucket
    /// across polls, so a user doesn't flap between URLs as the
    /// admin tweaks the rollout.
    private func deviceBucket() -> Int {
        guard let fp = ServerEndpointStore.deviceFingerprint() else { return 0 }
        // sha256 prefix → 8 hex → mod 100. Plenty of entropy for our N.
        let prefix = String(fp.prefix(8))
        let n = UInt64(prefix, radix: 16) ?? 0
        return Int(n % 100)
    }

    private func resolveTargetURL(from dto: EndpointDTO) -> String {
        let pct = max(0, min(dto.rollout_percentage ?? 100, 100))
        if pct >= 100 || dto.fallback_url == nil { return dto.primary_url }
        return deviceBucket() < pct ? dto.primary_url : dto.fallback_url!
    }

    private func fetchConfig() async throws -> EndpointResolved {
        let url = APIConfig.baseURL.appendingPathComponent("api/config/endpoint")
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        // Telemetry headers — let admin see rollout progress.
        // Cheap, non-PII (fp is sha256 of an opaque device id).
        req.setValue(APIConfig.baseURL.absoluteString,
                      forHTTPHeaderField: "X-Active-Endpoint")
        if let fp = ServerEndpointStore.deviceFingerprint() {
            req.setValue(fp, forHTTPHeaderField: "X-Device-Fp")
        }
        if let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String {
            req.setValue(v, forHTTPHeaderField: "X-App-Version")
        }
        let (data, _) = try await URLSession.shared.data(for: req)
        let dto = try JSONDecoder().decode(EndpointDTO.self, from: data)
        return EndpointResolved(primaryURL: resolveTargetURL(from: dto))
    }
}
