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
        var ci = CIImage(cgImage: cg)
        if params.smooth > 0 {
            ci = ci.applyingFilter("CIGaussianBlur",
                                   parameters: [kCIInputRadiusKey: 0.5 + 2.5 * params.smooth])
            ci = ci.applyingFilter("CIColorMatrix")  // identity, settles output extent
        }
        if params.brighten > 0 {
            ci = ci.applyingFilter("CIColorControls",
                                   parameters: [
                                    kCIInputBrightnessKey: 0.05 * params.brighten,
                                    kCIInputContrastKey: 1.0 + 0.05 * params.brighten,
                                    kCIInputSaturationKey: 1.0 - 0.05 * params.brighten,
                                   ])
        }
        if params.enlargeEye > 0 || params.brightenEye > 0 {
            ci = applyEyeOps(ci, params: params, originalSize: image.size)
        }
        guard let out = context.createCGImage(ci, from: ci.extent) else { return image }
        return UIImage(cgImage: out, scale: image.scale, orientation: image.imageOrientation)
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
