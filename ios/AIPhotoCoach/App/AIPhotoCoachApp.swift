import SwiftUI

@main
struct AIPhotoCoachApp: App {
    @StateObject private var router = AppRouter()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(router)
        }
    }
}
