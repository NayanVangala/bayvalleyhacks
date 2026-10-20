//
//  WSClient.swift
//  InsightBridge
//
//  WebSocket client for the OnRecord laptop server (protocol v1).
//  Endpoint: ws://<host>:<port>/ingest/audio?session_id=<uuid>
//  Primary transport is the laptop-hosted Wi-Fi network. Venue Wi-Fi
//  is never trusted; when the socket is down, every payload is staged
//  to disk via OfflineStaging and replayed in order on reconnect.
//
//  Behavior summary:
//  - connect sends hello (with resume_session_id on reconnects), then
//    replays staged items in original order before new live sends.
//  - Ping every 15 s. If no pong arrives for over 30 s, the connection
//    is treated as dropped and the reconnect loop takes over.
//  - Reconnect backoff: 1, 2, 4, 8, 16, 30 s cap, plus or minus 25% jitter.
//  - A fatal server error disconnects and does not auto-reconnect.
//
//  Pure Foundation (URLSessionWebSocketTask). No third party code.
//

import Foundation

public final class WSClient {

    // MARK: - Configuration and identity (set before connect)

    public let host: String
    public let port: Int

    /// Values sent in the hello control message.
    public var appVersion: String = "1.0.0"
    public var deviceID: String = UUID().uuidString
    public var glassesModel: String = "Ray-Ban Meta Display"
    public var transport: String = "laptop_wifi"
    public var audioSource: String = "glasses"

    // MARK: - Callbacks

    /// Called with the photo_id of every server capture_now message.
    public var onCaptureNow: ((String) -> Void)?
    /// Called whenever connectivity flips. True means the socket is up
    /// and hello has been sent.
    public var onConnectionChange: ((Bool) -> Void)?

    // MARK: - Observable state

    public private(set) var isConnected: Bool = false

    /// Estimated server clock minus local clock, in milliseconds.
    /// Updated on every pong. Zero until the first pong arrives.
    public private(set) var clockOffsetMs: Int = 0

    /// Total staged items successfully replayed since this client was
    /// created. Feed this into session_end stats as chunks_replayed.
    public private(set) var chunksReplayed: Int = 0

    /// Number of payloads currently waiting in offline staging.
    public var stagedReplayCount: Int {
        staging?.count ?? 0
    }

    /// Number of staged payloads evicted by the ring cap.
    /// Feed this into session_end stats as chunks_dropped.
    public var stagedDropCount: Int {
        staging?.evictions ?? 0
    }

    /// The session this client is bound to, if connect was called.
    public var sessionID: UUID? {
        lock.lock()
        defer { lock.unlock() }
        return currentSessionID
    }

    // MARK: - Internals

    private enum QueuedItem {
        case control(Data)
        case frame(Data)
    }

    private let urlSession: URLSession
    private var socketTask: URLSessionWebSocketTask?
    private var staging: OfflineStaging?

    private let sendQueue = DispatchQueue(label: "com.onrecord.wsclient.send")
    private let lock = NSLock()

    private var currentSessionID: UUID?
    private var sessionStartUptime: TimeInterval?
    private var intentionalDisconnect = false
    private var hasConnectedBefore = false
    private var reconnectAttempt = 0
    private var isReplaying = false
    private var liveQueue: [QueuedItem] = []
    private var lastPongAt: Date?

    private var receiveTask: Task<Void, Never>?
    private var pingTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private var replayTask: Task<Void, Never>?

    public init(host: String, port: Int) {
        self.host = host
        self.port = port
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 30
        self.urlSession = URLSession(configuration: config)
    }

    deinit {
        receiveTask?.cancel()
        pingTask?.cancel()
        reconnectTask?.cancel()
        replayTask?.cancel()
        socketTask?.cancel(with: .goingAway, reason: nil)
    }

    // MARK: - Connect / disconnect

    /// Opens the socket for `sessionID`, sends hello, starts the
    /// receive and ping loops, then replays anything staged on disk
    /// for this session. Failures do not throw: the reconnect loop
    /// keeps retrying in the background and sends are staged meanwhile.
    public func connect(sessionID: UUID) async {
        lock.lock()
        if currentSessionID != sessionID {
            currentSessionID = sessionID
            staging = OfflineStaging(sessionID: sessionID)
            sessionStartUptime = ProcessInfo.processInfo.systemUptime
        }
        intentionalDisconnect = false
        let alreadyConnected = isConnected
        lock.unlock()

        guard !alreadyConnected else { return }
        attemptConnect(isReconnect: hasConnectedBefore)
    }

