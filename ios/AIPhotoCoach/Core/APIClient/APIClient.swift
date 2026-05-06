import Foundation
import UIKit

enum APIError: Error, LocalizedError {
    case invalidResponse
    case http(Int, String)
    case decoding(Error)
    case underlying(Error)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "Invalid response from server"
        case .http(let code, let body): return "HTTP \(code): \(body)"
        case .decoding(let err): return "Decode failed: \(err)"
        case .underlying(let err): return err.localizedDescription
        }
    }
}

actor APIClient {
    static let shared = APIClient()

    private let session: URLSession

    init(session: URLSession? = nil) {
        if let s = session {
            self.session = s
        } else {
            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = APIConfig.connectTimeout
            config.timeoutIntervalForResource = APIConfig.requestTimeout
            self.session = URLSession(configuration: config)
        }
    }

    func health() async throws -> Bool {
        let url = APIConfig.baseURL.appendingPathComponent("healthz")
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            return false
        }
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (obj?["status"] as? String) == "ok"
    }

    /// Upload N keyframes + meta and return a structured shot plan.
    func analyze(
        meta: CaptureMeta,
        frames: [Data],
        referenceThumbnails: [Data] = []
    ) async throws -> AnalyzeResponse {
        let url = APIConfig.baseURL.appendingPathComponent("analyze")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")

        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .useDefaultKeys
        let metaJSON = try encoder.encode(meta)

        var body = Data()
        body.appendFormField(name: "meta", value: String(data: metaJSON, encoding: .utf8) ?? "{}", boundary: boundary)
        for (i, frame) in frames.enumerated() {
            body.appendFile(name: "frames", filename: "frame_\(i).jpg",
                            mimeType: "image/jpeg", data: frame, boundary: boundary)
        }
        for (i, ref) in referenceThumbnails.enumerated() {
            body.appendFile(name: "reference_thumbnails", filename: "ref_\(i).jpg",
                            mimeType: "image/jpeg", data: ref, boundary: boundary)
        }
        body.append("--\(boundary)--\r\n")

        request.httpBody = body

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.underlying(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.http(http.statusCode, body)
        }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        do {
            return try decoder.decode(AnalyzeResponse.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    /// Fetch the pose-library manifest.
    func fetchPoseManifest() async throws -> [PoseLibraryEntry] {
        let url = APIConfig.baseURL.appendingPathComponent("pose-library/manifest")
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw APIError.invalidResponse
        }
        let decoded = try JSONDecoder().decode(PoseLibraryManifest.self, from: data)
        return decoded.poses
    }

    /// Build the URL for a pose thumbnail. iOS uses AsyncImage to lazily load.
    func poseThumbnailURL(id: String) -> URL {
        APIConfig.baseURL.appendingPathComponent("pose-library/thumbnail/\(id).png")
    }
}

struct PoseLibraryManifest: Codable {
    let version: Int
    let count: Int
    let poses: [PoseLibraryEntry]
}

struct PoseLibraryEntry: Codable, Identifiable {
    let id: String
    let personCount: Int
    let layout: Layout
    let summary: String?
    let tags: [String]?
    let thumbnail: String?

    enum CodingKeys: String, CodingKey {
        case id
        case personCount = "person_count"
        case layout
        case summary
        case tags
        case thumbnail
    }
}

private extension Data {
    mutating func append(_ string: String) {
        if let d = string.data(using: .utf8) {
            append(d)
        }
    }

    mutating func appendFormField(name: String, value: String, boundary: String) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
        append("\(value)\r\n")
    }

    mutating func appendFile(name: String, filename: String, mimeType: String, data: Data, boundary: String) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mimeType)\r\n\r\n")
        append(data)
        append("\r\n")
    }
}
