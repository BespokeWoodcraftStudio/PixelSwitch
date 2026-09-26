import SwiftUI

/// Settings → Accounts: the same two ways to add an account as the popover.
/// "Sign In New Account" opens the sign-in window; "Add Current Account"
/// saves whoever Claude Code is signed in as, after the same confirmation.
struct SettingsSignInButtons: View {
    @EnvironmentObject private var appState: AppState
    @State private var confirmingAddCurrent = false

    var body: some View {
        HStack(spacing: 12) {
            Button("Add Current Account") {
                confirmingAddCurrent = true
            }
            Button("Sign In New Account") {
                appState.loginNewAccount()
            }
            .buttonStyle(.borderedProminent)
            .tint(.brand)
        }
        .disabled(appState.isLoggingIn)
        .confirmationDialog(
            "This will capture the currently logged-in Claude Code account.",
            isPresented: $confirmingAddCurrent,
            titleVisibility: .visible
        ) {
            Button("Add Account") {
                Task { await appState.addAccount() }
            }
            Button("Cancel", role: .cancel) {}
        }
    }
}
