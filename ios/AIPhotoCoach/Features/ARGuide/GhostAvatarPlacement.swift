// GhostAvatarPlacement.swift
//
// Computes the AR-world transform for the Stage-2 ghost avatar based
// on a ShotRecommendation. The Avatar is mounted on a dedicated
// "tweak" AnchorEntity which is itself a child of the chosen base
// anchor (Recon3D / Geo / World). User drags only mutate the tweak
// anchor's local translation, leaving the base anchor untouched so
// the photo position parameters stay in sync with backend semantics.

import Foundation
import ARKit
import RealityKit
import simd

@MainActor
enum GhostAvatarPlacement {
    /// Position the avatar relative to the camera using the shot's
    /// recommended distance + azimuth. Y is raycast onto the nearest
    /// horizontal plane when available, otherwise pinned to the camera
    /// floor (camera y - 1.5m).
    static func initialLocalTransform(in arView: ARView,
                                      shot: ShotRecommendation) -> Transform {
        let distance = Float(max(shot.angle.distanceM, 0.5))
        // Azimuth here is the *subject's* azimuth from the camera in
        // degrees clockwise from North. We convert to a camera-local
        // forward offset on the ground plane: at az=0 the avatar sits
        // straight ahead, at az=90 to the right, etc.
        let azRad = Float(shot.angle.azimuthDeg) * .pi / 180
        let dx = sin(azRad) * distance
        let dz = -cos(azRad) * distance  // -Z is forward in RealityKit

        guard let cam = arView.session.currentFrame?.camera else {
            return Transform(translation: SIMD3<Float>(dx, 0, dz))
        }
        let camPos = cam.transform.columns.3
        var worldX = camPos.x + dx
        var worldZ = camPos.z + dz
        var worldY = camPos.y - 1.5

        // Raycast straight down to find the floor, if a horizontal
        // plane has been detected.
        let origin = SIMD3<Float>(worldX, camPos.y + 0.5, worldZ)
        let direction = SIMD3<Float>(0, -1, 0)
        let query = ARRaycastQuery(origin: origin, direction: direction,
                                   allowing: .existingPlaneInfinite,
                                   alignment: .horizontal)
        if let hit = arView.session.raycast(query).first {
            let p = hit.worldTransform.columns.3
            worldX = p.x; worldY = p.y; worldZ = p.z
        }

        // Make the avatar face the camera by rotating around Y so its
        // -Z (front) points toward the camera position.
        let toCamera = SIMD3<Float>(camPos.x - worldX, 0, camPos.z - worldZ)
        let yaw = atan2(toCamera.x, toCamera.z)
        let rot = simd_quatf(angle: yaw, axis: SIMD3<Float>(0, 1, 0))
        return Transform(scale: .one, rotation: rot,
                         translation: SIMD3<Float>(worldX, worldY, worldZ))
    }

    /// Convert a screen-space drag to a ground-plane translation delta.
    /// Used by ShotNavigationView's DragGesture to nudge the tweak
    /// anchor without re-running the full placement.
    static func groundDelta(in arView: ARView,
                            from start: CGPoint,
                            to end: CGPoint) -> SIMD3<Float>? {
        guard let s = raycastGround(in: arView, screenPoint: start),
              let e = raycastGround(in: arView, screenPoint: end)
        else { return nil }
        return SIMD3<Float>(e.x - s.x, 0, e.z - s.z)
    }

    private static func raycastGround(in arView: ARView,
                                      screenPoint: CGPoint) -> SIMD3<Float>? {
        guard let query = arView.makeRaycastQuery(
            from: screenPoint,
            allowing: .existingPlaneInfinite,
            alignment: .horizontal,
        ) else { return nil }
        guard let hit = arView.session.raycast(query).first else { return nil }
        let p = hit.worldTransform.columns.3
        return SIMD3<Float>(p.x, p.y, p.z)
    }
}
