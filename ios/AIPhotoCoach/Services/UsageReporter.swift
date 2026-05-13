// UsageReporter.swift
//
// v18 — fire-and-forget client for the two iOS-driven feedback signals
// on a usage_record:
//
//   PATCH /me/usage/{id}/captured    (user actually shot)
//   PATCH /me/usage/{id}/satisfied   (user thumbs up/down on result)
//
// Failures are logged and dropped — these are observability signals,
// never block the photo flow. Zero photo bytes leave the device; the
// only payload we send for /satisfied is a single bool plus an
// optional 200-char text note.
//
// Backend already validates owner-scoping and rate limits, so we don't
// retry aggressively from the client.

import Foundation
import UIKit

@MainActor
final class UsageReporter {
    static let shared = UsageReporter()

    private init() {}

    func markCaptured(usageRecordId: String) {
        guard !usageRecordId.isEmpty else { return }
        Task.detached(priority: .background) {
            await Self.send(
                method: "PATCH",
                path: "me/usage/\(usageRecordId)/captured",
                body: nil,
            )
        }
    }

    /// note is truncated to 200 chars server-side; we keep this client
    /// soft-cap to surface the limit early in the UI without a round
    /// trip.
    func markSatisfied(usageRecordId: String,
                         satisfied: Bool,
                         grade: String? = nil,
                         note: String? = nil) {
        guard !usageRecordId.isEmpty else { return }
        var payload: [String: Any] = ["satisfied": satisfied]
        if let g = grade, !g.isEmpty {
            payload["grade"] = g
        }
        if let n = note, !n.isEmpty {
            payload["note"] = String(n.prefix(200))
        }
        Task.detached(priority: .background) {
            await Self.send(
                method: "PATCH",
                path: "me/usage/\(usageRecordId)/satisfied",
                body: payload,
            )
        }
    }

    // MARK: - Internal

    private static func send(method: String,
                              path: String,
                              body: [String: Any]?) async {
        // v18 m3 — request a background-task assertion so iOS doesn't
        // suspend us mid-flight when the user backgrounds the app
        // (very common right after capture: tap shutter → swipe up to
        // share to socials). We get ~30s grace; well over the 1-2s
        // PATCH actually needs.
        let bgIdHolder = await MainActor.run { () -> UIBackgroundTaskIdentifier in
            UIApplication.shared.beginBackgroundTask(
                withName: "UsageReporter.\(path)",
                expirationHandler: nil)
        }
        defer {
            if bgIdHolder != .invalid {
                Task { @MainActor in
                    UIApplication.shared.endBackgroundTask(bgIdHolder)
                }
            }
        }
        do {
            let url = APIConfig.baseURL.appendingPathComponent(path)
            var req = URLRequest(url: url)
            req.httpMethod = method
            req.timeoutInterval = APIConfig.connectTimeout
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            if let body = body {
                req.httpBody = try JSONSerialization.data(withJSONObject: body)
            }
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode >= 400 {
                #if DEBUG
                print("UsageReporter: \(method) \(path) -> \(http.statusCode)")
                #endif
            }
        } catch {
            #if DEBUG
            print("UsageReporter failed \(method) \(path): \(error)")
            #endif
        }
    }
}
