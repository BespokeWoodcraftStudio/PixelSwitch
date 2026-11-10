// Ultra: the Max 20x plan, which Anthropic has taken the 5-hour limit away from.
//
// The founder, 2026-11-10: "Anthropic got rid of the 5 hour limit on Max 200
// accounts, now called Claude Ultra accounts. So that should be removed from
// those types of accounts." For such an account PixelSwitch stops showing,
// tracking and acting on the Session (5-hour) limit, and the plan badge says
// Ultra instead of Max. Anything it cannot tell is NOT Ultra and keeps the
// 5-hour limit and today's badge exactly.
//
// What is pinned here: the plan resolver (AccountPlan), the stored tier
// (Account, AuthStatus, the saved-login parser), the one strip point for the
// usage reading, the engine's behaviour for an account with no session window,
// the control protocol's account (one number for such an account, the flag over
// the wire), the CLI's table, usage and threshold replies, the menu bar modules,
// the widget's data, a reply from an app that predates the field, and the
// wiring and strings a view test cannot compile. Compiled into the unit harness
// by Tests/run-unit-tests.sh and run from main.swift. Uses the global `check`.
import Foundation

@MainActor func runAccountPlanTests() {
    resolveTests()
    badgeTests()
    storedPlanTests()
    stripTests()
    savedLoginTests()
    engineTests()
    controlTests()
    cliTests()
    menuBarTests()
    widgetTests()
    oldAppTests()
    wiringAndStringTests()
}

// MARK: - Fixtures

private func ultra(_ name: String = "ultra", active: Bool = false, session: Double? = nil, weekly: Double? = nil,
                   switchThreshold: Double? = nil) -> Account {
    Account(email: "\(name)@x.com", displayName: name, subscriptionType: "max", isActive: active,
            switchThreshold: switchThreshold, sessionThreshold: session, weeklyThreshold: weekly, planTier: "max_20x")
}

private func max5x(_ name: String = "five", active: Bool = false, session: Double? = nil, weekly: Double? = nil) -> Account {
    Account(email: "\(name)@x.com", displayName: name, subscriptionType: "max", isActive: active,
            sessionThreshold: session, weeklyThreshold: weekly, planTier: "max_5x")
}

private func decodeUsage(_ json: String) -> UsageAPIResponse {
    try! JSONDecoder().decode(UsageAPIResponse.self, from: Data(json.utf8))
}

private let t0 = Date(timeIntervalSince1970: 1_790_000_000)

private func iso(hours: Double) -> String {
    ISO8601DateFormatter().string(from: t0.addingTimeInterval(hours * 3600))
}

/// The shape the usage API sends for a Max 20x login since early November 2026:
/// `five_hour` is present and JSON null, and `limits` has no session entry.
private let ultraWireUsage = #"{"five_hour":null,"seven_day":{"utilization":15,"resets_at":"2026-11-14T10:00:00+00:00","limit_dollars":null},"seven_day_opus":null,"seven_day_sonnet":null,"limits":[{"kind":"weekly_all","group":"weekly","percent":15,"resets_at":"2026-11-14T10:00:00+00:00"},{"kind":"weekly_scoped","group":"weekly","percent":74,"resets_at":"2026-11-14T10:00:00+00:00","scope":{"model":{"id":null,"display_name":"Fable"}}}]}"#

// MARK: - Which plan an account is on

