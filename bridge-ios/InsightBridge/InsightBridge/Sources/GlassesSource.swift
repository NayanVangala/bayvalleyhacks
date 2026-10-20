// GlassesSource.swift
// InsightBridge
//
// CaptureSource backed by the Ray-Ban Meta Display glasses through
// the Meta Wearables DAT SDK. This file and DATRegistration.swift
// are the ONLY files in the app allowed to import the SDK. Every
// type that leaves this file is an SDK-free type from
// Common/MediaTypes.swift (AudioChunk, VideoFrame, SourceHealth,
// StillPhoto), so downstream code never sees an SDK type.
//
// SDK version decision:
//   Findr pinned the DAT iOS SDK at 0.7.0. This project does NOT use
//   that pin. OnRecord targets toolkit 1.0, pinned at >= 1.0.1,
//   because 1.0.0 froze camera frames in the stream session.
//   Dev mode uses MetaAppID = 0 in Info.plist (the MWDAT dict).
//
// Session rule, learned the hard way in Findr and kept here:
//   ONE StreamSession stays open for the whole conversation. This
//   class never opens or closes a session per capture. Session churn
//   stalls the link, re-triggers connect prompts, and is a known
//   demo killer. start() creates the session once, stop() tears it
//   down once, and captureStill() reuses the live session.
//
// Verify against pinned DAT 1.0.x headers on the Mac; API names
// follow the 1.0 documented patterns.

import AVFoundation
import Combine
import CoreImage
import CoreVideo
import Foundation
import ImageIO
import MWDATCamera
import MWDATCore
import MWDATMockDevice
import UniformTypeIdentifiers
import os.log

/// Builds the stream session for a device id. Tests and failover
/// drills inject a factory that vends a session backed by
/// MWDATMockDevice instead of real hardware, so the exact same
/// GlassesSource code runs with no glasses present. Production uses
/// the default factory, which builds the real SDK session.
/// Verify against pinned DAT 1.0.x headers on the Mac; API names
/// follow the 1.0 documented patterns.
typealias GlassesStreamSessionFactory = (_ deviceID: String?) throws -> StreamSession

/// Builds the device state session for a device id. Injected for
/// the same reason as GlassesStreamSessionFactory: a mock backed
/// state session lets drills simulate battery drain, disconnects,
/// and hinge closes deterministically.
/// Verify against pinned DAT 1.0.x headers on the Mac; API names
/// follow the 1.0 documented patterns.
typealias GlassesDeviceStateSessionFactory = (_ deviceID: String?) -> DeviceStateSession

enum GlassesSourceError: Error, LocalizedError {
    case sessionNotStarted
    case stillCaptureFailed(String)
    case jpegEncodingFailed(String)

    var errorDescription: String? {
        switch self {
        case .sessionNotStarted:
            return "Glasses stream session is not started."
        case .stillCaptureFailed(let detail):
            return "Glasses still capture failed: \(detail)"
        case .jpegEncodingFailed(let detail):
            return "JPEG encoding failed: \(detail)"
        }
    }
}

final class GlassesSource: CaptureSource {

    // MARK: - CaptureSource surface

    let origin: CaptureOrigin = .glasses

    /// PCM16 16 kHz mono, 100 ms chunks. See handleAudioBuffer.
    let audio: AsyncStream<AudioChunk>

    /// Raw glasses frames, unthrottled. Downstream owns throttling
    /// (photo cadence, QR scans). Dropping frames here would hide
    /// link quality from the watchdog, so this class drops nothing.
    let video: AsyncStream<VideoFrame>

    /// Health snapshots, emitted on every device state change, on
    /// every SDK error, and on a 500 ms heartbeat timer so that
    /// lastFrameAge keeps growing visibly when frames stall.
    let health: AsyncStream<SourceHealth>

    // MARK: - Configuration

    /// Device selected by DATRegistration. Nil is allowed for mock
    /// backed sessions and for SDK builds that resolve the paired
    /// device implicitly.
    let deviceID: String?

    private let streamSessionFactory: GlassesStreamSessionFactory
    private let deviceStateSessionFactory: GlassesDeviceStateSessionFactory

    /// Health heartbeat cadence: 500 ms. Config.swift gives the
    /// watchdog a 3.0 s no-frame timeout, so this heartbeat lands
    /// about six health updates inside that window while frames are
    /// stalled, each one carrying a larger lastFrameAge.
    private static let healthIntervalNanoseconds: UInt64 = 500_000_000

