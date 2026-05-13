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

    private let privacyURL = URL(string: "https://aiphotocoach.app/privacy")!
    private let eulaURL = URL(string: "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")!

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.05, green: 0.06, blue: 0.13),
                    Color(red: 0.13, green: 0.10, blue: 0.22),
                ],
                startPoint: .top, endPoint: .bottom,
            ).ignoresSafeArea()

            ScrollView {
                VStack(spacing: 24) {
                    header
                    if loading {
                        ProgressView().tint(.white).padding(.top, 40)
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
                .padding(.top, 24)
                .padding(.bottom, 32)
            }
            closeButton
        }
        .task { await load() }
        .alert("订阅失败", isPresented: $showError) {
            Button("好") { }
        } message: {
            Text(errorMessage ?? "请稍后重试")
        }
    }

    // MARK: - Layout

    private var header: some View {
        VStack(spacing: 10) {
            Image(systemName: "sparkles")
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(LinearGradient(
                    colors: [.yellow, .pink],
                    startPoint: .topLeading, endPoint: .bottomTrailing,
                ))
            Text("解锁 AI Photo Coach Pro")
                .font(.title2).bold()
                .foregroundStyle(.white)
            Text("更高的智能分析额度 · 更多专业能力 · 更稳定的体验")
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(.white.opacity(0.75))
                .padding(.horizontal, 32)
        }
    }

    private func planCard(_ plan: IAPManager.Plan) -> some View {
        let product = products.first { $0.id == plan.rawValue }
        let isSelected = selected == plan
        return Button {
            selected = plan
        } label: {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: isSelected
                      ? "largecircle.fill.circle"
                      : "circle")
                    .font(.title3)
                    .foregroundStyle(isSelected ? Color.accentColor : .white.opacity(0.5))
                    .padding(.top, 4)

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(plan.displayName)
                            .font(.headline)
                            .foregroundStyle(.white)
                        if plan == .yearly {
                            Text("最划算")
                                .font(.caption2.bold())
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(LinearGradient(
                                    colors: [.orange, .pink],
                                    startPoint: .leading, endPoint: .trailing,
                                ))
                                .foregroundStyle(.white)
                                .clipShape(Capsule())
                        }
                        Spacer()
                        priceText(plan: plan, product: product)
                    }
                    Text(plan.quotaLabel)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.85))
                    Text("次数在订阅周期内有效，过期或续订后将重置，不结转。")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.55))
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 16)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(isSelected
                          ? Color.white.opacity(0.14)
                          : Color.white.opacity(0.06))
                    .overlay(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .stroke(isSelected
                                    ? Color.accentColor.opacity(0.7)
                                    : Color.clear,
                                    lineWidth: 1.5),
                    ),
            )
        }
        .buttonStyle(.plain)
    }

    private func priceText(plan: IAPManager.Plan, product: Product?) -> some View {
        let price = product?.displayPrice ?? plan.displayedFallbackPriceCNY
        return (Text(price).font(.headline).foregroundColor(.white)
                + Text(plan.periodLabel).font(.caption).foregroundColor(.white.opacity(0.7)))
    }

    private var purchaseButton: some View {
        Button {
            Task { await purchase() }
        } label: {
            HStack {
                if purchasing { ProgressView().tint(.black) }
                Text("订阅 \(selected.displayName)")
                    .fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Color.white)
            .foregroundStyle(.black)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .padding(.horizontal, 20)
        .disabled(purchasing)
    }

    private var restoreButton: some View {
        Button("恢复购买") {
            Task { await restore() }
        }
        .font(.footnote)
        .foregroundStyle(.white.opacity(0.85))
        .disabled(restoring)
    }

    private var autoRenewDisclaimer: some View {
        Text("订阅将自动续期，将在到期前 24 小时通过你的 Apple ID 自动扣费。\n如需取消，请前往 设置 → Apple ID → 订阅 关闭自动续订。\n次数用尽后将无法继续使用付费功能，已扣款不予退款。")
            .font(.caption2)
            .multilineTextAlignment(.center)
            .foregroundStyle(.white.opacity(0.6))
            .padding(.horizontal, 28)
            .padding(.top, 4)
    }

    private var legalLinks: some View {
        HStack(spacing: 12) {
            Link("用户协议", destination: eulaURL)
            Text("·").foregroundStyle(.white.opacity(0.4))
            Link("隐私政策", destination: privacyURL)
        }
        .font(.caption)
        .foregroundStyle(.white.opacity(0.85))
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
                        .foregroundStyle(.white)
                        .padding(10)
                        .background(Color.white.opacity(0.12))
                        .clipShape(Circle())
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
