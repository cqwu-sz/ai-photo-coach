// FrameSemantics.swift
//
// Per-keyframe semantic signal extraction. Runs Apple Vision on a small
// (10 image) batch *after* the KeyframeExtractor has done azimuth-bucket
// selection, so we only pay the Vision cost on frames we actually send.
//
// Three signals match `FrameMeta.person_box / saliency_quadrant /
// horizon_tilt_deg` on the backend:
//
//   personBox        - largest detected person rectangle, normalised to
//                       [0,1] in the frame's own coords (origin top-left
//                       so it lines up with frontend overlays).
//   saliencyQuadrant - which 2×2 quadrant of the frame is the visual
//                       centre of mass (top_left / top_right /
//                       bottom_left / bottom_right / center).
//   horizonTiltDeg   - detected horizon angle from VNDetectHorizonRequest.
//                       Sign convention matches the backend: + when the
//                       right side of the frame is higher.
//
// Each signal independently fails silent (returns nil) so a missing
// detector never blocks the upload. Together they let the LLM reason
// about composition with verifiable pixel evidence instead of guessing
// from the JPEGs alone.

import Foundation
import UIKit
import Vision
import CoreImage

enum FrameSemantics {
    struct Result {
        let personBox: [Double]?
        let saliencyQuadrant: String?
        let horizonTiltDeg: Double?
        /// v9 — three-layer composition signals. Both nullable.
        let foregroundCandidates: [ForegroundCandidate]?
        let depthLayers: DepthLayers?
        /// v10 — pose anchors for distance / tilt reasoning. Apple
        /// Vision returns body-pose keypoints; we extract nose +
        /// midpoint(ankles) y in [0,1] top-left frame coords.
        let poseNoseY: Double?
        let poseAnkleY: Double?
        /// v10.1 — face bbox height fraction (largest face) for tight
        /// portrait distance estimation, and horizon midpoint y for
        /// cross-validating the gyro-based pitch_deg signal.
        let faceHeightRatio: Double?
        let horizonY: Double?
        /// v10.2 — multi-person disambiguation.
        let personCount: Int?
        let subjectBox: [Double]?
        /// v11 — color science / lighting stats. All optional;
        /// computed from the same downscaled bitmap as horizonRowY.
        let rgbMean: [Double]?
        let lumaP05: Double?
        let lumaP95: Double?
        let highlightClipPct: Double?
        let shadowClipPct: Double?
        let saturationMean: Double?
        /// v12 — horizon triangulation + fine-grained pose.
        let horizonYVision: Double?
        let skyMaskTopPct: Double?
        let shoulderTiltDeg: Double?
        let hipOffsetX: Double?
        let chinForward: Double?
        let spineCurve: Double?
    }

    /// Process a sequence of keyframes with subject-stickiness across
    /// frames: the chosen subject from frame N-1 anchors the candidate
    /// scoring in frame N, so a passer-by who briefly enters one frame
    /// won't hijack the entire scan. Sequential by design.
    static func computeMany(images: [UIImage]) -> [Result] {
        var prevSubject: [Double]? = nil
        var out: [Result] = []
        out.reserveCapacity(images.count)
        for img in images {
            let r = compute(for: img, prevSubject: prevSubject)
            if let sb = r.subjectBox { prevSubject = sb }
            out.append(r)
        }
        return out
    }

    /// Single-frame backward-compat path. Calls compute(for:prevSubject:)
    /// with no prior subject so detection falls back to single-shot
    /// "biggest + most central" scoring.
    static func compute(for image: UIImage) -> Result {
        compute(for: image, prevSubject: nil)
    }

