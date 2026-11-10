import Foundation

// MARK: - The plan an account is on

/// Which plan an account is on, as far as the app can tell. Pure Foundation, so
/// the unit harness, the app and the offscreen renders compile it as it is.
///
/// Why it exists: Anthropic took the 5-hour limit away from the Max 20x plan,
/// now called Claude Ultra (founder, 2026-11-10: "Anthropic got rid of the 5
/// hour limit on Max 200 accounts, now called Claude Ultra accounts. So that
/// should be removed from those types of accounts."). The usage API already
/// sends `five_hour: null` for such a login. For an account on this plan the
/// app does not show, track or act on a Session limit, and the badge says
/// Ultra.
///
/// Anything the app cannot tell is NOT Ultra and keeps the 5-hour limit and
/// today's badge, exactly: a missing tier, an unrecognised one, a subscription
/// that is not Max, an older `claude` that prints no tier.
///
/// The API's own data is authoritative over the tier. A tier is only as fresh as
/// the last time the account was active, and Anthropic may change the plan or
/// bring the limit back, so an account the tier calls Ultra that the usage API
/// still sends a real 5-hour reading for is NOT treated as Ultra
/// (`Account.sessionLimitObserved`, set by `UsageAPIResponse.settingSession`)
/// until the API stops sending it. `AccountPlan.resolve` is the one place that
/// answers; every consumer asks `Account.hasSessionLimit`.
enum AccountPlan: Equatable, Sendable {
    case pro
    case max5x
    /// Max 20x, called Ultra: no 5-hour limit.
    case ultra
    /// Any other subscription type, lower-cased (team, enterprise), and Max
    /// whose tier is not known (it may be 5x or 20x).
    case other(String)
    /// Nothing is known.
    case unknown

    /// The tier `claude auth status` prints as `planTier` for Ultra.
    static let ultraTier = "max_20x"

    /// The two spellings of the 20x tier: `planTier` from `claude auth status`,
    /// and `rateLimitTier` in a saved login (and `organizationRateLimitTier` in
    /// `~/.claude.json`'s account block).
    private static let ultraTiers: Set<String> = ["max_20x", "default_claude_max_20x"]
    private static let max5xTiers: Set<String> = ["max_5x", "default_claude_max_5x"]

    /// Trimmed, lower-cased, nil when blank. Used to compare, never to display.
    private static func normalized(_ text: String?) -> String? {
        guard let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(), !trimmed.isEmpty else { return nil }
        return trimmed
    }

    /// Ultra exactly when the subscription is Max (or not known yet) and the tier
    /// is a 20x one, or Claude Code's own display name for the plan is "Ultra",
    /// and the usage API is not sending a real 5-hour reading for the account
    /// (`sessionObserved`: then it has a session limit, whatever the tier says,
    /// and is the plan of its subscription type: Max, or unknown).
    /// A tier that says 5x beats a display name that says Ultra: when the two
    /// disagree the account keeps its 5-hour limit. Pro, team, enterprise and
    /// anything else are never Ultra, whatever else is stored.
    static func resolve(subscriptionType: String?, planTier: String?, planDisplayName: String? = nil,
                        sessionObserved: Bool = false) -> AccountPlan {
        let type = normalized(subscriptionType)
        let tier = normalized(planTier)
        let name = normalized(planDisplayName)
        if type == nil || type == "max" {
            if let tier, max5xTiers.contains(tier) { return .max5x }
            if !sessionObserved {
                if let tier, ultraTiers.contains(tier) { return .ultra }
                if name == "ultra" { return .ultra }
            }
        }
        guard let type else { return .unknown }
        return type == "pro" ? .pro : .other(type)
    }

    /// False only for Ultra. Unknown keeps the limit.
    var hasSessionLimit: Bool {
        if case .ultra = self { return false }
        return true
    }

    /// The badge text: "Ultra" for Ultra, otherwise the subscription type with a
    /// capital letter exactly as before ("max" is "Max", "pro" is "Pro").
    func badgeText(subscriptionType: String?) -> String? {
        if case .ultra = self { return "Ultra" }
        return subscriptionType?.prefix(1).uppercased().appending(subscriptionType?.dropFirst() ?? "")
    }

    // MARK: Where the tier comes from

