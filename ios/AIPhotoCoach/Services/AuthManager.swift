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

@MainActor
final class AuthManager: NSObject, ObservableObject {
    static let shared = AuthManager()

    // MARK: - Published state (drives UI)
    @Published private(set) var userId: String?
    @Published private(set) var isAnonymous: Bool = true
    @Published private(set) var tier: String = "free"
    @Published private(set) var lastError: String?

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
    func accessToken() async throws -> String {
        if accessTokenValue == nil || refreshTokenValue == nil {
            try await ensureSession()
        }
        if Date().addingTimeInterval(60) >= accessExpiry, refreshTokenValue != nil {
            try await refreshIfPossible()
        }
        guard let t = accessTokenValue else {
            throw NSError(domain: "AuthManager", code: 401,
                          userInfo: [NSLocalizedDescriptionKey: "No session"])
        }
        return t
    }

    /// Boot an anonymous session if we don't have one.
    func ensureSession() async throws {
        if accessTokenValue != nil, refreshTokenValue != nil { return }
        let pair = try await postJSON(
            path: "/auth/anonymous",
            body: ["device_id": deviceId],
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
            // Refresh failed → fall back to a fresh anonymous session.
            accessTokenValue = nil
            refreshTokenValue = nil
            try await ensureSession()
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

    /// Server-side revoke + local Keychain wipe.
    func signOut() async {
        if let r = refreshTokenValue {
            _ = try? await postJSON(path: "/auth/logout", body: ["refresh_token": r])
        }
        clearAll()
        try? await ensureSession()
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
        try? await ensureSession()
    }

    // MARK: - Internals

    private func applyPair(_ obj: [String: Any]) {
        accessTokenValue = obj["access_token"] as? String
        refreshTokenValue = obj["refresh_token"] as? String
        let uid = (obj["user_id"] as? String) ?? ""
        userId = uid.isEmpty ? nil : uid
        isAnonymous = (obj["is_anonymous"] as? Bool) ?? true
        tier = (obj["tier"] as? String) ?? "free"
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
    }

    private func clearAll() {
        accessTokenValue = nil
        refreshTokenValue = nil
        accessExpiry = .distantPast
        userId = nil
        isAnonymous = true
        tier = "free"
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

    private func postJSON(path: String, body: [String: Any]) async throws -> [String: Any] {
        var req = makeRequest(path: path)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw NSError(domain: "AuthManager", code: 0)
        }
        guard (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? ""
            self.lastError = msg
            throw NSError(domain: "AuthManager", code: http.statusCode,
                          userInfo: [NSLocalizedDescriptionKey: msg])
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
