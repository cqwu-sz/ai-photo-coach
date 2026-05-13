// AuthManager.swift  (A0-10 of MULTI_USER_AUTH)
//
// Bridges iOS ↔ backend /auth/* endpoints. Responsibilities:
//   1. Persist a stable Keychain UUID as our `device_id`
//   2. Bootstrap an anonymous user on first launch
//   3. Hold the access + refresh JWTs and refresh on 401
//   4. Optionally upgrade to Sign in with Apple (`signInWithApple()`)
//   5. Wipe everything on `signOut()` / `deleteAccount()`
//
// Usage:
//      let token = try await AuthManager.shared.accessToken()  // refreshes if needed
//      var req = URLRequest(url: ...)
//      req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
//
// Security:
//   - device_id, access_token, refresh_token all live in Keychain via
//     `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` — survives
//     reboot but not iCloud restore (so a stolen backup can't replay).
//   - We never log tokens.

import Foundation
import Security
import AuthenticationServices

enum AuthError: LocalizedError {
    case notAuthenticated
    case server(message: String, code: Int)

    var errorDescription: String? {
        switch self {
        case .notAuthenticated: return "请先登录"
        case .server(let m, _):  return m
        }
    }
}

@MainActor
final class AuthManager: NSObject, ObservableObject {
    static let shared = AuthManager()

    // MARK: - Published state (drives UI)
    @Published private(set) var userId: String?
    @Published private(set) var isAnonymous: Bool = true
    @Published private(set) var tier: String = "free"
    @Published private(set) var role: String = "user"
    @Published private(set) var lastError: String?

    /// True when there is no usable session and the LoginView should
    /// take over the root. Updated whenever tokens change.
    @Published private(set) var isAuthenticated: Bool = false

    // MARK: - Tokens / device id
    private var accessTokenValue: String?
    private var refreshTokenValue: String?
    private var accessExpiry: Date = .distantPast

    private let kcService = "ai.photocoach.auth"
    private enum KCKey: String {
        case deviceId      = "device_id"
        case accessToken   = "access_token"
        case refreshToken  = "refresh_token"
        case userId        = "user_id"
    }

    private override init() {
        super.init()
        loadFromKeychain()
        isAuthenticated = (accessTokenValue != nil && refreshTokenValue != nil)
    }

    // MARK: - Public API

    /// Stable id for this install. Rebuilt only after `deleteAccount`.
    var deviceId: String {
        if let v = readKC(.deviceId), !v.isEmpty { return v }
        let v = UUID().uuidString
        writeKC(.deviceId, v)
        return v
    }

    /// Returns a fresh access token, refreshing if expired.
    /// v17: no longer auto-creates an anonymous session — callers must
    /// have driven the user through `LoginView` first. Throws
    /// `AuthError.notAuthenticated` so UI layers can route to login.
    func accessToken() async throws -> String {
        guard accessTokenValue != nil, refreshTokenValue != nil else {
            isAuthenticated = false
            throw AuthError.notAuthenticated
        }
        if Date().addingTimeInterval(60) >= accessExpiry, refreshTokenValue != nil {
            try await refreshIfPossible()
        }
        guard let t = accessTokenValue else {
            isAuthenticated = false
            throw AuthError.notAuthenticated
        }
        return t
    }

    /// Send an OTP via SMS or email. `target` is the phone (e.g. 13800000000)
    /// or the email address.
    func requestOtp(channel: String, target: String) async throws {
        // v17c — attach App Attest assertion when available so the
        // backend can verify request really came from a real install.
        // Backend currently in shadow mode; once enforce flips on
        // we still won't break: a failed assertion → header missing,
        // backend returns `attest_required`, friendlyMessage gives
        // user a "请升级 App" prompt.
        let attestHeaders = await AppAttestManager.shared.assertionHeaders(for: target)
        do {
            _ = try await postJSON(
                path: "/auth/otp/request",
                body: ["channel": channel, "target": target],
                extraHeaders: attestHeaders,
            )
        } catch let err as NSError {
            // v17d: if backend says attest is invalid/missing, try one
            // more time with a freshly-generated key. Common cause:
            // user reinstalled / changed env — old keyId is stale.
            let code = (err.userInfo["server_code"] as? String) ?? ""
            if code == "attest_invalid" || code == "attest_required" {
                _ = await AppAttestManager.shared.forceReBootstrap()
                let retryHeaders = await AppAttestManager.shared
                    .assertionHeaders(for: target)
                _ = try await postJSON(
                    path: "/auth/otp/request",
                    body: ["channel": channel, "target": target],
                    extraHeaders: retryHeaders,
                )
                return
            }
            throw err
        }
    }

