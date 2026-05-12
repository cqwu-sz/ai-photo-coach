// PersonaTone.swift
//
// Dev-only persona-tone audit. Mirrors `shared/copy/persona_tone.json` and
// `web/js/render.js`'s console.warn — if the AI's rationale slips back into
// teacher-y ("我建议你") or saccharine ("让我们") openers, we log it so we
// catch prompt regressions during dogfooding without bothering the user
// with UI.
//
// This is intentionally silent in production builds; we don't gate display
// or rewrite content, because a single banned opener should not block the
// user from seeing the plan.

import Foundation

enum PersonaTone {
    private static let bannedOpeners: [String] = [
        "我建议你", "你应该", "你需要",
        "让我们", "我们一起", "试想一下", "不妨",
    ]

    static func audit(rationale: String, context: String) {
        #if DEBUG
        let trimmed = rationale.trimmingCharacters(in: .whitespacesAndNewlines)
        for opener in bannedOpeners {
            if trimmed.hasPrefix(opener) || trimmed.contains("。\(opener)") {
                print("[persona-tone] \(context) uses banned opener \"\(opener)\": \(trimmed)")
                return
            }
        }
        #endif
    }
}
