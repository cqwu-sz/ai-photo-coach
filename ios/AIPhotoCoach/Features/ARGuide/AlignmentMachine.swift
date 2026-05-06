import Foundation
import Combine

/// Swift port of web/js/alignment.js. Tracks four signals — heading,
/// pitch, distance, person-present — and fires a "green light" event
/// once all four are in their `ok` band continuously for `holdTime`.
///
/// On iOS the heading & pitch are driven by ARKit's camera transform
/// (`ARFrame.camera.eulerAngles`), distance comes from LiDAR ray-cast,
/// and person presence comes from `ARFrame.detectedBody`. See
/// `ARSessionController` for wiring.
public final class AlignmentMachine: ObservableObject {
    public enum Status: String, CaseIterable {
        case ok, warn, bad, disabled
    }

    public struct DimensionState {
        public var status: Status = .bad
        public var value: Double? = nil
        public var hint: String = ""
    }

    public struct AggregateState {
        public var heading: DimensionState
        public var pitch: DimensionState
        public var distance: DimensionState
        public var person: DimensionState
        public var allOK: Bool
        public var worst: (label: String, hint: String, status: Status)
    }

    public struct Targets {
        public let azimuthDeg: Double
        public let pitchDeg: Double
        public let distanceM: Double
    }

    public struct Tolerances {
        public let headingOk: Double
        public let headingWarn: Double
        public let pitchOk: Double
        public let pitchWarn: Double
        public let distanceOkM: Double
        public let distanceWarnM: Double
        public let holdTime: TimeInterval

        public static let `default` = Tolerances(
            headingOk: 4.0, headingWarn: 12.0,
            pitchOk: 5.0, pitchWarn: 12.0,
            distanceOkM: 0.25, distanceWarnM: 0.6,
            holdTime: 0.7
        )
    }

    @Published public private(set) var state: AggregateState
    public let targets: Targets
    public let tol: Tolerances

    public var onGreenLight: (() -> Void)?

    private var greenSince: Date?
    private var lastFiredGreen = false

    public init(targets: Targets, tolerances: Tolerances = .default) {
        self.targets = targets
        self.tol = tolerances
        self.state = AggregateState(
            heading: .init(),
            pitch: .init(),
            distance: .init(),
            person: .init(),
            allOK: false,
            worst: ("方位", "把手机转向目标方向", .bad)
        )
    }

    // ---- inputs ---------------------------------------------------------------

    public func update(headingDeg: Double?) {
        var d = state.heading
        if let h = headingDeg {
            let delta = circDelta(h, targets.azimuthDeg)
            d.value = delta
            d.status = classify(abs(delta), ok: tol.headingOk, warn: tol.headingWarn)
            d.hint = headingHint(delta)
        } else {
            d.status = .disabled
            d.hint = "方位不可用"
        }
        state.heading = d
        recompute()
    }

    public func update(pitchDeg: Double?) {
        var d = state.pitch
        if let p = pitchDeg {
            let delta = p - targets.pitchDeg
            d.value = delta
            d.status = classify(abs(delta), ok: tol.pitchOk, warn: tol.pitchWarn)
            d.hint = pitchHint(delta)
        } else {
            d.status = .disabled
            d.hint = "仰角不可用"
        }
        state.pitch = d
        recompute()
    }

    public func update(distanceM: Double?) {
        var d = state.distance
        if let m = distanceM {
            let delta = m - targets.distanceM
            d.value = delta
            d.status = classify(abs(delta), ok: tol.distanceOkM, warn: tol.distanceWarnM)
            d.hint = distanceHint(delta)
        } else {
            d.status = .disabled
            d.hint = "距离不可用"
        }
        state.distance = d
        recompute()
    }

    public func update(personPresent: Bool?) {
        var d = state.person
        if let p = personPresent {
            d.value = p ? 1 : 0
            d.status = p ? .ok : .bad
            d.hint = p ? "已入框" : "请站到画面里"
        } else {
            d.status = .disabled
            d.hint = "人物检测不可用"
        }
        state.person = d
        recompute()
    }

    // ---- aggregation ----------------------------------------------------------

    private func recompute() {
        let dims = [
            ("方位", state.heading),
            ("仰角", state.pitch),
            ("距离", state.distance),
            ("入框", state.person),
        ]

        // .disabled counts as "OK" for green-light purposes (don't block on
        // a sensor we can't read).
        let activeBlocking = dims.filter { $0.1.status != .disabled }
        let allOK = !activeBlocking.isEmpty && activeBlocking.allSatisfy { $0.1.status == .ok }

        // Worst: bad > warn > ok; among same level, first in the canonical order.
        let priority: [Status: Int] = [.bad: 3, .warn: 2, .ok: 1, .disabled: 0]
        let worstDim = dims.max { (a, b) in
            (priority[a.1.status] ?? 0) < (priority[b.1.status] ?? 0)
        } ?? dims[0]
        state.allOK = allOK
        state.worst = (worstDim.0, worstDim.1.hint, worstDim.1.status)

        // Green-light hold-time logic
        let now = Date()
        if allOK {
            if greenSince == nil { greenSince = now }
            if !lastFiredGreen,
               let s = greenSince, now.timeIntervalSince(s) >= tol.holdTime {
                lastFiredGreen = true
                onGreenLight?()
            }
        } else {
            greenSince = nil
            lastFiredGreen = false
        }
    }

    // ---- helpers --------------------------------------------------------------

    private func classify(_ abs: Double, ok: Double, warn: Double) -> Status {
        if abs <= ok { return .ok }
        if abs <= warn { return .warn }
        return .bad
    }

    private func circDelta(_ a: Double, _ b: Double) -> Double {
        var d = a - b
        d = d.truncatingRemainder(dividingBy: 360)
        if d > 180 { d -= 360 }
        if d < -180 { d += 360 }
        return d
    }

    private func headingHint(_ delta: Double) -> String {
        if abs(delta) <= tol.headingOk { return "方位 OK" }
        if delta > 0 { return "向左转 \(Int(round(abs(delta))))°" }
        return "向右转 \(Int(round(abs(delta))))°"
    }

    private func pitchHint(_ delta: Double) -> String {
        if abs(delta) <= tol.pitchOk { return "仰角 OK" }
        if delta > 0 { return "手机抬高 \(Int(round(abs(delta))))°" }
        return "手机放低 \(Int(round(abs(delta))))°"
    }

    private func distanceHint(_ delta: Double) -> String {
        if abs(delta) <= tol.distanceOkM { return "距离 OK" }
        if delta > 0 { return String(format: "再走近 %.1f m", abs(delta)) }
        return String(format: "再退后 %.1f m", abs(delta))
    }
}
