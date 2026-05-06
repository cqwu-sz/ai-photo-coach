import SwiftUI

/// Capture flow:
/// 1. Show camera preview + a 12-segment ring that fills as the user pans 360 deg
/// 2. User taps Record, pans the phone in a circle. We sample frames+heading.
/// 3. User taps Stop -> we extract 8-12 keyframes and call /analyze.
struct EnvCaptureView: View {
    let personCount: Int
    let qualityMode: QualityMode
    let styleKeywords: [String]

    @StateObject private var heading = HeadingTracker()
    @StateObject private var capture: VideoCaptureSession
    @StateObject private var viewModel: EnvCaptureViewModel
    @EnvironmentObject var router: AppRouter

    init(personCount: Int, qualityMode: QualityMode, styleKeywords: [String]) {
        self.personCount = personCount
        self.qualityMode = qualityMode
        self.styleKeywords = styleKeywords

        let h = HeadingTracker()
        let cap = VideoCaptureSession(heading: h)
        _heading = StateObject(wrappedValue: h)
        _capture = StateObject(wrappedValue: cap)
        _viewModel = StateObject(wrappedValue: EnvCaptureViewModel(
            personCount: personCount,
            qualityMode: qualityMode,
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
        .onChange(of: viewModel.analyzeResult) { _, response in
            guard let response else { return }
            router.push(.results(response))
        }
    }

    private var topBar: some View {
        HStack {
            Text("人数 \(personCount)  •  \(qualityMode == .fast ? "Fast" : "High")")
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
        .padding(.bottom, 24)
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
        capture.beginRecording()
    }

    private func stopAndAnalyze() {
        Task { await viewModel.stopAndAnalyze(heading: heading) }
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