    // MARK: - Stream continuations

    private let audioContinuation: AsyncStream<AudioChunk>.Continuation
    private let videoContinuation: AsyncStream<VideoFrame>.Continuation
    private let healthContinuation: AsyncStream<SourceHealth>.Continuation

    // MARK: - Session state

    private var streamSession: StreamSession?
    private var deviceStateSession: DeviceStateSession?
    private var cancellables = Set<AnyCancellable>()
    private var healthTask: Task<Void, Never>?
    private var isStarted = false

    /// Most recent health snapshot, kept for the debug UI and for
    /// tests that do not want to race the health stream.
    private(set) var latestHealth: SourceHealth?

    // MARK: - Mutable capture state (guarded by stateLock)

    private let stateLock = NSLock()
    private var lastFrameDate: Date?
    private var lastBatteryLevel: Double?
    private var lastHingeClosed = false
    private var lastAlive = false
    private var lastDetail = "not started"

    /// Next audio sequence number. Monotonically increasing for the
    /// lifetime of this source, across stop and start cycles, so the
    /// server can order chunks and spot gaps honestly.
    private var nextAudioSeq: UInt64 = 0

    /// Converted PCM waiting to fill a full 3200 byte chunk.
    private var pendingPCM = Data()

    /// Cached converter for the current SDK audio format. Recreated
    /// whenever the incoming format changes.
    private var audioConverter: AVAudioConverter?
    private var audioConverterInputFormat: AVAudioFormat?

    private let logger = Logger(subsystem: "com.nayan.insightbridge", category: "GlassesSource")

    // MARK: - Init

    /// - Parameters:
    ///   - deviceID: id stored by DATRegistration, if discovery ran.
    ///   - streamSessionFactory: session builder. Inject a
    ///     MWDATMockDevice backed factory for drills and tests.
    ///   - deviceStateSessionFactory: state session builder, same
    ///     injection story as the stream session factory.
    init(
        deviceID: String? = nil,
        streamSessionFactory: @escaping GlassesStreamSessionFactory = { deviceID in
            // Verify against pinned DAT 1.0.x headers on the Mac;
            // API names follow the 1.0 documented patterns.
            StreamSession(deviceId: deviceID)
        },
        deviceStateSessionFactory: @escaping GlassesDeviceStateSessionFactory = { deviceID in
            // Verify against pinned DAT 1.0.x headers on the Mac;
            // API names follow the 1.0 documented patterns.
            DeviceStateSession(deviceId: deviceID)
        }
    ) {
        self.deviceID = deviceID
        self.streamSessionFactory = streamSessionFactory
        self.deviceStateSessionFactory = deviceStateSessionFactory

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
        audioContinuation.finish()
        videoContinuation.finish()
        healthContinuation.finish()
    }

    // MARK: - Start and stop

    /// Creates the ONE stream session for this conversation and
    /// subscribes to video, audio, errors, and device state.
    /// Calling start() twice is a no-op: the live session is kept.
    func start() async throws {
        guard !isStarted else {
            logger.info("GlassesSource.start called while already started. Keeping the existing session open.")
            return
        }

        // Setup day rule: dev mode must be logged before session
        // start. DATRegistration.checkDevMode() is the checklist
        // helper; this log line is the in-source backup so a session
        // started from any path still leaves the evidence.
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns.
        logger.info("Starting glasses session. isDevMode=\(Wearables.isDevMode, privacy: .public).")

        let stream: StreamSession
        do {
            stream = try streamSessionFactory(deviceID)
        } catch {
            forwardError(error, context: "stream session factory")
            throw error
        }
        let state = deviceStateSessionFactory(deviceID)
        streamSession = stream
        deviceStateSession = state

        subscribeVideo(stream)
        subscribeAudio(stream)
        subscribeErrors(stream)
        subscribeDeviceState(state)

        do {
            // Verify against pinned DAT 1.0.x headers on the Mac;
            // API names follow the 1.0 documented patterns. The
            // start and stop verbs and the device state session
            // start call need a header check.
            state.start()
            try await stream.start()
        } catch {
            forwardError(error, context: "stream session start")
            streamSession = nil
            deviceStateSession = nil
            cancellables.removeAll()
            throw error
        }

        isStarted = true
        setHealthSnapshot(alive: true, detail: "streaming")
        emitHealth()
        startHealthHeartbeat()
        logger.info("Glasses stream session is live. It stays open until stop() is called.")
    }

