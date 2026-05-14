// ServerEndpointPublicView.swift
//
// 仅 Internal 包包含。让用户在无需登录的前提下设置本机后端 URL，
// 解决"登录要后端、配后端要登录"的鸡生蛋问题。
//
// 安全模型：
//   - 整文件外层 #if INTERNAL_BUILD —— Production 包根本不会编译此文件，
//     连符号都不会出现在二进制里。配合 project.yml 的 postCompileScripts
//     做了一层兜底校验。
//   - 输入校验 (ServerEndpointStore.validate) 强制 https，仅在 host 是
//     loopback / RFC1918 / .local 时允许 http；拒绝云元数据 IP。
//   - 强制 /healthz 探测通过后才允许"应用"，避免用户填一个无效 URL 把
//     自己变成"我也连不上"。

#if INTERNAL_BUILD

import SwiftUI
import AVFoundation

struct ServerEndpointPublicView: View {
    @ObservedObject private var store = ServerEndpointStore.shared
    @State private var input: String = ""
    @State private var probing: Bool = false
    @State private var probeResult: ProbeResult = .untested
    @State private var validationError: String?
    @State private var showScanner: Bool = false
    @State private var scanToast: String?

    enum ProbeResult: Equatable {
        case untested
        case ok
        case failed(String)
    }