@MainActor private func resolveTests() {
    func plan(_ subscription: String?, _ tier: String?, _ displayName: String? = nil) -> AccountPlan {
        AccountPlan.resolve(subscriptionType: subscription, planTier: tier, planDisplayName: displayName)
    }

    // Ultra: Max with a 20x tier, in either spelling Claude uses.
    check(plan("max", "max_20x") == .ultra, "plan: Max with the planTier claude auth status prints (max_20x) is Ultra")
    check(plan("max", "default_claude_max_20x") == .ultra, "plan: Max with the rate-limit tier a saved login holds (default_claude_max_20x) is Ultra")
    check(plan("max", " MAX_20X ") == .ultra && plan("Max", "Default_Claude_Max_20x") == .ultra,
          "plan: the tier and the subscription are read without regard to case or surrounding spaces")
    check(plan("max", nil, "Ultra") == .ultra && plan("max", nil, " ultra ") == .ultra,
          "plan: Claude Code's own planDisplayName, Ultra, is enough (any case, spaces trimmed)")
    check(plan(nil, "max_20x") == .ultra, "plan: a 20x tier with no subscription type is Ultra (the type is simply not known yet)")

    // Not Ultra: everything that is not certain.
    check(plan("max", nil) == .other("max") && plan("max", nil).hasSessionLimit,
          "plan: Max with no tier is NOT Ultra (it may be Max 5x), so it keeps the 5-hour limit")
    check(plan("max", "max_5x") == .max5x && plan("max", "default_claude_max_5x") == .max5x,
          "plan: a 5x tier is Max 5x")
    check(plan("max", "max_5x", "Ultra") == .max5x, "plan: a tier that says 5x beats a display name that says Ultra (a conflict keeps the limit)")
    check(plan("pro", "default_claude_ai") == .pro && plan("pro", nil) == .pro, "plan: pro is Pro")
    check(plan("pro", "max_20x") == .pro && plan("pro", nil, "Ultra") == .pro, "plan: a Pro subscription is never Ultra, whatever else is stored")
    check(plan("team", "default_claude_max_20x") == .other("team"),
          "plan: a team subscription with a 20x tier is NOT Ultra")
    check(plan("enterprise", nil, "Ultra") == .other("enterprise"), "plan: an enterprise subscription with the display name Ultra is NOT Ultra")
    check(plan(nil, nil) == .unknown && plan("", "") == .unknown && plan("  ", nil, "  ") == .unknown, "plan: nothing known is unknown")
    check(plan("max", "garbage") == .other("max") && plan("max", "max_20xx") == .other("max") && plan("max", "20x") == .other("max"),
          "plan: a tier that is not one of the two 20x spellings is not Ultra")
    check(plan("max", nil, "Max") == .other("max") && plan("max", nil, "") == .other("max") && plan("max", nil, "Ultra Plus") == .other("max"),
          "plan: a display name that is not exactly Ultra is not Ultra")

    // The one question the rest of the app asks.
    check(AccountPlan.ultra.hasSessionLimit == false, "plan: Ultra has no 5-hour limit")
    check(AccountPlan.pro.hasSessionLimit && AccountPlan.max5x.hasSessionLimit && AccountPlan.other("team").hasSessionLimit && AccountPlan.unknown.hasSessionLimit,
          "plan: every other plan, and an unknown one, keeps the 5-hour limit")
    check(Account(email: "a@x.com", displayName: "A").hasSessionLimit
          && Account(email: "a@x.com", displayName: "A", subscriptionType: "max").hasSessionLimit
          && Account(email: "a@x.com", displayName: "A", subscriptionType: "pro", planTier: "default_claude_ai").hasSessionLimit,
          "plan: an account with no plan, with Max and no tier, or with Pro has the 5-hour limit")
    check(!ultra().hasSessionLimit && ultra().plan == .ultra && max5x().hasSessionLimit && max5x().plan == .max5x,
          "plan: an Account reads its plan from its subscription type and its stored tier")
}

// MARK: - The badge

@MainActor private func badgeTests() {
    check(AccountPlan.ultra.badgeText(subscriptionType: "max") == "Ultra", "badge: Ultra says Ultra, not Max")
    check(AccountPlan.ultra.badgeText(subscriptionType: nil) == "Ultra", "badge: Ultra says Ultra even with no subscription type")
    check(AccountPlan.other("max").badgeText(subscriptionType: "max") == "Max" && AccountPlan.max5x.badgeText(subscriptionType: "max") == "Max",
          "badge: Max 5x and Max with no tier still say Max")
    check(AccountPlan.pro.badgeText(subscriptionType: "pro") == "Pro", "badge: Pro still says Pro")
    check(AccountPlan.other("team").badgeText(subscriptionType: "team") == "Team", "badge: any other plan is its type with a capital letter, as before")
    check(AccountPlan.unknown.badgeText(subscriptionType: nil) == nil, "badge: no plan, no badge")

    check(ultra().displaySubscriptionType == "Ultra", "badge: an Ultra Account's displaySubscriptionType (every badge in the app) is Ultra")
    check(max5x().displaySubscriptionType == "Max" && Account(email: "a@x.com", displayName: "A", subscriptionType: "max").displaySubscriptionType == "Max"
          && Account(email: "a@x.com", displayName: "A", subscriptionType: "pro").displaySubscriptionType == "Pro"
          && Account(email: "a@x.com", displayName: "A").displaySubscriptionType == nil,
          "badge: Max 5x, Max with no tier, Pro and no plan read exactly as they always did")
    check(Account(email: "a@x.com", displayName: "A", subscriptionType: "team", planTier: "max_20x").displaySubscriptionType == "Team",
          "badge: a team account with a 20x tier is not relabelled Ultra")
}

// MARK: - The stored tier

