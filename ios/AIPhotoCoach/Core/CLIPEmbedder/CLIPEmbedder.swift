import CoreML
import Foundation
import UIKit

/// Wraps a CoreML CLIP image encoder.
///
/// We expect a model file named `CLIPImageEncoder.mlmodelc` to be in the app
/// bundle. If it's missing (e.g. during Phase 1 development), `embed` returns
/// nil and the rest of the app behaves as if no embedding is available.
///
/// To produce the model, run scripts/convert_clip_to_coreml.py on a Mac and
/// drag the resulting .mlpackage into `ios/AIPhotoCoach/Resources/`.
actor CLIPEmbedder {
    static let shared = CLIPEmbedder()

    private var model: MLModel?
    private var loaded = false

    private func ensureLoaded() {
        if loaded { return }
        loaded = true

        let candidates = [
            "CLIPImageEncoder",
            "CLIPImageEncoder_512",
            "clip_image_encoder",
        ]
        for name in candidates {
            if let url = Bundle.main.url(forResource: name, withExtension: "mlmodelc")
                ?? Bundle.main.url(forResource: name, withExtension: "mlpackage") {
                do {
                    self.model = try MLModel(contentsOf: url)
                    return
                } catch {
                    print("CLIP: failed to load \(name): \(error)")
                }
            }
        }
        print("CLIP: no model bundled. Embeddings disabled.")
    }

    func isAvailable() -> Bool {
        ensureLoaded()
        return model != nil
    }

    /// Returns a normalized float vector embedding, or nil if no model.
    func embed(image: UIImage) async -> [Float]? {
        ensureLoaded()
        guard let model else { return nil }

        guard let cg = image.cgImage,
              let resized = resizeAndNormalize(cgImage: cg, size: 224) else { return nil }

        do {
            let inputName = model.modelDescription.inputDescriptionsByName.keys.first ?? "image"
            let provider = try MLDictionaryFeatureProvider(dictionary: [
                inputName: MLFeatureValue(pixelBuffer: resized)
            ])
            let out = try await model.prediction(from: provider)
            let outputName = out.featureNames.first ?? ""
            guard let array = out.featureValue(for: outputName)?.multiArrayValue else {
                return nil
            }
            return Self.normalize(Self.toFloats(array))
        } catch {
            print("CLIP: prediction failed \(error)")
            return nil
        }
    }

    private func resizeAndNormalize(cgImage: CGImage, size: Int) -> CVPixelBuffer? {
        let attrs = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
        ] as CFDictionary
        var pb: CVPixelBuffer?
        guard CVPixelBufferCreate(
            kCFAllocatorDefault, size, size,
            kCVPixelFormatType_32ARGB, attrs, &pb) == kCVReturnSuccess,
            let buf = pb else { return nil }

        CVPixelBufferLockBaseAddress(buf, [])
        defer { CVPixelBufferUnlockBaseAddress(buf, []) }

        let ctx = CGContext(
            data: CVPixelBufferGetBaseAddress(buf),
            width: size, height: size,
            bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buf),
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue
        )
        ctx?.draw(cgImage, in: CGRect(x: 0, y: 0, width: size, height: size))
        return buf
    }

    private static func toFloats(_ array: MLMultiArray) -> [Float] {
        let count = array.count
        var out = [Float](repeating: 0, count: count)
        switch array.dataType {
        case .float16, .float32:
            for i in 0..<count {
                out[i] = Float(truncating: array[i])
            }
        default:
            for i in 0..<count {
                out[i] = Float(truncating: array[i])
            }
        }
        return out
    }

    private static func normalize(_ vec: [Float]) -> [Float] {
        let norm = sqrt(vec.reduce(0) { $0 + $1 * $1 })
        guard norm > 0 else { return vec }
        return vec.map { $0 / norm }
    }
}

enum CLIPSimilarity {
    /// Returns the cosine similarity between two normalized vectors. Both
    /// vectors are expected to already be L2-normalized.
    static func cosine(_ a: [Float], _ b: [Float]) -> Float {
        let n = min(a.count, b.count)
        var dot: Float = 0
        for i in 0..<n {
            dot += a[i] * b[i]
        }
        return dot
    }

    /// Top-K most similar reference entries to a query embedding.
    static func topK(query: [Float],
                     candidates: [ReferenceImageEntry],
                     k: Int = 3) -> [(ReferenceImageEntry, Float)] {
        candidates
            .compactMap { entry -> (ReferenceImageEntry, Float)? in
                guard let emb = entry.embedding else { return nil }
                return (entry, cosine(query, emb))
            }
            .sorted { $0.1 > $1.1 }
            .prefix(k)
            .map { $0 }
    }
}
