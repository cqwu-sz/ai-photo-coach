// AdminDashboardView.swift  (PR9 of subscription/auth rework)
//
// Admin-only audit dashboard with quick time-range filters
// (1h/3h/1d/7d/15d/1m/3m/6m/1y) and a Swift Charts line graph for
// the busiest metric (charged analyses per bucket). Below the chart
// we show the topline KPIs (订阅数 / 收入 / token / 支出) and the
// top spenders table.
//
// Visibility: surfaced only when `AuthManager.shared.role == "admin"`.
// Hook from MainTabs / AccountView when admin is detected.

import SwiftUI
import Charts

// MARK: - Range presets

enum AdminRange: String, CaseIterable, Identifiable {
    case h1 = "近1小时"
    case h3 = "近3小时"
    case d1 = "近1天"
    case d7 = "近7天"
    case d15 = "近15天"
    case m1 = "近1月"
    case m3 = "近3月"
    case m6 = "近半年"
    case y1 = "近1年"

    var id: String { rawValue }

    var hours: Int {
        switch self {
        case .h1: return 1
        case .h3: return 3
        case .d1: return 24
        case .d7: return 24 * 7
        case .d15: return 24 * 15
        case .m1: return 24 * 30
        case .m3: return 24 * 90
        case .m6: return 24 * 180
        case .y1: return 24 * 365
        }
    }

    var bucket: String {
        // Hour buckets when ≤ 7 days, otherwise day buckets to keep
        // the chart readable.
        return hours <= 24 * 7 ? "hour" : "day"
    }
}

// MARK: - View model

@MainActor
final class AdminDashboardModel: ObservableObject {
    struct Summary {
        var newSubs: Int = 0
        var newSubsByPlan: [String: Int] = [:]
        var revenueGross: Double = 0
        var revenueNet: Double = 0
        var analyzeTotal: Int = 0
        var analyzeFailed: Int = 0
        var analyzeCharged: Int = 0
        var promptTokens: Int = 0
        var completionTokens: Int = 0
        var costUsd: Double = 0
        var activeUsers: Int = 0
    }

    struct SeriesPoint: Identifiable {
        let id = UUID()
        let bucketStart: Date
        let analyzeCharged: Int
        let analyzeFailed: Int
        let revenueGross: Double
        let costUsd: Double
    }

    struct TopUser: Identifiable {
        let id: String
        let requests: Int
        let promptTokens: Int
        let completionTokens: Int
        let costUsd: Double
    }

    @Published var range: AdminRange = .d1
    @Published var summary = Summary()
    @Published var series: [SeriesPoint] = []
    @Published var topUsers: [TopUser] = []
    @Published var loading: Bool = false
    @Published var lastError: String?

    func reload() async {
        loading = true
        defer { loading = false }
        let now = Date()
        let since = Calendar.current.date(byAdding: .hour, value: -range.hours, to: now)
                    ?? now.addingTimeInterval(Double(-range.hours * 3600))
        let iso = ISO8601DateFormatter()
        let sinceStr = iso.string(from: since)
        let untilStr = iso.string(from: now)
        do {
            try await loadSummary(since: sinceStr, until: untilStr)
            try await loadSeries(since: sinceStr, until: untilStr)
            try await loadUsers(since: sinceStr, until: untilStr)
        } catch {
            lastError = (error as NSError).localizedDescription
        }
    }

    private func loadSummary(since: String, until: String) async throws {
        let body = try await fetch(path: "admin/audit/summary",
                                     query: [("since", since), ("until", until)])
        let s = body
        summary.newSubs = s["new_subscriptions"] as? Int ?? 0
        summary.newSubsByPlan = (s["new_subscriptions_by_plan"] as? [String: Int]) ?? [:]
        summary.revenueGross = s["revenue_cny_gross"] as? Double ?? 0
        summary.revenueNet = s["revenue_cny_net"] as? Double ?? 0
        summary.analyzeTotal = s["analyze_total"] as? Int ?? 0
        summary.analyzeFailed = s["analyze_failed"] as? Int ?? 0
        summary.analyzeCharged = s["analyze_charged"] as? Int ?? 0
        summary.promptTokens = s["prompt_tokens"] as? Int ?? 0
        summary.completionTokens = s["completion_tokens"] as? Int ?? 0
        summary.costUsd = s["cost_usd"] as? Double ?? 0
        summary.activeUsers = s["active_users"] as? Int ?? 0
    }

    private func loadSeries(since: String, until: String) async throws {
        let body = try await fetch(path: "admin/audit/series",
                                     query: [("since", since), ("until", until),
                                              ("bucket", range.bucket)])
        let raw = (body["points"] as? [[String: Any]]) ?? []
        let iso = ISO8601DateFormatter()
        series = raw.compactMap { d in
            guard let s = d["bucket_start"] as? String,
                  let date = iso.date(from: s) else { return nil }
            return SeriesPoint(
                bucketStart: date,
                analyzeCharged: d["analyze_charged"] as? Int ?? 0,
                analyzeFailed: d["analyze_failed"] as? Int ?? 0,
                revenueGross: d["revenue_cny_gross"] as? Double ?? 0,
                costUsd: d["cost_usd"] as? Double ?? 0,
            )
        }
    }

    private func loadUsers(since: String, until: String) async throws {
        let body = try await fetch(path: "admin/audit/users",
                                     query: [("since", since), ("until", until),
                                              ("limit", "20")])
        let raw = (body["items"] as? [[String: Any]]) ?? []
        topUsers = raw.compactMap { d in
            guard let uid = d["user_id"] as? String else { return nil }
            return TopUser(
                id: uid,
                requests: d["requests"] as? Int ?? 0,
                promptTokens: d["prompt_tokens"] as? Int ?? 0,
                completionTokens: d["completion_tokens"] as? Int ?? 0,
                costUsd: d["cost_usd"] as? Double ?? 0,
            )
        }
    }

