// "Needs authentication": when Claude Code has given up on the live login
// (AuthHealth), the two-read confirmation (LiveLoginWatch), what auto-switch
// does about it (DeadLoginPolicy, fed to the unchanged engine as `isUnusable`),
// and the renewal of a target that is about to expire (TargetRenewalPolicy).
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from
// main.swift. Uses the global `check(_:_:_:)` defined there.
//
// The founder, 2026-11-10: "when switching sometimes authentication has expired
// and the Claude sessions then get stuck. The PixelSwitch app should check for
// this and not switch to an account that needs authentication. Furthermore it
// should also check throughout the time an account is being used and if it
// detects that authentication is required again it automatically switches
// accounts."
//
// The real case (PixelSwitch-app.log, 2026-11-10): at 10:40:31Z the live
// credential shrank by 202 characters (Claude Code blanked the login) and
// `claude auth status` began to exit 1; both repeated at every 5-minute cycle
// for three hours while auto-switch sat still.
import Foundation

@MainActor func runAuthHealthTests() {
    shapeTests()
    judgeTests()
    watchTests()
    replayTests()
    policyTests()
    engineTests()
    renewalTests()
    wiringPins()
}

// MARK: - Fixtures

private let t0 = Date(timeIntervalSince1970: 1_790_000_000)

private func ms(_ date: Date) -> Double { date.timeIntervalSince1970 * 1000 }

