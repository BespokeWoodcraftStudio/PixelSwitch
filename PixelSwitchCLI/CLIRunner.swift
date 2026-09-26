import Foundation

/// Exit codes, as `pixelswitch help` lists them.
enum CLIExit {
    static let ok: Int32 = 0
    static let failed: Int32 = 1
    static let usage: Int32 = 2
    static let busy: Int32 = 3
    static let notFound: Int32 = 4
    static let unreachable: Int32 = 5

    static func code(for kind: ControlErrorKind) -> Int32 {
        switch kind {
        case .busy: return busy
        case .notFound, .ambiguous: return notFound
        case .invalidValue, .invalidParams: return usage
        default: return failed
        }
    }
}

/// Runs one parsed command against the app and prints the result.
struct CLIRunner {
    let client: ControlClient
    /// Unbuffered, like `err`: over SSH stdout is a pipe, and stdio would hold
    /// `watch`'s events back in a 4 KB buffer.
    var out: (String) -> Void = { FileHandle.standardOutput.write(Data(($0 + "\n").utf8)) }
    var err: (String) -> Void = { FileHandle.standardError.write(Data(($0 + "\n").utf8)) }
    /// How often `--wait` checks a sign-in, in seconds.
    var pollInterval: TimeInterval = 1

    func run(_ invocation: CLIInvocation) -> Int32 {
        switch invocation.command {
        case .help:
            out(CLIParser.helpText)
            return CLIExit.ok
        case .version:
            out("pixelswitch (PixelSwitch remote-control protocol \(ControlProtocol.version))")
            return CLIExit.ok
        default:
            break
        }
        do {
            try client.connect()
            try Self.checkProtocol(client, timeout: invocation.timeout)
            return try execute(invocation)
        } catch let error as ControlError {
            err("error: \(error.message)")
            if !error.candidates.isEmpty { err("  choices: " + error.candidates.joined(separator: ", ")) }
            return CLIExit.code(for: error.kind)
        } catch let error as ControlClient.ClientError {
            err("error: \(error.description)")
            if case .unreachable = error { return CLIExit.unreachable }
            return CLIExit.failed
        } catch {
            err("error: \(error.localizedDescription)")
            return CLIExit.failed
        }
    }

    /// Refuses an app that speaks another protocol version.
    static func checkProtocol(_ client: ControlClient, timeout: TimeInterval) throws {
        let status = try client.call(.statusGet, timeout: timeout).decode(StatusInfo.self)
        guard status.protocolVersion == ControlProtocol.version else {
            throw ControlError(.protocolMismatch,
                               "PixelSwitch \(status.appVersion) speaks remote-control protocol \(status.protocolVersion), but this pixelswitch speaks \(ControlProtocol.version). Quit and reopen PixelSwitch, then run the command again.")
        }
    }