    static func compute(for image: UIImage, prevSubject: [Double]?) -> Result {
        guard let cg = image.cgImage else {
            return Result(personBox: nil, saliencyQuadrant: nil,
                          horizonTiltDeg: nil,
                          foregroundCandidates: nil, depthLayers: nil,
                          poseNoseY: nil, poseAnkleY: nil,
                          faceHeightRatio: nil, horizonY: nil,
                          personCount: nil, subjectBox: nil,
                          rgbMean: nil, lumaP05: nil, lumaP95: nil,
                          highlightClipPct: nil, shadowClipPct: nil,
                          saturationMean: nil,
                          horizonYVision: nil, skyMaskTopPct: nil,
                          shoulderTiltDeg: nil, hipOffsetX: nil,
                          chinForward: nil, spineCurve: nil)
        }
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])

        let poses = allPoses(handler: handler)        // [PoseCandidate]
        let faces = allFaces(handler: handler)        // [(box, height)]
        let pickedPose = pickSubject(poses.map { $0.box }, prev: prevSubject)
            .flatMap { box in poses.first(where: { $0.box == box }) }
        let person = pickedPose?.box

        let saliency = saliencyQuadrant(handler: handler)
        let horizon = horizonTilt(handler: handler)
        let fg = foregroundCandidates(cg: cg, handler: handler)
        let depth = MidasDepth.shared.compute(cgImage: cg)

        // Face: prefer one overlapping the chosen pose, else heuristic.
        var faceHeight: Double? = nil
        if let person = person {
            if let f = faces.max(by: { iou($0.box, person) < iou($1.box, person) }),
               iou(f.box, person) > 0.05 {
                faceHeight = f.height
            }
        }
        if faceHeight == nil, let pickedFaceBox = pickSubject(faces.map { $0.box }, prev: prevSubject) {
            faceHeight = faces.first(where: { $0.box == pickedFaceBox })?.height
        }

        let horizonRow = horizonRowY(cgImage: cg)
        let color = computeColorStats(cgImage: cg)
        let horizonVision = horizonYFromVision(handler: handler)
        let skyMaskTop = skyMaskTopFraction(cgImage: cg)
        let poseFine = pickedPose.map { _ in finePoseStats(handler: handler) } ?? .empty
        let count = max(poses.count, faces.count)
        let subject = person ?? pickSubject(faces.map { $0.box }, prev: prevSubject)

        return Result(personBox: person, saliencyQuadrant: saliency,
                      horizonTiltDeg: horizon,
                      foregroundCandidates: fg, depthLayers: depth,
                      poseNoseY: pickedPose?.noseY,
                      poseAnkleY: pickedPose?.ankleY,
                      faceHeightRatio: faceHeight.map { round4($0) },
                      horizonY: horizonRow,
                      personCount: count > 0 ? count : nil,
                      subjectBox: subject,
                      rgbMean: color?.rgbMean,
                      lumaP05: color?.lumaP05,
                      lumaP95: color?.lumaP95,
                      highlightClipPct: color?.highlightClipPct,
                      shadowClipPct: color?.shadowClipPct,
                      saturationMean: color?.saturationMean,
                      horizonYVision: horizonVision,
                      skyMaskTopPct: skyMaskTop,
                      shoulderTiltDeg: poseFine.shoulderTilt,
                      hipOffsetX: poseFine.hipOffsetX,
                      chinForward: poseFine.chinForward,
                      spineCurve: poseFine.spineCurve)
    }

    // ---- v12 horizon (Vision pose-based + sky mask) -------------------
    /// Returns the y-coordinate (top-left, [0,1]) of the horizon line
    /// inferred from VNDetectHorizonRequest. Vision returns an angle +
    /// transform; we pull the line midpoint after applying the inverse
    /// transform to (0, 0.5).
    private static func horizonYFromVision(handler: VNImageRequestHandler) -> Double? {
        let req = VNDetectHorizonRequest()
        do { try handler.perform([req]) } catch { return nil }
        guard let obs = req.results?.first else { return nil }
        // The transform maps points from "level" coords back to image
        // coords. For the centre row at y=0.5 (level), apply the
        // inverse to find where it lands in the captured frame.
        let centre = CGPoint(x: 0.5, y: 0.5).applying(obs.transform.inverted())
        let yTopLeft = 1 - Double(centre.y)
        return round4(max(0, min(1, yTopLeft)))
    }

    /// Cheap sky detector: fraction of pixels in the *top half* of the
    /// frame that are bright (luma > 180) and slightly blue (B/R > 1.05).
    /// When this is < 5% we suppress horizon facts (indoor / no sky).
    private static func skyMaskTopFraction(cgImage: CGImage) -> Double? {
        let targetW = 96
        let aspect = Double(cgImage.height) / Double(cgImage.width)
        let targetH = max(48, Int((Double(targetW) * aspect).rounded()))
        let cs = CGColorSpaceCreateDeviceRGB()
        var pixels = [UInt8](repeating: 0, count: targetH * targetW * 4)
        guard let ctx = CGContext(data: &pixels,
                                  width: targetW, height: targetH,
                                  bitsPerComponent: 8,
                                  bytesPerRow: targetW * 4,
                                  space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            return nil
        }
        ctx.interpolationQuality = .low
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: targetW, height: targetH))
        let halfH = targetH / 2
        var hits = 0, total = 0
        // Bottom-left origin; "top half of image" = upper rows of pixels[].
        for y in (targetH - halfH)..<targetH {
            for x in 0..<targetW {
                let i = (y * targetW + x) * 4
                let r = Double(pixels[i]), g = Double(pixels[i + 1]), b = Double(pixels[i + 2])
                let luma = 0.2989 * r + 0.587 * g + 0.114 * b
                total += 1
                if luma > 180 && (r > 0 ? b / r : 0) > 1.05 { hits += 1 }
            }
        }
        return total > 0 ? round4(Double(hits) / Double(total)) : nil
    }

    // ---- v12 fine-grained pose ----------------------------------------
    private struct FinePose {
        let shoulderTilt: Double?
        let hipOffsetX: Double?
        let chinForward: Double?
        let spineCurve: Double?
        static let empty = FinePose(shoulderTilt: nil, hipOffsetX: nil, chinForward: nil, spineCurve: nil)
    }

    /// Compute shoulder tilt / hip offset / chin forward / spine curve
    /// from the *first* detected body pose. Matches what scene_aggregate
    /// expects in POSE FACTS. Single-person assumption is fine because
    /// the multi-person picker upstream has already chosen a subject.
    private static func finePoseStats(handler: VNImageRequestHandler) -> FinePose {
        let req = VNDetectHumanBodyPoseRequest()
        do { try handler.perform([req]) } catch { return .empty }
        guard let obs = req.results?.first,
              let pts = try? obs.recognizedPoints(.all) else {
            return .empty
        }
        func p(_ j: VNHumanBodyPoseObservation.JointName) -> CGPoint? {
            guard let v = pts[j], v.confidence > 0.4 else { return nil }
            return CGPoint(x: v.location.x, y: 1 - v.location.y)   // top-left origin
        }

        // Shoulder tilt
        var shoulderTilt: Double? = nil
        if let lS = p(.leftShoulder), let rS = p(.rightShoulder) {
            // From left to right: positive dy means right shoulder is
            // *lower* in top-left coords, so flip sign so + = right higher.
            let dy = rS.y - lS.y
            let dx = rS.x - lS.x
            if abs(dx) > 1e-3 {
                shoulderTilt = round1(-atan2(dy, dx) * 180 / .pi)
            }
        }

        // Hip offset
        var hipOffsetX: Double? = nil
        if let lH = p(.leftHip), let rH = p(.rightHip) {
            let mid = (lH.x + rH.x) / 2
            hipOffsetX = round4(Double(mid) * 2 - 1)        // (-1, +1)
        }

        // Chin forward = (nose.x - midShoulder.x) / shoulderWidth
        var chinForward: Double? = nil
        if let nose = p(.nose), let lS = p(.leftShoulder), let rS = p(.rightShoulder) {
            let midX = (lS.x + rS.x) / 2
            let shoulderWidth = abs(rS.x - lS.x)
            if shoulderWidth > 0.02 {
                chinForward = round4(Double(nose.x - midX) / Double(shoulderWidth))
            }
        }

        // Spine curve = triangle area of (head, mid-hip, mid-shoulder)
        // normalised by body height.
        var spineCurve: Double? = nil
        if let neck = p(.neck), let root = p(.root), let nose = p(.nose) {
            // Triangle area between three spine anchor points; near 0 = straight,
            // > 0.05 = noticeably bent.
            let area = abs(
                (Double(neck.x) - Double(nose.x)) * (Double(root.y) - Double(nose.y)) -
                (Double(root.x) - Double(nose.x)) * (Double(neck.y) - Double(nose.y))
            ) / 2
            let bodyH = max(0.05, Double(root.y) - Double(nose.y))
            spineCurve = round4(area / (bodyH * bodyH))
        }

        return FinePose(
            shoulderTilt: shoulderTilt,
            hipOffsetX: hipOffsetX,
            chinForward: chinForward,
            spineCurve: spineCurve,
        )
    }

    // ---- color / lighting stats ---------------------------------------
    private struct ColorStats {
        let rgbMean: [Double]
        let lumaP05: Double
        let lumaP95: Double
        let highlightClipPct: Double
        let shadowClipPct: Double
        let saturationMean: Double
    }

    /// Single-pass color stats over a 96×N downscale of the CGImage.
    /// Mirrors web/js/frame_semantics.js's computeColorStats: average
    /// RGB excludes clipped pixels (luma > 250 or < 5), saturation
    /// uses HSV's max-min/max, percentiles via 256-bin histogram.
    private static func computeColorStats(cgImage: CGImage) -> ColorStats? {
        let targetW = 96
        let aspect = Double(cgImage.height) / Double(cgImage.width)
        let targetH = max(48, Int((Double(targetW) * aspect).rounded()))
        let cs = CGColorSpaceCreateDeviceRGB()
        let bytesPerRow = targetW * 4
        var pixels = [UInt8](repeating: 0, count: targetH * bytesPerRow)
        guard let ctx = CGContext(data: &pixels,
                                  width: targetW, height: targetH,
                                  bitsPerComponent: 8,
                                  bytesPerRow: bytesPerRow,
                                  space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            return nil
        }
        ctx.interpolationQuality = .low
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: targetW, height: targetH))

        var rSum = 0.0, gSum = 0.0, bSum = 0.0, satSum = 0.0
        var nGood = 0
        var hiClip = 0, loClip = 0
        var hist = [Int](repeating: 0, count: 256)
        let totalPx = targetW * targetH

        for y in 0..<targetH {
            for x in 0..<targetW {
                let i = (y * targetW + x) * 4
                let r = Double(pixels[i]), g = Double(pixels[i + 1]), b = Double(pixels[i + 2])
                let luma = Int(0.2989 * r + 0.587 * g + 0.114 * b)
                hist[max(0, min(255, luma))] += 1
                if luma >= 250 { hiClip += 1 }
                else if luma <= 5 { loClip += 1 }
                else {
                    rSum += r; gSum += g; bSum += b
                    let mx = max(r, max(g, b))
                    let mn = min(r, min(g, b))
                    satSum += mx == 0 ? 0 : (mx - mn) / mx
                    nGood += 1
                }
            }
        }
        guard nGood > 0 else { return nil }

        let target05 = Double(totalPx) * 0.05
        let target95 = Double(totalPx) * 0.95
        var acc = 0
        var p05 = 0, p95 = 255
        var p05Set = false
        for v in 0..<256 {
            acc += hist[v]
            if !p05Set && Double(acc) >= target05 { p05 = v; p05Set = true }
            if Double(acc) >= target95 { p95 = v; break }
        }
        return ColorStats(
            rgbMean: [round1(rSum / Double(nGood)),
                      round1(gSum / Double(nGood)),
                      round1(bSum / Double(nGood))],
            lumaP05: Double(p05),
            lumaP95: Double(p95),
            highlightClipPct: round4(Double(hiClip) / Double(totalPx)),
            shadowClipPct:    round4(Double(loClip) / Double(totalPx)),
            saturationMean:   round4(satSum / Double(nGood))
        )
    }

    // ---- multi-person candidate pools -----------------------------------

    private struct PoseCandidate { let box: [Double]; let noseY: Double?; let ankleY: Double? }

    private static func allPoses(handler: VNImageRequestHandler) -> [PoseCandidate] {
        let req = VNDetectHumanBodyPoseRequest()    // returns all detected people
        do { try handler.perform([req]) } catch { return [] }
        let observations = req.results ?? []
        var out: [PoseCandidate] = []
        for obs in observations {
            // Build the bbox from recognised joints.
            guard let pts = try? obs.recognizedPoints(.all) else { continue }
            let visible = pts.values.filter { $0.confidence > 0.4 }
            guard visible.count >= 6 else { continue }
            let xs = visible.map { Double($0.location.x) }
            let ys = visible.map { 1 - Double($0.location.y) }
            let box: [Double] = [
                round4(max(0, xs.min() ?? 0)),
                round4(max(0, ys.min() ?? 0)),
                round4(min(1, (xs.max() ?? 0) - (xs.min() ?? 0))),
                round4(min(1, (ys.max() ?? 0) - (ys.min() ?? 0))),
            ]
            // Same nose / ankle extraction as before.
            func y(of joint: VNHumanBodyPoseObservation.JointName) -> Double? {
                guard let p = pts[joint], p.confidence > 0.4 else { return nil }
                return 1 - Double(p.location.y)
            }
            let nY = y(of: .nose).map { round4($0) }
            let lA = y(of: .leftAnkle), rA = y(of: .rightAnkle)
            let ankles = [lA, rA].compactMap { $0 }
            let aY = ankles.isEmpty ? nil : round4(ankles.reduce(0, +) / Double(ankles.count))
            out.append(PoseCandidate(box: box, noseY: nY, ankleY: aY))
        }
        return out
    }

    private static func allFaces(handler: VNImageRequestHandler) -> [(box: [Double], height: Double)] {
        let req = VNDetectFaceRectanglesRequest()
        do { try handler.perform([req]) } catch { return [] }
        let faces = req.results ?? []
        return faces.compactMap { obs in
            let bb = obs.boundingBox
            let h = Double(bb.height)
            guard h > 0.005 else { return nil }
            // top-left origin
            let box: [Double] = [
                round4(max(0, Double(bb.minX))),
                round4(max(0, 1 - Double(bb.minY) - Double(bb.height))),
                round4(min(1, Double(bb.width))),
                round4(min(1, Double(bb.height))),
            ]
            return (box, h)
        }
    }

    // ---- subject scoring ------------------------------------------------
    //
    // Mirrors the Web pickSubject() so multi-person scenes behave
    // identically across platforms. Score = stickiness × 1.4 + sqrt(area)
    // × 0.9 + central × 0.4.
    private static func pickSubject(_ boxes: [[Double]], prev: [Double]?) -> [Double]? {
        var best: [Double]? = nil
        var bestScore = -1.0
        for b in boxes {
            guard b.count == 4 else { continue }
            let area = b[2] * b[3]
            if area < 0.005 { continue }
            let cx = b[0] + b[2] / 2
            let cy = b[1] + b[3] / 2
            let dist = (((cx - 0.5) * (cx - 0.5)) + ((cy - 0.5) * (cy - 0.5))).squareRoot()
            let central = 1 - min(1, dist / 0.71)
            let stickiness = prev.map { iou(b, $0) } ?? 0
            let score = stickiness * 1.4 + area.squareRoot() * 0.9 + central * 0.4
            if score > bestScore { bestScore = score; best = b }
        }
        return best
    }

    private static func iou(_ a: [Double], _ b: [Double]) -> Double {
        guard a.count == 4, b.count == 4 else { return 0 }
        let x1 = max(a[0], b[0]), y1 = max(a[1], b[1])
        let x2 = min(a[0] + a[2], b[0] + b[2]), y2 = min(a[1] + a[3], b[1] + b[3])
        let inter = max(0, x2 - x1) * max(0, y2 - y1)
        let union = a[2] * a[3] + b[2] * b[3] - inter
        return union > 0 ? inter / union : 0
    }

    // ---- face bbox (largest detected face → height fraction) -----------
    /// Returns the largest detected face's bbox height in [0,1] frame
    /// coords, or nil if no face was found. Used by scene_aggregate as a
    /// sharper distance estimate when ankles are out of frame.
    private static func faceHeightRatio(handler: VNImageRequestHandler) -> Double? {
        let req = VNDetectFaceRectanglesRequest()
        do { try handler.perform([req]) } catch { return nil }
        let faces = req.results ?? []
        guard !faces.isEmpty else { return nil }
        let h = faces.map { Double($0.boundingBox.height) }.max() ?? 0
        return h > 0.005 ? round4(h) : nil
    }

    // ---- horizon midpoint y (row-gradient) -----------------------------
    /// CoreImage-based row-gradient horizon detector. Identical idea to
    /// the web implementation: downscale to ~96 wide, accumulate
    /// |dI/dy| per row over BT.601 luma, pick the row with the largest
    /// peak (smoothed by a 3-tap box filter). Returns y in [0,1] or nil
    /// when no clear horizon dominates.
    private static func horizonRowY(cgImage: CGImage) -> Double? {
        let targetW = 96
        let aspect = Double(cgImage.height) / Double(cgImage.width)
        let targetH = max(48, Int((Double(targetW) * aspect).rounded()))
        let cs = CGColorSpaceCreateDeviceRGB()
        let bytesPerRow = targetW * 4
        var pixels = [UInt8](repeating: 0, count: targetH * bytesPerRow)
        guard let ctx = CGContext(data: &pixels,
                                  width: targetW, height: targetH,
                                  bitsPerComponent: 8,
                                  bytesPerRow: bytesPerRow,
                                  space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            return nil
        }
        ctx.interpolationQuality = .low
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: targetW, height: targetH))
        // CoreGraphics draws with origin bottom-left when blitting into a
        // bitmap; that means our row 0 in `pixels` is the bottom of the
        // image. We just compute everything in that frame and flip y at
        // the end so the returned value matches top-left convention.
        var rowDy = [Double](repeating: 0, count: targetH)
        for y in 0..<(targetH - 1) {
            var sum: Double = 0
            for x in 0..<(targetW - 1) {
                let i  = (y * targetW + x) * 4
                let iD = ((y + 1) * targetW + x) * 4
                let l0 = 0.2989 * Double(pixels[i])     + 0.587 * Double(pixels[i + 1])     + 0.114 * Double(pixels[i + 2])
                let lD = 0.2989 * Double(pixels[iD])    + 0.587 * Double(pixels[iD + 1])    + 0.114 * Double(pixels[iD + 2])
                sum += abs(lD - l0)
            }
            rowDy[y] = sum
        }
        let skip = max(2, Int(Double(targetH) * 0.10))
        var bestY = -1
        var bestVal: Double = 0
        for y in skip..<(targetH - skip) {
            let v = (rowDy[y - 1] + rowDy[y] + rowDy[y + 1]) / 3.0
            if v > bestVal { bestVal = v; bestY = y }
        }
        let mean = rowDy.reduce(0, +) / Double(targetH)
        guard bestY >= 0, bestVal > mean * 1.6 else { return nil }
        // Flip from bottom-left to top-left origin.
        let yFromTop = Double(targetH - 1 - bestY) / Double(targetH)
        return round4(yFromTop)
    }

    // ---- person box ------------------------------------------------------

    private static func personBox(handler: VNImageRequestHandler) -> [Double]? {
        let req = VNDetectHumanRectanglesRequest()
        req.upperBodyOnly = false
        do { try handler.perform([req]) } catch { return nil }
        guard let observations = req.results, !observations.isEmpty else { return nil }
        // Pick the largest box — that's the most likely "subject".
        // Vision returns boxes in normalised image coords with origin at
        // bottom-left; backend & web overlays use top-left, so flip Y.
        let largest = observations.max(by: { lhs, rhs in
            (lhs.boundingBox.width * lhs.boundingBox.height)
                < (rhs.boundingBox.width * rhs.boundingBox.height)
        })
        guard let r = largest?.boundingBox else { return nil }
        let x = Double(r.minX)
        let y = Double(1 - r.minY - r.height)   // flip to top-left origin
        let w = Double(r.width)
        let h = Double(r.height)
        return [round4(x), round4(y), round4(w), round4(h)]
    }

    // ---- saliency quadrant ----------------------------------------------

    private static func saliencyQuadrant(handler: VNImageRequestHandler) -> String? {
        let req = VNGenerateAttentionBasedSaliencyImageRequest()
        do { try handler.perform([req]) } catch { return nil }
        guard let obs = req.results?.first else { return nil }
        // Saliency observation provides up to N salient objects with a
        // confidence-weighted bounding box. We average the centres
        // weighted by area as a "centre of attention" estimate.
        let regions = obs.salientObjects ?? []
        guard !regions.isEmpty else { return nil }
        var sumW: Double = 0
        var cx: Double = 0
        var cy: Double = 0
        for r in regions {
            let bb = r.boundingBox
            let w = Double(bb.width * bb.height)   // weight by area
            sumW += w
            cx += w * Double(bb.midX)
            cy += w * (1 - Double(bb.midY))        // flip Y to top-left
        }
        guard sumW > 0 else { return nil }
        let centerX = cx / sumW
        let centerY = cy / sumW
        return quadrantName(x: centerX, y: centerY)
    }

    /// 5-bucket grid: a generous "center" region (middle 30%) plus four
    /// corners. Matches the backend prompt's vocabulary.
    private static func quadrantName(x: Double, y: Double) -> String {
        let centerLow = 0.35
        let centerHi  = 0.65
        if (centerLow...centerHi).contains(x), (centerLow...centerHi).contains(y) {
            return "center"
        }
        let isTop = y < 0.5
        let isLeft = x < 0.5
        switch (isTop, isLeft) {
        case (true, true):   return "top_left"
        case (true, false):  return "top_right"
        case (false, true):  return "bottom_left"
        case (false, false): return "bottom_right"
        }
    }

    // ---- horizon tilt ----------------------------------------------------

    private static func horizonTilt(handler: VNImageRequestHandler) -> Double? {
        let req = VNDetectHorizonRequest()
        do { try handler.perform([req]) } catch { return nil }
        guard let obs = req.results?.first else { return nil }
        // Vision's `angle` is in radians, positive when image needs to
        // be rotated counter-clockwise to level. Convert to "right side
        // higher" convention: positive when the right edge is higher
        // than the left, which matches our schema.
        let degrees = -Double(obs.angle) * 180.0 / .pi
        return round1(degrees)
    }

    // ---- pose anchors (nose + midpoint(ankles)) -------------------------
    /// Returns y of nose + averaged y of L+R ankles in [0,1] top-left
    /// origin. Used by scene_aggregate to compute crouch/lift nudges
    /// and recommend a focal length from on-screen body height.
    private static func poseAnchors(handler: VNImageRequestHandler) -> (noseY: Double?, ankleY: Double?) {
        let req = VNDetectHumanBodyPoseRequest()
        do { try handler.perform([req]) } catch { return (nil, nil) }
        guard let obs = req.results?.first else { return (nil, nil) }

        // Vision points are bottom-left origin, normalised. Convert to
        // top-left to match the rest of our schema.
        func y(of joint: VNHumanBodyPoseObservation.JointName) -> Double? {
            guard let p = try? obs.recognizedPoint(joint), p.confidence > 0.4 else { return nil }
            return 1 - Double(p.location.y)
        }
        let noseY = y(of: .nose)
        let lAnk = y(of: .leftAnkle)
        let rAnk = y(of: .rightAnkle)
        let ankles = [lAnk, rAnk].compactMap { $0 }
        let ankleY = ankles.isEmpty ? nil : ankles.reduce(0, +) / Double(ankles.count)
        return (
            noseY.map { round4($0) },
            ankleY.map { round4($0) }
        )
    }

    private static func round4(_ x: Double) -> Double { (x * 10_000).rounded() / 10_000 }
    private static func round1(_ x: Double) -> Double { (x * 10).rounded() / 10 }

    // ---- foreground candidates ------------------------------------------

    /// Apple Vision doesn't ship a generic 80-class detector, so we
    /// emulate one in two passes:
    ///   1. ``VNGenerateObjectnessBasedSaliencyImageRequest`` → up to
    ///      8 anonymous "this looks like a discrete object" boxes.
    ///   2. For each box, crop and run ``VNClassifyImageRequest`` (1k
    ///      ImageNet classes). Filter to a curated foreground allow-list.
    /// Result: labelled foreground boxes, fully on-device, no model
    /// bundling. Output is capped at top-3 by area.
    private static func foregroundCandidates(cg: CGImage, handler: VNImageRequestHandler) -> [ForegroundCandidate]? {
        let salReq = VNGenerateObjectnessBasedSaliencyImageRequest()
        do { try handler.perform([salReq]) } catch { return nil }
        guard let observations = salReq.results?.first?.salientObjects, !observations.isEmpty else {
            return nil
        }
        var out: [ForegroundCandidate] = []
        for obs in observations {
            let bb = obs.boundingBox
            // Crop the original CGImage to the salient region (Vision
            // boxes are bottom-left origin and normalised).
            let cropRect = CGRect(
                x: bb.minX * CGFloat(cg.width),
                y: (1 - bb.minY - bb.height) * CGFloat(cg.height),
                width: bb.width * CGFloat(cg.width),
                height: bb.height * CGFloat(cg.height)
            ).integral
            guard cropRect.width > 16, cropRect.height > 16,
                  let cropCG = cg.cropping(to: cropRect) else { continue }

            let cls = classifyForeground(cg: cropCG)
            guard let cls else { continue }
            let x = Double(bb.minX)
            let y = Double(1 - bb.minY - bb.height)   // top-left origin
            let w = Double(bb.width)
            let h = Double(bb.height)
            out.append(ForegroundCandidate(
                label: cls.label,
                box: [round4(x), round4(y), round4(w), round4(h)],
                confidence: round4(cls.score),
                estimatedDistanceM: nil
            ))
        }
        out.sort { ($0.box[2] * $0.box[3]) > ($1.box[2] * $1.box[3]) }
        let top = Array(out.prefix(3))
        return top.isEmpty ? nil : top
    }

    /// Allow-list keyword → friendlier label sent to backend. We match
    /// ImageNet labels via case-insensitive substring (the labels are
    /// long phrases like "yellow lady's slipper, yellow lady-slipper,
    /// Cypripedium calceolus"). Anything not matching → discarded.
    private static let FOREGROUND_KEYWORDS: [(String, String)] = [
        ("flowerpot", "potted_plant"),
        ("vase",      "flower_vase"),
        ("daisy",     "flower"),
        ("rose",      "flower"),
        ("tulip",     "flower"),
        ("orchid",    "flower"),
        ("lily",      "flower"),
        ("park bench","bench"),
        ("bench",     "bench"),
        ("rocking chair","chair"),
        ("folding chair","chair"),
        ("umbrella",  "umbrella"),
        ("traffic light","sign_post"),
        ("street sign","sign_post"),
        ("fountain",  "fountain"),
        ("plant",     "plant"),
        ("tree",      "tree"),
        ("fence",     "fence"),
        ("picket fence","fence"),
        ("railing",   "railing"),
        ("balustrade","railing"),
        ("doorway",   "doorway"),
        ("archway",   "archway"),
        ("window",    "window"),
        ("birdcage",  "small_object"),
    ]

    private static func classifyForeground(cg: CGImage) -> (label: String, score: Double)? {
        let req = VNClassifyImageRequest()
        let h = VNImageRequestHandler(cgImage: cg, options: [:])
        do { try h.perform([req]) } catch { return nil }
        guard let cls = req.results else { return nil }
        for c in cls.prefix(8) {
            let id = c.identifier.lowercased()
            for (kw, friendly) in FOREGROUND_KEYWORDS {
                if id.contains(kw) {
                    return (friendly, Double(c.confidence))
                }
            }
        }
        return nil
    }
}

