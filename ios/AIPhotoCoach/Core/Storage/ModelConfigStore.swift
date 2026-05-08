import Foundation
import Security
import SwiftUI

/// BYOK model config storage.
///
/// - The model id and base url are stable preferences; they go in
///   UserDefaults / @AppStorage so they survive app restarts and SwiftUI
///   can subscribe to them.
/// - The API key is sensitive; it goes in the Keychain (kSecAttrAccessible
///   = AfterFirstUnlock) so other apps can't read it.
///
/// `currentForRequest()` is what the analyze flow calls right before
/// hitting POST /analyze.
struct ModelConfig: Equatable, Hashable {
    var modelId: String
    var apiKey: String
    var baseUrl: String

    static let empty = ModelConfig(modelId: "", apiKey: "", baseUrl: "")

    var hasOverride: Bool {
        !modelId.isEmpty || !apiKey.isEmpty || !baseUrl.isEmpty
    }
}

enum ModelConfigStore {
    private static let modelIdKey = "aphc.modelConfig.modelId"
    private static let baseUrlKey = "aphc.modelConfig.baseUrl"
    private static let keychainAccount = "aphc.modelConfig.apiKey"
    private static let keychainService = "ai.photo.coach.modelKey"

    static func currentForRequest() -> ModelConfig {
        ModelConfig(
            modelId: modelId(),
            apiKey: apiKey(),
            baseUrl: baseUrl()
        )
    }

    // MARK: - Per-field accessors

    static func modelId() -> String {
        UserDefaults.standard.string(forKey: modelIdKey) ?? ""
    }

    static func setModelId(_ value: String) {
        UserDefaults.standard.set(value, forKey: modelIdKey)
    }

    static func baseUrl() -> String {
        UserDefaults.standard.string(forKey: baseUrlKey) ?? ""
    }

    static func setBaseUrl(_ value: String) {
        UserDefaults.standard.set(value, forKey: baseUrlKey)
    }

    static func apiKey() -> String {
        readKeychain() ?? ""
    }

    static func setApiKey(_ value: String) {
        if value.isEmpty {
            deleteKeychain()
        } else {
            writeKeychain(value)
        }
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: modelIdKey)
        UserDefaults.standard.removeObject(forKey: baseUrlKey)
        deleteKeychain()
    }

    // MARK: - Keychain helpers

    private static func readKeychain() -> String? {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecReturnData as String: true,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data,
              let str = String(data: data, encoding: .utf8) else {
            return nil
        }
        return str
    }

    private static func writeKeychain(_ value: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
        ]
        let attrs: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attrs as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var add = query
            add.merge(attrs) { _, new in new }
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    private static func deleteKeychain() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
