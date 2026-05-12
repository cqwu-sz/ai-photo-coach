// AppIconPickerView.swift
//
// In-app picker for swapping between the default app icon (direction 2,
// "light finds her") and the alternate icon (direction 1, "saturated
// sunset scene"). Apple requires alternate icons to be switched at runtime
// via UIApplication.setAlternateIconName — they are *not* exposed by the
// system Settings app, so without this screen the alternate icon would
// just sit dead in the bundle.
//
// Also responsible for:
//   - persisting the user's icon choice (AppStorage so it survives reinstall
//     of the same build),
//   - sending a one-shot telemetry ping the first time the user actually
//     changes the icon (lets us see whether anyone cares about the feature
//     before we invest in more skins),
//   - showing a short "拾光 = 替你抓住光线" story overlay the very first
//     time the screen is opened, turning the icon into a brand moment
//     instead of a hidden setting.

import SwiftUI
import UIKit

struct AppIconOption: Identifiable, Hashable {
    /// `nil` means the primary (default) AppIcon.
    let alternateName: String?
    let id: String
    let title: String
    let tagline: String
    let previewAssetName: String

    static let `default` = AppIconOption(
        alternateName: nil,
        id: "default",
        title: "光找到她",
        tagline: "AI 替你抓住那束让画面成立的光。",
        previewAssetName: "IconPreview-Default"
    )
    static let sunset = AppIconOption(
        alternateName: "AppIcon-Sunset",
        id: "sunset",
        title: "夕阳前的她",
        tagline: "黄昏之前的最后一刻光线 —— 经典构图。",
        previewAssetName: "IconPreview-Sunset"
    )

    static let all: [AppIconOption] = [.default, .sunset]
}

struct AppIconPickerView: View {
    @AppStorage("aphc.appIcon.choice") private var storedChoice: String = AppIconOption.default.id
    @AppStorage("aphc.appIcon.storyShown") private var storyShown: Bool = false

    @State private var pending: AppIconOption?
    @State private var error: String?
    @State private var showStory: Bool = false

    var body: some View {
        Form {
            Section {
                ForEach(AppIconOption.all) { option in
                    IconRow(
                        option: option,
                        isSelected: storedChoice == option.id,
                        isBusy: pending?.id == option.id,
                        onTap: { switchTo(option) }
                    )
                }
            } header: {
                Text("挑一张你想每天打开的图")
            } footer: {
                Text("更换图标时 iOS 会闪一下确认弹窗，这是系统行为，没办法跳过。")
            }

            if let error {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                }
            }
        }
        .navigationTitle("外观与图标")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear(perform: bootstrap)
        .overlay {
            if showStory {
                StoryOverlay(onDismiss: {
                    withAnimation(.easeOut(duration: 0.25)) { showStory = false }
                    storyShown = true
                })
                .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: showStory)
    }

    private func bootstrap() {
        // Reconcile stored choice with what iOS actually reports — if the
        // user wiped the app or changed icon from a different build, we
        // honour the system's truth.
        let current = UIApplication.shared.alternateIconName
        let live = AppIconOption.all.first(where: { $0.alternateName == current }) ?? .default
        if storedChoice != live.id { storedChoice = live.id }

        if !storyShown {
            // Delay so the navigation push animation finishes first.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                withAnimation(.easeIn(duration: 0.25)) { showStory = true }
            }
        }
    }

    private func switchTo(_ option: AppIconOption) {
        guard option.id != storedChoice, pending == nil else { return }
        guard UIApplication.shared.supportsAlternateIcons else {
            error = "当前设备不支持切换 App 图标。"
            return
        }
        pending = option
        error = nil
        let previous = storedChoice
        UIApplication.shared.setAlternateIconName(option.alternateName) { err in
            Task { @MainActor in
                pending = nil
                if let err {
                    error = "切换失败：\(err.localizedDescription)"
                    return
                }
                storedChoice = option.id
                AppIconTelemetry.recordChange(from: previous, to: option.id)
            }
        }
    }
}

