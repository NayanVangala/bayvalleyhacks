//
//  SessionManager.swift
//  InsightBridge
//
//  The brain of the bridge app. Owns:
//    - which CaptureSource is live (glasses vs phone vs mock drill source)
//    - the conversation state machine (SessionStateMachine, Common)
//    - the watchdog that detects dead or flaky glasses
//    - the ONE failover path to phone-only (watchdog and kill switch share it)
//    - PTT wiring: active source audio -> PTTController -> sealed frames -> WSClient
//    - the photo cadence timer and server initiated capture_now stills
//
//  Hard rules from the build plan, enforced here:
//    1. Default mode at launch is PHONE. Glasses are opt-in sensors, attached
//       only via attachGlasses() (user action, retry button, or auto re-attach).
//    2. There is exactly ONE failover function: failoverToPhone(reason:).
//       The watchdog and the manual kill switch both funnel into it.
//    3. The session ID NEVER changes across failover. The WSClient connection
//       and the server side session survive; only the capture source swaps.
//    4. No modal dialogs anywhere in the failover path. The only user visible
//       signals are the persistent StatusBanner plus one haptic.
//
//  SDK rule: this file imports NO Meta SDK. It only sees the CaptureSource
//  protocol. The SDK lives in Sources/GlassesSource.swift and
//  Sources/DATRegistration.swift and nowhere else.
//
//  Wire notes (protocol v1, Net/WireProtocol.swift and Net/WSClient.swift):
//    - WSClient sends the hello message itself on every connect and
//      reconnect, using its audioSource property (plus resume_session_id on
//      reconnects). This class never builds a hello; it keeps
//      wsClient.audioSource set to the live origin, so the next hello the
//      client sends always names the current source of record.
//    - Segment indices on the wire come from PTTController.currentSegmentIndex,
//      because PTT stamps the audio frames themselves with that index. The
//      state machine has its own segment counter; the two can diverge after
//      a discarded sub-300 ms segment (PTT does not advance, the machine
//      does), and on the wire PTT wins so talk_start / talk_end always match
//      the audio frames they describe.
//    - Every other ControlMessage is built in the "WSClient call sites"
//      section at the bottom of this file, and nowhere else.
//

import Foundation
import Combine
import UIKit

// MARK: - Mode and banner types

/// Which capture path is live. `.reconnecting` is transient: it is only set
/// while a glasses attach or re-attach attempt is in flight.
enum SessionMode: Equatable {
    case glasses
    case phone
    case reconnecting
}

/// Persistent status banner model. The UI renders this at all times.
/// It is never a toast and never a modal; it just changes kind and text.
struct StatusBanner: Equatable {
    enum Kind: Equatable {
        case ok
        case warn
        case error
    }

    var kind: Kind
    var text: String

    static let phoneReady = StatusBanner(kind: .warn, text: "PHONE MODE - mic and camera on this phone")
    static let glassesLive = StatusBanner(kind: .ok, text: "GLASSES LIVE - POV capture")
    static let reconnecting = StatusBanner(kind: .error, text: "RECONNECTING - phone mic stays live")
}

extension FailoverReason {
    /// One line, judge readable explanation shown in the amber banner.
    var failoverBannerText: String {
        switch self {
        case .watchdogNoFrames:
            return "PHONE MODE - glasses stopped sending frames, phone mic is live"
        case .sessionError:
            return "PHONE MODE - glasses session error, phone mic is live"
        case .hingeClosed:
            return "PHONE MODE - glasses temples closed, phone mic is live"
        case .jitter:
            return "PHONE MODE - glasses link too unstable, phone mic is live"
        case .manual:
            return "PHONE MODE - switched by kill switch, phone mic is live"
        case .glassesUnavailable:
            return "PHONE MODE - glasses unavailable, phone mic is live"
        }
    }
}

// MARK: - Frame counter

/// Thread safe tally of the audio frames PTT has emitted this session.
/// The PTT frame sink runs under the PTT lock on whatever thread fed the
/// chunk, so it cannot touch SessionManager state directly; it bumps this
/// counter instead, and SessionManager reads it for talk_end and
/// session_end stats.
private final class FrameCounter {
    private let lock = NSLock()
    private(set) var frames: Int = 0
    private(set) var pcmBytes: Int = 0

    func recordFrame(pcmByteCount: Int) {
        lock.lock()
        frames += 1
        pcmBytes += pcmByteCount
        lock.unlock()
    }

    func reset() {
        lock.lock()
        frames = 0
        pcmBytes = 0
        lock.unlock()
    }
}

// MARK: - SessionManager

@MainActor
final class SessionManager: ObservableObject {

    // MARK: Published UI state

    /// Current capture mode. Starts as .phone on purpose (hard rule 1).
    @Published private(set) var mode: SessionMode = .phone

    /// Mirror of SessionStateMachine.state so SwiftUI can observe it.
    /// The machine itself is a value type and not Observable.
    @Published private(set) var conversationState: ConversationState = .idle

    /// Mirror of SessionStateMachine.sessionID. Nil when no session is armed.
    /// This value is set once in startSession() and is never reassigned by
    /// failover, re-attach, pause, or resume (hard rule 3).
    @Published private(set) var currentSessionID: UUID?

    /// Persistent banner. Always has a value, from the first frame of the app.
    @Published private(set) var banner: StatusBanner = .phoneReady

