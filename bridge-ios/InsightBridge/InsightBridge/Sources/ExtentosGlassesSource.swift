// ExtentosGlassesSource.swift
// InsightBridge
//
// CaptureSource backed by Meta glasses through the Extentos iOS SDK
// (swift-glasses 2.8.0, product GlassesCore), replacing the raw DAT
// SDK path in GlassesSource.swift. GlassesSource.swift is NOT deleted
// by this change; it stays for reference until the Mac compile gate.
//
// This file and (chunk A) Sources/ExtentosRegistration.swift are the
// ONLY files in the app allowed to import GlassesCore. Every type that
// leaves this file is an SDK-free type from Common/MediaTypes.swift
// (AudioChunk, VideoFrame, SourceHealth, StillPhoto), so downstream
// code never sees an SDK type.
//
// Name disambiguation: the SDK's hot-path carriers are literally named
// VideoFrame and AudioChunk, colliding with our internal types. Every
// SDK type is referenced with the GlassesCore. module prefix; the bare
// names always mean our internal MediaTypes.swift types.
//
// Session rule, unchanged from the DAT era: ONE ExtentosGlasses
// instance, ONE connect, ONE videoFrames subscription, ONE audioChunks
// subscription for the whole conversation. Never tear down and reopen
// streams to force recovery; the SDK docs warn this drops the
// connection. start() connects once, stop() disconnects once, and
// captureStill() reuses the live client.
//
// API provenance: every Extentos API name below was read verbatim from
// the swift-glasses 2.8.0 shell sources on 2026-10-21 (CameraClient,
// AudioClient, ConnectionClient, DisplayClient, ObservableState,
// Errors, TelemetryClient, ObservabilityClient, RuntimeClient,
// ToggleClient, VoiceClient). Anything not verifiable there carries a
// VERIFY-ON-MAC marker for the Xcode compile gate.

import AVFoundation
import CoreGraphics
import CoreVideo
import Foundation
import GlassesCore
import ImageIO
import UniformTypeIdentifiers
import os.log

enum ExtentosGlassesSourceError: Error, LocalizedError {
    case sessionNotStarted
    case connectFailed(String)
    case stillCaptureFailed(String)
    case photoBytesFailed(String)
    case jpegEncodingFailed(String)

    var errorDescription: String? {
        switch self {
        case .sessionNotStarted:
            return "Glasses connection is not started."
        case .connectFailed(let detail):
            return "Glasses connect failed: \(detail)"
        case .stillCaptureFailed(let detail):
            return "Glasses still capture failed: \(detail)"
        case .photoBytesFailed(let detail):
            return "Could not read photo bytes: \(detail)"
        case .jpegEncodingFailed(let detail):
            return "JPEG encoding failed: \(detail)"
        }
    }
}

/// One speaker card for the opt-in display helper. Kept app-side
/// because show() replaces the whole display every call.
struct SpeakerCard {
    var name: String
    var detail: String
}

final class ExtentosGlassesSource: CaptureSource {

    // MARK: - CaptureSource surface

    let origin: CaptureOrigin = .glasses

    /// PCM16 16 kHz mono, 100 ms chunks. See handleExtentosAudioChunk.
    let audio: AsyncStream<AudioChunk>

    /// Decoded glasses frames, unthrottled. Downstream owns throttling
    /// (photo cadence, stills). Dropping frames here would hide link
    /// quality from the watchdog, so this class drops nothing except
    /// frames it cannot decode (raw plane layout, unverified).
    let video: AsyncStream<VideoFrame>

    /// Health snapshots, emitted on every connection state change, on
    /// every camera stream state change, on every SDK error, and on a
    /// 500 ms heartbeat timer so that lastFrameAge keeps growing
    /// visibly when frames stall.
    let health: AsyncStream<SourceHealth>

    // MARK: - Configuration

    /// Device id label, kept for the debug UI. The typed DeviceId
    /// conversion for manual device selection is chunk A's job;
    /// this source always connects with deviceId nil.
    let deviceID: String?

    /// The ONE ExtentosGlasses instance for the app lifetime, created
    /// by ExtentosRegistration (chunk A) and injected here. Tests
    /// inject FakeExtentosGlasses (see the test double section below).
    private let glasses: any ExtentosGlasses

    /// Health heartbeat cadence: 500 ms. Config.swift gives the
    /// watchdog a 3.0 s no-frame timeout, so this heartbeat lands
    /// about six health updates inside that window while frames are
    /// stalled, each one carrying a larger lastFrameAge.
    private static let healthIntervalNanoseconds: UInt64 = 500_000_000

    /// Delay between videoFrames resubscribes after a temple-tap
    /// pause (CameraStreamPaused). The wearer resumes with a tap;
    /// the pump stays alive until frames flow again.
    private static let pauseRetryNanoseconds: UInt64 = 2_000_000_000

    // MARK: - Stream continuations

    private let audioContinuation: AsyncStream<AudioChunk>.Continuation
    private let videoContinuation: AsyncStream<VideoFrame>.Continuation
    private let healthContinuation: AsyncStream<SourceHealth>.Continuation

    // MARK: - Session state

    private var pumpTasks: [Task<Void, Never>] = []
    private var healthTask: Task<Void, Never>?
    private var isStarted = false

    /// Most recent health snapshot, kept for the debug UI and for
    /// tests that do not want to race the health stream.
    private(set) var latestHealth: SourceHealth?

    // MARK: - Mutable capture state (guarded by stateLock)

    private let stateLock = NSLock()
    private var lastFrameDate: Date?
    private var lastHingeClosed = false
    private var lastAlive = false
    private var lastDetail = "not started"

    /// Next audio sequence number. Monotonically increasing for the
    /// lifetime of this source, across stop and start cycles, so the
    /// server can order chunks and spot gaps honestly.
    private var nextAudioSeq: UInt64 = 0

