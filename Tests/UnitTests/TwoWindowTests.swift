// 2.0 lane MA: the split of the single threshold into session and weekly
// limits (spec 2.1, 2.2, 2.3), priority numbers (spec 3, D2, D3) and the
// settings readers and writers behind them. The shared conformance vectors are
// in ConformanceTests.swift; these pin what a vector cannot (stored values,
// settings keys, the pure edits AppState applies). Uses the global `check`.
import Foundation

@MainActor func runTwoWindowTests() {
    presenceReaderTests()
    resolutionTests()
    splitRuleTests()
    legacyWriteTests()
    defaultWriterTests()
    accountModelTests()
    priorityTests()
    removalOrderTests()
    equivalenceTests()
}

private func windowAccount(_ name: String, switchThreshold: Double? = nil, session: Double? = nil, weekly: Double? = nil,
                           priority: Int? = nil, setAt: Double? = nil) -> Account {
    Account(email: "\(name)@x.com", displayName: name, switchThreshold: switchThreshold,
            sessionThreshold: session, weeklyThreshold: weekly, priority: priority,
            prioritySetAt: setAt.map { Date(timeIntervalSince1970: $0) })
}

/// A throwaway UserDefaults domain, removed afterwards.
private func withScratchDefaults(_ body: (UserDefaults) -> Void) {
    let name = "pixelswitch.unit-tests.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: name)!
    defer { defaults.removePersistentDomain(forName: name) }
    body(defaults)
}

// MARK: - Presence-based readers

@MainActor private func presenceReaderTests() {
    let sessionKey = AutoSwitchSettings.sessionThresholdKey, weeklyKey = AutoSwitchSettings.weeklyThresholdKey
    check(sessionKey == "autoSwitchSessionThreshold" && weeklyKey == "autoSwitchWeeklyThreshold",
          "windows: the two new settings keys are the ones the spec fixes")

    withScratchDefaults { defaults in
        check(AutoSwitchSettings.storedThreshold(forKey: sessionKey, in: defaults) == nil, "windows: an absent window key is not set")
        defaults.set(0.0, forKey: sessionKey)
        check(AutoSwitchSettings.storedThreshold(forKey: sessionKey, in: defaults) == nil, "windows: a window key of 0 is not set")
        defaults.set(Double.nan, forKey: sessionKey)
        check(AutoSwitchSettings.storedThreshold(forKey: sessionKey, in: defaults) == nil, "windows: a window key that is not finite is not set")
        defaults.set("ninety", forKey: sessionKey)
        check(AutoSwitchSettings.storedThreshold(forKey: sessionKey, in: defaults) == nil, "windows: a window key that is not a number is not set")
        defaults.set(95, forKey: sessionKey)
        check(AutoSwitchSettings.storedThreshold(forKey: sessionKey, in: defaults) == 95, "windows: a whole number stored as an integer reads too")
        defaults.set(60.0, forKey: weeklyKey)
        check(AutoSwitchSettings.storedThreshold(forKey: weeklyKey, in: defaults) == 60, "windows: a set window key reads back")
    }

    // The end-to-end case from spec 2.1: only autoSwitchThreshold = 98 stored.
    let defaults = UserDefaults.standard
    let keys = [AutoSwitchSettings.thresholdKey, sessionKey, weeklyKey]
    let saved = keys.map { defaults.object(forKey: $0) }
    defer {
        for (key, value) in zip(keys, saved) {
            if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
        }
    }
    keys.forEach { defaults.removeObject(forKey: $0) }
    check(AutoSwitchSettings.defaultSessionThreshold == 90 && AutoSwitchSettings.defaultWeeklyThreshold == 90,
          "windows: nothing stored reads 90 and 90")
    defaults.set(98.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultSessionThreshold == 98 && AutoSwitchSettings.defaultWeeklyThreshold == 98
          && AutoSwitchSettings.defaultPair == AutoSwitchEngine.WindowPair(session: 98, weekly: 98),
          "windows: with only autoSwitchThreshold = 98 stored, both effective defaults read 98")
    check(defaults.object(forKey: sessionKey) == nil && defaults.object(forKey: weeklyKey) == nil,
          "windows: reading the defaults writes nothing")
    defaults.set(90.0, forKey: sessionKey)
    defaults.set(50.0, forKey: weeklyKey)
    check(AutoSwitchSettings.defaultPair == AutoSwitchEngine.WindowPair(session: 90, weekly: 50),
          "windows: the founder's example, 90 session and 50 weekly, reads back")
    defaults.set(0.0, forKey: weeklyKey)
    check(AutoSwitchSettings.defaultWeeklyThreshold == 98, "windows: a weekly key of 0 follows the base again")
}

