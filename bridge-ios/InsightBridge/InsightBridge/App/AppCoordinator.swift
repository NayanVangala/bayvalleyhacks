import Foundation

// AppCoordinator
// Owns cold start logic. No UI.
// Cold start rule from the plan: never block launch on glasses.
// Configure the SDK, then try to attach glasses only if the user
// prefers glasses. Otherwise stay in phone mode and let the user
// attach manually from SessionView or PairingView.

final class AppCoordinator: ObservableObject {

    private let sessionManager: SessionManager
    private let registration: ExtentosRegistration
    private var hasStarted = false

    init(sessionManager: SessionManager, registration: ExtentosRegistration) {
        self.sessionManager = sessionManager
        self.registration = registration
    }

    func start() {
        guard !hasStarted else { return }
        hasStarted = true

        registration.configure()

        // preferGlasses defaults to true when the key has never been set.
        let defaults = UserDefaults.standard
        let preferGlasses: Bool
        if defaults.object(forKey: "onrecord.preferGlasses") == nil {
            preferGlasses = true
        } else {
            preferGlasses = defaults.bool(forKey: "onrecord.preferGlasses")
        }

        guard preferGlasses else {
            // Stay in phone mode. SessionManager starts in phone mode
            // by default, so there is nothing else to do here.
            return
        }

        // Fire and forget. If no device is available or attach fails,
        // SessionManager is responsible for staying in phone mode and
        // surfacing that state through its banner. Launch is not blocked.
        Task { [sessionManager] in
            await sessionManager.attachGlasses()
        }
    }
}
