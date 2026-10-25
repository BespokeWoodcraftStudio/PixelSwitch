import Foundation

/// How auto-switch picks the next account among the eligible ones
/// (Settings → General → Auto-switch → "Choose the next account by").
enum AutoSwitchStrategy: String, CaseIterable, Codable, Sendable {
    /// Fable room first, then the most room left under each account's own
    /// threshold (with equal thresholds, the lowest use). The default.
    case mostRoom
    /// Shown as "Priority (lowest number first)" since 2.0: the accounts list's
    /// own order (sorted by priority number), always from the top, so a higher
    /// account that has freed up again beats the next one down. The raw value
    /// is persisted and on the wire and never changes.
    case myOrder
    /// The earliest weekly reset first, so quota about to expire is used
    /// before it is lost. Ties go to most room; an unknown reset ranks last.
    case resetsSoonest
}

/// Pure, UI-agnostic auto-switch decision logic.
///
/// Mirrors the proven design in `claude-swap` (threshold + hysteresis), with
/// separate limits for the 5-hour session window and the 7-day weekly window
/// since 2.0: when the active account reaches ITS OWN limit on either window,
/// pick a same-provider account that sits at least `hysteresisPct` (one point
/// since 1.8) below ITS OWN limit on both, so two accounts hovering at the line
/// never ping-pong. An account on Manual only is never switched to. Which
/// eligible account comes first is the user's `AutoSwitchStrategy`. All
/// guardrails that need state (cooldown, re-entrancy, verification) live in
/// `AppState`; this stays a pure function.
///
/// Two limits are watched, in order: the session/weekly pair (`.windows`), then
/// the weekly Fable allowance (`.fable`), which follows the weekly limit. With
/// "Resets soonest" and early switching on, one more rule can move the user
/// before any limit is reached. When no account under its weekly limit can take
/// the user, the weekly limit rises to the session limit for the whole plan
/// (spec 2.3 rule 9). The 1.9 single-threshold `plan` and `eligibleUtilization`
/// remain for the tests that predate the split and only map onto the two-window
/// engine in the extension at the end of this file.
enum AutoSwitchEngine {

    /// A limit auto-switch watches.
    enum Limit: String, Sendable {
        /// The 5-hour session and 7-day weekly windows (the higher of the two).
        case windows
        /// The weekly Fable allowance, from the usage response's `limits` list.
        case fable
    }

    /// The model whose weekly allowance `.fable` watches, as the API names it.
    static let fableModelName = "Fable"

    /// How far below its own threshold a threshold-triggered target must sit,
    /// in percentage points (half its threshold below 2%: see `ceiling`).
    /// 10 until 1.8. Founder, 2026-09-29: "If I set the limit to 98% or 99%
    /// then it needs to switch to any account that is less than that number."
    /// Readings are whole percentages, so a point under is "less than". Two
    /// accounts still cannot ping-pong: you leave only at or over a threshold,
    /// go only under one, and the 5-minute cooldown sits between switches.
    static let hysteresis: Double = 1

    /// The highest use at which an account may be switched TO: `room` under
    /// its own threshold, or half the threshold when that is smaller, so a low
    /// threshold such as 5% still accepts an account at 2.5% instead of none.
    /// Nil for Manual only (0), a negative value or NaN: the one place that
    /// excludes an account, shared by ranking and verification.
    static func ceiling(threshold: Double, room: Double) -> Double? {
        guard threshold > AutoSwitchSettings.manualOnlyThreshold else { return nil }
        return threshold - min(room, threshold / 2)
    }

    /// How far below its own threshold an early-drain target must sit on every
    /// watched limit, in percentage points. The founder's example is an
    /// account with 5% left that should still be used up before its week
    /// resets. Equal to `hysteresis` since 1.8. Also the room a target
    /// needs when the active account has no subscription (`.noSubscription`).
    static let drainMinimumRoom: Double = 1.0

    /// The binding utilization for an account = max of the windows we watch.
    /// We watch the 5-hour (session) and 7-day (weekly-all) windows — the two
    /// that the `/api/oauth/usage` endpoint still populates as top-level fields.
    ///
    /// A window reading whose `resets_at` lies in the past is discarded: accounts
    /// are polled round-robin, so a retained sample can outlive the window it
    /// described — the quota it reports as consumed no longer exists. Within an
    /// unexpired window, consumption only grows, so an unexpired reading is a
    /// valid lower bound even when it is several cycles old.
    ///
    /// `requireKnownWindow` controls what happens when `resets_at` is absent or
    /// unparseable (API omission, format change). Lenient (default) keeps the
    /// reading — right for candidates, where a stale-high reading merely
    /// excludes them and anything chosen is re-verified with a fresh fetch.
    /// Strict discards it — required for the ACTIVE side, where a retained
    /// high reading with no known expiry could otherwise TRIGGER a switch
    /// long after the real window reset.
    ///
    /// Returns nil when no usable reading exists for the account.
    static func bindingUtilization(
        _ usage: UsageAPIResponse?,
        asOf now: Date = Date(),
        requireKnownWindow: Bool = false
    ) -> Double? {
        guard let usage else { return nil }
        return [usage.fiveHour, usage.sevenDay]
            .compactMap { reading($0, asOf: now, requireKnownWindow: requireKnownWindow) }
            .max()
    }

    /// The utilization for `limit`, under the same expiry rules as
    /// `bindingUtilization`. Nil when there is no usable reading, which for
    /// `.fable` includes an account with no separate Fable allowance.
    static func utilization(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        asOf now: Date = Date(),
        requireKnownWindow: Bool = false
    ) -> Double? {
        switch limit {
        case .windows:
            return bindingUtilization(usage, asOf: now, requireKnownWindow: requireKnownWindow)
        case .fable:
            guard let window = usage?.modelWeeklyLimit(named: fableModelName)?.window else { return nil }
            return reading(window, asOf: now, requireKnownWindow: requireKnownWindow)
        }
    }

    /// A candidate's utilization on `limit` when it may be switched to for that
    /// limit, else nil. A switch made for Fable must also leave room on the
    /// session and weekly windows, or the next refresh would move the user
    /// straight off the account it just chose.
    static func eligibleUtilization(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        ceiling: Double,
        asOf now: Date = Date()
    ) -> Double? {
        guard let util = utilization(usage, limit: limit, asOf: now), util <= ceiling else { return nil }
        if limit == .fable {
            guard let windows = bindingUtilization(usage, asOf: now), windows <= ceiling else { return nil }
        }
        return util
    }

