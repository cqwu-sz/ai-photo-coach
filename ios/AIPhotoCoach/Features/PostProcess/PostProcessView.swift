// PostProcessView.swift (W10.3)
//
// Main editing screen: filter strip + 5 beauty sliders + before/after
// preview + save to PHAsset (as a copy, original preserved).

import SwiftUI
import Photos
import UIKit

@MainActor
final class PostProcessModel: ObservableObject {
    @Published var preset: FilterPreset = .original
    @Published var beauty = BeautyParams()
    @Published var rendered: UIImage
    @Published var showOriginal = false
    @Published var saveStatus: String?
    /// Backend-recommended LUT id, if any. Threaded through to
    /// ``FilterEngine.apply(_:lutId:to:)`` on every rerender so the
    /// LUT chains *after* the preset's CIFilter stack.
    @Published var lutId: String?
    /// Whether the current ``preset`` / ``beauty`` / ``lutId`` came
    /// from the backend recipe (true) or the user has manually edited
    /// (false). Lets the UI show a "已套用 AI 推荐" hint when true.
    @Published private(set) var recipeApplied: Bool = false
    /// Original recipe captured at init time — surfaced as a "重设回 AI 推荐" button.
    let recipe: PostProcessRecipe?

    let original: UIImage
    private let filterEngine = FilterEngine()
    private let beautyEngine = BeautyEngine()

    init(original: UIImage, recipe: PostProcessRecipe? = nil) {
        self.original = original
        self.rendered = original
        self.recipe = recipe
        if let recipe {
            self.applyRecipe(recipe, markAsApplied: true, rerenderImmediately: true)
        }
    }

    /// Apply (or re-apply) the backend recipe. ``rerenderImmediately``
    /// defaults to true; call with false when you're already inside an
    /// init / explicit rerender to avoid double work.
    func applyRecipe(_ recipe: PostProcessRecipe, markAsApplied: Bool = true,
                      rerenderImmediately: Bool = true) {
        self.preset = FilterPreset.from(recipeKey: recipe.filterPreset)
        self.beauty = BeautyParams.fromIntensity(recipe.beautyIntensity)
        self.lutId = recipe.lutId
        self.recipeApplied = markAsApplied
        if rerenderImmediately { self.rerender() }
    }

    func markUserOverride() {
        if recipeApplied { recipeApplied = false }
    }

    func rerender() {
        var img = filterEngine.apply(preset, lutId: lutId, to: original)
        img = beautyEngine.apply(beauty, to: img)
        self.rendered = img
    }

    func save() async {
        let img = self.rendered
        let ok = await PHPhotoLibrary.shared().performChangesAsync {
            PHAssetChangeRequest.creationRequestForAsset(from: img)
        }
        self.saveStatus = ok ? "已保存到相册" : "保存失败（请检查相册权限）"
    }
}

private extension PHPhotoLibrary {
    func performChangesAsync(_ block: @escaping () -> Void) async -> Bool {
        await withCheckedContinuation { c in
            performChanges(block) { ok, _ in c.resume(returning: ok) }
        }
    }
}

struct PostProcessView: View {
    @StateObject var model: PostProcessModel
    @State private var isProActive: Bool = false
    @State private var showPaywall: Bool = false
    @State private var paywallError: String? = nil

