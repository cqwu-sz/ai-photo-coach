// v7 Phase D — AR alignment HUD overlay.
//
// Layered on top of the RealityKit ARView. Renders:
//   - centred crosshair that turns green when alignment is achieved
//   - circular azimuth dial showing target heading vs current heading
//   - distance ruler at the bottom
//   - turn / step direction arrows (left / right / forward / back)
//   - a single green "READY · 拍摄" pill that sits front-and-centre once
//     all four signals lock for the hold time
//
// Independent of the controller; it just reads AlignmentMachine.state.

import SwiftUI

struct AlignmentHUDOverlay: View {
    @ObservedObject var alignment: AlignmentMachine
    let target: AlignmentMachine.Targets
    let isScenery: Bool
    var onShutter: () -> Void = {}

    var body: some View {
        ZStack {
            // 1) Soft vignette tint when far from target — pulls the
            //    user's eye to the centre crosshair as they get close.
            backgroundTint

            VStack(spacing: 0) {
                Spacer().frame(height: 80)
                // Azimuth dial — the ring around the crosshair.
                azimuthDial
                    .frame(width: 240, height: 240)
                    .overlay(crosshair)
                Spacer()
                directionHints
                Spacer().frame(height: 14)
                distanceRuler
                Spacer().frame(height: 26)
                shutterArea
                Spacer().frame(height: 90)
            }
            .padding(.horizontal, 18)
        }
        .allowsHitTesting(true)
    }

    // MARK: - 1. Background vignette

    private var backgroundTint: some View {
        let intensity = greenLight ? 0.0 : (alignment.state.allOK ? 0.05 : 0.18)
        return RadialGradient(
            colors: [.clear, .black.opacity(intensity)],
            center: .center, startRadius: 80, endRadius: 360,
        )
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }

    // MARK: - 2. Azimuth dial

