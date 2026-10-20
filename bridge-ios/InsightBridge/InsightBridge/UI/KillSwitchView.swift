import SwiftUI

// KillSwitchView
// Always visible GO PHONE-ONLY control. Visible in every mode.
// One tap calls killSwitch with no confirmation dialog.
// This is the demo failover path, so it must never be hidden
// behind a sheet, alert, or tab.

struct KillSwitchView: View {

    @EnvironmentObject private var sessionManager: SessionManager

    private var isPhoneOnlyActive: Bool {
        sessionManager.mode == .phone && sessionManager.isGlassesAttached == false
    }

    var body: some View {
        Button {
            sessionManager.killSwitch()
        } label: {
            Text(isPhoneOnlyActive ? "PHONE-ONLY ACTIVE" : "GO PHONE-ONLY")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
        }
        .background(isPhoneOnlyActive ? Color.gray : Color.red)
        .foregroundColor(.white)
        .disabled(isPhoneOnlyActive)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(.systemBackground))
    }
}