// MARK: - Spec 2.2: resolution

@MainActor private func resolutionTests() {
    let d = AutoSwitchEngine.WindowPair(session: 90, weekly: 80)
    func rule(_ a: Account) -> AutoSwitchEngine.WindowRule { AutoSwitchSettings.rule(for: a, defaults: d) }
    check(rule(windowAccount("a")) == AutoSwitchEngine.WindowRule(session: 90, weekly: 80, manualOnly: false),
          "rule: an account with nothing of its own follows the global window defaults")
    check(rule(windowAccount("a", switchThreshold: 70)) == AutoSwitchEngine.WindowRule(session: 70, weekly: 70, manualOnly: false),
          "rule: a both-windows value beats the global defaults")
    check(rule(windowAccount("a", switchThreshold: 70, session: 40)) == AutoSwitchEngine.WindowRule(session: 40, weekly: 70, manualOnly: false),
          "rule: a window value beats the both-windows value, the other window keeps it")
    check(rule(windowAccount("a", session: 40)) == AutoSwitchEngine.WindowRule(session: 40, weekly: 80, manualOnly: false),
          "rule: a window value alone leaves the other on its global default")
    check(rule(windowAccount("a", switchThreshold: 0, session: 40, weekly: 40)) == .manualOnlyRule,
          "rule: Manual only wins over window values")
    check(rule(windowAccount("a", switchThreshold: -5)).manualOnly && rule(windowAccount("a", switchThreshold: 0.4)).manualOnly,
          "rule: a both-windows value that rounds to 0 or below is Manual only")
    check(rule(windowAccount("a", session: 0, weekly: -5)) == AutoSwitchEngine.WindowRule(session: 90, weekly: 80, manualOnly: false),
          "rule: window values of 0 and below follow the next layer")
    check(rule(windowAccount("a", session: 100.4)).session == 100 && rule(windowAccount("a", session: 0.5)).session == 1,
          "rule: window values round half away from zero and stop at 100")
    let leave = AutoSwitchSettings.leaveAtPair(rule: .manualOnlyRule, defaults: d)
    check(leave == d, "rule: a Manual only account is left at the defaults, per window")
    check(AutoSwitchSettings.windowDefaults(base: 98, session: nil, weekly: nil) == AutoSwitchEngine.WindowPair(both: 98),
          "defaults: only the base stored gives 98 and 98")
    check(AutoSwitchSettings.windowDefaults(base: 30, session: 120, weekly: 10) == AutoSwitchEngine.WindowPair(session: 100, weekly: 50),
          "defaults: every default is kept in 50–100")
    check(AutoSwitchSettings.windowDefaults(base: nil, session: .nan, weekly: 0) == AutoSwitchEngine.WindowPair(both: 90),
          "defaults: a window default that is 0 or not a number follows the base (90)")

    let explained = AutoSwitchSettings.windowExplanation(rule: .init(session: 90, weekly: 50, manualOnly: false), defaults: d)
    check(explained?.leaveAt == AutoSwitchEngine.WindowPair(session: 90, weekly: 50)
          && explained?.arriveAt == AutoSwitchEngine.WindowPair(session: 89, weekly: 49)
          && explained?.relaxedArriveWeekly == 89,
          "explanation: 90 session and 50 weekly leave at 90 / 50, arrive at 89 / 49, and up to 89 weekly when nobody under its weekly limit can take you")
    check(AutoSwitchSettings.windowExplanation(rule: .manualOnlyRule, defaults: d) == nil, "explanation: none for Manual only")
}

// MARK: - Spec 2.1: the split and Manual only rules

