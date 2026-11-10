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
    /// is a 20x one, or Claude Code's own display name for the plan is "Ultra".
    /// A tier that says 5x beats a display name that says Ultra: when the two
    /// disagree the account keeps its 5-hour limit. Pro, team, enterprise and
    /// anything else are never Ultra, whatever else is stored.
    static func resolve(subscriptionType: String?, planTier: String?, planDisplayName: String? = nil) -> AccountPlan {
        let type = normalized(subscriptionType)
        let tier = normalized(planTier)
        let name = normalized(planDisplayName)
        if type == nil || type == "max" {
            if let tier, max5xTiers.contains(tier) { return .max5x }
            if let tier, ultraTiers.contains(tier) { return .ultra }
            if name == "ultra" { return .ultra }
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

    /// The tier to store after `claude auth status` reported one: the reported
    /// tier when there is one (so a downgrade is picked up), else what is
    /// already stored (an older `claude` prints no tier, and must not erase it).
    static func updatedTier(current: String?, reported: String?) -> String? {
        text(reported) ?? text(current)
    }
}

// MARK: - What an account says about its plan

extension Account {
    /// The plan, from the subscription type and the stored tier.
    var plan: AccountPlan {
        AccountPlan.resolve(subscriptionType: subscriptionType, planTier: planTier)
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

// MARK: - The one place the session reading is dropped

extension UsageAPIResponse {
    /// This reading without its 5-hour window. `AppState.requestUsage`, the one
    /// place a usage request is made, applies it to an account with no 5-hour
    /// limit, so no session reading is ever held for one: not the engine, the
    /// Usage card, the menu bar, the widget, nor the control protocol sees it.
    /// The API already sends `five_hour: null` for such a login; this is the
    /// guard for the day it sends an object again, and for an object whose
    /// utilization is null (which the views would draw as 0%).
    func droppingSession() -> UsageAPIResponse {
        guard fiveHour != nil else { return self }
        return UsageAPIResponse(fiveHour: nil, sevenDay: sevenDay, sevenDayOauthApps: sevenDayOauthApps,
                                sevenDayOpus: sevenDayOpus, sevenDaySonnet: sevenDaySonnet,
                                sevenDayCowork: sevenDayCowork, iguanaNecktie: iguanaNecktie,
                                extraUsage: extraUsage, limits: limits)
    }
}
