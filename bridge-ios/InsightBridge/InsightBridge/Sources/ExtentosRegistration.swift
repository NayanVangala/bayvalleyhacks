// ExtentosRegistration.swift
// InsightBridge
//
// Owns Extentos SDK registration for the OnRecord bridge app.
// This file and ExtentosGlassesSource.swift are the ONLY files in the
// app allowed to import the Extentos SDK modules (GlassesCore here;
// GlassesUI only where ExtentosConnectionPage is used in the pairing
// UI). Everything else in the app talks to the SDK-free types in
// Common/MediaTypes.swift and Common/CaptureSource.swift, so the rest
// of the codebase can be built and tested with no SDK present.
//
// Lifecycle:
//   1. App launch calls `configure()` exactly once. This creates the
//      ONE shared `any ExtentosGlasses` instance via
//      `Extentos.create(config:)` and keeps it for the app lifetime.
//   2. The app scene forwards every incoming URL to
//      `handleOpenURL(_:)` so the Meta AI registration deep link
//      callback can complete via `glasses.handleUrl(_:)`.
//
// SWIFTUI WIRING (do this in InsightBridgeApp.swift, replacing the old
// wiring that called the sync DATRegistration.handleOpenURL):
//
//   var body: some Scene {
//       WindowGroup {
//           RootView()
//               .environmentObject(registration)   // ExtentosRegistration
//               // ...
//               .onOpenURL { url in
//                   Task { await registration.handleOpenURL(url) }
//               }
//       }
//   }
//
// Note: handleOpenURL is async now because Extentos's handleUrl is
// async. The coordinator and any other caller must await it.
//
// DROP-IN PAIRING UI: Extentos's canonical pairing/connecting/
// connected view is `ExtentosConnectionPage(glasses:)` from the
// GlassesUI product. The plan keeps it as the preferred pairing UI.
// Place it in PairingView (or a dedicated pairing route) like this:
//
//   import GlassesUI   // GlassesUI product linked on the app target
//
//   struct PairingView: View {
//       @EnvironmentObject var registration: ExtentosRegistration
//       var body: some View {
//           ExtentosConnectionPage(glasses: registration.glasses)
//       }
//   }
//
// Do not roll a custom pairing UI unless a custom affordance is needed
// (r1: the Extentos docs say the same). If the page is not adopted,
// drive a custom UI off `connectionStates` below.
//
// INFO.PLIST: this file reads EXTENTOS_APP_ID and EXTENTOSProjectKey
// from the app's Info.plist and fails LOUD with fatalError when
// either is missing, so a misconfigured Mac build dies at launch
// instead of failing silently on the setup day device. The Meta DAT
// registration block (MWDAT dict, CFBundleURLTypes,
// LSApplicationQueriesSchemes fb-viewapp, external accessory
// protocols) stays in Info.plist because Extentos calls the same DAT
// 0.8.0 transport underneath for camera and display.

import Combine
import Foundation
import GlassesCore
import os.log

/// Registration, connection lifecycle, and device selection for the
/// paired Meta glasses, backed by the Extentos SDK.
///
/// The Extentos connection state machine observed on
/// `glasses.connection.state.stream` is, per the docs:
///   notRegistered -> registered -> deviceDiscovered -> connecting
///   -> active -> disconnected
///
/// `connect()` is NOT idempotent, so this class guards it to run
/// exactly once per process. Retries must go through `reconnect()`,
/// which is serialized (one in-flight retry at a time; a new call
/// supersedes the previous one).
final class ExtentosRegistration: NSObject, ObservableObject {

    /// Coarse registration state surfaced to SwiftUI.
    /// `ready` means the SDK is configured and the Meta AI
    /// registration handshake has completed. It does not by itself
    /// mean the link is up; check `isConnected` for that.
    enum RegistrationState: Equatable {
        case unconfigured
        case ready
        case registering
        case failed(String)
    }

    // MARK: - Published state

    @Published private(set) var registrationState: RegistrationState = .unconfigured

    /// True once the connection state machine reaches `active`.
    /// Use this, not `registrationState`, to decide whether the
    /// glasses link is currently up.
    @Published private(set) var isConnected = false

    /// Identifier selected by the manual override, if any.
    /// Extentos discovery is BLE-driven through the Meta AI app, so
    /// the normal path does not vend a device id; this stays nil on
    /// that path and is kept for drills and the manual fallback.
    @Published private(set) var selectedDeviceID: String?

