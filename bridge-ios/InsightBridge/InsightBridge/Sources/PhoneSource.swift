//
//  PhoneSource.swift
//  InsightBridge
//
//  The phone as a first class CaptureSource.
//
//  This is the deterministic shipping audio path and the full fallback
//  if the glasses die: PhoneSource runs the entire capture pipeline
//  alone under the same session, with no glasses hardware involved.
//  SessionManager can promote it mid session and downstream consumers
//  see the same AudioChunk, VideoFrame and SourceHealth contract,
//  only with origin .phone.
//
//  Audio rules (the part that must never drift):
//    - AVAudioSession category .playAndRecord, plain. No .defaultToSpeaker,
//      no voice chat processing. The mic hears the room as it is.
//    - The AVAudioEngine input tap feeds a persistent AVAudioConverter.
//    - The converter output format is asserted in code: PCM16, mono,
//      AudioFormat.sampleRate (16000 Hz). Hardware buffers are never
//      passed through. If the hardware runs at 48 kHz or anything
//      else, it is converted, every buffer, every time.
//    - Converted samples accumulate until an exact 100 ms frame is
//      available, then one AudioChunk of exactly AudioFormat.frameBytes
//      (3200 bytes) is emitted with the next sequence number.
//
//  Video rules:
//    - Rear camera on an AVCaptureSession, low preset to save battery.
//    - Video data output is throttled to about 2 fps. Downstream only
//      needs liveness plus stills, not a movie.
//    - captureStill uses AVCapturePhotoOutput, then downscales to
//      AudioFormat.photoLongEdgePx on the long edge and re-encodes as
//      JPEG at AudioFormat.photoJPEGQuality.
//
//  Health is emitted every 500 ms. alive means the audio engine is
//  running and the capture session is running. Phones have no hinge,
//  so hingeClosed is always false.
//
//  No Meta SDK is imported in this file. AVFoundation only, plus
//  UIKit for UIDevice battery level, which the health contract needs.
//

import AVFoundation
import CoreVideo
import Foundation
import ImageIO
import UIKit
import UniformTypeIdentifiers

// MARK: - Errors

/// Typed failures SessionManager can react to. The important one is
/// permissionDenied: SessionManager shows the permissions banner for
/// it instead of treating the source as dead hardware.
enum PhoneSourceError: Error, LocalizedError {
    case permissionDenied
    case microphoneUnavailable
    case cameraUnavailable
    case audioConverterUnavailable
    case captureSessionSetupFailed(String)
    case notRunning
    case photoCaptureFailed(String)
    case photoDataUnavailable

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Microphone or camera permission was denied for the phone source."
        case .microphoneUnavailable:
            return "No usable microphone input was found on this phone."
        case .cameraUnavailable:
            return "No usable rear camera was found on this phone."
        case .audioConverterUnavailable:
            return "Could not create the converter to 16 kHz PCM16 audio."
        case .captureSessionSetupFailed(let detail):
            return "Phone capture session setup failed: \(detail)"
        case .notRunning:
            return "PhoneSource is not running."
        case .photoCaptureFailed(let detail):
            return "Phone still capture failed: \(detail)"
        case .photoDataUnavailable:
            return "Phone still capture produced no image data."
        }
    }
}

// MARK: - PhoneSource

final class PhoneSource: NSObject, CaptureSource {

    // MARK: CaptureSource contract

    let origin: CaptureOrigin = .phone

    let audio: AsyncStream<AudioChunk>
    let video: AsyncStream<VideoFrame>
    let health: AsyncStream<SourceHealth>

    private let audioContinuation: AsyncStream<AudioChunk>.Continuation
    private let videoContinuation: AsyncStream<VideoFrame>.Continuation
    private let healthContinuation: AsyncStream<SourceHealth>.Continuation

    // MARK: Audio state

    private let audioEngine = AVAudioEngine()
    private var audioConverter: AVAudioConverter?
    private var targetAudioFormat: AVAudioFormat?

