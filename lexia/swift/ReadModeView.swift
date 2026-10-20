// Lexia Read Mode for OnRecord. Uncompiled Swift source written Oct 2026. Bridge team: copy into the bridge-ios target and wire the marked integration points.
//
// ReadModeView.swift
// SwiftUI skeleton for Read Mode on the phone and, in reduced form, on
// the lens. It intentionally uses only system default styling. The bridge
// team applies the OnRecord visual design and the lens layout constraints.
// The view shows exactly one chunk at a time through LensChunkDisplayModel.

import SwiftUI

public struct ReadModeView: View {

    // MARK: - State

    public enum State: Equatable {
        case idle
        case capturing
        case loading
        case reading
        case error(String)
    }

    @StateObject private var displayModel: LensChunkDisplayModel
    @State private var viewState: State = .idle

    private let apiClient: LexiaAPIClient
    private let captureSource: any ReadCaptureSource
    private let simplify: Bool

    // INTEGRATION POINT: the bridge app owns the trigger handler instance.
    // Wire its closures in the bridge app like this:
    //   triggerHandler.onPinch = { Task { await viewModelStartCapture() } }
    //   triggerHandler.onTap = { Task { await viewModelStartCapture() } }
    // Or set the closures in onAppear below if this view owns the handler.
    private let triggerHandler: ReadTriggerHandler?

    public init(
        apiClient: LexiaAPIClient,
        captureSource: any ReadCaptureSource,
        displayModel: LensChunkDisplayModel? = nil,
        triggerHandler: ReadTriggerHandler? = nil,
        simplify: Bool = false
    ) {
        self.apiClient = apiClient
        self.captureSource = captureSource
        self.triggerHandler = triggerHandler
        self.simplify = simplify
        _displayModel = StateObject(wrappedValue: displayModel ?? LensChunkDisplayModel())
    }

    // MARK: - Body

    public var body: some View {
        VStack(spacing: 16) {
            content
            controls
        }
        .padding()
        .onAppear {
            triggerHandler?.onPinch = {
                Task { await startCapture() }
            }
            triggerHandler?.onTap = {
                Task { await startCapture() }
            }
        }
    }

    // MARK: - Content by state

    @ViewBuilder
    private var content: some View {
        switch viewState {
        case .idle:
            Text("Read Mode")
                .font(.title)
            Text("Pinch or tap Read this to capture the text in view.")
                .multilineTextAlignment(.center)

        case .capturing:
            ProgressView()
            Text("Capturing")

        case .loading:
            ProgressView()
            Text("Reading text")

        case .reading:
            if let chunk = displayModel.currentChunk {
                Text(chunk.text)
                    .font(.largeTitle)
                    .multilineTextAlignment(.center)
                Text(displayModel.positionLabel)
                    .font(.caption)
            } else {
                Text("No text found.")
            }

        case .error(let message):
            Text("Something went wrong")
                .font(.headline)
            Text(message)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Controls

    private var controls: some View {
        VStack(spacing: 12) {
            Button("Read this") {
                if let triggerHandler {
                    triggerHandler.triggerTap()
                } else {
                    Task { await startCapture() }
                }
            }
            .disabled(viewState == .capturing || viewState == .loading)

            if viewState == .reading {
                HStack(spacing: 24) {
                    Button("Previous") {
                        displayModel.previous()
                    }
                    .disabled(displayModel.isAtStart)

                    Button("Next") {
                        displayModel.next()
                    }
                    .disabled(displayModel.isAtEnd)
                }

                Button(displayModel.isSpeaking ? "Speaking" : "Read Aloud") {
                    Task { await readAloud() }
                }
                .disabled(displayModel.isSpeaking || displayModel.sessionID == nil)
            }

            if case .error = viewState {
                Button("Try Again") {
                    viewState = .idle
                }
            }
        }
    }

    // MARK: - Actions

    /// Full capture flow: capture a still, send it to Lexia, load chunks.
    private func startCapture() async {
        viewState = .capturing
        do {
            let imageData = try await captureSource.captureStill()
            viewState = .loading
            let response = try await apiClient.read(imageData: imageData, simplify: simplify)
            displayModel.load(response)
            viewState = displayModel.hasChunks ? .reading : .error("No readable text was found in that image.")
        } catch let error as LexiaError {
            viewState = .error(error.localizedDescription)
        } catch {
            viewState = .error(error.localizedDescription)
        }
    }

    /// Asks Lexia to synthesize speech for the current session.
    private func readAloud() async {
        guard let sessionID = displayModel.sessionID else {
            viewState = .error(LexiaError.noSession.localizedDescription)
            return
        }
        displayModel.isSpeaking = true
        defer { displayModel.isSpeaking = false }
        do {
            let speakResponse = try await apiClient.speak(sessionID: sessionID)
            guard let url = apiClient.audioURL(for: speakResponse) else {
                viewState = .error("Lexia returned an invalid audio URL.")
                return
            }
            // INTEGRATION POINT: audio playback is wired by the bridge team.
            // Download or stream `url` and play it with AVAudioPlayer or
            // AVPlayer here, route audio to the glasses speaker when the
            // bridge audio session is configured for it, and set
            // displayModel.isSpeaking back to false when playback finishes.
            // SpeakResponse.durationSeconds can drive a progress timer.
            _ = url
            _ = speakResponse.durationSeconds
        } catch let error as LexiaError {
            viewState = .error(error.localizedDescription)
        } catch {
            viewState = .error(error.localizedDescription)
        }
    }
}

// MARK: - Preview

#if DEBUG
struct ReadModeView_Previews: PreviewProvider {
    static var previews: some View {
        // Preview uses a stub capture source, so tapping Read this in the
        // preview surfaces the not implemented error state. That is expected.
        let client = LexiaAPIClient(baseURL: URL(string: "http://localhost:8100")!)
        return ReadModeView(
            apiClient: client,
            captureSource: PhoneCameraCaptureStub()
        )
    }
}
#endif
