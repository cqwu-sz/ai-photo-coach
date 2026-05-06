import Foundation
import SQLite3
import UIKit

/// On-device storage for the user's "favorite reference images".
/// We deliberately keep this fully local for privacy / legal reasons.
/// Each image is saved as:
///   - full image -> Documents/References/<uuid>.jpg
///   - thumbnail -> Documents/References/<uuid>_thumb.jpg
///   - row in references.sqlite with metadata (and Phase 2 embedding)
@MainActor
final class ReferenceImageStore: ObservableObject {
    static let shared = ReferenceImageStore()

    @Published private(set) var entries: [ReferenceImageEntry] = []

    private let db: OpaquePointer?
    private let baseDir: URL

    init() {
        let fm = FileManager.default
        let docs = (try? fm.url(for: .documentDirectory,
                                in: .userDomainMask,
                                appropriateFor: nil,
                                create: true)) ?? URL(fileURLWithPath: NSTemporaryDirectory())
        baseDir = docs.appendingPathComponent("References", isDirectory: true)
        try? fm.createDirectory(at: baseDir, withIntermediateDirectories: true)

        let dbURL = docs.appendingPathComponent("references.sqlite")
        var handle: OpaquePointer?
        if sqlite3_open(dbURL.path, &handle) == SQLITE_OK {
            self.db = handle
            createSchemaIfNeeded()
            reload()
        } else {
            self.db = nil
        }
    }

    deinit {
        if let db { sqlite3_close(db) }
    }

