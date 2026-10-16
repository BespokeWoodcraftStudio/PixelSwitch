import CryptoKit
import Foundation

/// A short, non-reversible name for an access token, so the app can remember
/// "this exact token was refused" without keeping the token in a second place
/// or writing it to a log. SHA-256, first 16 hex characters.
enum TokenFingerprint {
    static func of(_ accessToken: String) -> String {
        SHA256.hash(data: Data(accessToken.utf8)).prefix(8).map { String(format: "%02x", $0) }.joined()
    }

    /// The part of a fingerprint that may appear in a log line.
    static func forLog(_ fingerprint: String) -> String { String(fingerprint.prefix(8)) }
}

/// What the app may do with an account's access token right now.
enum UsageDecision: Equatable {
    /// Send the usage request with this token.
    case request
    /// A saved login whose access token is expired or was refused, and whose
    /// refresh grant is not known to be dead: renew it first, then ask.
    case refreshFirst
    /// The LIVE login is expired or was refused. Send nothing: Claude Code
    /// renews it the next time a session runs, and the moment it does the
    /// token (and so its fingerprint) changes and the account is read again.
    case waitForClaudeCode
    /// The server refused this account's refresh grant (or the saved login is
    /// lost): nothing is sent until the user signs in again.
    case needsReauth
    /// The server answered 429 for this very token; leave it alone until then.
    case parked(until: Date)
    /// A 401 or 429 was just seen for this very token; leave it alone for a while.
    case coolingDown(until: Date)
}

/// Decides, before any request carrying an OAuth access token is made, whether
/// the token may be sent.
///
/// Why it exists (2026-10-16, `docs/investigations/2026-10-16-usage-429.md`):
/// Anthropic answers roughly the fourth refused (401) call on one access token
/// within about an hour with 429 and `Retry-After: 3600`, on `/api/oauth/usage`
/// and `/api/oauth/profile` alike. PixelSwitch kept sending tokens it could
/// know were dead, so it earned those 429s itself. Healthy tokens were never
/// refused. The rules:
///
/// - a. A token that got a 401 is never sent again until the account's stored
///   or live token changes.
/// - b. A token whose `expiresAt` is known and is past, or within 60 seconds,
///   is never sent. A missing `expiresAt` is allowed (rule a stops it after one 401).
/// - c. A 429 park holds only while the account still has the token it was earned on.
/// - d. After a 401 or 429 on a token, nothing else is sent with it for 20 minutes,
///   however many accounts are being polled.
/// - e. An account the server refused a refresh grant for sends nothing until its token changes.
/// - f. The live login is never dropped: it waits for Claude Code, and is read the
///   moment its token changes.
///
/// Pure logic: no network, no Keychain, no clock of its own (`now` is passed
/// in). A class with a lock because `ClaudeService` consults it from whichever
/// thread makes the request.
final class UsageRequestGuard: @unchecked Sendable {
    /// Rule d.
    static let cooldown: TimeInterval = 20 * 60
    /// Rule b.
    static let expirySlack: TimeInterval = 60
    /// The longest a 429 parks an account, and the shortest.
    static let longestPark: TimeInterval = 3600
    static let shortestPark: TimeInterval = 60
    /// The park when the server sent no Retry-After.
    static let defaultPark: TimeInterval = 120

    private struct Stamp {
        let until: Date
        let fingerprint: String
    }

    private struct State {
        /// The access token that got a 401 from /usage or /profile.
        var rejectedFingerprint: String?
        var lastAuthFailure: Date?
        var park: Stamp?
        var cooldown: Stamp?
        var needsReauth = false
        /// The token the account held when it was flagged. Nil for a flag
        /// restored after a relaunch, which adopts the first token it sees.
        var needsReauthFingerprint: String?
    }

    private let lock = NSLock()
    private var states: [UUID: State] = [:]
    /// Tokens refused where no account was known (a 401 on the live login
    /// before its owner was proven). Oldest forgotten first.
    private var refusedWithoutAccount: [String] = []
    private static let refusedWithoutAccountLimit = 64

    init(needsReauth: Set<UUID> = []) {
        restoreNeedsReauth(needsReauth)
    }