    /// Guards pendingPCM and nextAudioSeq. The tap runs on a realtime
    /// audio thread, so this lock is held only for memcpy scale work.
    private let audioStateLock = NSLock()
    private var pendingPCM = Data()
    private var nextAudioSeq: UInt64 = 0

    // MARK: Video state

    private let captureSession = AVCaptureSession()
    private let videoDataOutput = AVCaptureVideoDataOutput()
    private let photoOutput = AVCapturePhotoOutput()
    private let sessionQueue = DispatchQueue(label: "com.insightbridge.phonesource.session")
    private let videoStateLock = NSLock()
    private var lastVideoFrameDate: Date?
    private var lastYieldedVideoDate: Date?
    /// Minimum spacing between yielded VideoFrames: about 2 fps.
    private let videoYieldInterval: TimeInterval = 0.5

    // MARK: Photo state

    private struct PendingPhoto {
        let continuation: CheckedContinuation<StillPhoto, Error>
        let eager: Bool
    }

    private let photoStateLock = NSLock()
    private var pendingPhotos: [Int64: PendingPhoto] = [:]

    // MARK: Lifecycle state

    private let lifecycleLock = NSLock()
    private var isRunning = false
    private var streamsFinished = false
    private var startedAt: Date?
    private var healthTask: Task<Void, Never>?
    private var batteryMonitoringEnabledByUs = false

    // MARK: Init

    override init() {
        let audioStream = AsyncStream<AudioChunk>.makeStream()
        let videoStream = AsyncStream<VideoFrame>.makeStream()
        let healthStream = AsyncStream<SourceHealth>.makeStream()
        self.audio = audioStream.stream
        self.video = videoStream.stream
        self.health = healthStream.stream
        self.audioContinuation = audioStream.continuation
        self.videoContinuation = videoStream.continuation
        self.healthContinuation = healthStream.continuation
        super.init()
    }

    deinit {
        audioContinuation.finish()
        videoContinuation.finish()
        healthContinuation.finish()
    }

    // MARK: Permissions

    /// Asks for microphone and camera access. Returns true only when
    /// both are granted. Denial is a plain false here: start() turns
    /// it into PhoneSourceError.permissionDenied so SessionManager can
    /// show its banner.
    static func requestPermissions() async -> Bool {
        let micGranted = await requestMicrophonePermission()
        let cameraGranted = await requestCameraPermission()
        return micGranted && cameraGranted
    }

    private static func requestMicrophonePermission() async -> Bool {
        let session = AVAudioSession.sharedInstance()
        switch session.recordPermission {
        case .granted:
            return true
        case .denied:
            return false
        case .undetermined:
            return await withCheckedContinuation { continuation in
                session.requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            }
        @unknown default:
            return false
        }
    }

