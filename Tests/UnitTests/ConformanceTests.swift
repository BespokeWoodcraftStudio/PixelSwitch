// The shared conformance vectors (Tests/conformance/autoswitch-vectors.json,
// described by Tests/conformance/README.md): the Swift engine must produce, for
// every case, exactly the `expect` block. The Linux engine runs the same file.
// Uses the global `check(_:_:_:)` defined in main.swift.
import Foundation

@MainActor func runConformanceTests() {
    guard let path = conformanceVectorsPath(),
          let data = FileManager.default.contents(atPath: path),
          let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let cases = root["cases"] as? [[String: Any]] else {
        check(false, "conformance: the vectors file is found and parses", "looked for PIXELSWITCH_VECTORS, Tests/conformance/autoswitch-vectors.json")
        return
    }
    let hysteresisDefault = (root["hysteresisDefault"] as? Double) ?? AutoSwitchEngine.hysteresis
    check(hysteresisDefault == AutoSwitchEngine.hysteresis && (root["drainMinimumRoom"] as? Double) == AutoSwitchEngine.drainMinimumRoom,
          "conformance: the file's engine constants are the engine's own")

    var names = Set<String>()
    var passedCases = 0
    for vector in cases {
        let name = vector["name"] as? String ?? "(unnamed)"
        var problems = ConformanceCase.run(vector, hysteresisDefault: hysteresisDefault)
        if !names.insert(name).inserted { problems.append("duplicate case name") }
        if problems.isEmpty { passedCases += 1 }
        check(problems.isEmpty, "conformance: \(name)", problems.joined(separator: "; "))
    }
    print("conformance: \(passedCases)/\(cases.count) cases agree with the engine")
    check(passedCases == cases.count && cases.count >= 150, "conformance: every case passes (\(passedCases)/\(cases.count))")
    runConformanceStrictnessTests()
}

/// PIXELSWITCH_VECTORS, else the repo-relative path from the working directory
/// (the test script runs from the repo root), else relative to this file.
private func conformanceVectorsPath() -> String? {
    let fileManager = FileManager.default
    var candidates: [String] = []
    if let env = ProcessInfo.processInfo.environment["PIXELSWITCH_VECTORS"], !env.isEmpty { candidates.append(env) }
    candidates.append("Tests/conformance/autoswitch-vectors.json")
    let here = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    candidates.append(here.appendingPathComponent("conformance/autoswitch-vectors.json").path)
    return candidates.first { fileManager.fileExists(atPath: $0) }
}

enum ConformanceCase {
    static func number(_ value: Any?) -> Double? {
        guard let value, !(value is NSNull) else { return nil }
        return (value as? NSNumber)?.doubleValue
    }

    /// The API's usage JSON for one vector account; nil when the account has no sample at all.
    static func usage(from spec: [String: Any]?) -> UsageAPIResponse? {
        guard let spec else { return nil }
        var json: [String: Any] = [:]
        if let five = spec["five_hour"] { json["five_hour"] = five }
        if let seven = spec["seven_day"] { json["seven_day"] = seven }
        if let fable = spec["fable"] as? [String: Any] {
            var limit: [String: Any] = [
                "kind": "weekly_scoped", "group": "weekly",
                "percent": fable["utilization"] ?? NSNull(),
                "scope": ["model": ["id": NSNull(), "display_name": "Fable"]],
            ]
            if let resets = fable["resets_at"] { limit["resets_at"] = resets }
            json["limits"] = [limit]
        }
        guard let data = try? JSONSerialization.data(withJSONObject: json) else { return nil }
        return try? JSONDecoder().decode(UsageAPIResponse.self, from: data)
    }

