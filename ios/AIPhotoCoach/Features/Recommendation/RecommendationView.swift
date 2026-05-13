import SwiftUI

enum ShotRankingMode: String, CaseIterable, Identifiable {
    case recommended = "default"
    case overall     = "score"

    var id: String { rawValue }
    var label: String {
        switch self {
        case .recommended: return "推荐序"
        case .overall:     return "综合分"
        }
    }
}

struct RecommendationView: View {
    let response: AnalyzeResponse
    let avatarPicks: [String]

    @EnvironmentObject var router: AppRouter
    @State private var rankingMode: ShotRankingMode = ShotRankingMode(
        rawValue: UserDefaults.standard.string(forKey: "shotRankingMode") ?? ""
    ) ?? .recommended

    /// v7 Phase A — the active shot index inside the swipe pager.
    /// Defaults to 0 (the highest-scoring plan after applying the
    /// current ranking mode). Persisted across re-renders so the
    /// ranking-mode toggle below can reset it back to 0 cleanly.
    @State private var currentShot: Int = 0

    init(response: AnalyzeResponse, avatarPicks: [String] = []) {
        self.response = response
        self.avatarPicks = avatarPicks
    }

    /// True when at least one shot carries an ``overallScore`` so the
    /// toolbar has anything meaningful to sort on.
    private var hasOverallScore: Bool {
        response.shots.contains { $0.overallScore != nil }
    }

    /// v17j — explainer text for the cohort-based rerank. Returned
    /// only for the shot the backend told us to surface, and only
    /// while we're in `.recommended` ordering (overall score ranking
    /// is the user's choice, not ours, so don't second-guess it).
    private func cohortBadge(for shot: ShotRecommendation) -> String? {
        guard rankingMode == .recommended,
              let pid = response.debug?.cohortRecommendedProposalId,
              let n = response.debug?.cohortSize, n >= 5,
              shot.id == pid else { return nil }
        let basis = response.debug?.cohortBasis ?? ""
        let sceneLabel = response.debug?.cohortSceneLabel
        let where_: String
        if basis.hasPrefix("scene+keyword:") {
            let kw = String(basis.dropFirst("scene+keyword:".count))
            if let s = sceneLabel {
                where_ = "在「\(s)」场景里选了「\(kw)」的"
            } else {
                where_ = "选了「\(kw)」的"
            }
        } else if let s = sceneLabel {
            where_ = "在「\(s)」场景里的"
        } else {
            where_ = "同场景的"
        }
        return "\(where_) \(n) 位用户里多数选了它"
    }

    private var orderedShots: [ShotRecommendation] {
        switch rankingMode {
        case .recommended:
            // v17i — if the backend's cohort recommender returned a
            // best-fit proposal_id (≥5 distinct similar users), bump
            // it to the front. Falls back to LLM-natural order when
            // the cohort is too sparse.
            let cohortId = response.debug?.cohortRecommendedProposalId
            guard let cohortId,
                  let idx = response.shots.firstIndex(where: { $0.id == cohortId }),
                  idx > 0 else {
                return response.shots
            }
            var reordered = response.shots
            let pick = reordered.remove(at: idx)
            reordered.insert(pick, at: 0)
            return reordered
        case .overall:
            return response.shots.sorted {
                ($0.overallScore ?? 0) > ($1.overallScore ?? 0)
            }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            stickyHeader
                .padding(.horizontal)
                .padding(.top)
                .background(Color(.systemGroupedBackground))

            // The pager itself. SwiftUI's TabView with .page style gives
            // us native swipe + dot indicator on iOS, with smooth state
            // binding via `selection`.
            TabView(selection: $currentShot) {
                ForEach(Array(orderedShots.enumerated()), id: \.element.id) { idx, shot in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 16) {
                            ShotCard(
                                index: idx,
                                shot: shot,
                                cohortBadge: cohortBadge(for: shot),
                                onTryShot: { tryShot(shot) },
                                onShootForReal: { shootForReal(shot) },
                            )
                            if idx == orderedShots.count - 1 {
                                FooterCaption(model: response.model)
                            }
                        }
                        .padding()
                    }
                    .tag(idx)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: response.shots.count > 1 ? .always : .never))
            .indexViewStyle(.page(backgroundDisplayMode: .always))
        }
        .navigationTitle("拍摄方案")
        .navigationBarTitleDisplayMode(.inline)
        .background(Color(.systemGroupedBackground))
        .onChange(of: rankingMode) { _, new in
            UserDefaults.standard.set(new.rawValue, forKey: "shotRankingMode")
            // After a sort change the user expects the top pick (index 0)
            // to come into view — otherwise they'd be stranded on a now-
            // arbitrary slide.
            withAnimation { currentShot = 0 }
        }
    }

    private func hasEnvData(_ env: EnvironmentSnapshot) -> Bool {
        if env.sun != nil { return true }
        if env.weather != nil { return true }
        if let vl = env.visionLight, vl.directionDeg != nil { return true }
        return false
    }

    // Split out so the top-level `body` doesn't blow past the
    // SwiftUI type-checker timeout under WMO. Each subview returns
    // a concrete type the compiler can resolve in isolation.
    @ViewBuilder
    private var stickyHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            bannerArea
            if let env = response.environment, hasEnvData(env) {
                EnvironmentStrip(env: env, shots: response.shots)
            }
            RecScenecard(scene: response.scene, debug: response.debug)
            if response.shots.count > 1 {
                ShotsPagerHeader(
                    shots: orderedShots,
                    currentIndex: $currentShot,
                )
            }
            if response.shots.count > 1 && hasOverallScore {
                RankingToolbar(mode: $rankingMode)
            }
        }
    }

    // Banner merge (v9 UX polish #21) — show at most ONE top banner
    // so the user isn't punched in the face by two negative signals.
    // Severity ladder:
    //   1. capture_quality.shouldRetake (score ≤ 2) wins
    //   2. light_recapture_hint
    //   3. capture_quality score == 3 (soft)
    // Loser is degraded to an inline note inside the winner.
    @ViewBuilder
    private var bannerArea: some View {
        let hint = response.lightRecaptureHint?.enabled == true ? response.lightRecaptureHint : nil
        let cq = response.scene.captureQuality
        let cqCritical = cq?.shouldRetake == true
        if cqCritical, let cq = cq {
            CaptureAdvisoryBanner(
                quality: cq,
                onRetake: handleAdvisoryRetake,
                degradedHint: hint,
            )
        } else if let hint = hint {
            LightRecaptureBanner(
                hint: hint,
                onTap: handleRecapture,
                degradedAdvisory: (cq?.score ?? 5) <= 3 ? cq : nil,
            )
        } else if let cq = cq, cq.score <= 3 {
            CaptureAdvisoryBanner(
                quality: cq,
                onRetake: handleAdvisoryRetake,
                degradedHint: nil,
            )
        }
    }

    private func handleRecapture() {
        // Persist the suggested azimuth so the next env-capture screen can
        // centre the new pass on it. We then pop back to the wizard root
        // so the user can re-enter capture with the same scene mode.
        if let az = response.lightRecaptureHint?.suggestedAzimuthDeg {
            UserDefaults.standard.set(az, forKey: "lightRecaptureSuggestedAzimuth")
        }
        UserDefaults.standard.set(true, forKey: "lightRecaptureRequested")
        router.popToRoot()
    }

    private func handleAdvisoryRetake() {
        // Capture-quality advisory: the LLM judged the env video unfit.
        // Drop a flag so the wizard auto-jumps back to the capture step
        // and the user can shoot a cleaner pass.
        UserDefaults.standard.set(true, forKey: "captureRetakeRequested")
        router.popToRoot()
    }

    private func tryShot(_ shot: ShotRecommendation) {
        // Use the first avatar pick for the AR view; future versions can
        // pass all picks for multi-person AR placement.
        let id = avatarPicks.first ?? AvatarPresets.defaultPicks[0]
        router.push(.arGuide(shot: shot, avatarStyleId: id))
    }

    private func shootForReal(_ shot: ShotRecommendation) {
        // Push the real shoot screen — opens AVCaptureSession, applies
        // the AI plan to AVCaptureDevice and shows the alignment HUD.
        // v18 — pass the usage_record_id so the shoot screen can call
        // PATCH /captured + /satisfied. Server returns it in
        // response.debug; tolerated nil on pre-v18 deployments.
        router.push(.shoot(shot: shot,
                            usageRecordId: response.debug?.usageRecordId))
    }
}

