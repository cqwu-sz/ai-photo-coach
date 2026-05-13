// AdminSatisfactionView.swift  (v18)
//
// Operator dashboard for the cross-user satisfaction signal that
// optionally feeds the analyze prompt as a "##  CROSS_USER_TREND"
// hint. Shows what would (and would not) light up under the current
// thresholds, plus a one-tap kill switch.
//
// Data flow:
//   GET  /admin/satisfaction/aggregates    — read all (scene, style)
//                                            rows + current knobs
//   POST /admin/satisfaction/global_hint/kill — write enabled = false,
//                                            reset cache, audit it
//   PUT  /admin/runtime_settings           — adjust the four knobs
//
// We also surface the tier-warning if the operator forgets to set
// recipients for `severity.trend`, since this dashboard is the
// primary place where such "trend"-class signals originate.

import SwiftUI

private struct AggregateRow: Identifiable, Decodable {
    let scene_mode: String
    let style_id: String
    let label_zh: String
    let satisfied: Int
    let dissatisfied: Int
    let distinct_users: Int
    let satisfaction_rate: Double?
    let updated_at: String?
    var id: String { "\(scene_mode):\(style_id)" }
}

private struct AggregatesDTO: Decodable {
    let enabled: Bool
    let thresholds: Thresholds
    let items: [AggregateRow]

    struct Thresholds: Decodable {
        let min_distinct_users: Int
        let min_satisfaction_rate: String
        let cooldown_sec: Int
    }
}

@MainActor
private final class SatisfactionModel: ObservableObject {
    @Published var enabled: Bool = false
    @Published var minDistinctUsers: Int = 30
    @Published var minSatisfactionRate: Double = 0.6
    @Published var cooldownSec: Int = 300
    @Published var rows: [AggregateRow] = []
    @Published var error: String?
    @Published var busy = false
    /// v18 s3 — keys we found configured under alert.recipients.*.
    /// Used to nag if `pref.global_hint.enabled = true` but
    /// `severity.trend` has no dedicated recipient.
    @Published var configuredRecipientKeys: Set<String> = []

    func load() async {
        busy = true; defer { busy = false }
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/satisfaction/aggregates")
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let dto = try JSONDecoder().decode(AggregatesDTO.self, from: data)
            enabled = dto.enabled
            minDistinctUsers = dto.thresholds.min_distinct_users
            minSatisfactionRate = Double(dto.thresholds.min_satisfaction_rate)
                ?? 0.6
            cooldownSec = dto.thresholds.cooldown_sec
            rows = dto.items
            error = nil
            await loadRecipientsKeys(token: token)
        } catch {
            self.error = "加载失败：\(error.localizedDescription)"
        }
    }

    /// v18 s3 — pull just the keys (no values) so we can warn if
    /// trend alerts will fall back to default. Failure is swallowed
    /// because the warning is best-effort.
    private func loadRecipientsKeys(token: String) async {
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/alerts/recipients")
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            struct Wrap: Decodable { let items: [Item] }
            struct Item: Decodable { let action: String }
            let items = try JSONDecoder().decode(Wrap.self, from: data).items
            configuredRecipientKeys = Set(items.map { $0.action })
        } catch {
            configuredRecipientKeys = []
        }
    }

    func setKnob(_ key: String, _ value: String) async {
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/runtime_settings")
            var req = URLRequest(url: url)
            req.httpMethod = "PUT"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.httpBody = try JSONSerialization.data(
                withJSONObject: ["key": key, "value": value])
            _ = try await URLSession.shared.data(for: req)
        } catch {
            self.error = "保存失败：\(error.localizedDescription)"
        }
    }

    func kill() async {
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/satisfaction/global_hint/kill")
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            _ = try await URLSession.shared.data(for: req)
            await load()
        } catch {
            self.error = "关闭失败：\(error.localizedDescription)"
        }
    }
}

struct AdminSatisfactionView: View {
    @StateObject private var model = SatisfactionModel()
    @State private var confirmKill = false
    @AppStorage("shoot.chip_min_shots") private var chipMinShots: Int = 3

