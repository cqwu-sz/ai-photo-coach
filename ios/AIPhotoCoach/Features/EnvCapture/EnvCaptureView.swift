import SwiftUI

/// Capture flow:
/// 1. Show camera preview + a 12-segment ring that fills as the user pans 360 deg
/// 2. User taps Record, pans the phone in a circle. We sample frames+heading.
/// 3. User taps Stop -> we extract 8-12 keyframes and call /analyze.
struct EnvCaptureView: View {
    let personCount: Int
    let qualityMode: QualityMode
    let sceneMode: SceneMode
    let styleKeywords: [String]

    @StateObject private var heading = HeadingTracker()
    @StateObject private var capture: VideoCaptureSession
    @StateObject private var viewModel: EnvCaptureViewModel
    @EnvironmentObject var router: AppRouter

    init(personCount: Int, qualityMode: QualityMode, sceneMode: SceneMode = .portrait, styleKeywords: [String]) {
        self.personCount = personCount
        self.qualityMode = qualityMode
        self.sceneMode = sceneMode
        self.styleKeywords = styleKeywords

        let h = HeadingTracker()
        let cap = VideoCaptureSession(heading: h)
        _heading = StateObject(wrappedValue: h)
        _capture = StateObject(wrappedValue: cap)
        _viewModel = StateObject(wrappedValue: EnvCaptureViewModel(
            personCount: personCount,
            qualityMode: qualityMode,
            sceneMode: sceneMode,
            styleKeywords: styleKeywords,
            capture: cap
        ))
    }

    var body: some View {
        ZStack {
            CameraPreviewView(session: capture.session)
                .ignoresSafeArea()

            VStack {
                topBar
                Spacer()
                guidanceOverlay
                Spacer()
                bottomBar
            }
            .padding()

            if viewModel.isAnalyzing {
                analyzingOverlay
            }
        }
        .navigationBarBackButtonHidden(viewModel.isAnalyzing)
        .task {
            await capture.configure()
            capture.start()
            heading.start()
        }
        .onDisappear {
            capture.stop()
            heading.stop()
        }
        .alert("分析失败", isPresented: $viewModel.showError) {
            Button("好的", role: .cancel) {}
        } message: {
            Text(viewModel.errorMessage ?? "Unknown error")
        }
        .sheet(item: Binding(
            get: { viewModel.clientVerdict.map(IdentifiedVerdict.init) },
            set: { _ in viewModel.dismissVerdict() }
        )) { wrapper in
            CaptureQualitySheet(
                verdict: wrapper.verdict,
                onRetake: { viewModel.dismissVerdict() },
                onProceed: viewModel.clientVerdict?.severity == .warn
                    ? { Task { await viewModel.proceedAnalyze() } }
                    : nil
            )
            .presentationDetents([.medium])
        }
        .onChange(of: viewModel.analyzeResult) { _, response in
            guard let response else { return }
            router.push(.results(response))
        }
    }

    private var topBar: some View {
        HStack {
            Text("\(sceneMode.displayName) · \(personCount) 人 · \(qualityMode == .fast ? "Fast" : "High")")
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(.ultraThinMaterial, in: Capsule())
            Spacer()
        }
    }

    private var guidanceOverlay: some View {
        VStack(spacing: 16) {
            HeadingRing(
                azimuth: heading.azimuthDeg,
                covered: heading.coveredAngles,
                isRecording: capture.isRecording
            )
            .frame(width: 220, height: 220)

            Text(guidanceText)
                .font(.headline)
                .foregroundColor(.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(.black.opacity(0.4), in: Capsule())
        }
    }

    private var bottomBar: some View {
        VStack(spacing: 12) {
            if qualityMode == .high && !capture.isRecording {
                Text("精致出片：请慢速转一圈 ≈ 8 秒，会同时录制视频上传给 AI")
                    .font(.system(size: 12))
                    .foregroundStyle(.white.opacity(0.85))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(.black.opacity(0.45), in: Capsule())
            }
            if viewModel.degradedFromHighToFast {
                // High mode failed (no .mov, oversized, etc.) — surface
                // an honest "auto-降级" badge so the user isn't billed/
                // surprised by a fast-only result they expected to be high.
                Text("⚠ 本次环境不支持高质量模式，已为你按快速模式分析")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Color.orange.opacity(0.85), in: Capsule())
            }
            inner
        }
        .padding(.bottom, 24)
    }

    private var inner: some View {
        HStack(spacing: 24) {
            if capture.isRecording {
                Button(action: stopAndAnalyze) {
                    Image(systemName: "stop.circle.fill")
                        .resizable()
                        .frame(width: 72, height: 72)
                        .foregroundColor(.red)
                }
                .disabled(viewModel.isAnalyzing)
            } else {
                Button(action: startRecording) {
                    Image(systemName: "record.circle")
                        .resizable()
                        .frame(width: 72, height: 72)
                        .foregroundColor(.white)
                }
                .disabled(viewModel.isAnalyzing)
            }
        }
    }

