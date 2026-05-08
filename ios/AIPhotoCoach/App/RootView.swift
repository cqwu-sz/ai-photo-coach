import SwiftUI
import SceneKit

// MARK: - Wizard step enum -----------------------------------------------------

/// 4-step onboarding flow. Mirrors the web PWA wizard so users get an
/// identical experience on Web and iOS.
enum WizardStep: Int, CaseIterable, Identifiable {
    case scene = 1
    case cast
    case tone
    case review

    var id: Int { rawValue }

    var label: String {
        switch self {
        case .scene:   return "场景"
        case .cast:    return "阵容"
        case .tone:    return "基调"
        case .review:  return "开拍"
        }
    }
}

// MARK: - RootView -------------------------------------------------------------

/// Top-level home screen. Drives a 4-step wizard:
///   1 · Scene     pick the shot scenario (portrait / scenery / etc.)
///   2 · Cast      person count + virtual avatars  (skipped in scenery)
///   3 · Tone      style keywords + quality preset
///   4 · Review    summary + the big "start capture" CTA
///
/// State survives between launches via @AppStorage so a returning user
/// lands directly on Step 4 with previous picks preselected. They can still
/// jump back to any earlier step by tapping the progress bar or summary
/// chips.
struct RootView: View {
    @EnvironmentObject var router: AppRouter

    // ---- Persisted preferences (same keys the legacy code used) ------------
    @AppStorage("aphc.sceneMode") private var sceneModeRaw: String = SceneMode.portrait.rawValue
    @AppStorage("aphc.personCount") private var personCount: Int = 1
    @AppStorage("aphc.qualityMode") private var qualityModeRaw: String = QualityMode.fast.rawValue
    @AppStorage("aphc.styleKeywords") private var styleInput: String = ""
    @AppStorage("aphc.avatarPicks") private var avatarPicksRaw: String = ""

    // ---- Wizard state -------------------------------------------------------
    @AppStorage("aphc.wizardCompleted") private var wizardCompleted: Bool = false
    @AppStorage("aphc.wizardFurthestStep") private var furthestStepRaw: Int = 1
    @State private var currentStep: WizardStep = .scene
    @State private var showSettings = false

    // ---- Reuse-environment cache (Step 4 chip) ------------------------------
    @State private var cachedMeta: CapturedFramesStore.Meta?
    @State private var reuseBusy: Bool = false
    @State private var reuseError: String?
    @State private var showReuseError: Bool = false

    private var sceneMode: SceneMode {
        SceneMode(rawValue: sceneModeRaw) ?? .portrait
    }

    private var qualityMode: QualityMode {
        QualityMode(rawValue: qualityModeRaw) ?? .fast
    }