    /// Human readable device name for the status banner, if the SDK
    /// reports one. Display only, never used as an identifier.
    @Published private(set) var selectedDeviceName: String?

    /// Device type reported by the SDK, for display only.
    /// VERIFY-ON-MAC: the `glasses.device.type` accessor and the
    /// GlassesDeviceType case names below come from the Extentos
    /// docs (r1), not from the compiled SPM source (r2 verified only
    /// `.notRegistered` and `.registered` of GlassesState in source).
    @Published private(set) var selectedDeviceType: GlassesDeviceType?

    // MARK: - Shared instance

    /// The ONE ExtentosGlasses instance for the app lifetime.
    /// Created by `configure()`; force-unwrapped style access is
    /// intentional because every caller must run after configure.
    private(set) var glasses: (any ExtentosGlasses)!

    // MARK: - Private state

    /// Guard flag so `Extentos.create(config:)` runs exactly once per
    /// process. The SDK vends a single-owner handle; a second
    /// instance in the same process is not supported.
    private var didConfigure = false

    /// Exactly-once guard for `connect()`. Set under `stateLock`
    /// before the first await so no second caller can slip in.
    private var didCallConnect = false

    /// The one in-flight `reconnect()` task, if any. A new reconnect
    /// call cancels the previous one; retries are serialized, never
    /// concurrent, because connect paths are not idempotent.
    private var reconnectTask: Task<ExtentosResult<Void, ConnectError>, Never>?

    private let stateLock = NSLock()

    /// Task observing `glasses.connection.state.stream`. Cancelled by
    /// `shutdown()`.
    private var stateObserverTask: Task<Void, Never>?

    private let logger = Logger(subsystem: "com.nayan.insightbridge", category: "ExtentosRegistration")

    override init() {
        super.init()
    }

    // MARK: - Configure

    /// One time SDK bootstrap. Call from app init or the first scene
    /// appearance. Safe to call repeatedly; only the first call
    /// reaches the SDK.
    ///
    /// Reads EXTENTOS_APP_ID and EXTENTOSProjectKey from Info.plist
    /// and fails loud (fatalError) when either is missing. Production
    /// environment is set explicitly: the SDK default is development.
    func configure() {
        guard !didConfigure else {
            logger.info("ExtentosRegistration.configure called again, ignoring. SDK already configured.")
            return
        }
        didConfigure = true

        let appId = Self.infoPlistString("EXTENTOS_APP_ID")
        _ = Self.infoPlistString("EXTENTOSProjectKey")

        // VERIFY-ON-MAC: the memberwise ExtentosConfig initializer
        // labels (appId, transport, environment, usedCapabilities).
        // All four fields are verified in the SPM source (r2); only
        // the exact initializer shape needs a compile check.
        // VERIFY-ON-MAC: `Extentos.create(config:)` return type; r2
        // reads `-> ExtentosGlasses`, the plan reads `any
        // ExtentosGlasses`. The cast below assumes the existential.
        let config = ExtentosConfig(
            appId: appId,
            transport: .realMeta,
            environment: .production,
            usedCapabilities: [.microphone, .speaker, .camera, .display]
        )
        glasses = Extentos.create(config: config)

        startObservingConnectionState()

        if registrationState == .unconfigured {
            registrationState = .ready
        }
        logger.info("Extentos configured. appId=\(appId, privacy: .public) transport=realMeta environment=production.")
        logSetupChecklist()
    }

    // MARK: - Meta AI callback

    /// Forwards a URL to the SDK. Wire this from the SwiftUI
    /// `.onOpenURL` modifier (see the wiring snippet at the top of
    /// this file) so the Meta AI registration deep link callback can
    /// complete. Returns true when the SDK consumed the URL.
    ///
    /// This is async because Extentos's `handleUrl` is async; the old
    /// DATRegistration version was sync, so callers must now await.
    @discardableResult
    func handleOpenURL(_ url: URL) async -> Bool {
        guard didConfigure else { configure() }
        // VERIFY-ON-MAC: `handleUrl(_:) async -> Bool` is verified in
        // the SPM source (r2). Confirm the forwarding from
        // `.onOpenURL` actually receives the Meta AI callback on the
        // setup day device.
        let handled = await glasses.handleUrl(url)
        if handled {
            if registrationState == .registering || registrationState == .unconfigured {
                registrationState = .ready
            }
            logger.info("glasses.handleUrl consumed the callback URL. Registration state is ready.")
        } else {
            logger.info("glasses.handleUrl did not consume the URL. Leaving registration state unchanged.")
        }
        return handled
    }

