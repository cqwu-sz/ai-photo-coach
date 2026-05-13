// AppAttestManager.swift  (v17c)
//
// Thin wrapper around DeviceCheck's App Attest API. Lifecycle:
//   1. On first authenticated launch, call `bootstrap()` — this
//      generates a key in the Secure Enclave and POSTs the
//      attestation blob to /devices/attest.
//   2. Before each high-sensitivity request (OTP request, /analyze),
//      call `assertion(challenge:)` to get headers to attach.
//
// Failure handling: every method swallows errors and returns nil
// rather than throwing. App Attest can fail for legitimate reasons
// (jailbroken device, simulator, network blip during attest); we
// don't want to brick the app over it. The backend decides whether
// to enforce — if it does and we have nothing, the user sees the
// `attest_required` error and we re-prompt to update the app.

import Foundation
import DeviceCheck
import CryptoKit

@MainActor
final class AppAttestManager {
    static let shared = AppAttestManager()

    private let service = DCAppAttestService.shared
    private let kKeyId = "v17c.appattest.keyId"

    private init() {}

    var keyId: String? {
        UserDefaults.standard.string(forKey: kKeyId)
    }

    var isSupported: Bool { service.isSupported }

    /// Forget the cached key id and run a fresh attest. Used when
    /// the backend rejects our assertion (`attest_invalid`) — most
    /// commonly because we registered against a different env or
    /// the keychain item drifted.
    func forceReBootstrap() async -> String? {
        UserDefaults.standard.removeObject(forKey: kKeyId)
        return await bootstrap()
    }

    /// Generate + attest a Secure Enclave key and POST the attestation
    /// blob to the backend. Idempotent: if we already have a stored
    /// key id, this just returns it.
    @discardableResult
    func bootstrap() async -> String? {
        if let existing = keyId { return existing }
        guard isSupported else { return nil }
        do {
            let kid = try await service.generateKey()
            let challenge = UUID().uuidString
            let challengeData = Data(SHA256.hash(data: Data(challenge.utf8)))
            let attestation = try await service.attestKey(kid, clientDataHash: challengeData)
            // POST to backend.
            let url = APIConfig.baseURL.appendingPathComponent("devices/attest")
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let body: [String: Any] = [
                "key_id": kid,
                "attestation_b64": attestation.base64EncodedString(),
                "challenge": challenge,
            ]
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                UserDefaults.standard.set(kid, forKey: kKeyId)
                return kid
            } else {
                print("[AppAttest] register failed: \(String(data: data, encoding: .utf8) ?? "")")
                return nil
            }
        } catch {
            print("[AppAttest] bootstrap failed: \(error.localizedDescription)")
            return nil
        }
    }

    /// Build an assertion for the given per-request challenge string.
    /// Returns the headers to attach, or empty dict on failure.
    func assertionHeaders(for challenge: String) async -> [String: String] {
        guard let kid = keyId else { return [:] }
        guard isSupported else { return [:] }
        do {
            let cdh = Data(SHA256.hash(data: Data(challenge.utf8)))
            let assertion = try await service.generateAssertion(kid, clientDataHash: cdh)
            return [
                "X-Attest-KeyId": kid,
                "X-Attest-Assertion": assertion.base64EncodedString(),
                "X-Attest-Challenge": challenge,
            ]
        } catch {
            print("[AppAttest] assertion failed: \(error.localizedDescription)")
            return [:]
        }
    }
}
