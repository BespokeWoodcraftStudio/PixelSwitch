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
                // Off while a switch runs (a sign-in running swaps these for its state above): the
                // control API refuses both then. The tooltip is on the wrapper, not the disabled buttons.
                let switching = appState.switchingTo != nil
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
                .disabled(switching)
                .contentShape(Rectangle())
                .help(switching ? String(localized: "Wait for the current switch or sign-in to finish.", bundle: L10n.bundle) : "")
            }
        }
        .confirmationDialog(
            "This will capture the currently logged-in Claude Code account.",
            isPresented: $confirmingAddCurrent,
            titleVisibility: .visible
        ) {
            Button("Add Account") {
                // A switch may have started while the question was up.
                guard SwitchAvailability.canAddOrRemove(isSwitching: appState.switchingTo != nil, isLoggingIn: appState.isLoggingIn) else {
                    appState.errorMessage = String(localized: "A switch or sign-in is in progress. Try again in a moment.", bundle: L10n.bundle)
                    return
                }
                Task { await appState.addAccount() }
            }
            Button("Cancel", role: .cancel) {}
        }
    }
}