    private var azimuthDial: some View {
        let curHead = alignment.state.heading.value ?? 0
        let targetHead = target.azimuthDeg
        let delta = shortestAngleDelta(curHead, target: targetHead)
        let okRange = 4.0
        let warnRange = 12.0

        return ZStack {
            // ring
            Circle()
                .stroke(Color.white.opacity(0.18), lineWidth: 1.2)

            // ok zone arc (centred at target = top of ring).
            Circle()
                .trim(from: 0.5 - okRange / 360, to: 0.5 + okRange / 360)
                .stroke(Color.green.opacity(0.7), style: StrokeStyle(lineWidth: 4, lineCap: .round))
                .rotationEffect(.degrees(-90))

            Circle()
                .trim(from: 0.5 - warnRange / 360, to: 0.5 + warnRange / 360)
                .stroke(Color.yellow.opacity(0.32), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .rotationEffect(.degrees(-90))

            // current-heading arrow at top
            // Δ° rotates the arrow around the ring; positive means we
            // need to turn right.
            Image(systemName: "arrowtriangle.down.fill")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(deltaColor(delta, ok: okRange, warn: warnRange))
                .offset(y: -120)
                .rotationEffect(.degrees(-delta), anchor: .center)
                .animation(.linear(duration: 0.06), value: delta)

            // labels
            VStack(spacing: 2) {
                Text("方位")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.7))
                Text(String(format: "%+0.0f°", delta))
                    .font(.system(size: 28, weight: .bold, design: .rounded).monospacedDigit())
                    .foregroundStyle(deltaColor(delta, ok: okRange, warn: warnRange))
                Text(String(format: "目标 %.0f°", targetHead))
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.5))
            }
            .padding(20)
        }
        .background(
            Circle().fill(.black.opacity(0.18))
                .blur(radius: 6)
        )
    }

    // MARK: - 3. Crosshair

    private var crosshair: some View {
        ZStack {
            Group {
                Rectangle().frame(width: 28, height: 1.4)
                Rectangle().frame(width: 1.4, height: 28)
            }
            .foregroundStyle(crosshairColor)

            Circle()
                .strokeBorder(crosshairColor, lineWidth: 1.4)
                .frame(width: 56, height: 56)
                .opacity(greenLight ? 1 : 0.4)
                .scaleEffect(greenLight ? 1.05 : 1.0)
                .animation(.easeInOut(duration: 0.18).repeatForever(autoreverses: true),
                           value: greenLight)
        }
    }

    private var crosshairColor: Color {
        if greenLight { return .green }
        return alignment.state.heading.status == .ok ? .green : .white
    }

    // MARK: - 4. Direction hints (turn / step)

    private var directionHints: some View {
        HStack(spacing: 10) {
            if let h = alignment.state.heading.value {
                let delta = shortestAngleDelta(h, target: target.azimuthDeg)
                if abs(delta) > 4 {
                    DirectionPill(
                        icon: delta < 0 ? "arrow.turn.up.left" : "arrow.turn.up.right",
                        text: String(format: "向%@转 %.0f°", delta < 0 ? "左" : "右", abs(delta)),
                        kind: abs(delta) > 12 ? .bad : .warn,
                    )
                }
            }
            if let p = alignment.state.pitch.value {
                let dpitch = p - target.pitchDeg
                if abs(dpitch) > 5 {
                    DirectionPill(
                        icon: dpitch > 0 ? "arrow.down" : "arrow.up",
                        text: String(format: "%@俯仰 %.0f°", dpitch > 0 ? "压低" : "抬高", abs(dpitch)),
                        kind: abs(dpitch) > 12 ? .bad : .warn,
                    )
                }
            }
        }
    }

    // MARK: - 5. Distance ruler

    private var distanceRuler: some View {
        let cur = alignment.state.distance.value ?? 0
        let tgt = target.distanceM
        let delta = cur - tgt
        let okBand = 0.25
        let warnBand = 0.6
        let pct = clamp((cur / max(0.6, tgt * 2)), 0.05, 0.95)

        return VStack(spacing: 6) {
            HStack {
                Text("距离").font(.caption2.weight(.semibold)).foregroundStyle(.white.opacity(0.7))
                Spacer()
                Text(String(format: "%.1fm  /  目标 %.1fm", cur, tgt))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(deltaColor(delta, ok: okBand, warn: warnBand))
            }
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 4).fill(.white.opacity(0.10))
                    .frame(height: 8)
                // Target marker
                RoundedRectangle(cornerRadius: 4)
                    .fill(.white.opacity(0.4))
                    .frame(width: 3, height: 14)
                    .offset(x: 0.5 * 240 - 1.5, y: 0)
                // Current marker
                Circle()
                    .fill(deltaColor(delta, ok: okBand, warn: warnBand))
                    .frame(width: 14, height: 14)
                    .offset(x: pct * 240 - 7, y: 0)
                    .animation(.easeOut(duration: 0.12), value: cur)
            }
            .frame(width: 240)
            if abs(delta) > okBand {
                Text(delta > 0 ? "向前一步" : "向后一步")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(deltaColor(delta, ok: okBand, warn: warnBand))
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 12)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    // MARK: - 6. Shutter area

    private var shutterArea: some View {
        Group {
            if greenLight {
                Button(action: onShutter) {
                    HStack(spacing: 8) {
                        Circle().fill(.white).frame(width: 10, height: 10)
                        Text("到位 · 按下拍摄")
                            .font(.headline)
                    }
                    .padding(.horizontal, 22).padding(.vertical, 12)
                    .background(
                        Capsule().fill(LinearGradient(
                            colors: [.green, .green.opacity(0.8)],
                            startPoint: .top, endPoint: .bottom,
                        ))
                    )
                    .foregroundStyle(.white)
                    .shadow(color: .green.opacity(0.45), radius: 16, y: 4)
                }
                .buttonStyle(.plain)
            } else {
                Text(alignment.state.worst.hint.isEmpty ? "请把手机举到推荐机位" : alignment.state.worst.hint)
                    .font(.callout.weight(.semibold))
                    .padding(.horizontal, 16).padding(.vertical, 9)
                    .foregroundStyle(.white)
                    .background(.ultraThinMaterial, in: Capsule())
            }
        }
    }

    private var greenLight: Bool { alignment.state.allOK }

    // MARK: - Helpers

    private func deltaColor(_ delta: Double, ok: Double, warn: Double) -> Color {
        let absD = abs(delta)
        if absD <= ok { return .green }
        if absD <= warn { return .yellow }
        return .red
    }

    private func shortestAngleDelta(_ current: Double, target: Double) -> Double {
        var d = current - target
        d = (d + 540).truncatingRemainder(dividingBy: 360) - 180
        return d
    }

    private func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double {
        max(lo, min(hi, v))
    }
}

private struct DirectionPill: View {
    enum Kind { case ok, warn, bad }
    let icon: String
    let text: String
    let kind: Kind

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 13, weight: .bold))
            Text(text).font(.caption.weight(.semibold))
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(Capsule().fill(bg))
        .foregroundStyle(fg)
    }

    private var bg: some ShapeStyle {
        switch kind {
        case .ok:   return Color.green.opacity(0.20)
        case .warn: return Color.yellow.opacity(0.22)
        case .bad:  return Color.red.opacity(0.22)
        }
    }
    private var fg: Color {
        switch kind {
        case .ok:   return .green
        case .warn: return .yellow
        case .bad:  return .red
        }
    }
}
