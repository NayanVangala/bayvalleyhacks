import SwiftUI

// SettingsView
// Plain Form settings. Values save on edit via AppStorage,
// which writes to UserDefaults immediately.
// Note: server host and port are read by Config at client
// construction time in InsightBridgeApp, so a restart applies
// changed server values. Toggles for prefer-glasses and the
// mock source are also read at launch.

struct SettingsView: View {

    @EnvironmentObject private var sessionManager: SessionManager

    @AppStorage("onrecord.serverHost") private var serverHost: String = "192.168.2.1"
    @AppStorage("onrecord.serverPort") private var serverPort: Int = 8000
    @AppStorage("onrecord.preferGlasses") private var preferGlasses: Bool = true
    @AppStorage("onrecord.useMockSource") private var useMockSource: Bool = false

    var body: some View {
        Form {
            Section {
                TextField("Server host", text: $serverHost)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled(true)
                    .keyboardType(.URL)

                TextField("Server port", value: $serverPort, format: .number)
                    .keyboardType(.numberPad)
            } header: {
                Text("Server")
            } footer: {
                Text("Server changes apply after restarting the app.")
            }

            Section {
                Toggle("Prefer glasses at launch", isOn: $preferGlasses)
                Toggle("Use mock source (drills)", isOn: $useMockSource)
            } header: {
                Text("Capture")
            } footer: {
                Text("The mock source is for drills only. Turn it off for a live demo.")
            }

            Section {
                LabeledContent("Active origin", value: String(describing: sessionManager.activeOrigin))
                LabeledContent("Mode", value: modeText)
                LabeledContent("Glasses attached", value: sessionManager.isGlassesAttached ? "Yes" : "No")
                LabeledContent("State", value: stateText)
            } header: {
                Text("Audio source")
            }
        }
        .navigationTitle("Settings")
    }

    private var modeText: String {
        switch sessionManager.mode {
        case .glasses: return "Glasses"
        case .phone: return "Phone"
        case .reconnecting: return "Reconnecting"
        }
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
}