    /// Converted PCM waiting to fill a full 3200 byte chunk.
    private var pendingPCM = Data()

    /// Cached converter for the current glasses audio format. Recreated
    /// whenever the incoming sample rate changes.
    private var audioConverter: AVAudioConverter?
    private var audioConverterInputFormat: AVAudioFormat?

    /// First-frame anchor for presentationTimeUs to Date conversion.
    /// Reset on every start().
    private var firstFrameAnchor: (ptsUs: Int64, date: Date)?

    /// Set once the armed stream config has been logged.
    private var didLogStreamInfo = false

    private let logger = Logger(subsystem: "com.nayan.insightbridge", category: "ExtentosGlassesSource")

    // MARK: - Init

    /// - Parameters:
    ///   - glasses: the shared ExtentosGlasses instance (chunk A owns
    ///     its creation). Inject FakeExtentosGlasses for drills and
    ///     tests; the exact same source code runs with no glasses.
    ///   - deviceID: informational label only for now.
    init(glasses: any ExtentosGlasses, deviceID: String? = nil) {
        self.glasses = glasses
        self.deviceID = deviceID

        var audioCont: AsyncStream<AudioChunk>.Continuation!
        self.audio = AsyncStream<AudioChunk> { continuation in
            audioCont = continuation
        }
        self.audioContinuation = audioCont

        var videoCont: AsyncStream<VideoFrame>.Continuation!
        self.video = AsyncStream<VideoFrame> { continuation in
            videoCont = continuation
        }
        self.videoContinuation = videoCont

        var healthCont: AsyncStream<SourceHealth>.Continuation!
        self.health = AsyncStream<SourceHealth> { continuation in
            healthCont = continuation
        }
        self.healthContinuation = healthCont
    }

    deinit {
        healthTask?.cancel()
        pumpTasks.forEach { $0.cancel() }
        audioContinuation.finish()
        videoContinuation.finish()
        healthContinuation.finish()
    }

    // MARK: - Start and stop

    /// Connects once and subscribes to video, audio, connection state,
    /// and camera stream state for the whole conversation. Calling
    /// start() twice is a no-op: the live connection is kept.
    ///
    /// connect() is NOT idempotent, so exactly one call happens here,
    /// behind the isStarted guard. Retries go through reconnect()
    /// (SessionManager's path), never by re-running start().
    func start() async throws {
        guard !isStarted else {
            logger.info("ExtentosGlassesSource.start called while already started. Keeping the existing connection open.")
            return
        }

        // VERIFY-ON-MAC: the connect() no-arg overload is an SDK
        // extension on ConnectionClient that passes deviceId: nil.
        let connectResult = await glasses.connection.connect()
        switch connectResult {
        case .success:
            break
        case .failure(let connectError):
            let detail = "connectFailed: \(String(describing: connectError))"
            stateLock.lock()
            lastAlive = false
            lastDetail = detail
            stateLock.unlock()
            emitHealth()
            logger.error("Glasses connect failed: \(String(describing: connectError), privacy: .public).")
            throw ExtentosGlassesSourceError.connectFailed(String(describing: connectError))
        }

        isStarted = true
        didLogStreamInfo = false
        stateLock.lock()
        firstFrameAnchor = nil
        lastHingeClosed = false
        stateLock.unlock()

        pumpTasks = [
            Task { [weak self] in
                guard let self else { return }
                await self.runConnectionPump()
            },
            Task { [weak self] in
                guard let self else { return }
                await self.runVideoPump()
            },
            Task { [weak self] in
                guard let self else { return }
                await self.runAudioPump()
            },
            Task { [weak self] in
                guard let self else { return }
                await self.runCameraStatePump()
            },
        ]

        setHealthSnapshot(alive: true, detail: "connecting")
        emitHealth()
        startHealthHeartbeat()
        logger.info("Glasses connection is live. Streams stay open until stop() is called.")
    }

    /// Tears the connection down once. Best effort and non-blocking in
    /// spirit: SessionManager calls this on the failover path and
    /// immediately starts the phone source, so nothing here may hang.
    /// The stream continuations are deliberately NOT finished, so
    /// this source can be started again after a failover cooldown.
    func stop() async {
        healthTask?.cancel()
        healthTask = nil
        pumpTasks.forEach { $0.cancel() }
        pumpTasks.removeAll()

        await glasses.connection.disconnect()
        isStarted = false

        stateLock.lock()
        pendingPCM.removeAll(keepingCapacity: false)
        firstFrameAnchor = nil
        stateLock.unlock()

        setHealthSnapshot(alive: false, detail: "stopped")
        emitHealth()
        logger.info("Glasses connection stopped.")
    }

    // MARK: - Connection state pump

    private func runConnectionPump() async {
        // ObservableState.stream is a replaying stream (StateFlow
        // semantics), so subscribing after connect() still yields the
        // current state immediately.
        for await state in glasses.connection.state.stream {
            if Task.isCancelled {
                break
            }
            let snapshot = Self.connectionSnapshot(from: state)
            stateLock.lock()
            lastAlive = snapshot.connected
            lastHingeClosed = snapshot.hingeClosed
            lastDetail = snapshot.detail
            stateLock.unlock()
            emitHealth()
            logger.info("Glasses connection state: \(snapshot.detail, privacy: .public).")
        }
    }

