// BeautyEngine.swift (W10.2)
//
// Five-knob "美颜" pipeline. Uses Apple Vision face landmarks to mask
// just the face / eyes for some operations (so the entire frame doesn't
// turn pasty), then applies CIFilter ops keyed by the user-selected
// strength sliders. All on-device, no model files.

import CoreImage
import CoreImage.CIFilterBuiltins
import UIKit
import Vision

struct BeautyParams: Equatable {
    /// 0...1 sliders.
    var smooth: Double = 0      // 磨皮 (bilateral)
    var brighten: Double = 0    // 美白
    var slim: Double = 0        // 瘦脸 (mesh warp; reserved)
    var enlargeEye: Double = 0  // 大眼 (CIBumpDistortion at eye centres)
    var brightenEye: Double = 0 // 亮眼 (local exposure at eyes)

    /// Map a backend ``PostProcessRecipe.beautyIntensity`` (0...1) to
    /// a sensible per-slider distribution. We keep eye-related sliders
    /// (slim / enlargeEye) at 0 unless mesh-warp is shipped, since
    /// those are gated behind ``ai_photo.beauty.meshWarp`` in
    /// PostProcessView. Skin smoothing and brighten map at full
    /// intensity; brightenEye at 60% to avoid the "shiny eyeball" look.
    static func fromIntensity(_ intensity: Double) -> BeautyParams {
        let clamped = max(0.0, min(1.0, intensity))
        var p = BeautyParams()
        p.smooth = clamped
        p.brighten = clamped * 0.75
        p.brightenEye = clamped * 0.6
        return p
    }

    var isIdentity: Bool {
        smooth == 0 && brighten == 0 && slim == 0 &&
        enlargeEye == 0 && brightenEye == 0
    }
}

final class BeautyEngine {
    private let context = CIContext(options: [.useSoftwareRenderer: false])

    func apply(_ params: BeautyParams, to image: UIImage) -> UIImage {
        if params.isIdentity { return image }
        guard let cg = image.cgImage else { return image }
        let base = CIImage(cgImage: cg)

        // E-face-mask — detect face once and build a soft luminance mask
        // around it. When a face exists, smooth + brighten only run
        // inside the mask so the whole frame doesn't go pasty (e.g.
        // wooden table textures or a sweater pattern keep their grain).
        // When no face is found we fall back to global ops so the
        // sliders still do something for "selfie of a hand holding
        // something" type shots.
        let faceMask = detectFaceMask(in: base)
        var ci = base

        if params.smooth > 0 {
            let blurred = base.applyingFilter("CIGaussianBlur",
                                              parameters: [kCIInputRadiusKey: 0.5 + 2.5 * params.smooth])
                .cropped(to: base.extent)
            ci = composite(over: ci, with: blurred, mask: faceMask)
        }
        if params.brighten > 0 {
            let brightened = ci.applyingFilter("CIColorControls",
                                                parameters: [
                                                    kCIInputBrightnessKey: 0.05 * params.brighten,
                                                    kCIInputContrastKey: 1.0 + 0.05 * params.brighten,
                                                    kCIInputSaturationKey: 1.0 - 0.05 * params.brighten,
                                                ])
            ci = composite(over: ci, with: brightened, mask: faceMask)
        }
        if params.enlargeEye > 0 || params.brightenEye > 0 {
            ci = applyEyeOps(ci, params: params, originalSize: image.size)
        }
        guard let out = context.createCGImage(ci, from: ci.extent) else { return image }
        return UIImage(cgImage: out, scale: image.scale, orientation: image.imageOrientation)
    }

