import SwiftUI

/// Settings sheet for picking a vision model + supplying a BYOK API key.
///
/// The sheet pulls the registry from GET /models on first appear; user
/// selection is persisted via `ModelConfigStore`. The "测试连通性" button
/// hits POST /models/test so users can validate their key before they
/// burn tokens on a real analysis.
struct ModelSettingsView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var modelId: String = ModelConfigStore.modelId()
    @State private var apiKey: String = ModelConfigStore.apiKey()
    @State private var baseUrl: String = ModelConfigStore.baseUrl()

    @State private var registry: ModelsResponse?
    @State private var loadError: String?
    @State private var isTesting: Bool = false
    @State private var testResult: TestResult?

    enum TestResult: Equatable {
        case ok(String)
        case failure(String)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NavigationLink {
                        AppIconPickerView()
                    } label: {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("外观与图标")
                                    .font(.system(size: 15, weight: .semibold))
                                Text("挑一张你想每天打开的「拾光」图")
                                    .font(.system(size: 12))
                                    .foregroundStyle(.secondary)
                            }
                        } icon: {
                            Image(systemName: "app.badge.fill")
                                .symbolRenderingMode(.hierarchical)
                                .foregroundStyle(Color.orange)
                        }
                    }
                }

                Section {
                    Text("密钥仅保存在你的设备 Keychain，分析时随请求发到后端，绝不会被服务端持久化。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("视觉模型") {
                    if let reg = registry {
                        Picker("模型", selection: $modelId) {
                            Text("使用后端默认 (\(reg.defaultModelId))").tag("")
                            ForEach(groupedModels(reg.models), id: \.0) { vendor, items in
                                Section(vendorLabel(vendor)) {
                                    ForEach(items) { m in
                                        Text(m.displayName + (m.hasOperatorKey ? "" : " ⚠")).tag(m.id)
                                    }
                                }
                            }
                        }
                        .pickerStyle(.menu)
                    } else if let err = loadError {
                        Text("加载失败：\(err)").foregroundStyle(.red)
                    } else {
                        ProgressView("加载模型列表…")
                    }
                }

                Section {
                    SecureField("留空使用后端 fallback key", text: $apiKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                } header: {
                    Text("API Key")
                } footer: {
                    if let hint = currentVendorHint {
                        Text(hint).font(.footnote)
                    }
                }

                Section {
                    TextField(currentBaseUrlPlaceholder, text: $baseUrl)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                        .keyboardType(.URL)
                } header: {
                    Text("自定义 Base URL")
                } footer: {
                    Text("仅自建代理时使用；留空会使用预设地址。")
                        .font(.footnote)
                }

                Section {
                    Button {
                        Task { await runTest() }
                    } label: {
                        if isTesting {
                            HStack { ProgressView(); Text("测试中…") }
                        } else {
                            Text("测试连通性")
                        }
                    }
                    .disabled(isTesting)
                    if let result = testResult {
                        switch result {
                        case .ok(let snippet):
                            Label(snippet.isEmpty ? "连通成功" : "连通成功 · \(snippet)", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        case .failure(let msg):
                            Label(msg, systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                        }
                    }
                }

                Section {
                    Button("清除本地保存的模型配置", role: .destructive) {
                        ModelConfigStore.clear()
                        modelId = ""
                        apiKey = ""
                        baseUrl = ""
                        testResult = nil
                    }
                }
            }
            .navigationTitle("模型与密钥")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        ModelConfigStore.setModelId(modelId)
                        ModelConfigStore.setBaseUrl(baseUrl)
                        ModelConfigStore.setApiKey(apiKey)
                        dismiss()
                    }
                }
            }
            .task { await loadRegistry() }
        }
    }

    // MARK: - Helpers

    private var currentVendorHint: String? {
        guard let m = registry?.models.first(where: { $0.id == modelId }) else {
            return "未选择具体模型时使用后端默认配置。"
        }
        var lines: [String] = ["Vendor: \(vendorLabel(m.vendor))"]
        if let url = vendorKeyUrl(m.vendor) {
            lines.append("到 \(url) 申请 API Key")
        }
        if m.hasOperatorKey {
            lines.append("（后端已配置该家的 fallback key，可留空）")
        } else if m.requiresKey {
            lines.append("（后端没有 fallback，必须填你自己的 key）")
        }
        return lines.joined(separator: " · ")
    }

    private var currentBaseUrlPlaceholder: String {
        registry?.models.first(where: { $0.id == modelId })?.baseUrl
            ?? "留空使用预设地址"
    }

    private func loadRegistry() async {
        do {
            let r = try await APIClient.shared.fetchModels()
            await MainActor.run {
                self.registry = r
                self.loadError = nil
            }
        } catch {
            await MainActor.run {
                self.loadError = error.localizedDescription
            }
        }
    }

    private func runTest() async {
        await MainActor.run {
            isTesting = true
            testResult = nil
        }
        defer {
            Task { @MainActor in isTesting = false }
        }
        do {
            let target = modelId.isEmpty
                ? (registry?.defaultModelId ?? "gemini-2.5-flash")
                : modelId
            let r = try await APIClient.shared.testModel(
                modelId: target,
                apiKey: apiKey.isEmpty ? nil : apiKey,
                baseUrl: baseUrl.isEmpty ? nil : baseUrl
            )
            await MainActor.run {
                testResult = r.ok
                    ? .ok(r.snippet ?? "")
                    : .failure(r.error ?? "未知错误")
            }
        } catch {
            await MainActor.run {
                testResult = .failure(error.localizedDescription)
            }
        }
    }

    private func groupedModels(_ models: [ModelPreset]) -> [(String, [ModelPreset])] {
        let order = ["google", "openai", "zhipu", "dashscope", "deepseek", "moonshot"]
        var byVendor: [String: [ModelPreset]] = [:]
        for m in models {
            byVendor[m.vendor, default: []].append(m)
        }
        return order.compactMap { v in
            if let items = byVendor[v], !items.isEmpty {
                return (v, items)
            }
            return nil
        }
    }

    private func vendorLabel(_ vendor: String) -> String {
        switch vendor {
        case "google": return "Google · Gemini"
        case "openai": return "OpenAI"
        case "zhipu": return "智谱 · GLM"
        case "dashscope": return "阿里 · 通义千问"
        case "deepseek": return "DeepSeek"
        case "moonshot": return "Moonshot · Kimi"
        default: return vendor
        }
    }

    private func vendorKeyUrl(_ vendor: String) -> String? {
        switch vendor {
        case "google": return "https://aistudio.google.com/app/apikey"
        case "openai": return "https://platform.openai.com/api-keys"
        case "zhipu": return "https://open.bigmodel.cn/usercenter/apikeys"
        case "dashscope": return "https://dashscope.console.aliyun.com/apiKey"
        case "deepseek": return "https://platform.deepseek.com/api_keys"
        case "moonshot": return "https://platform.moonshot.cn/console/api-keys"
        default: return nil
        }
    }
}
