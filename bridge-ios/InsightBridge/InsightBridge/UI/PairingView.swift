import SwiftUI

// PairingView
// Instructional pairing flow for the glasses.
// Registration calls go through DATRegistration only.
// No Meta SDK imports in this view.

struct PairingView: View {

    @EnvironmentObject private var registration: DATRegistration
    @EnvironmentObject private var sessionManager: SessionManager

    @State private var isDiscovering = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {

                Text("Pair glasses")
                    .font(.title2)
                    .fontWeight(.bold)

                VStack(alignment: .leading, spacing: 10) {
                    Text("1. Enable Developer Mode in the Meta AI app.")
                    Text("Open the Meta AI app, go to Settings, then App Info, and tap the app version 5 times to reveal the Developer Mode toggle. Turn Developer Mode on.")
                    Text("2. Pair the glasses to THIS phone.")
                    Text("Pair the glasses in the Meta AI app on this phone, not on another phone. The bridge can only use glasses paired to this device.")
                    Text("3. Return here and tap Pair glasses below.")
                    Text("4. Once paired, go to the Session tab and tap Attach Glasses if the glasses do not attach on their own.")
                }
                .font(.body)

                Divider()

                VStack(alignment: .leading, spacing: 6) {
                    Text("Registration state")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(String(describing: registration.registrationState))
                        .font(.body)
                }

                Button {
                    isDiscovering = true
                    Task {
                        await registration.discoverAndSelectDevice()
                        isDiscovering = false
                    }
                } label: {
                    Text(isDiscovering ? "Searching..." : "Pair glasses")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .buttonStyle(.borderedProminent)
                .disabled(isDiscovering)

                if !sessionManager.isGlassesAttached {
                    Button("Attach Glasses") {
                        Task {
                            await sessionManager.attachGlasses()
                        }
                    }
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
                }

                Divider()

                Text("Note: Developer mode in this build uses MetaAppID 0. No production Meta app ID is configured.")
                    .font(.footnote)
                    .foregroundColor(.secondary)

                Spacer()
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Pairing")
    }
}
