// ARKitDepthSource.swift
//
// Optional ARKit-driven depth source. When the device supports
// ``ARWorldTrackingConfiguration.frameSemantics.smoothedSceneDepth``
// (LiDAR-equipped iPhones / iPads since iPhone 12 Pro), we strongly
// prefer it over AVCaptureDepthDataOutput because:
//   1. Per-pixel absolute distance in metres (Float32) is correct out
//      of the box — no calibration needed.
//   2. Smoothed across frames, which removes the per-pixel noise that
//      AVDepthData exhibits on dual-cam phones.
//   3. ARKit also exposes `confidenceMap` so we can mask unreliable
//      pixels (motion blur, edge of frame) before computing histograms.
//
// We keep the existing AVCaptureSession path (DepthFusion.swift) as
// the fallback for non-LiDAR phones. EnvCaptureViewModel chooses which
// source to start at session-config time and routes per-frame depth to
// the same DepthRingBuffer.
//
// This source is *headless*: it only collects depth + intrinsics. The
// RGB frames still come from VideoCaptureSession to keep the rest of
// the pipeline (Vision, MediaPipe equiv, color science) unchanged.
// On LiDAR devices we run ARSession in parallel — Apple supports this
// because both share the same back camera at the system level.

import ARKit
import CoreVideo
import Foundation

/// Whether the current device + iOS version support smoothed scene depth.
@MainActor
enum ARKitDepthSupport {
    static var isAvailable: Bool {
        // LiDAR is the gating capability; smoothedSceneDepth requires it.
        return ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth)
    }
}

@MainActor
final class ARKitDepthSource: NSObject, ARSessionDelegate {
    private let session = ARSession()
    private let onDepth: @MainActor (CVPixelBuffer, CVPixelBuffer?, Int) -> Void
    private(set) var isRunning = false
    private let startMs = Int(Date().timeIntervalSince1970 * 1000)
    /// v12 — latest device gravity-derived pitch (deg). Sampled by
    /// VideoCaptureSession into HeadedFrame so the backend horizon
    /// vote has a third source (gravity-only, independent of camera
    /// CMMotionManager which can drift). Updated every ARFrame.
    @Published private(set) var lastGravityPitchDeg: Double? = nil

    /// `onDepth(depthMap, confidenceMap, timestampMs)` is called on the
    /// main actor for each ARFrame that carries smoothed depth. The
    /// depthMap is `kCVPixelFormatType_DepthFloat32` (metres), and the
    /// confidence map is `kCVPixelFormatType_OneComponent8` with values
    /// 0=low, 1=medium, 2=high.
    init(onDepth: @escaping @MainActor (CVPixelBuffer, CVPixelBuffer?, Int) -> Void) {
        self.onDepth = onDepth
        super.init()
        session.delegate = self
    }

    func start() {
        guard ARKitDepthSupport.isAvailable, !isRunning else { return }
        let cfg = ARWorldTrackingConfiguration()
        cfg.frameSemantics = [.smoothedSceneDepth]
        cfg.planeDetection = []
        cfg.isLightEstimationEnabled = false
        session.run(cfg, options: [.resetTracking, .removeExistingAnchors])
        isRunning = true
    }

    func stop() {
        guard isRunning else { return }
        session.pause()
        isRunning = false
    }

    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        // Pull pitch from the device transform's rotation matrix. The
        // simd_float4x4 is column-major; column 2 is the camera's
        // -Z (forward) axis in world coords; its y component is sin(pitch).
        let m = frame.camera.transform
        let forwardY = m.columns.2.y    // sin(pitch_rad)
        let pitchDeg = Double(asin(forwardY)) * 180 / .pi
        let depth = frame.smoothedSceneDepth ?? frame.sceneDepth
        let depthMap = depth?.depthMap
        let confMap = depth?.confidenceMap
        let ts = Int(frame.timestamp * 1000)
        Task { @MainActor [onDepth] in
            self.lastGravityPitchDeg = pitchDeg
            if let depthMap {
                onDepth(depthMap, confMap, ts)
            }
        }
    }
}