    /// Marks registration as in flight. Call right before handing the
    /// user over to the Meta AI app, so a returning deep link is
    /// interpreted by `handleOpenURL(_:)` as a completed callback.
    ///
    /// VERIFY-ON-MAC: whether the one-time Meta AI confirmation is
    /// triggered by an explicit SDK call or is driven entirely by
    /// `ExtentosConnectionPage(glasses:)` plus the Meta AI app. If an
    /// explicit deep link URL is needed, add it here; do not invent
    /// one without a docs or source reference.
    func beginRegistration() {
        if !didConfigure {
            configure()
        }
        registrationState = .registering
        logger.info("Extentos registration marked in flight. Waiting for the Meta AI callback URL.")
    }

    // MARK: - Connection state observation

    /// AsyncStream of the raw Extentos connection state machine for
    /// UI rendering. The six cases (per docs; only `.notRegistered`
    /// and `.registered` verified in SPM source, rest VERIFY-ON-MAC):
    /// notRegistered, registered, deviceDiscovered, connecting,
    /// active, disconnected.
    ///
    /// Prefer this or `ExtentosConnectionPage(glasses:)` for pairing
    /// UI. Do not branch stream-activity logic on these states; a
    /// camera stream can stop delivering while the connection stays
    /// `active`.
    var connectionStates: AsyncStream<GlassesState> {
        // VERIFY-ON-MAC: `.stream` on ObservableState is read from
        // the SPM source (r2) but confirm the replaying-AsyncStream
        // behavior on device; `current` is the synchronous snapshot.
        glasses.connection.state.stream
    }

    /// Current connection state snapshot, for one-shot reads.
    var currentConnectionState: GlassesState {
        // VERIFY-ON-MAC: `current` snapshot accessor on
        // ObservableState, per r2 source reading.
        glasses.connection.state.current
    }

    private func startObservingConnectionState() {
        stateObserverTask?.cancel()
        stateObserverTask = Task { [weak self] in
            guard let self else { return }
            // VERIFY-ON-MAC: GlassesState case list beyond
            // `.notRegistered` and `.registered` comes from the
            // Extentos docs (r1), not the compiled source (r2
            // verified only those two in source). If the compiler
            // rejects a case here, check the uniffi-generated enum.
            for await state in self.glasses.connection.state.stream {
                await self.applyConnectionState(state)
            }
        }
    }

    @MainActor
    private func applyConnectionState(_ state: GlassesState) {
        switch state {
        case .notRegistered:
            isConnected = false
            logger.info("Connection state: notRegistered. Meta AI registration not done yet.")
        case .registered:
            isConnected = false
            if registrationState == .registering || registrationState == .unconfigured {
                registrationState = .ready
            }
            logger.info("Connection state: registered. App is in Meta AI App Connections.")
        case .deviceDiscovered:
            isConnected = false
            if registrationState == .unconfigured {
                registrationState = .ready
            }
            logger.info("Connection state: deviceDiscovered. Paired glasses visible over BLE.")
        case .connecting:
            isConnected = false
            logger.info("Connection state: connecting.")
        case .active:
            isConnected = true
            if registrationState == .unconfigured || registrationState == .registering {
                registrationState = .ready
            }
            refreshDeviceTypeLabel()
            logger.info("Connection state: active. Glasses link is up.")
        case .disconnected:
            isConnected = false
            // VERIFY-ON-MAC: the `cause` payload on `.disconnected`
            // and its cases (UserRequested, DeviceDroppedConnection,
            // ThermalCritical, HingesClosed, SimulatorSessionExpired,
            // SimulatorBrowserClosed, SimulatorMeterExhausted,
            // TransportFailure) come from the Extentos docs (r1),
            // not the compiled source. Hinge-close mapping to
            // SourceHealth.hingeClosed belongs to the capture source
            // (chunk B), not here.
            logger.info("Connection state: disconnected. Link lost; retry via reconnect(), not connect().")
        }
    }

    private func refreshDeviceTypeLabel() {
        // VERIFY-ON-MAC: `glasses.device.type` and the
        // GlassesDeviceType cases (RAYBAN_META, RAYBAN_DISPLAY,
        // OAKLEY_META_HSTN, OAKLEY_META_VANGUARD, RAYBAN_META_OPTICS,
        // META_GLASSES, UNKNOWN) come from the Extentos docs (r1).
        // Used for display only; never branch product logic on it.
        // VERIFY-ON-MAC: `glasses.device.vendor` exists per r1.
        let type = glasses.device.type
        Task { @MainActor in
            self.selectedDeviceType = type
            self.selectedDeviceName = String(describing: type)
        }
        logger.info("Device type: \(String(describing: type), privacy: .public).")
    }