    /// Origin of the source currently feeding the pipeline.
    @Published private(set) var activeOrigin: CaptureOrigin = .phone

    /// True only while glasses are started, healthy, and routed in.
    @Published private(set) var isGlassesAttached: Bool = false

    /// Live mic level for the session UI meter, mirrored from PTTController.
    @Published private(set) var audioLevel: Float = 0

    /// Last known WSClient connectivity. Informational only: capture keeps
    /// running while the socket is down (WSClient stages and replays).
    @Published private(set) var isServerConnected: Bool = false

    /// True once auto re-attach has given up (attempt cap hit, or the user
    /// hit the kill switch). Cleared only by retryGlasses().
    @Published private(set) var isLatchedToPhone: Bool = false

    // MARK: Dependencies

    private let glassesSource: CaptureSource
    private let phoneSource: CaptureSource
    private let mockSource: CaptureSource?
    private let ws: WSClient
    private let ptt: PTTController

    /// The source that plays the "glasses" role. When a MockSource was
    /// injected at construction (failover drills, no hardware), it stands in
    /// for the real glasses for the whole app lifetime. The swap is a
    /// construction time decision only, never a runtime surprise.
    private var glassesLikeSource: CaptureSource {
        mockSource ?? glassesSource
    }

    /// Source that photos are taken from.
    private var activeCaptureSource: CaptureSource {
        mode == .glasses ? glassesLikeSource : phoneSource
    }

    // MARK: State machine

    /// The single conversation state machine. All talk, pause, resume, and
    /// end transitions go through it; this class never invents its own
    /// conversation state.
    private(set) var machine = SessionStateMachine()

    // MARK: Monotonic clock

    /// Process uptime captured at session start. All capture timestamps are
    /// milliseconds since this instant, from the monotonic system clock, so
    /// wall clock changes can never corrupt ordering. Marked
    /// nonisolated(unsafe) because PTTController's clock closure reads it
    /// (via captureTsMs) from audio threads; it is written only at session
    /// start and cleared only at session end.
    nonisolated(unsafe) private(set) var sessionStartUptime: TimeInterval?

    /// Milliseconds since session start on the monotonic clock.
    /// Returns 0 before a session is armed.
    nonisolated func captureTsMs() -> UInt32 {
        guard let start = sessionStartUptime else { return 0 }
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        guard elapsed > 0 else { return 0 }
        let ms = elapsed * 1000.0
        if ms >= Double(UInt32.max) { return UInt32.max }
        return UInt32(ms)
    }

    private func nowUptime() -> TimeInterval {
        ProcessInfo.processInfo.systemUptime
    }

    // MARK: Bookkeeping

    private let frameCounter = FrameCounter()
    private var cancellables = Set<AnyCancellable>()

    private var photoFrameSeq: UInt32 = 0
    private var photoCount: Int = 0
    private var failoverCount: Int = 0
    private var segmentsDiscarded: Int = 0
    private var sessionStartedAt: Date?

    /// Frame counter snapshot at the current segment's press, so talk_end
    /// can report the per segment frames_sent the protocol asks for.
    private var segmentStartFrameCount: Int = 0
    private var segmentStartTsMs: UInt32 = 0

    private var glassesRunning = false
    private var phoneRunning = false
    private var isFailingOver = false
    private var isAttachingGlasses = false

    /// Anti-flap state. lastFailoverAt starts the cooldown clock;
    /// autoReattachAttempts counts automatic retries this session.
    private var lastFailoverAt: Date?
    private var autoReattachAttempts: Int = 0

    /// Liveness state maintained by the routing tasks, read by the watchdog.
    private var lastGlassesVideoAt: TimeInterval?
    private var lastGlassesAudioAt: TimeInterval?
    private var lastGlassesVideoFrameTimestamp: Date?
    private var jitterStrikeTimestamps: [Date] = []

    // MARK: Tasks

    private var glassesVideoTask: Task<Void, Never>?
    private var glassesAudioTask: Task<Void, Never>?
    private var glassesHealthTask: Task<Void, Never>?
    private var phoneAudioTask: Task<Void, Never>?
    private var watchdogTask: Task<Void, Never>?
    private var photoTask: Task<Void, Never>?
    private var autoReattachTask: Task<Void, Never>?

    // MARK: Init

