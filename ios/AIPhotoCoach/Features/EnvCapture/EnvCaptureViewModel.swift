import Foundation
import UIKit

@MainActor
final class EnvCaptureViewModel: ObservableObject {
    @Published var isAnalyzing = false
    @Published var showError = false
    @Published var errorMessage: String?
    @Published var analyzeResult: AnalyzeResponse?

    private let personCount: Int
    private let qualityMode: QualityMode
    private let styleKeywords: [String]
    private let capture: VideoCaptureSession

    init(personCount: Int,
         qualityMode: QualityMode,
         styleKeywords: [String],
         capture: VideoCaptureSession) {
        self.personCount = personCount
        self.qualityMode = qualityMode
        self.styleKeywords = styleKeywords
        self.capture = capture
    }

    func stopAndAnalyze(heading: HeadingTracker) async {
        isAnalyzing = true
        defer { isAnalyzing = false }

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

        let frameMeta = keyframes.enumerated().map { idx, kf in
            FrameMeta(
                index: idx,
                azimuthDeg: kf.azimuthDeg,
                pitchDeg: kf.pitchDeg,
                rollDeg: kf.rollDeg,
                timestampMs: kf.timestampMs,
                ambientLux: nil
            )
        }

        let meta = CaptureMeta(
            personCount: personCount,
            qualityMode: qualityMode,
            styleKeywords: styleKeywords,
            frameMeta: frameMeta
        )

        let frameData: [Data] = keyframes.compactMap {
            $0.image.jpegData(compressionQuality: 0.7)
        }

        let referenceData = await ReferenceImageStore.shared.activeThumbnailData(limit: 4)

        do {
            let response = try await APIClient.shared.analyze(
                meta: meta,
                frames: frameData,
                referenceThumbnails: referenceData
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
}
