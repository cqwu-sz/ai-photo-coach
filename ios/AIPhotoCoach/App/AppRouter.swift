import Foundation
import SwiftUI

enum AppDestination: Hashable {
    case capture(personCount: Int, qualityMode: QualityMode, sceneMode: SceneMode, styleKeywords: [String])
    case results(AnalyzeResponse)
    case referenceLibrary
    case arGuide(shot: ShotRecommendation, avatarStyleId: String)
    /// Real shoot screen — opens AVCaptureSession, applies the AI plan
    /// to AVCaptureDevice, shows alignment HUD + shutter.
    case shoot(shot: ShotRecommendation)
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
