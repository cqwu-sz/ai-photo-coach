import SwiftUI

@main
struct AIPhotoCoachApp: App {
    @StateObject private var router = AppRouter()

    /// Set to `true` once the user dismisses the splash. Mirrors the
    /// `aphc.welcomeSeen` localStorage flag used by the web PWA.
    @AppStorage("aphc.welcomeSeen") private var welcomeSeen: Bool = false

    var body: some Scene {
        WindowGroup {
            Group {
                if welcomeSeen {
                    RootView()
                        .environmentObject(router)
                        .transition(.opacity)
                } else {
                    WelcomeView(onContinue: {
                        withAnimation(.easeInOut(duration: 0.45)) {
                            welcomeSeen = true
                        }
                    })
                    .transition(.opacity)
                }
            }
            .preferredColorScheme(.dark)
        }
    }
}
