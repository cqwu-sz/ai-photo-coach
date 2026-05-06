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

        Task { @MainActor [weak self] in
            guard let self, self.isRecording else { return }
            let frame = HeadedFrame(
                image: UIImage(cgImage: cg, scale: 1, orientation: .right),
                azimuthDeg: self.heading.azimuthDeg,
                pitchDeg: self.heading.pitchDeg,
                rollDeg: self.heading.rollDeg,
                timestampMs: timestampMs
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
}
