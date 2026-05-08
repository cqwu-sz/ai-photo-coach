import AVFoundation
import Combine
import Foundation
import UIKit

/// Records a video to a temp file and concurrently emits per-frame samples
/// (image + heading) so the keyframe extractor can pick representative
/// frames without re-decoding the video afterwards.
@MainActor
final class VideoCaptureSession: NSObject, ObservableObject {
    @Published private(set) var isRunning = false
    @Published private(set) var isRecording = false
    @Published private(set) var sampleCount = 0
    @Published private(set) var lastError: String?

    let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let movieOutput = AVCaptureMovieFileOutput()
    private let sampleQueue = DispatchQueue(label: "VideoCapture.sampleQueue")

    private(set) var capturedSamples: [HeadedFrame] = []

    private var heading: HeadingTracker

    init(heading: HeadingTracker) {
        self.heading = heading
        super.init()
    }

    func configure() async {
        guard await Self.requestCameraAccess() else {
            lastError = "Camera access denied"
            return
        }
        session.beginConfiguration()
        session.sessionPreset = .high

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera,
                                                   for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else {
            session.commitConfiguration()
            lastError = "Could not configure back camera"
            return
        }
        session.addInput(input)

        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        videoOutput.setSampleBufferDelegate(self, queue: sampleQueue)
        if session.canAddOutput(videoOutput) {
            session.addOutput(videoOutput)
        }
        if session.canAddOutput(movieOutput) {
            session.addOutput(movieOutput)
        }
        session.commitConfiguration()
    }

    func start() {
        guard !session.isRunning else { return }
        Task.detached { [session] in
            session.startRunning()
        }
        isRunning = true
    }

    func stop() {
        if session.isRunning {
            session.stopRunning()
        }
        isRunning = false
    }

    func beginRecording() {
        capturedSamples.removeAll()
        sampleCount = 0
        isRecording = true
        heading.reset()
    }

    func endRecording() -> [HeadedFrame] {
        isRecording = false
        return capturedSamples
    }

    static func requestCameraAccess() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined:
            return await withCheckedContinuation { cont in
                AVCaptureDevice.requestAccess(for: .video) { ok in
                    cont.resume(returning: ok)
                }
            }
        default: return false
        }
    }
}

extension VideoCaptureSession: AVCaptureVideoDataOutputSampleBufferDelegate {
    nonisolated func captureOutput(_ output: AVCaptureOutput,
                       didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let pixel = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let ts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let timestampMs = Int(CMTimeGetSeconds(ts) * 1000)

        let ciImage = CIImage(cvPixelBuffer: pixel)
        let context = CIContext(options: nil)
        guard let cg = context.createCGImage(ciImage, from: ciImage.extent) else { return }

        // Compute on-device quality signals here on the sample queue so we
        // don't block the main actor with per-frame Accelerate work.
        let quality = FrameQuality.compute(cgImage: cg)

        Task { @MainActor [weak self] in
            guard let self, self.isRecording else { return }
            let frame = HeadedFrame(
                image: UIImage(cgImage: cg, scale: 1, orientation: .right),
                azimuthDeg: self.heading.azimuthDeg,
                pitchDeg: self.heading.pitchDeg,
                rollDeg: self.heading.rollDeg,
                timestampMs: timestampMs,
                meanLuma: quality.meanLuma,
                blurScore: quality.blurScore
            )
            self.capturedSamples.append(frame)
            self.sampleCount = self.capturedSamples.count
        }
    }
}

struct HeadedFrame: Sendable {
    let image: UIImage
    let azimuthDeg: Double
    let pitchDeg: Double
    let rollDeg: Double
    let timestampMs: Int
    /// BT-601 mean luma in [0, 1] computed at 96 px during capture.
    let meanLuma: Double?
    /// Average |dI/dx| over a 96-px greyscale view; >= 8 ≈ in focus,
    /// < 3 ≈ blurry. See ``FrameQuality.compute`` for calibration.
    let blurScore: Double?

    init(
        image: UIImage,
        azimuthDeg: Double,
        pitchDeg: Double,
        rollDeg: Double,
        timestampMs: Int,
        meanLuma: Double? = nil,
        blurScore: Double? = nil
    ) {
        self.image = image
        self.azimuthDeg = azimuthDeg
        self.pitchDeg = pitchDeg
        self.rollDeg = rollDeg
        self.timestampMs = timestampMs
        self.meanLuma = meanLuma
        self.blurScore = blurScore
    }
}

/// Cheap on-device quality signals that pair with the LLM's `capture_quality`
/// self-assessment. Computed during sampling so the user can be warned
/// *before* the round-trip to the model.
enum FrameQuality {
    /// Compute (meanLuma, blurScore) over a 96-px-wide greyscale grab of
    /// ``cg``. Runs in a few hundred microseconds — safe to call per
    /// captured sample on the camera queue.
    static func compute(cgImage cg: CGImage) -> (meanLuma: Double, blurScore: Double) {
        let targetW = 96
        let aspect = Double(cg.height) / Double(cg.width)
        let targetH = max(48, Int(Double(targetW) * aspect))

        let cs = CGColorSpaceCreateDeviceGray()
        guard let ctx = CGContext(
            data: nil, width: targetW, height: targetH,
            bitsPerComponent: 8, bytesPerRow: targetW,
            space: cs, bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else {
            return (0.5, 0)
        }
        ctx.interpolationQuality = .low
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: targetW, height: targetH))
        guard let data = ctx.data else { return (0.5, 0) }
        let buf = data.bindMemory(to: UInt8.self, capacity: targetW * targetH)

        var sum: UInt64 = 0
        let px = targetW * targetH
        for i in 0..<px { sum &+= UInt64(buf[i]) }
        let meanLuma = (Double(sum) / Double(px)) / 255.0

        var grad: UInt64 = 0
        for y in 0..<targetH {
            let row = y * targetW
            for x in 0..<(targetW - 1) {
                let a = Int(buf[row + x])
                let b = Int(buf[row + x + 1])
                grad &+= UInt64(abs(a - b))
            }
        }
        let blurScore = Double(grad) / Double(px)

        return (
            (meanLuma * 1000).rounded() / 1000,
            (blurScore * 1000).rounded() / 1000
        )
    }
}
