import Foundation

// MARK: - Provider Type (extensible for Gemini, Codex, etc.)

enum AIProviderType: String, Codable, CaseIterable, Identifiable {
    case claudeCode = "Claude Code"
    case gemini = "Gemini"
    case codex = "Codex"

    var id: String { rawValue }

    var iconName: String {
        switch self {
        case .claudeCode: return "brain.head.profile"
        case .gemini: return "sparkles"
        case .codex: return "chevron.left.forwardslash.chevron.right"
        }
    }

    var configDirectory: String {
        switch self {
        case .claudeCode: return "~/.claude"
        case .gemini: return "~/.gemini"
        case .codex: return "~/.codex"
        }
    }
}

// MARK: - Account Model

struct Account: Identifiable, Codable, Hashable {
    let id: UUID
    var email: String
    var displayName: String
    var provider: AIProviderType
    var orgName: String?
    var subscriptionType: String?
    var isActive: Bool
    var lastUsed: Date?
    var customLabel: String?
    /// This account's own auto-switch threshold, 0–100 percent; 0 means
    /// Manual only (auto-switch never switches to it). nil means it uses the
    /// default (global) threshold. Saved accounts from before this
    /// existed decode it as nil, so they behave exactly as they did.
    var switchThreshold: Double?   // 0...100; 0 = Manual only; nil = use the default (global) threshold
    /// 2.0: this account's own limit for the 5-hour session window alone, 1–100.
    /// nil follows the next layer (`switchThreshold`, then the global default).
    /// Accounts saved by 1.9 decode it as nil, and a nil is never written.
    var sessionThreshold: Double?
    /// 2.0: the same for the 7-day weekly window (and Fable, which follows it).
    var weeklyThreshold: Double?
    /// 2.0: this account's priority number, 1–99; lower is tried first. nil =
    /// none: such accounts come after every numbered one. Saved accounts from
    /// before it existed decode it as nil.
    var priority: Int?
    /// 2.0: when `priority` was last set, so that among equal numbers the one
    /// set most recently goes first (spec D2). nil = never set by the user.
    var prioritySetAt: Date?
    /// The plan's rate-limit tier, as `claude auth status` prints it in
    /// `planTier` ("max_20x") or a saved login holds it ("default_claude_max_20x").
    /// With `subscriptionType` it tells Max 20x, called Ultra, which has no
    /// 5-hour limit, from Max 5x (see `AccountPlan`). nil = not known: such an
    /// account keeps the 5-hour limit. Accounts saved before it existed decode
    /// it as nil, and a nil is never written.
    var planTier: String?
    /// True while the usage API itself sends a real 5-hour reading for this
    /// account although its tier says Ultra (Max 20x, no 5-hour limit). The API's
    /// own data wins: such an account HAS a session limit (the tier may be stale,
    /// or Anthropic may have brought the limit back), and `AccountPlan.resolve`
    /// answers that way until the API stops sending it. nil = not observed; only
    /// ever written as true or nil, so a nil is never saved and accounts saved
    /// before it existed decode it as nil (see `UsageAPIResponse.settingSession`).
    var sessionLimitObserved: Bool?

    // The plan badge's text (`displaySubscriptionType`) and `hasSessionLimit`
    // live in AccountPlan.swift, so that there is one place that decides both.

    var obfuscatedEmail: String {
        // This field IS an address — mask structurally, no detection step, so
        // non-ASCII/quoted/punycode/literal forms can never slip through.
        return email.maskedAsEmailAddress()
    }

    var obfuscatedDisplayName: String {
        // displayName is orgName ?? email: when it IS the email, mask it
        // structurally like the email field; otherwise treat it as free text
        // and only mask what the conservative detector recognizes.
        if displayName == email {
            return email.maskedAsEmailAddress()
        }
        return displayName.obfuscatedEmail()
    }

    /// Returns customLabel if set and non-empty, otherwise falls back to obfuscatedDisplayName.
    var effectiveDisplayName: String {
        if let label = customLabel, !label.isEmpty {
            return label
        }
        return obfuscatedDisplayName
    }

    /// Returns the email, optionally obfuscated.
    func displayEmail(obfuscated: Bool) -> String {
        obfuscated ? obfuscatedEmail : email
    }

    /// Returns the effective display name, optionally obfuscating the fallback.
    func effectiveDisplayName(obfuscated: Bool) -> String {
        if let label = customLabel, !label.isEmpty {
            return label
        }
        return obfuscated ? obfuscatedDisplayName : displayName
    }

    init(
        id: UUID = UUID(),
        email: String,
        displayName: String,
        provider: AIProviderType = .claudeCode,
        orgName: String? = nil,
        subscriptionType: String? = nil,
        isActive: Bool = false,
        lastUsed: Date? = nil,
        customLabel: String? = nil,
        switchThreshold: Double? = nil,
        sessionThreshold: Double? = nil,
        weeklyThreshold: Double? = nil,
        priority: Int? = nil,
        prioritySetAt: Date? = nil,
        planTier: String? = nil,
        sessionLimitObserved: Bool? = nil
    ) {
        self.id = id
        self.email = email
        self.displayName = displayName
        self.provider = provider
        self.orgName = orgName
        self.subscriptionType = subscriptionType
        self.isActive = isActive
        self.lastUsed = lastUsed
        self.customLabel = customLabel
        self.switchThreshold = switchThreshold
        self.sessionThreshold = sessionThreshold
        self.weeklyThreshold = weeklyThreshold
        self.priority = priority
        self.prioritySetAt = prioritySetAt
        self.planTier = planTier
        self.sessionLimitObserved = sessionLimitObserved
    }
}

// MARK: - Auth Status (from `claude auth status`)

/// Decoded `claude auth status` output.
///
/// The CLI only emits `email`, `orgId`, `orgName` and `subscriptionType` when
/// `authMethod == "claude.ai"` — that is, when the stored claude.ai login is the
/// credential source it actually resolves to. Any higher-precedence source
/// shadows the stored login and the whole identity block is omitted while
/// `loggedIn` stays `true`: an `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN`
/// environment variable, an `apiKeyHelper`, an OAuth token file, an Anthropic
/// profile in `~/.config/anthropic`, or a third-party provider (Bedrock, Vertex,
/// …). Callers must therefore treat a missing `email` as "identity unavailable",
/// never as "a different account is active".
struct AuthStatus: Codable {
    let loggedIn: Bool
    let authMethod: String?
    let apiProvider: String?
    let email: String?
    let orgId: String?
    let orgName: String?
    let subscriptionType: String?
    /// The plan's rate-limit tier ("max_20x" for Max 20x, called Ultra). Newer
    /// `claude` builds print it; older ones do not, and it then decodes as nil.
    /// A `var` with an optional type, not a `let`, so the memberwise initialiser
    /// keeps working without it.
    var planTier: String?

    /// True when the CLI reports the identity of the stored claude.ai login,
    /// i.e. when `email` is meaningful.
    var reportsClaudeAiIdentity: Bool {
        authMethod == "claude.ai"
    }

    /// The credential source shadowing the stored claude.ai login, or `nil` when
    /// nothing shadows it. Only meaningful while `loggedIn` is `true`.
    var shadowingAuthMethod: String? {
        reportsClaudeAiIdentity ? nil : (authMethod ?? "unknown")
    }
}
