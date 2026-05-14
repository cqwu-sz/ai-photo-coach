// PrivacyDisclosureView.swift  (PR10 of subscription/auth rework)
//
// Required by 个人信息保护法 + Apple 5.1.1: a single screen that tells
// the user EXACTLY what leaves their device and what doesn't, plus
// the export & delete actions.

import SwiftUI

struct PrivacyDisclosureView: View {
    @State private var exporting: Bool = false
    @State private var exportError: String?
    @State private var exportedURL: URL?
    @State private var showShare: Bool = false
    @State private var showDeleteConfirm: Bool = false
    @State private var deleting: Bool = false
    @State private var showResetPrefsConfirm: Bool = false
    @State private var resettingPrefs: Bool = false
    @State private var resetPrefsToast: String?

    // body is intentionally tiny: each Section is a separate computed
    // property and all alerts/sheets are folded into one modifier chain.
    // SwiftUI's type-checker is exponential on Form/Section depth — the
    // previous monolithic body tripped "unable to type-check in
    // reasonable time" on CI. Keep this thin.
    var body: some View {
        Form {
            uploadedSection
            notUploadedSection
            anonymousAggregateSection
            satisfactionSection
            rightsSection
            footerSection
        }
        .navigationTitle("数据与隐私")
        .modifier(PrivacyDialogs(
            exportError: $exportError,
            showShare: $showShare,
            exportedURL: exportedURL,
            showDeleteConfirm: $showDeleteConfirm,
            showResetPrefsConfirm: $showResetPrefsConfirm,
            resetPrefsToast: $resetPrefsToast,
            onDelete: { await runDelete() },
            onResetPrefs: { await resetPreferences() }
        ))
    }

    // MARK: - Sections

    @ViewBuilder
    private var uploadedSection: some View {
        Section("会上传到云端的内容") {
            row("登录凭证", "手机号或邮箱（用于发送验证码）",
                 icon: "person.crop.circle.badge.checkmark")
            row("订阅状态", "Apple 订阅 ID 与到期时间（用于配额）",
                 icon: "creditcard")
            row("拍摄配置", "场景 / 人数 / 风格关键词 / 质量档（不含原图）",
                 icon: "slider.horizontal.3")
            row("分析返回", "AI 给出的拍摄方案文本（不含图像）",
                 icon: "text.alignleft")
        }
    }

    @ViewBuilder
    private var notUploadedSection: some View {
        Section("绝不上传的内容") {
            row("原始照片像素", "全部留在你的相册与本机沙盒",
                 icon: "photo.on.rectangle.angled", color: .green)
            row("原始视频帧", "环境扫描的视频流仅在本机解析",
                 icon: "video.slash", color: .green)
            row("精确位置", "经纬度只取小数点后两位用于太阳计算",
                 icon: "location.slash", color: .green)
            row("OTP 明文", "服务端只保留 HMAC 哈希，5 分钟后失效",
                 icon: "lock.shield", color: .green)
        }
    }

    @ViewBuilder
    private var anonymousAggregateSection: some View {
        Section("用于改进 App 的匿名聚合") {
            row("非个人化使用统计",
                 "选择的拍摄场景 / 画质偏好 / 风格关键词 / 采纳的方案，"
                 + "我们会做聚合统计来改进 App。聚合结果不含账号 ID，"
                 + "且每个分组至少 5 个独立用户才显示，单人偏好不可被反推。",
                 icon: "chart.bar.doc.horizontal", color: .blue)
            row("删除即清",
                 "删除账号时，相关原始记录立刻随账号一并清理，聚合统计也"
                 + "自动失去您的贡献。",
                 icon: "trash.slash", color: .green)
        }
    }

    @ViewBuilder
    private var satisfactionSection: some View {
        Section("满意度反馈（可选）") {
            row("拍完点拇指",
                 "拍完一张后，App 会询问「这次满意吗？」。回答完全可选，"
                 + "跳过不影响任何功能。回答仅是 1 bit + 可选 200 字备注，"
                 + "永远不会上传任何照片。",
                 icon: "hand.thumbsup", color: .orange)
            row("仅个人化",
                 "默认情况下，您的回答只用来调整未来给您本人生成的方案。"
                 + "全局聚合（≥30 位独立用户的趋势）默认关闭，仅在管理员"
                 + "显式启用时才会作为弱建议影响其他用户。",
                 icon: "person.crop.circle", color: .blue)
        }
    }

