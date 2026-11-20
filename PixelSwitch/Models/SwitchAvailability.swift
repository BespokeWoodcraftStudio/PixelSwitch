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

    /// Whether Re-sign works: off while a switch or a sign-in is running, as on the
    /// Usage card, because a second sign-in would only be refused ("A sign-in is
    /// already in progress."). Never off for being the active or an expired account:
    /// those are the very accounts it is for.
    static func canResign(isSwitching: Bool, isLoggingIn: Bool) -> Bool {
        !isSwitching && !isLoggingIn
    }
}
