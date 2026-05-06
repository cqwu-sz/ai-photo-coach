import PhotosUI
import SwiftUI

struct ReferenceLibraryView: View {
    @StateObject private var store = ReferenceImageStore.shared
    @State private var pickerItems: [PhotosPickerItem] = []
    @State private var isImporting = false
    @State private var importError: String?

    private let columns = [GridItem(.adaptive(minimum: 120), spacing: 12)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header
                if store.entries.isEmpty {
                    emptyState
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(store.entries) { entry in
                            ReferenceTile(entry: entry,
                                          onToggle: { store.setActive(!entry.active, for: entry.id) },
                                          onDelete: { store.remove(id: entry.id) })
                        }
                    }
                    .padding(.horizontal, 4)
                }
            }
            .padding()
        }
        .navigationTitle("我的参考图")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                PhotosPicker(selection: $pickerItems,
                             maxSelectionCount: 10,
                             matching: .images) {
                    Image(systemName: "plus")
                }
            }
        }
        .onChange(of: pickerItems) { _, newItems in
            Task { await importPicked(newItems) }
        }
        .alert("导入失败", isPresented: Binding(
            get: { importError != nil },
            set: { if !$0 { importError = nil } }
        )) {
            Button("好的", role: .cancel) {}
        } message: {
            Text(importError ?? "")
        }
        .overlay {
            if isImporting {
                ProgressView("导入中...")
                    .padding()
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("收藏喜欢的拍照风格图，下次分析会按你的风格出方案。")
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("\(store.entries.filter { $0.active }.count) 张参与训练 / 共 \(store.entries.count) 张")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "photo.on.rectangle.angled")
                .font(.system(size: 48))
                .foregroundStyle(.tertiary)
            Text("还没有参考图")
                .font(.headline)
            Text("点右上角 + 从相册导入喜欢的样片")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    private func importPicked(_ items: [PhotosPickerItem]) async {
        guard !items.isEmpty else { return }
        isImporting = true
        defer {
            isImporting = false
            pickerItems = []
        }

        for item in items {
            do {
                guard let data = try await item.loadTransferable(type: Data.self),
                      let img = UIImage(data: data) else { continue }
                _ = store.add(image: img)

                let entryId = store.entries.first?.id
                if let entryId, let vec = await CLIPEmbedder.shared.embed(image: img) {
                    store.updateEmbedding(vec, for: entryId)
                }
            } catch {
                importError = error.localizedDescription
            }
        }
    }
}

private struct ReferenceTile: View {
    let entry: ReferenceImageEntry
    let onToggle: () -> Void
    let onDelete: () -> Void

    var body: some View {
        ZStack(alignment: .topTrailing) {
            if let img = UIImage(contentsOfFile: entry.thumbPath) {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFill()
                    .frame(height: 120)
                    .clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .opacity(entry.active ? 1 : 0.4)
            }

            VStack(spacing: 6) {
                Button(action: onToggle) {
                    Image(systemName: entry.active ? "checkmark.circle.fill" : "circle")
                        .foregroundColor(entry.active ? .green : .white)
                        .background(.black.opacity(0.4), in: Circle())
                }
                Button(action: onDelete) {
                    Image(systemName: "trash.fill")
                        .foregroundColor(.white)
                        .padding(6)
                        .background(.black.opacity(0.4), in: Circle())
                }
            }
            .padding(6)
        }
    }
}
