import SwiftUI

@main
struct MocapCamApp: App {
    @StateObject private var model = CaptureAppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onAppear {
                    model.start()
                }
                .onDisappear {
                    model.stop()
                }
        }
    }
}
