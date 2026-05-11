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
    /// In high quality mode the capture session writes a temp .mov; we
    /// upload it as an extra `video` part so Gemini Pro can do temporal
    /// reasoning. nil otherwise. Cleaned up after the API call.
    private var lastRecordedMovieURL: URL?
    /// Surfaced in the result card so the user knows whether high mode
    /// silently fell back to fast (e.g. video record/upload failed).
    @Published var degradedFromHighToFast: Bool = false

    /// Optional opt-in walk recorded *after* the standing pan, used by
    /// the backend's three-source position fusion. Set this from the
    /// UI before calling ``stopAndAnalyze`` (or ``proceedAnalyze``);
    /// nil disables the SfM branch and analyze runs as before.
    var pendingWalkSegment: WalkSegment?

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
        let result = await capture.endRecording()
        let frames = result.frames
        self.lastRecordedMovieURL = result.movieURL
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

        // Run Apple Vision once per keyframe (10 images total) on a
        // background queue. Each frame yields person box / saliency
        // quadrant / horizon tilt + foreground candidates + (optional)
        // MiDaS depth — the prompt builder folds these into
        // ENVIRONMENT FACTS so the LLM doesn't have to re-derive them
        // from the JPEGs.
        // v10.2: process frames sequentially so subject selection can
        // see the previous frame's chosen box. The per-frame cost is
        // < 400 ms even with face + pose + saliency + classification +
        // MiDaS, so 10 frames sequentially fit well inside the typical
        // network round-trip budget that follows.
        let semanticsByIndex: [Int: FrameSemantics.Result] =
            await Task.detached(priority: .utility) { () -> [Int: FrameSemantics.Result] in
                var dict: [Int: FrameSemantics.Result] = [:]
                let results = FrameSemantics.computeMany(images: keyframes.map { $0.image })
                for (idx, r) in results.enumerated() { dict[idx] = r }
                return dict
            }.value

        // AVDepthData fusion (块 D). When the device captured depth
        // alongside the video stream, replace MiDaS's relative-depth
        // histogram with sensor-grade meters and stamp every
        // foreground candidate with an absolute distance estimate.
        let fusedSemantics: [Int: FrameSemantics.Result] =
            fuseAvDepth(into: semanticsByIndex, keyframes: keyframes)

        let frameMeta = keyframes.enumerated().map { idx, kf -> FrameMeta in
            let sem = fusedSemantics[idx]
            return FrameMeta(
                index: idx,
                azimuthDeg: kf.azimuthDeg,
                pitchDeg: kf.pitchDeg,
                rollDeg: kf.rollDeg,
                timestampMs: kf.timestampMs,
                ambientLux: nil,
                blurScore: kf.blurScore,
                meanLuma: kf.meanLuma,
                faceHit: sem?.personBox != nil ? true : nil,
                personBox: sem?.personBox,
                saliencyQuadrant: sem?.saliencyQuadrant,
                horizonTiltDeg: sem?.horizonTiltDeg,
                foregroundCandidates: sem?.foregroundCandidates,
                depthLayers: sem?.depthLayers,
                poseNoseY: sem?.poseNoseY,
                poseAnkleY: sem?.poseAnkleY,
                faceHeightRatio: sem?.faceHeightRatio,
                horizonY: sem?.horizonY,
                personCount: sem?.personCount,
                subjectBox: sem?.subjectBox,
                rgbMean: sem?.rgbMean,
                lumaP05: sem?.lumaP05,
                lumaP95: sem?.lumaP95,
                highlightClipPct: sem?.highlightClipPct,
                shadowClipPct: sem?.shadowClipPct,
                saturationMean: sem?.saturationMean,
                focalLengthMm: kf.focalLengthMm,
                focalLength35mmEq: kf.focalLength35mmEq,
                sensorWidthMm: kf.sensorWidthMm,
                horizonYVision: sem?.horizonYVision,
                horizonYGravity: Self.gravityHorizonY(
                    pitchDeg: kf.pitchDeg,
                    focalEqMm: kf.focalLength35mmEq,
                    sensorWidthMm: kf.sensorWidthMm
                ),
                skyMaskTopPct: sem?.skyMaskTopPct,
                shoulderTiltDeg: sem?.shoulderTiltDeg,
                hipOffsetX: sem?.hipOffsetX,
                chinForward: sem?.chinForward,
                spineCurve: sem?.spineCurve
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
            geo: geoFix,
            walkSegment: pendingWalkSegment
        )
        pendingWalkSegment = nil

        // Per-quality JPEG export. fast keeps the historical 768px/0.82
        // (sharper than the 384 we used to send); high goes to 1024px/
        // 0.88 so Gemini Pro has more pixels to read fine geometry /
        // leading lines / horizon detail.
        let exportWidth: CGFloat = (qualityMode == .high) ? 1024 : 768
        let exportQuality: CGFloat = (qualityMode == .high) ? 0.88 : 0.82
        let frameData: [Data] = keyframes.compactMap {
            Self.exportKeyframeJPEG($0.image, targetWidth: exportWidth,
                                    quality: exportQuality)
        }

        let referenceData = await ReferenceImageStore.shared.activeThumbnailData(limit: 4)
        let modelCfg = ModelConfigStore.currentForRequest()

        // High mode: read the .mov bytes if available. If reading or
        // sizing fails, surface a degraded flag — analyze still runs.
        var videoData: Data?
        if qualityMode == .high, let url = lastRecordedMovieURL {
            let cap = 12 * 1024 * 1024
            if let data = try? Data(contentsOf: url, options: .mappedIfSafe),
               data.count <= cap {
                videoData = data
            } else {
                degradedFromHighToFast = true
            }
            try? FileManager.default.removeItem(at: url)
            lastRecordedMovieURL = nil
        } else if qualityMode == .high {
            degradedFromHighToFast = true
        }

        do {
            let response = try await APIClient.shared.analyze(
                meta: meta,
                frames: frameData,
                referenceThumbnails: referenceData,
                modelId: modelCfg.modelId.isEmpty ? nil : modelCfg.modelId,
                modelApiKey: modelCfg.apiKey.isEmpty ? nil : modelCfg.apiKey,
                modelBaseUrl: modelCfg.baseUrl.isEmpty ? nil : modelCfg.baseUrl,
                videoMP4: videoData
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

    /// Compute the y-coordinate (top-left, [0,1]) of the horizon line
    /// implied by gravity alone: take the device pitch and the
    /// camera's vertical FOV, then horizon_y = 0.5 + pitch/VFOV.
    /// Returns nil when intrinsics are unknown (Web / non-EXIF iOS).
    static func gravityHorizonY(pitchDeg: Double, focalEqMm: Double?, sensorWidthMm: Double?) -> Double? {
        guard let focalEq = focalEqMm, focalEq > 0 else { return nil }
        // 35mm-equivalent → horizontal FOV (sensor width = 36mm in eq).
        let hFovDeg = 2 * atan(36.0 / (2 * focalEq)) * 180 / .pi
        // Vertical FOV from 4:3 video aspect (HEVC default for video).
        let aspect = 4.0 / 3.0
        let vFovDeg = 2 * atan(tan(hFovDeg * .pi / 360) / aspect) * 180 / .pi
        let raw = 0.5 + pitchDeg / vFovDeg
        return max(0, min(1, raw))
    }

    private static func median(_ xs: [Double]) -> Double {
        guard !xs.isEmpty else { return 0 }
        let s = xs.sorted()
        let m = s.count / 2
        return s.count.isMultiple(of: 2) ? (s[m - 1] + s[m]) / 2 : s[m]
    }

    /// Replace MiDaS depth + annotate foreground candidate distances
    /// using AVDepthData when the capture session collected it. Pure
    /// pass-through when the depth ring is empty (most non-Pro phones).
    private func fuseAvDepth(
        into semantics: [Int: FrameSemantics.Result],
        keyframes: [HeadedFrame]
    ) -> [Int: FrameSemantics.Result] {
        var out = semantics
        for (idx, kf) in keyframes.enumerated() {
            guard let sem = semantics[idx] else { continue }
            guard let snap = capture.depthRing.nearest(toTimestampMs: kf.timestampMs) else { continue }

            let layers = DepthFusion.histogram(payload: snap.payload, source: snap.source) ?? sem.depthLayers
            let candidates = sem.foregroundCandidates?.map { c -> ForegroundCandidate in
                let dist = DepthFusion.medianDepth(in: c.box, payload: snap.payload) ?? c.estimatedDistanceM
                return ForegroundCandidate(
                    label: c.label, box: c.box,
                    confidence: c.confidence,
                    estimatedDistanceM: dist
                )
            }
            out[idx] = FrameSemantics.Result(
                personBox: sem.personBox,
                saliencyQuadrant: sem.saliencyQuadrant,
                horizonTiltDeg: sem.horizonTiltDeg,
                foregroundCandidates: candidates,
                depthLayers: layers,
                poseNoseY: sem.poseNoseY,
                poseAnkleY: sem.poseAnkleY,
                faceHeightRatio: sem.faceHeightRatio,
                horizonY: sem.horizonY,
                personCount: sem.personCount,
                subjectBox: sem.subjectBox,
                rgbMean: sem.rgbMean,
                lumaP05: sem.lumaP05,
                lumaP95: sem.lumaP95,
                highlightClipPct: sem.highlightClipPct,
                shadowClipPct: sem.shadowClipPct,
                saturationMean: sem.saturationMean,
                horizonYVision: sem.horizonYVision,
                skyMaskTopPct: sem.skyMaskTopPct,
                shoulderTiltDeg: sem.shoulderTiltDeg,
                hipOffsetX: sem.hipOffsetX,
                chinForward: sem.chinForward,
                spineCurve: sem.spineCurve
            )
        }
        return out
    }

    /// Resize-to-width then JPEG encode at the requested quality.
    /// We avoid sending the full sensor-resolution UIImage (1920x1080+)
    /// because Gemini downsamples anyway and the wire cost is real.
    static func exportKeyframeJPEG(_ image: UIImage,
                                   targetWidth: CGFloat,
                                   quality: CGFloat) -> Data? {
        let srcW = image.size.width
        guard srcW > 0 else { return nil }
        let scale = min(1.0, targetWidth / srcW)
        let newSize = CGSize(width: image.size.width * scale,
                             height: image.size.height * scale)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1   // produce pixels at newSize, not @2x/@3x
        let renderer = UIGraphicsImageRenderer(size: newSize, format: format)
        let resized = renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: newSize))
        }
        return resized.jpegData(compressionQuality: quality)
    }
}
