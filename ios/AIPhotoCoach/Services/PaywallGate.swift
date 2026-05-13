// PaywallGate.swift  (PR7 of subscription/auth rework)
//
// Single chokepoint between any "premium" feature in the app and the
// PaywallView. Responsibilities:
//   1. Check Pro entitlement (force-refresh from server first so a
//      refunded user can't cheat the cache)
//   2. If not Pro: hand the caller a binding so it can present
//      PaywallView in a sheet
//   3. Per-feature cost map (analyze=1, beauty=0, advanced_filter=0)
//      so a single capture session never burns multiple quota slots
//
// Usage (SwiftUI):
//
//      @StateObject private var gate = PaywallGate()
//      ...
//      Button("应用胶片暖") {
//          Task {
//              if await gate.allow(feature: .advancedFilter) {
//                  filterEngine.apply(.filmWarm, ...)
//              }
//          }
//      }
//      .sheet(isPresented: $gate.showPaywall) { PaywallView() }

import Foundation
import SwiftUI

@MainActor
final class PaywallGate: ObservableObject {
    enum Feature: String {
        case analyze
        case advancedFilter = "advanced_filter"
        case beauty
        case recon3d

        /// Quota cost when the gate succeeds. analyze is the only
        /// thing that should consume a slot today; everything else
        /// rides on the analyze that produced the photo, so cost = 0.
        var cost: Double {
            switch self {
            case .analyze: return 1.0
            case .advancedFilter, .beauty: return 0.0
            case .recon3d: return 1.0
            }
        }
    }

    @Published var showPaywall: Bool = false
    @Published var lastDenialReason: String?

    /// Returns true when the feature should run. False means the
    /// caller MUST NOT proceed — `showPaywall` flips to true so a
    /// `.sheet(isPresented:)` modifier can pop the PaywallView.
    func allow(feature: Feature) async -> Bool {
        let iap = IAPManager.shared
        let auth = AuthManager.shared

        // Admin bypasses everything.
        if auth.role == "admin" {
            return true
        }

        // Force a fresh server check so a refunded user is denied
        // within seconds, not the 10-minute entitlement TTL.
        await iap.refreshEntitlement(force: true)
        if !iap.entitlement.isPro {
            lastDenialReason = "需要 Pro 订阅"
            showPaywall = true
            return false
        }

        // Pro user — check quota only when cost > 0.
        if feature.cost > 0 {
            // Backend `/me/quota` is the truth source for remaining.
            // We've just refreshed entitlement so the cached snapshot
            // is recent enough; conservatively re-read here when used.
            if let remaining = iap.entitlement.quotaRemaining,
               feature.cost > Double(remaining) {
                lastDenialReason = "本周期次数已用尽"
                showPaywall = true
                return false
            }
        }
        return true
    }
}