    init(glassesSource: CaptureSource,
         phoneSource: CaptureSource,
         mockSource: CaptureSource?,
         wsClient: WSClient,
         pttController: PTTController) {
        self.glassesSource = glassesSource
        self.phoneSource = phoneSource
        self.mockSource = mockSource
        self.ws = wsClient
        self.ptt = pttController

        // PTT wiring, done once for the app lifetime.
        //
        // The frame sink forwards each sealed frame straight to WSClient.
        // PTT invokes the sink while holding its internal lock, so the sink
        // must be fast, non-blocking, and must never call back into PTT:
        // WSClient.sendAudioFrame is exactly that (it encodes a 16 byte
        // header and hands off to its own serial queue). The sink therefore
        // captures the WSClient and the frame counter directly and never
        // touches SessionManager state.
        let counter = frameCounter
        ptt.attach(frameSink: { segIdx, frameSeq, captureTsMs, flags, pcm in
            counter.recordFrame(pcmByteCount: pcm.count)
            wsClient.sendAudioFrame(segIdx: segIdx,
                                    frameSeq: frameSeq,
                                    captureTsMs: captureTsMs,
                                    flags: flags,
                                    pcm: pcm)
        })

        // PTT timestamps its frames from this clock: milliseconds since
        // session start, the same clock every control message uses.
        ptt.setClock { [weak self] in
            self?.captureTsMs() ?? 0
        }

        // Sub-300 ms segments: PTT discards them after their frames were
        // already streamed. Protocol v1 has no segment-drop control
        // message, so the honest handling available here is to count and
        // log them; the server sees the short segment and its talk_end
        // duration and can drop it on its side. Flagged for the protocol
        // owners rather than papered over with an invented message type.
        ptt.onSegmentDiscarded = { [weak self] segIdx in
            NSLog("SessionManager: PTT discarded short segment %d", segIdx)
            Task { @MainActor in
                self?.segmentsDiscarded += 1
            }
        }

        // Mic level mirror for the session UI meter.
        ptt.$audioLevel
            .sink { [weak self] level in
                Task { @MainActor in
                    self?.audioLevel = level
                }
            }
            .store(in: &cancellables)

        // Server initiated still: a capture_now message carries a photo_id
        // the server expects echoed back in photo_meta. It bypasses the
        // cadence timer and always takes an eager still from the active
        // source.
        ws.onCaptureNow = { [weak self] photoID in
            guard let self else { return }
            Task { @MainActor in
                await self.captureAndSendPhoto(eager: true, photoID: photoID)
            }
        }

        // Socket state for the UI. No capture decision depends on it:
        // WSClient owns reconnect, staging, and replay.
        ws.onConnectionChange = { [weak self] connected in
            Task { @MainActor in
                self?.isServerConnected = connected
            }
        }

        // Server rejected the session (e.g. 4404): surface it in the
        // banner and stop; WSClient already refuses to reconnect-spin.
        // XCODE-VERIFY: banner shows on 4404 simulation against the dev server.
        ws.onSessionError = { [weak self] code, message in
            Task { @MainActor in
                self?.banner = StatusBanner(kind: .sessionError, text: "Session error (\(code)): \(message)")
            }
        }

        // The hello the client will send names this origin. Until glasses
        // are attached and live, the phone is the source of record.
        ws.audioSource = CaptureOrigin.phone.rawValue
    }

    deinit {
        glassesVideoTask?.cancel()
        glassesAudioTask?.cancel()
        glassesHealthTask?.cancel()
        phoneAudioTask?.cancel()
        watchdogTask?.cancel()
        photoTask?.cancel()
        autoReattachTask?.cancel()
    }

    // MARK: - Session control API (called by the UI)

    /// Starts a new conversation session on whatever source is currently
    /// live (phone by default). Generates the session ID exactly once; this
    /// ID is then stable for the whole session, failovers included.
    func startSession() {
        // arm() is only legal from .idle, and a machine that already ended
        // a session stays .ended. A fresh session gets a fresh machine,
        // which is also what resets its segment counter.
        if machine.state == .ended {
            machine = SessionStateMachine()
        }
        guard machine.state == .idle else { return }

        let sessionID = UUID()
        guard machine.arm(sessionID: sessionID) else { return }

        currentSessionID = sessionID
        sessionStartUptime = nowUptime()
        sessionStartedAt = Date()
        photoFrameSeq = 0
        photoCount = 0
        failoverCount = 0
        segmentsDiscarded = 0
        autoReattachAttempts = 0
        isLatchedToPhone = false
        lastFailoverAt = nil
        frameCounter.reset()
        ptt.reset()
        syncMachineMirror()

        Task { [weak self] in
            guard let self else { return }
            // The hello sent by connect() must name the live origin.
            ws.audioSource = activeOrigin.rawValue
            await ws.connect(sessionID: sessionID)
            sendSessionStart()

            // Start whichever source is live. If glasses were attached
            // before the session started, they are already running and
            // routing just needs to (re)start against the now active
            // session.
            do {
                try await startActiveSourceIfNeeded()
            } catch {
                // Phone is the baseline product: if even the phone source
                // fails to start, surface it in the banner and stay armed
                // so the user can retry. Never block on a dialog.
                banner = StatusBanner(kind: .error, text: "Capture failed to start on this phone")
                return
            }

            startRoutingTasks()
            startPhotoCadence()

            // Eager still at session start so the server has a speaker
            // view before the first cadence tick lands.
            await captureAndSendPhoto(eager: true)

            banner = (mode == .glasses) ? .glassesLive : .phoneReady
        }
    }

    /// Push-to-talk press. Forwards to the state machine first; only a
    /// legal transition reaches PTT and the server, so stray presses are
    /// no-ops.
    func pressBegan() {
        guard currentSessionID != nil else { return }
        guard machine.beginTalk() else { return }
        syncMachineMirror()
        segmentStartFrameCount = frameCounter.frames
        segmentStartTsMs = captureTsMs()
        // talk_start goes on the wire BEFORE the segment's first audio
        // frame (PROTOCOL.md 5.2 wire order). PTT drains the 10 s
        // pre-roll ring synchronously inside pressBegan() and emits
        // those frames through the sink, so the control message must
        // be sent first or every segment would start with audio frames
        // ahead of their talk_start.
        sendTalkStart()
        ptt.pressBegan()
    }

