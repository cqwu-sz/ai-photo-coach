import Foundation
import UIKit

/// Client-side capture-quality verdict, computed *before* /analyze is hit.
/// Mirrors the Web `assessCaptureQuality` shape — it's the same idea on a
/// different platform.
struct ClientCaptureVerdict: Equatable, Sendable {
    enum Severity: String, Sendable {
        case ok, warn, block
    }
    let severity: Severity
    let issues: [String]
    let meanLuma: Double
    let medianBlur: Double
    let azimuthSpan: Double
    let pitchAbsAvg: Double
}

@MainActor
final class EnvCaptureViewModel: ObservableObject {
    @Published var isAnalyzing = false
    @Published var showError = false
    @Published var errorMessage: String?
    @Published var analyzeResult: AnalyzeResponse?
    /// Set when stopAndAnalyze finds a "warn"-or-worse verdict on the
    /// freshly-captured samples. EnvCaptureView uses this to show the
    /// `CaptureQualitySheet` modal *before* burning a /analyze call.
    @Published var clientVerdict: ClientCaptureVerdict?

    private let personCount: Int
    private let qualityMode: QualityMode
    private let sceneMode: SceneMode
    private let styleKeywords: [String]
    private let capture: VideoCaptureSession

    /// Stashed between the precheck step and the user's "proceed" tap so
    /// we don't recompute keyframes after the modal closes.
    private var pendingKeyframes: [HeadedFrame]?

    init(personCount: Int,
         qualityMode: QualityMode,
         sceneMode: SceneMode = .portrait,
         styleKeywords: [String],
         capture: VideoCaptureSession) {
        self.personCount = personCount
        self.qualityMode = qualityMode
        self.sceneMode = sceneMode
        self.styleKeywords = styleKeywords
        self.capture = capture
    }

    func stopAndAnalyze(heading: HeadingTracker) async {
        let frames = capture.endRecording()
        guard frames.count >= 4 else {
            present(error: "录制时间太短，请至少环视 5 秒")
            return
        }

        let extractor = KeyframeExtractor()
        let keyframes = extractor.extract(from: frames, target: 10)
        guard keyframes.count >= 4 else {
            present(error: "提取关键帧失败，请重试")
            return
        }

        // Client-side capture quality precheck — runs on the cheap
        // signals computed during sampling. Block-severity issues stop
        // the analyze; warn-severity ones surface a sheet so the user
        // can decide.
        let verdict = Self.assessQuality(samples: frames)
        if verdict.severity != .ok {
            self.pendingKeyframes = keyframes
            self.clientVerdict = verdict
            if verdict.severity == .block {
                // We DON'T isAnalyzing-spin for block; the sheet alone is
                // enough. Reset the spinner just in case it was shown.
                self.isAnalyzing = false
                return
            }
            // For warn we still wait for the sheet to call proceedAnalyze.
            return
        }

        await runAnalyze(keyframes: keyframes)
    }

    /// Called by EnvCaptureView when the user tapped "知道了，继续分析"
    /// in the CaptureQualitySheet (only available for `warn` severity).
    func proceedAnalyze() async {
        guard let kfs = pendingKeyframes else { return }
        clientVerdict = nil
        pendingKeyframes = nil
        await runAnalyze(keyframes: kfs)
    }

    /// Called when the user tapped "重新环视" in the sheet — clears state
    /// so the next record cycle starts clean.
    func dismissVerdict() {
        clientVerdict = nil
        pendingKeyframes = nil
    }

