import ARKit
import Combine
import RealityKit
import SceneKit
import SwiftUI

/// v7 — ARGuide screen.
///
/// Now built around `RealityARController` (RealityKit + ARView + USDZ
/// avatars). The legacy SCN path is kept as a fallback when the USDZ
/// asset for the picked preset isn't bundled (e.g. before
/// `scripts/glb_to_usdz.sh` is run on the dev machine).
///
/// Visual layout:
///   - ARView underlay (camera passthrough + virtual subject)
///   - AlignmentHUDOverlay (crosshair + dial + ruler + green light)
///   - Bottom coach bubble
struct ARGuideView: View {
    let shot: ShotRecommendation
    let avatarStyle: AvatarStyle
    /// User's persisted RPM avatar pick id; controls which USDZ to
    /// load. nil → fall back to a sensible default.
    let presetId: String?

    @StateObject private var realityCtrl = RealityARController()
    @StateObject private var legacyCtrl = ARSessionController()
    @StateObject private var alignment: AlignmentMachine
    @StateObject private var manifest = AvatarManifest.shared

    @State private var greenLightActive = false
    @State private var didPlace = false
    @State private var usingRealityKit = true

    private var isScenery: Bool { shot.poses.isEmpty }

    init(shot: ShotRecommendation, avatarStyle: AvatarStyle, presetId: String? = nil) {
        self.shot = shot
        self.avatarStyle = avatarStyle
        self.presetId = presetId
        let targets = AlignmentMachine.Targets(
            azimuthDeg: shot.angle.azimuthDeg,
            pitchDeg: shot.angle.pitchDeg,
            distanceM: shot.angle.distanceM,
        )
        _alignment = StateObject(wrappedValue: AlignmentMachine(targets: targets))
    }

    var body: some View {
        ZStack(alignment: .top) {
            // Underlay: AR camera passthrough.
            if usingRealityKit {
                RealityARViewContainer(controller: realityCtrl)
                    .ignoresSafeArea()
            } else {
                LegacyARViewContainer(controller: legacyCtrl)
                    .ignoresSafeArea()
            }

            // v7 — alignment HUD replaces the four StatusCards from v6.
            AlignmentHUDOverlay(
                alignment: alignment,
                target: alignment.targets,
                isScenery: isScenery,
                onShutter: handleShutter,
            )
            .ignoresSafeArea(edges: .bottom)

            // Bottom coach bubble (kept from v6 — useful context).
            VStack {
                Spacer()
                coachBubble
                    .padding(.horizontal)
                    .padding(.bottom, 6)
            }
        }
        .navigationTitle("机位 #\(displayIndex)")
        .navigationBarTitleDisplayMode(.inline)
        .task { await setupOnAppear() }
        .onChange(of: realityCtrl.headingDeg) { _, v in
            if usingRealityKit { alignment.update(headingDeg: v) }
        }
        .onChange(of: realityCtrl.pitchDeg) { _, v in
            if usingRealityKit { alignment.update(pitchDeg: v) }
        }
        .onChange(of: realityCtrl.distanceM) { _, v in
            if usingRealityKit { alignment.update(distanceM: v) }
        }
        .onChange(of: realityCtrl.personDetected) { _, v in
            if usingRealityKit { alignment.update(personPresent: v) }
        }
        .onChange(of: legacyCtrl.headingDeg) { _, v in
            if !usingRealityKit { alignment.update(headingDeg: v) }
        }
        .onChange(of: legacyCtrl.pitchDeg) { _, v in
            if !usingRealityKit { alignment.update(pitchDeg: v) }
        }
        .onChange(of: legacyCtrl.distanceM) { _, v in
            if !usingRealityKit { alignment.update(distanceM: v) }
        }
        .onChange(of: legacyCtrl.personDetected) { _, v in
            if !usingRealityKit { alignment.update(personPresent: v) }
        }
        .onChange(of: alignment.state.allOK) { _, ok in
            if !ok { greenLightActive = false }
        }
    }

    private var displayIndex: Int { 1 }

    // MARK: - Setup

    @MainActor
    private func setupOnAppear() async {
        if isScenery {
            alignment.disable(dimension: .person)
        }
        alignment.onGreenLight = {
            greenLightActive = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }

        // 1) Try RealityKit + USDZ. If it can't place the avatar (no
        //    USDZ bundled yet), fall back to the legacy SCN pipeline.
        guard !didPlace else { return }
        let payload = await manifest.load()
        let preset = presetId ?? AvatarPicker.pick(
            personIndex: 0,
            from: payload?.presets ?? [],
        )

        if isScenery {
            // No subject to place; AR view still renders for alignment.
            didPlace = true
            return
        }

        let placed = await realityCtrl.placeSubject(
            presetId: preset,
            target: alignment.targets,
            shot: shot,
            manifest: payload,
        )
        if placed {
            usingRealityKit = true
        } else {
            // Fall back to SCN pipeline; ARGuideView from v6 will work.
            usingRealityKit = false
            let pose = shot.poses.first
            legacyCtrl.placeAvatar(
                style: avatarStyle, pose: pose,
                target: alignment.targets,
            )
        }
        didPlace = true
    }

    private func handleShutter() {
        // Hand off to the real-shoot screen so the user gets the live
        // camera + auto-applied iPhone parameters.
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        // The router would normally be injected; keep the action local
        // so this view stays self-contained.
        NotificationCenter.default.post(
            name: Notification.Name("aphc.ar.shutter"), object: shot,
        )
    }

    // MARK: - Coach bubble

    private var coachBubble: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let brief = shot.coachBrief {
                Text("\u{201C}\(brief)\u{201D}")
                    .font(.headline)
            }
            Text(shot.rationale)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

// MARK: - View hosts

private struct RealityARViewContainer: UIViewRepresentable {
    let controller: RealityARController

    func makeUIView(context: Context) -> ARView { controller.arView }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

private struct LegacyARViewContainer: UIViewRepresentable {
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
