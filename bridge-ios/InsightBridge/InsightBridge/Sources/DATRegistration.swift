// DATRegistration.swift
// InsightBridge
//
// Owns Meta Wearables DAT registration for the OnRecord bridge app.
// This file and GlassesSource.swift are the ONLY files in the app
// allowed to import the Meta Wearables DAT SDK. Everything else in
// the app talks to the SDK-free types in Common/MediaTypes.swift and
// Common/CaptureSource.swift, so the rest of the codebase can be
// built and tested with no SDK present.
//
// SDK version decision:
//   Findr pinned the DAT iOS SDK at 0.7.0. This project does NOT use
//   that pin. OnRecord targets toolkit 1.0, pinned at >= 1.0.1,
//   because 1.0.0 froze camera frames in the stream session.
//   Dev mode uses MetaAppID = 0 in Info.plist (the MWDAT dict), which
//   skips production app registration on the setup day device.
//
// Verify against pinned DAT 1.0.x headers on the Mac; API names
// follow the 1.0 documented patterns.

import Combine
import Foundation
import MWDATCore
import os.log

/// Registration and device selection for the paired Ray-Ban glasses.
///
/// Lifecycle:
///   1. App launch calls `configure()` exactly once.
///   2. The app scene forwards every incoming URL to `handleOpenURL(_:)`
///      so the Meta AI universal link callback can complete.
///   3. Before a conversation starts, the UI calls
///      `discoverAndSelectDevice()` and then logs `checkDevMode()`.
///      The setup day checklist requires isDevMode == true in the log
///      before any stream session is started.
final class DATRegistration: NSObject, ObservableObject {

    /// Coarse registration state surfaced to SwiftUI.
    /// `ready` means the SDK is configured and, in dev mode, usable.
    /// It does not by itself mean a device is selected; check
    /// `selectedDeviceID` for that.
    enum RegistrationState: Equatable {
        case unconfigured
        case ready
        case registering
        case failed(String)
    }

    // MARK: - Published state

    @Published private(set) var registrationState: RegistrationState = .unconfigured

    /// Identifier of the Ray-Ban selected by discovery, if any.
    /// GlassesSource is created with this id. Nil means no device has
    /// been selected yet and the app should stay in phone mode.
    @Published private(set) var selectedDeviceID: String?

    /// Human readable device name for the status banner, if the SDK
    /// reports one. Display only, never used as an identifier.
    @Published private(set) var selectedDeviceName: String?

    /// Last dev mode value read from the SDK or device state.
    /// Nil until `checkDevMode()` or `refreshDevMode()` has run.
    @Published private(set) var lastKnownDevMode: Bool?

    // MARK: - Private state

    /// Guard flag so `Wearables.configure()` runs exactly once per
    /// process. Calling configure twice is a no-op by design; the SDK
    /// does not document repeated configure as safe.
    private var didConfigure = false

    /// Kept alive for the lifetime of an in-flight discovery. The
    /// selector pattern in toolkit 1.0 vends devices asynchronously
    /// and cancelling it early loses the selection.
    /// Verify against pinned DAT 1.0.x headers on the Mac; API names
    /// follow the 1.0 documented patterns.
    private var autoDeviceSelector: AutoDeviceSelector?

    private let logger = Logger(subsystem: "com.nayan.insightbridge", category: "DATRegistration")

    override init() {
        super.init()
    }

    // MARK: - Configure

    /// One time SDK configuration. Call from app init or the first
    /// scene appearance. Safe to call repeatedly; only the first call
    /// reaches the SDK.
    func configure() {
        guard !didConfigure else {
            logger.info("DATRegistration.configure called again, ignoring. SDK already configured.")
            return
        }
        didConfigure = true

        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns.
        Wearables.configure()

        if registrationState == .unconfigured {
            registrationState = .ready
        }
        let devMode = checkDevMode()
        logger.info("DAT configured. isDevMode=\(devMode, privacy: .public). MetaAppID 0 in Info.plist means dev mode registration.")
    }

    // MARK: - Meta AI callback

    /// Forwards a URL to the SDK. Wire this from the SwiftUI
    /// `onOpenURL` modifier and from the app delegate URL handler.
    /// Returns true when the SDK consumed the URL, which is the case
    /// for the Meta AI registration universal link callback.
    @discardableResult
    func handleOpenURL(_ url: URL) -> Bool {
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns.
        let handled = Wearables.shared.handleUrl(url)
        if handled {
            if registrationState == .registering || registrationState == .unconfigured {
                registrationState = .ready
            }
            logger.info("Wearables.handleUrl consumed the callback URL. Registration state is ready.")
        } else {
            logger.info("Wearables.handleUrl did not consume the URL. Leaving registration state unchanged.")
        }
        return handled
    }