// Renamed from SceneCard to RecScenecard to avoid clashing with the
// RootView's user-facing SceneCard picker chip. They're both private/
// internal scoped but Swift 6 emits 'invalid redeclaration' under the
// archive-time whole-module check.
private struct RecScenecard: View {
    let scene: SceneSummary
    let debug: AnalyzeDebug?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(scene.type, systemImage: "viewfinder")
                .font(.headline)
            // Wrap chips so they flow even when many lighting facts.
            FlowLayout(spacing: 6) {
                Tag(text: lightingLabel)
                if !scene.cautions.isEmpty {
                    Tag(text: "需注意 \(scene.cautions.count)", color: .orange)
                }
                ForEach(lightingChips(), id: \.text) { chip in
                    Tag(text: chip.text, color: chip.color)
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
            // v12 — pose facts list (mirrors Web `ul.pose-facts`). One
            // line per finding so the user can scan "fix shoulder, fix
            // chin" without expanding a chip tooltip.
            if let poseFacts = debug?.poseHorizon?.poseFacts, !poseFacts.isEmpty {
                Divider().padding(.top, 4)
                Text("姿态修正")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                ForEach(poseFacts, id: \.self) { fact in
                    Label(fact, systemImage: "figure.stand")
                        .font(.caption)
                        .foregroundStyle(.green)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            // Composition facts list — same treatment.
            if let compFacts = debug?.composition?.facts, !compFacts.isEmpty {
                Divider().padding(.top, 4)
                Text("构图建议")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                ForEach(compFacts, id: \.self) { fact in
                    Label(fact, systemImage: "square.grid.3x3")
                        .font(.caption)
                        .foregroundStyle(.blue)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }

    private struct ChipDef { let text: String; let color: Color }
    private func lightingChips() -> [ChipDef] {
        var out: [ChipDef] = []
        if let l = debug?.lighting {
            if let k = l.cctK {
                let warmth = k < 4500 ? "暖" : k < 6000 ? "中性" : "冷"
                let color: Color = k < 4500 ? .orange : k < 6000 ? .gray : .blue
                out.append(.init(text: "\(warmth) \(k)K", color: color))
            }
            if let d = l.lightDirection {
                let lbl = ["front":"顺光","side":"侧光","back":"逆光"][d] ?? d
                out.append(.init(text: lbl, color: .green))
            }
            if let h = l.highlightClipPct, h > 0.05 {
                out.append(.init(text: "高光裁剪 \(Int(h*100))%", color: .red))
            }
            if let s = l.shadowClipPct, s > 0.10 {
                out.append(.init(text: "暗部死黑 \(Int(s*100))%", color: .blue))
            }
            if l.dynamicRange == "extreme" {
                out.append(.init(text: "动态超限 · HDR", color: .orange))
            }
        }
        if let drift = debug?.styleCompliance?.paletteDrift {
            for d in drift.prefix(2) {
                if let axis = d.axis {
                    out.append(.init(text: "风格偏离·\(axis)", color: .orange))
                }
            }
        }
        if let pf = debug?.poseHorizon?.poseFacts, !pf.isEmpty {
            out.append(.init(text: "姿态修正 \(pf.count)", color: .green))
        }
        if let f = debug?.lightForecast {
            if let g = f.goldenHourCountdownMin, g > 0, g <= 60 {
                out.append(.init(text: "金光 \(g) 分钟后", color: .orange))
            }
            if let c = f.cloudIn30Min, c >= 0.5 {
                out.append(.init(text: "30 分内云遮 \(Int(c*100))%", color: .blue))
            }
        }
        return out
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
    /// v17j — when non-nil, render an explainer chip at the very
    /// top of the card so the user understands why this shot was
    /// promoted to position 1 by the cohort recommender. nil = no
    /// chip (either this isn't the recommended shot, or the cohort
    /// is too sparse / user picked overall-score ranking).
    var cohortBadge: String? = nil
    let onTryShot: () -> Void
    let onShootForReal: () -> Void

    /// v9 UX polish #6 — long-tail expert panels (7-dim score, style
    /// clamp report, foreground doctrine, iPhone tips, raw rows) live
    /// behind one disclosure so the primary→secondary→action flow on
    /// first view is clean. The user opts in when they want depth.
    @State private var showDetails: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let badge = cohortBadge {
                HStack(spacing: 6) {
                    Image(systemName: "person.2.fill")
                        .font(.caption2)
                    Text(badge)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.accentColor.opacity(0.12),
                              in: Capsule())
                .foregroundStyle(Color.accentColor)
                .accessibilityLabel("基于相似用户的推荐：\(badge)")
            }
            // ── Header: shot index + title + confidence
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

            // ── HERO ANSWER — the four numbers everyone wants, right
            // under the title. (v9 #6: zero-scroll access to the literal
            // "怎么按快门".)
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
                    .onAppear { PersonaTone.audit(rationale: rationale, context: "shot.rationale") }
            }

            // ── PRIMARY + SECONDARY CTA — kept above the fold. iOS users
            // can act before scrolling past evaluation panels.
            VStack(spacing: 8) {
                Button(action: onShootForReal) {
                    HStack(spacing: 8) {
                        Image(systemName: "camera.aperture")
                        Text("按此方案拍 · 自动调好参数")
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
                .tint(.accentColor)

                Button(action: onTryShot) {
                    Label("先用 AR 演练人物站位", systemImage: "arkit")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.bordered)
                .tint(.accentColor)
            }

            // ── POSES — core deliverable, kept visible. Scenery shots
            // fall back to scenery tips so the slot is never empty.
            Divider()
            if shot.poses.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("风景出片要点")
                        .font(.headline)
                    Text("构图：\(compositionDisplayName)\u{200B}")
                        .font(.callout)
                    Text(String(format: "站位：朝向 %.0f° · 距主景 %.1f m",
                                shot.angle.azimuthDeg, shot.angle.distanceM))
                        .font(.callout)
                }
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    Text("姿势建议")
                        .font(.headline)
                    ForEach(Array(shot.poses.enumerated()), id: \.offset) { i, pose in
                        PoseSuggestionCard(pose: pose, index: i)
                    }
                }
            }

            // ── COLLAPSED LONG TAIL — v9 UX polish #6. Everything an
            // expert wants to inspect (scoring, style match, foreground
            // doctrine, iPhone tips, raw angle/composition rows) is
            // here, default closed.
            DisclosureGroup(isExpanded: $showDetails) {
                VStack(alignment: .leading, spacing: 12) {
                    AngleRow(angle: shot.angle)
                    CompositionRow(comp: shot.composition)

                    if let score = shot.criteriaScore {
                        CriteriaPanel(score: score,
                                      notes: shot.criteriaNotes,
                                      strongestAxis: shot.strongestAxis,
                                      weakestAxis: shot.weakestAxis,
                                      overallScore: shot.overallScore)
                    }

                    // Three-layer composition (FOREGROUND DOCTRINE).
                    if let fg = shot.foreground {
                        ForegroundCard(foreground: fg)
                    }

                    // iPhone tips drawer — adapts to physical lens
                    // constraints (fixed aperture, lens switching,
                    // ProRAW, exposure lock).
                    if !shot.iphoneTips.isEmpty || (shot.camera.iphoneApplyPlan?.apertureNote.isEmpty == false) {
                        IphoneTipsCard(
                            tips: shot.iphoneTips,
                            apertureNote: shot.camera.iphoneApplyPlan?.apertureNote ?? "",
                            plan: shot.camera.iphoneApplyPlan,
                        )
                    }
                }
                .padding(.top, 8)
            } label: {
                HStack {
                    Image(systemName: "chart.bar.doc.horizontal")
                        .foregroundStyle(.secondary)
                    Text("展开更多分析 · 评分 / 构图 / iPhone 适配")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.primary)
                }
            }
            .padding(.top, 4)
        }
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }

    private var compositionDisplayName: String {
        switch shot.composition.primary {
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

private struct AngleRow: View {
    let angle: Angle
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
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
            // Actionable nudge derived from height_hint + pitch — tells
            // the user *how* to physically achieve the recommended angle.
            if let action = tiltAction(angle: angle) {
                Text(action.text)
                    .font(.system(size: 12, weight: .medium))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(action.color.opacity(0.12), in: Capsule())
                    .overlay(Capsule().stroke(action.color.opacity(0.40), lineWidth: 1))
                    .foregroundStyle(action.color)
            }
        }
    }

    private func tiltAction(angle: Angle) -> (text: String, color: Color)? {
        let p = angle.pitchDeg
        let h = angle.heightHint
        if h == .low || p < -8 {
            return ("蹲下来 / 举高镜头仰拍", .green)
        }
        if h == .high || p > 8 {
            return ("举高手机 / 站到台阶俯拍", .orange)
        }
        if h == .overhead {
            return ("正上方俯拍", .orange)
        }
        return ("平举即可", .gray)
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

// MARK: - Environment strip (light_shadow result page) ----------------------

/// Sun compass + phase / countdown / color-temp / weather chips. Shown at
/// the top of the result page whenever we have *any* useful environmental
/// data: real sun (geo), weather, or even a vision-only light estimate.
/// Mirrors the web `.env-strip`.
private struct EnvironmentStrip: View {
    let env: EnvironmentSnapshot
    let shots: [ShotRecommendation]

    private var countdown: (title: String, subtitle: String, isTight: Bool)? {
        guard let sun = env.sun else { return nil }
        if let m = sun.minutesToGoldenEnd {
            let mm = Int(m.rounded())
            return (
                "黄金时刻还剩 \(mm) 分钟",
                mm <= 30 ? "光线在加速消失，先拍排前面的方案" : "暖光柔光最佳窗口",
                mm <= 30
            )
        }
        if let m = sun.minutesToBlueEnd {
            let mm = Int(m.rounded())
            return (
                "蓝调时刻还剩 \(mm) 分钟",
                "天空冷蓝调 · 适合做电影感剪影",
                mm <= 30
            )
        }
        if let m = sun.minutesToSunset, m <= 90 {
            let mm = Int(m.rounded())
            return (
                "距日落 \(mm) 分钟",
                "光线方向开始向西偏低，注意逆光保留高光",
                false
            )
        }
        return nil
    }

    private var hasCompass: Bool {
        if env.sun != nil { return true }
        if let vl = env.visionLight, vl.directionDeg != nil { return true }
        return false
    }

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            if hasCompass {
                SunCompass(sun: env.sun, visionLight: env.visionLight, shots: shots)
                    .frame(width: 132, height: 132)
            }

            VStack(alignment: .leading, spacing: 6) {
                phaseLabel

                if let cd = countdown {
                    chip(glyph: "timer",
                         title: cd.title,
                         subtitle: cd.subtitle,
                         tight: cd.isTight,
                         dashed: false)
                }

                if let sun = env.sun {
                    chip(glyph: "thermometer.sun",
                         title: "\(sun.colorTempKEstimate)K",
                         subtitle: "估算色温 · 高度角 \(Int(sun.altitudeDeg.rounded()))°",
                         tight: false,
                         dashed: false)
                } else if let vl = env.visionLight, let dir = vl.directionDeg {
                    chip(glyph: "sparkles",
                         title: "\(vl.qualityZh) · \(Int(dir.rounded()))°",
                         subtitle: "置信度 \(vl.confidencePct)% · 来自视频帧分析",
                         tight: false,
                         dashed: true)
                }

                if let weather = env.weather {
                    chip(glyph: weather.softnessGlyph,
                         title: weather.softnessLabelZh,
                         subtitle: weatherSubtitle(weather),
                         tight: false,
                         dashed: weather.softness == "unknown")
                }

                if env.sun?.isTimeTight ?? false {
                    Text("AI 已按主光方向重排方案：第 1 张是当下最该抢拍的角度")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Color.accentColor)
                        .padding(.top, 2)
                }
            }
        }
        .padding(14)
        .background(
            ZStack {
                LinearGradient(colors: [Color.accentColor.opacity(0.04),
                                        Color.accentColor.opacity(0.10)],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                RadialGradient(colors: [Color.accentColor.opacity(0.18), .clear],
                               center: .topLeading, startRadius: 0, endRadius: 220)
            }
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.accentColor.opacity(0.30), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .shadow(color: Color.accentColor.opacity(0.10), radius: 16, y: 6)
    }

    private func weatherSubtitle(_ w: WeatherSnapshot) -> String {
        var parts: [String] = []
        if let label = w.codeLabelZh { parts.append(label) }
        if let cloud = w.cloudCoverPct { parts.append("云量 \(cloud)%") }
        if let temp = w.temperatureC { parts.append("\(Int(temp.rounded()))°C") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var phaseLabel: some View {
        if let sun = env.sun {
            HStack(spacing: 6) {
                Circle()
                    .fill(Color.accentColor)
                    .frame(width: 6, height: 6)
                    .shadow(color: Color.accentColor, radius: 4)
                Text(sun.phaseDisplayName.uppercased())
                    .font(.system(size: 9.5, weight: .heavy))
                    .tracking(2.4)
                    .foregroundStyle(Color.accentColor)
            }
        } else if env.visionLight?.directionDeg != nil {
            HStack(spacing: 6) {
                Circle()
                    .fill(Color.accentColor.opacity(0.6))
                    .frame(width: 6, height: 6)
                Text("视觉估算光向".uppercased())
                    .font(.system(size: 9.5, weight: .heavy))
                    .tracking(2.4)
                    .foregroundStyle(Color.accentColor.opacity(0.85))
            }
        }
    }

    @ViewBuilder
    private func chip(glyph: String, title: String, subtitle: String,
                      tight: Bool, dashed: Bool) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: glyph)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color.accentColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.system(size: 12.5, weight: .heavy))
                    .foregroundStyle(.primary)
                Text(subtitle)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 12)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(tight
                      ? Color.accentColor.opacity(0.10)
                      : Color.primary.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(
                    tight ? Color.accentColor.opacity(0.55) : Color.primary.opacity(0.06),
                    style: StrokeStyle(
                        lineWidth: 1,
                        dash: dashed ? [4, 3] : []
                    )
                )
        )
    }
}

/// v7 Phase A — chip strip that mirrors the swipe pager state.
/// Tapping a chip jumps the TabView to that index; conversely, swiping
/// updates the binding which re-highlights the active chip. We show the
/// shot title (truncated) and overall_score as a compact badge.
private struct ShotsPagerHeader: View {
    let shots: [ShotRecommendation]
    @Binding var currentIndex: Int

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(shots.enumerated()), id: \.element.id) { idx, shot in
                        Button {
                            withAnimation { currentIndex = idx }
                        } label: {
                            ShotsPagerChip(
                                index: idx,
                                title: shot.title,
                                overallScore: shot.overallScore,
                                isActive: idx == currentIndex,
                            )
                        }
                        .buttonStyle(.plain)
                        .id(idx)
                    }
                }
                .padding(.vertical, 4)
            }
            .onChange(of: currentIndex) { _, new in
                // Keep the active chip on-screen as the user pages.
                withAnimation { proxy.scrollTo(new, anchor: .center) }
            }
        }
    }
}