    /// The weekly reset "Resets soonest" ranks by: the 7-day window for
    /// `.windows`, the Fable weekly allowance for `.fable`. Never the 5-hour
    /// window, which resets every few hours and would make the choice
    /// meaningless.
    ///
    /// Nil unless that window has a reading AND a reset time still in the
    /// future. A reset in the past means the sample describes a week that has
    /// already ended (round-robin polling keeps old samples), so when the NEXT
    /// week ends is unknown.
    static func weeklyReset(_ usage: UsageAPIResponse?, limit: Limit, asOf now: Date = Date()) -> Date? {
        let window: UsageWindow?
        switch limit {
        case .windows: window = usage?.sevenDay
        case .fable: window = usage?.modelWeeklyLimit(named: fableModelName)?.window
        }
        guard let window, window.utilization != nil,
              let resets = window.resetsAtDate, resets > now else { return nil }
        return resets
    }

    /// The one rule that decides whether a candidate may be switched to, for
    /// the rule that fired, with ONE threshold per account (the 1.9 shape).
    ///
    /// COMPATIBILITY OVERLOAD, for tests only: the single value is mapped to
    /// equal session and weekly rules and handed to `eligibleWindowed`. No
    /// non-test code may call it (`AppState` calls `eligibleWindowed`).
    ///
    /// - `threshold` is the CANDIDATE's own effective threshold; 0 (Manual
    ///   only) is never eligible, by any rule.
    static func eligibleUtilization(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        trigger: Trigger,
        threshold: Double,
        hysteresisPct: Double,
        activeWeeklyReset: Date?,
        drainWithin: TimeInterval,
        watchFable: Bool,
        asOf now: Date = Date()
    ) -> Double? {
        eligibleWindowed(usage, limit: limit, trigger: trigger, rule: .both(threshold), relaxed: false,
                         hysteresisPct: hysteresisPct, activeWeeklyReset: activeWeeklyReset,
                         drainWithin: drainWithin, watchFable: watchFable, asOf: now)?.value(for: limit)
    }

    /// Whether the cooldown still holds auto-switch back: less than `cooldown`
    /// since the last automatic switch. Never for an active account with no
    /// subscription: the cooldown stops two accounts trading places, and
    /// nothing moves you back to an account that cannot be used.
    static func cooldownHolds(lastSwitchAt: Date?, cooldown: TimeInterval, activeUnusable: Bool, asOf now: Date = Date()) -> Bool {
        guard !activeUnusable, let lastSwitchAt else { return false }
        return now.timeIntervalSince(lastSwitchAt) < cooldown
    }

    /// One window's utilization, or nil if it has none or its window has reset.
    private static func reading(_ window: UsageWindow?, asOf now: Date, requireKnownWindow: Bool) -> Double? {
        guard let window, let util = window.utilization else { return nil }
        guard let resets = window.resetsAtDate else {
            return requireKnownWindow ? nil : util
        }
        return resets < now ? nil : util
    }

    /// Which limit to act on, by which rule, and where to go; nil to stay put,
    /// with ONE threshold per account (the 1.9 shape).
    ///
    /// COMPATIBILITY OVERLOAD, for tests only: every account's single value
    /// becomes equal session and weekly rules and the call goes to
    /// `planWindowed`. With equal rules the weekly-overflow pass changes
    /// nothing, so the answer is exactly 1.9's (spec 2.3 rule 8). No non-test
    /// code may call it.
    ///
    /// - `threshold`: each account's rule value (0 = Manual only).
    /// - `defaultThreshold`: the global default (50–100): where an active
    ///   Manual only account is left.
    static func plan(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        isUnusable: (Account) -> Bool = { _ in false },
        activeSampledThisCycle: Bool,
        threshold: (Account) -> Double,
        defaultThreshold: Double,
        hysteresisPct: Double,
        watchFable: Bool = true,
        strategy: AutoSwitchStrategy = .mostRoom,
        drainEarly: Bool = false,
        drainWithin: TimeInterval = 24 * 3600,
        asOf now: Date = Date()
    ) -> (limit: Limit, trigger: Trigger, targets: [Account])? {
        let plan = planWindowed(
            active: active, candidates: candidates, usageByAccount: usageByAccount,
            isSwitchable: isSwitchable, isUnusable: isUnusable, activeSampledThisCycle: activeSampledThisCycle,
            rule: { .both(threshold($0)) },
            defaults: WindowPair(both: AutoSwitchSettings.clampedThreshold(defaultThreshold)),
            hysteresisPct: hysteresisPct, watchFable: watchFable, strategy: strategy,
            drainEarly: drainEarly, drainWithin: drainWithin, asOf: now)
        return plan.map { ($0.limit, $0.trigger, $0.targets) }
    }
}

// MARK: - Two windows (2.0)

/// The 2.0 engine: the session (5-hour) and weekly (7-day) windows have their
/// own limits. Spec `docs/superpowers/specs/2026-10-24-2.0-spec.md` section 2.3
/// is the contract and `Tests/conformance/autoswitch-vectors.json` its
/// executable form. Everything the app calls lives here; the single-threshold
/// functions above only map onto it, for the tests that predate it.
extension AutoSwitchEngine {

    /// One of the two windows a limit applies to.
    enum Window: String, Sendable, CaseIterable { case session, weekly }

    /// A session value and a weekly value: the global defaults, or where an
    /// account is left.
    struct WindowPair: Equatable, Sendable {
        var session: Double
        var weekly: Double

        init(session: Double, weekly: Double) {
            self.session = session
            self.weekly = weekly
        }

        init(both value: Double) {
            self.init(session: value, weekly: value)
        }

        /// The weekly limit raised to the session limit (rule 9).
        var relaxed: WindowPair { WindowPair(session: session, weekly: max(weekly, session)) }

        func value(for window: Window) -> Double {
            window == .session ? session : weekly
        }
    }

    /// One account's resolved auto-switch rule: where each window leaves and
    /// arrives, or Manual only (both 0). Built by `AutoSwitchSettings.rule`.
    struct WindowRule: Equatable, Sendable {
        var session: Double
        var weekly: Double
        var manualOnly: Bool

