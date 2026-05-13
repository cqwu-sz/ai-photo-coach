// IAPManager.swift (P1-9.2)
//
// StoreKit 2 wrapper for the "AI Photo Coach Pro" monthly subscription.
// One-line API:
//
//     if await IAPManager.shared.isProActive { ... }
//     try await IAPManager.shared.purchasePro()
//
// Configuration (TODO before shipping):
//   - In App Store Connect, create an auto-renewable subscription with
//     product id "ai_photo_coach.pro.monthly" priced at ¥18 / month.
//   - Add the StoreKit configuration file with the same id and price
//     to the Xcode project's scheme so debug runs work without TestFlight.
//
// Until the real product id is wired up, all calls succeed in
// "shadow-pro" mode (returns true) so the rest of the app can integrate
// the Pro checks without blocking on Apple's side. Switch
// `IAPManager.useShadowPro` to false once the product is live.
import Foundation
import StoreKit

@MainActor
final class IAPManager: ObservableObject {
    static let shared = IAPManager()

    /// Switch to true ONLY in dev when the App Store Connect product
    /// isn't live yet. In shadow mode `isProActive` always returns
    /// false (so paywalls still render) but `purchasePro` succeeds
    /// without contacting Apple. Production builds MUST keep this
    /// `false` — a Pro check that returns true without a verified
    /// receipt is a 3.1.1 failure.
    /// v17 / opt-iap-shadow-pro-prod-guard — `useShadowPro` is a dev
    /// shortcut that bypasses StoreKit so we can demo paid features
    /// without a sandbox account. It MUST never ship to TestFlight /
    /// App Store, hence the `#if DEBUG` guard. Setting it in a Release
    /// build is silently ignored.
    #if DEBUG
    var useShadowPro: Bool = false
    #else
    let useShadowPro: Bool = false
    #endif

