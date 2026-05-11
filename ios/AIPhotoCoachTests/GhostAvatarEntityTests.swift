import XCTest
import RealityKit
@testable import AIPhotoCoach

@MainActor
final class GhostAvatarEntityTests: XCTestCase {
    /// Without an avatar mesh mounted, the entity should still expose
    /// the bare disc + arrow so the shot marker stays visible even
    /// when the usdz preset is missing from the bundle.
    func testBareEntityHasDiscAndArrow() {
        let g = GhostAvatarEntity(mode: .ghost)
        let modelChildren = g.children.compactMap { $0 as? ModelEntity }
        XCTAssertEqual(modelChildren.count, 2,
                       "GhostAvatarEntity should ship with a disc + arrow even without a mounted avatar")
    }

    /// Mounting a synthetic ModelEntity in .ghost mode should rewrite
    /// every material on every descendant ModelEntity to a translucent
    /// UnlitMaterial.
    func testGhostModeReplacesMaterialsWithTranslucentUnlit() throws {
        let g = GhostAvatarEntity(mode: .ghost, tint: .systemTeal)
        let mesh = MeshResource.generateBox(size: 0.5)
        let opaque = SimpleMaterial(color: .red, isMetallic: false)
        let avatar = ModelEntity(mesh: mesh, materials: [opaque, opaque])
        g.mountAvatar(avatar)

        guard let component = avatar.model else {
            XCTFail("mounted avatar lost its ModelComponent"); return
        }
        XCTAssertEqual(component.materials.count, 2)
        for material in component.materials {
            XCTAssertTrue(material is UnlitMaterial,
                          "ghost mode should swap PBR materials for UnlitMaterial; got \(type(of: material))")
        }
    }

    /// Switching to .solidFade should restore the original materials
    /// snapshotted at mount time.
    func testSolidFadeRestoresOriginalMaterials() throws {
        let g = GhostAvatarEntity(mode: .ghost)
        let mesh = MeshResource.generateBox(size: 0.5)
        let original = SimpleMaterial(color: .blue, isMetallic: false)
        let avatar = ModelEntity(mesh: mesh, materials: [original])
        g.mountAvatar(avatar)
        XCTAssertTrue(avatar.model?.materials.first is UnlitMaterial)

        g.setMode(.solidFade)
        XCTAssertTrue(avatar.model?.materials.first is SimpleMaterial,
                      "solidFade should restore the avatar's original PBR materials")
    }

    /// In .solidFade mode, proximity < 1.5m should attach an
    /// OpacityComponent with alpha < 1, while a far proximity should
    /// reset opacity to 1.
    func testSolidFadeProximityAdjustsOpacity() {
        let g = GhostAvatarEntity(mode: .solidFade)
        let mesh = MeshResource.generateBox(size: 0.5)
        let avatar = ModelEntity(mesh: mesh,
                                 materials: [SimpleMaterial(color: .green, isMetallic: false)])
        g.mountAvatar(avatar)

        g.setProximity(0.5)
        let near = avatar.components[OpacityComponent.self]?.opacity ?? 1.0
        XCTAssertLessThan(near, 1.0)
        XCTAssertGreaterThanOrEqual(near, 0.1)

        g.setProximity(.infinity)
        let far = avatar.components[OpacityComponent.self]?.opacity ?? 1.0
        XCTAssertEqual(far, 1.0, accuracy: 0.001)
    }
}
