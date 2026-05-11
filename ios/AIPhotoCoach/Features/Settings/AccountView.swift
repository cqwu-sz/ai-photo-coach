// AccountView.swift  (A0-12 of MULTI_USER_AUTH)
//
// Account management screen. Three responsibilities:
//   - Show the current account state (匿名 / 已用 Apple 登录 / Pro 状态)
//   - Sign in with Apple button (Apple 4.5.4 — required when we offer
//     any third-party login or account system)
//   - Delete account button (Apple 5.1.1(v) — must wipe everything)
//
// Wire it from your Settings sheet:
//      NavigationLink("账户与订阅") { AccountView() }

import SwiftUI

struct AccountView: View {
    @StateObject private var auth = AuthManager.shared
    @StateObject private var iap = IAPManager.shared

    @State private var busy: Bool = false
    @State private var error: String?
    @State private var showingDeleteConfirm: Bool = false
    @State private var showingError: Bool = false
    @State private var resolvedPrivacyURL: String?
    @State private var resolvedEulaURL: String?

    var body: some View {
        Form {
            Section("账户") {
                LabeledContent("用户 ID", value: shortened(auth.userId ?? "—"))
                LabeledContent("登录方式", value: auth.isAnonymous ? "匿名（仅本机）" : "Sign in with Apple")
                LabeledContent("当前等级") {
                    Text(iap.entitlement.isPro ? "Pro" : "Free")
                        .foregroundStyle(iap.entitlement.isPro ? .orange : .secondary)
                        .fontWeight(iap.entitlement.isPro ? .semibold : .regular)
                }
                if let exp = iap.entitlement.expiresAt {
                    LabeledContent("订阅到期", value: exp.formatted(date: .abbreviated, time: .shortened))
                }
            }

            if auth.isAnonymous {
                Section {
                    Button {
                        Task { await runAsync { try await auth.signInWithApple() } }
                    } label: {
                        HStack {
                            Image(systemName: "applelogo")
                            Text("使用 Apple ID 登录")
                            Spacer()
                            if busy { ProgressView() }
                        }
                    }
                    .disabled(busy)
                } footer: {
                    Text("登录后你的机位收藏、订阅状态可在多设备间同步。我们仅保存 Apple 提供的匿名 sub 标识，不读取邮箱/姓名以外的任何信息。")
                }
            } else {
                Section {
                    Button("退出登录") {
                        Task { await runAsync { await auth.signOut() } }
                    }
                    .disabled(busy)
                }
            }

            Section("订阅") {
                Button {
                    Task { await runAsync { try await iap.purchasePro() } }
                } label: {
                    Label(iap.entitlement.isPro ? "管理订阅" : "升级 Pro",
                          systemImage: "star.circle")
                }
                Button("恢复购买") {
                    Task { await runAsync { await iap.restore() } }
                }
                if let url = URL(string: "https://apps.apple.com/account/subscriptions") {
                    Link("在 App Store 管理订阅", destination: url)
                }
            }

            Section("法律") {
                if let p = URL(string: privacyPolicyURL) {
                    Link("隐私政策", destination: p)
                }
                if let e = URL(string: eulaURL) {
                    Link("用户协议（EULA）", destination: e)
                }
            }

            Section {
                Button(role: .destructive) {
                    showingDeleteConfirm = true
                } label: {
                    Label("删除账户与所有数据", systemImage: "trash")
                }
            } footer: {
                Text("删除后我们会立即清除你的反馈数据、3D 重建任务、订阅记录。Apple 端的订阅请额外在 “在 App Store 管理订阅” 中取消，避免继续扣费。")
            }
        }
        .navigationTitle("账户与订阅")
        .navigationBarTitleDisplayMode(.inline)
        .alert("确定删除账户？", isPresented: $showingDeleteConfirm) {
            Button("删除", role: .destructive) {
                Task { await runAsync { try await auth.deleteAccount() } }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("此操作不可恢复。本机匿名身份会被重新创建以便继续使用。")
        }
        .alert("操作失败", isPresented: $showingError, presenting: error) { _ in
            Button("好") { error = nil }
        } message: { msg in
            Text(msg)
        }
        .task {
            await iap.refreshEntitlement(force: true)
            await fetchLegalURLs()
        }
    }

    private func fetchLegalURLs() async {
        // /healthz publishes the operator-configured policy URLs so we
        // don't have to ship a release just to point at a new domain.
        var req = URLRequest(url: APIConfig.baseURL.appendingPathComponent("healthz"))
        req.timeoutInterval = APIConfig.connectTimeout
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            if let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                resolvedPrivacyURL = obj["privacy_policy_url"] as? String
                resolvedEulaURL = obj["eula_url"] as? String
            }
        } catch { /* fall through to defaults */ }
    }

    private func shortened(_ s: String) -> String {
        guard s.count > 8 else { return s }
        return String(s.prefix(8)) + "…"
    }

    private func runAsync(_ work: @escaping () async throws -> Void) async {
        busy = true
        defer { busy = false }
        do {
            try await work()
        } catch {
            self.error = (error as NSError).localizedDescription
            self.showingError = true
        }
    }

    private func runAsync(_ work: @escaping () async -> Void) async {
        busy = true
        defer { busy = false }
        await work()
    }

    /// Operator-configured URL when /healthz returns one; otherwise we
    /// fall back to the bundled web page (same origin as API) so first
    /// submission still has a valid privacy policy link.
    private var privacyPolicyURL: String {
        if let s = resolvedPrivacyURL, !s.isEmpty { return absolutize(s) }
        return APIConfig.baseURL.appendingPathComponent("web/privacy.html").absoluteString
    }
    private var eulaURL: String {
        if let s = resolvedEulaURL, !s.isEmpty { return absolutize(s) }
        return "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/"
    }

    private func absolutize(_ s: String) -> String {
        if s.hasPrefix("http://") || s.hasPrefix("https://") { return s }
        return APIConfig.baseURL.appendingPathComponent(
            String(s.drop(while: { $0 == "/" }))
        ).absoluteString
    }
}