    var body: some View {
        Form {
            currentSection
            inputSection
            historySection
            helpSection
        }
        .navigationTitle("连接设置")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showScanner = true
                } label: {
                    Image(systemName: "qrcode.viewfinder")
                }
                .accessibilityLabel("扫描二维码")
            }
        }
        .sheet(isPresented: $showScanner) {
            EndpointQRScannerView { result in
                showScanner = false
                guard let raw = result else { return }
                input = raw
                probeResult = .untested
                validationError = nil
                // Immediately validate so the user sees instant feedback;
                // they still have to tap "测试连接" before we'll let them
                // save (defense in depth — a phishing QR shouldn't be
                // one-tap-deployable).
                if case .failure(let err) = ServerEndpointStore.validate(raw) {
                    validationError = err.errorDescription
                    scanToast = "已导入但校验失败：\(err.errorDescription ?? "")"
                } else {
                    scanToast = "已导入：\(raw)"
                }
                // Auto-dismiss after 2s so the user sees the import
                // happened but isn't stuck reading.
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    scanToast = nil
                }
            }
        }
        .overlay(alignment: .top) {
            if let toast = scanToast {
                Text(toast)
                    .font(.footnote)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
                    .padding(.top, 8)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: scanToast)
        .onAppear {
            input = store.activeOverrideRaw ?? ""
            probeResult = .untested
        }
    }

    // MARK: - Sections

    private var currentSection: some View {
        Section("当前生效") {
            if let url = APIConfig.resolvedBaseURL() {
                LabeledContent("Base URL", value: url.absoluteString)
                    .font(.footnote.monospaced())
            } else {
                Label("未配置任何服务器", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            }
            if let last = store.lastSyncedAt {
                LabeledContent("远端同步时间",
                                value: last.formatted(date: .abbreviated, time: .standard))
                    .font(.footnote)
            }
        }
    }

    private var inputSection: some View {
        Section {
            HStack(spacing: 8) {
                TextField("https://api.example.com 或 http://192.168.1.x:8000",
                            text: $input)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .onChange(of: input) { old, new in
                        // Only invalidate the probe result when the
                        // *trimmed* URL actually changed — typing a
                        // stray space or pasting a value identical to
                        // what we just probed shouldn't make the user
                        // re-run the test.
                        let oldTrim = old.trimmingCharacters(in: .whitespaces)
                        let newTrim = new.trimmingCharacters(in: .whitespaces)
                        if oldTrim != newTrim {
                            probeResult = .untested
                            validationError = nil
                        }
                    }
                // Native paste affordance — surfaces in the Dynamic
                // Island as a security-vetted button (user can see
                // exactly what pasteboard fragment is being read).
                PasteButton(payloadType: String.self) { strings in
                    guard let first = strings.first else { return }
                    let trimmed = first.trimmingCharacters(in: .whitespacesAndNewlines)
                    DispatchQueue.main.async {
                        input = trimmed
                        probeResult = .untested
                        if case .failure(let err) = ServerEndpointStore.validate(trimmed) {
                            validationError = err.errorDescription
                        } else {
                            validationError = nil
                        }
                    }
                }
                .labelStyle(.iconOnly)
                .buttonBorderShape(.capsule)
            }

            HStack {
                Button {
                    Task { await testConnection() }
                } label: {
                    HStack(spacing: 6) {
                        if probing { ProgressView().controlSize(.small) }
                        Text(probing ? "测试中…" : "测试连接")
                    }
                }
                .disabled(probing || input.trimmingCharacters(in: .whitespaces).isEmpty)

                Spacer()

                Button("应用并保存") { applyOverride() }
                    .disabled(probeResult != .ok)
                    .buttonStyle(.borderedProminent)
            }

            switch probeResult {
            case .untested:
                Text("提示：先点「测试连接」，确认 /healthz 可达后再应用。")
                    .font(.caption).foregroundStyle(.secondary)
            case .ok:
                Label("/healthz 200 OK，可以应用。", systemImage: "checkmark.circle.fill")
                    .font(.caption).foregroundStyle(.green)
            case .failed(let msg):
                Label(msg, systemImage: "xmark.octagon.fill")
                    .font(.caption).foregroundStyle(.red)
            }

            if let err = validationError {
                Text(err).font(.caption).foregroundStyle(.red)
            }

            if store.activeOverrideRaw != nil {
                Button("清除本机覆盖", role: .destructive) {
                    _ = store.setOverrideForInternalBuild(nil, healthzOk: false)
                    input = ""
                    probeResult = .untested
                }
            }
        } header: {
            Text("本机覆盖")
        } footer: {
            Text("覆盖仅影响本机，不影响其他设备/用户。Internal 构建专属能力，正式包不存在此入口。")
        }
    }

    @ViewBuilder
    private var historySection: some View {
        if !store.overrideHistory.isEmpty {
            Section("最近 5 次变更（点击回滚）") {
                ForEach(store.overrideHistory) { entry in
                    Button {
                        rollback(to: entry)
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(entry.url ?? "(清除覆盖)")
                                    .font(.footnote.monospaced())
                                    .foregroundStyle(.primary)
                                HStack(spacing: 6) {
                                    Text(entry.appliedAt
                                            .formatted(date: .abbreviated, time: .shortened))
                                    if entry.healthzOk {
                                        Label("健康", systemImage: "checkmark.seal.fill")
                                            .foregroundStyle(.green)
                                    }
                                }
                                .font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: "arrow.uturn.backward.circle")
                                .foregroundStyle(.tint)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var helpSection: some View {
        Section("帮助") {
            Text("• 局域网联调：路由器给后端电脑绑静态 IP，填 http://<ip>:<port>")
                .font(.caption)
            Text("• 公网 staging：必须 https://")
                .font(.caption)
            Text("• 二维码导入：让团队负责后端的同学在 Mac 终端跑 qrencode -t ANSI '<url>'，或网页搜 “qr code generator” 把 URL 转成二维码贴墙上扫")
                .font(.caption)
            Text("• 测试连接成功后，登录请求会立刻打到你填写的地址")
                .font(.caption)
        }
        .foregroundStyle(.secondary)
    }

    // MARK: - Actions

    private func testConnection() async {
        validationError = nil
        let raw = input.trimmingCharacters(in: .whitespaces)
        // 先做纯输入校验，给即时反馈。
        switch ServerEndpointStore.validate(raw) {
        case .failure(let err):
            validationError = err.errorDescription
            probeResult = .untested
            return
        case .success:
            break
        }
        probing = true
        defer { probing = false }
        let ok = await store.probeHealthz(raw)
        probeResult = ok ? .ok : .failed("无法连接到 \(raw)/healthz（超时或非 2xx）")
    }

    private func applyOverride() {
        let healthOk = (probeResult == .ok)
        let result = store.setOverrideForInternalBuild(input, healthzOk: healthOk)
        switch result {
        case .success(let canonical):
            input = canonical ?? ""
        case .failure(let err):
            validationError = err.errorDescription
        }
    }

    private func rollback(to entry: OverrideHistoryEntry) {
        // 不再二次探测，因为这是用户主动选历史值，本就承担风险。
        // 但仍走 setOverrideForInternalBuild → 经过 validate 校验，
        // 避免历史里某条因新规则失效（如以前允许的 host 现在进了黑名单）。
        let raw = entry.url
        switch store.setOverrideForInternalBuild(raw, healthzOk: entry.healthzOk) {
        case .success(let canonical):
            input = canonical ?? ""
            probeResult = .untested
        case .failure(let err):
            validationError = "历史项不再有效：\(err.errorDescription ?? "")"
        }
    }
}

#Preview {
    NavigationStack { ServerEndpointPublicView() }
}

// MARK: - QR scanner
//
// Thin AVFoundation wrapper, Internal-only. We deliberately stay minimal:
//   - Single-shot: stops the capture session as soon as the first valid
//     QR payload is observed.
//   - Returns the raw string verbatim (validation happens in
//     ServerEndpointPublicView via ServerEndpointStore.validate).
//   - Camera permission failure simply dismisses the sheet — the user
//     can still type URLs manually, and we don't want to over-engineer
//     a flow that internal-only debug surface.

private struct EndpointQRScannerView: View {
    let onScan: (String?) -> Void
    @State private var permissionDenied = false
    @State private var torchOn = false

    var body: some View {
        ZStack {
            QRScannerRepresentable(onResult: onScan,
                                     onPermissionDenied: { permissionDenied = true },
                                     torchOn: $torchOn)
                .ignoresSafeArea()
            VStack {
                Spacer()
                Text("将二维码对准取景框")
                    .font(.footnote)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.black.opacity(0.55), in: Capsule())
                    .foregroundStyle(.white)
                    .padding(.bottom, 36)
            }
            if permissionDenied {
                permissionDeniedOverlay
            }
        }
        .overlay(alignment: .topLeading) {
            Button {
                onScan(nil)
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title)
                    .foregroundStyle(.white, .black.opacity(0.5))
            }
            .padding()
        }
        .overlay(alignment: .topTrailing) {
            // Torch toggle — IDC server rooms, dim offices, etc. The
            // representable layer flips the AVCaptureDevice torchMode
            // when this @State changes.
            Button {
                torchOn.toggle()
            } label: {
                Image(systemName: torchOn ? "flashlight.on.fill"
                                          : "flashlight.off.fill")
                    .font(.title2)
                    .padding(10)
                    .background(.black.opacity(0.5), in: Circle())
                    .foregroundStyle(.white)
            }
            .padding()
        }
    }

    private var permissionDeniedOverlay: some View {
        VStack(spacing: 16) {
            Image(systemName: "video.slash.fill")
                .font(.system(size: 44))
                .foregroundStyle(.white)
            Text("摄像头未授权")
                .font(.headline).foregroundStyle(.white)
            Text("请在系统设置中允许 “拾光 Dev” 使用摄像头，或返回手动输入 URL。")
                .multilineTextAlignment(.center)
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.7))
                .padding(.horizontal, 24)
            Button("返回") { onScan(nil) }
                .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(.black.opacity(0.7))
    }
}

