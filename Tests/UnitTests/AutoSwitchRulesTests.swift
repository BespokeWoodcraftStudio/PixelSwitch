// Part A: per-account thresholds, the next-account strategy and early
// draining. Compiled into the unit harness by Tests/run-unit-tests.sh and run
// from main.swift. Uses the global `check(_:_:_:)` defined there.
import Foundation

@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
    strategyTests()
    earlyDrainTests()
    accountOrderTests()
    manualOnlySettingsTests()
    ceilingTests()
    manualOnlyEngineTests()
    lowThresholdTests()
    thresholdStringsTests()
}

/// Fixtures shared by every case in this file.
private enum Rules {
    /// The fixed clock every case uses.
    static let now = ISO8601DateFormatter().date(from: "2026-09-22T17:00:00Z")!

    /// An ISO 8601 time `hours` from `now` (negative is in the past).
    static func iso(_ hours: Double) -> String {
        ISO8601DateFormatter().string(from: now.addingTimeInterval(hours * 3600))
    }

    typealias Plan = (limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger, targets: [Account])

    /// The engine as AppState calls it, with each account's effective threshold.
    static func plan(active: Account, candidates: [Account], _ usage: [UUID: UsageAPIResponse],
                     defaultThreshold: Double = 90, strategy: AutoSwitchStrategy = .mostRoom,
                     drainEarly: Bool = false, drainWithinHours: Double = 24, watchFable: Bool = true,
                     switchable: (Account) -> Bool = { _ in true }, sampled: Bool = true) -> Plan? {
        AutoSwitchEngine.plan(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: switchable, activeSampledThisCycle: sampled,
            threshold: { AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: defaultThreshold) },
            defaultThreshold: defaultThreshold,
            hysteresisPct: AutoSwitchEngine.hysteresis, watchFable: watchFable, strategy: strategy,
            drainEarly: drainEarly, drainWithin: drainWithinHours * 3600, asOf: now)
    }

    /// "stay", or "<limit>/<trigger>: <targets in order>".
    static func describe(_ p: Plan?) -> String {
        guard let p else { return "stay" }
        return "\(p.limit.rawValue)/\(p.trigger.rawValue): " + p.targets.map(\.displayName).joined(separator: ",")
    }
}