    private func fetch(path: String,
                        query: [(String, String)]) async throws -> [String: Any] {
        var comps = URLComponents(url: APIConfig.baseURL.appendingPathComponent(path),
                                    resolvingAgainstBaseURL: false)!
        comps.queryItems = query.map { URLQueryItem(name: $0.0, value: $0.1) }
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = APIConfig.connectTimeout
        let token = try await AuthManager.shared.accessToken()
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, _) = try await URLSession.shared.data(for: req)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }
}

// MARK: - View

struct AdminDashboardView: View {
    @StateObject private var model = AdminDashboardModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    NavigationLink {
                        AdminAuditOverviewView()
                    } label: {
                        HStack {
                            Image(systemName: "exclamationmark.shield.fill")
                                .foregroundStyle(.orange)
                            Text("运营态势 (24h)").bold()
                            Spacer()
                            Image(systemName: "chevron.right")
                                .foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial,
                                      in: RoundedRectangle(cornerRadius: 12))
                    }
                    rangePicker
                    kpiGrid
                    chartCard
                    topUsersCard
                    NavigationLink {
                        AdminEndpointSettingsView()
                    } label: {
                        HStack {
                            Image(systemName: "antenna.radiowaves.left.and.right")
                            Text("服务器地址配置")
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                    NavigationLink {
                        AdminEndpointOverrideAuditView()
                    } label: {
                        HStack {
                            Image(systemName: "list.bullet.clipboard")
                            Text("本机覆盖审计（内部包用户）")
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                    NavigationLink {
                        AdminRuntimeSettingsView()
                    } label: {
                        HStack {
                            Image(systemName: "slider.horizontal.3")
                            Text("运行时阈值（限流/反爆破）")
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                    NavigationLink {
                        AdminInsightsView()
                    } label: {
                        HStack {
                            Image(systemName: "chart.bar.doc.horizontal")
                            Text("产品洞察（匿名聚合）")
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                    NavigationLink {
                        AdminAlertRecipientsView()
                    } label: {
                        HStack {
                            Image(systemName: "envelope.badge")
                            Text("告警邮件收件人")
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                    NavigationLink {
                        AdminSatisfactionView()
                    } label: {
                        HStack {
                            Image(systemName: "hand.thumbsup")
                            Text("满意度 / 群体偏好")
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 14)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .navigationTitle("管理员审计")
            .refreshable { await model.reload() }
            .task { await model.reload() }
            .overlay {
                if model.loading && model.series.isEmpty {
                    ProgressView()
                }
            }
        }
    }

    private var rangePicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(AdminRange.allCases) { r in
                    Button {
                        model.range = r
                        Task { await model.reload() }
                    } label: {
                        Text(r.rawValue)
                            .font(.caption.bold())
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(model.range == r
                                          ? Color.accentColor
                                          : Color(.secondarySystemBackground))
                            .foregroundStyle(model.range == r
                                              ? Color.white : Color.primary)
                            .clipShape(Capsule())
                    }
                }
            }
            .padding(.vertical, 4)
        }
    }

    private var kpiGrid: some View {
        let cols = [GridItem(.flexible()), GridItem(.flexible())]
        return LazyVGrid(columns: cols, spacing: 12) {
            kpi("新增订阅", value: "\(model.summary.newSubs)",
                  detail: planBreakdown())
            kpi("收入(CNY)", value: String(format: "¥%.0f", model.summary.revenueGross),
                  detail: "净 ¥\(Int(model.summary.revenueNet))")
            kpi("活跃用户", value: "\(model.summary.activeUsers)",
                  detail: "分析 \(model.summary.analyzeCharged) / \(model.summary.analyzeTotal)")
            kpi("支出(USD)", value: String(format: "$%.2f", model.summary.costUsd),
                  detail: "tokens \(model.summary.promptTokens + model.summary.completionTokens)")
        }
    }

    private func kpi(_ title: String, value: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2.bold())
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func planBreakdown() -> String {
        let map = model.summary.newSubsByPlan
        if map.isEmpty { return "—" }
        return map
            .sorted { $0.key < $1.key }
            .map { "\($0.key) \($0.value)" }
            .joined(separator: " · ")
    }

    @ViewBuilder
    private var chartCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("成功 vs 失败")
                .font(.subheadline).foregroundStyle(.secondary)
            if model.series.isEmpty {
                Text("暂无数据")
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, minHeight: 180)
            } else {
                Chart(model.series) { p in
                    LineMark(
                        x: .value("时间", p.bucketStart),
                        y: .value("成功", p.analyzeCharged),
                    )
                    .foregroundStyle(by: .value("类型", "成功"))

                    LineMark(
                        x: .value("时间", p.bucketStart),
                        y: .value("失败", p.analyzeFailed),
                    )
                    .foregroundStyle(by: .value("类型", "失败"))
                }
                .chartLegend(position: .bottom)
                .frame(minHeight: 220)
            }
        }
        .padding(14)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var topUsersCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Top 用户(按支出)")
                .font(.subheadline).foregroundStyle(.secondary)
            if model.topUsers.isEmpty {
                Text("暂无").foregroundStyle(.tertiary)
            } else {
                ForEach(model.topUsers) { u in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(String(u.id.prefix(12)) + "…")
                                .font(.footnote.monospaced())
                            Text("\(u.requests) 次 · \(u.promptTokens + u.completionTokens) tokens")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(String(format: "$%.3f", u.costUsd))
                            .font(.footnote.bold())
                    }
                    .padding(.vertical, 6)
                    Divider()
                }
            }
        }
        .padding(14)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

#Preview {
    AdminDashboardView()
}
