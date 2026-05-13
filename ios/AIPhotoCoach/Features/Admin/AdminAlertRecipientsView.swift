// AdminAlertRecipientsView.swift  (v17g)
//
// Manage which inbox(es) get the formatted alert mails when high-
// value audit events fire (refunds, perm-locks, admin login from
// new IP, etc.).
//
// Backend stores them in runtime_settings under
// `alert.recipients.<action>`. This view treats it as a list-of-lists.

import SwiftUI

private let kSuggestedActions: [(String, String)] = [
    ("default", "默认（兜底）"),
    // v17j — severity tiers. Use these to keep critical paging
    // separate from analytics noise. Concrete-action overrides
    // above still win when present.
    ("severity.critical", "严重（安全事故 / 资金损失）"),
    ("severity.warning", "警告（业务影响事件）"),
    ("severity.info", "提醒（管理员操作 / 配置变更）"),
    ("severity.trend", "趋势（关键词突起 / 产品分析）"),
    ("iap.asn.refund", "退款"),
    ("iap.asn.revoke", "撤销订阅"),
    ("iap.asn.*", "所有 Apple 订阅事件"),
    ("asn.signature_invalid", "ASN 验签失败（高危）"),
    ("asn.unmatched", "孤儿订阅"),
    ("otp.permanent_lock", "OTP 永久封号"),
    ("alert.webhook_failed", "告警通道自身失败（高危）"),
    ("trend.anomaly", "关键词突起"),
    ("auth.admin_login_success", "管理员登录"),
    ("endpoint_config.save", "服务器地址变更"),
    ("user.data_export", "用户导出数据"),
]

private struct RecipientItem: Identifiable, Decodable {
    let action: String
    let recipients: [String]
    let updated_by: String?
    let updated_at: String?
    var id: String { action }
}

private struct RecipientsListDTO: Decodable {
    let enabled: Bool
    let default_cooldown_sec: Int
    let items: [RecipientItem]
}

private struct AlertPreviewDTO: Decodable {
    let action: String
    let subject: String
    let body: String
    let would_send_to: [String]
}

@MainActor
private final class AlertRecipientsModel: ObservableObject {
    @Published var enabled = true
    @Published var defaultCooldown = 300
    @Published var items: [RecipientItem] = []
    @Published var loading = false
    @Published var saving = false
    @Published var error: String?
    @Published var lastTestSent: Int?
    @Published var preview: AlertPreviewDTO?

    func load() async {
        loading = true; defer { loading = false }
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/alerts/recipients")
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let dto = try JSONDecoder().decode(RecipientsListDTO.self, from: data)
            self.enabled = dto.enabled
            self.defaultCooldown = dto.default_cooldown_sec
            self.items = dto.items
        } catch {
            self.error = "加载失败：\(error.localizedDescription)"
        }
    }

    func save(action: String, recipients: [String]) async {
        saving = true; defer { saving = false }
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/alerts/recipients")
            var req = URLRequest(url: url)
            req.httpMethod = "PUT"
            req.timeoutInterval = APIConfig.connectTimeout
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.httpBody = try JSONSerialization.data(withJSONObject: [
                "action": action, "recipients": recipients,
            ])
            _ = try await URLSession.shared.data(for: req)
            await load()
        } catch {
            self.error = "保存失败：\(error.localizedDescription)"
        }
    }

    func loadPreview(action: String) async {
        do {
            var comps = URLComponents(url: APIConfig.baseURL.appendingPathComponent(
                "admin/alerts/preview"), resolvingAgainstBaseURL: false)!
            comps.queryItems = [URLQueryItem(name: "action", value: action)]
            var req = URLRequest(url: comps.url!)
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            self.preview = try JSONDecoder().decode(AlertPreviewDTO.self, from: data)
        } catch {
            self.error = "预览失败：\(error.localizedDescription)"
        }
    }

    func sendTest() async {
        do {
            let url = APIConfig.baseURL.appendingPathComponent(
                "admin/alerts/test?action=test")
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            struct R: Decodable { let sent: Int }
            self.lastTestSent = try JSONDecoder().decode(R.self, from: data).sent
        } catch {
            self.error = "测试发送失败：\(error.localizedDescription)"
        }
    }
}