@MainActor private func splitRuleTests() {
    let d = AutoSwitchEngine.WindowPair(session: 90, weekly: 80)
    let seventy = windowAccount("a", switchThreshold: 70)

    // Spec 2.1's own test: switchThreshold 70, Session set to Default.
    let defaulted = AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .clear, weekly: .leave)
    check(defaulted?.switchThreshold == nil && defaulted?.sessionThreshold == nil && defaulted?.weeklyThreshold == 70,
          "split: 70 then Session set to Default leaves session on its default, weekly 70, switchThreshold gone")
    check(defaulted.map { AutoSwitchSettings.rule(for: $0, defaults: d) } == AutoSwitchEngine.WindowRule(session: 90, weekly: 70, manualOnly: false),
          "split: …which resolves to defaults.session for the session and 70 for the week")

    let sessionForty = AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(40), weekly: .leave)
    check(sessionForty?.sessionThreshold == 40 && sessionForty?.weeklyThreshold == 70 && sessionForty?.switchThreshold == nil,
          "split: 70 then Session 40 keeps weekly at 70")
    let onlyWeekly = AutoSwitchSettings.applyingWindowEdits(to: windowAccount("a"), session: .leave, weekly: .set(55))
    check(onlyWeekly?.weeklyThreshold == 55 && onlyWeekly?.sessionThreshold == nil && onlyWeekly?.switchThreshold == nil,
          "split: an account on defaults given only a weekly value keeps its session on the default")
    let both = AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(60), weekly: .set(65))
    check(both?.sessionThreshold == 60 && both?.weeklyThreshold == 65 && both?.switchThreshold == nil,
          "split: both windows in one write set both")
    check(AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .leave, weekly: .leave) == seventy,
          "split: a write that names neither window changes nothing, not even the split")

    // Values: rounded half away from zero, then 1-100.
    check(AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(0), weekly: .leave) == nil
          && AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(0.4), weekly: .leave) == nil
          && AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(-5), weekly: .leave) == nil
          && AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(.nan), weekly: .leave) == nil,
          "split: 0, a value that rounds to 0, a negative and NaN are refused")
    check(AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(0.5), weekly: .leave)?.sessionThreshold == 1
          && AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(100.4), weekly: .leave)?.sessionThreshold == 100
          && AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(150), weekly: .leave)?.sessionThreshold == 100,
          "split: 0.5 rounds up to 1, 100.4 is 100, and 150 stops at 100")
    check(AutoSwitchSettings.applyingWindowEdits(to: seventy, session: .set(45), weekly: .set(0)) == nil,
          "split: one bad number refuses the whole write")

    // Manual only.
    let manual = windowAccount("m", switchThreshold: 0)
    let cleared = AutoSwitchSettings.applyingWindowEdits(to: manual, session: .set(60), weekly: .leave)
    check(cleared?.switchThreshold == nil && cleared?.sessionThreshold == 60 && cleared?.weeklyThreshold == nil,
          "manual: a window number clears Manual only, sets that window, and the other follows its default")
    check(AutoSwitchSettings.applyingWindowEdits(to: manual, session: .clear, weekly: .leave) == manual
          && AutoSwitchSettings.applyingWindowEdits(to: manual, session: .clear, weekly: .clear) == manual,
          "manual: null (Default) on a Manual only account changes nothing")
    let settingsDefault = AutoSwitchSettings.clearingManualOnly(manual)
    check(settingsDefault.switchThreshold == nil && settingsDefault.sessionThreshold == nil && settingsDefault.weeklyThreshold == nil,
          "manual: in Settings, Default on a Manual only row clears Manual only and both windows follow their defaults")
    check(AutoSwitchSettings.clearingManualOnly(seventy) == seventy, "manual: the Settings helper leaves any other account alone")
}

// MARK: - D8: a legacy write means "both windows"

@MainActor private func legacyWriteTests() {
    let split = windowAccount("a", switchThreshold: 70, session: 40, weekly: 60)
    let legacy = AutoSwitchSettings.applyingBothWindows(70, to: split)
    check(legacy.switchThreshold == 70 && legacy.sessionThreshold == nil && legacy.weeklyThreshold == nil,
          "legacy: setting 70 again clears the window values")
    check([legacy.switchThreshold, legacy.sessionThreshold, legacy.weeklyThreshold] != [split.switchThreshold, split.sessionThreshold, split.weeklyThreshold],
          "legacy: the no-op check compares all three values, so that write is not skipped")
    check(AutoSwitchSettings.applyingBothWindows(0, to: split).switchThreshold == 0
          && AutoSwitchSettings.applyingBothWindows(0, to: split).sessionThreshold == nil,
          "legacy: Manual only clears the window values")
    let defaulted = AutoSwitchSettings.applyingBothWindows(nil, to: split)
    check(defaulted.switchThreshold == nil && defaulted.sessionThreshold == nil && defaulted.weeklyThreshold == nil,
          "legacy: default clears everything")
    check(AutoSwitchSettings.applyingBothWindows(.nan, to: split).switchThreshold == nil
          && AutoSwitchSettings.applyingBothWindows(100.4, to: split).switchThreshold == 100,
          "legacy: NaN follows the default and 100.4 is 100")
}