    private var analyzingOverlay: some View {
        VStack(spacing: 16) {
            ProgressView()
                .progressViewStyle(.circular)
                .tint(.white)
                .scaleEffect(1.6)
            Text("正在分析环境...")
                .foregroundStyle(.white)
                .font(.headline)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.black.opacity(0.6))
        .ignoresSafeArea()
    }

    private var guidanceText: String {
        if !capture.isRecording {
            return "对准场景，点录制开始环视一圈"
        }
        if heading.coverageProgress >= 0.9 {
            return "覆盖完成 ✓ 可以停止录制"
        }
        if heading.coverageProgress >= 0.5 {
            return "继续顺时针转动手机..."
        }
        return "缓慢顺时针转动 (覆盖 \(Int(heading.coverageProgress * 100))%)"
    }

    private func startRecording() {
        // High mode also records a 720p .mov so Gemini Pro can do
        // temporal reasoning. Capture session writes to a temp file and
        // hands the URL back via endRecording().
        capture.shouldRecordVideo = (qualityMode == .high)
        capture.beginRecording()
    }

    private func stopAndAnalyze() {
        Task { await viewModel.stopAndAnalyze(heading: heading) }
    }
}

/// Tiny wrapper so `.sheet(item:)` can use the value-typed
/// ``ClientCaptureVerdict`` (sheet items must be Identifiable).
private struct IdentifiedVerdict: Identifiable {
    let id = UUID()
    let verdict: ClientCaptureVerdict
}

/// Modal that surfaces the client-side capture-quality verdict before
/// /analyze is called. Mirrors `.capture-sheet` on web. For block-severity
/// it only offers "重新环视"; for warn it also offers "知道了，继续分析".
private struct CaptureQualitySheet: View {
    let verdict: ClientCaptureVerdict
    let onRetake: () -> Void
    /// nil -> hide the proceed button (block severity).
    let onProceed: (() -> Void)?

    private var title: String {
        switch verdict.severity {
        case .block: return "这段环视看起来不太够 AI 出片"
        case .warn:  return "环视有几个小问题，要继续吗？"
        case .ok:    return "环视已就绪"
        }
    }

    private var tint: Color {
        switch verdict.severity {
        case .block: return Color(red: 0.93, green: 0.40, blue: 0.40)
        case .warn:  return Color(red: 0.96, green: 0.72, blue: 0.38)
        case .ok:    return Color.accentColor
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: verdict.severity == .block
                      ? "exclamationmark.triangle.fill"
                      : "exclamationmark.circle.fill")
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(tint)
                Text(title)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 6) {
                ForEach(verdict.issues, id: \.self) { issue in
                    HStack(alignment: .top, spacing: 6) {
                        Text("·").foregroundStyle(.secondary)
                        Text(issue)
                            .font(.subheadline)
                            .foregroundStyle(.primary)
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(.secondarySystemBackground))
            )
            HStack(spacing: 10) {
                Button(action: onRetake) {
                    Text("重新环视")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(LinearGradient(
                                    colors: [tint, tint.opacity(0.7)],
                                    startPoint: .leading, endPoint: .trailing))
                        )
                }
                .buttonStyle(.plain)
                if let proceed = onProceed {
                    Button(action: proceed) {
                        Text("知道了，继续分析")
                            .font(.callout.weight(.semibold))
                            .foregroundStyle(.primary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(
                                RoundedRectangle(cornerRadius: 12, style: .continuous)
                                    .stroke(Color.primary.opacity(0.18), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(20)
        .padding(.top, 6)
    }
}

private struct HeadingRing: View {
    let azimuth: Double
    let covered: Set<Int>
    let isRecording: Bool

    var body: some View {
        ZStack {
            ForEach(0..<12, id: \.self) { i in
                let angle = Double(i) * 30.0
                let bucket = i * 30
                Circle()
                    .trim(from: 0, to: 1.0 / 14.0)
                    .stroke(
                        covered.contains(bucket) ? Color.green : Color.white.opacity(0.3),
                        style: StrokeStyle(lineWidth: 14, lineCap: .round)
                    )
                    .rotationEffect(.degrees(angle - 90 - 12))
            }

            Image(systemName: isRecording ? "circle.fill" : "viewfinder")
                .resizable()
                .frame(width: 28, height: 28)
                .foregroundColor(isRecording ? .red : .white)
                .rotationEffect(.degrees(azimuth))
        }
    }
}
