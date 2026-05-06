import ARKit
import SceneKit
import SwiftUI

/// SwiftUI wrapper around ARSCNView for the AR-guide page. Drives the
/// AlignmentMachine off the ARSessionController's published signals,
/// shows a HUD with 4 status cards, and pulses the screen green when
/// all four signals are aligned for the hold time.
struct ARGuideView: View {
    let shot: ShotRecommendation
    let avatarStyle: AvatarStyle

    @StateObject private var session = ARSessionController()
    @StateObject private var alignment: AlignmentMachine
    @State private var greenLightActive = false

    init(shot: ShotRecommendation, avatarStyle: AvatarStyle) {
        self.shot = shot
        self.avatarStyle = avatarStyle
        let targets = AlignmentMachine.Targets(
            azimuthDeg: shot.angle.azimuthDeg,
            pitchDeg: shot.angle.pitchDeg,
            distanceM: shot.angle.distanceM
        )
        _alignment = StateObject(wrappedValue: AlignmentMachine(targets: targets))
    }

    var body: some View {
        ZStack(alignment: .top) {
            ARViewContainer(controller: session)
                .ignoresSafeArea()

            okFrame
                .opacity(greenLightActive ? 1 : 0)
                .animation(.easeInOut(duration: 0.25), value: greenLightActive)

            VStack(spacing: 12) {
                statusCards
                if greenLightActive {
                    okBanner
                } else {
                    Text(alignment.state.worst.hint)
                        .font(.callout.weight(.semibold))
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(.thinMaterial, in: Capsule())
                }
                Spacer()
                coachBubble
                    .padding(.horizontal)
            }
            .padding()
        }
        .navigationTitle("机位 #\(displayIndex)")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            let pose = shot.poses.first
            session.placeAvatar(style: avatarStyle, pose: pose, target: alignment.targets)
            alignment.onGreenLight = {
                greenLightActive = true
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
        }
        .onChange(of: session.headingDeg) { _, v in alignment.update(headingDeg: v) }
        .onChange(of: session.pitchDeg) { _, v in alignment.update(pitchDeg: v) }
        .onChange(of: session.distanceM) { _, v in alignment.update(distanceM: v) }
        .onChange(of: session.personDetected) { _, v in alignment.update(personPresent: v) }
        .onChange(of: alignment.state.allOK) { _, ok in
            if !ok { greenLightActive = false }
        }
    }

    private var displayIndex: Int { 1 }

    // ---- HUD --------------------------------------------------------------

    private var statusCards: some View {
        HStack(spacing: 8) {
            StatusCard(label: "方位", state: alignment.state.heading,
                       formatter: { v in v.map { String(format: "%+.0f°", $0) } ?? "--" },
                       targetText: "目标 \(Int(round(shot.angle.azimuthDeg)))°")
            StatusCard(label: "仰角", state: alignment.state.pitch,
                       formatter: { v in v.map { String(format: "%+.0f°", $0) } ?? "--" },
                       targetText: "目标 \(Int(round(shot.angle.pitchDeg)))°")
            StatusCard(label: "距离", state: alignment.state.distance,
                       formatter: { v in v.map { String(format: "%+.1fm", $0) } ?? "--" },
                       targetText: String(format: "目标 %.1fm", shot.angle.distanceM))
            StatusCard(label: "入框", state: alignment.state.person,
                       formatter: { v in (v ?? 0) > 0 ? "✓" : "—" },
                       targetText: "≥ 1 人")
        }
    }

    private var okFrame: some View {
        Rectangle()
            .strokeBorder(Color.green, lineWidth: 6)
            .blur(radius: 1)
            .ignoresSafeArea()
    }

    private var okBanner: some View {
        HStack(spacing: 6) {
            Circle().fill(Color.green).frame(width: 10, height: 10)
            Text("全部对位 — 按下快门")
                .font(.headline)
        }
        .padding(.horizontal, 18).padding(.vertical, 10)
        .background(.regularMaterial, in: Capsule())
    }

    private var coachBubble: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let brief = shot.coachBrief {
                Text("\u{201C}\(brief)\u{201D}")
                    .font(.headline)
            }
            Text(shot.rationale)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct StatusCard: View {
    let label: String
    let state: AlignmentMachine.DimensionState
    let formatter: (Double?) -> String
    let targetText: String

    var body: some View {
        VStack(spacing: 4) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(formatter(state.value))
                .font(.headline.monospacedDigit())
                .foregroundStyle(color)
            Text(targetText).font(.caption2).foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8).padding(.horizontal, 6)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(color.opacity(0.4)))
    }

    private var color: Color {
        switch state.status {
        case .ok: return .green
        case .warn: return .orange
        case .bad: return .red
        case .disabled: return .secondary
        }
    }
}

private struct ARViewContainer: UIViewRepresentable {
    let controller: ARSessionController

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView()
        view.session = controller.session
        view.scene = controller.scene
        view.automaticallyUpdatesLighting = true
        view.antialiasingMode = .multisampling4X
        view.preferredFramesPerSecond = 60
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}