private struct ShotsPagerChip: View {
    let index: Int
    let title: String?
    let overallScore: Double?
    let isActive: Bool

    var body: some View {
        HStack(spacing: 6) {
            Text("#\(index + 1)")
                .font(.system(size: 12.5, weight: .bold))
                .opacity(0.86)
            if let title, !title.isEmpty {
                Text(title)
                    .font(.system(size: 12.5, weight: .medium))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 130, alignment: .leading)
            }
            if let s = overallScore {
                Text(String(format: "%.1f", s))
                    .font(.system(size: 11.5, weight: .bold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(
                        Capsule().fill(
                            isActive
                                ? Color.white.opacity(0.20)
                                : Color.accentColor.opacity(0.20)
                        )
                    )
                    .foregroundStyle(isActive ? Color.white : Color.accentColor)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
        .background(
            Capsule().fill(
                isActive
                    ? Color.accentColor.opacity(0.25)
                    : Color.primary.opacity(0.05)
            )
        )
        .overlay(
            Capsule().stroke(
                isActive
                    ? Color.accentColor.opacity(0.65)
                    : Color.primary.opacity(0.10),
                lineWidth: 1,
            )
        )
        .foregroundStyle(isActive ? Color.white : Color.primary)
        .shadow(
            color: isActive ? Color.accentColor.opacity(0.20) : .clear,
            radius: 8, y: 2,
        )
        .animation(.easeInOut(duration: 0.18), value: isActive)
    }
}

/// Light-pass recapture nudge — sits at the top of the result page in
/// light_shadow mode when AI couldn't reliably reason about the light.
/// One-tap returns to the wizard root so the user can capture again.
/// Phase 3.3 — local sort toggle. Backend pre-computed overall_score
/// for every shot; this toolbar just rearranges them on the client so
/// a switch costs zero LLM calls.
private struct RankingToolbar: View {
    @Binding var mode: ShotRankingMode

    var body: some View {
        HStack(spacing: 8) {
            Text("排序方式")
                .font(.caption2.weight(.semibold))
                .tracking(0.4)
                .foregroundStyle(.secondary)
            ForEach(ShotRankingMode.allCases) { m in
                Button {
                    mode = m
                } label: {
                    Text(m.label)
                        .font(.system(size: 12.5, weight: .semibold))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(
                            Capsule()
                                .fill(m == mode
                                      ? Color.accentColor.opacity(0.20)
                                      : Color.primary.opacity(0.05))
                        )
                        .overlay(
                            Capsule()
                                .stroke(m == mode
                                        ? Color.accentColor.opacity(0.55)
                                        : Color.primary.opacity(0.10),
                                        lineWidth: 1)
                        )
                        .foregroundStyle(m == mode ? Color.accentColor : Color.primary)
                }
                .buttonStyle(.plain)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.primary.opacity(0.04))
        )
    }
}

private struct LightRecaptureBanner: View {
    let hint: LightRecaptureHint
    let onTap: () -> Void
    /// v9 UX polish #21 — when a soft capture-quality advisory (score==3,
    /// not retake-worthy) also exists, surface it inline so the user
    /// still sees the signal without a competing orange block.
    var degradedAdvisory: CaptureQuality? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "sparkles")
                    .font(.system(size: 18, weight: .bold))
                    .frame(width: 36, height: 36)
                    .background(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(LinearGradient(
                                colors: [Color.accentColor.opacity(0.55),
                                         Color.accentColor.opacity(0.15)],
                                startPoint: .topLeading, endPoint: .bottomTrailing))
                    )
                    .foregroundStyle(Color(red: 1.0, green: 0.96, blue: 0.84))
                    .shadow(color: Color.accentColor.opacity(0.30), radius: 8)

                VStack(alignment: .leading, spacing: 4) {
                    Text(hint.title)
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(.primary)
                    Text(hint.detail)
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let az = hint.suggestedAzimuthDeg {
                        Text("建议中心方位：\(Int(az.rounded()))°（已为你预设）")
                            .font(.system(size: 11.5, weight: .semibold))
                            .foregroundStyle(Color.accentColor)
                            .padding(.top, 2)
                    }
                    if let adv = degradedAdvisory, let s = adv.summaryZh, !s.isEmpty {
                        Text("素材质量 \(adv.score)/5 · \(s)")
                            .font(.system(size: 11.5, weight: .medium))
                            .foregroundStyle(.secondary)
                            .padding(.top, 4)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
                Button(action: onTap) {
                    Text("去补一段")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color(red: 1.0, green: 0.96, blue: 0.84))
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(LinearGradient(
                                    colors: [Color.accentColor.opacity(0.30),
                                             Color.accentColor.opacity(0.10)],
                                    startPoint: .topLeading, endPoint: .bottomTrailing))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(Color.accentColor.opacity(0.65), lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .background(
            ZStack {
                LinearGradient(
                    colors: [Color.accentColor.opacity(0.14),
                             Color.accentColor.opacity(0.04)],
                    startPoint: .topLeading, endPoint: .bottomTrailing)
                RadialGradient(
                    colors: [Color.accentColor.opacity(0.20), .clear],
                    center: .topTrailing, startRadius: 0, endRadius: 240)
            }
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.accentColor.opacity(0.45), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .shadow(color: Color.accentColor.opacity(0.15), radius: 16, y: 6)
    }
}

/// Capture-quality advisory banner — fires when the LLM judged the env
/// video unfit (cluttered bg / too dark / ground-only / etc.). Mirrors
/// `.capture-advisory` on web. Loud red-orange when ``isCritical``,
/// muted amber when it's just a heads-up.
private struct CaptureAdvisoryBanner: View {
    let quality: CaptureQuality
    let onRetake: () -> Void
    /// v9 UX polish #21 — when this advisory wins the top slot, surface
    /// the light-recapture hint as an inline note inside the same card
    /// so the user still sees both signals without two competing banners.
    var degradedHint: LightRecaptureHint? = nil

    private var tintColor: Color {
        quality.isCritical ? Color(red: 0.93, green: 0.40, blue: 0.40)
                           : Color(red: 0.96, green: 0.72, blue: 0.38)
    }

    private var iconName: String {
        quality.isCritical ? "exclamationmark.triangle.fill"
                           : "exclamationmark.circle.fill"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            headerRow
            if !quality.issues.isEmpty {
                issuesRow
            }
            if quality.shouldRetake {
                retakeButton
            }
            if let h = degradedHint {
                degradedInline(h)
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(tintColor.opacity(0.10))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(tintColor.opacity(0.45), lineWidth: 1)
        )
    }

    private var starsString: String {
        let s = max(0, min(5, quality.score))
        return String(repeating: "★", count: s) + String(repeating: "☆", count: 5 - s)
    }

    // Split bodies — Xcode 16 type-checker chokes on the previously
    // inlined ~80-line VStack. Splitting keeps every helper under
    // the WMO timeout while preserving the visual output.
    @ViewBuilder
    private var headerRow: some View {
        HStack(spacing: 10) {
            Image(systemName: iconName)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(tintColor)
                .frame(width: 28, height: 28)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(tintColor.opacity(0.15))
                )
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(starsString)
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(Color(red: 1.0, green: 0.72, blue: 0.30))
                    Text("素材质量 \(quality.score)/5")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
                if let summary = quality.summaryZh, !summary.isEmpty {
                    Text(summary)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
            if quality.shouldRetake {
                Tag(text: "建议重拍", color: tintColor)
            }
        }
    }

    @ViewBuilder
    private var issuesRow: some View {
        FlowLayout(spacing: 6) {
            ForEach(quality.issues, id: \.self) { issue in
                Text("· " + issue.labelZh)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize()
            }
        }
    }

    @ViewBuilder
    private var retakeButton: some View {
        Button(action: onRetake) {
            HStack(spacing: 6) {
                Image(systemName: "arrow.counterclockwise.circle")
                Text("重新环视一段")
            }
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(LinearGradient(
                        colors: [tintColor, tintColor.opacity(0.7)],
                        startPoint: .leading, endPoint: .trailing))
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func degradedInline(_ h: LightRecaptureHint) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(h.title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.primary)
            Text(h.detail)
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.primary.opacity(0.04))
        )
    }
}

/// Tiny flow layout — wraps inline issue chips when they overflow the row.
/// SwiftUI's `Layout` protocol shipped in iOS 16 and our deployment target
/// is 17.0. Under Swift 6 the protocol is implicitly `@MainActor`, so
/// the conforming type must opt into MainActor isolation too — without
/// the attribute Xcode 16 reports "inheritance from non-protocol type 'Layout'"
/// because it cannot resolve the protocol from a nonisolated context.
@available(iOS 16.0, *)
@MainActor
private struct FlowLayout: SwiftUI.Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: LayoutSubviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var totalHeight: CGFloat = 0
        var totalWidth: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > maxWidth, x > 0 {
                totalHeight += rowHeight + spacing
                totalWidth = max(totalWidth, x - spacing)
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += s.width + spacing
            rowHeight = max(rowHeight, s.height)
        }
        totalHeight += rowHeight
        totalWidth = max(totalWidth, x - spacing)
        return CGSize(width: totalWidth, height: totalHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: LayoutSubviews, cache: inout ()) {
        let maxWidth = bounds.width
        var x: CGFloat = bounds.minX
        var y: CGFloat = bounds.minY
        var rowHeight: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            rowHeight = max(rowHeight, s.height)
            _ = maxWidth // silence warning
        }
    }
}

/// Pure SwiftUI sun compass — no MapKit, no images. Renders an N/E/S/W
/// ring, tick marks every 30°, the sun dot at its real azimuth (or a
/// dashed indicator at the LLM's vision-only direction guess when geo
/// isn't available), and a small arrow + index dot for each shot's
/// recommended camera azimuth.
private struct SunCompass: View {
    let sun: SunSnapshot?
    let visionLight: VisionLightHint?
    let shots: [ShotRecommendation]

    private var lightAzimuth: Double? {
        if let s = sun { return s.azimuthDeg }
        return visionLight?.directionDeg
    }
    private var isVisionFallback: Bool { sun == nil && visionLight?.directionDeg != nil }

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            let c = size / 2
            let rOuter = c - 4
            let rInner = rOuter - 14

            ZStack {
                Circle()
                    .fill(Color.black.opacity(0.55))
                    .overlay(Circle().stroke(Color.white.opacity(0.10), lineWidth: 1))
                    .padding(4)
                Circle()
                    .stroke(Color.white.opacity(0.06), lineWidth: 1)
                    .frame(width: rInner * 2, height: rInner * 2)

                ForEach(["N", "E", "S", "W"], id: \.self) { label in
                    let az: Double = label == "N" ? 0 : label == "E" ? 90 : label == "S" ? 180 : 270
                    let p = polar(c: c, r: rOuter + 2, azDeg: az)
                    Text(label)
                        .font(.system(size: 9, weight: .heavy, design: .monospaced))
                        .tracking(1)
                        .foregroundStyle(.secondary)
                        .position(p)
                }

                ForEach(0..<12, id: \.self) { i in
                    let az = Double(i) * 30
                    let p1 = polar(c: c, r: rInner,     azDeg: az)
                    let p2 = polar(c: c, r: rInner + 4, azDeg: az)
                    Path { p in p.move(to: p1); p.addLine(to: p2) }
                        .stroke(Color.white.opacity(0.22), lineWidth: 1)
                }

                ForEach(Array(shots.enumerated()), id: \.offset) { idx, shot in
                    let az = shot.angle.azimuthDeg
                    let p1 = polar(c: c, r: 14,         azDeg: az)
                    let p2 = polar(c: c, r: rInner - 6, azDeg: az)
                    Path { p in p.move(to: p1); p.addLine(to: p2) }
                        .stroke(idx == 0 ? Color.accentColor : Color.accentColor.opacity(0.55),
                                lineWidth: idx == 0 ? 2 : 1.4)
                    Circle()
                        .fill(idx == 0 ? Color.accentColor : Color.accentColor.opacity(0.85))
                        .overlay(Circle().stroke(Color.black.opacity(0.9), lineWidth: 1.5))
                        .frame(width: 11, height: 11)
                        .position(p2)
                    Text("\(idx + 1)")
                        .font(.system(size: 8.5, weight: .heavy, design: .monospaced))
                        .foregroundStyle(Color.black)
                        .position(p2)
                }

                if let lightAz = lightAzimuth {
                    let sunPos = polar(c: c, r: rInner - 2, azDeg: lightAz)
                    Circle()
                        .fill(Color.yellow.opacity(isVisionFallback ? 0.18 : 0.30))
                        .frame(width: 26, height: 26)
                        .position(sunPos)
                        .blur(radius: 2)
                    if isVisionFallback {
                        // Lower-confidence indicator: dashed ring + softer dot
                        Circle()
                            .stroke(
                                Color.accentColor.opacity(0.55),
                                style: StrokeStyle(lineWidth: 1, dash: [3, 2])
                            )
                            .frame(width: 22, height: 22)
                            .position(sunPos)
                        Circle()
                            .fill(Color(red: 1.0, green: 0.96, blue: 0.84).opacity(0.85))
                            .overlay(
                                Circle().stroke(
                                    Color.accentColor.opacity(0.55),
                                    style: StrokeStyle(lineWidth: 1, dash: [2, 1.5])
                                )
                            )
                            .frame(width: 10, height: 10)
                            .position(sunPos)
                    } else {
                        Circle()
                            .fill(Color(red: 1.0, green: 0.96, blue: 0.84))
                            .overlay(Circle().stroke(Color.accentColor, lineWidth: 1.4))
                            .frame(width: 11, height: 11)
                            .position(sunPos)
                            .shadow(color: Color.accentColor, radius: 4)
                    }
                }

                Circle()
                    .fill(Color.black.opacity(0.92))
                    .overlay(Circle().stroke(Color.white.opacity(0.18), lineWidth: 1))
                    .frame(width: 18, height: 18)
                    .position(x: c, y: c)
                Text("📷")
                    .font(.system(size: 11))
                    .position(x: c, y: c)
            }
        }
    }