    private static let _isoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let _displayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MM-dd HH:mm"
        return f
    }()

    private static func relative(_ iso: String?) -> String {
        guard let iso = iso else { return "—" }
        let date = _isoFormatter.date(from: iso)
            ?? ISO8601DateFormatter().date(from: iso)
        guard let d = date else { return iso }
        let now = Date()
        let secs = Int(now.timeIntervalSince(d))
        if secs < 60 { return "刚刚" }
        if secs < 3600 { return "\(secs/60) 分钟前" }
        if secs < 86400 { return "\(secs/3600) 小时前" }
        return _displayFormatter.string(from: d)
    }

    var body: some View {
        Form {
            if let err = model.error {
                Section {
                    Text(err)
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }

            if model.enabled, !trendRecipientConfigured {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.triangle.fill")
                            Text("severity.trend 未配独立收件人")
                                .font(.subheadline.bold())
                        }
                        Text("群体偏好 hint 已启用，但「告警邮件收件人」里没有 severity.trend 的独立收件人。趋势异常邮件会落到默认地址，容易被淹没。建议在「告警邮件收件人」加 severity.trend 收件人。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
                .listRowBackground(Color.orange.opacity(0.10))
            }

            Section("总开关 · 群体偏好 hint") {
                Toggle("启用群体偏好 hint", isOn: Binding(
                    get: { model.enabled },
                    set: { newVal in
                        Task {
                            await model.setKnob(
                                "pref.global_hint.enabled",
                                newVal ? "true" : "false")
                            await model.load()
                        }
                    }
                ))
                Text("OFF（默认）：每个用户的方案只受自己历史偏好影响。\n"
                       + "ON：达到 k-anon 与满意率门槛的 (场景, 风格) 会作为弱建议进入 prompt。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button(role: .destructive) {
                    confirmKill = true
                } label: {
                    Label("一键关闭（写 false 并清缓存）", systemImage: "xmark.octagon")
                }
                .confirmationDialog(
                    "确认关闭群体偏好 hint？",
                    isPresented: $confirmKill,
                    titleVisibility: .visible
                ) {
                    Button("立即关闭", role: .destructive) {
                        Task { await model.kill() }
                    }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("会立即把 pref.global_hint.enabled 改为 false 并清空缓存。所有节点 30 秒内生效；写入会被审计。误触请取消。")
                }
            }

            Section("门槛") {
                Stepper(value: Binding(
                    get: { model.minDistinctUsers },
                    set: { model.minDistinctUsers = $0 }
                ), in: 5...500, step: 5) {
                    LabeledContent("min_distinct_users",
                                     value: "\(model.minDistinctUsers)")
                }
                Stepper(value: Binding(
                    get: { model.minSatisfactionRate },
                    set: { model.minSatisfactionRate = $0 }
                ), in: 0.0...1.0, step: 0.05) {
                    LabeledContent("min_satisfaction_rate",
                                     value: String(format: "%.2f",
                                                      model.minSatisfactionRate))
                }
                Stepper(value: Binding(
                    get: { model.cooldownSec },
                    set: { model.cooldownSec = $0 }
                ), in: 30...3600, step: 30) {
                    LabeledContent("cooldown_sec",
                                     value: "\(model.cooldownSec)")
                }
                Button {
                    Task {
                        await model.setKnob(
                            "pref.global_hint.min_distinct_users",
                            String(model.minDistinctUsers))
                        await model.setKnob(
                            "pref.global_hint.min_satisfaction_rate",
                            String(format: "%.3f", model.minSatisfactionRate))
                        await model.setKnob(
                            "pref.global_hint.cooldown_sec",
                            String(model.cooldownSec))
                        await model.load()
                    }
                } label: {
                    Label("保存门槛", systemImage: "tray.and.arrow.down")
                }
            }

            Section("客户端体验（仅本机生效）") {
                Stepper(value: $chipMinShots, in: 1...20) {
                    LabeledContent("满意度 chip 出现前需拍张数",
                                     value: "\(chipMinShots)")
                }
                Text("默认 3。本设备本地写 UserDefaults `shoot.chip_min_shots`；用于 A/B 体验，无需发版。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("当前聚合 · 含未达标行") {
                if model.rows.isEmpty {
                    Text(model.busy ? "加载中…" : "暂无满意度数据")
                        .foregroundStyle(.secondary)
                        .font(.footnote)
                } else {
                    ForEach(model.rows) { r in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text("\(r.scene_mode) · \(r.label_zh)")
                                    .font(.subheadline.bold())
                                Spacer()
                                if let rate = r.satisfaction_rate {
                                    Text(String(format: "%.0f%%", rate * 100))
                                        .font(.caption.bold())
                                        .foregroundStyle(passes(r) ? .green
                                                                       : .secondary)
                                }
                            }
                            HStack(spacing: 6) {
                                Text("\(r.satisfied) 满意 · \(r.dissatisfied) 不满意 · \(r.distinct_users) 位用户")
                                Spacer()
                                Text("更新于 \(Self.relative(r.updated_at))")
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            if !passes(r) {
                                Text(reasonNotPassing(r))
                                    .font(.caption2)
                                    .foregroundStyle(.orange)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
        }
        .navigationTitle("满意度 / 群体偏好")
        .task { await model.load() }
        .refreshable { await model.load() }
    }

    private var trendRecipientConfigured: Bool {
        let keys = model.configuredRecipientKeys
        // exact "severity.trend" key OR a fine-grained prefix match
        // (e.g. "satisfaction.global_hint" override).
        return keys.contains("severity.trend")
            || keys.contains(where: { $0.hasPrefix("satisfaction.")
                                        || $0.hasPrefix("trend_anomaly") })
    }

    private func passes(_ r: AggregateRow) -> Bool {
        guard let rate = r.satisfaction_rate else { return false }
        return r.distinct_users >= model.minDistinctUsers
            && rate >= model.minSatisfactionRate
    }

    private func reasonNotPassing(_ r: AggregateRow) -> String {
        var bits: [String] = []
        if r.distinct_users < model.minDistinctUsers {
            bits.append("distinct \(r.distinct_users) < \(model.minDistinctUsers)")
        }
        if let rate = r.satisfaction_rate, rate < model.minSatisfactionRate {
            bits.append(String(format: "rate %.2f < %.2f", rate,
                                  model.minSatisfactionRate))
        }
        return "未达标：" + bits.joined(separator: "；")
    }
}