    /// Maps the SDK connection state to (connected, hingeClosed,
    /// detail). Battery level has no Extentos counterpart; health
    /// carries nil until an API is found (open item, not a blocker).
    ///
    /// VERIFY-ON-MAC: r2 verified only GlassesState.notRegistered and
    /// .registered in the SDK source; the
    /// deviceDiscovered/connecting/active/disconnected(+cause) machine
    /// is r1 docs-level. Description matching keeps this compiling
    /// against the real case list; replace with typed case matches on
    /// the Mac.
    private static func connectionSnapshot(
        from state: GlassesCore.GlassesState
    ) -> (connected: Bool, hingeClosed: Bool, detail: String) {
        switch state {
        case .notRegistered, .registered:
            return (false, false, "not connected")
        default:
            let desc = String(describing: state).lowercased()
            if desc.contains("hinge") {
                // Hinge close is a health fact, never a crash and never
                // a silent drop. The watchdog reads hingeClosed and fails
                // over to the phone source.
                return (false, true, "hingesClosed")
            }
            if desc.contains("active") {
                return (true, false, "connected")
            }
            if desc.contains("disconnect") {
                return (false, false, "disconnected")
            }
            return (false, false, "connecting")
        }
    }

    // MARK: - Video pump

    private func runVideoPump() async {
        // Our standing request: low resolution ceiling, 2 fps, JPEG per
        // frame, drop-oldest backpressure. 2 fps is a DAT frame-rate tier
        // and matches the old ~1 fps style JPEG pipeline for Gemini.
        // Always read per-frame width/height: the SDK steps resolution
        // down with Bluetooth bandwidth inside the armed config.
        let config = GlassesCore.VideoFrameConfig(
            resolution: .low,
            frameRate: 2,
            codec: .hvc1,
            backpressure: .dropOldest
        )
        while !Task.isCancelled {
            do {
                for try await frame in glasses.camera.videoFrames(config: config) {
                    if Task.isCancelled {
                        break
                    }
                    handleExtentosFrame(frame)
                }
                if Task.isCancelled {
                    break
                }
                // A clean stream end without error is unexpected on real
                // glasses (a missing camera path throws CameraUnavailable
                // instead). Log and leave the retry loop.
                logger.warning("videoFrames stream ended without error. Stopping the video pump.")
                break
            } catch let paused as GlassesCore.CameraStreamPaused {
                // The wearer paused the camera with a temple tap before
                // stream start. This surfaces as paused health, NOT a
                // failover: alive stays true. The pump resubscribes until
                // the wearer taps again and frames flow.
                handleCameraPaused(paused)
                try? await Task.sleep(nanoseconds: Self.pauseRetryNanoseconds)
                continue
            } catch let unavailable as GlassesCore.CameraUnavailable {
                forwardError(unavailable, context: "camera unavailable")
                break
            } catch {
                forwardError(error, context: "video frames")
                break
            }
        }
    }

    private func handleCameraPaused(_ paused: GlassesCore.CameraStreamPaused) {
        stateLock.lock()
        // Paused health, not a failover: alive stays true. The 3 s
        // watchdog still watches lastFrameAge; SessionManager owns the
        // failover decision and its contract is unchanged.
        lastDetail = "cameraPausedByWearer"
        stateLock.unlock()
        logger.info("Camera paused by wearer (temple tap): \(paused.message, privacy: .public). Pump will resubscribe.")
        emitHealth()
    }

    private func handleExtentosFrame(_ frame: GlassesCore.VideoFrame) {
        // Always read per-frame width/height: resolution is a ceiling
        // and the transport steps it down with bandwidth.
        let width = frame.width
        let height = frame.height

        guard frame.isCompressed else {
            // VERIFY-ON-MAC: raw plane layout unverified. The SDK ships
            // the raw pre-encode pixel bytes for the .raw codec; r2 notes
            // they are likely planar YUV from the DAT camera path,
            // unconfirmed (NV12 vs BGRA not stated). On the Mac, capture
            // one raw frame and compare buffer.count against
            // width*height ratios (1.5x suggests NV12/I420, 4x suggests
            // BGRA) before writing the wrap. Until then the default
            // config stays .hvc1 and raw frames are dropped with a health
            // note, never a crash and never a guessed conversion.
            logger.warning("Dropping raw (uncompressed) glasses frame \(width, privacy: .public)x\(height, privacy: .public): plane layout unverified on this build.")
            stateLock.lock()
            if !lastHingeClosed, lastDetail == "streaming" {
                lastDetail = "rawFrameDroppedUnverified"
            }
            stateLock.unlock()
            emitHealth()
            return
        }

        guard let pixelBuffer = Self.pixelBuffer(fromJPEG: frame.buffer) else {
            logger.error("JPEG decode failed for one glasses frame. Frame dropped, session kept alive.")
            return
        }

        let timestamp = ptsToDate(presentationTimeUs: frame.presentationTimeUs)
        stateLock.lock()
        lastFrameDate = timestamp
        if !lastHingeClosed, lastDetail != "streaming" {
            // Frames flowing again after a pause: restore the detail.
            lastDetail = "streaming"
        }
        stateLock.unlock()

        if !didLogStreamInfo, let info = glasses.camera.activeStreamInfo() {
            // activeStreamInfo() exposes the armed resolution and
            // frameRate after first use; diagnostics only.
            didLogStreamInfo = true
            logger.info("Camera stream armed at resolution=\(String(describing: info.resolution), privacy: .public) frameRate=\(info.frameRate, privacy: .public). Per-frame size \(width, privacy: .public)x\(height, privacy: .public).")
        }

        videoContinuation.yield(VideoFrame(pixelBuffer: pixelBuffer, timestamp: timestamp, origin: .glasses))
    }

    /// Converts the SDK's microsecond presentation timestamp to Date
    /// with an anchor recorded on the first frame of the stream
    /// (receipt Date minus the first PTS). If the anchor math proves
    /// flaky on hardware, fall back to receipt time here; the watchdog
    /// only needs a monotonic age.
    private func ptsToDate(presentationTimeUs: Int64) -> Date {
        stateLock.lock()
        defer { stateLock.unlock() }
        if let anchor = firstFrameAnchor {
            return anchor.date.addingTimeInterval(TimeInterval(presentationTimeUs - anchor.ptsUs) / 1_000_000)
        }
        let now = Date()
        firstFrameAnchor = (ptsUs: presentationTimeUs, date: now)
        return now
    }

