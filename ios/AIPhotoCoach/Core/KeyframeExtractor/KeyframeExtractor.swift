import Foundation
import UIKit

/// Picks `target` keyframes from a sequence of HeadedFrame samples so that
/// the chosen frames span the captured azimuth range as evenly as possible.
///
/// Strategy:
///   1. Bucket samples by azimuth (8-12 buckets across the panned range).
///   2. From each populated bucket, pick the sample whose timestamp is most
///      central to that bucket (most stable hold).
///   3. If we still have fewer than `target / 2` frames, fall back to
///      uniform time sampling.
struct KeyframeExtractor {
    func extract(from samples: [HeadedFrame], target: Int = 10) -> [HeadedFrame] {
        guard !samples.isEmpty else { return [] }
        guard samples.count > target else { return samples }

        let azimuths = samples.map { $0.azimuthDeg }
        let minAz = azimuths.min() ?? 0
        let maxAz = azimuths.max() ?? 0
        let span = maxAz - minAz

        if span < 30 {
            return uniformByTime(samples: samples, target: target)
        }

        let bucketCount = max(target, 8)
        let bucketWidth = max(1.0, span / Double(bucketCount))

        var buckets: [Int: [HeadedFrame]] = [:]
        for s in samples {
            let idx = min(Int((s.azimuthDeg - minAz) / bucketWidth), bucketCount - 1)
            buckets[idx, default: []].append(s)
        }

        var result: [HeadedFrame] = []
        for idx in 0..<bucketCount {
            guard let bucket = buckets[idx], !bucket.isEmpty else { continue }
            let median = bucket[bucket.count / 2]
            result.append(median)
        }

        if result.count < target {
            let extras = uniformByTime(samples: samples, target: target - result.count)
            for e in extras where !result.contains(where: { $0.timestampMs == e.timestampMs }) {
                result.append(e)
                if result.count >= target { break }
            }
        }

        if result.count > target {
            result = Array(result.prefix(target))
        }

        return result.sorted { $0.timestampMs < $1.timestampMs }
    }

    private func uniformByTime(samples: [HeadedFrame], target: Int) -> [HeadedFrame] {
        guard !samples.isEmpty, target > 0 else { return [] }
        let n = samples.count
        if n <= target { return samples }
        let step = Double(n - 1) / Double(target - 1)
        return (0..<target).map { i in
            let idx = Int(round(Double(i) * step))
            return samples[min(idx, n - 1)]
        }
    }
}