    /// Push-to-talk release. Seals the open segment.
    func pressEnded() {
        guard machine.state == .recording else { return }
        guard machine.endTalk() else { return }
        syncMachineMirror()
        // Capture the wire segment index BEFORE sealing: when PTT seals a
        // kept segment it advances currentSegmentIndex to the next segment,
        // and talk_end must name the segment that was just sealed, the one
        // its audio frames carry.
        let sealedSegIdx = ptt.currentSegmentIndex
        ptt.pressEnded()
        sendTalkEnd(segIdx: sealedSegIdx)
    }

    /// Pauses capture. The state machine allows pause straight from
    /// .recording, and PTT.setPaused seals any open segment itself, so a
    /// segment never spans a pause and the transcript can render an honest
    /// PAUSED gap.
    func pauseSession() {
        guard currentSessionID != nil else { return }
        let wasRecording = machine.state == .recording
        guard machine.pause() else { return }
        syncMachineMirror()
        // Same index capture rule as pressEnded: setPaused seals the open
        // segment, which can advance the PTT index.
        let sealedSegIdx = ptt.currentSegmentIndex
        ptt.setPaused(true)
        if wasRecording {
            sendTalkEnd(segIdx: sealedSegIdx)
        }
        sendSessionPause()
    }

    /// Resumes a paused session on the same session ID and same source mode.
    func resumeSession() {
        guard currentSessionID != nil else { return }
        guard machine.resume() else { return }
        syncMachineMirror()
        ptt.setPaused(false)
        sendSessionResume()
    }

    /// Ends the session: seals any open segment, reports final stats (with
    /// WSClient's replay and drop counters, so staged losses are accounted
    /// for, never silent), stops every source, and disconnects. The server
    /// keeps the session record; nothing about ending depends on which
    /// source was live.
    func endSession() {
        guard let sessionID = currentSessionID else { return }
        let wasRecording = machine.state == .recording
        if wasRecording {
            let sealedSegIdx = ptt.currentSegmentIndex
            ptt.pressEnded()
            sendTalkEnd(segIdx: sealedSegIdx)
        }
        guard machine.end() else { return }
        syncMachineMirror()

        let stats = ControlMessage.SessionStats(
            photosSent: photoCount,
            audioSeconds: Double(frameCounter.pcmBytes) / 32_000.0,
            framesSent: frameCounter.frames,
            chunksReplayed: ws.chunksReplayed,
            chunksDropped: ws.stagedDropCount
        )
        sendSessionEnd(sessionID: sessionID, stats: stats)

        // Reset PTT only after the stats snapshot above: reset zeroes the
        // counters the snapshot was partly built from.
        ptt.reset()

        stopAllTasks()
        let glasses = glassesLikeSource
        let phone = phoneSource
        let wasGlassesRunning = glassesRunning
        let wasPhoneRunning = phoneRunning
        glassesRunning = false
        phoneRunning = false
        Task {
            if wasGlassesRunning { await glasses.stop() }
            if wasPhoneRunning { await phone.stop() }
        }
        ws.disconnect()

        isGlassesAttached = false
        if mode == .glasses { mode = .phone }
        activeOrigin = .phone
        ws.audioSource = CaptureOrigin.phone.rawValue
        currentSessionID = nil
        sessionStartUptime = nil
        banner = StatusBanner(kind: .ok, text: "Session ended - saved on the server")
    }

    /// Tags the current moment for later lookup (a name just got said, a
    /// commitment was made). Pure control message; capture is undisturbed.
    /// `source` names the input that produced the tag, for example
    /// "neural-band", "temple-touchpad", "ui", or "requery".
    func tagMoment(source: String) {
        guard currentSessionID != nil else { return }
        sendTagMoment(source: source)
    }

    // MARK: - Glasses attach

    /// Attaches the glasses as the live capture source. Safe to call with no
    /// session running (pre-attach, which is what AppCoordinator does at
    /// launch when the user prefers glasses) or mid session: the session ID,
    /// the WS connection, and any open conversation carry over untouched.
    ///
    /// Cold start matrix behavior:
    ///   - glasses start throws (absent, dead, unpaired, registration gone):
    ///     stay in phone mode, banner explains, no spinner, no dialog.
    ///   - glasses start but no healthy frame within the timeout: same.
    ///   - first healthy frame arrives: flip to .glasses and route in.
    func attachGlasses() async {
        await attachGlasses(isAutoAttempt: false, bypassCooldown: false)
    }

    /// Manual "Retry glasses" button. This is the only way out of the
    /// latched to phone state: it resets the attempt counter and the
    /// cooldown, then runs a normal attach.
    func retryGlasses() {
        autoReattachAttempts = 0
        isLatchedToPhone = false
        lastFailoverAt = nil
        autoReattachTask?.cancel()
        autoReattachTask = nil
        Task { [weak self] in
            await self?.attachGlasses(isAutoAttempt: false, bypassCooldown: true)
        }
    }

