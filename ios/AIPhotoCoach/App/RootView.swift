import SwiftUI
import SceneKit

struct RootView: View {
    @EnvironmentObject var router: AppRouter
    @State private var personCount = 1
    @State private var qualityMode: QualityMode = .fast
    @State private var styleInput: String = ""
    @AppStorage("aphc.avatarPicks") private var avatarPicksRaw: String = ""

    var body: some View {
        NavigationStack(path: $router.path) {
            ScrollView {
                VStack(spacing: 24) {
                    Text("AI 摄影教练")
                        .font(.largeTitle.bold())
                    Text("环视一圈拍 10-20 秒视频，AI 给你出片方案")
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)

                    groupSetup
                    avatarSection
                    qualityPicker
                    styleInputField

                    Button {
                        router.push(.capture(
                            personCount: personCount,
                            qualityMode: qualityMode,
                            styleKeywords: parseKeywords(styleInput)
                        ))
                    } label: {
                        Text("开始环视拍摄")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)

                    Button("我的参考图库") {
                        router.push(.referenceLibrary)
                    }
                    .font(.body)
                }
                .padding(24)
            }
            .navigationDestination(for: AppDestination.self) { destination in
                switch destination {
                case .capture(let n, let mode, let keywords):
                    EnvCaptureView(personCount: n, qualityMode: mode, styleKeywords: keywords)
                case .results(let response):
                    RecommendationView(response: response, avatarPicks: avatarPicks)
                case .referenceLibrary:
                    ReferenceLibraryView()
                case .arGuide(let shot, let id):
                    ARGuideView(shot: shot, avatarStyle: AvatarPresets.style(for: id))
                }
            }
        }
    }

    // ---- Sections ---------------------------------------------------------

    private var groupSetup: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("人数").font(.headline)
            HStack {
                ForEach([1, 2, 3, 4], id: \.self) { n in
                    Button {
                        personCount = n
                    } label: {
                        Text("\(n)")
                            .frame(width: 44, height: 44)
                            .background(personCount == n ? Color.accentColor : Color.secondary.opacity(0.2))
                            .foregroundStyle(personCount == n ? .white : .primary)
                            .clipShape(Circle())
                    }
                }
            }
        }
    }

    private var avatarSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("选你的虚拟角色").font(.headline)
                Spacer()
                Text("\(personCount) 个 slot").font(.caption).foregroundStyle(.secondary)
            }
            // Slots
            HStack(spacing: 8) {
                ForEach(0..<personCount, id: \.self) { i in
                    let id = avatarPicks[safe: i] ?? AvatarPresets.defaultPicks[i % AvatarPresets.defaultPicks.count]
                    let style = AvatarPresets.style(for: id)
                    NavigationLink {
                        AvatarChooserView(slotIndex: i,
                                          currentId: id,
                                          onSelect: { newId in setAvatar(at: i, id: newId) })
                    } label: {
                        VStack(spacing: 4) {
                            ZStack(alignment: .topLeading) {
                                AvatarThumbView(style: style)
                                    .frame(width: 56, height: 70)
                                Text("\(i + 1)")
                                    .font(.caption2.bold())
                                    .foregroundStyle(.white)
                                    .padding(4)
                                    .background(Color.accentColor, in: Circle())
                                    .padding(2)
                            }
                            Text(style.name).font(.caption2).lineLimit(1)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var qualityPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("质量").font(.headline)
            Picker("Quality", selection: $qualityMode) {
                Text("快速 (Flash)").tag(QualityMode.fast)
                Text("高质量 (Pro)").tag(QualityMode.high)
            }
            .pickerStyle(.segmented)
        }
    }

    private var styleInputField: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("风格关键词 (逗号分隔)").font(.headline)
            TextField("例如：cinematic, moody, clean", text: $styleInput)
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
        }
    }

    // ---- Avatar persistence ----------------------------------------------

    private var avatarPicks: [String] {
        if avatarPicksRaw.isEmpty { return AvatarPresets.resolve([], count: personCount) }
        let parts = avatarPicksRaw.split(separator: ",").map(String.init)
        return AvatarPresets.resolve(parts, count: personCount)
    }

    private func setAvatar(at index: Int, id: String) {
        var picks = avatarPicks
        while picks.count <= index { picks.append(AvatarPresets.defaultPicks[picks.count % AvatarPresets.defaultPicks.count]) }
        picks[index] = id
        avatarPicksRaw = picks.joined(separator: ",")
    }

    private func parseKeywords(_ raw: String) -> [String] {
        raw.split(whereSeparator: { ",，;；".contains($0) })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }
}

// MARK: - Avatar chooser

private struct AvatarChooserView: View {
    let slotIndex: Int
    let currentId: String
    let onSelect: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    private let columns = [GridItem(.adaptive(minimum: 100, maximum: 140), spacing: 12)]

    var body: some View {
        ScrollView {
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(AvatarPresets.all) { style in
                    Button {
                        onSelect(style.id)
                        dismiss()
                    } label: {
                        VStack {
                            AvatarThumbView(style: style)
                                .frame(width: 100, height: 130)
                            Text(style.name).font(.caption.bold())
                            Text(style.summary).font(.caption2).foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .padding(8)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(style.id == currentId ? Color.accentColor : .clear, lineWidth: 2)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding()
        }
        .navigationTitle("第 \(slotIndex + 1) 人 · 选角色")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Avatar thumb (live SceneKit render)

struct AvatarThumbView: UIViewRepresentable {
    let style: AvatarStyle

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = .clear
        view.allowsCameraControl = false
        view.autoenablesDefaultLighting = true
        view.antialiasingMode = .multisampling4X
        view.scene = makeScene()
        return view
    }

    func updateUIView(_ uiView: SCNView, context: Context) {
        uiView.scene = makeScene()
    }

    private func makeScene() -> SCNScene {
        let scene = SCNScene()
        let cam = SCNCamera()
        cam.fieldOfView = 28
        let camNode = SCNNode()
        camNode.camera = cam
        camNode.position = SCNVector3(0, 1.05, 3.6)
        camNode.look(at: SCNVector3(0, 0.95, 0))
        scene.rootNode.addChildNode(camNode)

        let built = AvatarBuilderSCN.build(style)
        PosePresets.apply("hands_clasped", joints: built.joints)
        scene.rootNode.addChildNode(built.root)
        return scene
    }
}

// MARK: - Helpers

private extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