    var body: some View {
        NavigationStack(path: $router.path) {
            ZStack {
                CinemaBackdrop()
                VStack(spacing: 0) {
                    WizardProgressBar(
                        currentStep: currentStep,
                        furthestStep: WizardStep(rawValue: furthestStepRaw) ?? .scene,
                        onTap: jumpTo
                    )
                    .padding(.horizontal, 20)
                    .padding(.bottom, 12)

                    GeometryReader { _ in
                        ZStack {
                            stepContent(for: currentStep)
                                .id(currentStep)
                                .transition(stepTransition)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }

                    wizardFooter
                        .padding(.horizontal, 20)
                        .padding(.top, 6)
                        .padding(.bottom, 6)
                        .background(
                            LinearGradient(
                                colors: [Color.clear,
                                         CinemaTheme.bgBase.opacity(0.78),
                                         CinemaTheme.bgBase.opacity(0.92)],
                                startPoint: .top,
                                endPoint: .bottom
                            )
                            .ignoresSafeArea(edges: .bottom)
                        )
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    HStack(spacing: 8) {
                        Image(systemName: "camera.aperture")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(CinemaTheme.accentGradient)
                        Text("拾光")
                            .font(.system(size: 16, weight: .heavy))
                            .foregroundStyle(CinemaTheme.heroGradient)
                            .tracking(0.6)
                        Text("AI 摄影教练")
                            .font(.system(size: 9.5, weight: .semibold))
                            .tracking(1.2)
                            .foregroundStyle(CinemaTheme.inkMuted)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .overlay(
                                RoundedRectangle(cornerRadius: 4)
                                    .stroke(CinemaTheme.borderSoft, lineWidth: 1)
                            )
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .foregroundStyle(CinemaTheme.inkSoft)
                            .padding(8)
                            .background(.ultraThinMaterial,
                                        in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(CinemaTheme.borderSoft, lineWidth: 1)
                            )
                    }
                    .accessibilityLabel("模型设置")
                }
            }
            .toolbarBackground(.hidden, for: .navigationBar)
            .sheet(isPresented: $showSettings) {
                ModelSettingsView()
            }
            .navigationDestination(for: AppDestination.self) { destination in
                switch destination {
                case .capture(let n, let mode, let scene, let keywords):
                    EnvCaptureView(personCount: n, qualityMode: mode, sceneMode: scene, styleKeywords: keywords)
                case .results(let response):
                    RecommendationView(response: response, avatarPicks: avatarPicks)
                case .referenceLibrary:
                    ReferenceLibraryView()
                case .arGuide(let shot, let id):
                    ARGuideView(
                        shot: shot,
                        avatarStyle: AvatarPresets.style(for: id),
                        presetId: UserDefaults.standard.stringArray(forKey: "avatarPicks")?.first ?? id,
                    )
                case .shoot(let shot):
                    ShootView(shot: shot)
                }
            }
        }
        .preferredColorScheme(.dark)
        .tint(CinemaTheme.accentWarm)
        .onAppear {
            bootstrapStep()
            refreshCachedMeta()
        }
        .onChange(of: currentStep) { _, step in
            if step == .review { refreshCachedMeta() }
        }
        .alert("复用失败",
               isPresented: $showReuseError,
               actions: { Button("好", role: .cancel) {} },
               message: { Text(reuseError ?? "未知错误") })
    }

    // ---------------------------------------------------------------------
    // Step content + transition
    // ---------------------------------------------------------------------

    @ViewBuilder
    private func stepContent(for step: WizardStep) -> some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 18) {
                switch step {
                case .scene:    sceneStepView
                case .cast:     castStepView
                case .tone:     toneStepView
                case .review:   reviewStepView
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 4)
            .padding(.bottom, 80)
        }
    }

    private var stepTransition: AnyTransition {
        .asymmetric(
            insertion: .move(edge: .trailing).combined(with: .opacity),
            removal: .move(edge: .leading).combined(with: .opacity)
        )
    }

    // ---------------------------------------------------------------------
    // STEP 1 — Scene
    // ---------------------------------------------------------------------

    private var sceneStepView: some View {
        VStack(alignment: .leading, spacing: 18) {
            stepHeader(eyebrow: "第 1 步 / 共 4 步",
                       title: "想出什么样的",
                       gradTail: "片",
                       suffix: "？",
                       sub: "挑一个出片场景，AI 会按这个意图来选机位、构图和相机参数。")

            LazyVGrid(columns: [GridItem(.flexible(), spacing: 12),
                                GridItem(.flexible(), spacing: 12)],
                      spacing: 12) {
                ForEach(SceneMode.allCases, id: \.self) { mode in
                    SceneCard(mode: mode,
                              isActive: sceneMode == mode,
                              tap: { applySceneMode(mode) })
                }
            }
        }
    }

    // ---------------------------------------------------------------------
    // STEP 2 — Cast
    // ---------------------------------------------------------------------

    private var castStepView: some View {
        VStack(alignment: .leading, spacing: 18) {
            stepHeader(eyebrow: "第 2 步 / 共 4 步",
                       title: "谁来",
                       gradTail: "出镜",
                       suffix: "？",
                       sub: "告诉 AI 几个人参与，再选好对应的虚拟角色 — 结果页会让他们摆姿势给你看。")

            CinemaSection(title: "人数") {
                HStack(spacing: 10) {
                    ForEach(personCountOptions, id: \.self) { n in
                        PersonPill(value: n,
                                   isActive: personCount == n,
                                   tap: { personCount = n })
                    }
                    Spacer()
                }
            }

            if sceneMode != .scenery {
                CinemaSection(title: "选你的虚拟角色",
                              hint: "每个机位会让这些角色摆姿势给你看") {
                    HStack(spacing: 10) {
                        ForEach(0..<max(personCount, 1), id: \.self) { i in
                            avatarSlot(at: i)
                        }
                        Spacer()
                    }
                }
            }
        }
    }

    private func avatarSlot(at i: Int) -> some View {
        let id = avatarPicks[safe: i] ?? AvatarPresets.defaultPicks[i % AvatarPresets.defaultPicks.count]
        let style = AvatarPresets.style(for: id)
        return NavigationLink {
            AvatarChooserView(slotIndex: i,
                              currentId: id,
                              onSelect: { newId in setAvatar(at: i, id: newId) })
        } label: {
            VStack(spacing: 6) {
                ZStack(alignment: .topLeading) {
                    AvatarThumbView(style: style)
                        .frame(width: 64, height: 80)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color.white.opacity(0.04))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(CinemaTheme.borderSoft, lineWidth: 1)
                        )
                    Text("\(i + 1)")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.black.opacity(0.85))
                        .padding(5)
                        .background(Circle().fill(CinemaTheme.accentWarm))
                        .padding(4)
                }
                Text(style.name)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(CinemaTheme.inkSoft)
                    .lineLimit(1)
            }
        }
        .buttonStyle(.plain)
    }

    private var personCountOptions: [Int] {
        sceneMode == .scenery ? [0, 1, 2, 3, 4] : [1, 2, 3, 4]
    }

    // ---------------------------------------------------------------------
    // STEP 3 — Tone
    // ---------------------------------------------------------------------

    private var toneStepView: some View {
        VStack(alignment: .leading, spacing: 18) {
            stepHeader(eyebrow: "第 3 步 / 共 4 步",
                       title: "想要怎样的",
                       gradTail: "基调",
                       suffix: "？",
                       sub: "关键词决定色调和氛围；质量档决定 AI 想得多深。都可跳过用默认。")

            CinemaSection(title: "风格关键词（可选）") {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("", text: $styleInput,
                              prompt: Text("例如：cinematic, moody, clean")
                                .foregroundColor(CinemaTheme.inkMuted))
                        .font(.system(size: 14.5))
                        .foregroundStyle(CinemaTheme.ink)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(Color.black.opacity(0.28))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(CinemaTheme.borderSoft, lineWidth: 1)
                        )
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(StyleSuggestion.all, id: \.self) { tag in
                                Button {
                                    styleInput = tag
                                } label: {
                                    Text(tag)
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundStyle(CinemaTheme.inkSoft)
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 7)
                                        .background(Capsule().fill(Color.white.opacity(0.04)))
                                        .overlay(Capsule().stroke(CinemaTheme.borderSoft, lineWidth: 1))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }

            CinemaSection(title: "质量档") {
                HStack(spacing: 10) {
                    QualityCard(mode: .fast, title: "快速",
                                sub: "Flash · ~30s · 免费额度大",
                                isActive: qualityMode == .fast,
                                tap: { qualityModeRaw = QualityMode.fast.rawValue })
                    QualityCard(mode: .high, title: "高质量",
                                sub: "Pro · ~60s · 推理更稳",
                                isActive: qualityMode == .high,
                                tap: { qualityModeRaw = QualityMode.high.rawValue })
                }
            }
        }
    }

    // ---------------------------------------------------------------------
    // STEP 4 — Review
    // ---------------------------------------------------------------------

    private var reviewStepView: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepHeader(eyebrow: "第 4 步 / 共 4 步",
                       title: "准备好",
                       gradTail: "开拍",
                       suffix: "了吗？",
                       sub: "下面是 AI 收到的全部上下文。任意一条不对都可以点回去改。")

            if wizardCompleted {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: "sparkles")
                        .foregroundStyle(CinemaTheme.accentCool)
                    Text("欢迎回来。已经按你上次的偏好准备好了，直接开拍或点上面的步骤改设置。")
                        .font(.system(size: 12.5))
                        .foregroundStyle(CinemaTheme.inkSoft)
                        .lineSpacing(2)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(CinemaTheme.accentCool.opacity(0.10))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(CinemaTheme.accentCool.opacity(0.30), lineWidth: 1)
                )
            }

            if let cachedMeta, isReuseFresh(cachedMeta) {
                ReuseChip(
                    cachedSceneName: cachedSceneDisplayName,
                    currentSceneName: sceneMode.displayName,
                    sameScene: cachedMeta.sceneMode == sceneMode.rawValue,
                    count: cachedMeta.count,
                    relativeAge: CapturedFramesStore.relativeAge(
                        Date().timeIntervalSince(cachedMeta.capturedAt)),
                    isBusy: reuseBusy,
                    tap: { runReuseFlow() }
                )
            }

            VStack(spacing: 8) {
                SummaryChip(label: "SCENE",
                            value: sceneSummary,
                            tap: { jumpTo(.scene) })
                SummaryChip(label: "CAST",
                            value: castSummary,
                            tap: { jumpTo(.cast) })
                SummaryChip(label: "TONE",
                            value: toneSummary,
                            tap: { jumpTo(.tone) })
            }

            Button {
                router.push(.referenceLibrary)
            } label: {
                Label("我的参考图库", systemImage: "photo.stack")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(CinemaTheme.inkSoft)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(.ultraThinMaterial,
                                in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(CinemaTheme.borderSoft, lineWidth: 1)
                    )
            }
            .padding(.top, 4)
        }
    }

    private var sceneSummary: String { sceneMode.displayName }
    private var castSummary: String {
        if sceneMode == .scenery { return "纯风景，不出人" }
        let names = avatarPicks.prefix(personCount)
            .map { AvatarPresets.style(for: $0).name }
            .filter { !$0.isEmpty }
        let suffix: String = {
            if names.isEmpty { return " · 角色待选" }
            if names.count <= 2 { return " · " + names.joined(separator: " / ") }
            return " · \(names[0]) 等 \(names.count) 位"
        }()
        return "\(personCount) 人\(suffix)"
    }
    private var toneSummary: String {
        let q = qualityMode == .fast ? "快速 (Flash)" : "高质量 (Pro)"
        let kws = parseKeywords(styleInput)
        let tone = kws.isEmpty ? "无指定关键词" : kws.joined(separator: " + ")
        return "\(q) · \(tone)"
    }

    // ---------------------------------------------------------------------
    // Header helper
    // ---------------------------------------------------------------------

    @ViewBuilder
    private func stepHeader(eyebrow: String,
                            title: String,
                            gradTail: String,
                            suffix: String,
                            sub: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Circle()
                    .fill(CinemaTheme.accentWarm)
                    .frame(width: 5, height: 5)
                    .shadow(color: CinemaTheme.accentWarm.opacity(0.7), radius: 5)
                Text(eyebrow)
                    .font(.system(size: 11, weight: .bold))
                    .kerning(2.0)
                    .foregroundStyle(CinemaTheme.accentWarm)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                Capsule().fill(CinemaTheme.accentWarm.opacity(0.10))
            )
            .overlay(
                Capsule().stroke(CinemaTheme.accentWarm.opacity(0.32), lineWidth: 1)
            )

            (Text(title)
                .foregroundStyle(CinemaTheme.ink)
             + Text(gradTail)
                .foregroundStyle(CinemaTheme.heroGradient)
             + Text(suffix)
                .foregroundStyle(CinemaTheme.ink))
                .font(.system(size: 32, weight: .heavy))
                .kerning(-0.8)
                .multilineTextAlignment(.leading)

            Text(sub)
                .font(.system(size: 13.5))
                .foregroundStyle(CinemaTheme.inkSoft)
                .lineSpacing(3)
        }
    }

    // ---------------------------------------------------------------------
    // Footer (back + next/CTA)
    // ---------------------------------------------------------------------

    private var wizardFooter: some View {
        HStack(spacing: 10) {
            Button { goBack() } label: {
                Image(systemName: "arrow.left")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(currentStep == .scene
                                     ? CinemaTheme.inkMuted : CinemaTheme.inkSoft)
                    .frame(width: 56, height: 56)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(Color.white.opacity(0.05))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .stroke(CinemaTheme.borderSoft, lineWidth: 1)
                    )
                    .opacity(currentStep == .scene ? 0.4 : 1.0)
            }
            .disabled(currentStep == .scene)
            .buttonStyle(.plain)

            Button(action: goNext) {
                HStack(spacing: 10) {
                    Text(nextLabel)
                        .font(.system(size: 16.5, weight: .bold))
                    Image(systemName: "arrow.right")
                        .font(.system(size: 14, weight: .heavy))
                }
                .foregroundStyle(.black.opacity(0.9))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(CinemaTheme.ctaGradient)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(Color.white.opacity(0.16), lineWidth: 1)
                )
                .shadow(color: CinemaTheme.accentWarm.opacity(0.45), radius: 16, y: 6)
            }
            .buttonStyle(CTAButtonStyle())
        }
    }

    private var nextLabel: String {
        switch currentStep {
        case .review:  return "开始环视拍摄"
        case .tone where sceneMode == .scenery: return "下一步：开拍"
        default:       return "继续"
        }
    }

    // ---------------------------------------------------------------------
    // Wizard navigation
    // ---------------------------------------------------------------------

    /// Coerce personCount=0 only when scene mode is scenery.
    private var effectivePersonCount: Int {
        if sceneMode == .scenery { return personCount }
        return max(1, personCount)
    }

    private func bootstrapStep() {
        if wizardCompleted {
            currentStep = .review
        } else if let s = WizardStep(rawValue: furthestStepRaw) {
            currentStep = s
        }
    }

    private func recordFurthest(_ step: WizardStep) {
        if step.rawValue > furthestStepRaw {
            furthestStepRaw = step.rawValue
        }
    }

    private func goNext() {
        switch currentStep {
        case .scene:
            jumpTo(sceneMode == .scenery ? .tone : .cast)
        case .cast:
            jumpTo(.tone)
        case .tone:
            jumpTo(.review)
        case .review:
            startCapture()
        }
    }

    private func goBack() {
        switch currentStep {
        case .scene:    break
        case .cast:     jumpTo(.scene)
        case .tone:     jumpTo(sceneMode == .scenery ? .scene : .cast)
        case .review:   jumpTo(.tone)
        }
    }

    private func jumpTo(_ step: WizardStep) {
        // Don't let users skip ahead past the furthest step they've reached.
        if step.rawValue > max(currentStep.rawValue, furthestStepRaw) { return }
        withAnimation(.spring(response: 0.45, dampingFraction: 0.82)) {
            currentStep = step
        }
        recordFurthest(step)
    }

    private func startCapture() {
        wizardCompleted = true
        recordFurthest(.review)
        router.push(.capture(
            personCount: effectivePersonCount,
            qualityMode: qualityMode,
            sceneMode: sceneMode,
            styleKeywords: parseKeywords(styleInput)
        ))
    }

    // ---------------------------------------------------------------------
    // Side effects on scene mode change
    // ---------------------------------------------------------------------

    private func applySceneMode(_ mode: SceneMode) {
        withAnimation(.spring(response: 0.34, dampingFraction: 0.78)) {
            sceneModeRaw = mode.rawValue
            if mode == .scenery {
                personCount = 0
            } else if personCount == 0 {
                personCount = 1
            }
        }
    }

    // ---------------------------------------------------------------------
    // Reuse-environment helpers
    // ---------------------------------------------------------------------

    private func refreshCachedMeta() {
        cachedMeta = CapturedFramesStore.peekMeta()
    }

    private func isReuseFresh(_ meta: CapturedFramesStore.Meta) -> Bool {
        Date().timeIntervalSince(meta.capturedAt) < CapturedFramesStore.maxAge
    }

    private var cachedSceneDisplayName: String {
        guard let raw = cachedMeta?.sceneMode else { return "" }
        return SceneMode(rawValue: raw)?.displayName ?? raw
    }

    /// Re-run /analyze using the previously cached panorama frames so the
    /// user can switch scene/tone settings without re-shooting.
    private func runReuseFlow() {
        guard let load = CapturedFramesStore.load(), !load.frames.isEmpty else {
            reuseError = "缓存已被清空，请重新拍摄"
            showReuseError = true
            cachedMeta = nil
            return
        }
        reuseBusy = true
        wizardCompleted = true

        Task { @MainActor in
            defer { reuseBusy = false }
            do {
                let frameMetas = load.meta.frames.map {
                    FrameMeta(
                        index: $0.index,
                        azimuthDeg: $0.azimuthDeg,
                        pitchDeg: $0.pitchDeg,
                        rollDeg: $0.rollDeg,
                        timestampMs: $0.timestampMs,
                        ambientLux: nil)
                }
                let meta = CaptureMeta(
                    personCount: effectivePersonCount,
                    qualityMode: qualityMode,
                    sceneMode: sceneMode,
                    styleKeywords: parseKeywords(styleInput),
                    frameMeta: frameMetas)
                let refs = await ReferenceImageStore.shared.activeThumbnailData(limit: 4)
                let cfg = ModelConfigStore.currentForRequest()
                let response = try await APIClient.shared.analyze(
                    meta: meta,
                    frames: load.frames,
                    referenceThumbnails: refs,
                    modelId: cfg.modelId.isEmpty ? nil : cfg.modelId,
                    modelApiKey: cfg.apiKey.isEmpty ? nil : cfg.apiKey,
                    modelBaseUrl: cfg.baseUrl.isEmpty ? nil : cfg.baseUrl)
                router.push(.results(response))
            } catch {
                let raw = error.localizedDescription
                let pretty: String = {
                    if raw.contains("503") || raw.localizedCaseInsensitiveContains("UNAVAILABLE") {
                        return "AI 当前繁忙（503），稍等几秒再点一次。"
                    }
                    if raw.localizedCaseInsensitiveContains("RESOURCE_EXHAUSTED") {
                        return "免费额度今天用完了，明天再来。"
                    }
                    return raw
                }()
                reuseError = pretty
                showReuseError = true
            }
        }
    }

    // ---------------------------------------------------------------------
    // Avatar persistence
    // ---------------------------------------------------------------------

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