    private func attachGlasses(isAutoAttempt: Bool, bypassCooldown: Bool) async {
        _ = isAutoAttempt // attempts are counted by the scheduler, not here
        guard !isAttachingGlasses else { return }
        guard !isGlassesAttached else { return }

        // Anti-flap cooldown: automatic attempts are scheduled after the
        // cooldown already, but a stray manual attach inside the window is
        // refused quietly (banner only) unless it came via retryGlasses().
        if !bypassCooldown, let lastFailoverAt,
           Date().timeIntervalSince(lastFailoverAt) < Config.failoverCooldownSeconds {
            banner = StatusBanner(kind: .warn, text: "PHONE MODE - glasses retry cooling down")
            return
        }
        if isLatchedToPhone && !bypassCooldown {
            banner = StatusBanner(kind: .warn, text: "PHONE MODE - tap Retry Glasses to try again")
            return
        }

        isAttachingGlasses = true
        defer { isAttachingGlasses = false }

        mode = .reconnecting
        banner = .reconnecting

        let source = glassesLikeSource
        do {
            try await source.start()
        } catch {
            // Cold start matrix row: glasses absent or registration revoked.
            // Phone mode is the product working as designed, not an error
            // state, so this stays a one line banner, never a dialog.
            mode = .phone
            banner = StatusBanner(kind: .warn,
                                  text: FailoverReason.glassesUnavailable.failoverBannerText)
            return
        }
        glassesRunning = true

        // Start the glasses routing and health tasks so liveness state
        // starts filling in, then wait briefly for the first healthy frame.
        // The tasks are the single consumers of the source streams; this
        // wait only polls the state they maintain, it never touches the
        // streams itself.
        startGlassesRoutingTasks()
        startGlassesHealthTask()
        let healthy = await waitForFirstHealthyGlassesFrame(timeout: Config.noFrameTimeoutSeconds + 2.0)

        guard healthy else {
            // Started but silent: treat as unavailable, tear down best
            // effort, and remain exactly where we were.
            stopGlassesRoutingTasks()
            glassesHealthTask?.cancel()
            glassesHealthTask = nil
            glassesRunning = false
            await stopSourceWithTimeout(source)
            mode = .phone
            banner = StatusBanner(kind: .warn,
                                  text: FailoverReason.glassesUnavailable.failoverBannerText)
            return
        }

        // First healthy frame seen: glasses are live.
        isGlassesAttached = true
        mode = .glasses
        activeOrigin = source.origin
        banner = .glassesLive
        UINotificationFeedbackGenerator().notificationOccurred(.success)

        // A successful attach resets the anti-flap counters.
        autoReattachAttempts = 0

        // From the next hello onward (connect or reconnect), the server
        // hears about the new source of record. Session ID unchanged.
        ws.audioSource = source.origin.rawValue

        // Phone stays running as the hot audio standby (see the audio
        // routing note in startGlassesRoutingTasks). If a session is live,
        // make sure the phone source is up so the fallback is instant.
        if currentSessionID != nil && !phoneRunning {
            do {
                try await phoneSource.start()
                phoneRunning = true
                startPhoneAudioTask()
            } catch {
                // Glasses are live; a dead phone standby only weakens the
                // fallback. Keep going with the green banner: if a failover
                // fires later and the phone still will not start, the
                // failover path surfaces that as an error banner then.
            }
        }

        startWatchdog()
    }

    /// Polls the liveness state filled in by the routing tasks until the
    /// first glasses video frame lands, or the timeout expires.
    private func waitForFirstHealthyGlassesFrame(timeout: TimeInterval) async -> Bool {
        let deadline = nowUptime() + timeout
        while nowUptime() < deadline {
            if lastGlassesVideoAt != nil {
                return true
            }
            if Task.isCancelled { return false }
            try? await Task.sleep(nanoseconds: 100_000_000) // 100 ms poll
        }
        return lastGlassesVideoAt != nil
    }

    // MARK: - Failover (the ONE path)

    /// The manual kill switch behind the big red GO PHONE-ONLY button.
    /// Synchronous, immediate, no confirmation, by design: it Task-launches
    /// the same failoverToPhone the watchdog uses, with reason .manual.
    /// A manual failover also latches to phone (no auto re-attach), because
    /// the user just said they want the phone, deliberately.
    func killSwitch() {
        isLatchedToPhone = true
        autoReattachTask?.cancel()
        autoReattachTask = nil
        Task { [weak self] in
            await self?.failoverToPhone(reason: .manual)
        }
    }