    // MARK: - Connect / reconnect / disconnect

    /// Opens the glasses link exactly once per process. `connect()`
    /// is NOT idempotent per the Extentos docs, so a second call is
    /// ignored with a log line instead of reaching the SDK.
    ///
    /// Call this when the user enters the glasses feature (not at app
    /// launch): connect opens a DAT device session with vendor
    /// exclusivity and holds the glasses.
    ///
    /// Returns the SDK result so callers can surface
    /// `ConnectError.registrationRequired` (registration not done)
    /// or `ConnectError.noDeviceAvailable` (nothing paired).
    @discardableResult
    func connect() async -> ExtentosResult<Void, ConnectError> {
        if !didConfigure {
            configure()
        }
        stateLock.lock()
        if didCallConnect {
            stateLock.unlock()
            logger.info("connect() called twice; ignoring. Use reconnect() for retries.")
            return .success(())
        }
        didCallConnect = true
        stateLock.unlock()

        // VERIFY-ON-MAC: `connection.connect()` nil-deviceId
        // convenience overload, and the typed `DeviceId` form
        // `connection.connect(deviceId:)`. The manual
        // `selectDevice(id:name:)` override stores a String; if the
        // SDK exposes a String-to-DeviceId conversion, use it here,
        // otherwise keep passing nil (BLE discovery via the Meta AI
        // app picks the device).
        // VERIFY-ON-MAC: pattern-match on ExtentosResult (.success /
        // .failure) rather than do/catch; verified in r2 source.
        let result = await glasses.connection.connect()
        switch result {
        case .success:
            logger.info("connect() succeeded. Waiting for the state machine to reach active.")
        case .failure(let error):
            logger.error("connect() failed: \(String(describing: error), privacy: .public).")
        }
        return result
    }

    /// Serialized retry path. Safe to call repeatedly; only one
    /// retry is in flight at a time and a new call cancels the
    /// previous one. This is the ONLY retry primitive: never call
    /// `connect()` again after the first call.
    @discardableResult
    func reconnect() async -> ExtentosResult<Void, ConnectError> {
        if !didConfigure {
            configure()
        }
        stateLock.lock()
        reconnectTask?.cancel()
        let task = Task<ExtentosResult<Void, ConnectError>, Never> { [weak self] in
            guard let self else { return .failure(.transportFailure(code: "deallocated", message: "registration released")) }
            // VERIFY-ON-MAC: `connection.reconnect()` nil-deviceId
            // overload and the typed `reconnect(deviceId:)` form.
            // VERIFY-ON-MAC: the ConnectError case used above
            // (`transportFailure(code:message:)`) is verified in r2
            // source; keep the shape in sync with the uniffi enum.
            return await self.glasses.connection.reconnect()
        }
        reconnectTask = task
        stateLock.unlock()

        let result = await task.value
        switch result {
        case .success:
            logger.info("reconnect() succeeded.")
        case .failure(let error):
            logger.error("reconnect() failed: \(String(describing: error), privacy: .public).")
        }
        return result
    }

    /// Drops the link without tearing down the SDK. The instance
    /// stays usable; a later `reconnect()` brings the link back.
    func disconnect() async {
        // VERIFY-ON-MAC: `connection.disconnect()` signature,
        // verified in r2 source.
        await glasses.connection.disconnect()
        logger.info("disconnect() called. Link dropped; instance retained.")
    }

    // MARK: - Device selection

