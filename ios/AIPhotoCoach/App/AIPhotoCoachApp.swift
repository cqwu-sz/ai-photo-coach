import SwiftUI

@main
struct AIPhotoCoachApp: App {
    @StateObject private var router = AppRouter()

    init() {
        // v17b — start polling /api/config/endpoint so an admin-driven
        // server URL switch propagates to all clients without an app
        // update. Safe to start unconditionally; first probe is async.
        EndpointSyncService.shared.start()
        CatalogSync.shared.bootstrap()
        // v17c — attempt App Attest bootstrap once. No-op on simulator
        // / unsupported devices / network issues; backend currently
        // runs in shadow mode so failures don't block users.
        Task { @MainActor in
            _ = await AppAttestManager.shared.bootstrap()
        }
    }

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
