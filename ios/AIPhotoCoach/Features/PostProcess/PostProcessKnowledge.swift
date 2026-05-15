// PostProcessKnowledge.swift
//
// Tiny per-preset / per-LUT teaching strings surfaced from the "AI 推荐"
// long-press sheet in PostProcessView. The goal isn't to be a manual —
// it's to translate one LLM decision into one sentence the user could
// repeat to themselves next time.

import Foundation

enum PostProcessKnowledge {
    static func explain(preset: FilterPreset) -> String {
        switch preset {
        case .original:
            return "不做任何调色，保留你拍到的原始色彩。"
        case .cleanBright:
            return "轻微提亮 + 微抬对比。适合本身光线就漂亮、想让颜色干净通透的场景。"
        case .filmWarm:
            return "暖色偏移 + 略低对比。让暖光更暖、肤色更舒服，常用于黄昏 / 室内暖光 / 木质场景。"
        case .streetCool:
            return "冷色偏移 + 阴影压一点。模仿胶片冷调，适合阴天街拍 / 海边 / 蓝调时刻。"
        case .bw:
            return "去色 + 局部 S 曲线提对比。当光影本身够戏剧时，去掉颜色反而更聚焦。"
        case .japanCrisp:
            return "提亮阴影 + 降饱和 + 加一点冷调。日系小清新的核心：低对比 + 高亮度 + 微冷白。"
        case .cinematic:
            return "压一点高光 + 抬一点阴影 + 加暖色饱和。电影感的本质是宽容度 + 色调统一。"
        case .retroFade:
            return "提亮纯黑 + 压住纯白 + 降饱和。胶卷褪色感，适合复古 / 慵懒午后。"
        case .hkVibe:
            return "强对比 + 高饱和 + 偏品红蓝。港风霓虹必备，但夜景别加太重，会糊。"
        case .beautyNatural, .beautyStrong:
            return "针对人像做柔肤 + 局部提亮 + 眼神光，保留毛孔质感。"
        }
    }

    static func explainLUT(id: String) -> String {
        switch id.lowercased() {
        case "natural":        return "中性查找表，仅做轻微对比与饱和度修正。"
        case "film_warm":      return "暖色胶片曲线，模拟 Portra / Gold 系胶卷的偏色。"
        case "film_cool":      return "冷色胶片曲线，模拟 Cinestill / Ektar 阴天的偏色。"
        case "mono":           return "黑白响应曲线，按 BT.709 亮度加 S 形对比。"
        case "hk_neon":        return "推高对比、加强品红/青色分离，做出霓虹海报感。"
        case "japanese_clean": return "抬阴影、降饱和、轻微冷调，日系小清新基础调。"
        case "golden_glow":    return "高光偏琥珀，中间调加暖，模拟金色时刻。"
        case "moody_fade":     return "黑色不到 0、白色不到 1 的褪色曲线 + 低饱和。"
        default:               return "自定义 LUT。"
        }
    }
}
