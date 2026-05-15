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

    /// Map the backend ``PostProcessRecipe.filterPreset`` string (a
    /// small fixed vocabulary the LLM is constrained to emit) into one
    /// of our concrete presets. Unknown values degrade to ``.original``
    /// so an unfamiliar backend version never crashes the UI.
    static func from(recipeKey: String) -> FilterPreset {
        switch recipeKey.lowercased() {
        case "natural":         return .cleanBright
        case "film_warm":       return .filmWarm
        case "film_cool":       return .streetCool
        case "mono":            return .bw
        case "hk_neon":         return .hkVibe
        case "japanese_clean":  return .japanCrisp
        case "golden_glow":     return .cinematic
        case "moody_fade":      return .retroFade
        default:                return .original
        }
    }

    /// Nearest free preset for paywall downgrade when the recipe asked
    /// for a Pro-only preset and the user isn't subscribed. Picks a
    /// flavour-similar free preset so the look is "close enough" rather
    /// than dropping to ``.original`` (which would feel like a bug).
    /// Returns ``self`` when already free.
    var freeFallback: FilterPreset {
        guard requiresPro else { return self }
        switch self {
        case .cinematic:   return .cleanBright   // golden_glow → clean
        case .filmWarm:    return .cleanBright   // film_warm   → clean
        case .hkVibe:      return .streetCool    // hk_neon     → cool
        case .retroFade:   return .bw            // moody_fade  → mono
        case .beautyNatural, .beautyStrong:
            return .cleanBright
        default:
            return .cleanBright
        }
    }
}

/// P3-strong-2 — outcome of the most recent ``apply(_:lutId:to:)`` call.
/// Surfaced to ``PostProcessModel`` so the UI can show a small warning
/// when a LUT was requested but couldn't be loaded (file missing from
/// bundle, parse failure, etc.) instead of silently returning the
/// preset-only render that looks identical to "no LUT requested".
enum LUTResolutionStatus: Equatable {
    case notRequested
    case applied(String)
    case missing(String)        // bundle has no such .cube file
    case parseFailure(String)   // .cube file exists but is malformed
}

final class FilterEngine {
    private let context = CIContext(options: [.useSoftwareRenderer: false])
    /// Caches decoded 64^3 LUT tables so back-to-back applies don't
    /// reparse the same .cube file. Key = lut id (file stem).
    private var lutCache: [String: Data] = [:]
    /// Outcome of the LUT lookup during the most recent ``apply`` call.
    /// Read by ``PostProcessModel`` to drive a "LUT 未加载" UI hint.
    private(set) var lastLUTStatus: LUTResolutionStatus = .notRequested

    func apply(_ preset: FilterPreset, to image: UIImage) -> UIImage {
        return self.apply(preset, lutId: nil, to: image)
    }

    /// Apply a preset, then optionally a LUT lookup, then return the
    /// processed UIImage. ``lutId`` matches a ``Resources/LUTs/<id>.cube``
    /// shipped in the app bundle. Missing LUT id → falls back to the
    /// preset-only chain so the user still gets *something*; ``lastLUTStatus``
    /// records why so the UI can surface it.
    func apply(_ preset: FilterPreset, lutId: String?, to image: UIImage) -> UIImage {
        guard let cg = image.cgImage else {
            self.lastLUTStatus = .notRequested
            return image
        }
        var ci = CIImage(cgImage: cg)
        if preset != .original {
            ci = chain(for: preset, image: ci)
        }
        if let lutId {
            let (filter, status) = self.makeLUTFilter(lutId: lutId)
            self.lastLUTStatus = status
            if let lutFilter = filter {
                lutFilter.setValue(ci, forKey: kCIInputImageKey)
                if let out = lutFilter.outputImage {
                    ci = out
                }
            }
        } else {
            self.lastLUTStatus = .notRequested
        }
        guard let out = context.createCGImage(ci, from: ci.extent) else { return image }
        return UIImage(cgImage: out, scale: image.scale, orientation: image.imageOrientation)
    }

    // MARK: - LUT loading

    /// Parse a Resolve-style ``.cube`` file from the bundle and build
    /// a ``CIColorCubeWithColorSpace`` filter ready to apply. Returns
    /// nil + a structured ``LUTResolutionStatus`` reason when the file
    /// is missing or unparseable; caller surfaces the status to the UI
    /// and falls back to no-LUT.
    private func makeLUTFilter(lutId: String) -> (CIFilter?, LUTResolutionStatus) {
        let (dimension, data): (Int, Data)
        if let cached = self.lutCache[lutId] {
            // Caches the *packed* data; dimension is captured in the
            // packed length: dim³ × 4 floats × 4 bytes.
            let total = cached.count / (4 * MemoryLayout<Float>.size)
            let dim = Int(round(pow(Double(total), 1.0/3.0)))
            (dimension, data) = (dim, cached)
        } else {
            guard let url = Bundle.main.url(forResource: lutId, withExtension: "cube",
                                            subdirectory: "LUTs")
                  ?? Bundle.main.url(forResource: lutId, withExtension: "cube") else {
                return (nil, .missing(lutId))
            }
            guard let text = try? String(contentsOf: url, encoding: .utf8),
                  let parsed = self.parseCubeLUT(text) else {
                return (nil, .parseFailure(lutId))
            }
            (dimension, data) = parsed
            self.lutCache[lutId] = data
        }

        let filter = CIFilter(name: "CIColorCubeWithColorSpace")
        filter?.setValue(dimension, forKey: "inputCubeDimension")
        filter?.setValue(data, forKey: "inputCubeData")
        filter?.setValue(CGColorSpace(name: CGColorSpace.sRGB), forKey: "inputColorSpace")
        return (filter, .applied(lutId))
    }

    /// Minimal .cube parser — accepts the subset Resolve / Photoshop
    /// emit: ``LUT_3D_SIZE N`` + ``N^3`` lines of ``r g b`` floats.
    private func parseCubeLUT(_ text: String) -> (dimension: Int, data: Data)? {
        var dimension = 0
        var values: [Float] = []
        for raw in text.split(whereSeparator: { $0.isNewline }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") || line.hasPrefix("//") { continue }
            if line.uppercased().hasPrefix("LUT_3D_SIZE") {
                let parts = line.split(whereSeparator: { $0.isWhitespace })
                if parts.count >= 2, let n = Int(parts[1]) { dimension = n }
                continue
            }
            // Skip "TITLE ..." / "DOMAIN_MIN" / "DOMAIN_MAX" / "LUT_1D_*" lines.
            if line.uppercased().hasPrefix("TITLE")
                || line.uppercased().hasPrefix("DOMAIN")
                || line.uppercased().hasPrefix("LUT_1D") {
                continue
            }
            let parts = line.split(whereSeparator: { $0.isWhitespace })
            if parts.count == 3,
               let r = Float(parts[0]), let g = Float(parts[1]), let b = Float(parts[2]) {
                values.append(r)
                values.append(g)
                values.append(b)
                values.append(1.0)               // alpha = 1
            }
        }
        guard dimension > 0, values.count == dimension * dimension * dimension * 4 else {
            return nil
        }
        let data = values.withUnsafeBufferPointer { buf in
            Data(bytes: buf.baseAddress!, count: buf.count * MemoryLayout<Float>.size)
        }
        return (dimension, data)
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
