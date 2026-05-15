// PostProcessKnowledge.swift
//
// Tiny per-preset / per-LUT teaching strings surfaced from the "AI 推荐"
// long-press sheet in PostProcessView. The goal isn't to be a manual —
// it's to translate one LLM decision into one sentence the user could
// repeat to themselves next time.
//
// All copy is now resolved through NSLocalizedString so en-locale users
// get readable English; missing translations fall back to the raw key
// which is human-readable Chinese, so the app never shows a Localizable
// key to the user even if the en bundle is incomplete.

import Foundation

enum PostProcessKnowledge {
    static func explain(preset: FilterPreset) -> String {
        let key: String
        switch preset {
        case .original:      key = "postproc.preset.original"
        case .cleanBright:   key = "postproc.preset.cleanBright"
        case .filmWarm:      key = "postproc.preset.filmWarm"
        case .streetCool:    key = "postproc.preset.streetCool"
        case .bw:            key = "postproc.preset.bw"
        case .japanCrisp:    key = "postproc.preset.japanCrisp"
        case .cinematic:     key = "postproc.preset.cinematic"
        case .retroFade:     key = "postproc.preset.retroFade"
        case .hkVibe:        key = "postproc.preset.hkVibe"
        case .beautyNatural, .beautyStrong:
            key = "postproc.preset.beauty"
        }
        return localized(key,
                          fallback: "针对人像做柔肤 + 局部提亮 + 眼神光，保留毛孔质感。")
    }

    static func explainLUT(id: String) -> String {
        let key = "postproc.lut.\(id.lowercased())"
        return localized(key, fallback: localized("postproc.lut.custom",
                                                    fallback: "自定义 LUT。"))
    }

    /// Wrapper so callers don't have to repeat the comment / bundle
    /// boilerplate. When a key is missing from the active .lproj,
    /// ``NSLocalizedString`` returns the key itself; we substitute the
    /// supplied fallback so the user never sees raw "postproc.preset.X".
    private static func localized(_ key: String, fallback: String) -> String {
        let value = NSLocalizedString(key, comment: "")
        return value == key ? fallback : value
    }
}
