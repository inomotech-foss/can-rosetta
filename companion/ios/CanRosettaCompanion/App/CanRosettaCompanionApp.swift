import SwiftUI

/// Shared app-wide constants (logging subsystem, bundle identity).
enum AppInfo {
    static let subsystem = "com.inomotech.canrosetta.companion"
    static let displayName = "CAN-Rosetta Companion"
}

@main
struct CanRosettaCompanionApp: App {
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
