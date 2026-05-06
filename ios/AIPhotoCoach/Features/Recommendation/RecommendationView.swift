import SwiftUI

struct RecommendationView: View {
    let response: AnalyzeResponse
    let avatarPicks: [String]

    @EnvironmentObject var router: AppRouter

    init(response: AnalyzeResponse, avatarPicks: [String] = []) {
        self.response = response
        self.avatarPicks = avatarPicks
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SceneCard(scene: response.scene)
                ForEach(Array(response.shots.enumerated()), id: \.element.id) { idx, shot in
                    ShotCard(index: idx, shot: shot, onTryShot: { tryShot(shot) })
                }
                FooterCaption(model: response.model)
            }
            .padding()
        }
        .navigationTitle("拍摄方案")
        .navigationBarTitleDisplayMode(.inline)
        .background(Color(.systemGroupedBackground))
    }

    private func tryShot(_ shot: ShotRecommendation) {
        // Use the first avatar pick for the AR view; future versions can
        // pass all picks for multi-person AR placement.
        let id = avatarPicks.first ?? AvatarPresets.defaultPicks[0]
        router.push(.arGuide(shot: shot, avatarStyleId: id))
    }
}

private struct SceneCard: View {
    let scene: SceneSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(scene.type, systemImage: "viewfinder")
                .font(.headline)
            HStack {
                Tag(text: lightingLabel)
                if !scene.cautions.isEmpty {
                    Tag(text: "需注意 \(scene.cautions.count)", color: .orange)
                }
            }
            Text(scene.backgroundSummary)
                .font(.body)
                .foregroundStyle(.secondary)
            ForEach(scene.cautions, id: \.self) { caution in
                Label(caution, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }

    private var lightingLabel: String {
        switch scene.lighting {
        case .goldenHour: return "黄金时段"
        case .blueHour: return "蓝调时段"
        case .harshNoon: return "正午顶光"
        case .overcast: return "阴天"
        case .shade: return "阴影"
        case .indoorWarm: return "室内暖光"
        case .indoorCool: return "室内冷光"
        case .lowLight: return "弱光"
        case .backlight: return "逆光"
        case .mixed: return "混合光"
        }
    }
}

private struct ShotCard: View {
    let index: Int
    let shot: ShotRecommendation
    let onTryShot: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("机位 #\(index + 1)")
                    .font(.title3.bold())
                if let title = shot.title {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                ConfidenceBadge(value: shot.confidence)
            }

            AngleRow(angle: shot.angle)
            CompositionRow(comp: shot.composition)
            CameraSettingsRow(camera: shot.camera)

            if let brief = shot.coachBrief, !brief.isEmpty {
                Text("\u{201C}\(brief)\u{201D}")
                    .font(.headline)
                    .padding(.top, 4)
            }
            if let rationale = shot.rationale.isEmpty ? nil : shot.rationale {
                Text(rationale)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
            }

            Button(action: onTryShot) {
                Label("试拍这个机位 (AR)", systemImage: "arkit")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .tint(.accentColor)

            Divider()
            VStack(alignment: .leading, spacing: 12) {
                Text("姿势建议")
                    .font(.headline)
                ForEach(Array(shot.poses.enumerated()), id: \.offset) { i, pose in
                    PoseSuggestionCard(pose: pose, index: i)
                }
            }
        }
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct AngleRow: View {
    let angle: Angle
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "compass.drawing")
                .foregroundStyle(.tint)
            Text(String(format: "方向 %.0f°", angle.azimuthDeg))
            Text("·")
            Text(String(format: "俯仰 %+.0f°", angle.pitchDeg))
            Text("·")
            Text(String(format: "距离 %.1fm", angle.distanceM))
            Spacer()
            if let h = angle.heightHint {
                Tag(text: heightLabel(h))
            }
        }
        .font(.subheadline)
    }

    private func heightLabel(_ h: HeightHint) -> String {
        switch h {
        case .low: return "低位"
        case .eyeLevel: return "平视"
        case .high: return "高位"
        case .overhead: return "俯拍"
        }
    }
}

private struct CompositionRow: View {
    let comp: Composition
    var body: some View {
        HStack {
            Image(systemName: "square.split.2x2")
                .foregroundStyle(.tint)
            Text(label)
            if !comp.secondary.isEmpty {
                Tag(text: comp.secondary.joined(separator: " + "))
            }
            Spacer()
        }
        .font(.subheadline)
    }

