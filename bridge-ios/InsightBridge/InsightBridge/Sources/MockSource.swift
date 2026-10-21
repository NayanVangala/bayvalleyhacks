//
//  MockSource.swift
//  InsightBridge
//
//  Pure Swift synthetic CaptureSource for failover drills and tests.
//
//  On the Mac this concept can also be backed by MWDATMockDevice for
//  SDK level drills against the toolkit mock device. This version
//  needs no SDK import at all: it synthesizes the exact same
//  AudioChunk, VideoFrame and SourceHealth contract in process, so
//  drills run on the phone alone, in the simulator, and in unit
//  tests with no hardware attached.
//
//  Determinism contract:
//    - Sequence numbers start at 0 on every start(), for audio and
//      video independently.
//    - Audio content is a pure function of the chunk sequence number.
//      No randomness anywhere in this file.
//    - The moving block in video frames is positioned from the frame
//      sequence number only.
//    - Battery drain is a pure function of elapsed time since start.
//
//  Audio pattern, repeating every 5 seconds (50 chunks):
//    - Chunks 0 to 19 of the cycle: soft 220 Hz sine, low amplitude.
//      Sounds like a quiet room tone to a human, and gives speech
//      tooling a stable signal to lock onto.
//    - Chunks 20 to 48 of the cycle: digital silence.
//    - Chunk 49 of the cycle: one 100 ms blip at 1000 Hz, higher
//      amplitude, so STT smoke tests see structure on a known beat.
//  Every chunk is exactly AudioFormat.frameBytes (3200 bytes) of
//  PCM16 mono at AudioFormat.sampleRate (16000 Hz), emitted on a
//  100 ms deadline schedule so pacing does not drift.
//
//  Video pattern: 640 by 480 BGRA pixel buffers at 2 fps, solid dark
//  background with one bright block that steps across the frame.
//  captureStill renders a labeled test card with CoreGraphics and
//  CoreText (the word MOCK plus the capture timestamp) and encodes
//  it as JPEG with a 960 px long edge.
//
//  Test hooks for scripted drills:
//    - simulateHingeClose() flips hingeClosed to true and emits a
//      health update immediately, like the glasses case closing.
//    - simulateDeath() emits one final health update with
//      alive false, then cancels all work and finishes all streams.
//      Death is terminal for the instance: create a fresh MockSource
//      to run again, exactly like SessionManager creates a fresh
//      source after real hardware loss.
//

import CoreGraphics
import CoreText
import CoreVideo
import Foundation
import ImageIO
import UniformTypeIdentifiers

// MARK: - Errors

enum MockSourceError: Error, LocalizedError {
    case notRunning
    case dead
    case lifecycleEnded
    case stillRenderingFailed

    var errorDescription: String? {
        switch self {
        case .notRunning:
            return "MockSource is not running."
        case .dead:
            return "MockSource has simulated death and cannot capture."
        case .lifecycleEnded:
            return "MockSource streams are finished. Create a fresh instance to run again."
        case .stillRenderingFailed:
            return "MockSource could not render its test still image."
        }
    }
}

// MARK: - MockSource

final class MockSource: CaptureSource {

    // MARK: CaptureSource contract

    let origin: CaptureOrigin = .mock

    let audio: AsyncStream<AudioChunk>
    let video: AsyncStream<VideoFrame>
    let health: AsyncStream<SourceHealth>

    private let audioContinuation: AsyncStream<AudioChunk>.Continuation
    private let videoContinuation: AsyncStream<VideoFrame>.Continuation
    private let healthContinuation: AsyncStream<SourceHealth>.Continuation

    // MARK: Tunables

    private static let frameWidth = 640
    private static let frameHeight = 480
    private static let blockSize = 64
    private static let blockStepPx = 24
    private static let startingBattery: Double = 0.85
    /// Fraction of battery lost per second: about 0.36 per hour.
    private static let batteryDrainPerSecond: Double = 0.0001
    private static let chunksPerCycle: UInt64 = 50

    // MARK: State

    private let stateLock = NSLock()
    private var isRunning = false
    private var isDead = false
    private var streamsFinished = false
    private var hingeClosedFlag = false
    private var nextAudioSeq: UInt64 = 0
    private var nextVideoSeq: UInt64 = 0
    private var startedAt: Date?
    private var lastVideoFrameDate: Date?

    private var audioTask: Task<Void, Never>?
    private var videoTask: Task<Void, Never>?
    private var healthTask: Task<Void, Never>?