    private func polar(c: Double, r: Double, azDeg: Double) -> CGPoint {
        // 0° = north, increasing clockwise; SVG/SwiftUI y points down.
        let rad = (azDeg - 90) * .pi / 180
        return CGPoint(x: c + cos(rad) * r, y: c + sin(rad) * r)
    }
}

// MARK: - 4-dimension criteria panel ----------------------------------------

/// Composition / Light / Color / Depth scores rendered as 4 mini bars with
/// the LLM's one-line rule citation per axis. The strongest axis gets a
/// "亮点" pill; the weakest gets a "可改" pill. Mirrors `.criteria-panel`
/// on the web result page.
private struct CriteriaPanel: View {
    let score: CriteriaScore
    let notes: CriteriaNotes?
    let strongestAxis: String?
    let weakestAxis: String?
    /// Backend-computed weighted score 0-5 (v6). Optional for backward
    /// compatibility with cached responses pre-dating the field.
    let overallScore: Double?

    init(
        score: CriteriaScore,
        notes: CriteriaNotes?,
        strongestAxis: String?,
        weakestAxis: String?,
        overallScore: Double? = nil
    ) {
        self.score = score
        self.notes = notes
        self.strongestAxis = strongestAxis
        self.weakestAxis = weakestAxis
        self.overallScore = overallScore
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("7 维质量分析")
                        .font(.subheadline.weight(.heavy))
                    Text("构图 · 主体感 · 背景 · 主题 · 光线 · 色彩 · 景深，每项 1-5 分")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                Spacer(minLength: 6)
                if let overall = overallScore {
                    VStack(alignment: .trailing, spacing: 0) {
                        Text(String(format: "%.2f", overall))
                            .font(.system(size: 22, weight: .heavy, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(Color.accentColor)
                        Text("综合分 / 5")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    .padding(.leading, 10)
                    .overlay(
                        Rectangle()
                            .fill(Color.primary.opacity(0.08))
                            .frame(width: 1)
                            .padding(.vertical, 2),
                        alignment: .leading
                    )
                }
            }

            ForEach(score.asArray, id: \.key) { axis in
                CriteriaRow(
                    label: axis.label,
                    glyph: glyph(for: axis.key),
                    value: axis.value,
                    note: notes?.note(for: axis.key),
                    isStrong: axis.key == strongestAxis,
                    isWeak:   axis.key == weakestAxis
                )
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color.accentColor.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private func glyph(for key: String) -> String {
        switch key {
        case "composition": return "viewfinder.rectangular"
        case "light":       return "sun.max"
        case "color":       return "paintpalette"
        case "depth":       return "camera.aperture"
        case "subject_fit": return "person.crop.circle"
        case "background":  return "photo"
        case "theme":       return "sparkles"
        default:            return "circle"
        }
    }
}

private struct CriteriaRow: View {
    let label: String
    let glyph: String
    let value: Int
    let note: String?
    let isStrong: Bool
    let isWeak: Bool

    private var clamped: Int { max(1, min(5, value)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 8) {
                Image(systemName: glyph)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.secondary)
                    .frame(width: 14)
                Text(label)
                    .font(.caption.weight(.heavy))
                    .tracking(0.6)
                    .foregroundStyle(.secondary)
                if isStrong { pill(text: "亮点", filled: true,  color: .accentColor) }
                if isWeak   { pill(text: "可改", filled: false, color: Color(.systemRed)) }
                Spacer()
                Text("\(clamped)/5")
                    .font(.caption2.weight(.heavy))
                    .monospacedDigit()
                    .foregroundStyle(.primary)
            }
            barView
            if let note, !note.isEmpty {
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(isStrong ? .primary : .secondary)
                    .padding(.leading, 22)
            }
        }
    }

    private var barView: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.primary.opacity(0.06))
                    .frame(height: 7)
                Capsule()
                    .fill(LinearGradient(
                        colors: isWeak
                            ? [Color(.systemRed).opacity(0.7), Color(.systemRed)]
                            : [Color.accentColor.opacity(0.7), Color.accentColor],
                        startPoint: .leading, endPoint: .trailing))
                    .frame(width: geo.size.width * CGFloat(clamped) / 5,
                           height: 7)
                ForEach(1..<6, id: \.self) { i in
                    let lit = i <= clamped
                    Circle()
                        .fill(lit ? Color.white.opacity(0.85) : Color.primary.opacity(0.18))
                        .frame(width: 2.5, height: 2.5)
                        .offset(x: geo.size.width * (CGFloat(i) - 0.5) / 5 - 1.25)
                }
            }
        }
        .frame(height: 8)
        .padding(.leading, 22)
    }

    @ViewBuilder
    private func pill(text: String, filled: Bool, color: Color) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .heavy))
            .tracking(1)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(filled ? color : Color.clear,
                        in: Capsule())
            .overlay(Capsule().stroke(color, lineWidth: 1))
            .foregroundStyle(filled ? Color.black : color)
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