/// A usage reading for these cases: session and weekly use in percent (nil is
/// sent as JSON null), every `...ResetsIn` in hours from `Rules.now` (negative
/// is in the past, nil leaves `resets_at` out), and an optional Fable allowance.
private func sample(_ session: Double?, _ weekly: Double?,
                    weeklyResetsIn: Double? = 100, sessionResetsIn: Double? = 2,
                    fable: Double? = nil, fableResetsIn: Double? = 100) -> UsageAPIResponse {
    func window(_ util: Double?, _ resetsIn: Double?) -> String {
        let u = util.map { #""utilization":\#($0)"# } ?? #""utilization":null"#
        let r = resetsIn.map { #","resets_at":"\#(Rules.iso($0))""# } ?? ""
        return "{" + u + r + "}"
    }
    var json = #"{"five_hour":"# + window(session, sessionResetsIn) + #","seven_day":"# + window(weekly, weeklyResetsIn)
    if let fable {
        let r = fableResetsIn.map { #","resets_at":"\#(Rules.iso($0))""# } ?? ""
        json += #","limits":[{"kind":"weekly_scoped","group":"weekly","percent":\#(fable)\#(r),"scope":{"model":{"id":null,"display_name":"Fable"}}}]"#
    }
    return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data((json + "}").utf8))
}

// MARK: - Settings keys, defaults, ranges; saved accounts

@MainActor private func autoSwitchSettingsTests() {
    // Saved accounts from before per-account thresholds existed.
    let legacy = #"[{"id":"CB7797E5-5257-402B-80D6-ADAE1220D368","email":"a@x.com","displayName":"A","provider":"Claude Code","isActive":true,"customLabel":"Work"}]"#
    let decoded = try? JSONDecoder().decode([Account].self, from: Data(legacy.utf8))
    check(decoded?.count == 1 && decoded?.first?.switchThreshold == nil,
          "rules: saved accounts without switchThreshold decode, with no threshold of their own")
    check(decoded?.first?.customLabel == "Work" && decoded?.first?.isActive == true,
          "rules: an old saved account keeps everything else it had")

    let own = Account(email: "b@x.com", displayName: "B", switchThreshold: 75)
    let roundTrip = (try? JSONEncoder().encode([own])).flatMap { try? JSONDecoder().decode([Account].self, from: $0) }
    check(roundTrip?.first?.switchThreshold == 75, "rules: a per-account threshold survives saving and loading")
    let plainJSON = (try? JSONEncoder().encode([Account(email: "c@x.com", displayName: "C")])).map { String(decoding: $0, as: UTF8.self) } ?? ""
    check(!plainJSON.contains("switchThreshold"),
          "rules: an account on the default threshold saves no switchThreshold key, so an older build reads it unchanged", plainJSON)

    check(AutoSwitchSettings.enabledKey == "autoSwitchEnabled"
          && AutoSwitchSettings.thresholdKey == "autoSwitchThreshold"
          && AutoSwitchSettings.onFableKey == "autoSwitchOnFable"
          && AutoSwitchSettings.strategyKey == "autoSwitchStrategy"
          && AutoSwitchSettings.drainEarlyKey == "autoSwitchDrainEarly"
          && AutoSwitchSettings.drainWithinHoursKey == "autoSwitchDrainWithinHours",
          "rules: the setting keys are the ones the design fixes")
    check(AutoSwitchSettings.thresholdRange == 50.0...100.0 && AutoSwitchSettings.drainHoursRange == 1.0...72.0,
          "rules: thresholds run 50–100 and the drain window 1–72 hours")
    check(AutoSwitchStrategy.allCases.map(\.rawValue) == ["mostRoom", "myOrder", "resetsSoonest"],
          "rules: the three strategies, by the names settings and the CLI use")

    let defaults = UserDefaults.standard
    let keys = [AutoSwitchSettings.thresholdKey, AutoSwitchSettings.strategyKey,
                AutoSwitchSettings.drainEarlyKey, AutoSwitchSettings.drainWithinHoursKey]
    let saved = keys.map { defaults.object(forKey: $0) }
    defer {
        for (key, value) in zip(keys, saved) {
            if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
        }
    }
    keys.forEach { defaults.removeObject(forKey: $0) }
    check(AutoSwitchSettings.defaultThreshold == 90 && AutoSwitchSettings.strategy == .mostRoom
          && AutoSwitchSettings.drainEarly && AutoSwitchSettings.drainWithinHours == 24,
          "rules: untouched settings read as 90%, most room left, early switching on, 24 hours")

    defaults.set(100.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 100, "rules: the default threshold can now be 100%")
    defaults.set(99.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 99, "rules: a threshold saved by the old 50–99 slider still reads the same")
    defaults.set(30.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 50, "rules: a default threshold below 50 reads as 50")

    defaults.set("resetsSoonest", forKey: AutoSwitchSettings.strategyKey)
    check(AutoSwitchSettings.strategy == .resetsSoonest, "rules: a saved strategy is read back")
    defaults.set("fastest", forKey: AutoSwitchSettings.strategyKey)
    check(AutoSwitchSettings.strategy == .mostRoom, "rules: an unknown strategy name falls back to most room left")

    defaults.set(false, forKey: AutoSwitchSettings.drainEarlyKey)
    check(AutoSwitchSettings.drainEarly == false, "rules: early switching can be turned off")

    defaults.set(0.5, forKey: AutoSwitchSettings.drainWithinHoursKey)
    let low = AutoSwitchSettings.drainWithinHours
    defaults.set(500.0, forKey: AutoSwitchSettings.drainWithinHoursKey)
    let high = AutoSwitchSettings.drainWithinHours
    defaults.set(12, forKey: AutoSwitchSettings.drainWithinHoursKey)
    let whole = AutoSwitchSettings.drainWithinHours
    check(low == 1 && high == 72 && whole == 12, "rules: the drain window is kept within 1–72 hours, and a whole number reads too", "\(low) \(high) \(whole)")

    check(AutoSwitchSettings.clampedThreshold(49) == 50 && AutoSwitchSettings.clampedThreshold(101) == 100
          && AutoSwitchSettings.clampedThreshold(75) == 75 && AutoSwitchSettings.clampedThreshold(.nan) == 90,
          "rules: thresholds are kept within 50–100, and not-a-number reads as 90")
    check(AutoSwitchSettings.normalizedAccountThreshold(nil) == nil
          && AutoSwitchSettings.normalizedAccountThreshold(.nan) == nil
          && AutoSwitchSettings.normalizedAccountThreshold(120) == 100
          && AutoSwitchSettings.normalizedAccountThreshold(70) == 70,
          "rules: a per-account threshold is stored clamped, and nil or not-a-number clears it")
    check(AutoSwitchSettings.effectiveThreshold(own: nil, defaultThreshold: 90) == 90
          && AutoSwitchSettings.effectiveThreshold(own: 70, defaultThreshold: 90) == 70
          && AutoSwitchSettings.effectiveThreshold(own: nil, defaultThreshold: 30) == 50,
          "rules: an account's own threshold wins; otherwise the default applies")
}

// MARK: - Per-account thresholds (trigger and eligibility)

@MainActor private func perAccountThresholdTests() {
    let a70 = Account(email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 70)
    let aDefault = Account(email: "a@x.com", displayName: "A", isActive: true)
    let a100 = Account(email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 100)
    let b70 = Account(email: "b@x.com", displayName: "B", switchThreshold: 70)
    let c = Account(email: "c@x.com", displayName: "C")
    let d100 = Account(email: "d@x.com", displayName: "D", switchThreshold: 100)
    let fresh = Account(email: "e@x.com", displayName: "E")

    // The ACTIVE account's own threshold decides when to move.
    let at72: [UUID: UsageAPIResponse] = [a70.id: sample(72, 40), c.id: sample(10, 10)]
    check(Rules.describe(Rules.plan(active: a70, candidates: [c], at72)) == "windows/threshold: C",
          "rules: an account with its own 70% threshold switches at 72%", Rules.describe(Rules.plan(active: a70, candidates: [c], at72)))
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [c], [aDefault.id: sample(72, 40), c.id: sample(10, 10)])) == "stay",
          "rules: the same 72% on an account using the default 90% stays put")
    check(Rules.describe(Rules.plan(active: a70, candidates: [c], [a70.id: sample(69, 40), c.id: sample(10, 10)])) == "stay",
          "rules: 69% under a 70% threshold stays put")

    // Each CANDIDATE is judged against its own threshold minus the 10-point hysteresis.
    let mixed: [UUID: UsageAPIResponse] = [
        aDefault.id: sample(95, 40),
        b70.id: sample(65, 20),   // over its own 60% ceiling
        c.id: sample(75, 20),     // under the default 80% ceiling
    ]
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [b70, c], mixed)) == "windows/threshold: C",
          "rules: a candidate with its own 70% threshold must be at or under 60%", Rules.describe(Rules.plan(active: aDefault, candidates: [b70, c], mixed)))
    let b70AtCeiling: [UUID: UsageAPIResponse] = [aDefault.id: sample(95, 40), b70.id: sample(60, 20)]
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [b70], b70AtCeiling)) == "windows/threshold: B",
          "rules: exactly at its own ceiling is still eligible")

    // Fable triggers on the active account's own threshold too.
    let fableAt75: [UUID: UsageAPIResponse] = [a70.id: sample(10, 20, fable: 75), c.id: sample(5, 10, fable: 20)]
    check(Rules.describe(Rules.plan(active: a70, candidates: [c], fableAt75)) == "fable/threshold: C",
          "rules: Fable at 75% moves an account whose own threshold is 70%", Rules.describe(Rules.plan(active: a70, candidates: [c], fableAt75)))

    // Threshold 100: use the account until it is empty.
    check(Rules.describe(Rules.plan(active: a100, candidates: [c], [a100.id: sample(99, 50), c.id: sample(10, 10)])) == "stay",
          "rules: an active account on 100% stays at 99%")
    check(Rules.describe(Rules.plan(active: a100, candidates: [c], [a100.id: sample(100, 50), c.id: sample(10, 10)])) == "windows/threshold: C",
          "rules: an active account on 100% moves once it is empty")
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [d100], [aDefault.id: sample(95, 40), d100.id: sample(90, 90)])) == "windows/threshold: D",
          "rules: a candidate on 100% is eligible up to 90%")
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [d100], [aDefault.id: sample(95, 40), d100.id: sample(91, 20)])) == "stay",
          "rules: a candidate on 100% at 91% is not")

    // Review focus: an account with no usage sample yet, and nobody eligible.
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [fresh], [aDefault.id: sample(95, 40)])) == "stay",
          "rules: an account with no usage sample yet is never chosen")
    let nobody: [UUID: UsageAPIResponse] = [aDefault.id: sample(95, 40), b70.id: sample(61, 20), c.id: sample(81, 20)]
    check(Rules.plan(active: aDefault, candidates: [b70, c], nobody) == nil,
          "rules: when no candidate is eligible the plan is to stay put")
}