    /// A non-blank string, trimmed; nil for anything else (a number, null, blank).
    private static func text(_ value: Any?) -> String? {
        guard let string = (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !string.isEmpty else { return nil }
        return string
    }

    /// The tier to store for an account, read from its saved login: the
    /// credential's `claudeAiOauth.rateLimitTier`, else the account block's
    /// `organizationRateLimitTier`, else the canonical 20x tier when Claude
    /// Code's `planDisplayName` there is "Ultra". Nil when none says anything.
    ///
    /// Reads only those tier and name fields. It returns the tier text and never
    /// anything else out of the credential, so no token can leave through it.
    static func tier(fromCredential credential: String?, oauthAccount: [String: Any]?) -> String? {
        if let credential, let data = credential.data(using: .utf8),
           let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let oauth = object["claudeAiOauth"] as? [String: Any],
           let tier = text(oauth["rateLimitTier"]) {
            return tier
        }
        if let tier = text(oauthAccount?["organizationRateLimitTier"]) { return tier }
        if normalized(text(oauthAccount?["planDisplayName"])) == "ultra" { return ultraTier }
        return nil
    }

    /// Tiers the app knows what they mean: 5x, 20x, Pro. Anything else the CLI
    /// reports (`max_other`, `other`, a string from a newer `claude`) is vague: it
    /// says "I do not know", not "the plan changed".
    private static let recognisedTiers: Set<String> = ultraTiers.union(max5xTiers).union(["pro", "default_claude_ai"])

    /// The tier to store after `claude auth status` reported one: the reported
    /// tier when there is one (so a downgrade is picked up), else what is
    /// already stored (an older `claude` prints no tier, and must not erase it).
    ///
    /// A vague reported tier never overwrites a known 5x or 20x one: `claude`
    /// prints `max_other` for any Max login whose saved `rateLimitTier` is
    /// missing or new (an older backup swapped in, a tier it has no name for),
    /// and that must not turn an Ultra account into an unknown one. A
    /// recognised, different tier (pro, max_5x, a 20x for a 5x) still replaces it.
    static func updatedTier(current: String?, reported: String?) -> String? {
        let reported = text(reported), current = text(current)
        guard let reported else { return current }
        if let current, let known = normalized(current), ultraTiers.contains(known) || max5xTiers.contains(known),
           let incoming = normalized(reported), !recognisedTiers.contains(incoming) {
            return current
        }
        return reported
    }
}

// MARK: - What an account says about its plan

extension Account {
    /// The plan, from the subscription type and the stored tier.
    var plan: AccountPlan {
        AccountPlan.resolve(subscriptionType: subscriptionType, planTier: planTier, sessionObserved: sessionLimitObserved == true)
    }

    /// Whether the plan fields alone (subscription type and tier) say Ultra,
    /// before the API's own word about the 5-hour window is taken into account.
    var isClassedUltra: Bool {
        AccountPlan.resolve(subscriptionType: subscriptionType, planTier: planTier) == .ultra
    }

    /// False for Ultra: there is no Session (5-hour) limit to show, track or act on.
    var hasSessionLimit: Bool { plan.hasSessionLimit }

    /// The plan badge's text, in every place the app shows one (the popover
    /// header, the Usage card, the Accounts list, the widget, the command line
    /// and MCP): "Ultra" for Ultra, else the subscription type with a capital
    /// letter ("max" is "Max").
    var displaySubscriptionType: String? {
        plan.badgeText(subscriptionType: subscriptionType)
    }
}

// MARK: - The one place the session reading is decided

extension UsageAPIResponse {
    /// True when the 5-hour window carries a number (0 counts): a real reading.
    var hasSessionReading: Bool { fiveHour?.utilization != nil }

    /// This reading without a 5-hour window that says nothing: absent and null
    /// are already nothing, and an object whose utilization is null (which the
    /// views would draw as 0% used) is dropped. A window that carries a number
    /// is never dropped, and every other field is copied as it was.
    func droppingEmptySession() -> UsageAPIResponse {
        guard fiveHour != nil, !hasSessionReading else { return self }
        return UsageAPIResponse(fiveHour: nil, sevenDay: sevenDay, sevenDayOauthApps: sevenDayOauthApps,
                                sevenDayOpus: sevenDayOpus, sevenDaySonnet: sevenDaySonnet,
                                sevenDayCowork: sevenDayCowork, iguanaNecktie: iguanaNecktie,
                                extraUsage: extraUsage, limits: limits)
    }

