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
    /// v12 — cached camera intrinsics, captured at session-config time
    /// so each per-frame creation is O(1).
    private var cachedIntrinsics: (focalMm: Double?, focalEq: Double?, sensorMm: Double?) = (nil, nil, nil)
    private let depthOutput = AVCaptureDepthDataOutput()
    private let sampleQueue = DispatchQueue(label: "VideoCapture.sampleQueue")
    private let depthQueue = DispatchQueue(label: "VideoCapture.depthQueue")

    private(set) var capturedSamples: [HeadedFrame] = []
    /// Ring of recent depth frames (≤ 32) collected during recording
    /// when AVDepthData is available on the device. Empty otherwise.
    let depthRing = DepthRingBuffer()
    /// Optional ARKit depth source — used on LiDAR-equipped iPhones
    /// (12 Pro+, 13 Pro+, 14 Pro+, 15 Pro+, 16 Pro+) where smoothed
    /// scene depth is dramatically more accurate than AVDepthData.
    /// Nil on non-LiDAR devices; we fall back to AVCaptureDepthDataOutput.
    private var arkitDepthSource: ARKitDepthSource?

    /// Latest gravity-derived pitch from ARKit (deg). Nil on non-LiDAR
    /// devices. EnvCaptureViewModel reads this when building FrameMeta
    /// to populate `horizon_y_gravity`.
    var lastGravityPitchDeg: Double? { arkitDepthSource?.lastGravityPitchDeg }
    /// "avdepth_lidar" on Pro models, "avdepth_dual" on dual-cam phones,
    /// nil when no depth output could be wired up. Surfaced into the
    /// per-frame DepthLayers source so the LLM/backend can weight it.
    private(set) var depthSource: String?

    /// In high quality mode we also write the raw scan to a temp .mov
    /// and hand the URL back via `endRecording`. Nil otherwise.
    private(set) var lastRecordedMovieURL: URL?
    /// Toggled by the ViewModel before beginRecording so we know whether
    /// to fire up the movie file output for this cycle.
    var shouldRecordVideo: Bool = false

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
        // v12 — stash this lens's intrinsics for HeadedFrame stamping.
        cachedIntrinsics = CameraIntrinsicsResolver.resolve(from: device)

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

        // Best-effort depth output. Only some devices/positions/formats
        // support it. We don't switch the active format aggressively —
        // if the current ``.high`` preset doesn't expose depth we just
        // skip and let MiDaS handle it on the JPEG side.
        if session.canAddOutput(depthOutput),
           let activeFormat = device.activeFormat as AVCaptureDevice.Format?,
           !activeFormat.supportedDepthDataFormats.isEmpty {
            session.addOutput(depthOutput)
            depthOutput.isFilteringEnabled = true
            depthOutput.alwaysDiscardsLateDepthData = true
            depthOutput.setDelegate(self, callbackQueue: depthQueue)
            // LiDAR Pro models advertise `kCVPixelFormatType_DepthFloat32`
            // directly; dual-cam ones come back as Float16 disparity.
            // DepthFusion handles both, so we just record source string.
            let pixelFormats = activeFormat.supportedDepthDataFormats
                .map { CMFormatDescriptionGetMediaSubType($0.formatDescription) }
            let isLidar = pixelFormats.contains(kCVPixelFormatType_DepthFloat32)
            depthSource = isLidar ? "avdepth_lidar" : "avdepth_dual"
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
        depthRing.reset()
        sampleCount = 0
        isRecording = true
        lastRecordedMovieURL = nil
        heading.reset()

        // v12 — boot ARKit smoothed sceneDepth on LiDAR phones. Runs in
        // parallel with AVCaptureSession; both sources happily share
        // the back camera at the system level. The ring buffer gets
        // both AVDepth and ARKit frames; the analyzer prefers ARKit
        // because its source string sorts first in the priority table.
        if ARKitDepthSupport.isAvailable && arkitDepthSource == nil {
            arkitDepthSource = ARKitDepthSource { [weak self] depth, conf, ts in
                guard let self, self.isRecording else { return }
                self.depthRing.record(arkitDepth: depth, confidence: conf, atTimestampMs: ts)
            }
        }
        arkitDepthSource?.start()

        if shouldRecordVideo, !movieOutput.isRecording {
            // Temp .mov in caches; wiped after upload by the ViewModel.
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("envscan-\(UUID().uuidString).mov")
            movieOutput.startRecording(to: url, recordingDelegate: self)
        }
    }

    func endRecording() async -> (frames: [HeadedFrame], movieURL: URL?) {
        isRecording = false
        arkitDepthSource?.stop()
        if movieOutput.isRecording {
            await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                self._stopContinuation = cont
                self.movieOutput.stopRecording()
            }
        }
        return (capturedSamples, lastRecordedMovieURL)
    }

    private var _stopContinuation: CheckedContinuation<Void, Never>?

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