// MARK: - MiDaS depth (optional)

import CoreML

/// MiDaS Small (or DepthAnything Small) Core ML wrapper. The .mlmodelc
/// file isn't bundled by default; if present it's loaded once and used
/// to return per-frame depth-layer histograms. Missing model = no
/// signal (NIL), backend gracefully falls back.
final class MidasDepth {
    static let shared = MidasDepth()

    private let model: MLModel?
    private let inputName: String?
    private let inputSize: Int

    private init() {
        // Look for either name in main bundle. We don't crash if absent.
        let candidates = ["MiDaSSmall", "DepthAnythingSmall", "MiDaS"]
        var loaded: (MLModel, String, Int)?
        for name in candidates {
            guard let url = Bundle.main.url(forResource: name, withExtension: "mlmodelc"),
                  let m = try? MLModel(contentsOf: url) else { continue }
            // Sniff input name + size from model description.
            let desc = m.modelDescription
            guard let firstInput = desc.inputDescriptionsByName.first else { continue }
            let key = firstInput.key
            let imgConstraint = firstInput.value.imageConstraint
            let size = Int(imgConstraint?.pixelsHigh ?? 256)
            loaded = (m, key, size)
            break
        }
        if let l = loaded {
            self.model = l.0
            self.inputName = l.1
            self.inputSize = l.2
        } else {
            self.model = nil
            self.inputName = nil
            self.inputSize = 256
        }
    }

