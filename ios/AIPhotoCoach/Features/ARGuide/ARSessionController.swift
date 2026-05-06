import ARKit
import RealityKit
import SceneKit
import Combine
import simd

/// Wraps an ARKit session so the SwiftUI ARGuideView only has to listen
/// for high-level events (heading/pitch/distance/personPresent) and
/// place the virtual avatar in world space.
///
/// Tries to enable scene reconstruction (LiDAR mesh) when supported —
/// gracefully falls back to plane-only when on a non-LiDAR device.
@MainActor
public final class ARSessionController: NSObject, ObservableObject, ARSessionDelegate {
    public let scene = SCNScene()
    public let session = ARSession()

    @Published public private(set) var hasLiDAR: Bool = false
    @Published public private(set) var personDetected: Bool = false
    @Published public private(set) var distanceM: Double? = nil
    @Published public private(set) var headingDeg: Double? = nil
    @Published public private(set) var pitchDeg: Double? = nil

    private(set) var avatarRoot: SCNNode? = nil
    private var avatarAnchor: ARAnchor? = nil

    /// Target azimuth (relative to the user's initial heading at session
    /// start) and distance the avatar should be placed at.
    public var target: AlignmentMachine.Targets?

    public override init() {
        super.init()
        session.delegate = self
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.isLightEstimationEnabled = true
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
            hasLiDAR = true
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics([.bodyDetection]) {
            config.frameSemantics.insert(.bodyDetection)
        }
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
    }

    public func placeAvatar(style: AvatarStyle, pose: PoseSuggestion?, target: AlignmentMachine.Targets) {
        self.target = target
        // Build the avatar
        let built = AvatarBuilderSCN.build(style)
        let preset: String = pose?.persons.first.map(PosePresets.pick) ?? "standing"
        PosePresets.apply(preset, joints: built.joints)
        if let p = pose?.persons.first {
            built.facePlate.geometry?.firstMaterial?.diffuse.contents =
                ExpressionRenderer.render(PosePresets.classifyExpression(p), style: style)
        }

        // Position relative to the camera origin: forward = -Z by ARKit
        // convention (camera looks toward -Z when at identity).
        let az = Float(target.azimuthDeg) * .pi / 180
        let dist = Float(target.distanceM)
        let x = sin(az) * dist
        let z = -cos(az) * dist
        let transform = matrix_identity_float4x4
        var t = transform
        t.columns.3 = SIMD4<Float>(x, 0, z, 1)

        // Yaw to face back toward camera origin
        let yaw = atan2(-x, -z)
        let cy = cos(yaw), sy = sin(yaw)
        var rot = matrix_identity_float4x4
        rot.columns.0 = SIMD4<Float>(cy, 0, -sy, 0)
        rot.columns.2 = SIMD4<Float>(sy, 0, cy, 0)
        let final = simd_mul(t, rot)

        let anchor = ARAnchor(name: "aphc.avatar", transform: final)
        if let prev = avatarAnchor { session.remove(anchor: prev) }
        session.add(anchor: anchor)
        avatarAnchor = anchor

        // Mount the SCNNode directly under the scene's rootNode at the
        // anchor's transform — the SCNView uses the same world space.
        if let root = avatarRoot { root.removeFromParentNode() }
        let container = SCNNode()
        container.simdTransform = final
        container.addChildNode(built.root)
        scene.rootNode.addChildNode(container)
        avatarRoot = container
    }

    // ---- ARSessionDelegate ----------------------------------------------------

    nonisolated public func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let cam = frame.camera
        // ARKit eulerAngles: pitch (x), yaw (y), roll (z). The camera
        // y-rotation gives compass-like heading once we map identity to
        // 0° (the user's starting orientation = the AI's reference).
        let yaw = cam.eulerAngles.y // radians
        let pitch = cam.eulerAngles.x

        // Map yaw radians -> 0..360°. Negative yaw because ARKit -Z is
        // forward and azimuth grows clockwise looking down.
        var heading = -Double(yaw) * 180.0 / .pi
        heading = (heading.truncatingRemainder(dividingBy: 360) + 360)
            .truncatingRemainder(dividingBy: 360)

        // Body detection
        let person = frame.detectedBody != nil

        // Distance: prefer LiDAR ray-cast forward; fall back to body
        // anchor depth from feature points.
        let dist = self.estimateDistance(frame: frame)

        Task { @MainActor in
            self.headingDeg = heading
            self.pitchDeg = Double(pitch) * 180.0 / .pi
            self.personDetected = person
            self.distanceM = dist
        }
    }

    nonisolated private func estimateDistance(frame: ARFrame) -> Double? {
        // Prefer the body-anchor depth when ARKit detected a person —
        // it's the most relevant distance for a "subject is X meters
        // away" UX signal.
        if let bodyAnchor = frame.anchors.first(where: { $0 is ARBodyAnchor }) as? ARBodyAnchor {
            let bodyPos = bodyAnchor.transform.columns.3
            let camPos = frame.camera.transform.columns.3
            let dx = bodyPos.x - camPos.x
            let dy = bodyPos.y - camPos.y
            let dz = bodyPos.z - camPos.z
            return Double(sqrt(dx * dx + dy * dy + dz * dz))
        }
        // Otherwise: mid-screen ray-cast against estimated planes —
        // gives the distance to the floor / wall that the camera is
        // pointed at, which is a fine approximation when the user is
        // pointing roughly at where the subject will stand.
        let q = ARRaycastQuery(
            origin: SIMD3<Float>(frame.camera.transform.columns.3.x,
                                 frame.camera.transform.columns.3.y,
                                 frame.camera.transform.columns.3.z),
            direction: simd_normalize(SIMD3<Float>(
                -frame.camera.transform.columns.2.x,
                -frame.camera.transform.columns.2.y,
                -frame.camera.transform.columns.2.z,
            )),
            allowing: .estimatedPlane,
            alignment: .any
        )
        guard let hit = session.raycast(q).first else { return nil }
        let dx = hit.worldTransform.columns.3.x - frame.camera.transform.columns.3.x
        let dy = hit.worldTransform.columns.3.y - frame.camera.transform.columns.3.y
        let dz = hit.worldTransform.columns.3.z - frame.camera.transform.columns.3.z
        return Double(sqrt(dx * dx + dy * dy + dz * dz))
    }
}