        init(session: Double, weekly: Double, manualOnly: Bool) {
            self.session = session
            self.weekly = weekly
            self.manualOnly = manualOnly
        }

        static let manualOnlyRule = WindowRule(session: 0, weekly: 0, manualOnly: true)

        /// One value for both windows (0 or below = Manual only): how the 1.9
        /// single threshold maps onto the two-window engine.
        static func both(_ threshold: Double) -> WindowRule {
            threshold > AutoSwitchSettings.manualOnlyThreshold
                ? WindowRule(session: threshold, weekly: threshold, manualOnly: false)
                : manualOnlyRule
        }

        /// Rule 9: the weekly rule raised to the session rule. Manual only stays Manual only.
        var relaxed: WindowRule {
            manualOnly ? self : WindowRule(session: session, weekly: max(weekly, session), manualOnly: false)
        }
    }

    /// What a candidate's reading was judged on.
    struct WindowReadings: Equatable, Sendable {
        var session: Double?
        var weekly: Double?
        var fable: Double?

        /// The higher of the two windows, nil with no reading.
        var binding: Double? { [session, weekly].compactMap { $0 }.max() }

        /// The reading on `limit`: the higher window for `.windows`, Fable for `.fable`.
        func value(for limit: Limit) -> Double? {
            limit == .windows ? binding : fable
        }
    }

    /// A decision to switch: which limit, which rule, which window fired (only
    /// for a threshold on `.windows`), whether it was made by the weekly
    /// overflow pass, and the eligible candidates best first.
    struct WindowedPlan {
        let limit: Limit
        let trigger: Trigger
        let window: Window?
        let relaxed: Bool
        let targets: [Account]
    }

    /// Whether the active account has reached a window limit: `session` first.
    /// `leaveAt` is where it leaves (`AutoSwitchSettings.leaveAtPair`). A
    /// missing window cannot fire. With `requireKnownWindow` a reading that
    /// has no `resets_at` is dropped (a retained sample, rule 1).
    static func firedWindow(_ usage: UsageAPIResponse?, leaveAt: WindowPair, asOf now: Date = Date(),
                            requireKnownWindow: Bool = false) -> Window? {
        guard let usage else { return nil }
        if let s = reading(usage.fiveHour, asOf: now, requireKnownWindow: requireKnownWindow), s >= leaveAt.session { return .session }
        if let w = reading(usage.sevenDay, asOf: now, requireKnownWindow: requireKnownWindow), w >= leaveAt.weekly { return .weekly }
        return nil
    }

    /// One window's reading under the engine's expiry rules (nil with none): for
    /// the decision log and the staying-put note, so they show the number the
    /// engine judged.
    static func windowUtilization(_ usage: UsageAPIResponse?, window: Window, asOf now: Date = Date(),
                                  requireKnownWindow: Bool = false) -> Double? {
        guard let usage else { return nil }
        return reading(window == .session ? usage.fiveHour : usage.sevenDay, asOf: now, requireKnownWindow: requireKnownWindow)
    }

    /// Whether the active account's Fable use has reached its limit (the weekly one).
    static func fableReached(_ usage: UsageAPIResponse?, leaveAt: WindowPair, asOf now: Date = Date(),
                             requireKnownWindow: Bool = false) -> Bool {
        guard let fable = utilization(usage, limit: .fable, asOf: now, requireKnownWindow: requireKnownWindow) else { return false }
        return fable >= leaveAt.weekly
    }

    /// Rule 9: the weekly overflow pass may run only when the active account
    /// has no subscription, or has reached its STRICT limit on the windows. A
    /// Fable-only trigger does not count, and an account still under both of
    /// its strict window limits never does (a drain or Fable move from it
    /// would land on an account past its weekly limit and rule 1 would bring
    /// you back).
    static func overflowHolds(activeUnusable: Bool, activeUsage: UsageAPIResponse?, activeRule: WindowRule,
                              defaults: WindowPair, activeSampledThisCycle: Bool, asOf now: Date = Date()) -> Bool {
        if activeUnusable { return true }
        let leave = AutoSwitchSettings.leaveAtPair(rule: activeRule, defaults: defaults)
        return firedWindow(activeUsage, leaveAt: leave, asOf: now, requireKnownWindow: !activeSampledThisCycle) != nil
    }

    /// What the "staying put" log line should say about an active account that
    /// has reached a limit while no candidate was chosen.
    enum StayingPut: Equatable, Sendable {
        /// The active account has not reached a limit on a watched window.
        case notAtLimit
        /// It is at a limit and no account has room under its own limits (the
        /// 1.9 sentence). With equal windows this is always the answer once a
        /// limit is reached.
        case noRoom
        /// It reached its STRICT limit but is still under its RELAXED limits
        /// (weekly raised to the session limit), and even the relaxed plan found
        /// nobody: it stays until the session limit (rule 10).
        case waitingForSessionLimit
    }

    /// Rule 10's wording choice, as one pure function: the overflow sentence
    /// applies only when overflow holds AND the active account is still under
    /// its relaxed limits. Past them (always the case with equal windows, where
    /// relaxed equals strict) the account is not "staying until the session
    /// limit": it already passed it, and the true reason is that nobody has room.
    static func stayingPutKind(activeUsage: UsageAPIResponse?, activeRule: WindowRule, defaults: WindowPair,
                               activeSampledThisCycle: Bool, watchFable: Bool, asOf now: Date = Date()) -> StayingPut {
        let strict = AutoSwitchSettings.leaveAtPair(rule: activeRule, defaults: defaults)
        let strictFire = firedWindow(activeUsage, leaveAt: strict, asOf: now, requireKnownWindow: !activeSampledThisCycle) != nil
        let fableFire = watchFable
            && fableReached(activeUsage, leaveAt: strict, asOf: now, requireKnownWindow: !activeSampledThisCycle)
        guard strictFire || fableFire else { return .notAtLimit }
        // A Fable-only trigger never makes overflow hold.
        guard strictFire else { return .noRoom }
        let relaxed = AutoSwitchSettings.leaveAtPair(rule: activeRule.relaxed, defaults: defaults.relaxed)
        let relaxedFire = firedWindow(activeUsage, leaveAt: relaxed, asOf: now, requireKnownWindow: !activeSampledThisCycle) != nil
            || (watchFable && fableReached(activeUsage, leaveAt: relaxed, asOf: now, requireKnownWindow: !activeSampledThisCycle))
        return relaxedFire ? .noRoom : .waitingForSessionLimit
    }