// MARK: - Choosing the next account

@MainActor private func strategyTests() {
    let a = Account(email: "a@x.com", displayName: "A", isActive: true)
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    let d = Account(email: "d@x.com", displayName: "D")
    let e = Account(email: "e@x.com", displayName: "E")
    func plan(_ usage: [UUID: UsageAPIResponse], _ strategy: AutoSwitchStrategy, active: Account? = nil, candidates: [Account]? = nil) -> String {
        Rules.describe(Rules.plan(active: active ?? a, candidates: candidates ?? [b, c, d], usage, strategy: strategy))
    }

    // Review focus: an account with no usage sample yet is never chosen, whatever the strategy.
    let fresh = Account(email: "f@x.com", displayName: "F")
    for strategy in AutoSwitchStrategy.allCases {
        check(plan([a.id: sample(95, 40)], strategy, candidates: [fresh]) == "stay",
              "rules: an account with no usage sample yet is never chosen (\(strategy.rawValue))")
    }

    // Three eligible accounts, listed B, C, D.
    let three: [UUID: UsageAPIResponse] = [
        a.id: sample(95, 40),
        b.id: sample(10, 50),   // binding 50, weekly resets in 100 h
        c.id: sample(10, 20, weeklyResetsIn: 30),    // binding 20, resets in 30 h
        d.id: sample(10, 40, weeklyResetsIn: 10),    // binding 40, resets in 10 h
    ]
    check(plan(three, .mostRoom) == "windows/threshold: C,D,B", "rules: most room left ranks by lowest use when thresholds are equal", plan(three, .mostRoom))
    check(plan(three, .myOrder) == "windows/threshold: B,C,D", "rules: my order keeps the list's order", plan(three, .myOrder))
    check(plan(three, .resetsSoonest) == "windows/threshold: D,C,B", "rules: resets soonest ranks by the weekly reset", plan(three, .resetsSoonest))

    // Resets soonest: a tie (same minute) goes to most room; an unknown reset ranks last.
    let ties: [UUID: UsageAPIResponse] = [
        a.id: sample(95, 40),
        b.id: sample(10, 60, weeklyResetsIn: 30),
        c.id: sample(10, 30, weeklyResetsIn: 30 + 20.0 / 3600),   // 20 seconds later: same minute
        d.id: sample(5, 5, weeklyResetsIn: nil),                  // no weekly reset time at all
        e.id: sample(5, 5, weeklyResetsIn: -3),                   // retained from a week that has ended
    ]
    let tied = plan(ties, .resetsSoonest, candidates: [b, c, d, e])
    check(tied == "windows/threshold: C,B,D,E", "rules: resets soonest breaks a tie by most room, and puts unknown resets last", tied)
    check(AutoSwitchEngine.weeklyReset(ties[e.id], limit: .windows, asOf: Rules.now) == nil,
          "rules: a weekly reset in the past counts as unknown, not as soonest")

    // My order: always from the top, never "the next one after the active account".
    let fromTop: [UUID: UsageAPIResponse] = [a.id: sample(10, 10), c.id: sample(95, 40), b.id: sample(10, 10), d.id: sample(10, 5)]
    check(plan(fromTop, .myOrder, active: c, candidates: [a, b, d]) == "windows/threshold: A,B,D",
          "rules: my order starts from the top of the list, not after the active account", plan(fromTop, .myOrder, active: c, candidates: [a, b, d]))
    let freedUp: [UUID: UsageAPIResponse] = [d.id: sample(95, 40), a.id: sample(99, 99), b.id: sample(30, 30), c.id: sample(5, 5)]
    check(plan(freedUp, .myOrder, active: d, candidates: [a, b, c]) == "windows/threshold: B,C",
          "rules: a higher account that has freed up again comes before the next one down", plan(freedUp, .myOrder, active: d, candidates: [a, b, c]))

    // Fable: most room keeps preferring Fable room; the other two honour the user strictly.
    let fableSpare: [UUID: UsageAPIResponse] = [a.id: sample(95, 40, fable: 50), b.id: sample(10, 10, fable: 100), c.id: sample(20, 20, fable: 10)]
    check(plan(fableSpare, .mostRoom, candidates: [b, c]) == "windows/threshold: C,B", "rules: most room left still prefers an account with Fable to spare")
    check(plan(fableSpare, .myOrder, candidates: [b, c]) == "windows/threshold: B,C", "rules: my order keeps its order even when the first account is out of Fable")

    // A Fable switch under resets soonest ranks by the FABLE weekly reset.
    let fableReset: [UUID: UsageAPIResponse] = [
        a.id: sample(10, 20, fable: 95),
        b.id: sample(5, 10, weeklyResetsIn: 5, fable: 10, fableResetsIn: 50),     // weekly resets in 5 h, Fable in 50 h
        c.id: sample(5, 10, fable: 30, fableResetsIn: 20),   // weekly in 100 h, Fable in 20 h
    ]
    check(plan(fableReset, .resetsSoonest, candidates: [b, c]) == "fable/threshold: C,B",
          "rules: a Fable switch under resets soonest uses the Fable weekly reset", plan(fableReset, .resetsSoonest, candidates: [b, c]))
    check(plan(fableReset, .mostRoom, candidates: [b, c]) == "fable/threshold: B,C", "rules: the same Fable switch under most room left ranks by Fable room")

    // Most room: equal use keeps the list's order, so the result never flickers.
    let equal: [UUID: UsageAPIResponse] = [a.id: sample(95, 40), b.id: sample(10, 30), c.id: sample(10, 30)]
    check(plan(equal, .mostRoom, candidates: [c, b]) == "windows/threshold: C,B", "rules: most room left breaks a tie by the list's order")

    // Founder, 2026-09-25: "Most room left" means room left under each account's OWN
    // threshold. X at 60% of a 70% threshold has 10 points left; Y at 65% of a 100%
    // threshold has 35. Y has more room, although its use is higher.
    let x = Account(email: "x@x.com", displayName: "X", switchThreshold: 70)
    let y = Account(email: "y@x.com", displayName: "Y", switchThreshold: 100)
    let ownRoom: [UUID: UsageAPIResponse] = [a.id: sample(95, 40), x.id: sample(10, 60), y.id: sample(10, 65)]
    check(plan(ownRoom, .mostRoom, candidates: [x, y]) == "windows/threshold: Y,X",
          "rules: most room left ranks by room under each account's own threshold", plan(ownRoom, .mostRoom, candidates: [x, y]))
    let ownRoomTie: [UUID: UsageAPIResponse] = [a.id: sample(95, 40), x.id: sample(10, 50, weeklyResetsIn: 30), y.id: sample(10, 60, weeklyResetsIn: 30)]
    check(plan(ownRoomTie, .resetsSoonest, candidates: [x, y]) == "windows/threshold: Y,X",
          "rules: resets soonest breaks a tie by room under each account's own threshold", plan(ownRoomTie, .resetsSoonest, candidates: [x, y]))
}