struct AdminAlertRecipientsView: View {
    @StateObject private var model = AlertRecipientsModel()
    @State private var editingAction: String?
    @State private var editorText = ""
    @State private var previewAction: String?

    private func severityHint(_ key: String) -> String {
        switch key {
        case "severity.critical":
            return "安全事故/资金损失，建议接 PagerDuty / 值班手机邮箱"
        case "severity.trend":
            return "关键词突起等产品分析信号，建议接增长群机器人"
        default:
            return ""
        }
    }

    var body: some View {
        Form {
            if let err = model.error {
                Section { Text(err).foregroundStyle(.red).font(.footnote) }
            }

            Section("总开关") {
                Toggle("启用邮件告警", isOn: Binding(
                    get: { model.enabled },
                    set: { newVal in
                        Task {
                            // Toggle through runtime_settings via the
                            // existing PUT path.
                            let url = APIConfig.baseURL.appendingPathComponent(
                                "admin/runtime_settings")
                            var req = URLRequest(url: url)
                            req.httpMethod = "PUT"
                            req.setValue("application/json",
                                          forHTTPHeaderField: "Content-Type")
                            let token = try await AuthManager.shared.accessToken()
                            req.setValue("Bearer \(token)",
                                          forHTTPHeaderField: "Authorization")
                            req.httpBody = try JSONSerialization.data(
                                withJSONObject: [
                                    "key": "alert.enabled",
                                    "value": newVal ? "true" : "false",
                                ])
                            _ = try await URLSession.shared.data(for: req)
                            await model.load()
                        }
                    }
                ))
                LabeledContent("默认冷却",
                                 value: "\(model.defaultCooldown)s（同事件最短间隔）")
                Button {
                    Task { await model.sendTest() }
                } label: {
                    HStack {
                        Image(systemName: "paperplane")
                        Text("发送测试邮件到「test」配置的收件人")
                    }
                }
                if let n = model.lastTestSent {
                    Text("已发送 \(n) 封")
                        .font(.footnote)
                        .foregroundStyle(n > 0 ? .green : .orange)
                }
            }

            // v17k — coverage hint. The whole point of severity tiers
            // is to keep paging-grade alerts (critical) out of the same
            // inbox as analytics-grade signals (trend). If admin only
            // configured `default`, that separation is lost — surface
            // it loudly, not buried in the docs.
            let configured = Set(model.items.map(\.action))
            let hasDefault = configured.contains("default")
            let missing = ["severity.critical", "severity.trend"]
                .filter { !configured.contains($0) && !configured
                    .contains(where: { c in c.hasPrefix($0.prefix(8)) }) }
            if hasDefault && !missing.isEmpty {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.triangle.fill")
                            Text("建议为关键告警配独立收件人")
                                .font(.subheadline.bold())
                        }
                        Text("当前所有事件都会落到默认收件人。建议至少为以下两类各配一个独立收件人，避免重要告警被淹没：")
                            .font(.footnote)
                        ForEach(missing, id: \.self) { key in
                            Text("• \(key)：\(severityHint(key))")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }
                .listRowBackground(Color.orange.opacity(0.10))
            }

            Section("当前已配置") {
                if model.items.isEmpty {
                    Text(model.loading ? "加载中…" : "暂无任何收件人配置")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(model.items) { it in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(it.action)
                                .font(.system(.subheadline, design: .monospaced))
                            ForEach(it.recipients, id: \.self) { e in
                                Text("• \(e)")
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.blue)
                            }
                        }
                        .swipeActions {
                            Button(role: .destructive) {
                                Task { await model.save(action: it.action,
                                                          recipients: []) }
                            } label: {
                                Label("删除", systemImage: "trash")
                            }
                            Button {
                                editingAction = it.action
                                editorText = it.recipients.joined(separator: "\n")
                            } label: {
                                Label("编辑", systemImage: "pencil")
                            }.tint(.blue)
                        }
                    }
                }
            }

            Section("常用事件速选（点击设置 / 长按预览）") {
                ForEach(kSuggestedActions, id: \.0) { action, label in
                    Button {
                        editingAction = action
                        editorText = (model.items.first { $0.action == action }?
                                       .recipients.joined(separator: "\n")) ?? ""
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(label).foregroundStyle(.primary)
                            Text("alert.recipients.\(action)")
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .contextMenu {
                        Button {
                            previewAction = action
                            Task { await model.loadPreview(action: action) }
                        } label: {
                            Label("预览这封邮件", systemImage: "eye")
                        }
                    }
                }
            }
        }
        .sheet(item: Binding(
            get: { previewAction.map { ActionWrap(name: $0) } },
            set: { previewAction = $0?.name }
        )) { _ in
            NavigationStack {
                ScrollView {
                    if let p = model.preview {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Subject").font(.caption).foregroundStyle(.secondary)
                            Text(p.subject).font(.headline)
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.thinMaterial,
                                              in: RoundedRectangle(cornerRadius: 8))
                            Text("Body").font(.caption).foregroundStyle(.secondary)
                            Text(p.body).font(.system(.body, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10)
                                .background(.thinMaterial,
                                              in: RoundedRectangle(cornerRadius: 8))
                            if !p.would_send_to.isEmpty {
                                Text("将发送到").font(.caption).foregroundStyle(.secondary)
                                ForEach(p.would_send_to, id: \.self) { e in
                                    Text("• \(e)")
                                        .font(.caption.monospaced())
                                        .foregroundStyle(.blue)
                                }
                            } else {
                                Text("⚠️ 该事件未配置接收人，将走 default 兜底。")
                                    .font(.caption).foregroundStyle(.orange)
                            }
                        }.padding()
                    } else {
                        ProgressView().padding(40)
                    }
                }
                .navigationTitle("邮件预览")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("关闭") { previewAction = nil }
                    }
                }
            }
        }
        .navigationTitle("告警收件人")
        .task { await model.load() }
        .refreshable { await model.load() }
        .sheet(item: Binding(
            get: { editingAction.map { ActionWrap(name: $0) } },
            set: { editingAction = $0?.name }
        )) { wrap in
            NavigationStack {
                Form {
                    Section("收件人邮箱（一行一个）") {
                        TextEditor(text: $editorText)
                            .frame(minHeight: 200)
                            .font(.system(.body, design: .monospaced))
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    Section("支持的格式") {
                        Text("• 邮箱：name@example.com")
                            .font(.caption.monospaced())
                        Text("• 飞书：lark://https://open.feishu.cn/open-apis/bot/v2/hook/<token>")
                            .font(.caption.monospaced())
                        Text("• 钉钉：dingtalk://https://oapi.dingtalk.com/robot/send?access_token=<t>")
                            .font(.caption.monospaced())
                        Text("• 通用 webhook：webhook://https://your-svc/api/alert")
                            .font(.caption.monospaced())
                    }
                    Section {
                        Text("事件：\(wrap.name)").font(.footnote)
                        Text("空 = 删除该事件的接收人配置（回退到 default）。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                .navigationTitle("配置接收人")
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("取消") { editingAction = nil }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("保存") {
                            let lines = editorText
                                .split(whereSeparator: \.isNewline)
                                .map { $0.trimmingCharacters(in: .whitespaces) }
                                .filter { !$0.isEmpty }
                            Task {
                                await model.save(action: wrap.name,
                                                   recipients: lines)
                                editingAction = nil
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct ActionWrap: Identifiable { let name: String; var id: String { name } }
