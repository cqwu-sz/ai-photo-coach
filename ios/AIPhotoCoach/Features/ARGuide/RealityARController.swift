// v7 Phase D — RealityKit AR controller.
//
// Replaces the legacy ARSCNView pipeline with ARView + RealityKit. Uses
// USDZ avatars produced by `scripts/glb_to_usdz.sh` (see Phase B). Falls
// back to the SceneKit AvatarBuilderSCN path when a USDZ asset isn't
// bundled, so the AR guide always renders something.
//
// Public API:
//
//   let ctrl = RealityARController()
//   await ctrl.placeSubject(presetId: "male_casual_25", target: targets,
//                            shot: shot, animations: manifest)
//   ctrl.attach(to: arView)
//
// Published properties (heading, pitch, distance, personPresent) match
// the legacy ARSessionController so AlignmentMachine wiring is reusable.

import ARKit
import Combine
import RealityKit
import simd
import UIKit

@MainActor
public final class RealityARController: NSObject, ObservableObject, ARSessionDelegate {
    public let arView: ARView

    @Published public private(set) var hasLiDAR: Bool = false
    @Published public private(set) var personDetected: Bool = false
    @Published public private(set) var distanceM: Double? = nil
    @Published public private(set) var headingDeg: Double? = nil
    @Published public private(set) var pitchDeg: Double? = nil

    private var subjectAnchor: AnchorEntity?
    private var subjectEntity: Entity?
    private var animController: AnimationPlaybackController?
    public private(set) var target: AlignmentMachine.Targets?

    public override init() {
        // ARView with realistic shading for the in-app AR preview. We
        // use .nonAR initially to avoid blocking the main thread while
        // session config spins up; the run() call below switches to the
        // proper world-tracking config.
        self.arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        super.init()
        configureSession()
    }

    private func configureSession() {
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.isLightEstimationEnabled = true
        config.environmentTexturing = .automatic

        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
            self.hasLiDAR = true
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics([.bodyDetection]) {
            config.frameSemantics.insert(.bodyDetection)
        }

        arView.session.delegate = self
        arView.session.run(config, options: [.resetTracking, .removeExistingAnchors])

        // Realistic environment + IBL so the USDZ avatar reflects the
        // user's actual surroundings.
        arView.environment.lighting.intensityExponent = 1.0
        arView.environment.background = .cameraFeed()
        arView.renderOptions.remove(.disableMotionBlur)
        arView.renderOptions.remove(.disableDepthOfField)
    }

    /// Place a USDZ avatar at the recommended world-space pose.
    /// Falls back to nil when the asset isn't bundled — the caller
    /// should then fall back to the legacy SceneKit avatar.
    @discardableResult
    public func placeSubject(
        presetId: String?,
        target: AlignmentMachine.Targets,
        shot: ShotRecommendation?,
        manifest: AvatarManifestPayload?,
    ) async -> Bool {
        self.target = target

        // Tear down any previous subject so re-placement is idempotent.
        subjectAnchor.flatMap { arView.scene.removeAnchor($0) }
        subjectAnchor = nil
        subjectEntity = nil
        animController?.stop()
        animController = nil

        guard let presetId else { return false }
        let entity = try? await AvatarLoader.shared.load(presetId: presetId)
        guard let entity else { return false }

        let az = Float(target.azimuthDeg) * .pi / 180
        let dist = Float(max(0.5, target.distanceM))
        // ARKit world: -Z is forward (camera initial look direction);
        // azimuth=0 should put the subject in front of the user.
        let x = sin(az) * dist
        let z = -cos(az) * dist
        let yaw = atan2(-x, -z)

        let anchor = AnchorEntity(world: SIMD3<Float>(x, 0, z))
        anchor.transform.rotation = simd_quatf(angle: yaw, axis: SIMD3<Float>(0, 1, 0))
        anchor.addChild(entity)
        arView.scene.addAnchor(anchor)
        self.subjectAnchor = anchor
        self.subjectEntity = entity

        // Animation: pull the LLM pose id and translate to a Mixamo
        // animation id via the manifest.
        let poseId = shot?.poses.first?.id ?? shot?.poses.first?.referenceThumbnailId
        let count = shot?.poses.first?.persons.count ?? 1
        animController = await AvatarLoader.shared.playPose(
            poseId, personCount: count, on: entity,
            manifest: manifest?.poseToMixamo,
        )
        return true
    }

    /// Attach the underlying ARView to a SwiftUI UIViewRepresentable.
    public func attach(to host: ARView) {
        // No-op when the host IS the controller's arView (the typical
        // path when SwiftUI's UIViewRepresentable returns ctrl.arView).
        guard host !== arView else { return }
    }

    // MARK: - ARSessionDelegate

    public func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let cam = frame.camera
        let yaw = cam.eulerAngles.y
        let pitch = cam.eulerAngles.x

        var heading = -Double(yaw) * 180.0 / .pi
        heading = (heading.truncatingRemainder(dividingBy: 360) + 360)
            .truncatingRemainder(dividingBy: 360)

        let person = frame.detectedBody != nil
        let dist = estimateDistance(frame: frame)

        // Already on MainActor (class is @MainActor).
        self.headingDeg = heading
        self.pitchDeg = Double(pitch) * 180.0 / .pi
        self.personDetected = person
        self.distanceM = dist
    }

    private func estimateDistance(frame: ARFrame) -> Double? {
        if let bodyAnchor = frame.anchors.first(where: { $0 is ARBodyAnchor }) as? ARBodyAnchor {
            let body = bodyAnchor.transform.columns.3
            let cam = frame.camera.transform.columns.3
            let dx = body.x - cam.x, dy = body.y - cam.y, dz = body.z - cam.z
            return Double(sqrt(dx * dx + dy * dy + dz * dz))
        }
        let origin = SIMD3<Float>(
            frame.camera.transform.columns.3.x,
            frame.camera.transform.columns.3.y,
            frame.camera.transform.columns.3.z,
        )
        let direction = simd_normalize(SIMD3<Float>(
            -frame.camera.transform.columns.2.x,
            -frame.camera.transform.columns.2.y,
            -frame.camera.transform.columns.2.z,
        ))
        let q = ARRaycastQuery(
            origin: origin, direction: direction,
            allowing: .estimatedPlane, alignment: .any,
        )
        guard let hit = arView.session.raycast(q).first else { return nil }
        let h = hit.worldTransform.columns.3
        let dx = h.x - origin.x, dy = h.y - origin.y, dz = h.z - origin.z
        return Double(sqrt(dx * dx + dy * dy + dz * dz))
    }
}