    static func run(_ vector: [String: Any], hysteresisDefault: Double) -> [String] {
        // A case that is missing a field or carries one this harness does not know
        // fails loudly; nothing falls back to a default (a typo or an alias such as
        // "priority" must never run as Most room left and pass for the wrong reason).
        let shape = conformanceShapeProblems(vector)
        if !shape.isEmpty { return shape }
        var problems: [String] = []
        guard let nowText = vector["now"] as? String, let now = ISO8601DateFormatter().date(from: nowText),
              let settings = vector["settings"] as? [String: Any],
              let accountSpecs = vector["accounts"] as? [[String: Any]],
              let activeId = vector["active"] as? String,
              let expect = vector["expect"] as? [String: Any] else { return ["the case is malformed"] }

        // Accounts in STORED order, each with a fixed UUID.
        var uuidById: [String: UUID] = [:]
        var idByUuid: [UUID: String] = [:]
        var lastSetByUuid: [UUID: Double] = [:]
        var usageByUuid: [UUID: UsageAPIResponse] = [:]
        var switchable: [UUID: Bool] = [:]
        var unusable: [UUID: Bool] = [:]
        var stored: [Account] = []
        for (index, spec) in accountSpecs.enumerated() {
            guard let id = spec["id"] as? String else { return ["an account has no id"] }
            let uuid = UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index + 1))!
            uuidById[id] = uuid
            idByUuid[uuid] = id
            stored.append(Account(id: uuid, email: "\(id)@example.com", displayName: id,
                                  switchThreshold: number(spec["switchThreshold"]),
                                  sessionThreshold: number(spec["sessionThreshold"]),
                                  weeklyThreshold: number(spec["weeklyThreshold"]),
                                  priority: number(spec["priority"]).map { Int($0) }))
            if let order = number(spec["prioritySetOrder"]) { lastSetByUuid[uuid] = order }
            if let usage = usage(from: spec["usage"] as? [String: Any]) { usageByUuid[uuid] = usage }
            guard let isSwitchable = spec["switchable"] as? Bool, let isUnusable = spec["unusable"] as? Bool else {
                return ["account \(id) needs boolean switchable and unusable"]
            }
            switchable[uuid] = isSwitchable
            unusable[uuid] = isUnusable
        }
        guard let activeUuid = uuidById[activeId] else { return ["the active account is not on the list"] }

        // 1. Sort by priority.
        let sorted = AccountOrder.sortedByPriority(stored, lastSet: { lastSetByUuid[$0.id] })
        let order = sorted.compactMap { idByUuid[$0.id] }
        if let expected = expect["order"] as? [String], expected != order {
            problems.append("order \(order) expected \(expected)")
        }

        // 2./3. Resolve every account's rule from the RAW values.
        let rawDefaults = settings["defaults"] as? [String: Any] ?? [:]
        let defaults = AutoSwitchSettings.windowDefaults(base: number(rawDefaults["base"]),
                                                         session: number(rawDefaults["session"]),
                                                         weekly: number(rawDefaults["weekly"]))
        var rules: [UUID: AutoSwitchEngine.WindowRule] = [:]
        for account in sorted { rules[account.id] = AutoSwitchSettings.rule(for: account, defaults: defaults) }
        if let expectedRules = expect["rules"] as? [String: [String: Any]] {
            for (id, want) in expectedRules {
                guard let uuid = uuidById[id], let rule = rules[uuid] else { problems.append("rules: unknown account \(id)"); continue }
                let leave = AutoSwitchSettings.leaveAtPair(rule: rule, defaults: defaults)
                if number(want["session"]) != rule.session { problems.append("\(id) session \(rule.session) expected \(String(describing: want["session"]))") }
                if number(want["weekly"]) != rule.weekly { problems.append("\(id) weekly \(rule.weekly) expected \(String(describing: want["weekly"]))") }
                if (want["manualOnly"] as? Bool) != rule.manualOnly { problems.append("\(id) manualOnly \(rule.manualOnly)") }
                if number(want["leaveSession"]) != leave.session { problems.append("\(id) leaveSession \(leave.session) expected \(String(describing: want["leaveSession"]))") }
                if number(want["leaveWeekly"]) != leave.weekly { problems.append("\(id) leaveWeekly \(leave.weekly) expected \(String(describing: want["leaveWeekly"]))") }
            }
        }

        // 4. Plan.
        guard let active = sorted.first(where: { $0.id == activeUuid }) else { return problems + ["the active account was lost"] }
        let candidates = sorted.filter { $0.id != activeUuid }
        guard let strategyName = settings["strategy"] as? String, let strategy = AutoSwitchStrategy(rawValue: strategyName),
              let watchFable = settings["watchFable"] as? Bool, let drainEarly = settings["drainEarly"] as? Bool,
              let drainWithinHours = number(settings["drainWithinHours"]),
              let activeSampledThisCycle = vector["activeSampledThisCycle"] as? Bool else {
            return problems + ["settings need a known strategy, watchFable, drainEarly, drainWithinHours and activeSampledThisCycle"]
        }
        let hysteresis = number(settings["hysteresis"]) ?? hysteresisDefault
        let plan = AutoSwitchEngine.planWindowed(
            active: active, candidates: candidates, usageByAccount: usageByUuid,
            isSwitchable: { switchable[$0.id] ?? false }, isUnusable: { unusable[$0.id] ?? false },
            activeSampledThisCycle: activeSampledThisCycle,
            rule: { rules[$0.id] ?? .manualOnlyRule }, defaults: defaults,
            hysteresisPct: hysteresis,
            watchFable: watchFable,
            strategy: strategy,
            drainEarly: drainEarly,
            drainWithin: drainWithinHours * 3600,
            asOf: now)

        let want = expect["plan"]
        if want is NSNull {
            if let plan {
                problems.append("plan should stay put, got \(plan.limit.rawValue)/\(plan.trigger.rawValue) \(plan.targets.compactMap { idByUuid[$0.id] })")
            }
        } else if let want = want as? [String: Any] {
            guard let plan else { return problems + ["plan expected \(want["limit"] ?? "?")/\(want["trigger"] ?? "?") but the engine stays put"] }
            if (want["limit"] as? String) != plan.limit.rawValue { problems.append("limit \(plan.limit.rawValue) expected \(want["limit"] ?? "?")") }
            if (want["trigger"] as? String) != plan.trigger.rawValue { problems.append("trigger \(plan.trigger.rawValue) expected \(want["trigger"] ?? "?")") }
            if (want["relaxed"] as? Bool) != plan.relaxed { problems.append("relaxed \(plan.relaxed) expected \(want["relaxed"] ?? "?")") }
            if (want["window"] as? String) != plan.window?.rawValue { problems.append("window \(plan.window?.rawValue ?? "none") expected \(want["window"] ?? "none")") }
            let targets = plan.targets.compactMap { idByUuid[$0.id] }
            if (want["targets"] as? [String]) != targets { problems.append("targets \(targets) expected \(want["targets"] ?? "?")") }
        }
        return problems
    }
}


