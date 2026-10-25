// Spec 2.3 rule 8, tested against a FROZEN copy of the 1.9 engine
// (Engine19Oracle.swift, `AutoSwitchEngine19`): when every account's session and
// weekly rules are equal and the two defaults are equal, the 2.0 two-window
// entry points (`planWindowed`, `eligibleWindowed`, with rules resolved by
// `AutoSwitchSettings.rule`) decide exactly as 1.9 did. The grid is seeded, so a
// failure reproduces. Uses the global `check`.
import Foundation

@MainActor func runEngine19OracleTests() {
    eligibilityGrid()
    planGrid()
}

/// A small deterministic generator (SplitMix64), so every run sees the same grid.
private struct SeededGenerator: RandomNumberGenerator {
    var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

private let now = ISO8601DateFormatter().date(from: "2026-10-24T12:00:00Z")!

/// The raw values the grid feeds both engines (the brief's list, plus the edges around 98).
private let switchThresholds: [Double?] = [nil, 0, 0.4, -5, 1, 3, 50, 90, 98, 100, 100.4]
private let bases: [Double?] = [nil, 0, 30, 98, 120]
private let utilizations: [Double?] = [nil, 0, 10, 49, 50, 89, 90, 97, 98, 99, 100]

private enum ResetKind: CaseIterable { case missing, past, atNow, soon, later, muchLater }

private func iso(_ seconds: TimeInterval) -> String {
    ISO8601DateFormatter().string(from: now.addingTimeInterval(seconds))
}

private func resetField(_ kind: ResetKind) -> String {
    switch kind {
    case .missing: return ""
    case .past: return #","resets_at":"\#(iso(-3600))""#
    case .atNow: return #","resets_at":"\#(iso(0))""#
    case .soon: return #","resets_at":"\#(iso(3 * 3600))""#
    case .later: return #","resets_at":"\#(iso(30 * 3600))""#
    case .muchLater: return #","resets_at":"\#(iso(100 * 3600))""#
    }
}

private struct Reading {
    var session: Double?; var sessionReset: ResetKind
    var weekly: Double?; var weeklyReset: ResetKind
    /// nil: no Fable allowance on the response.
    var fable: Double??; var fableReset: ResetKind

    func response() -> UsageAPIResponse {
        func window(_ util: Double?, _ reset: ResetKind) -> String {
            #"{"utilization":\#(util.map { String($0) } ?? "null")\#(resetField(reset))}"#
        }
        var json = #"{"five_hour":\#(window(session, sessionReset)),"seven_day":\#(window(weekly, weeklyReset))"#
        if let fable {
            json += #","limits":[{"kind":"weekly_scoped","group":"weekly","percent":\#(fable.map { String($0) } ?? "null")\#(resetField(fableReset)),"scope":{"model":{"id":null,"display_name":"Fable"}}}]"#
        }
        return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data((json + "}").utf8))
    }

    static func random(using rng: inout SeededGenerator) -> Reading {
        let hasFable = Int.random(in: 0..<10, using: &rng) < 4
        return Reading(
            session: utilizations.randomElement(using: &rng)!, sessionReset: ResetKind.allCases.randomElement(using: &rng)!,
            weekly: utilizations.randomElement(using: &rng)!, weeklyReset: ResetKind.allCases.randomElement(using: &rng)!,
            fable: hasFable ? .some(utilizations.randomElement(using: &rng)!) : nil,
            fableReset: ResetKind.allCases.randomElement(using: &rng)!)
    }
}

/// 1.9's default threshold from the raw stored base (`AutoSwitchSettings.defaultThreshold` in 1.9).
private func defaultThreshold19(_ base: Double?) -> Double {
    let stored = base ?? 0
    return stored == 0 ? 90 : AutoSwitchSettings19.clampedThreshold(stored)
}

private func describe(_ plan19: (limit: AutoSwitchEngine19.Limit, trigger: AutoSwitchEngine19.Trigger, targets: [Account])?) -> String {
    guard let plan19 else { return "stay" }
    return "\(plan19.limit.rawValue)/\(plan19.trigger.rawValue) \(plan19.targets.map(\.displayName))"
}