private struct QRScannerRepresentable: UIViewControllerRepresentable {
    let onResult: (String?) -> Void
    let onPermissionDenied: () -> Void
    @Binding var torchOn: Bool

    func makeUIViewController(context: Context) -> QRScannerVC {
        let vc = QRScannerVC()
        vc.onResult = onResult
        vc.onPermissionDenied = onPermissionDenied
        return vc
    }

    func updateUIViewController(_ uiViewController: QRScannerVC, context: Context) {
        uiViewController.setTorch(torchOn)
    }
}

private final class QRScannerVC: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onResult: ((String?) -> Void)?
    var onPermissionDenied: (() -> Void)?
    private let session = AVCaptureSession()
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var didEmit = false
    private var currentDevice: AVCaptureDevice?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            setup()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] ok in
                DispatchQueue.main.async {
                    if ok { self?.setup() } else { self?.onPermissionDenied?() }
                }
            }
        default:
            onPermissionDenied?()
        }
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        if !session.isRunning {
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.session.startRunning()
            }
        }
    }

    override func viewDidDisappear(_ animated: Bool) {
        super.viewDidDisappear(animated)
        if session.isRunning { session.stopRunning() }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    /// Toggle the back-camera torch. Called from SwiftUI's
    /// updateUIViewController whenever the @State binding flips.
    /// No-op when the device has no torch (simulator, iPad front cam).
    func setTorch(_ on: Bool) {
        guard let device = currentDevice, device.hasTorch else { return }
        do {
            try device.lockForConfiguration()
            device.torchMode = on ? .on : .off
            device.unlockForConfiguration()
        } catch {
            // Silent — torch failure shouldn't crash the scanner.
        }
    }

    private func setup() {
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else {
            onResult?(nil)
            return
        }
        currentDevice = device
        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else {
            onResult?(nil)
            return
        }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]

        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.bounds
        view.layer.addSublayer(layer)
        previewLayer = layer

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.session.startRunning()
        }
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput,
                          didOutput metadataObjects: [AVMetadataObject],
                          from connection: AVCaptureConnection) {
        guard !didEmit,
              let obj = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              obj.type == .qr,
              let raw = obj.stringValue, !raw.isEmpty else {
            return
        }
        didEmit = true
        session.stopRunning()
        // Haptic confirmation so user knows the scan succeeded even
        // before the sheet animates away.
        let gen = UINotificationFeedbackGenerator()
        gen.notificationOccurred(.success)
        onResult?(raw)
    }
}

#endif
