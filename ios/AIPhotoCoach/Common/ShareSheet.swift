// ShareSheet.swift  (v18b)
//
// Tiny UIActivityViewController bridge used wherever we want to surface
// the system share sheet from SwiftUI. Lifted out of
// PrivacyDisclosureView so cross-feature callers (e.g. AdminInsightsView
// CSV export) don't depend on a private nested type — that coupling
// caused the "cannot find 'ShareSheet' in scope" build break in CI run
// 25776688634.

import SwiftUI
import UIKit

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController,
                                 context: Context) {}
}
