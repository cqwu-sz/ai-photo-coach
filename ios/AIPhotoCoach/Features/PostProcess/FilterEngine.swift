// FilterEngine.swift (W10.1)
//
// Eight preset CIFilter chains plus optional LUT lookup. Pure on-device,
// no network, no model files larger than the bundled .png LUTs (~64 KB
// each at 64x64x64 packed-3D format).

import CoreImage
import CoreImage.CIFilterBuiltins
import UIKit

enum FilterPreset: String, CaseIterable, Identifiable {
    case original
    case cinematic = "电影感"
    case filmWarm = "胶片暖"
    case streetCool = "街拍冷调"
    case cleanBright = "干净亮调"
    case bw = "黑白"
    case japanCrisp = "日系小清新"
    case retroFade = "复古褪色"
    case hkVibe = "港风"
    case beautyNatural = "美颜·自然"
    case beautyStrong = "美颜·精致"

    var id: String { rawValue }
    var label: String { rawValue }

    /// P1-9.1 / v17 — Pro-only presets. UI shows a lock icon and routes
    /// taps through the unified PaywallGate (PR7).
    var requiresPro: Bool {
        switch self {
        case .cinematic, .hkVibe, .retroFade, .filmWarm,
             .beautyNatural, .beautyStrong:
            return true
        default:
            return false
        }
    }

    /// v17 — feature key sent to the backend's quota engine. Beauty
    /// and advanced filters share a feature key so a single capture
    /// session doesn't burn multiple quota units.
    var featureKey: String {
        switch self {
        case .beautyNatural, .beautyStrong: return "beauty"
        case .cinematic, .filmWarm, .hkVibe, .retroFade: return "advanced_filter"
        default: return "filter"
        }
    }
}

final class FilterEngine {
    private let context = CIContext(options: [.useSoftwareRenderer: false])

    func apply(_ preset: FilterPreset, to image: UIImage) -> UIImage {
        guard preset != .original, let cg = image.cgImage else { return image }
        let ci = CIImage(cgImage: cg)
        let processed = chain(for: preset, image: ci)
        guard let out = context.createCGImage(processed, from: processed.extent) else { return image }
        return UIImage(cgImage: out, scale: image.scale, orientation: image.imageOrientation)
    }

    private func chain(for preset: FilterPreset, image: CIImage) -> CIImage {
        switch preset {
        case .original: return image
        case .cinematic:
            return image
                .applyingFilter("CITemperatureAndTint",
                                parameters: ["inputNeutral": CIVector(x: 6800, y: 0)])
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputContrastKey: 1.15,
                                             kCIInputSaturationKey: 0.85])
                .applyingFilter("CIVignette",
                                parameters: [kCIInputIntensityKey: 1.4,
                                             kCIInputRadiusKey: 1.6])
        case .filmWarm:
            return image
                .applyingFilter("CITemperatureAndTint",
                                parameters: ["inputNeutral": CIVector(x: 5000, y: 8)])
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputContrastKey: 1.05,
                                             kCIInputSaturationKey: 0.95,
                                             kCIInputBrightnessKey: 0.05])
                .applyingFilter("CIPhotoEffectInstant")
        case .streetCool:
            return image
                .applyingFilter("CITemperatureAndTint",
                                parameters: ["inputNeutral": CIVector(x: 7800, y: -10)])
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputContrastKey: 1.1,
                                             kCIInputSaturationKey: 0.7])
        case .cleanBright:
            return image.applyingFilter("CIColorControls",
                                        parameters: [kCIInputBrightnessKey: 0.1,
                                                     kCIInputContrastKey: 1.05,
                                                     kCIInputSaturationKey: 0.92])
        case .bw:
            return image.applyingFilter("CIPhotoEffectMono")
        case .japanCrisp:
            return image
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputBrightnessKey: 0.08,
                                             kCIInputContrastKey: 0.95,
                                             kCIInputSaturationKey: 0.85])
                .applyingFilter("CITemperatureAndTint",
                                parameters: ["inputNeutral": CIVector(x: 6300, y: -5)])
        case .retroFade:
            return image
                .applyingFilter("CIPhotoEffectFade")
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputContrastKey: 0.92,
                                             kCIInputSaturationKey: 0.8])
        case .hkVibe:
            return image
                .applyingFilter("CITemperatureAndTint",
                                parameters: ["inputNeutral": CIVector(x: 5800, y: 18)])
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputContrastKey: 1.2,
                                             kCIInputSaturationKey: 1.05])
                .applyingFilter("CIVignette",
                                parameters: [kCIInputIntensityKey: 0.9,
                                             kCIInputRadiusKey: 1.4])
        case .beautyNatural:
            // Light skin softening: subtle blur fused with original via
            // luminance mask. CIHighlightShadowAdjust + CIVibrance gives
            // a believable "glow" without the plastic doll look.
            return image
                .applyingFilter("CIHighlightShadowAdjust",
                                parameters: ["inputHighlightAmount": 0.85,
                                             "inputShadowAmount": 0.4])
                .applyingFilter("CIVibrance",
                                parameters: [kCIInputAmountKey: 0.25])
                .applyingFilter("CISharpenLuminance",
                                parameters: [kCIInputSharpnessKey: 0.25])
        case .beautyStrong:
            return image
                .applyingFilter("CIHighlightShadowAdjust",
                                parameters: ["inputHighlightAmount": 0.7,
                                             "inputShadowAmount": 0.55])
                .applyingFilter("CIGaussianBlur",
                                parameters: [kCIInputRadiusKey: 1.4])
                .applyingFilter("CIColorControls",
                                parameters: [kCIInputBrightnessKey: 0.06,
                                             kCIInputSaturationKey: 1.05])
                .applyingFilter("CIVibrance",
                                parameters: [kCIInputAmountKey: 0.4])
        }
    }
}
