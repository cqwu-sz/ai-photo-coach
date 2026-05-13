// AdminEndpointSettingsView.swift  (v17b)
//
// Two responsibilities, in order of authority:
//   1. **Local override** (this device only) — for "let me debug
//      against staging without affecting anyone else".
//   2. **Server canonical URL** (everyone, via /api/config/endpoint)
//      — for "we're moving the production server, propagate to all
//      installs in <5min".
//
// Safety: switching the canonical URL probes /healthz on the new
// host before the backend even accepts the change isn't enforced
// by the API, but iOS's `EndpointSyncService` re-probes before
// adopting it, and we always keep the previous URL as a fallback.

import Charts
import SwiftUI

@MainActor
final class AdminEndpointModel: ObservableObject {
    @Published var primaryURL = ""
    @Published var fallbackURL = ""
    @Published var note = ""
    @Published var rolloutPercentage: Double = 100
    @Published var loading = false
    @Published var saving = false
    @Published var error: String?
    @Published var lastUpdated: String?

    func load() async {
        loading = true; defer { loading = false }
        do {
            let url = APIConfig.baseURL.appendingPathComponent("admin/endpoint")
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let cfg = try JSONDecoder().decode(AdminEndpointDTO.self, from: data)
            primaryURL = cfg.primary_url
            fallbackURL = cfg.fallback_url ?? ""
            note = cfg.note ?? ""
            rolloutPercentage = Double(cfg.rollout_percentage ?? 100)
            lastUpdated = cfg.updated_at
        } catch {
            self.error = "加载失败：\(error.localizedDescription)"
        }
    }

    func save() async {
        saving = true; defer { saving = false }
        do {
            var body: [String: Any] = ["primary_url": primaryURL]
            if !fallbackURL.isEmpty { body["fallback_url"] = fallbackURL }
            if !note.isEmpty { body["note"] = note }
            body["rollout_percentage"] = Int(rolloutPercentage.rounded())
            body["reason"] = "admin update via iOS"

            let url = APIConfig.baseURL.appendingPathComponent("admin/endpoint")
            var req = URLRequest(url: url)
            req.httpMethod = "PUT"
            req.timeoutInterval = APIConfig.connectTimeout
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                throw NSError(domain: "AdminEndpoint", code: http.statusCode,
                               userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode)"])
            }
            await load()
            // Force a remote-config re-sync immediately so the local
            // app picks up the new URL within seconds (rather than
            // waiting for the 5-min poll).
            await EndpointSyncService.shared.syncOnce()
        } catch {
            self.error = "保存失败：\(error.localizedDescription)"
        }
    }
}

struct AdminEndpointDTO: Decodable {
    let primary_url: String
    let fallback_url: String?
    let note: String?
    let updated_at: String?
    let rollout_percentage: Int?
}


// MARK: - Distribution view

private struct DistributionSeriesDTO: Decodable {
    struct Bucket: Decodable, Identifiable {
        let t: String
        let pct: Double
        let total: Int
        let canonical: Int
        var id: String { t }
        var date: Date? { ISO8601DateFormatter().date(from: t) }
    }
    let canonical_url: String
    let target_pct: Int
    let window_hours: Int
    let bucket_minutes: Int
    let buckets: [Bucket]
}

private struct DistributionDTO: Decodable {
    struct Item: Decodable, Identifiable {
        let active_url: String
        let devices: Int
        let polls: Int
        let is_canonical: Bool
        var id: String { active_url }
    }
    let canonical_url: String
    let total_devices: Int
    let canonical_devices: Int
    let rollout_pct: Double
    let target_pct: Int
    let alert: String
    let alert_message: String?
    let items: [Item]
}

@MainActor
private final class DistributionModel: ObservableObject {
    @Published var data: DistributionDTO?
    @Published var series: DistributionSeriesDTO?
    @Published var error: String?
    @Published var loading = false
    @Published var hours: Int = 1

    func reload() async {
        loading = true; defer { loading = false }
        async let snap: () = reloadSnapshot()
        async let line: () = reloadSeries()
        _ = await (snap, line)
    }

