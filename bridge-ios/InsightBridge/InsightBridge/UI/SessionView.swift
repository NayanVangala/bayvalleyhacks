import SwiftUI

// SessionView
// Main screen. One big hold-to-talk button, audio level,
// session controls, tag moment, and glasses attach or retry.
// Plain VStack layout. Styling stays functional only.

struct SessionView: View {

    @EnvironmentObject private var sessionManager: SessionManager

    @State private var isHolding = false
    @State private var startedAt: Date?
    @State private var lastElapsed: TimeInterval = 0

    private var canTalk: Bool {
        sessionManager.conversationState == .armed
            || sessionManager.conversationState == .recording
    }

    private var canTag: Bool {
        sessionManager.conversationState == .armed
            || sessionManager.conversationState == .recording
            || sessionManager.conversationState == .paused
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {

                // Readouts
                VStack(alignment: .leading, spacing: 6) {
                    LabeledContent("State", value: stateText)
                    LabeledContent("Mode", value: modeText)
                    LabeledContent("Active origin", value: String(describing: sessionManager.activeOrigin))
                    LabeledContent("Glasses attached", value: sessionManager.isGlassesAttached ? "Yes" : "No")
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        LabeledContent("Duration", value: durationText)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Divider()

                // Audio level
                VStack(alignment: .leading, spacing: 6) {
                    Text("Audio level")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    ProgressView(value: clampedLevel, total: 1.0)
                }

                // Hold-to-talk
                holdToTalkButton

                // Session controls
                HStack(spacing: 12) {
                    if sessionManager.conversationState == .idle
                        || sessionManager.conversationState == .ended {
                        Button("Start Session") {
                            sessionManager.startSession()
                        }
                        .buttonStyle(.borderedProminent)
                    }

                    if sessionManager.conversationState == .armed
                        || sessionManager.conversationState == .recording {
                        Button("Pause") {
                            sessionManager.pauseSession()
                        }
                        .buttonStyle(.bordered)
                    }

                    if sessionManager.conversationState == .paused {
                        Button("Resume") {
                            sessionManager.resumeSession()
                        }
                        .buttonStyle(.borderedProminent)
                    }

                    if sessionManager.conversationState != .idle
                        && sessionManager.conversationState != .ended {
                        Button("End Session", role: .destructive) {
                            sessionManager.endSession()
                        }
                        .buttonStyle(.bordered)
                    }
                }

                // Tag moment (pinch equivalent on phone)
                Button("Tag moment") {
                    sessionManager.tagMoment(source: "ui")
                }
                .buttonStyle(.bordered)
                .disabled(!canTag)

                // Attach or retry glasses
                if !sessionManager.isGlassesAttached {
                    if sessionManager.mode == .reconnecting {
                        Button("Retry Glasses") {
                            sessionManager.retryGlasses()
                        }
                        .buttonStyle(.bordered)
                    } else {
                        Button("Attach Glasses") {
                            Task {
                                await sessionManager.attachGlasses()
                            }
                        }
                        .buttonStyle(.bordered)
                    }
                }

                Spacer()
            }
            .padding(16)
        }
        .navigationTitle("Session")
        .onChange(of: sessionManager.conversationState) { _, newState in
            switch newState {
            case .armed, .recording:
                if startedAt == nil {
                    startedAt = Date()
                }
            case .ended, .idle:
                if let startedAt {
                    lastElapsed = Date().timeIntervalSince(startedAt)
                }
                if newState == .idle {
                    startedAt = nil
                    lastElapsed = 0
                }
            case .paused:
                break
            }
        }
    }

    // MARK: - Hold-to-talk

    private var holdToTalkButton: some View {
        let isRecording = sessionManager.conversationState == .recording
        return Text(isRecording ? "RECORDING - Release to stop" : "HOLD TO TALK")
            .font(.title3)
            .fontWeight(.bold)
            .foregroundColor(.white)
            .frame(maxWidth: .infinity)
            .frame(height: 160)
            .background(holdButtonColor)
            .cornerRadius(12)
            .opacity(canTalk ? 1.0 : 0.5)
            .onLongPressGesture(
                minimumDuration: 0.05,
                maximumDistance: 60,
                pressing: { pressing in
                    guard canTalk else { return }
                    if pressing {
                        guard !isHolding else { return }
                        isHolding = true
                        sessionManager.pressBegan()
                    } else {
                        guard isHolding else { return }
                        isHolding = false
                        sessionManager.pressEnded()
                    }
                },
                perform: {}
            )
            .disabled(!canTalk)
            .accessibilityLabel(isRecording ? "Recording. Release to stop." : "Hold to talk")
            .accessibilityHint("Press and hold to record a segment. Release to end the segment.")
    }

    private var holdButtonColor: Color {
        if sessionManager.conversationState == .recording {
            return .red
        }
        if canTalk {
            return .blue
        }
        return .gray
    }

    // MARK: - Helpers

    private var clampedLevel: Double {
        let level = Double(sessionManager.audioLevel)
        if level.isNaN { return 0 }
        return min(max(level, 0), 1)
    }

    private var durationText: String {
        let elapsed: TimeInterval
        if let startedAt,
           sessionManager.conversationState != .idle,
           sessionManager.conversationState != .ended {
            elapsed = Date().timeIntervalSince(startedAt)
        } else if sessionManager.conversationState == .ended {
            elapsed = lastElapsed
        } else {
            elapsed = 0
        }
        let total = Int(elapsed)
        let minutes = total / 60
        let seconds = total % 60
        return String(format: "%02d:%02d", minutes, seconds)
    }

    private var stateText: String {
        switch sessionManager.conversationState {
        case .idle: return "Idle"
        case .armed: return "Armed"
        case .recording: return "Recording"
        case .paused: return "Paused"
        case .ended: return "Ended"
        }
    }

    private var modeText: String {
        switch sessionManager.mode {
        case .glasses: return "Glasses"
        case .phone: return "Phone"
        case .reconnecting: return "Reconnecting"
        }
    }
}
