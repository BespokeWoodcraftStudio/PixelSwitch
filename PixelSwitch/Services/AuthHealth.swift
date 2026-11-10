import Foundation

/// When does the LIVE login "need authentication"? The rules, as pure logic.
///
/// Why it exists (founder, 2026-11-10): "when switching sometimes authentication
/// has expired and the Claude sessions then get stuck ... it should also check
/// throughout the time an account is being used and if it detects that
/// authentication is required again it automatically switches accounts. Again
/// the idea is so agentic coding does not get blocked."
///
/// The case it was written from (PixelSwitch-app.log, 2026-11-10): at 10:40:31Z
/// Claude Code gave up on the active account's login. It blanked the credential
/// (`accessToken` and `refreshToken` empty, `expiresAt` 0, which is 202 characters
/// shorter) and `claude auth status` began to exit 1. PixelSwitch logged "No token
/// for <account>, skipping" every five minutes for three hours and switched
/// nowhere, because a dead LIVE login was never flagged (a saved login that the
/// server refuses is; see `UsageRequestGuard` and `needsReauth`).
///
/// What counts, decided by the CTO and fixed here:
///
/// - A live login needs authentication when Claude Code has blanked it AND
///   `claude auth status` exits non-zero, confirmed on two reads at least 15
///   seconds apart (`LiveLoginWatch`).
/// - It NEVER counts: an expired `expiresAt` alone (an idle Claude Code leaves an
///   expired access token for hours, and renews it by itself the next time a
///   session runs), a usage 401, a 429, a 5xx, no network, a CLI that timed out or
///   would not start, a credential that could not be read, or one failed read.
///   None of those change the credential, and the only thing here that spawns the
///   CLI is the blank shape, so they cannot reach the verdict at all.
///
/// No Keychain, no network, no clock of its own (`now` is passed in).
enum AuthHealth {
    /// How often AppState looks at the live credential. The look is one local
    /// Keychain read (about 50 to 100 ms, no network) and `claude auth status` is
    /// spawned only after the blank shape is seen, so 30 seconds is cheap, and it
    /// makes the second read of a confirmation land one tick after the first:
    /// the founder's sessions are unblocked within about a minute, not within the
    /// 5-minute usage refresh.
    static let watchInterval: TimeInterval = 30

    /// What the live credential looks like, without asking anyone.
    enum Shape: Equatable {
        /// Nothing came back, or what came back is not a JSON object (a locked
        /// Keychain, an item mid-write). Unknown, never signed out.
        case unreadable
        /// A JSON object with no `claudeAiOauth` object: this Mac signs in another
        /// way (an environment token), so there is no login to blank.
        case noOAuthLogin
        /// Claude Code has blanked the login: `accessToken` and `refreshToken` both
        /// empty, or `expiresAt` 0.
        case blank
        /// A login Claude Code has not given up on. `isExpired` is only "its access
        /// token has expired, or will within a minute": normal, and Claude Code
        /// renews it itself.
        case present(isExpired: Bool)
    }

    /// What `claude auth status` said.
    enum CLIProbe: Equatable {
        /// It exited 0 and says it is logged in.
        case loggedIn
        /// It exited non-zero (what a logged-out CLI does), or exited 0 and said it
        /// is not logged in.
        case failed
        /// It timed out, would not start, or printed something that cannot be read.
        /// Says nothing about the login.
        case unavailable
    }

    /// The verdict of one read.
    enum Verdict: Equatable {
        /// The login is fine as far as this read can tell.
        case healthy
        /// The login has an expired access token, which is normal: Claude Code
        /// renews it the next time a session runs. Never "needs authentication".
        case waitingForClaudeCode
        /// Blank login and a failing CLI: signed out, seen once. Two such reads,
        /// 15 seconds apart, make it a fact (`LiveLoginWatch`).
        case signedOutSeen
        /// This read cannot say. It neither confirms nor resets.
        case unknown
    }

    /// The same slack the usage guard gives an access token.
    private static let expirySlack: TimeInterval = 60

    static func shape(ofCredential json: String?, now: Date = Date()) -> Shape {
        guard let json, let data = json.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return .unreadable }
        guard let oauth = object["claudeAiOauth"] as? [String: Any] else { return .noOAuthLogin }
        func blank(_ key: String) -> Bool {
            guard let value = oauth[key] as? String else { return true }
            return value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        let expiresAt = (oauth["expiresAt"] as? NSNumber).flatMap { CFGetTypeID($0) == CFBooleanGetTypeID() ? nil : $0.doubleValue }
        if (blank("accessToken") && blank("refreshToken")) || expiresAt == 0 { return .blank }
        let expired = expiresAt.map { $0 / 1000 < now.timeIntervalSince1970 + expirySlack } ?? false
        return .present(isExpired: expired)
    }

