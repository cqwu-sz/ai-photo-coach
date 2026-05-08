// v7 Phase E — iOS avatar gallery picker.
//
// Replaces the v6 procedural AvatarStyle picker with the 8 ReadyPlayerMe
// presets. Picks are stored in UserDefaults under "avatarPicks" — the
// same key shape the web client uses (a JSON array of preset ids), so
// users see consistent character choices across web + iOS.
//
// Falls back to the legacy AvatarPresets list when the manifest can't
// be loaded (no network, fresh install before backend handshake), so
// the picker never shows an empty gallery.

import SwiftUI

struct AvatarGalleryView: View {
    /// How many avatar slots to show (== personCount of the upcoming
    /// shoot). Set to 0 for scenery mode.
    let slotCount: Int

    @StateObject private var manifest = AvatarManifest.shared
    @State private var picks: [String] = []
    @State private var activeSlot: Int = 0
    @State private var didLoad = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if slotCount > 0 {
                slotStrip
            }
            gallery
        }
        .padding()
        .navigationTitle("挑选角色")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadManifestIfNeeded() }
    }

    // MARK: - Slot strip

    private var slotStrip: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("分配到 \(slotCount) 个出镜位")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(0..<slotCount, id: \.self) { idx in
                        slotCell(idx: idx)
                    }
                }
            }
        }
    }

    private func slotCell(idx: Int) -> some View {
        let id = picks.indices.contains(idx) ? picks[idx] : ""
        let preset = manifest.payload?.presets.first(where: { $0.id == id })
        return Button {
            withAnimation { activeSlot = idx }
        } label: {
            VStack(spacing: 4) {
                ZStack(alignment: .bottomTrailing) {
                    avatarThumb(presetId: id)
                        .frame(width: 64, height: 80)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10)
                                .stroke(activeSlot == idx
                                        ? Color.accentColor
                                        : Color.primary.opacity(0.10),
                                        lineWidth: activeSlot == idx ? 2.5 : 1)
                        )
                    Text("\(idx + 1)")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(Color.accentColor))
                        .padding(4)
                }
                Text(preset?.nameZh ?? "未选")
                    .font(.caption2)
                    .lineLimit(1)
                    .frame(maxWidth: 80)
            }
        }
        .buttonStyle(.plain)
    }

    // MARK: - Gallery grid

    private var gallery: some View {
        let presets = manifest.payload?.presets ?? []
        return ScrollView {
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 96), spacing: 12)],
                spacing: 14,
            ) {
                ForEach(presets, id: \.id) { p in
                    galleryCell(preset: p)
                }
            }
            .padding(.top, 4)
        }
    }

    private func galleryCell(preset: AvatarPresetEntry) -> some View {
        let active = picks.indices.contains(activeSlot) ? picks[activeSlot] == preset.id : false
        return Button {
            assignToActiveSlot(preset.id)
        } label: {
            VStack(spacing: 6) {
                avatarThumb(presetId: preset.id)
                    .frame(width: 86, height: 110)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(active
                                    ? Color.accentColor
                                    : Color.primary.opacity(0.08),
                                    lineWidth: active ? 3 : 1)
                    )
                Text(preset.nameZh)
                    .font(.footnote.weight(.semibold))
                    .lineLimit(1)
                Text("\(preset.style) · \(preset.gender == "female" ? "女" : "男")")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .buttonStyle(.plain)
    }

    private func avatarThumb(presetId: String) -> some View {
        let preset = manifest.payload?.presets.first { $0.id == presetId }
        let urlString: String? = preset?.thumbnail.map(absoluteThumbURL)
        return AsyncImage(
            url: urlString.flatMap(URL.init(string:))
        ) { phase in
            switch phase {
            case .success(let image):
                image.resizable().scaledToFill()
            default:
                ZStack {
                    Color.primary.opacity(0.06)
                    Image(systemName: "person.crop.rectangle")
                        .font(.title3)
                        .foregroundStyle(Color.primary.opacity(0.30))
                }
            }
        }
    }

    private func absoluteThumbURL(_ rel: String) -> String {
        if rel.hasPrefix("http") { return rel }
        let base = UserDefaults.standard.string(forKey: "apiBaseURL")
            ?? "http://127.0.0.1:8000"
        return base.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + rel
    }

    // MARK: - Loading + persistence

    @MainActor
    private func loadManifestIfNeeded() async {
        if didLoad { return }
        didLoad = true
        await manifest.load()
        // Read persisted picks, padding to slotCount with sensible
        // defaults from the rotation order.
        let saved = UserDefaults.standard.stringArray(forKey: "avatarPicks") ?? []
        picks = (0..<max(slotCount, 1)).map { i in
            if i < saved.count, !saved[i].isEmpty { return saved[i] }
            return AvatarPicker.pick(
                personIndex: i,
                from: manifest.payload?.presets ?? [],
            ) ?? ""
        }
        if slotCount == 0 { picks = [] }
        persist()
    }

    private func assignToActiveSlot(_ presetId: String) {
        guard slotCount > 0 else { return }
        if !picks.indices.contains(activeSlot) {
            picks.append(contentsOf: Array(repeating: "", count: max(0, activeSlot - picks.count + 1)))
        }
        picks[activeSlot] = presetId
        persist()
        // Auto-advance to keep the user moving through the slots.
        if activeSlot < slotCount - 1 {
            withAnimation { activeSlot += 1 }
        }
    }

    private func persist() {
        UserDefaults.standard.set(picks, forKey: "avatarPicks")
    }
}
