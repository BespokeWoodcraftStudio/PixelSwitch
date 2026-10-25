// 2.0 lane MB: the remote-control face of separate session and weekly
// thresholds and priority numbers (spec section 4): the two new methods and
// their presence rules, the new settings keys and the strategy alias, the new
// events, the CLI and MCP surfaces, the check against an old app, a 1.9 payload
// decoding in the new CLI, and the new strings. Called from ControlTests.swift.
// Uses the global `check`.
import Foundation

@MainActor func runControlSplitTests() {
    runSplitThresholdAPITests()
    runPriorityAPITests()
    runSplitSettingsTests()
    runSplitEventTests()
    runSplitCLIParserTests()
    runSplitCLIOutputTests()
    runSplitMCPTests()
    runOldAppTests()
    runNewAppEndToEndTests()
    runSplitStringTests()
}

private typealias Kit = ControlTestKit

private func kind(_ response: RPCResponse?) -> String? { response?.error.map { ControlError(rpc: $0).kind.rawValue } }

@MainActor private func account(_ app: Kit.FakeApp, _ reference: String, api: ControlAPI) -> JSONValue? {
    Kit.call(api, "accounts.list")?.result?["accounts"]?.arrayValue?.first { $0["email"]?.stringValue == reference }
}

// MARK: - accounts.setThresholds

@MainActor private func runSplitThresholdAPITests() {
    let app = Kit.FakeApp()
    let api = ControlAPI(controller: app)
    func set(_ account: String, _ fields: [String: JSONValue]) -> RPCResponse? {
        var params = fields
        params["account"] = .string(account)
        return Kit.call(api, "accounts.setThresholds", .object(params))
    }

    // The founder's example: 90 for the 5-hour limit, 50 for the week.
    let founder = set("alice@work.com", ["session": .number(90), "weekly": .number(50)])?.result?["account"]
    check(founder?["sessionThreshold"] == .number(90) && founder?["weeklyThreshold"] == .number(50)
          && founder?["effectiveSessionThreshold"] == .number(90) && founder?["effectiveWeeklyThreshold"] == .number(50)
          && founder?["effectiveThreshold"] == .number(50) && founder?["manualOnly"] == .bool(false) && founder?["threshold"] == nil,
          "setThresholds: 90 / 50 sets both windows; effectiveThreshold (what a 1.9 reader sees) is the lower, 50", founder?.compactText ?? "nil")

    // Presence: an absent key is left alone, null follows the default.
    let weeklyOnly = set("alice@work.com", ["weekly": .number(60)])?.result?["account"]
    check(weeklyOnly?["sessionThreshold"] == .number(90) && weeklyOnly?["weeklyThreshold"] == .number(60),
          "setThresholds: a key left out is left alone", weeklyOnly?.compactText ?? "nil")
    let cleared = set("alice@work.com", ["session": .null])?.result?["account"]
    check(cleared?["sessionThreshold"] == nil && cleared?["weeklyThreshold"] == .number(60) && cleared?["effectiveSessionThreshold"] == .number(90),
          "setThresholds: null makes that window follow the default again (and only that window)", cleared?.compactText ?? "nil")
    check(app.calls.last == "thresholds clear leave", "setThresholds: the app sees null as clear and an absent key as leave", app.calls.last ?? "")

    // Neither key present: invalidParams, and nothing reaches the app.
    let before = app.calls.count
    let neither = set("alice@work.com", [:])
    check(kind(neither) == "invalidParams" && app.calls.count == before, "setThresholds: neither window named is invalidParams")
    check(kind(Kit.call(api, "accounts.setThresholds", .object(["session": .number(80)]))) == "invalidParams", "setThresholds: no account is invalidParams")
    check(kind(set("nobody", ["session": .number(80)])) == "notFound", "setThresholds: an unknown account is notFound")

    // Values: a number rounds half away from zero, then must be 1-100.
    let message = "A threshold must be 1–100, or null to follow the default. For manual only use accounts.setThreshold with 0."
    for (label, value) in [("0", JSONValue.number(0)), ("-5", .number(-5)), ("101", .number(101)), ("0.4", .number(0.4)),
                           ("a string", .string("80")), ("a boolean", .bool(true)), ("a list", .array([]))] {
        let refused = set("alice@work.com", ["session": value])
        check(kind(refused) == "invalidValue" && refused?.error?.message == message,
              "setThresholds: \(label) is refused with the exact text", refused?.error?.message ?? "nil")
    }
    check(set("alice@work.com", ["session": .number(100.4)])?.result?["account"]?["sessionThreshold"] == .number(100)
          && set("alice@work.com", ["session": .number(0.6)])?.result?["account"]?["sessionThreshold"] == .number(1),
          "setThresholds: 100.4 is 100, 0.6 is 1")
    // 48.5 and 2.5 are the halves that round-half-to-even gets wrong (it gives 48 and 2); the Python port's round() is that rule.
    check(set("alice@work.com", ["session": .number(48.5)])?.result?["account"]?["sessionThreshold"] == .number(49)
          && set("alice@work.com", ["session": .number(2.5)])?.result?["account"]?["sessionThreshold"] == .number(3)
          && set("alice@work.com", ["session": .number(49.5)])?.result?["account"]?["sessionThreshold"] == .number(50),
          "setThresholds: 48.5 is 49, 2.5 is 3 and 49.5 is 50 (half away from zero, not half to even)")
    _ = set("alice@work.com", ["session": .number(1)])
    let refusedSecond = set("alice@work.com", ["session": .number(70), "weekly": .number(0)])
    check(kind(refusedSecond) == "invalidValue" && account(app, "alice@work.com", api: api)?["sessionThreshold"] == .number(1),
          "setThresholds: one bad value refuses the whole request; nothing is half applied")

    // The split of a both-windows value (spec 2.1): bob has 70 for both.
    let bob = set("bob@home.com", ["session": .null])?.result?["account"]
    check(bob?["sessionThreshold"] == nil && bob?["weeklyThreshold"] == .number(70) && bob?["threshold"] == nil
          && bob?["effectiveSessionThreshold"] == .number(90) && bob?["effectiveWeeklyThreshold"] == .number(70),
          "setThresholds: 70 for both, Session set to Default: session follows the default (90), weekly is 70, the both value is gone",
          bob?.compactText ?? "nil")

    // Legacy writes mean both windows and clear the window values (D8), even for the same number.
    _ = Kit.call(api, "accounts.setThreshold", .object(["account": .string("bob@home.com"), "threshold": .number(70)]))
    let legacy = account(app, "bob@home.com", api: api)
    check(legacy?["threshold"] == .number(70) && legacy?["sessionThreshold"] == nil && legacy?["weeklyThreshold"] == nil
          && legacy?["effectiveSessionThreshold"] == .number(70) && legacy?["effectiveWeeklyThreshold"] == .number(70),
          "setThreshold: the old method sets both windows and clears any separate window value", legacy?.compactText ?? "nil")
    _ = set("bob@home.com", ["weekly": .number(40)])
    _ = Kit.call(api, "accounts.setThreshold", .object(["account": .string("bob@home.com"), "threshold": .null]))
    let followed = account(app, "bob@home.com", api: api)
    check(followed?["threshold"] == nil && followed?["sessionThreshold"] == nil && followed?["weeklyThreshold"] == nil && followed?["effectiveThreshold"] == .number(90),
          "setThreshold: null on the old method clears window values too")

    // Manual only: only a number clears it; null alone changes nothing.
    _ = Kit.call(api, "accounts.setThreshold", .object(["account": .string("carol@work.com"), "threshold": .number(0)]))
    let stillManual = set("carol@work.com", ["session": .null])?.result?["account"]
    check(stillManual?["manualOnly"] == .bool(true) && stillManual?["threshold"] == .number(0) && stillManual?["sessionThreshold"] == nil,
          "setThresholds: null on a Manual only account changes nothing", stillManual?.compactText ?? "nil")
    let woken = set("carol@work.com", ["weekly": .number(55)])?.result?["account"]
    check(woken?["manualOnly"] == .bool(false) && woken?["weeklyThreshold"] == .number(55) && woken?["sessionThreshold"] == nil
          && woken?["threshold"] == nil && woken?["effectiveSessionThreshold"] == .number(90) && woken?["effectiveWeeklyThreshold"] == .number(55),
          "setThresholds: a number on a Manual only account clears Manual only, sets that window, and the other follows its default", woken?.compactText ?? "nil")

    // Manual only reports the defaults as where it is left, per window.
    app.windowDefaults = AutoSwitchEngine.WindowPair(session: 90, weekly: 50)
    _ = Kit.call(api, "accounts.setThreshold", .object(["account": .string("carol@work.com"), "threshold": .number(0)]))
    let manualSplit = account(app, "carol@work.com", api: api)
    check(manualSplit?["manualOnly"] == .bool(true) && manualSplit?["effectiveSessionThreshold"] == .number(90)
          && manualSplit?["effectiveWeeklyThreshold"] == .number(50) && manualSplit?["effectiveThreshold"] == .number(50),
          "accounts.list: a Manual only account is left at the two defaults", manualSplit?.compactText ?? "nil")
    app.windowDefaults = AutoSwitchEngine.WindowPair(both: 90)
}

