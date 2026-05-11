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

    /// Inject the current bearer token + device id on every outbound
    /// request. Pulled out so analyze/feedback/recon3d all stay
    /// consistent. Best-effort: when AuthManager has no session yet we
    /// fall back to X-Device-Id only — the backend's
    /// `enable_legacy_device_id_auth` path will still pick us up.
    private func attachAuth(to request: inout URLRequest) async {
        let auth = await MainActor.run { AuthManager.shared }
        request.setValue(auth.deviceId, forHTTPHeaderField: "X-Device-Id")
        do {
            let token = try await auth.accessToken()
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        } catch {
            // No token; rely on X-Device-Id fallback. Don't block the
            // request — most upstream callers can tolerate a 401 and
            // surface their own retry.
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
    /// Optional BYOK overrides are forwarded to the backend on this single
    /// request and are never persisted server-side.
    func analyze(
        meta: CaptureMeta,
        frames: [Data],
        referenceThumbnails: [Data] = [],
        modelId: String? = nil,
        modelApiKey: String? = nil,
        modelBaseUrl: String? = nil,
        videoMP4: Data? = nil
    ) async throws -> AnalyzeResponse {
        let url = APIConfig.baseURL.appendingPathComponent("analyze")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        await attachAuth(to: &request)

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")

        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .useDefaultKeys
        let metaJSON = try encoder.encode(meta)

        var body = Data()
        body.appendFormField(name: "meta", value: String(data: metaJSON, encoding: .utf8) ?? "{}", boundary: boundary)
        // A1-exif-strip: scrub EXIF/GPS before any image leaves the
        // device. Cheap (<5ms per JPEG on A14+) and idempotent.
        let safeFrames = ImageSanitizer.stripped(all: frames)
        let safeRefs = ImageSanitizer.stripped(all: referenceThumbnails)
        for (i, frame) in safeFrames.enumerated() {
            body.appendFile(name: "frames", filename: "frame_\(i).jpg",
                            mimeType: "image/jpeg", data: frame, boundary: boundary)
        }
        for (i, ref) in safeRefs.enumerated() {
            body.appendFile(name: "reference_thumbnails", filename: "ref_\(i).jpg",
                            mimeType: "image/jpeg", data: ref, boundary: boundary)
        }
        if let id = modelId, !id.isEmpty {
            body.appendFormField(name: "model_id", value: id, boundary: boundary)
        }
        if let key = modelApiKey, !key.isEmpty {
            body.appendFormField(name: "model_api_key", value: key, boundary: boundary)
        }
        if let baseUrl = modelBaseUrl, !baseUrl.isEmpty {
            body.appendFormField(name: "model_base_url", value: baseUrl, boundary: boundary)
        }
        if let video = videoMP4, !video.isEmpty {
            body.appendFile(name: "video", filename: "scan.mov",
                            mimeType: "video/quicktime", data: video, boundary: boundary)
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

    /// Fetch the registry of available vision-model presets.
    func fetchModels() async throws -> ModelsResponse {
        let url = APIConfig.baseURL.appendingPathComponent("models")
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw APIError.invalidResponse
        }
        return try JSONDecoder().decode(ModelsResponse.self, from: data)
    }

    /// Sanity-check a (model_id, api_key, base_url?) tuple.
    func testModel(modelId: String, apiKey: String?, baseUrl: String?) async throws -> ModelsTestResponse {
        let url = APIConfig.baseURL.appendingPathComponent("models/test")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let payload: [String: Any?] = [
            "model_id": modelId,
            "api_key": apiKey,
            "base_url": baseUrl,
        ]
        let cleaned = payload.compactMapValues { $0 }
        request.httpBody = try JSONSerialization.data(withJSONObject: cleaned)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw APIError.invalidResponse
        }
        return try JSONDecoder().decode(ModelsTestResponse.self, from: data)
    }
}

struct ModelsResponse: Codable, Sendable {
    let defaultModelId: String
    let enableByok: Bool
    let models: [ModelPreset]

    enum CodingKeys: String, CodingKey {
        case defaultModelId = "default_model_id"
        case enableByok = "enable_byok"
        case models
    }
}

struct ModelPreset: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let displayName: String
    let vendor: String
    let kind: String
    let baseUrl: String?
    let supportsNativeVideo: Bool
    let jsonSchemaMode: String
    let apiKeyEnv: String?
    let notes: String
    let requiresKey: Bool
    let hasOperatorKey: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case vendor
        case kind
        case baseUrl = "base_url"
        case supportsNativeVideo = "supports_native_video"
        case jsonSchemaMode = "json_schema_mode"
        case apiKeyEnv = "api_key_env"
        case notes
        case requiresKey = "requires_key"
        case hasOperatorKey = "has_operator_key"
    }
}

struct ModelsTestResponse: Codable, Sendable {
    let ok: Bool
    let snippet: String?
    let error: String?
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