    private func reloadSnapshot() async {
        do {
            var comps = URLComponents(url: APIConfig.baseURL.appendingPathComponent(
                "admin/endpoint/distribution"), resolvingAgainstBaseURL: false)!
            comps.queryItems = [URLQueryItem(name: "hours", value: String(hours))]
            var req = URLRequest(url: comps.url!)
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            self.data = try JSONDecoder().decode(DistributionDTO.self, from: data)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func reloadSeries() async {
        do {
            // Pick a reasonable bucket size for the chosen window so
            // we always show ~12-24 points (any more is unreadable
            // on iPhone, any less hides micro-stalls).
            let bucket: Int = {
                switch hours {
                case 1: return 5
                case 3: return 15
                case 12: return 30
                default: return 60
                }
            }()
            var comps = URLComponents(url: APIConfig.baseURL.appendingPathComponent(
                "admin/endpoint/distribution/series"), resolvingAgainstBaseURL: false)!
            comps.queryItems = [
                URLQueryItem(name: "hours", value: String(hours)),
                URLQueryItem(name: "bucket_minutes", value: String(bucket)),
            ]
            var req = URLRequest(url: comps.url!)
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            self.series = try JSONDecoder().decode(DistributionSeriesDTO.self,
                                                       from: data)
        } catch {
            // Snapshot already surfaced an error; don't double-toast.
        }
    }
}

struct AdminEndpointDistributionView: View {
    @StateObject private var model = DistributionModel()
    @State private var autoRefresh: Bool = false
    @State private var refreshTask: Task<Void, Never>?

    var body: some View {
        List {
            Section {
                Picker("时间窗口", selection: $model.hours) {
                    Text("1h").tag(1)
                    Text("3h").tag(3)
                    Text("12h").tag(12)
                    Text("24h").tag(24)
                }
                .pickerStyle(.segmented)
                .onChange(of: model.hours) { _, _ in Task { await model.reload() } }
                Toggle("每 5 秒自动刷新（切换中实时观察用）",
                         isOn: $autoRefresh)
                    .onChange(of: autoRefresh) { _, on in
                        refreshTask?.cancel()
                        guard on else { return }
                        refreshTask = Task {
                            while !Task.isCancelled {
                                try? await Task.sleep(nanoseconds: 5_000_000_000)
                                if Task.isCancelled { break }
                                await model.reload()
                            }
                        }
                    }
            }

            if let s = model.series, !s.buckets.isEmpty {
                Section("采用率推进曲线（绿线=目标 \(s.target_pct)%）") {
                    Chart {
                        // Target rule line — shows where we want to be.
                        RuleMark(y: .value("目标", Double(s.target_pct)))
                            .foregroundStyle(.green.opacity(0.6))
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                        ForEach(s.buckets) { b in
                            if let date = b.date {
                                LineMark(
                                    x: .value("时间", date),
                                    y: .value("采用率", b.pct)
                                )
                                .interpolationMethod(.monotone)
                                .foregroundStyle(.blue)
                                AreaMark(
                                    x: .value("时间", date),
                                    y: .value("采用率", b.pct)
                                )
                                .interpolationMethod(.monotone)
                                .foregroundStyle(
                                    LinearGradient(colors: [.blue.opacity(0.3),
                                                              .blue.opacity(0.05)],
                                                    startPoint: .top, endPoint: .bottom)
                                )
                            }
                        }
                    }
                    .chartYScale(domain: 0...100)
                    .chartYAxis {
                        AxisMarks(values: [0, 25, 50, 75, 100]) { v in
                            AxisGridLine()
                            AxisValueLabel("\(v.as(Int.self) ?? 0)%")
                        }
                    }
                    .frame(height: 180)
                    Text("每 \(s.bucket_minutes) 分钟一格 · 共 \(s.buckets.count) 个采样点")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }

            if let d = model.data {
                Section("汇总") {
                    LabeledContent("规范 URL", value: d.canonical_url)
                    LabeledContent("活跃设备", value: "\(d.total_devices)")
                    LabeledContent("已采用", value: "\(d.canonical_devices)")
                    LabeledContent("采用率", value: String(format: "%.1f%% / 目标 %d%%",
                                                              d.rollout_pct, d.target_pct))
                    if let msg = d.alert_message {
                        Text(msg)
                            .font(.footnote)
                            .foregroundStyle(d.alert == "critical" ? .red :
                                              (d.alert == "warning" ? .orange : .secondary))
                    }
                }
                Section("各 URL 设备分布") {
                    ForEach(d.items) { item in
                        VStack(alignment: .leading) {
                            HStack {
                                Text(item.active_url).font(.footnote.monospaced()).lineLimit(2)
                                if item.is_canonical {
                                    Text("规范")
                                        .font(.caption2.bold())
                                        .padding(.horizontal, 6).padding(.vertical, 2)
                                        .background(.green.opacity(0.2),
                                                      in: Capsule())
                                }
                            }
                            Text("\(item.devices) 设备 · \(item.polls) 次轮询")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let err = model.error {
                Section { Text(err).foregroundStyle(.red) }
            }
        }
        .navigationTitle("采用率分布")
        .task { await model.reload() }
        .refreshable { await model.reload() }
        .onDisappear { refreshTask?.cancel() }
    }
}


// MARK: - History view

private struct HistoryItemDTO: Decodable, Identifiable {
    let primary_url: String
    let fallback_url: String?
    let changed_by: String?
    let changed_at: String
    let reason: String?
    var id: String { changed_at + primary_url }
}

private struct HistoryListDTO: Decodable {
    let items: [HistoryItemDTO]
}

struct AdminEndpointHistoryView: View {
    @State private var items: [HistoryItemDTO] = []
    @State private var error: String?

    var body: some View {
        List(items) { e in
            VStack(alignment: .leading, spacing: 4) {
                Text(e.primary_url).font(.footnote.monospaced())
                if let f = e.fallback_url, !f.isEmpty {
                    Text("fallback: \(f)").font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    Text(e.changed_at).font(.caption2).foregroundStyle(.secondary)
                    if let by = e.changed_by {
                        Text("by \(by)").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let r = e.reason, !r.isEmpty {
                    Text(r).font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 4)
        }
        .navigationTitle("变更历史")
        .overlay {
            if let err = error { Text(err).foregroundStyle(.red) }
        }
        .task {
            do {
                let url = APIConfig.baseURL.appendingPathComponent(
                    "admin/endpoint/history")
                var req = URLRequest(url: url)
                let token = try await AuthManager.shared.accessToken()
                req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                let (data, _) = try await URLSession.shared.data(for: req)
                items = (try JSONDecoder().decode(HistoryListDTO.self, from: data)).items
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

struct AdminEndpointSettingsView: View {
    @StateObject private var model = AdminEndpointModel()
    @ObservedObject private var store = ServerEndpointStore.shared
    @State private var localOverride = ""

    var body: some View {
        Form {
            Section("当前生效") {
                LabeledContent("Active baseURL", value: APIConfig.baseURL.absoluteString)
                if let last = store.lastSyncedAt {
                    LabeledContent("远端配置同步时间",
                                    value: last.formatted(date: .abbreviated, time: .standard))
                }
            }

            Section("本机覆盖（仅本机）") {
                TextField("https://staging.example.com", text: $localOverride)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                HStack {
                    Button("应用覆盖") {
                        if !store.setOverride(localOverride) {
                            model.error = "仅管理员可设置本机覆盖。"
                        }
                    }
                    .disabled(localOverride.isEmpty)
                    Spacer()
                    Button("清除覆盖", role: .destructive) {
                        _ = store.setOverride(nil)
                        localOverride = ""
                    }
                    .disabled(store.activeOverrideRaw == nil)
                }
                if let cur = store.activeOverrideRaw {
                    Text("当前覆盖：\(cur)")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }

            Section("全局规范地址（影响所有用户）") {
                TextField("primary_url (https://...)", text: $model.primaryURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("fallback_url（可选）", text: $model.fallbackURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("备注（可选）", text: $model.note)
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("灰度比例")
                        Spacer()
                        Text("\(Int(model.rolloutPercentage.rounded()))%")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    Slider(value: $model.rolloutPercentage, in: 0...100, step: 5)
                    Text("<100% 时未命中的设备会走 fallback_url，建议先 10%/50%/100% 三段灰度。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if let ts = model.lastUpdated {
                    Text("上次更新：\(ts)")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Button(model.saving ? "保存中…" : "保存到服务器") {
                    Task { await model.save() }
                }
                .disabled(model.saving || model.primaryURL.isEmpty)
            }

            if let err = model.error {
                Section { Text(err).foregroundStyle(.red).font(.footnote) }
            }

            Section("观测") {
                NavigationLink("采用率分布 / 灰度进度") {
                    AdminEndpointDistributionView()
                }
                NavigationLink("历史变更记录") {
                    AdminEndpointHistoryView()
                }
            }

            Section {
                Text("说明：覆盖只影响本机；保存到服务器会让所有客户端在 5 分钟内（或下次冷启动）切换到新地址。" +
                      "正在进行中的请求会沿用旧地址，避免打断；新请求自动走新地址。" +
                      "若新地址 /healthz 不通，客户端会拒绝采纳并保留旧地址。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("服务器地址")
        .task { await model.load() }
        .onAppear { localOverride = store.activeOverrideRaw ?? "" }
    }
}
