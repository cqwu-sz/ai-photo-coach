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
final class AlignmentMachine: ObservableObject {
enum Status: String, CaseIterable {
        case ok, warn, bad, disabled
    }

struct DimensionState {
var status: Status = .bad
var value: Double? = nil
var hint: String = ""
    }

struct AggregateState {
var heading: DimensionState
var pitch: DimensionState
var distance: DimensionState
var person: DimensionState
var allOK: Bool
var worst: (label: String, hint: String, status: Status)
    }

struct Targets {
let azimuthDeg: Double
let pitchDeg: Double
let distanceM: Double
    }

struct Tolerances {
let headingOk: Double
let headingWarn: Double
let pitchOk: Double
let pitchWarn: Double
/// P3-alignment-pitch — four-tier pitch tolerance. The classic
/// ``pitchOk`` / ``pitchWarn`` thresholds remain authoritative for
/// the green-light state machine, but ``pitchNear`` / ``pitchFar``
/// add two coarser bands so we can surface "微调一点点" vs "差很多"
/// copy without overpromising. ``pitchNear`` should be > ``pitchOk``
/// and < ``pitchWarn``; ``pitchFar`` should be > ``pitchWarn``.
let pitchNear: Double
let pitchFar: Double
let distanceOkM: Double
let distanceWarnM: Double
let holdTime: TimeInterval

static let `default` = Tolerances(
            headingOk: 4.0, headingWarn: 12.0,
            pitchOk: 5.0, pitchWarn: 12.0,
            pitchNear: 8.0, pitchFar: 20.0,
            distanceOkM: 0.25, distanceWarnM: 0.6,
            holdTime: 0.7
        )
    }

    /// Coarse pitch alignment classification surfaced to the UI for
    /// nicer copy. ``onTarget`` ⊆ ``Status.ok``; ``slight`` ⊆ ``.warn``;
    /// ``noticeable`` and ``severe`` partition ``.bad``.
    enum PitchTier: String { case onTarget, slight, noticeable, severe }

    /// Last computed pitch tier. ``nil`` until ``update(pitchDeg:)``
    /// has been called with a non-nil value at least once.
    @Published private(set) var pitchTier: PitchTier?

    @Published private(set) var state: AggregateState
let targets: Targets
let tol: Tolerances

var onGreenLight: (() -> Void)?
    /// P3-strong-3 — fired exactly once the moment ``allOK`` first
    /// flips true (the green-light edge), carrying the |pitchDelta|
    /// at that instant and its tier. Used by callers to pipe a
    /// ``FeedbackUploader.recordAlignmentPitch`` sample so the
    /// 8°/20° thresholds can be calibrated from real users instead
    /// of being a guess. Not fired again until ``allOK`` drops and
    /// rises again.
    var onGreenLightPitch: ((Double, PitchTier) -> Void)?

    private var greenSince: Date?
    private var lastFiredGreen = false

init(targets: Targets, tolerances: Tolerances = .default) {
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

    /// Force a dimension into ``.disabled`` so the green-light aggregator
    /// stops waiting on it. Used for scenery shots where we don't need a
    /// person in frame.
    enum Dimension { case heading, pitch, distance, person }

    func disable(dimension: Dimension) {
        switch dimension {
        case .heading:
            state.heading.status = .disabled
            state.heading.hint = "方位不参与对位"
        case .pitch:
            state.pitch.status = .disabled
            state.pitch.hint = "仰角不参与对位"
        case .distance:
            state.distance.status = .disabled
            state.distance.hint = "距离不参与对位"
        case .person:
            state.person.status = .disabled
            state.person.hint = "无需入框"
        }
        recompute()
    }

    // ---- inputs ---------------------------------------------------------------

func update(headingDeg: Double?) {
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

func update(pitchDeg: Double?) {
        var d = state.pitch
        if let p = pitchDeg {
            let delta = p - targets.pitchDeg
            d.value = delta
            d.status = classify(abs(delta), ok: tol.pitchOk, warn: tol.pitchWarn)
            let tier = classifyPitchTier(abs(delta))
            self.pitchTier = tier
            d.hint = pitchHint(delta, tier: tier)
        } else {
            d.status = .disabled
            d.hint = "仰角不可用"
            self.pitchTier = nil
        }
        state.pitch = d
        recompute()
    }

    private func classifyPitchTier(_ absDelta: Double) -> PitchTier {
        if absDelta <= tol.pitchOk { return .onTarget }
        if absDelta <= tol.pitchNear { return .slight }
        if absDelta <= tol.pitchFar { return .noticeable }
        return .severe
    }

func update(distanceM: Double?) {
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

func update(personPresent: Bool?) {
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
                if let pd = state.pitch.value, let tier = pitchTier {
                    onGreenLightPitch?(abs(pd), tier)
                }
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

    private func pitchHint(_ delta: Double, tier: PitchTier) -> String {
        let mag = Int(round(abs(delta)))
        let direction = delta > 0 ? "抬高" : "放低"
        switch tier {
        case .onTarget:
            return "仰角 OK"
        case .slight:
            return "再\(direction)一点点"
        case .noticeable:
            return "手机\(direction) \(mag)°"
        case .severe:
            return "手机\(direction) \(mag)°（差距较大）"
        }
    }

    private func distanceHint(_ delta: Double) -> String {
        if abs(delta) <= tol.distanceOkM { return "距离 OK" }
        if delta > 0 { return String(format: "再走近 %.1f m", abs(delta)) }
        return String(format: "再退后 %.1f m", abs(delta))
    }
}
