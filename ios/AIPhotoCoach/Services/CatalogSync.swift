// CatalogSync.swift  (v18 c3)
//
// Pulls /health/catalog once per launch and caches the
// scene_mode → 中文 / style_id → 中文 maps in UserDefaults so
// other views can avoid hard-coding them. Failure is silent;
// callers fall back to the local seed map.
//
// We do NOT block UI on this; everything that consumes the catalog
// has a baked-in default identical to backend at v18 release time.

import Foundation

@MainActor
final class CatalogSync {
    static let shared = CatalogSync()

    private struct Catalog: Decodable {
        let version: Int
        let styles: [Item]
        let scene_modes: [Item]
        struct Item: Decodable { let id: String; let label_zh: String }
    }

    private static let kStyleKey = "catalog.styles_v1"
    private static let kSceneKey = "catalog.scenes_v1"

    func bootstrap() {
        Task.detached(priority: .background) {
            await Self.fetchAndCache()
        }
    }

    static func sceneLabelZh(_ id: String) -> String {
        let cached = UserDefaults.standard.dictionary(forKey: kSceneKey)
            as? [String: String]
        return cached?[id] ?? _localFallbackScenes[id] ?? id
    }

    static func styleLabelZh(_ id: String) -> String {
        let cached = UserDefaults.standard.dictionary(forKey: kStyleKey)
            as? [String: String]
        return cached?[id] ?? _localFallbackStyles[id] ?? id
    }

    private static func fetchAndCache() async {
        do {
            let url = APIConfig.baseURL.appendingPathComponent("health/catalog")
            var req = URLRequest(url: url)
            req.timeoutInterval = APIConfig.connectTimeout
            let (data, _) = try await URLSession.shared.data(for: req)
            let cat = try JSONDecoder().decode(Catalog.self, from: data)
            var sceneMap: [String: String] = [:]
            for it in cat.scene_modes { sceneMap[it.id] = it.label_zh }
            var styleMap: [String: String] = [:]
            for it in cat.styles { styleMap[it.id] = it.label_zh }
            UserDefaults.standard.set(sceneMap, forKey: kSceneKey)
            UserDefaults.standard.set(styleMap, forKey: kStyleKey)
        } catch {
            #if DEBUG
            print("CatalogSync failed (using local fallbacks): \(error)")
            #endif
        }
    }

    // Same map as backend services/style_catalog.SCENE_LABEL_ZH at
    // v18 release. Used until /health/catalog returns once.
    private static let _localFallbackScenes: [String: String] = [
        "portrait":     "人像",
        "closeup":      "特写",
        "full_body":    "全身",
        "documentary":  "纪实",
        "scenery":      "风景",
        "light_shadow": "光影",
    ]

    private static let _localFallbackStyles: [String: String] = [
        "cinematic_moody":   "氛围感",
        "clean_bright":      "清爽日系",
        "film_warm":         "温柔暖光",
        "street_candid":     "自然随手",
        "editorial_fashion": "大片感",
    ]
}
