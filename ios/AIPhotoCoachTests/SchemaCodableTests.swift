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
}
