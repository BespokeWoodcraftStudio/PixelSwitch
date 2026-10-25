// The usage-request guard: which access tokens may be sent to Anthropic, and
// what a card says when one is not. Compiled into the unit harness by
// Tests/run-unit-tests.sh and run from main.swift. Uses the global
// `check(_:_:_:)` defined there.
//
// Background (docs/investigations/2026-10-16-usage-429.md): Anthropic answers
// roughly the fourth refused (401) call on one access token within about an
// hour with 429 and Retry-After 3600. PixelSwitch kept sending tokens it could
// know were dead, so it earned those 429s itself.
import Foundation

@MainActor func runUsageGuardTests() {
    typealias Login = CredentialOwnership.Login
    let t0 = Date(timeIntervalSince1970: 1_790_000_000)   // a fixed "now"
    func ms(_ date: Date) -> Double { date.timeIntervalSince1970 * 1000 }
    /// A login whose access token is `token`, expiring `hours` after `now`
    /// (nil = the credential carries no expiresAt).
    func login(_ token: String, expiresIn seconds: TimeInterval?, from now: Date = t0) -> Login {
        Login(accessToken: token, refreshToken: "refresh-\(token)", expiresAt: seconds.map { ms(now.addingTimeInterval($0)) })
    }
    let hour: TimeInterval = 3600
    let minute: TimeInterval = 60

    // MARK: fingerprint

    let fp = TokenFingerprint.of("sk-ant-oat01-SECRETSECRETSECRET")
    check(fp.count == 16 && fp.allSatisfy { "0123456789abcdef".contains($0) }, "guard: a fingerprint is 16 hex characters", fp)
    check(!fp.contains("SECRET") && fp == TokenFingerprint.of("sk-ant-oat01-SECRETSECRETSECRET")
          && fp != TokenFingerprint.of("sk-ant-oat01-SECRETSECRETSECRET2"),
          "guard: a fingerprint is stable, differs per token, and holds none of the token")
    check(TokenFingerprint.forLog(fp).count == 8, "guard: a log line gets at most the first 8 hex of a fingerprint")

    // MARK: expiry (rule b) and the three kinds of "ask"

    do {
        let g = UsageRequestGuard()
        let live = UUID(), backup = UUID()
        check(g.decision(for: live, isActive: true, login: login("A", expiresIn: -10 * minute), now: t0) == .waitForClaudeCode,
              "guard: an expired LIVE login waits for Claude Code and sends nothing")
        check(g.decision(for: backup, isActive: false, login: login("B", expiresIn: -10 * minute), now: t0) == .refreshFirst,
              "guard: an expired saved login is renewed first")
        check(g.decision(for: backup, isActive: false, login: login("B2", expiresIn: 2 * hour), now: t0) == .request
              && g.decision(for: live, isActive: true, login: login("A2", expiresIn: 2 * hour), now: t0) == .request,
              "guard: a login that is good for hours is asked")
        check(g.decision(for: backup, isActive: false, login: login("B3", expiresIn: 59), now: t0) == .refreshFirst
              && g.decision(for: live, isActive: true, login: login("A3", expiresIn: 59), now: t0) == .waitForClaudeCode,
              "guard: a token that expires within 60 seconds counts as expired")
        check(g.decision(for: backup, isActive: false, login: login("B4", expiresIn: 61), now: t0) == .request,
              "guard: a token with 61 seconds left is still asked")
        check(g.decision(for: backup, isActive: false, login: login("B5", expiresIn: nil), now: t0) == .request
              && g.decision(for: live, isActive: true, login: login("A5", expiresIn: nil), now: t0) == .request,
              "guard: a missing expiresAt is allowed (rule a stops it after one 401)")
    }

    // MARK: a refused token is never sent again (rule a), until the token changes

    for isActive in [false, true] {
        let g = UsageRequestGuard()
        let id = UUID()
        let dead = login("DEAD", expiresIn: nil)   // revoked before its expiry, so no expiresAt to warn us
        check(g.decision(for: id, isActive: isActive, login: dead, now: t0) == .request, "guard: the first ask for a token with no expiresAt goes out (\(isActive ? "live" : "saved"))")
        g.recordRejected(account: id, accessToken: "DEAD", now: t0)
        let after = [1.0, 5 * minute, 19 * minute, 21 * minute, 2 * hour, 3 * 24 * hour].map { g.decision(for: id, isActive: isActive, login: dead, now: t0.addingTimeInterval($0)) }
        check(after.allSatisfy { $0 != .request },
              "guard: a token that got a 401 is never asked again, however long we wait (\(isActive ? "live" : "saved"))", "\(after)")
        check(after.last == (isActive ? .waitForClaudeCode : .refreshFirst),
              "guard: after the cool-down a refused token waits (live) or is renewed (saved)", "\(String(describing: after.last))")
        let renewed = login("FRESH", expiresIn: 8 * hour, from: t0.addingTimeInterval(30 * minute))
        check(g.decision(for: id, isActive: isActive, login: renewed, now: t0.addingTimeInterval(30 * minute)) == .request,
              "guard: the moment the token changes the account is asked again (\(isActive ? "live" : "saved"))")
    }

    // A 401 where no account was known (the live login before its owner is proven) still blocks that token.
    do {
        let g = UsageRequestGuard()
        let id = UUID()
        g.recordRejected(account: nil, accessToken: "ORPHAN", now: t0)
        check(g.decision(for: id, isActive: true, login: login("ORPHAN", expiresIn: nil), now: t0.addingTimeInterval(hour)) == .waitForClaudeCode
              && !g.mayUse(accessToken: "ORPHAN", now: t0.addingTimeInterval(hour))
              && g.mayUse(accessToken: "OTHER", now: t0.addingTimeInterval(hour)),
              "guard: a token refused before its owner was known is blocked for every account")
    }

    // A 429 where no account was known is recorded too (spec D20 revision 4): with no active account the live
    // login is looked up with no account, and the hourly 429 loop of 1.9 must not come back through that path.
    do {
        let g = UsageRequestGuard()
        let id = UUID()
        let until = g.recordRateLimited(account: nil, accessToken: "ORPHAN-429", retryAfter: 3600, now: t0)
        check(until == t0.addingTimeInterval(3600), "guard: a 429 with no account known parks for the server's Retry-After")
        let valid = login("ORPHAN-429", expiresIn: 6 * hour)
        check(!g.mayUse(accessToken: "ORPHAN-429", now: t0.addingTimeInterval(minute))
              && !g.mayUse(accessToken: "ORPHAN-429", now: t0.addingTimeInterval(59 * minute))
              && g.mayUse(accessToken: "ORPHAN-429", now: t0.addingTimeInterval(61 * minute))
              && g.mayUse(accessToken: "OTHER", now: t0.addingTimeInterval(minute)),
              "guard: a token that got a 429 with no account known is refused until the park ends, and other tokens are not")
        check(g.decision(for: id, isActive: false, login: valid, now: t0.addingTimeInterval(30 * minute)) == .parked(until: until)
              && g.decision(for: id, isActive: true, login: valid, now: t0.addingTimeInterval(30 * minute)) == .parked(until: until),
              "guard: once an account owns that token its usage reading is held by the same park")
        check(g.decision(for: id, isActive: false, login: login("ORPHAN-429-RENEWED", expiresIn: 6 * hour), now: t0.addingTimeInterval(30 * minute)) == .request,
              "guard: a renewed token is not held by the old token's park")

        // The 20-minute cool-down applies even after a short Retry-After, like the account-bound case.
        let short = UsageRequestGuard()
        short.recordRateLimited(account: nil, accessToken: "SHORT", retryAfter: 0, now: t0)
        check(!short.mayUse(accessToken: "SHORT", now: t0.addingTimeInterval(19 * minute)) && short.mayUse(accessToken: "SHORT", now: t0.addingTimeInterval(21 * minute)),
              "guard: a 429 with no account known rests the token 20 minutes even if Retry-After was 0")

        // Repeated lookups cannot loop: after the first 429 every later ask is refused by mayUse.
        var asks = 0
        let loop = UsageRequestGuard()
        for step in 0..<59 {
            let now = t0.addingTimeInterval(Double(step) * minute)
            if loop.mayUse(accessToken: "LOOP", now: now) { asks += 1; loop.recordRateLimited(account: nil, accessToken: "LOOP", retryAfter: 3600, now: now) }
        }
        check(asks == 1, "guard: a profile lookup that gets 429 every time is sent once in the hour, not every cycle", "asked \(asks) times")

        // The record is bounded.
        let many = UsageRequestGuard()
        for index in 0..<200 { many.recordRateLimited(account: nil, accessToken: "T\(index)", retryAfter: 3600, now: t0) }
        check(!many.mayUse(accessToken: "T199", now: t0.addingTimeInterval(minute)),
              "guard: the newest 429s with no account are kept when the record is full")
    }

    // MARK: a 429 park belongs to its token (rule c)

    do {
        let g = UsageRequestGuard()
        let id = UUID()
        let until = g.recordRateLimited(account: id, accessToken: "T1", retryAfter: 3600, now: t0)
        check(until == t0.addingTimeInterval(3600), "guard: a 429 parks for the server's Retry-After")
        check(g.decision(for: id, isActive: false, login: login("T1", expiresIn: 5 * hour), now: t0.addingTimeInterval(30 * minute)) == .parked(until: until),
              "guard: a 429 parks the token it was earned on")
        check(g.decision(for: id, isActive: false, login: login("T2", expiresIn: 5 * hour), now: t0.addingTimeInterval(30 * minute)) == .request,
              "guard: a NEW token on the same account is not held by the old token's park")
        check(g.decision(for: id, isActive: false, login: login("T1", expiresIn: 5 * hour), now: t0.addingTimeInterval(30 * minute)) == .request,
              "guard: ...and the old park does not come back (it was forgotten with the old token)")
        check(g.recordRateLimited(account: id, accessToken: "T3", retryAfter: nil, now: t0) == t0.addingTimeInterval(120)
              && g.recordRateLimited(account: id, accessToken: "T3", retryAfter: 0, now: t0) == t0.addingTimeInterval(60)
              && g.recordRateLimited(account: id, accessToken: "T3", retryAfter: 999_999, now: t0) == t0.addingTimeInterval(3600),
              "guard: a missing Retry-After parks 2 minutes, never less than 1 minute or more than 1 hour")
    }

    // MARK: 20-minute cool-down after a 401 or 429, whatever the number of accounts (rule d)

    do {
        // After a 429 with a SHORT Retry-After (the old "burst" case), the token still rests for 20 minutes.
        let g = UsageRequestGuard()
        let id = UUID()
        g.recordRateLimited(account: id, accessToken: "T", retryAfter: 0, now: t0)
        let valid = login("T", expiresIn: 6 * hour)
        check(g.decision(for: id, isActive: false, login: valid, now: t0.addingTimeInterval(2 * minute)) == .coolingDown(until: t0.addingTimeInterval(20 * minute)),
              "guard: after a 429 the token rests 20 minutes even if Retry-After was 0")
        check(g.decision(for: id, isActive: false, login: valid, now: t0.addingTimeInterval(20 * minute - 1)) != .request
              && g.decision(for: id, isActive: false, login: valid, now: t0.addingTimeInterval(20 * minute + 1)) == .request,
              "guard: the cool-down ends at 20 minutes")
        check(g.mayUse(accessToken: "T", now: t0.addingTimeInterval(minute)) == false
              && g.mayUse(accessToken: "T", now: t0.addingTimeInterval(21 * minute)),
              "guard: a profile lookup is held back by the same cool-down")
    }
    // The round-robin speed-up: when other accounts are parked, the remaining ones are visited more often. Even a
    // visit EVERY MINUTE, at any number of accounts, must not send a 429'd token inside its 20 minutes.
    for others in 1...5 {
        let g = UsageRequestGuard()
        let accounts = (0..<others).map { _ in UUID() }
        let victim = accounts[0]
        g.recordRateLimited(account: victim, accessToken: "VICTIM", retryAfter: 60, now: t0)
        var sentWithinCooldown = 0
        var sentAfter = 0
        for minuteIndex in 0..<30 {
            let visited = accounts[minuteIndex % accounts.count]
            let token = visited == victim ? "VICTIM" : "OK-\(visited)"
            let now = t0.addingTimeInterval(Double(minuteIndex) * minute + 1)
            if g.decision(for: visited, isActive: false, login: login(token, expiresIn: 6 * hour), now: now) == .request, visited == victim {
                if now < t0.addingTimeInterval(20 * minute) { sentWithinCooldown += 1 } else { sentAfter += 1 }
            }
        }
        check(sentWithinCooldown == 0, "guard: a 429'd token is not asked within 20 minutes with \(others) account(s) polling", "sent \(sentWithinCooldown)")
        check(sentAfter > 0,
              "guard: ...and is asked again once the 20 minutes are up (\(others) account(s))", "sent \(sentAfter)")
    }

    // MARK: a token change clears everything (R3)

    do {
        let g = UsageRequestGuard()
        let id = UUID()
        g.recordRejected(account: id, accessToken: "OLD", now: t0)
        g.recordRateLimited(account: id, accessToken: "OLD", retryAfter: 3600, now: t0)
        g.markNeedsReauth(account: id, accessToken: "OLD")
        check(g.decision(for: id, isActive: false, login: login("OLD", expiresIn: nil), now: t0.addingTimeInterval(minute)) == .needsReauth,
              "guard: with the refusal, the park, the cool-down and needs-reauth all set, the dead token sends nothing")
        let cleared = g.credentialChanged(account: id, accessToken: "NEW")
        check(cleared && !g.isNeedsReauth(id) && g.needsReauthAccounts.isEmpty, "guard: a credential change clears needs-reauth", "\(cleared)")
        check(g.decision(for: id, isActive: false, login: login("NEW", expiresIn: 8 * hour), now: t0.addingTimeInterval(2 * minute)) == .request,
              "guard: ...and the park, the refusal and the cool-down, so the new token is asked at once")
        check(g.credentialChanged(account: id, accessToken: "NEW") == false, "guard: telling it again, with nothing left to forget, changes nothing")
    }
    do {
        // The hook with the SAME token must not forget what that token earned.
        let g = UsageRequestGuard()
        let id = UUID()
        g.recordRateLimited(account: id, accessToken: "SAME", retryAfter: 3600, now: t0)
        g.credentialChanged(account: id, accessToken: "SAME")
        check(g.decision(for: id, isActive: false, login: login("SAME", expiresIn: 5 * hour), now: t0.addingTimeInterval(minute)) == .parked(until: t0.addingTimeInterval(3600)),
              "guard: a credential change that kept the same access token does not lift its park")
    }
    do {
        // Without the hook: a decision with a different token forgets it by itself (rules a, c, d, e).
        let g = UsageRequestGuard()
        let id = UUID()
        g.markNeedsReauth(account: id, accessToken: "DEADGRANT")
        check(g.decision(for: id, isActive: false, login: login("DEADGRANT", expiresIn: -hour), now: t0) == .needsReauth
              && g.decision(for: id, isActive: false, login: login("DEADGRANT", expiresIn: -hour), now: t0.addingTimeInterval(7 * 24 * hour)) == .needsReauth,
              "guard: needs-reauth holds for as long as the account keeps the same token")
        check(g.decision(for: id, isActive: true, login: login("SIGNEDINAGAIN", expiresIn: 8 * hour, from: t0.addingTimeInterval(8 * 24 * hour)), now: t0.addingTimeInterval(8 * 24 * hour)) == .request,
              "guard: needs-reauth is cleared when the account holds a different token (it became active with a new login)")
    }
    do {
        // A flag restored after a relaunch (ids only) adopts the first token it sees, and a credential change clears it.
        let id = UUID()
        let g = UsageRequestGuard(needsReauth: [id])
        check(g.isNeedsReauth(id) && g.decision(for: id, isActive: false, login: login("X", expiresIn: -hour), now: t0) == .needsReauth,
              "guard: a flag restored after a relaunch still holds")
        g.credentialChanged(account: id, accessToken: "Y")
        check(!g.isNeedsReauth(id), "guard: ...and a credential change clears it")
        let g2 = UsageRequestGuard()
        g2.restoreNeedsReauth([id])
        g2.markNeedsReauth(account: id, accessToken: "DEAD")
        check(g2.decision(for: id, isActive: false, login: login("OTHER", expiresIn: 5 * hour), now: t0) == .request,
              "guard: a restored flag tied to its dead token is cleared by any other token")
    }

    // MARK: needs-reauth is only for the server's refusal (R4)

    do {
        let flags: [(BackupRefreshOutcome, Bool)] = [
            (.grantRejected, true), (.rotationLost, true), (.belongsToAnotherAccount("x@y.z"), true),
            (.sameGrantAsAnotherLogin, false), (.unreachable, false), (.noBackup, false), (.storeUnavailable, false), (.refreshed("{}"), false),
        ]
        check(flags.allSatisfy { $0.0.requiresReauth == $0.1 }, "guard: only invalid_grant, a lost rotation and another account's login mean sign in again")
        check(!BackupRefreshOutcome.sameGrantAsAnotherLogin.requiresReauth,
              "guard: PixelSwitch's own same-grant guard does NOT set needs-reauth (a healthy account is never flagged)")
        let g = UsageRequestGuard()
        let id = UUID()
        // What a same-grant refusal does to the guard: nothing at all.
        check(g.decision(for: id, isActive: false, login: login("EXPIRED", expiresIn: -hour), now: t0) == .refreshFirst && !g.isNeedsReauth(id),
              "guard: an expired saved login is renewable, not flagged")
    }

    // MARK: a flag with no token never takes the live login (review finding P1, 2026-10-16)

    do {
        // The flag came from a removed backup (no token). The account later
        // becomes the active one through a sign-in PixelSwitch never hooked.
        let id = UUID()
        let g = UsageRequestGuard()
        g.markNeedsReauth(account: id, accessToken: nil)
        check(g.decision(for: id, isActive: true, login: login("LIVE", expiresIn: 8 * hour), now: t0) == .request,
              "guard: a no-token needs-reauth flag plus an active account with a valid token gives request")
        check(!g.isNeedsReauth(id), "guard: ...and the flag is gone, so it is not saved again")
        check(g.decision(for: id, isActive: true, login: login("LIVE", expiresIn: 8 * hour), now: t0.addingTimeInterval(4 * hour)) == .request,
              "guard: ...and four hours later the live login is still read")
        // The same after a relaunch (restored flag, no token).
        let r = UsageRequestGuard(needsReauth: [id])
        check(r.decision(for: id, isActive: true, login: login("LIVE", expiresIn: 8 * hour), now: t0) == .request,
              "guard: a restored flag does not latch onto the active account's live token")
        // A saved login still adopts the first token it sees, then clears on a change.
        let b = UsageRequestGuard(needsReauth: [id])
        check(b.decision(for: id, isActive: false, login: login("DEAD", expiresIn: -hour), now: t0) == .needsReauth,
              "guard: a restored flag still holds a saved login")
        check(b.decision(for: id, isActive: false, login: login("NEW", expiresIn: 8 * hour), now: t0) == .request,
              "guard: ...until that saved login changes")
        // A flag tied to a token keeps holding the active account on that very token.
        let t = UsageRequestGuard()
        t.markNeedsReauth(account: id, accessToken: "LIVE")
        check(t.decision(for: id, isActive: true, login: login("LIVE", expiresIn: 8 * hour), now: t0) == .needsReauth,
              "guard: a flag tied to the token it was raised on still holds")
    }

    // MARK: the local same-grant refusal, and the rejected-token question the switch asks

    do {
        check(BackupRefreshOutcome.localRefusal(isLive: true, sharesLineage: false) == .sameGrantAsAnotherLogin
              && BackupRefreshOutcome.localRefusal(isLive: false, sharesLineage: true) == .sameGrantAsAnotherLogin,
              "guard: the same grant as the live login or another account's is refused locally as sameGrantAsAnotherLogin")
        check(BackupRefreshOutcome.localRefusal(isLive: false, sharesLineage: false) == nil,
              "guard: an own grant is not refused locally")
        check(BackupRefreshOutcome.localRefusal(isLive: true, sharesLineage: true)?.requiresReauth == false,
              "guard: the local refusal never means sign in again")
        check(!BackupRefreshOutcome.unreachable.requiresReauth, "guard: an unreachable token endpoint does not flag the account")
        let g = UsageRequestGuard()
        let id = UUID()
        g.recordRejected(account: id, accessToken: "T", now: t0)
        check(g.wasRejected(account: id, accessToken: "T") && !g.wasRejected(account: id, accessToken: "U"),
              "guard: after a 401 the switch can tell the token was refused")
        check(g.decision(for: id, isActive: false, login: login("T", expiresIn: 5 * hour), now: t0) != .refreshFirst,
              "guard: ...even though decision() answers cooling down, which is why the switch asks wasRejected")
    }

    // MARK: the measured histories, replayed

    do {
        // ahmed@pixelventures.ai, 2026-10-12: a saved login whose refresh grant was revoked. It used to cost a usage 401
        // plus a refresh POST failing invalid_grant on EVERY round-robin visit, for days (473 such lines).
        let g = UsageRequestGuard()
        let id = UUID()
        let saved = login("PV-SAVED", expiresIn: nil, from: t0)   // not expired by its expiresAt, revoked server-side
        var usageRequests = 0, refreshPosts = 0
        var now = t0
        for visit in 0..<(3 * 24 * 12) {                          // every 5 minutes for 3 days
            now = t0.addingTimeInterval(Double(visit) * 5 * minute)
            switch g.decision(for: id, isActive: false, login: saved, now: now) {
            case .request:
                usageRequests += 1
                g.recordRejected(account: id, accessToken: "PV-SAVED", now: now)      // the one 401
                refreshPosts += 1
                g.markNeedsReauth(account: id, accessToken: "PV-SAVED")              // invalid_grant: the server refused the grant
            case .refreshFirst:
                refreshPosts += 1
                g.markNeedsReauth(account: id, accessToken: "PV-SAVED")
            default:
                break
            }
        }
        check(usageRequests == 1 && refreshPosts == 1,
              "guard: replay of the dead-grant history: one 401, one invalid_grant, then no request for 3 days", "usage \(usageRequests), refresh \(refreshPosts)")
        check(g.decision(for: id, isActive: false, login: saved, now: now) == .needsReauth, "guard: ...and the account is waiting to sign in again")
        // Signing in again.
        g.credentialChanged(account: id, accessToken: "PV-REAUTH")
        check(g.decision(for: id, isActive: false, login: login("PV-REAUTH", expiresIn: 8 * hour, from: now), now: now) == .request,
              "guard: ...and a new sign-in is read at once")
    }
    do {
        // claude@pixelventures.ai, 2026-10-13: the live login expired at 01:13 while Claude Code sat idle. It used to
        // send a usage request AND a profile lookup every 5 minutes for 24 hours (453 profile 401s) and earned a 429
        // an hour. Claude Code renewed it on 10-14 at about 01:01.
        let g = UsageRequestGuard()
        let id = UUID()
        let stale = login("CPV-STALE", expiresIn: 0, from: t0)                      // expiresAt = t0
        var sent = 0
        var profileLookups = 0
        for tick in 1..<(24 * 12) {                                                  // every 5 minutes for 24 hours
            let now = t0.addingTimeInterval(Double(tick) * 5 * minute)
            if g.decision(for: id, isActive: true, login: stale, now: now) == .request { sent += 1 }
            if g.mayUse(accessToken: "CPV-STALE", expiresAt: stale.expiresAt, now: now) { profileLookups += 1 }
        }
        check(sent == 0 && profileLookups == 0,
              "guard: replay of the stale live login: no usage request and no profile lookup until the token changes", "usage \(sent), profile \(profileLookups)")
        let renewedAt = t0.addingTimeInterval(24 * hour)
        let renewed = login("CPV-RENEWED", expiresIn: 8 * hour, from: renewedAt)
        check(g.decision(for: id, isActive: true, login: renewed, now: renewedAt) == .request
              && g.mayUse(accessToken: "CPV-RENEWED", expiresAt: renewed.expiresAt, now: renewedAt),
              "guard: ...then one request the moment Claude Code renews it")
    }

    // MARK: the card never lies (R6)

    do {
        let until = t0.addingTimeInterval(hour)
        typealias N = UsageNotice
        check(N.choose(for: .parked(until: until), current: .resign) == .keepCurrent
              && N.choose(for: .parked(until: until), current: .noSubscription) == .keepCurrent,
              "notice: a 429 pause never overwrites a re-sign or no-subscription message")
        check(N.choose(for: .parked(until: until), current: .nothing) == .paused(until: until)
              && N.choose(for: .parked(until: until), current: .transient) == .paused(until: until),
              "notice: a 429 pause shows until when, and keeps the last reading")
        check(N.choose(for: .waitForClaudeCode, current: .nothing) == .waitingForClaudeCode
              && N.choose(for: .waitForClaudeCode, current: .transient) == .waitingForClaudeCode
              && N.choose(for: .waitForClaudeCode, current: .resign) == .keepCurrent,
              "notice: the live login waiting for Claude Code says so, and keeps the last reading")
        check(N.choose(for: .needsReauth, current: .nothing) == .resign
              && N.choose(for: .needsReauth, current: .transient) == .resign
              && N.choose(for: .needsReauth, current: .noSubscription) == .resign,
              "notice: an account that must sign in again shows the re-sign message")
        check(N.choose(for: .needsReauth, current: .resign) == .keepCurrent,
              "notice: ...once, so a more specific re-sign message stays")
        check(N.choose(for: .coolingDown(until: until), current: .nothing) == .keepCurrent
              && N.choose(for: .request, current: .resign) == .keepCurrent
              && N.choose(for: .refreshFirst, current: .transient) == .keepCurrent,
              "notice: a cool-down, or an ask, changes nothing on the card")
    }

    // MARK: words, in all five languages

    do {
        let paused = "Usage reading paused until %@. Claude Code itself is not affected."
        let waiting = "Waiting for Claude Code to renew this login. It renews the next time a session runs."
        let languages = ["en", "de", "fr", "ja", "zh-Hans"]
        let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
        check(tables.allSatisfy { $0[paused] != nil && $0[waiting] != nil && $0[paused]?.contains("%@") == true },
              "l10n: the pause and wait sentences exist in all five languages, and the pause names a time")
        check(tables.allSatisfy { $0["API Rate Limited. Try again later."] == nil },
              "l10n: \"API Rate Limited. Try again later.\" is gone from all five languages")
        check(tables[0][paused] == paused && tables[0][waiting] == waiting, "l10n: the English sentences are the ones the founder approved")
        let rateLimited = UsageRequestError.rateLimited(retryAfter: 3600).message
        check(!rateLimited.contains("API Rate Limited") && rateLimited.contains("Claude Code itself is not affected"),
              "usage errors: a 429 never reads \"API Rate Limited\"", rateLimited)
        check(UsageRequestError.pausedMessage(until: t0).contains("Claude Code itself is not affected"),
              "usage errors: the pause message says Claude Code is not affected")
    }
}