    private var label: String {
        switch comp.primary {
        case .ruleOfThirds: return "三分线"
        case .leadingLine: return "引导线"
        case .symmetry: return "对称"
        case .frameWithinFrame: return "框中框"
        case .negativeSpace: return "负空间"
        case .centered: return "居中"
        case .diagonal: return "对角线"
        case .goldenRatio: return "黄金比例"
        }
    }
}

private struct CameraSettingsRow: View {
    let camera: CameraSettings
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "camera.aperture")
                    .foregroundStyle(.tint)
                Text(String(format: "%.0fmm", camera.focalLengthMm))
                Tag(text: camera.aperture)
                Tag(text: camera.shutter)
                Tag(text: "ISO \(camera.iso)")
                if let wb = camera.whiteBalanceK {
                    Tag(text: "\(wb)K")
                }
                if let ev = camera.evCompensation {
                    Tag(text: String(format: "%+.1fEV", ev))
                }
                Spacer()
            }
            .font(.subheadline)

            if let lens = camera.deviceHints?.iphoneLens {
                Text("iPhone 镜头: \(lensLabel(lens))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let r = camera.rationale, !r.isEmpty {
                Text(r)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func lensLabel(_ l: IphoneLens) -> String {
        switch l {
        case .ultrawide: return "0.5x 超广角"
        case .wide: return "1x 主摄"
        case .tele2x: return "2x 长焦"
        case .tele3x: return "3x 长焦"
        case .tele5x: return "5x 长焦"
        }
    }
}

private struct PoseSuggestionCard: View {
    let pose: PoseSuggestion
    let index: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                if let id = pose.referenceThumbnailId {
                    AsyncImage(url: APIClient.shared.poseThumbnailURLLocal(id: id)) { phase in
                        switch phase {
                        case .empty: ProgressView()
                        case .success(let img): img.resizable().scaledToFit()
                        case .failure: Image(systemName: "photo").resizable().scaledToFit()
                        @unknown default: EmptyView()
                        }
                    }
                    .frame(width: 96, height: 96)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(layoutLabel)
                        .font(.subheadline.bold())
                    Text("\(pose.personCount) 人")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let interaction = pose.interaction {
                        Text(interaction)
                            .font(.callout)
                    }
                }
                Spacer()
            }

            ForEach(pose.persons) { person in
                PersonRow(person: person)
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    private var layoutLabel: String {
        switch pose.layout {
        case .single: return "单人"
        case .sideBySide: return "并肩"
        case .highLowOffset: return "高低错位"
        case .triangle: return "三角"
        case .line: return "一字排开"
        case .cluster: return "簇拥"
        case .diagonal: return "对角分布"
        case .vFormation: return "V 型"
        case .circle: return "围圈"
        case .custom: return "自定义"
        }
    }
}

private struct PersonRow: View {
    let person: PersonPose
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(person.role)
                .font(.caption.bold())
                .foregroundColor(.accentColor)
            VStack(alignment: .leading, spacing: 2) {
                if let s = person.stance { Text("• 站姿: \(s)").font(.caption) }
                if let u = person.upperBody { Text("• 上身: \(u)").font(.caption) }
                if let h = person.hands { Text("• 手部: \(h)").font(.caption) }
                if let g = person.gaze { Text("• 视线: \(g)").font(.caption) }
                if let e = person.expression { Text("• 表情: \(e)").font(.caption) }
                if let p = person.positionHint { Text("• 站位: \(p)").font(.caption) }
            }
            .foregroundStyle(.secondary)
        }
    }
}

private struct ConfidenceBadge: View {
    let value: Double
    var body: some View {
        let pct = Int((value * 100).rounded())
        Text("\(pct)%")
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.accentColor.opacity(0.15))
            .foregroundColor(.accentColor)
            .clipShape(Capsule())
    }
}

private struct Tag: View {
    let text: String
    var color: Color = .secondary
    var body: some View {
        Text(text)
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .clipShape(Capsule())
    }
}

private struct FooterCaption: View {
    let model: String
    var body: some View {
        HStack {
            Spacer()
            Text("by \(model.isEmpty ? "AI Photo Coach" : model)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }
}

extension APIClient {
    nonisolated func poseThumbnailURLLocal(id: String) -> URL {
        APIConfig.baseURL.appendingPathComponent("pose-library/thumbnail/\(id).png")
    }
}
