import Combine
import CoreLocation
import CoreMotion
import Foundation

/// Reads the device's current heading + pitch (from CoreMotion) and
/// compares them with the AI's target azimuth/pitch so the HUD can show
/// 1) how far off you are, 2) when everything's "in tolerance" and the
/// green-light + haptic should fire.
///
/// Using ``CMMotionManager`` (deviceMotion) gives us a fused yaw/pitch/
/// roll that doesn't drift the way raw gyro does, and it doesn't need
/// CoreLocation (so no extra permission). The yaw is relative to the
/// magnetic north reference frame — same convention as the analyze
/// pipeline, so target/measured azimuths are directly comparable.
@MainActor
final class AlignmentTracker: ObservableObject {
    @Published private(set) var azimuthDeg: Double = 0
    @Published private(set) var pitchDeg:  Double = 0
    @Published private(set) var rollDeg:   Double = 0
    @Published private(set) var isRunning: Bool = false

    private let motion = CMMotionManager()
    private let queue  = OperationQueue()

    init() {
        queue.qualityOfService = .userInteractive
    }

    func start() {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = 1.0 / 30.0
        motion.startDeviceMotionUpdates(
            using: .xMagneticNorthZVertical, to: queue
        ) { [weak self] motion, _ in
            guard let motion else { return }
            // ``yaw`` is measured CCW from the magnetic-north x-axis, in
            // radians. Convert to compass-style 0..360 clockwise from north.
            let yawDeg = -motion.attitude.yaw * 180 / .pi
            let azimuth = (yawDeg + 360).truncatingRemainder(dividingBy: 360)
            let pitch = motion.attitude.pitch * 180 / .pi
            let roll  = motion.attitude.roll  * 180 / .pi

            Task { @MainActor in
                self?.azimuthDeg = azimuth
                self?.pitchDeg   = pitch
                self?.rollDeg    = roll
            }
        }
        isRunning = true
    }

    func stop() {
        if motion.isDeviceMotionActive {
            motion.stopDeviceMotionUpdates()
        }
        isRunning = false
    }

    /// Signed shortest-arc difference, in degrees, in [-180, 180].
    /// Positive = target is to the right (clockwise).
    static func azimuthDelta(measured: Double, target: Double) -> Double {
        let d = (target - measured + 540).truncatingRemainder(dividingBy: 360) - 180
        return d
    }
}

/// Aggregated alignment state the HUD uses to colour each axis indicator
/// and decide when to fire the green-light + haptic.
struct AlignmentState: Equatable {
    /// Signed azimuth delta in degrees ([-180, 180]); 0 = on-target.
    var azimuthDelta: Double = 0
    /// Pitch delta in degrees; positive = need to tilt up.
    var pitchDelta: Double = 0

    /// Tolerance windows. Anything inside both windows + the optional
    /// distance window counts as "in position".
    static let azimuthTolerance: Double = 6
    static let pitchTolerance:   Double = 5

    var isAzimuthInRange: Bool { abs(azimuthDelta) <= Self.azimuthTolerance }
    var isPitchInRange:   Bool { abs(pitchDelta)   <= Self.pitchTolerance }

    var isAllAligned: Bool { isAzimuthInRange && isPitchInRange }
}