    /// Verify the OTP code; on success applies the new token pair so
    /// the rest of the app sees `isAuthenticated == true`.
    func verifyOtp(channel: String, target: String, code: String) async throws {
        let pair = try await postJSON(
            path: "/auth/otp/verify",
            body: ["channel": channel, "target": target, "code": code],
        )
        applyPair(pair)
    }

    func refreshIfPossible() async throws {
        guard let r = refreshTokenValue else { return }
        do {
            let pair = try await postJSON(
                path: "/auth/refresh",
                body: ["refresh_token": r],
            )
            applyPair(pair)
        } catch {
            // v17: no auto-anonymous fallback. Clear so the LoginView
            // takes over the next time the UI checks `isAuthenticated`.
            clearAll()
            throw AuthError.notAuthenticated
        }
    }

    /// Trigger Sign in with Apple. Returns when the upgrade completes
    /// or throws on user cancel / Apple error.
    func signInWithApple() async throws {
        let identityToken = try await SiwaCoordinator.run()
        let pair = try await postJSON(
            path: "/auth/siwa",
            body: [
                "identity_token": identityToken,
                "device_id": deviceId,
            ],
        )
        applyPair(pair)
    }

    /// Server-side revoke + local Keychain wipe. v17: the user is sent
    /// back to the LoginView; we do not auto-create an anonymous one.
    func signOut() async {
        if let r = refreshTokenValue {
            _ = try? await postJSON(path: "/auth/logout", body: ["refresh_token": r])
        }
        clearAll()
    }

    /// DELETE /users/me + local wipe. Apple 5.1.1(v).
    func deleteAccount() async throws {
        guard let token = accessTokenValue else { return }
        var req = makeRequest(path: "/users/me")
        req.httpMethod = "DELETE"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (_, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            throw NSError(domain: "AuthManager", code: 500,
                          userInfo: [NSLocalizedDescriptionKey: "Delete failed"])
        }
        clearAll()
    }

    // MARK: - Internals

    private func applyPair(_ obj: [String: Any]) {
        accessTokenValue = obj["access_token"] as? String
        refreshTokenValue = obj["refresh_token"] as? String
        let uid = (obj["user_id"] as? String) ?? ""
        userId = uid.isEmpty ? nil : uid
        isAnonymous = (obj["is_anonymous"] as? Bool) ?? true
        tier = (obj["tier"] as? String) ?? "free"
        role = (obj["role"] as? String) ?? "user"
        // Best-effort expiry parse; default 14 min if missing.
        if let s = obj["access_expires_at"] as? String,
           let d = ISO8601DateFormatter().date(from: s) {
            accessExpiry = d
        } else {
            accessExpiry = Date().addingTimeInterval(14 * 60)
        }
        if let t = accessTokenValue { writeKC(.accessToken, t) }
        if let t = refreshTokenValue { writeKC(.refreshToken, t) }
        if let u = userId { writeKC(.userId, u) }
        isAuthenticated = (accessTokenValue != nil && refreshTokenValue != nil)
    }

    private func clearAll() {
        accessTokenValue = nil
        refreshTokenValue = nil
        accessExpiry = .distantPast
        userId = nil
        isAnonymous = true
        tier = "free"
        role = "user"
        isAuthenticated = false
        deleteKC(.accessToken)
        deleteKC(.refreshToken)
        deleteKC(.userId)
        // device_id intentionally NOT wiped — the user can still create
        // a fresh anonymous account bound to the same device. Wipe it
        // only if the user explicitly factory-resets the app.
    }

    private func loadFromKeychain() {
        accessTokenValue = readKC(.accessToken)
        refreshTokenValue = readKC(.refreshToken)
        userId = readKC(.userId)
    }

    private func makeRequest(path: String) -> URLRequest {
        let url = APIConfig.baseURL.appendingPathComponent(String(path.drop(while: { $0 == "/" })))
        var req = URLRequest(url: url)
        req.timeoutInterval = APIConfig.connectTimeout
        return req
    }

    /// Map server error codes (`{detail:{error:{code}}}`) to UX-grade
    /// Chinese messages. Centralised so views don't drift. v17c.
    static func friendlyMessage(code: String, fallback: String, http: Int) -> String {
        switch code {
        case "otp_cooldown":
            return "请稍候 1 分钟再获取验证码。"
        case "otp_target_locked", "otp_too_many_failures":
            return "尝试次数过多，账号已临时锁定 3 小时，请稍后再试。"
        case "otp_code_invalid", "otp_code_mismatch":
            return "验证码不正确，剩余尝试次数有限。"
        case "otp_code_expired":
            return "验证码已过期，请重新获取。"
        case "otp_ip_throttled":
            return "当前网络环境短时间内尝试了过多账号。\n请切换到 4G/5G 蜂窝网络后重试。"
        case "otp_daily_target_exhausted":
            return "今日该号码请求验证码次数已达上限，请明天再试。"
        case "otp_daily_ip_exhausted":
            return "今日该网络请求验证码次数已达上限，请切换网络或明天再试。"
        case "otp_service_busy":
            return "短信服务繁忙，请 1 分钟后再试。"
        case "otp_target_blocked", "otp_ip_blocked", "user_blocked", "ip_blocked":
            return "该账号或网络已被封禁，如有疑问请联系客服。"
        case "rate_limited", "auth_rate_limited":
            return "请求过于频繁，请稍后再试。"
        default:
            if !fallback.isEmpty { return fallback }
            return "操作失败 (\(http))，请稍后重试。"
        }
    }

