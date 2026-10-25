// Fix lane FA, after the Opus review of lane MA: the removal rules (spec D20),
// the "already live" shortcut, the live-login backup and re-read decisions
// (spec 5.3), and the staying-put wording (spec 2.3 rule 10). Each of these is a
// pure function or a loop over closures, so the harness runs the logic the app
// calls without the app. Uses the global `check`.
import Foundation

@MainActor func runRemovalAndBackupTests() {
    removalPlanTests()
    removalFallbackTests()
    shortcutTests()
    liveBackupResultTests()
    rereadDecisionTests()
    stayingPutTests()
}

private func person(_ name: String, switchThreshold: Double? = nil, priority: Int? = nil) -> Account {
    Account(email: "\(name)@x.com", displayName: name, switchThreshold: switchThreshold, priority: priority)
}

private func goodLogin(_ access: String = "A1", refresh: String = "R1") -> String {
    #"{"claudeAiOauth":{"accessToken":"\#(access)","refreshToken":"\#(refresh)","expiresAt":1790000000000}}"#
}

private let blankedLogin = #"{"claudeAiOauth":{"accessToken":"","refreshToken":"","expiresAt":0},"mcpOAuth":{"s":{"x":1}}}"#

// MARK: - D20: removing accounts

@MainActor private func removalPlanTests() {
    let b = person("b"), c = person("c", priority: 1), m = person("m", switchThreshold: 0)

    let lastActive = AccountRemoval.plan(removedWasActive: true, remaining: [])
    let lastNotActive = AccountRemoval.plan(removedWasActive: false, remaining: [])
    check(lastActive == lastNotActive && lastActive.clearActive && lastActive.markRemovedLast && lastActive.fallbackIds.isEmpty,
          "remove: the last account sets the removed-last flag and clears the active account whether or not it was active")

    let activeRemoved = AccountRemoval.plan(removedWasActive: true, remaining: [b, c, m])
    check(!activeRemoved.clearActive && !activeRemoved.markRemovedLast && activeRemoved.fallbackIds == AccountOrder.fallbackOrder(from: [b, c, m]).map(\.id),
          "remove: removing the active account (others remain) starts a fallback in AccountOrder.fallbackOrder")
    check(activeRemoved.fallbackIds.first == AccountOrder.fallback(from: [b, c, m])?.id && activeRemoved.fallbackIds.last == m.id,
          "remove: the fallback tries accounts that are not Manual only first, Manual only last")

    let inactiveRemoved = AccountRemoval.plan(removedWasActive: false, remaining: [b, c])
    check(inactiveRemoved == AccountRemoval.Plan(clearActive: false, markRemovedLast: false, fallbackIds: []),
          "remove: removing an account that is not active, with others left, changes nothing else")

    check(!AccountRemoval.mayCreateFromLiveLogin(accountsAreEmpty: true, removedLastAccount: lastNotActive.markRemovedLast),
          "remove: after removing a last account that was not active, a refresh does not recreate it from the live login")
    check(AccountRemoval.mayCreateFromLiveLogin(accountsAreEmpty: true, removedLastAccount: false)
          && !AccountRemoval.mayCreateFromLiveLogin(accountsAreEmpty: false, removedLastAccount: false)
          && !AccountRemoval.mayCreateFromLiveLogin(accountsAreEmpty: false, removedLastAccount: true),
          "remove: the first account is still made from the live login on a fresh install, and only then")
}

