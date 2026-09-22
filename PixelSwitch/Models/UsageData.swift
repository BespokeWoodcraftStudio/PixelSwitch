import Foundation

// MARK: - Usage API Response (from /api/oauth/usage)

struct UsageAPIResponse: Codable {
    let fiveHour: UsageWindow?
    let sevenDay: UsageWindow?
    let sevenDayOauthApps: UsageWindow?
    let sevenDayOpus: UsageWindow?
    let sevenDaySonnet: UsageWindow?
    let sevenDayCowork: UsageWindow?
    let iguanaNecktie: UsageWindow?
    let extraUsage: ExtraUsage?
    /// The newer `limits` list. Per-model weekly allowances (Fable) appear only
    /// here; `seven_day_opus` and `seven_day_sonnet` come back null.
    let limits: [UsageLimit]?

    enum CodingKeys: String, CodingKey {
        case fiveHour = "five_hour"
        case sevenDay = "seven_day"
        case sevenDayOauthApps = "seven_day_oauth_apps"
        case sevenDayOpus = "seven_day_opus"
        case sevenDaySonnet = "seven_day_sonnet"
        case sevenDayCowork = "seven_day_cowork"
        case iguanaNecktie = "iguana_necktie"
        case extraUsage = "extra_usage"
        case limits
    }
}

extension UsageAPIResponse {
    /// Decodes the windows exactly as the synthesized decoder did, and `limits`
    /// leniently: an entry of an unexpected shape is skipped and a `limits`
    /// that is not a list reads as none, so a change there can never cost
    /// the session and weekly numbers.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        fiveHour = try c.decodeIfPresent(UsageWindow.self, forKey: .fiveHour)
        sevenDay = try c.decodeIfPresent(UsageWindow.self, forKey: .sevenDay)
        sevenDayOauthApps = try c.decodeIfPresent(UsageWindow.self, forKey: .sevenDayOauthApps)
        sevenDayOpus = try c.decodeIfPresent(UsageWindow.self, forKey: .sevenDayOpus)
        sevenDaySonnet = try c.decodeIfPresent(UsageWindow.self, forKey: .sevenDaySonnet)
        sevenDayCowork = try c.decodeIfPresent(UsageWindow.self, forKey: .sevenDayCowork)
        iguanaNecktie = try c.decodeIfPresent(UsageWindow.self, forKey: .iguanaNecktie)
        extraUsage = try c.decodeIfPresent(ExtraUsage.self, forKey: .extraUsage)
        limits = (try? c.decodeIfPresent(LenientList<UsageLimit>.self, forKey: .limits))?.elements
    }

    /// Per-model weekly allowances (today only Fable), in the API's order.
    var modelWeeklyLimits: [UsageLimit] {
        (limits ?? []).filter { $0.isModelWeekly }
    }

    /// The weekly allowance for one model, matched by its display name.
    func modelWeeklyLimit(named name: String) -> UsageLimit? {
        modelWeeklyLimits.first { $0.modelName?.caseInsensitiveCompare(name) == .orderedSame }
    }
}

/// One entry of the usage response's `limits` list, for example:
/// `{"kind":"weekly_scoped","group":"weekly","percent":74,"severity":"normal",
///   "resets_at":"2026-09-28T18:00:00.177837+00:00",
///   "scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":true}`
struct UsageLimit: Codable, Equatable {
    let kind: String?
    let group: String?
    /// Percent of the allowance used, 0 to 100.
    let percent: Double?
    let severity: String?
    let resetsAt: String?
    let scope: Scope?
    let isActive: Bool?

    struct Scope: Codable, Equatable {
        let model: Model?
    }

    struct Model: Codable, Equatable {
        let id: String?
        let displayName: String?

        enum CodingKeys: String, CodingKey {
            case id
            case displayName = "display_name"
        }
    }

    enum CodingKeys: String, CodingKey {
        case kind, group, percent, severity, scope
        case resetsAt = "resets_at"
        case isActive = "is_active"
    }

    /// The model this limit is for ("Fable"), when it is a per-model allowance.
    var modelName: String? {
        guard let name = scope?.model?.displayName?.trimmingCharacters(in: .whitespaces),
              !name.isEmpty else { return nil }
        return name
    }

    /// A weekly allowance for one model, with a reading to show.
    var isModelWeekly: Bool {
        (kind == "weekly_scoped" || group == "weekly") && modelName != nil && percent != nil
    }

    /// Percent of the allowance still unused, 0 to 100.
    var percentLeft: Double? {
        percent.map { min(max(100 - $0, 0), 100) }
    }

    /// The same reading as a `UsageWindow`, to reuse its reset formatting.
    var window: UsageWindow {
        UsageWindow(utilization: percent, resetsAt: resetsAt)
    }
}

/// Decodes a JSON list, keeping the entries that fit `Element` and skipping
/// the rest. Anything that is not a list decodes as empty. Never throws.
struct LenientList<Element: Decodable>: Decodable {
    let elements: [Element]

    init(from decoder: Decoder) throws {
        guard var list = try? decoder.unkeyedContainer() else {
            elements = []
            return
        }
        var kept: [Element] = []
        while !list.isAtEnd {
            if (try? list.decodeNil()) == true { continue }
            let index = list.currentIndex
            if let element = try? list.decode(Element.self) {
                kept.append(element)
                continue
            }
            _ = try? list.decode(Skip.self)
            // A decoder that cannot step past an entry would spin here forever.
            if list.currentIndex == index { break }
        }
        elements = kept
    }