    /// THE failover function. Every glasses failure, automatic or manual,
    /// lands here. Steps, in order:
    ///   1. Best effort glasses stop with a 1 s timeout. A hung glasses
    ///      teardown must never delay the phone taking over.
    ///   2. Ensure PhoneSource is started (it usually already is, running
    ///      as the hot audio standby, so this is normally a no-op check).
    ///   3. Flip mode to .phone, amber banner with the reason, one haptic.
    ///   4. Set the cooldown timestamp and schedule or latch auto re-attach.
    ///
    /// What deliberately does NOT happen here: no new session ID, no WS
    /// reconnect, no state machine reset, no dialog. The conversation and
    /// its server side record continue as if only a microphone changed
    /// hands, which is exactly what happened.
    func failoverToPhone(reason: FailoverReason) async {
        guard mode == .glasses || mode == .reconnecting else {
            // Already on the phone. A late watchdog tick or a double kill
            // switch tap lands here and is a no-op, not an error.
            if reason == .manual {
                banner = StatusBanner(kind: .warn, text: FailoverReason.manual.failoverBannerText)
            }
            return
        }
        guard !isFailingOver else { return }
        isFailingOver = true
        defer { isFailingOver = false }

        failoverCount += 1
        lastFailoverAt = Date()

        // The watchdog and glasses routing tasks are about to be obsolete.
        watchdogTask?.cancel()
        watchdogTask = nil
        stopGlassesRoutingTasks()
        glassesHealthTask?.cancel()
        glassesHealthTask = nil

        // Step 1: best effort glasses stop, 1 s cap.
        if glassesRunning {
            glassesRunning = false
            await stopSourceWithTimeout(glassesLikeSource)
        }
        isGlassesAttached = false
        lastGlassesVideoAt = nil
        lastGlassesAudioAt = nil
        lastGlassesVideoFrameTimestamp = nil
        jitterStrikeTimestamps.removeAll()

        // Step 2: make sure the phone source is live. In the common case it
        // already is (hot standby during glasses mode) and only its audio
        // routing needs to become unconditional, which re-arming the phone
        // audio task below handles.
        if !phoneRunning {
            do {
                try await phoneSource.start()
                phoneRunning = true
            } catch {
                banner = StatusBanner(kind: .error, text: "PHONE CAPTURE FAILED - check mic and camera permission")
                mode = .phone
                activeOrigin = .phone
                ws.audioSource = CaptureOrigin.phone.rawValue
                return
            }
        }
        startPhoneAudioTask()

        // Step 3: flip mode, banner, haptic. Banner text carries the reason
        // so a judge (or the user) can see why the switch happened.
        mode = .phone
        activeOrigin = phoneSource.origin
        banner = StatusBanner(kind: .warn, text: reason.failoverBannerText)
        UINotificationFeedbackGenerator().notificationOccurred(.warning)

        // The server does not need a dedicated source-changed message:
        // audio frames are stamped by PTT with no origin ambiguity in the
        // segment stream, photos carry the sentinel segment index, and the
        // next hello WSClient sends (any reconnect) carries the updated
        // audio_source, which the line below sets now.
        ws.audioSource = phoneSource.origin.rawValue

        // Step 4: anti-flap. Manual failovers latch (set by killSwitch()).
        // Automatic failovers schedule ONE re-attach after the cooldown,
        // until the attempt cap latches us to phone for good.
        if reason == .manual {
            isLatchedToPhone = true
        } else {
            scheduleAutoReattachIfNeeded()
        }
    }

    private func scheduleAutoReattachIfNeeded() {
        guard !isLatchedToPhone else { return }
        guard autoReattachAttempts < Config.maxAutoReattachAttempts else {
            isLatchedToPhone = true
            banner = StatusBanner(kind: .warn,
                                  text: "PHONE MODE - glasses retries used up, tap Retry Glasses to try again")
            return
        }
        autoReattachTask?.cancel()
        autoReattachTask = Task { [weak self] in
            // Cooldown: no auto re-attach for failoverCooldownSeconds.
            try? await Task.sleep(nanoseconds: UInt64(Config.failoverCooldownSeconds * 1_000_000_000))
            guard !Task.isCancelled else { return }
            guard let self else { return }
            guard !self.isLatchedToPhone else { return }
            // Never resurrect glasses for a session the user already ended.
            guard self.machine.state != .ended else { return }
            self.autoReattachAttempts += 1
            await self.attachGlasses(isAutoAttempt: true, bypassCooldown: true)
            if !self.isGlassesAttached && !self.isLatchedToPhone {
                // That attempt failed. Schedule the next one the same way,
                // until the cap in scheduleAutoReattachIfNeeded latches us.
                self.scheduleAutoReattachIfNeeded()
            }
        }
    }

    // MARK: - Watchdog

    /// Starts (or restarts) the watchdog. It ticks 4x per second while
    /// glasses are live and applies the plan thresholds:
    ///   - no video frame for more than noFrameTimeoutSeconds (3 s):
    ///     failover with .watchdogNoFrames
    ///   - jitter: 3 frame gaps over 1.5 s inside a 30 s window (strikes
    ///     are recorded by the video routing task, counted here):
    ///     failover with .jitter
    /// Health driven triggers (alive == false, hingeClosed) are handled by
    /// the health task below, immediately, not on this tick. Every trigger
    /// calls the one failoverToPhone. Nothing else does.
    private func startWatchdog() {
        watchdogTask?.cancel()
        watchdogTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 250_000_000) // 250 ms tick
                guard let self else { return }
                guard self.mode == .glasses, !self.isFailingOver else { continue }

                // Prune jitter strikes outside the strike window first.
                let cutoff = Date().addingTimeInterval(-Config.jitterStrikeWindowSeconds)
                self.jitterStrikeTimestamps.removeAll { $0 < cutoff }
                if self.jitterStrikeTimestamps.count >= Config.jitterStrikeLimit {
                    await self.failoverToPhone(reason: .jitter)
                    return
                }