    private func execute(_ invocation: CLIInvocation) throws -> Int32 {
        let timeout = invocation.timeout
        let json = invocation.json
        func call(_ method: ControlMethod, _ params: JSONValue? = nil, slow: Bool = false) throws -> JSONValue {
            try client.call(method, params, timeout: slow ? max(timeout, 120) : timeout)
        }
        func show(_ result: JSONValue, _ text: () -> String) {
            out(json ? result.prettyText : text())
        }

        switch invocation.command {
        case .help, .version, .mcp:
            return CLIExit.ok

        case .status:
            let result = try call(.statusGet)
            show(result) { CLIOutput.status((try? result.decode(StatusInfo.self))) }

        case .listAccounts:
            let result = try call(.accountsList)
            show(result) { CLIOutput.accounts(Self.accounts(result["accounts"])) }

        case .switchAccount(let reference):
            let result = try call(.accountsSwitch, .object(["account": .string(reference)]), slow: true)
            show(result) { "Switched to \(CLIOutput.name(try? result["account"]?.decode(AccountInfo.self)))." }

        case .addCurrent:
            let result = try call(.accountsAddCurrent, slow: true)
            show(result) { "Saved \(CLIOutput.name(try? result["account"]?.decode(AccountInfo.self)))." }

        case .signIn(let account, let open, let wait):
            var params: [String: JSONValue] = [:]
            if let account { params["account"] = .string(account) }
            if let open = open.parameter { params["open"] = .string(open) }
            var info = try Self.signIn(try call(.signInStart, .object(params)))
            // The links arrive a moment after the CLI starts; wait for them (or the fallback).
            let linkDeadline = Date().addingTimeInterval(15)
            while info.state == "starting", Date() < linkDeadline {
                Thread.sleep(forTimeInterval: 0.5)
                info = try Self.signIn(try call(.signInStatus))
            }
            if !wait {
                show((try? JSONValue.encode(info)) ?? .null) { CLIOutput.signIn(info) }
                return CLIExit.ok
            }
            if !json { out(CLIOutput.signIn(info)) }
            var lastState = info.state
            let waitDeadline = Date().addingTimeInterval(16 * 60)
            while !["succeeded", "failed", "cancelled"].contains(info.state), Date() < waitDeadline {
                Thread.sleep(forTimeInterval: pollInterval)
                info = try Self.signIn(try call(.signInStatus))
                if info.state != lastState, !json { out("… " + CLIOutput.stateWords(info)) }
                lastState = info.state
            }
            if json { out(((try? JSONValue.encode(info)) ?? .null).prettyText) }
            return info.state == "succeeded" ? CLIExit.ok : CLIExit.failed

        case .signInStatus:
            let result = try call(.signInStatus)
            show(result) { result["signIn"].flatMap { try? $0.decode(SignInInfo.self) }.map(CLIOutput.signIn) ?? "No sign-in has been started." }

        case .signInCode(let code):
            let result = try call(.signInSubmitCode, .object(["code": .string(code)]))
            show(result) { "Code handed to Claude Code; it is checking it now." }

        case .signInCancel:
            let result = try call(.signInCancel)
            show(result) { "Sign-in cancelled." }

        case .remove(let reference):
            let result = try call(.accountsRemove, .object(["account": .string(reference)]))
            show(result) { "Removed \(CLIOutput.name(try? result["removed"]?.decode(AccountInfo.self)))." }

        case .label(let reference, let label):
            let result = try call(.accountsSetLabel, .object(["account": .string(reference), "label": label.map(JSONValue.string) ?? .null]))
            show(result) { label == nil ? "Label cleared." : "Label set: \(CLIOutput.name(try? result["account"]?.decode(AccountInfo.self)))." }

        case .threshold(let reference, let threshold):
            let result = try call(.accountsSetThreshold, .object(["account": .string(reference), "threshold": threshold.map(JSONValue.number) ?? .null]))
            show(result) {
                guard let account = try? result["account"]?.decode(AccountInfo.self) else { return "Threshold set." }
                return account.threshold == nil
                    ? "\(account.email) now follows the default threshold (\(CLIOutput.percent(account.effectiveThreshold)))."
                    : "\(account.email) now switches at \(CLIOutput.percent(account.effectiveThreshold))."
            }

        case .order(let references):
            let result = try call(.accountsSetOrder, .object(["accounts": .array(references.map(JSONValue.string))]))
            show(result) { CLIOutput.accounts(Self.accounts(result["accounts"])) }

        case .usage(let reference):
            let result = try call(.usageGet, reference.map { .object(["account": .string($0)]) })
            show(result) { CLIOutput.usage(try? result.decode(UsageReport.self)) }

        case .refresh:
            let result = try call(.usageRefresh, slow: true)
            show(result) { CLIOutput.usage(try? result.decode(UsageReport.self)) }

        case .watch:
            _ = try call(.eventsSubscribe)
            if !json { err("Watching PixelSwitch. Press Control-C to stop.") }
            while true {
                guard let note = try client.nextEvent(until: .distantFuture) else { continue }
                out((note.params ?? .null).compactText)
            }

        case .settingsGet(let key):
            let result = try call(.settingsGet, key.map { .object(["key": .string($0)]) })
            show(result) { CLIOutput.settings(result["settings"]) }

        case .settingsSet(let key, let text):
            let result = try call(.settingsSet, .object(["key": .string(key), "value": Self.value(from: text)]))
            show(result) { CLIOutput.settings(result["settings"]) }

        case .updateCheck:
            let result = try call(.appCheckForUpdates)
            show(result) { "PixelSwitch is checking for updates; its window opens on that Mac." }

        case .quit:
            let result = try call(.appQuit)
            show(result) { "PixelSwitch is quitting." }
        }
        return CLIExit.ok
    }