    /// A candidate's readings when it under both ceilings, else nil. Needs at
    /// least one usable window reading; judges every usable one (rule 3).
    private static func readingsUnder(_ usage: UsageAPIResponse?, session: Double, weekly: Double, asOf now: Date) -> WindowReadings? {
        guard let usage else { return nil }
        let s = reading(usage.fiveHour, asOf: now, requireKnownWindow: false)
        let w = reading(usage.sevenDay, asOf: now, requireKnownWindow: false)
        guard s != nil || w != nil else { return nil }
        if let s, s > session { return nil }
        if let w, w > weekly { return nil }
        return WindowReadings(session: s, weekly: w, fable: nil)
    }

    /// The one rule that decides whether a candidate may be switched to, for
    /// the rule that fired. `planWindowed` ranks with it and `AppState`
    /// verifies the chosen account's fresh reading with it, in the mode
    /// (strict or `relaxed`) the plan was made in, so ranking and verification
    /// cannot disagree. Returns what the candidate was judged on, nil if not eligible.
    ///
    /// - `rule` is the CANDIDATE's own resolved rule (before `relaxed` is applied).
    /// - `.threshold`: session at or under `ceiling(rule.session)` and weekly at
    ///   or under `ceiling(rule.weekly)`, every usable window judged, at least
    ///   one needed; a Fable switch also needs Fable at or under the weekly ceiling.
    /// - `.drainEarly`: `.windows` only, needs the active account's weekly
    ///   reset; the candidate's weekly reset must be known, strictly earlier and
    ///   within `drainWithin`; the same ceilings with `drainMinimumRoom`; and
    ///   with Fable switching on, Fable under the weekly ceiling too.
    /// - `.noSubscription`: `.windows` only, ceilings with `drainMinimumRoom`.
    /// Manual only is never eligible, by any rule.
    static func eligibleWindowed(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        trigger: Trigger,
        rule: WindowRule,
        relaxed: Bool = false,
        hysteresisPct: Double,
        activeWeeklyReset: Date?,
        drainWithin: TimeInterval,
        watchFable: Bool,
        asOf now: Date = Date()
    ) -> WindowReadings? {
        let r = relaxed ? rule.relaxed : rule
        switch trigger {
        case .threshold:
            guard let cs = ceiling(threshold: r.session, room: hysteresisPct),
                  let cw = ceiling(threshold: r.weekly, room: hysteresisPct),
                  var readings = readingsUnder(usage, session: cs, weekly: cw, asOf: now) else { return nil }
            if limit == .fable {
                guard let fable = utilization(usage, limit: .fable, asOf: now), fable <= cw else { return nil }
                readings.fable = fable
            }
            return readings
        case .drainEarly:
            guard limit == .windows, let activeWeeklyReset,
                  let cs = ceiling(threshold: r.session, room: drainMinimumRoom),
                  let cw = ceiling(threshold: r.weekly, room: drainMinimumRoom),
                  let resets = weeklyReset(usage, limit: .windows, asOf: now),
                  resets < activeWeeklyReset,
                  resets.timeIntervalSince(now) <= drainWithin,
                  var readings = readingsUnder(usage, session: cs, weekly: cw, asOf: now) else { return nil }
            if watchFable, let fable = utilization(usage, limit: .fable, asOf: now) {
                if fable > cw { return nil }
                readings.fable = fable
            }
            return readings
        case .noSubscription:
            guard limit == .windows,
                  let cs = ceiling(threshold: r.session, room: drainMinimumRoom),
                  let cw = ceiling(threshold: r.weekly, room: drainMinimumRoom) else { return nil }
            return readingsUnder(usage, session: cs, weekly: cw, asOf: now)
        }
    }

    /// Which limit to act on, by which rule, and where to go; nil to stay put.
    /// This is rule 9: the strict plan, else (only when the weekly overflow
    /// holds) the relaxed plan.
    ///
    /// 0. No subscription: an active account that cannot be used at all is
    ///    left at once, whatever its rule.
    /// 1. Threshold: the windows first (session, then weekly), then Fable
    ///    (only when `watchFable`), against the ACTIVE account's leave-at pair.
    /// 2. Early drain: only when no threshold was reached at all, the active
    ///    account is not Manual only, the strategy is `.resetsSoonest` and
    ///    `drainEarly` is on.
    ///
    /// - Parameters:
    ///   - candidates: every other account (same provider) IN PRIORITY ORDER:
    ///     `.myOrder` ranks by it and it breaks every tie.
    ///   - rule: each account's resolved rule (`AutoSwitchSettings.rule`).
    ///   - defaults: the global session and weekly defaults (where an active
    ///     Manual only account is left).
    ///   - activeSampledThisCycle: whether the active account's sample was taken
    ///     by the cycle that is asking; only RETAINED readings need the strict
    ///     expiry check.
    static func planWindowed(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        isUnusable: (Account) -> Bool = { _ in false },
        activeSampledThisCycle: Bool,
        rule: (Account) -> WindowRule,
        defaults: WindowPair,
        hysteresisPct: Double,
        watchFable: Bool = true,
        strategy: AutoSwitchStrategy = .mostRoom,
        drainEarly: Bool = false,
        drainWithin: TimeInterval = 24 * 3600,
        asOf now: Date = Date()
    ) -> WindowedPlan? {
        func make(relaxed: Bool) -> WindowedPlan? {
            windowPass(active: active, candidates: candidates, usageByAccount: usageByAccount,
                       isSwitchable: isSwitchable, isUnusable: isUnusable, activeSampledThisCycle: activeSampledThisCycle,
                       rule: rule, defaults: defaults, relaxed: relaxed, hysteresisPct: hysteresisPct,
                       watchFable: watchFable, strategy: strategy, drainEarly: drainEarly,
                       drainWithin: drainWithin, asOf: now)
        }
        if let strict = make(relaxed: false) { return strict }
        guard overflowHolds(activeUnusable: isUnusable(active), activeUsage: usageByAccount[active.id],
                            activeRule: rule(active), defaults: defaults,
                            activeSampledThisCycle: activeSampledThisCycle, asOf: now) else { return nil }
        return make(relaxed: true)
    }

