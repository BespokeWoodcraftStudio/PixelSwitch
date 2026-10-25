import Foundation

/// The pure rules behind removing an account and moving Claude Code off it
/// (spec D20) and behind the "already live" shortcut of a switch. `AppState`
/// applies them; the unit harness runs them without the app.
enum AccountRemoval {

    /// What removing an account changes, decided from the accounts that remain.
    struct Plan: Equatable {
        /// No account is active afterwards (the list is empty).
        let clearActive: Bool
        /// Remember that the user removed their last account, so a refresh does
        /// not recreate it from the login Claude Code still holds.
        let markRemovedLast: Bool
        /// The accounts to try switching to, in order, when the removed account
        /// was the active one. Empty when nothing needs switching.
        let fallbackIds: [UUID]
    }

    /// Removing the last account always clears the active account and marks the
    /// removal, whether or not that account was marked active (D20; a failed
    /// fallback can leave no account marked active, so the last one removed
    /// later is not active). Otherwise only removing the ACTIVE account starts a
    /// fallback switch.
    static func plan(removedWasActive: Bool, remaining: [Account]) -> Plan {
        if remaining.isEmpty { return Plan(clearActive: true, markRemovedLast: true, fallbackIds: []) }
        if removedWasActive {
            return Plan(clearActive: false, markRemovedLast: false, fallbackIds: AccountOrder.fallbackOrder(from: remaining).map(\.id))
        }
        return Plan(clearActive: false, markRemovedLast: false, fallbackIds: [])
    }

    /// Whether a refresh may create the first account from the login Claude Code
    /// holds: only while the list is empty and the user has not removed their
    /// last account on purpose.
    static func mayCreateFromLiveLogin(accountsAreEmpty: Bool, removedLastAccount: Bool) -> Bool {
        accountsAreEmpty && !removedLastAccount
    }
}

/// The fallback loop of a removal: try the candidates in order and stop as soon
/// as the situation it was started for no longer holds. Pure over closures, so
/// the harness can drive it with fakes.
enum RemovalFallback {

    /// What one try of one candidate came to.
    enum Attempt: Equatable {
        /// The candidate is now live and active.
        case switched
        /// Another switch or a sign-in is running; nothing was tried.
        case busy(message: String)
        /// The switch was tried (or refused before writing) and did not happen.
        /// `message` is the sentence the user would be shown, when there is one.
        case failed(message: String?)
        /// The candidate is no longer on the list.
        case gone
    }

    /// How the loop ended.
    enum Result: Equatable {
        case switched(UUID)
        /// Something else chose an account (or emptied the list) while the loop
        /// ran, so it stopped and changed nothing.
        case stoodDown
        /// A switch or sign-in kept running for the whole wait. Nothing was tried;
        /// the active account is left as it is.
        case busyGaveUp(message: String)
        /// Every candidate was tried or gone and none took over.
        case noneWorked(lastFailure: String?)
    }

    /// How many one-second waits a busy switch or sign-in is given in all. A
    /// sign-in can take minutes in the browser, and nothing is lost by waiting.
    static let defaultMaxBusyWaits = 150

    /// Runs the loop.
    /// - `stillNeeded`: checked before every candidate and after every wait. False
    ///   as soon as anything else made an account active or emptied the list.
    /// - `attempt`: tries to switch to one candidate.
    /// - `pause`: waits about a second.
    /// - `note`: the log, one line per decision, with the reason for every failure.
    @MainActor
    static func run(
        candidates: [UUID],
        maxBusyWaits: Int = defaultMaxBusyWaits,
        stillNeeded: @MainActor () -> Bool,
        attempt: @MainActor (UUID) async -> Attempt,
        pause: @MainActor () async -> Void,
        note: @MainActor (String) -> Void
    ) async -> Result {
        var lastFailure: String?
        var busyWaits = 0
        for id in candidates {
            tries: while true {
                guard stillNeeded() else {
                    note("Another account became active (or none are left); stopping the fallback")
                    return .stoodDown
                }
                switch await attempt(id) {
                case .switched:
                    return .switched(id)
                case .gone:
                    note("Candidate \(id) is no longer on the list; skipping it")
                    break tries
                case .failed(let message):
                    if let message { lastFailure = message }
                    note("Switch to \(id) failed (\(message ?? "no reason given")); trying the next account")
                    break tries
                case .busy(let message):
                    busyWaits += 1
                    if busyWaits > maxBusyWaits {
                        note("A switch or sign-in is still running after \(maxBusyWaits) s; giving up without trying anything or clearing the active account")
                        return .busyGaveUp(message: message)
                    }
                    await pause()
                }
            }
        }
        guard stillNeeded() else {
            note("Another account became active (or none are left); nothing more to do")
            return .stoodDown
        }
        return .noneWorked(lastFailure: lastFailure)
    }
}

