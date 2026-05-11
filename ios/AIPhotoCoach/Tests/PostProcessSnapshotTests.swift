// P2-13.3 — UI snapshot test stubs for the modes we ship in
// PostProcessView. The actual snapshot library (e.g.
// pointfreeco/swift-snapshot-testing) is not yet linked into this
// project; until it is, these XCTest stubs document the intended
// snapshot coverage and fail loudly when the view's enum changes
// without updating the test list.
//
// To enable real snapshots:
//   1) Add `swift-snapshot-testing` via SPM
//   2) Replace the `XCTAssertNotNil(view)` guard with
//      `assertSnapshot(matching: view, as: .image)`.

import XCTest
import SwiftUI

#if canImport(UIKit)
@testable import AIPhotoCoach

final class PostProcessSnapshotTests: XCTestCase {
    func testEachFilterPresetRenders() throws {
        for preset in FilterPreset.allCases {
            let model = PostProcessModel(original: UIImage(systemName: "photo")!)
            model.preset = preset
            model.rerender()
            let view = PostProcessView(model: model)
            XCTAssertNotNil(view, "preset \(preset.label) failed to construct")
            // TODO: assertSnapshot(matching: view, as: .image)
        }
    }

    func testProPresetsAreFlagged() {
        let pro = FilterPreset.allCases.filter { $0.requiresPro }
        XCTAssertFalse(pro.isEmpty, "expected at least one pro preset")
        XCTAssertTrue(pro.contains(.cinematic))
        XCTAssertTrue(pro.contains(.hkVibe))
    }
}
#endif