    var body: some View {
        VStack(spacing: 12) {
            previewArea
                .gesture(LongPressGesture(minimumDuration: 0.1)
                    .onChanged { _ in model.showOriginal = true }
                    .onEnded { _ in model.showOriginal = false })
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(FilterPreset.allCases) { p in
                        Button {
                            if p.requiresPro {
                                // A1-paywall-refresh: force a server
                                // round-trip the moment the user pokes
                                // a Pro filter — entitlement may have
                                // flipped (refund / expiry) since the
                                // 10-min cache filled.
                                Task {
                                    let pro = await IAPManager.shared.paywallGate()
                                    isProActive = pro
                                    if !pro {
                                        showPaywall = true
                                        return
                                    }
                                    model.preset = p
                                    model.markUserOverride()
                                    model.rerender()
                                }
                                return
                            }
                            model.preset = p
                            model.markUserOverride()
                            model.rerender()
                        } label: {
                            HStack(spacing: 4) {
                                if p.requiresPro && !isProActive {
                                    Image(systemName: "lock.fill")
                                        .font(.caption2)
                                }
                                Text(p.label)
                                    .font(.caption.weight(.semibold))
                            }
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(p == model.preset ? .blue.opacity(0.25) : .gray.opacity(0.15),
                                        in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }.padding(.horizontal)
            }
            beautySliders
            if model.recipeApplied, let rationale = model.recipe?.rationaleZh {
                Text("AI 推荐：\(rationale)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal)
                    .multilineTextAlignment(.leading)
            }
            HStack {
                Button {
                    model.preset = .original
                    model.beauty = BeautyParams()
                    model.lutId = nil
                    model.markUserOverride()
                    model.rerender()
                } label: {
                    Label("重置", systemImage: "arrow.uturn.backward")
                }
                if let recipe = model.recipe, !model.recipeApplied {
                    Button {
                        model.applyRecipe(recipe)
                    } label: {
                        Label("AI 推荐", systemImage: "sparkles")
                    }
                }
                Spacer()
                Button {
                    Task { await model.save() }
                } label: {
                    Label("保存到相册", systemImage: "square.and.arrow.down")
                        .frame(maxWidth: 200)
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(.horizontal)
            if let s = model.saveStatus {
                Text(s).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical)
        .task {
            isProActive = await IAPManager.shared.isProActive
        }
        .sheet(isPresented: $showPaywall) {
            // PR7: route the legacy single-product paywall through
            // the unified three-tier PaywallView. Detents stay
            // friendly; PaywallView itself drives its own scroll.
            PaywallView()
                .presentationDetents([.large])
        }
    }

    private var paywallSheet: some View {
        VStack(spacing: 14) {
            Image(systemName: "sparkles")
                .font(.system(size: 44))
                .foregroundStyle(.tint)
            Text("拾光 Pro")
                .font(.title3.weight(.semibold))
            Text("解锁电影感 / 港风 / 复古褪色 / 胶片暖 4 个高级滤镜，以及未来上线的所有 Pro 修图能力。")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 8)
            Button {
                Task {
                    do {
                        try await IAPManager.shared.purchasePro()
                        isProActive = await IAPManager.shared.isProActive
                        showPaywall = false
                    } catch {
                        paywallError = error.localizedDescription
                    }
                }
            } label: {
                Text("¥18 / 月 · 立即解锁")
                    .font(.callout.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)
            Button("已经购买？恢复") {
                Task {
                    await IAPManager.shared.restore()
                    isProActive = await IAPManager.shared.isProActive
                }
            }
            .font(.footnote)
            if let err = paywallError {
                Text(err).font(.caption2).foregroundStyle(.red)
            }
        }
        .padding(20)
    }

    private var previewArea: some View {
        Image(uiImage: model.showOriginal ? model.original : model.rendered)
            .resizable()
            .scaledToFit()
            .frame(maxHeight: 420)
            .overlay(alignment: .topTrailing) {
                if model.showOriginal {
                    Text("原图")
                        .font(.caption2.weight(.bold))
                        .padding(6)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(8)
                }
            }
    }

    /// P1-9.4 — when face mesh warp isn't shipped yet (default), hide
    /// the slim/enlargeEye sliders so we don't promise something that
    /// doesn't actually move pixels. Toggle the flag via UserDefaults
    /// once Vision-mesh warp lands (P1-9.3).
    private var meshWarpAvailable: Bool {
        UserDefaults.standard.bool(forKey: "ai_photo.beauty.meshWarp")
    }

    private var beautySliders: some View {
        VStack(spacing: 6) {
            slider("磨皮", value: $model.beauty.smooth)
            slider("美白", value: $model.beauty.brighten)
            if meshWarpAvailable {
                slider("瘦脸", value: $model.beauty.slim)
                slider("大眼", value: $model.beauty.enlargeEye)
            }
            slider("亮眼", value: $model.beauty.brightenEye)
        }
        .padding(.horizontal)
    }

    private func slider(_ name: String, value: Binding<Double>) -> some View {
        HStack {
            Text(name).font(.caption).frame(width: 36, alignment: .leading)
            Slider(value: value, in: 0...1) { editing in
                if !editing {
                    model.markUserOverride()
                    model.rerender()
                }
            }
            Text("\(Int(value.wrappedValue * 100))")
                .font(.caption.monospacedDigit())
                .frame(width: 28, alignment: .trailing)
        }
    }
}