    /// Decodes one JPEG frame to a BGRA pixel buffer via CoreGraphics.
    /// Decode cost at 2 fps is negligible.
    private static func pixelBuffer(fromJPEG data: Data) -> CVPixelBuffer? {
        guard
            let source = CGImageSourceCreateWithData(data as CFData, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            return nil
        }
        var pixelBuffer: CVPixelBuffer?
        let attrs: [CFString: Any] = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
        ]
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            image.width,
            image.height,
            kCVPixelFormatType_32BGRA,
            attrs as CFDictionary,
            &pixelBuffer
        )
        guard status == kCVReturnSuccess, let buffer = pixelBuffer else {
            return nil
        }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else {
            return nil
        }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(buffer)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: base,
            width: image.width,
            height: image.height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
        ) else {
            return nil
        }
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return buffer
    }

    // MARK: - Camera stream state pump

    private func runCameraStatePump() async {
        // streamState() reports stream ACTIVITY (arming/streaming/paused/
        // stopping), orthogonal to the connection health. A mid-stream
        // temple-tap pause is not an error: frames simply stop and resume,
        // and this is the signal that says so.
        for await phase in glasses.camera.streamState() {
            if Task.isCancelled {
                break
            }
            let detail: String
            switch phase {
            case .idle:
                detail = "cameraIdle"
            case .arming:
                detail = "arming"
            case .streaming:
                detail = "streaming"
            case .paused:
                detail = "cameraPausedByWearer"
            case .stopping:
                detail = "cameraStopping"
            @unknown default:
                detail = "cameraUnknown"
            }
            stateLock.lock()
            // A temple-tap pause surfaces as paused health, NOT a
            // failover: alive stays true.
            if !lastHingeClosed {
                lastDetail = detail
            }
            stateLock.unlock()
            emitHealth()
        }
    }

    // MARK: - Audio pump

    private func runAudioPump() async {
        // 20 ms chunks, 16 kHz requested, drop-oldest backpressure.
        // Note the SDK default backpressure is .suspend(bufferSize: 16);
        // we pass .dropOldest explicitly to match the iOS guidance.
        let config = GlassesCore.AudioChunkConfig(
            chunkMillis: 20,
            sampleRate: 16000,
            backpressure: .dropOldest
        )
        for await chunk in glasses.audio.audioChunks(config: config) {
            if Task.isCancelled {
                break
            }
            handleExtentosAudioChunk(chunk)
        }
        logger.info("audioChunks stream ended.")
    }

    /// Accumulates 20 ms SDK chunks into the pipeline's 100 ms /
    /// 3200-byte chunk shape with monotonic sequence numbers.
    private func handleExtentosAudioChunk(_ chunk: GlassesCore.AudioChunk) {
        let pcm: Data
        if chunk.sampleRate == AudioFormat.sampleRate {
            pcm = chunk.samples
        } else {
            // VERIFY-ON-MAC: per-chunk sampleRate is the effective
            // platform rate (config.sampleRate is a request; the
            // platform audio path sets the effective rate, e.g. HFP
            // drops capture to the voice band while capture is active).
            // Read it on EVERY chunk; convert only when it differs.
            guard let converted = convertExtentosPCM(chunk.samples, fromSampleRate: chunk.sampleRate) else {
                logger.error("Audio resample failed for one glasses chunk at \(chunk.sampleRate, privacy: .public) Hz. Chunk dropped, session kept alive.")
                return
            }
            pcm = converted
        }

        // VERIFY-ON-MAC: timestampMs epoch base. Assumed Unix epoch
        // milliseconds; falls back to receipt time when not positive.
        let timestamp: Date
        if chunk.timestampMs > 0 {
            timestamp = Date(timeIntervalSince1970: TimeInterval(chunk.timestampMs) / 1000)
        } else {
            timestamp = Date()
        }

        var chunks: [AudioChunk] = []
        stateLock.lock()
        pendingPCM.append(pcm)
        while pendingPCM.count >= AudioFormat.frameBytes {
            let chunkData = Data(pendingPCM.prefix(AudioFormat.frameBytes))
            pendingPCM.removeFirst(AudioFormat.frameBytes)
            let seq = nextAudioSeq
            nextAudioSeq += 1
            chunks.append(
                AudioChunk(
                    seq: seq,
                    pcm: chunkData,
                    sampleRate: AudioFormat.sampleRate,
                    timestamp: timestamp,
                    origin: .glasses
                )
            )
        }
        stateLock.unlock()

        for outChunk in chunks {
            audioContinuation.yield(outChunk)
        }
    }

    /// Wraps signed 16-bit LE mono PCM bytes into an AVAudioPCMBuffer
    /// so the existing AVAudioConverter path can resample it to 16 kHz.
    private func wrapExtentosPCM16(_ data: Data, sampleRate: Int) -> AVAudioPCMBuffer? {
        guard
            let format = AVAudioFormat(
                commonFormat: .pcmFormatInt16,
                sampleRate: Double(sampleRate),
                channels: 1,
                interleaved: true
            )
        else {
            return nil
        }
        let frameCount = data.count / MemoryLayout<Int16>.size
        guard
            frameCount > 0,
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)),
            let channel = buffer.int16ChannelData
        else {
            return nil
        }
        data.withUnsafeBytes { raw in
            guard let src = raw.baseAddress else {
                return
            }
            channel[0].update(from: src.assumingMemoryBound(to: Int16.self), count: frameCount)
        }
        buffer.frameLength = AVAudioFrameCount(frameCount)
        return buffer
    }

    private func convertExtentosPCM(_ data: Data, fromSampleRate: Int) -> Data? {
        guard let buffer = wrapExtentosPCM16(data, sampleRate: fromSampleRate) else {
            return nil
        }
        return convertToPCM16Mono16kHz(buffer)
    }

    /// AVAudioConverter path from whatever sample rate the glasses
    /// deliver to the one format the whole pipeline speaks: PCM16,
    /// 16 kHz, mono, interleaved. Returns nil on conversion failure;
    /// the caller logs and drops that chunk rather than emitting a
    /// malformed one.
    private func convertToPCM16Mono16kHz(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: Double(AudioFormat.sampleRate),
            channels: 1,
            interleaved: true
        ) else {
            return nil
        }

        if Self.formatsMatch(buffer.format, targetFormat) {
            return pcm16Data(from: buffer)
        }

        stateLock.lock()
        if audioConverter == nil
            || audioConverterInputFormat == nil
            || !Self.formatsMatch(audioConverterInputFormat!, buffer.format) {
            audioConverter = AVAudioConverter(from: buffer.format, to: targetFormat)
            audioConverterInputFormat = buffer.format
        }
        guard let converter = audioConverter else {
            stateLock.unlock()
            return nil
        }
        stateLock.unlock()

        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
            return nil
        }

        var conversionError: NSError?
        var didConsumeInput = false
        let status = converter.convert(to: output, error: &conversionError) { _, outStatus in
            if didConsumeInput {
                outStatus.pointee = .noDataNow
                return nil
            }
            didConsumeInput = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, conversionError == nil else {
            if let conversionError {
                logger.error("AVAudioConverter failed: \(conversionError.localizedDescription, privacy: .public)")
            }
            return nil
        }
        return pcm16Data(from: output)
    }

    /// Compares the fields that matter for the converter cache.
    /// Avoids relying on AVAudioFormat equality conformance.
    private static func formatsMatch(_ a: AVAudioFormat, _ b: AVAudioFormat) -> Bool {
        a.sampleRate == b.sampleRate
            && a.channelCount == b.channelCount
            && a.commonFormat == b.commonFormat
            && a.isInterleaved == b.isInterleaved
    }

    /// Extracts interleaved PCM16 bytes from a converted buffer.
    private func pcm16Data(from buffer: AVAudioPCMBuffer) -> Data? {
        guard let channelData = buffer.int16ChannelData else {
            return nil
        }
        let byteCount = Int(buffer.frameLength) * MemoryLayout<Int16>.size
        guard byteCount > 0 else {
            return nil
        }
        return Data(bytes: channelData[0], count: byteCount)
    }

    // MARK: - Health

    /// SDK errors are never swallowed. Every error becomes a health
    /// update with alive = false and the error in detail, which is
    /// the signal SessionManager's watchdog fails over on.
    private func forwardError(_ error: Error, context: String) {
        let hinge = Self.isHingeError(error)
        stateLock.lock()
        lastAlive = false
        if hinge {
            lastHingeClosed = true
        }
        lastDetail = "\(context): \(error.localizedDescription)"
        stateLock.unlock()
        logger.error("Glasses error forwarded as health. context=\(context, privacy: .public) detail=\(error.localizedDescription, privacy: .public) hinge=\(hinge, privacy: .public).")
        emitHealth()
    }

    /// Hinge close arrives typed as CaptureError.hingesClosed where the
    /// SDK surfaces it; the description match is the fallback for
    /// untyped errors, kept from the DAT era.
    private static func isHingeError(_ error: Error) -> Bool {
        if let captureError = error as? GlassesCore.CaptureError,
           case .hingesClosed = captureError {
            return true
        }
        return String(describing: error).lowercased().contains("hinge")
    }

    private func setHealthSnapshot(alive: Bool, detail: String) {
        stateLock.lock()
        lastAlive = alive
        lastDetail = detail
        stateLock.unlock()
    }

    private func emitHealth() {
        stateLock.lock()
        let age: TimeInterval
        if let lastFrameDate {
            age = Date().timeIntervalSince(lastFrameDate)
        } else {
            // No frame yet. A large finite age reads clearly in
            // logs and trips the watchdog threshold, unlike 0, which
            // would look like a frame just arrived.
            age = 9999
        }
        let snapshot = SourceHealth(
            origin: .glasses,
            alive: lastAlive,
            batteryLevel: nil,
            hingeClosed: lastHingeClosed,
            lastFrameAge: age,
            detail: lastDetail
        )
        stateLock.unlock()
        latestHealth = snapshot
        healthContinuation.yield(snapshot)
    }

    private func startHealthHeartbeat() {
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.healthIntervalNanoseconds)
                guard !Task.isCancelled else {
                    return
                }
                self?.emitHealth()
            }
        }
    }

    // MARK: - Still capture

    /// Captures one still on the live client and returns it as a JPEG
    /// at 960 px long edge, quality 0.7, the exact shape downstream
    /// QR and identity code expects.
    ///
    /// `eager` marks captures taken at talk start or session start,
    /// where identity matters most. The flag rides through on
    /// StillPhoto untouched; capture behavior is identical either
    /// way, because the connection is already open.
    func captureStill(eager: Bool) async throws -> StillPhoto {
        guard isStarted else {
            throw ExtentosGlassesSourceError.sessionNotStarted
        }

        // VERIFY-ON-MAC: the default-config capturePhoto() overload is
        // an SDK extension on CameraClient; confirm on the Mac.
        let result = await glasses.camera.capturePhoto()
        switch result {
        case .success(let photo):
            do {
                let bytes = try await Self.photoBytes(from: photo)
                let jpeg = try Self.encodeJPEG(
                    fromImageData: bytes,
                    maxLongEdge: AudioFormat.photoLongEdgePx,
                    quality: AudioFormat.photoJPEGQuality
                )
                return StillPhoto(jpeg: jpeg, timestamp: Date(), origin: .glasses, eager: eager)
            } catch let error as ExtentosGlassesSourceError {
                throw error
            } catch {
                throw ExtentosGlassesSourceError.stillCaptureFailed(error.localizedDescription)
            }
        case .failure(let captureError):
            if case GlassesCore.CaptureError.hingesClosed = captureError {
                stateLock.lock()
                lastHingeClosed = true
                lastAlive = false
                lastDetail = "hingesClosed"
                stateLock.unlock()
                emitHealth()
            } else {
                forwardError(captureError, context: "still capture")
            }
            throw ExtentosGlassesSourceError.stillCaptureFailed(String(describing: captureError))
        }
    }

    /// Reads the photo bytes out of the SDK Photo value.
    ///
    /// VERIFY-ON-MAC: Photo.uri shape. The migrated core type carries
    /// uri: String? (was url: URL). Whether it is a file URL string, a
    /// plain filesystem path, or an http(s) URL is unverified; all
    /// three are handled, anything else throws.
    private static func photoBytes(from photo: GlassesCore.Photo) async throws -> Data {
        guard let uri = photo.uri, !uri.isEmpty else {
            throw ExtentosGlassesSourceError.photoBytesFailed("Photo.uri is empty.")
        }
        if let url = URL(string: uri), url.isFileURL {
            return try Data(contentsOf: url)
        }
        if FileManager.default.fileExists(atPath: uri) {
            return try Data(contentsOf: URL(fileURLWithPath: uri))
        }
        if let url = URL(string: uri),
           let scheme = url.scheme?.lowercased(),
           scheme == "http" || scheme == "https" {
            let (data, _) = try await URLSession.shared.data(from: url)
            return data
        }
        throw ExtentosGlassesSourceError.photoBytesFailed("Unrecognized Photo.uri format.")
    }

    // MARK: - Display (opt-in, NOT wired to session flow)

    /// Shows a speaker card on the glasses display. Opt-in only:
    /// nothing in the session flow calls this. Gated on
    /// display.isAvailable, so it no-ops on display-less glasses.
    /// show() replaces the whole display every call.
    func showSpeakerCard(_ card: SpeakerCard) async {
        // VERIFY-ON-MAC: the DisplayRootScope builder vocabulary is
        // verbatim from the SDK source (DisplayClient.swift);
        // TextStyle.heading is per the r1 docs example. isAvailable
        // flips live per the SDK docs; never branch on the model name.
        guard glasses.display.isAvailable else {
            return
        }
        let display = glasses.display
        await display.show(
            onBack: { Task { await display.clear() } },
            content: { root in
                root.flexBox(
                    direction: .column,
                    gap: 8,
                    padding: GlassesCore.EdgeInsets(top: 12, right: 12, bottom: 12, left: 12)
                ) { col in
                    // Bright text, no dark backgrounds: dark pixels are
                    // invisible on the additive 600x600 panel.
                    col.text(card.name, style: .heading)
                    col.text(card.detail, style: .body)
                }
            }
        )
    }

    /// Clears the glasses display. Opt-in only.
    func clearDisplay() async {
        await glasses.display.clear()
    }

    // MARK: - JPEG encoding helpers

    /// Decodes captured image data, scales the long edge down to
    /// maxLongEdge, and re-encodes as JPEG at the given quality.
    /// Data that is already JPEG at or under the size limit still
    /// goes through this path, so every still the app emits has one
    /// consistent shape.
    static func encodeJPEG(fromImageData data: Data, maxLongEdge: Int, quality: Double) throws -> Data {
        guard
            let source = CGImageSourceCreateWithData(data as CFData, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            throw ExtentosGlassesSourceError.jpegEncodingFailed("Could not decode captured image data.")
        }
        return try encodeJPEG(from: image, maxLongEdge: maxLongEdge, quality: quality)
    }

    static func encodeJPEG(from image: CGImage, maxLongEdge: Int, quality: Double) throws -> Data {
        let scaled = scaledImage(image, maxLongEdge: maxLongEdge)
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            output,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            throw ExtentosGlassesSourceError.jpegEncodingFailed("Could not create a JPEG destination.")
        }
        let options: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: quality
        ]
        CGImageDestinationAddImage(destination, scaled, options as CFDictionary)
        guard CGImageDestinationFinalize(destination) else {
            throw ExtentosGlassesSourceError.jpegEncodingFailed("JPEG destination finalize failed.")
        }
        return output as Data
    }

    /// Scales so the long edge equals maxLongEdge. Images already at
    /// or under the limit are returned unchanged.
    private static func scaledImage(_ image: CGImage, maxLongEdge: Int) -> CGImage {
        let width = image.width
        let height = image.height
        let longEdge = max(width, height)
        guard maxLongEdge > 0, longEdge > maxLongEdge else {
            return image
        }
        let scale = Double(maxLongEdge) / Double(longEdge)
        let newWidth = max(1, Int((Double(width) * scale).rounded()))
        let newHeight = max(1, Int((Double(height) * scale).rounded()))
        let colorSpace = image.colorSpace ?? CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: nil,
            width: newWidth,
            height: newHeight,
            bitsPerComponent: image.bitsPerComponent,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: image.bitmapInfo.rawValue
        ) else {
            return image
        }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: newWidth, height: newHeight))
        return context.makeImage() ?? image
    }
}