@MainActor private func removalFallbackTests() {
    typealias Fallback = RemovalFallback
    let one = UUID(), two = UUID(), three = UUID()

    /// Drives the loop with scripted answers, recording what it did.
    @MainActor final class Script {
        var answers: [UUID: [Fallback.Attempt]]
        var tried: [UUID] = []
        var notes: [String] = []
        var pauses = 0
        var needed = true
        /// Runs after each attempt (to model something else changing state mid-loop).
        var afterAttempt: ((UUID) -> Void)?
        var afterPause: (() -> Void)?
        init(_ answers: [UUID: [Fallback.Attempt]]) { self.answers = answers }

        func run(candidates: [UUID], maxBusyWaits: Int = Fallback.defaultMaxBusyWaits) -> Fallback.Result? {
            ControlTestKit.wait { [self] in
                await Fallback.run(
                    candidates: candidates, maxBusyWaits: maxBusyWaits,
                    stillNeeded: { self.needed },
                    attempt: { id in
                        self.tried.append(id)
                        var queue = self.answers[id] ?? [.gone]
                        let answer = queue.isEmpty ? Fallback.Attempt.gone : queue.removeFirst()
                        self.answers[id] = queue
                        self.afterAttempt?(id)
                        return answer
                    },
                    pause: { self.pauses += 1; self.afterPause?() },
                    note: { self.notes.append($0) })
            }
        }
    }

    // The first candidate works.
    var script = Script([one: [.switched]])
    check(script.run(candidates: [one, two]) == .switched(one) && script.tried == [one],
          "fallback: the first candidate that works takes over and the rest are not tried")

    // A failure is logged with its reason and the next candidate is tried.
    script = Script([one: [.failed(message: "The saved login for one has expired.")], two: [.switched]])
    check(script.run(candidates: [one, two]) == .switched(two) && script.tried == [one, two]
          && script.notes.contains { $0.contains("The saved login for one has expired.") },
          "fallback: a failed candidate is logged with its reason and the next one is tried")

    // Nothing works: the last failure is reported for the user, every reason is in the log.
    script = Script([one: [.failed(message: "first reason")], two: [.failed(message: "second reason")], three: [.failed(message: nil)]])
    let none = script.run(candidates: [one, two, three])
    check(none == .noneWorked(lastFailure: "second reason") && script.tried == [one, two, three]
          && script.notes.contains { $0.contains("first reason") } && script.notes.contains { $0.contains("second reason") }
          && script.notes.contains { $0.contains("no reason given") },
          "fallback: when none works the last sentence is returned for the user and every failure is in the log")

    // Candidates that are gone are skipped, not counted as failures.
    script = Script([one: [.gone], two: [.gone]])
    check(script.run(candidates: [one, two]) == .noneWorked(lastFailure: nil),
          "fallback: candidates that left the list are skipped and leave no failure sentence")

    // The situation changes during the loop (a sign-in or another switch made an account active).
    script = Script([one: [.failed(message: "x")], two: [.switched]])
    script.afterAttempt = { [script] _ in script.needed = false }
    check(script.run(candidates: [one, two]) == .stoodDown && script.tried == [one],
          "fallback: the loop re-checks before every candidate and stops once another account is active")

    // It is also re-checked before the first try.
    script = Script([one: [.switched]])
    script.needed = false
    check(script.run(candidates: [one]) == .stoodDown && script.tried.isEmpty && script.pauses == 0,
          "fallback: if the removed account is no longer the active one at the start, nothing is tried")

    // A busy switch is waited out.
    script = Script([one: [.busy(message: "busy"), .busy(message: "busy"), .switched]])
    check(script.run(candidates: [one, two]) == .switched(one) && script.pauses == 2 && script.tried == [one, one, one],
          "fallback: a busy switch or sign-in is waited out and the same candidate retried")

    // A sign-in that finishes inside the wait stops the loop before it undoes the sign-in.
    script = Script([one: [.busy(message: "busy"), .switched], two: [.switched]])
    script.afterPause = { [script] in script.needed = false }
    check(script.run(candidates: [one, two]) == .stoodDown && script.tried == [one],
          "fallback: an account made active while a busy switch is waited out is never switched away from")

    // Busy for longer than the whole wait: nothing was tried, so it is not a failure.
    script = Script([one: Array(repeating: .busy(message: "A switch or sign-in is in progress."), count: 20), two: [.switched]])
    let gaveUp = script.run(candidates: [one, two], maxBusyWaits: 3)
    check(gaveUp == .busyGaveUp(message: "A switch or sign-in is in progress.") && script.pauses == 3 && script.tried == [one, one, one, one],
          "fallback: busy for the whole wait gives up once, without moving on to the next candidate or reporting a failure")
}

// MARK: - The "already live" shortcut

