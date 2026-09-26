// Part A: per-account thresholds, the next-account strategy and early
// draining. Compiled into the unit harness by Tests/run-unit-tests.sh and run
// from main.swift. Uses the global `check(_:_:_:)` defined there.
import Foundation

@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
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
                     defaultThreshold: Double = 90, watchFable: Bool = true,
                     switchable: (Account) -> Bool = { _ in true }, sampled: Bool = true) -> Plan? {
        AutoSwitchEngine.plan(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: switchable, activeSampledThisCycle: sampled,
            threshold: { AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: defaultThreshold) },
            hysteresisPct: 10, watchFable: watchFable, asOf: now)
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