                // No frame timeout. lastGlassesVideoAt is stamped by the
                // video routing task on the monotonic clock.
                if let last = self.lastGlassesVideoAt,
                   self.nowUptime() - last > Config.noFrameTimeoutSeconds {
                    await self.failoverToPhone(reason: .watchdogNoFrames)
                    return
                }
            }
        }
    }

    /// Consumes glasses health. Two immediate triggers:
    ///   - hingeClosed == true: the temples were folded. Failover with
    ///     .hingeClosed, never a crash, never a wait for the frame timeout.
    ///   - alive == false: the source reported itself dead (stream error,
    ///     disconnect, unpair; the specific cause rides in health.detail
    ///     for the logs). Failover with .sessionError.
    /// Only acts once glasses are actually live; during an attach attempt a
    /// dead report is handled by the attach flow itself, which simply stays
    /// on the phone.
    private func startGlassesHealthTask() {
        glassesHealthTask?.cancel()
        let source = glassesLikeSource
        glassesHealthTask = Task { [weak self] in
            for await health in source.health {
                guard let self else { return }
                guard self.mode == .glasses, self.isGlassesAttached else { continue }
                if health.hingeClosed {
                    await self.failoverToPhone(reason: .hingeClosed)
                    return
                }
                if !health.alive {
                    NSLog("SessionManager: glasses health dead: %@", health.detail)
                    await self.failoverToPhone(reason: .sessionError)
                    return
                }
            }
        }
    }

    // MARK: - Routing tasks

    /// Starts routing for whichever mode is current. Used at session start;
    /// attach and failover manage their own task transitions directly.
    private func startRoutingTasks() {
        if mode == .glasses {
            startGlassesRoutingTasks()
            startGlassesHealthTask()
            startWatchdog()
            // Phone runs as hot standby in glasses mode too.
            if phoneRunning {
                startPhoneAudioTask()
            }
        } else {
            startPhoneAudioTask()
        }
    }

    private func startActiveSourceIfNeeded() async throws {
        if mode == .glasses {
            if !glassesRunning {
                try await glassesLikeSource.start()
                glassesRunning = true
                isGlassesAttached = true
            }
            if !phoneRunning {
                // Hot standby for the audio fallback rule below. A failure
                // here is non-fatal while glasses are healthy.
                do {
                    try await phoneSource.start()
                    phoneRunning = true
                } catch {
                    phoneRunning = false
                }
            }
        } else {
            if !phoneRunning {
                try await phoneSource.start()
                phoneRunning = true
            }
        }
    }

    /// Glasses routing: video feeds liveness and jitter accounting only
    /// (frames themselves are not forwarded by the bridge; the server works
    /// from audio plus stills). Audio feeds PTT, subject to the rule below.
    ///
    /// AUDIO ROUTING RULE (plan decision, read carefully):
    /// The phone mic is the audio of record whenever glasses audio is not
    /// actually flowing. Glasses chunks are preferred, but if no glasses
    /// audio chunk has arrived for more than 3 s while in glasses mode, the
    /// phone audio task below starts feeding PTT from the phone mic, chunk
    /// by chunk, until glasses audio resumes. The switch is per chunk and
    /// silent: PTT just sees one continuous chunk stream, and chunk origin
    /// tags preserve provenance for anything downstream that cares.
    private func startGlassesRoutingTasks() {
        let source = glassesLikeSource

        glassesVideoTask?.cancel()
        glassesVideoTask = Task { [weak self] in
            for await frame in source.video {
                guard let self else { return }
                let previousTimestamp = self.lastGlassesVideoFrameTimestamp
                self.lastGlassesVideoFrameTimestamp = frame.timestamp
                self.lastGlassesVideoAt = self.nowUptime()

                // Jitter accounting: a gap between consecutive frame
                // timestamps over 1.5 s is a strike. The watchdog counts
                // strikes inside the window and fails over at the limit.
                if let previousTimestamp {
                    let gap = frame.timestamp.timeIntervalSince(previousTimestamp)
                    if gap > 1.5 {
                        self.jitterStrikeTimestamps.append(Date())
                    }
                }
            }
        }

        glassesAudioTask?.cancel()
        glassesAudioTask = Task { [weak self] in
            for await chunk in source.audio {
                guard let self else { return }
                self.lastGlassesAudioAt = self.nowUptime()
                // Glasses audio is flowing, so it wins: feed PTT directly.
                self.ptt.feed(chunk)
            }
        }
    }

    private func stopGlassesRoutingTasks() {
        glassesVideoTask?.cancel()
        glassesVideoTask = nil
        glassesAudioTask?.cancel()
        glassesAudioTask = nil
    }

    /// Phone audio routing. Behavior depends on mode:
    ///   - phone mode: every chunk feeds PTT. The phone is the only source.
    ///   - glasses mode: chunks feed PTT only while glasses audio has been
    ///     silent for more than 3 s (the audio of record rule documented in
    ///     startGlassesRoutingTasks). While glasses audio is flowing, phone
    ///     chunks are dropped, never mixed, so PTT never sees double audio.
    private func startPhoneAudioTask() {
        phoneAudioTask?.cancel()
        let source = phoneSource
        phoneAudioTask = Task { [weak self] in
            for await chunk in source.audio {
                guard let self else { return }
                if self.mode == .glasses {
                    let glassesSilentFor = self.lastGlassesAudioAt.map { self.nowUptime() - $0 } ?? .greatestFiniteMagnitude
                    guard glassesSilentFor > Config.noFrameTimeoutSeconds else { continue }
                    self.ptt.feed(chunk)
                } else {
                    self.ptt.feed(chunk)
                }
            }
        }
    }

    private func stopAllTasks() {
        stopGlassesRoutingTasks()
        glassesHealthTask?.cancel()
        glassesHealthTask = nil
        phoneAudioTask?.cancel()
        phoneAudioTask = nil
        watchdogTask?.cancel()
        watchdogTask = nil
        photoTask?.cancel()
        photoTask = nil
        autoReattachTask?.cancel()
        autoReattachTask = nil
    }

    /// Races a source stop against a 1 s deadline. Used for glasses
    /// teardown in the failover path, where a hung stop must never block
    /// the phone taking over.
    private func stopSourceWithTimeout(_ source: CaptureSource) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask {
                await source.stop()
            }
            group.addTask {
                try? await Task.sleep(nanoseconds: 1_000_000_000) // 1 s cap
            }
            // First finisher wins; cancel the loser (the sleep, normally,
            // or abandon the hung stop, which is the point of the timeout).
            _ = await group.next()
            group.cancelAll()
        }
    }

    // MARK: - Photos

    /// Cadence loop: one still every photoCadenceSeconds (2.5 s) from the
    /// ACTIVE source, so a failover retargets the very next photo from
    /// glasses to phone camera with no timer restart and no code branch at
    /// the call site. Note the protocol stamps photo frames with the
    /// sentinel segment index (see BinaryFrameHeader.photoSegIdx): photos
    /// are session scoped, not segment scoped.
    private func startPhotoCadence() {
        photoTask?.cancel()
        photoTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Config.photoCadenceSeconds * 1_000_000_000))
                guard !Task.isCancelled else { return }
                guard let self else { return }
                guard self.currentSessionID != nil else { continue }
                // Cadence photos only make sense while a conversation is
                // live. Paused means not capturing, photos included.
                guard self.machine.state == .armed || self.machine.state == .recording else { continue }
                await self.captureAndSendPhoto(eager: false)
            }
        }
    }

    /// Takes one still from the active source and sends it: a photo_meta
    /// control message first, then the binary photo frame, so the server
    /// can attach timing context before the JPEG arrives.
    /// `photoID` is set only for server requested stills (capture_now),
    /// where the server expects its own ID echoed back; self initiated
    /// stills get a fresh UUID here.
    /// Failures are swallowed on purpose: a missed still is a gap in visual
    /// context, never a reason to disturb the conversation or the session.
    func captureAndSendPhoto(eager: Bool, photoID: String? = nil) async {
        guard currentSessionID != nil else { return }
        let source = activeCaptureSource
        guard let still = try? await source.captureStill(eager: eager) else { return }
        photoFrameSeq &+= 1
        photoCount += 1
        let ts = captureTsMs()
        sendPhotoMeta(photoID: photoID ?? UUID().uuidString,
                      seq: photoFrameSeq,
                      capturedAt: still.timestamp,
                      eager: eager,
                      byteCount: still.jpeg.count)
        ws.sendPhotoFrame(frameSeq: photoFrameSeq, captureTsMs: ts, jpeg: still.jpeg)
    }

    // MARK: - Machine mirror

    private func syncMachineMirror() {
        conversationState = machine.state
        if let id = machine.sessionID {
            currentSessionID = id
        }
        // When the machine reports no session ID there is nothing to mirror;
        // endSession() clears currentSessionID itself once its sends finish.
    }

    // MARK: - WSClient call sites
    //
    // Every ControlMessage construction lives here and only here, using the
    // static constructors in Net/WireProtocol.swift. Segment indices come
    // from PTTController (see the wire notes at the top of this file).

    private func sendSessionStart() {
        guard let sessionID = currentSessionID else { return }
        ws.sendControl(.sessionStart(sessionID: sessionID,
                                     startedAt: sessionStartedAt ?? Date(),
                                     talkMode: "push_to_talk"))
    }

    private func sendTalkStart() {
        guard let sessionID = currentSessionID else { return }
        ws.sendControl(.talkStart(sessionID: sessionID,
                                  segIdx: ptt.currentSegmentIndex,
                                  t0MonoMs: UInt64(segmentStartTsMs)))
    }

    private func sendTalkEnd(segIdx: UInt32) {
        guard let sessionID = currentSessionID else { return }
        let framesThisSegment = frameCounter.frames - segmentStartFrameCount
        let nowMs = captureTsMs()
        let durationMs = Int(nowMs >= segmentStartTsMs ? nowMs - segmentStartTsMs : 0)
        ws.sendControl(.talkEnd(sessionID: sessionID,
                                segIdx: segIdx,
                                t1MonoMs: UInt64(nowMs),
                                durationMs: durationMs,
                                framesSent: framesThisSegment))
    }

    private func sendPhotoMeta(photoID: String, seq: UInt32, capturedAt: Date, eager: Bool, byteCount: Int) {
        guard let sessionID = currentSessionID else { return }
        ws.sendControl(.photoMeta(sessionID: sessionID,
                                  photoID: photoID,
                                  seq: seq,
                                  capturedAt: capturedAt,
                                  eager: eager,
                                  bytes: byteCount))
    }

    private func sendTagMoment(source: String) {
        guard let sessionID = currentSessionID else { return }
        ws.sendControl(.tagMoment(sessionID: sessionID,
                                  tMonoMs: UInt64(captureTsMs()),
                                  source: source))
    }

    private func sendSessionPause() {
        guard let sessionID = currentSessionID else { return }
        ws.sendControl(.sessionPause(sessionID: sessionID))
    }

    private func sendSessionResume() {
        guard let sessionID = currentSessionID else { return }
        ws.sendControl(.sessionResume(sessionID: sessionID))
    }

    private func sendSessionEnd(sessionID: UUID, stats: ControlMessage.SessionStats) {
        ws.sendControl(.sessionEnd(sessionID: sessionID,
                                   endedAt: Date(),
                                   reason: "user",
                                   stats: stats))
    }
}