    /// Only the blank shape is worth spawning `claude auth status` for.
    static func needsCLIProbe(_ shape: Shape) -> Bool { shape == .blank }

    /// `cli` is nil when the CLI was not asked.
    static func judge(shape: Shape, cli: CLIProbe?) -> Verdict {
        switch shape {
        case .unreadable, .noOAuthLogin:
            return .unknown
        case .present(let isExpired):
            return isExpired ? .waitingForClaudeCode : .healthy
        case .blank:
            switch cli {
            case .failed: return .signedOutSeen
            // A blank file while the CLI is logged in: it signs in some other way.
            case .loggedIn: return .healthy
            case .unavailable, nil: return .unknown
            }
        }
    }

    static func judge(credential json: String?, cli: CLIProbe?, now: Date = Date()) -> Verdict {
        judge(shape: shape(ofCredential: json, now: now), cli: cli)
    }

    /// What `claude auth status` said when it exited 0.
    static func probe(exitedZeroLoggedIn loggedIn: Bool) -> CLIProbe { loggedIn ? .loggedIn : .failed }

    /// Why `claude auth status` gave no answer to read. `ClaudeService` sorts its
    /// own errors into these (an exhaustive switch, so a new error cannot slip by
    /// unjudged); what each one means for the verdict is decided here, where the
    /// unit tests reach it.
    enum StatusFailure: Equatable {
        /// The CLI ran and exited non-zero: what a logged-out CLI does.
        case exitedNonZero
        /// It did not answer in time.
        case timedOut
        /// It could not be started.
        case couldNotStart
        /// It exited 0 but printed something that cannot be read.
        case unreadableOutput
    }

    /// Only a CLI that ran and exited non-zero counts as "failed". A timeout, a CLI
    /// that will not start and unreadable output say nothing about the login, so a
    /// slow or broken CLI can never make a signed-in account look signed out.
    static func probe(after failure: StatusFailure) -> CLIProbe {
        failure == .exitedNonZero ? .failed : .unavailable
    }
}

/// Turns reads into a confirmed "this account's live login is dead": two
/// `signedOutSeen` reads at least `confirmationGap` apart, with no healthy or
/// waiting read between. One failed read never counts. Value type, no clock of
/// its own; AppState keeps one.
struct LiveLoginWatch {
    /// The second read must be at least this long after the first.
    static let confirmationGap: TimeInterval = 15
    /// Once an account is confirmed dead its login stays blank, so the CLI is not
    /// spawned at every tick (it would fill the log); it is asked again this often,
    /// so a login restored some other way (an environment token) is noticed.
    static let reprobeInterval: TimeInterval = 300

    /// What a read changed. Each is worth one log line; `.none` is worth none.
    enum Change: Equatable {
        case none
        /// The first signed-out read: not a fact yet.
        case firstSighting
        /// The second: the live login of `confirmedDeadAccount` is dead.
        case confirmed
        /// A confirmed-dead login is healthy (or merely expired) again.
        case recovered
    }

    private var accountId: UUID?
    private var firstSeenAt: Date?
    private var lastProbeAt: Date?
    /// The account whose live login is confirmed dead, or nil.
    private(set) var confirmedDeadAccount: UUID?

    /// Whether this tick should spawn `claude auth status` for `shape`.
    func shouldProbeCLI(for shape: AuthHealth.Shape, now: Date) -> Bool {
        guard AuthHealth.needsCLIProbe(shape) else { return false }
        guard confirmedDeadAccount != nil, let last = lastProbeAt else { return true }
        return now.timeIntervalSince(last) >= Self.reprobeInterval || now < last
    }

    mutating func noteProbe(at now: Date) { lastProbeAt = now }

    /// Feeds one read, taken for `account` (the believed active account) at `now`.
    mutating func observe(_ verdict: AuthHealth.Verdict, account: UUID?, at now: Date) -> Change {
        // Another account is being read (a switch, a sign-in): nothing carries over.
        if account != accountId {
            firstSeenAt = nil
            confirmedDeadAccount = nil
            accountId = account
        }
        guard account != nil else { return .none }

        switch verdict {
        case .unknown:
            return .none
        case .healthy, .waitingForClaudeCode:
            let wasDead = confirmedDeadAccount != nil
            firstSeenAt = nil
            confirmedDeadAccount = nil
            return wasDead ? .recovered : .none
        case .signedOutSeen:
            if confirmedDeadAccount != nil { return .none }
            guard let first = firstSeenAt else {
                firstSeenAt = now
                return .firstSighting
            }
            // A clock that went backwards must not stall a confirmation for ever.
            if now < first {
                firstSeenAt = now
                return .firstSighting
            }
            guard now.timeIntervalSince(first) >= Self.confirmationGap else { return .none }
            confirmedDeadAccount = account
            return .confirmed
        }
    }

