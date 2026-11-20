import Foundation

/// Whether an account's Switch button in Settings → Accounts is on, and when it is
/// off, which reason the button's tooltip gives.
///
/// Pure, so the harness tests it and the view only turns the answer into words.
/// It reads what the app already publishes and never the Keychain: the Keychain
/// is read when a switch is actually made (`AppState.performSwitch` refuses an
/// unusable saved login and says why), not on every redraw of a list.
enum SwitchAvailability: Equatable {
    /// Switch works.
    case available
    /// This is the account Claude Code is using: there is nothing to switch to.
    case alreadyActive
    /// Its sign-in has ended (the card says so), so a switch to it would fail until
    /// it is signed in again. Re-sign is the fix.
    case needsSignIn
    /// Another switch or a sign-in is running; a second one would only be refused.
    case busy

    /// The first reason that applies wins, in the order a person would want to hear
    /// it: you are already on it, then it needs signing in, then wait.
    static func of(isActive: Bool, signInEnded: Bool, isSwitching: Bool, isLoggingIn: Bool) -> SwitchAvailability {
        if isActive { return .alreadyActive }
        if signInEnded { return .needsSignIn }
        if isSwitching || isLoggingIn { return .busy }
        return .available
    }

    var isAvailable: Bool { self == .available }

    /// A switch or a sign-in is running. The control API refuses its own switch, sign-in,
    /// add and remove requests in this state (`AppController.refuseWhileBusy`), so every
    /// button that does one of those waits too.
    static func isBusy(isSwitching: Bool, isLoggingIn: Bool) -> Bool {
        isSwitching || isLoggingIn
    }

    /// Whether Re-sign, Add current account, Sign in a new account and Remove account work:
    /// the control API's own rule, so a button never does what the API refuses. (A second
    /// sign-in would only be refused with "A sign-in is already in progress.") Being the
    /// active or an expired account never turns Re-sign off: those are the accounts it is for.
    static func canAddOrRemove(isSwitching: Bool, isLoggingIn: Bool) -> Bool {
        !isBusy(isSwitching: isSwitching, isLoggingIn: isLoggingIn)
    }

    /// The card says the sign-in ended: its login expired, which signing in again fixes.
    /// "No active subscription" is not this: signing in again does not help.
    static func signInEnded(isExpired: Bool, isNoSubscription: Bool) -> Bool {
        isExpired && !isNoSubscription
    }
}