@MainActor private func shortcutTests() {
    let a = UUID(), b = UUID()
    check(SwitchShortcut.alreadyLive(liveOwnerId: a, believedActiveId: b, targetId: a), "shortcut: the proven owner of the live login is already live")
    check(!SwitchShortcut.alreadyLive(liveOwnerId: a, believedActiveId: b, targetId: b),
          "shortcut: a proven owner other than the target overrides what is believed active")
    check(SwitchShortcut.alreadyLive(liveOwnerId: nil, believedActiveId: b, targetId: b), "shortcut: an unproven login counts as the believed active account's")
    check(!SwitchShortcut.alreadyLive(liveOwnerId: nil, believedActiveId: nil, targetId: b),
          "shortcut: with no active account and no proven owner nothing is already live, so the switch goes ahead")
    check(!SwitchShortcut.alreadyLive(liveOwnerId: nil, believedActiveId: a, targetId: b), "shortcut: an unproven login and a different target switches")
}

// MARK: - Step 1: what "saved" means

@MainActor private func liveBackupResultTests() {
    typealias Backup = LiveLoginBackup
    func result(_ fresh: String?, owner: Bool = true, proven: Bool = true, identity: Bool = true, store: Bool = true) -> Backup.Result {
        Backup.result(fresh: fresh, ownerKnown: owner, proven: proven, identityMatches: identity, storeAccepted: store)
    }
    check(result(goodLogin()) == .saved, "backup: a usable proven login whose identity matches and that the store took is saved")
    check(result(nil) == .notSaved(.unreadable) && !result(nil).isSettled, "backup: a read that returned nothing is NOT saved and is not settled")
    var neverSaved = true
    for owner in [true, false] { for proven in [true, false] { for identity in [true, false] { for store in [true, false] {
        let r = result(nil, owner: owner, proven: proven, identity: identity, store: store)
        if r.isSettled { neverSaved = false }
    } } } }
    check(neverSaved, "backup: a nil read is never saved or settled, whatever else is true")
    check(result("not json at all") == .notSaved(.unreadable) && result("[]") == .notSaved(.unreadable) && result("") == .notSaved(.unreadable),
          "backup: text that is not a credential object is a read it could not use, not 'nothing to save'")
    check(result(blankedLogin) == .nothingWorthSaving && result(blankedLogin).isSettled,
          "backup: a present credential holding a blanked login is nothing worth saving (spec D12)")
    check(result(#"{"mcpOAuth":{"s":1}}"#) == .nothingWorthSaving, "backup: a credential with no claudeAiOauth is nothing worth saving")
    check(result(goodLogin(), owner: false) == .notSaved(.noOwner), "backup: no proven owner means nothing is saved")
    check(result(goodLogin(), proven: false) == .notSaved(.unproven), "backup: an unproven login is not saved, and the reason is 'unproven'")
    check(result(goodLogin(), identity: false) == .notSaved(.identityMismatch), "backup: no matching identity details is its own reason")
    check(result(goodLogin(), store: false) == .notSaved(.storeRefused), "backup: a store that refused the write is its own reason")
    check(CredentialOwnership.isCredentialObject(blankedLogin) && !CredentialOwnership.isCredentialObject(nil) && !CredentialOwnership.isCredentialObject("{"),
          "backup: only text that parses as a JSON object is a credential object")
}

// MARK: - Re-read before write

@MainActor private func rereadDecisionTests() {
    typealias Backup = LiveLoginBackup
    let a1 = goodLogin("A1", refresh: "R1"), a2 = goodLogin("A2", refresh: "R2"), a3 = goodLogin("A3", refresh: "R3")

    // First pass.
    check(Backup.afterFirstPass(seen: a1, liveNow: a1, ownerKnown: true) == .proceed, "reread: an unchanged login goes ahead")
    check(Backup.afterFirstPass(seen: a1, liveNow: a2, ownerKnown: true) == .retry, "reread: a renewed login is backed up again")
    check(Backup.afterFirstPass(seen: nil, liveNow: nil, ownerKnown: true) == .abort(.unreadable),
          "reread: a login proven a moment ago that now reads as nothing, twice, stops with nothing written")
    check(Backup.afterFirstPass(seen: nil, liveNow: a1, ownerKnown: true) == .abort(.unreadable),
          "reread: a login that could not be read for step 1 is not written over, even if it can be read now")
    check(Backup.afterFirstPass(seen: nil, liveNow: nil, ownerKnown: false) == .proceed,
          "reread: with no login on this Mac at all (no proven owner) the switch is not refused")
    check(Backup.afterFirstPass(seen: nil, liveNow: a1, ownerKnown: false) == .retry, "reread: a login that appeared since step 1 is looked at again")

    // The review's P1: the retry reads nothing twice.
    check(Backup.afterRetry(firstSeen: a1, seen: nil, result: .notSaved(.unreadable), liveNow: nil, ownerKnown: true) == .unreadable,
          "reread: a retry that reads nothing twice, after a first pass that saw a login, stops with nothing written")
    check(Backup.afterRetry(firstSeen: a1, seen: nil, result: .notSaved(.unreadable), liveNow: nil, ownerKnown: false) == .unreadable,
          "reread: and so does a retry with no proven owner (the login vanished or the Keychain flaked)")
    check(Backup.afterRetry(firstSeen: nil, seen: nil, result: .notSaved(.unreadable), liveNow: nil, ownerKnown: false) == nil,
          "reread: a login that was never there is not a reason to refuse")

    // The two causes the old combined check hid.
    check(Backup.afterRetry(firstSeen: a1, seen: a2, result: .saved, liveNow: a3, ownerKnown: true) == .stillChanging,
          "reread: a login still changing is 'still changing'")
    check(Backup.afterRetry(firstSeen: a1, seen: a2, result: .notSaved(.unproven), liveNow: a2, ownerKnown: true) == .notSaved(.unproven),
          "reread: a renewed login that could not be saved is a different abort, with the reason")
    check(Backup.afterRetry(firstSeen: a1, seen: a2, result: .notSaved(.storeRefused), liveNow: a2, ownerKnown: true) == .notSaved(.storeRefused)
          && Backup.afterRetry(firstSeen: a1, seen: a2, result: .notSaved(.identityMismatch), liveNow: a2, ownerKnown: true) == .notSaved(.identityMismatch),
          "reread: the reason (proof, identity or store) comes through")
    check(Backup.afterRetry(firstSeen: a1, seen: a2, result: .saved, liveNow: a2, ownerKnown: true) == nil,
          "reread: a renewed login that was saved and holds still goes ahead")
    check(Backup.afterRetry(firstSeen: a1, seen: a2, result: .notSaved(.noOwner), liveNow: a2, ownerKnown: false) == nil,
          "reread: with no proven owner an unsaved login does not stop the switch (nothing could be saved for anyone)")
    check(Backup.afterRetry(firstSeen: a1, seen: blankedLogin, result: .nothingWorthSaving, liveNow: blankedLogin, ownerKnown: true) == nil,
          "reread: a login Claude Code blanked meanwhile is nothing to save and goes ahead (spec D12)")
}

// MARK: - Rule 10: the staying-put sentence

@MainActor private func stayingPutTests() {
    let reset = ISO8601DateFormatter().string(from: Date().addingTimeInterval(48 * 3600))
    func usage(_ session: Double, _ weekly: Double, fable: Double? = nil) -> UsageAPIResponse {
        var json = #"{"five_hour":{"utilization":\#(session),"resets_at":"\#(reset)"},"seven_day":{"utilization":\#(weekly),"resets_at":"\#(reset)"}"#
        if let fable {
            json += #","limits":[{"kind":"weekly_scoped","group":"weekly","percent":\#(fable),"resets_at":"\#(reset)","scope":{"model":{"id":null,"display_name":"Fable"}}}]"#
        }
        return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data((json + "}").utf8))
    }
    func kind(_ u: UsageAPIResponse, rule: AutoSwitchEngine.WindowRule, defaults: AutoSwitchEngine.WindowPair, fable: Bool = true) -> AutoSwitchEngine.StayingPut {
        AutoSwitchEngine.stayingPutKind(activeUsage: u, activeRule: rule, defaults: defaults, activeSampledThisCycle: true, watchFable: fable)
    }
    let equal = AutoSwitchEngine.WindowPair(both: 98)
    let equalRule = AutoSwitchEngine.WindowRule.both(98)

    // The Beeco Mac (98 / 98): 1.9's sentence whenever a window fired.
    check(kind(usage(100, 90), rule: equalRule, defaults: equal) == .noRoom,
          "staying put: with 98 / 98 and the session window past its limit, 1.9's sentence is used (nobody has room)")
    check(kind(usage(10, 99), rule: equalRule, defaults: equal) == .noRoom, "staying put: with 98 / 98 and the weekly window at its limit, 1.9's sentence is used")
    check(kind(usage(50, 50), rule: equalRule, defaults: equal) == .notAtLimit, "staying put: under both limits there is nothing to say")

    // The founder's split (90 session / 50 weekly).
    let split = AutoSwitchEngine.WindowPair(session: 90, weekly: 50)
    let splitRule = AutoSwitchEngine.WindowRule(session: 90, weekly: 50, manualOnly: false)
    check(kind(usage(95, 60), rule: splitRule, defaults: split) == .noRoom,
          "staying put: with 90 / 50, session 95 and weekly 60, the account is past its session limit, so the overflow sentence would be false")
    check(kind(usage(60, 60), rule: splitRule, defaults: split) == .waitingForSessionLimit,
          "staying put: with 90 / 50 and weekly 60 but session 60, it stays until the session limit and says so")
    check(kind(usage(10, 55), rule: splitRule, defaults: split) == .waitingForSessionLimit,
          "staying put: weekly over its limit and session far under it is the overflow case")
    check(kind(usage(30, 30), rule: splitRule, defaults: split) == .notAtLimit, "staying put: under both split limits there is nothing to say")

    // Fable alone never makes the overflow sentence apply (rule 9).
    check(kind(usage(10, 10, fable: 60), rule: splitRule, defaults: split) == .noRoom,
          "staying put: a Fable-only trigger is the plain sentence")
    check(kind(usage(10, 10, fable: 60), rule: splitRule, defaults: split, fable: false) == .notAtLimit,
          "staying put: with Fable watching off a Fable reading says nothing")

    // Manual only on the active account leaves at the defaults.
    check(kind(usage(95, 60), rule: .manualOnlyRule, defaults: split) == .noRoom && kind(usage(60, 60), rule: .manualOnlyRule, defaults: split) == .waitingForSessionLimit,
          "staying put: a Manual only active account is judged at the defaults")
}

// MARK: - Source pins: the app calls the rules the harness tests
//
// KeychainService, ClaudeService and AppState need the Keychain, the network and
// SwiftUI, so the harness cannot compile them. What it can do is read their source
// and pin that each one still routes its decision through the pure function tested
// above, before it does anything irreversible. Deleting a call turns a check red.
// These pins read text; they cannot prove the call is reached at run time.

/// The source of `path` (repo-relative, the harness runs from the repo root).
private func sourceText(_ path: String) -> String {
    (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
}

/// The text of one member: from `signature` to the next member at the same
/// indentation (a `func`, a doc comment, an attribute, a mark), or the end.
private func member(_ source: String, startingAt signature: String) -> String {
    guard let start = source.range(of: signature) else { return "" }
    let rest = source[start.lowerBound...].dropFirst(signature.count)
    let ends = ["\n    func ", "\n    private func ", "\n    /// ", "\n    @discardableResult", "\n    // MARK", "\n    static func ", "\n    @MainActor"]
        .compactMap { rest.range(of: $0)?.lowerBound }
    let end = ends.min() ?? rest.endIndex
    return String(source[start.lowerBound..<end])
}

/// True when `first` appears in `text` before `second` (and `first` appears).
private func comesBefore(_ first: String, _ second: String, in text: String) -> Bool {
    guard let a = text.range(of: first) else { return false }
    guard let b = text.range(of: second) else { return true }
    return a.lowerBound < b.lowerBound
}

@MainActor func runSourcePinTests() {
    let keychain = sourceText("PixelSwitch/Services/KeychainService.swift")
    let claude = sourceText("PixelSwitch/Services/ClaudeService.swift")
    let app = sourceText("PixelSwitch/AppState.swift")
    check(!keychain.isEmpty && !claude.isEmpty && !app.isEmpty, "pins: the three source files are found from the repo root")

    // D12 (a): the one store-save function refuses an unusable login before it touches the store.
    let save = member(keychain, startingAt: "func saveAccountBackup(")
    check(save.contains("LoginGate.save(token: token)") && comesBefore("LoginGate.save(token:", "storeLock.lock()", in: save)
          && comesBefore("LoginGate.save(token:", "saveBackupStore(", in: save),
          "pins D12(a): KeychainService.saveAccountBackup asks LoginGate.save before it locks or writes the store")

    // D12 (b): the switch refuses an unusable target before it writes Claude Code's live store.
    let switchAccount = member(claude, startingAt: "func switchAccount(")
    check(comesBefore("LoginGate.targetProblem(backupToken: targetBackup.token)", "keychain.writeClaudeToken(", in: switchAccount)
          && comesBefore("LoginGate.targetProblem(backupToken: targetBackup.token)", "backUpLiveLogin()", in: switchAccount),
          "pins D12(b): ClaudeService.switchAccount asks LoginGate.targetProblem before it backs up or writes anything")
    let validated = member(app, startingAt: "private func validatedTargetBackup(")
    check(validated.contains("LoginGate.targetProblem(backupToken: backup.token)")
          && comesBefore("LoginGate.targetProblem(", "requestGuard.decision(", in: validated),
          "pins D12(b): AppState.validatedTargetBackup fails an unusable saved login with the re-sign message before any network call")

    // D12 (c): switchable.
    let switchable = member(app, startingAt: "func isSwitchable(_ account: Account) -> Bool {")
    check(switchable.contains("LoginGate.isSwitchable(") && switchable.contains("needsReauth: requestGuard.isNeedsReauth")
          && switchable.contains("cardSaysLoginExpired:"),
          "pins D12(c): AppState.isSwitchable is LoginGate.isSwitchable over the backup, the guard and the card")
    let evaluate = member(app, startingAt: "private func evaluateAutoSwitch() async {")
    check(evaluate.contains("isSwitchable: { [unowned self] in self.isSwitchable($0) }"),
          "pins D12(c): auto-switch ranks only accounts AppState.isSwitchable accepts")

    // The re-read before the write and step 1 go through LiveLoginBackup.
    check(switchAccount.contains("LiveLoginBackup.result(") && switchAccount.contains("LiveLoginBackup.afterFirstPass(")
          && switchAccount.contains("LiveLoginBackup.afterRetry(") && comesBefore("LiveLoginBackup.afterRetry(", "keychain.writeClaudeToken(", in: switchAccount)
          && !switchAccount.contains("saved || !CredentialOwnership.isUsableLogin"),
          "pins 5.3: the switch decides step 1, the re-read and the retry with LiveLoginBackup before it writes, and the old 'saved || not usable' shortcut is gone")

    // Removal and the shortcut.
    let remove = member(app, startingAt: "func removeAccount(_ account: Account) {")
    check(remove.contains("AccountRemoval.plan(removedWasActive: wasActive")
          && remove.contains("if plan.clearActive") && remove.contains("removedLastAccountKey"),
          "pins D20: removeAccount takes its decisions from AccountRemoval.plan and sets the removed-last flag for the last account whether or not it was active")
    let after = member(app, startingAt: "private func switchAfterRemoval(")
    check(after.contains("RemovalFallback.run(") && after.contains("self.activeAccount?.id == removed.id") && after.contains("errorMessage = lastFailure"),
          "pins D20: the fallback is RemovalFallback.run, stops when the removed account is no longer the active one, and shows the last failure")
    let perform = member(app, startingAt: "func performSwitch(to account: Account) async throws {")
    check(perform.contains("SwitchShortcut.alreadyLive(") && perform.contains("guard !accounts.isEmpty") && !perform.contains("guard let currentActive = activeAccount"),
          "pins D20: performSwitch no longer refuses just because no account is active")
    let update = member(app, startingAt: "private func updateActiveAccount(from status: AuthStatus) {")
    check(update.contains("AccountRemoval.mayCreateFromLiveLogin("), "pins D20: a refresh decides whether to create the first account with AccountRemoval.mayCreateFromLiveLogin")
    let staying = member(app, startingAt: "private func noteStayingPut(")
    check(staying.contains("AutoSwitchEngine.stayingPutKind(") && staying.contains("kind == .waitingForSessionLimit"),
          "pins rule 10: noteStayingPut chooses its sentence with AutoSwitchEngine.stayingPutKind")
}
