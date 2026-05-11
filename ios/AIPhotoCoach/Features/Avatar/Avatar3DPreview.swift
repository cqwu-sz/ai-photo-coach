import SwiftUI
import SceneKit

private let preferredAvatarOrder = [
    "female_youth_18",
    "male_casual_25",
    "female_casual_22",
    "female_elegant_30",
]

func orderedAvatarPresets(_ presets: [AvatarPresetEntry]) -> [AvatarPresetEntry] {
    let rank = Dictionary(uniqueKeysWithValues: preferredAvatarOrder.enumerated().map { ($1, $0) })
    return presets.sorted { lhs, rhs in
        let l = rank[lhs.id] ?? Int.max
        let r = rank[rhs.id] ?? Int.max
        if l != r { return l < r }
        return lhs.nameZh.localizedStandardCompare(rhs.nameZh) == .orderedAscending
    }
}

struct AvatarPreset3DPreview: UIViewRepresentable {
    let preset: AvatarPresetEntry
    var interactive: Bool = false

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = .clear
        view.antialiasingMode = .multisampling4X
        view.autoenablesDefaultLighting = false
        view.allowsCameraControl = interactive
        view.isPlaying = true
        view.rendersContinuously = true
        view.scene = makeScene()
        return view
    }

    func updateUIView(_ uiView: SCNView, context: Context) {
        uiView.allowsCameraControl = interactive
        uiView.scene = makeScene()
    }

    private func makeScene() -> SCNScene {
        let scene = SCNScene()
        scene.background.contents = UIColor.clear

        let camera = SCNCamera()
        camera.fieldOfView = interactive ? 30 : 28
        camera.wantsHDR = true
        let cameraNode = SCNNode()
        cameraNode.camera = camera
        cameraNode.position = SCNVector3(0, 1.05, interactive ? 3.2 : 3.0)
        cameraNode.look(at: SCNVector3(0, 0.95, 0))
        scene.rootNode.addChildNode(cameraNode)

        let ambient = SCNNode()
        ambient.light = SCNLight()
        ambient.light?.type = .ambient
        ambient.light?.color = UIColor(white: 0.55, alpha: 1)
        scene.rootNode.addChildNode(ambient)

        let key = SCNNode()
        key.light = SCNLight()
        key.light?.type = .omni
        key.light?.intensity = 1200
        key.position = SCNVector3(1.8, 3.6, 3.6)
        scene.rootNode.addChildNode(key)

        let fill = SCNNode()
        fill.light = SCNLight()
        fill.light?.type = .omni
        fill.light?.intensity = 480
        fill.position = SCNVector3(-2.4, 2.0, -2.0)
        scene.rootNode.addChildNode(fill)

        let floor = SCNNode(geometry: SCNCylinder(radius: interactive ? 0.95 : 0.72, height: 0.02))
        floor.geometry?.firstMaterial?.diffuse.contents = UIColor(red: 0.11, green: 0.15, blue: 0.22, alpha: 0.92)
        floor.geometry?.firstMaterial?.metalness.contents = 0.0
        floor.geometry?.firstMaterial?.roughness.contents = 0.95
        floor.position = SCNVector3(0, -0.01, 0)
        scene.rootNode.addChildNode(floor)

        let avatar = makeAvatarNode()
        if !interactive {
            let spin = SCNAction.repeatForever(.rotateBy(x: 0, y: 0.45, z: 0, duration: 6.5))
            avatar.runAction(spin)
        }
        scene.rootNode.addChildNode(avatar)
        return scene
    }

    private func makeAvatarNode() -> SCNNode {
        guard let scene = loadPresetScene() else {
            return placeholderNode()
        }
        let container = SCNNode()
        for child in scene.rootNode.childNodes {
            container.addChildNode(child.clone())
        }
        normalize(container)
        return container
    }

    private func loadPresetScene() -> SCNScene? {
        if let url = Bundle.main.url(forResource: preset.id, withExtension: "usdz", subdirectory: "Avatars") {
            return try? SCNScene(url: url, options: nil)
        }
        let pathParts = preset.usdz.split(separator: "/")
        if pathParts.count == 2,
           let url = Bundle.main.url(forResource: String(pathParts[1].dropLast(5)),
                                     withExtension: "usdz",
                                     subdirectory: String(pathParts[0])) {
            return try? SCNScene(url: url, options: nil)
        }
        return nil
    }

    private func normalize(_ node: SCNNode) {
        var minVec = SCNVector3Zero
        var maxVec = SCNVector3Zero
        let ok = node.__getBoundingBoxMin(&minVec, max: &maxVec)
        guard ok else { return }
        let height = maxVec.y - minVec.y
        guard height > 0.001 else { return }
        let targetHeight: Float = interactive ? 1.92 : 1.78
        let scale = targetHeight / height
        node.scale = SCNVector3(scale, scale, scale)
        node.position = SCNVector3(
            -((minVec.x + maxVec.x) * 0.5 * scale),
            -(minVec.y * scale),
            -((minVec.z + maxVec.z) * 0.5 * scale)
        )
        node.eulerAngles.y = interactive ? -0.18 : -0.10
    }

    private func placeholderNode() -> SCNNode {
        let capsule = SCNCapsule(capRadius: 0.22, height: 1.45)
        capsule.firstMaterial?.diffuse.contents = UIColor(red: 0.80, green: 0.84, blue: 0.90, alpha: 1)
        let node = SCNNode(geometry: capsule)
        node.position = SCNVector3(0, 0.72, 0)
        return node
    }
}