// MARK: - Reusable wizard widgets ---------------------------------------------

/// 4-bead progress strip with connecting hairlines. Beads light up gold
/// when reached; the active bead pulses with a soft glow. Tap a completed
/// bead to jump back to that step.
struct WizardProgressBar: View {
    let currentStep: WizardStep
    let furthestStep: WizardStep
    let onTap: (WizardStep) -> Void

    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(WizardStep.allCases.enumerated()), id: \.element) { idx, step in
                bead(for: step)
                if idx < WizardStep.allCases.count - 1 {
                    line(after: step)
                }
            }
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func bead(for step: WizardStep) -> some View {
        let isActive = step == currentStep
        let isCompleted = step.rawValue < currentStep.rawValue || step.rawValue <= furthestStep.rawValue
        let canJump = step.rawValue <= furthestStep.rawValue || step.rawValue <= currentStep.rawValue

        Button {
            if canJump { onTap(step) }
        } label: {
            HStack(spacing: 8) {
                ZStack {
                    Circle()
                        .fill(isCompleted || isActive
                              ? AnyShapeStyle(CinemaTheme.accentGradient)
                              : AnyShapeStyle(Color.white.opacity(0.04)))
                        .frame(width: 26, height: 26)
                    Circle()
                        .stroke(
                            (isCompleted || isActive)
                                ? Color.clear
                                : CinemaTheme.borderSoft,
                            lineWidth: 1
                        )
                        .frame(width: 26, height: 26)
                    Text("\(step.rawValue)")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(isCompleted || isActive
                                         ? Color.black.opacity(0.85)
                                         : CinemaTheme.inkMuted)
                }
                .scaleEffect(isActive ? 1.08 : 1.0)
                .shadow(color: isActive ? CinemaTheme.accentWarm.opacity(0.55) : .clear,
                        radius: 8, y: 0)
                .animation(.spring(response: 0.4, dampingFraction: 0.7), value: isActive)
                Text(step.label)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(isActive
                                     ? CinemaTheme.ink
                                     : (isCompleted ? CinemaTheme.inkSoft : CinemaTheme.inkMuted))
            }
            .opacity(canJump ? 1.0 : 0.55)
        }
        .buttonStyle(.plain)
        .disabled(!canJump)
    }

    private func line(after step: WizardStep) -> some View {
        let cleared = step.rawValue < currentStep.rawValue
        return Rectangle()
            .fill(cleared
                  ? AnyShapeStyle(CinemaTheme.accentGradient)
                  : AnyShapeStyle(Color.white.opacity(0.12)))
            .frame(height: 1)
            .frame(maxWidth: .infinity)
    }
}

