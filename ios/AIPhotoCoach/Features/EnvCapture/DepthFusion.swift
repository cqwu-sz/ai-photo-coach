// DepthFusion.swift
//
// Thin wrapper around AVCaptureDepthDataOutput + per-keyframe depth
// fusion. Optional path: when the device exposes a depth-capable
// format on the back camera (LiDAR Pro, dual-cam non-Pro since iPhone
// 11), VideoCaptureSession opportunistically wires up a second output
// and calls ``record(depth:)`` on every depth frame.
//
// At keyframe analysis time we look up the depth frame nearest to the
// keyframe's timestamp and use it for two things:
//   1. Override MiDaS's relative-depth histogram with absolute meters.
//   2. Annotate every ``ForegroundCandidate`` with the median depth
//      inside its bounding box → real ``estimatedDistanceM``.
//
// All-or-nothing per device: if AVDepthData isn't available, the rest
// of the pipeline still works via MiDaS and the LLM still gets useful
// (just less accurate) depth guidance.

import AVFoundation
import CoreImage
import Foundation

@MainActor
final class DepthRingBuffer {
    /// Polymorphic depth payload: AVDepthData on dual-cam phones,
    /// ARKit's smoothed Float32 pixel buffer on LiDAR phones. Both
    /// land in the same nearest() lookup.
    enum Payload {
        case avDepth(AVDepthData)
        case arkit(depth: CVPixelBuffer, confidence: CVPixelBuffer?)
    }
    private struct Entry { let timestampMs: Int; let payload: Payload; let source: String }
    private var buffer: [Entry] = []
    private let cap = 32

    func record(depth: AVDepthData, atTimestampMs ts: Int, source: String) {
        buffer.append(Entry(timestampMs: ts, payload: .avDepth(depth), source: source))
        if buffer.count > cap { buffer.removeFirst(buffer.count - cap) }
    }

    /// v12 — ARKit smoothed scene depth path. `confidence` may be nil
    /// on devices that don't surface a confidence map; histogram code
    /// then accepts every pixel.
    func record(arkitDepth: CVPixelBuffer, confidence: CVPixelBuffer?, atTimestampMs ts: Int) {
        buffer.append(Entry(timestampMs: ts,
                            payload: .arkit(depth: arkitDepth, confidence: confidence),
                            source: "arkit"))
        if buffer.count > cap { buffer.removeFirst(buffer.count - cap) }
    }

    func reset() { buffer.removeAll() }

    /// Return the depth frame closest in time to ``ts``. Same 1.5 s
    /// tolerance for both source types so the rest of the pipeline
    /// stays oblivious to which sensor produced the data.
    func nearest(toTimestampMs ts: Int) -> (payload: Payload, source: String)? {
        guard !buffer.isEmpty else { return nil }
        var best = buffer[0]
        var bestDelta = abs(buffer[0].timestampMs - ts)
        for e in buffer {
            let d = abs(e.timestampMs - ts)
            if d < bestDelta { best = e; bestDelta = d }
        }
        guard bestDelta <= 1500 else { return nil }
        return (best.payload, best.source)
    }
}

/// Pure functions that turn an AVDepthData frame into the same shape
/// as MiDaS output (DepthLayers histogram + per-box distance lookup).
enum DepthFusion {
    /// Polymorphic entry point — dispatches on the buffer payload kind.
    /// Call this from EnvCaptureViewModel; it picks AVDepthData vs.
    /// ARKit code paths transparently.
    static func histogram(payload: DepthRingBuffer.Payload, source: String) -> DepthLayers? {
        switch payload {
        case .avDepth(let d):
            return histogram(from: d, source: source)
        case .arkit(let depth, let confidence):
            return histogramFromArkit(depth: depth, confidence: confidence, source: source)
        }
    }

    static func medianDepth(in box: [Double], payload: DepthRingBuffer.Payload) -> Double? {
        switch payload {
        case .avDepth(let d):
            return medianDepth(in: box, from: d)
        case .arkit(let depth, let confidence):
            return medianDepthFromArkit(in: box, depth: depth, confidence: confidence)
        }
    }