    private func createSchemaIfNeeded() {
        let sql = """
        CREATE TABLE IF NOT EXISTS references_v1 (
            id TEXT PRIMARY KEY,
            full_path TEXT NOT NULL,
            thumb_path TEXT NOT NULL,
            created_at REAL NOT NULL,
            tag TEXT,
            embedding BLOB,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
        sqlite3_exec(db, sql, nil, nil, nil)
    }

    func reload() {
        entries = fetchAll()
    }

    @discardableResult
    func add(image: UIImage, tag: String? = nil) -> ReferenceImageEntry? {
        guard let full = image.jpegData(compressionQuality: 0.85) else { return nil }
        let id = UUID().uuidString
        let fullURL = baseDir.appendingPathComponent("\(id).jpg")
        let thumbURL = baseDir.appendingPathComponent("\(id)_thumb.jpg")
        let thumb = image.thumbnail(maxSide: 256)
        guard let thumbData = thumb.jpegData(compressionQuality: 0.7) else { return nil }

        do {
            try full.write(to: fullURL)
            try thumbData.write(to: thumbURL)
        } catch {
            return nil
        }

        let entry = ReferenceImageEntry(
            id: id,
            fullPath: fullURL.path,
            thumbPath: thumbURL.path,
            createdAt: Date(),
            tag: tag,
            embedding: nil,
            active: true
        )
        insert(entry)
        reload()
        return entry
    }

    func remove(id: String) {
        guard let entry = entries.first(where: { $0.id == id }) else { return }
        try? FileManager.default.removeItem(atPath: entry.fullPath)
        try? FileManager.default.removeItem(atPath: entry.thumbPath)
        let sql = "DELETE FROM references_v1 WHERE id = ?;"
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            sqlite3_bind_text(stmt, 1, (id as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
        }
        sqlite3_finalize(stmt)
        reload()
    }

    func setActive(_ active: Bool, for id: String) {
        let sql = "UPDATE references_v1 SET active = ? WHERE id = ?;"
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            sqlite3_bind_int(stmt, 1, active ? 1 : 0)
            sqlite3_bind_text(stmt, 2, (id as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
        }
        sqlite3_finalize(stmt)
        reload()
    }

    func updateEmbedding(_ vector: [Float], for id: String) {
        var data = Data(count: vector.count * MemoryLayout<Float>.size)
        data.withUnsafeMutableBytes { buf in
            _ = vector.withUnsafeBytes { src in
                buf.copyMemory(from: src)
            }
        }
        let sql = "UPDATE references_v1 SET embedding = ? WHERE id = ?;"
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            data.withUnsafeBytes { rawBuf in
                guard let p = rawBuf.baseAddress else { return }
                sqlite3_bind_blob(stmt, 1, p, Int32(data.count), nil)
            }
            sqlite3_bind_text(stmt, 2, (id as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
        }
        sqlite3_finalize(stmt)
        reload()
    }

    /// Returns thumbnail JPEG data for the top-N "active" entries — used by
    /// EnvCaptureViewModel when uploading to the backend for personalization.
    func activeThumbnailData(limit: Int) async -> [Data] {
        entries
            .filter { $0.active }
            .prefix(limit)
            .compactMap { entry in
                guard let img = UIImage(contentsOfFile: entry.thumbPath) else { return nil }
                return img.jpegData(compressionQuality: 0.7)
            }
    }

    private func insert(_ entry: ReferenceImageEntry) {
        let sql = """
        INSERT OR REPLACE INTO references_v1
            (id, full_path, thumb_path, created_at, tag, embedding, active)
        VALUES (?, ?, ?, ?, ?, NULL, ?);
        """
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            sqlite3_bind_text(stmt, 1, (entry.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (entry.fullPath as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (entry.thumbPath as NSString).utf8String, -1, nil)
            sqlite3_bind_double(stmt, 4, entry.createdAt.timeIntervalSince1970)
            if let tag = entry.tag {
                sqlite3_bind_text(stmt, 5, (tag as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(stmt, 5)
            }
            sqlite3_bind_int(stmt, 6, entry.active ? 1 : 0)
            sqlite3_step(stmt)
        }
        sqlite3_finalize(stmt)
    }

    private func fetchAll() -> [ReferenceImageEntry] {
        let sql = """
        SELECT id, full_path, thumb_path, created_at, tag, embedding, active
        FROM references_v1
        ORDER BY created_at DESC;
        """
        var stmt: OpaquePointer?
        var out: [ReferenceImageEntry] = []
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            while sqlite3_step(stmt) == SQLITE_ROW {
                let id = String(cString: sqlite3_column_text(stmt, 0))
                let fullPath = String(cString: sqlite3_column_text(stmt, 1))
                let thumbPath = String(cString: sqlite3_column_text(stmt, 2))
                let createdAt = Date(timeIntervalSince1970: sqlite3_column_double(stmt, 3))
                let tagPtr = sqlite3_column_text(stmt, 4)
                let tag = tagPtr.map { String(cString: $0) }
                var embedding: [Float]?
                if let blob = sqlite3_column_blob(stmt, 5) {
                    let bytes = Int(sqlite3_column_bytes(stmt, 5))
                    let count = bytes / MemoryLayout<Float>.size
                    if count > 0 {
                        embedding = Array(unsafeUninitializedCapacity: count) { buf, len in
                            memcpy(buf.baseAddress, blob, bytes)
                            len = count
                        }
                    }
                }
                let active = sqlite3_column_int(stmt, 6) != 0
                out.append(ReferenceImageEntry(
                    id: id,
                    fullPath: fullPath,
                    thumbPath: thumbPath,
                    createdAt: createdAt,
                    tag: tag,
                    embedding: embedding,
                    active: active
                ))
            }
        }
        sqlite3_finalize(stmt)
        return out
    }
}

struct ReferenceImageEntry: Identifiable, Equatable, Sendable {
    let id: String
    let fullPath: String
    let thumbPath: String
    let createdAt: Date
    let tag: String?
    let embedding: [Float]?
    let active: Bool
}

private extension UIImage {
    func thumbnail(maxSide: CGFloat) -> UIImage {
        let scale = min(1.0, maxSide / max(size.width, size.height))
        let newSize = CGSize(width: size.width * scale, height: size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: newSize)
        return renderer.image { _ in
            draw(in: CGRect(origin: .zero, size: newSize))
        }
    }
}