// MARK: - The global defaults' writers

@MainActor private func defaultWriterTests() {
    withScratchDefaults { defaults in
        let session = AutoSwitchSettings.sessionThresholdKey, weekly = AutoSwitchSettings.weeklyThresholdKey
        check(AutoSwitchSettings.setDefaultThreshold(.session, to: 90.4, in: defaults)
              && AutoSwitchSettings.storedThreshold(forKey: session, in: defaults) == 90,
              "writers: a session default is stored as a whole number")
        check(AutoSwitchSettings.setDefaultThreshold(.weekly, to: 20, in: defaults)
              && AutoSwitchSettings.storedThreshold(forKey: weekly, in: defaults) == 50,
              "writers: a weekly default stays within 50–100")
        check(!AutoSwitchSettings.setDefaultThreshold(.weekly, to: .nan, in: defaults)
              && AutoSwitchSettings.storedThreshold(forKey: weekly, in: defaults) == 50,
              "writers: a default that is not a number writes nothing")
        check(AutoSwitchSettings.setDefaultThreshold(.weekly, to: nil, in: defaults)
              && defaults.object(forKey: weekly) == nil,
              "writers: nil removes the key so the window follows the base again")
        AutoSwitchSettings.setDefaultThreshold(.weekly, to: 55, in: defaults)
        check(AutoSwitchSettings.setBaseThreshold(97, in: defaults)
              && defaults.object(forKey: session) == nil && defaults.object(forKey: weekly) == nil
              && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.thresholdKey, in: defaults) == 97,
              "writers: the base setter removes both window keys (a legacy write is never held back)")
        check(!AutoSwitchSettings.setBaseThreshold(.infinity, in: defaults)
              && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.thresholdKey, in: defaults) == 97,
              "writers: a base that is not finite writes nothing")
    }
}

// MARK: - Account model

@MainActor private func accountModelTests() {
    let legacy = #"[{"id":"CB7797E5-5257-402B-80D6-ADAE1220D368","email":"a@x.com","displayName":"A","provider":"Claude Code","isActive":true,"switchThreshold":70}]"#
    let old = try? JSONDecoder().decode([Account].self, from: Data(legacy.utf8))
    check(old?.first?.switchThreshold == 70 && old?.first?.sessionThreshold == nil && old?.first?.weeklyThreshold == nil
          && old?.first?.priority == nil && old?.first?.prioritySetAt == nil,
          "model: a 1.9 account decodes with no window values and no priority")
    let plain = (try? JSONEncoder().encode([Account(email: "c@x.com", displayName: "C")])).map { String(decoding: $0, as: UTF8.self) } ?? ""
    check(!plain.contains("sessionThreshold") && !plain.contains("weeklyThreshold") && !plain.contains("priority"),
          "model: an account with none of the new values saves none of the new keys, so 1.9 reads it unchanged", plain)
    let set = windowAccount("d", session: 40, weekly: 55, priority: 3, setAt: 1_000)
    let back = (try? JSONEncoder().encode([set])).flatMap { try? JSONDecoder().decode([Account].self, from: $0) }
    check(back == [set], "model: window values, priority and its set time survive saving and loading")
}

// MARK: - Spec 3: priority numbers

