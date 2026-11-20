import SwiftUI

/// Settings → Accounts: the same two ways to add an account as the popover's footer.
/// "Sign In New Account" opens the sign-in window; "Add Current Account"
/// saves whoever Claude Code is signed in as, after the same confirmation.
///
/// While a sign-in is running the two buttons give way to its state and a way back
/// to its window ("Show sign-in window"), as the popover's footer does, so a sign-in
/// that went behind another window is never lost.
struct SettingsSignInButtons: View {
    @EnvironmentObject private var appState: AppState
    @State private var confirmingAddCurrent = false

    var body: some View {
        Group {
            if appState.isLoggingIn {
                HStack(spacing: 10) {
                    ProgressView()
                        .controlSize(.small)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Signing in…")
                            .font(.callout.weight(.medium))
                        Text("Finish in the sign-in window.")
                            .font(.caption)
                            .foregroundStyle(.textSecondary)
                    }
                    Button("Show sign-in window") {
                        NotificationCenter.default.post(name: .pixelswitchShowSignIn, object: nil)
                    }
                }
            } else {
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
            }
        }
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
