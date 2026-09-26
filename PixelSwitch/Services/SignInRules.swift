import Foundation

/// The code the "another device" page shows. Claude Code reads it from stdin as
/// one `code#state` line and splits it on `#`.
enum SignInCode {
    /// The line to hand to Claude Code, or nil unless `pasted` is one whole
    /// `code#state`: exactly one `#`, text on both sides, and no whitespace or
    /// control character inside (a newline would send two lines).
    static func normalized(_ pasted: String) -> String? {
        let trimmed = pasted.trimmingCharacters(in: .whitespacesAndNewlines)
        let forbidden = CharacterSet.whitespacesAndNewlines.union(.controlCharacters)
        guard !trimmed.isEmpty, !trimmed.unicodeScalars.contains(where: forbidden.contains) else { return nil }
        let halves = trimmed.split(separator: "#", omittingEmptySubsequences: false)
        guard halves.count == 2, !halves[0].isEmpty, !halves[1].isEmpty else { return nil }
        return trimmed
    }
}

/// Whether a sign-in may start now. One credential mutation at a time: a
/// sign-in entering while a switch is suspended mid-swap would back up the
/// WRONG live credential under the old active account's id.
enum SignInGate {
    enum Decision: Equatable, Sendable { case allowed, busy, claudeUnavailable }

    static func decide(claudeAvailable: Bool, isSwitching: Bool, isLoggingIn: Bool, current: SignInState?) -> Decision {
        guard claudeAvailable else { return .claudeUnavailable }
        if isSwitching || isLoggingIn { return .busy }
        if let current, !current.isFinished { return .busy }
        return .allowed
    }
}

/// What `claude auth status` says after Claude Code finished a sign-in,
/// classified the way `AppState` has always acted on it. Emails compare
/// exactly, as they always have.
enum SignInResult {
    enum NewAccount: Equatable, Sendable {
        case notLoggedIn
        /// A credential source outranking the stored claude.ai login hides the identity.
        case noIdentity
        /// The browser signed in to an account PixelSwitch already has.
        case existing(accountId: UUID)
        case new(email: String)
    }

    enum Reauthentication: Equatable, Sendable {
        case notLoggedIn
        case noIdentity
        case matches
        /// The browser signed in to a different account than the one being re-authenticated.
        case wrongAccount(signedInAs: String)
    }

    static func newAccount(status: AuthStatus, accounts: [Account]) -> NewAccount {
        guard status.loggedIn else { return .notLoggedIn }
        guard let email = status.email else { return .noIdentity }
        if let existing = accounts.first(where: { $0.email == email }) { return .existing(accountId: existing.id) }
        return .new(email: email)
    }

    static func reauthentication(status: AuthStatus, expectedEmail: String) -> Reauthentication {
        guard status.loggedIn else { return .notLoggedIn }
        guard let email = status.email else { return .noIdentity }
        return email == expectedEmail ? .matches : .wrongAccount(signedInAs: email)
    }
}