    @ViewBuilder
    private var rightsSection: some View {
        Section("你的权利") {
            Button {
                Task { await exportData() }
            } label: {
                HStack {
                    Label("下载我的所有数据 (JSON)", systemImage: "square.and.arrow.down")
                    Spacer()
                    if exporting { ProgressView() }
                }
            }
            .disabled(exporting)

            Link(destination: BrandConstants.privacyURL) {
                Label("阅读完整隐私政策", systemImage: "doc.text")
            }

            Button(role: .destructive) {
                showResetPrefsConfirm = true
            } label: {
                HStack {
                    Label("清空我的风格偏好快照",
                            systemImage: "arrow.uturn.backward")
                    Spacer()
                    if resettingPrefs { ProgressView() }
                }
            }
            .disabled(resettingPrefs)

            Button(role: .destructive) {
                showDeleteConfirm = true
            } label: {
                HStack {
                    Label("删除我的账号与全部数据", systemImage: "trash")
                    Spacer()
                    if deleting { ProgressView() }
                }
            }
            .disabled(deleting)
        }
    }

    @ViewBuilder
    private var footerSection: some View {
        Section {
            Text("我们遵循《中华人民共和国个人信息保护法》与 Apple App Store Review Guidelines 5.1.1 — 5.1.2，对最小必要原则负责。如对数据处理有疑问，请通过 \(BrandConstants.privacyContactEmail) 联系。")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func resetPreferences() async {
        resettingPrefs = true; defer { resettingPrefs = false }
        do {
            var req = URLRequest(url: APIConfig.baseURL
                .appendingPathComponent("me/preferences"))
            req.httpMethod = "DELETE"
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode >= 400 {
                exportError = "清空失败：HTTP \(http.statusCode)"
                return
            }
            resetPrefsToast = "未来的方案不再带个人偏好倾向。"
        } catch {
            exportError = (error as NSError).localizedDescription
        }
    }

    private func runDelete() async {
        deleting = true
        defer { deleting = false }
        do {
            try await AuthManager.shared.deleteAccount()
        } catch {
            exportError = (error as NSError).localizedDescription
        }
    }

    private func row(_ title: String, _ detail: String,
                       icon: String, color: Color = .accentColor) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(color)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.bold())
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func exportData() async {
        exporting = true
        defer { exporting = false }
        do {
            var req = URLRequest(url: APIConfig.baseURL
                .appendingPathComponent("me/data/export"))
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let dir = FileManager.default.temporaryDirectory
            let url = dir.appendingPathComponent(
                "ai-photo-coach-export-\(Int(Date().timeIntervalSince1970)).json")
            try data.write(to: url)
            exportedURL = url
            showShare = true
        } catch {
            exportError = (error as NSError).localizedDescription
        }
    }
}

// ShareSheet now lives in Common/ShareSheet.swift so cross-feature
// callers (e.g. AdminInsightsView) can use it without importing this file.

/// Folds all alerts / sheets / confirmation dialogs of
/// PrivacyDisclosureView into a single modifier so the parent body
/// stays under SwiftUI's type-checker complexity budget.
private struct PrivacyDialogs: ViewModifier {
    @Binding var exportError: String?
    @Binding var showShare: Bool
    let exportedURL: URL?
    @Binding var showDeleteConfirm: Bool
    @Binding var showResetPrefsConfirm: Bool
    @Binding var resetPrefsToast: String?
    let onDelete: () async -> Void
    let onResetPrefs: () async -> Void

    func body(content: Content) -> some View {
        content
            .alert("导出失败", isPresented: Binding(
                get: { exportError != nil },
                set: { if !$0 { exportError = nil } }
            )) {
                Button("好") { exportError = nil }
            } message: {
                Text(exportError ?? "")
            }
            .sheet(isPresented: $showShare) {
                if let url = exportedURL {
                    ShareSheet(items: [url])
                }
            }
            .alert("确认删除账号?", isPresented: $showDeleteConfirm) {
                Button("取消", role: .cancel) { }
                Button("永久删除", role: .destructive) {
                    Task { await onDelete() }
                }
            } message: {
                Text("这将立即软删除你的账号并在 24 小时内硬删全部数据。订阅在 Apple 端不会自动取消，请前往「设置 → Apple ID → 订阅」自行关闭。")
            }
            .confirmationDialog("清空风格偏好?", isPresented: $showResetPrefsConfirm,
                                  titleVisibility: .visible) {
                Button("立即清空", role: .destructive) {
                    Task { await onResetPrefs() }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("会删除「我历史上点过满意的风格」记录，未来出片方案不再倾向这些风格。账号、订阅、使用记录都不受影响；可以再次拍摄重新建立偏好。")
            }
            .alert("已清空", isPresented: Binding(
                get: { resetPrefsToast != nil },
                set: { if !$0 { resetPrefsToast = nil } }
            )) {
                Button("好") { resetPrefsToast = nil }
            } message: {
                Text(resetPrefsToast ?? "")
            }
    }
}

#Preview {
    NavigationStack { PrivacyDisclosureView() }
}
