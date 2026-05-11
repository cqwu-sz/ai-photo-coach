// WalkSegmentRecorder.swift
//
// Optional 10-20 s walk recorded *after* the standing pan finishes,
// used by the backend's three-source position fusion to unlock far
// (50 m+) shot candidates that the relative polar coords can't express.
//
// We run a dedicated ``ARSession`` with ``ARWorldTrackingConfiguration``
// (no scene depth needed — we only want VIO ``camera.transform``) and
// sample the camera pose at 10 Hz. Origin = the first frame, so the
// resulting ``WalkSegment`` is in metres relative to where the user
// started; the backend rotates by ``initial_heading_deg`` and converts
// to (lat, lon) using the user's GeoFix.
//
// This recorder is intentionally headless: it does NOT show a preview
// (the standing-pan camera UI stays up) and it does NOT capture frames.
// The user is asked to simply walk while their phone tracks itself.

import ARKit
import Foundation
import simd

@MainActor
final class WalkSegmentRecorder: NSObject, ARSessionDelegate {
    /// True iff this device supports the world-tracking config we use
    /// (every iPhone since the 6s — practically always).
    static var isAvailable: Bool {
        return ARWorldTrackingConfiguration.isSupported
    }

    private let session = ARSession()
    private let initialHeadingDeg: Double?
    private let sampleIntervalMs: Int
    private var startMs: Int = 0
    private var lastSampleMs: Int = 0
    private(set) var poses: [WalkPose] = []
    private(set) var isRecording = false

    /// - Parameters:
    ///   - initialHeadingDeg: device compass heading at walk start (0=N,
    ///     90=E). When unknown, the backend falls back to local-ENU
    ///     coordinates (less useful for absolute lat/lon but still
    ///     ranks ok).
    ///   - sampleIntervalMs: minimum gap between two recorded poses.
    ///     100 ms (10 Hz) is plenty — the user walks ~1 m/s so each
    ///     pose is 10 cm apart.
    init(initialHeadingDeg: Double?, sampleIntervalMs: Int = 100) {
        self.initialHeadingDeg = initialHeadingDeg
        self.sampleIntervalMs = sampleIntervalMs
        super.init()
        session.delegate = self
    }

    func start() {
        guard !isRecording else { return }
        let cfg = ARWorldTrackingConfiguration()
        cfg.worldAlignment = .gravityAndHeading   // align +Z to true north
        cfg.planeDetection = []
        session.run(cfg, options: [.resetTracking, .removeExistingAnchors])
        startMs = Int(Date().timeIntervalSince1970 * 1000)
        lastSampleMs = 0
        poses.removeAll()
        isRecording = true
    }

    /// Stop and return the captured WalkSegment. Returns nil when no
    /// usable poses were recorded (user dismissed too fast or VIO never
    /// initialised).
    func stop() -> WalkSegment? {
        guard isRecording else { return nil }
        isRecording = false
        session.pause()
        guard poses.count >= 3 else { return nil }
        return WalkSegment(
            source: .arkit,
            initialHeadingDeg: initialHeadingDeg,
            poses: poses,
            sparsePoints: nil,
            // P2-12 fields — iOS recorder doesn't yet capture GPS/
            // keyframes alongside VIO. Pass nil so the backend treats
            // it the same as a pre-P2-12 client.
            gpsTrack: nil,
            keyframesB64: nil
        )
    }

    /// Total straight-line displacement from origin in metres (UI uses
    /// it to render "已经走了 N m" while recording).
    var coverageM: Double {
        guard let last = poses.last else { return 0 }
        return sqrt(last.x * last.x + last.y * last.y)
    }

    // MARK: - ARSessionDelegate

    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let nowMs = Int(frame.timestamp * 1000)
        Task { @MainActor in
            self.handleFrame(frame, nowMs: nowMs)
        }
    }

    private func handleFrame(_ frame: ARFrame, nowMs: Int) {
        let elapsed = nowMs - startMs
        if !poses.isEmpty && (nowMs - lastSampleMs) < sampleIntervalMs {
            return
        }
        lastSampleMs = nowMs
        let m = frame.camera.transform
        // Translation: ARKit world is right-handed Y-up. We want ENU
        // metres relative to the first sample, so on the very first
        // sample we anchor to (0, 0, 0); subsequent samples store the
        // delta. ARKit's gravityAndHeading alignment puts +Z toward the
        // true north pole, +X toward east, +Y up — perfect ENU already.
        let tx = Double(m.columns.3.x)
        let ty = Double(m.columns.3.y)
        let tz = Double(m.columns.3.z)

        if poses.isEmpty {
            poses.append(WalkPose(tMs: 0, x: 0, y: 0, z: 0,
                                  qx: 0, qy: 0, qz: 0, qw: 1))
            originX = tx; originY = ty; originZ = tz
            return
        }

        let dx = tx - originX
        let dz = tz - originZ
        let dy = ty - originY
        // Quaternion of camera orientation (q.xyz, q.w).
        let q = simd_quatf(m)
        // Map ARKit (x=east, y=up, z=north) to our schema (x=east,
        // y=north, z=up) so backend's _enu_to_latlon works directly.
        poses.append(WalkPose(
            tMs: elapsed,
            x: dx,
            y: -dz,    // ARKit -Z is north
            z: dy,
            qx: Double(q.imag.x), qy: Double(q.imag.y),
            qz: Double(q.imag.z), qw: Double(q.real)
        ))
    }

    private var originX: Double = 0
    private var originY: Double = 0
    private var originZ: Double = 0
}
