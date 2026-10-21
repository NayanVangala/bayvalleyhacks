import AVFoundation
import Combine
import Foundation

// Records exactly one 10 second voice enrollment sample from the
// phone microphone.
//
// Output contract: raw PCM16 little endian, 16 kHz, mono.
// Exactly 10.0 s, so exactly 320,000 bytes. Stopping early pads with
// silence (zero bytes). Running long is truncated. Callers hand the
// result straight to EnrollmentAPI.enroll(name:photoJPEG:voicePCM:),
// which base64-encodes it as voice_b64; the wire carries RAW PCM, no
// WAV header (the server rejects a RIFF header with 422).
//
// XCODE-VERIFY: confirm on the Mac that the recorder still outputs
// raw PCM16 little endian, 16 kHz, mono (2 bytes per sample, 320000
// bytes for 10 s) with no WAV header bytes, since the enrollment
// server contract takes raw PCM.
//
// Conversion discipline matches PhoneSource: the hardware format is
// converted directly to PCM16 16 kHz mono with a single
// AVAudioConverter. Samples are never written at an intermediate rate
// and resampled again later, so the byte stream is deterministic for a
// given input stream.

@MainActor
final class VoiceSampleRecorder: ObservableObject {

    // MARK: Constants

    static let durationSeconds: TimeInterval = 10.0
    static var targetByteCount: Int {
        AudioFormat.sampleRate * Int(durationSeconds) * 2
    }

    // MARK: Published state

    @Published private(set) var elapsed: TimeInterval = 0
    /// RMS level of the most recent converted chunk, 0.0 to 1.0.
    @Published private(set) var level: Float = 0
    @Published private(set) var isRecording: Bool = false

    /// Called on the main actor when recording finishes, either from
    /// `stop()` or from the automatic stop at 10.0 s. The data is
    /// always exactly `targetByteCount` bytes.
    var onFinish: ((Data) -> Void)?

    // MARK: Errors

    enum RecorderError: Error, LocalizedError {
        case microphoneDenied
        case noInputAvailable
        case converterUnavailable
        case sessionSetupFailed(String)

        var errorDescription: String? {
            switch self {
            case .microphoneDenied:
                return "Microphone access is denied. Enable it in Settings."
            case .noInputAvailable:
                return "No microphone input is available."
            case .converterUnavailable:
                return "Could not create the 16 kHz audio converter."
            case .sessionSetupFailed(let message):
                return "Audio session setup failed: \(message)"
            }
        }
    }

