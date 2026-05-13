// AdminInsightsView.swift  (v17g)
//
// Anonymized product analytics: scene mode mix, quality mode mix,
// top style keywords, proposal adoption rate. All numbers come
// from /admin/insights/* which apply a min-N=5 floor.

import SwiftUI

private struct DistItem: Decodable, Identifiable {
    let key: String
    let calls: Int
    let distinct_users: Int
    let merged_from_low_n: Bool?
    var id: String { key }
}

private struct DistDTO: Decodable {
    let since_hours: Int
    let total_calls: Int?
    let distinct_keywords_seen: Int?
    let items: [DistItem]
}

private struct ProposalItem: Decodable, Identifiable {
    let key: String
    let calls: Int
    let picked: Int
    let captured: Int
    let adoption_rate: Double
    let capture_rate: Double
    let merged_from_low_n: Bool?
    var id: String { key }
}

private struct ProposalDTO: Decodable {
    let since_hours: Int
    let total_offered: Int
    let total_picked: Int
    let total_captured: Int
    let items: [ProposalItem]
}

@MainActor
private final class InsightsModel: ObservableObject {
    enum Metric: String, CaseIterable { case calls, distinct_users }

    @Published var hours: Int = 24 * 7
    @Published var metric: Metric = .calls
    @Published var loading = false
    @Published var error: String?
    @Published var scene: DistDTO?
    @Published var quality: DistDTO?
    @Published var keywords: DistDTO?
    @Published var proposals: ProposalDTO?

    func reload() async {
        loading = true; defer { loading = false }
        let m = "metric=\(metric.rawValue)"
        async let a: () = load(\.scene, "admin/insights/scene_modes?\(m)")
        async let b: () = load(\.quality, "admin/insights/quality_modes?\(m)")
        async let c: () = load(\.keywords,
                                  "admin/insights/style_keywords?top_n=30&\(m)")
        async let d: () = loadProposals()
        _ = await (a, b, c, d)
    }

    func csvURL() -> URL {
        var comps = URLComponents(url: APIConfig.baseURL.appendingPathComponent(
            "admin/insights/export.csv"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "hours", value: String(hours))]
        return comps.url!
    }

    func downloadCSV() async -> URL? {
        do {
            var req = URLRequest(url: csvURL())
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let tmp = FileManager.default.temporaryDirectory
                .appendingPathComponent("aiphoto-insights-\(hours)h.csv")
            try data.write(to: tmp, options: .atomic)
            return tmp
        } catch {
            self.error = "CSV 下载失败：\(error.localizedDescription)"
            return nil
        }
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let glue = path.contains("?") ? "&" : "?"
        let url = APIConfig.baseURL.appendingPathComponent(path)
        var comps = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        let extra = "hours=\(hours)"
        comps.percentEncodedQuery = (comps.percentEncodedQuery.map { $0 + "&" + extra })
            ?? extra
        var req = URLRequest(url: comps.url!)
        let token = try await AuthManager.shared.accessToken()
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func load<T: Decodable>(_ kp: ReferenceWritableKeyPath<InsightsModel, T?>,
                                       _ path: String) async {
        do { self[keyPath: kp] = try await get(path) }
        catch { self.error = "加载失败：\(error.localizedDescription)" }
    }

    private func loadProposals() async {
        do { proposals = try await get("admin/insights/proposal_adoption") }
        catch { self.error = "加载失败：\(error.localizedDescription)" }
    }
}

struct AdminInsightsView: View {
    @StateObject private var model = InsightsModel()
    @State private var csvURL: URL?
    @State private var showShare = false

    var body: some View {
        Form {
            Section {
                Picker("时间窗口", selection: $model.hours) {
                    Text("24h").tag(24)
                    Text("7d").tag(24 * 7)
                    Text("30d").tag(24 * 30)
                    Text("90d").tag(24 * 90)
                }
                .pickerStyle(.segmented)
                .onChange(of: model.hours) { _, _ in Task { await model.reload() } }
                Picker("排序口径", selection: $model.metric) {
                    Text("调用次数").tag(InsightsModel.Metric.calls)
                    Text("独立用户").tag(InsightsModel.Metric.distinct_users)
                }
                .pickerStyle(.segmented)
                .onChange(of: model.metric) { _, _ in Task { await model.reload() } }
                Text("数据已做 k-anon 处理：分布桶 < 5 个独立用户会合并为「(其它)」。"
                       + "「独立用户」视角排除高活跃用户的偏好放大效应。")
                    .font(.caption2).foregroundStyle(.secondary)
                Button {
                    Task {
                        if let url = await model.downloadCSV() {
                            csvURL = url; showShare = true
                        }
                    }
                } label: {
                    Label("导出 CSV (\(hoursLabel))", systemImage: "square.and.arrow.down")
                }
            }

            if let err = model.error {
                Section { Text(err).foregroundStyle(.red).font(.footnote) }
            }

            distSection(title: "出片模式（scene_mode）", dto: model.scene)
            distSection(title: "画质偏好（quality_mode）", dto: model.quality)
            distSection(title: "风格关键词 Top 30", dto: model.keywords)
            proposalSection
        }
        .navigationTitle("产品洞察")
        .task { await model.reload() }
        .refreshable { await model.reload() }
        .overlay { if model.loading && model.scene == nil { ProgressView() } }
        .sheet(isPresented: $showShare) {
            if let url = csvURL {
                ShareSheet(items: [url])
            }
        }
    }

    private var hoursLabel: String {
        switch model.hours {
        case 24: return "24h"
        case 24 * 7: return "7d"
        case 24 * 30: return "30d"
        case 24 * 90: return "90d"
        default: return "\(model.hours)h"
        }
    }

    @ViewBuilder
    private func distSection(title: String, dto: DistDTO?) -> some View {
        Section(title) {
            if let d = dto {
                if let total = d.total_calls {
                    Text("窗口内 \(total) 次调用").font(.caption).foregroundStyle(.secondary)
                }
                if let kw = d.distinct_keywords_seen {
                    Text("出现过的不同关键词：\(kw)").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(d.items) { row in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.key)
                                .font(.system(.subheadline, design: .monospaced))
                            Text("\(row.distinct_users) 用户")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("\(row.calls)").bold()
                        if row.merged_from_low_n == true {
                            Image(systemName: "lock.shield")
                                .foregroundStyle(.green).font(.caption)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var proposalSection: some View {
        Section("出片方案采纳率") {
            if let p = model.proposals {
                Text("offered \(p.total_offered) · picked \(p.total_picked) · captured \(p.total_captured)")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(p.items) { row in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(row.key)
                                .font(.system(.subheadline, design: .monospaced))
                                .lineLimit(1)
                            Spacer()
                            Text("offered \(row.calls)")
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                        HStack(spacing: 12) {
                            tag("采纳", value: "\(Int(row.adoption_rate * 100))%",
                                 color: row.adoption_rate >= 0.3 ? .green : .orange)
                            tag("拍摄", value: "\(Int(row.capture_rate * 100))%",
                                 color: row.capture_rate >= 0.5 ? .green : .secondary)
                            Text("picked \(row.picked) · cap \(row.captured)")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    private func tag(_ label: String, value: String, color: Color) -> some View {
        HStack(spacing: 4) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.caption.bold()).foregroundStyle(color)
        }
        .padding(.horizontal, 8).padding(.vertical, 3)
        .background(color.opacity(0.12), in: Capsule())
    }
}