    /// One pass of the plan, strict or relaxed.
    private static func windowPass(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        isUnusable: (Account) -> Bool,
        activeSampledThisCycle: Bool,
        rule: (Account) -> WindowRule,
        defaults: WindowPair,
        relaxed: Bool,
        hysteresisPct: Double,
        watchFable: Bool,
        strategy: AutoSwitchStrategy,
        drainEarly: Bool,
        drainWithin: TimeInterval,
        asOf now: Date
    ) -> WindowedPlan? {
        if isUnusable(active) {
            let targets = rankedTargetsWindowed(
                active: active, candidates: candidates, usageByAccount: usageByAccount,
                isSwitchable: isSwitchable, isUnusable: isUnusable, rule: rule, relaxed: relaxed,
                hysteresisPct: hysteresisPct, limit: .windows, trigger: .noSubscription, strategy: strategy,
                activeWeeklyReset: nil, drainWithin: drainWithin, watchFable: watchFable, asOf: now)
            return targets.isEmpty ? nil : WindowedPlan(limit: .windows, trigger: .noSubscription, window: nil, relaxed: relaxed, targets: targets)
        }
        let activeRule = relaxed ? rule(active).relaxed : rule(active)
        let leave = AutoSwitchSettings.leaveAtPair(rule: activeRule, defaults: relaxed ? defaults.relaxed : defaults)
        var thresholdReached = false
        for limit in (watchFable ? [Limit.windows, .fable] : [Limit.windows]) {
            // Only act once the active account has reached its limit on a limit
            // we watch. The trigger must never rest on a RETAINED reading whose
            // expiry we cannot establish, but a reading this very cycle fetched
            // is current whether or not the endpoint sent a parseable resets_at.
            var fired: Window?
            switch limit {
            case .windows:
                fired = firedWindow(usageByAccount[active.id], leaveAt: leave, asOf: now, requireKnownWindow: !activeSampledThisCycle)
                guard fired != nil else { continue }
            case .fable:
                guard fableReached(usageByAccount[active.id], leaveAt: leave, asOf: now, requireKnownWindow: !activeSampledThisCycle) else { continue }
            }
            thresholdReached = true
            let targets = rankedTargetsWindowed(
                active: active, candidates: candidates, usageByAccount: usageByAccount,
                isSwitchable: isSwitchable, isUnusable: isUnusable, rule: rule, relaxed: relaxed,
                hysteresisPct: hysteresisPct, limit: limit, trigger: .threshold, strategy: strategy,
                activeWeeklyReset: nil, drainWithin: drainWithin, watchFable: watchFable, asOf: now)
            if !targets.isEmpty {
                return WindowedPlan(limit: limit, trigger: .threshold, window: fired, relaxed: relaxed, targets: targets)
            }
        }

        guard !thresholdReached, !activeRule.manualOnly, strategy == .resetsSoonest, drainEarly,
              let activeWeeklyReset = weeklyReset(usageByAccount[active.id], limit: .windows, asOf: now) else {
            return nil
        }
        let targets = rankedTargetsWindowed(
            active: active, candidates: candidates, usageByAccount: usageByAccount,
            isSwitchable: isSwitchable, isUnusable: isUnusable, rule: rule, relaxed: relaxed,
            hysteresisPct: hysteresisPct, limit: .windows, trigger: .drainEarly, strategy: strategy,
            activeWeeklyReset: activeWeeklyReset, drainWithin: drainWithin, watchFable: watchFable, asOf: now)
        return targets.isEmpty ? nil : WindowedPlan(limit: .windows, trigger: .drainEarly, window: nil, relaxed: relaxed, targets: targets)
    }

    /// Rank the accounts worth switching to for one limit and rule, best first.
    ///
    /// The result is a list of *proposals*, not decisions: it is computed from
    /// whatever samples the caller happens to hold, which round-robin polling can
    /// leave several cycles old. `AppState` verifies a candidate's usage before
    /// committing to a switch, and falls through the list when one fails.
    ///
    /// An account with no active subscription (`isUnusable`) is never
    /// eligible, whatever reading it still holds. A candidate with no usable
    /// reading is NOT eligible: accounts are polled round-robin, so "no
    /// sample" usually means "not reached yet" rather than "idle".
    static func rankedTargetsWindowed(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        isUnusable: (Account) -> Bool = { _ in false },
        rule: (Account) -> WindowRule,
        relaxed: Bool = false,
        hysteresisPct: Double,
        limit: Limit,
        trigger: Trigger,
        strategy: AutoSwitchStrategy,
        activeWeeklyReset: Date?,
        drainWithin: TimeInterval,
        watchFable: Bool,
        asOf now: Date = Date()
    ) -> [Account] {
        struct Ranked {
            let account: Account
            /// Position in `candidates`, i.e. in the priority order.
            let position: Int
            /// Points left under this account's own limits: what "most room"
            /// means (founder, 2026-09-25). The smaller of the two windows'
            /// room (rule 6), or the weekly room on Fable.
            let room: Double
            let keepsFable: Bool
            /// The weekly reset in whole minutes since 1970, or nil when unknown.
            /// Whole minutes because the API stamps each reading with the
            /// fetch's own microseconds, so two accounts on the same weekly
            /// schedule never compare exactly equal.
            let resetMinute: Double?
        }

        let eligible = candidates.enumerated().compactMap { position, candidate -> Ranked? in
            let usage = usageByAccount[candidate.id]
            let own = rule(candidate)
            // The usage check comes first: `isSwitchable` reads the Keychain.
            guard candidate.id != active.id, !isUnusable(candidate),
                  let readings = eligibleWindowed(usage, limit: limit, trigger: trigger, rule: own, relaxed: relaxed,
                                                  hysteresisPct: hysteresisPct, activeWeeklyReset: activeWeeklyReset,
                                                  drainWithin: drainWithin, watchFable: watchFable, asOf: now),
                  isSwitchable(candidate) else { return nil }
            let r = relaxed ? own.relaxed : own
            let room: Double
            switch limit {
            case .windows:
                room = min(readings.session.map { r.session - $0 } ?? .infinity, readings.weekly.map { r.weekly - $0 } ?? .infinity)
            case .fable:
                room = r.weekly - (readings.fable ?? 0)
            }
            let keepsFable = utilization(usage, limit: .fable, asOf: now)
                .flatMap { fable in Self.ceiling(threshold: r.weekly, room: hysteresisPct).map { fable <= $0 } } ?? false
            let resetMinute = weeklyReset(usage, limit: limit, asOf: now)
                .map { ($0.timeIntervalSince1970 / 60).rounded(.down) }
            return Ranked(account: candidate, position: position, room: room, keepsFable: keepsFable, resetMinute: resetMinute)
        }

        let ordered: [Ranked]
        switch strategy {
        case .mostRoom:
            // Accounts with Fable to spare first, so a session/weekly switch
            // does not land on an account that is out of Fable and force a
            // second switch minutes later; then most room under each account's
            // own limits; then the priority order, so a tie is stable.
            ordered = eligible.sorted {
                ($0.keepsFable ? 0 : 1, -$0.room, $0.position) < ($1.keepsFable ? 0 : 1, -$1.room, $1.position)
            }
        case .myOrder:
            // Already in priority order: always from the top.
            ordered = eligible
        case .resetsSoonest:
            ordered = eligible.sorted { a, b in
                switch (a.resetMinute, b.resetMinute) {
                case let (x?, y?) where x != y: return x < y
                case (.some, nil): return true
                case (nil, .some): return false
                default: return (-a.room, a.position) < (-b.room, b.position)
                }
            }
        }
        return ordered.map(\.account)
    }
}