    private func postJSON(path: String, body: [String: Any],
                            extraHeaders: [String: String] = [:]) async throws -> [String: Any] {
        var req = makeRequest(path: path)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (k, v) in extraHeaders { req.setValue(v, forHTTPHeaderField: k) }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw NSError(domain: "AuthManager", code: 0)
        }
        guard (200..<300).contains(http.statusCode) else {
            // Map our standard `{detail: {error: {code, message}}}`
            // envelope into a friendly localized string + retain the
            // server code so views can branch on it (e.g. show a
            // network-switch hint on `otp_ip_throttled`).
            var serverCode = ""
            var serverMsg = ""
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? [String: Any],
               let err = detail["error"] as? [String: Any] {
                serverCode = (err["code"] as? String) ?? ""
                serverMsg = (err["message"] as? String) ?? ""
            }
            let friendly = AuthManager.friendlyMessage(
                code: serverCode, fallback: serverMsg, http: http.statusCode
            )
            self.lastError = friendly
            throw NSError(domain: "AuthManager", code: http.statusCode,
                          userInfo: [
                            NSLocalizedDescriptionKey: friendly,
                            "server_code": serverCode,
                          ])
        }
        return (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    // MARK: - Keychain helpers

    private func writeKC(_ key: KCKey, _ value: String) {
        let q: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  kcService,
            kSecAttrAccount as String:  key.rawValue,
        ]
        let attrs: [String: Any] = [
            kSecValueData as String:    Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemUpdate(q as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            var add = q
            add.merge(attrs) { _, new in new }
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    private func readKC(_ key: KCKey) -> String? {
        let q: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  kcService,
            kSecAttrAccount as String:  key.rawValue,
            kSecReturnData as String:   true,
            kSecMatchLimit as String:   kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let s = String(data: data, encoding: .utf8) else { return nil }
        return s
    }

    private func deleteKC(_ key: KCKey) {
        let q: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  kcService,
            kSecAttrAccount as String:  key.rawValue,
        ]
        SecItemDelete(q as CFDictionary)
    }
}

// MARK: - Sign in with Apple coordinator
//
// Pulled out so AuthManager doesn't need to inherit from NSObject for
// the delegate protocol everywhere; this little helper does it once.

private final class SiwaCoordinator: NSObject, ASAuthorizationControllerDelegate,
                                      ASAuthorizationControllerPresentationContextProviding {
    private var continuation: CheckedContinuation<String, Error>?

    static func run() async throws -> String {
        let c = SiwaCoordinator()
        return try await withCheckedThrowingContinuation { cont in
            c.continuation = cont
            let req = ASAuthorizationAppleIDProvider().createRequest()
            req.requestedScopes = [.fullName, .email]
            let ctrl = ASAuthorizationController(authorizationRequests: [req])
            ctrl.delegate = c
            ctrl.presentationContextProvider = c
            ctrl.performRequests()
            // Retain c until completion via continuation closure.
            _ = c
        }
    }

    func authorizationController(controller: ASAuthorizationController,
                                  didCompleteWithAuthorization authorization: ASAuthorization) {
        guard let cred = authorization.credential as? ASAuthorizationAppleIDCredential,
              let tokenData = cred.identityToken,
              let token = String(data: tokenData, encoding: .utf8) else {
            continuation?.resume(throwing: NSError(
                domain: "SIWA", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Missing identity token"]))
            continuation = nil
            return
        }
        continuation?.resume(returning: token)
        continuation = nil
    }

    func authorizationController(controller: ASAuthorizationController,
                                  didCompleteWithError error: Error) {
        continuation?.resume(throwing: error)
        continuation = nil
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        // Find the active key window. Falls back to a fresh one if none.
        if let w = UIApplication.shared.connectedScenes
            .compactMap({ ($0 as? UIWindowScene)?.windows.first(where: { $0.isKeyWindow }) })
            .first {
            return w
        }
        return ASPresentationAnchor()
    }
}