// MARK: - accounts.setPriority and setOrder

@MainActor private func runPriorityAPITests() {
    let app = Kit.FakeApp()
    let api = ControlAPI(controller: app)
    func setPriority(_ account: String, _ value: JSONValue?) -> RPCResponse? {
        var params: [String: JSONValue] = ["account": .string(account)]
        if let value { params["priority"] = value }
        return Kit.call(api, "accounts.setPriority", .object(params))
    }
    func order(_ response: RPCResponse?) -> [String] {
        response?.result?["accounts"]?.arrayValue?.compactMap { $0["email"]?.stringValue } ?? []
    }
    func priorities(_ response: RPCResponse?) -> [Int?] {
        response?.result?["accounts"]?.arrayValue?.map { $0["priority"]?.doubleValue.map { Int($0) } } ?? []
    }

    // The founder's example: one account is 3, another is 1.
    var response = setPriority("alice@work.com", .number(3))
    check(order(response) == ["alice@work.com", "bob@home.com", "carol@work.com"] && priorities(response) == [3, nil, nil],
          "setPriority: a number is stored as typed (3), not as a list position", "\(order(response)) \(priorities(response))")
    response = setPriority("bob@home.com", .number(1))
    check(order(response) == ["bob@home.com", "alice@work.com", "carol@work.com"] && priorities(response) == [1, 3, nil],
          "setPriority: setting another account to 1 sorts it first and leaves the 3 as 3 (spec D2)", "\(order(response)) \(priorities(response))")
    check(response?.result?["accounts"]?.arrayValue?.map { $0["position"]?.doubleValue } == [1, 2, 3],
          "setPriority: the reply's position is the place in the list, whatever the number")
    response = setPriority("carol@work.com", .number(3))
    check(order(response) == ["bob@home.com", "carol@work.com", "alice@work.com"],
          "setPriority: equal numbers: the account set most recently goes first (spec D2)", order(response).joined(separator: ","))
    response = setPriority("carol@work.com", .null)
    check(order(response) == ["bob@home.com", "alice@work.com", "carol@work.com"] && priorities(response) == [1, 3, nil],
          "setPriority: null clears the number; an unnumbered account comes after every numbered one")
    check(app.calls.last == "priority nil", "setPriority: null reaches the app as none", app.calls.last ?? "")

    // The key must be present; the value must be a whole number 1-99 or null.
    let before = app.calls.count
    check(kind(setPriority("1", nil)) == "invalidParams" && app.calls.count == before, "setPriority: no priority key is invalidParams")
    check(kind(Kit.call(api, "accounts.setPriority", .object(["priority": .number(2)]))) == "invalidParams", "setPriority: no account is invalidParams")
    check(kind(setPriority("nobody", .number(2))) == "notFound", "setPriority: an unknown account is notFound")
    let message = "A priority must be a whole number from 1 to 99, or null for none."
    for (label, value) in [("0", JSONValue.number(0)), ("100", .number(100)), ("-1", .number(-1)), ("2.5", .number(2.5)),
                           ("a string", .string("3")), ("a boolean", .bool(true))] {
        let refused = setPriority("1", value)
        check(kind(refused) == "invalidValue" && refused?.error?.message == message,
              "setPriority: \(label) is refused with the exact text", refused?.error?.message ?? "nil")
    }
    check(app.calls.count == before, "setPriority: a refused value never reaches the app")
    let top = setPriority("alice@work.com", .number(99))
    check(kind(top) == nil && priorities(top).contains(99), "setPriority: 99, the top of the range, is accepted and stored", "\(priorities(top))")
    let bottom = setPriority("alice@work.com", .number(1))
    check(kind(bottom) == nil && priorities(bottom).contains(1), "setPriority: 1, the bottom of the range, is accepted and stored", "\(priorities(bottom))")
    check(kind(setPriority("alice@work.com", .number(98))) == nil && priorities(setPriority("alice@work.com", .number(3.0))).contains(3),
          "setPriority: 3.0 is the whole number 3")

    // A priority number is never an account reference: "3" still means the third in the list.
    // Here alice has priority 3 and is NOT in third place; if "3" meant "the account with priority 3" it would pick her.
    let aliceRow = account(app, "alice@work.com", api: api)
    check(aliceRow?["priority"] == .number(3) && aliceRow?["position"] != .number(3),
          "references: set up: alice has priority 3 and is not the third row", aliceRow?.compactText ?? "nil")
    let third = Kit.call(api, "accounts.switch", .object(["account": .string("3")]))
    let thirdEmail = third?.result?["account"]?["email"]?.stringValue
    check(thirdEmail == app.accounts[2].email && thirdEmail != "alice@work.com",
          "references: \"3\" is the position, not the account with priority 3", thirdEmail ?? "nil")

    // The account vanished between resolving its name and writing: both new methods say so.
    app.refuseAccountWrites = true
    let gonePriority = setPriority("bob@home.com", .number(5))
    let goneThresholds = Kit.call(api, "accounts.setThresholds", .object(["account": .string("bob@home.com"), "session": .number(80)]))
    check(kind(gonePriority) == "notFound" && gonePriority?.error?.message == "That account no longer exists."
          && kind(goneThresholds) == "notFound" && goneThresholds?.error?.message == "That account no longer exists.",
          "setPriority and setThresholds: an account that vanished after its name was resolved is notFound, 'That account no longer exists.'")
    app.refuseAccountWrites = false

    // setOrder numbers every account 1..N, replacing typed numbers (spec D3).
    _ = setPriority("alice@work.com", .number(40))
    let ordered = Kit.call(api, "accounts.setOrder", .object(["accounts": .array([.string("3"), .string("1"), .string("2")])]))
    check(priorities(ordered) == [1, 2, 3] && order(ordered).count == 3, "setOrder: numbers the accounts 1, 2, 3 in the new order, replacing typed numbers", "\(priorities(ordered))")
    check(kind(Kit.call(api, "accounts.setOrder", .object(["accounts": .array([.string("1"), .string("2")])]))) == "invalidValue",
          "setOrder: validation is unchanged (every account exactly once)")
}