extension AutoSwitchEngine {
    /// The rule that produced a plan: the active account reached its
    /// threshold, (Resets soonest only) another account's weekly quota is
    /// about to reset unused, or the active account has no active
    /// subscription and cannot be used at all. (1.7's `exhausted`, a fallback
    /// at 100% for the old 10-point room, went in 1.8: with one point of room
    /// the threshold rule already takes every account it could.)
    enum Trigger: String, Sendable { case threshold, drainEarly, noSubscription }
}

/// Whether auto-switch also acts on the weekly Fable allowance. On by default;
/// off leaves Fable as a reading only, and session and weekly switching alone.
enum AutoSwitchFableSetting {
    static let key = "autoSwitchOnFable"

    static var isOn: Bool {
        UserDefaults.standard.object(forKey: key) as? Bool ?? true
    }
}

/// The auto-switch settings, their UserDefaults keys, defaults and ranges.
/// The Settings window binds to the keys with `@AppStorage`; `AppState` and
/// (later) the control API read them through the typed readers here, so the
/// defaults live in one place.
enum AutoSwitchSettings {
    static let enabledKey = "autoSwitchEnabled"
    static let thresholdKey = "autoSwitchThreshold"
    /// 2.0: the global session (5-hour) and weekly (7-day) defaults. Each counts
    /// as set only while it is present, a finite number and not 0; otherwise it
    /// follows `thresholdKey` (spec 2.1). Never written by an older build.
    static let sessionThresholdKey = "autoSwitchSessionThreshold"
    static let weeklyThresholdKey = "autoSwitchWeeklyThreshold"
    static let onFableKey = AutoSwitchFableSetting.key
    static let strategyKey = "autoSwitchStrategy"
    static let drainEarlyKey = "autoSwitchDrainEarly"
    static let drainWithinHoursKey = "autoSwitchDrainWithinHours"

    /// The global DEFAULT threshold only (Settings → General, settings key
    /// autoSwitch.threshold). Never below 50: a 0% default would stop every
    /// account being used.
    static let thresholdRange = 50.0...100.0
    /// One account's own threshold; 0 = Manual only (founder, 2026-09-26).
    static let accountThresholdRange = 0.0...100.0
    /// The Settings stepper: Manual only is chosen by name, never by stepping
    /// down from 1%.
    static let accountStepperRange = 1.0...100.0
    /// An account's own threshold that means Manual only: auto-switch never
    /// switches to it; while it is active it is left at the default.
    static let manualOnlyThreshold = 0.0
    /// How soon a weekly reset must be for an early drain, in hours.
    static let drainHoursRange = 1.0...72.0

    /// The default threshold when none has been saved.
    static let fallbackThreshold = 90.0
    /// The drain window when none has been saved.
    static let fallbackDrainWithinHours = 24.0

    /// The global default threshold (Settings → General → Default threshold).
    /// Unset (read as 0) means 90, as before; anything else is kept in range.
    static var defaultThreshold: Double {
        let stored = UserDefaults.standard.double(forKey: thresholdKey)
        return stored == 0 ? fallbackThreshold : clampedThreshold(stored)
    }

    /// A global window default as stored, or nil while it is not set: absent,
    /// not a number, not finite or 0 (spec 2.1). `object(forKey:)`, never
    /// `double(forKey:)`, which reads 0 for a missing key.
    static func storedThreshold(forKey key: String, in defaults: UserDefaults = .standard) -> Double? {
        guard let value = defaults.object(forKey: key) as? Double, value.isFinite, value != 0 else { return nil }
        return value
    }

    /// The effective global session default: its own key if set, else the base. 50–100.
    static var defaultSessionThreshold: Double { defaultPair.session }

    /// The effective global weekly default: its own key if set, else the base. 50–100.
    static var defaultWeeklyThreshold: Double { defaultPair.weekly }

    /// Both effective global defaults, read from `UserDefaults.standard`.
    static var defaultPair: AutoSwitchEngine.WindowPair {
        windowDefaults(base: storedThreshold(forKey: thresholdKey),
                       session: storedThreshold(forKey: sessionThresholdKey),
                       weekly: storedThreshold(forKey: weeklyThresholdKey))
    }

    /// The next-account strategy. Unset or unrecognised reads as `.mostRoom`.
    static var strategy: AutoSwitchStrategy {
        UserDefaults.standard.string(forKey: strategyKey).flatMap(AutoSwitchStrategy.init(rawValue:)) ?? .mostRoom
    }

    /// "Switch early to use quota before it resets". On unless turned off.
    static var drainEarly: Bool {
        UserDefaults.standard.object(forKey: drainEarlyKey) as? Bool ?? true
    }

    /// The early-drain window in hours, kept in 1–72. Unset reads as 24.
    static var drainWithinHours: Double {
        guard let stored = UserDefaults.standard.object(forKey: drainWithinHoursKey) as? Double, stored.isFinite else {
            return fallbackDrainWithinHours
        }
        return min(max(stored, drainHoursRange.lowerBound), drainHoursRange.upperBound)
    }