    /// Forgets everything (a switch, a sign-in, a relaunch).
    mutating func reset() {
        accountId = nil
        firstSeenAt = nil
        lastProbeAt = nil
        confirmedDeadAccount = nil
    }
}

/// What auto-switch does when the ACTIVE account's live login is confirmed dead.
///
/// The account is always flagged (`needsReauth`), logged once per change and
/// shown as needing sign-in. With auto-switch off the user's choice stands and
/// nothing moves. With it on, the account is left at once, Manual only or not
/// (CTO decision, 2026-11-10: Manual only means "never move me for usage
/// reasons", not "keep me when I cannot work at all"), through the unchanged
/// engine's `isUnusable` input, exactly like the 1.6 `noSubscription` rule: it
/// bypasses the 5-minute cooldown, never picks an account that needs sign-in,
/// has no subscription, is parked or is Manual only, and takes the user's
/// strategy for the ordering.
enum DeadLoginPolicy {
    enum StayReason: Equatable {
        case autoSwitchOff
    }

    enum Response: Equatable {
        case leaveNow
        case stayAndShow(StayReason)
    }

    static func response(autoSwitchEnabled: Bool) -> Response {
        autoSwitchEnabled ? .leaveNow : .stayAndShow(.autoSwitchOff)
    }

    /// Whether the engine is told the active account is unusable.
    static func leavesAutomatically(autoSwitchEnabled: Bool) -> Bool {
        response(autoSwitchEnabled: autoSwitchEnabled) == .leaveNow
    }

    /// How many times the 30-second looks may ask auto-switch to leave one
    /// confirmed-dead login: the first try, and four more if nothing moved (the
    /// best candidate's fresh reading can fail). That is about two minutes after
    /// the confirmation; after it the ordinary 5-minute refresh carries on. Each
    /// try takes at most one fresh reading, so this is bounded.
    static let evaluationsPerConfirmation = 5

    /// What one 30-second look does about the change it saw. Pure, so the unit
    /// tests can pin "a confirmation flags the account and asks auto-switch to
    /// leave it" without AppState.
    struct Reaction: Equatable {
        /// Mark the active account as needing sign-in (`flagNeedsReauth`).
        var flagActive = false
        /// The number of auto-switch tries now pending (0 clears them); nil leaves
        /// whatever is pending alone.
        var evaluations: Int?
        /// The login came back: run a refresh, which proves whose it is.
        var refreshAfter = false
    }

    static func reaction(to change: LiveLoginWatch.Change, autoSwitchEnabled: Bool) -> Reaction {
        switch change {
        case .none, .firstSighting:
            return Reaction()
        case .confirmed:
            return Reaction(flagActive: true,
                            evaluations: leavesAutomatically(autoSwitchEnabled: autoSwitchEnabled) ? evaluationsPerConfirmation : 0)
        case .recovered:
            return Reaction(evaluations: 0, refreshAfter: true)
        }
    }

    /// Whether a look may run auto-switch now. While a refresh, another evaluation,
    /// a switch or a sign-in is running, the next look tries again.
    static func mayEvaluateNow(evaluationsLeft: Int, leavesActive: Bool,
                               isRefreshing: Bool, isEvaluating: Bool, isSwitching: Bool, isLoggingIn: Bool) -> Bool {
        evaluationsLeft > 0 && leavesActive && !isRefreshing && !isEvaluating && !isSwitching && !isLoggingIn
    }
}

/// Before EVERY switch to a saved login, renew it first (through the existing
/// renew path, which persists the rotated pair safely before anything is written
/// live), so the server's verdict on its refresh grant arrives BEFORE the switch
/// instead of after Claude Code gets stuck on it. Nothing is polled: this runs
/// only for the one target of a switch that is already happening.
///
/// Why every switch and not only a login about to expire (CTO decision,
/// 2026-11-10, on the review of the first version, which renewed only within 60
/// minutes of expiry): a refresh grant can be revoked or spent while the access
/// token still has hours left, and Claude Code spends the refresh token for its
/// own reasons (a scope expansion, for one) whether or not the access token has
/// expired. The one request costs nothing against the usage and profile limiter.
enum TargetRenewalPolicy {
    /// A saved login this app renewed within this long ago is not renewed again for
    /// a switch: the server answered for that very refresh token moments ago, and
    /// the usual double (a verification read that renewed it, then the switch) is
    /// pointless. Five minutes, the same as the auto-switch cooldown and the
    /// default refresh cycle. The record is tied to the access token that
    /// renewal produced, so any change to the saved login (a rotation by Claude
    /// Code after it was live, a sign-in) ends the exemption at once.
    static let recentRenewalWindow: TimeInterval = 300

