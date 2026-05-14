// PaywallView.swift  (PR4 of subscription/auth rework)
//
// Three-tier subscription paywall — yearly / quarterly / monthly.
//
// Compliance hard requirements (Apple 3.1.2 + 国内合规):
//   - Each plan card shows full price + period + 次数说明.
//   - 自动续订 + 取消方式说明常驻底部.
//   - 「恢复购买」「隐私政策」「用户协议」按钮齐全.
//   - 价格优先用 `Product.displayPrice`, 离线时回落到本地 ¥ 字符串.
//   - 「次数过期不结转」一行小字明示, 防止客诉.

import SwiftUI
import StoreKit

struct PaywallView: View {
    @ObservedObject private var iap = IAPManager.shared
    @Environment(\.dismiss) private var dismiss

    @State private var products: [Product] = []
    @State private var loading: Bool = true
    @State private var selected: IAPManager.Plan = .yearly
    @State private var purchasing: Bool = false
    @State private var restoring: Bool = false
    @State private var errorMessage: String?
    @State private var showError: Bool = false

    private let privacyURL = BrandConstants.privacyURL
    private let eulaURL = BrandConstants.appleEulaURL

    var body: some View {
        ZStack {
            CinemaBackdrop()

            ScrollView {
                VStack(spacing: 22) {
                    header
                    if loading {
                        ProgressView().tint(CinemaTheme.accentWarm).padding(.top, 40)
                    } else {
                        VStack(spacing: 12) {
                            ForEach(IAPManager.Plan.allCases) { plan in
                                planCard(plan)
                            }
                        }
                        .padding(.horizontal, 20)

                        purchaseButton
                        restoreButton
                        autoRenewDisclaimer
                        legalLinks
                    }
                }
                .padding(.top, 28)
                .padding(.bottom, 32)
            }
            closeButton
        }
        .preferredColorScheme(.dark)
        .task { await load() }
        .alert("订阅失败", isPresented: $showError) {
            Button("好") { }
        } message: {
            Text(errorMessage ?? "请稍后重试")
        }
    }

    // MARK: - Layout

    private var header: some View {
        VStack(spacing: 12) {
            HStack(spacing: 6) {
                Circle()
                    .fill(CinemaTheme.accentWarm)
                    .frame(width: 5, height: 5)
                    .shadow(color: CinemaTheme.accentWarm.opacity(0.7), radius: 5)
                Text("CINEMA HOUSE · PRO · \(BrandConstants.brandYearTag)")
                    .font(.system(size: 10, weight: .heavy))
                    .tracking(2.4)
                    .foregroundStyle(CinemaTheme.accentWarm)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 5)
            .background(Capsule().fill(CinemaTheme.accentWarm.opacity(0.10)))
            .overlay(Capsule().stroke(CinemaTheme.accentWarm.opacity(0.36), lineWidth: 1))

            Image(systemName: "sparkles")
                .font(.system(size: 38, weight: .light))
                .foregroundStyle(CinemaTheme.accentGradient)
                .shadow(color: CinemaTheme.accentWarm.opacity(0.5), radius: 14, y: 6)

            (Text("解锁 ").foregroundStyle(CinemaTheme.ink)
             + Text("拾光 Pro").foregroundStyle(CinemaTheme.heroGradient))
                .font(.system(size: 26, weight: .heavy))
                .kerning(0.3)

            Text("更高的智能分析额度 · 更多专业能力 · 更稳定的体验")
                .font(.system(size: 12.5))
                .multilineTextAlignment(.center)
                .foregroundStyle(CinemaTheme.inkSoft)
                .padding(.horizontal, 32)
                .lineSpacing(2)
        }
    }