    /// Stops the socket and every background loop. Staged items stay
    /// on disk for the next connect. No auto-reconnect after this call.
    public func disconnect() {
        lock.lock()
        intentionalDisconnect = true
        lock.unlock()

        reconnectTask?.cancel()
        reconnectTask = nil
        receiveTask?.cancel()
        receiveTask = nil
        pingTask?.cancel()
        pingTask = nil
        replayTask?.cancel()
        replayTask = nil

        socketTask?.cancel(with: .normalClosure, reason: nil)
        socketTask = nil
        setConnected(false)
    }

    // MARK: - Send API

    public func sendControl(_ message: ControlMessage) {
        guard let data = try? message.jsonData() else {
            NSLog("WSClient: failed to encode control message type %@", message.type)
            return
        }
        dispatch(.control(data))
    }

    public func sendAudioFrame(segIdx: UInt32, frameSeq: UInt32, captureTsMs: UInt32, flags: UInt8, pcm: Data) {
        let header = BinaryFrameHeader(
            kind: .audio,
            flags: flags,
            segIdx: segIdx,
            frameSeq: frameSeq,
            captureTsMs: captureTsMs
        )
        dispatch(.frame(header.encodedFrame(payload: pcm)))
    }

    public func sendPhotoFrame(frameSeq: UInt32, captureTsMs: UInt32, jpeg: Data) {
        let header = BinaryFrameHeader(
            kind: .photo,
            flags: 0,
            segIdx: BinaryFrameHeader.photoSegIdx,
            frameSeq: frameSeq,
            captureTsMs: captureTsMs
        )
        dispatch(.frame(header.encodedFrame(payload: jpeg)))
    }

    // MARK: - Send routing

    /// Live sends go straight to the socket. While a replay is in
    /// flight they queue in memory so staged items keep their order
    /// advantage. While disconnected they are written to staging.
    private func dispatch(_ item: QueuedItem) {
        lock.lock()
        if isReplaying {
            liveQueue.append(item)
            lock.unlock()
            return
        }
        let connected = isConnected
        lock.unlock()

        guard connected else {
            stage(item)
            return
        }
        socketSend(item) { [weak self] error in
            guard let self, error != nil else { return }
            // The send failed, so the payload was not delivered.
            // Preserve it on disk, then let the drop handler reconnect.
            self.stage(item)
            self.handleDrop()
        }
    }

    private func stage(_ item: QueuedItem) {
        lock.lock()
        let staging = self.staging
        let noSession = currentSessionID == nil
        if noSession {
            // No session yet, so there is no staging directory to write
            // to. Hold in memory; connect flushes the live queue after
            // its first replay pass.
            liveQueue.append(item)
        }
        lock.unlock()

        guard !noSession, let staging else { return }
        switch item {
        case .control(let data): staging.stageControl(data)
        case .frame(let data): staging.stageFrame(data)
        }
    }

    /// Issues one socket send on the serial send queue so frames keep
    /// their call order on the wire.
    private func socketSend(_ item: QueuedItem, completion: @escaping (Error?) -> Void) {
        sendQueue.async { [weak self] in
            guard let self, let task = self.socketTask else {
                completion(NSError(domain: "WSClient", code: 1, userInfo: [NSLocalizedDescriptionKey: "socket not open"]))
                return
            }
            let message: URLSessionWebSocketTask.Message
            switch item {
            case .control(let data):
                message = .string(String(decoding: data, as: UTF8.self))
            case .frame(let data):
                message = .data(data)
            }
            task.send(message) { error in
                completion(error)
            }
        }
    }

    private func socketSendAsync(_ item: QueuedItem) async -> Error? {
        await withCheckedContinuation { continuation in
            socketSend(item) { error in
                continuation.resume(returning: error)
            }
        }
    }

    // MARK: - Connection lifecycle

