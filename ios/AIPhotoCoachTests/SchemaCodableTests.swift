import XCTest
@testable import AIPhotoCoach

final class SchemaCodableTests: XCTestCase {
    func testCanDecodeSampleResponse() throws {
        let json = """
        {
          "scene": {
            "type": "outdoor_park",
            "lighting": "golden_hour",
            "background_summary": "Open lawn",
            "cautions": []
          },
          "shots": [{
            "id": "shot_1",
            "title": "test",
            "angle": {"azimuth_deg": 90, "pitch_deg": 0, "distance_m": 2.0},
            "composition": {"primary": "rule_of_thirds", "secondary": [], "notes": null},
            "camera": {
              "focal_length_mm": 50,
              "aperture": "f/2.0",
              "shutter": "1/250",
              "iso": 200,
              "white_balance_k": 5500,
              "ev_compensation": -0.3,
              "rationale": "test",
              "device_hints": null
            },
            "poses": [{
              "person_count": 1,
              "layout": "single",
              "persons": [{"role": "person_a", "stance": "standing", "upper_body": null, "hands": null, "gaze": null, "expression": null, "position_hint": null}],
              "interaction": null,
              "reference_thumbnail_id": "pose_single_relaxed_001",
              "difficulty": "easy"
            }],
            "rationale": "test",
            "confidence": 0.8
          }],
          "generated_at": "2026-05-05T15:00:00Z",
          "model": "mock-1"
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        XCTAssertEqual(parsed.shots.count, 1)
        XCTAssertEqual(parsed.shots[0].camera.focalLengthMm, 50)
        XCTAssertEqual(parsed.shots[0].poses[0].layout, .single)
        XCTAssertEqual(parsed.scene.lighting, .goldenHour)
    }

    func testCaptureMetaEncodesSnakeCase() throws {
        let meta = CaptureMeta(
            personCount: 2,
            qualityMode: .fast,
            styleKeywords: ["clean"],
            frameMeta: [FrameMeta(index: 0, azimuthDeg: 0, pitchDeg: 0, rollDeg: 0, timestampMs: 0, ambientLux: nil)]
        )
        let data = try JSONEncoder().encode(meta)
        let str = String(data: data, encoding: .utf8) ?? ""
        XCTAssertTrue(str.contains("person_count"))
        XCTAssertTrue(str.contains("frame_meta"))
        XCTAssertTrue(str.contains("style_keywords"))
    }

    /// Phase 1 — make sure the new criteria_score / criteria_notes /
    /// strongest_axis / weakest_axis fields round-trip through Codable so
    /// the result UI can read them straight from AnalyzeResponse.
    func testCriteriaScoreDecodes() throws {
        let json = """
        {
          "scene": {"type":"x","lighting":"golden_hour","background_summary":"x","cautions":[]},
          "shots":[{
            "id":"a","title":"t",
            "angle":{"azimuth_deg":10,"pitch_deg":0,"distance_m":2},
            "composition":{"primary":"rule_of_thirds","secondary":[],"notes":null},
            "camera":{"focal_length_mm":50,"aperture":"f/2","shutter":"1/250","iso":200,"white_balance_k":5500,"ev_compensation":0,"rationale":"x","device_hints":null},
            "poses":[],
            "rationale":"x",
            "confidence":0.8,
            "criteria_score":{"composition":5,"light":4,"color":3,"depth":4},
            "criteria_notes":{"composition":"a","light":"b","color":"c","depth":"d"},
            "strongest_axis":"composition",
            "weakest_axis":"color"
          }],
          "generated_at":"2026-05-05T15:00:00Z",
          "model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let shot = parsed.shots[0]
        XCTAssertEqual(shot.criteriaScore?.composition, 5)
        XCTAssertEqual(shot.criteriaScore?.light, 4)
        XCTAssertEqual(shot.criteriaNotes?.composition, "a")
        XCTAssertEqual(shot.strongestAxis, "composition")
        XCTAssertEqual(shot.weakestAxis, "color")
    }

    /// Phase 2 — environment snapshot + sun info round-trip.
    func testEnvironmentSnapshotDecodes() throws {
        let json = """
        {
          "scene": {"type":"x","lighting":"golden_hour","background_summary":"x","cautions":[]},
          "shots":[],
          "environment":{
            "sun":{
              "azimuth_deg":245.3,"altitude_deg":7.2,
              "phase":"golden_hour_dusk","color_temp_k_estimate":3200,
              "minutes_to_golden_end":23,"minutes_to_blue_end":null,
              "minutes_to_sunset":23,"minutes_to_sunrise":null
            },
            "timestamp":"2026-05-05T19:30:00Z"
          },
          "generated_at":"2026-05-05T15:00:00Z",
          "model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let sun = parsed.environment?.sun
        XCTAssertEqual(sun?.phase, "golden_hour_dusk")
        XCTAssertEqual(sun?.colorTempKEstimate, 3200)
        XCTAssertEqual(sun?.minutesToGoldenEnd, 23)
        XCTAssertNil(sun?.minutesToBlueEnd)
        XCTAssertEqual(sun?.isTimeTight, true)
    }

    /// Phase 2 — geo fix encodes with snake_case so backend can read it.
    func testGeoFixEncodesSnakeCase() throws {
        let meta = CaptureMeta(
            personCount: 1, qualityMode: .fast, sceneMode: .lightShadow,
            styleKeywords: [],
            frameMeta: [FrameMeta(index: 0, azimuthDeg: 0, pitchDeg: 0,
                                  rollDeg: 0, timestampMs: 0, ambientLux: nil)],
            geo: GeoFix(lat: 40.0, lon: 116.0, accuracyM: 12, timestamp: nil)
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let str = String(data: try encoder.encode(meta), encoding: .utf8) ?? ""
        XCTAssertTrue(str.contains("\"geo\""))
        XCTAssertTrue(str.contains("\"accuracy_m\""))
        XCTAssertTrue(str.contains("light_shadow"))
    }

    /// Phase 4 (Open-Meteo + vision_light + recapture) — ensure all the
    /// new optional fields round-trip cleanly so the iOS UI can read them
    /// straight off AnalyzeResponse.
    func testWeatherAndVisionLightAndRecaptureDecode() throws {
        let json = """
        {
          "scene": {
            "type":"x","lighting":"overcast","background_summary":"x","cautions":[],
            "vision_light":{"direction_deg":248.0,"quality":"hard","confidence":0.7,"notes":"hi"}
          },
          "shots":[],
          "environment":{
            "sun":{
              "azimuth_deg":245.0,"altitude_deg":12.0,"phase":"golden_hour_dusk",
              "color_temp_k_estimate":3200,"minutes_to_golden_end":15,
              "minutes_to_blue_end":null,"minutes_to_sunset":15,"minutes_to_sunrise":null
            },
            "weather":{
              "cloud_cover_pct":85,"visibility_m":9000,"uv_index":3.2,
              "temperature_c":16.0,"weather_code":3,"softness":"soft",
              "code_label_zh":"阴"
            },
            "vision_light":{"direction_deg":248.0,"quality":"hard","confidence":0.7,"notes":"hi"},
            "timestamp":"2026-05-05T19:30:00Z"
          },
          "light_recapture_hint":{
            "enabled":true,
            "title":"光线证据不足",
            "detail":"对着最亮的方向慢转 10 秒",
            "suggested_azimuth_deg":248.0
          },
          "generated_at":"2026-05-05T15:00:00Z",
          "model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let env = parsed.environment
        XCTAssertEqual(env?.weather?.softness, "soft")
        XCTAssertEqual(env?.weather?.cloudCoverPct, 85)
        XCTAssertEqual(env?.weather?.codeLabelZh, "阴")
        XCTAssertEqual(env?.weather?.softnessLabelZh, "软光")
        XCTAssertEqual(env?.visionLight?.directionDeg, 248.0)
        XCTAssertEqual(env?.visionLight?.qualityZh, "硬光")
        XCTAssertEqual(parsed.scene.visionLight?.confidence, 0.7)
        XCTAssertEqual(parsed.lightRecaptureHint?.enabled, true)
        XCTAssertEqual(parsed.lightRecaptureHint?.suggestedAzimuthDeg, 248.0)
    }

    /// New v6 fields: each shot has a populated ``iphone_apply_plan``
    /// + ``iphone_tips`` array. iOS must decode them cleanly so the
    /// shoot screen can apply the plan to AVCaptureDevice.
    func testIphoneApplyPlanAndTipsDecode() throws {
        let json = """
        {
          "scene":{"type":"x","lighting":"shade","background_summary":"x","cautions":[]},
          "shots":[{
            "id":"shot_1",
            "title":"主机位",
            "angle":{"azimuth_deg":110,"pitch_deg":-5,"distance_m":2.2},
            "composition":{"primary":"rule_of_thirds","secondary":[],"notes":null},
            "camera":{
              "focal_length_mm":50,"aperture":"f/2.0","shutter":"1/250","iso":200,
              "white_balance_k":5500,"ev_compensation":-0.3,
              "rationale":"x","device_hints":null,
              "iphone_apply_plan":{
                "zoom_factor":1.92,"iso":200,"shutter_seconds":0.004,
                "ev_compensation":-0.3,"white_balance_k":5500,
                "aperture_note":"iPhone 物理光圈 f/1.78","can_apply":true
              }
            },
            "poses":[],"rationale":"x","confidence":0.8,
            "iphone_tips":[
              "切到 2x 长焦端拍 50mm 等效",
              "iPhone 物理光圈 f/1.78 已是最大",
              "长按主体锁定 AE/AF 后下滑 -0.3 EV"
            ]
          }],
          "generated_at":"2026-05-05T15:00:00Z","model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let shot = try XCTUnwrap(parsed.shots.first)
        let plan = try XCTUnwrap(shot.camera.iphoneApplyPlan)
        XCTAssertEqual(plan.zoomFactor, 1.92, accuracy: 0.01)
        XCTAssertEqual(plan.iso, 200)
        XCTAssertEqual(plan.shutterSeconds, 0.004, accuracy: 1e-6)
        XCTAssertEqual(plan.shutterDisplay, "1/250")
        XCTAssertEqual(plan.equivalentFocalMm, 50)
        XCTAssertEqual(plan.evCompensation, -0.3, accuracy: 1e-6)
        XCTAssertEqual(plan.whiteBalanceK, 5500)
        XCTAssertTrue(plan.canApply)
        XCTAssertTrue(plan.apertureNote.contains("f/1.78"))
        XCTAssertEqual(shot.iphoneTips.count, 3)
        XCTAssertEqual(shot.iphoneTips[0], "切到 2x 长焦端拍 50mm 等效")
    }

    /// Older cached responses without ``iphone_apply_plan`` /
    /// ``iphone_tips`` must still decode (the iOS app should not crash
    /// when offline-loading a v5 response).
    func testLegacyResponseWithoutIphoneFieldsDecodes() throws {
        let json = """
        {
          "scene":{"type":"x","lighting":"shade","background_summary":"x","cautions":[]},
          "shots":[{
            "id":"shot_1",
            "angle":{"azimuth_deg":0,"pitch_deg":0,"distance_m":2},
            "composition":{"primary":"rule_of_thirds","secondary":[],"notes":null},
            "camera":{"focal_length_mm":35,"aperture":"f/2.8","shutter":"1/200","iso":200,
                      "white_balance_k":5200,"ev_compensation":0,"rationale":"x","device_hints":null},
            "poses":[],"rationale":"x","confidence":0.7
          }],
          "generated_at":"2026-05-05T15:00:00Z","model":"old-model"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let shot = try XCTUnwrap(parsed.shots.first)
        XCTAssertNil(shot.camera.iphoneApplyPlan)
        XCTAssertEqual(shot.iphoneTips, [])
    }

    /// Vision-only environment (no sun, no weather) must still render —
    /// matches the result UI's "dashed light indicator" fallback path.
    func testVisionOnlyEnvironmentDecodes() throws {
        let json = """
        {
          "scene":{"type":"x","lighting":"shade","background_summary":"x","cautions":[]},
          "shots":[],
          "environment":{
            "vision_light":{"direction_deg":120,"quality":"soft","confidence":0.45,"notes":"only video"}
          },
          "generated_at":"2026-05-05T15:00:00Z",
          "model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        XCTAssertNil(parsed.environment?.sun)
        XCTAssertNil(parsed.environment?.weather)
        XCTAssertEqual(parsed.environment?.visionLight?.directionDeg, 120)
        XCTAssertEqual(parsed.environment?.visionLight?.qualityZh, "软光")
        XCTAssertEqual(parsed.environment?.visionLight?.confidencePct, 45)
    }

    // ─────────────────────────────────────────────────────────────────
    // v6 — CaptureQuality / 7-axis CriteriaScore / overallScore /
    // FrameMeta visual signals.
    // ─────────────────────────────────────────────────────────────────

    /// Phase 1 — capture_quality on the scene must round-trip so the
    /// CaptureAdvisoryBanner has data to render.
    func testCaptureQualityDecodes() throws {
        let json = """
        {
          "scene": {
            "type":"x","lighting":"shade","background_summary":"x","cautions":[],
            "capture_quality":{
              "score":2,
              "issues":["cluttered_bg","too_dark","narrow_pan"],
              "summary_zh":"画面较暗且背景杂乱",
              "should_retake":true
            }
          },
          "shots":[],
          "generated_at":"2026-05-05T15:00:00Z","model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let cq = try XCTUnwrap(parsed.scene.captureQuality)
        XCTAssertEqual(cq.score, 2)
        XCTAssertTrue(cq.shouldRetake)
        XCTAssertTrue(cq.isCritical)
        XCTAssertEqual(cq.issues.count, 3)
        XCTAssertEqual(cq.issues[0], .clutteredBg)
        XCTAssertEqual(cq.issues[1], .tooDark)
        XCTAssertEqual(cq.issues[2], .narrowPan)
        XCTAssertEqual(cq.issues[0].labelZh, "背景太杂")
        XCTAssertEqual(cq.summaryZh, "画面较暗且背景杂乱")
    }

    /// Phase 1 — old responses without capture_quality still decode.
    func testCaptureQualityIsOptional() throws {
        let json = """
        {
          "scene":{"type":"x","lighting":"shade","background_summary":"x","cautions":[]},
          "shots":[],
          "generated_at":"2026-05-05T15:00:00Z","model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        XCTAssertNil(parsed.scene.captureQuality)
    }

    /// v6 — CriteriaScore now has 7 axes. Decode and verify the new
    /// ones (subject_fit / background / theme) come through.
    func testSevenAxisCriteriaScoreDecodes() throws {
        let json = """
        {
          "scene":{"type":"x","lighting":"shade","background_summary":"x","cautions":[]},
          "shots":[{
            "id":"a","angle":{"azimuth_deg":0,"pitch_deg":0,"distance_m":2},
            "composition":{"primary":"rule_of_thirds","secondary":[],"notes":null},
            "camera":{"focal_length_mm":35,"aperture":"f/2.8","shutter":"1/200","iso":200,
                      "white_balance_k":5500,"ev_compensation":0,"rationale":"x","device_hints":null},
            "poses":[],"rationale":"x","confidence":0.8,
            "criteria_score":{
              "composition":5,"light":4,"color":4,"depth":3,
              "subject_fit":5,"background":4,"theme":5
            },
            "criteria_notes":{
              "composition":"[comp_rule_of_thirds] 三分线交点",
              "subject_fit":"[sub_subject_size] 主体占比合适",
              "theme":"[theme_one_idea] 一句话主题: 咖啡店午后"
            },
            "strongest_axis":"theme","weakest_axis":"depth",
            "overall_score":4.21
          }],
          "generated_at":"2026-05-05T15:00:00Z","model":"mock-1"
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let parsed = try decoder.decode(AnalyzeResponse.self, from: json)
        let shot = try XCTUnwrap(parsed.shots.first)
        let score = try XCTUnwrap(shot.criteriaScore)
        XCTAssertEqual(score.composition, 5)
        XCTAssertEqual(score.subjectFit, 5)
        XCTAssertEqual(score.background, 4)
        XCTAssertEqual(score.theme, 5)
        // asArray returns the 7 axes in display order
        XCTAssertEqual(score.asArray.count, 7)
        XCTAssertEqual(score.asArray.first?.key, "composition")
        XCTAssertEqual(score.asArray.last?.key, "depth")
        // overall_score round-trips
        XCTAssertEqual(shot.overallScore, 4.21, accuracy: 0.001)
        // criteria_notes can address the new axes
        XCTAssertEqual(
            shot.criteriaNotes?.note(for: "subject_fit"),
            "[sub_subject_size] 主体占比合适"
        )
    }

    /// Backward compat — old 4-axis criteria_score must still decode,
    /// with subject_fit/background/theme defaulting to neutral 3.
    func testFourAxisCriteriaScoreDecodesWithDefaults() throws {
        let json = """
        {"composition":4,"light":5,"color":4,"depth":3}
        """.data(using: .utf8)!
        let parsed = try JSONDecoder().decode(CriteriaScore.self, from: json)
        XCTAssertEqual(parsed.composition, 4)
        XCTAssertEqual(parsed.subjectFit, 3, "default neutral when absent")
        XCTAssertEqual(parsed.background, 3)
        XCTAssertEqual(parsed.theme, 3)
        XCTAssertEqual(parsed.asArray.count, 7)
    }

    /// FrameMeta now carries client-side visual signals — make sure
    /// they encode as snake_case so backend can read them.
    func testFrameMetaVisualSignalsEncodeSnakeCase() throws {
        let meta = FrameMeta(
            index: 3, azimuthDeg: 120, pitchDeg: -2, rollDeg: 0.5,
            timestampMs: 1500, ambientLux: nil,
            blurScore: 9.21, meanLuma: 0.345, faceHit: true
        )
        let data = try JSONEncoder().encode(meta)
        let str = String(data: data, encoding: .utf8) ?? ""
        XCTAssertTrue(str.contains("\"blur_score\""))
        XCTAssertTrue(str.contains("\"mean_luma\""))
        XCTAssertTrue(str.contains("\"face_hit\""))
        // Back-decode and verify values are preserved.
        let back = try JSONDecoder().decode(FrameMeta.self, from: data)
        XCTAssertEqual(back.blurScore, 9.21, accuracy: 0.001)
        XCTAssertEqual(back.meanLuma, 0.345, accuracy: 0.001)
        XCTAssertEqual(back.faceHit, true)
    }

    // MARK: - v7 Phase B — Avatar manifest

    /// AvatarManifestPayload should round-trip the full v7 manifest
    /// shape (presets + pose_to_mixamo nested sections).
    func testAvatarManifestDecodes() throws {
        let json = """
        {
          "version": "v7",
          "presets": [
            {
              "id": "male_casual_25",
              "name_zh": "休闲男 · 25",
              "gender": "male", "age": 25,
              "style": "casual", "tags": ["street"],
              "glb": "/web/avatars/preset/male_casual_25.glb",
              "usdz": "Avatars/male_casual_25.usdz",
              "thumbnail": "/web/avatars/preset/male_casual_25.png"
            }
          ],
          "pose_to_mixamo": {
            "single": {"pose_single_relaxed_001": "idle_relaxed"},
            "two_person": {"pose_two_high_low_001": "couple_high_low"},
            "three_person": {},
            "four_person": {},
            "fallback_by_count": {"1": "idle_relaxed", "2": "couple_side_by_side", "3": "group_triangle_pose", "4": "group_diamond_pose"}
          }
        }
        """.data(using: .utf8)!
        let parsed = try JSONDecoder().decode(AvatarManifestPayload.self, from: json)
        XCTAssertEqual(parsed.presets.count, 1)
        XCTAssertEqual(parsed.presets.first?.nameZh, "休闲男 · 25")
        XCTAssertEqual(parsed.poseToMixamo.flatPoseMap["pose_single_relaxed_001"], "idle_relaxed")
        XCTAssertEqual(parsed.poseToMixamo.fallbackByCount["1"], "idle_relaxed")
    }

    /// resolve(poseId:personCount:) must direct-match when the pose id
    /// is in the map, and fall back by count when it isn't.
    func testAvatarAnimationManifestResolveFallsBackByCount() throws {
        let manifest = AvatarAnimationManifest(
            single: ["pose_single_relaxed_001": "idle_relaxed"],
            twoPerson: ["pose_two_high_low_001": "couple_high_low"],
            threePerson: [:],
            fourPerson: [:],
            fallbackByCount: [
                "1": "idle_relaxed",
                "2": "couple_side_by_side",
                "3": "group_triangle_pose",
                "4": "group_diamond_pose",
            ],
        )
        XCTAssertEqual(
            manifest.resolve(poseId: "pose_single_relaxed_001", personCount: 1),
            "idle_relaxed",
        )
        XCTAssertEqual(
            manifest.resolve(poseId: "pose_two_high_low_001", personCount: 2),
            "couple_high_low",
        )
        // Unknown id falls back to count slot
        XCTAssertEqual(
            manifest.resolve(poseId: "pose_does_not_exist_999", personCount: 3),
            "group_triangle_pose",
        )
        XCTAssertEqual(
            manifest.resolve(poseId: nil, personCount: 4),
            "group_diamond_pose",
        )
    }

    /// AvatarPicker.pick should respect the persisted picks list first,
    /// then rotate through the default preset order so couples don't
    /// pick the same avatar twice.
    func testAvatarPickerHonorsPersistedAndRotation() {
        let presets: [AvatarPresetEntry] = [
            stubPreset("male_casual_25"),
            stubPreset("female_casual_22"),
            stubPreset("female_elegant_30"),
        ]
        // Persisted wins
        XCTAssertEqual(
            AvatarPicker.pick(personIndex: 0, from: presets, persisted: ["male_casual_25"]),
            "male_casual_25",
        )
        // Empty persisted -> rotation order. female_casual_22 is first
        // in the default rotation when present.
        XCTAssertEqual(
            AvatarPicker.pick(personIndex: 0, from: presets, persisted: []),
            "female_casual_22",
        )
        // Second person rotates to next preset (alternates gender to
        // avoid identical-twin couple shots).
        XCTAssertEqual(
            AvatarPicker.pick(personIndex: 1, from: presets, persisted: []),
            "male_casual_25",
        )
    }

    private func stubPreset(_ id: String) -> AvatarPresetEntry {
        AvatarPresetEntry(
            id: id, nameZh: id, gender: "male", age: 25,
            style: "test", tags: [],
            glb: "/x.glb", usdz: "X.usdz", thumbnail: "/x.png",
        )
    }
}