    /// ARKit smoothed sceneDepth path. Buffer is Float32 metres; the
    /// confidence map (when present) is OneComponent8 with 0=low,
    /// 1=medium, 2=high — we drop low-confidence pixels.
    static func histogramFromArkit(depth: CVPixelBuffer, confidence: CVPixelBuffer?, source: String) -> DepthLayers? {
        CVPixelBufferLockBaseAddress(depth, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depth, .readOnly) }
        let w = CVPixelBufferGetWidth(depth)
        let h = CVPixelBufferGetHeight(depth)
        let bpr = CVPixelBufferGetBytesPerRow(depth)
        guard let base = CVPixelBufferGetBaseAddress(depth) else { return nil }

        var confBase: UnsafeMutableRawPointer? = nil
        var confBpr = 0
        if let conf = confidence {
            CVPixelBufferLockBaseAddress(conf, .readOnly)
            confBase = CVPixelBufferGetBaseAddress(conf)
            confBpr = CVPixelBufferGetBytesPerRow(conf)
        }
        defer { if let conf = confidence { CVPixelBufferUnlockBaseAddress(conf, .readOnly) } }

        var near = 0, mid = 0, far = 0, total = 0
        for y in 0..<h {
            let row = base.advanced(by: y * bpr).bindMemory(to: Float32.self, capacity: w)
            let confRow = confBase?.advanced(by: y * confBpr).bindMemory(to: UInt8.self, capacity: w)
            for x in 0..<w {
                if let c = confRow, c[x] == 0 { continue }   // drop low confidence
                let v = row[x]
                if !v.isFinite || v <= 0 { continue }
                total += 1
                if v < 1.5 { near += 1 }
                else if v < 5.0 { mid += 1 }
                else { far += 1 }
            }
        }
        guard total > 0 else { return nil }
        return DepthLayers(
            nearPct: round4(Double(near) / Double(total)),
            midPct:  round4(Double(mid)  / Double(total)),
            farPct:  round4(Double(far)  / Double(total)),
            source: source
        )
    }

    static func medianDepthFromArkit(in box: [Double], depth: CVPixelBuffer, confidence: CVPixelBuffer?) -> Double? {
        guard box.count == 4 else { return nil }
        CVPixelBufferLockBaseAddress(depth, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depth, .readOnly) }
        let w = CVPixelBufferGetWidth(depth)
        let h = CVPixelBufferGetHeight(depth)
        let bpr = CVPixelBufferGetBytesPerRow(depth)
        guard let base = CVPixelBufferGetBaseAddress(depth) else { return nil }

        var confBase: UnsafeMutableRawPointer? = nil
        var confBpr = 0
        if let conf = confidence {
            CVPixelBufferLockBaseAddress(conf, .readOnly)
            confBase = CVPixelBufferGetBaseAddress(conf)
            confBpr = CVPixelBufferGetBytesPerRow(conf)
        }
        defer { if let conf = confidence { CVPixelBufferUnlockBaseAddress(conf, .readOnly) } }

        let x0 = max(0, min(w - 1, Int(box[0] * Double(w))))
        let y0 = max(0, min(h - 1, Int(box[1] * Double(h))))
        let x1 = max(x0 + 1, min(w, Int((box[0] + box[2]) * Double(w))))
        let y1 = max(y0 + 1, min(h, Int((box[1] + box[3]) * Double(h))))

        var samples: [Float] = []
        samples.reserveCapacity(min(2000, (x1 - x0) * (y1 - y0)))
        let strideX = max(1, (x1 - x0) / 50)
        let strideY = max(1, (y1 - y0) / 50)
        for y in Swift.stride(from: y0, to: y1, by: strideY) {
            let row = base.advanced(by: y * bpr).bindMemory(to: Float32.self, capacity: w)
            let confRow = confBase?.advanced(by: y * confBpr).bindMemory(to: UInt8.self, capacity: w)
            for x in Swift.stride(from: x0, to: x1, by: strideX) {
                if let c = confRow, c[x] == 0 { continue }
                let v = row[x]
                if v.isFinite, v > 0, v < 30 { samples.append(v) }
            }
        }
        guard samples.count >= 8 else { return nil }
        samples.sort()
        let median = Double(samples[samples.count / 2])
        return (median * 100).rounded() / 100
    }

    /// Convert AVDepthData (typically Float16 disparity for dual-cam
    /// or Float32 absolute depth for LiDAR) into a near/mid/far
    /// histogram with hard meter thresholds:
    ///   * near = depth < 1.5 m
    ///   * mid  = 1.5 m ≤ depth < 5 m
    ///   * far  = depth >= 5 m  (incl. invalid pixels treated as far)
    /// Caller passes ``source`` so the backend knows whether the
    /// depth came from LiDAR (highest trust) or dual-cam (medium).
    static func histogram(from depthData: AVDepthData, source: String) -> DepthLayers? {
        // Convert disparity → depth if needed; depth is in metres.
        let depthFrame = depthData.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32)
        let buf = depthFrame.depthDataMap
        CVPixelBufferLockBaseAddress(buf, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }

        let w = CVPixelBufferGetWidth(buf)
        let h = CVPixelBufferGetHeight(buf)
        let bpr = CVPixelBufferGetBytesPerRow(buf)
        guard let base = CVPixelBufferGetBaseAddress(buf) else { return nil }

        var near = 0, mid = 0, far = 0
        let total = w * h
        for y in 0..<h {
            let row = base.advanced(by: y * bpr).bindMemory(to: Float32.self, capacity: w)
            for x in 0..<w {
                let v = row[x]
                if !v.isFinite || v <= 0 { far += 1; continue }
                if v < 1.5 { near += 1 }
                else if v < 5.0 { mid += 1 }
                else { far += 1 }
            }
        }
        return DepthLayers(
            nearPct: round4(Double(near) / Double(total)),
            midPct:  round4(Double(mid)  / Double(total)),
            farPct:  round4(Double(far)  / Double(total)),
            source: source
        )
    }

    /// Look up the median valid depth value inside a normalised box
    /// in the depth map's coordinate space. Use to annotate a
    /// ForegroundCandidate with a real metres estimate.
    static func medianDepth(in box: [Double], from depthData: AVDepthData) -> Double? {
        guard box.count == 4 else { return nil }
        let depthFrame = depthData.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32)
        let buf = depthFrame.depthDataMap
        CVPixelBufferLockBaseAddress(buf, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }

        let w = CVPixelBufferGetWidth(buf)
        let h = CVPixelBufferGetHeight(buf)
        let bpr = CVPixelBufferGetBytesPerRow(buf)
        guard let base = CVPixelBufferGetBaseAddress(buf) else { return nil }

        // Box uses top-left origin in [0,1]; depth map in iOS is also
        // top-left after conversion. Clamp to safe ranges.
        let x0 = max(0, min(w - 1, Int(box[0] * Double(w))))
        let y0 = max(0, min(h - 1, Int(box[1] * Double(h))))
        let x1 = max(x0 + 1, min(w, Int((box[0] + box[2]) * Double(w))))
        let y1 = max(y0 + 1, min(h, Int((box[1] + box[3]) * Double(h))))

        var samples: [Float] = []
        samples.reserveCapacity(min(2000, (x1 - x0) * (y1 - y0)))
        // Stride to keep cost bounded for huge boxes — 50x50 grid is plenty.
        let strideX = max(1, (x1 - x0) / 50)
        let strideY = max(1, (y1 - y0) / 50)
        for y in Swift.stride(from: y0, to: y1, by: strideY) {
            let row = base.advanced(by: y * bpr).bindMemory(to: Float32.self, capacity: w)
            for x in Swift.stride(from: x0, to: x1, by: strideX) {
                let v = row[x]
                if v.isFinite, v > 0, v < 30 { samples.append(v) }
            }
        }
        guard samples.count >= 8 else { return nil }
        samples.sort()
        let median = Double(samples[samples.count / 2])
        return (median * 100).rounded() / 100   // round to cm
    }

    private static func round4(_ x: Double) -> Double { (x * 10_000).rounded() / 10_000 }
}
