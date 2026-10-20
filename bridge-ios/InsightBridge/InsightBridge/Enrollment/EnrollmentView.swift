import SwiftUI
import UIKit

// One time speaker enrollment flow.
//
// Steps: name, face photo, 10 s voice sample, upload, done.
// No QR code UI anywhere: identity is server side face plus voice
// fusion (ArcFace and ECAPA embeddings stored on the server).
// Styling is intentionally plain; a template restyle comes later.

struct EnrollmentView: View {

    @StateObject private var viewModel = EnrollmentViewModel()
    @State private var showingCamera = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Enroll a speaker")
                    .font(.title2)
                    .fontWeight(.semibold)

                content
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .sheet(isPresented: $showingCamera) {
            CameraImagePicker { jpeg in
                viewModel.setPhoto(jpeg: jpeg)
                showingCamera = false
            } onCancel: {
                showingCamera = false
            }
            .ignoresSafeArea()
        }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.step {
        case .name:
            nameStep
        case .photo:
            photoStep
        case .voice:
            VoiceStepView(viewModel: viewModel)
        case .uploading:
            uploadingStep
        case .done(let result):
            doneStep(result)
        case .failed(let message):
            failedStep(message)
        }
    }

    // MARK: Step 1, name

    private var nameStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            stepLabel(1, "Name")

            Text("Look at the camera and say your name and one sentence when prompted")
                .font(.body)

            Text("Enrollment takes about 20 seconds. The server stores a face embedding and a voice embedding for this person so the glasses can recognize them later.")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            TextField("Full name", text: $viewModel.personName)
                .textFieldStyle(.roundedBorder)
                .textContentType(.name)
                .autocorrectionDisabled(false)

            Button("Continue") {
                viewModel.advanceFromName()
            }
            .buttonStyle(.borderedProminent)
            .disabled(viewModel.trimmedName.isEmpty)
        }
    }

    // MARK: Step 2, photo

    private var photoStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            stepLabel(2, "Face photo")

            Text("Face the front camera, fill the frame with the face, and keep a neutral expression in good light.")
                .font(.body)

            if let data = viewModel.photoJPEG,
               let image = UIImage(data: data) {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                HStack {
                    Button("Retake photo") {
                        showingCamera = true
                    }
                    .buttonStyle(.bordered)

                    Button("Use this photo") {
                        viewModel.advanceFromPhoto()
                    }
                    .buttonStyle(.borderedProminent)
                }
            } else {
                Button("Take photo") {
                    showingCamera = true
                }
                .buttonStyle(.borderedProminent)
            }

            Button("Back") {
                viewModel.backToName()
            }
            .buttonStyle(.borderless)
        }
    }

    // MARK: Step 4, uploading

    private var uploadingStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            stepLabel(4, "Uploading")

            HStack(spacing: 12) {
                ProgressView()
                Text("Sending photo and voice sample to the server...")
            }

            Text("The server computes the face and voice embeddings. This can take a few seconds.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Step 5, done

    private func doneStep(_ result: EnrollmentResult) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            stepLabel(5, "Enrolled")

            VStack(alignment: .leading, spacing: 10) {
                Text(result.name)
                    .font(.headline)

                enrollmentRow(label: "Face enrolled",
                              ok: result.faceEnrolled)
                enrollmentRow(label: "Voice enrolled",
                              ok: result.voiceEnrolled)

                Text("Person ID: \(result.personID)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            Button("Enroll another person") {
                viewModel.reset()
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private func enrollmentRow(label: String, ok: Bool) -> some View {
        Label(label,
              systemImage: ok ? "checkmark.circle.fill"
                              : "xmark.circle.fill")
            .foregroundStyle(ok ? Color.green : Color.red)
    }

    // MARK: Failure

    private func failedStep(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Enrollment failed")
                .font(.headline)

            Text(message)
                .font(.body)
                .foregroundStyle(.red)

            if viewModel.canSubmit {
                Button("Retry upload") {
                    viewModel.retryAfterFailure()
                }
                .buttonStyle(.borderedProminent)
            }

            Button("Start over") {
                viewModel.reset()
            }
            .buttonStyle(.bordered)
        }
    }

    // MARK: Helpers

    private func stepLabel(_ number: Int, _ title: String) -> some View {
        Text("Step \(number) of 5: \(title)")
            .font(.subheadline)
            .foregroundStyle(.secondary)
    }
}

// MARK: - Step 3, voice

private struct VoiceStepView: View {

    @ObservedObject var viewModel: EnrollmentViewModel
    @ObservedObject var recorder: VoiceSampleRecorder

    init(viewModel: EnrollmentViewModel) {
        self.viewModel = viewModel
        self.recorder = viewModel.recorder
    }

    private var remaining: Double {
        max(0, VoiceSampleRecorder.durationSeconds - recorder.elapsed)
    }

    private var progress: Double {
        min(1, max(0, recorder.elapsed
                      / VoiceSampleRecorder.durationSeconds))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Step 3 of 5: Voice sample")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Text("Tap record, then read this sentence out loud:")
                .font(.body)

            Text(viewModel.suggestedSentence)
                .font(.title3)
                .fontWeight(.medium)
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))

            // Countdown ring.
            HStack(spacing: 16) {
                ZStack {
                    Circle()
                        .stroke(Color(.systemGray5), lineWidth: 8)
                    Circle()
                        .trim(from: 0, to: progress)
                        .stroke(Color.accentColor,
                                style: StrokeStyle(lineWidth: 8,
                                                   lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    Text(String(format: "%.0f", remaining))
                        .font(.headline)
                        .monospacedDigit()
                }
                .frame(width: 72, height: 72)

                VStack(alignment: .leading, spacing: 6) {
                    Text(recorder.isRecording
                         ? "Recording..."
                         : (viewModel.voicePCM == nil
                            ? "Ready to record"
                            : "Sample captured"))
                        .font(.body)

                    Text("Level")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    ProgressView(value: Double(recorder.level))
                        .frame(width: 140)
                }
            }

            if recorder.isRecording {
                Button("Stop early") {
                    viewModel.stopVoiceRecording()
                }
                .buttonStyle(.bordered)
            } else if viewModel.voicePCM == nil {
                Button("Record 10 seconds") {
                    viewModel.recordVoice()
                }
                .buttonStyle(.borderedProminent)
            } else {
                HStack {
                    Button("Record again") {
                        viewModel.redoVoice()
                    }
                    .buttonStyle(.bordered)

                    Button("Enroll \(viewModel.trimmedName)") {
                        Task { await viewModel.submit() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!viewModel.canSubmit)
                }

                Text("Stopped early samples are padded with silence to exactly 10 seconds.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Button("Back") {
                viewModel.backToPhoto()
            }
            .buttonStyle(.borderless)
            .disabled(recorder.isRecording)
        }
    }
}

// MARK: - Camera picker

/// Minimal UIImagePickerController wrapper. Front camera is preferred
/// for enrollment, since the person enrolls themselves facing the
/// phone. Falls back to any available camera on devices without one.
private struct CameraImagePicker: UIViewControllerRepresentable {

    var onCapture: (Data) -> Void
    var onCancel: () -> Void

    func makeUIViewController(
        context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.delegate = context.coordinator
        if UIImagePickerController.isSourceTypeAvailable(.camera) {
            picker.sourceType = .camera
            if UIImagePickerController.isCameraDeviceAvailable(.front) {
                picker.cameraDevice = .front
            }
            picker.cameraCaptureMode = .photo
        } else {
            // Simulator and camera less devices: photo library keeps
            // the flow testable end to end.
            picker.sourceType = .photoLibrary
        }
        picker.allowsEditing = false
        return picker
    }

    func updateUIViewController(
        _ uiViewController: UIImagePickerController,
        context: Context) {
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onCapture: onCapture, onCancel: onCancel)
    }

    final class Coordinator: NSObject,
                             UIImagePickerControllerDelegate,
                             UINavigationControllerDelegate {
        let onCapture: (Data) -> Void
        let onCancel: () -> Void

        init(onCapture: @escaping (Data) -> Void,
             onCancel: @escaping () -> Void) {
            self.onCapture = onCapture
            self.onCancel = onCancel
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo
                info: [UIImagePickerController.InfoKey: Any]) {
            guard let image = info[.originalImage] as? UIImage,
                  let jpeg = image.jpegData(compressionQuality: 0.8)
            else {
                onCancel()
                return
            }
            onCapture(jpeg)
        }

        func imagePickerControllerDidCancel(
            _ picker: UIImagePickerController) {
            onCancel()
        }
    }
}

#Preview {
    EnrollmentView()
}