// MARK: - The loader refuses what it does not know

/// True for a JSON number (not a boolean, which Foundation also bridges to NSNumber).
private func isJSONNumber(_ value: Any?) -> Bool {
    guard let number = value as? NSNumber else { return false }
    return CFGetTypeID(number) != CFBooleanGetTypeID()
}

private func isJSONBool(_ value: Any?) -> Bool {
    guard let number = value as? NSNumber else { return false }
    return CFGetTypeID(number) == CFBooleanGetTypeID()
}

/// Every problem with the SHAPE of one vector: a missing required field, a field
/// of the wrong type, a field this harness does not know, or a strategy that is
/// not one of the three raw values. Empty for a well-formed case. The harness
/// never defaults a field; it reports it.
func conformanceShapeProblems(_ vector: [String: Any]) -> [String] {
    var problems: [String] = []

    func unknownKeys(_ object: [String: Any], allowed: Set<String>, in place: String) {
        for key in object.keys.sorted() where !allowed.contains(key) { problems.append("\(place): unknown field \(key)") }
    }
    func require(_ object: [String: Any], _ key: String, in place: String, _ valid: (Any?) -> Bool, _ what: String) {
        guard let value = object[key] else { problems.append("\(place): missing \(key)"); return }
        if !valid(value) { problems.append("\(place): \(key) must be \(what)") }
    }
    func numberOrNull(_ value: Any?) -> Bool { value is NSNull || isJSONNumber(value) }

    unknownKeys(vector, allowed: ["name", "now", "settings", "accounts", "active", "activeSampledThisCycle", "expect"], in: "case")
    require(vector, "name", in: "case", { $0 is String }, "a string")
    require(vector, "now", in: "case", { $0 is String }, "a string")
    require(vector, "active", in: "case", { $0 is String }, "a string")
    require(vector, "activeSampledThisCycle", in: "case", isJSONBool, "a boolean")

    if let settings = vector["settings"] as? [String: Any] {
        unknownKeys(settings, allowed: ["strategy", "watchFable", "drainEarly", "drainWithinHours", "hysteresis", "defaults"], in: "settings")
        require(settings, "strategy", in: "settings", { ($0 as? String).flatMap(AutoSwitchStrategy.init(rawValue:)) != nil }, "mostRoom, myOrder or resetsSoonest")
        require(settings, "watchFable", in: "settings", isJSONBool, "a boolean")
        require(settings, "drainEarly", in: "settings", isJSONBool, "a boolean")
        require(settings, "drainWithinHours", in: "settings", isJSONNumber, "a number")
        if let hysteresis = settings["hysteresis"], !isJSONNumber(hysteresis) { problems.append("settings: hysteresis must be a number") }
        if let defaults = settings["defaults"] as? [String: Any] {
            unknownKeys(defaults, allowed: ["base", "session", "weekly"], in: "settings.defaults")
            for (key, value) in defaults where !numberOrNull(value) { problems.append("settings.defaults: \(key) must be a number or null") }
        } else {
            problems.append("settings: defaults must be an object")
        }
    } else {
        problems.append("case: settings must be an object")
    }

    if let accounts = vector["accounts"] as? [[String: Any]] {
        if accounts.isEmpty { problems.append("case: accounts is empty") }
        for (index, account) in accounts.enumerated() {
            let place = "accounts[\(index)]"
            unknownKeys(account, allowed: ["id", "priority", "prioritySetOrder", "switchThreshold", "sessionThreshold", "weeklyThreshold",
                                           "switchable", "unusable", "usage"], in: place)
            require(account, "id", in: place, { $0 is String }, "a string")
            require(account, "switchable", in: place, isJSONBool, "a boolean")
            require(account, "unusable", in: place, isJSONBool, "a boolean")
            for key in ["priority", "prioritySetOrder", "switchThreshold", "sessionThreshold", "weeklyThreshold"] {
                if let value = account[key], !numberOrNull(value) { problems.append("\(place): \(key) must be a number or null") }
            }
            if let usage = account["usage"] {
                guard let usage = usage as? [String: Any] else { problems.append("\(place): usage must be an object"); continue }
                unknownKeys(usage, allowed: ["five_hour", "seven_day", "fable"], in: "\(place).usage")
                for (key, window) in usage {
                    guard let window = window as? [String: Any] else { problems.append("\(place).usage.\(key) must be an object"); continue }
                    unknownKeys(window, allowed: ["utilization", "resets_at"], in: "\(place).usage.\(key)")
                    if let utilization = window["utilization"], !numberOrNull(utilization) { problems.append("\(place).usage.\(key): utilization must be a number or null") }
                    if let resets = window["resets_at"], !(resets is String) { problems.append("\(place).usage.\(key): resets_at must be a string") }
                }
            }
        }
    } else {
        problems.append("case: accounts must be a list of objects")
    }

    if let expect = vector["expect"] as? [String: Any] {
        unknownKeys(expect, allowed: ["order", "rules", "plan"], in: "expect")
        require(expect, "order", in: "expect", { $0 is [String] }, "a list of ids")
        if let rules = expect["rules"] as? [String: [String: Any]] {
            for (id, rule) in rules {
                let place = "expect.rules.\(id)"
                unknownKeys(rule, allowed: ["session", "weekly", "manualOnly", "leaveSession", "leaveWeekly"], in: place)
                for key in ["session", "weekly", "leaveSession", "leaveWeekly"] { require(rule, key, in: place, isJSONNumber, "a number") }
                require(rule, "manualOnly", in: place, isJSONBool, "a boolean")
            }
        } else {
            problems.append("expect: rules must be an object of rules")
        }
        // `plan` must be present: null means "stay put", but a missing key is not that.
        if let plan = expect["plan"] {
            if let plan = plan as? [String: Any] {
                unknownKeys(plan, allowed: ["limit", "trigger", "relaxed", "window", "targets"], in: "expect.plan")
                require(plan, "limit", in: "expect.plan", { $0 is String }, "a string")
                require(plan, "trigger", in: "expect.plan", { $0 is String }, "a string")
                require(plan, "relaxed", in: "expect.plan", isJSONBool, "a boolean")
                require(plan, "targets", in: "expect.plan", { $0 is [String] }, "a list of ids")
                if let window = plan["window"], !(window is String) { problems.append("expect.plan: window must be a string") }
            } else if !(plan is NSNull) {
                problems.append("expect: plan must be null or an object")
            }
        } else {
            problems.append("expect: missing plan (use null to say stay put)")
        }
    } else {
        problems.append("case: expect must be an object")
    }
    return problems
}