    private func runAnalyze(keyframes: [HeadedFrame]) async {
        isAnalyzing = true
        defer { isAnalyzing = false }

        let frameMeta = keyframes.enumerated().map { idx, kf in
            FrameMeta(
                index: idx,
                azimuthDeg: kf.azimuthDeg,
                pitchDeg: kf.pitchDeg,
                rollDeg: kf.rollDeg,
                timestampMs: kf.timestampMs,
                ambientLux: nil,
                blurScore: kf.blurScore,
                meanLuma: kf.meanLuma,
                faceHit: nil
            )
        }

        var geoFix: GeoFix?
        if sceneMode.needsSunInfo {
            geoFix = await LocationProvider.shared.ensureGeoFix()
        }

        let meta = CaptureMeta(
            personCount: personCount,
            qualityMode: qualityMode,
            sceneMode: sceneMode,
            styleKeywords: styleKeywords,
            frameMeta: frameMeta,
            geo: geoFix
        )

        let frameData: [Data] = keyframes.compactMap {
            $0.image.jpegData(compressionQuality: 0.7)
        }

        let referenceData = await ReferenceImageStore.shared.activeThumbnailData(limit: 4)
        let modelCfg = ModelConfigStore.currentForRequest()

        do {
            let response = try await APIClient.shared.analyze(
                meta: meta,
                frames: frameData,
                referenceThumbnails: referenceData,
                modelId: modelCfg.modelId.isEmpty ? nil : modelCfg.modelId,
                modelApiKey: modelCfg.apiKey.isEmpty ? nil : modelCfg.apiKey,
                modelBaseUrl: modelCfg.baseUrl.isEmpty ? nil : modelCfg.baseUrl
            )
            CapturedFramesStore.save(
                frames: frameData,
                frameMeta: frameMeta,
                sceneMode: sceneMode
            )
            analyzeResult = response
        } catch {
            present(error: error.localizedDescription)
        }
    }

    private func present(error: String) {
        errorMessage = error
        showError = true
    }

    /// Pure / testable static — compute a verdict from the raw samples.
    /// Calibrated to match the Web equivalent (`assessCaptureQuality`).
    static func assessQuality(samples: [HeadedFrame]) -> ClientCaptureVerdict {
        let lumas = samples.compactMap { $0.meanLuma }
        let blurs = samples.compactMap { $0.blurScore }
        let azs   = samples.map { $0.azimuthDeg }
        let pitches = samples.map { abs($0.pitchDeg) }

        let meanLuma = lumas.isEmpty ? 0.5 : lumas.reduce(0, +) / Double(lumas.count)
        let medianBlur = median(blurs)
        let azSpan = (azs.max() ?? 0) - (azs.min() ?? 0)
        let pitchAbsAvg = pitches.isEmpty ? 0
            : pitches.reduce(0, +) / Double(pitches.count)

        var issues: [String] = []
        var severity: ClientCaptureVerdict.Severity = .ok
        func bump(_ s: ClientCaptureVerdict.Severity) {
            let order: [ClientCaptureVerdict.Severity: Int] = [.ok: 0, .warn: 1, .block: 2]
            if (order[s] ?? 0) > (order[severity] ?? 0) { severity = s }
        }

        if meanLuma < 0.06 {
            issues.append("环境太暗（亮度 < 6%）"); bump(.block)
        } else if meanLuma < 0.12 {
            issues.append("环境偏暗（亮度 < 12%）"); bump(.warn)
        }
        if azSpan < 30 {
            issues.append("环视范围太窄（仅转了 \(Int(azSpan))°）"); bump(.block)
        } else if azSpan < 90 {
            issues.append("环视范围偏窄（\(Int(azSpan))°，建议 > 180°）"); bump(.warn)
        }
        if medianBlur < 1.5 {
            issues.append("画面偏糊，可能晃动太快或失焦"); bump(.block)
        } else if medianBlur < 4 {
            issues.append("画面有些糊，建议慢一点"); bump(.warn)
        }
        if pitchAbsAvg > 35 {
            issues.append("镜头倾角偏大（平均 \(Int(pitchAbsAvg))°），可能怼着地面或天空")
            bump(.warn)
        }

        return ClientCaptureVerdict(
            severity: severity, issues: issues,
            meanLuma: meanLuma, medianBlur: medianBlur,
            azimuthSpan: azSpan, pitchAbsAvg: pitchAbsAvg
        )
    }

    private static func median(_ xs: [Double]) -> Double {
        guard !xs.isEmpty else { return 0 }
        let s = xs.sorted()
        let m = s.count / 2
        return s.count.isMultiple(of: 2) ? (s[m - 1] + s[m]) / 2 : s[m]
    }
}
