import Combine
import CoreMotion
import Foundation

/// Wraps CMMotionManager to expose a continuously-updating heading
/// (yaw / azimuth in degrees, 0..360) plus pitch and roll.
@MainActor
final class HeadingTracker: ObservableObject {
    @Published private(set) var azimuthDeg: Double = 0
    @Published private(set) var pitchDeg: Double = 0
    @Published private(set) var rollDeg: Double = 0
    @Published private(set) var isRunning = false
    @Published private(set) var coveredAngles: Set<Int> = []  // bucketed at 30 deg

    private let manager = CMMotionManager()

    func start() {
        guard manager.isDeviceMotionAvailable else { return }
        manager.deviceMotionUpdateInterval = 1.0 / 30.0
        manager.startDeviceMotionUpdates(using: .xMagneticNorthZVertical, to: .main) { [weak self] motion, _ in
            guard let self, let m = motion else { return }
            let yaw = m.attitude.yaw
            var deg = (yaw * 180.0 / .pi)
            deg = (deg + 360).truncatingRemainder(dividingBy: 360)
            self.azimuthDeg = deg
            self.pitchDeg = m.attitude.pitch * 180.0 / .pi
            self.rollDeg = m.attitude.roll * 180.0 / .pi
            let bucket = Int(deg / 30) * 30
            self.coveredAngles.insert(bucket)
        }
        isRunning = true
    }

    func stop() {
        if manager.isDeviceMotionActive {
            manager.stopDeviceMotionUpdates()
        }
        isRunning = false
    }

    func reset() {
        coveredAngles.removeAll()
    }

    var coverageProgress: Double {
        Double(coveredAngles.count) / 12.0  // 12 buckets of 30 deg
    }
}