/// Scene poster card used in Step 1.
struct SceneCard: View {
    let mode: SceneMode
    let isActive: Bool
    let tap: () -> Void

    var body: some View {
        Button(action: tap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(glyph)
                        .font(.system(size: 22))
                    Spacer()
                    if isActive {
                        Text("已选 ✓")
                            .font(.system(size: 9.5, weight: .bold))
                            .kerning(1.6)
                            .foregroundStyle(CinemaTheme.accentWarm)
                    }
                }
                Text(mode.displayName)
                    .font(.system(size: 15, weight: .heavy))
                    .foregroundStyle(isActive ? CinemaTheme.ink : CinemaTheme.inkSoft)
                Text(mode.blurb)
                    .font(.system(size: 11.5))
                    .foregroundStyle(CinemaTheme.inkMuted)
                    .multilineTextAlignment(.leading)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(LinearGradient(colors: [Color.white.opacity(0.045),
                                                  Color.white.opacity(0.018)],
                                         startPoint: .top, endPoint: .bottom))
            )
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(isActive ? CinemaTheme.activeChipFill : LinearGradient(
                        colors: [.clear, .clear],
                        startPoint: .top, endPoint: .bottom
                    ))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(isActive ? CinemaTheme.accentWarm.opacity(0.55)
                                     : CinemaTheme.borderSoft,
                            lineWidth: 1)
            )
            .shadow(color: isActive ? CinemaTheme.accentWarm.opacity(0.32) : .clear,
                    radius: 16, y: 4)
        }
        .buttonStyle(.plain)
    }

    private var glyph: String {
        switch mode {
        case .portrait:     return "🎭"
        case .closeup:      return "👁"
        case .fullBody:     return "🚶"
        case .documentary:  return "📰"
        case .scenery:      return "🏔"
        case .lightShadow:  return "🌗"
        }
    }
}