/// iPhone-specific tips drawer below each ShotCard. Shows:
///   - the honest aperture-physics note ("镜头光圈固定 f/1.78...")
///   - 2-3 LLM-curated tips ("切到 2x 长焦端 / 长按主体锁定 AE...")
///   - a compact apply-plan readout (zoom factor, ISO, shutter)
/// Mirrors the web `.iphone-tips-card`.
// Mirrors web .foreground-panel. Surfaces the LLM's three-layer
// composition strategy: layer chip + 1-line blurb + actionable nudge
// + provenance chips (azimuth, canvas quadrant, distance).
private struct ForegroundCard: View {
    let foreground: ShotForeground

    private var layerColor: Color {
        switch foreground.layer {
        case "bokeh_plant":   return .green
        case "natural_frame": return .orange
        case "leading_line":  return .blue
        default:              return .gray
        }
    }
    private var blurbZh: String {
        switch foreground.layer {
        case "bokeh_plant":   return "用近距离的植物 / 花做模糊色块，包住主体"
        case "natural_frame": return "用门洞 / 树枝 / 栏杆把主体框起来"
        case "leading_line":  return "用栏杆 / 台阶 / 地砖把视线带到主体"
        default:              return "本次场景缺少 1.5 m 内的前景元素"
        }
    }
    private static let quadrantZh: [String: String] = [
        "top_left": "左上", "top_right": "右上",
        "bottom_left": "左下", "bottom_right": "右下",
        "left_edge": "左侧贴边", "right_edge": "右侧贴边",
        "top_edge": "顶部贴边", "bottom_edge": "底部贴边",
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(foreground.layerLabelZh)
                    .font(.system(size: 12, weight: .semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 3)
                    .background(layerColor.opacity(0.18), in: Capsule())
                    .overlay(Capsule().stroke(layerColor.opacity(0.50), lineWidth: 1))
                    .foregroundStyle(layerColor)
                Text(blurbZh)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            if !foreground.suggestionZh.isEmpty {
                Text(foreground.suggestionZh)
                    .font(.callout)
                    .foregroundStyle(.primary)
            }
            HStack(spacing: 6) {
                if let az = foreground.sourceAzimuthDeg {
                    metaChip("参考方位 \(Int(az))°", color: .secondary)
                }
                if let q = foreground.canvasQuadrant {
                    metaChip("画面 \(Self.quadrantZh[q] ?? q)", color: .secondary)
                }
                if let d = foreground.estimatedDistanceM {
                    let close = d < 1.5
                    let text = String(format: "距离 %.1f m · %@", d,
                                      close ? "适合虚化" : "偏远，需更靠近")
                    metaChip(text, color: close ? .green : .orange)
                }
                Spacer(minLength: 0)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(layerColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(layerColor.opacity(0.30), lineWidth: 1))
    }

    private func metaChip(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 11.5))
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6)
                .stroke(color.opacity(0.35), lineWidth: 1))
            .foregroundStyle(color)
    }
}