@MainActor private func storedPlanTests() {
    // Saved accounts from before the tier existed decode with none, and a nil tier is never written.
    let old = #"[{"id":"CB7797E5-5257-402B-80D6-ADAE1220D368","email":"a@x.com","displayName":"A","provider":"Claude Code","subscriptionType":"max","isActive":true}]"#
    let decoded = try? JSONDecoder().decode([Account].self, from: Data(old.utf8))
    check(decoded?.count == 1 && decoded?.first?.planTier == nil && decoded?.first?.hasSessionLimit == true && decoded?.first?.displaySubscriptionType == "Max",
          "stored plan: an account saved before the tier existed decodes with none, keeps the 5-hour limit and still says Max")
    let plain = (try? JSONEncoder().encode([Account(email: "c@x.com", displayName: "C", subscriptionType: "max")])).map { String(decoding: $0, as: UTF8.self) } ?? ""
    check(!plain.contains("planTier"), "stored plan: an account with no tier saves no planTier key, so an older build reads it unchanged", plain)
    let roundTrip = (try? JSONEncoder().encode([ultra()])).flatMap { try? JSONDecoder().decode([Account].self, from: $0) }
    check(roundTrip?.first?.planTier == "max_20x" && roundTrip?.first?.plan == .ultra, "stored plan: the tier survives saving and loading")
    let saved = (try? JSONEncoder().encode([ultra()])).map { String(decoding: $0, as: UTF8.self) } ?? ""
    check(saved.contains(#""planTier":"max_20x""#), "stored plan: the tier is saved under the key planTier, the name claude auth status uses", saved)

    // `claude auth status` prints planTier; an older CLI does not.
    let status = #"{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","email":"a@x.com","orgId":"o","orgName":"Org","subscriptionType":"max","planTier":"max_20x","loginExpiresAt":"2026-11-11T00:00:00Z","storedClaudeAiLogin":true}"#
    let withTier = try? JSONDecoder().decode(AuthStatus.self, from: Data(status.utf8))
    check(withTier?.subscriptionType == "max" && withTier?.planTier == "max_20x", "stored plan: claude auth status's planTier is decoded")
    let noTier = try? JSONDecoder().decode(AuthStatus.self, from: Data(#"{"loggedIn":true,"authMethod":"claude.ai","email":"a@x.com","subscriptionType":"max"}"#.utf8))
    check(noTier?.planTier == nil && noTier?.subscriptionType == "max", "stored plan: an older claude auth status with no planTier still decodes")
    check(AuthStatus(loggedIn: true, authMethod: "claude.ai", apiProvider: nil, email: "a@x.com", orgId: nil, orgName: nil, subscriptionType: "max").planTier == nil,
          "stored plan: AuthStatus's memberwise initialiser still works without a planTier")

    // A tier the CLI reports wins; one it does not report leaves what is stored.
    check(AccountPlan.updatedTier(current: "max_20x", reported: "max_5x") == "max_5x", "stored plan: a reported tier replaces the stored one (a downgrade is picked up)")
    check(AccountPlan.updatedTier(current: "max_20x", reported: nil) == "max_20x" && AccountPlan.updatedTier(current: "max_20x", reported: "  ") == "max_20x",
          "stored plan: a CLI that reports no tier does not erase the one already stored")
    check(AccountPlan.updatedTier(current: nil, reported: " max_20x ") == "max_20x" && AccountPlan.updatedTier(current: nil, reported: nil) == nil,
          "stored plan: a first tier is stored trimmed; none stays none")
}

// MARK: - The one strip point for the usage reading

@MainActor private func stripTests() {
    let wire = decodeUsage(ultraWireUsage)
    check(wire.fiveHour == nil && wire.sevenDay?.utilization == 15, "strip: the real Max 20x reading, five_hour null, decodes with no session window")
    let strippedWire = wire.droppingSession()
    check(strippedWire.fiveHour == nil && strippedWire.sevenDay?.utilization == 15
          && strippedWire.modelWeeklyLimit(named: "Fable")?.percent == 74 && strippedWire.limits?.count == 2,
          "strip: on the null shape nothing changes: weekly and Fable are kept")

    let object = decodeUsage(#"{"five_hour":{"utilization":97,"resets_at":"2026-11-10T20:00:00+00:00"},"seven_day":{"utilization":15,"resets_at":"2026-11-14T10:00:00+00:00"},"extra_usage":{"is_enabled":true,"monthly_limit":5000,"used_credits":12,"utilization":0.2},"limits":[{"kind":"weekly_scoped","group":"weekly","percent":74,"scope":{"model":{"id":null,"display_name":"Fable"}}}]}"#)
    check(object.fiveHour?.utilization == 97, "strip: fixture: an object five_hour decodes")
    let strippedObject = object.droppingSession()
    check(strippedObject.fiveHour == nil && strippedObject.sevenDay?.utilization == 15 && strippedObject.extraUsage?.isEnabled == true
          && strippedObject.modelWeeklyLimit(named: "Fable")?.percent == 74,
          "strip: on a five_hour object (the day Anthropic sends one again) the session is dropped and weekly, Fable and extra usage are kept")

    let nullUtilization = decodeUsage(#"{"five_hour":{"utilization":null},"seven_day":{"utilization":15}}"#)
    check(nullUtilization.fiveHour != nil && nullUtilization.droppingSession().fiveHour == nil,
          "strip: a five_hour object with a null utilization (which the views would draw as 0%) is dropped too")
    let absent = decodeUsage(#"{"seven_day":{"utilization":15}}"#)
    check(absent.fiveHour == nil && absent.droppingSession().fiveHour == nil && absent.droppingSession().sevenDay?.utilization == 15,
          "strip: with five_hour absent nothing changes")

    // What the control protocol then says about the stripped reading.
    let info = ControlSnapshots.usageInfo(object.droppingSession(), sampledAt: nil, error: nil)
    let json = (try? JSONValue.encode(info)) ?? .null
    check(info?.session == nil && info?.weekly?.utilization == 15 && info?.fable?.utilization == 74 && json["session"] == nil,
          "strip: usage.get has no session for a stripped reading (the key is omitted, never sent as null)")
}

// MARK: - Reading the plan out of a saved login

@MainActor private func savedLoginTests() {
    let secret = "sk-ant-oat01-DO-NOT-LEAK"
    func credential(tier: String?, sub: String? = "max") -> String {
        var oauth = #""accessToken":"\#(secret)","refreshToken":"sk-ant-ort01-DO-NOT-LEAK","expiresAt":1790000000000"#
        if let sub { oauth += #","subscriptionType":"\#(sub)""# }
        if let tier { oauth += #","rateLimitTier":"\#(tier)""# }
        return #"{"claudeAiOauth":{"# + oauth + #"},"mcpOAuth":{}}"#
    }
    func tier(_ credential: String?, _ account: [String: Any]? = nil) -> String? {
        AccountPlan.tier(fromCredential: credential, oauthAccount: account)
    }

    check(tier(credential(tier: "default_claude_max_20x")) == "default_claude_max_20x", "saved login: the credential's rateLimitTier is the tier")
    check(tier(credential(tier: nil), ["organizationRateLimitTier": "default_claude_max_5x"]) == "default_claude_max_5x",
          "saved login: with none in the credential, the account block's organizationRateLimitTier is the tier")
    check(tier(credential(tier: "default_claude_max_20x"), ["organizationRateLimitTier": "default_claude_max_5x"]) == "default_claude_max_20x",
          "saved login: the credential's tier wins over the account block's")
    check(tier(credential(tier: nil), ["planDisplayName": "Ultra"]) == AccountPlan.ultraTier
          && AccountPlan.resolve(subscriptionType: "max", planTier: AccountPlan.ultraTier) == .ultra,
          "saved login: with no tier anywhere, Claude Code's display name Ultra is stored as the canonical 20x tier")
    check(tier(credential(tier: nil), ["planDisplayName": "Max"]) == nil && tier(credential(tier: nil), [:]) == nil && tier(credential(tier: nil)) == nil,
          "saved login: nothing recognisable gives no tier (the account stays unknown and keeps the 5-hour limit)")
    check(tier(credential(tier: ""), ["organizationRateLimitTier": "  "]) == nil && tier(credential(tier: nil), ["organizationRateLimitTier": 20]) == nil,
          "saved login: a blank or non-text tier is ignored")
    check(tier(nil) == nil && tier("not json") == nil && tier("[1,2]") == nil && tier(#"{"claudeAiOauth":"x"}"#) == nil,
          "saved login: text that is not a credential gives no tier")
    check(tier(credential(tier: "default_claude_max_20x"))?.contains("DO-NOT-LEAK") == false && tier(credential(tier: "default_claude_max_20x"))?.contains("sk-ant") == false,
          "saved login: only the tier text is returned, never a token")
}

// MARK: - The unchanged engine, fed an account with no session window

@MainActor private func engineTests() {
    func windowUsage(_ weekly: Double, session: Double? = nil) -> UsageAPIResponse {
        let five = session.map { #"{"utilization":\#($0),"resets_at":"\#(iso(hours: 2))"}"# } ?? "null"
        return decodeUsage(#"{"five_hour":"# + five + #","seven_day":{"utilization":\#(weekly),"resets_at":"\#(iso(hours: 100))"}}"#)
    }
    // The founder's own limits: 91% session, 97% weekly.
    let defaults = AutoSwitchEngine.WindowPair(session: 91, weekly: 97)
    func plan(active: Account, candidates: [Account], _ usage: [UUID: UsageAPIResponse]) -> String {
        let result = AutoSwitchEngine.planWindowed(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: { _ in true }, activeSampledThisCycle: true,
            rule: { AutoSwitchSettings.rule(for: $0, defaults: defaults) }, defaults: defaults,
            hysteresisPct: AutoSwitchEngine.hysteresis, watchFable: true, strategy: .mostRoom, asOf: t0)
        guard let result else { return "stay" }
        return "\(result.limit.rawValue)/\(result.trigger.rawValue)/\(result.window?.rawValue ?? "-"): " + result.targets.map(\.displayName).joined(separator: ",")
    }

    let active = ultra("active", active: true)
    let other = ultra("other")
    let five = max5x("five")
    check(plan(active: active, candidates: [other], [active.id: windowUsage(97), other.id: windowUsage(40)]) == "windows/threshold/weekly: other",
          "engine: an Ultra account at 97% weekly with no session window leaves at its weekly limit, for an account with room")
    check(plan(active: active, candidates: [other], [active.id: windowUsage(96), other.id: windowUsage(40)]) == "stay",
          "engine: at 96% weekly, under its limit, it stays: the missing session never fires (the session default of 91 is not applied to it)")
    check(plan(active: active, candidates: [five], [active.id: windowUsage(97), five.id: windowUsage(40, session: 99)]) == "stay",
          "engine: a Max 5x account over its own session limit is still not a target, so the session limit still guards accounts that have one")
    check(plan(active: active, candidates: [five], [active.id: windowUsage(97), five.id: windowUsage(40, session: 20)]) == "windows/threshold/weekly: five",
          "engine: a Max 5x account with room on both windows is a target for an Ultra account")

    // Why the strip matters: a session reading on a 5-hour-less account would fire a limit it does not have.
    let objectOnUltra = windowUsage(50, session: 95)
    check(plan(active: active, candidates: [other], [active.id: objectOnUltra, other.id: windowUsage(40)]) == "windows/threshold/session: other",
          "engine: (the reason for the strip) an unstripped session reading of 95% would move an Ultra account off at the 91% default")
    check(plan(active: active, candidates: [other], [active.id: objectOnUltra.droppingSession(), other.id: windowUsage(40)]) == "stay",
          "engine: after the strip the same reading moves nothing, so a returning five_hour object cannot make an Ultra account switch on a limit it does not have")
    let ultraWire = decodeUsage(ultraWireUsage)
    check(plan(active: active, candidates: [other], [active.id: ultraWire, other.id: ultraWire]) == "stay",
          "engine: the real Max 20x reading (15% weekly, five_hour null) stays put")
}

// MARK: - The control protocol

@MainActor private func controlTests() {
    let defaults = AutoSwitchEngine.WindowPair(session: 91, weekly: 97)
    func info(_ account: Account, usage: UsageInfo? = nil) -> AccountInfo {
        let rule = AutoSwitchSettings.rule(for: account, defaults: defaults)
        return ControlSnapshots.accountInfo(account, position: 1, isSwitchable: true,
                                            leaveAt: AutoSwitchSettings.leaveAtPair(rule: rule, defaults: defaults), usage: usage)
    }

    let u = info(ultra())
    check(u.subscription == "Ultra" && u.hasSessionLimit == false, "control: an Ultra account says Ultra and hasSessionLimit false")
    check(u.effectiveThreshold == 97 && u.effectiveWeeklyThreshold == 97,
          "control: its one threshold, effectiveThreshold, is the weekly leave-at (97), not the lower of 91 and 97")
    check(u.sessionThreshold == nil && u.effectiveSessionThreshold == nil, "control: an Ultra account has no session threshold and no effective session threshold")
    let stored = info(ultra(session: 80, weekly: 60))
    check(stored.sessionThreshold == nil && stored.effectiveSessionThreshold == nil && stored.weeklyThreshold == 60 && stored.effectiveThreshold == 60 && stored.effectiveWeeklyThreshold == 60,
          "control: a session value stored on an Ultra account is kept but not reported; its weekly value is the one number")
    let manual = info(ultra(switchThreshold: 0))
    check(manual.manualOnly && manual.effectiveThreshold == 97 && manual.effectiveSessionThreshold == nil && manual.hasSessionLimit == false,
          "control: a Manual only Ultra account is left at the weekly default (97)")
    let json = (try? JSONValue.encode(u)) ?? .null
    check(json["hasSessionLimit"] == .bool(false) && json["sessionThreshold"] == nil && json["effectiveSessionThreshold"] == nil && json["effectiveWeeklyThreshold"] == .number(97),
          "control: on the wire hasSessionLimit is false and the two session numbers are left out", json.compactText)

    let five = info(max5x(session: 80))
    check(five.hasSessionLimit == true && five.subscription == "Max" && five.effectiveThreshold == 80 && five.sessionThreshold == 80 && five.effectiveSessionThreshold == 80,
          "control: a Max 5x account is unchanged: hasSessionLimit true, the lower of its two limits, its session numbers present")
    let unknown = info(Account(email: "u@x.com", displayName: "U", subscriptionType: "max"))
    check(unknown.hasSessionLimit == true && unknown.subscription == "Max" && unknown.effectiveSessionThreshold == 91 && unknown.effectiveThreshold == 91,
          "control: a Max account whose tier is not known keeps the 5-hour limit and says Max")
    let noPlan = info(Account(email: "n@x.com", displayName: "N"))
    check(noPlan.hasSessionLimit == true && noPlan.subscription == nil, "control: an account with no plan keeps the 5-hour limit")

    // accountsChanged fires when the plan changes, and not when nothing does.
    typealias Fields = ControlSnapshots.AccountFields
    let a = ultra("same")
    var b = a
    check(Fields(a) == Fields(b), "control: an unchanged account is unchanged for accountsChanged")
    b.planTier = "max_5x"
    check(Fields(a) != Fields(b), "control: an account that stops being Ultra changes its fields, so accountsChanged fires")
    var c = a
    c.planTier = "default_claude_max_20x"
    check(Fields(a) == Fields(c), "control: a tier respelled to the same plan is not a change")

    check(ControlProtocol.version == 2 && ControlProtocol.revision == 4 && ControlProtocol.features == ["priority", "splitThresholds"],
          "control: the new field is additive: protocol stays 2, the revision is 4 and no new feature is announced")
}

// MARK: - The command line

@MainActor private func cliTests() {
    let defaults = AutoSwitchEngine.WindowPair(session: 91, weekly: 97)
    func info(_ account: Account, usage: UsageInfo? = nil, position: Int = 1) -> AccountInfo {
        let rule = AutoSwitchSettings.rule(for: account, defaults: defaults)
        return ControlSnapshots.accountInfo(account, position: position, isSwitchable: true,
                                            leaveAt: AutoSwitchSettings.leaveAtPair(rule: rule, defaults: defaults), usage: usage)
    }
    /// The same account as a CLI sees it: through the wire, so nothing app-only is needed.
    func overTheWire(_ account: AccountInfo) -> AccountInfo {
        let value = try! JSONValue.encode(account)
        return try! value.decode(AccountInfo.self)
    }
    let readings = UsageInfo(session: nil, weekly: WindowInfo(utilization: 15, resetsAt: "2026-11-14T10:00:00+00:00"),
                             fable: WindowInfo(utilization: 74, resetsAt: nil), extraUsage: nil, sampledAt: nil, error: nil)
    let ultraRow = overTheWire(info(ultra("alice", active: true), usage: readings, position: 1))
    let fiveRow = overTheWire(info(max5x("bob"), usage: UsageInfo(session: WindowInfo(utilization: 42, resetsAt: nil), weekly: WindowInfo(utilization: 61, resetsAt: nil), fable: nil, extraUsage: nil, sampledAt: nil, error: nil), position: 2))
    let waitingRow = overTheWire(info(max5x("carol"), position: 3))
    check(ultraRow.hasSessionLimit == false && fiveRow.hasSessionLimit == true, "cli: the flag arrives over the wire")

    let lines = CLIOutput.accounts([ultraRow, fiveRow, waitingRow]).split(separator: "\n").map(String.init)
    /// A table row's cells, counted from the end (the marker column is empty, and so trimmed away, on rows that are not active):
    /// Session, Weekly, Fable, Switches at, Priority.
    func cells(_ line: String) -> [String] { line.components(separatedBy: "  ").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
    func sessionCell(_ line: String) -> String { let c = cells(line); return c[c.count - 5] }
    let alice = lines[1], bob = lines[2], carol = lines[3]
    check(sessionCell(alice) == "n/a" && cells(alice)[cells(alice).count - 4] == "15%", "cli: an Ultra account's Session cell reads n/a and its Weekly cell its reading", alice)
    check(alice.contains("97% (default)") && !alice.contains("5h ") && !alice.contains("7d "),
          "cli: its Switches at is one number, 97%, never 5h 91% / 7d 97%", alice)
    check(sessionCell(bob) == "42%" && bob.contains("5h 91% / 7d 97% (default)"),
          "cli: a Max 5x account's Session cell is its reading, and its Switches at keeps both windows", bob)
    check(sessionCell(carol) == "—", "cli: a Max 5x account with no reading yet still shows a dash, not n/a", carol)
    check(lines.contains { $0.hasPrefix("n/a") && $0.contains("5-hour") },
          "cli: when a row says n/a, the legend under the table says why", lines.joined(separator: "\n"))
    check(!CLIOutput.accounts([fiveRow, waitingRow]).contains("n/a"), "cli: with no Ultra account the table and legend are exactly as before")

    let usage = CLIOutput.usage(UsageReport(accounts: [ultraRow, fiveRow], machine: MachineUsageInfo(todayCost: 1, totalCost: 2, conversationTurns: 3, activeCodingMinutes: 4, linesWritten: 5, modelUsage: [:]), lastRefresh: nil))
    let usageLines = usage.split(separator: "\n").map(String.init)
    let ultraBlock = Array(usageLines[0..<usageLines.firstIndex { $0.hasPrefix("2.") }!])
    check(!ultraBlock.contains { $0.contains("Session") } && ultraBlock.contains { $0.contains("Weekly") && $0.contains("15%") } && ultraBlock.contains { $0.contains("Fable") },
          "cli: usage leaves out the Session line for an Ultra account and keeps Weekly and Fable", usage)
    check(usageLines.contains { $0.contains("Session") && $0.contains("42%") }, "cli: usage still prints the Session line for a Max 5x account", usage)

    // accounts threshold <account> --session N on an Ultra account: accepted and stored, and the reply says it has no effect.
    let said = CLIOutput.thresholdSet(ultraRow, sessionWasSet: true)
    check(said.contains("alice@x.com") && said.contains("97%") && said.contains("no 5-hour limit") && said.contains("no effect"),
          "cli: setting --session on an Ultra account says it has no 5-hour limit, so the value has no effect", said)
    check(CLIOutput.thresholdSet(ultraRow) == "alice@x.com now follows the default threshold (97%).",
          "cli: setting only the weekly limit on an Ultra account gets the ordinary reply, with the weekly number")
    check(CLIOutput.thresholdSet(fiveRow, sessionWasSet: true) == CLIOutput.thresholdSet(fiveRow),
          "cli: --session on a Max 5x account gets the ordinary reply")
    let weeklyOnly = overTheWire(info(ultra("dan", weekly: 60)))
    check(CLIOutput.thresholdSet(weeklyOnly) == "dan@x.com now switches at 60%.", "cli: an Ultra account with its own weekly limit switches at that number")
    check(CLIOutput.leaveWords(ultraRow) == "97%", "cli: an Ultra account is left at one number in words")
}

// MARK: - The menu bar

@MainActor private func menuBarTests() {
    let sessionModules: Set<MenuBarModule> = [.sessionBar, .sessionBarPlain, .sessionReset]
    check(Set(MenuBarModule.allCases.filter(\.usesSessionWindow)) == sessionModules,
          "menu bar: exactly the three session modules use the session window")
    let saved: [MenuBarModule] = [.account, .sessionBarPlain, .weeklyBar, .sessionReset, .fableBar, .sessionBar]
    check(MenuBarModule.visibleModules(saved, activeHasSessionLimit: false) == [.account, .weeklyBar, .fableBar],
          "menu bar: while an Ultra account is active the session modules are not drawn, and the others keep their order")
    check(MenuBarModule.visibleModules(saved, activeHasSessionLimit: true) == saved,
          "menu bar: with a Max 5x, unknown or no active account every saved module is drawn")
    check(MenuBarModuleStore.decode(MenuBarModuleStore.encode(saved)) == saved, "menu bar: the user's saved module list is untouched (filtering returns a new list)")
    check(MenuBarModule.visibleModules([], activeHasSessionLimit: false).isEmpty && MenuBarModule.visibleModules([.weeklyBar], activeHasSessionLimit: false) == [.weeklyBar],
          "menu bar: an empty list and a list with no session module are unchanged")
}

// MARK: - The widget

@MainActor private func widgetTests() {
    let old = #"{"email":"a@x.com","displayName":"A","subscriptionType":"Max","isActive":true,"sessionUtilization":42,"sessionResetTime":"2 hr","weeklyUtilization":10,"weeklyResetTime":"3 d","extraUsageEnabled":false,"hasError":false}"#
    let decodedOld = try? JSONDecoder().decode(WidgetAccountData.self, from: Data(old.utf8))
    check(decodedOld != nil && decodedOld?.hasSessionLimit == nil && decodedOld?.showsSession == true,
          "widget: widget data written by an older app (no hasSessionLimit) decodes and still shows the Session views")
    let data = WidgetAccountData(email: "a@x.com", displayName: "A", subscriptionType: "Ultra", isActive: true, sessionUtilization: nil, sessionResetTime: nil,
                                 weeklyUtilization: 15, weeklyResetTime: "3 d", extraUsageEnabled: nil, hasError: false, errorMessage: nil, hasSessionLimit: false)
    let back = (try? JSONEncoder().encode(data)).flatMap { try? JSONDecoder().decode(WidgetAccountData.self, from: $0) }
    check(back?.hasSessionLimit == false && back?.showsSession == false && back?.subscriptionType == "Ultra",
          "widget: an Ultra account's data round trips and hides the Session views")
    check(WidgetAccountData(email: "a", displayName: "A", subscriptionType: nil, isActive: false, sessionUtilization: 1, sessionResetTime: nil, weeklyUtilization: 2,
                            weeklyResetTime: nil, extraUsageEnabled: nil, hasError: false, errorMessage: nil).showsSession,
          "widget: data built without the flag shows the Session views (the initialiser's default is unchanged)")
}

// MARK: - An app that predates the field

@MainActor private func oldAppTests() {
    let reply = #"{"accounts":[{"effectiveThreshold":98,"email":"alice@work.com","id":"11111111-1111-1111-1111-111111111111","isActive":true,"isSwitchable":true,"manualOnly":false,"position":1,"subscription":"Max","usage":{"sampledAt":"2026-11-10T15:00:00Z","session":{"resetsAt":"2026-11-10T18:00:00+00:00","utilization":42},"weekly":{"resetsAt":"2026-11-14T09:00:00+00:00","utilization":61}}}]}"#
    let value = try? ControlCoding.decoder.decode(JSONValue.self, from: Data(reply.utf8))
    let decoded = CLIRunner.accounts(value?["accounts"])
    check(decoded.count == 1 && decoded[0].hasSessionLimit == nil, "old app: a reply with no hasSessionLimit decodes, with the field absent")
    let table = CLIOutput.accounts(decoded)
    check(table.contains("42%") && !table.contains("n/a") && table.contains("98% (default)"),
          "old app: an app that never sends the flag is read as having the 5-hour limit: its Session reading is printed", table)
    let usage = CLIOutput.usage(UsageReport(accounts: decoded, machine: MachineUsageInfo(todayCost: 0, totalCost: 0, conversationTurns: 0, activeCodingMinutes: 0, linesWritten: 0, modelUsage: [:]), lastRefresh: nil))
    check(usage.contains("Session 42%"), "old app: and usage prints its Session line", usage)
    check(CLIOutput.thresholdSet(decoded[0], sessionWasSet: true) == CLIOutput.thresholdSet(decoded[0]),
          "old app: --session against an app that never sends the flag gets the ordinary reply")
}

// MARK: - Wiring a view test cannot compile, and the strings

private func planSource(_ path: String) -> String {
    (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
}

private func planMember(_ source: String, startingAt signature: String) -> String {
    guard let start = source.range(of: signature) else { return "" }
    let rest = source[start.lowerBound...].dropFirst(signature.count)
    let ends = ["\n    func ", "\n    private func ", "\n    /// ", "\n    @discardableResult", "\n    // MARK", "\n    static func ", "\n    @MainActor"]
        .compactMap { rest.range(of: $0)?.lowerBound }
    return String(source[start.lowerBound..<(ends.min() ?? rest.endIndex)])
}

@MainActor private func wiringAndStringTests() {
    let app = planSource("PixelSwitch/AppState.swift")
    let engine = planSource("PixelSwitch/Services/AutoSwitchEngine.swift")
    check(!app.isEmpty && !engine.isEmpty, "ultra pins: the source files are found from the repo root")

    // One strip point: the only place a usage request is made.
    let request = planMember(app, startingAt: "private func requestUsage(")
    check(request.contains("getUsageLimits(accessToken:") && request.contains("droppingSession()") && request.contains("hasSessionLimit"),
          "ultra pins: requestUsage, the one place a usage request is made, drops the session reading of an account with no 5-hour limit")
    check(app.components(separatedBy: "getUsageLimits(accessToken:").count == 2, "ultra pins: requestUsage is still the only caller of getUsageLimits")

    // The plan is filled wherever the subscription type is copied.
    check(app.components(separatedBy: "planTier: status.planTier").count >= 4,
          "ultra pins: a new account (add current, sign in new, auto-created) carries the tier claude auth status reported")
    let update = planMember(app, startingAt: "private func updateActiveAccount(from status: AuthStatus) {")
    check(update.contains("accounts[index].planTier = AccountPlan.updatedTier(current: accounts[index].planTier, reported: status.planTier)"),
          "ultra pins: refreshing the active account updates its tier from claude auth status without erasing it when the CLI reports none")
    check(app.components(separatedBy: "accounts[index].planTier = AccountPlan.updatedTier(current: accounts[index].planTier, reported: status.planTier)").count == 3,
          "ultra pins: a re-sign updates the tier the same way")
    let backfill = planMember(app, startingAt: "private func backfillPlans() async {")
    check(backfill.contains("allAccountBackups()") && backfill.contains("AccountPlan.tier(fromCredential:") && backfill.contains("planTier == nil")
          && !backfill.contains("log.info(\"\\(backup") && !backfill.contains(".token)"),
          "ultra pins: accounts with no tier get one from their saved login, filling only blanks, and the token is never logged")
    let refresh = planMember(app, startingAt: "private func refreshData() async -> Bool {")
    check(refresh.contains("await backfillPlans()"), "ultra pins: the backfill runs from the refresh, after the active account's tier is known")
    let widget = planMember(app, startingAt: "private func updateWidgetData() {")
    check(widget.contains("hasSessionLimit: account.hasSessionLimit"), "ultra pins: the widget file carries the flag")

    // The engine, the conformance vectors and the Linux port are not touched.
    check(!engine.contains("hasSessionLimit") && !engine.contains("AccountPlan"), "ultra pins: the engine knows nothing of plans: it already copes with a missing session window")

    // Views the harness cannot compile.
    let strip = planSource("PixelSwitch/Views/MenuBarStripView.swift")
    check(strip.contains("MenuBarModule.visibleModules(config.modules, activeHasSessionLimit:") && !strip.contains("ForEach(config.modules)"),
          "ultra pins: the menu bar strip draws config.modules through visibleModules")
    let dashboard = planSource("PixelSwitch/Views/UsageDashboardView.swift")
    check(dashboard.contains("usageBars(usage, showsSession: account.hasSessionLimit)") && dashboard.contains("if showsSession, let session = usage.fiveHour"),
          "ultra pins: the Usage card draws the Session row only for an account with a 5-hour limit")
    let tab = planSource("PixelSwitch/Views/SettingsAccountsTab.swift")
    check(tab.contains("hasSessionLimit: account.hasSessionLimit"), "ultra pins: Settings -> Accounts tells each row whether it has a 5-hour limit")
    let row = planSource("PixelSwitch/Views/SettingsAccountRow.swift")
    check(row.contains("var hasSessionLimit = true") && row.contains("No 5-hour limit") && row.contains("if hasSessionLimit {"),
          "ultra pins: the row's Session menu is replaced by the caption No 5-hour limit when the account has none, and the init stays source compatible")
    let widgetViews = planSource("PixelSwitchWidget/PixelSwitchWidget.swift")
    check(widgetViews.components(separatedBy: "if account.showsSession {").count == 5,
          "ultra pins: all four of the widget's Session views (compact bar, medium bar, list bar, ring) are hidden for an account with no 5-hour limit")
    let moduleView = planSource("PixelSwitch/Views/MenuBarModuleView.swift")
    check(!moduleView.isEmpty && moduleView.contains("fiveHour"), "ultra pins: the module view still reads fiveHour for accounts that have one")
    let cli = planSource("PixelSwitchCLI/CLIRunner.swift") + planSource("PixelSwitchCLI/MCPServer.swift")
    check(!cli.contains("AccountPlan") && !cli.contains("account.plan") && !cli.contains("AutoSwitchSettings"),
          "ultra pins: the command-line tool uses only what the protocol carries, never the app's AccountPlan (its target cannot see it)")

    // Strings: the new text is in all five tables, translated.
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    let key = "No 5-hour limit"
    check(tables.allSatisfy { ($0[key] ?? "").isEmpty == false }, "ultra strings: 'No 5-hour limit' is in all five tables")
    check(tables[0][key] == key && zip(tables.dropFirst(), languages.dropFirst()).allSatisfy { $0.0[key] != key },
          "ultra strings: the English line is the key and de, fr, ja and zh-Hans are translated, not copied")
    check(tables.allSatisfy { Set($0.keys) == Set(tables[0].keys) }, "ultra strings: the five tables still have exactly the same keys")
}