    /// Tears the session down once. Best effort and non-blocking in
    /// spirit: SessionManager calls this on the failover path and
    /// immediately starts the phone source, so nothing here may hang.
    /// The stream continuations are deliberately NOT finished, so
    /// this source can be started again after a failover cooldown.
    func stop() async {
        healthTask?.cancel()
        healthTask = nil
        cancellables.removeAll()

        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns.
        if let stream = streamSession {
            await stream.stop()
        }
        deviceStateSession?.stop()
        streamSession = nil
        deviceStateSession = nil
        isStarted = false

        stateLock.lock()
        pendingPCM.removeAll(keepingCapacity: false)
        stateLock.unlock()

        setHealthSnapshot(alive: false, detail: "stopped")
        emitHealth()
        logger.info("Glasses stream session stopped.")
    }

    // MARK: - Video

    private func subscribeVideo(_ stream: StreamSession) {
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. The frame
        // publisher name and whether it vends CVPixelBuffer values
        // directly need a header check.
        stream.videoFramePublisher
            .sink(
                receiveCompletion: { [weak self] completion in
                    if case .failure(let error) = completion {
                        self?.forwardError(error, context: "video stream")
                    }
                },
                receiveValue: { [weak self] pixelBuffer in
                    guard let self else { return }
                    let now = Date()
                    self.stateLock.lock()
                    self.lastFrameDate = now
                    self.stateLock.unlock()
                    // Timestamp is receipt time. If the pinned
                    // headers show frames carrying a capture
                    // timestamp, prefer that value here.
                    self.videoContinuation.yield(
                        VideoFrame(pixelBuffer: pixelBuffer, timestamp: now, origin: .glasses)
                    )
                }
            )
            .store(in: &cancellables)
    }

    // MARK: - Audio
    //
    // EXPERIMENTAL in toolkit 1.0: the glasses microphone is exposed
    // only through the camera stream audio track, alongside video.
    // Meta documents this path as experimental, which is why the
    // product treats the phone mic as the deterministic fallback and
    // why SessionManager, not this class, decides which mic the
    // conversation is actually using. This class converts whatever
    // the glasses track delivers into the shared chunk format and
    // reports failures through health instead of hiding them.

    private func subscribeAudio(_ stream: StreamSession) {
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. The audio
        // publisher name and buffer type need a header check.
        stream.audioBufferPublisher
            .sink(
                receiveCompletion: { [weak self] completion in
                    if case .failure(let error) = completion {
                        self?.forwardError(error, context: "glasses audio track")
                    }
                },
                receiveValue: { [weak self] buffer in
                    self?.handleAudioBuffer(buffer)
                }
            )
            .store(in: &cancellables)
    }

