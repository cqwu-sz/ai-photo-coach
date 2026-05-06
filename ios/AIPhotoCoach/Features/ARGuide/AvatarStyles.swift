import Foundation
import UIKit

/// Mirrors web/js/avatar_styles.js. Same 7 presets so the user picks an
/// avatar on Web and gets the *same* character in the native AR view —
/// they're saved in `userDefaults` keyed by id and translated to/from
/// the Web build via the shared API contract.
struct AvatarStyle: Identifiable, Hashable, Codable {
enum Gender: String, Codable { case male, female }
enum Hair: String, Codable {
        case short, buzz, bob, longStraight = "long_straight"
        case twinTails = "twin_tails", longCurly = "long_curly"
        case sideSwept = "side_swept", wolfTail = "wolf_tail"
    }
enum Top: String, Codable {
        case shortSleeve = "short_sleeve", jacket, dress, hoodie, sweater
    }
enum Bottom: String, Codable {
        case pants, shorts, skirt, longSkirt = "long_skirt", jeans, dress
    }
enum Accessory: String, Codable {
        case none, glasses, hairband, earrings
    }

let id: String
let gender: Gender
let name: String
let summary: String
let height: CGFloat
let skinHue: CGFloat
let skinLightness: CGFloat
let hairColorHex: String
let hair: Hair
let top: Top
let topColorHex: String
let bottom: Bottom
let bottomColorHex: String
let accessory: Accessory
let accessoryColorHex: String?
let shoeColorHex: String

var hairColor: UIColor { UIColor(hex: hairColorHex) }
var topColor: UIColor { UIColor(hex: topColorHex) }
var bottomColor: UIColor { UIColor(hex: bottomColorHex) }
var accessoryColor: UIColor? { accessoryColorHex.map(UIColor.init(hex:)) }
var shoeColor: UIColor { UIColor(hex: shoeColorHex) }
var skinColor: UIColor {
        UIColor(hue: skinHue / 360.0, saturation: 0.45, brightness: skinLightness, alpha: 1.0)
    }
}

enum AvatarPresets {
static let all: [AvatarStyle] = [
        .init(id: "akira", gender: .male, name: "彻 Akira", summary: "黑短发 · 蓝衬衫",
              height: 1.78, skinHue: 24, skinLightness: 0.74,
              hairColorHex: "#1a1a22", hair: .short,
              top: .shortSleeve, topColorHex: "#3a6dbe",
              bottom: .jeans, bottomColorHex: "#2a3a5a",
              accessory: .none, accessoryColorHex: nil, shoeColorHex: "#1c1c1c"),
        .init(id: "jun", gender: .male, name: "纯 Jun", summary: "棕寸头 · 黑夹克 · 眼镜",
              height: 1.81, skinHue: 26, skinLightness: 0.72,
              hairColorHex: "#5a4030", hair: .buzz,
              top: .jacket, topColorHex: "#1a1a1f",
              bottom: .pants, bottomColorHex: "#3a3340",
              accessory: .glasses, accessoryColorHex: "#222222", shoeColorHex: "#0d0d0d"),
        .init(id: "yuki", gender: .female, name: "雪 Yuki", summary: "黑长直发 · 白连衣裙",
              height: 1.62, skinHue: 22, skinLightness: 0.82,
              hairColorHex: "#101018", hair: .longStraight,
              top: .dress, topColorHex: "#f8f5ee",
              bottom: .longSkirt, bottomColorHex: "#f8f5ee",
              accessory: .none, accessoryColorHex: nil, shoeColorHex: "#a48b6a"),
        .init(id: "sakura", gender: .female, name: "樱 Sakura", summary: "粉色双马尾 · 粉色短裙",
              height: 1.58, skinHue: 22, skinLightness: 0.84,
              hairColorHex: "#f59ac4", hair: .twinTails,
              top: .shortSleeve, topColorHex: "#ffffff",
              bottom: .skirt, bottomColorHex: "#f590b5",
              accessory: .hairband, accessoryColorHex: "#ffffff", shoeColorHex: "#f55090"),
        .init(id: "rena", gender: .female, name: "玲奈 Rena", summary: "棕色波波头 · 黄毛衣",
              height: 1.63, skinHue: 24, skinLightness: 0.78,
              hairColorHex: "#7a4f2a", hair: .bob,
              top: .sweater, topColorHex: "#f5c64a",
              bottom: .jeans, bottomColorHex: "#5a6a8a",
              accessory: .none, accessoryColorHex: nil, shoeColorHex: "#7a3a2a"),
        .init(id: "luna", gender: .female, name: "露娜 Luna", summary: "银色长卷 · 黑外套",
              height: 1.66, skinHue: 22, skinLightness: 0.86,
              hairColorHex: "#c8c8d0", hair: .longCurly,
              top: .jacket, topColorHex: "#1a1a26",
              bottom: .pants, bottomColorHex: "#2a2a36",
              accessory: .none, accessoryColorHex: nil, shoeColorHex: "#1a1a26"),
        .init(id: "haruko", gender: .female, name: "春子 Haruko", summary: "红狼尾 · 牛仔风",
              height: 1.64, skinHue: 22, skinLightness: 0.78,
              hairColorHex: "#c83838", hair: .wolfTail,
              top: .shortSleeve, topColorHex: "#ffffff",
              bottom: .shorts, bottomColorHex: "#3a527a",
              accessory: .none, accessoryColorHex: nil, shoeColorHex: "#3a3a3a"),
    ]

static let defaultPicks = ["akira", "yuki", "sakura", "luna"]

static func style(for id: String) -> AvatarStyle {
        all.first { $0.id == id } ?? all[0]
    }

static func resolve(_ stored: [String], count n: Int) -> [String] {
        (0..<n).map { i in
            let s = i < stored.count ? stored[i] : nil
            if let s, all.contains(where: { $0.id == s }) { return s }
            return defaultPicks[i % defaultPicks.count]
        }
    }
}

extension UIColor {
    convenience init(hex: String) {
        var clean = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.hasPrefix("#") { clean.removeFirst() }
        guard clean.count == 6, let v = UInt32(clean, radix: 16) else {
            self.init(white: 0.5, alpha: 1)
            return
        }
        let r = CGFloat((v >> 16) & 0xFF) / 255.0
        let g = CGFloat((v >> 8) & 0xFF) / 255.0
        let b = CGFloat(v & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b, alpha: 1)
    }
}