@MainActor private func priorityTests() {
    func names(_ accounts: [Account]) -> [String] { accounts.map(\.displayName) }

    check(names(AccountOrder.sortedByPriority([windowAccount("a", priority: 7), windowAccount("b", priority: 1), windowAccount("c", priority: 4)])) == ["b", "c", "a"],
          "priority: gaps are allowed and the lowest number goes first")
    check(names(AccountOrder.sortedByPriority([windowAccount("a"), windowAccount("b", priority: 50), windowAccount("c"), windowAccount("d", priority: 2)])) == ["d", "b", "a", "c"],
          "priority: unnumbered accounts come after every numbered one, in stored order")
    // Ties: the account set most recently goes first (D2).
    check(names(AccountOrder.sortedByPriority([windowAccount("a", priority: 2, setAt: 100), windowAccount("b", priority: 2, setAt: 200), windowAccount("c", priority: 2, setAt: 150)])) == ["b", "c", "a"],
          "priority: among equal numbers the one set most recently goes first")
    check(names(AccountOrder.sortedByPriority([windowAccount("a", priority: 2), windowAccount("b", priority: 2, setAt: 5), windowAccount("c", priority: 2)])) == ["b", "a", "c"],
          "priority: a number never set sorts after one that was, and never-set ones keep the stored order")
    check(names(AccountOrder.sortedByPriority([windowAccount("a", priority: 2, setAt: 9), windowAccount("b", priority: 2, setAt: 9)])) == ["a", "b"],
          "priority: equal set times keep the stored order")
    check(names(AccountOrder.sortedByPriority([windowAccount("a", priority: 0), windowAccount("b", priority: 100), windowAccount("c", priority: 5)])) == ["c", "a", "b"],
          "priority: a stored number outside 1–99 counts as none")
    // The injected recency, as the conformance harness uses it.
    let first = windowAccount("a", priority: 1), second = windowAccount("b", priority: 1)
    check(names(AccountOrder.sortedByPriority([first, second], lastSet: { $0.id == second.id ? 10 : nil })) == ["b", "a"],
          "priority: the lastSet argument decides ties")

    // The founder's example: gmail = 3, then pixelventures = 1.
    let gmail = windowAccount("gmail"), pv = windowAccount("pv"), other = windowAccount("other")
    let t0 = Date(timeIntervalSince1970: 1_000)
    let afterGmail = AccountOrder.settingPriority(3, of: gmail.id, in: [gmail, pv, other], at: t0)
    check(names(afterGmail ?? []) == ["gmail", "pv", "other"] && afterGmail?.first?.priority == 3,
          "priority: setting gmail to 3 keeps it 3 and puts it first of the unnumbered")
    let afterPv = afterGmail.flatMap { AccountOrder.settingPriority(1, of: pv.id, in: $0, at: t0) }
    check(names(afterPv ?? []) == ["pv", "gmail", "other"] && afterPv?.map(\.priority) == [1, 3, nil],
          "priority: then pv to 1 gives pv, gmail, and gmail still has the number he typed (3)")
    // Two sets at the same instant still resolve to the later one.
    let tie1 = AccountOrder.settingPriority(2, of: gmail.id, in: [gmail, pv], at: t0)
    let tie2 = tie1.flatMap { AccountOrder.settingPriority(2, of: pv.id, in: $0, at: t0) }
    check(names(tie2 ?? []) == ["pv", "gmail"], "priority: the number set last goes first even when the clock has not moved")
    check(AccountOrder.settingPriority(nil, of: pv.id, in: afterPv ?? [], at: t0)?.first(where: { $0.id == pv.id })?.prioritySetAt == nil,
          "priority: clearing a number clears when it was set")
    check(AccountOrder.settingPriority(0, of: pv.id, in: [pv]) == nil && AccountOrder.settingPriority(100, of: pv.id, in: [pv]) == nil
          && AccountOrder.settingPriority(-1, of: pv.id, in: [pv]) == nil,
          "priority: a number outside 1–99 is refused")
    check(AccountOrder.settingPriority(1, of: UUID(), in: [pv]) == nil, "priority: an unknown account is refused")
    let same = AccountOrder.settingPriority(3, of: gmail.id, in: afterGmail ?? [], at: Date(timeIntervalSince1970: 9_999))
    check(same == afterGmail, "priority: setting the number an account already has changes nothing, not even its recency")

    // numberedInOrder: a drag or setOrder renumbers 1...N.
    let numbered = AccountOrder.numberedInOrder([windowAccount("a", priority: 9, setAt: 5), windowAccount("b"), windowAccount("c", priority: 3)])
    check(numbered.map(\.priority) == [1, 2, 3] && numbered.allSatisfy { $0.prioritySetAt == nil },
          "priority: numberedInOrder numbers 1…N and replaces what was typed")
    check(AccountOrder.numberedInOrder([]).isEmpty, "priority: numbering nothing gives nothing")
    let dragged = AccountOrder.numberedInOrder(AccountOrder.moving(numbered, fromOffsets: IndexSet(integer: 2), toOffset: 0))
    check(names(dragged) == ["c", "a", "b"] && dragged.map(\.priority) == [1, 2, 3],
          "priority: dragging the last row to the top renumbers every account from 1")
    check(AccountOrder.sortedByPriority(dragged).map(\.id) == dragged.map(\.id), "priority: a dragged order is already sorted, so the sort never undoes it")
}

