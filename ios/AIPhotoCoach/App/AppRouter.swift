import Foundation
import SwiftUI

enum AppDestination: Hashable {
    case capture(personCount: Int, qualityMode: QualityMode, sceneMode: SceneMode, styleKeywords: [String])
    case results(AnalyzeResponse)
    case referenceLibrary
    case arGuide(shot: ShotRecommendation, avatarStyleId: String)
    /// Real shoot screen — opens AVCaptureSession, applies the AI plan
    /// to AVCaptureDevice, shows alignment HUD + shutter.
    /// `usageRecordId` is the backend pk of the analyze that produced
    /// this shot; passed so the screen can mark it captured + collect
    /// satisfaction signal. nil tolerated for pre-v18 server compat.
    case shoot(shot: ShotRecommendation, usageRecordId: String? = nil)
}

@MainActor
final class AppRouter: ObservableObject {
    @Published var path = NavigationPath()

    func push(_ destination: AppDestination) {
        path.append(destination)
    }

    func popToRoot() {
        path = NavigationPath()
    }

    func pop() {
        if !path.isEmpty {
            path.removeLast()
        }
    }
}