// MARK: - Settings keys and the strategy alias

@MainActor private func runSplitSettingsTests() {
    func norm(_ key: SettingKey, _ value: JSONValue) -> JSONValue? { try? key.normalize(value) }
    check(SettingKey.allCases.count == 24 && SettingKey.autoSwitchSessionThreshold.rawValue == "autoSwitch.sessionThreshold"
          && SettingKey.autoSwitchWeeklyThreshold.rawValue == "autoSwitch.weeklyThreshold",
          "settings: 24 keys, the two new ones are autoSwitch.sessionThreshold and autoSwitch.weeklyThreshold")
    for key in [SettingKey.autoSwitchSessionThreshold, .autoSwitchWeeklyThreshold] {
        check(norm(key, .number(72.4)) == .number(72) && norm(key, .string("85")) == .number(85) && norm(key, .number(100)) == .number(100)
              && norm(key, .number(50)) == .number(50), "settings: \(key.rawValue) takes whole numbers from 50 to 100")
        check(norm(key, .number(72.5)) == .number(73) && norm(key, .number(50.5)) == .number(51) && norm(key, .string("98.5")) == .number(99),
              "settings: \(key.rawValue) rounds half away from zero (72.5 is 73, not the half-to-even 72)")
        check(norm(key, .number(49)) == nil && norm(key, .number(101)) == nil && norm(key, .string("high")) == nil && norm(key, .bool(true)) == nil,
              "settings: \(key.rawValue) refuses 49, 101, words and booleans")
        check(norm(key, .null) == .null && norm(key, .string("default")) == .null && norm(key, .string(" DEFAULT ")) == .null,
              "settings: \(key.rawValue): null or \"default\" clears it to follow autoSwitch.threshold")
        do {
            _ = try key.normalize(.number(10))
        } catch let error as ControlError {
            check(error.kind == .invalidValue && error.message == "\(key.rawValue) must be 50–100, whole numbers, or \"default\".",
                  "settings: \(key.rawValue) says what it accepts", error.message)
        } catch {}
    }
    check(norm(.autoSwitchThreshold, .null) == nil && norm(.autoSwitchThreshold, .string("default")) == nil,
          "settings: the base autoSwitch.threshold still refuses null and \"default\"")

    // The alias: Priority is the strategy stored as myOrder (spec D4).
    check(norm(.autoSwitchStrategy, .string("priority")) == .string("myOrder") && norm(.autoSwitchStrategy, .string(" Priority ")) == .string("myOrder")
          && norm(.autoSwitchStrategy, .string("myOrder")) == .string("myOrder") && norm(.autoSwitchStrategy, .string("fastest")) == nil,
          "settings: autoSwitch.strategy priority is an alias that stores myOrder")
    check(norm(.autoSwitchEnabled, .string("priority")) == nil, "settings: the alias belongs to the strategy only")

    // Through the API: the app sees the normalized value (null stays null).
    let app = Kit.FakeApp()
    let api = ControlAPI(controller: app)
    _ = Kit.call(api, "settings.set", .object(["key": .string("autoSwitch.sessionThreshold"), "value": .string("default")]))
    check(app.calls.last == "set autoSwitch.sessionThreshold=null", "api: settings.set \"default\" reaches the app as null", app.calls.last ?? "")
    _ = Kit.call(api, "settings.set", .object(["key": .string("autoSwitch.weeklyThreshold"), "value": .number(50)]))
    check(app.calls.last == "set autoSwitch.weeklyThreshold=50", "api: settings.set weeklyThreshold 50 reaches the app", app.calls.last ?? "")
    _ = Kit.call(api, "settings.set", .object(["key": .string("autoSwitch.strategy"), "value": .string("priority")]))
    check(app.calls.last == "set autoSwitch.strategy=\"myOrder\"", "api: settings.set strategy priority reaches the app as myOrder", app.calls.last ?? "")
    let bad = Kit.call(api, "settings.set", .object(["key": .string("autoSwitch.sessionThreshold"), "value": .number(10)]))
    check(kind(bad) == "invalidValue", "api: a session default under 50 is refused")

    // What SettingsStore calls: the base write clears both window defaults; a window write touches only itself.
    let name = "pixelswitch.unit-tests.control.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: name)!
    defer { defaults.removePersistentDomain(forName: name) }
    AutoSwitchSettings.setDefaultThreshold(.session, to: 95, in: defaults)
    AutoSwitchSettings.setDefaultThreshold(.weekly, to: 60, in: defaults)
    check(AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.sessionThresholdKey, in: defaults) == 95
          && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.weeklyThresholdKey, in: defaults) == 60
          && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.thresholdKey, in: defaults) == nil,
          "settings: a window default writes only its own key")
    AutoSwitchSettings.setBaseThreshold(80, in: defaults)
    check(AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.thresholdKey, in: defaults) == 80
          && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.sessionThresholdKey, in: defaults) == nil
          && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.weeklyThresholdKey, in: defaults) == nil,
          "settings: autoSwitch.threshold writes the base and clears both window defaults (D8)")
    AutoSwitchSettings.setDefaultThreshold(.weekly, to: nil, in: defaults)
    check(AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.weeklyThresholdKey, in: defaults) == nil,
          "settings: null removes a window default so it follows the base")
    // The write of a global default rounds half away from zero too (72.5 is 73; half to even would store 72).
    AutoSwitchSettings.setDefaultThreshold(.session, to: 72.5, in: defaults)
    AutoSwitchSettings.setDefaultThreshold(.weekly, to: 50.5, in: defaults)
    check(AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.sessionThresholdKey, in: defaults) == 73
          && AutoSwitchSettings.storedThreshold(forKey: AutoSwitchSettings.weeklyThresholdKey, in: defaults) == 51,
          "settings: a window default of 72.5 is stored as 73 and 50.5 as 51 (half away from zero)")

    // What settings.get answers (SettingsStore.value calls this): the Beeco Mac stores only the base, 98, and
    // both new keys must then read 98, not null or 0.
    let readName = "pixelswitch.unit-tests.control.read.\(UUID().uuidString)"
    let stored = UserDefaults(suiteName: readName)!
    defer { stored.removePersistentDomain(forName: readName) }
    func thresholdValues() -> [JSONValue?] {
        [SettingKey.autoSwitchThreshold, .autoSwitchSessionThreshold, .autoSwitchWeeklyThreshold].map { SettingKey.autoSwitchThresholdValue($0, in: stored) }
    }
    check(thresholdValues() == [.number(90), .number(90), .number(90)], "settings.get: nothing stored reads 90 for the base and both windows", "\(thresholdValues())")
    stored.set(98.0, forKey: AutoSwitchSettings.thresholdKey)
    check(thresholdValues() == [.number(98), .number(98), .number(98)],
          "settings.get: with only autoSwitchThreshold = 98 stored, autoSwitch.sessionThreshold and autoSwitch.weeklyThreshold both read 98", "\(thresholdValues())")
    stored.set(95.0, forKey: AutoSwitchSettings.sessionThresholdKey)
    check(thresholdValues() == [.number(98), .number(95), .number(98)],
          "settings.get: a window default of its own reads its own number; the base and the other window are unchanged", "\(thresholdValues())")
    stored.set(0.0, forKey: AutoSwitchSettings.sessionThresholdKey)
    check(thresholdValues() == [.number(98), .number(98), .number(98)], "settings.get: a window default stored as 0 counts as not set")
    check(SettingKey.autoSwitchThresholdValue(.refreshInterval, in: stored) == nil, "settings.get: the threshold reader answers nothing for another key")
}

