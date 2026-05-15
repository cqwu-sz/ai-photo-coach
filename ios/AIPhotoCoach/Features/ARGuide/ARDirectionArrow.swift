// ARDirectionArrow.swift
//
// B-ar-arrow — a small floating 3D arrow that hovers ~0.6 m in front of
// the camera and points towards the target subject position. Used when
// the user has walked away from the recommended camera spot and we want
// to nudge them back without crowding the screen with text overlays.
//
// Lifecycle:
//   let arrow = ARDirectionArrow()
//   arrow.attach(to: realityArController.arView)
//   arrow.update(targetAzimuthDeg: 78, distanceM: 4.2, camera: frame.camera)
//   arrow.detach()
//
// The arrow's *visual* magnitude (length, opacity) is bound to distance:
// far away = bigger, more urgent; on target = small + green; very close
// = hidden entirely so it doesn't block the framing preview.

import ARKit
import RealityKit
import simd
import UIKit

@MainActor
final class ARDirectionArrow {
    private weak var arView: ARView?
    private var anchor: AnchorEntity?
    private var arrowEntity: ModelEntity?
    private var bodyMaterial = SimpleMaterial(color: .systemBlue,
                                                roughness: 0.5,
                                                isMetallic: false)

    /// Attach to an ARView. Idempotent — calling twice without detach
    /// in between just keeps the existing entity.
    func attach(to view: ARView) {
        if anchor != nil { return }
        self.arView = view
        let anchor = AnchorEntity(.camera)        // follows the camera
        // NOTE: MeshResource.generateCone is iOS 18+. We're targeting
        // iOS 17, so approximate the arrow with a thin elongated box
        // (10 cm long, 5 cm wide, 2 cm thick). Visually reads as a chevron
        // once rotated and tinted; users perceive direction from the long
        // axis, not from a pointed tip.
        let box = MeshResource.generateBox(size: SIMD3<Float>(0.05, 0.10, 0.02),
                                            cornerRadius: 0.01)
        let entity = ModelEntity(mesh: box, materials: [bodyMaterial])
        // Default position: 0.6 m in front of camera, slightly below
        // centre so it doesn't cover the subject.
        entity.position = SIMD3<Float>(0, -0.05, -0.6)
        // Cone points along +Y; we want it to point along -Z (forward)
        // by default, then we'll rotate it per-frame to face the target.
        entity.orientation = simd_quatf(angle: -.pi / 2, axis: SIMD3<Float>(1, 0, 0))
        anchor.addChild(entity)
        view.scene.addAnchor(anchor)
        self.anchor = anchor
        self.arrowEntity = entity
    }

    func detach() {
        if let anchor, let arView { arView.scene.removeAnchor(anchor) }
        anchor = nil
        arrowEntity = nil
    }

    /// Update the arrow's orientation, colour and opacity.
    ///
    /// - Parameters:
    ///   - targetAzimuthDeg: 0..360, the heading the user **should** be
    ///     facing for the recommended shot (0 = N, 90 = E, ...).
    ///   - distanceM: estimated metres from the user's current standing
    ///     point to the recommended spot. Drives the urgency colour.
    ///   - camera: the current ``ARCamera`` so we can compute the user's
    ///     own heading and the *relative* yaw to spin the cone toward
    ///     the target.
    func update(targetAzimuthDeg: Double, distanceM: Double, camera: ARCamera) {
        guard let entity = arrowEntity else { return }

        // Current camera yaw (Y rotation). ARKit world: -Z forward, so
        // azimuth 0 == camera looking along -Z when yaw is 0.
        let cameraYawRad = camera.eulerAngles.y
        let cameraHeadingDeg = (-Double(cameraYawRad) * 180.0 / .pi)
            .truncatingRemainder(dividingBy: 360)
        let normalized = (cameraHeadingDeg + 360).truncatingRemainder(dividingBy: 360)
        // Relative azimuth: positive = turn right.
        var delta = targetAzimuthDeg - normalized
        if delta > 180  { delta -= 360 }
        if delta < -180 { delta += 360 }

        // Spin the cone around its local Y so it points toward the
        // target. The default orientation already points along -Z
        // (forward), so we rotate by ``delta`` around the camera-local
        // up axis. Negative because RealityKit Y-up is right-handed.
        let yawDelta = Float(-delta * .pi / 180.0)
        let baseRotation = simd_quatf(angle: -.pi / 2, axis: SIMD3<Float>(1, 0, 0))
        let spin = simd_quatf(angle: yawDelta, axis: SIMD3<Float>(0, 0, 1))
        entity.orientation = spin * baseRotation

        // Distance-driven appearance.
        let (color, opacity, hideEntirely): (UIColor, Float, Bool) = {
            if distanceM < 0.8 {
                return (.systemGreen, 0.0, true)        // we're there
            } else if distanceM < 2.0 {
                return (.systemGreen, 0.6, false)
            } else if distanceM < 5.0 {
                return (.systemBlue, 0.85, false)
            } else {
                return (.systemOrange, 1.0, false)      // far — be loud
            }
        }()
        entity.isEnabled = !hideEntirely
        bodyMaterial.color = .init(tint: color.withAlphaComponent(CGFloat(opacity)),
                                     texture: nil)
        entity.model?.materials = [bodyMaterial]

        // Larger when far, smaller when close, so the urgency lands
        // before the user has to read any number.
        let scale = Float(0.7 + min(1.5, distanceM / 5.0))
        entity.scale = SIMD3<Float>(scale, scale, scale)
    }
}
