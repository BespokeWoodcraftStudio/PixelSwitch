// Part A: per-account thresholds, the next-account strategy and early
// draining. Compiled into the unit harness by Tests/run-unit-tests.sh and run
// from main.swift. Uses the global `check(_:_:_:)` defined there.
import Foundation

@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
    strategyTests()
    earlyDrainTests()
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
            hysteresisPct: 10, watchFable: watchFable, strategy: strategy,
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
    check(plan(three, .mostRoom) == "windows/threshold: C,D,B", "rules: most room left ranks by lowest use", plan(three, .mostRoom))
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
