// ARGuideSettings.swift
//
// User-facing toggle that controls how the Stage-2 ghost avatar is
// rendered in live AR. Persists in UserDefaults via @AppStorage so
// both the preview screen and the AR session can read the same key
// without an explicit DI graph.

import SwiftUI

enum ARGuideSettingsKeys {
    static let ghostMode    = "ar.ghostMode"
    /// Persists "只显示脚印" privacy mode. When true, ghost avatars
    /// are not rendered even after reaching framing stage; only the
    /// disc + arrow are visible.
    static let privacyMode  = "ar.privacyMode"
    /// Set after the first-run intro sheet is dismissed.
    static let didShowIntro = "ar.didShowIntro"
    /// Set after the user has been shown the post-capture trust sheet
    /// once. We only nag once per device.
    static let didShowTrust = "ar.didShowTrust"
    /// When true, ShotNavigationView hands off to ARGuideView after the
    /// user reaches the recommended position so the dedicated alignment
    /// + USDZ subject pipeline takes over. When false, ShotNavigationView
    /// renders its own GhostAvatar inline (legacy path).
    static let handoffToGuide = "ar.handoffToGuide"
}

extension GhostAvatarEntity.RenderMode {
    static var current: GhostAvatarEntity.RenderMode {
        let raw = UserDefaults.standard.string(forKey: ARGuideSettingsKeys.ghostMode)
            ?? GhostAvatarEntity.RenderMode.ghost.rawValue
        return GhostAvatarEntity.RenderMode(rawValue: raw) ?? .ghost
    }
}

/// Pre-warm helpers used by callers that *know* the user is about to
/// enter an AR-guided shoot but hasn't pushed the navigation view yet
/// (e.g. when they tap the recommendation card). Calling these
/// functions early hides the manifest fetch latency behind whatever
/// transition animation is on-screen.
@MainActor
enum ARGuidePreWarm {
    private static var didPreWarm = false

    /// Fire-and-forget. Idempotent — calling repeatedly is cheap; the
    /// manifest is cached in-memory after the first hit. Also pre-
    /// loads the USDZ for the most likely avatar pick so the
    /// Stage-2 Ghost can mount instantly when the user reaches the
    /// recommended position.
    static func preWarmManifest() {
        guard !didPreWarm else { return }
        didPreWarm = true
        Task { @MainActor in
            let payload = await AvatarManifest.shared.load()
            let presetId = AvatarPicker.pick(
                personIndex: 0,
                from: payload?.presets ?? [],
            ) ?? "female_youth_18"
            // Ignore the result — we just want the cache populated.
            // Errors here are non-fatal; ShotNavigationModel will
            // happily fall through to its own load if this didn't
            // run / didn't finish in time.
            _ = try? await AvatarLoader.shared.load(presetId: presetId)
        }
    }
}

struct ARGuideSettingsView: View {
    @AppStorage(ARGuideSettingsKeys.ghostMode)
    private var rawMode: String = GhostAvatarEntity.RenderMode.ghost.rawValue
    @AppStorage(ARGuideSettingsKeys.privacyMode)
    private var privacyMode: Bool = false
    @AppStorage(ARGuideSettingsKeys.handoffToGuide)
    private var handoffToGuide: Bool = true

    var body: some View {
        Form {
            Section {
                Picker("AR 引导虚拟人", selection: $rawMode) {
                    Text("半透明幽灵").tag(GhostAvatarEntity.RenderMode.ghost.rawValue)
                    Text("实体 · 靠近淡出").tag(GhostAvatarEntity.RenderMode.solidFade.rawValue)
                }
                .pickerStyle(.inline)
            } header: {
                Text("实拍引导虚拟人")
            } footer: {
                Text("半透明幽灵适合大多数场景；实体淡出在光线很强或需要更直观对位时更清晰，但要求设备支持人物分割。")
            }

            Section {
                Toggle("只显示脚印（不渲染虚拟人）", isOn: $privacyMode)
            } footer: {
                Text("启用后只展示发光圆圈和朝向箭头，不在屏幕上渲染虚拟人。适合不喜欢看到 3D 模型或希望最大化真实场景可见度的场景。")
            }

            Section {
                Toggle("到位后自动切到对位画面", isOn: $handoffToGuide)
            } footer: {
                Text("打开（推荐）：走到推荐机位后，自动切到专门的对位画面，那里会有更精确的虚拟人和水平/角度提示。关掉：留在当前画面，所有对位提示都在原地完成（适合熟悉流程的老用户）。")
            }

            Section {
                Button {
                    UserDefaults.standard.removeObject(
                        forKey: ARGuideSettingsKeys.didShowIntro)
                } label: {
                    Label("重新看 AR 引导初次说明",
                          systemImage: "sparkles")
                }
                Button {
                    UserDefaults.standard.removeObject(
                        forKey: ARGuideSettingsKeys.didShowTrust)
                } label: {
                    Label("重新看「拍摄后信任对比」",
                          systemImage: "checkmark.shield")
                }
            } footer: {
                Text("两个按钮独立——只想复习其中一个时不必清空另一个的状态。")
            }
        }
        .navigationTitle("AR 引导")
    }
}
