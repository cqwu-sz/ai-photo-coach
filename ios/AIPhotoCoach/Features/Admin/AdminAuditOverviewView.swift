// AdminAuditOverviewView.swift  (v17g)
//
// On-call admin's first stop. Three cards:
//   1. 24h anomaly summary (refunds, perm-locks, rollbacks, blocklist hits)
//   2. Recent logins (admin sessions highlighted)
//   3. Active devices (sanity check on rollout reach)
//
// Backed by /admin/anomaly_summary, /admin/audit/recent_logins,
// /admin/active_devices. All polled in parallel on appear &
// pull-to-refresh.

import SwiftUI

private struct AnomalyDTO: Decodable {
    let window_hours: Int
    let counts: [String: Int]
    let blocklist_enforce_by_scope: [String: Int]
    struct Rollback: Decodable, Identifiable {
        let occurred_at: String
        let primary_url: String?
        var id: String { occurred_at }
    }
    let recent_rollbacks: [Rollback]
}

private struct LoginDTO: Decodable, Identifiable {
    let id: Int
    let user_id: String?
    let channel: String?
    let client_ip: String?
    let user_agent: String?
    let is_admin: Bool
    let occurred_at: String
}

private struct ActiveDevicesDTO: Decodable {
    let window_hours: Int
    let total_devices: Int
    struct ByVer: Decodable, Identifiable {
        let app_version: String
        let devices: Int
        var id: String { app_version }
    }
    let by_app_version: [ByVer]
}

@MainActor
private final class AuditOverviewModel: ObservableObject {
    @Published var anomaly: AnomalyDTO?
    @Published var logins: [LoginDTO] = []
    @Published var devices: ActiveDevicesDTO?
    @Published var loading = false
    @Published var error: String?

    func reload() async {
        loading = true; defer { loading = false }
        async let a: () = loadAnomaly()
        async let l: () = loadLogins()
        async let d: () = loadDevices()
        _ = await (a, l, d)
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var req = URLRequest(url: APIConfig.baseURL.appendingPathComponent(path))
        req.timeoutInterval = APIConfig.connectTimeout
        let token = try await AuthManager.shared.accessToken()
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func loadAnomaly() async {
        do { anomaly = try await get("admin/anomaly_summary?hours=24") }
        catch { self.error = "异常摘要加载失败：\(error.localizedDescription)" }
    }

    private func loadLogins() async {
        struct Wrap: Decodable { let items: [LoginDTO] }
        do { logins = (try await get("admin/audit/recent_logins?limit=20") as Wrap).items }
        catch { /* anomaly already toasted */ }
    }

    private func loadDevices() async {
        do { devices = try await get("admin/active_devices?hours=24") }
        catch { /* same */ }
    }
}

struct AdminAuditOverviewView: View {
    @StateObject private var model = AuditOverviewModel()

    private var alertLevel: (color: Color, label: String) {
        guard let a = model.anomaly else { return (.gray, "加载中") }
        let refunds = a.counts["iap.asn.refund"] ?? 0
        let revokes = a.counts["iap.asn.revoke"] ?? 0
        let permLocks = a.counts["otp.permanent_lock"] ?? 0
        let rollbacks = a.counts["endpoint.rollback"] ?? 0
        let blocklistHits = a.blocklist_enforce_by_scope.values.reduce(0, +)
        if refunds > 5 || revokes > 0 || rollbacks > 0 {
            return (.red, "需关注")
        }
        if permLocks > 0 || blocklistHits > 100 {
            return (.orange, "有异常活动")
        }
        return (.green, "一切正常")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                anomalyCard
                loginsCard
                devicesCard
            }
            .padding(16)
        }
        .navigationTitle("运营态势 (24h)")
        .task { await model.reload() }
        .refreshable { await model.reload() }
        .overlay {
            if model.loading && model.anomaly == nil {
                ProgressView()
            }
        }
    }

    private var anomalyCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Circle().fill(alertLevel.color).frame(width: 10, height: 10)
                Text(alertLevel.label).font(.headline)
                Spacer()
                Text("近 24 小时").font(.caption).foregroundStyle(.secondary)
            }
            if let a = model.anomaly {
                let pairs: [(String, Int, Color)] = [
                    ("退款", a.counts["iap.asn.refund"] ?? 0, .red),
                    ("撤销", a.counts["iap.asn.revoke"] ?? 0, .red),
                    ("过期", a.counts["iap.asn.expired"] ?? 0, .secondary),
                    ("永久封号", a.counts["otp.permanent_lock"] ?? 0, .orange),
                    ("账号删除", a.counts["user.soft_delete"] ?? 0, .secondary),
                    ("数据导出", a.counts["user.data_export"] ?? 0, .blue),
                    ("URL 回滚", a.counts["endpoint.rollback"] ?? 0, .red),
                ]
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12),
                                            count: 3), spacing: 12) {
                    ForEach(pairs, id: \.0) { name, count, color in
                        VStack(spacing: 4) {
                            Text("\(count)").font(.title2.bold()).foregroundStyle(color)
                            Text(name).font(.caption).foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
                    }
                }
                if !a.blocklist_enforce_by_scope.isEmpty {
                    HStack {
                        Image(systemName: "shield.lefthalf.filled")
                        Text("blocklist 拦截：")
                        Text(a.blocklist_enforce_by_scope.map { "\($0.key)×\($0.value)" }
                                .joined(separator: " · "))
                            .font(.footnote.monospaced())
                    }
                    .font(.footnote).foregroundStyle(.secondary)
                }
                if !a.recent_rollbacks.isEmpty {
                    Divider()
                    Text("最近 URL 回滚").font(.caption.bold()).foregroundStyle(.red)
                    ForEach(a.recent_rollbacks) { r in
                        Text("→ \(r.primary_url ?? "?") @ \(r.occurred_at)")
                            .font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(16)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var loginsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "person.crop.circle.badge.checkmark")
                Text("最近登录 (20)").font(.headline)
                Spacer()
            }
            if model.logins.isEmpty {
                Text("无").font(.caption).foregroundStyle(.secondary)
            } else {
                ForEach(model.logins) { row in
                    HStack(spacing: 8) {
                        if row.is_admin {
                            Image(systemName: "shield.fill").foregroundStyle(.purple)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.user_id ?? "?")
                                .font(.caption.monospaced()).lineLimit(1)
                            Text("\(row.channel ?? "?") · \(row.client_ip ?? "?")")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(formatTime(row.occurred_at))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Divider()
                }
            }
        }
        .padding(16)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var devicesCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "iphone.gen3")
                Text("活跃设备 (24h)").font(.headline)
                Spacer()
                if let d = model.devices {
                    Text("\(d.total_devices) 台").font(.title3.bold())
                }
            }
            if let d = model.devices, !d.by_app_version.isEmpty {
                ForEach(d.by_app_version) { row in
                    HStack {
                        Text(row.app_version).font(.caption.monospaced())
                        Spacer()
                        Text("\(row.devices)").font(.caption.bold())
                    }
                }
            } else if model.devices != nil {
                Text("暂无遥测数据").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private func formatTime(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter().date(from: iso) else { return iso }
        let f = DateFormatter()
        f.dateFormat = "MM-dd HH:mm"
        return f.string(from: d)
    }
}
