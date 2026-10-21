import Foundation
import SwiftUI

// Drives the one time per person enrollment flow (about 20 s):
// name, face photo, 10 s voice sample, upload, done.
//
// Identity is server side. This view model only captures media and
// hands it to EnrollmentAPI. The server computes and stores the
// ArcFace face embedding and the ECAPA voice embedding.

@MainActor
final class EnrollmentViewModel: ObservableObject {

    enum Step: Equatable {
        case name
        case photo
        case voice
        case uploading
        case done(EnrollmentResult)
        case failed(String)
    }

    @Published var step: Step = .name
    @Published var personName: String = ""

    /// JPEG bytes from the camera step. Set through `setPhoto(jpeg:)`.
    @Published private(set) var photoJPEG: Data?
    /// Raw PCM16 16 kHz mono, always exactly 320,000 bytes once set.
    @Published private(set) var voicePCM: Data?

    /// Exposed so the view can bind to elapsed, level, and isRecording.
    let recorder = VoiceSampleRecorder()

    private let api: EnrollmentAPI

    init(api: EnrollmentAPI = EnrollmentAPI()) {
        self.api = api
        recorder.onFinish = { [weak self] pcm in
            guard let self else { return }
            // onFinish fires on the main actor (recorder is MainActor
            // isolated apart from its audio tap internals).
            self.voicePCM = pcm
        }
    }

    var trimmedName: String {
        personName.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var suggestedSentence: String {
        let displayName = trimmedName.isEmpty ? "your name" : trimmedName
        return "My name is \(displayName). I am happy to be here today."
    }

    var canSubmit: Bool {
        photoJPEG != nil && voicePCM != nil && !trimmedName.isEmpty
    }

    // MARK: Name step

    func advanceFromName() {
        guard !trimmedName.isEmpty else { return }
        step = .photo
    }

    func backToName() {
        recorder.cancel()
        step = .name
    }

    // MARK: Photo step

    /// Called by the camera wrapper in EnrollmentView with JPEG data
    /// for the captured image.
    func setPhoto(jpeg: Data) {
        guard !jpeg.isEmpty else { return }
        photoJPEG = jpeg
    }

    func clearPhoto() {
        photoJPEG = nil
    }

    func advanceFromPhoto() {
        guard photoJPEG != nil else { return }
        step = .voice
    }

    func backToPhoto() {
        recorder.cancel()
        voicePCM = nil
        step = .photo
    }

    // MARK: Voice step

    /// Starts the 10 s voice sample. Requests microphone permission
    /// first. The recorder auto stops at 10.0 s and `onFinish` stores
    /// the padded PCM in `voicePCM`.
    func recordVoice() {
        guard !recorder.isRecording else { return }
        voicePCM = nil
        Task {
            let granted = await VoiceSampleRecorder.requestPermission()
            guard granted else {
                step = .failed(
                    "Microphone access is needed for the voice sample. "
                    + "Enable it in Settings, then try again.")
                return
            }
            do {
                try recorder.start()
            } catch {
                step = .failed(error.localizedDescription)
            }
        }
    }

    /// Stops early. The recorder pads with silence to exactly 10.0 s,
    /// and the padded data lands in `voicePCM` via `onFinish`.
    func stopVoiceRecording() {
        guard recorder.isRecording else { return }
        _ = recorder.stop()
    }

    /// Discards the current voice sample so it can be recorded again.
    func redoVoice() {
        recorder.cancel()
        voicePCM = nil
    }

    // MARK: Upload

    func submit() async {
        guard let photoJPEG else {
            step = .failed("No photo captured yet. Go back and retake it.")
            return
        }
        guard let voicePCM else {
            step = .failed("No voice sample recorded yet.")
            return
        }
        guard !trimmedName.isEmpty else {
            step = .failed("Name is empty.")
            return
        }

        step = .uploading
        // XCODE-VERIFY: the voice contract is RAW PCM16 little endian,
        // 16 kHz, mono with NO WAV header (base64 of the raw bytes;
        // the server rejects a RIFF header with 422).
        // VoiceSampleRecorder produces exactly that (see its output
        // contract). Do not wrap this in WAV here.
        let voicePCM = voicePCM

        do {
            let result = try await api.enroll(
                name: trimmedName,
                photoJPEG: photoJPEG,
                voicePCM: voicePCM)
            step = .done(result)
        } catch let error as EnrollmentError {
            step = .failed(error.localizedDescription)
        } catch {
            step = .failed(error.localizedDescription)
        }
    }

    /// Retries the upload after a failure, keeping the captured photo
    /// and voice sample. Falls back to the name step if the captures
    /// are gone.
    func retryAfterFailure() {
        if canSubmit {
            Task { await submit() }
        } else {
            reset()
        }
    }

    // MARK: Reset

    /// Clears everything so the next person can enroll back to back.
    func reset() {
        recorder.cancel()
        personName = ""
        photoJPEG = nil
        voicePCM = nil
        step = .name
    }
}
