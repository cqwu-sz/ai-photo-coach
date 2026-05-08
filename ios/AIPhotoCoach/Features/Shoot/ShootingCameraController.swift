import AVFoundation
import Combine
import Foundation
import Photos
import UIKit

/// Wraps AVCaptureSession + AVCaptureDevice to apply the backend's
/// ``IphoneApplyPlan`` directly. Live values (ISO, shutter, EV, zoom)
/// are published so the HUD chips can render "AI 设定 → 实测" pairs.
///
/// Threading model:
///   - Public API is ``@MainActor`` for SwiftUI binding.
///   - Heavy AVCaptureSession start/stop runs on a background queue
///     so the UI thread never blocks on device negotiation.
///   - All ``device.lockForConfiguration()`` work happens on the main
///     actor; AVFoundation is documented as thread-safe for that pattern.
@MainActor
final class ShootingCameraController: NSObject, ObservableObject {
    /// Visible to ``CameraPreviewView``.
    let session = AVCaptureSession()

    @Published private(set) var isReady: Bool = false
    @Published private(set) var lastError: String?
    @Published private(set) var live = LiveCameraValues()
    /// Path to the most recently captured photo on disk (App sandbox).
    @Published private(set) var lastCapturedURL: URL?

    /// Set after ``apply(plan:)`` succeeds. The HUD reads it to compare
    /// against ``live`` and decide when everything is "in tolerance".
    @Published private(set) var activePlan: IphoneApplyPlan?

    private let photoOutput = AVCapturePhotoOutput()
    private var device: AVCaptureDevice?
    private var input:  AVCaptureDeviceInput?

    /// Continuation used by ``capturePhoto()`` to wait for the
    /// AVCapturePhotoCaptureDelegate callback.
    private var captureContinuation: CheckedContinuation<URL, Error>?

    // ---------------------------------------------------------------
    // Lifecycle
    // ---------------------------------------------------------------

    func start() async {
        guard await Self.requestCameraAccess() else {
            lastError = "需要相机权限：在「设置 - 拾光」开启「相机」"
            return
        }

        if device == nil {
            await configure()
        }
        guard !session.isRunning else { return }
        // startRunning blocks; off-load to avoid jank on the main actor.
        let s = session
        Task.detached { s.startRunning() }
        isReady = true
        startKVO()
    }

    func stop() {
        stopKVO()
        guard session.isRunning else { return }
        session.stopRunning()
        isReady = false
    }

    /// Choose a device that can satisfy the requested zoom factor. We
    /// prefer ``builtInTripleCamera`` (auto-switching across 0.5/1/2/5x)
    /// when available, falling back to the wide-angle main lens.
    private func configure() async {
        session.beginConfiguration()
        defer { session.commitConfiguration() }

        session.sessionPreset = .photo

        let preferred: [(AVCaptureDevice.DeviceType, AVCaptureDevice.Position)] = [
            (.builtInTripleCamera, .back),
            (.builtInDualWideCamera, .back),
            (.builtInWideAngleCamera, .back),
        ]
        let dev: AVCaptureDevice? = preferred.lazy
            .compactMap { AVCaptureDevice.default($0.0, for: .video, position: $0.1) }
            .first

        guard let dev,
              let inp = try? AVCaptureDeviceInput(device: dev),
              session.canAddInput(inp) else {
            lastError = "无法初始化相机"
            return
        }
        session.addInput(inp)
        device = dev
        input  = inp

        if session.canAddOutput(photoOutput) {
            session.addOutput(photoOutput)
        }
        photoOutput.maxPhotoQualityPrioritization = .quality
    }

    // ---------------------------------------------------------------
    // Apply the AI plan
    // ---------------------------------------------------------------

