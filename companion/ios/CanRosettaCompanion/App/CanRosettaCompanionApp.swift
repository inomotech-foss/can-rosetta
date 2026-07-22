import SwiftUI

/// Shared app-wide constants (logging subsystem, bundle identity).
enum AppInfo {
    static let subsystem = "com.inomotech.canrosetta.companion"
    static let displayName = "CAN-Rosetta Companion"
}

@main
struct CanRosettaCompanionApp: App {
    // The CarPlay scene is wired to `CarPlaySceneDelegate` via the Info.plist
    // scene manifest (see project.yml). SwiftUI keeps managing the phone
    // `WindowGroup` scene — declaring only the CarPlay role in the manifest
    // leaves the window role to SwiftUI.
    @StateObject private var controller = RecordingController()
    @StateObject private var connection = EdgeConnection()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(controller)
                .environmentObject(connection)
                .onAppear { controller.requestPermissions() }
        }
    }
}