/// The "already live" shortcut of `AppState.performSwitch`.
enum SwitchShortcut {
    /// Whether the target's login is already the live one: the live login is
    /// proven to be the target's, or it is unproven and the target is the
    /// account believed active. With no active account and no proven owner
    /// nothing is "already live" here; `LiveLoginDisposal` decides first whether
    /// the switch may go ahead at all.
    static func alreadyLive(liveOwnerId: UUID?, believedActiveId: UUID?, targetId: UUID) -> Bool {
        if let liveOwnerId { return liveOwnerId == targetId }
        return believedActiveId == targetId
    }
}

/// What a switch does when NO account is active and the live login's owner is not
/// proven (spec D20, revision 4). A switch is allowed in this state, or the app
/// would be stuck after a removal whose fallbacks all failed. But it overwrites
/// the live login without saving it anywhere (nothing is saved under an
/// unproven owner), so it may only do that to a login that is provably
/// disposable. Pure: `AppState` gathers the facts and acts on the answer.
enum LiveLoginDisposal {

    /// What the API said about the live access token.
    enum APIAnswer: Equatable {
        /// It could not say (expired token, parked or refused token, network).
        case couldNotSay
        /// It answered with an identity that names no remaining account.
        case namesNoRemainingAccount
        /// It answered with an identity that names a remaining account, or
        /// more than one, without settling on exactly one (so no owner is proven).
        case namesRemainingAccount
    }

    /// Why going ahead is safe.
    enum Basis: Equatable {
        /// The live credential is absent or holds a blanked login: nothing to lose.
        case nothingToLose
        /// The API proved the login belongs to someone who is not on the list.
        case apiNamesNoRemainingAccount
        /// The login is the same grant as a login of an account that was removed.
        case sameGrantAsRemovedAccount
    }

    enum Decision: Equatable {
        /// Go ahead; the login being replaced is disposable (nothing is saved for it).
        case proceed(Basis)
        /// The login is the same grant as exactly this remaining account's saved
        /// login: treat that account as the believed active one, so the "already
        /// live" shortcut, adopting it and 1.9's rules apply.
        case treatAsActive(UUID)
        /// Cannot tell whose login it is: refuse with `refusalSentence`.
        case refuse
    }

    /// The English source of the sentence shown when the switch is refused (the
    /// key in all five string tables).
    static let refusalSentence = "PixelSwitch cannot tell whose login Claude Code holds right now. Use Claude Code once, then try again."

    /// - `liveLoginUsable`: the live credential is a usable login (D12). Absent
    ///   or blanked means there is nothing to lose.
    /// - `api`: what the API said about the live access token.
    /// - `remainingSameGrant`: ids of the remaining accounts whose SAVED login is
    ///   the same grant as the live login; nil when the backup store cannot be
    ///   read, in which case lineage proves nothing.
    /// - `sameGrantAsRemovedLogin`: the live login is the same grant as a saved
    ///   or live login recorded when an account was removed.
    ///
    /// Order, most certain first. The API's answer wins when it names no remaining
    /// account; when it names one without settling on exactly one, nothing is
    /// discarded. Then a login that is a remaining account's is never discarded as
    /// the removed account's: exactly one such account becomes the believed active
    /// one, several are ambiguous and refuse. Only a login that is the removed
    /// account's and no remaining account's may go.
    static func decide(liveLoginUsable: Bool, api: APIAnswer, remainingSameGrant: [UUID]?, sameGrantAsRemovedLogin: Bool) -> Decision {
        guard liveLoginUsable else { return .proceed(.nothingToLose) }
        if api == .namesNoRemainingAccount { return .proceed(.apiNamesNoRemainingAccount) }
        // The API names a remaining account but not exactly one (its id and email
        // disagree, or two accounts share one): the data is contradictory, so
        // nothing is discarded on it.
        if api == .namesRemainingAccount { return .refuse }
        if let remaining = remainingSameGrant {
            if remaining.count == 1 { return .treatAsActive(remaining[0]) }
            if remaining.isEmpty, sameGrantAsRemovedLogin { return .proceed(.sameGrantAsRemovedAccount) }
        }
        return .refuse
    }
}