    // MARK: - Asking

    /// May this account's token be sent now? Also forgets anything that was
    /// earned on a different token (rules a, c, d and e all end with a new token).
    func decision(for account: UUID, isActive: Bool, login: CredentialOwnership.Login, now: Date = Date()) -> UsageDecision {
        lock.lock(); defer { lock.unlock() }
        let fingerprint = TokenFingerprint.of(login.accessToken)
        var state = states[account] ?? State()
        // The live login is Claude Code's own, never the saved login a flag
        // was raised about. A flag that holds no token (its backup was removed,
        // or it was restored after a relaunch) must not adopt the live token
        // as the dead one: it goes, and the live login is judged on its own.
        if isActive, state.needsReauth, state.needsReauthFingerprint == nil { state.needsReauth = false }
        Self.dropWhatIsNotAboutThisToken(&state, fingerprint: fingerprint)
        states[account] = state

        if state.needsReauth { return .needsReauth }
        let isDead = Self.isExpired(login.expiresAt, now: now)
            || state.rejectedFingerprint == fingerprint
            || refusedWithoutAccount.contains(fingerprint)
        // The live login that is expired or was refused waits for Claude Code,
        // whatever else is known about it (the honest message is "waiting").
        if isActive && isDead { return .waitForClaudeCode }
        if let park = state.park, park.until > now { return .parked(until: park.until) }
        if let cooldown = state.cooldown, cooldown.until > now { return .coolingDown(until: cooldown.until) }
        // A saved login that is expired or was refused is renewed first (a POST
        // to the token endpoint); the renewed token carries no park.
        if isDead { return .refreshFirst }
        return .request
    }

