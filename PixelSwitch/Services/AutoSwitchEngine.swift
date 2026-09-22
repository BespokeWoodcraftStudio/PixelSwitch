import Foundation

/// Pure, UI-agnostic auto-switch decision logic.
///
/// Mirrors the proven design in `claude-swap` (threshold + hysteresis): when the
/// active account's *binding window* (the higher of its 5h / weekly utilization)
/// reaches the configured threshold, pick the same-provider account with the most
/// quota left — but only one that sits at least `hysteresisPct` below the threshold,
/// so two accounts hovering at the line never ping-pong. All guardrails that need
/// state (cooldown, re-entrancy, verification) live in `AppState`; this stays a
/// pure function.
///
/// Two limits are watched, in order: the session/weekly pair (`.windows`), then
/// the weekly Fable allowance (`.fable`), which runs out on its own schedule.
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
    /// straight off the account it just chose. Used both to rank candidates and
    /// to verify the chosen one before switching, so the two cannot disagree.
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

    /// One window's utilization, or nil if it has none or its window has reset.
    private static func reading(_ window: UsageWindow?, asOf now: Date, requireKnownWindow: Bool) -> Double? {
        guard let window, let util = window.utilization else { return nil }
        guard let resets = window.resetsAtDate else {
            return requireKnownWindow ? nil : util
        }
        return resets < now ? nil : util
    }

    /// Which limit to act on and where to go, or nil to stay put. Session and
    /// weekly are checked first; Fable only when they have not triggered a
    /// switch, since a Fable target must have session and weekly room anyway.
    ///
    /// `watchFable` is the user's setting: off means Fable is shown but never
    /// moves anyone, while session and weekly keep working exactly as before.
    static func plan(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        activeSampledThisCycle: Bool,
        threshold: Double,
        hysteresisPct: Double,
        watchFable: Bool = true,
        asOf now: Date = Date()
    ) -> (limit: Limit, targets: [Account])? {
        for limit in (watchFable ? [Limit.windows, .fable] : [Limit.windows]) {
            let targets = rankedTargets(
                active: active,
                candidates: candidates,
                usageByAccount: usageByAccount,
                isSwitchable: isSwitchable,
                activeSampledThisCycle: activeSampledThisCycle,
                threshold: threshold,
                hysteresisPct: hysteresisPct,
                limit: limit,
                asOf: now
            )
            if !targets.isEmpty { return (limit, targets) }
        }
        return nil
    }

    /// Rank the accounts worth switching to, best first (most headroom).
    ///
    /// The result is a list of *proposals*, not decisions: it is computed from
    /// whatever samples the caller happens to hold, which round-robin polling can
    /// leave several cycles old. `AppState` verifies a candidate's usage before
    /// committing to a switch, and falls through the list when one fails.
    ///
    /// - Parameters:
    ///   - active: the currently active account.
    ///   - candidates: every other account (already filtered to the same provider).
    ///   - usageByAccount: latest usage sample per account id.
    ///   - isSwitchable: whether an account can actually be switched to right now
    ///     (has a stored backup token and isn't flagged expired).
    ///   - activeSampledThisCycle: whether the active account's sample was taken
    ///     by the refresh cycle that is asking. Fresh readings are trusted even
    ///     without a parseable `resets_at` (the number is current by
    ///     construction); only RETAINED readings need the strict expiry check,
    ///     because they are the ones that can describe a window that has since
    ///     reset.
    ///   - threshold: switch when the active binding utilization is >= this (e.g. 90).
    ///   - hysteresisPct: a candidate must sit at least this far below the threshold
    ///     to be eligible (e.g. 10 -> candidate must be <= threshold - 10).
    ///   - limit: the limit that triggers and ranks (see `eligibleUtilization`).
    ///   - now: injected clock, for window-expiry checks and testability.
    /// - Returns: eligible accounts ordered best-first; empty means stay put.
    static func rankedTargets(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        activeSampledThisCycle: Bool,
        threshold: Double,
        hysteresisPct: Double,
        limit: Limit = .windows,
        asOf now: Date = Date()
    ) -> [Account] {
        // 1) Only act once the active account has reached the threshold on a
        //    window we watch. Unknown active usage -> do nothing (can't decide).
        //    The trigger to move the user off an account must never rest on a
        //    RETAINED reading whose expiry we cannot establish — but a reading
        //    this very cycle fetched is current whether or not the endpoint
        //    sent a parseable resets_at.
        guard let activeUtil = utilization(usageByAccount[active.id], limit: limit, asOf: now, requireKnownWindow: !activeSampledThisCycle),
              activeUtil >= threshold else {
            return []
        }

        // 2) Keep only switchable candidates KNOWN to sit safely below the
        //    threshold. A candidate with no usable reading is NOT eligible:
        //    accounts are polled round-robin, so "no sample" usually means "not
        //    reached yet" rather than "idle" — treating it as a failover fallback
        //    let an automatic switch land on an account that was itself maxed
        //    out, which is the exact failure this feature exists to prevent.
        let ceiling = threshold - hysteresisPct
        return candidates
            .compactMap { candidate -> (account: Account, util: Double, keepsFable: Bool)? in
                // The usage check comes first: `isSwitchable` reads the Keychain.
                guard candidate.id != active.id,
                      let util = eligibleUtilization(usageByAccount[candidate.id], limit: limit, ceiling: ceiling, asOf: now),
                      isSwitchable(candidate) else { return nil }
                let keepsFable = utilization(usageByAccount[candidate.id], limit: .fable, asOf: now).map { $0 <= ceiling } ?? false
                return (candidate, util, keepsFable)
            }
            // 3) Accounts with Fable to spare first, so a session/weekly switch
            //    does not land on an account that is out of Fable and force a
            //    second switch minutes later; then most headroom first (lowest
            //    known utilization). Every `.fable` candidate keeps Fable, so
            //    those rank purely by Fable headroom.
            .sorted { ($0.keepsFable ? 0 : 1, $0.util) < ($1.keepsFable ? 0 : 1, $1.util) }
            .map(\.account)
    }
}

/// Whether auto-switch also acts on the weekly Fable allowance. On by default;
/// off leaves Fable as a reading only, and session and weekly switching alone.
enum AutoSwitchFableSetting {
    static let key = "autoSwitchOnFable"

    static var isOn: Bool {
        UserDefaults.standard.object(forKey: key) as? Bool ?? true
    }
}
