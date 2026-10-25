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

private enum ConformanceCase {
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
            switchable[uuid] = spec["switchable"] as? Bool ?? true
            unusable[uuid] = spec["unusable"] as? Bool ?? false
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
        let hysteresis = number(settings["hysteresis"]) ?? hysteresisDefault
        let plan = AutoSwitchEngine.planWindowed(
            active: active, candidates: candidates, usageByAccount: usageByUuid,
            isSwitchable: { switchable[$0.id] ?? false }, isUnusable: { unusable[$0.id] ?? false },
            activeSampledThisCycle: vector["activeSampledThisCycle"] as? Bool ?? true,
            rule: { rules[$0.id] ?? .manualOnlyRule }, defaults: defaults,
            hysteresisPct: hysteresis,
            watchFable: settings["watchFable"] as? Bool ?? true,
            strategy: AutoSwitchStrategy(rawValue: settings["strategy"] as? String ?? "") ?? .mostRoom,
            drainEarly: settings["drainEarly"] as? Bool ?? false,
            drainWithin: (number(settings["drainWithinHours"]) ?? 24) * 3600,
            asOf: now)

        let want = expect["plan"]
        if want == nil || want is NSNull {
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