#if DEBUG
// MARK: - Local test double
//
// FakeExtentosGlasses: a scriptable in-file fake of any
// ExtentosGlasses for drills and unit tests (hinge cause, temple-tap
// pause, sample-rate change, JPEG decode, connect failure).
//
// DELETE THIS SECTION on the Mac once the GlassesTesting SPM product
// is linked: it ships the real FakeExtentosGlasses, and keeping both
// creates an ambiguous reference in test targets. Every member below
// is transcribed from the swift-glasses 2.8.0 shell sources read on
// 2026-10-21; anything inferred carries a VERIFY-ON-MAC marker.
//
// Debug-only: release builds never carry the fake.

enum FakeGlassesError: Error {
    case assistantUnused
}

/// Replayable ObservableState fake (StateFlow semantics: the current
/// value is yielded immediately on subscription).
final class FakeObservableState<T: Sendable>: ObservableState, @unchecked Sendable {
    private let lock = NSLock()
    private var value: T
    private var continuations: [AsyncStream<T>.Continuation] = []

    init(initial: T) {
        self.value = initial
    }

    var current: T {
        lock.withLock { value }
    }

    var stream: AsyncStream<T> {
        AsyncStream { continuation in
            let currentValue = lock.withLock { () -> T in
                continuations.append(continuation)
                return value
            }
            continuation.yield(currentValue)
        }
    }