    /// Converts one SDK audio buffer to PCM16 16 kHz mono and emits
    /// any complete 100 ms chunks it produces.
    private func handleAudioBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let pcm = convertToPCM16Mono16kHz(buffer) else {
            logger.error("Audio conversion failed for one glasses buffer. Buffer dropped, session kept alive.")
            return
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
                    timestamp: Date(),
                    origin: .glasses
                )
            )
        }
        stateLock.unlock()

        for chunk in chunks {
            audioContinuation.yield(chunk)
        }
    }

    /// AVAudioConverter path from whatever the SDK delivers (rate,
    /// channel count, and sample format all vary by transport) to
    /// the one format the whole pipeline speaks: PCM16, 16 kHz,
    /// mono, interleaved. Returns nil on conversion failure; the
    /// caller logs and drops that buffer rather than emitting a
    /// malformed chunk.
    private func convertToPCM16Mono16kHz(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: Double(AudioFormat.sampleRate),
            channels: 1,
            interleaved: true
        ) else {
            return nil
        }

        if buffer.format == targetFormat {
            return pcm16Data(from: buffer)
        }

        stateLock.lock()
        if audioConverter == nil || audioConverterInputFormat != buffer.format {
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

    // MARK: - Device state and health

    private func subscribeDeviceState(_ stateSession: DeviceStateSession) {
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. The state
        // publisher name and the state field names (battery level,
        // hinges closed, connected) need a header check.
        stateSession.statePublisher
            .sink(
                receiveCompletion: { [weak self] completion in
                    if case .failure(let error) = completion {
                        self?.forwardError(error, context: "device state")
                    }
                },
                receiveValue: { [weak self] state in
                    guard let self else { return }
                    let battery = Self.normalizedBatteryLevel(state.batteryLevel)
                    self.stateLock.lock()
                    if let battery {
                        self.lastBatteryLevel = battery
                    }
                    self.lastHingeClosed = state.hingesClosed
                    self.lastAlive = state.isConnected
                    if state.hingesClosed {
                        // Hinge close is a health fact, never a
                        // crash and never a silent drop. The
                        // watchdog reads hingeClosed and fails over
                        // to the phone source.
                        self.lastDetail = "hingesClosed"
                    } else if !state.isConnected {
                        self.lastDetail = "disconnected"
                    } else {
                        self.lastDetail = "streaming"
                    }
                    self.stateLock.unlock()
                    self.emitHealth()
                }
            )
            .store(in: &cancellables)
    }

    private func subscribeErrors(_ stream: StreamSession) {
        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. Toolkit errors
        // arrive on a dedicated publisher in 1.0; if the pinned
        // build folds errors into the frame publisher completion
        // instead, those are already forwarded by subscribeVideo.
        stream.errorPublisher
            .sink { [weak self] error in
                self?.forwardError(error, context: "stream session")
            }
            .store(in: &cancellables)
    }

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

    /// The pinned headers expose hinge close as a dedicated SDK
    /// error case. Matching on the description here keeps this file
    /// honest about that uncertainty; on the Mac, switch this to a
    /// typed case match against the real error enum.
    private static func isHingeError(_ error: Error) -> Bool {
        String(describing: error).lowercased().contains("hinge")
    }

    /// SDK battery reporting is inconsistent across builds: some
    /// report 0 to 1, some report 0 to 100. Normalize to 0 to 1 and
    /// clamp, so SourceHealth always carries the same scale.
    private static func normalizedBatteryLevel(_ raw: Double?) -> Double? {
        guard let raw else {
            return nil
        }
        let scaled = raw > 1.0 ? raw / 100.0 : raw
        return min(1.0, max(0.0, scaled))
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
            batteryLevel: lastBatteryLevel,
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

    /// Captures one still on the live stream session and returns it
    /// as a JPEG at 960 px long edge, quality 0.7, the exact shape
    /// downstream QR and identity code expects.
    ///
    /// `eager` marks captures taken at talk start or session start,
    /// where identity matters most. The flag rides through on
    /// StillPhoto untouched; capture behavior is identical either
    /// way, because the session is already open.
    func captureStill(eager: Bool) async throws -> StillPhoto {
        guard isStarted, let stream = streamSession else {
            throw GlassesSourceError.sessionNotStarted
        }

        // Verify against pinned DAT 1.0.x headers on the Mac; API
        // names follow the 1.0 documented patterns. The still
        // capture call name and its return type need a header check.
        // If the pinned build vends a CVPixelBuffer instead of image
        // data, use encodeJPEG(fromPixelBuffer:) below for the same
        // output shape.
        let rawImageData: Data
        do {
            rawImageData = try await stream.capturePhoto()
        } catch {
            forwardError(error, context: "still capture")
            throw GlassesSourceError.stillCaptureFailed(error.localizedDescription)
        }

        let jpeg = try Self.encodeJPEG(
            fromImageData: rawImageData,
            maxLongEdge: AudioFormat.photoLongEdgePx,
            quality: AudioFormat.photoJPEGQuality
        )
        return StillPhoto(jpeg: jpeg, timestamp: Date(), origin: .glasses, eager: eager)
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
            throw GlassesSourceError.jpegEncodingFailed("Could not decode captured image data.")
        }
        return try encodeJPEG(from: image, maxLongEdge: maxLongEdge, quality: quality)
    }

    /// Pixel buffer variant, used when a capture path vends frames
    /// instead of encoded image data.
    static func encodeJPEG(fromPixelBuffer pixelBuffer: CVPixelBuffer, maxLongEdge: Int, quality: Double) throws -> Data {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        guard let image = context.createCGImage(ciImage, from: ciImage.extent) else {
            throw GlassesSourceError.jpegEncodingFailed("Could not create an image from the pixel buffer.")
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
            throw GlassesSourceError.jpegEncodingFailed("Could not create a JPEG destination.")
        }
        let options: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: quality
        ]
        CGImageDestinationAddImage(destination, scaled, options as CFDictionary)
        guard CGImageDestinationFinalize(destination) else {
            throw GlassesSourceError.jpegEncodingFailed("JPEG destination finalize failed.")
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