    // MARK: Init

    init() {
        let audioStream = AsyncStream<AudioChunk>.makeStream()
        let videoStream = AsyncStream<VideoFrame>.makeStream()
        let healthStream = AsyncStream<SourceHealth>.makeStream()
        self.audio = audioStream.stream
        self.video = videoStream.stream
        self.health = healthStream.stream
        self.audioContinuation = audioStream.continuation
        self.videoContinuation = videoStream.continuation
        self.healthContinuation = healthStream.continuation
    }

    deinit {
        audioTask?.cancel()
        videoTask?.cancel()
        healthTask?.cancel()
        audioContinuation.finish()
        videoContinuation.finish()
        healthContinuation.finish()
    }

    // MARK: Start

    func start() async throws {
        stateLock.lock()
        if isRunning {
            stateLock.unlock()
            return
        }
        if isDead {
            stateLock.unlock()
            throw MockSourceError.dead
        }
        if streamsFinished {
            stateLock.unlock()
            throw MockSourceError.lifecycleEnded
        }
        // Deterministic restart state: same numbers every start.
        nextAudioSeq = 0
        nextVideoSeq = 0
        hingeClosedFlag = false
        lastVideoFrameDate = nil
        startedAt = Date()
        isRunning = true
        stateLock.unlock()

        startAudioLoop()
        startVideoLoop()
        startHealthLoop()
        emitHealth()
    }

    // MARK: Audio loop

    private func startAudioLoop() {
        audioTask?.cancel()
        audioTask = Task { [weak self] in
            guard let self else { return }
            var deadline = ContinuousClock.now
            while !Task.isCancelled {
                self.stateLock.lock()
                let running = self.isRunning && !self.isDead
                let seq = self.nextAudioSeq
                if running {
                    self.nextAudioSeq += 1
                }
                self.stateLock.unlock()
                guard running else { return }

                let chunk = Self.makeAudioChunk(seq: seq)
                self.audioContinuation.yield(chunk)

                deadline += .milliseconds(AudioFormat.frameMs)
                let now = ContinuousClock.now
                if deadline > now {
                    try? await Task.sleep(until: deadline, clock: .continuous)
                } else {
                    // Fell behind (suspended process, debugger). Skip
                    // the missed beats instead of bursting catch up
                    // chunks; sequence numbers stay gap free and the
                    // next chunk lands on a fresh deadline.
                    deadline = now
                }
            }
        }
    }

    /// Pure function of seq. Same seq in, same 3200 bytes out, on any
    /// machine, on any run.
    static func makeAudioChunk(seq: UInt64) -> AudioChunk {
        let samplesPerChunk = AudioFormat.sampleRate * AudioFormat.frameMs / 1000
        let positionInCycle = seq % chunksPerCycle

        var samples = [Int16](repeating: 0, count: samplesPerChunk)
        if positionInCycle < 20 || positionInCycle == chunksPerCycle - 1 {
            let isBlip = positionInCycle == chunksPerCycle - 1
            let frequency: Double = isBlip ? 1000.0 : 220.0
            let amplitude: Double = isBlip ? 2500.0 : 1200.0
            for index in 0..<samplesPerChunk {
                let globalSample = seq * UInt64(samplesPerChunk) + UInt64(index)
                let t = Double(globalSample) / Double(AudioFormat.sampleRate)
                let value = sin(2.0 * Double.pi * frequency * t) * amplitude
                let clamped = max(-32768.0, min(32767.0, value.rounded()))
                samples[index] = Int16(clamped)
            }
        }
        // Chunks 20 to 48 stay digital silence: samples already zero.

        // Int16 samples are little endian on every Apple platform,
        // which is exactly the PCM16 wire layout the contract wants.
        let pcm = samples.withUnsafeBufferPointer { pointer -> Data in
            guard let base = pointer.baseAddress else { return Data() }
            return Data(bytes: base, count: samplesPerChunk * MemoryLayout<Int16>.size)
        }
        precondition(pcm.count == AudioFormat.frameBytes, "mock chunk must be exactly one frame")

        return AudioChunk(
            seq: seq,
            pcm: pcm,
            sampleRate: AudioFormat.sampleRate,
            timestamp: Date(),
            origin: .mock
        )
    }

    // MARK: Video loop

