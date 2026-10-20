import SwiftUI

// StatusBannerView
// Persistent status banner. Always visible, never a modal.
// Color is functional only: green means glasses live and ok,
// amber means phone mode or a warning, red means reconnecting
// or an error.

struct StatusBannerView: View {

    @EnvironmentObject private var sessionManager: SessionManager

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(Color.red)
                .frame(width: 10, height: 10)
                .opacity(sessionManager.conversationState == .recording ? 1.0 : 0.25)

            Text(displayText)
                .font(.subheadline)
                .fontWeight(.semibold)
                .lineLimit(2)

            Spacer()

            Text(modeText)
                .font(.caption)
                .fontWeight(.bold)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(backgroundColor)
        .foregroundColor(.white)
    }

    private var displayText: String {
        let bannerText = sessionManager.banner.text
        if !bannerText.isEmpty {
            return bannerText
        }
        switch sessionManager.mode {
        case .glasses:
            return "GLASSES LIVE - POV capture"
        case .phone:
            return "PHONE MODE - Phone mic capture"
        case .reconnecting:
            return "RECONNECTING - Trying glasses again"
        }
    }

    private var modeText: String {
        switch sessionManager.mode {
        case .glasses:
            return "GLASSES"
        case .phone:
            return "PHONE"
        case .reconnecting:
            return "RECONNECTING"
        }
    }

    private var backgroundColor: Color {
        if sessionManager.banner.kind == .error {
            return .red
        }
        if sessionManager.mode == .reconnecting {
            return .red
        }
        if sessionManager.banner.kind == .warn {
            return .orange
        }
        switch sessionManager.mode {
        case .glasses:
            return .green
        case .phone:
            return .orange
        case .reconnecting:
            return .red
        }
    }
}
