import AVFoundation
import Photos
import SwiftUI
import UIKit

/// Real shoot screen — opens after the user taps "按此方案拍" on a
/// recommended shot. It:
///   1) Applies the backend's ``IphoneApplyPlan`` to AVCaptureDevice.
///   2) Renders a live viewfinder with composition guides + an
///      alignment HUD (azimuth + pitch deltas vs the AI target).
///   3) Pulses a green border + haptic when everything is in range.
///   4) Big shutter button → save to App sandbox; "保存到相册" copies
///      the latest capture into the Photos library on demand.
struct ShootView: View {
    let shot: ShotRecommendation
    /// v18 — backend pk of the analyze that produced this shot. Lets
    /// the screen mark it captured + collect satisfaction signal.
    /// nil tolerated for pre-v18 server compat (old responses didn't
    /// surface `usage_record_id` in `debug`).
    let usageRecordId: String?

    @StateObject private var camera = ShootingCameraController()
    @StateObject private var align  = AlignmentTracker()
    @Environment(\.dismiss) private var dismiss

    @State private var alignState = AlignmentState()
    @State private var celebratedAlignment = false
    @State private var savedToAlbum = false
    @State private var showTipsSheet = false
    @State private var capturedURL: URL?
    /// v18 — guard against double-firing PATCH /captured if user
    /// hits shutter twice in the same session.
    @State private var capturedReported = false
    /// v18 — once user gives a thumbs answer we hide the chip and
    /// mark it confirmed. nil = not answered yet (and chip is shown).
    @State private var satisfactionAnswer: SatisfactionAnswer? = nil
    /// v18 s1 — only ask for satisfaction after the user has actually
    /// taken a few comparison shots. Asking after the very first
    /// frame interrupts the natural "shoot 3-5 then pick the best"
    /// loop. Chip appears once shotCount >= _kChipMinShots.
    @State private var shotCount: Int = 0
    /// v18 s2 — overridable from admin "运行时阈值" via a UserDefaults
    /// key `shoot.chip_min_shots`. Default 3; clamped to [1, 20].
    private var kChipMinShots: Int {
        let raw = UserDefaults.standard.integer(forKey: "shoot.chip_min_shots")
        return raw == 0 ? 3 : max(1, min(20, raw))
    }

    enum SatisfactionAnswer: Equatable { case love, ok, bad
        var isPositive: Bool { self != .bad }
        var apiBool: Bool { self != .bad }
    }

    init(shot: ShotRecommendation, usageRecordId: String? = nil) {
        self.shot = shot
        self.usageRecordId = usageRecordId
    }

    private var plan: IphoneApplyPlan? { shot.camera.iphoneApplyPlan }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            CameraPreviewView(session: camera.session)
                .ignoresSafeArea()
                .gesture(
                    SpatialTapGesture(coordinateSpace: .local)
                        .onEnded { value in
                            Task {
                                let bounds = UIScreen.main.bounds
                                let p = CGPoint(
                                    x: value.location.x / bounds.width,
                                    y: value.location.y / bounds.height,
                                )
                                await camera.tapToFocus(at: p)
                            }
                        }
                )

            CompositionOverlay(primary: shot.composition.primary)
                .allowsHitTesting(false)

            VStack(spacing: 0) {
                topBar
                Spacer()
                bottomPanel
            }
            .padding(.horizontal)
            .padding(.bottom, 8)