// MARK: - Switching early to use quota before it resets

@MainActor private func earlyDrainTests() {
    let a = Account(email: "a@x.com", displayName: "A", isActive: true)
    let y = Account(email: "y@x.com", displayName: "Y")
    let y100 = Account(email: "y@x.com", displayName: "Y", switchThreshold: 100)
    let z = Account(email: "z@x.com", displayName: "Z")
    func drain(_ usage: [UUID: UsageAPIResponse], active: Account? = nil, candidates: [Account]? = nil,
               strategy: AutoSwitchStrategy = .resetsSoonest, drainEarly: Bool = true, within: Double = 24,
               watchFable: Bool = true, switchable: (Account) -> Bool = { _ in true }) -> String {
        Rules.describe(Rules.plan(active: active ?? a, candidates: candidates ?? [y], usage, strategy: strategy,
                                  drainEarly: drainEarly, drainWithinHours: within, watchFable: watchFable, switchable: switchable))
    }
    // A is well under its threshold and resets in 100 h.
    let activeA = sample(30, 40)

    // The founder's example: resets in 12 hours with 5% left.
    check(drain([a.id: activeA, y100.id: sample(10, 95, weeklyResetsIn: 12)], candidates: [y100]) == "windows/drainEarly: Y",
          "rules: drain: an account on 100% with 5% left that resets in 12 h is used first")
    check(drain([a.id: activeA, y.id: sample(10, 95, weeklyResetsIn: 12)]) == "stay",
          "rules: drain: the same 95% is past a 90% threshold, so it is not a target")

    let yIn12 = sample(10, 80, weeklyResetsIn: 12)
    check(drain([a.id: activeA, y.id: yIn12]) == "windows/drainEarly: Y", "rules: drain fires for an account resetting in 12 h with room")
    check(drain([a.id: activeA, y.id: sample(10, 80, weeklyResetsIn: 30)]) == "stay", "rules: drain: a reset 30 h away is outside a 24 h window")
    check(drain([a.id: activeA, y.id: sample(10, 80, weeklyResetsIn: 30)], within: 48) == "windows/drainEarly: Y", "rules: drain: ...and inside a 48 h one")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: 10), y.id: yIn12]) == "stay", "rules: drain: never to an account that resets later than the active one")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: 12), y.id: yIn12]) == "stay", "rules: drain: never to one that resets at the same time")
    check(drain([a.id: activeA, y.id: sample(10, 89.5, weeklyResetsIn: 12)]) == "stay", "rules: drain: 89.5% under a 90% threshold is less than 1 point of room")
    check(drain([a.id: activeA, y.id: sample(10, 89, weeklyResetsIn: 12)]) == "windows/drainEarly: Y", "rules: drain: 89% under a 90% threshold is enough room")
    check(drain([a.id: activeA, y.id: sample(95, 20, weeklyResetsIn: 12)]) == "stay", "rules: drain: no room on the 5-hour window means no drain")
    check(drain([a.id: activeA, y.id: yIn12], strategy: .mostRoom) == "stay", "rules: drain: never under most room left")
    check(drain([a.id: activeA, y.id: yIn12], strategy: .myOrder) == "stay", "rules: drain: never under my order")
    check(drain([a.id: activeA, y.id: yIn12], drainEarly: false) == "stay", "rules: drain: never with the switch off")
    check(drain([a.id: activeA, y.id: yIn12], switchable: { $0.id != y.id }) == "stay", "rules: drain: never to an account that cannot be switched to")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: nil), y.id: yIn12]) == "stay", "rules: drain: never when the active account's weekly reset is unknown")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: -1), y.id: yIn12]) == "stay", "rules: drain: never when the active account's sample is from a week that has ended")
    check(drain([a.id: activeA, y.id: sample(10, 5, weeklyResetsIn: -1)]) == "stay", "rules: drain: never to an account whose weekly sample is from a week that has ended")
    check(drain([a.id: activeA]) == "stay", "rules: drain: never to an account with no usage sample yet")

    // Never on Fable: the drain looks at the 7-day window only.
    check(drain([a.id: activeA, y.id: sample(10, 20, fable: 10, fableResetsIn: 5)]) == "stay", "rules: drain: a Fable reset coming up soon never drains")
    // ...but a drain target must not be at its Fable threshold, or the Fable
    // trigger would move you off it and the drain would bring you back.
    check(drain([a.id: activeA, y.id: sample(10, 20, weeklyResetsIn: 12, fable: 95)]) == "stay", "rules: drain: not to an account out of Fable while Fable switching is on")
    check(drain([a.id: activeA, y.id: sample(10, 20, weeklyResetsIn: 12, fable: 95)], watchFable: false) == "windows/drainEarly: Y", "rules: drain: ...but yes with Fable switching off")

    // Two targets: the one that resets first.
    check(drain([a.id: activeA, y.id: yIn12, z.id: sample(10, 50, weeklyResetsIn: 6)], candidates: [y, z]) == "windows/drainEarly: Z,Y",
          "rules: drain ranks targets by the soonest reset")

    // Review focus: a threshold reached with nowhere to go is still "reached": the drain stays out of it.
    check(drain([a.id: sample(92, 40), y.id: sample(10, 85, weeklyResetsIn: 12)]) == "stay",
          "rules: drain never fires once the active account has reached its threshold")
    check(drain([a.id: sample(92, 40), y.id: sample(10, 85, weeklyResetsIn: 12), z.id: sample(10, 70, weeklyResetsIn: 50)], candidates: [y, z]) == "windows/threshold: Z",
          "rules: at the threshold the threshold rule, with its hysteresis, decides")

    // Ping-pong. Drained onto Y; Y reaches its threshold; the threshold rule moves you on...
    let yFull: [UUID: UsageAPIResponse] = [y.id: sample(10, 90, weeklyResetsIn: 12), a.id: activeA]
    let yActive = Account(id: y.id, email: "y@x.com", displayName: "Y", isActive: true)
    let aIdle = Account(id: a.id, email: "a@x.com", displayName: "A")
    check(drain(yFull, active: yActive, candidates: [aIdle]) == "windows/threshold: A",
          "rules: ping-pong: the drained account reaching its threshold moves you on")
    // ...and the drain cannot move you straight back: Y has no room left.
    check(drain(yFull, active: a, candidates: [y]) == "stay",
          "rules: ping-pong: the drain does not bring you back to an account at its threshold")
    // Once Y's 5-hour window resets, its weekly quota still expires first, so
    // using it again is the point of the feature, not a ping-pong.
    let ySessionReset: [UUID: UsageAPIResponse] = [y.id: sample(90, 50, weeklyResetsIn: 12, sessionResetsIn: -0.1), a.id: activeA]
    check(drain(ySessionReset, active: a, candidates: [y]) == "windows/drainEarly: Y",
          "rules: drain uses an account again once the 5-hour window that stopped it has reset")

    // The shared rule AppState verifies with.
    let fresh = sample(10, 85, weeklyResetsIn: 12)
    let reset = Rules.now.addingTimeInterval(100 * 3600)
    let asDrain = AutoSwitchEngine.eligibleUtilization(fresh, limit: .windows, trigger: .drainEarly, threshold: 90, hysteresisPct: 10,
                                                       activeWeeklyReset: reset, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let asThreshold = AutoSwitchEngine.eligibleUtilization(fresh, limit: .windows, trigger: .threshold, threshold: 90, hysteresisPct: 10,
                                                           activeWeeklyReset: nil, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let noActiveReset = AutoSwitchEngine.eligibleUtilization(fresh, limit: .windows, trigger: .drainEarly, threshold: 90, hysteresisPct: 10,
                                                             activeWeeklyReset: nil, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let onFable = AutoSwitchEngine.eligibleUtilization(fresh, limit: .fable, trigger: .drainEarly, threshold: 90, hysteresisPct: 10,
                                                       activeWeeklyReset: reset, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    check(asDrain == 85 && asThreshold == nil && noActiveReset == nil && onFable == nil,
          "rules: verification: 85% passes as a drain (1-point room) but not as a threshold switch (10-point), and a drain needs the active reset and the windows limit",
          "\(String(describing: asDrain)) \(String(describing: asThreshold)) \(String(describing: noActiveReset)) \(String(describing: onFable))")
    check(AutoSwitchEngine.drainMinimumRoom == 1.0, "rules: the drain's minimum room is 1 percentage point")
}

// MARK: - The accounts list's order

@MainActor private func accountOrderTests() {
    let a = Account(email: "a@x.com", displayName: "A")
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    let d = Account(email: "d@x.com", displayName: "D")
    let removed = Account(email: "gone@x.com", displayName: "Gone")
    let list = [a, b, c, d]
    func names(_ accounts: [Account]?) -> String { accounts.map { $0.map(\.displayName).joined() } ?? "refused" }

    check(names(AccountOrder.reordered(list, by: [c.id, a.id, d.id, b.id])) == "CADB", "order: a full permutation is applied")
    check(names(AccountOrder.reordered(list, by: list.map(\.id))) == "ABCD", "order: the same order is accepted unchanged")
    check(names(AccountOrder.reordered(list, by: [a.id, b.id, c.id])) == "refused", "order: leaving an account out is refused")
    check(names(AccountOrder.reordered(list, by: [a.id, b.id, c.id, removed.id])) == "refused", "order: naming a removed account is refused")
    check(names(AccountOrder.reordered(list, by: [a.id, b.id, c.id, d.id, removed.id])) == "refused", "order: an extra account is refused")
    check(names(AccountOrder.reordered(list, by: [a.id, a.id, c.id, d.id])) == "refused", "order: repeating an account is refused")
    check(names(AccountOrder.reordered([], by: [])) == "", "order: no accounts and an empty order is fine")

    func moved(_ from: [Int], to: Int) -> String { AccountOrder.moving(list, fromOffsets: IndexSet(from), toOffset: to).map(\.displayName).joined() }
    check(moved([0], to: 3) == "BCAD", "order: dragging the first account down to above the last", moved([0], to: 3))
    check(moved([3], to: 0) == "DABC", "order: dragging the last account to the top", moved([3], to: 0))
    check(moved([1, 2], to: 4) == "ADBC", "order: dragging two accounts to the bottom", moved([1, 2], to: 4))
    check(moved([1], to: 2) == "ABCD" && moved([1], to: 1) == "ABCD", "order: dropping an account where it already is changes nothing")
    check(moved([9], to: 0) == "ABCD", "order: an offset outside the list is ignored")
    check(moved([0], to: 99) == "BCDA", "order: a destination past the end moves to the bottom")
}

// MARK: - Manual only (0%) and thresholds below 50 (founder, 2026-09-26)

@MainActor private func manualOnlySettingsTests() {
    check(AutoSwitchSettings.accountThresholdRange == 0.0...100.0 && AutoSwitchSettings.accountStepperRange == 1.0...100.0
          && AutoSwitchSettings.thresholdRange == 50.0...100.0 && AutoSwitchSettings.manualOnlyThreshold == 0,
          "rules: a per-account threshold runs 0–100 while the default stays 50–100")
    let n = AutoSwitchSettings.normalizedAccountThreshold
    check(n(-5) == 0 && n(0) == 0 && n(0.4) == 0 && n(0.6) == 1 && n(35) == 35 && n(72.6) == 73 && n(120) == 100
          && n(nil) == nil && n(.nan) == nil && n(-0.3)?.sign == .plus,
          "rules: a per-account threshold is stored as a whole number within 0–100")
    let manual = AutoSwitchSettings.isManualOnly
    check(manual(0) && manual(0.4) && !manual(nil) && !manual(1) && !manual(50) && !manual(100),
          "rules: 0 is Manual only and nothing else is")
    check(AutoSwitchSettings.effectiveThreshold(own: 35, defaultThreshold: 90) == 35
          && AutoSwitchSettings.effectiveThreshold(own: 0, defaultThreshold: 90) == 0
          && AutoSwitchSettings.effectiveThreshold(own: nil, defaultThreshold: 30) == 50,
          "rules: an account's own threshold below 50 is kept, Manual only's rule value is 0, and the default still clamps to 50")
    let leave = AutoSwitchSettings.leaveAtThreshold
    check(leave(0, 90) == 90 && leave(0, 70) == 70 && leave(0, 30) == 50 && leave(35, 90) == 35 && leave(90, 70) == 90,
          "rules: a Manual only account is left at the default")

    let m = Account(email: "m@x.com", displayName: "M", switchThreshold: 0)
    let back = (try? JSONEncoder().encode([m])).flatMap { try? JSONDecoder().decode([Account].self, from: $0) }?.first
    check(back?.switchThreshold == 0 && AutoSwitchSettings.isManualOnly(own: back?.switchThreshold),
          "rules: a Manual only account saves switchThreshold 0 and loads it back")

    let saved14 = #"[{"id":"CB7797E5-5257-402B-80D6-ADAE1220D361","email":"a@x.com","displayName":"A","provider":"Claude Code","isActive":true,"switchThreshold":50},{"id":"CB7797E5-5257-402B-80D6-ADAE1220D362","email":"b@x.com","displayName":"B","provider":"Claude Code","isActive":false,"switchThreshold":75},{"id":"CB7797E5-5257-402B-80D6-ADAE1220D363","email":"c@x.com","displayName":"C","provider":"Claude Code","isActive":false,"switchThreshold":100},{"id":"CB7797E5-5257-402B-80D6-ADAE1220D364","email":"d@x.com","displayName":"D","provider":"Claude Code","isActive":false}]"#
    let old = (try? JSONDecoder().decode([Account].self, from: Data(saved14.utf8))) ?? []
    check(old.map { AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: 90) } == [50, 75, 100, 90]
          && !old.contains { AutoSwitchSettings.isManualOnly(own: $0.switchThreshold) },
          "rules: accounts saved by 1.4 behave exactly as before")

    let defaults = UserDefaults.standard
    let savedDefault = defaults.object(forKey: AutoSwitchSettings.thresholdKey)
    defaults.set(0.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 90, "rules: a saved default of 0 still reads 90, never Manual only")
    if let savedDefault { defaults.set(savedDefault, forKey: AutoSwitchSettings.thresholdKey) } else { defaults.removeObject(forKey: AutoSwitchSettings.thresholdKey) }

    let mA = Account(email: "m@x.com", displayName: "M", switchThreshold: 0)
    let mB = Account(email: "n@x.com", displayName: "N", switchThreshold: 0)
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    check(AccountOrder.fallback(from: [mA, b, c])?.id == b.id && AccountOrder.fallback(from: [b, mA])?.id == b.id
          && AccountOrder.fallback(from: [mA, mB])?.id == mA.id && AccountOrder.fallback(from: []) == nil,
          "order: removing the active account falls back to the first account that is not Manual only")
}

@MainActor private func ceilingTests() {
    let ceiling = AutoSwitchEngine.ceiling
    check(AutoSwitchEngine.hysteresis == 10, "rules: the hysteresis is 10 points")
    check(ceiling(100, 10) == 90 && ceiling(50, 10) == 40 && ceiling(20, 10) == 10 && ceiling(19, 10) == 9.5
          && ceiling(10, 10) == 5 && ceiling(5, 10) == 2.5 && ceiling(1, 10) == 0.5,
          "rules: ceiling: 10 points under from 20% up, half the threshold below")
    check(ceiling(90, 1) == 89 && ceiling(2, 1) == 1 && ceiling(1, 1) == 0.5,
          "rules: ceiling: the drain's 1 point, or half the threshold below 2%")
    check(ceiling(0, 10) == nil && ceiling(0, 1) == nil && ceiling(-5, 10) == nil && ceiling(.nan, 10) == nil,
          "rules: ceiling: Manual only has none")
    var sound = true
    var previous: (Double, Double) = (0, 0)
    for t in 1...100 {
        let T = Double(t)
        guard let c = ceiling(T, 10), let d = ceiling(T, 1) else { sound = false; break }
        if !(0 < c && c < T && c <= d && d < T && c >= previous.0 && d >= previous.1) { sound = false; break }
        previous = (c, d)
    }
    check(sound, "rules: ceiling: every whole threshold 1–100 leaves room, never reaches it, and the drain's is never tighter")
    let same = (20...100).allSatisfy { ceiling(Double($0), 10) == Double($0) - 10 }
        && (2...100).allSatisfy { ceiling(Double($0), 1) == Double($0) - 1 }
    check(same, "rules: ceiling: identical to 1.4 for 20–100 (switch) and 2–100 (drain)")
}

@MainActor private func manualOnlyEngineTests() {
    let aDefault = Account(email: "a@x.com", displayName: "A", isActive: true)
    let m0 = Account(email: "m@x.com", displayName: "M", switchThreshold: 0)
    let mActive = Account(email: "m@x.com", displayName: "M", isActive: true, switchThreshold: 0)
    let b = Account(email: "b@x.com", displayName: "B")
    let bActive = Account(email: "b@x.com", displayName: "B", isActive: true)
    let c = Account(email: "c@x.com", displayName: "C")
    let y = Account(email: "y@x.com", displayName: "Y")
    let strategies: [AutoSwitchStrategy] = [.mostRoom, .myOrder, .resetsSoonest]
    func describe(_ p: Rules.Plan?) -> String { Rules.describe(p) }

    for s in strategies {
        check(describe(Rules.plan(active: aDefault, candidates: [m0], [aDefault.id: sample(95, 40), m0.id: sample(0, 0)], strategy: s)) == "stay",
              "rules: manual only: never a target, even at 0% use (\(s.rawValue))")
        check(describe(Rules.plan(active: aDefault, candidates: [m0, c],
                                  [aDefault.id: sample(95, 40), m0.id: sample(0, 0), c.id: sample(50, 50)], strategy: s)) == "windows/threshold: C",
              "rules: manual only: skipped for the next account (\(s.rawValue))")
    }
    check(describe(Rules.plan(active: aDefault, candidates: [m0], [aDefault.id: sample(10, 20, fable: 95), m0.id: sample(0, 0, fable: 0)])) == "stay"
          && describe(Rules.plan(active: aDefault, candidates: [m0, c],
                                 [aDefault.id: sample(10, 20, fable: 95), m0.id: sample(0, 0, fable: 0), c.id: sample(5, 10, fable: 20)])) == "fable/threshold: C",
          "rules: manual only: never a Fable target")
    let drainUsage: [UUID: UsageAPIResponse] = [aDefault.id: sample(30, 40), m0.id: sample(10, 80, weeklyResetsIn: 12), y.id: sample(10, 80, weeklyResetsIn: 12)]
    check(describe(Rules.plan(active: aDefault, candidates: [m0], drainUsage, strategy: .resetsSoonest, drainEarly: true)) == "stay"
          && describe(Rules.plan(active: aDefault, candidates: [y], drainUsage, strategy: .resetsSoonest, drainEarly: true)) == "windows/drainEarly: Y",
          "rules: manual only: never an early-drain target")
    let refusedThreshold = AutoSwitchEngine.eligibleUtilization(sample(0, 0), limit: .windows, trigger: .threshold, threshold: 0, hysteresisPct: 10,
                                                                activeWeeklyReset: nil, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let refusedDrain = AutoSwitchEngine.eligibleUtilization(sample(0, 0, weeklyResetsIn: 12), limit: .windows, trigger: .drainEarly, threshold: 0, hysteresisPct: 10,
                                                            activeWeeklyReset: Rules.now.addingTimeInterval(100 * 3600), drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    check(refusedThreshold == nil && refusedDrain == nil, "rules: manual only: verification refuses it under both rules")
    var keychainReads = 0
    _ = Rules.plan(active: aDefault, candidates: [m0], [aDefault.id: sample(95, 40), m0.id: sample(0, 0)], switchable: { _ in keychainReads += 1; return true })
    check(keychainReads == 0, "rules: manual only: never checked in the Keychain")
    check(describe(Rules.plan(active: aDefault, candidates: [m0, b],
                              [aDefault.id: sample(95, 40, fable: 50), m0.id: sample(0, 0, fable: 0), b.id: sample(50, 50, fable: 100)])) == "windows/threshold: B",
          "rules: manual only: most room left never ranks it, even with Fable to spare")

    check(describe(Rules.plan(active: mActive, candidates: [c], [mActive.id: sample(89, 40), c.id: sample(50, 50)])) == "stay",
          "rules: manual only, active: stays below the default threshold")
    check(describe(Rules.plan(active: mActive, candidates: [c], [mActive.id: sample(90, 40), c.id: sample(50, 50)])) == "windows/threshold: C",
          "rules: manual only, active: leaves at the default threshold")
    check(describe(Rules.plan(active: mActive, candidates: [c], [mActive.id: sample(72, 40), c.id: sample(50, 50)], defaultThreshold: 70)) == "windows/threshold: C"
          && describe(Rules.plan(active: mActive, candidates: [c], [mActive.id: sample(69, 40), c.id: sample(50, 50)], defaultThreshold: 70)) == "stay",
          "rules: manual only, active: follows the user's default, not 90")
    let fableUsage: [UUID: UsageAPIResponse] = [mActive.id: sample(10, 20, fable: 90), c.id: sample(5, 10, fable: 20)]
    check(describe(Rules.plan(active: mActive, candidates: [c], fableUsage)) == "fable/threshold: C"
          && describe(Rules.plan(active: mActive, candidates: [c], fableUsage, watchFable: false)) == "stay",
          "rules: manual only, active: also leaves on Fable at the default")
    let activeDrain: [UUID: UsageAPIResponse] = [mActive.id: sample(30, 40), aDefault.id: sample(30, 40), y.id: sample(10, 80, weeklyResetsIn: 12)]
    check(describe(Rules.plan(active: mActive, candidates: [y], activeDrain, strategy: .resetsSoonest, drainEarly: true)) == "stay"
          && describe(Rules.plan(active: aDefault, candidates: [y], activeDrain, strategy: .resetsSoonest, drainEarly: true)) == "windows/drainEarly: Y",
          "rules: manual only, active: early drain never moves you off it")
    check(describe(Rules.plan(active: mActive, candidates: [c], [c.id: sample(50, 50)])) == "stay"
          && describe(Rules.plan(active: mActive, candidates: [c], [mActive.id: sample(95, 95, weeklyResetsIn: nil, sessionResetsIn: nil), c.id: sample(50, 50)], sampled: false)) == "stay",
          "rules: manual only, active: no reading, no move")
    check(Rules.plan(active: mActive, candidates: [c], [mActive.id: sample(95, 40), c.id: sample(85, 85)]) == nil,
          "rules: manual only, active: stays when nobody has room")
    let pingPong = strategies.allSatisfy {
        describe(Rules.plan(active: bActive, candidates: [m0], [bActive.id: sample(95, 40), m0.id: sample(0, 0)], strategy: $0)) == "stay"
    } && describe(Rules.plan(active: bActive, candidates: [m0], [bActive.id: sample(30, 40), m0.id: sample(10, 10, weeklyResetsIn: 12)],
                             strategy: .resetsSoonest, drainEarly: true)) == "stay"
    check(pingPong, "rules: ping-pong: nothing brings you back to a Manual only account")
}

@MainActor private func lowThresholdTests() {
    let aDefault = Account(email: "a@x.com", displayName: "A", isActive: true)
    func target(_ t: Double, _ name: String = "B") -> Account { Account(email: "\(name.lowercased())@x.com", displayName: name, switchThreshold: t) }
    func describe(_ p: Rules.Plan?) -> String { Rules.describe(p) }
    func one(_ b: Account, _ u: UsageAPIResponse) -> String {
        describe(Rules.plan(active: aDefault, candidates: [b], [aDefault.id: sample(95, 40), b.id: u]))
    }
    let b20 = target(20), b10 = target(10), b1 = target(1)
    check(one(b20, sample(10, 10)) == "windows/threshold: B" && one(b20, sample(11, 5)) == "stay",
          "rules: low thresholds: a 20% candidate is eligible at 10%, not at 11%")
    check(one(b10, sample(5, 5)) == "windows/threshold: B" && one(b10, sample(6, 0)) == "stay",
          "rules: low thresholds: a 10% candidate is eligible at 5%, not at 6%")
    check(one(b1, sample(0.5, 0)) == "windows/threshold: B" && one(b1, sample(1, 0)) == "stay",
          "rules: low thresholds: a 1% candidate only at 0.5% or less")

    let a5 = Account(email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 5)
    let c = Account(email: "c@x.com", displayName: "C")
    check(describe(Rules.plan(active: a5, candidates: [c], [a5.id: sample(5, 1), c.id: sample(0, 0)])) == "windows/threshold: C"
          && describe(Rules.plan(active: a5, candidates: [c], [a5.id: sample(4, 1), c.id: sample(0, 0)])) == "stay",
          "rules: low thresholds: an active account on 5% leaves at 5% and stays at 4%")
    let bActive = Account(email: "b@x.com", displayName: "B", isActive: true)
    let a6 = target(6, "A")
    check(describe(Rules.plan(active: bActive, candidates: [a6], [bActive.id: sample(95, 40), a6.id: sample(3.5, 0)])) == "stay"
          && describe(Rules.plan(active: bActive, candidates: [a6], [bActive.id: sample(95, 40), a6.id: sample(3, 0)])) == "windows/threshold: A",
          "rules: low thresholds: an account left at 6% is a target again only at 3% or less")
    let A5 = Account(email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 5)
    let B5 = Account(email: "b@x.com", displayName: "B", switchThreshold: 5)
    let B5active = Account(id: B5.id, email: "b@x.com", displayName: "B", isActive: true, switchThreshold: 5)
    let A5idle = Account(id: A5.id, email: "a@x.com", displayName: "A", switchThreshold: 5)
    check(describe(Rules.plan(active: A5, candidates: [B5], [A5.id: sample(5, 0), B5.id: sample(2, 0)])) == "windows/threshold: B"
          && describe(Rules.plan(active: B5active, candidates: [A5idle], [B5.id: sample(5, 0), A5.id: sample(5, 0)])) == "stay",
          "rules: low thresholds: two 5% accounts never ping-pong")
    let y1 = target(1, "Y"), y10 = target(10, "Y")
    func drain(_ y: Account, _ u: UsageAPIResponse) -> String {
        describe(Rules.plan(active: aDefault, candidates: [y], [aDefault.id: sample(30, 40), y.id: u], strategy: .resetsSoonest, drainEarly: true))
    }
    check(drain(y1, sample(0.5, 0.5, weeklyResetsIn: 12)) == "windows/drainEarly: Y" && drain(y1, sample(1, 0, weeklyResetsIn: 12)) == "stay"
          && drain(y10, sample(9, 9, weeklyResetsIn: 12)) == "windows/drainEarly: Y",
          "rules: low thresholds: drain: a 1% account needs 0.5% or less; a 10% account at 9% has room")
    let x10 = target(10, "X"), yy10 = target(10, "Y")
    check(describe(Rules.plan(active: aDefault, candidates: [x10, yy10],
                              [aDefault.id: sample(95, 40, fable: 50), x10.id: sample(1, 1, fable: 4), yy10.id: sample(0, 0, fable: 6)])) == "windows/threshold: X,Y",
          "rules: low thresholds: the Fable preference uses the same halved room")
    let y90 = target(90, "Y")
    check(describe(Rules.plan(active: aDefault, candidates: [x10, y90],
                              [aDefault.id: sample(95, 40), x10.id: sample(2, 2), y90.id: sample(70, 70)])) == "windows/threshold: Y,X",
          "rules: low thresholds: most room left measures room under each account's own threshold")
}

/// The five string tables share one key set, and the Manual only strings are in place.
@MainActor private func thresholdStringsTests() {
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    let keySets = tables.map { Set($0.keys) }
    check(!keySets[0].isEmpty && keySets.allSatisfy { $0 == keySets[0] }, "l10n: the app's five string tables have the same keys",
          zip(languages, keySets).map { "\($0.0) \($0.1.count)" }.joined(separator: ", "))
    let added = [
        "Manual only (%lld%%)",
        "Manual only",
        "Auto-switch moves you off this account at %@, and moves you to it only while it is at %@ or less.",
        "Auto-switch never moves you to this account. You can still switch to it yourself; while you're on it, auto-switch moves you off it at the default threshold (%@).",
        "You're using it now. Auto-switch moves you off it at %@ and won't move you back to it.",
        "Every account except the one you're using is Manual only, so auto-switch has nowhere to move you.",
        "Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only. Manual only accounts are never switched to automatically; you can still switch to them yourself.",
        "Auto-switch never moves you to this account. You can still switch to it here.",
        "Any account can have its own threshold in Settings → Accounts, from 1% to 100%, or be Manual only so auto-switch never moves you to it. 100% uses an account until it is empty.",
        "When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold (at or under half of it, for thresholds below 20%) and is not Manual only. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping.",
    ]
    let retired = [
        "When this account's usage reaches this level, auto-switch moves you to another account.",
        "Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only.",
        "Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty.",
        "When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping.",
    ]
    let missing = added.filter { key in !tables.allSatisfy { $0[key] != nil } }
    let leftover = retired.filter { key in tables.contains { $0[key] != nil } }
    check(missing.isEmpty && leftover.isEmpty, "l10n: the Manual only strings exist in all five languages and the retired ones are gone",
          "missing \(missing.count), leftover \(leftover.count)")
}