    /// May a token be sent to /api/oauth/profile (or /usage) by a caller that
    /// has no account to hand? True unless the token is expired, was refused,
    /// or is inside a park or cool-down earned on it by any account.
    func mayUse(accessToken: String, expiresAt: Double? = nil, now: Date = Date()) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if Self.isExpired(expiresAt, now: now) { return false }
        let fingerprint = TokenFingerprint.of(accessToken)
        if refusedWithoutAccount.contains(fingerprint) { return false }
        for state in states.values {
            if state.rejectedFingerprint == fingerprint { return false }
            if let park = state.park, park.fingerprint == fingerprint, park.until > now { return false }
            if let cooldown = state.cooldown, cooldown.fingerprint == fingerprint, cooldown.until > now { return false }
        }
        return true
    }

    /// Whether this exact token already got a 401 for this account. Lets a
    /// caller that just saw a lookup fail tell "refused" from a blip, without
    /// asking `decision` (which a cool-down answers first).
    func wasRejected(account: UUID, accessToken: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return states[account]?.rejectedFingerprint == TokenFingerprint.of(accessToken)
    }

    /// Rule b: an `expiresAt` (milliseconds since 1970) that is known and past,
    /// or within a minute. Missing means "not known to be expired".
    static func isExpired(_ expiresAt: Double?, now: Date) -> Bool {
        guard let expiresAt else { return false }
        return expiresAt / 1000 < now.timeIntervalSince1970 + expirySlack
    }

    // MARK: - Recording what the server said

    /// The server answered 401 for this exact token, from /usage or /profile.
    func recordRejected(account: UUID?, accessToken: String, now: Date = Date()) {
        lock.lock(); defer { lock.unlock() }
        let fingerprint = TokenFingerprint.of(accessToken)
        guard let account else {
            if !refusedWithoutAccount.contains(fingerprint) {
                refusedWithoutAccount.append(fingerprint)
                if refusedWithoutAccount.count > Self.refusedWithoutAccountLimit { refusedWithoutAccount.removeFirst() }
            }
            return
        }
        var state = states[account] ?? State()
        Self.dropWhatIsNotAboutThisToken(&state, fingerprint: fingerprint)
        state.rejectedFingerprint = fingerprint
        state.lastAuthFailure = now
        state.cooldown = Stamp(until: now.addingTimeInterval(Self.cooldown), fingerprint: fingerprint)
        states[account] = state
    }

    /// The server answered 429 for this exact token. Returns the park's end.
    @discardableResult
    func recordRateLimited(account: UUID, accessToken: String, retryAfter: TimeInterval?, now: Date = Date()) -> Date {
        lock.lock(); defer { lock.unlock() }
        let fingerprint = TokenFingerprint.of(accessToken)
        var state = states[account] ?? State()
        Self.dropWhatIsNotAboutThisToken(&state, fingerprint: fingerprint)
        let parkFor = min(max(retryAfter ?? Self.defaultPark, Self.shortestPark), Self.longestPark)
        let until = now.addingTimeInterval(parkFor)
        state.park = Stamp(until: until, fingerprint: fingerprint)
        state.cooldown = Stamp(until: now.addingTimeInterval(Self.cooldown), fingerprint: fingerprint)
        states[account] = state
        return until
    }

    /// The server refused this account's refresh grant (`invalid_grant`), or
    /// its saved login is lost or was someone else's. `accessToken` is the one
    /// the account holds now, so a different token later clears the flag.
    func markNeedsReauth(account: UUID, accessToken: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        var state = states[account] ?? State()
        state.needsReauth = true
        state.needsReauthFingerprint = accessToken.map(TokenFingerprint.of)
        states[account] = state
    }

    /// The account's stored or live credential changed (a sign-in, a renewal,
    /// a switch). Forgets everything earned on any other token; `accessToken`
    /// is the one it holds now. Returns whether anything was forgotten.
    @discardableResult
    func credentialChanged(account: UUID, accessToken: String?) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard var state = states[account] else { return false }
        let before = (state.rejectedFingerprint, state.park?.fingerprint, state.cooldown?.fingerprint, state.needsReauth)
        if let accessToken {
            // A flag restored without a token has nothing to compare; the
            // caller says the credential changed, so it goes.
            if state.needsReauth, state.needsReauthFingerprint == nil { state.needsReauth = false }
            Self.dropWhatIsNotAboutThisToken(&state, fingerprint: TokenFingerprint.of(accessToken))
        } else {
            state = State()
        }
        states[account] = state
        let after = (state.rejectedFingerprint, state.park?.fingerprint, state.cooldown?.fingerprint, state.needsReauth)
        return before.0 != after.0 || before.1 != after.1 || before.2 != after.2 || before.3 != after.3
    }

    /// Drops everything about a removed account.
    func forget(account: UUID) {
        lock.lock(); defer { lock.unlock() }
        states[account] = nil
    }

    // MARK: - needsReauth, persisted by the caller

    func isNeedsReauth(_ account: UUID) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return states[account]?.needsReauth == true
    }

    /// The accounts to persist (ids only).
    var needsReauthAccounts: Set<UUID> {
        lock.lock(); defer { lock.unlock() }
        return Set(states.filter { $0.value.needsReauth }.keys)
    }

    /// Re-applies a persisted set after a relaunch.
    func restoreNeedsReauth(_ accounts: Set<UUID>) {
        lock.lock(); defer { lock.unlock() }
        for account in accounts {
            var state = states[account] ?? State()
            state.needsReauth = true
            states[account] = state
        }
    }

    /// When the account last got a 401, if it has.
    func lastAuthFailure(of account: UUID) -> Date? {
        lock.lock(); defer { lock.unlock() }
        return states[account]?.lastAuthFailure
    }

    // MARK: - Internals

    /// Rules a, c, d, e: whatever was earned on another token no longer applies.
    private static func dropWhatIsNotAboutThisToken(_ state: inout State, fingerprint: String) {
        if let rejected = state.rejectedFingerprint, rejected != fingerprint {
            state.rejectedFingerprint = nil
            state.lastAuthFailure = nil
        }
        if let park = state.park, park.fingerprint != fingerprint { state.park = nil }
        if let cooldown = state.cooldown, cooldown.fingerprint != fingerprint { state.cooldown = nil }
        if state.needsReauth {
            if let flagged = state.needsReauthFingerprint {
                if flagged != fingerprint {
                    state.needsReauth = false
                    state.needsReauthFingerprint = nil
                }
            } else {
                state.needsReauthFingerprint = fingerprint
            }
        }
    }
}