    private static func requestCameraPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            return true
        case .denied, .restricted:
            return false
        case .notDetermined:
            return await AVCaptureDevice.requestAccess(for: .video)
        @unknown default:
            return false
        }
    }

    private static var permissionsAlreadyDenied: Bool {
        let micDenied = AVAudioSession.sharedInstance().recordPermission == .denied
        let cameraStatus = AVCaptureDevice.authorizationStatus(for: .video)
        let cameraDenied = cameraStatus == .denied || cameraStatus == .restricted
        return micDenied || cameraDenied
    }

    // MARK: Start

    func start() async throws {
        lifecycleLock.lock()
        if isRunning {
            lifecycleLock.unlock()
            return
        }
        if streamsFinished {
            lifecycleLock.unlock()
            throw PhoneSourceError.notRunning
        }
        lifecycleLock.unlock()

        // Plain denial handling: if either permission is already
        // denied, fail fast with the typed error. If a permission is
        // simply undecided, ask once here and fail the same way when
        // the user says no.
        if Self.permissionsAlreadyDenied {
            throw PhoneSourceError.permissionDenied
        }
        let granted = await Self.requestPermissions()
        guard granted else {
            throw PhoneSourceError.permissionDenied
        }

        audioStateLock.lock()
        pendingPCM.removeAll(keepingCapacity: true)
        nextAudioSeq = 0
        audioStateLock.unlock()

        videoStateLock.lock()
        lastVideoFrameDate = nil
        lastYieldedVideoDate = nil
        videoStateLock.unlock()

        try startAudioEngine()
        try await configureAndStartCaptureSession()

        if !UIDevice.current.isBatteryMonitoringEnabled {
            UIDevice.current.isBatteryMonitoringEnabled = true
            batteryMonitoringEnabledByUs = true
        }

        lifecycleLock.lock()
        isRunning = true
        startedAt = Date()
        lifecycleLock.unlock()

        startHealthLoop()
    }

    private func startAudioEngine() throws {
        let session = AVAudioSession.sharedInstance()
        // Plain .playAndRecord on purpose: no .defaultToSpeaker, no
        // mode that turns on voice processing. Deterministic in,
        // deterministic out.
        try session.setCategory(.playAndRecord, mode: .default, options: [])
        try session.setActive(true)

        let inputNode = audioEngine.inputNode
        let hardwareFormat = inputNode.outputFormat(forBus: 0)
        guard hardwareFormat.sampleRate > 0, hardwareFormat.channelCount > 0 else {
            throw PhoneSourceError.microphoneUnavailable
        }

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: Double(AudioFormat.sampleRate),
            channels: 1,
            interleaved: true
        ) else {
            throw PhoneSourceError.audioConverterUnavailable
        }

        // The deterministic contract, asserted not assumed: output is
        // 16 kHz PCM16 mono no matter what the hardware reported.
        assert(AudioFormat.sampleRate == 16000, "AudioFormat.sampleRate must stay 16000")
        assert(Int(targetFormat.sampleRate) == AudioFormat.sampleRate)
        precondition(AudioFormat.frameBytes == 3200, "100 ms at 16 kHz PCM16 mono is 3200 bytes")

        guard let converter = AVAudioConverter(from: hardwareFormat, to: targetFormat) else {
            throw PhoneSourceError.audioConverterUnavailable
        }
        converter.sampleRateConverterQuality = .high
        audioConverter = converter
        targetAudioFormat = targetFormat

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: hardwareFormat) { [weak self] buffer, _ in
            self?.handleInputBuffer(buffer)
        }
        audioEngine.prepare()
        try audioEngine.start()
    }

    /// Converts one hardware buffer to the target format and slices
    /// the result into exact frame sized chunks. Runs on the audio
    /// tap thread: no allocation heavy work beyond the conversion
    /// buffer, no locks held while yielding.
    private func handleInputBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let converter = audioConverter, let targetFormat = targetAudioFormat else { return }

        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard capacity > 0,
              let outputBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
            return
        }

        var conversionError: NSError?
        var inputConsumed = false
        let status = converter.convert(to: outputBuffer, error: &conversionError) { _, outStatus in
            if inputConsumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            inputConsumed = true
            outStatus.pointee = .haveData
            return buffer
        }

        guard status != .error,
              outputBuffer.frameLength > 0,
              let channels = outputBuffer.int16ChannelData else {
            return
        }

        // Interleaved mono PCM16: frameLength samples, 2 bytes each.
        let byteCount = Int(outputBuffer.frameLength) * MemoryLayout<Int16>.size
        let converted = Data(bytes: channels[0], count: byteCount)
        appendPCM(converted)
    }

    private func appendPCM(_ data: Data) {
        var chunks: [AudioChunk] = []
        audioStateLock.lock()
        pendingPCM.append(data)
        while pendingPCM.count >= AudioFormat.frameBytes {
            let frame = Data(pendingPCM.prefix(AudioFormat.frameBytes))
            pendingPCM.removeFirst(AudioFormat.frameBytes)
            precondition(frame.count == AudioFormat.frameBytes)
            let chunk = AudioChunk(
                seq: nextAudioSeq,
                pcm: frame,
                sampleRate: AudioFormat.sampleRate,
                timestamp: Date(),
                origin: .phone
            )
            nextAudioSeq += 1
            chunks.append(chunk)
        }
        audioStateLock.unlock()

        for chunk in chunks {
            audioContinuation.yield(chunk)
        }
    }

    private func configureAndStartCaptureSession() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            sessionQueue.async { [self] in
                do {
                    try configureCaptureSessionLocked()
                    captureSession.startRunning()
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    /// Must be called on sessionQueue.
    private func configureCaptureSessionLocked() throws {
        guard captureSession.inputs.isEmpty else { return }

        captureSession.beginConfiguration()
        defer { captureSession.commitConfiguration() }

        // Low preset: downstream needs liveness and stills, and the
        // phone may have to carry a whole session on its own battery.
        captureSession.sessionPreset = .vga640x480

        let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
            ?? AVCaptureDevice.default(for: .video)
        guard let camera else {
            throw PhoneSourceError.cameraUnavailable
        }

        let input: AVCaptureDeviceInput
        do {
            input = try AVCaptureDeviceInput(device: camera)
        } catch {
            throw PhoneSourceError.captureSessionSetupFailed(error.localizedDescription)
        }
        guard captureSession.canAddInput(input) else {
            throw PhoneSourceError.captureSessionSetupFailed("cannot add camera input")
        }
        captureSession.addInput(input)

        videoDataOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        videoDataOutput.alwaysDiscardsLateVideoFrames = true
        videoDataOutput.setSampleBufferDelegate(self, queue: sessionQueue)
        guard captureSession.canAddOutput(videoDataOutput) else {
            throw PhoneSourceError.captureSessionSetupFailed("cannot add video data output")
        }
        captureSession.addOutput(videoDataOutput)

        photoOutput.maxPhotoQualityPrioritization = .quality
        guard captureSession.canAddOutput(photoOutput) else {
            throw PhoneSourceError.captureSessionSetupFailed("cannot add photo output")
        }
        captureSession.addOutput(photoOutput)
    }

    // MARK: Health

    private func startHealthLoop() {
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.emitHealth()
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    private func emitHealth() async {
        let batteryLevel = await MainActor.run { () -> Double? in
            let level = UIDevice.current.batteryLevel
            return level >= 0 ? Double(level) : nil
        }

        lifecycleLock.lock()
        let running = isRunning
        let start = startedAt
        lifecycleLock.unlock()

        videoStateLock.lock()
        let lastFrame = lastVideoFrameDate
        videoStateLock.unlock()

        let now = Date()
        let frameAge: TimeInterval
        if let lastFrame {
            frameAge = now.timeIntervalSince(lastFrame)
        } else if let start {
            frameAge = now.timeIntervalSince(start)
        } else {
            frameAge = 0
        }

        let engineRunning = audioEngine.isRunning
        let sessionRunning = captureSession.isRunning
        let alive = running && engineRunning && sessionRunning

        let detail: String
        if !alive {
            detail = "phone source not fully running (engine \(engineRunning), session \(sessionRunning))"
        } else if lastFrame == nil {
            detail = "phone ok, waiting for first video frame"
        } else {
            detail = "phone ok"
        }

        let health = SourceHealth(
            origin: .phone,
            alive: alive,
            batteryLevel: batteryLevel,
            hingeClosed: false,
            lastFrameAge: frameAge,
            detail: detail
        )
        healthContinuation.yield(health)
    }

    // MARK: Still capture

    func captureStill(eager: Bool) async throws -> StillPhoto {
        lifecycleLock.lock()
        let running = isRunning
        lifecycleLock.unlock()
        guard running else {
            throw PhoneSourceError.notRunning
        }

        return try await withCheckedThrowingContinuation { continuation in
            let settings = AVCapturePhotoSettings()
            settings.flashMode = .off
            // Eager stills feed identity enrollment and want speed.
            // Lazy stills can take the quality path.
            settings.maxPhotoQualityPrioritization = eager ? .speed : .quality

            let id = settings.uniqueID
            photoStateLock.lock()
            pendingPhotos[id] = PendingPhoto(continuation: continuation, eager: eager)
            photoStateLock.unlock()

            sessionQueue.async { [self] in
                photoOutput.capturePhoto(with: settings, delegate: self)
            }
        }
    }

    /// Downscales image data so the long edge is at most maxLongEdge,
    /// then re-encodes as JPEG at the given quality. If anything in
    /// the ImageIO path fails, the encoded full size image is used as
    /// a fallback: a still that is too big beats no still at all.
    static func downscaledJPEG(from data: Data, maxLongEdge: Int, quality: Double) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else {
            return nil
        }

        let thumbnailOptions: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxLongEdge
        ]
        let image = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbnailOptions as CFDictionary)
            ?? CGImageSourceCreateImageAtIndex(source, 0, nil)
        guard let image else { return nil }

        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            output,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            return nil
        }
        let encodeOptions: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: quality
        ]
        CGImageDestinationAddImage(destination, image, encodeOptions as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return output as Data
    }

    // MARK: Stop

    func stop() async {
        lifecycleLock.lock()
        if streamsFinished {
            lifecycleLock.unlock()
            return
        }
        isRunning = false
        streamsFinished = true
        lifecycleLock.unlock()

        healthTask?.cancel()
        healthTask = nil

        // Audio first: no new chunks after stop begins.
        if audioEngine.isRunning {
            audioEngine.inputNode.removeTap(onBus: 0)
            audioEngine.stop()
        } else {
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        audioConverter = nil
        targetAudioFormat = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])

        // Capture session stops on its own queue, per AVFoundation
        // guidance, and we wait for it so stop means stopped.
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            sessionQueue.async { [self] in
                if captureSession.isRunning {
                    captureSession.stopRunning()
                }
                continuation.resume()
            }
        }

        // Any still in flight fails instead of hanging forever.
        photoStateLock.lock()
        let orphaned = pendingPhotos
        pendingPhotos.removeAll()
        photoStateLock.unlock()
        for pending in orphaned.values {
            pending.continuation.resume(throwing: PhoneSourceError.notRunning)
        }

        if batteryMonitoringEnabledByUs {
            UIDevice.current.isBatteryMonitoringEnabled = false
            batteryMonitoringEnabledByUs = false
        }

        audioContinuation.finish()
        videoContinuation.finish()
        healthContinuation.finish()
    }
}

