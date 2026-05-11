// ShotParameterHUD.swift (W8.4)
//
// Floating HUD comparing the *current* AVCaptureDevice settings to the
// recommended values from the analyze response. Each chip turns red
// when the live value is far from the target.

import SwiftUI

struct ShotParameterHUD: View {
    struct Live: Equatable {
        var zoomFactor: Float
        var ev: Float
        var subjectDistanceM: Float?
    }
    let live: Live
    let target: IphoneApplyPlan?
    let recommendedDistanceM: Float?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            chip("Zoom",
                 current: String(format: "%.1fx", live.zoomFactor),
                 target: target.map { String(format: "%.1fx", $0.zoomFactor) },
                 ok: zoomOk)
            chip("EV",
                 current: String(format: "%+.1f", live.ev),
                 target: target.map { String(format: "%+.1f", Double($0.evCompensation)) },
                 ok: evOk)
            if let want = recommendedDistanceM {
                chip("距离",
                     current: live.subjectDistanceM.map { String(format: "%.1f m", $0) } ?? "—",
                     target: String(format: "%.1f m", want),
                     ok: distanceOk(want: want))
            }
        }
        .padding(10)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
    }

    private func chip(_ label: String, current: String, target: String?, ok: Bool) -> some View {
        HStack(spacing: 6) {
            Text(label)
                .font(.caption2.weight(.bold))
                .foregroundStyle(.secondary)
            Text(current)
                .font(.caption.weight(.semibold))
                .foregroundStyle(ok ? .primary : .red)
            if let t = target {
                Text("→ \(t)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var zoomOk: Bool {
        guard let t = target else { return true }
        return abs(Double(live.zoomFactor) - t.zoomFactor) < 0.25
    }
    private var evOk: Bool {
        guard let t = target else { return true }
        return abs(Double(live.ev) - t.evCompensation) < 0.5
    }
    private func distanceOk(want: Float) -> Bool {
        guard let cur = live.subjectDistanceM else { return false }
        return abs(cur - want) < 0.5
    }
}
