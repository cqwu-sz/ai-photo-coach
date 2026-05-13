// UsageHistoryView.swift  (PR6 of subscription/auth rework)
//
// User-visible audit of every /analyze run. Lets users see:
//   - 时间 + 状态 (charged / failed / pending)
//   - 四步配置 (scene / 人数 / 风格)
//   - 出片方案列表
//   - 自己选了哪个、是不是真的拍了
//   - 失败的请求会显著标注「未扣除次数」, 安抚客诉

import SwiftUI

@MainActor
final class UsageHistoryModel: ObservableObject {
    @Published var items: [Summary] = []
    @Published var loading: Bool = false
    @Published var nextCursor: String?
    @Published var lastError: String?

    struct Summary: Identifiable, Hashable {
        let id: String
        let requestId: String
        let status: String          // charged | failed | pending
        let createdAt: Date
        let chargeAt: Date?
        let errorCode: String?
        let captured: Bool
        let pickedProposalId: String?
        let sceneMode: String?
        let personCount: Int?
    }

    func loadFirstPage() async {
        items = []
        nextCursor = nil
        await loadMore()
    }

    func loadMore() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            var url = APIConfig.baseURL.appendingPathComponent("me/usage")
            var comps = URLComponents(url: url, resolvingAgainstBaseURL: false)!
            comps.queryItems = [URLQueryItem(name: "limit", value: "20")]
            if let c = nextCursor {
                comps.queryItems?.append(URLQueryItem(name: "before", value: c))
            }
            url = comps.url!
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let obj = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            let raw = (obj["items"] as? [[String: Any]]) ?? []
            let iso = ISO8601DateFormatter()
            let mapped = raw.map { d -> Summary in
                Summary(
                    id: (d["id"] as? String) ?? UUID().uuidString,
                    requestId: (d["request_id"] as? String) ?? "",
                    status: (d["status"] as? String) ?? "pending",
                    createdAt: (d["created_at"] as? String).flatMap { iso.date(from: $0) }
                                ?? Date(),
                    chargeAt: (d["charge_at"] as? String).flatMap { iso.date(from: $0) },
                    errorCode: d["error_code"] as? String,
                    captured: (d["captured"] as? Bool) ?? false,
                    pickedProposalId: d["picked_proposal_id"] as? String,
                    sceneMode: d["scene_mode"] as? String,
                    personCount: d["person_count"] as? Int,
                )
            }
            items.append(contentsOf: mapped)
            nextCursor = obj["next_cursor"] as? String
        } catch {
            lastError = (error as NSError).localizedDescription
        }
    }
}

struct UsageHistoryView: View {
    @StateObject private var model = UsageHistoryModel()

    var body: some View {
        NavigationStack {
            List {
                ForEach(model.items) { item in
                    NavigationLink {
                        UsageDetailView(recordId: item.id)
                    } label: {
                        row(item)
                    }
                }
                if model.nextCursor != nil {
                    Button {
                        Task { await model.loadMore() }
                    } label: {
                        HStack {
                            if model.loading { ProgressView() }
                            Text("加载更多")
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("使用记录")
            .refreshable { await model.loadFirstPage() }
            .task { if model.items.isEmpty { await model.loadFirstPage() } }
            .overlay {
                if model.items.isEmpty && !model.loading {
                    ContentUnavailableView("暂无记录",
                                              systemImage: "tray",
                                              description: Text("智能分析后会出现在这里"))
                }
            }
        }
    }

    private func row(_ item: UsageHistoryModel.Summary) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(Self.timeFormatter.string(from: item.createdAt))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Spacer()
                statusBadge(for: item.status)
            }
            HStack(spacing: 8) {
                if let scene = item.sceneMode {
                    Text(scene)
                        .font(.subheadline.bold())
                }
                if let count = item.personCount {
                    Text("· \(count) 人")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            if item.status == "failed" {
                Text("本次未消耗次数")
                    .font(.caption)
                    .foregroundStyle(.green)
            } else if item.status == "charged" {
                HStack(spacing: 8) {
                    if item.captured {
                        Label("已拍摄", systemImage: "camera.fill")
                            .font(.caption)
                            .foregroundStyle(.blue)
                    }
                    if item.pickedProposalId != nil {
                        Label("已选方案", systemImage: "checkmark.seal.fill")
                            .font(.caption)
                            .foregroundStyle(.purple)
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func statusBadge(for status: String) -> some View {
        switch status {
        case "charged":
            Text("已扣费").badge(.green)
        case "failed":
            Text("失败").badge(.red)
        case "pending":
            Text("处理中").badge(.orange)
        default:
            Text(status).badge(.gray)
        }
    }

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MM月dd日 HH:mm"
        return f
    }()
}

private extension Text {
    func badge(_ color: Color) -> some View {
        self.font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.18))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

// MARK: - Detail

struct UsageDetailView: View {
    let recordId: String
    @State private var detail: [String: Any] = [:]
    @State private var loading: Bool = false

    var body: some View {
        List {
            if let stepConfig = detail["step_config"] as? [String: Any] {
                Section("四步配置") {
                    ForEach(stepConfig.keys.sorted(), id: \.self) { key in
                        HStack {
                            Text(key).foregroundStyle(.secondary)
                            Spacer()
                            Text(String(describing: stepConfig[key] ?? "—"))
                                .multilineTextAlignment(.trailing)
                                .lineLimit(2)
                        }
                    }
                }
            }
            if let proposals = detail["proposals"] as? [[String: Any]], !proposals.isEmpty {
                Section("出片方案") {
                    ForEach(0..<proposals.count, id: \.self) { i in
                        VStack(alignment: .leading) {
                            Text(proposals[i]["id"] as? String ?? "shot \(i)")
                                .font(.subheadline.bold())
                            if let s = proposals[i]["summary"] as? String {
                                Text(s).font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            if let model = detail["model_id"] as? String {
                Section("模型与成本") {
                    LabeledContent("model", value: model)
                    if let p = detail["prompt_tokens"] as? Int {
                        LabeledContent("prompt tokens", value: String(p))
                    }
                    if let c = detail["completion_tokens"] as? Int {
                        LabeledContent("completion tokens", value: String(c))
                    }
                    if let cost = detail["cost_usd"] as? Double {
                        LabeledContent("cost", value: String(format: "$%.4f", cost))
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("使用详情")
        .task { await load() }
        .refreshable { await load() }
        .overlay {
            if loading && detail.isEmpty {
                ProgressView()
            }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            var req = URLRequest(url: APIConfig.baseURL
                .appendingPathComponent("me/usage/\(recordId)"))
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        } catch {
            // Keep prior state on error.
        }
    }
}

#Preview {
    UsageHistoryView()
}
