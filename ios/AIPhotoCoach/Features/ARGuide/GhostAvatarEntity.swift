// GhostAvatarEntity.swift
//
// AR-side wrapper around an AvatarLoader-loaded RealityKit Entity that
// renders the same digital human used in Stage-1 preview as a
// translucent "ghost" guide in Stage-2 live AR. Two render modes:
//
//   .ghost      — alpha-blended unlit material (~0.4) with optional
//                 fresnel rim if CustomMaterial compiles. Default; works
//                 on every device.
//   .solidFade  — original PBR materials kept; entity opacity ramps
//                 down to ~0.1 when a real human walks within ~1.5m
//                 (driven externally via setProximity(_:)).
//
// The entity also adds a glowing ground disc + a forward-facing arrow
// + an optional pose-thumbnail label so it doubles as a precise
// "stand here, face that way" marker even when the avatar mesh fails
// to load.

import Foundation
import RealityKit
import UIKit
import simd

@MainActor
final class GhostAvatarEntity: Entity {
    enum RenderMode: String, CaseIterable, Sendable {
        case ghost
        case solidFade
    }

    private(set) var mode: RenderMode
    /// Tint applied to the ground disc + arrow. Changes per-ghost in
    /// multi-person / privacy mode so each spot is visually distinct.
    private(set) var tint: UIColor
    /// Tint applied to the avatar mesh in `.ghost` render mode. Held
    /// separately from `tint` so the multi-person disc colour palette
    /// doesn't bleed into the avatar body — the digital human itself
    /// stays a neutral teal regardless of which spot it represents.
    private let meshTint: UIColor
    private var avatarRoot: Entity?
    private var originalMaterials: [ObjectIdentifier: [Material]] = [:]
    private var groundDisc: ModelEntity?
    private var forwardArrow: ModelEntity?

    /// Last reported real-human distance (metres). Used by .solidFade.
    private var proximityM: Float = .infinity

    init(mode: RenderMode = .ghost,
         tint: UIColor = .systemTeal,
         meshTint: UIColor = .systemTeal) {
        self.mode = mode
        self.tint = tint
        self.meshTint = meshTint
        super.init()
        addChild(buildGroundDisc())
        addChild(buildForwardArrow())
    }

    required init() {
        self.mode = .ghost
        self.tint = .systemTeal
        self.meshTint = .systemTeal
        super.init()
    }

    // MARK: - Avatar mounting

    /// Mount an Entity (typically the result of `AvatarLoader.load(presetId:)`)
    /// as the visible mesh. Re-mounting replaces any previous avatar.
    func mountAvatar(_ entity: Entity) {
        avatarRoot?.removeFromParent()
        originalMaterials.removeAll()
        avatarRoot = entity
        addChild(entity)
        snapshotOriginalMaterials(entity)
        applyMode(mode)
    }

    /// Convenience: load via `AvatarLoader.shared` and mount. Returns
    /// false when the preset isn't bundled — caller can fall back to
    /// the bare disc + arrow (which are still visible).
    @discardableResult
    func loadAndMount(presetId: String) async -> Bool {
        do {
            guard let entity = try await AvatarLoader.shared.load(presetId: presetId) else {
                return false
            }
            mountAvatar(entity)
            return true
        } catch {
            print("[GhostAvatar] preset \(presetId) load failed:", error)
            return false
        }
    }

    /// Drive the avatar into the same pose used in the Stage-1 preview.
    func setPose(poseId: String?, personCount: Int,
                 manifest: AvatarAnimationManifest?) async {
        guard let avatarRoot else { return }
        _ = await AvatarLoader.shared.playPose(
            poseId,
            personCount: personCount,
            on: avatarRoot,
            manifest: manifest,
        )
    }

    // MARK: - Render mode

    func setMode(_ newMode: RenderMode) {
        guard newMode != mode else { return }
        mode = newMode
        applyMode(newMode)
    }

    /// Recolour the disc + arrow markers. Used by privacy / multi-
    /// person mode so different ghosts get distinct ground discs and
    /// arrows even when their avatar meshes share the rotation pool.
    /// This deliberately does NOT touch the avatar mesh — the mesh's
    /// `meshTint` is fixed at init so the digital human stays a
    /// consistent neutral colour even when discs are recoloured.
    func tint(_ newTint: UIColor) {
        tint = newTint
        if let disc = groundDisc {
            disc.model?.materials = [UnlitMaterial(color: newTint.withAlphaComponent(0.55))]
        }
        if let arrow = forwardArrow {
            arrow.model?.materials = [UnlitMaterial(color: newTint.withAlphaComponent(0.85))]
        }
    }

