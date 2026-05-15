// FeedbackUploader.swift
//
// v12 — closed-loop feedback. After the user finishes an analyze
// session, we look at their Photos library for an asset captured
// within the last 5 minutes (post-recommendation), pull its EXIF, and
// POST the (recommendation, realised EXIF) pair to /feedback.
//
// The backend persists this pair into a sqlite table that periodic
// calibration scripts mine to refine K_face / K_body / STYLE_PALETTE
// against ground-truth iPhone behaviour.
//
// Permissions: requires NSPhotoLibraryUsageDescription. We ask for
// the *.addOnly* / *.readWrite* permission lazily and downgrade to
// no-op when denied.

import Foundation
import ImageIO
import Photos
import UIKit

extension JSONEncoder {
    /// Shared encoder that snake-cases keys + uses ISO8601 dates,
    /// matching the backend Pydantic conventions.
    static var snakeIso8601: JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.dateEncodingStrategy = .iso8601
        return e
    }
}

actor FeedbackUploader {
    private let endpoint: URL
    private let baseURL: URL

    init(baseURL: URL) {
        self.baseURL = baseURL
        self.endpoint = baseURL.appendingPathComponent("/feedback/")
    }

    /// P1-7.1 / P3-feedback-loop — record what filters / beauty knobs
    /// the user *actually* applied versus what the LLM recommended via
    /// ``PostProcessRecipe``. The diff (e.g. recipe said ``film_warm``
    /// but user picked ``hk_neon``) is what the calibration job mines
    /// to refine the ``plan → filter`` mapping. Fire-and-forget; not
    /// user-blocking.
    func recordPostProcess(
        analyzeRequestId: String?,
        presetId: String,
        lutId: String? = nil,
        recipeApplied: Bool? = nil,
        recipeFilterPreset: String? = nil,
        recipeLutId: String? = nil,
        recipeBeautyIntensity: Double? = nil,
        recipeDowngraded: Bool? = nil,
        presetSwapCount: Int? = nil,
        smooth: Double,
        brighten: Double,
        slim: Double,
        enlargeEye: Double,
        brightenEye: Double,
    ) async {
        let url = baseURL.appendingPathComponent("/feedback/post_process")
        var body: [String: Any] = [
            "analyze_request_id": analyzeRequestId ?? NSNull(),
            "preset_id": presetId,
            "lut_id": lutId ?? NSNull(),
            "recipe_applied": recipeApplied ?? NSNull(),
            "recipe_filter_preset": recipeFilterPreset ?? NSNull(),
            "recipe_lut_id": recipeLutId ?? NSNull(),
            "recipe_beauty_intensity": recipeBeautyIntensity ?? NSNull(),
            "recipe_downgraded": recipeDowngraded ?? NSNull(),
            "preset_swap_count": presetSwapCount ?? NSNull(),
            "smooth": smooth,
            "brighten": brighten,
            "slim": slim,
            "enlarge_eye": enlargeEye,
            "brighten_eye": brightenEye,
        ]
        // Convenience flag so the calibration query doesn't have to
        // recompute the override in SQL — true iff the user diverged
        // from the recipe on either the preset key or the LUT id.
        if let recipeApplied {
            let presetDiverged = (recipeFilterPreset != nil) && (recipeFilterPreset != presetId)
            let lutDiverged = (recipeLutId ?? "") != (lutId ?? "")
            body["recipe_user_override"] = (!recipeApplied) && (presetDiverged || lutDiverged)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.addValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    /// P3-strong-3 — sample |pitchDelta| at the green-light edge so
    /// we can calibrate ``AlignmentMachine.Tolerances.pitchNear`` /
    /// ``pitchFar`` from real-user P90 instead of leaving them as
    /// hand-picked 8°/20° constants. Fire-and-forget telemetry.
    func recordAlignmentPitch(
        analyzeRequestId: String?,
        absDeltaDeg: Double,
        tier: String,
        targetPitchDeg: Double?,
    ) async {
        let url = baseURL.appendingPathComponent("/feedback/alignment_pitch")
        let body: [String: Any] = [
            "analyze_request_id": analyzeRequestId ?? NSNull(),
            "abs_delta_deg": absDeltaDeg,
            "tier": tier,
            "target_pitch_deg": targetPitchDeg ?? NSNull(),
        ]
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.addValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    /// P2-10.3 — record an AR navigation funnel event.
    /// `event` ∈ {attempted, arrived, shot_taken, abandoned}.
    func recordArNav(event: String, payload: [String: Any] = [:]) async {
        let url = baseURL.appendingPathComponent("/feedback/ar_nav")
        var body = payload
        body["event"] = event
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.addValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    /// P1-7.2 — silent positive signal: if the user kept a freshly-saved
    /// PHAsset for `delay` minutes (i.e. didn't delete it), treat that
    /// as an implicit ≥3.5-star vote so the time_optimal aggregator can
    /// learn even from quiet users.
    func recordSilentPositive(
        analyzeRequestId: String?,
        chosenPosition: ShotPosition?,
        sceneKind: String?,
        delayMinutes: Int = 10,
    ) async {
        try? await Task.sleep(nanoseconds: UInt64(delayMinutes) * 60 * 1_000_000_000)
        // Only fire if the user's most-recent photo (within the window) still exists.
        guard await fetchLatestAsset(within: delayMinutes + 1) != nil else { return }
        await submitRating(
            analyzeRequestId: analyzeRequestId,
            chosenPosition: chosenPosition,
            rating: 4,
            sceneKind: sceneKind,
        )
    }

    /// W2.3 — submit a rating + chosen ShotPosition. Lightweight feedback
    /// sent immediately after the user picks a star count, separate from
    /// the EXIF round-trip above. Returns the server's reported UGC
    /// action ("insert" | "merge" | "skipped" | "noop") on success.
    @discardableResult
    func submitRating(
        analyzeRequestId: String?,
        chosenPosition: ShotPosition?,
        rating: Int,
        sceneKind: String? = nil,
    ) async -> String? {
        var body: [String: Any] = [
            "analyze_request_id": analyzeRequestId ?? NSNull(),
            "rating": rating,
            "scene_kind": sceneKind ?? NSNull(),
        ]
        if let pos = chosenPosition,
           let data = try? JSONEncoder.snakeIso8601.encode(pos),
           let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            body["chosen_position"] = dict
            if pos.kind == .absolute {
                body["geo_lat"] = pos.lat ?? NSNull()
                body["geo_lon"] = pos.lon ?? NSNull()
            }
        }
        var req = URLRequest(url: endpoint)
        req.httpMethod = "POST"
        req.addValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return json["ugc_action"] as? String
            }
            return nil
        } catch {
            return nil
        }
    }

    /// Best-effort: returns true when feedback was actually posted.
    /// Never throws — feedback is fire-and-forget telemetry.
    func uploadLatestPhotoIfAny(
        analyzeRequestId: String?,
        styleKeywords: [String],
        recommendationSnapshot: [String: Any]?,
        arGuideTelemetry: [String: Any]? = nil,
        within minutes: Int = 5,
    ) async -> Bool {
        guard await ensurePermission() else { return false }
        guard let asset = await fetchLatestAsset(within: minutes) else { return false }
        guard let exif = await readExif(asset: asset) else { return false }

        var body: [String: Any] = [
            "analyze_request_id":   analyzeRequestId ?? NSNull(),
            "style_keywords":       styleKeywords,
            "captured_at_utc":      ISO8601DateFormatter().string(from: asset.creationDate ?? Date()),
            "focal_length_mm":      exif.focalLengthMm ?? NSNull(),
            "focal_length_35mm_eq": exif.focalLength35mmEq ?? NSNull(),
            "aperture":             exif.aperture ?? NSNull(),
            "exposure_time_s":      exif.exposureTimeS ?? NSNull(),
            "iso":                  exif.iso ?? NSNull(),
            "white_balance_k":      exif.whiteBalanceK ?? NSNull(),
        ]
        if let geo = asset.location {
            body["geo_lat"] = geo.coordinate.latitude
            body["geo_lon"] = geo.coordinate.longitude
        }
        if let rec = recommendationSnapshot {
            body["recommendation_snapshot"] = rec
        }
        if let tel = arGuideTelemetry {
            body["ar_guide_telemetry"] = tel
        }

        var req = URLRequest(url: endpoint)
        req.httpMethod = "POST"
        req.addValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            return (resp as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    // ----------------------------------------------------------------
    private func ensurePermission() async -> Bool {
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        if status == .authorized || status == .limited { return true }
        if status == .denied || status == .restricted { return false }
        return await withCheckedContinuation { c in
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { s in
                c.resume(returning: s == .authorized || s == .limited)
            }
        }
    }

    internal func fetchLatestAsset(within minutes: Int) async -> PHAsset? {
        let cutoff = Date().addingTimeInterval(-Double(minutes * 60))
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "mediaType == %d AND creationDate > %@",
                                     PHAssetMediaType.image.rawValue,
                                     cutoff as NSDate)
        opts.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        opts.fetchLimit = 1
        let result = PHAsset.fetchAssets(with: opts)
        return result.firstObject
    }

    private struct ExifFacts {
        var focalLengthMm: Double?
        var focalLength35mmEq: Double?
        var aperture: Double?
        var exposureTimeS: Double?
        var iso: Int?
        var whiteBalanceK: Int?
    }

    private func readExif(asset: PHAsset) async -> ExifFacts? {
        let opts = PHContentEditingInputRequestOptions()
        opts.isNetworkAccessAllowed = true
        return await withCheckedContinuation { c in
            asset.requestContentEditingInput(with: opts) { input, _ in
                guard let url = input?.fullSizeImageURL,
                      let src = CGImageSourceCreateWithURL(url as CFURL, nil),
                      let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any]
                else {
                    c.resume(returning: nil)
                    return
                }
                let exif = props[kCGImagePropertyExifDictionary] as? [CFString: Any] ?? [:]
                let tiff = props[kCGImagePropertyTIFFDictionary] as? [CFString: Any] ?? [:]
                _ = tiff   // reserved for future use (camera make/model)
                var f = ExifFacts()
                f.focalLengthMm     = exif[kCGImagePropertyExifFocalLength] as? Double
                f.focalLength35mmEq = exif[kCGImagePropertyExifFocalLenIn35mmFilm] as? Double
                f.aperture          = exif[kCGImagePropertyExifFNumber] as? Double
                f.exposureTimeS     = exif[kCGImagePropertyExifExposureTime] as? Double
                if let iso = (exif[kCGImagePropertyExifISOSpeedRatings] as? [Int])?.first {
                    f.iso = iso
                }
                // White balance is rarely in EXIF as Kelvin; iOS HEIC
                // sometimes exposes it under "{MakerApple}" → ColorTempK.
                // Best-effort: leave nil when absent.
                c.resume(returning: f)
            }
        }
    }
}