private struct IphoneTipsCard: View {
    let tips: [String]
    let apertureNote: String
    let plan: IphoneApplyPlan?

    @State private var expanded: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "iphone.gen3")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 28, height: 28)
                    .background(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .fill(Color.accentColor.opacity(0.85))
                    )
                VStack(alignment: .leading, spacing: 1) {
                    Text("iPhone 适配建议")
                        .font(.system(size: 13.5, weight: .heavy))
                    Text("按此方案拍时会自动应用，下面是你需要知道的差异")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Button(expanded ? "收起" : "展开") { expanded.toggle() }
                    .font(.system(size: 12, weight: .semibold))
                    .buttonStyle(.borderless)
            }

            if expanded {
                if let plan = plan, plan.canApply {
                    HStack(spacing: 6) {
                        planChip(label: "焦段",
                                 value: "\(plan.equivalentFocalMm)mm · \(String(format: "%.1fx", plan.zoomFactor))")
                        planChip(label: "ISO", value: "\(plan.iso)")
                        planChip(label: "快门", value: plan.shutterDisplay)
                        planChip(label: "EV", value: String(format: "%+.1f", plan.evCompensation))
                    }
                }
                if !apertureNote.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "circle.lefthalf.filled.righthalf.striped.horizontal")
                            .font(.system(size: 11))
                            .foregroundStyle(.orange)
                            .padding(.top, 2)
                        Text(apertureNote)
                            .font(.system(size: 12))
                            .foregroundStyle(.primary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(10)
                    .background(
                        RoundedRectangle(cornerRadius: 10)
                            .fill(Color.orange.opacity(0.10))
                    )
                }
                ForEach(Array(tips.enumerated()), id: \.offset) { idx, tip in
                    HStack(alignment: .top, spacing: 8) {
                        Text("\(idx + 1)")
                            .font(.system(size: 11, weight: .heavy, design: .monospaced))
                            .foregroundStyle(Color.accentColor)
                            .frame(width: 18, height: 18)
                            .background(Circle().fill(Color.accentColor.opacity(0.18)))
                        Text(tip)
                            .font(.system(size: 12.5))
                            .foregroundStyle(.primary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.accentColor.opacity(0.05))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color.accentColor.opacity(0.25), lineWidth: 1)
        )
    }

    private func planChip(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label)
                .font(.system(size: 8.5, weight: .heavy))
                .tracking(1.0)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 11.5, weight: .heavy))
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.primary.opacity(0.05))
        )
    }
}

extension APIClient {
    nonisolated func poseThumbnailURLLocal(id: String) -> URL {
        APIConfig.baseURL.appendingPathComponent("pose-library/thumbnail/\(id).png")
    }
}
