import XCTest
import UIKit
@testable import AIPhotoCoach

final class KeyframeExtractorTests: XCTestCase {
    func testReturnsAllSamplesWhenFewerThanTarget() {
        let samples = makeSamples(azimuths: [0, 30, 60])
        let out = KeyframeExtractor().extract(from: samples, target: 10)
        XCTAssertEqual(out.count, 3)
    }

    func testEvenSpreadAcrossAzimuths() {
        let azimuths = stride(from: 0.0, through: 350.0, by: 5.0).map { $0 }
        let samples = makeSamples(azimuths: azimuths)
        let out = KeyframeExtractor().extract(from: samples, target: 10)
        XCTAssertGreaterThanOrEqual(out.count, 8)
        XCTAssertLessThanOrEqual(out.count, 10)

        let outAz = out.map { $0.azimuthDeg }
        let span = (outAz.max() ?? 0) - (outAz.min() ?? 0)
        XCTAssertGreaterThan(span, 200, "keyframes should span most of the panned arc")
    }

    func testFallsBackToTimeUniformWhenSpanIsTiny() {
        let azimuths: [Double] = stride(from: 0.0, through: 1.0, by: 0.05).map { $0 }
        let samples = makeSamples(azimuths: azimuths)
        let out = KeyframeExtractor().extract(from: samples, target: 8)
        XCTAssertEqual(out.count, 8)
    }

    private func makeSamples(azimuths: [Double]) -> [HeadedFrame] {
        let img = UIImage(systemName: "photo") ?? UIImage()
        return azimuths.enumerated().map { i, az in
            HeadedFrame(image: img,
                        azimuthDeg: az,
                        pitchDeg: 0,
                        rollDeg: 0,
                        timestampMs: i * 100)
        }
    }
}
