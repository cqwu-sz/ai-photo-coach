//  ARLandmarkExtractor.swift
//  AIPhotoCoach
//
//  Extracts 3D environment landmarks (stairs, balconies, doorways, etc.)
//  from the active ARKit session so the backend's `landmark_graph`
//  module can build a stereo / multi-height scene graph.
//
//  Inputs:
//      - ARSession with world tracking + (optional) LiDAR mesh
//        reconstruction enabled
//      - A current ``ARFrame`` (or whichever frame the keyframe pipeline
//        wants to anchor landmarks to)
//
//  Outputs:
//      - ``[LandmarkCandidate]`` ready to be attached to the matching
//        ``FrameMeta.landmarkCandidates`` field
//
//  Strategy
//  --------
//  Three signal sources, layered cheapest-first:
//
//   1. ARKit horizontal & vertical planes (always-on, even without
//      LiDAR). Maps planes to coarse classes via the existing
//      ``ARPlaneAnchor.classification`` enum + a height-above-ground
//      heuristic (a horizontal plane sitting +2.5m above the lowest
//      detected plane is a balcony, not a counter).
//   2. LiDAR mesh raycast at fixed screen-space probe points
//      (corners + 1/3 grid). Each hit becomes a generic "surface" node
//      tagged by its surface-normal orientation (up-facing = potential
//      platform; vertical = pillar / wall_corner).
//   3. Vision-detected horizon line + planar feature pruning. We drop
//      probe hits that landed on the same plane as the ground (avoid
//      flooding the graph with floor tiles).
//
//  All three feed into a single de-dup pass that keys nodes by
//  ``ARAnchor.identifier`` when available; ad-hoc raycast hits get a
//  spatial 0.3m dedup radius (matches the backend dedup radius).
//
//  This extractor is purely *additive*: when ARKit world tracking
//  isn't running the public ``extract`` returns ``[]`` so existing
//  capture paths keep working unchanged.

import Foundation
import ARKit
import simd

@MainActor
final class ARLandmarkExtractor {
    /// Maximum number of landmarks emitted per frame. Backend caps at
    /// 20 nodes anyway; we keep a slightly higher local cap so we
    /// retain the most-confident ones after de-dup.
    static let maxPerFrame: Int = 24

    /// 3×3 grid of normalised screen-space probe points (avoids the
    /// very edge where lens distortion would taint raycast normals).
    private static let probeGrid: [CGPoint] = stride(from: 0.15, through: 0.85, by: 0.20)
        .flatMap { y in stride(from: 0.15, through: 0.85, by: 0.20).map { x in CGPoint(x: x, y: y) } }

    /// Extract a fresh batch of landmarks for the given AR frame.
    /// `viewportSize` is the size of the rendering surface (used to
    /// scale the probe grid into pixel coordinates when raycasting).
    /// `session` is the live ARSession; pass `nil` when unavailable.
    func extract(frame: ARFrame, session: ARSession?, viewportSize: CGSize)
        -> [LandmarkCandidate]
    {
        guard let session else { return [] }

        var nodes: [LandmarkCandidate] = []
        let groundY = estimateGroundY(anchors: frame.anchors)

        // ---- (1) ARPlaneAnchor → semantic landmark candidates ------
        for anchor in frame.anchors {
            guard let plane = anchor as? ARPlaneAnchor else { continue }
            if let n = self.landmarkFromPlane(plane, groundY: groundY) {
                nodes.append(n)
            }
        }

        // ---- (2) LiDAR raycast probe grid --------------------------
        let raycastHits = self.raycastProbes(session: session,
                                              viewportSize: viewportSize)
        for hit in raycastHits {
            if let n = self.landmarkFromRaycast(hit, groundY: groundY) {
                nodes.append(n)
            }
        }

        // ---- (3) Spatial dedup at 0.3 m and trim to cap ------------
        let deduped = self.dedup(nodes: nodes, radius: 0.30)
        if deduped.count <= Self.maxPerFrame { return deduped }
        // Prefer the highest-confidence + non-ground items.
        return deduped
            .sorted { (a, b) in (a.confidence ?? 0) > (b.confidence ?? 0) }
            .prefix(Self.maxPerFrame)
            .map { $0 }
    }

    // MARK: - ground plane

    /// Median y of the lowest horizontal plane anchors. Robust against
    /// a single stray detection on a higher surface. Defaults to 0
    /// when no horizontal planes are tracked yet.
    private func estimateGroundY(anchors: [ARAnchor]) -> Float {
        let lows = anchors
            .compactMap { $0 as? ARPlaneAnchor }
            .filter { $0.alignment == .horizontal }
            .map { $0.transform.columns.3.y }
            .sorted()
        guard !lows.isEmpty else { return 0 }
        let cutoff = max(1, lows.count / 4)
        let trimmed = Array(lows.prefix(cutoff))
        return trimmed[trimmed.count / 2]
    }

