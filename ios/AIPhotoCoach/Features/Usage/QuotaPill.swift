// QuotaPill.swift  (PR6 of subscription/auth rework)
//
// Compact, always-visible reminder of "剩余次数 / 重置时间".
// Designed for the top of the home wizard so the user never wonders
// where their quota stands. Pulls /me/quota every time it appears.
//
// Color levels:
//   - admin    → 紫色 "管理员"
//   - 充足     → 白色
//   - <30%     → 橙色
//   - 0        → 红色 + 「升级套餐」按钮

import SwiftUI

@MainActor
final class QuotaModel: ObservableObject {
    @Published var loading: Bool = false
    @Published var lastError: String?
    @Published var snapshot: Snapshot?

    struct Snapshot: Equatable {
        var plan: String?
        var total: Int?
        var used: Int?
        var remaining: Int?
        var periodEnd: Date?
        var isUnlimited: Bool

        var isAdmin: Bool { plan == "admin" || isUnlimited }
        var isFree: Bool { plan == nil && !isUnlimited }
        var ratioRemaining: Double {
            guard let t = total, t > 0, let r = remaining else { return 1.0 }
            return Double(r) / Double(t)
        }
    }

    func refresh() async {
        loading = true
        defer { loading = false }
        do {
            var req = URLRequest(url: APIConfig.baseURL
                .appendingPathComponent("me/quota"))
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let obj = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            let iso = ISO8601DateFormatter()
            snapshot = Snapshot(
                plan: obj["plan"] as? String,
                total: obj["total"] as? Int,
                used: obj["used"] as? Int,
                remaining: obj["remaining"] as? Int,
                periodEnd: (obj["period_end"] as? String).flatMap { iso.date(from: $0) },
                isUnlimited: (obj["is_unlimited"] as? Bool) ?? false,
            )
        } catch {
            lastError = (error as NSError).localizedDescription
        }
    }
}

struct QuotaPill: View {
    @StateObject private var model = QuotaModel()
    @State private var showPaywall: Bool = false

    var body: some View {
        Group {
            if let snap = model.snapshot {
                pill(for: snap)
            } else if model.loading {
                ProgressView().tint(.white)
                    .padding(.vertical, 6)
            } else {
                EmptyView()
            }
        }
        .task { await model.refresh() }
        .sheet(isPresented: $showPaywall) {
            PaywallView()
        }
    }

    @ViewBuilder
    private func pill(for snap: QuotaModel.Snapshot) -> some View {
        if snap.isAdmin {
            label("管理员 · 无限制", color: .purple, icon: "shield.lefthalf.filled")
        } else if snap.isFree {
            Button { showPaywall = true } label: {
                label("免费体验 · 升级解锁更多次数",
                       color: .accentColor, icon: "bolt.fill")
            }
        } else if let remaining = snap.remaining, let total = snap.total {
            let color: Color = {
                if remaining == 0 { return .red }
                if snap.ratioRemaining < 0.3 { return .orange }
                return .white.opacity(0.95)
            }()
            HStack(spacing: 8) {
                Image(systemName: "gauge.with.dots.needle.bottom.50percent")
                    .foregroundStyle(color)
                Text("剩余 \(remaining)/\(total) 次")
                    .foregroundStyle(color)
                if let end = snap.periodEnd {
                    Text("· " + Self.dateFormatter.string(from: end) + " 重置")
                        .foregroundStyle(.white.opacity(0.6))
                }
                if remaining == 0 {
                    Button("升级") { showPaywall = true }
                        .font(.caption.bold())
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(Color.white)
                        .foregroundStyle(.black)
                        .clipShape(Capsule())
                }
            }
            .font(.footnote)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(Color.white.opacity(0.08))
            .clipShape(Capsule())
        }
    }

    private func label(_ text: String, color: Color, icon: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
            Text(text)
        }
        .font(.footnote)
        .foregroundStyle(color)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.white.opacity(0.08))
        .clipShape(Capsule())
    }

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "M月d日"
        return f
    }()
}

#Preview {
    ZStack {
        Color.black.ignoresSafeArea()
        QuotaPill()
    }
}
