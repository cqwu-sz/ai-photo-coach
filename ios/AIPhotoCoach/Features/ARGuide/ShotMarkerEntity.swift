// ShotMarkerEntity.swift (W8.2)
//
// RealityKit entity that renders a glowing ground circle + a floating
// distance label at a target ShotPosition. Notifies the parent via
// `onArrival` when the camera is within `arrivalRadiusM` of the marker.

import Foundation
import RealityKit
import simd

@MainActor
final class ShotMarkerEntity: Entity {
    private let arrivalRadiusM: Float
    private var hasFiredArrival = false
    private(set) var distanceLabel: ModelEntity?
    var onArrival: (() -> Void)?

    init(arrivalRadiusM: Float = 3.0,
         tintColor: UIColor = .systemBlue) {
        self.arrivalRadiusM = arrivalRadiusM
        super.init()
        components.set(buildGlowingDisc(tint: tintColor))
        addChild(buildDistanceLabel())
    }

    required init() {
        self.arrivalRadiusM = 3.0
        super.init()
    }

    private func buildGlowingDisc(tint: UIColor) -> ModelComponent {
        let mesh = MeshResource.generateBox(width: 1.4, height: 0.02, depth: 1.4,
                                            cornerRadius: 0.7)
        let material = UnlitMaterial(color: tint.withAlphaComponent(0.55))
        return ModelComponent(mesh: mesh, materials: [material])
    }

    private func buildDistanceLabel() -> Entity {
        let mesh = MeshResource.generateText(
            "—",
            extrusionDepth: 0.001,
            font: .systemFont(ofSize: 0.22, weight: .semibold),
            containerFrame: .zero,
            alignment: .center,
            lineBreakMode: .byWordWrapping,
        )
        let label = ModelEntity(mesh: mesh,
                                materials: [UnlitMaterial(color: .white)])
        label.position = SIMD3<Float>(0, 1.2, 0)
        label.scale = SIMD3<Float>(repeating: 1)
        self.distanceLabel = label
        return label
    }

    func updateDistance(_ metres: Float) {
        guard let label = distanceLabel,
              let model = label.model else { return }
        let text = String(format: "%.1f m", metres)
        let mesh = MeshResource.generateText(
            text,
            extrusionDepth: 0.001,
            font: .systemFont(ofSize: 0.22, weight: .semibold),
            alignment: .center,
        )
        label.model = ModelComponent(mesh: mesh, materials: model.materials)
        if !hasFiredArrival && metres <= arrivalRadiusM {
            hasFiredArrival = true
            onArrival?()
        }
    }
}