    /// v17 — three subscription tiers. Order matters for default UI
    /// rendering (yearly first, marked as "best value").
    enum Plan: String, CaseIterable, Identifiable {
        case yearly    = "ai_photo_coach.pro.yearly"
        case quarterly = "ai_photo_coach.pro.quarterly"
        case monthly   = "ai_photo_coach.pro.monthly"

        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .yearly:    return "年度会员"
            case .quarterly: return "季度会员"
            case .monthly:   return "月度会员"
            }
        }
        var quotaCount: Int {
            switch self {
            case .yearly: return 2000
            case .quarterly: return 500
            case .monthly: return 100
            }
        }
        var quotaLabel: String {
            switch self {
            case .yearly:    return "含 2000 次智能分析"
            case .quarterly: return "含 500 次智能分析"
            case .monthly:   return "含 100 次智能分析"
            }
        }
        var periodLabel: String {
            switch self {
            case .yearly: return "/年"
            case .quarterly: return "/季"
            case .monthly: return "/月"
            }
        }
        var displayedFallbackPriceCNY: String {
            // Backup for offline / Apple metadata not yet propagated.
            // Real price always preferred from `Product.displayPrice`.
            switch self {
            case .yearly:    return "¥412"
            case .quarterly: return "¥108"
            case .monthly:   return "¥39"
            }
        }
    }

    static let allProductIds: [String] = Plan.allCases.map { $0.rawValue }
    static let productId = Plan.monthly.rawValue   // legacy alias

    /// Cached server entitlement. Refreshed on init, on app foreground,
    /// before paywalled flows, and after every successful purchase.
    @Published private(set) var entitlement: Entitlement = .free
    private var entitlementFetchedAt: Date = .distantPast
    private let entitlementTTL: TimeInterval = 10 * 60

    struct Entitlement: Equatable {
        var tier: String          // 'free' | 'pro'
        var plan: String?         // 'monthly' | 'quarterly' | 'yearly' | 'admin'
        var productId: String?
        var expiresAt: Date?
        var inGracePeriod: Bool
        var environment: String?
        var quotaTotal: Int?      // nil = unlimited (admin)
        var quotaUsed: Int?
        var quotaRemaining: Int?
        var periodEnd: Date?

        static let free = Entitlement(
            tier: "free", plan: nil, productId: nil, expiresAt: nil,
            inGracePeriod: false, environment: nil,
            quotaTotal: nil, quotaUsed: nil, quotaRemaining: nil,
            periodEnd: nil,
        )

        var isPro: Bool { tier == "pro" }
        var isAdmin: Bool { plan == "admin" }
    }

    private init() {
        Task.detached { [weak self] in
            await self?.observeTransactions()
        }
        Task { await refreshEntitlement(force: true) }
    }

    /// Truth-source for "should this paywall be unlocked?"
    /// Always trusts the server entitlement; falls back to free when
    /// offline + no cache, which is the safe default.
    var isProActive: Bool {
        get async {
            if useShadowPro { return false }
            await refreshEntitlement(force: false)
            return entitlement.isPro
        }
    }

    /// Call right before opening any paywalled feature. Forces a server
    /// round-trip so a refunded user loses access within seconds, not
    /// 10 min (the cached TTL). Returns the same value as `isProActive`.
    func paywallGate() async -> Bool {
        if useShadowPro { return false }
        await refreshEntitlement(force: true)
        return entitlement.isPro
    }

    /// Fetch all three subscription products (yearly / quarterly /
    /// monthly) so the Paywall can render them with Apple-localised
    /// prices.
    func loadProducts() async throws -> [Product] {
        try await Product.products(for: Self.allProductIds)
    }

    /// Purchase the given subscription product id.
    func purchase(productId: String) async throws {
        if useShadowPro { return }
        guard let product = try await Product.products(for: [productId]).first else {
            throw NSError(domain: "iap", code: 404,
                           userInfo: [NSLocalizedDescriptionKey: "Pro 商品未上架"])
        }
        let result = try await product.purchase()
        switch result {
        case .success(let verification):
            if case .verified(let txn) = verification {
                // Hand the JWS to our server so the entitlement is
                // anchored in our DB — Apple's local Transaction is
                // only a cache, not the source of truth.
                await uploadJWS(txn.jsonRepresentation)
                await txn.finish()
                await refreshEntitlement(force: true)
            }
        case .userCancelled, .pending:
            return
        @unknown default:
            return
        }
    }

    /// Legacy single-product helper kept so older callsites (e.g.
    /// PostProcessView Paywall) keep compiling. Defaults to monthly.
    func purchasePro() async throws {
        try await purchase(productId: Plan.monthly.rawValue)
    }

    func restore() async {
        try? await AppStore.sync()
        // After AppStore.sync the local Transaction.currentEntitlements
        // may have new rows; ship them all up so the server sees them.
        for await result in Transaction.currentEntitlements {
            if case .verified(let t) = result {
                await uploadJWS(t.jsonRepresentation)
            }
        }
        await refreshEntitlement(force: true)
    }

    // MARK: - Server bridge

    func refreshEntitlement(force: Bool) async {
        if !force,
           Date().timeIntervalSince(entitlementFetchedAt) < entitlementTTL {
            return
        }
        do {
            var req = URLRequest(url: APIConfig.baseURL
                .appendingPathComponent("me/entitlements"))
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.setValue(AuthManager.shared.deviceId, forHTTPHeaderField: "X-Device-Id")
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode) else { return }
            let obj = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            let iso = ISO8601DateFormatter()
            entitlement = Entitlement(
                tier: (obj["tier"] as? String) ?? "free",
                plan: obj["plan"] as? String,
                productId: obj["product_id"] as? String,
                expiresAt: (obj["expires_at"] as? String).flatMap { iso.date(from: $0) },
                inGracePeriod: (obj["in_grace_period"] as? Bool) ?? false,
                environment: obj["environment"] as? String,
                quotaTotal: obj["quota_total"] as? Int,
                quotaUsed: obj["quota_used"] as? Int,
                quotaRemaining: obj["quota_remaining"] as? Int,
                periodEnd: (obj["period_end"] as? String).flatMap { iso.date(from: $0) },
            )
            entitlementFetchedAt = Date()
        } catch {
            // Stay on cached value; never crash a paywall over a
            // network blip.
        }
    }

    private func uploadJWS(_ jws: Data) async {
        guard let str = String(data: jws, encoding: .utf8) else { return }
        do {
            var req = URLRequest(url: APIConfig.baseURL
                .appendingPathComponent("iap/verify"))
            req.httpMethod = "POST"
            req.timeoutInterval = APIConfig.connectTimeout
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.setValue(AuthManager.shared.deviceId, forHTTPHeaderField: "X-Device-Id")
            req.httpBody = try JSONSerialization.data(
                withJSONObject: ["jws_representation": str])
            _ = try? await URLSession.shared.data(for: req)
        } catch {
            // Server can still pick up the entitlement via the ASN V2
            // webhook — we just lose the immediate UX update.
        }
    }

    private func observeTransactions() async {
        for await result in Transaction.updates {
            if case .verified(let t) = result {
                await uploadJWS(t.jsonRepresentation)
                await t.finish()
                await refreshEntitlement(force: true)
            }
        }
    }
}