    /// The last renewal this app did for one account: when, and which access
    /// token it produced (a fingerprint, never the token).
    struct Renewal: Equatable {
        let accessTokenFingerprint: String
        let at: Date
    }

    /// Whether the switch must renew the target's saved login first. Yes, unless
    /// that very login was renewed by this app within `recentRenewalWindow`.
    /// `savedAccessTokenFingerprint` is the fingerprint of the access token the
    /// saved login holds now.
    static func mustRenewBeforeSwitch(savedAccessTokenFingerprint: String?, lastRenewal: Renewal?, now: Date) -> Bool {
        guard let lastRenewal, let saved = savedAccessTokenFingerprint, lastRenewal.accessTokenFingerprint == saved else { return true }
        let age = now.timeIntervalSince(lastRenewal.at)
        // A clock that went backwards proves nothing about how long ago it was.
        return age < 0 || age >= recentRenewalWindow
    }

    /// What the switch does after the renewal ended as `outcome`.
    enum Step: Equatable {
        /// Carry on with the freshly renewed login.
        case useRenewedLogin
        /// Nothing was learned (a blip, or nothing could be tried): carry on as the
        /// switch always did; Claude Code renews it on first use.
        case switchWithoutRenewal
        /// The server refused the grant: the account must sign in again. It is
        /// flagged `needsReauth` by the renewal itself, and auto-switch tries the
        /// next target.
        case refuseNeedsSignIn
        /// The login is expired or was refused and cannot be renewed from here.
        case refuseNotRenewable
        case refuseBelongsToAnother(String)

        var refusesSwitch: Bool {
            switch self {
            case .useRenewedLogin, .switchWithoutRenewal: return false
            case .refuseNeedsSignIn, .refuseNotRenewable, .refuseBelongsToAnother: return true
            }
        }
    }

    /// `loginIsDead` is the switch's long-standing case: the saved access token is
    /// expired or was refused, so a failed renewal leaves nothing to switch with.
    /// False is the other case: the token is still good, so only the server's own
    /// verdict (or a login that is someone else's) stops the switch, and no answer
    /// (offline, 5xx, 429, timeout) lets it go ahead as it always did.
    static func step(after outcome: BackupRefreshOutcome, loginIsDead: Bool) -> Step {
        switch outcome {
        case .refreshed:
            return .useRenewedLogin
        case .storeUnavailable, .unreachable:
            return .switchWithoutRenewal
        case .belongsToAnotherAccount(let owner):
            return .refuseBelongsToAnother(owner)
        case .grantRejected, .rotationLost:
            return loginIsDead ? .refuseNotRenewable : .refuseNeedsSignIn
        case .noBackup:
            // The backup was found a moment ago and is gone now: something else is
            // writing the store. Nothing is switched from a copy that may be stale.
            return .refuseNotRenewable
        case .sameGrantAsAnotherLogin:
            return loginIsDead ? .refuseNotRenewable : .switchWithoutRenewal
        }
    }

    /// The saved login the switch carries on with after a renewal that SUCCEEDED:
    /// always the renewed credential. Its refresh token was rotated, so the pair
    /// read before the renewal is spent; writing it as the live login would let
    /// the account's backup be overwritten with a dead refresh token. Only the
    /// identity details may come from a re-read of the store (they are not
    /// renewed); when that re-read fails, the details read before the renewal stand.
    static func savedLoginAfterRenewal<Details>(renewed: String, detailsBefore: Details,
                                                reread: (token: String, details: Details)?) -> (token: String, details: Details) {
        (renewed, reread?.details ?? detailsBefore)
    }

    /// What auto-switch does once `performSwitch` has come back for a target.
    enum AfterAttempt: Equatable {
        /// The target is live.
        case completed
        /// The switch stayed on the old account because renewing the target showed
        /// the server refused its login (it is flagged and no longer switchable):
        /// choose the next candidate.
        case targetNeedsSignIn
        /// Anything else: log it and stop for this evaluation.
        case didNotComplete
    }

    static func afterSwitchAttempt(activeNow: UUID?, previousActive: UUID, target: UUID, targetStillSwitchable: Bool) -> AfterAttempt {
        if activeNow == target { return .completed }
        if activeNow == previousActive, !targetStillSwitchable { return .targetNeedsSignIn }
        return .didNotComplete
    }
}
