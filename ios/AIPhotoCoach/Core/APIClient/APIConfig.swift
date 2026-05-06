import Foundation

enum APIConfig {
    static var baseURL: URL {
        if let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }

    static let connectTimeout: TimeInterval = 15
    static let requestTimeout: TimeInterval = 60
}