    /// What a person typed after `settings set <key>`, as JSON when it reads
    /// as JSON (true, 90, ["a","b"], "text"), otherwise as plain text.
    static func value(from text: String) -> JSONValue {
        if let parsed = try? ControlCoding.decoder.decode(JSONValue.self, from: Data(text.utf8)) { return parsed }
        return .string(text)
    }

    static func accounts(_ value: JSONValue?) -> [AccountInfo] {
        (try? value?.decode([AccountInfo].self)) ?? []
    }

    static func signIn(_ result: JSONValue) throws -> SignInInfo {
        guard let value = result["signIn"], !value.isNull else {
            throw ControlError(.notFound, "No sign-in has been started.")
        }
        return try value.decode(SignInInfo.self)
    }
}

/// Readable output. Pure, so the formats are unit-tested.
enum CLIOutput {
    static func percent(_ value: Double?) -> String {
        guard let value else { return "—" }
        return "\(Int(value.rounded()))%"
    }

    static func name(_ account: AccountInfo?) -> String {
        guard let account else { return "the account" }
        if let label = account.label { return "\(account.email) (\(label))" }
        return account.email
    }

    static func time(_ date: Date?) -> String {
        guard let date else { return "—" }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: date)
    }

    static func reset(_ iso: String?) -> String {
        guard let iso, let date = parseISO(iso) else { return "" }
        return " (resets \(time(date)))"
    }

    static func parseISO(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }

    static func strategyWords(_ raw: String) -> String {
        switch raw {
        case "myOrder": return "my order"
        case "resetsSoonest": return "resets soonest"
        default: return "most room left"
        }
    }

    static func status(_ status: StatusInfo?) -> String {
        guard let status else { return "PixelSwitch answered, but not in a form this pixelswitch understands." }
        var lines = ["PixelSwitch \(status.appVersion) (remote-control protocol \(status.protocolVersion))"]
        lines.append("Active:        \(status.activeAccountEmail ?? "none")")
        lines.append("Accounts:      \(status.accountCount)")
        let a = status.autoSwitch
        var auto = a.enabled ? "on, default \(percent(a.defaultThreshold)), next by \(strategyWords(a.strategy))" : "off"
        if a.enabled, !a.onFable { auto += ", Fable ignored" }
        if a.enabled, a.strategy == "resetsSoonest" {
            auto += a.drainEarly ? ", switches early within \(Int(a.drainWithinHours)) h" : ", no early switching"
        }
        lines.append("Auto-switch:   \(auto)")
        lines.append("Claude CLI:    \(status.claudeAvailable ? "found" : "not found")")
        lines.append("Last refresh:  \(time(status.lastRefresh))")
        if let signIn = status.signIn { lines.append("Sign-in:       \(stateWords(signIn))") }
        return lines.joined(separator: "\n")
    }

    static func accounts(_ accounts: [AccountInfo]) -> String {
        guard !accounts.isEmpty else { return "No accounts yet. Add one: pixelswitch accounts add-current" }
        var rows = [["", "#", "Account", "Session", "Weekly", "Fable", "Switches at"]]
        for account in accounts {
            let usage = account.usage
            let at = percent(account.effectiveThreshold) + (account.threshold == nil ? " (default)" : "")
            rows.append([
                account.isActive ? "*" : (account.isSwitchable ? "" : "!"),
                String(account.position),
                name(account),
                percent(usage?.session?.utilization),
                percent(usage?.weekly?.utilization),
                percent(usage?.fable?.utilization),
                at
            ])
        }
        var text = table(rows)
        text += "\n* active   ! cannot be switched to until it is re-signed (pixelswitch accounts reauth <account>)"
        return text
    }

    static func usage(_ report: UsageReport?) -> String {
        guard let report else { return "No usage yet." }
        var lines: [String] = []
        for account in report.accounts {
            lines.append("\(account.position). \(name(account))\(account.isActive ? "  [active]" : "")")
            if let usage = account.usage {
                lines.append("   Session \(percent(usage.session?.utilization))\(reset(usage.session?.resetsAt))")
                lines.append("   Weekly  \(percent(usage.weekly?.utilization))\(reset(usage.weekly?.resetsAt))")
                if let fable = usage.fable { lines.append("   Fable   \(percent(fable.utilization))\(reset(fable.resetsAt))") }
                if let error = usage.error { lines.append("   Note: \(error)") }
                if let sampled = usage.sampledAt { lines.append("   Read at \(time(sampled))") }
            } else {
                lines.append("   No reading yet.")
            }
        }
        let m = report.machine
        lines.append(String(format: "This Mac today: $%.2f, %d turns, %d min coding, %d lines written", m.todayCost, m.conversationTurns, m.activeCodingMinutes, m.linesWritten))
        return lines.joined(separator: "\n")
    }

    static func stateWords(_ info: SignInInfo) -> String {
        switch info.state {
        case "starting": return "getting the sign-in link"
        case "waitingForUser": return info.codeSubmitted ? "checking the code" : "waiting for someone to sign in in a browser"
        case "completing": return "saving the account"
        case "succeeded": return "signed in"
        case "failed": return "failed: \(info.message ?? "unknown reason")"
        case "cancelled": return "cancelled"
        default: return info.state
        }
    }

    static func signIn(_ info: SignInInfo) -> String {
        let what = info.purpose == "reauthenticate" ? "Re-sign \(info.email ?? "account")" : "New account"
        var lines = ["\(what): \(stateWords(info))"]
        if let link = info.automaticLink {
            lines.append("On the Mac running PixelSwitch, open this in any browser (it finishes by itself):")
            lines.append("  \(link)")
        }
        if let link = info.manualLink {
            lines.append("On another device, open this; the page then shows a code:")
            lines.append("  \(link)")
            lines.append("  then run: pixelswitch accounts sign-in code <code>")
        }
        if let notice = info.notice { lines.append(notice) }
        return lines.joined(separator: "\n")
    }

    static func settings(_ value: JSONValue?) -> String {
        guard let object = value?.objectValue else { return "" }
        let order = SettingKeyNames.all
        let keys = object.keys.sorted { (order.firstIndex(of: $0) ?? .max, $0) < (order.firstIndex(of: $1) ?? .max, $1) }
        return keys.map { "\($0) = \(object[$0]!.compactText)" }.joined(separator: "\n")
    }

    static func table(_ rows: [[String]]) -> String {
        let widths = (0..<(rows.first?.count ?? 0)).map { column in rows.map { $0[column].count }.max() ?? 0 }
        return rows.map { row in
            row.enumerated().map { $1.padding(toLength: widths[$0], withPad: " ", startingAt: 0) }
                .joined(separator: "  ").trimmingCharacters(in: .whitespaces)
        }.joined(separator: "\n")
    }
}

/// The settings' names in the order the Settings window shows them. Kept here
/// (not taken from the app's `SettingKey`) because the tool does not compile
/// the app's code; `ControlTests` checks the two lists match.
enum SettingKeyNames {
    static let all = [
        "refreshInterval", "transcriptLookbackHours",
        "autoSwitch.enabled", "autoSwitch.threshold", "autoSwitch.onFable", "autoSwitch.strategy",
        "autoSwitch.drainEarly", "autoSwitch.drainWithinHours",
        "maskEmailAddresses", "colorCodeAccounts", "appLanguage", "launchAtLogin",
        "menuBar.showsHeadIcon", "menuBar.modules", "menuBar.customizesLimitBarColors",
        "menuBar.color.session", "menuBar.color.weekly", "menuBar.color.fable", "menuBar.color.lowRemaining",
        "menuBar.lowRemainingWarningThreshold", "claude.binaryPath"
    ]
}
