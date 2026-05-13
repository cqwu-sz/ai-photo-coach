// AdminRuntimeSettingsView.swift  (v17d)
//
// On-device cockpit for the runtime_settings KV — same plumbing
// as APP_ATTEST flags / OTP RPM caps. Lets an admin tune limits
// during an incident from the iPhone instead of SSHing into prod.
//
// We keep the UI deliberately unsafe-friendly: free-form key + value
// because the back-end already validates schema and `rs_svc.set_value`
// rejects malformed input. We surface 12 well-known keys as a
// shortcut so you don't have to remember the exact names under fire.

import SwiftUI

private struct KnownKey: Identifiable {
    let id = UUID()
    let key: String
    let label: String
    let hint: String
}

private let kKnownKeys: [KnownKey] = [
    .init(key: "otp.daily_max_per_target", label: "OTP 每号每天上限",
          hint: "默认 8。事故时调到 3 可立刻收紧"),
    .init(key: "otp.daily_max_per_ip", label: "OTP 每 IP 每天上限",
          hint: "默认 30。NAT 大用户视情况放宽"),
    .init(key: "otp.global_rpm", label: "OTP 全局每分钟上限",
          hint: "默认 50。短信账单告警时调小"),
    .init(key: "http.ip_rpm", label: "HTTP 每 IP 每分钟",
          hint: "默认 120。压测前临时调高"),
    .init(key: "http.ip_rph", label: "HTTP 每 IP 每小时",
          hint: "默认 1500"),
    .init(key: "http.auth_rpm", label: "/auth/* 每 IP 每分钟",
          hint: "默认 20。爆破苗头出现就降到 5"),
    // v18 s2 — satisfaction system runtime knobs.
    .init(key: "pref.personal_cooldown_sec",
          label: "个人偏好冷却（秒）",
          hint: "默认 604800（7 天）。同 (user, scene, style) 在窗口内只计 1 次"),
    .init(key: "pref.global_hint.enabled",
          label: "群体偏好 hint 总开关",
          hint: "true / false。默认 false。优先在「满意度」页用一键关"),
    .init(key: "pref.global_hint.min_distinct_users",
          label: "群体 hint · 最小独立用户数",
          hint: "默认 30"),
    .init(key: "pref.global_hint.min_satisfaction_rate",
          label: "群体 hint · 最低满意率",
          hint: "默认 0.6（60%）"),
    .init(key: "pref.global_hint.cooldown_sec",
          label: "群体 hint · 缓存 TTL（秒）",
          hint: "默认 300"),
]

@MainActor
private final class RuntimeSettingsModel: ObservableObject {
    struct Item: Identifiable, Decodable {
        var id: String { key }
        let key: String
        let value: String
        let updated_by: String?
        let updated_at: String?
    }

    @Published var items: [Item] = []
    @Published var loading = false
    @Published var saving = false
    @Published var error: String?
    @Published var draftKey = ""
    @Published var draftValue = ""

    func load() async {
        loading = true; defer { loading = false }
        do {
            let url = APIConfig.baseURL
                .appendingPathComponent("admin/runtime_settings")
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            struct Wrap: Decodable { let items: [Item] }
            items = try JSONDecoder().decode(Wrap.self, from: data).items
            error = nil
        } catch {
            self.error = "加载失败：\(error.localizedDescription)"
        }
    }

    func save() async {
        let key = draftKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let value = draftValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty, !value.isEmpty else { return }
        saving = true; defer { saving = false }
        do {
            let url = APIConfig.baseURL
                .appendingPathComponent("admin/runtime_settings")
            var req = URLRequest(url: url)
            req.httpMethod = "PUT"
            req.timeoutInterval = APIConfig.connectTimeout
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.httpBody = try JSONSerialization.data(
                withJSONObject: ["key": key, "value": value])
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse,
               !(200...299).contains(http.statusCode) {
                throw NSError(domain: "runtime", code: http.statusCode,
                              userInfo: [NSLocalizedDescriptionKey:
                                          "HTTP \(http.statusCode)"])
            }
            draftKey = ""; draftValue = ""
            await load()
        } catch {
            self.error = "保存失败：\(error.localizedDescription)"
        }
    }
}

struct AdminRuntimeSettingsView: View {
    @StateObject private var model = RuntimeSettingsModel()
    @State private var showHelp = false

    var body: some View {
        Form {
            if let err = model.error {
                Section {
                    Text(err).foregroundColor(.red).font(.footnote)
                }
            }

            Section("当前已设值") {
                if model.items.isEmpty {
                    Text(model.loading ? "加载中…" : "暂无（全部走代码默认值）")
                        .foregroundColor(.secondary).font(.footnote)
                } else {
                    ForEach(model.items) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(item.key).font(.system(.body, design: .monospaced))
                                Spacer()
                                Text(item.value)
                                    .font(.system(.body, design: .monospaced))
                                    .foregroundColor(.blue)
                            }
                            if let by = item.updated_by, let at = item.updated_at {
                                Text("\(by) · \(at)")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .swipeActions {
                            Button {
                                model.draftKey = item.key
                                model.draftValue = item.value
                            } label: { Label("编辑", systemImage: "pencil") }
                        }
                    }
                }
            }

            Section("常用 key 速选") {
                ForEach(kKnownKeys) { k in
                    Button {
                        model.draftKey = k.key
                        if let cur = model.items.first(where: { $0.key == k.key }) {
                            model.draftValue = cur.value
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(k.label).foregroundColor(.primary)
                            Text(k.key)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundColor(.secondary)
                            Text(k.hint).font(.caption2).foregroundColor(.secondary)
                        }
                    }
                }
            }

            Section("写入新值") {
                TextField("key (e.g. otp.global_rpm)", text: $model.draftKey)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.system(.body, design: .monospaced))
                TextField("value", text: $model.draftValue)
                    .keyboardType(.numbersAndPunctuation)
                    .font(.system(.body, design: .monospaced))
                Button {
                    Task { await model.save() }
                } label: {
                    if model.saving { ProgressView() }
                    else { Text("写入并立即生效（30s 内全节点同步）") }
                }
                .disabled(model.saving
                          || model.draftKey.isEmpty
                          || model.draftValue.isEmpty)
            }

            Section {
                Button {
                    showHelp = true
                } label: {
                    Label("使用提示", systemImage: "questionmark.circle")
                }
            }
        }
        .navigationTitle("运行时阈值")
        .task { await model.load() }
        .refreshable { await model.load() }
        .sheet(isPresented: $showHelp) {
            NavigationStack {
                ScrollView {
                    Text("""
这些值优先级最高：runtime_settings → 代码默认。
写入后 30 秒内全节点生效（in-process 缓存 TTL）。
事故时常用动作：
  • 短信被刷：otp.global_rpm = 10
  • 暴力登录：http.auth_rpm = 5
  • 单 IP 滥用：http.ip_rpm = 30
事后记得删回（写入相同 key 但值设回默认即可，
后端会以 runtime 为准；要彻底回退请联系研发清表）。
"""
                    )
                    .padding()
                    .font(.callout)
                }
                .navigationTitle("使用提示")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("关闭") { showHelp = false }
                    }
                }
            }
        }
    }
}