// MARK: - Telemetry shim

/// Lightweight, network-free preference recorder. We only ship the local
/// counter for now — when we wire up a real analytics backend, replace the
/// implementation here and every call site keeps working.
enum AppIconTelemetry {
    private static let keyChangeCount = "aphc.appIcon.changeCount"
    private static let keyLatestChoice = "aphc.appIcon.latestChoice"
    private static let keyFirstChangeAt = "aphc.appIcon.firstChangeAt"

    static func recordChange(from old: String, to new: String) {
        let d = UserDefaults.standard
        d.set(d.integer(forKey: keyChangeCount) + 1, forKey: keyChangeCount)
        d.set(new, forKey: keyLatestChoice)
        if d.object(forKey: keyFirstChangeAt) == nil {
            d.set(Date(), forKey: keyFirstChangeAt)
        }
        #if DEBUG
        print("[icon-telemetry] \(old) → \(new) (total changes: \(d.integer(forKey: keyChangeCount)))")
        #endif
    }
}

// MARK: - Subviews

private struct IconRow: View {
    let option: AppIconOption
    let isSelected: Bool
    let isBusy: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 14) {
                IconThumb(assetName: option.previewAssetName)
                    .frame(width: 56, height: 56)
                VStack(alignment: .leading, spacing: 4) {
                    Text(option.title)
                        .font(.system(size: 15, weight: .semibold))
                    Text(option.tagline)
                        .font(.system(size: 12.5))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                if isBusy {
                    ProgressView()
                } else if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Color.accentColor)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isBusy)
    }
}

/// Renders the icon's 1024 art directly from the asset catalog. We can't use
/// `Image("AppIcon")` because Apple deliberately blocks loading the bundle
/// icon by name from code, so we ship a UIImage-backed copy via the
/// `previewAssetName` (which points at the 1024 PNG embedded as a regular
/// imageset under Assets.xcassets/IconPreviews/).
private struct IconThumb: View {
    let assetName: String

    var body: some View {
        Group {
            if let image = UIImage(named: assetName) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                // Fallback so a missing imageset still draws something
                // recognisable instead of a blank rect.
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(LinearGradient(
                        colors: [Color.orange.opacity(0.5), Color.purple.opacity(0.5)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing))
                    .overlay(
                        Image(systemName: "camera.aperture")
                            .foregroundStyle(.white.opacity(0.9))
                    )
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
        )
    }
}

/// One-shot brand story shown the very first time the picker opens. Keeps
/// the icon picker from feeling like a leftover settings toggle — it's the
/// only place in the app where we tell the user *why* the product is named
/// 拾光.
private struct StoryOverlay: View {
    let onDismiss: () -> Void

    var body: some View {
        ZStack {
            Color.black.opacity(0.55)
                .ignoresSafeArea()
                .onTapGesture(perform: onDismiss)

            VStack(spacing: 16) {
                Image(systemName: "sun.max.fill")
                    .font(.system(size: 32, weight: .bold))
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, Color.orange)

                Text("拾光")
                    .font(.system(size: 28, weight: .heavy))
                    .tracking(4)

                Text("「替你抓住光线遇见画面的瞬间。」")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)

                Text("这两张图标分别讲了一个故事：默认那张是「光找到她」，备选那张是「她在落日前」。挑你今天想见到的那束光。")
                    .font(.system(size: 12.5))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 8)

                Button(action: onDismiss) {
                    Text("开始挑光")
                        .font(.system(size: 14, weight: .semibold))
                        .padding(.horizontal, 24)
                        .padding(.vertical, 10)
                        .background(
                            Capsule().fill(Color.orange.opacity(0.85))
                        )
                        .foregroundStyle(.white)
                }
                .padding(.top, 4)
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 28)
            .background(
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(.ultraThinMaterial)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            )
            .padding(.horizontal, 32)
        }
    }
}