    func send(_ newValue: T) {
        let targets = lock.withLock { () -> [AsyncStream<T>.Continuation] in
            value = newValue
            return continuations
        }
        for target in targets {
            target.yield(newValue)
        }
    }
}

final class FakeConnectionClient: ConnectionClient, @unchecked Sendable {
    let stateHolder = FakeObservableState<GlassesCore.GlassesState>(initial: .notRegistered)

    var state: any ObservableState<GlassesCore.GlassesState> {
        stateHolder
    }

    var simulatorHint: any ObservableState<GlassesCore.SimulatorHint?> {
        FakeObservableState<GlassesCore.SimulatorHint?>(initial: nil)
    }

    var uiState: any ObservableState<GlassesCore.ExtentosUiState> {
        fatalError("FakeConnectionClient: uiState is not used by drills.")
    }

    /// Script the connect outcome. Tests drive stateHolder directly
    /// for state transitions; connect() itself is side-effect free.
    var connectResult: GlassesCore.ExtentosResult<Void, GlassesCore.ConnectError> = .success(())

    func connect(deviceId: GlassesCore.DeviceId?) async -> GlassesCore.ExtentosResult<Void, GlassesCore.ConnectError> {
        connectResult
    }

    func disconnect() async {
        // Tests drive stateHolder directly.
    }