/// A credential shaped like Claude Code's, with the given `claudeAiOauth` fields.
/// `expiresAt` nil leaves the key out. The values are obviously fake.
private func credential(access: String? = "fake-access-token", refresh: String? = "fake-refresh-token",
                        expiresAt: Double? = ms(t0.addingTimeInterval(4 * 3600))) -> String {
    var fields: [String] = []
    if let access { fields.append(#""accessToken":"\#(access)""#) }
    if let refresh { fields.append(#""refreshToken":"\#(refresh)""#) }
    if let expiresAt { fields.append(#""expiresAt":\#(Int64(expiresAt))"#) }
    fields.append(#""scopes":["user:inference","user:profile"],"subscriptionType":"max""#)
    return #"{"claudeAiOauth":{"# + fields.joined(separator: ",") + #"},"mcpOAuth":{"x":{"serverName":"x"}}}"#
}

/// What Claude Code leaves behind when it has found a login dead.
private let blankedCredential = credential(access: "", refresh: "", expiresAt: 0)

// MARK: - The shape of the live credential

@MainActor private func shapeTests() {
    typealias Shape = AuthHealth.Shape
    func shape(_ json: String?) -> Shape { AuthHealth.shape(ofCredential: json, now: t0) }

    check(shape(blankedCredential) == .blank, "auth: Claude Code's blanked login (both tokens empty, expiresAt 0) is the blank shape")
    check(shape(credential(access: "", refresh: "", expiresAt: ms(t0.addingTimeInterval(-3600)))) == .blank,
          "auth: both tokens empty is blank whatever expiresAt was left at")
    check(shape(credential(access: "   ", refresh: "\\n", expiresAt: nil)) == .blank,
          "auth: tokens that are only whitespace count as empty")
    check(shape(credential(access: nil, refresh: nil, expiresAt: nil)) == .blank,
          "auth: tokens that are missing count as empty")
    check(shape(credential(expiresAt: 0)) == .blank,
          "auth: an expiresAt of 0 is the blank shape (the founder's brief: 'or expiresAt 0')")

    check(shape(credential(access: "", refresh: "fake-refresh-token")) == .present(isExpired: false),
          "auth: an empty access token with a refresh token left is not blank: Claude Code can still renew it")
    check(shape(credential(access: "fake-access-token", refresh: "")) == .present(isExpired: false),
          "auth: an empty refresh token with an access token left is not blank")
    check(shape(credential()) == .present(isExpired: false), "auth: a normal login is present and not expired")
    check(shape(credential(expiresAt: ms(t0.addingTimeInterval(-5 * 3600)))) == .present(isExpired: true),
          "auth: an expired access token is still a present login, only expired (an idle Claude Code leaves it for hours)")
    check(shape(credential(expiresAt: ms(t0.addingTimeInterval(30)))) == .present(isExpired: true),
          "auth: an access token with under a minute left reads as expired, like the usage guard's slack")
    check(shape(credential(expiresAt: nil)) == .present(isExpired: false), "auth: no expiresAt is present and not known to be expired")

    check(shape(nil) == .unreadable, "auth: a Keychain read that returned nothing is unreadable, never signed out")
    check(shape("") == .unreadable && shape("not json") == .unreadable && shape("[1,2]") == .unreadable,
          "auth: text that is not a JSON object is unreadable")
    check(shape(#"{"mcpOAuth":{}}"#) == .noOAuthLogin,
          "auth: a credential with no claudeAiOauth is a Mac that signs in another way, not a blanked login")
    check(shape(#"{"claudeAiOauth":null}"#) == .noOAuthLogin && shape(#"{"claudeAiOauth":"x"}"#) == .noOAuthLogin,
          "auth: a claudeAiOauth that is not an object is not the blank shape")

    check(AuthHealth.needsCLIProbe(.blank)
          && !AuthHealth.needsCLIProbe(.present(isExpired: false)) && !AuthHealth.needsCLIProbe(.present(isExpired: true))
          && !AuthHealth.needsCLIProbe(.unreadable) && !AuthHealth.needsCLIProbe(.noOAuthLogin),
          "auth: `claude auth status` is spawned only after the blank shape is seen")
    check(AuthHealth.watchInterval == 30, "auth: the live login is looked at every 30 seconds")
}

// MARK: - The verdict of one read

@MainActor private func judgeTests() {
    typealias Verdict = AuthHealth.Verdict
    let probes: [AuthHealth.CLIProbe?] = [.loggedIn, .failed, .unavailable, nil]

    check(AuthHealth.judge(shape: .blank, cli: .failed) == .signedOutSeen, "auth: blank login and `claude auth status` failing is signed out (seen once)")
    check(AuthHealth.judge(shape: .blank, cli: .loggedIn) == .healthy,
          "auth: a blank login file while the CLI says it is logged in (a token from the environment) is healthy")
    check(AuthHealth.judge(shape: .blank, cli: .unavailable) == .unknown && AuthHealth.judge(shape: .blank, cli: nil) == .unknown,
          "auth: a blank login and a CLI that timed out, did not start, or was not asked is unknown, not signed out")

    for cli in probes {
        check(AuthHealth.judge(shape: .present(isExpired: false), cli: cli) == .healthy,
              "auth: a present login is healthy whatever the CLI says (\(String(describing: cli)))")
        check(AuthHealth.judge(shape: .present(isExpired: true), cli: cli) == .waitingForClaudeCode,
              "auth: an expired login is waiting for Claude Code, never signed out (\(String(describing: cli)))")
        check(AuthHealth.judge(shape: .unreadable, cli: cli) == .unknown && AuthHealth.judge(shape: .noOAuthLogin, cli: cli) == .unknown,
              "auth: an unreadable credential, or one with no claudeAiOauth, is unknown whatever the CLI says (\(String(describing: cli)))")
    }

    check(AuthHealth.judge(credential: blankedCredential, cli: .failed, now: t0) == .signedOutSeen
          && AuthHealth.judge(credential: credential(), cli: .failed, now: t0) == .healthy
          && AuthHealth.judge(credential: nil, cli: .failed, now: t0) == .unknown,
          "auth: judging a credential string reads its shape first")

    check(AuthHealth.probe(exitedZeroLoggedIn: true) == .loggedIn && AuthHealth.probe(exitedZeroLoggedIn: false) == .failed,
          "auth: a CLI that exits 0 is logged in; one that says loggedIn false counts as failed")
}

// MARK: - The two-read confirmation

@MainActor private func watchTests() {
    typealias Watch = LiveLoginWatch
    let a = UUID(), b = UUID()
    func at(_ seconds: TimeInterval) -> Date { t0.addingTimeInterval(seconds) }

    check(Watch.confirmationGap == 15 && Watch.reprobeInterval == 300, "auth: two reads at least 15 seconds apart; a confirmed login is re-asked of the CLI every 5 minutes")

    // One read never confirms.
    do {
        var w = Watch()
        check(w.observe(.signedOutSeen, account: a, at: at(0)) == .firstSighting && w.confirmedDeadAccount == nil,
              "auth: one signed-out read never confirms; it starts the wait")
    }
    // Two reads 15 seconds apart do.
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        check(w.observe(.signedOutSeen, account: a, at: at(15)) == .confirmed && w.confirmedDeadAccount == a,
              "auth: a second signed-out read 15 seconds after the first confirms, for that account")
        check(w.observe(.signedOutSeen, account: a, at: at(45)) == .none && w.confirmedDeadAccount == a,
              "auth: later signed-out reads change nothing, so nothing is logged again")
    }
    // Too soon does not count; the gap runs from the first read.
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        check(w.observe(.signedOutSeen, account: a, at: at(14)) == .none && w.confirmedDeadAccount == nil,
              "auth: a second read 14 seconds after the first is too soon to count")
        check(w.observe(.signedOutSeen, account: a, at: at(16)) == .confirmed,
              "auth: the gap is measured from the first read, so a read at 16 seconds confirms")
    }
    // A healthy or waiting read in between resets.
    for between in [AuthHealth.Verdict.healthy, .waitingForClaudeCode] {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        check(w.observe(between, account: a, at: at(20)) == .none, "auth: a \(between) read after one signed-out read is not a recovery, only a reset")
        check(w.observe(.signedOutSeen, account: a, at: at(40)) == .firstSighting && w.confirmedDeadAccount == nil,
              "auth: after a \(between) read the next signed-out read starts over (one failed read is never enough)")
    }
    // Unknown reads neither confirm nor reset.
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        check(w.observe(.unknown, account: a, at: at(20)) == .none && w.confirmedDeadAccount == nil, "auth: an unknown read does not confirm")
        check(w.observe(.signedOutSeen, account: a, at: at(40)) == .confirmed, "auth: an unknown read does not reset either, so the next signed-out read confirms")
        check(w.observe(.unknown, account: a, at: at(70)) == .none && w.confirmedDeadAccount == a,
              "auth: once confirmed, unknown reads (a CLI timeout, a Keychain hiccup) do not undo it")
    }
    // Recovery.
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        _ = w.observe(.signedOutSeen, account: a, at: at(30))
        check(w.observe(.healthy, account: a, at: at(60)) == .recovered && w.confirmedDeadAccount == nil,
              "auth: a healthy read after confirmation is a recovery, and the account is no longer dead")
        check(w.observe(.healthy, account: a, at: at(90)) == .none, "auth: recovery is reported once")
        var v = Watch()
        _ = v.observe(.signedOutSeen, account: a, at: at(0))
        _ = v.observe(.signedOutSeen, account: a, at: at(30))
        check(v.observe(.waitingForClaudeCode, account: a, at: at(60)) == .recovered,
              "auth: a login that is back but expired (Claude Code will renew it) is a recovery too")
    }
    // A different account starts over.
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        check(w.observe(.signedOutSeen, account: b, at: at(20)) == .firstSighting && w.confirmedDeadAccount == nil,
              "auth: a read for another account does not complete the first account's confirmation")
        _ = w.observe(.signedOutSeen, account: b, at: at(40))
        check(w.confirmedDeadAccount == b && w.observe(.signedOutSeen, account: a, at: at(60)) == .firstSighting && w.confirmedDeadAccount == nil,
              "auth: the account that was confirmed dead is forgotten the moment another account is the one being read")
        check(w.observe(.signedOutSeen, account: nil, at: at(80)) == .none && w.confirmedDeadAccount == nil,
              "auth: with no active account there is nothing to confirm")
    }
    // The clock going backwards cannot stall a confirmation for ever.
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(1000))
        check(w.observe(.signedOutSeen, account: a, at: at(0)) == .firstSighting, "auth: a read stamped before the first one starts the wait again")
        check(w.observe(.signedOutSeen, account: a, at: at(20)) == .confirmed, "auth: and then confirms 20 seconds later")
    }
    // reset().
    do {
        var w = Watch()
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        _ = w.observe(.signedOutSeen, account: a, at: at(30))
        w.reset()
        check(w.confirmedDeadAccount == nil && w.observe(.signedOutSeen, account: a, at: at(60)) == .firstSighting,
              "auth: reset (a switch, a sign-in) forgets the confirmation")
    }
    // When to spawn the CLI.
    do {
        var w = Watch()
        check(w.shouldProbeCLI(for: .blank, now: at(0)), "auth: a blank login that is not confirmed yet always asks the CLI")
        check(!w.shouldProbeCLI(for: .present(isExpired: false), now: at(0)) && !w.shouldProbeCLI(for: .unreadable, now: at(0)),
              "auth: no other shape asks the CLI")
        w.noteProbe(at: at(0))
        _ = w.observe(.signedOutSeen, account: a, at: at(0))
        check(w.shouldProbeCLI(for: .blank, now: at(30)), "auth: still unconfirmed, so every read asks the CLI")
        w.noteProbe(at: at(30))
        _ = w.observe(.signedOutSeen, account: a, at: at(30))
        check(!w.shouldProbeCLI(for: .blank, now: at(60)) && !w.shouldProbeCLI(for: .blank, now: at(329)),
              "auth: once confirmed, the CLI is not spawned every 30 seconds (it would fill the log)")
        check(w.shouldProbeCLI(for: .blank, now: at(330)),
              "auth: but it is asked again after 5 minutes, so a login restored another way is noticed")
    }
}

// MARK: - The real case, and the cases that must not trigger

@MainActor private func replayTests() {
    let account = UUID()
    /// Feeds one tick the way AppState does: read the shape, spawn the CLI only
    /// when the shape says to, judge, observe.
    func tick(_ w: inout LiveLoginWatch, _ json: String?, cli: AuthHealth.CLIProbe, at seconds: TimeInterval) -> LiveLoginWatch.Change {
        let now = t0.addingTimeInterval(seconds)
        let shape = AuthHealth.shape(ofCredential: json, now: now)
        var probe: AuthHealth.CLIProbe?
        if AuthHealth.needsCLIProbe(shape), w.shouldProbeCLI(for: shape, now: now) {
            probe = cli
            w.noteProbe(at: now)
        }
        return w.observe(AuthHealth.judge(shape: shape, cli: probe), account: account, at: now)
    }

    // The log of 2026-11-10: healthy at 10:35:31Z, then the blank credential and exit 1 from 10:40:31Z.
    do {
        var w = LiveLoginWatch()
        let healthy = credential(expiresAt: ms(t0.addingTimeInterval(6 * 3600)))
        check(tick(&w, healthy, cli: .loggedIn, at: 0) == .none, "auth replay: 10:35:31 a healthy login changes nothing")
        let t = 300.0   // 10:40:31
        check(tick(&w, blankedCredential, cli: .failed, at: t) == .firstSighting && w.confirmedDeadAccount == nil,
              "auth replay: 10:40:31 blank credential and `claude auth status` exit 1: the first read, not yet dead")
        check(tick(&w, blankedCredential, cli: .failed, at: t + 30) == .confirmed && w.confirmedDeadAccount == account,
              "auth replay: the next read (30 seconds later) confirms it, instead of three hours of nothing")
        for n in 2...40 {
            _ = tick(&w, blankedCredential, cli: .failed, at: t + Double(n) * 30)
        }
        check(w.confirmedDeadAccount == account, "auth replay: it stays dead while the login stays blank")
        // 13:39:42 a switch by hand; the new live login is healthy.
        w.reset()
        check(tick(&w, credential(expiresAt: ms(t0.addingTimeInterval(20 * 3600))), cli: .loggedIn, at: t + 3 * 3600) == .none
              && w.confirmedDeadAccount == nil, "auth replay: after the switch the new login reads healthy")
    }

    // The dead login comes back without a switch: the user signs in again at the terminal.
    do {
        var w = LiveLoginWatch()
        _ = tick(&w, blankedCredential, cli: .failed, at: 0)
        _ = tick(&w, blankedCredential, cli: .failed, at: 30)
        check(tick(&w, credential(), cli: .loggedIn, at: 60) == .recovered && w.confirmedDeadAccount == nil,
              "auth replay: a login that comes back (a re-sign) clears the dead state")
    }

    // Must NOT trigger: an idle Claude Code leaves an expired token for hours, whatever the CLI says.
    for cli in [AuthHealth.CLIProbe.loggedIn, .failed, .unavailable] {
        var w = LiveLoginWatch()
        let expired = credential(expiresAt: ms(t0.addingTimeInterval(-9 * 3600)))
        var changes = Set<String>()
        for n in 0..<200 { changes.insert("\(tick(&w, expired, cli: cli, at: Double(n) * 30))") }
        check(w.confirmedDeadAccount == nil && changes == ["none"],
              "auth replay: an access token that expired nine hours ago is normal and never needs authentication (CLI \(cli))")
    }
    // Must NOT trigger: a usage 401, a 429, a 5xx, no network. None of them changes the credential, so every
    // read is a present login, and a present login is never signed out, whatever else is going on.
    do {
        var w = LiveLoginWatch()
        for n in 0..<200 { _ = tick(&w, credential(), cli: .failed, at: Double(n) * 30) }
        check(w.confirmedDeadAccount == nil, "auth replay: a login that is still in the Keychain is never dead, whatever the usage endpoint answers")
    }
    // Must NOT trigger: one failed read.
    do {
        var w = LiveLoginWatch()
        check(tick(&w, blankedCredential, cli: .failed, at: 0) == .firstSighting, "auth replay: one blank read with a failing CLI")
        check(tick(&w, credential(), cli: .loggedIn, at: 30) == .none && w.confirmedDeadAccount == nil,
              "auth replay: then a healthy read: nothing was ever confirmed")
        check(tick(&w, blankedCredential, cli: .failed, at: 60) == .firstSighting, "auth replay: a later blank read starts from one again")
    }
    // Must NOT trigger: a CLI that times out (or will not start) on the blank login.
    do {
        var w = LiveLoginWatch()
        for n in 0..<100 { _ = tick(&w, blankedCredential, cli: .unavailable, at: Double(n) * 30) }
        check(w.confirmedDeadAccount == nil, "auth replay: a blank login while the CLI never answers is never confirmed")
    }
    // Must NOT trigger: no Keychain item readable (a locked Keychain, an item mid-write).
    do {
        var w = LiveLoginWatch()
        _ = tick(&w, blankedCredential, cli: .failed, at: 0)
        for n in 1..<50 { _ = tick(&w, nil, cli: .failed, at: Double(n) * 30) }
        check(w.confirmedDeadAccount == nil, "auth replay: unreadable reads neither confirm a blank login nor count as one")
    }
    // Must NOT trigger: a Mac that signs in with an environment token has no claudeAiOauth at all.
    do {
        var w = LiveLoginWatch()
        for n in 0..<100 { _ = tick(&w, #"{"mcpOAuth":{}}"#, cli: .failed, at: Double(n) * 30) }
        check(w.confirmedDeadAccount == nil, "auth replay: no claudeAiOauth in the credential is not a blanked login")
    }
}

// MARK: - What auto-switch does about a dead login

@MainActor private func policyTests() {
    typealias Policy = DeadLoginPolicy
    check(Policy.response(autoSwitchEnabled: true, activeIsManualOnly: false) == .leaveNow,
          "auth policy: auto-switch on, an ordinary account: leave it now")
    check(Policy.response(autoSwitchEnabled: false, activeIsManualOnly: false) == .stayAndShow(.autoSwitchOff),
          "auth policy: auto-switch off: the user's choice stands; the account is only shown as needing sign-in")
    check(Policy.response(autoSwitchEnabled: true, activeIsManualOnly: true) == .stayAndShow(.manualOnly),
          "auth policy: a Manual only account is not left on the app's own say-so: flagged and shown only")
    check(Policy.response(autoSwitchEnabled: false, activeIsManualOnly: true) == .stayAndShow(.autoSwitchOff),
          "auth policy: auto-switch off wins over Manual only as the reason")
    check(Policy.leavesAutomatically(autoSwitchEnabled: true, activeIsManualOnly: false)
          && !Policy.leavesAutomatically(autoSwitchEnabled: true, activeIsManualOnly: true)
          && !Policy.leavesAutomatically(autoSwitchEnabled: false, activeIsManualOnly: false),
          "auth policy: only an ordinary account under auto-switch is fed to the engine as unusable")
}

// MARK: - The unchanged engine, fed a dead live login through isUnusable

private func authSample(_ session: Double?, _ weekly: Double?) -> UsageAPIResponse {
    let iso = ISO8601DateFormatter()
    func window(_ util: Double?, hours: Double) -> String {
        let u = util.map { #""utilization":\#($0)"# } ?? #""utilization":null"#
        return "{" + u + #","resets_at":"\#(iso.string(from: t0.addingTimeInterval(hours * 3600)))""# + "}"
    }
    let json = #"{"five_hour":"# + window(session, hours: 2) + #","seven_day":"# + window(weekly, hours: 100) + "}"
    return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data(json.utf8))
}

@MainActor private func engineTests() {
    let a = Account(email: "a@x.com", displayName: "A", isActive: true)
    let manualA = Account(id: a.id, email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 0)
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    let manualB = Account(email: "m@x.com", displayName: "M", switchThreshold: 0)

    /// The engine as AppState calls it for a dead live login: the policy decides
    /// whether the active account is fed as unusable; `unswitchable` stands for
    /// accounts flagged needsReauth (`isSwitchable` is false for them).
    func plan(active: Account, candidates: [Account], _ usage: [UUID: UsageAPIResponse], autoSwitch: Bool = true,
              unswitchable: Set<UUID> = [], strategy: AutoSwitchStrategy = .mostRoom) -> String {
        let dead: Set<UUID> = DeadLoginPolicy.leavesAutomatically(autoSwitchEnabled: autoSwitch, activeIsManualOnly: active.switchThreshold == 0) ? [active.id] : []
        let result = AutoSwitchEngine.planWindowed(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: { !unswitchable.contains($0.id) }, isUnusable: { dead.contains($0.id) },
            activeSampledThisCycle: false,
            rule: { AutoSwitchEngine.WindowRule.both(AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: 90)) },
            defaults: AutoSwitchEngine.WindowPair(session: 90, weekly: 90),
            hysteresisPct: AutoSwitchEngine.hysteresis, watchFable: true, strategy: strategy, asOf: t0)
        guard let result else { return "stay" }
        return "\(result.trigger.rawValue): " + result.targets.map(\.displayName).joined(separator: ",")
    }

    check(plan(active: a, candidates: [b], [b.id: authSample(40, 40)]) == "noSubscription: B",
          "auth engine: a dead live login is left for an account with room, with no reading for the dead one at all")
    check(plan(active: a, candidates: [b], [a.id: authSample(5, 5), b.id: authSample(40, 40)]) == "noSubscription: B",
          "auth engine: left even when an old reading says it has room (its login is dead, whatever it used)")
    let now = t0
    check(!AutoSwitchEngine.cooldownHolds(lastSwitchAt: now.addingTimeInterval(-60), cooldown: 300, activeUnusable: true, asOf: now)
          && AutoSwitchEngine.cooldownHolds(lastSwitchAt: now.addingTimeInterval(-60), cooldown: 300, activeUnusable: false, asOf: now),
          "auth engine: a dead login bypasses the 5-minute cooldown; a live one does not")
    check(plan(active: a, candidates: [b], [b.id: authSample(40, 40)], autoSwitch: false) == "stay",
          "auth engine: with auto-switch off the dead account is not fed to the engine, so nothing moves")
    check(plan(active: manualA, candidates: [b], [b.id: authSample(40, 40)]) == "stay",
          "auth engine: a Manual only dead account is not left by the engine")
    check(plan(active: a, candidates: [b], [b.id: authSample(40, 40)], unswitchable: [b.id]) == "stay",
          "auth engine: an account that needs sign-in is never the target; with no other, stay put")
    check(plan(active: a, candidates: [b, c], [b.id: authSample(10, 10), c.id: authSample(40, 40)], unswitchable: [b.id]) == "noSubscription: C",
          "auth engine: an account that needs sign-in is skipped for the next one")
    check(plan(active: a, candidates: [b, c], [b.id: authSample(0, 0), c.id: authSample(0, 0)], unswitchable: [b.id, c.id]) == "stay",
          "auth engine: dead to dead is never a move")
    check(plan(active: a, candidates: [manualB], [manualB.id: authSample(0, 0)]) == "stay"
          && plan(active: a, candidates: [manualB, c], [manualB.id: authSample(0, 0), c.id: authSample(40, 40)]) == "noSubscription: C",
          "auth engine: a Manual only account is still never a target")
    check(plan(active: a, candidates: [b], [b.id: authSample(95, 95)]) == "stay"
          && plan(active: a, candidates: [b], [b.id: authSample(89.5, 0)]) == "stay",
          "auth engine: no healthy account with room: stay put (the card says sign in; checking goes on)")
    check(plan(active: a, candidates: [b], [:]) == "stay",
          "auth engine: a target with no reading is not eligible: the next cycle takes one")
    let usage: [UUID: UsageAPIResponse] = [b.id: authSample(80, 80), c.id: authSample(20, 20)]
    check(plan(active: a, candidates: [b, c], usage, strategy: .mostRoom) == "noSubscription: C,B"
          && plan(active: a, candidates: [b, c], usage, strategy: .myOrder) == "noSubscription: B,C",
          "auth engine: targets still follow the user's strategy")
    check(AutoSwitchEngine.Trigger.noSubscription.rawValue == "noSubscription",
          "auth engine: the event still names the rule noSubscription (the engine is unchanged; the cause is in the log line)")
}

// MARK: - A target about to expire is renewed first

@MainActor private func renewalTests() {
    typealias Policy = TargetRenewalPolicy
    func expiring(in seconds: TimeInterval?) -> Double? { seconds.map { ms(t0.addingTimeInterval($0)) } }

    check(Policy.renewWithin == 3600, "auth renewal: a target is renewed before the switch when its access token expires within 60 minutes")
    check(Policy.mustRenewBeforeSwitch(expiresAt: expiring(in: 59 * 60 + 59), now: t0)
          && Policy.mustRenewBeforeSwitch(expiresAt: expiring(in: 3600), now: t0),
          "auth renewal: 59 minutes 59 seconds and exactly 60 minutes both count as within")
    check(!Policy.mustRenewBeforeSwitch(expiresAt: expiring(in: 3601), now: t0) && !Policy.mustRenewBeforeSwitch(expiresAt: expiring(in: 5 * 3600), now: t0),
          "auth renewal: more than 60 minutes left is left alone (no new calls)")
    check(Policy.mustRenewBeforeSwitch(expiresAt: expiring(in: -600), now: t0) && Policy.mustRenewBeforeSwitch(expiresAt: expiring(in: 0), now: t0),
          "auth renewal: an expired one is renewed too")
    check(!Policy.mustRenewBeforeSwitch(expiresAt: nil, now: t0), "auth renewal: no expiresAt is not known to be near expiry")

    // What each way a renewal can end means for the switch.
    typealias O = BackupRefreshOutcome
    let table: [(O, Policy.Step, Policy.Step)] = [
        // outcome,                       the access token is expired or refused, it is only near expiry
        (.refreshed("{}"),                .useRenewedLogin,                       .useRenewedLogin),
        (.grantRejected,                  .refuseNotRenewable,                    .refuseNeedsSignIn),
        (.rotationLost,                   .refuseNotRenewable,                    .refuseNeedsSignIn),
        (.belongsToAnotherAccount("o@x"), .refuseBelongsToAnother("o@x"),         .refuseBelongsToAnother("o@x")),
        (.sameGrantAsAnotherLogin,        .refuseNotRenewable,                    .switchWithoutRenewal),
        (.noBackup,                       .refuseNotRenewable,                    .switchWithoutRenewal),
        (.unreachable,                    .switchWithoutRenewal,                  .switchWithoutRenewal),
        (.storeUnavailable,               .switchWithoutRenewal,                  .switchWithoutRenewal),
    ]
    for (outcome, whenDead, whenNear) in table {
        check(Policy.step(after: outcome, loginIsDead: true) == whenDead, "auth renewal: \(outcome) with an expired or refused token -> \(whenDead)")
        check(Policy.step(after: outcome, loginIsDead: false) == whenNear, "auth renewal: \(outcome) with a token only near expiry -> \(whenNear)")
    }
    check(table.allSatisfy { outcome, _, _ in
        let refuses = Policy.step(after: outcome, loginIsDead: false).refusesSwitch
        return refuses == (outcome.requiresReauth)
    }, "auth renewal: a switch to a near-expiry target is refused exactly when the server's verdict says the account must sign in again")
    check(!BackupRefreshOutcome.unreachable.requiresReauth && !BackupRefreshOutcome.storeUnavailable.requiresReauth,
          "auth renewal: no verdict from Anthropic (offline, 5xx, 429) never flags an account or blocks a switch to a near-expiry target")
}

// MARK: - AppState is not in the harness: pin the wiring in its source

private func authSource(_ path: String) -> String {
    (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
}

private func authMember(_ source: String, startingAt signature: String) -> String {
    guard let start = source.range(of: signature) else { return "" }
    let rest = source[start.lowerBound...].dropFirst(signature.count)
    let ends = ["\n    func ", "\n    private func ", "\n    /// ", "\n    @discardableResult", "\n    // MARK", "\n    static func ", "\n    @MainActor"]
        .compactMap { rest.range(of: $0)?.lowerBound }
    return String(source[start.lowerBound..<(ends.min() ?? rest.endIndex)])
}

private func authComesBefore(_ first: String, _ second: String, in text: String) -> Bool {
    guard let a = text.range(of: first) else { return false }
    guard let b = text.range(of: second) else { return true }
    return a.lowerBound < b.lowerBound
}

@MainActor private func wiringPins() {
    let app = authSource("PixelSwitch/AppState.swift")
    let claude = authSource("PixelSwitch/Services/ClaudeService.swift")
    let engine = authSource("PixelSwitch/Services/AutoSwitchEngine.swift")
    check(!app.isEmpty && !claude.isEmpty && !engine.isEmpty, "auth pins: the source files are found from the repo root")

    let evaluate = authMember(app, startingAt: "private func evaluateAutoSwitch() async {")
    check(evaluate.contains("isUnusable: { [unowned self] in self.isUnusable($0) }")
          && evaluate.contains("let activeUnusable = isUnusable(active)")
          && evaluate.contains("activeUnusable: activeUnusable"),
          "auth pins: auto-switch feeds the engine one isUnusable (no subscription OR dead login), and the cooldown bypass uses the same answer")
    let unusable = authMember(app, startingAt: "private func isUnusable(_ account: Account) -> Bool {")
    check(unusable.contains("hasNoSubscription(account)") && unusable.contains("leavesDeadLogin(account)"),
          "auth pins: unusable means no subscription or a dead live login the policy lets auto-switch leave")
    let leaves = authMember(app, startingAt: "private func leavesDeadLogin(_ account: Account) -> Bool {")
    check(leaves.contains("DeadLoginPolicy.leavesAutomatically(") && leaves.contains("liveLoginWatch.confirmedDeadAccount == account.id")
          && leaves.contains("windowRule(for: account).manualOnly") && leaves.contains("autoSwitchEnabled"),
          "auth pins: a dead login is left only for the account the watch confirmed, under auto-switch, and never for a Manual only one")

    let tick = authMember(app, startingAt: "private func authWatchTick() async {")
    check(!tick.isEmpty && tick.contains("guard !isAuthWatchTicking, !isSwitching, !isLoggingIn")
          && tick.contains("isAuthWatchTicking = true") && tick.contains("defer { isAuthWatchTicking = false }"),
          "auth pins: the watch cannot overlap itself, a switch or a sign-in")
    check(tick.contains("keychain.readClaudeTokenQuietly()") && tick.contains("AuthHealth.shape(ofCredential:")
          && tick.contains("liveLoginWatch.observe(") && authComesBefore("AuthHealth.shape(ofCredential:", "claudeService.probeAuthStatus()", in: tick),
          "auth pins: the watch reads the credential quietly, judges its shape, and only then may spawn the CLI")
    check(!tick.contains("refreshBackupInPlace") && !tick.contains("refreshOAuthCredentials") && !tick.contains("writeClaudeToken")
          && !tick.contains("performSwitch") && !tick.contains("switchTo("),
          "auth pins: the watch never renews, rewrites or switches the live login itself")
    check(tick.contains("guard !isSwitching, !isLoggingIn, activeAccount?.id == active.id"),
          "auth pins: after the CLI answers, the watch drops its read if a switch or sign-in began meanwhile")
    let startRefresh = authMember(app, startingAt: "func startAutoRefresh(interval: TimeInterval = 300) {")
    let stopRefresh = authMember(app, startingAt: "func stopAutoRefresh() {")
    check(startRefresh.contains("startAuthWatch()") && stopRefresh.contains("stopAuthWatch()"),
          "auth pins: the watch starts and stops with the refresh timer")
    let startWatch = authMember(app, startingAt: "private func startAuthWatch() {")
    check(startWatch.contains("withTimeInterval: AuthHealth.watchInterval"), "auth pins: the watch runs on AuthHealth.watchInterval")

    for signature in ["private func requestUsage(", "private func recordUsageFailure(", "private func readUsage(for account: Account) async -> UsageAPIResponse? {"] {
        let body = authMember(app, startingAt: signature)
        check(!body.isEmpty && !body.contains("liveLoginWatch"),
              "auth pins: \(signature.dropFirst(13).prefix(16)) (a usage 401, 429, 5xx or no network) never touches the live-login watch")
    }

    let validated = authMember(app, startingAt: "private func validatedTargetBackup(for account: Account) async -> Result<AccountBackup, SwitchProblem> {")
    check(validated.contains("TargetRenewalPolicy.mustRenewBeforeSwitch(") && validated.contains("TargetRenewalPolicy.step(after:")
          && authComesBefore("LoginGate.targetProblem(", "TargetRenewalPolicy.mustRenewBeforeSwitch(", in: validated),
          "auth pins: validatedTargetBackup renews a target that expires within 60 minutes, through the existing renew path, after the usable-login check")
    check(!validated.contains("claudeService.refreshOAuthCredentials"), "auth pins: and never calls the token endpoint itself")

    let perform = authMember(app, startingAt: "func performSwitch(to account: Account) async throws {")
    check(perform.contains("settleLeftLiveLogin(") && authComesBefore("noteCredentialChanged(for: account)", "settleLeftLiveLogin(", in: perform)
          && authComesBefore("settleLeftLiveLogin(", "await refresh()", in: perform),
          "auth pins: after a switch off a dead login, the account just left gets the server's verdict before the refresh")
    let settle = authMember(app, startingAt: "private func settleLeftLiveLogin(")
    check(settle.contains("refreshBackupInPlace(for:") && !settle.contains("keychain.readClaudeToken()"),
          "auth pins: the verdict is the saved login's renewal through the existing path; the live login is not touched")

    check(evaluate.contains("!isSwitchable(target)") && evaluate.contains("trying next"),
          "auth pins: when a switch fails because the target needs sign-in, auto-switch moves on to the next candidate")

    // The engine, the conformance vectors and the Python port are not touched by this work.
    check(!engine.contains("needsSignIn") && !engine.contains("deadLogin") && !engine.contains("AuthHealth"),
          "auth pins: AutoSwitchEngine.swift knows nothing of this (the 150 conformance vectors stay frozen)")
    check(claude.contains("func probeAuthStatus() async -> AuthHealth.CLIProbe"), "auth pins: ClaudeService maps the CLI's answer to a CLIProbe")
}