    private func attemptConnect(isReconnect: Bool) {
        guard let sessionID = currentSessionID else { return }

        var components = URLComponents()
        components.scheme = "ws"
        components.host = host
        components.port = port
        components.path = "/ingest/audio"
        components.queryItems = [URLQueryItem(name: "session_id", value: sessionID.uuidString)]
        guard let url = components.url else {
            scheduleReconnect()
            return
        }

        let task = urlSession.webSocketTask(with: url)
        socketTask = task
        task.resume()

        lock.lock()
        lastPongAt = Date()
        // reconnectAttempt is NOT reset here: attemptConnect runs on
        // every retry, so resetting here pinned the backoff at 1 s
        // forever. It resets on welcome (see handleServerMessage),
        // which is the first sign a reconnect actually worked.
        lock.unlock()

        setConnected(true)
        startReceiveLoop()
        startPingLoop()

        let hello = ControlMessage.hello(
            sessionID: sessionID,
            deviceID: deviceID,
            glassesModel: glassesModel,
            transport: transport,
            audioSource: audioSource,
            resumeSessionID: (isReconnect || hasConnectedBefore) ? sessionID : nil,
            appVersion: appVersion
        )
        hasConnectedBefore = true

        if let helloData = try? hello.jsonData() {
            socketSend(.control(helloData)) { [weak self] error in
                if error != nil {
                    self?.handleDrop()
                }
            }
        }

        startReplay()
    }

    private func setConnected(_ value: Bool) {
        lock.lock()
        let changed = isConnected != value
        isConnected = value
        lock.unlock()
        if changed {
            onConnectionChange?(value)
        }
    }