    // reconnect(deviceId:) uses the SDK protocol extension default
    // (disconnect then connect), so it is not reimplemented here.
}

final class FakeCameraClient: CameraClient, @unchecked Sendable {
    var photoResult: GlassesCore.ExtentosResult<GlassesCore.Photo, GlassesCore.CaptureError> = .failure(.notConnected)
    var videoResult: GlassesCore.ExtentosResult<GlassesCore.VideoClip, GlassesCore.CaptureError> = .failure(.notConnected)

    /// Script the frame stream: yield frames, throw
    /// CameraStreamPaused() to drill the temple-tap path, or finish
    /// immediately (the default).
    var videoFramesFactory: () -> AsyncThrowingStream<GlassesCore.VideoFrame, Error> = {
        AsyncThrowingStream { $0.finish() }
    }

    /// Script the stream-activity stream. Defaults to a stream that
    /// yields .idle once and stays subscribed.
    var streamStateStream: AsyncStream<GlassesCore.CameraStreamState> = AsyncStream { continuation in
        continuation.yield(.idle)
    }

    func capturePhoto(config: GlassesCore.PhotoConfig) async -> GlassesCore.ExtentosResult<GlassesCore.Photo, GlassesCore.CaptureError> {
        photoResult
    }

    func captureVideo(config: GlassesCore.VideoConfig) async -> GlassesCore.ExtentosResult<GlassesCore.VideoClip, GlassesCore.CaptureError> {
        videoResult
    }

    func stopVideo() async {}

    func videoFrames(config: GlassesCore.VideoFrameConfig) -> AsyncThrowingStream<GlassesCore.VideoFrame, Error> {
        videoFramesFactory()
    }

    func streamState() -> AsyncStream<GlassesCore.CameraStreamState> {
        streamStateStream
    }

    // activeStreamInfo() uses the SDK protocol extension default (nil).
}

final class FakeAudioClient: AudioClient, @unchecked Sendable {
    /// Script the chunk stream. Defaults to a stream that never emits
    /// and never finishes. GlassesCore.AudioChunk has a public
    /// init(samples:sampleRate:timestampMs:), so tests build chunks
    /// directly, including chunks with a non-16000 sampleRate to drill
    /// the resample path.
    var audioChunksStream: AsyncStream<GlassesCore.AudioChunk> = AsyncStream { _ in }

    func speak(_ text: String, config: GlassesCore.SpeakConfig) async -> GlassesCore.ExtentosResult<Void, GlassesCore.AudioError> {
        .failure(.notConnected)
    }

    func cancelSpeak() async {}

    func earcon(_ sound: GlassesCore.EarconSound, volume: Float) async {}

    func sendAudio(_ pcm16: Data, sampleRate: Int) async -> GlassesCore.ExtentosResult<Void, GlassesCore.AudioError> {
        .failure(.notConnected)
    }

    func stopAudio() async {}

    var outputFidelity: GlassesCore.OutgoingAudioFidelity {
        .narrowband
    }

    func recordDiscrete(config: GlassesCore.AudioRecordConfig) async -> GlassesCore.ExtentosResult<GlassesCore.AudioRecording, GlassesCore.AudioError> {
        .failure(.notConnected)
    }

    func audioChunks(config: GlassesCore.AudioChunkConfig) -> AsyncStream<GlassesCore.AudioChunk> {
        audioChunksStream
    }

    func transcriptions(config: GlassesCore.TranscriptionConfig) -> AsyncStream<GlassesCore.Transcript> {
        AsyncStream { _ in }
    }
}

final class FakeDisplayClient: DisplayClient, @unchecked Sendable {
    var isAvailable: Bool = false
    private(set) var showCallCount = 0
    private(set) var clearCallCount = 0