private func describe(_ plan: AutoSwitchEngine.WindowedPlan?) -> String {
    guard let plan else { return "stay" }
    return "\(plan.limit.rawValue)/\(plan.trigger.rawValue) \(plan.targets.map(\.displayName)) relaxed=\(plan.relaxed)"
}

private func trigger19(_ trigger: AutoSwitchEngine.Trigger) -> AutoSwitchEngine19.Trigger {
    switch trigger {
    case .threshold: return .threshold
    case .drainEarly: return .drainEarly
    case .noSubscription: return .noSubscription
    }
}

private func limit19(_ limit: AutoSwitchEngine.Limit) -> AutoSwitchEngine19.Limit {
    limit == .windows ? .windows : .fable
}

// MARK: - eligibleUtilization against eligibleWindowed

@MainActor private func eligibilityGrid() {
    var rng = SeededGenerator(seed: 0x2_0_1024)
    var compared = 0, eligible = 0
    var firstMismatch: String?
    var mismatches = 0
    let limits: [AutoSwitchEngine.Limit] = [.windows, .fable]
    let triggers: [AutoSwitchEngine.Trigger] = [.threshold, .drainEarly, .noSubscription]
    let activeWeeklyResets: [Date?] = [nil, now.addingTimeInterval(48 * 3600), now.addingTimeInterval(4 * 3600)]
    let drainWindows: [TimeInterval] = [3600, 24 * 3600, 72 * 3600]

    for own in switchThresholds {
        for base in bases {
            let account = Account(email: "x@x.com", displayName: "x", switchThreshold: own)
            let defaults = AutoSwitchSettings.windowDefaults(base: base, session: nil, weekly: nil)
            let rule = AutoSwitchSettings.rule(for: account, defaults: defaults)
            let threshold = AutoSwitchSettings19.effectiveThreshold(own: own, defaultThreshold: defaultThreshold19(base))
            for _ in 0..<40 {
                let usage = Reading.random(using: &rng).response()
                for limit in limits {
                    for trigger in triggers {
                        for activeReset in activeWeeklyResets {
                            let drain = drainWindows.randomElement(using: &rng)!
                            let watchFable = Bool.random(using: &rng)
                            let old = AutoSwitchEngine19.eligibleUtilization(
                                usage, limit: limit19(limit), trigger: trigger19(trigger), threshold: threshold,
                                hysteresisPct: AutoSwitchEngine19.hysteresis, activeWeeklyReset: activeReset,
                                drainWithin: drain, watchFable: watchFable, asOf: now)
                            let new = AutoSwitchEngine.eligibleWindowed(
                                usage, limit: limit, trigger: trigger, rule: rule, relaxed: false,
                                hysteresisPct: AutoSwitchEngine.hysteresis, activeWeeklyReset: activeReset,
                                drainWithin: drain, watchFable: watchFable, asOf: now)?.value(for: limit)
                            let compat = AutoSwitchEngine.eligibleUtilization(
                                usage, limit: limit, trigger: trigger, threshold: threshold,
                                hysteresisPct: AutoSwitchEngine.hysteresis, activeWeeklyReset: activeReset,
                                drainWithin: drain, watchFable: watchFable, asOf: now)
                            compared += 1
                            if old != nil { eligible += 1 }
                            if old != new || old != compat {
                                mismatches += 1
                                if firstMismatch == nil {
                                    firstMismatch = "own \(String(describing: own)) base \(String(describing: base)) limit \(limit) trigger \(trigger) 1.9 \(String(describing: old)) 2.0 \(String(describing: new)) compat \(String(describing: compat))"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    check(mismatches == 0, "oracle: eligibleWindowed answers exactly as 1.9's eligibleUtilization for equal windows (\(compared) comparisons)", firstMismatch ?? "")
    check(eligible > compared / 20 && eligible < compared, "oracle: the eligibility grid is not vacuous (\(eligible) of \(compared) eligible)")
}

// MARK: - plan against planWindowed

@MainActor private func planGrid() {
    var rng = SeededGenerator(seed: 0x1_9_0_0_1_9)
    let strategies = AutoSwitchStrategy.allCases
    var scenarios = 0, moved = 0, mismatches = 0
    var firstMismatch: String?
    var triggersSeen = Set<String>()
    var strategiesMoved = Set<String>()

    for base in bases {
        let defaults = AutoSwitchSettings.windowDefaults(base: base, session: nil, weekly: nil)
        let default19 = defaultThreshold19(base)
        for _ in 0..<1800 {
            scenarios += 1
            func randomAccount(_ name: String) -> Account {
                Account(email: "\(name)@x.com", displayName: name, switchThreshold: switchThresholds.randomElement(using: &rng)!)
            }
            let active = randomAccount("active")
            let candidates = ["b", "c", "d"].map(randomAccount)
            var usage: [UUID: UsageAPIResponse] = [:]
            for account in [active] + candidates where Int.random(in: 0..<12, using: &rng) != 0 {
                usage[account.id] = Reading.random(using: &rng).response()
            }
            var switchable: [UUID: Bool] = [:], unusable: [UUID: Bool] = [:]
            for account in [active] + candidates {
                switchable[account.id] = Int.random(in: 0..<5, using: &rng) != 0
                unusable[account.id] = Int.random(in: 0..<10, using: &rng) == 0
            }
            let strategy = strategies.randomElement(using: &rng)!
            let drainEarly = Bool.random(using: &rng)
            let sampled = Bool.random(using: &rng)
            let watchFable = Bool.random(using: &rng)
            let drainWithin: TimeInterval = [3600, 24 * 3600, 72 * 3600].randomElement(using: &rng)!

            let old = AutoSwitchEngine19.plan(
                active: active, candidates: candidates, usageByAccount: usage,
                isSwitchable: { switchable[$0.id] ?? false }, isUnusable: { unusable[$0.id] ?? false },
                activeSampledThisCycle: sampled,
                threshold: { AutoSwitchSettings19.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: default19) },
                defaultThreshold: default19, hysteresisPct: AutoSwitchEngine19.hysteresis,
                watchFable: watchFable, strategy: strategy, drainEarly: drainEarly, drainWithin: drainWithin, asOf: now)
            let new = AutoSwitchEngine.planWindowed(
                active: active, candidates: candidates, usageByAccount: usage,
                isSwitchable: { switchable[$0.id] ?? false }, isUnusable: { unusable[$0.id] ?? false },
                activeSampledThisCycle: sampled,
                rule: { AutoSwitchSettings.rule(for: $0, defaults: defaults) }, defaults: defaults,
                hysteresisPct: AutoSwitchEngine.hysteresis,
                watchFable: watchFable, strategy: strategy, drainEarly: drainEarly, drainWithin: drainWithin, asOf: now)

            let same = old?.limit.rawValue == new?.limit.rawValue
                && old?.trigger.rawValue == new?.trigger.rawValue
                && old?.targets.map(\.id) == new?.targets.map(\.id)
                && new?.relaxed != true
                && (old == nil) == (new == nil)
            if let old {
                moved += 1
                triggersSeen.insert(old.trigger.rawValue)
                strategiesMoved.insert(strategy.rawValue)
            }
            if !same {
                mismatches += 1
                if firstMismatch == nil {
                    firstMismatch = "base \(String(describing: base)) strategy \(strategy) drain \(drainEarly) sampled \(sampled) fable \(watchFable): 1.9 \(describe(old)) vs 2.0 \(describe(new))"
                }
            }
        }
    }
    check(mismatches == 0, "oracle: planWindowed decides exactly as 1.9's plan for equal windows, never relaxed (\(scenarios) scenarios)", firstMismatch ?? "")
    check(moved > scenarios / 10, "oracle: the plan grid is not vacuous (\(moved) of \(scenarios) scenarios moved)")
    check(triggersSeen == ["threshold", "drainEarly", "noSubscription"], "oracle: the plan grid reaches every trigger", "\(triggersSeen.sorted())")
    check(strategiesMoved == Set(strategies.map(\.rawValue)), "oracle: the plan grid moves under every strategy", "\(strategiesMoved.sorted())")
}