/// What a card says while the app is NOT asking the server about an account.
enum UsageNotice: Equatable {
    /// Leave the card exactly as it is.
    case keepCurrent
    /// "Usage reading paused until HH:MM. Claude Code itself is not affected."
    case paused(until: Date)
    /// "Waiting for Claude Code to renew this login."
    case waitingForClaudeCode
    /// "Session expired. Re-authenticate (↻) to fix."
    case resign

    /// What the card says now, for `choose`.
    enum Current: Equatable {
        case nothing
        /// A message that says nothing about the login itself (paused, waiting,
        /// a server or network error, a retry hint).
        case transient
        /// The re-sign message, or "its saved login belonged to another account".
        case resign
        case noSubscription
    }

    /// The honest message for a decision that sends nothing. An expired or
    /// re-sign message is never overwritten by a pause (the 2026-10-16 banner
    /// that said "API Rate Limited" over "Session expired").
    static func choose(for decision: UsageDecision, current: Current) -> UsageNotice {
        let keepsWhatItSays = current == .resign || current == .noSubscription
        switch decision {
        case .needsReauth:
            // Say it once; a message already saying so (it may be the more
            // specific "its saved login belonged to another account") stays.
            return current == .resign ? .keepCurrent : .resign
        case .parked(let until):
            return keepsWhatItSays ? .keepCurrent : .paused(until: until)
        case .waitForClaudeCode:
            return keepsWhatItSays ? .keepCurrent : .waitingForClaudeCode
        case .coolingDown, .request, .refreshFirst:
            return .keepCurrent
        }
    }
}

/// How renewing a saved login in place ended, and what that says about the
/// account. Kept here, apart from `AppState`, so the rule "which outcomes mean
/// the account must sign in again" is unit-tested.
enum BackupRefreshOutcome: Equatable {
    /// New credential JSON, persisted to the store.
    case refreshed(String)
    /// The SERVER decided the grant is invalid (`invalid_grant`): the refresh
    /// token is dead (rotated elsewhere or revoked). Only re-authentication
    /// fixes this.
    case grantRejected
    /// PixelSwitch's own guard refused to spend the refresh token because the
    /// saved login is the same grant as the live login or as another account's.
    /// Nothing was sent and the server said nothing, so the account is healthy
    /// as far as anyone knows and is NOT flagged `needsReauth`.
    case sameGrantAsAnotherLogin
    /// The refresh request never got a verdict: no connection, a timeout, a 5xx
    /// or a 429 from the token endpoint. Nothing was spent; the card keeps its
    /// last reading and says Anthropic could not be reached.
    case unreachable
    /// No stored backup exists for this account at all.
    case noBackup
    /// The store cannot be read or written right now (or the network failed).
    /// Nothing was spent; heals by itself on a later cycle.
    case storeUnavailable
    /// The renewed login belongs to the named other account; the backup was
    /// removed. Only re-authentication fixes this.
    case belongsToAnotherAccount(String)
    /// Worst case: the rotation succeeded but the result could not be persisted
    /// even after a retry. The old refresh token is spent and the new
    /// credential is gone; only re-authentication brings the account back.
    /// Deliberately NOT held in memory: a credential that exists only in
    /// volatile state while some paths read the (dead) stored one is the
    /// split-brain that broke the previous attempt at this feature.
    case rotationLost

    /// Whether the account must sign in again, so nothing more is sent for it.
    /// Only the server's refusal, a lost rotation, and a login that was someone
    /// else's. Never PixelSwitch's own same-grant guard.
    var requiresReauth: Bool {
        switch self {
        case .grantRejected, .rotationLost, .belongsToAnotherAccount: return true
        case .refreshed, .sameGrantAsAnotherLogin, .unreachable, .noBackup, .storeUnavailable: return false
        }
    }

    /// PixelSwitch's own refusal to spend a refresh token, decided before any
    /// request: the saved login is the same grant as the live login or as
    /// another account's. Never a server verdict, so never `requiresReauth`.
    static func localRefusal(isLive: Bool, sharesLineage: Bool) -> BackupRefreshOutcome? {
        (isLive || sharesLineage) ? .sameGrantAsAnotherLogin : nil
    }
}