extension VideoCaptureSession: AVCaptureDepthDataOutputDelegate {
    nonisolated func depthDataOutput(
        _ output: AVCaptureDepthDataOutput,
        didOutput depthData: AVDepthData,
        timestamp: CMTime,
        connection: AVCaptureConnection
    ) {
        let timestampMs = Int(CMTimeGetSeconds(timestamp) * 1000)
        Task { @MainActor [weak self] in
            guard let self, self.isRecording, let src = self.depthSource else { return }
            self.depthRing.record(depth: depthData, atTimestampMs: timestampMs, source: src)
        }
    }
}

extension VideoCaptureSession: AVCaptureFileOutputRecordingDelegate {
    nonisolated func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            // Only keep the URL if the recording actually finished cleanly
            // and the file is non-trivially sized. Failure → ViewModel falls
            // back to fast mode automatically.
            if error == nil,
               let attrs = try? FileManager.default.attributesOfItem(atPath: outputFileURL.path),
               let size = attrs[.size] as? Int, size > 32_000 {
                self.lastRecordedMovieURL = outputFileURL
            } else {
                try? FileManager.default.removeItem(at: outputFileURL)
                self.lastRecordedMovieURL = nil
            }
            self._stopContinuation?.resume()
            self._stopContinuation = nil
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
                blurScore: quality.blurScore,
                focalLengthMm: self.cachedIntrinsics.focalMm,
                focalLength35mmEq: self.cachedIntrinsics.focalEq,
                sensorWidthMm: self.cachedIntrinsics.sensorMm
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
    /// v12 — camera intrinsics captured at session config time.
    /// AVCaptureDevice.activeFormat.videoFieldOfView gives horizontal
    /// FOV in degrees; we derive (focal_length_mm, sensor_width_mm)
    /// from the device's known sensor + that FOV.
    let focalLengthMm: Double?
    let focalLength35mmEq: Double?
    let sensorWidthMm: Double?

    init(
        image: UIImage,
        azimuthDeg: Double,
        pitchDeg: Double,
        rollDeg: Double,
        timestampMs: Int,
        meanLuma: Double? = nil,
        blurScore: Double? = nil,
        focalLengthMm: Double? = nil,
        focalLength35mmEq: Double? = nil,
        sensorWidthMm: Double? = nil
    ) {
        self.image = image
        self.azimuthDeg = azimuthDeg
        self.pitchDeg = pitchDeg
        self.rollDeg = rollDeg
        self.timestampMs = timestampMs
        self.meanLuma = meanLuma
        self.blurScore = blurScore
        self.focalLengthMm = focalLengthMm
        self.focalLength35mmEq = focalLength35mmEq
        self.sensorWidthMm = sensorWidthMm
    }
}

/// Convert AVCaptureDevice intrinsics into the schema fields backend
/// uses for distance calibration. Returns (focal_mm, focal_35eq,
/// sensor_width_mm). Falls back to public iPhone sensor specs when
/// the device doesn't expose lensFocalLength directly (most do not).
enum CameraIntrinsicsResolver {
    /// Standard iPhone main-camera sensor widths (Apple has been
    /// publishing these via teardowns; values in mm). Keep in sync
    /// with backend lens table.
    private static let SENSOR_WIDTH_BY_LENS: [String: Double] = [
        "ultra wide camera":  3.51,    // 13mm equiv
        "wide camera":        7.01,    // 26mm equiv (1x main)
        "telephoto camera":   5.20,    // 77mm-class
        // 5x tetraprism (15 Pro Max+) — sensor is similar to 3x but
        // the optical path is folded; for FOV math, equivalent width.
    ]

    static func resolve(from device: AVCaptureDevice) -> (focalMm: Double?, focalEq: Double?, sensorMm: Double?) {
        // localizedName looks like "Back Camera", "Front Camera", "Back
        // Wide Camera"; deviceType is the canonical lens identifier.
        let lensName = device.deviceType.rawValue.lowercased()
        let sensorMm: Double? = SENSOR_WIDTH_BY_LENS.first(where: { lensName.contains($0.key.split(separator: " ").last!.lowercased()) })?.value
        let fovDeg = Double(device.activeFormat.videoFieldOfView)   // horizontal
        guard fovDeg > 0 else { return (nil, nil, sensorMm) }
        // tan(fov/2) = (sensor/2) / focal  →  focal = (sensor/2) / tan(fov/2)
        let focalMm: Double? = sensorMm.map { ($0 / 2.0) / tan(fovDeg * .pi / 360.0) }
        // 35mm equivalent: focal × (36 / sensor_width).
        let focalEq: Double? = (focalMm != nil && sensorMm != nil)
            ? focalMm! * (36.0 / sensorMm!) : nil
        return (focalMm.map { round($0 * 100) / 100 },
                focalEq.map { round($0 * 10) / 10 },
                sensorMm)
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