    /// `value` kept within 50–100; a value that is not a number reads as 90.
    static func clampedThreshold(_ value: Double) -> Double {
        guard value.isFinite else { return fallbackThreshold }
        return min(max(value, thresholdRange.lowerBound), thresholdRange.upperBound)
    }

    /// What a per-account threshold is stored as: nil (use the default) for
    /// nil or a value that is not a number, else a whole number within 0–100
    /// (0 = Manual only; never -0).
    static func normalizedAccountThreshold(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        let whole = value.rounded()
        return whole <= accountThresholdRange.lowerBound ? accountThresholdRange.lowerBound : min(whole, accountThresholdRange.upperBound)
    }

    /// Whether an account's own threshold makes it Manual only.
    static func isManualOnly(own: Double?) -> Bool {
        normalizedAccountThreshold(own) == manualOnlyThreshold
    }

    /// The rule value auto-switch uses for an account: its own 0–100 when set
    /// (0 = Manual only), else the default 50–100. The engine reads 0; use
    /// `leaveAtThreshold` for where an active account is left.
    static func effectiveThreshold(own: Double?, defaultThreshold: Double) -> Double {
        normalizedAccountThreshold(own) ?? clampedThreshold(defaultThreshold)
    }

    /// Where auto-switch moves the user OFF an account while it is active:
    /// the rule value, or the default for Manual only.
    static func leaveAtThreshold(rule: Double, defaultThreshold: Double) -> Double {
        rule > manualOnlyThreshold ? rule : clampedThreshold(defaultThreshold)
    }

    /// The numbers Settings → Accounts states for an account, from the
    /// engine's own rules: where auto-switch leaves it, the highest use at
    /// which it moves you to it (threshold rule), and, with Resets soonest and
    /// early switching on, the higher ceiling the early switch allows. Nil for
    /// Manual only.
    static func explanation(own: Double?, defaultThreshold: Double, strategy: AutoSwitchStrategy,
                            drainEarly: Bool) -> (leaveAt: Double, arriveAt: Double, drainArriveAt: Double?)? {
        let rule = effectiveThreshold(own: own, defaultThreshold: defaultThreshold)
        guard let arriveAt = AutoSwitchEngine.ceiling(threshold: rule, room: AutoSwitchEngine.hysteresis) else { return nil }
        // Only stated when it says something: since 1.8 both rules need the
        // same point of room, so the early-switch number equals `arriveAt`.
        let drain = strategy == .resetsSoonest && drainEarly
            ? AutoSwitchEngine.ceiling(threshold: rule, room: AutoSwitchEngine.drainMinimumRoom).flatMap { $0 > arriveAt ? $0 : nil } : nil
        return (rule, arriveAt, drain)
    }

    // MARK: Two windows (2.0, spec 2.1 and 2.2)

    /// The base default: `autoSwitchThreshold` as stored. Unset or 0 means 90,
    /// anything else is kept in 50–100.
    static func baseThreshold(stored: Double?) -> Double {
        guard let stored, stored != 0 else { return fallbackThreshold }
        return clampedThreshold(stored)
    }

    /// The two global defaults from the RAW stored values (spec 2.2). A window
    /// key counts as set only when it is a finite number and not 0; otherwise
    /// it follows the base. Each result is kept in 50–100.
    static func windowDefaults(base: Double?, session: Double?, weekly: Double?) -> AutoSwitchEngine.WindowPair {
        let baseValue = baseThreshold(stored: base)
        func pick(_ stored: Double?) -> Double {
            guard let stored, stored.isFinite, stored != 0 else { return baseValue }
            return clampedThreshold(stored)
        }
        return AutoSwitchEngine.WindowPair(session: pick(session), weekly: pick(weekly))
    }

    /// What one window's own value is stored as: nil (follow the next layer)
    /// for nil, a value that is not a number, or one that rounds to 0 or
    /// below; else a whole number up to 100 (rounded half away from zero).
    static func normalizedWindowThreshold(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        let whole = value.rounded()
        return whole <= 0 ? nil : min(whole, accountThresholdRange.upperBound)
    }

    /// The write-side check for one window's own value (spec 4: out of range is
    /// refused, not clamped). The whole number it rounds to (half away from
    /// zero) when that is 1–100; nil for NaN, infinity, anything that rounds to
    /// 0 or below, and anything above 100. `normalizedWindowThreshold` is for
    /// RESOLVING stored values, which clamps above 100; this is for writes.
    static func validWindowWrite(_ value: Double) -> Double? {
        guard value.isFinite else { return nil }
        let whole = value.rounded()
        return (1.0...accountThresholdRange.upperBound).contains(whole) ? whole : nil
    }

    /// The write-side check for a global default (base, session or weekly):
    /// the whole number it rounds to when that is within 50–100, else nil.
    static func validDefaultWrite(_ value: Double) -> Double? {
        guard value.isFinite else { return nil }
        let whole = value.rounded()
        return thresholdRange.contains(whole) ? whole : nil
    }

    /// One account's resolved rule (spec 2.2): Manual only wins; otherwise each
    /// window takes its own value, else the account's both-windows value, else
    /// the global default for that window.
    static func rule(switchThreshold: Double?, sessionThreshold: Double?, weeklyThreshold: Double?,
                     defaults: AutoSwitchEngine.WindowPair) -> AutoSwitchEngine.WindowRule {
        let both = normalizedAccountThreshold(switchThreshold)
        if both == manualOnlyThreshold { return .manualOnlyRule }
        let bothValue = (both ?? 0) > manualOnlyThreshold ? both : nil
        return AutoSwitchEngine.WindowRule(
            session: normalizedWindowThreshold(sessionThreshold) ?? bothValue ?? defaults.session,
            weekly: normalizedWindowThreshold(weeklyThreshold) ?? bothValue ?? defaults.weekly,
            manualOnly: false)
    }

    static func rule(for account: Account, defaults: AutoSwitchEngine.WindowPair) -> AutoSwitchEngine.WindowRule {
        rule(switchThreshold: account.switchThreshold, sessionThreshold: account.sessionThreshold,
             weeklyThreshold: account.weeklyThreshold, defaults: defaults)
    }

