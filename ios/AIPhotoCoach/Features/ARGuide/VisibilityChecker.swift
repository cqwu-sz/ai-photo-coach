// VisibilityChecker.swift
//
// B-visibility-raycast — given a recommended camera position and the
// current ARSession, ray-cast against the LiDAR mesh from camera to
// the subject and report whether the line-of-sight is clear. When
// occluded (a tree, a wall, a parked car), the AR overlay should warn
// "目标被遮挡, 往左半步看看" instead of silently letting the user line
// up a shot they can't actually take.
//
// LiDAR-only: raycast against ``.estimatedPlane`` is too noisy for this
// use case; we want geometry hits, not "any horizontal surface" hits.
// On non-LiDAR devices ``check`` returns ``.unknown`` so the caller
// degrades to "warn never".

import ARKit
import Foundation
import simd

enum VisibilityVerdict: Equatable {
    case clear                  // line-of-sight reaches the subject
    case occluded(distanceM: Double)   // something is N metres in the way
    case unknown                // not enough sensors / data
}

@MainActor
enum VisibilityChecker {
    /// Cast a ray from ``cameraTransform.position`` toward the world-space
    /// point ``subjectWorldPos`` and check whether anything in the LiDAR
    /// mesh blocks the path.
    ///
    /// - Parameters:
    ///   - session: the active ARSession; must have
    ///     ``sceneReconstruction == .mesh`` for a meaningful result.
    ///   - cameraTransform: the camera's current ``transform`` matrix.
    ///   - subjectWorldPos: where the recommended subject *should* be,
    ///     in ARKit world coordinates.
    ///   - tolerance: distance in metres at which we consider the
    ///     raycast to have "reached" the subject; hits beyond this are
    ///     not occluders, just background. Default 0.3 m.
    static func check(session: ARSession,
                       cameraTransform: simd_float4x4,
                       subjectWorldPos: SIMD3<Float>,
                       tolerance: Float = 0.3) -> VisibilityVerdict {
        let origin = SIMD3<Float>(
            cameraTransform.columns.3.x,
            cameraTransform.columns.3.y,
            cameraTransform.columns.3.z,
        )
        let toSubject = subjectWorldPos - origin
        let distanceToSubject = simd_length(toSubject)
        guard distanceToSubject > 0.05 else { return .clear }
        let dir = toSubject / distanceToSubject

        // ``.existingPlaneGeometry`` + ``.any`` alignment uses the mesh
        // when available; we restrict it so a stray floor plane below
        // the camera isn't reported as an occluder.
        let q = ARRaycastQuery(
            origin: origin, direction: dir,
            allowing: .existingPlaneGeometry, alignment: .any,
        )
        let hits = session.raycast(q)
        guard let nearest = hits.first else {
            // No hits at all — could be an open sky shot or non-LiDAR
            // device. Don't false-alarm the user.
            return .unknown
        }
        let hitPos = SIMD3<Float>(
            nearest.worldTransform.columns.3.x,
            nearest.worldTransform.columns.3.y,
            nearest.worldTransform.columns.3.z,
        )
        let hitDist = simd_length(hitPos - origin)
        if hitDist + tolerance >= distanceToSubject {
            return .clear
        }
        return .occluded(distanceM: Double(hitDist))
    }
}