            if alignState.isAllAligned {
                Rectangle()
                    .stroke(Color.green, lineWidth: 6)
                    .ignoresSafeArea()
                    .transition(.opacity)
                    .allowsHitTesting(false)
            }
        }
        .preferredColorScheme(.dark)
        .navigationBarBackButtonHidden(true)
        .task {
            await camera.start()
            align.start()
            if let plan = plan {
                await camera.apply(plan: plan)
            }
        }
        .onDisappear {
            camera.stop()
            align.stop()
        }
        .onReceive(align.$azimuthDeg.combineLatest(align.$pitchDeg)) { (az, pitch) in
            updateAlignment(az: az, pitch: pitch)
        }
        .alert("拍摄出错", isPresented: .constant(camera.lastError != nil)) {
            Button("好") { /* the binding clears via `lastError` setter */ }
        } message: {
            Text(camera.lastError ?? "")
        }
        .sheet(isPresented: $showTipsSheet) {
            iphoneTipsSheet
        }
    }

    // MARK: - Top bar (close + plan summary)
    private var topBar: some View {
        HStack(alignment: .top, spacing: 12) {
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .padding(10)
                    .background(.ultraThinMaterial, in: Circle())
            }

            VStack(alignment: .leading, spacing: 4) {
                if let title = shot.title {
                    Text(title)
                        .font(.system(size: 15, weight: .heavy))
                        .foregroundStyle(.white)
                }
                if let brief = shot.coachBrief {
                    Text(brief)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(.white.opacity(0.85))
                        .lineLimit(2)
                }
            }
            .padding(.vertical, 8)
            .padding(.horizontal, 12)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))

            Spacer()

            if !shot.iphoneTips.isEmpty {
                Button {
                    showTipsSheet = true
                } label: {
                    Image(systemName: "iphone.gen3")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(10)
                        .background(.ultraThinMaterial, in: Circle())
                        .overlay(
                            Circle().stroke(Color.accentColor.opacity(0.85), lineWidth: 1.5)
                        )
                }
                .accessibilityLabel("iPhone 拍摄建议")
            }
        }
        .padding(.top, 4)
    }

    // MARK: - Bottom panel (chips + HUD ring + shutter)
    private var bottomPanel: some View {
        VStack(spacing: 12) {
            paramChips
            alignmentRing
            shutterRow
            // v18 s1 — chip appears only after the user has taken
            // a meaningful number of comparison shots (default 3).
            // This protects the natural "shoot a few, pick the best"
            // loop. User can still ignore it; we never block.
            if shotCount >= kChipMinShots,
               usageRecordId != nil,
               satisfactionAnswer == nil {
                satisfactionChip
            } else if let ans = satisfactionAnswer {
                satisfactionConfirmed(ans)
            }
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 16)
        .background(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(.ultraThinMaterial)
        )
        .animation(.easeInOut(duration: 0.18), value: satisfactionAnswer)
    }

    private var satisfactionChip: some View {
        HStack(spacing: 8) {
            Text("拍了几张了，整体满意吗？")
                .font(.system(size: 12.5, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(1).minimumScaleFactor(0.8)
            Spacer(minLength: 0)
            satisfactionButton(.love, icon: "hand.thumbsup.fill",
                                tint: Color.green)
            satisfactionButton(.ok, icon: "hand.raised.fill",
                                tint: Color.blue.opacity(0.7))
            satisfactionButton(.bad, icon: "hand.thumbsdown.fill",
                                tint: Color.gray)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.white.opacity(0.10))
        )
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private func satisfactionButton(_ ans: SatisfactionAnswer,
                                      icon: String,
                                      tint: Color) -> some View {
        Button {
            recordSatisfaction(ans)
        } label: {
            Image(systemName: icon)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(tint.opacity(0.85), in: Circle())
        }
        .accessibilityLabel(ans == .love ? "非常满意"
                              : ans == .ok ? "还行" : "不满意")
    }

    private func satisfactionConfirmed(_ ans: SatisfactionAnswer) -> some View {
        let label: String = {
            switch ans {
            case .love: return "已记录：非常满意"
            case .ok:   return "已记录：还行"
            case .bad:  return "已记录：不满意 · 我们会调整"
            }
        }()
        return HStack(spacing: 6) {
            Image(systemName: ans.isPositive ? "checkmark.circle.fill"
                                                : "checkmark.circle")
                .foregroundStyle(ans.isPositive ? Color.green : Color.gray)
            Text(label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.85))
        }
        .padding(.vertical, 4)
    }

    private func recordSatisfaction(_ ans: SatisfactionAnswer) {
        guard let id = usageRecordId else { return }
        satisfactionAnswer = ans
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        // v18 s1 — backend now persists the 3-grade signal in
        // `usage_records.satisfied_grade`. We still send the bool
        // for back-compat with the older boolean-only column.
        let grade: String = {
            switch ans {
            case .love: return "love"
            case .ok:   return "ok"
            case .bad:  return "bad"
            }
        }()
        UsageReporter.shared.markSatisfied(usageRecordId: id,
                                              satisfied: ans.apiBool,
                                              grade: grade,
                                              note: nil)
    }

    private var paramChips: some View {
        HStack(spacing: 8) {
            chip(label: "焦段", value: focalDisplay,
                 measured: live(for: .zoom),
                 inRange: true)
            chip(label: "ISO", value: plan.map { "\($0.iso)" } ?? "—",
                 measured: live(for: .iso), inRange: true)
            chip(label: "快门", value: plan?.shutterDisplay ?? "—",
                 measured: live(for: .shutter), inRange: true)
            chip(label: "EV", value: plan.map { String(format: "%+.1f", $0.evCompensation) } ?? "—",
                 measured: live(for: .ev), inRange: true)
        }
    }

    /// Compass + pitch ring. Needle points where you should turn; centre
    /// dot turns green when both axes are in tolerance.
    private var alignmentRing: some View {
        HStack(spacing: 14) {
            AlignmentRing(
                azimuthDelta: alignState.azimuthDelta,
                pitchDelta: alignState.pitchDelta,
                isAligned: alignState.isAllAligned,
            )
            .frame(width: 86, height: 86)

            VStack(alignment: .leading, spacing: 4) {
                hudRow(
                    icon: "location.north.line.fill",
                    label: "方位",
                    delta: alignState.azimuthDelta,
                    tolerance: AlignmentState.azimuthTolerance,
                )
                hudRow(
                    icon: "arrow.up.and.down",
                    label: "俯仰",
                    delta: alignState.pitchDelta,
                    tolerance: AlignmentState.pitchTolerance,
                )

                if alignState.isAllAligned {
                    Text("到位 · 可以按下快门")
                        .font(.system(size: 12, weight: .heavy))
                        .foregroundStyle(.green)
                } else {
                    Text("微调到全部转绿再按")
                        .font(.system(size: 11.5, weight: .medium))
                        .foregroundStyle(.white.opacity(0.7))
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var shutterRow: some View {
        HStack(spacing: 14) {
            Button {
                Task { await openLastInPhotos() }
            } label: {
                Image(systemName: "photo.on.rectangle.angled")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 48, height: 48)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .disabled(capturedURL == nil)
            .opacity(capturedURL == nil ? 0.4 : 1.0)

            Spacer()

            Button {
                Task { await capture() }
            } label: {
                ZStack {
                    Circle()
                        .stroke(Color.white, lineWidth: 3)
                        .frame(width: 76, height: 76)
                    Circle()
                        .fill(alignState.isAllAligned ? Color.green : Color.white)
                        .frame(width: 60, height: 60)
                }
                .shadow(color: alignState.isAllAligned ? .green : .white.opacity(0.4),
                        radius: alignState.isAllAligned ? 14 : 6)
            }

            Spacer()

            Button {
                Task { await saveLastToAlbum() }
            } label: {
                VStack(spacing: 2) {
                    Image(systemName: savedToAlbum ? "checkmark.circle.fill" : "square.and.arrow.down")
                        .font(.system(size: 16, weight: .semibold))
                    Text(savedToAlbum ? "已存" : "存相册")
                        .font(.system(size: 9.5, weight: .heavy))
                }
                .foregroundStyle(.white)
                .frame(width: 48, height: 48)
                .background(.ultraThinMaterial, in: Circle())
            }
            .disabled(capturedURL == nil)
            .opacity(capturedURL == nil ? 0.4 : 1.0)
        }
    }

    private var iphoneTipsSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let plan = plan, !plan.apertureNote.isEmpty {
                        Label {
                            Text(plan.apertureNote)
                                .font(.system(size: 13))
                                .foregroundStyle(.primary)
                        } icon: {
                            Image(systemName: "circle.lefthalf.filled.righthalf.striped.horizontal")
                                .foregroundStyle(.orange)
                        }
                        .padding(12)
                        .background(
                            RoundedRectangle(cornerRadius: 12)
                                .fill(Color.orange.opacity(0.12))
                        )
                    }

                    ForEach(Array(shot.iphoneTips.enumerated()), id: \.offset) { idx, tip in
                        HStack(alignment: .top, spacing: 10) {
                            Text("\(idx + 1)")
                                .font(.system(size: 12, weight: .heavy, design: .monospaced))
                                .foregroundStyle(Color.accentColor)
                                .frame(width: 22, height: 22)
                                .background(Circle().fill(Color.accentColor.opacity(0.15)))
                            Text(tip)
                                .font(.system(size: 14))
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("iPhone 拍摄建议")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") { showTipsSheet = false }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    // MARK: - Helpers
    private enum LiveMeasurement { case zoom, iso, shutter, ev }

    private func live(for kind: LiveMeasurement) -> String? {
        switch kind {
        case .zoom:
            guard let z = camera.live.zoomFactor else { return nil }
            return String(format: "%.1fx", z)
        case .iso:
            guard let v = camera.live.iso else { return nil }
            return "\(Int(v))"
        case .shutter:
            guard let s = camera.live.shutterSeconds, s > 0 else { return nil }
            let denom = 1.0 / s
            if denom >= 2 {
                return "1/\(Int(denom.rounded()))"
            }
            return String(format: "%.1fs", s)
        case .ev:
            guard let v = camera.live.ev else { return nil }
            return String(format: "%+.1f", v)
        }
    }

    private var focalDisplay: String {
        guard let plan = plan else { return "\(Int(shot.camera.focalLengthMm))mm" }
        return "\(plan.equivalentFocalMm)mm · \(String(format: "%.1fx", plan.zoomFactor))"
    }

    @ViewBuilder
    private func chip(label: String, value: String, measured: String?, inRange: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 9, weight: .heavy))
                .tracking(1.2)
                .foregroundStyle(.white.opacity(0.55))
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value)
                    .font(.system(size: 13, weight: .heavy))
                    .foregroundStyle(.white)
                if let measured, measured != value {
                    Text("→\(measured)")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.white.opacity(0.55))
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.white.opacity(0.08))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(inRange ? Color.green.opacity(0.55) : Color.white.opacity(0.14),
                        lineWidth: 1)
        )
        .frame(maxWidth: .infinity)
    }

    private func hudRow(icon: String, label: String, delta: Double, tolerance: Double) -> some View {
        let inRange = abs(delta) <= tolerance
        return HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(inRange ? .green : .white)
                .frame(width: 16)
            Text(label).font(.system(size: 11, weight: .heavy))
                .foregroundStyle(.white.opacity(0.85))
            Text(String(format: "%+.0f°", delta))
                .font(.system(size: 12, weight: .heavy, design: .monospaced))
                .foregroundStyle(inRange ? .green : .white)
                .frame(width: 44, alignment: .trailing)
            arrow(for: delta, axis: .horizontal)
                .font(.system(size: 12))
                .foregroundStyle(inRange ? .green : Color.accentColor)
        }
    }

    private enum AxisOrientation { case horizontal, vertical }
    private func arrow(for delta: Double, axis: AxisOrientation) -> Text {
        if abs(delta) <= 1 { return Text("●") }
        switch axis {
        case .horizontal:
            return Text(delta > 0 ? "→" : "←")
        case .vertical:
            return Text(delta > 0 ? "↑" : "↓")
        }
    }

    private func updateAlignment(az: Double, pitch: Double) {
        let azDelta = AlignmentTracker.azimuthDelta(
            measured: az, target: shot.angle.azimuthDeg
        )
        let pitchDelta = shot.angle.pitchDeg - pitch
        var newState = AlignmentState()
        newState.azimuthDelta = azDelta
        newState.pitchDelta   = pitchDelta
        alignState = newState

        if newState.isAllAligned, !celebratedAlignment {
            celebratedAlignment = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } else if !newState.isAllAligned, celebratedAlignment {
            // Re-arm so we celebrate again next time the user re-aligns.
            celebratedAlignment = false
        }
    }

    private func capture() async {
        do {
            let url = try await camera.capturePhoto()
            capturedURL = url
            savedToAlbum = false
            shotCount += 1
            UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
            // v18 — fire-and-forget tell backend this proposal was
            // actually shot. Idempotent server-side; we still gate
            // here so a chatty user doesn't burn requests.
            if !capturedReported, let id = usageRecordId {
                capturedReported = true
                UsageReporter.shared.markCaptured(usageRecordId: id)
            }
        } catch {
            camera.objectWillChange.send()
        }
    }

    private func saveLastToAlbum() async {
        do {
            try await camera.saveLastToPhotosLibrary()
            savedToAlbum = true
        } catch {
            // Surface via lastError; ShootingCameraController already
            // logs this in production builds.
        }
    }

    /// Just opens the system Photos app at the latest capture if one
    /// exists. Provides a quick "review the shot" affordance without
    /// reimplementing a previewer.
    private func openLastInPhotos() async {
        guard let url = capturedURL else { return }
        await MainActor.run {
            UIApplication.shared.open(url)
        }
    }
}