    /// Where auto-switch moves the user OFF an account while it is active, per
    /// window: the rule value, or the default for Manual only (spec 2.2).
    static func leaveAtPair(rule: AutoSwitchEngine.WindowRule, defaults: AutoSwitchEngine.WindowPair) -> AutoSwitchEngine.WindowPair {
        AutoSwitchEngine.WindowPair(session: rule.session > manualOnlyThreshold ? rule.session : defaults.session,
                                    weekly: rule.weekly > manualOnlyThreshold ? rule.weekly : defaults.weekly)
    }

    /// The numbers Settings → Accounts states for an account, per window:
    /// where auto-switch leaves it, the highest use at which it moves you to
    /// it, and the weekly number that holds when no account under its weekly
    /// limit can take you (the weekly limit raised to the session limit). Nil
    /// for Manual only. Not an overload of `explanation`.
    static func windowExplanation(rule: AutoSwitchEngine.WindowRule, defaults: AutoSwitchEngine.WindowPair)
        -> (leaveAt: AutoSwitchEngine.WindowPair, arriveAt: AutoSwitchEngine.WindowPair, relaxedArriveWeekly: Double)? {
        guard !rule.manualOnly,
              let session = AutoSwitchEngine.ceiling(threshold: rule.session, room: AutoSwitchEngine.hysteresis),
              let weekly = AutoSwitchEngine.ceiling(threshold: rule.weekly, room: AutoSwitchEngine.hysteresis),
              let relaxedWeekly = AutoSwitchEngine.ceiling(threshold: rule.relaxed.weekly, room: AutoSwitchEngine.hysteresis)
        else { return nil }
        return (leaveAtPair(rule: rule, defaults: defaults), AutoSwitchEngine.WindowPair(session: session, weekly: weekly), relaxedWeekly)
    }

    // MARK: Editing one account's thresholds

    /// What a per-window write does to one window.
    enum WindowEdit: Equatable, Sendable {
        /// Not part of this write (the key was absent).
        case leave
        /// Follow the default again (null / "Default").
        case clear
        /// Its own value: rounded half away from zero, then 1–100.
        case set(Double)

        /// The number a `.set` carries.
        var number: Double? {
            if case .set(let value) = self { return value }
            return nil
        }
    }

    /// `account` after a per-window write (spec 2.1), or nil when a `.set`
    /// value is not a number from 1 to 100 once rounded (`validWindowWrite`: a
    /// value above 100 is refused, never clamped; the account is then unchanged
    /// by the caller).
    ///
    /// - Both edits `.leave`: nothing changes.
    /// - Manual only: only a number clears it, which sets that window and
    ///   leaves the other following its default; null alone changes nothing.
    /// - Otherwise the split first: a both-windows value of 1–100 is copied into
    ///   each window that has none, and the both-windows value goes, then the
    ///   edits apply. So `clear` always means the global window default.
    static func applyingWindowEdits(to account: Account, session: WindowEdit, weekly: WindowEdit) -> Account? {
        // A `.set` that is not a whole number from 1 to 100 once rounded refuses
        // the whole write (spec 4: invalidValue), before anything changes.
        for edit in [session, weekly] {
            if let number = edit.number, validWindowWrite(number) == nil { return nil }
        }
        if session == .leave && weekly == .leave { return account }

        var result = account
        if isManualOnly(own: account.switchThreshold) {
            guard session.number != nil || weekly.number != nil else { return account }
            result.switchThreshold = nil
            result.sessionThreshold = nil
            result.weeklyThreshold = nil
        } else if let both = normalizedAccountThreshold(account.switchThreshold), both > manualOnlyThreshold {
            if result.sessionThreshold == nil { result.sessionThreshold = both }
            if result.weeklyThreshold == nil { result.weeklyThreshold = both }
            result.switchThreshold = nil
        }
        switch session {
        case .leave: break
        case .clear: result.sessionThreshold = nil
        case .set(let number): result.sessionThreshold = validWindowWrite(number)
        }
        switch weekly {
        case .leave: break
        case .clear: result.weeklyThreshold = nil
        case .set(let number): result.weeklyThreshold = validWindowWrite(number)
        }
        return result
    }

    /// `account` after the legacy "both windows" write (D8): the both-windows
    /// value is set (`nil` follows the default, 0 is Manual only) and both
    /// window values go, even when the both-windows value is unchanged.
    static func applyingBothWindows(_ threshold: Double?, to account: Account) -> Account {
        var result = account
        result.switchThreshold = normalizedAccountThreshold(threshold)
        result.sessionThreshold = nil
        result.weeklyThreshold = nil
        return result
    }

    /// Settings-only (spec 2.4): Default chosen in either menu of a Manual only
    /// row clears Manual only, and both windows follow their defaults. Any other
    /// account is returned unchanged.
    static func clearingManualOnly(_ account: Account) -> Account {
        guard isManualOnly(own: account.switchThreshold) else { return account }
        return applyingBothWindows(nil, to: account)
    }

    // MARK: Writing the global defaults

    /// Writes one global window default: a whole number within 50–100, or nil
    /// to remove the key so the window follows the base again. False, and
    /// nothing written, for a value that is not a finite number or that rounds
    /// outside 50–100 (refused, not clamped: what is stored is what the caller asked for).
    @discardableResult
    static func setDefaultThreshold(_ window: AutoSwitchEngine.Window, to value: Double?, in defaults: UserDefaults = .standard) -> Bool {
        let key = window == .session ? sessionThresholdKey : weeklyThresholdKey
        guard let value else {
            defaults.removeObject(forKey: key)
            return true
        }
        guard let whole = validDefaultWrite(value) else { return false }
        defaults.set(whole, forKey: key)
        return true
    }

    /// Writes the base default (`autoSwitchThreshold`, 50–100) and removes both
    /// window keys, so a legacy "both" write is never held back by an old
    /// window value (D8). False, nothing written, for a value that is not finite
    /// or that rounds outside 50–100.
    @discardableResult
    static func setBaseThreshold(_ value: Double, in defaults: UserDefaults = .standard) -> Bool {
        guard let whole = validDefaultWrite(value) else { return false }
        defaults.set(whole, forKey: thresholdKey)
        defaults.removeObject(forKey: sessionThresholdKey)
        defaults.removeObject(forKey: weeklyThresholdKey)
        return true
    }
}