    private func handleDrop() {
        lock.lock()
        let intentional = intentionalDisconnect
        let wasConnected = isConnected
        lock.unlock()
        guard !intentional else { return }

        receiveTask?.cancel()
        receiveTask = nil
        pingTask?.cancel()
        pingTask = nil
        replayTask?.cancel()
        replayTask = nil

        socketTask?.cancel(with: .abnormalClosure, reason: nil)
        socketTask = nil

        if wasConnected {
            setConnected(false)
        }
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        lock.lock()
        let intentional = intentionalDisconnect
        let sessionKnown = currentSessionID != nil
        lock.unlock()
        guard !intentional, sessionKnown else { return }
        guard reconnectTask == nil else { return }

        lock.lock()
        reconnectAttempt += 1
        let attempt = reconnectAttempt
        lock.unlock()

        // 1, 2, 4, 8, 16, 30, 30, ... seconds, plus or minus 25%
        // jitter (PROTOCOL.md section 6 caps the backoff at 30 s).
        let base = min(30.0, pow(2.0, Double(attempt - 1)))
        let delay = base * Double.random(in: 0.75...1.25)

        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            self.lock.lock()
            self.reconnectTask = nil
            let stillIntentional = self.intentionalDisconnect
            let stillConnected = self.isConnected
            self.lock.unlock()
            guard !stillIntentional, !stillConnected else { return }
            self.attemptConnect(isReconnect: true)
        }
    }

    // MARK: - Replay

    private func startReplay() {
        replayTask?.cancel()
        replayTask = Task { [weak self] in
            await self?.replayStaged()
        }
    }

    /// Sends staged items oldest first, deleting each file only after
    /// its send succeeds. Live sends arriving during replay wait in
    /// the live queue and flush after the last staged item.
    private func replayStaged() async {
        guard let staging else {
            flushLiveQueue()
            return
        }

        lock.lock()
        isReplaying = true
        lock.unlock()

        let items = staging.pendingItems()
        for item in items {
            if Task.isCancelled { break }
            guard isConnected else { break }
            guard let data = try? Data(contentsOf: item.url) else {
                // Unreadable file: remove it so replay cannot wedge on
                // it forever. The bytes are already lost at the OS
                // level; the eviction style accounting does not apply.
                staging.markSent(seq: item.seq)
                continue
            }
            let queued: QueuedItem = (item.kind == .control) ? .control(data) : .frame(data)
            let error = await socketSendAsync(queued)
            if let error {
                NSLog("WSClient: replay stopped at seq %d: %@", item.seq, String(describing: error))
                break
            }
            staging.markSent(seq: item.seq)
            lock.lock()
            chunksReplayed += 1
            lock.unlock()
        }

        lock.lock()
        isReplaying = false
        lock.unlock()

        flushLiveQueue()
    }

    private func flushLiveQueue() {
        lock.lock()
        let queued = liveQueue
        liveQueue.removeAll()
        lock.unlock()
        for item in queued {
            dispatch(item)
        }
    }

    // MARK: - Receive loop

    private func startReceiveLoop() {
        receiveTask?.cancel()
        receiveTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                guard let task = self.socketTask else { return }
                do {
                    let message = try await task.receive()
                    self.handle(message)
                } catch {
                    if !Task.isCancelled {
                        NSLog("WSClient: receive failed: %@", String(describing: error))
                        self.handleDrop()
                    }
                    return
                }
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .string(let text):
            handleServerMessage(ServerMessage.parse(text))
        case .data(let data):
            // The server speaks JSON control only. Binary downlink is
            // not part of protocol v1, so log and ignore.
            NSLog("WSClient: ignoring %d byte binary downlink frame", data.count)
        @unknown default:
            break
        }
    }

    private func handleServerMessage(_ message: ServerMessage) {
        switch message {
        case .welcome:
            lock.lock()
            lastPongAt = Date()
            reconnectAttempt = 0
            lock.unlock()
        case .pong(_, let serverTime):
            lock.lock()
            lastPongAt = Date()
            lock.unlock()
            updateClockOffset(serverTime: serverTime)
        case .captureNow(let photoID):
            onCaptureNow?(photoID)
        case .error(let code, let text, let fatal):
            if fatal {
                NSLog("WSClient: fatal server error %@: %@", code, text)
                disconnect()
            } else {
                NSLog("WSClient: server error %@: %@", code, text)
            }
        case .unknown(let raw):
            NSLog("WSClient: unknown server message: %@", raw)
        }
    }

    /// Clock offset estimate: server time minus local time at the
    /// moment the pong was handled. Ignores half the round trip, which
    /// is fine for the sub-second display and logging uses here.
    private func updateClockOffset(serverTime: String?) {
        guard let serverTime, let serverDate = parseServerTime(serverTime) else { return }
        let offset = Int(serverDate.timeIntervalSince(Date()) * 1000)
        lock.lock()
        clockOffsetMs = offset
        lock.unlock()
    }

    private func parseServerTime(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: value) { return date }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let date = plain.date(from: value) { return date }
        if let epoch = Double(value) {
            // Numeric epoch: values above 1e11 are milliseconds,
            // values above 1e9 are seconds.
            if epoch > 100_000_000_000 { return Date(timeIntervalSince1970: epoch / 1000) }
            if epoch > 1_000_000_000 { return Date(timeIntervalSince1970: epoch) }
        }
        return nil
    }

    // MARK: - Ping loop

    private func startPingLoop() {
        pingTask?.cancel()
        pingTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                if Task.isCancelled { return }
                guard self.isConnected else { return }

                // Watchdog: a pong gap over 30 s means the link is
                // dead even if the socket has not noticed yet.
                self.lock.lock()
                let lastPong = self.lastPongAt
                self.lock.unlock()
                if let lastPong, Date().timeIntervalSince(lastPong) > 30 {
                    NSLog("WSClient: no pong for over 30 s, treating as drop")
                    self.handleDrop()
                    return
                }

                self.sendPing()
            }
        }
    }

    private func sendPing() {
        // Pings bypass offline staging on purpose: a stale ping has no
        // replay value, and staging it would corrupt replay order.
        let ping = ControlMessage.ping(sessionID: currentSessionID, tMonoMs: monoNowMs())
        guard let data = try? ping.jsonData() else { return }
        socketSend(.control(data)) { [weak self] error in
            if error != nil {
                self?.handleDrop()
            }
        }
    }

    /// Milliseconds since this session started, from the monotonic
    /// system clock. Zero if connect has not run yet.
    private func monoNowMs() -> UInt64 {
        lock.lock()
        let start = sessionStartUptime
        lock.unlock()
        guard let start else { return 0 }
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        return UInt64(max(0, elapsed) * 1000)
    }
}