    /// Return depth_layers for the given frame, or nil if no model is
    /// bundled / inference fails. Quantile-bucket the inverse-depth
    /// output into near/mid/far thirds so we don't need calibration.
    func compute(cgImage cg: CGImage) -> DepthLayers? {
        guard let model, let inputName else { return nil }
        guard let pixelBuf = cg.toCVPixelBuffer(width: inputSize, height: inputSize) else {
            return nil
        }
        let provider: MLFeatureProvider
        do {
            provider = try MLDictionaryFeatureProvider(
                dictionary: [inputName: MLFeatureValue(pixelBuffer: pixelBuf)]
            )
        } catch { return nil }
        let result: MLFeatureProvider
        do { result = try model.prediction(from: provider) } catch { return nil }
        guard let firstOutput = result.featureNames.first,
              let arr = result.featureValue(for: firstOutput)?.multiArrayValue else { return nil }

        let count = arr.count
        guard count > 0 else { return nil }
        var values = [Float](repeating: 0, count: count)
        for i in 0..<count {
            values[i] = Float(truncating: arr[i])
        }
        let sorted = values.sorted()
        let q1 = sorted[Int(Double(sorted.count) * 0.33)]
        let q2 = sorted[Int(Double(sorted.count) * 0.66)]
        var near = 0, mid = 0, far = 0
        for v in values {
            if v >= q2 { near += 1 }
            else if v >= q1 { mid += 1 }
            else { far += 1 }
        }
        let total = Double(count)
        return DepthLayers(
            nearPct: round4(Double(near) / total),
            midPct:  round4(Double(mid)  / total),
            farPct:  round4(Double(far)  / total),
            source: "midas_ios"
        )
    }

    private func round4(_ x: Double) -> Double { (x * 10_000).rounded() / 10_000 }
}

// CGImage → CVPixelBuffer for Core ML input. Resizes via CG drawing,
// not Accelerate, because we're already on a background queue and the
// keyframe count is small.
private extension CGImage {
    func toCVPixelBuffer(width: Int, height: Int) -> CVPixelBuffer? {
        var pb: CVPixelBuffer?
        let attrs: [CFString: Any] = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
        ]
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault, width, height,
            kCVPixelFormatType_32BGRA,
            attrs as CFDictionary, &pb
        )
        guard status == kCVReturnSuccess, let buf = pb else { return nil }
        CVPixelBufferLockBaseAddress(buf, [])
        defer { CVPixelBufferUnlockBaseAddress(buf, []) }
        guard let ctx = CGContext(
            data: CVPixelBufferGetBaseAddress(buf),
            width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buf),
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue
                | CGBitmapInfo.byteOrder32Little.rawValue
        ) else { return nil }
        ctx.draw(self, in: CGRect(x: 0, y: 0, width: width, height: height))
        return buf
    }
}