// MARK: - Events and status

@MainActor private func runSplitEventTests() {
    let from = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!, to = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
    let weekly = ControlSnapshots.autoSwitchedData(from: from, to: to, limit: .windows, trigger: .threshold, window: .weekly, relaxed: false)
    check(weekly["window"] == .string("weekly") && weekly["relaxed"] == .bool(false) && weekly["limit"] == .string("windows")
          && weekly["trigger"] == .string("threshold") && weekly["from"] == .string(from.uuidString) && weekly["to"] == .string(to.uuidString),
          "events: autoSwitched keeps limit and trigger and gains window and relaxed", JSONValue.object(weekly).compactText)
    let drain = ControlSnapshots.autoSwitchedData(from: from, to: to, limit: .windows, trigger: .drainEarly, window: nil, relaxed: true)
    check(drain["window"] == nil && drain["relaxed"] == .bool(true), "events: window is left out unless a window's limit fired; relaxed marks the overflow pass")
    let fable = ControlSnapshots.autoSwitchedData(from: from, to: to, limit: .fable, trigger: .threshold, window: nil, relaxed: false)
    check(fable["limit"] == .string("fable") && fable["window"] == nil, "events: a Fable switch names no window")

    let app = Kit.FakeApp()
    let api = ControlAPI(controller: app)
    let data = api.accountsChangedData()
    let list = Kit.call(api, "accounts.list")?.result?["accounts"]
    check(data["accounts"] == list && data["accounts"]?.arrayValue?.count == 3, "events: accountsChanged carries the list exactly as accounts.list would")
    _ = Kit.call(api, "accounts.setPriority", .object(["account": .string("2"), "priority": .number(1)]))
    check(api.accountsChangedData()["accounts"]?.arrayValue?.first?["priority"] == .number(1), "events: accountsChanged reads the list as it is now")

    // accountsChanged fires when this key changes (the hub compares AccountFields and DefaultFields): every field a
    // priority, threshold, label or active edit touches is in it, and the order is.
    let base = Kit.a
    func fields(_ edit: (inout Account) -> Void) -> ControlSnapshots.AccountFields {
        var changed = base
        edit(&changed)
        return ControlSnapshots.AccountFields(changed)
    }
    let reference = ControlSnapshots.AccountFields(base)
    check(fields { $0.customLabel = "Renamed" } != reference && fields { $0.switchThreshold = 80 } != reference
          && fields { $0.sessionThreshold = 80 } != reference && fields { $0.weeklyThreshold = 60 } != reference
          && fields { $0.priority = 2 } != reference && fields { $0.isActive = false } != reference
          && fields { $0.subscriptionType = "pro" } != reference && fields { _ in } == reference,
          "events: a label, threshold, window threshold, priority, active or plan change alters the accountsChanged key; no change does not")
    let listAB = [Kit.a, Kit.b].map(ControlSnapshots.AccountFields.init), listBA = [Kit.b, Kit.a].map(ControlSnapshots.AccountFields.init)
    check(listAB != listBA, "events: a new order alters the accountsChanged key")
    check(ControlSnapshots.DefaultFields(base: 90, session: 90, weekly: 90) != ControlSnapshots.DefaultFields(base: 90, session: 80, weekly: 90)
          && ControlSnapshots.DefaultFields(base: 90, session: 90, weekly: 90) == ControlSnapshots.DefaultFields(base: 90, session: 90, weekly: 90),
          "events: a window default change alters the accountsChanged key")

    let status = Kit.call(api, "status.get")?.result.flatMap { try? $0.decode(StatusInfo.self) }
    check(status?.protocolVersion == 2 && status?.protocolRevision == 3 && status?.features == ["priority", "splitThresholds"]
          && status?.platform == "macos" && status?.testMode == nil,
          "status: protocol 2, revision 3, both features, macos, no testMode on the Mac")
    app.autoSwitch.defaultSessionThreshold = 90
    app.autoSwitch.defaultWeeklyThreshold = 50
    let split = Kit.call(api, "status.get")?.result?["autoSwitch"]
    check(split?["defaultSessionThreshold"] == .number(90) && split?["defaultWeeklyThreshold"] == .number(50) && split?["defaultThreshold"] == .number(90),
          "status: autoSwitch reports both effective defaults beside the base", split?.compactText ?? "nil")
}

// MARK: - CLI parser

@MainActor private func runSplitCLIParserTests() {
    func parsed(_ line: String) -> CLICommand? { try? CLIParser.parse(line.split(separator: " ").map(String.init)).command }
    func refused(_ line: String) -> String? {
        do { _ = try CLIParser.parse(line.split(separator: " ").map(String.init)); return nil } catch let e as CLIUsageError { return e.message } catch { return "?" }
    }
    check(parsed("accounts threshold 2 --session 90 --weekly 50") == .windowThresholds("2", session: .percent(90), weekly: .percent(50)),
          "cli: --session and --weekly set both windows")
    check(parsed("accounts threshold 2 --weekly 50") == .windowThresholds("2", session: nil, weekly: .percent(50))
          && parsed("accounts threshold --session 75% 2") == .windowThresholds("2", session: .percent(75), weekly: nil),
          "cli: one flag leaves the other window alone; the account and a percent sign go anywhere")
    check(parsed("accounts threshold 2 --session default --weekly DEFAULT") == .windowThresholds("2", session: .follow, weekly: .follow),
          "cli: default makes a window follow its default")
    check(parsed("accounts threshold 2 75") == .threshold("2", 75) && parsed("accounts threshold 2 default") == .threshold("2", nil)
          && parsed("accounts threshold 2 manual") == .threshold("2", 0), "cli: the old threshold syntax is unchanged")
    check(refused("accounts threshold 2 --session") != nil && refused("accounts threshold 2 --session x") != nil
          && refused("accounts threshold 2 --session 101") != nil && refused("accounts threshold 2 --session 0") != nil
          && refused("accounts threshold --session 80") != nil && refused("accounts threshold 2 3 --session 80") != nil
          && refused("accounts threshold 2 --bogus 80") != nil, "cli: bad flag values, a missing account and stray words are refused")
    check(refused("accounts threshold 2 --session manual")?.contains("pixelswitch accounts threshold <account> manual") == true,
          "cli: manual for one window is refused, pointing at the whole-account command")
    check(parsed("accounts priority 2 1") == .priority("2", 1) && parsed("accounts priority bob@home.com 99") == .priority("bob@home.com", 99)
          && parsed("accounts priority 2 none") == .priority("2", nil) && parsed("accounts priority 2 NONE") == .priority("2", nil),
          "cli: accounts priority takes 1-99 or none")
    check(["0", "100", "P1", "-3", "1.5", "high"].allSatisfy { refused("accounts priority 2 \($0)")?.contains("1 to 99") == true }
          && refused("accounts priority 2") != nil && refused("accounts priority") != nil,
          "cli: a priority outside 1-99, with a P, or fractional is refused")
    let help = CLIParser.helpText
    check(help.contains("--session") && help.contains("accounts priority <account> <1-99|none>")
          && help.contains("<account> is an email address, a label, an id, or a position")
          && help.contains("never a priority number"), "cli: the help names the new commands and says <account> is never a priority number")
}