    /// Push the AI-derived plan into AVCaptureDevice. Each step is
    /// individually try'd so a partial failure (e.g. zoom out of range)
    /// doesn't roll back the whole apply.
    func apply(plan: IphoneApplyPlan) async {
        guard let dev = device else {
            lastError = "相机尚未就绪"
            return
        }
        activePlan = plan

        // Hand the work to a non-isolated helper so we can call into
        // AVCaptureDevice off the main actor without a Sendable warning
        // about the @MainActor-isolated `self`.
        let result = await Self.applyPlanOnDevice(dev, plan: plan)
        if let err = result {
            lastError = "应用参数失败: \(err.localizedDescription)"
        }
    }

    nonisolated private static func applyPlanOnDevice(
        _ dev: AVCaptureDevice, plan: IphoneApplyPlan
    ) async -> Error? {
        do {
            try dev.lockForConfiguration()
            defer { dev.unlockForConfiguration() }

            // Zoom — clamp into the device's actual range. The triple-
            // camera reports its real lower bound (often 0.5 on iPhones
            // with an ultra-wide module) via ``minAvailableVideoZoomFactor``.
            let zoomMin = dev.minAvailableVideoZoomFactor
            let zoomMax = dev.maxAvailableVideoZoomFactor
            let zoomTarget = max(zoomMin, min(CGFloat(plan.zoomFactor), zoomMax))
            dev.videoZoomFactor = zoomTarget

            // ISO + shutter via custom exposure.
            let activeFmt = dev.activeFormat
            let isoTarget = max(activeFmt.minISO,
                                min(Float(plan.iso), activeFmt.maxISO))
            let requested = CMTime(seconds: plan.shutterSeconds, preferredTimescale: 1_000_000)
            let durTarget = CMTimeMaximum(activeFmt.minExposureDuration,
                                          CMTimeMinimum(requested, activeFmt.maxExposureDuration))
            dev.setExposureModeCustom(duration: durTarget,
                                      iso: isoTarget) { _ in }

            // EV bias.
            let bias = max(dev.minExposureTargetBias,
                           min(Float(plan.evCompensation), dev.maxExposureTargetBias))
            dev.setExposureTargetBias(bias) { _ in }

            // White balance — convert Kelvin to RGB gains via a
            // chromaticity round-trip the device can validate.
            if dev.isWhiteBalanceModeSupported(.locked) {
                let temp = AVCaptureDevice.WhiteBalanceTemperatureAndTintValues(
                    temperature: Float(plan.whiteBalanceK), tint: 0
                )
                let gains = dev.deviceWhiteBalanceGains(for: temp)
                let clamped = clampGains(gains, max: dev.maxWhiteBalanceGain)
                dev.setWhiteBalanceModeLocked(with: clamped) { _ in }
            }
            return nil
        } catch {
            return error
        }
    }

    nonisolated private static func clampGains(
        _ g: AVCaptureDevice.WhiteBalanceGains, max: Float
    ) -> AVCaptureDevice.WhiteBalanceGains {
        // RGB gains must be in [1.0, maxWhiteBalanceGain].
        AVCaptureDevice.WhiteBalanceGains(
            redGain:   min(Swift.max(g.redGain, 1.0), max),
            greenGain: min(Swift.max(g.greenGain, 1.0), max),
            blueGain:  min(Swift.max(g.blueGain, 1.0), max)
        )
    }

    // ---------------------------------------------------------------
    // Tap-to-focus
    // ---------------------------------------------------------------

    /// ``point`` is in normalized device coordinates (0..1, top-left).
    /// Maps to the device's focusPointOfInterest space and re-locks
    /// AE/AF on that subject.
    func tapToFocus(at point: CGPoint) async {
        guard let dev = device else { return }
        do {
            try dev.lockForConfiguration()
            defer { dev.unlockForConfiguration() }
            if dev.isFocusPointOfInterestSupported {
                dev.focusPointOfInterest = point
                dev.focusMode = .autoFocus
            }
            if dev.isExposurePointOfInterestSupported {
                dev.exposurePointOfInterest = point
                dev.exposureMode = .autoExpose
            }
        } catch {
            lastError = "对焦失败: \(error.localizedDescription)"
        }
    }

    // ---------------------------------------------------------------
    // Capture
    // ---------------------------------------------------------------