/// Person count pill (Step 2).
struct PersonPill: View {
    let value: Int
    let isActive: Bool
    let tap: () -> Void

    var body: some View {
        Button(action: tap) {
            Text(value == 0 ? "0 人" : "\(value)")
                .font(.system(size: 17, weight: .bold))
                .frame(width: value == 0 ? 60 : 48, height: 48)
                .foregroundStyle(isActive ? CinemaTheme.ink : CinemaTheme.inkSoft)
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(isActive ? CinemaTheme.activeChipFill : LinearGradient(
                            colors: [Color.white.opacity(0.04), Color.white.opacity(0.04)],
                            startPoint: .top, endPoint: .bottom
                        ))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(isActive ? CinemaTheme.accentWarm.opacity(0.55)
                                         : CinemaTheme.borderSoft,
                                lineWidth: 1)
                )
                .shadow(color: isActive ? CinemaTheme.accentWarm.opacity(0.32) : .clear,
                        radius: 10, y: 3)
        }
        .buttonStyle(.plain)
    }
}

/// Quality preset card (Step 3).
struct QualityCard: View {
    let mode: QualityMode
    let title: String
    let sub: String
    let isActive: Bool
    let tap: () -> Void

    var body: some View {
        Button(action: tap) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 15, weight: .heavy))
                    .foregroundStyle(isActive ? CinemaTheme.ink : CinemaTheme.inkSoft)
                Text(sub)
                    .font(.system(size: 11.5))
                    .foregroundStyle(CinemaTheme.inkMuted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(isActive
                          ? AnyShapeStyle(LinearGradient(
                                colors: [CinemaTheme.accentCool.opacity(0.18),
                                         CinemaTheme.accentWarm.opacity(0.06)],
                                startPoint: .topLeading, endPoint: .bottomTrailing))
                          : AnyShapeStyle(Color.white.opacity(0.04)))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(isActive ? CinemaTheme.accentCool.opacity(0.55)
                                     : CinemaTheme.borderSoft,
                            lineWidth: 1)
            )
            .shadow(color: isActive ? CinemaTheme.accentCool.opacity(0.35) : .clear,
                    radius: 12, y: 4)
        }
        .buttonStyle(.plain)
    }
}