// MARK: - AVCaptureVideoDataOutputSampleBufferDelegate

extension PhoneSource: AVCaptureVideoDataOutputSampleBufferDelegate {

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let now = Date()

        videoStateLock.lock()
        lastVideoFrameDate = now
        let shouldYield: Bool
        if let lastYield = lastYieldedVideoDate {
            shouldYield = now.timeIntervalSince(lastYield) >= videoYieldInterval
        } else {
            shouldYield = true
        }
        if shouldYield {
            lastYieldedVideoDate = now
        }
        videoStateLock.unlock()

        // Every frame updates liveness above. Only about 2 fps are
        // yielded downstream; the rest are dropped on purpose.
        guard shouldYield else { return }
        let frame = VideoFrame(pixelBuffer: pixelBuffer, timestamp: now, origin: .phone)
        videoContinuation.yield(frame)
    }
}

// MARK: - AVCapturePhotoCaptureDelegate

extension PhoneSource: AVCapturePhotoCaptureDelegate {

    func photoOutput(
        _ output: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        let id = photo.resolvedSettings.uniqueID
        photoStateLock.lock()
        let pending = pendingPhotos.removeValue(forKey: id)
        photoStateLock.unlock()

        guard let pending else { return }

        if let error {
            pending.continuation.resume(
                throwing: PhoneSourceError.photoCaptureFailed(error.localizedDescription)
            )
            return
        }

        guard let rawData = photo.fileDataRepresentation() else {
            pending.continuation.resume(throwing: PhoneSourceError.photoDataUnavailable)
            return
        }

        let jpeg = Self.downscaledJPEG(
            from: rawData,
            maxLongEdge: AudioFormat.photoLongEdgePx,
            quality: AudioFormat.photoJPEGQuality
        ) ?? rawData

        let still = StillPhoto(
            jpeg: jpeg,
            timestamp: Date(),
            origin: .phone,
            eager: pending.eager
        )
        pending.continuation.resume(returning: still)
    }
}