// MARK: - D20: where to go when the active account is removed

@MainActor private func removalOrderTests() {
    func names(_ accounts: [Account]) -> [String] { accounts.map(\.displayName) }
    let accounts = [windowAccount("a", switchThreshold: 0, priority: 1), windowAccount("b", priority: 3), windowAccount("c", priority: 2), windowAccount("d")]
    check(names(AccountOrder.fallbackOrder(from: accounts)) == ["c", "b", "d", "a"],
          "remove: the fallbacks are the accounts that are not Manual only in priority order, then the Manual only ones")
    check(AccountOrder.fallback(from: accounts)?.displayName == "c", "remove: the first fallback is the first account in priority order that is not Manual only")
    check(AccountOrder.fallback(from: [windowAccount("m", switchThreshold: 0), windowAccount("n", switchThreshold: 0)])?.displayName == "m",
          "remove: with only Manual only accounts left the first is chosen")
    check(AccountOrder.fallbackOrder(from: []).isEmpty && AccountOrder.fallback(from: []) == nil,
          "remove: removing the last account leaves no fallback, so no account is made active")
}

// MARK: - Rule 8: equal rules reproduce 1.9

@MainActor private func equivalenceTests() {
    let now = ISO8601DateFormatter().date(from: "2026-10-24T12:00:00Z")!
    func usage(_ session: Double, _ weekly: Double) -> UsageAPIResponse {
        let json = #"{"five_hour":{"utilization":\#(session),"resets_at":"2026-10-24T15:00:00Z"},"seven_day":{"utilization":\#(weekly),"resets_at":"2026-10-27T12:00:00Z"}}"#
        return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data(json.utf8))
    }
    let active = windowAccount("active"), b = windowAccount("b"), c = windowAccount("c")
    var allEqual = true
    for threshold in [50.0, 90, 98, 100] {
        for s in stride(from: 0.0, through: 100, by: 10) {
            for w in stride(from: 0.0, through: 100, by: 20) {
                let byAccount = [active.id: usage(s, w), b.id: usage(100 - s, 100 - w), c.id: usage(w, s)]
                let old = AutoSwitchEngine.plan(active: active, candidates: [b, c], usageByAccount: byAccount,
                                                isSwitchable: { _ in true }, activeSampledThisCycle: true,
                                                threshold: { _ in threshold }, defaultThreshold: threshold,
                                                hysteresisPct: AutoSwitchEngine.hysteresis, asOf: now)
                let windowed = AutoSwitchEngine.planWindowed(active: active, candidates: [b, c], usageByAccount: byAccount,
                                                             isSwitchable: { _ in true }, activeSampledThisCycle: true,
                                                             rule: { _ in .both(threshold) }, defaults: .init(both: threshold),
                                                             hysteresisPct: AutoSwitchEngine.hysteresis, asOf: now)
                let same = old?.targets.map(\.id) == windowed?.targets.map(\.id) && old?.limit == windowed?.limit
                    && old?.trigger == windowed?.trigger && windowed?.relaxed != true
                if !same { allEqual = false }
            }
        }
    }
    check(allEqual, "rule 8: with equal session and weekly rules the two-window plan is the 1.9 plan and never relaxed")
    let relaxedRule = AutoSwitchEngine.WindowRule(session: 90, weekly: 50, manualOnly: false).relaxed
    check(relaxedRule.weekly == 90 && relaxedRule.session == 90 && AutoSwitchEngine.WindowRule.manualOnlyRule.relaxed == .manualOnlyRule,
          "rule 9: the relaxed rule raises the weekly limit to the session limit and leaves Manual only alone")
    check(AutoSwitchEngine.WindowPair(session: 90, weekly: 50).relaxed == AutoSwitchEngine.WindowPair(both: 90)
          && AutoSwitchEngine.WindowPair(session: 60, weekly: 95).relaxed.weekly == 95,
          "rule 9: relaxing never lowers a weekly limit")
}