    /// Marks registration as in flight. Call right before handing the
    /// user over to the Meta AI app, so a returning universal link is
    /// interpreted by `handleOpenURL(_:)` as a completed callback.
    func beginRegistration() {
        if !didConfigure {
            configure()
        }
        registrationState = .registering
        logger.info("DAT registration marked in flight. Waiting for the Meta AI callback URL.")
    }

    // MARK: - Device discovery

    /// Finds the paired Ray-Ban and stores its identifier.
    ///
    /// Uses the toolkit device discovery pattern built around
    /// AutoDeviceSelector: the selector watches for the paired device
    /// associated with this phone's Meta AI app and vends the first
    /// available match. There is exactly one demo device, so first
    /// match wins and no chooser UI is needed.
    ///
    /// Returns the selected device id, or nil on failure. Failures are
    /// also published through `registrationState` as `.failed` so the
    /// UI can explain phone mode instead of spinning forever.
    @discardableResult
    func discoverAndSelectDevice() async -> String? {
        if !didConfigure {
            configure()
        }
        _ = checkDevMode()

        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. In particular the
        // selector initializer, the async selection call, and the
        // device identifier property name need a header check.
        let selector = AutoDeviceSelector()
        autoDeviceSelector = selector
        defer { autoDeviceSelector = nil }

        do {
            let device = try await selector.selectDevice()
            let deviceID = device.identifier
            selectedDeviceID = deviceID
            selectedDeviceName = device.name
            if registrationState == .unconfigured || registrationState == .registering {
                registrationState = .ready
            }
            logger.info("AutoDeviceSelector selected device id=\(deviceID, privacy: .public) name=\(device.name ?? "unknown", privacy: .public).")
            return deviceID
        } catch {
            let message = "Device discovery failed: \(error.localizedDescription)"
            registrationState = .failed(message)
            logger.error("\(message, privacy: .public)")
            return nil
        }
    }

    /// Manual selection override for drills and for the setup day
    /// fallback where the device id is copied from the Meta AI app.
    /// Discovery remains the normal path; this only stores the id.
    func selectDevice(id: String, name: String? = nil) {
        selectedDeviceID = id
        selectedDeviceName = name
        if registrationState == .unconfigured {
            registrationState = .ready
        }
        logger.info("Device selected manually. id=\(id, privacy: .public).")
    }

    /// Clears the current selection, for example after an unpair.
    /// Does not unregister the SDK and does not touch any session.
    func clearSelectedDevice() {
        selectedDeviceID = nil
        selectedDeviceName = nil
        logger.info("Selected device cleared.")
    }

    // MARK: - Dev mode

    /// Synchronous dev mode read from the SDK.
    ///
    /// Dev mode is what makes MetaAppID = 0 work. It can silently
    /// flip off, which is a known setup day failure, so this helper
    /// logs on every call and callers are expected to call it right
    /// before starting a stream session, not just at app launch.
    var isDevMode: Bool {
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. Some builds expose
        // this on the shared instance instead of as a static.
        Wearables.isDevMode
    }

    /// Reads dev mode, records it in `lastKnownDevMode`, logs it, and
    /// returns it. The setup day checklist step is literally: call
    /// this, confirm the log line says isDevMode == true, then start
    /// the session.
    @discardableResult
    func checkDevMode() -> Bool {
        let value = isDevMode
        lastKnownDevMode = value
        if value {
            logger.info("isDevMode == true. Dev mode is active, session start is allowed.")
        } else {
            logger.error("isDevMode == false. Dev mode is NOT active. Enable Developer Mode in the Meta AI app before starting a session.")
        }
        return value
    }

    /// Reads dev mode from live device state when a device is
    /// selected, falling back to the synchronous SDK value.
    ///
    /// Prefer `checkDevMode()` on the session start path because it
    /// never waits on the device. Use this refresh on the setup day
    /// checklist screen, where waiting for one state update is fine.
    @discardableResult
    func refreshDevMode() async -> Bool {
        guard let deviceID = selectedDeviceID else {
            return checkDevMode()
        }

        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. The device state
        // session type, its state publisher, and the dev mode field
        // on the state value all need a header check.
        let stateSession = DeviceStateSession(deviceId: deviceID)
        if let state = try? await stateSession.statePublisher.values.first(where: { _ in true }) {
            let value = state.isDevMode
            lastKnownDevMode = value
            logger.info("Device state reports isDevMode=\(value, privacy: .public) for device id=\(deviceID, privacy: .public).")
            return value
        }

        logger.info("No device state arrived during refreshDevMode. Falling back to the SDK value.")
        return checkDevMode()
    }
}