    private func planCard(_ plan: IAPManager.Plan) -> some View {
        let product = products.first { $0.id == plan.rawValue }
        let isSelected = selected == plan
        return Button {
            withAnimation(.spring(response: 0.3, dampingFraction: 0.78)) {
                selected = plan
            }
        } label: {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: isSelected
                      ? "largecircle.fill.circle"
                      : "circle")
                    .font(.title3)
                    .foregroundStyle(isSelected ? CinemaTheme.accentWarm : CinemaTheme.inkMuted)
                    .padding(.top, 4)

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(plan.displayName)
                            .font(.system(size: 15.5, weight: .heavy))
                            .foregroundStyle(CinemaTheme.ink)
                        if plan == .yearly {
                            Text("最划算")
                                .font(.system(size: 9.5, weight: .heavy))
                                .tracking(1.2)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(CinemaTheme.accentGradient)
                                .foregroundStyle(.black.opacity(0.88))
                                .clipShape(Capsule())
                        }
                        Spacer()
                        priceText(plan: plan, product: product)
                    }
                    Text(plan.quotaLabel)
                        .font(.system(size: 13))
                        .foregroundStyle(CinemaTheme.inkSoft)
                    Text("次数在订阅周期内有效，过期或续订后将重置，不结转。")
                        .font(.system(size: 10.5))
                        .foregroundStyle(CinemaTheme.inkMuted)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 16)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(isSelected
                          ? AnyShapeStyle(CinemaTheme.activeChipFill)
                          : AnyShapeStyle(Color.white.opacity(0.04)))
            )
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(.ultraThinMaterial)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(isSelected
                            ? CinemaTheme.accentWarm.opacity(0.55)
                            : CinemaTheme.borderSoft,
                            lineWidth: 1)
            )
            .shadow(color: isSelected ? CinemaTheme.accentWarm.opacity(0.28) : .clear,
                    radius: 14, y: 6)
        }
        .buttonStyle(.plain)
    }

    private func priceText(plan: IAPManager.Plan, product: Product?) -> some View {
        let price = product?.displayPrice ?? plan.displayedFallbackPriceCNY
        return (Text(price)
                    .font(.system(size: 16, weight: .heavy))
                    .foregroundStyle(CinemaTheme.ink)
                + Text(plan.periodLabel)
                    .font(.system(size: 11))
                    .foregroundStyle(CinemaTheme.inkMuted))
    }

    private var purchaseButton: some View {
        Button {
            Task { await purchase() }
        } label: {
            HStack(spacing: 10) {
                if purchasing { ProgressView().tint(.black) }
                Text("订阅 \(selected.displayName)")
                    .font(.system(size: 16.5, weight: .heavy))
                Image(systemName: "arrow.right")
                    .font(.system(size: 14, weight: .heavy))
            }
            .foregroundStyle(.black.opacity(0.9))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(CinemaTheme.ctaGradient)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.white.opacity(0.16), lineWidth: 1)
            )
            .shadow(color: CinemaTheme.accentWarm.opacity(0.45), radius: 16, y: 6)
        }
        .padding(.horizontal, 20)
        .disabled(purchasing)
    }

    private var restoreButton: some View {
        Button("恢复购买") {
            Task { await restore() }
        }
        .font(.system(size: 12.5, weight: .semibold))
        .foregroundStyle(CinemaTheme.inkSoft)
        .disabled(restoring)
    }

    private var autoRenewDisclaimer: some View {
        Text("订阅将自动续期，将在到期前 24 小时通过你的 Apple ID 自动扣费。\n如需取消，请前往 设置 → Apple ID → 订阅 关闭自动续订。\n次数用尽后将无法继续使用付费功能，已扣款不予退款。")
            .font(.system(size: 10.5))
            .multilineTextAlignment(.center)
            .foregroundStyle(CinemaTheme.inkMuted)
            .padding(.horizontal, 28)
            .padding(.top, 4)
            .lineSpacing(2)
    }

    private var legalLinks: some View {
        HStack(spacing: 12) {
            Link("用户协议", destination: eulaURL)
            Text("·").foregroundStyle(CinemaTheme.inkMuted)
            Link("隐私政策", destination: privacyURL)
        }
        .font(.system(size: 11.5, weight: .semibold))
        .tint(CinemaTheme.inkSoft)
        .foregroundStyle(CinemaTheme.inkSoft)
    }

    private var closeButton: some View {
        VStack {
            HStack {
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(CinemaTheme.inkSoft)
                        .padding(10)
                        .background(.ultraThinMaterial, in: Circle())
                        .overlay(Circle().stroke(CinemaTheme.borderSoft, lineWidth: 1))
                }
                .padding(.trailing, 18)
                .padding(.top, 12)
            }
            Spacer()
        }
    }

    // MARK: - Actions

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            products = try await iap.loadProducts()
        } catch {
            // Silent — fallback prices still render.
        }
    }

    private func purchase() async {
        purchasing = true
        defer { purchasing = false }
        do {
            try await iap.purchase(productId: selected.rawValue)
            if iap.entitlement.isPro { dismiss() }
        } catch {
            errorMessage = (error as NSError).localizedDescription
            showError = true
        }
    }

    private func restore() async {
        restoring = true
        defer { restoring = false }
        await iap.restore()
        if iap.entitlement.isPro { dismiss() }
    }
}

#Preview {
    PaywallView()
}