    /// What `AppState.requestUsage`, the one place a usage request is made, holds
    /// for `account` after a reading, and what it remembers about the account's
    /// 5-hour window. The API's data is authoritative:
    ///
    /// - An account the plan fields call Ultra (no 5-hour limit): a window with no
    ///   number is dropped; a real one is kept whole and the account is marked as
    ///   having a session limit (`sessionLimitObserved` true) until a later reading
    ///   carries none (nil again). So a stale tier (a 20x account downgraded while
    ///   not active) can never hide a real session limit and let an exhausted
    ///   account be chosen, and every consumer follows `Account.hasSessionLimit`.
    /// - Any other account: the reading as it came, and nothing remembered.
    ///
    /// `sessionLimitObserved` is the value to store on the account: true or nil.
    func settingSession(for account: Account) -> (usage: UsageAPIResponse, sessionLimitObserved: Bool?) {
        guard account.isClassedUltra else { return (self, nil) }
        return hasSessionReading ? (self, true) : (droppingEmptySession(), nil)
    }
}

// MARK: - The rule the engine is handed

extension AutoSwitchEngine.WindowRule {
    /// This rule for an account with no 5-hour limit: its weekly number in both
    /// windows. The session number is hidden from the user for such an account,
    /// but `relaxed` (rule 9, the overflow pass) raises the weekly limit to the
    /// session one, so a hidden session number higher than the weekly one would
    /// still move where the engine leaves and arrives. With the two equal,
    /// `relaxed` changes nothing, and the engine, the conformance vectors and the
    /// Linux port stay as they are. Manual only stays Manual only.
    var withoutSessionLimit: AutoSwitchEngine.WindowRule {
        manualOnly ? self : AutoSwitchEngine.WindowRule(session: weekly, weekly: weekly, manualOnly: false)
    }
}

extension AutoSwitchSettings {
    /// `account`'s resolved rule as the engine is handed it: `rule(for:)`, less
    /// the session number for an account with no 5-hour limit. The stored session
    /// value is kept on the account (it counts again if the account gets a session
    /// limit); it just never reaches the engine for one.
    static func effectiveRule(for account: Account, defaults: AutoSwitchEngine.WindowPair) -> AutoSwitchEngine.WindowRule {
        let rule = rule(for: account, defaults: defaults)
        return account.hasSessionLimit ? rule : rule.withoutSessionLimit
    }
}

// MARK: - Filling the plan from saved logins

/// One saved login as far as the plan is concerned: the credential text and the
/// account block of `~/.claude.json`, as `AccountPlan.tier` reads them.
struct PlanSavedLogin {
    let credential: String?
    let oauthAccount: [String: Any]?
}

/// The bookkeeping of the plan backfill (`AppState.backfillPlans`): which
/// accounts have been settled this launch, and how often the store was unreadable.
///
/// An account is settled only by a read that WORKED: it returned data, or the
/// store read fine and holds no backup for the account (a definitive no backup).
/// An unreadable store (locked, a prompt denied) settles nothing, so the next
/// refresh asks again; the number of such reads per launch is bounded, so a
/// store that stays unreadable is not asked on every refresh for ever.
struct PlanBackfillLedger: Equatable, Sendable {
    /// Unreadable reads allowed per launch (one per refresh at most).
    static let unreadableLimit = 3

    private(set) var settled: Set<UUID> = []
    private(set) var unreadable = 0

    /// The accounts to look at now: no tier, not settled, and the store has not
    /// been unreadable `unreadableLimit` times already.
    func wanted(_ accounts: [Account]) -> [Account] {
        guard unreadable < Self.unreadableLimit else { return [] }
        return accounts.filter { $0.planTier == nil && !settled.contains($0.id) }
    }

    /// Records one read of every saved login (`read`, keyed by account id; nil
    /// when the store was unreadable) for `wanted`, and returns the tier to store
    /// for each account whose saved login names one.
    mutating func record(wanted: [Account], read: [String: PlanSavedLogin]?) -> [UUID: String] {
        guard let read else {
            unreadable += 1
            return [:]
        }
        var tiers: [UUID: String] = [:]
        for account in wanted {
            settled.insert(account.id)
            if let login = read[account.id.uuidString],
               let tier = AccountPlan.tier(fromCredential: login.credential, oauthAccount: login.oauthAccount) {
                tiers[account.id] = tier
            }
        }
        return tiers
    }
}