    /// Accepts any value, to step past an entry that did not fit.
    private struct Skip: Decodable {
        init(from decoder: Decoder) throws {}
    }
}

struct UsageWindow: Codable {
    let utilization: Double?
    let resetsAt: String?

    enum CodingKeys: String, CodingKey {
        case utilization
        case resetsAt = "resets_at"
    }

    var resetsAtDate: Date? {
        guard let resetsAt else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: resetsAt) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: resetsAt)
    }

    var resetTimeString: String? {
        guard let date = resetsAtDate else { return nil }
        let remaining = date.timeIntervalSinceNow
        guard remaining > 0 else { return "now" }

        let hours = Int(remaining) / 3600
        let minutes = (Int(remaining) % 3600) / 60

        if hours > 24 {
            let formatter = DateFormatter()
            formatter.dateFormat = "EEE h:mm a"
            return formatter.string(from: date)
        } else if hours > 0 {
            return "\(hours) hr \(minutes) min"
        } else {
            return "\(minutes) min"
        }
    }

    /// True when `resetTimeString` is a weekday and clock time rather than a
    /// countdown, so a label can read "Resets Mon 11:00 AM" instead of the
    /// ungrammatical "Resets in Mon 11:00 AM". Mirrors `resetTimeString`'s
    /// own 24-hour cutover.
    var resetIsAbsolute: Bool {
        guard let date = resetsAtDate else { return false }
        let remaining = date.timeIntervalSinceNow
        guard remaining > 0 else { return false }
        return Int(remaining) / 3600 > 24
    }

    /// Compact countdown rendering for narrow menu-bar modules.
    /// Always returns a fixed-shape string ("now", "5m", "2h 14m", "4d 6h").
    /// Never falls back to a date format — the menu bar can't afford the width.
    var compactResetString: String? {
        guard let date = resetsAtDate else { return nil }
        let remaining = Int(date.timeIntervalSinceNow)
        guard remaining > 0 else { return "now" }

        let days = remaining / 86_400
        let hours = (remaining % 86_400) / 3600
        let minutes = (remaining % 3600) / 60

        if days > 0 { return "\(days)d \(hours)h" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(max(minutes, 1))m"
    }

    /// How much of the rate-limit window has already elapsed, as a 0–100
    /// percentage: `1 − remaining / windowSeconds`, clamped to [0, 100].
    /// `windowSeconds` is the fixed window length (5h = 18000, 7d = 604800).
    /// Returns nil if there's no reset timestamp. Compared against the usage
    /// bar this shows whether you're burning faster or slower than the clock.
    func elapsedPercent(windowSeconds: Double) -> Double? {
        guard windowSeconds > 0, let date = resetsAtDate else { return nil }
        let elapsed = 1.0 - (date.timeIntervalSinceNow / windowSeconds)
        return min(max(elapsed, 0), 1) * 100
    }
}

/// Fixed rate-limit window lengths, in seconds.
enum RateLimitWindow {
    static let fiveHourSeconds: Double = 5 * 60 * 60       // 18,000
    static let sevenDaySeconds: Double = 7 * 24 * 60 * 60  // 604,800
}

struct ExtraUsage: Codable {
    let isEnabled: Bool?
    let monthlyLimit: Double?
    let usedCredits: Double?
    let utilization: Double?

    enum CodingKeys: String, CodingKey {
        case isEnabled = "is_enabled"
        case monthlyLimit = "monthly_limit"
        case usedCredits = "used_credits"
        case utilization
    }
}

// MARK: - Stats Cache (matches ~/.claude/stats-cache.json)

struct StatsCache: Codable {
    let version: Int?
    let lastComputedDate: String?
    let dailyActivity: [DailyActivity]?
    let totalSessions: Int?
    let totalMessages: Int?
    let longestSession: LongestSession?
    let firstSessionDate: String?
    let hourCounts: [String: Int]?
}

struct DailyActivity: Codable, Identifiable {
    let date: String
    let messageCount: Int
    let sessionCount: Int
    let toolCallCount: Int

    var id: String { date }

    var parsedDate: Date? {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: date)
    }
}

struct LongestSession: Codable {
    let sessionId: String?
    let duration: Int?
    let messageCount: Int?
    let timestamp: String?
}

// MARK: - Computed Usage Summary

struct UsageSummary {
    let weeklyMessages: Int
    let weeklySessionCount: Int
    let weeklyToolCalls: Int
    let todayMessages: Int
    let todaySessionCount: Int
    let todayToolCalls: Int
    let totalMessages: Int
    let totalSessions: Int
    let dailyActivity: [DailyActivity]

    static let empty = UsageSummary(
        weeklyMessages: 0,
        weeklySessionCount: 0,
        weeklyToolCalls: 0,
        todayMessages: 0,
        todaySessionCount: 0,
        todayToolCalls: 0,
        totalMessages: 0,
        totalSessions: 0,
        dailyActivity: []
    )
}

// MARK: - Session Info (from ~/.claude/sessions/*.json)

struct SessionInfo: Codable, Identifiable {
    let pid: Int
    let sessionId: String
    let cwd: String?
    let startedAt: Double?

    var id: String { sessionId }

    var startDate: Date? {
        guard let startedAt else { return nil }
        return Date(timeIntervalSince1970: startedAt / 1000)
    }
}