// MARK: - AlignmentRing
/// Compact compass + pitch indicator. The needle's angle is the azimuth
/// delta; the centre dot turns green when both deltas are in tolerance.
private struct AlignmentRing: View {
    let azimuthDelta: Double
    let pitchDelta: Double
    let isAligned: Bool

    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.30), lineWidth: 2)
            Circle()
                .stroke(
                    isAligned ? Color.green.opacity(0.85) : Color.accentColor.opacity(0.55),
                    lineWidth: 2
                )
                .scaleEffect(0.78)

            // Pitch hint — horizontal line that drifts up/down as needed.
            Rectangle()
                .fill(isAligned ? Color.green : Color.white.opacity(0.65))
                .frame(width: 36, height: 1.5)
                .offset(y: CGFloat(-pitchDelta) * 1.2)

            // Azimuth needle
            ZStack(alignment: .top) {
                Capsule()
                    .fill(isAligned ? Color.green : Color.accentColor)
                    .frame(width: 3, height: 36)
                Circle()
                    .fill(isAligned ? Color.green : Color.accentColor)
                    .frame(width: 8, height: 8)
                    .offset(y: -2)
            }
            .offset(y: -16)
            .rotationEffect(.degrees(azimuthDelta))
            .animation(.easeOut(duration: 0.18), value: azimuthDelta)

            Circle()
                .fill(isAligned ? Color.green : Color.white.opacity(0.85))
                .frame(width: 6, height: 6)
        }
    }
}