    /// Show/hide just the avatar mesh while keeping the disc+arrow
    /// visible. Used by privacy mode.
    func setMeshVisible(_ visible: Bool) {
        avatarRoot?.isEnabled = visible
    }

    /// Update the .solidFade proximity. Pass `.infinity` when no human
    /// is detected, or the camera-space distance to the nearest real
    /// human in metres.
    func setProximity(_ metres: Float) {
        proximityM = metres
        guard mode == .solidFade else { return }
        applyFadeOpacity()
    }

    private func applyMode(_ m: RenderMode) {
        switch m {
        case .ghost:
            applyGhostMaterials()
        case .solidFade:
            restoreOriginalMaterials()
            applyFadeOpacity()
        }
    }

    private func applyGhostMaterials() {
        guard let avatarRoot else { return }
        let mat = makeGhostMaterial()
        forEachModelEntity(avatarRoot) { model in
            guard var component = model.model else { return }
            component.materials = Array(repeating: mat,
                                        count: max(component.materials.count, 1))
            model.model = component
        }
    }

    private func restoreOriginalMaterials() {
        guard let avatarRoot else { return }
        forEachModelEntity(avatarRoot) { model in
            guard var component = model.model,
                  let original = self.originalMaterials[ObjectIdentifier(model)]
            else { return }
            component.materials = original
            model.model = component
        }
    }

    private func applyFadeOpacity() {
        guard let avatarRoot else { return }
        let alpha: Float
        if proximityM.isFinite, proximityM < 1.5 {
            // 0.1 at 0m, 1.0 at 1.5m+
            alpha = max(0.1, min(1.0, proximityM / 1.5))
        } else {
            alpha = 1.0
        }
        avatarRoot.components.set(OpacityComponent(opacity: alpha))
    }

    private func makeGhostMaterial() -> Material {
        // CustomMaterial with a fresnel surface shader would go here.
        // Falling back to UnlitMaterial keeps every device path
        // working without a Metal shader compile.
        // Use `meshTint`, NOT `tint`: the mesh colour stays neutral
        // even when the marker disc is recoloured per-person.
        var unlit = UnlitMaterial(color: meshTint.withAlphaComponent(0.4))
        unlit.blending = .transparent(opacity: .init(floatLiteral: 0.4))
        return unlit
    }

    private func snapshotOriginalMaterials(_ root: Entity) {
        forEachModelEntity(root) { model in
            if let component = model.model {
                self.originalMaterials[ObjectIdentifier(model)] = component.materials
            }
        }
    }

    private func forEachModelEntity(_ root: Entity, _ body: (ModelEntity) -> Void) {
        if let m = root as? ModelEntity { body(m) }
        for child in root.children { forEachModelEntity(child, body) }
    }

    // MARK: - Ground disc + arrow

    private func buildGroundDisc() -> ModelEntity {
        let mesh = MeshResource.generateBox(width: 1.0, height: 0.02, depth: 1.0,
                                            cornerRadius: 0.5)
        let material = UnlitMaterial(color: tint.withAlphaComponent(0.55))
        let disc = ModelEntity(mesh: mesh, materials: [material])
        disc.position = SIMD3<Float>(0, 0.01, 0)
        groundDisc = disc
        return disc
    }

    private func buildForwardArrow() -> ModelEntity {
        // Thin box pointing along +Z (forward in RealityKit is -Z, but
        // our placement code rotates the entity so the avatar faces
        // the camera; the arrow points away from the avatar's chest,
        // i.e. toward where the photographer should stand).
        let mesh = MeshResource.generateBox(width: 0.08, height: 0.02, depth: 0.6,
                                            cornerRadius: 0.02)
        let material = UnlitMaterial(color: tint.withAlphaComponent(0.85))
        let arrow = ModelEntity(mesh: mesh, materials: [material])
        arrow.position = SIMD3<Float>(0, 0.025, -0.4)
        forwardArrow = arrow
        return arrow
    }
}