    /// Discovery replacement. Extentos discovery is BLE-based through
    /// the Meta AI app, so there is no in-app scan: this ensures the
    /// SDK is configured, triggers the exactly-once `connect()`, and
    /// waits for the state machine to reach `active`.
    ///
    /// Returns a device label on success, nil on failure. Failures
    /// publish through `registrationState` as `.failed` so the UI
    /// can explain phone mode instead of spinning forever.
    ///
    /// There is exactly one demo device, so no chooser UI is needed.
    @discardableResult
    func discoverAndSelectDevice() async -> String? {
        if !didConfigure {
            configure()
        }

        let result = await connect()
        switch result {
        case .success:
            break
        case .failure(let error):
            let message = "Glasses connect failed: \(String(describing: error))"
            await MainActor.run { self.registrationState = .failed(message) }
            logger.error("\(message, privacy: .public)")
            return nil
        }

        // Wait for the state machine to reach active. The connect
        // timeout mirrors the old 40 s first-frame attach wait; the
        // first camera arm underneath takes ~14 s and DAT kills the
        // attempt at ~30 s.
        // VERIFY-ON-MAC: confirm the wall-clock budget against the
        // real-model run on the Mac.
        let deadline = Date().addingTimeInterval(40)
        while Date() < deadline {
            let state = currentConnectionState
            if state == .active {
                let label = await MainActor.run { () -> String in
                    if registrationState == .unconfigured || registrationState == .registering {
                        registrationState = .ready
                    }
                    return selectedDeviceName ?? "glasses"
                }
                logger.info("Device active. label=\(label, privacy: .public).")
                return label
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }

        let message = "Timed out waiting for the glasses link to reach active."
        await MainActor.run { self.registrationState = .failed(message) }
        logger.error("\(message, privacy: .public)")
        return nil
    }

    /// Manual selection override for drills and for the setup day
    /// fallback where the device id is copied from the Meta AI app.
    /// Discovery remains the normal path; this only stores the id.
    ///
    /// VERIFY-ON-MAC: whether the typed DeviceId the SDK wants can
    /// be built from this String. Until verified, connect passes nil
    /// and this id is diagnostic only.
    func selectDevice(id: String, name: String? = nil) {
        selectedDeviceID = id
        selectedDeviceName = name
        if registrationState == .unconfigured {
            registrationState = .ready
        }
        logger.info("Device selected manually. id=\(id, privacy: .public). Typed DeviceId conversion unverified; connect still passes nil.")
    }

    /// Clears the current selection, for example after an unpair.
    /// Does not unregister the SDK and does not touch any session.
    func clearSelectedDevice() {
        selectedDeviceID = nil
        selectedDeviceName = nil
        selectedDeviceType = nil
        logger.info("Selected device cleared.")
    }

    // MARK: - Setup checklist (replaces checkDevMode)

    /// The old dev-mode checklist helper (`checkDevMode`, MetaAppID =
    /// 0) has no Extentos equivalent. This logs the connection state
    /// plus the EXTENTOSProjectKey presence check, which is the new
    /// setup day gate: without the project key the simulator works
    /// but real hardware fails at runtime with
    /// `AssistantError.NoApiKey`.
    ///
    /// VERIFY-ON-MAC: confirm the AssistantError.NoApiKey failure
    /// signature on the setup day device if the key is ever absent.
    func logSetupChecklist() {
        // configure() already fatalErrors when either key is
        // missing, so reaching here means both are present.
        let state = currentConnectionState
        logger.info("Setup checklist: EXTENTOS_APP_ID present, EXTENTOSProjectKey present, connection state=\(String(describing: state), privacy: .public).")
    }

    // MARK: - Shutdown

    /// Full teardown for app exit. Stops the state observer and shuts
    /// down the SDK (assistant, voice client, transport, event
    /// logger drain). Call once from the app scene on termination;
    /// do not call it to recover from errors, use `reconnect()`.
    func shutdown() async {
        stateObserverTask?.cancel()
        stateObserverTask = nil
        stateLock.lock()
        reconnectTask?.cancel()
        reconnectTask = nil
        stateLock.unlock()
        // VERIFY-ON-MAC: `shutdown()` on ExtentosGlasses is verified
        // in the SPM source (r2). Confirm it is safe to call without
        // a prior connect on the real-model run.
        await glasses.shutdown()
        await MainActor.run {
            self.isConnected = false
            self.registrationState = .unconfigured
        }
        logger.info("Extentos shutdown complete.")
    }

    // MARK: - Info.plist

    /// Reads a required String key from Info.plist. Fails loud with a
    /// clear message so a misconfigured Mac build dies at launch,
    /// not silently on the setup day device.
    private static func infoPlistString(_ key: String) -> String {
        guard let value = Bundle.main.infoDictionary?[key] as? String,
              !value.isEmpty else {
            fatalError(
                "InsightBridge misconfigured: Info.plist is missing the '\(key)' key. " +
                "Run generateConnectionModule (Extentos MCP server) at least once for this project " +
                "to mint EXTENTOS_APP_ID / EXTENTOSProjectKey into the build."
            )
        }
        return value
    }
}