// MARK: - CLI output

private func info(_ id: String, email: String, position: Int, active: Bool = false, threshold: Double? = nil,
                  session: Double? = nil, weekly: Double? = nil, effective: (Double, Double) = (90, 90), manual: Bool = false,
                  priority: Int? = nil) -> AccountInfo {
    AccountInfo(id: id, email: email, label: nil, subscription: nil, isActive: active, isSwitchable: true, position: position,
                threshold: threshold, effectiveThreshold: min(effective.0, effective.1), manualOnly: manual, usage: nil,
                priority: priority, sessionThreshold: session, weeklyThreshold: weekly,
                effectiveSessionThreshold: effective.0, effectiveWeeklyThreshold: effective.1)
}

@MainActor private func runSplitCLIOutputTests() {
    // Equal windows: the table is 1.9's, plus the last column.
    let equal = [info("1", email: "alice@work.com", position: 1, active: true, priority: 1),
                 info("2", email: "bob@home.com", position: 2, threshold: 70, effective: (70, 70))]
    let expected = """
    *  1  alice@work.com  —        —       —      90% (default)  P1
    2  bob@home.com    —        —       —      70%            —
    """
    let table = CLIOutput.accounts(equal).split(separator: "\n").map(String.init)
    check(table.first == "#  Account         Session  Weekly  Fable  Switches at    Priority"
          && Array(table[1...2]).joined(separator: "\n") == expected,
          "cli output: equal windows: 1.9's table with a Priority column last (P1 or a dash)", table.joined(separator: "\n"))
    check(table.last == "* active   ! cannot be switched to until it is re-signed (pixelswitch accounts reauth <account>)",
          "cli output: the legend is unchanged")

    // The founder's split.
    let split = [info("1", email: "alice@work.com", position: 1, active: true, session: 90, weekly: 50, effective: (90, 50), priority: 1),
                 info("2", email: "bob@home.com", position: 2, effective: (90, 50), priority: 3),
                 info("3", email: "carol@work.com", position: 3, threshold: 0, effective: (90, 50), manual: true)]
    let cells = CLIOutput.accounts(split).split(separator: "\n").map(String.init)
    check(cells[1].contains("5h 90% / 7d 50% ") && cells[1].hasSuffix("P1") && !cells[1].contains("(default)")
          && cells[2].contains("5h 90% / 7d 50% (default) ") && cells[2].hasSuffix("P3")
          && cells[3].contains("5h 90% / 7d 50% (manual only)") && cells[3].hasSuffix("—"),
          "cli output: split windows read 5h 90% / 7d 50% in Switches at, with the same suffixes", cells.joined(separator: "\n"))
    check(CLIOutput.switchesAt(split[0]) == "5h 90% / 7d 50%" && CLIOutput.switchesAt(equal[0]) == "90% (default)",
          "cli output: Switches at is one number when the windows are equal")
    check(CLIOutput.priorityCell(split[1]) == "P3" && CLIOutput.priorityCell(split[2]) == "—", "cli output: priorities read P3, never a bare 3")

    // thresholdSet.
    check(CLIOutput.thresholdSet(split[0]) == "alice@work.com now switches at 90% of its session or 50% of its week.",
          "cli output: setting a split says 90% of its session or 50% of its week", CLIOutput.thresholdSet(split[0]))
    check(CLIOutput.thresholdSet(equal[0]) == "alice@work.com now follows the default threshold (90%)."
          && CLIOutput.thresholdSet(equal[1]) == "bob@home.com now switches at 70%.",
          "cli output: equal windows keep today's sentences word for word")
    let bothOwn = info("4", email: "dan@x.com", position: 4, session: 80, weekly: 80, effective: (80, 80))
    check(CLIOutput.thresholdSet(bothOwn) == "dan@x.com now switches at 80%.", "cli output: two equal window values of its own read as switches at 80%, not as the default")
    let manualActive = info("5", email: "eve@x.com", position: 5, active: true, threshold: 0, effective: (90, 50), manual: true)
    check(CLIOutput.thresholdSet(manualActive).hasSuffix("You're using it now: auto-switch moves you off it at 90% of its session or 50% of its week and won't move you back to it."),
          "cli output: a Manual only account in use is left at each window's default")
    check(CLIOutput.switched(manualActive) == "Switched to eve@x.com. It is manual only: auto-switch moves you off it at 90% of its session or 50% of its week and won't move you back to it.",
          "cli output: switching to a Manual only account says where each window leaves it")

    // status.
    let splitInfo = AutoSwitchInfo(enabled: true, defaultThreshold: 90, onFable: true, strategy: "myOrder", drainEarly: true, drainWithinHours: 24,
                                   defaultSessionThreshold: 90, defaultWeeklyThreshold: 50)
    func status(_ auto: AutoSwitchInfo) -> StatusInfo {
        StatusInfo(appVersion: "2.0", protocolVersion: 2, activeAccountId: nil, activeAccountEmail: nil, accountCount: 0,
                   claudeAvailable: true, lastRefresh: nil, autoSwitch: auto, signIn: nil)
    }
    check(CLIOutput.status(status(splitInfo)).contains("Auto-switch:   on, default 5h 90% / 7d 50%, next by priority"),
          "cli output: status reads default 5h 90% / 7d 50% and next by priority", CLIOutput.status(status(splitInfo)))
    let equalInfo = AutoSwitchInfo(enabled: true, defaultThreshold: 98, onFable: true, strategy: "mostRoom", drainEarly: true, drainWithinHours: 24,
                                   defaultSessionThreshold: 98, defaultWeeklyThreshold: 98)
    let oldInfo = AutoSwitchInfo(enabled: true, defaultThreshold: 98, onFable: true, strategy: "mostRoom", drainEarly: true, drainWithinHours: 24)
    check(CLIOutput.status(status(equalInfo)).contains("Auto-switch:   on, default 98%, next by most room left")
          && CLIOutput.status(status(oldInfo)).contains("Auto-switch:   on, default 98%, next by most room left"),
          "cli output: equal defaults (and an old app that sends none) read 98% as before")
    // The Settings window never writes the base: equal window defaults that differ from it are a normal state,
    // and the line must say what auto-switch actually uses (the windows' own default), not the base.
    let movedInfo = AutoSwitchInfo(enabled: true, defaultThreshold: 98, onFable: true, strategy: "mostRoom", drainEarly: true, drainWithinHours: 24,
                                   defaultSessionThreshold: 95, defaultWeeklyThreshold: 95)
    let lowerInfo = AutoSwitchInfo(enabled: true, defaultThreshold: 90, onFable: true, strategy: "mostRoom", drainEarly: true, drainWithinHours: 24,
                                   defaultSessionThreshold: 80, defaultWeeklyThreshold: 80)
    check(CLIOutput.status(status(movedInfo)).contains("Auto-switch:   on, default 95%, next by most room left")
          && CLIOutput.status(status(lowerInfo)).contains("Auto-switch:   on, default 80%, next by most room left"),
          "cli output: equal window defaults that differ from the base read the windows' value (95%, 80%), not the base",
          CLIOutput.status(status(movedInfo)))
    check(CLIOutput.defaultWords(movedInfo) == "95%" && CLIOutput.defaultWords(oldInfo) == "98%"
          && CLIOutput.defaultWords(splitInfo) == "5h 90% / 7d 50%", "cli output: defaultWords: windows first, the base only when the app sends neither")
    check(CLIOutput.strategyWords("myOrder") == "priority" && CLIOutput.strategyWords("resetsSoonest") == "resets soonest",
          "cli output: myOrder is called priority")

    // accounts priority / accounts order.
    let set = CLIOutput.prioritySet(equal, strategy: "mostRoom")
    check(set.hasPrefix(CLIOutput.accounts(equal)) && set.contains("Auto-switch follows these numbers only when it chooses by Priority.")
          && set.contains("pixelswitch settings set autoSwitch.strategy priority"),
          "cli output: setting a priority under another strategy says the numbers do nothing yet, and how to use them")
    check(CLIOutput.prioritySet(equal, strategy: "myOrder") == CLIOutput.accounts(equal), "cli output: under Priority there is no extra sentence")
    check(CLIOutput.orderSet(equal, numbered: true).hasPrefix(CLIOutput.accounts(equal)) && CLIOutput.orderSet(equal, numbered: true).contains("numbered from 1")
          && CLIOutput.orderSet(equal, numbered: true).contains("replacing any priority numbers"),
          "cli output: accounts order says it renumbers every account from 1 when the app numbers accounts")
    check(CLIOutput.orderSet(equal, numbered: false) == CLIOutput.accounts(equal),
          "cli output: accounts order says nothing about numbering when the app does not number accounts (1.9)")
}