    private func startVideoLoop() {
        videoTask?.cancel()
        videoTask = Task { [weak self] in
            guard let self else { return }
            var deadline = ContinuousClock.now
            while !Task.isCancelled {
                self.stateLock.lock()
                let running = self.isRunning && !self.isDead
                let seq = self.nextVideoSeq
                if running {
                    self.nextVideoSeq += 1
                }
                self.stateLock.unlock()
                guard running else { return }

                if let pixelBuffer = Self.makePixelBuffer(frameIndex: seq) {
                    let now = Date()
                    self.stateLock.lock()
                    self.lastVideoFrameDate = now
                    self.stateLock.unlock()
                    let frame = VideoFrame(pixelBuffer: pixelBuffer, timestamp: now, origin: .mock)
                    self.videoContinuation.yield(frame)
                }

                deadline += .milliseconds(500)
                let now = ContinuousClock.now
                if deadline > now {
                    try? await Task.sleep(until: deadline, clock: .continuous)
                } else {
                    deadline = now
                }
            }
        }
    }

    /// Solid background with one moving block. The block position is
    /// derived from frameIndex only, so a recorded drill replays the
    /// same motion every time.
    static func makePixelBuffer(frameIndex: UInt64) -> CVPixelBuffer? {
        let width = frameWidth
        let height = frameHeight

        var pixelBuffer: CVPixelBuffer?
        let attributes: [CFString: Any] = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true
        ]
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            width,
            height,
            kCVPixelFormatType_32BGRA,
            attributes as CFDictionary,
            &pixelBuffer
        )
        guard status == kCVReturnSuccess, let pixelBuffer else { return nil }

        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }

        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        // BGRA in memory: alpha first byte ignored, little endian word.
        let bitmapInfo = CGImageAlphaInfo.noneSkipFirst.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue
        guard let context = CGContext(
            data: baseAddress,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: bitmapInfo
        ) else {
            return nil
        }

        context.setFillColor(CGColor(red: 0.08, green: 0.10, blue: 0.14, alpha: 1.0))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))

        let travel = max(1, width - blockSize)
        let blockX = Int(frameIndex * UInt64(blockStepPx)) % travel
        let blockY = (height - blockSize) / 2
        context.setFillColor(CGColor(red: 0.95, green: 0.75, blue: 0.20, alpha: 1.0))
        context.fill(CGRect(x: blockX, y: blockY, width: blockSize, height: blockSize))

        return pixelBuffer
    }

    // MARK: Health loop

    private func startHealthLoop() {
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(500))
                guard let self, !Task.isCancelled else { return }
                self.emitHealth()
            }
        }
    }

    private func currentHealth() -> SourceHealth {
        stateLock.lock()
        let running = isRunning
        let dead = isDead
        let hinge = hingeClosedFlag
        let start = startedAt
        let lastFrame = lastVideoFrameDate
        stateLock.unlock()

        let now = Date()
        let elapsed = start.map { now.timeIntervalSince($0) } ?? 0
        let battery = max(0.0, Self.startingBattery - elapsed * Self.batteryDrainPerSecond)

        let frameAge: TimeInterval
        if let lastFrame {
            frameAge = now.timeIntervalSince(lastFrame)
        } else {
            frameAge = elapsed
        }

        let alive = running && !dead
        let detail: String
        if dead {
            detail = "mock dead (simulated)"
        } else if hinge {
            detail = "mock hinge closed (simulated)"
        } else if alive {
            detail = "mock ok"
        } else {
            detail = "mock stopped"
        }

        return SourceHealth(
            origin: .mock,
            alive: alive,
            batteryLevel: battery,
            hingeClosed: hinge,
            lastFrameAge: frameAge,
            detail: detail
        )
    }

    private func emitHealth() {
        healthContinuation.yield(currentHealth())
    }

    // MARK: Still capture

    func captureStill(eager: Bool) async throws -> StillPhoto {
        stateLock.lock()
        let running = isRunning
        let dead = isDead
        stateLock.unlock()

        if dead {
            throw MockSourceError.dead
        }
        guard running else {
            throw MockSourceError.notRunning
        }

        let timestamp = Date()
        guard let jpeg = Self.makeStillJPEG(timestamp: timestamp) else {
            throw MockSourceError.stillRenderingFailed
        }
        return StillPhoto(jpeg: jpeg, timestamp: timestamp, origin: .mock, eager: eager)
    }

    /// Renders the labeled test card: dark background, the moving
    /// block motif in a corner, the word MOCK in large type and the
    /// capture timestamp under it. Encoded as JPEG with the long edge
    /// at AudioFormat.photoLongEdgePx and the contract quality, so a
    /// mock still is indistinguishable in shape from a real one.
    static func makeStillJPEG(timestamp: Date) -> Data? {
        let width = AudioFormat.photoLongEdgePx
        let height = width * 3 / 4

        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: bitmapInfo
        ) else {
            return nil
        }

        context.setFillColor(CGColor(red: 0.08, green: 0.10, blue: 0.14, alpha: 1.0))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))

        context.setFillColor(CGColor(red: 0.95, green: 0.75, blue: 0.20, alpha: 1.0))
        context.fill(CGRect(x: width - 176, y: height - 176, width: 112, height: 112))

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let timestampText = formatter.string(from: timestamp)

        drawText("MOCK", in: context, x: 64, baselineFromTop: 180, fontSize: 120)
        drawText(timestampText, in: context, x: 64, baselineFromTop: 280, fontSize: 44)
        drawText("InsightBridge mock still", in: context, x: 64, baselineFromTop: 348, fontSize: 32)

        guard let image = context.makeImage() else { return nil }

        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            output,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            return nil
        }
        let options: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: AudioFormat.photoJPEGQuality
        ]
        CGImageDestinationAddImage(destination, image, options as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return output as Data
    }

    /// Draws one line of white text with CoreText. CoreGraphics bitmap
    /// contexts have a bottom left origin, so the caller measures the
    /// baseline down from the top edge and this helper flips it.
    private static func drawText(
        _ text: String,
        in context: CGContext,
        x: CGFloat,
        baselineFromTop: CGFloat,
        fontSize: CGFloat
    ) {
        let font = CTFontCreateWithName("Helvetica-Bold" as CFString, fontSize, nil)
        let attributes: [CFString: Any] = [
            kCTFontAttributeName: font,
            kCTForegroundColorAttributeName: CGColor(red: 1, green: 1, blue: 1, alpha: 1)
        ]
        guard let attributed = CFAttributedStringCreate(
            kCFAllocatorDefault,
            text as CFString,
            attributes as CFDictionary
        ) else {
            return
        }
        let line = CTLineCreateWithAttributedString(attributed)
        let contextHeight = CGFloat(context.height)
        context.textPosition = CGPoint(x: x, y: contextHeight - baselineFromTop)
        CTLineDraw(line, context)
    }

    // MARK: Test hooks

    /// Simulates the glasses case closing: hingeClosed flips to true
    /// and a health update goes out immediately so a scripted drill
    /// does not have to wait for the next 500 ms tick. The source
    /// keeps running; whether hinge close triggers failover is
    /// SessionManager policy, not source policy.
    func simulateHingeClose() {
        stateLock.lock()
        hingeClosedFlag = true
        let running = isRunning && !isDead
        stateLock.unlock()
        if running {
            emitHealth()
        }
    }

    /// Reopens the simulated hinge, the counterpart drills need after
    /// a hinge close step.
    func simulateHingeOpen() {
        stateLock.lock()
        hingeClosedFlag = false
        let running = isRunning && !isDead
        stateLock.unlock()
        if running {
            emitHealth()
        }
    }

    /// Simulates sudden hardware death. Emits one final health update
    /// with alive false while the health stream is still open, then
    /// cancels all loops and finishes every stream. Terminal for this
    /// instance: start() after death throws MockSourceError.dead.
    func simulateDeath() {
        stateLock.lock()
        if isDead {
            stateLock.unlock()
            return
        }
        isDead = true
        isRunning = false
        streamsFinished = true
        stateLock.unlock()

        // Final health first, while consumers can still receive it.
        emitHealth()

        audioTask?.cancel()
        videoTask?.cancel()
        healthTask?.cancel()
        audioTask = nil
        videoTask = nil
        healthTask = nil

        audioContinuation.finish()
        videoContinuation.finish()
        healthContinuation.finish()
    }

    // MARK: Stop

    func stop() async {
        stateLock.lock()
        if streamsFinished {
            // Only simulateDeath() finishes the streams, and it is
            // terminal. A plain stop must stay restartable:
            // SessionManager holds one MockSource for the whole app
            // lifetime and reattaches it after failover drills, the
            // same lifecycle ExtentosGlassesSource follows.
            stateLock.unlock()
            return
        }
        isRunning = false
        stateLock.unlock()

        audioTask?.cancel()
        videoTask?.cancel()
        healthTask?.cancel()
        audioTask = nil
        videoTask = nil
        healthTask = nil

        // Continuations stay open so start() can run again on this
        // instance. deinit and simulateDeath() are the only paths
        // that finish them.
    }
}