// MARK: - CompositionOverlay
private struct CompositionOverlay: View {
    let primary: CompositionType

    var body: some View {
        GeometryReader { geo in
            Path { p in
                let w = geo.size.width
                let h = geo.size.height
                switch primary {
                case .ruleOfThirds, .leadingLine, .negativeSpace, .goldenRatio:
                    // Rule-of-thirds grid (also a reasonable proxy for the
                    // golden ratio in a viewfinder this size).
                    p.move(to: CGPoint(x: w / 3, y: 0))
                    p.addLine(to: CGPoint(x: w / 3, y: h))
                    p.move(to: CGPoint(x: 2 * w / 3, y: 0))
                    p.addLine(to: CGPoint(x: 2 * w / 3, y: h))
                    p.move(to: CGPoint(x: 0, y: h / 3))
                    p.addLine(to: CGPoint(x: w, y: h / 3))
                    p.move(to: CGPoint(x: 0, y: 2 * h / 3))
                    p.addLine(to: CGPoint(x: w, y: 2 * h / 3))
                case .symmetry, .centered:
                    p.move(to: CGPoint(x: w / 2, y: 0))
                    p.addLine(to: CGPoint(x: w / 2, y: h))
                    p.move(to: CGPoint(x: 0, y: h / 2))
                    p.addLine(to: CGPoint(x: w, y: h / 2))
                case .frameWithinFrame:
                    let inset: CGFloat = 24
                    p.addRect(CGRect(x: inset, y: inset,
                                     width: w - 2 * inset, height: h - 2 * inset))
                case .diagonal:
                    p.move(to: CGPoint(x: 0, y: 0))
                    p.addLine(to: CGPoint(x: w, y: h))
                    p.move(to: CGPoint(x: w, y: 0))
                    p.addLine(to: CGPoint(x: 0, y: h))
                }
            }
            .stroke(Color.white.opacity(0.30), lineWidth: 0.8)
        }
    }
}