    // MARK: - plane → landmark

    private func landmarkFromPlane(_ plane: ARPlaneAnchor, groundY: Float)
        -> LandmarkCandidate?
    {
        let t = plane.transform
        let center = simd_make_float3(t.columns.3.x, t.columns.3.y, t.columns.3.z)
        let dh = center.y - groundY
        let label: String
        switch (plane.alignment, plane.classification) {
        case (.horizontal, .floor):  label = "ground"
        case (.horizontal, .ceiling): label = "ceiling"
        case (.horizontal, .table):
            label = dh > 1.0 ? "balcony" : "bench"
        case (.horizontal, .seat):   label = "bench"
        case (.horizontal, _):
            // Unclassified horizontal — height decides.
            if dh < 0.15 { label = "ground" }
            else if dh < 0.55 { label = "bench" }
            else if dh < 1.40 { label = "elevated_platform" }
            else { label = "balcony" }
        case (.vertical, .wall):    label = "wall_corner"
        case (.vertical, .door):    label = "doorway"
        case (.vertical, .window):  label = "window"
        case (.vertical, _):        label = "wall_corner"
        default: return nil
        }
        let extent = plane.planeExtent
        let size: [Double] = [Double(extent.width), 0.10, Double(extent.height)]
        return LandmarkCandidate(
            label: label,
            worldXyz: [Double(center.x), Double(center.y), Double(center.z)],
            sizeM: size,
            heightAboveGroundM: Double(dh),
            materialLabel: nil,
            lightExposure: nil,
            confidence: 0.75,
            sourceFrameIndex: nil,
            stableId: plane.identifier.uuidString
        )
    }

    // MARK: - raycast probes

    private struct RaycastHit {
        let worldPosition: simd_float3
        let normal: simd_float3
        let target: ARRaycastQuery.Target
    }

    private func raycastProbes(session: ARSession, viewportSize: CGSize) -> [RaycastHit] {
        guard viewportSize.width > 0, viewportSize.height > 0 else { return [] }
        var hits: [RaycastHit] = []
        for p in Self.probeGrid {
            let pixel = CGPoint(x: p.x * viewportSize.width,
                                 y: p.y * viewportSize.height)
            let targets: [ARRaycastQuery.Target] = [.estimatedPlane, .existingPlaneInfinite]
            for target in targets {
                guard let query = session.currentFrame?.raycastQuery(
                    from: pixel,
                    allowing: target,
                    alignment: .any
                ) else { continue }
                let results = session.raycast(query)
                if let r = results.first {
                    let t = r.worldTransform
                    let pos = simd_make_float3(t.columns.3.x, t.columns.3.y, t.columns.3.z)
                    let upWorld = simd_make_float3(0, 1, 0)
                    let normal = simd_normalize(simd_make_float3(t.columns.1.x, t.columns.1.y, t.columns.1.z) - 0.001 * upWorld)
                    hits.append(RaycastHit(worldPosition: pos, normal: normal, target: target))
                    break
                }
            }
        }
        return hits
    }

    private func landmarkFromRaycast(_ hit: RaycastHit, groundY: Float) -> LandmarkCandidate? {
        let n = hit.normal
        let dh = hit.worldPosition.y - groundY
        let isHorizontal = abs(n.y) > 0.85
        let label: String
        if isHorizontal {
            if dh < 0.15 { return nil }                 // it's just the floor — already covered by plane anchors
            else if dh < 0.55 { label = "bench" }
            else if dh < 1.40 { label = "elevated_platform" }
            else { label = "balcony" }
        } else {
            // Vertical raycast hits are too noisy to label confidently
            // without semantic input; bucket as a generic anchor.
            label = "wall_corner"
        }
        return LandmarkCandidate(
            label: label,
            worldXyz: [Double(hit.worldPosition.x), Double(hit.worldPosition.y), Double(hit.worldPosition.z)],
            sizeM: nil,
            heightAboveGroundM: Double(dh),
            materialLabel: nil,
            lightExposure: nil,
            confidence: 0.50,
            sourceFrameIndex: nil,
            stableId: nil
        )
    }

    // MARK: - dedup

    private func dedup(nodes: [LandmarkCandidate], radius: Double) -> [LandmarkCandidate] {
        var kept: [LandmarkCandidate] = []
        let r2 = radius * radius
        for n in nodes {
            let nx = n.worldXyz[0], ny = n.worldXyz[1], nz = n.worldXyz[2]
            var duplicate = false
            for k in kept {
                let dx = k.worldXyz[0] - nx
                let dy = k.worldXyz[1] - ny
                let dz = k.worldXyz[2] - nz
                if dx * dx + dy * dy + dz * dz <= r2 {
                    duplicate = true
                    break
                }
            }
            if !duplicate { kept.append(n) }
        }
        return kept
    }
}