/// Tappable summary chip on the review screen.
struct SummaryChip: View {
    let label: String
    let value: String
    let tap: () -> Void

    var body: some View {
        Button(action: tap) {
            HStack(spacing: 12) {
                Text(label)
                    .font(.system(size: 10.5, weight: .bold))
                    .kerning(2.0)
                    .foregroundStyle(CinemaTheme.accentWarm)
                    .frame(width: 60, alignment: .leading)
                Text(value)
                    .font(.system(size: 14.5, weight: .semibold))
                    .foregroundStyle(CinemaTheme.ink)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.right")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(CinemaTheme.inkMuted)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(LinearGradient(colors: [Color.white.opacity(0.045),
                                                  Color.white.opacity(0.018)],
                                         startPoint: .top, endPoint: .bottom))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(CinemaTheme.borderSoft, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Reuse-environment chip --------------------------------------------

/// Step 4 chip that lets a returning user re-run /analyze using the
/// previously cached panorama frames. Mirrors `.reuse-chip` in the web PWA.
struct ReuseChip: View {
    let cachedSceneName: String
    let currentSceneName: String
    let sameScene: Bool
    let count: Int
    let relativeAge: String
    let isBusy: Bool
    let tap: () -> Void

    @State private var pulsePhase: Double = 0

    var body: some View {
        Button(action: tap) {
            HStack(spacing: 12) {
                Image(systemName: isBusy ? "hourglass" : "arrow.triangle.2.circlepath")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(CinemaTheme.accentWarm)
                    .symbolEffect(.pulse, isActive: isBusy)

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(CinemaTheme.ink)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    Text("\(count) 张 · \(relativeAge) · 上次拍的「\(cachedSceneName)」环境")
                        .font(.system(size: 11.5, weight: .medium))
                        .foregroundStyle(CinemaTheme.inkSoft)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: "arrow.right")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(CinemaTheme.accentWarm)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(LinearGradient(
                        colors: [CinemaTheme.accentWarm.opacity(0.16),
                                 CinemaTheme.accentCoral.opacity(0.10)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(CinemaTheme.accentWarm.opacity(isBusy ? 0.30 : 0.55),
                            lineWidth: 1)
            )
            .shadow(color: CinemaTheme.accentWarm.opacity(0.30 + 0.15 * pulsePhase),
                    radius: 16 + 6 * pulsePhase, y: 10)
        }
        .buttonStyle(.plain)
        .disabled(isBusy)
        .opacity(isBusy ? 0.7 : 1)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) {
                pulsePhase = 1
            }
        }
    }

    private var title: String {
        sameScene
            ? "上次环境帧还在 · 直接出方案"
            : "换成「\(currentSceneName)」用上次环境出方案"
    }
}

// MARK: - Style suggestion list (matches web pills)

enum StyleSuggestion {
    static let all: [String] = [
        "cinematic, moody",
        "clean, bright",
        "film, warm",
        "street, candid",
        "editorial, fashion",
    ]
}

// MARK: - Cinema theme tokens (single source of truth) -----------------------

enum CinemaTheme {
    static let bgBase = Color(red: 0x07/255.0, green: 0x07/255.0, blue: 0x0d/255.0)
    static let bgElev = Color(red: 0x0e/255.0, green: 0x0f/255.0, blue: 0x1c/255.0)
    static let ink = Color(red: 0xf5/255.0, green: 0xf4/255.0, blue: 0xee/255.0)
    static let inkSoft = Color.white.opacity(0.78)
    static let inkMuted = Color.white.opacity(0.40)
    static let borderSoft = Color.white.opacity(0.12)

    static let accentWarm = Color(red: 0xf4/255.0, green: 0xb8/255.0, blue: 0x60/255.0)
    static let accentCool = Color(red: 0x7c/255.0, green: 0x8c/255.0, blue: 0xff/255.0)
    static let accentCoral = Color(red: 0xff/255.0, green: 0x7a/255.0, blue: 0x6c/255.0)

    static let activeChipFill = LinearGradient(
        colors: [accentWarm.opacity(0.22), accentCoral.opacity(0.18)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    static let ctaGradient = LinearGradient(
        colors: [accentWarm, accentCoral, accentCool],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    static let accentGradient = LinearGradient(
        colors: [accentWarm, accentCoral],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    static let heroGradient = LinearGradient(
        colors: [.white, accentWarm, Color(red: 1, green: 0.85, blue: 0.66),
                 .white, Color(red: 0.71, green: 0.75, blue: 1.0), accentCool],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

/// Full-screen cinematic gradient backdrop used behind the home screen.
struct CinemaBackdrop: View {
    var body: some View {
        ZStack {
            CinemaTheme.bgBase.ignoresSafeArea()
            RadialGradient(
                colors: [CinemaTheme.accentCool.opacity(0.22), .clear],
                center: .topLeading, startRadius: 40, endRadius: 600
            )
            .blur(radius: 40)
            .ignoresSafeArea()
            RadialGradient(
                colors: [CinemaTheme.accentWarm.opacity(0.16), .clear],
                center: .topTrailing, startRadius: 40, endRadius: 500
            )
            .blur(radius: 40)
            .ignoresSafeArea()
            RadialGradient(
                colors: [CinemaTheme.accentCoral.opacity(0.10), .clear],
                center: .bottom, startRadius: 60, endRadius: 700
            )
            .blur(radius: 50)
            .ignoresSafeArea()
        }
    }
}

/// Glass section card with a tiny gold marker before its title.
/// Used on Cast / Tone / Review steps.
struct CinemaSection<Content: View>: View {
    let title: String
    let hint: String?
    @ViewBuilder var content: () -> Content

    init(title: String, hint: String? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.hint = hint
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 10) {
                Capsule()
                    .fill(LinearGradient(colors: [CinemaTheme.accentWarm,
                                                  CinemaTheme.accentCoral,
                                                  CinemaTheme.accentCool],
                                         startPoint: .leading, endPoint: .trailing))
                    .frame(width: 14, height: 2)
                Text(title.uppercased())
                    .font(.system(size: 11, weight: .bold))
                    .kerning(2.2)
                    .foregroundStyle(CinemaTheme.inkSoft)
                if let hint {
                    Text(hint)
                        .font(.system(size: 11))
                        .foregroundStyle(CinemaTheme.inkMuted)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                Spacer(minLength: 0)
            }

            content()
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 18)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(.ultraThinMaterial)
        )
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(LinearGradient(colors: [Color.white.opacity(0.045),
                                              Color.white.opacity(0.018)],
                                     startPoint: .top, endPoint: .bottom))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(CinemaTheme.borderSoft, lineWidth: 1)
        )
        .overlay(
            VStack {
                LinearGradient(colors: [.clear,
                                        Color.white.opacity(0.20),
                                        Color.white.opacity(0.10),
                                        .clear],
                               startPoint: .leading, endPoint: .trailing)
                    .frame(height: 1)
                Spacer()
            }
            .padding(.horizontal, 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(0.4), radius: 20, x: 0, y: 8)
    }
}

/// Springy press animation for the primary CTA button.
private struct CTAButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1.0)
            .brightness(configuration.isPressed ? -0.04 : 0)
            .animation(.spring(response: 0.22, dampingFraction: 0.78), value: configuration.isPressed)
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
        ZStack {
            CinemaBackdrop()
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
                                    .foregroundStyle(CinemaTheme.ink)
                                Text(style.summary).font(.caption2).foregroundStyle(CinemaTheme.inkMuted)
                                    .multilineTextAlignment(.center)
                            }
                            .padding(8)
                            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
                            .overlay(
                                RoundedRectangle(cornerRadius: 14)
                                    .stroke(style.id == currentId
                                            ? CinemaTheme.accentWarm
                                            : CinemaTheme.borderSoft,
                                            lineWidth: style.id == currentId ? 2 : 1)
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding()
            }
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