/// The loader's refusals, pinned: a well-formed case passes and each kind of
/// defect the review found (an unknown strategy or alias, a missing flag, a
/// misspelled field, a missing plan) is reported.
@MainActor func runConformanceStrictnessTests() {
    func base() -> [String: Any] {
        ["name": "x", "now": "2026-10-24T12:00:00Z", "active": "a", "activeSampledThisCycle": true,
         "settings": ["strategy": "mostRoom", "watchFable": true, "drainEarly": false, "drainWithinHours": 24, "hysteresis": 1,
                      "defaults": ["base": 90, "session": NSNull()]],
         "accounts": [["id": "a", "switchable": true, "unusable": false, "switchThreshold": 0,
                       "usage": ["five_hour": ["utilization": 10, "resets_at": "2026-10-24T15:00:00Z"]]]],
         "expect": ["order": ["a"], "rules": ["a": ["session": 0, "weekly": 0, "manualOnly": true, "leaveSession": 90, "leaveWeekly": 90]],
                    "plan": NSNull()]]
    }
    func mutated(_ change: (inout [String: Any]) -> Void) -> [String] {
        var vector = base()
        change(&vector)
        return conformanceShapeProblems(vector)
    }
    func settingsChange(_ change: (inout [String: Any]) -> Void) -> [String] {
        mutated { v in
            var s = v["settings"] as! [String: Any]
            change(&s)
            v["settings"] = s
        }
    }
    check(conformanceShapeProblems(base()).isEmpty, "conformance loader: a well-formed case has no shape problems", "\(conformanceShapeProblems(base()))")
    check(settingsChange { $0["strategy"] = "priority" }.contains { $0.contains("strategy") },
          "conformance loader: an alias or unknown strategy is refused, not run as Most room left")
    check(settingsChange { $0["strategy"] = nil }.contains { $0.contains("missing strategy") }, "conformance loader: a missing strategy is refused")
    check(settingsChange { $0["watchFable"] = nil }.contains { $0.contains("missing watchFable") }, "conformance loader: a missing watchFable is refused")
    check(settingsChange { $0["drainEarly"] = nil }.contains { $0.contains("missing drainEarly") }, "conformance loader: a missing drainEarly is refused")
    check(mutated { $0["activeSampledThisCycle"] = nil }.contains { $0.contains("missing activeSampledThisCycle") },
          "conformance loader: a missing activeSampledThisCycle is refused")
    check(settingsChange { $0["watchFable"] = 1 }.contains { $0.contains("watchFable must be a boolean") },
          "conformance loader: a number where a boolean belongs is refused")
    check(settingsChange { $0["watchfable"] = true }.contains { $0.contains("unknown field watchfable") },
          "conformance loader: a misspelled field is reported as unknown")
    check(mutated { $0["extra"] = 1 }.contains { $0.contains("unknown field extra") }, "conformance loader: an unknown case field is reported")
    check(mutated { v in var e = v["expect"] as! [String: Any]; e["plan"] = nil; v["expect"] = e }.contains { $0.contains("missing plan") },
          "conformance loader: a missing plan is refused, not read as 'stay put'")
    check(mutated { v in
        var accounts = v["accounts"] as! [[String: Any]]
        accounts[0]["switchable"] = nil
        v["accounts"] = accounts
    }.contains { $0.contains("missing switchable") }, "conformance loader: a missing switchable is refused")
    check(mutated { v in
        var accounts = v["accounts"] as! [[String: Any]]
        accounts[0]["usage"] = ["five_hour": ["util": 10]]
        v["accounts"] = accounts
    }.contains { $0.contains("unknown field util") }, "conformance loader: a misspelled usage field is reported")
    check(ConformanceCase.run(mutatedVector { $0["activeSampledThisCycle"] = nil }, hysteresisDefault: 1).contains { $0.contains("missing activeSampledThisCycle") },
          "conformance loader: run() reports the shape problems instead of running the case")
}

private func mutatedVector(_ change: (inout [String: Any]) -> Void) -> [String: Any] {
    var vector: [String: Any] = ["name": "x", "now": "2026-10-24T12:00:00Z", "active": "a", "activeSampledThisCycle": true,
        "settings": ["strategy": "mostRoom", "watchFable": true, "drainEarly": false, "drainWithinHours": 24, "defaults": [String: Any]()],
        "accounts": [["id": "a", "switchable": true, "unusable": false]],
        "expect": ["order": ["a"], "rules": ["a": ["session": 90, "weekly": 90, "manualOnly": false, "leaveSession": 90, "leaveWeekly": 90]], "plan": NSNull()]]
    change(&vector)
    return vector
}