    // MARK: Audio graph (touched from the audio tap thread)

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private let targetFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: Double(AudioFormat.sampleRate),
        channels: 1,
        interleaved: true
    )!

    private let bufferLock = NSLock()
    private var pcmBuffer = Data()
    private var finishedPCM: Data?

    private var timer: Timer?

    // MARK: Permission

    /// Requests microphone permission. Returns true when granted.
    static func requestPermission() async -> Bool {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            return true
        case .denied:
            return false
        case .undetermined:
            return await AVAudioApplication.requestRecordPermission()
        @unknown default:
            return false
        }
    }

    // MARK: Start

    func start() throws {
        guard !isRecording else { return }

        if AVAudioApplication.shared.recordPermission == .denied {
            throw RecorderError.microphoneDenied
        }

        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.record, mode: .measurement, options: [])
            try session.setActive(true, options: [])
        } catch {
            throw RecorderError.sessionSetupFailed(error.localizedDescription)
        }

        let input = engine.inputNode
        guard input.numberOfInputs > 0 else {
            throw RecorderError.noInputAvailable
        }
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw RecorderError.noInputAvailable
        }
        guard let converter = AVAudioConverter(
            from: inputFormat, to: targetFormat) else {
            throw RecorderError.converterUnavailable
        }
        // Deterministic conversion settings: high quality sinc based
        // resampling, no dither surprises between runs on same input.
        converter.sampleRateConverterQuality =
            AVAudioQuality.high.rawValue
        self.converter = converter

        bufferLock.lock()
        pcmBuffer = Data()
        pcmBuffer.reserveCapacity(Self.targetByteCount)
        finishedPCM = nil
        bufferLock.unlock()

        elapsed = 0
        level = 0

        let targetFormat = self.targetFormat
        // Tap buffer size is a hint only. Conversion below uses actual
        // frame counts, so chunk sizes stay correct at any hint.
        input.installTap(onBus: 0,
                         bufferSize: 4096,
                         format: inputFormat) { [weak self] buffer, _ in
            self?.convertAndAppend(buffer,
                                   converter: converter,
                                   targetFormat: targetFormat)
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            teardownGraph()
            throw RecorderError.sessionSetupFailed(
                error.localizedDescription)
        }

        isRecording = true
        startTimer()
    }

    // MARK: Stop

    /// Stops recording and returns exactly 10.0 s of PCM, padded with
    /// silence when stopped early. Safe to call when not recording:
    /// returns the finished sample if one exists, otherwise 10.0 s of
    /// silence.
    @discardableResult
    func stop() -> Data {
        let data: Data
        if isRecording {
            data = finishRecording()
        } else if let finishedPCM {
            data = finishedPCM
        } else {
            data = Data(count: Self.targetByteCount)
        }
        return data
    }

    /// Discards any in progress recording and resets published state.
    func cancel() {
        if isRecording {
            teardownGraph()
            isRecording = false
        }
        timer?.invalidate()
        timer = nil
        bufferLock.lock()
        pcmBuffer = Data()
        finishedPCM = nil
        bufferLock.unlock()
        elapsed = 0
        level = 0
    }

    deinit {
        timer?.invalidate()
        if engine.isRunning {
            engine.stop()
        }
        engine.inputNode.removeTap(onBus: 0)
    }

    // MARK: Conversion (audio thread)

    private func convertAndAppend(_ buffer: AVAudioPCMBuffer,
                                  converter: AVAudioConverter,
                                  targetFormat: AVAudioFormat) {
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(
            (Double(buffer.frameLength) * ratio).rounded(.up)) + 16
        guard capacity > 0,
              let outBuffer = AVAudioPCMBuffer(
                pcmFormat: targetFormat, frameCapacity: capacity) else {
            return
        }

        var consumed = false
        var conversionError: NSError?
        let status = converter.convert(to: outBuffer,
                                       error: &conversionError) {
            _, outStatus in
            if consumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }

        guard status != .error,
              outBuffer.frameLength > 0,
              let channel = outBuffer.int16ChannelData else {
            return
        }

        let frameCount = Int(outBuffer.frameLength)
        let byteCount = frameCount * 2
        let chunk = Data(bytes: channel[0], count: byteCount)

        // RMS over this chunk for the live level meter.
        var sumSquares: Float = 0
        chunk.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            for sample in samples {
                let value = Float(sample) / Float(Int16.max)
                sumSquares += value * value
            }
        }
        let rms = frameCount > 0
            ? sqrt(sumSquares / Float(frameCount))
            : 0

        bufferLock.lock()
        if pcmBuffer.count < Self.targetByteCount {
            let remaining = Self.targetByteCount - pcmBuffer.count
            pcmBuffer.append(chunk.prefix(remaining))
        }
        let newCount = pcmBuffer.count
        bufferLock.unlock()

        let newElapsed = min(
            Self.durationSeconds,
            Double(newCount) / Double(AudioFormat.sampleRate * 2))
        DispatchQueue.main.async { [weak self] in
            guard let self, self.isRecording else { return }
            self.level = min(1, rms * 2.5)
            self.elapsed = newElapsed
        }
    }

    // MARK: Timer and finish

    private func startTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.05,
                                     repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.tick()
            }
        }
    }

    private func tick() {
        guard isRecording else { return }
        bufferLock.lock()
        let count = pcmBuffer.count
        bufferLock.unlock()
        elapsed = min(
            Self.durationSeconds,
            Double(count) / Double(AudioFormat.sampleRate * 2))
        if count >= Self.targetByteCount {
            _ = finishRecording()
        }
    }

    private func finishRecording() -> Data {
        teardownGraph()
        isRecording = false
        timer?.invalidate()
        timer = nil
        level = 0
        elapsed = Self.durationSeconds

        bufferLock.lock()
        var data = pcmBuffer
        pcmBuffer = Data()
        bufferLock.unlock()

        if data.count > Self.targetByteCount {
            data = Data(data.prefix(Self.targetByteCount))
        } else if data.count < Self.targetByteCount {
            // Pad with silence so the sample is always exactly 10.0 s.
            data.append(Data(count: Self.targetByteCount - data.count))
        }

        finishedPCM = data
        onFinish?(data)
        return data
    }

    private func teardownGraph() {
        if engine.isRunning {
            engine.stop()
        }
        engine.inputNode.removeTap(onBus: 0)
        converter = nil
        try? AVAudioSession.sharedInstance().setActive(
            false, options: [.notifyOthersOnDeactivation])
    }
}