    func capturePhoto() async throws -> URL {
        let settings = AVCapturePhotoSettings()
        settings.flashMode = .off
        settings.photoQualityPrioritization = .quality

        return try await withCheckedThrowingContinuation { cont in
            self.captureContinuation = cont
            self.photoOutput.capturePhoto(with: settings, delegate: self)
        }
    }

    /// Copies the most recent capture into the system Photos library.
    /// Requires ``NSPhotoLibraryAddUsageDescription`` in Info.plist.
    func saveLastToPhotosLibrary() async throws {
        guard let url = lastCapturedURL else {
            throw NSError(
                domain: "ShootingCamera",
                code: -1,
                userInfo: [NSLocalizedDescriptionKey: "还没有拍下照片"]
            )
        }
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else {
            throw NSError(
                domain: "ShootingCamera",
                code: -2,
                userInfo: [NSLocalizedDescriptionKey: "未授权写入相册"]
            )
        }
        try await PHPhotoLibrary.shared().performChanges({
            let req = PHAssetCreationRequest.forAsset()
            req.addResource(with: .photo, fileURL: url, options: nil)
        })
    }

    // ---------------------------------------------------------------
    // Permissions
    // ---------------------------------------------------------------

    private static func requestCameraAccess() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:        return true
        case .notDetermined:     return await AVCaptureDevice.requestAccess(for: .video)
        case .denied, .restricted: return false
        @unknown default:        return false
        }
    }

    // ---------------------------------------------------------------
    // KVO of live values for the HUD chips
    // ---------------------------------------------------------------

    private var observers: [NSKeyValueObservation] = []

    private func startKVO() {
        guard let dev = device else { return }
        stopKVO()
        observers.append(dev.observe(\.iso, options: [.initial, .new]) { [weak self] d, _ in
            Task { @MainActor in self?.live.iso = d.iso }
        })
        observers.append(dev.observe(\.exposureDuration, options: [.initial, .new]) { [weak self] d, _ in
            Task { @MainActor in
                self?.live.shutterSeconds = CMTimeGetSeconds(d.exposureDuration)
            }
        })
        observers.append(dev.observe(\.exposureTargetBias, options: [.initial, .new]) { [weak self] d, _ in
            Task { @MainActor in self?.live.ev = d.exposureTargetBias }
        })
        observers.append(dev.observe(\.videoZoomFactor, options: [.initial, .new]) { [weak self] d, _ in
            Task { @MainActor in self?.live.zoomFactor = Float(d.videoZoomFactor) }
        })
    }

    private func stopKVO() {
        observers.forEach { $0.invalidate() }
        observers = []
    }
}

// MARK: - Live values
struct LiveCameraValues: Equatable {
    var iso: Float?
    var shutterSeconds: Double?
    var ev: Float?
    var zoomFactor: Float?
}

// MARK: - AVCapturePhotoCaptureDelegate
extension ShootingCameraController: AVCapturePhotoCaptureDelegate {
    nonisolated func photoOutput(_ output: AVCapturePhotoOutput,
                                 didFinishProcessingPhoto photo: AVCapturePhoto,
                                 error: Error?) {
        if let error {
            Task { @MainActor in
                self.captureContinuation?.resume(throwing: error)
                self.captureContinuation = nil
            }
            return
        }
        guard let data = photo.fileDataRepresentation() else {
            Task { @MainActor in
                self.captureContinuation?.resume(
                    throwing: NSError(
                        domain: "ShootingCamera", code: -3,
                        userInfo: [NSLocalizedDescriptionKey: "无法读取拍摄数据"]
                    )
                )
                self.captureContinuation = nil
            }
            return
        }
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Captures", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("shot_\(Int(Date().timeIntervalSince1970)).jpg")
        do {
            try data.write(to: url)
            Task { @MainActor in
                self.lastCapturedURL = url
                self.captureContinuation?.resume(returning: url)
                self.captureContinuation = nil
            }
        } catch {
            Task { @MainActor in
                self.captureContinuation?.resume(throwing: error)
                self.captureContinuation = nil
            }
        }
    }
}