/// The refresh tokens of removed accounts, remembered as fingerprints so a later
/// switch can tell that the live login is a removed account's and disposable
/// (spec D20 revision 4). Only a fingerprint is kept, never a token, and only for
/// logins provably the removed account's (`fingerprintsToRecord`). Cleared when an
/// account is added, signed in, adopted as the active one, or switched to.
enum RemovedLogins {
    /// The most fingerprints kept (a handful of removals, two or three logins each).
    static let limit = 16

    /// Fingerprints of the refresh tokens in these credentials. A credential that
    /// is absent or has no refresh token adds nothing.
    static func fingerprints(ofCredentials credentials: [String?]) -> [String] {
        credentials.compactMap { credential in
            credential.flatMap { CredentialOwnership.login(fromCredential: $0) }?.refreshToken.map(TokenFingerprint.of)
        }
    }

    /// A live login the API proved belongs to one account, kept by that token's
    /// refresh-token fingerprint (never the token). Kept in memory from the last
    /// proven owner lookup, so a removal can tell a live login that really was the
    /// removed account's from one that only was BELIEVED to be.
    struct ProvenLiveLogin: Equatable {
        let accountId: UUID
        let refreshFingerprint: String
    }

    /// The proof for `credential` being `owner`'s; nil if it has no refresh token.
    static func proof(owner: UUID, credential: String?) -> ProvenLiveLogin? {
        guard let refresh = credential.flatMap({ CredentialOwnership.login(fromCredential: $0) })?.refreshToken else { return nil }
        return ProvenLiveLogin(accountId: owner, refreshFingerprint: TokenFingerprint.of(refresh))
    }

    /// What to remember when `removedAccount` is removed (spec D20 revision 4):
    /// only logins that are provably its own. Its saved login's refresh token
    /// always. The live login's only when it is the same grant as that saved
    /// login, or the API proved it is the removed account's (`proof`). Belief that
    /// the account was active is not proof: until the next lookup the live login
    /// can be another account's (one signed in by hand), and recording it as the
    /// removed account's would let a later switch discard it. An unproven live
    /// login is left out, so the switch refuses it; Claude Code renewing it lets
    /// the API name its owner and the switch go ahead.
    static func fingerprintsToRecord(removedAccount: UUID, savedCredential: String?, liveCredential: String?, proof: ProvenLiveLogin?) -> [String] {
        var recorded = fingerprints(ofCredentials: [savedCredential])
        guard let live = liveCredential.flatMap({ CredentialOwnership.login(fromCredential: $0) }), let refresh = live.refreshToken else { return recorded }
        let sameGrantAsSaved = savedCredential.flatMap { CredentialOwnership.login(fromCredential: $0) }
            .map { CredentialOwnership.sameGrant($0, live) } ?? false
        let fingerprint = TokenFingerprint.of(refresh)
        let provenRemovedAccounts = proof.map { $0.accountId == removedAccount && $0.refreshFingerprint == fingerprint } ?? false
        if sameGrantAsSaved || provenRemovedAccounts, !recorded.contains(fingerprint) { recorded.append(fingerprint) }
        return recorded
    }

    /// `existing` plus `new`, without repeats, oldest dropped past `limit`.
    static func remembering(_ new: [String], existing: [String]) -> [String] {
        var all = existing
        for fingerprint in new where !all.contains(fingerprint) { all.append(fingerprint) }
        return Array(all.suffix(limit))
    }

    /// Whether this live login is the same grant as a removed account's login:
    /// its refresh token is one that was recorded. (An access token is never
    /// recorded, so a renewed login that rotated its refresh token no longer
    /// matches; the API's answer covers that case.)
    static func isRemovedLogin(_ login: CredentialOwnership.Login, fingerprints: [String]) -> Bool {
        guard let refresh = login.refreshToken else { return false }
        return fingerprints.contains(TokenFingerprint.of(refresh))
    }
}