    /// Run Vision face landmarks and return a soft alpha mask CIImage
    /// covering the largest face (head + neck) with a feathered edge.
    /// Returns nil when no face is detected — caller treats that as
    /// "apply globally".
    private func detectFaceMask(in image: CIImage) -> CIImage? {
        let request = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(ciImage: image, options: [:])
        do { try handler.perform([request]) } catch { return nil }
        guard let face = (request.results as? [VNFaceObservation])?
            .max(by: { $0.boundingBox.size.width * $0.boundingBox.size.height
                       < $1.boundingBox.size.width * $1.boundingBox.size.height })
        else { return nil }

        let extent = image.extent
        let bbox = face.boundingBox
        // Vision uses bottom-left origin in normalised space; CIImage
        // matches that so no Y-flip is needed.
        let rect = CGRect(
            x: bbox.origin.x * extent.width,
            y: bbox.origin.y * extent.height,
            width: bbox.size.width * extent.width,
            height: bbox.size.height * extent.height,
        ).insetBy(dx: -bbox.size.width * extent.width * 0.10,
                  dy: -bbox.size.height * extent.height * 0.20)

        // Build a soft circular falloff mask via a radial gradient
        // centred on the face. Radius slightly bigger than half the
        // bbox so the mask fades smoothly past the cheeks.
        let center = CIVector(x: rect.midX, y: rect.midY)
        let r = Float(max(rect.width, rect.height) * 0.55)
        let gradient = CIFilter(name: "CIRadialGradient", parameters: [
            "inputCenter":  center,
            "inputRadius0": r * 0.65,           // fully opaque inside this
            "inputRadius1": r,                  // fully transparent outside
            "inputColor0":  CIColor(red: 1, green: 1, blue: 1, alpha: 1),
            "inputColor1":  CIColor(red: 1, green: 1, blue: 1, alpha: 0),
        ])
        return gradient?.outputImage?.cropped(to: extent)
    }

    /// Composite ``effect`` over ``base`` using ``mask`` (alpha
    /// luminance) so the effect only lands where the mask is opaque.
    /// When ``mask`` is nil we fall back to full-frame replacement.
    private func composite(over base: CIImage, with effect: CIImage,
                            mask: CIImage?) -> CIImage {
        guard let mask else { return effect }
        return effect
            .applyingFilter("CIBlendWithMask", parameters: [
                kCIInputBackgroundImageKey: base,
                kCIInputMaskImageKey:       mask,
            ])
            .cropped(to: base.extent)
    }

    private func applyEyeOps(_ image: CIImage, params: BeautyParams,
                              originalSize: CGSize) -> CIImage {
        let request = VNDetectFaceLandmarksRequest()
        let handler = VNImageRequestHandler(ciImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return image
        }
        guard let face = (request.results as? [VNFaceObservation])?.first,
              let landmarks = face.landmarks else { return image }
        var current = image
        let bounds = face.boundingBox
        let imgRect = image.extent
        func absPoint(_ pt: CGPoint) -> CGPoint {
            let fx = bounds.origin.x + pt.x * bounds.size.width
            let fy = bounds.origin.y + pt.y * bounds.size.height
            return CGPoint(x: fx * imgRect.width, y: fy * imgRect.height)
        }
        if params.enlargeEye > 0 {
            for region in [landmarks.leftEye, landmarks.rightEye].compactMap({ $0 }) {
                guard region.pointCount > 0 else { continue }
                let pts = (0..<region.pointCount).map { region.normalizedPoints[$0] }
                let cx = pts.map { $0.x }.reduce(0, +) / CGFloat(pts.count)
                let cy = pts.map { $0.y }.reduce(0, +) / CGFloat(pts.count)
                let center = absPoint(CGPoint(x: cx, y: cy))
                current = current.applyingFilter("CIBumpDistortion", parameters: [
                    kCIInputCenterKey: CIVector(x: center.x, y: center.y),
                    kCIInputRadiusKey: 30.0 + 30.0 * params.enlargeEye,
                    kCIInputScaleKey: 0.15 * params.enlargeEye,
                ])
            }
        }
        if params.brightenEye > 0 {
            current = current.applyingFilter("CIExposureAdjust",
                                             parameters: [kCIInputEVKey: 0.2 * params.brightenEye])
        }
        return current
    }
}
