import SwiftUI

// RootView
// Top level layout. Status banner is pinned at the top in all
// tabs and the kill switch is pinned at the bottom in all tabs.
// Neither one lives inside a tab, so switching tabs can never
// hide them.

struct RootView: View {

    var body: some View {
        VStack(spacing: 0) {
            StatusBannerView()

            TabView {
                SessionView()
                    .tabItem {
                        Label("Session", systemImage: "mic.fill")
                    }

                MoreView()
                    .tabItem {
                        Label("More", systemImage: "ellipsis.circle")
                    }
            }

            KillSwitchView()
        }
    }
}

// More tab: settings plus entry points for pairing and enrollment.
private struct MoreView: View {

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink {
                        SettingsView()
                    } label: {
                        Text("Settings")
                    }

                    NavigationLink {
                        PairingView()
                    } label: {
                        Text("Pairing")
                    }

                    NavigationLink {
                        EnrollmentView()
                    } label: {
                        Text("Enrollment")
                    }
                }
            }
            .navigationTitle("More")
        }
    }
}