// MARK: - MCP

@MainActor private func runSplitMCPTests() {
    let calls = Kit.Box<[String]>([])
    let server = MCPServer(serverVersion: "2.0") { method, params in
        MainActor.assumeIsolated { calls.value.append("\(method.rawValue) \(params?.compactText ?? "nil")") }
        return .object(["ok": .bool(true)])
    }
    func reply(_ json: String) -> JSONValue? {
        server.handle(json).flatMap { try? ControlCoding.decoder.decode(JSONValue.self, from: Data($0.utf8)) }
    }
    let tools = reply(#"{"jsonrpc":"2.0","id":1,"method":"tools/list"}"#)?["result"]?["tools"]?.arrayValue ?? []
    func tool(_ name: String) -> JSONValue? { tools.first { $0["name"] == .string(name) } }
    check(tools.count == 20, "mcp: 20 tools", "\(tools.count)")

    let thresholds = tool("set_account_thresholds")
    let tp = thresholds?["inputSchema"]?["properties"]
    check(thresholds?["inputSchema"]?["required"] == .array([.string("account")])
          && tp?["session"]?["type"] == .array([.string("number"), .string("null")]) && tp?["weekly"]?["type"] == .array([.string("number"), .string("null")])
          && tp?["session"]?["minimum"] == .number(1) && tp?["session"]?["maximum"] == .number(100)
          && tp?["weekly"]?["minimum"] == .number(1) && tp?["weekly"]?["maximum"] == .number(100),
          "mcp: set_account_thresholds: account required; session and weekly are number or null, 1-100")
    let priority = tool("set_account_priority")
    let pp = priority?["inputSchema"]?["properties"]
    check(priority?["inputSchema"]?["required"] == .array([.string("account")])
          && pp?["priority"]?["type"] == .array([.string("integer"), .string("null")])
          && pp?["priority"]?["minimum"] == .number(1) && pp?["priority"]?["maximum"] == .number(99)
          && priority?["description"]?.stringValue?.contains("null clears") == true,
          "mcp: set_account_priority: only account is required; priority is an integer 1-99 or null, and the description says null clears it")
    // Every tool that takes an account, found from the schemas (not a list typed here), says a priority number is not one.
    let withAccount = tools.filter { $0["inputSchema"]?["properties"]?["account"] != nil }
    let silent = withAccount.filter { $0["inputSchema"]?["properties"]?["account"]?["description"]?.stringValue?.contains("never a priority number") != true }
        .compactMap { $0["name"]?.stringValue }
    check(withAccount.count >= 8 && silent.isEmpty, "mcp: every account argument of every tool says it is never a priority number (\(withAccount.count) tools)", silent.joined(separator: ", "))
    check(tool("get_usage") != nil && tool("start_sign_in") != nil && withAccount.contains { $0["name"] == .string("get_usage") }
          && withAccount.contains { $0["name"] == .string("start_sign_in") }, "mcp: get_usage and start_sign_in are among the tools checked")

    _ = reply(#"{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"set_account_thresholds","arguments":{"account":"2","session":90,"weekly":null}}}"#)
    check(calls.value.last == #"accounts.setThresholds {"account":"2","session":90,"weekly":null}"#, "mcp: set_account_thresholds forwards its arguments, null included", calls.value.last ?? "")
    _ = reply(#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"set_account_priority","arguments":{"account":"2","priority":null}}}"#)
    check(calls.value.last == #"accounts.setPriority {"account":"2","priority":null}"#, "mcp: set_account_priority forwards a null priority", calls.value.last ?? "")
    let noAccount = reply(#"{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"set_account_priority","arguments":{"priority":2}}}"#)
    check(noAccount?["result"]?["isError"] == .bool(true), "mcp: a priority call without an account is refused before the app is called")

    check(tool("set_account_threshold")?["description"]?.stringValue?.contains("BOTH") == true
          && tool("set_account_order")?["description"]?.stringValue?.contains("numbers every account from 1") == true
          && tool("list_accounts")?["description"]?.stringValue?.contains("effectiveSessionThreshold") == true
          && tool("get_settings")?["description"]?.stringValue?.contains("autoSwitch.sessionThreshold") == true
          && tool("get_settings")?["description"]?.stringValue?.contains("\"priority\" is accepted") == true,
          "mcp: the descriptions of the threshold, order, list and settings tools tell the 2.0 story")
    // A model is never told to send null where the tool refuses it: set_setting's value is required, so the description says "default".
    let settingsWords = tool("get_settings")?["description"]?.stringValue ?? ""
    check(settingsWords.contains("or \"default\" to follow autoSwitch.threshold") && !settingsWords.contains("null/"),
          "mcp: get_settings tells a model to send \"default\", not null, to follow the base threshold")
    _ = reply(#"{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"set_setting","arguments":{"key":"autoSwitch.sessionThreshold","value":"default"}}}"#)
    check(calls.value.last == #"settings.set {"key":"autoSwitch.sessionThreshold","value":"default"}"#, "mcp: set_setting forwards \"default\"", calls.value.last ?? "")
    let discover = reply(#"{"jsonrpc":"2.0","id":5,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}"#)
    check(discover?["result"]?["instructions"]?.stringValue?.contains("never by priority number") == true, "mcp: the instructions say accounts are never named by priority number")
}

// MARK: - An old app, and a 1.9 payload

/// Counts what a stand-in app was asked, safely from the server's side.
private final class AskedLog: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String] = []
    func add(_ item: String) { lock.lock(); items.append(item); lock.unlock() }
    var all: [String] { lock.lock(); defer { lock.unlock() }; return items }
}

private final class Lines: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String] = []
    func add(_ item: String) { lock.lock(); items.append(item); lock.unlock() }
    var text: String { lock.lock(); defer { lock.unlock() }; return items.joined(separator: "\n") }
}

/// What a 1.9 app answers, literally: no priority, no window thresholds, no features.
private enum Payload19 {
    static let status = #"{"accountCount":2,"activeAccountEmail":"alice@work.com","activeAccountId":"11111111-1111-1111-1111-111111111111","appVersion":"1.9","autoSwitch":{"defaultThreshold":98,"drainEarly":true,"drainWithinHours":24,"enabled":true,"onFable":true,"strategy":"myOrder"},"claudeAvailable":true,"lastRefresh":"2026-10-16T15:00:00Z","protocolVersion":2}"#
    static let accounts = #"{"accounts":[{"effectiveThreshold":98,"email":"alice@work.com","id":"11111111-1111-1111-1111-111111111111","isActive":true,"isSwitchable":true,"label":"Work","manualOnly":false,"position":1,"subscription":"Max","usage":{"sampledAt":"2026-10-16T15:00:00Z","session":{"resetsAt":"2026-10-16T18:00:00.000000+00:00","utilization":42},"weekly":{"resetsAt":"2026-10-20T09:00:00+00:00","utilization":61}}},{"effectiveThreshold":98,"email":"bob@home.com","id":"22222222-2222-2222-2222-222222222222","isActive":false,"isSwitchable":true,"manualOnly":true,"position":2,"threshold":0}]}"#
}

@MainActor private func runOldAppTests() {
    // The literal 1.9 payloads decode in the new CLI (the optional-field rule).
    let status = try? ControlCoding.decoder.decode(StatusInfo.self, from: Data(Payload19.status.utf8))
    check(status?.appVersion == "1.9" && status?.protocolVersion == 2 && status?.features == nil && status?.protocolRevision == nil
          && status?.platform == nil && status?.autoSwitch.defaultThreshold == 98 && status?.autoSwitch.defaultSessionThreshold == nil,
          "old app: a 1.9 status.get decodes in the new CLI with every new field absent")
    let accountsValue = try? ControlCoding.decoder.decode(JSONValue.self, from: Data(Payload19.accounts.utf8))
    let decoded = CLIRunner.accounts(accountsValue?["accounts"])
    check(decoded.count == 2 && decoded[0].priority == nil && decoded[0].effectiveSessionThreshold == nil && decoded[1].manualOnly,
          "NEW CLI DECODES OLD APP JSON: a literal 1.9 accounts.list decodes (not \"No accounts yet\")", "\(decoded.count)")
    let table = CLIOutput.accounts(decoded)
    check(table.contains("98% (default)") && table.contains("98% (manual only)") && table.contains("Priority") && table.hasPrefix("#  Account"),
          "NEW CLI DECODES OLD APP JSON: it prints the 1.9 accounts with the old numbers and a dash for every priority", table)

    // End to end against a stand-in 1.9 app: reading works, a new command stops before it is sent.
    let dir = Kit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let path = dir.appendingPathComponent("old19.sock").path
    let asked = AskedLog()
    let oldApp = ControlServer(path: path, handler: { line, _ in
        let request = try? ControlCoding.decoder.decode(RPCRequest.self, from: Data(line.utf8))
        asked.add(request?.method ?? "?")
        let result: JSONValue
        switch request?.method {
        case "status.get": result = (try? ControlCoding.decoder.decode(JSONValue.self, from: Data(Payload19.status.utf8))) ?? .null
        case "accounts.list", "accounts.setOrder": result = (try? ControlCoding.decoder.decode(JSONValue.self, from: Data(Payload19.accounts.utf8))) ?? .null
        default: return RPCResponse.failure(request?.id, ControlError(.methodNotFound, "There is no method called \"\(request?.method ?? "")\".")).line
        }
        return RPCResponse.success(request?.id, result).line
    })
    guard (try? oldApp.start()) != nil else { check(false, "old app: a stand-in 1.9 app starts"); return }
    defer { oldApp.stop() }

    func run(_ arguments: [String]) -> (Int32, String, String)? { runCLI(path, arguments) }
    let listed = run(["accounts"])
    check(listed?.0 == 0 && listed?.1.contains("alice@work.com (Work)") == true && listed?.1.contains("98% (default)") == true,
          "old app: the new CLI lists a 1.9 app's accounts", listed?.1 ?? "nil")
    let before = asked.all.count
    let thresholds = run(["accounts", "threshold", "1", "--session", "90"])
    check(thresholds?.0 == 1 && thresholds?.2 == "error: This PixelSwitch (1.9) cannot set separate thresholds; update it."
          && asked.all.count == before + 1 && !asked.all.contains("accounts.setThresholds"),
          "old app: accounts threshold --session stops with 'This PixelSwitch (1.9) cannot set separate thresholds; update it.', exit 1, and never sends the method",
          "\(thresholds?.0 ?? -1) \(thresholds?.2 ?? "nil") \(asked.all)")
    let priority = run(["accounts", "priority", "1", "1"])
    check(priority?.0 == 1 && priority?.2 == "error: This PixelSwitch (1.9) cannot set priorities; update it." && !asked.all.contains("accounts.setPriority"),
          "old app: accounts priority stops with 'This PixelSwitch (1.9) cannot set priorities; update it.', exit 1, and never sends the method", priority?.2 ?? "nil")
    // A 1.9 app's setOrder numbers nothing, so the CLI must not claim it did.
    let reordered = run(["accounts", "order", "2", "1"])
    check(reordered?.0 == 0 && reordered?.1.hasPrefix("#  Account") == true && reordered?.1.contains("numbered from 1") == false
          && reordered?.1.contains("replacing any priority numbers") == false && asked.all.contains("accounts.setOrder"),
          "old app: accounts order against a 1.9 app prints the table and does not say it numbered the accounts", reordered?.1 ?? "nil")
    let legacy = run(["accounts", "threshold", "1", "80"])
    check(asked.all.contains("accounts.setThreshold") && legacy?.0 == 1,
          "old app: the old threshold syntax is still sent as before (this stand-in has no such method, so it answers methodNotFound)")
}

/// Runs one CLI command against the socket at `path` off the main thread (the stand-in app is served by the main actor).
@MainActor private func runCLI(_ path: String, _ arguments: [String]) -> (Int32, String, String)? {
    let work = Kit.offMain { () throws -> (Int32, String, String) in
        let out = Lines(), err = Lines()
        var runner = CLIRunner(client: ControlClient(path: path))
        runner.out = { out.add($0) }
        runner.err = { err.add($0) }
        let code = runner.run(try CLIParser.parse(arguments))
        return (code, out.text, err.text)
    }
    return (try? work?.get())
}

// MARK: - The new commands, end to end against a stand-in 2.0 app

/// The CLI against a 2.0 app (the real `ControlAPI` over a fake app, behind a real socket), recording what the CLI sends.
@MainActor private func runNewAppEndToEndTests() {
    let dir = Kit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let path = dir.appendingPathComponent("new20.sock").path
    let app = Kit.FakeApp()
    app.appVersion = "2.0"
    let api = ControlAPI(controller: app)
    let asked = AskedLog()
    let server = ControlServer(path: path, handler: { line, _ in
        if let request = try? ControlCoding.decoder.decode(RPCRequest.self, from: Data(line.utf8)) {
            asked.add("\(request.method) \(request.params?.compactText ?? "nil")")
        }
        return await api.handle(line)
    })
    guard (try? server.start()) != nil else { check(false, "2.0 app: a stand-in 2.0 app starts"); return }
    defer { server.stop() }
    func lastSent(_ method: String) -> String? { asked.all.last { $0.hasPrefix(method + " ") }.map { String($0.dropFirst(method.count + 1)) } }

    // What each flag puts on the wire: a flag left out is absent (the other window is left alone), default is null.
    let weekly = runCLI(path, ["accounts", "threshold", "1", "--weekly", "50"])
    check(weekly?.0 == 0 && lastSent("accounts.setThresholds") == #"{"account":"1","weekly":50}"#,
          "2.0 app: --weekly 50 sends exactly the account and weekly, with no session key", lastSent("accounts.setThresholds") ?? "nil")
    let session = runCLI(path, ["accounts", "threshold", "1", "--session", "80"])
    check(session?.0 == 0 && lastSent("accounts.setThresholds") == #"{"account":"1","session":80}"#,
          "2.0 app: --session 80 sends no weekly key, so a weekly limit of its own is not wiped", lastSent("accounts.setThresholds") ?? "nil")
    let followed = runCLI(path, ["accounts", "threshold", "1", "--session", "default"])
    check(followed?.0 == 0 && lastSent("accounts.setThresholds") == #"{"account":"1","session":null}"#,
          "2.0 app: --session default sends session null", lastSent("accounts.setThresholds") ?? "nil")
    let both = runCLI(path, ["accounts", "threshold", "1", "--session", "90", "--weekly", "50"])
    check(both?.0 == 0 && both?.1 == "alice@work.com now switches at 90% of its session or 50% of its week."
          && account(app, "alice@work.com", api: api)?["sessionThreshold"] == .number(90),
          "2.0 app: --session 90 --weekly 50 is applied and read back in words", both?.1 ?? "nil")

    let cleared = runCLI(path, ["accounts", "priority", "1", "none"])
    check(cleared?.0 == 0 && lastSent("accounts.setPriority") == #"{"account":"1","priority":null}"#,
          "2.0 app: accounts priority 1 none sends priority null", lastSent("accounts.setPriority") ?? "nil")

    // The sentence about the strategy depends on the live strategy the app reports.
    let note = "Auto-switch follows these numbers only when it chooses by Priority."
    let underMostRoom = runCLI(path, ["accounts", "priority", "1", "5"])
    check(underMostRoom?.0 == 0 && underMostRoom?.1.contains(note) == true && lastSent("accounts.setPriority") == #"{"account":"1","priority":5}"#,
          "2.0 app: setting a priority while auto-switch chooses by most room prints that the numbers are not followed yet", underMostRoom?.1 ?? "nil")
    app.autoSwitch = AutoSwitchInfo(enabled: true, defaultThreshold: 90, onFable: true, strategy: "myOrder", drainEarly: true, drainWithinHours: 24)
    let underPriority = runCLI(path, ["accounts", "priority", "1", "6"])
    check(underPriority?.0 == 0 && underPriority?.1.contains(note) == false && underPriority?.1.hasPrefix("#  Account") == true,
          "2.0 app: the same command while auto-switch chooses by Priority prints no such sentence", underPriority?.1 ?? "nil")

    let reordered = runCLI(path, ["accounts", "order", "3", "2", "1"])
    check(reordered?.0 == 0 && reordered?.1.contains("numbered from 1") == true,
          "2.0 app: accounts order against a 2.0 app says it numbered every account from 1", reordered?.1 ?? "nil")
}

// MARK: - Strings

@MainActor private func runSplitStringTests() {
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let paths = languages.map { "PixelSwitch/\($0).lproj/Localizable.strings" }
    let tables = paths.map { NSDictionary(contentsOfFile: $0) as? [String: String] ?? [:] }
    let keySets = tables.map { Set($0.keys) }
    check(!keySets[0].isEmpty && keySets.allSatisfy { $0 == keySets[0] }, "l10n: the five string tables still share one key set",
          zip(languages, keySets).map { "\($0.0) \($0.1.count)" }.joined(separator: ", "))

    // Every string 2.0 added: the English table's lines from its "/* 2.0" comment on. Derived, never typed by hand,
    // so a string added later is covered the day it is added.
    let lines = ((try? String(contentsOfFile: paths[0], encoding: .utf8)) ?? "").components(separatedBy: "\n")
    let start = lines.firstIndex { $0.hasPrefix("/* 2.0") } ?? lines.count
    let keys = lines[min(start, lines.count)...].compactMap { line -> String? in
        guard line.hasPrefix("\""), let entry = ("{ " + line + " }").propertyList() as? [String: String] else { return nil }
        return entry.keys.first
    }
    check(keys.count >= 20, "l10n: the English table's 2.0 block is found and has its strings (at least 20)", "\(keys.count)")
    let missing = keys.filter { key in !tables.allSatisfy { $0[key] != nil } }
    check(missing.isEmpty, "l10n: every new 2.0 string exists in all five languages", missing.joined(separator: " | "))
    // English is the key; the other four must be real translations, not the English copied over.
    let untranslated = keys.filter { key in
        guard key.count > 12 else { return false }
        return tables[1...].contains { $0[key] == key }
    }
    check(untranslated.isEmpty, "l10n: the long 2.0 strings are translated in de, fr, ja and zh-Hans", untranslated.joined(separator: " | "))
    // The translation keeps the same placeholders as the English: %@, %lld and %% (a dropped or extra one shows a sentence with a number missing).
    func placeholders(_ text: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: #"%(?:\d+\$)?(?:%|lld|@|d)"#) else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        return regex.matches(in: text, range: range).compactMap { Range($0.range, in: text).map { String(text[$0]).replacingOccurrences(of: #"\d+\$"#, with: "", options: .regularExpression) } }.sorted()
    }
    let changed = keys.flatMap { key in languages.indices.dropFirst().compactMap { index -> String? in
        guard let translated = tables[index][key], placeholders(translated) != placeholders(key) else { return nil }
        return "\(languages[index]): \(key.prefix(40))"
    } }
    check(changed.isEmpty, "l10n: every 2.0 translation keeps the English string's placeholders", changed.joined(separator: " | "))
    check(tables.allSatisfy { $0["Priority (lowest number first)"] != nil && $0["My order"] == nil },
          "l10n: the strategy is called Priority (lowest number first) and the old \"My order\" label is gone")
    check(tables[0]["Lower numbers are tried first. Dragging renumbers every account from 1."] == "Lower numbers are tried first. Dragging renumbers every account from 1."
          && tables[0]["Auto-switch follows these numbers only when it chooses by Priority."] == "Auto-switch follows these numbers only when it chooses by Priority."
          && tables[0]["Fable uses the weekly limit."] == "Fable uses the weekly limit.",
          "l10n: the English sentences are the ones the spec fixes")
    // The strings the account row speaks to VoiceOver, with the account's name in them.
    let spoken = ["Priority for %@", "Session limit for %@", "Weekly limit for %@"]
    check(spoken.allSatisfy { key in tables.allSatisfy { $0[key] != nil && placeholders($0[key] ?? "") == ["%@"] } },
          "l10n: the Priority, Session limit and Weekly limit labels exist in all five languages and each names the account")
    // Every key in all five tables whose English has a placeholder keeps it (older strings too).
    let drifted = tables[0].keys.filter { key in languages.indices.dropFirst().contains { index in
        tables[index][key].map { placeholders($0) != placeholders(key) } ?? false } }
    check(drifted.isEmpty, "l10n: no string in any table has lost or gained a placeholder", drifted.sorted().prefix(3).joined(separator: " | "))
}