    func show(onBack: (@Sendable () -> Void)?, content: (GlassesCore.DisplayRootScope) -> Void) async {
        // DisplayScope.nodes is internal to GlassesCore, so the fake
        // records invocation only, not the built tree.
        showCallCount += 1
    }

    func clear() async {
        clearCallCount += 1
    }

    func prepareVideo(clip: GlassesCore.VideoClip) async -> Bool {
        false
    }

    func forgetHostedVideo(clip: GlassesCore.VideoClip) async {}

    func forgetHostedImage(photo: GlassesCore.Photo) async {}
}

final class FakeRuntimeClient: RuntimeClient, @unchecked Sendable {
    var events: AsyncStream<GlassesCore.RuntimeEvent> {
        AsyncStream { _ in }
    }

    func snapshotEvents() async -> [GlassesCore.RuntimeEvent] {
        []
    }
}

final class FakeToggleClient: ToggleClient, @unchecked Sendable {
    let stateHolder = FakeObservableState<GlassesCore.Toggles>(initial: GlassesCore.Toggles())

    var state: any ObservableState<GlassesCore.Toggles> {
        stateHolder
    }

    func get(key: String) -> GlassesCore.JSONValue? {
        nil
    }

    func put(key: String, value: GlassesCore.JSONValue, source: GlassesCore.ToggleSource) async {}
}

final class FakeVoiceRegistration: VoiceRegistration, @unchecked Sendable {
    let id: String = UUID().uuidString
    private(set) var cancelled = false

    func cancel() {
        cancelled = true
    }
}

final class FakeVoiceClient: VoiceClient, @unchecked Sendable {
    var hints: any ObservableState<[GlassesCore.VoiceHint]> {
        FakeObservableState<[GlassesCore.VoiceHint]>(initial: [])
    }

    var stats: any ObservableState<[GlassesCore.VoiceHintStats]> {
        FakeObservableState<[GlassesCore.VoiceHintStats]>(initial: [:])
    }

    func onPhrase(
        phrase: String,
        label: String?,
        stops: [String],
        firesWhen: GlassesCore.VoiceScope,
        handler: @escaping @Sendable () async -> Void
    ) -> VoiceRegistration {
        FakeVoiceRegistration()
    }

    func registerHint(phrase: String, label: String?, stops: [String]) -> VoiceRegistration {
        FakeVoiceRegistration()
    }

    func reportFired(id: String) {}
}

final class FakeTelemetryClient: TelemetryClient, @unchecked Sendable {
    private(set) var segments: [String?] = []
    private(set) var trackedEvents: [(name: String, properties: [String: GlassesCore.JSONValue])] = []

    func setUserSegment(_ segment: String?) {
        segments.append(segment)
    }

    func trackEvent(name: String, properties: [String: GlassesCore.JSONValue]) {
        trackedEvents.append((name: name, properties: properties))
    }

    var consent: Bool {
        true
    }
}

final class FakeObservabilityClient: ObservabilityClient, @unchecked Sendable {
    func aiCall<T: Sendable>(
        label: String,
        metadata: [String: String],
        failureReason: (@Sendable (T) -> String?)?,
        block: @Sendable () async throws -> T
    ) async rethrows -> T {
        try await block()
    }
}

final class FakeAssistantClient: AssistantClient, @unchecked Sendable {
    func start(
        provider: GlassesCore.AssistantProvider,
        _ configure: (GlassesCore.AssistantConfigBuilder) -> Void
    ) async throws -> any AssistantSession {
        throw FakeGlassesError.assistantUnused
    }

    // VERIFY-ON-MAC: the AssistantConfig parameter type name for
    // createSession(config:); the assistant surface is unused by drills.
    func createSession(config: GlassesCore.AssistantConfig) -> any AssistantSession {
        fatalError("FakeAssistantClient: the assistant surface is not used by drills.")
    }

    func stop() async {}

    // VERIFY-ON-MAC: activeSession optionality against the SDK headers.
    var activeSession: (any AssistantSession)? {
        nil
    }
}

/// Scriptable fake of any ExtentosGlasses. Inject into
/// ExtentosGlassesSource for drills and unit tests.
final class FakeExtentosGlasses: ExtentosGlasses, @unchecked Sendable {
    let connectionClient = FakeConnectionClient()
    let cameraClient = FakeCameraClient()
    let audioClient = FakeAudioClient()
    let runtimeClient = FakeRuntimeClient()
    let toggleClient = FakeToggleClient()
    let voiceClient = FakeVoiceClient()
    let telemetryClient = FakeTelemetryClient()
    let observabilityClient = FakeObservabilityClient()
    let assistantClient = FakeAssistantClient()
    let displayClient = FakeDisplayClient()

    var connection: any ConnectionClient { connectionClient }
    var camera: any CameraClient { cameraClient }
    var audio: any AudioClient { audioClient }
    var runtime: any RuntimeClient { runtimeClient }
    var toggles: any ToggleClient { toggleClient }
    var voice: any VoiceClient { voiceClient }
    var telemetry: any TelemetryClient { telemetryClient }
    var observability: any ObservabilityClient { observabilityClient }
    var assistant: any AssistantClient { assistantClient }
    var display: any DisplayClient { displayClient }

    var usedCapabilities: [GlassesCore.DeclaredCapability] = [.camera, .microphone]

    // VERIFY-ON-MAC: DeviceCapabilitySet memberwise init is public per
    // r2; confirm field order against the SDK headers on the Mac.
    var capabilities: GlassesCore.DeviceCapabilitySet = GlassesCore.DeviceCapabilitySet(
        camera: true,
        microphone: true,
        speaker: false,
        display: false
    )

    func connect() async -> GlassesCore.ExtentosResult<Void, GlassesCore.ConnectError> {
        await connectionClient.connect(deviceId: nil)
    }

    func shutdown() async {}

    func handleUrl(_ url: URL) async -> Bool {
        false
    }
}
#endif
