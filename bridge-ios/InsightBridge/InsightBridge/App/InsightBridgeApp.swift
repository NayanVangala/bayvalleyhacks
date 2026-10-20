import SwiftUI

// InsightBridgeApp
// Entry point for the OnRecord bridge app.
// Builds the full object graph once and injects it via environment.
// No Meta SDK imports in this file. SDK access stays behind
// DATRegistration and GlassesSource.

@main
struct InsightBridgeApp: App {

    @StateObject private var registration: DATRegistration
    @StateObject private var sessionManager: SessionManager
    @StateObject private var coordinator: AppCoordinator

    init() {
        // Read persisted settings. These keys are written by SettingsView
        // and read once at construction time.
        // A restart applies changed server host and port values.
        let defaults = UserDefaults.standard

        // Fall back to the configured server (Config reads the
        // SERVER_HOST build setting, default 192.168.2.1 on the demo
        // network). A 127.0.0.1 fallback here pointed a fresh install
        // at the phone itself, so the WebSocket could never reach the
        // laptop while enrollment (which uses Config) worked.
        let host = defaults.string(forKey: "onrecord.serverHost") ?? Config.serverHost
        let storedPort = defaults.integer(forKey: "onrecord.serverPort")
        let port = storedPort > 0 ? storedPort : Config.serverPort
        let useMockSource = defaults.bool(forKey: "onrecord.useMockSource")

        let registration = DATRegistration()

        let glassesSource = GlassesSource()
        let phoneSource = PhoneSource()
        // Mock source is only created for drills when the toggle is on.
        // When the toggle is off this stays nil and no mock is wired in.
        let mockSource: MockSource? = useMockSource ? MockSource() : nil

        let wsClient = WSClient(host: host, port: port)
        let pttController = PTTController()

        let sessionManager = SessionManager(
            glassesSource: glassesSource,
            phoneSource: phoneSource,
            mockSource: mockSource,
            wsClient: wsClient,
            pttController: pttController
        )

        let coordinator = AppCoordinator(
            sessionManager: sessionManager,
            registration: registration
        )

        _registration = StateObject(wrappedValue: registration)
        _sessionManager = StateObject(wrappedValue: sessionManager)
        _coordinator = StateObject(wrappedValue: coordinator)
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(sessionManager)
                .environmentObject(registration)
                .environmentObject(coordinator)
                .onAppear {
                    coordinator.start()
                }
                .onOpenURL { url in
                    _ = registration.handleOpenURL(url)
                }
        }
    }
}
