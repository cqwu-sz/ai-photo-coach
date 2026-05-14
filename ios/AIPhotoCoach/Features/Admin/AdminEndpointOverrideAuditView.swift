// AdminEndpointOverrideAuditView.swift
//
// 列出 Internal 包用户的本机 baseURL 覆盖事件，供客服 / SRE 排查
// "为啥这个设备连不上"。后端表 endpoint_override_audit 由
// POST /api/telemetry/endpoint_override 写入，admin 通过
// GET /admin/endpoint/override_audit 查询。
//
// 该视图本身在正式包里也能看到（admin 角色专属，由 AdminDashboardView 路由
// 入口控制），但数据来源是 Internal 包用户上报 — 正式包不写这张表。

import SwiftUI

private struct OverrideAuditItem: Decodable, Identifiable {
    let id: Int
    let device_fp: String?
    let old_url: String?
    let new_url: String?
    let healthz_ok: Bool
    let source: String
    let app_version: String?
    let reported_at: String
}

private struct OverrideAuditDTO: Decodable {
    let window_hours: Int
    let total_events: Int
    let distinct_devices: Int
    let items: [OverrideAuditItem]
}

@MainActor
private final class OverrideAuditModel: ObservableObject {
    @Published var items: [OverrideAuditItem] = []
    @Published var totalEvents: Int = 0
    @Published var distinctDevices: Int = 0
    @Published var hours: Int = 24
    @Published var deviceFilter: String = ""
    @Published var loading: Bool = false
    @Published var error: String?

    func reload() async {
        loading = true; defer { loading = false }
        do {
            guard let base = APIConfig.resolvedBaseURL() else {
                error = "服务器未配置"
                return
            }
            var comps = URLComponents(
                url: base.appendingPathComponent("admin/endpoint/override_audit"),
                resolvingAgainstBaseURL: false,
            )!
            var qs = [URLQueryItem(name: "hours", value: String(hours))]
            let fp = deviceFilter.trimmingCharacters(in: .whitespaces)
            if !fp.isEmpty { qs.append(URLQueryItem(name: "device_fp", value: fp)) }
            comps.queryItems = qs

            var req = URLRequest(url: comps.url!)
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let dto = try JSONDecoder().decode(OverrideAuditDTO.self, from: data)
            items = dto.items
            totalEvents = dto.total_events
            distinctDevices = dto.distinct_devices
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct AdminEndpointOverrideAuditView: View {
    @StateObject private var model = OverrideAuditModel()
    @State private var exportURL: URL?
    @State private var showShare: Bool = false
    @State private var exporting: Bool = false

    var body: some View {
        List {
            Section("过滤") {
                Picker("时间窗口", selection: $model.hours) {
                    Text("1h").tag(1)
                    Text("6h").tag(6)
                    Text("24h").tag(24)
                    Text("7d").tag(24 * 7)
                    Text("30d").tag(24 * 30)
                }
                .pickerStyle(.segmented)
                .onChange(of: model.hours) { _, _ in Task { await model.reload() } }

                HStack {
                    TextField("device_fp (可选, 完整 sha256)", text: $model.deviceFilter)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.footnote.monospaced())
                    if !model.deviceFilter.isEmpty {
                        Button {
                            model.deviceFilter = ""
                            Task { await model.reload() }
                        } label: { Image(systemName: "xmark.circle.fill") }
                            .foregroundStyle(.secondary)
                    }
                }
                Button("查询") { Task { await model.reload() } }
                    .disabled(model.loading)
            }

            Section("汇总（窗口内）") {
                LabeledContent("事件数", value: "\(model.totalEvents)")
                LabeledContent("独立设备", value: "\(model.distinctDevices)")
            }

            if let err = model.error {
                Section { Text(err).foregroundStyle(.red).font(.footnote) }
            }

            Section("事件列表（最多 100 条）") {
                if model.items.isEmpty && !model.loading {
                    Text("窗口内无事件").foregroundStyle(.secondary).font(.footnote)
                }
                ForEach(model.items) { item in
                    auditRow(item)
                }
            }
        }
        .navigationTitle("本机覆盖审计")
        .task { await model.reload() }
        .refreshable { await model.reload() }
        .overlay {
            if model.loading {
                ProgressView().controlSize(.large)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await exportCSV() }
                } label: {
                    if exporting {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "square.and.arrow.up")
                    }
                }
                .disabled(exporting || model.items.isEmpty)
                .accessibilityLabel("导出 CSV")
            }
        }
        .sheet(isPresented: $showShare) {
            if let url = exportURL {
                ShareSheet(items: [url])
            }
        }
    }

    private func exportCSV() async {
        guard !exporting else { return }
        exporting = true; defer { exporting = false }
        do {
            guard let base = APIConfig.resolvedBaseURL() else { return }
            var comps = URLComponents(
                url: base.appendingPathComponent("admin/endpoint/override_audit"),
                resolvingAgainstBaseURL: false,
            )!
            var qs = [
                URLQueryItem(name: "hours", value: String(model.hours)),
                URLQueryItem(name: "format", value: "csv"),
                URLQueryItem(name: "limit", value: "500"),
            ]
            let fp = model.deviceFilter.trimmingCharacters(in: .whitespaces)
            if !fp.isEmpty { qs.append(URLQueryItem(name: "device_fp", value: fp)) }
            comps.queryItems = qs

            var req = URLRequest(url: comps.url!)
            let token = try await AuthManager.shared.accessToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let fname = "endpoint_override_audit_\(model.hours)h.csv"
            let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(fname)
            try data.write(to: tmp, options: .atomic)
            exportURL = tmp
            showShare = true
        } catch {
            model.error = "导出失败：\(error.localizedDescription)"
        }
    }

    @ViewBuilder
    private func auditRow(_ item: OverrideAuditItem) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(item.reported_at)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                Spacer()
                if item.healthz_ok {
                    Label("healthz ok", systemImage: "checkmark.seal.fill")
                        .labelStyle(.iconOnly)
                        .foregroundStyle(.green)
                } else if item.new_url != nil {
                    Label("untested", systemImage: "questionmark.circle.fill")
                        .labelStyle(.iconOnly)
                        .foregroundStyle(.orange)
                }
            }
            if let fp = item.device_fp {
                HStack(spacing: 4) {
                    Text("device: \(fp.prefix(16))…")
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                    Button {
                        UIPasteboard.general.string = fp
                        // Tactile confirmation so admin knows the full
                        // sha256 (not just the truncated prefix) landed
                        // on the pasteboard.
                        let gen = UINotificationFeedbackGenerator()
                        gen.notificationOccurred(.success)
                    } label: {
                        Image(systemName: "doc.on.doc")
                            .font(.caption2)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.tint)
                    .accessibilityLabel("复制 device fingerprint")
                    Spacer()
                    Button("仅看此设备") {
                        model.deviceFilter = fp
                        Task { await model.reload() }
                    }
                    .font(.caption2)
                    .buttonStyle(.plain)
                    .foregroundStyle(.tint)
                }
            }
            if let from = item.old_url {
                Text("← \(from)").font(.footnote.monospaced())
                    .foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
            if let to = item.new_url {
                Text("→ \(to)").font(.footnote.monospaced()).lineLimit(1).truncationMode(.middle)
            } else {
                Text("→ (清除覆盖)").font(.footnote).foregroundStyle(.secondary)
            }
            if let v = item.app_version {
                Text("v\(v) · \(item.source)").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}

#Preview {
    NavigationStack { AdminEndpointOverrideAuditView() }
}
