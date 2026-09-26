// Remote control (Part C): the protocol, the account resolver, the settings
// catalog, the control API against a fake app, the no-token rule, the
// command-line parser and output, the MCP server, the socket server and client
// end to end, and the command-line tool installer. Called from main.swift.
import Foundation

@MainActor func runControlTests() {
    runControlProtocolTests()
    runControlResolverTests()
    runControlSettingsCatalogTests()
    runControlAPITests()
    runControlNoTokenTests()
    runControlSocketTests()
    runControlCLIParserTests()
    runControlCLIOutputTests()
    runControlMCPTests()
    runControlInstallerTests()
}

/// Shared helpers, in one namespace so nothing collides with other test files.
enum ControlTestKit {
    @MainActor final class Box<Value> {
        var value: Value
        init(_ value: Value) { self.value = value }
    }

    /// Runs the main run loop until `condition` holds or `timeout` passes.
    @MainActor @discardableResult
    static func spin(until condition: () -> Bool, timeout: TimeInterval = 5) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition(), Date() < deadline {
            RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.01))
        }
        return condition()
    }

    /// Runs async main-actor work to completion from synchronous test code.
    @MainActor
    static func wait<T>(_ body: @escaping @MainActor () async -> T) -> T? {
        let box = Box<T?>(nil)
        Task { @MainActor in box.value = await body() }
        spin(until: { box.value != nil })
        return box.value
    }

    static let a = Account(id: UUID(uuidString: "11111111-1111-1111-1111-111111111111")!, email: "alice@work.com", displayName: "Alice",
                           subscriptionType: "max", isActive: true, customLabel: "Work")
    static let b = Account(id: UUID(uuidString: "22222222-2222-2222-2222-222222222222")!, email: "bob@home.com", displayName: "Bob",
                           subscriptionType: "pro", switchThreshold: 70)
    static let c = Account(id: UUID(uuidString: "33333333-3333-3333-3333-333333333333")!, email: "carol@work.com", displayName: "Carol",
                           customLabel: "Work")

    static let usageJSON = #"{"five_hour":{"utilization":42.0,"resets_at":"2026-09-25T18:00:00.000000+00:00"},"seven_day":{"utilization":61.0,"resets_at":"2026-09-30T09:00:00+00:00"},"extra_usage":{"is_enabled":false,"monthly_limit":null,"used_credits":null,"utilization":null},"limits":[{"kind":"weekly_scoped","group":"weekly","percent":10,"severity":"normal","resets_at":"2026-09-30T09:00:00+00:00","scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":true}]}"#

    /// Runs blocking client work off the main thread while the main actor serves it.
    @MainActor
    static func offMain<T: Sendable>(_ work: @escaping @Sendable () throws -> T) -> Result<T, Error>? {
        let box = Box<Result<T, Error>?>(nil)
        DispatchQueue.global().async {
            let result = Result { try work() }
            DispatchQueue.main.async { MainActor.assumeIsolated { box.value = result } }
        }
        spin(until: { box.value != nil }, timeout: 10)
        return box.value
    }

    static func makeTempDirectory() -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("psc-\(UUID().uuidString.prefix(8))", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static func permissions(_ path: String) -> Int {
        ((try? FileManager.default.attributesOfItem(atPath: path))?[.posixPermissions] as? NSNumber)?.intValue ?? -1
    }
}

// MARK: - Protocol

@MainActor func runControlProtocolTests() {
    let value: JSONValue = .object(["b": .bool(true), "n": .number(2.5), "s": .string("x"), "a": .array([.null, .number(1)]), "o": .object([:])])
    let round = try? ControlCoding.decoder.decode(JSONValue.self, from: Data(value.compactText.utf8))
    check(round == value, "control protocol: any JSON value survives a round trip", value.compactText)
    let plainAccount = AccountInfo(id: "1", email: "a@x.com", label: nil, subscription: nil, isActive: false, isSwitchable: true, position: 1,
                                   threshold: nil, effectiveThreshold: 90, manualOnly: false, usage: nil)
    check(ControlProtocol.version == 2 && (try? JSONValue.encode(plainAccount))?["manualOnly"] == .bool(false),
          "control protocol: version 2 (accounts carry manualOnly)")
    check(JSONValue.bool(true).compactText == "true" && JSONValue.number(1).compactText == "1", "control protocol: true stays a boolean and 1 a number")

    let success = RPCResponse.success(.int(7), .object(["ok": .bool(true)])).line
    check(success == #"{"id":7,"jsonrpc":"2.0","result":{"ok":true}}"#, "control protocol: a success reply has no error member", success)
    let failure = RPCResponse.failure(.string("x"), ControlError(.ambiguous, "two", candidates: ["a", "b"])).line
    check(!failure.contains("\"result\"") && failure.contains("-32003") && failure.contains("\"candidates\":[\"a\",\"b\"]"),
          "control protocol: an error reply carries the code, kind and candidates", failure)
    let decoded = (try? ControlCoding.decoder.decode(RPCResponse.self, from: Data(failure.utf8)))?.error.map(ControlError.init(rpc:))
    check(decoded == ControlError(.ambiguous, "two", candidates: ["a", "b"]), "control protocol: an error survives the trip back to the client")

    let codes = ControlErrorKind.allCases.map(\.code)
    check(Set(codes).count == codes.count, "control protocol: every error kind has its own code")
    check(ControlErrorKind(code: -32001) == .busy && ControlErrorKind(code: 12345) == .failed, "control protocol: codes map back to kinds, unknown ones to failed")

    check(ControlMethod.allCases.map(\.rawValue) == [
        "status.get", "accounts.list", "accounts.switch", "accounts.addCurrent", "accounts.signIn.start", "accounts.signIn.status",
        "accounts.signIn.submitCode", "accounts.signIn.cancel", "accounts.remove", "accounts.setLabel", "accounts.setThreshold",
        "accounts.setOrder", "usage.get", "usage.refresh", "settings.get", "settings.set", "events.subscribe",
        "app.checkForUpdates", "app.quit"], "control protocol: the method names are the ones the design lists")

    let socket = ControlProtocol.socketPath(home: "/Users/someone")
    check(socket == "/Users/someone/Library/Application Support/PixelSwitch/control.sock", "control protocol: the socket lives in PixelSwitch's Application Support folder")
    check(ControlProtocol.socketPath(home: NSHomeDirectory()).utf8.count <= ControlProtocol.maxSocketPathBytes,
          "control protocol: this Mac's socket path fits the kernel's limit")

    let date = Date(timeIntervalSince1970: 1_790_000_000)
    let info = try? JSONValue.encode(WindowInfo(utilization: 5, resetsAt: nil))
    check(info == .object(["utilization": .number(5)]), "control protocol: an absent value is left out, not sent as null", info?.compactText ?? "nil")
    let event = try? JSONValue.encode(ControlEvent(type: "usageUpdated", at: date, data: nil))
    check(event?["at"] == .string("2026-09-21T14:13:20Z"), "control protocol: dates travel as ISO 8601", event?.compactText ?? "nil")
}

// MARK: - Account resolver

@MainActor func runControlResolverTests() {
    let accounts = [ControlTestKit.a, ControlTestKit.b, ControlTestKit.c]
    func resolved(_ ref: String) -> String {
        do { return try AccountResolver.resolve(ref, in: accounts).email } catch let e as ControlError { return "\(e.kind.rawValue):\(e.candidates.count)" } catch { return "?" }
    }
    check(resolved("22222222-2222-2222-2222-222222222222") == "bob@home.com", "resolver: an id picks the account")
    check(resolved("22222222-2222-2222-2222-222222222222".lowercased()) == "bob@home.com", "resolver: ids are case-insensitive")
    check(resolved("  BOB@home.com ") == "bob@home.com", "resolver: an email, case-insensitive and trimmed")
    check(resolved("work") == "ambiguous:2", "resolver: a label two accounts share is ambiguous and lists both")
    check(resolved("3") == "carol@work.com", "resolver: a position picks from the priority order")
    check(resolved("4") == "notFound:3" && resolved("nobody") == "notFound:3", "resolver: no match lists every account")
    check(resolved("") == "invalidValue:0", "resolver: an empty name is refused")
    let labelled = Account(email: "x@y.com", displayName: "X", customLabel: "2")
    check((try? AccountResolver.resolve("2", in: [ControlTestKit.a, labelled]))?.email == "x@y.com",
          "resolver: a label that looks like a number wins over the position")
    check(AccountResolver.describe(ControlTestKit.a) == "alice@work.com (Work)" && AccountResolver.describe(ControlTestKit.b) == "bob@home.com",
          "resolver: accounts are named by email, with the label when there is one")
    check((try? AccountResolver.resolveAll(["1", "nobody"], in: accounts)) == nil, "resolver: one bad name refuses the whole list")
}

// MARK: - Settings catalog

@MainActor func runControlSettingsCatalogTests() {
    func norm(_ key: SettingKey, _ value: JSONValue) -> JSONValue? { try? key.normalize(value) }
    check(norm(.autoSwitchEnabled, .string("on")) == .bool(true) && norm(.autoSwitchEnabled, .string("NO")) == .bool(false)
          && norm(.autoSwitchEnabled, .number(1)) == .bool(true) && norm(.autoSwitchEnabled, .string("maybe")) == nil,
          "settings: on/off, yes/no, 1/0 and true/false all read as booleans")
    check(norm(.refreshInterval, .string("60")) == .number(60) && norm(.refreshInterval, .number(45)) == nil,
          "settings: the refresh interval takes only the window's choices")
    check(norm(.transcriptLookbackHours, .number(0)) == .number(0), "settings: 0 (all history) is a lookback choice")
    check(norm(.autoSwitchStrategy, .string("RESETSSOONEST")) == .string("resetsSoonest") && norm(.autoSwitchStrategy, .string("fastest")) == nil,
          "settings: the strategy is matched case-insensitively and stored by its canonical name")
    check(norm(.autoSwitchThreshold, .string("72.4")) == .number(72) && norm(.autoSwitchThreshold, .number(100)) == .number(100)
          && norm(.autoSwitchThreshold, .number(49)) == nil && norm(.autoSwitchThreshold, .number(.infinity)) == nil
          && norm(.autoSwitchThreshold, .number(0)) == nil,
          "settings: the default threshold is a whole number from 50 to 100")
    check(norm(.autoSwitchDrainWithinHours, .number(0.4)) == nil && norm(.autoSwitchDrainWithinHours, .number(72)) == .number(72),
          "settings: the early-switch window is 1–72 hours")
    check(norm(.menuBarLowRemainingWarningThreshold, .number(33.5)) == .number(33.5), "settings: the low-remaining threshold keeps a fraction")
    check(norm(.menuBarModules, .string("account, weeklyBar,ACCOUNT")) == .array([.string("account"), .string("weeklyBar")])
          && norm(.menuBarModules, .array([.string("nope")])) == nil,
          "settings: menu bar modules come as a list or comma text, repeats dropped, unknown names refused")
    check(norm(.menuBarColorSession, .string("34c759")) == .string("#34C759") && norm(.menuBarColorSession, .string("#12345")) == nil,
          "settings: colours are stored as #RRGGBB")
    check(norm(.claudeBinaryPath, .string("auto")) == .string("") && norm(.claudeBinaryPath, .string("/opt/bin/claude")) == .string("/opt/bin/claude")
          && norm(.claudeBinaryPath, .string("claude")) == nil,
          "settings: the claude path is auto or absolute")
    check(norm(.appLanguage, .string("ja")) == .string("ja") && norm(.appLanguage, .string("es")) == nil, "settings: only the app's five languages or auto")
    check((try? SettingKey.named("AUTOSWITCH.ENABLED")) == .autoSwitchEnabled, "settings: keys are found case-insensitively")
    check((try? SettingKey.named("updates.automatic")) == .autoUpdate && norm(.autoUpdate, .string("on")) == .bool(true)
          && norm(.autoUpdate, .string("sometimes")) == nil,
          "settings: automatic updates is an on/off setting called updates.automatic")
    do {
        _ = try SettingKey.named("volume")
        check(false, "settings: an unknown key lists the real ones")
    } catch let error as ControlError {
        check(error.kind == .notFound && error.candidates.count == SettingKey.allCases.count, "settings: an unknown key lists the real ones")
    } catch {
        check(false, "settings: an unknown key lists the real ones")
    }
    do {
        _ = try SettingKey.autoSwitchThreshold.normalize(.number(10))
    } catch let error as ControlError {
        check(error.message == "autoSwitch.threshold must be 50–100, whole numbers.", "settings: a refused value says what is allowed", error.message)
    } catch {}
}

// MARK: - Control API

extension ControlTestKit {
    /// A stand-in app: records every call, answers from plain properties.
    @MainActor final class FakeApp: AppControlling {
        var appVersion = "1.2"
        var claudeAvailable = true
        var lastUsageRefresh: Date? = Date(timeIntervalSince1970: 1_790_000_000)
        var accounts: [Account] = [ControlTestKit.a, ControlTestKit.b, ControlTestKit.c]
        var autoSwitch = AutoSwitchInfo(enabled: true, defaultThreshold: 90, onFable: true, strategy: "mostRoom", drainEarly: true, drainWithinHours: 24)
        var machineUsage = MachineUsageInfo(todayCost: 12.5, totalCost: 80, conversationTurns: 30, activeCodingMinutes: 95, linesWritten: 400, modelUsage: ["opus": 3])
        var signIn: SignInInfo?
        var usageById: [UUID: UsageAPIResponse] = [:]
        var switchable: Set<UUID> = [ControlTestKit.a.id, ControlTestKit.b.id, ControlTestKit.c.id]
        var calls: [String] = []
        var nextError: ControlError?
        var orderResult = true
        var settings: [SettingKey: JSONValue] = [:]

        init() {
            usageById[ControlTestKit.a.id] = try? JSONDecoder().decode(UsageAPIResponse.self, from: Data(ControlTestKit.usageJSON.utf8))
        }

        func usage(for id: UUID) -> UsageAPIResponse? { usageById[id] }
        func usageSampledAt(for id: UUID) -> Date? { usageById[id] == nil ? nil : Date(timeIntervalSince1970: 1_790_000_100) }
        func usageError(for id: UUID) -> String? { id == ControlTestKit.c.id ? "Token expired. Switch to refresh." : nil }
        func isSwitchable(_ id: UUID) -> Bool { switchable.contains(id) }
        func effectiveSwitchThreshold(for account: Account) -> Double { account.switchThreshold ?? autoSwitch.defaultThreshold }

        private func fail() throws { if let error = nextError { nextError = nil; throw error } }

        func switchAccount(to id: UUID) async throws {
            calls.append("switch \(id.uuidString)")
            try fail()
            accounts = accounts.map { var a = $0; a.isActive = (a.id == id); return a }
        }
        func addCurrentAccount() async throws -> UUID {
            calls.append("addCurrent")
            try fail()
            return ControlTestKit.b.id
        }
        func startSignIn(_ purpose: SignInPurpose, open: String?) throws -> SignInInfo {
            calls.append("signIn \(purpose) open=\(open ?? "nil")")
            try fail()
            let info = SignInInfo(id: "S1", purpose: "newAccount", accountId: nil, email: nil, state: "starting", message: nil,
                                  resultAccountId: nil, automaticLink: nil, manualLink: nil, notice: nil, codeSubmitted: false,
                                  startedAt: Date(timeIntervalSince1970: 1_790_000_000))
            signIn = info
            return info
        }
        func submitSignInCode(_ code: String) throws -> SignInInfo {
            calls.append("code \(code)")
            try fail()
            return signIn!
        }
        func cancelSignIn() throws -> SignInInfo {
            calls.append("cancel")
            try fail()
            return signIn!
        }
        func removeAccount(_ id: UUID) throws {
            calls.append("remove \(id.uuidString)")
            try fail()
            accounts.removeAll { $0.id == id }
        }
        func setLabel(_ label: String?, for id: UUID) {
            calls.append("label \(label ?? "nil")")
            accounts = accounts.map { var a = $0; if a.id == id { a.customLabel = label }; return a }
        }
        func setSwitchThreshold(_ threshold: Double?, for id: UUID) {
            calls.append("threshold \(threshold.map { String($0) } ?? "nil")")
            accounts = accounts.map { var a = $0; if a.id == id { a.switchThreshold = threshold }; return a }
        }
        func setAccountOrder(_ ids: [UUID]) -> Bool {
            calls.append("order \(ids.map { String($0.uuidString.prefix(1)) }.joined())")
            guard orderResult else { return false }
            accounts = ids.compactMap { id in accounts.first { $0.id == id } }
            return true
        }
        func refreshUsage() async { calls.append("refresh") }
        func settingValue(_ key: SettingKey) -> JSONValue { settings[key] ?? .string("value of \(key.rawValue)") }
        func setSetting(_ key: SettingKey, to value: JSONValue) throws {
            calls.append("set \(key.rawValue)=\(value.compactText)")
            try fail()
            settings[key] = value
        }
        func checkForUpdates() { calls.append("updates") }
        func quit() { calls.append("quit") }
    }

    /// Sends one request through `api` and returns the decoded response.
    @MainActor
    static func call(_ api: ControlAPI, _ method: String, _ params: JSONValue? = nil, id: Int? = 1,
                     onSubscribe: @escaping @MainActor () -> Void = {}) -> RPCResponse? {
        var request: [String: JSONValue] = ["jsonrpc": .string("2.0"), "method": .string(method)]
        if let id { request["id"] = .number(Double(id)) }
        if let params { request["params"] = params }
        let line = JSONValue.object(request).compactText
        guard let reply = wait({ await api.handle(line, onSubscribe: onSubscribe) ?? "" }), !reply.isEmpty else { return nil }
        return try? ControlCoding.decoder.decode(RPCResponse.self, from: Data(reply.utf8))
    }
}


@MainActor func runControlAPITests() {
    typealias Kit = ControlTestKit
    let app = Kit.FakeApp()
    let logs = Kit.Box<[String]>([])
    let api = ControlAPI(controller: app, log: { logs.value.append($0) })
    func kind(_ response: RPCResponse?) -> String? { response?.error.map { ControlError(rpc: $0).kind.rawValue } }

    let status = Kit.call(api, "status.get")?.result.flatMap { try? $0.decode(StatusInfo.self) }
    check(status?.protocolVersion == ControlProtocol.version && status?.activeAccountEmail == "alice@work.com" && status?.accountCount == 3,
          "api: status names the active account and the protocol version")

    let list = Kit.call(api, "accounts.list")?.result?["accounts"].flatMap { try? $0.decode([AccountInfo].self) } ?? []
    check(list.map(\.position) == [1, 2, 3] && list.map(\.email) == ["alice@work.com", "bob@home.com", "carol@work.com"],
          "api: accounts come in priority order with their positions")
    check(list.first?.usage?.session?.utilization == 42 && list.first?.usage?.fable?.utilization == 10 && list.first?.usage?.weekly?.resetsAt == "2026-09-30T09:00:00+00:00",
          "api: an account's session, weekly and Fable usage are reported")
    check(list[1].threshold == 70 && list[1].effectiveThreshold == 70 && list[0].threshold == nil && list[0].effectiveThreshold == 90,
          "api: own and effective thresholds are both reported")
    check(list[2].usage?.error == "Token expired. Switch to refresh." && list[2].usage?.session == nil,
          "api: an account with no reading but an error still reports the error")

    let switched = Kit.call(api, "accounts.switch", .object(["account": .string("bob@home.com")]))
    check(switched?.result?["account"]?["email"] == .string("bob@home.com") && switched?.result?["account"]?["isActive"] == .bool(true)
          && app.calls.last == "switch 22222222-2222-2222-2222-222222222222", "api: switch resolves the name and switches")
    app.nextError = ControlError(.busy, "A switch or sign-in is in progress.")
    check(kind(Kit.call(api, "accounts.switch", .object(["account": .string("1")]))) == "busy", "api: a busy app answers busy")
    check(kind(Kit.call(api, "accounts.switch", .object(["account": .string("work")]))) == "ambiguous", "api: an ambiguous name is refused before anything runs")
    check(kind(Kit.call(api, "accounts.switch", .object(["acount": .string("1")]))) == "invalidParams", "api: a misspelt parameter is invalidParams")

    check(Kit.call(api, "accounts.addCurrent")?.result?["account"]?["email"] == .string("bob@home.com"), "api: add-current returns the saved account")

    _ = Kit.call(api, "accounts.signIn.start", .object(["open": .string(" none ")]))
    check(app.calls.last == "signIn newAccount open=nil", "api: a new sign-in with open \"none\" opens nothing", app.calls.last ?? "")
    _ = Kit.call(api, "accounts.signIn.start", .object(["account": .string("2"), "open": .string("Safari")]))
    check(app.calls.last == "signIn reauthenticate(accountId: 22222222-2222-2222-2222-222222222222, email: \"bob@home.com\") open=Safari",
          "api: re-signing names the account and passes the browser", app.calls.last ?? "")
    check(Kit.call(api, "accounts.signIn.status")?.result?["signIn"]?["state"] == .string("starting"), "api: sign-in status returns the sign-in")
    app.signIn = nil
    check(Kit.call(api, "accounts.signIn.status")?.result?["signIn"] == .null, "api: with no sign-in the status is null")
    _ = Kit.call(api, "accounts.signIn.start")
    _ = Kit.call(api, "accounts.signIn.submitCode", .object(["code": .string("abc#def")]))
    check(app.calls.last == "code abc#def", "api: a code is handed to the sign-in")
    _ = Kit.call(api, "accounts.signIn.cancel")
    check(app.calls.last == "cancel", "api: cancel reaches the sign-in")

    let labelled = Kit.call(api, "accounts.setLabel", .object(["account": .string("carol@work.com"), "label": .string("  Side  ")]))
    check(labelled?.result?["account"]?["label"] == .string("Side"), "api: a label is trimmed")
    _ = Kit.call(api, "accounts.setLabel", .object(["account": .string("carol@work.com"), "label": .string("   ")]))
    check(app.calls.last == "label nil", "api: a blank label clears it")
    check(kind(Kit.call(api, "accounts.setLabel", .object(["account": .string("1"), "label": .string(String(repeating: "x", count: 101))]))) == "invalidValue",
          "api: a label over 100 characters is refused")

    let threshold = Kit.call(api, "accounts.setThreshold", .object(["account": .string("1"), "threshold": .number(72.6)]))
    check(threshold?.result?["account"]?["threshold"] == .number(73) && app.calls.last == "threshold 73.0", "api: a threshold is rounded to a whole number")
    _ = Kit.call(api, "accounts.setThreshold", .object(["account": .string("1"), "threshold": .null]))
    check(app.calls.last == "threshold nil", "api: a null threshold goes back to the default")
    func setThreshold(_ account: String, _ value: JSONValue) -> RPCResponse? {
        Kit.call(api, "accounts.setThreshold", .object(["account": .string(account), "threshold": value]))
    }
    let manual = setThreshold("1", .number(0))?.result?["account"]
    check(manual?["threshold"] == .number(0) && manual?["manualOnly"] == .bool(true) && manual?["effectiveThreshold"] == .number(90)
          && app.calls.last == "threshold 0.0", "api: a threshold of 0 makes the account manual only", manual?.compactText ?? "nil")
    let one = setThreshold("1", .number(1))?.result?["account"]
    let low = setThreshold("1", .number(35))?.result?["account"]
    check(one?["threshold"] == .number(1) && low?["threshold"] == .number(35) && low?["effectiveThreshold"] == .number(35)
          && one?["manualOnly"] == .bool(false) && low?["manualOnly"] == .bool(false), "api: thresholds below 50 are accepted")
    let point4 = setThreshold("1", .number(0.4))?.result?["account"]
    let call4 = app.calls.last
    _ = setThreshold("1", .number(0.6))
    check(call4 == "threshold 0.0" && point4?["manualOnly"] == .bool(true) && app.calls.last == "threshold 1.0",
          "api: 0.4 rounds to manual only and 0.6 to 1")
    let under = setThreshold("1", .number(-1))
    check(kind(under) == "invalidValue" && kind(setThreshold("1", .number(101))) == "invalidValue"
          && under?.error?.message == "A threshold must be 1–100, 0 for manual only, or null to follow the default.",
          "api: a threshold outside 0–100 is refused", under?.error?.message ?? "nil")
    _ = setThreshold("1", .null)
    let before = Kit.call(api, "accounts.list")?.result?["accounts"]?.arrayValue ?? []
    _ = setThreshold("bob@home.com", .number(0))
    let after = Kit.call(api, "accounts.list")?.result?["accounts"]?.arrayValue ?? []
    let bob = after.first { $0["email"] == .string("bob@home.com") }
    check(before.count == 3 && before.allSatisfy { $0["manualOnly"] == .bool(false) }
          && bob?["manualOnly"] == .bool(true) && bob?["effectiveThreshold"] == .number(90),
          "api: accounts.list reports manualOnly")
    _ = setThreshold("bob@home.com", .number(70))

    let order = Kit.call(api, "accounts.setOrder", .object(["accounts": .array([.string("3"), .string("bob@home.com"), .string("11111111-1111-1111-1111-111111111111")])]))
    check(order?.result?["accounts"]?.arrayValue?.compactMap { $0["email"]?.stringValue } == ["carol@work.com", "bob@home.com", "alice@work.com"],
          "api: set-order takes every account once and returns the new order")
    check(kind(Kit.call(api, "accounts.setOrder", .object(["accounts": .array([.string("1"), .string("2")])]))) == "invalidValue",
          "api: an order that leaves an account out is refused")
    check(kind(Kit.call(api, "accounts.setOrder", .object(["accounts": .array([.string("1"), .string("1"), .string("2")])]))) == "invalidValue",
          "api: an order that repeats an account is refused")
    app.orderResult = false
    check(kind(Kit.call(api, "accounts.setOrder", .object(["accounts": .array([.string("1"), .string("2"), .string("3")])]))) == "invalidValue",
          "api: an order the app refuses (the list changed meanwhile) is reported")
    app.orderResult = true

    let usageAll = Kit.call(api, "usage.get")?.result.flatMap { try? $0.decode(UsageReport.self) }
    check(usageAll?.accounts.count == 3 && usageAll?.machine.todayCost == 12.5, "api: usage covers every account and this Mac's cost")
    let usageOne = Kit.call(api, "usage.get", .object(["account": .string("alice@work.com")]))?.result.flatMap { try? $0.decode(UsageReport.self) }
    check(usageOne?.accounts.map(\.email) == ["alice@work.com"], "api: usage can be asked for one account")
    _ = Kit.call(api, "usage.refresh")
    check(app.calls.last == "refresh", "api: refresh refreshes")

    let allSettings = Kit.call(api, "settings.get")?.result?["settings"]?.objectValue
    check(allSettings?.count == SettingKey.allCases.count, "api: settings.get with no key returns every setting")
    check(Kit.call(api, "settings.get", .object(["key": .string("autoSwitch.strategy")]))?.result?["settings"]?.objectValue?.keys.sorted() == ["autoSwitch.strategy"],
          "api: settings.get with a key returns that one")
    check(kind(Kit.call(api, "settings.get", .object(["key": .string("volume")]))) == "notFound", "api: an unknown setting is notFound")
    let set = Kit.call(api, "settings.set", .object(["key": .string("autoSwitch.enabled"), "value": .string("off")]))
    check(app.calls.last == "set autoSwitch.enabled=false" && set?.result?["settings"]?["autoSwitch.enabled"] == .bool(false),
          "api: a setting is normalized before the app sees it, and the new value comes back")
    check(kind(Kit.call(api, "settings.set", .object(["key": .string("refreshInterval"), "value": .number(7)]))) == "invalidValue",
          "api: an invalid setting value is refused")

    let subscribed = Kit.Box(false)
    let sub = Kit.call(api, "events.subscribe", onSubscribe: { subscribed.value = true })
    check(sub?.result?["subscribed"] == .bool(true) && subscribed.value, "api: events.subscribe marks the connection")
    _ = Kit.call(api, "app.checkForUpdates")
    _ = Kit.call(api, "app.quit")
    check(Array(app.calls.suffix(2)) == ["updates", "quit"], "api: update check and quit reach the app")

    let removed = Kit.call(api, "accounts.remove", .object(["account": .string("carol@work.com")]))
    check(removed?.result?["removed"]?["email"] == .string("carol@work.com") && !app.accounts.contains { $0.email == "carol@work.com" },
          "api: remove returns the account that was removed")

    check(kind(Kit.call(api, "accounts.teleport")) == "methodNotFound", "api: an unknown method is methodNotFound")
    check(Kit.call(api, "status.get", id: nil) == nil, "api: a notification gets no reply")
    let garbage = Kit.wait({ await api.handle("{not json") ?? "" })
    check(garbage?.contains("-32700") == true, "api: an unreadable line gets a parse error")
    let wrongVersion = Kit.wait({ await api.handle(#"{"jsonrpc":"1.0","id":1,"method":"status.get"}"#) ?? "" })
    check(wrongVersion?.contains("-32600") == true, "api: a request that is not JSON-RPC 2.0 is refused")

    let logged = logs.value.joined(separator: "\n")
    check(!logged.contains("@") && logged.contains("[control] accounts.switch 22222222-2222-2222-2222-222222222222 ok")
          && logged.contains("[control] settings.set autoSwitch.enabled ok") && logged.contains("busy"),
          "api: the log names methods, account ids, setting keys and outcomes, never an email address", logged)
}

// MARK: - No token ever crosses the socket

@MainActor func runControlNoTokenTests() {
    let app = ControlTestKit.FakeApp()
    app.signIn = SignInInfo(id: "S", purpose: "newAccount", accountId: nil, email: nil, state: "waitingForUser", message: nil,
                            resultAccountId: nil, automaticLink: "https://claude.com/cai/oauth/authorize?state=X",
                            manualLink: "https://claude.com/cai/oauth/authorize?state=Y", notice: nil, codeSubmitted: false,
                            startedAt: Date())
    let api = ControlAPI(controller: app)
    let requests: [(String, JSONValue?)] = [
        ("status.get", nil), ("accounts.list", nil), ("accounts.signIn.status", nil), ("usage.get", nil),
        ("settings.get", nil), ("accounts.switch", .object(["account": .string("2")])), ("accounts.addCurrent", nil)
    ]
    let forbiddenKey = try! NSRegularExpression(pattern: "token|credential|secret|refresh|password", options: .caseInsensitive)
    var offenders: [String] = []
    func scan(_ value: JSONValue, _ path: String) {
        switch value {
        case .object(let object):
            for (key, child) in object {
                if forbiddenKey.firstMatch(in: key, range: NSRange(key.startIndex..., in: key)) != nil, !["lastRefresh", "refreshInterval"].contains(key) { offenders.append(path + "." + key) }
                scan(child, path + "." + key)
            }
        case .array(let items): items.forEach { scan($0, path + "[]") }
        case .string(let text): if text.contains("sk-ant-") { offenders.append(path) }
        default: break
        }
    }
    for (method, params) in requests {
        guard let result = ControlTestKit.call(api, method, params)?.result else { offenders.append(method + " (no result)"); continue }
        scan(result, method)
    }
    check(offenders.isEmpty, "no token: no result of any method carries a token, credential, secret or refresh field", offenders.joined(separator: ", "))
    let settingsResult = ControlTestKit.call(api, "settings.get")?.result?.compactText ?? ""
    check(!settingsResult.contains("sk-ant-"), "no token: settings carry nothing that looks like a key")
}

// MARK: - Socket server and client, end to end

@MainActor func runControlSocketTests() {
    let dir = ControlTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let path = dir.appendingPathComponent("ctl/control.sock").path
    let app = ControlTestKit.FakeApp()
    let api = ControlAPI(controller: app)
    let server = ControlServer(path: path, handler: { line, connection in
        await api.handle(line, onSubscribe: { connection.markSubscribed() })
    })
    do {
        try server.start()
    } catch {
        check(false, "socket: the server starts", "\(error)")
        return
    }
    defer { server.stop() }
    check(server.isListening, "socket: the server listens")
    check(ControlTestKit.permissions(path) == 0o600, "socket: the socket is private to this user (0600)", String(ControlTestKit.permissions(path), radix: 8))
    check(ControlTestKit.permissions((path as NSString).deletingLastPathComponent) == 0o700, "socket: its folder is private (0700)")

    let status = ControlTestKit.offMain { () throws -> String in
        let client = ControlClient(path: path)
        try client.connect(launch: false)
        return try client.call(.statusGet).decode(StatusInfo.self).activeAccountEmail ?? "nil"
    }
    check((try? status?.get()) == "alice@work.com", "socket: a client call goes through the real socket and back")

    let busy = ControlTestKit.offMain { () throws -> String in
        let client = ControlClient(path: path)
        try client.connect(launch: false)
        _ = try client.call(.accountsSwitch, .object(["account": .string("nobody")]))
        return "no error"
    }
    if case .failure(let error as ControlError)? = busy {
        check(error.kind == .notFound && error.candidates.count == 3, "socket: an error reply reaches the client as a ControlError")
    } else {
        check(false, "socket: an error reply reaches the client as a ControlError", "\(String(describing: busy))")
    }

    let line = RPCNotification(method: ControlEvent.notificationMethod,
                               params: try? JSONValue.encode(ControlEvent(type: "usageUpdated", at: Date(), data: nil))).line
    let waiter = ControlTestKit.Box<String?>(nil)
    DispatchQueue.global().async {
        let client = ControlClient(path: path)
        var type = "none"
        if (try? client.connect(launch: false)) != nil, (try? client.call(.eventsSubscribe)) != nil {
            type = (try? client.nextEvent(until: Date().addingTimeInterval(5)))??.params?["type"]?.stringValue ?? "none"
        }
        DispatchQueue.main.async { MainActor.assumeIsolated { waiter.value = type } }
    }
    let before = server.subscribers().count
    ControlTestKit.spin(until: { server.subscribers().count > before }, timeout: 5)
    server.subscribers().forEach { $0.send(line) }
    ControlTestKit.spin(until: { waiter.value != nil }, timeout: 5)
    check(waiter.value == "usageUpdated", "socket: a pushed event reaches a subscribed client", waiter.value ?? "nil")

    let second = ControlServer(path: path, handler: { _, _ in nil })
    do {
        try second.start()
        check(false, "socket: a second copy does not take over a live socket")
        second.stop()
    } catch let error as ControlServer.StartError {
        check(error == .alreadyRunning, "socket: a second copy does not take over a live socket", "\(error)")
    } catch {
        check(false, "socket: a second copy does not take over a live socket", "\(error)")
    }

    let flood = ControlTestKit.offMain { () throws -> Bool in
        let client = ControlClient(path: path)
        try client.connect(launch: false)
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = ControlServer.address(path)
        _ = withUnsafePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
        let chunk = [UInt8](repeating: 0x61, count: 64 * 1024)
        var total = 0
        while total < ControlProtocol.maxLineBytes + 256 * 1024 {
            let written = chunk.withUnsafeBytes { write(fd, $0.baseAddress!, $0.count) }
            if written <= 0 { break }
            total += written
        }
        close(fd)
        // The server is still serving other clients.
        return (try? client.call(.statusGet)) != nil
    }
    check((try? flood?.get()) == true, "socket: a client that never sends a newline is cut off and others keep working")

    server.stop()
    check(!FileManager.default.fileExists(atPath: path), "socket: stopping removes the socket file")

    // A socket file left by a crash (nothing listening) is replaced.
    let stale = socket(AF_UNIX, SOCK_STREAM, 0)
    var address = ControlServer.address(path)
    _ = withUnsafePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(stale, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
    close(stale)
    check(FileManager.default.fileExists(atPath: path) && !ControlServer.canConnect(to: path), "socket: a dead socket file is left behind (the crash case)")
    let restarted = ControlServer(path: path, handler: { _, _ in nil })
    check((try? restarted.start()) != nil && restarted.isListening, "socket: a dead socket file is replaced at start")
    restarted.stop()

    let long = ControlServer(path: "/tmp/" + String(repeating: "x", count: 120), handler: { _, _ in nil })
    do {
        try long.start()
        check(false, "socket: a path too long for macOS is refused clearly")
    } catch let error as ControlServer.StartError {
        if case .pathTooLong = error { check(true, "socket: a path too long for macOS is refused clearly") }
        else { check(false, "socket: a path too long for macOS is refused clearly", "\(error)") }
    } catch {
        check(false, "socket: a path too long for macOS is refused clearly", "\(error)")
    }

    let unreachable = ControlTestKit.offMain { () throws -> String in
        try ControlClient(path: path).connect(launch: false)
        return "connected"
    }
    if case .failure(let error as ControlClient.ClientError)? = unreachable, case .unreachable = error {
        check(true, "socket: with the app not running, the client says it is unreachable")
    } else {
        check(false, "socket: with the app not running, the client says it is unreachable", "\(String(describing: unreachable))")
    }

    // A subscriber that stops reading (a frozen SSH session running `watch`)
    // is dropped at its first stalled write. Left open, every event would
    // stall the main thread and leave half a line in its stream.
    let quickPath = dir.appendingPathComponent("q/control.sock").path
    let quick = ControlServer(path: quickPath, handler: { line, connection in
        await api.handle(line, onSubscribe: { connection.markSubscribed() })
    }, writeTimeoutMilliseconds: 100)
    guard (try? quick.start()) != nil else { check(false, "socket: the second server starts"); return }
    defer { quick.stop() }
    let stuck = socket(AF_UNIX, SOCK_STREAM, 0)
    defer { close(stuck) }
    var small: Int32 = 1024
    setsockopt(stuck, SOL_SOCKET, SO_RCVBUF, &small, socklen_t(MemoryLayout<Int32>.size))
    var quickAddress = ControlServer.address(quickPath)
    _ = withUnsafePointer(to: &quickAddress) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(stuck, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
    let subscribe = Array((#"{"jsonrpc":"2.0","id":1,"method":"events.subscribe"}"# + "\n").utf8)
    _ = subscribe.withUnsafeBytes { write(stuck, $0.baseAddress!, $0.count) }
    ControlTestKit.spin(until: { !quick.subscribers().isEmpty }, timeout: 5)
    if let watcher = quick.subscribers().first {
        let event = String(repeating: "x", count: 32 * 1024)
        var attempts = 0
        while watcher.isSubscribed, attempts < 40 { watcher.send(event); attempts += 1 }
        check(!watcher.isSubscribed && quick.subscribers().isEmpty && !watcher.send("{}"),
              "socket: a subscriber that stops reading is dropped at its first stalled write", "attempts \(attempts)")
    } else {
        check(false, "socket: a subscriber that stops reading is dropped at its first stalled write", "never subscribed")
    }
}

// MARK: - Command-line parser

@MainActor func runControlCLIParserTests() {
    func parsed(_ line: String) -> CLICommand? { try? CLIParser.parse(line.split(separator: " ").map(String.init)).command }
    func refused(_ line: String) -> String? {
        do { _ = try CLIParser.parse(line.split(separator: " ").map(String.init)); return nil } catch let e as CLIUsageError { return e.message } catch { return "?" }
    }
    check((try? CLIParser.parse([]))?.command == .help, "cli: no arguments shows help")
    check(parsed("status") == .status && parsed("accounts") == .listAccounts && parsed("accounts list") == .listAccounts, "cli: status and the account list")
    check(parsed("switch 2") == .switchAccount("2") && refused("switch") != nil, "cli: switch needs exactly one account")
    check(parsed("accounts add-current") == .addCurrent, "cli: add-current")
    check(parsed("accounts sign-in") == .signIn(account: nil, open: .none, wait: false), "cli: a plain sign-in opens nothing")
    check(parsed("accounts sign-in --open --wait") == .signIn(account: nil, open: .defaultBrowser, wait: true), "cli: sign-in can open the default browser and wait")
    check(parsed("accounts sign-in --open-in Firefox") == .signIn(account: nil, open: .browser("Firefox"), wait: false), "cli: sign-in can open a named browser")
    check(refused("accounts sign-in --open-in") != nil, "cli: --open-in needs a browser")
    check(parsed("accounts reauth bob@home.com --open-in Safari") == .signIn(account: "bob@home.com", open: .browser("Safari"), wait: false), "cli: reauth names the account")
    check(parsed("accounts sign-in code abc#def") == .signInCode("abc#def") && parsed("accounts sign-in cancel") == .signInCancel
          && parsed("accounts sign-in status") == .signInStatus, "cli: sign-in code, cancel and status")
    check(parsed("accounts remove 2 --yes") == .remove("2") && refused("accounts remove 2")?.contains("--yes") == true,
          "cli: remove refuses without --yes, and says so")
    check(parsed("accounts label 2 Side project") == .label("2", "Side project") && parsed("accounts label 2 --clear") == .label("2", nil),
          "cli: a label can have spaces, and --clear clears it")
    check(parsed("accounts threshold 2 75%") == .threshold("2", 75) && parsed("accounts threshold 2 default") == .threshold("2", nil)
          && refused("accounts threshold 2 high") != nil, "cli: a threshold is a number or default")
    check(parsed("accounts threshold 2 manual") == .threshold("2", 0) && parsed("accounts threshold 2 MANUAL") == .threshold("2", 0)
          && parsed("accounts threshold 2 0") == .threshold("2", 0) && parsed("accounts threshold 2 0%") == .threshold("2", 0)
          && parsed("accounts threshold 2 35") == .threshold("2", 35)
          && ["off", "never", "high"].allSatisfy { refused("accounts threshold 2 \($0)")?.contains("manual") == true },
          "cli: a threshold is 1–100, default or manual")
    check(parsed("accounts order 1 4 2") == .order(["1", "4", "2"]) && refused("accounts order") != nil, "cli: order lists the accounts")
    check(parsed("usage") == .usage(nil) && parsed("usage 2") == .usage("2"), "cli: usage for all or one")
    check(parsed("settings") == .settingsGet(nil) && parsed("settings autoSwitch.enabled") == .settingsGet("autoSwitch.enabled")
          && parsed("settings set menuBar.modules account weeklyBar") == .settingsSet("menuBar.modules", "account weeklyBar"),
          "cli: settings get and set")
    check(parsed("refresh") == .refresh && parsed("watch") == .watch && parsed("update-check") == .updateCheck && parsed("quit") == .quit && parsed("mcp") == .mcp,
          "cli: refresh, watch, update-check, quit and mcp")
    let invocation = try? CLIParser.parse(["--json", "usage", "--timeout", "90"])
    check(invocation == CLIInvocation(command: .usage(nil), json: true, timeout: 90), "cli: --json and --timeout work anywhere")
    check(refused("usage --timeout soon") != nil && refused("teleport") != nil, "cli: a bad timeout and an unknown command are refused")
    check((try? CLIParser.parse(["status", "--help"]))?.command == .help, "cli: --help anywhere shows help")
    check(CLIRunner.value(from: "true") == .bool(true) && CLIRunner.value(from: "90") == .number(90)
          && CLIRunner.value(from: #"["account","weeklyBar"]"#) == .array([.string("account"), .string("weeklyBar")])
          && CLIRunner.value(from: "resetsSoonest") == .string("resetsSoonest"),
          "cli: a setting value is read as JSON when it is JSON, otherwise as text")
}

// MARK: - Command-line output

@MainActor func runControlCLIOutputTests() {
    // Over SSH, or into a file, stdout is not a terminal. A line printed
    // through stdio waits there in a 4 KB buffer, so `ssh … pixelswitch watch`
    // showed nothing for dozens of events and lost them when stopped. Every
    // line must reach the pipe as it is written.
    var pipeFDs: [Int32] = [0, 0]
    if pipe(&pipeFDs) == 0 {
        fflush(stdout)
        let savedStdout = dup(STDOUT_FILENO)
        dup2(pipeFDs[1], STDOUT_FILENO)
        CLIRunner(client: ControlClient(path: "/nonexistent")).out("probe-line")
        dup2(savedStdout, STDOUT_FILENO)
        close(savedStdout)
        close(pipeFDs[1])
        _ = fcntl(pipeFDs[0], F_SETFL, fcntl(pipeFDs[0], F_GETFL) | O_NONBLOCK)
        var bytes = [UInt8](repeating: 0, count: 256)
        let count = read(pipeFDs[0], &bytes, bytes.count)
        close(pipeFDs[0])
        let arrived = count > 0 ? String(decoding: bytes[0..<count], as: UTF8.self) : ""
        check(arrived == "probe-line\n", "cli: each line reaches a pipe at once (watch over SSH is not held in a buffer)", "got \(arrived.debugDescription)")
    } else {
        check(false, "cli: each line reaches a pipe at once (watch over SSH is not held in a buffer)", "pipe() failed")
    }

    let usage = UsageInfo(session: WindowInfo(utilization: 42.4, resetsAt: nil), weekly: WindowInfo(utilization: 61, resetsAt: nil),
                          fable: nil, extraUsage: nil, sampledAt: nil, error: nil)
    let accounts = [
        AccountInfo(id: "1", email: "alice@work.com", label: "Work", subscription: "Max", isActive: true, isSwitchable: true, position: 1,
                    threshold: nil, effectiveThreshold: 90, manualOnly: false, usage: usage),
        AccountInfo(id: "2", email: "bob@home.com", label: nil, subscription: nil, isActive: false, isSwitchable: false, position: 2,
                    threshold: 70, effectiveThreshold: 70, manualOnly: false, usage: nil)
    ]
    let table = CLIOutput.accounts(accounts)
    let lines = table.split(separator: "\n").map(String.init)
    check(lines.count == 4 && lines[1].hasPrefix("*  1  alice@work.com (Work)") && lines[1].contains("42%") && lines[1].contains("90% (default)"),
          "cli output: the active account is starred, with its usage and default threshold", table)
    check(lines[2].hasPrefix("!  2  bob@home.com") && lines[2].contains("70%") && !lines[2].contains("default"),
          "cli output: an account that cannot be switched to is marked, with its own threshold", table)
    check(CLIOutput.accounts([]).contains("add-current"), "cli output: no accounts says how to add one")
    let carol = AccountInfo(id: "3", email: "carol@work.com", label: nil, subscription: nil, isActive: false, isSwitchable: true, position: 3,
                            threshold: 0, effectiveThreshold: 90, manualOnly: true, usage: nil)
    let carolActive = AccountInfo(id: "3", email: "carol@work.com", label: nil, subscription: nil, isActive: true, isSwitchable: true, position: 3,
                                  threshold: 0, effectiveThreshold: 90, manualOnly: true, usage: nil)
    let withManual = CLIOutput.accounts(accounts + [carol]).split(separator: "\n").map(String.init)
    check(withManual.contains { $0.contains("carol@work.com") && $0.contains("90% (manual only)") }
          && withManual.contains { $0.hasPrefix("manual only:") },
          "cli output: a Manual only account shows where it is left, and the legend explains it", withManual.joined(separator: "\n"))
    check(CLIOutput.thresholdSet(carol) == "carol@work.com is now manual only: auto-switch never switches to it. You can still switch to it yourself."
          && CLIOutput.thresholdSet(carolActive) == "carol@work.com is now manual only: auto-switch never switches to it. You can still switch to it yourself. You're using it now: auto-switch moves you off it at 90% and won't move you back to it."
          && CLIOutput.thresholdSet(accounts[0]) == "alice@work.com now follows the default threshold (90%)."
          && CLIOutput.thresholdSet(accounts[1]) == "bob@home.com now switches at 70%."
          && CLIOutput.thresholdSet(nil) == "Threshold set.",
          "cli output: setting manual only says so, and warns when it is the active account", CLIOutput.thresholdSet(carolActive))
    check(CLIOutput.switched(carolActive) == "Switched to carol@work.com. It is manual only: auto-switch moves you off it at 90% and won't move you back to it."
          && CLIOutput.switched(accounts[1]) == "Switched to bob@home.com.",
          "cli output: switching to a Manual only account says where it will be left", CLIOutput.switched(carolActive))
    check(CLIOutput.percent(99.6) == "100%" && CLIOutput.percent(nil) == "—", "cli output: percentages are whole numbers")
    let status = StatusInfo(appVersion: "1.2", protocolVersion: 1, activeAccountId: "1", activeAccountEmail: "alice@work.com", accountCount: 2,
                            claudeAvailable: true, lastRefresh: nil,
                            autoSwitch: AutoSwitchInfo(enabled: true, defaultThreshold: 90, onFable: false, strategy: "resetsSoonest", drainEarly: true, drainWithinHours: 12),
                            signIn: nil)
    let text = CLIOutput.status(status)
    check(text.contains("Active:        alice@work.com") && text.contains("on, default 90%, next by resets soonest, Fable ignored, switches early within 12 h"),
          "cli output: status spells out auto-switch", text)
    let signIn = SignInInfo(id: "S", purpose: "reauthenticate", accountId: "2", email: "bob@home.com", state: "waitingForUser", message: nil,
                            resultAccountId: nil, automaticLink: "https://a", manualLink: "https://m", notice: nil, codeSubmitted: false, startedAt: Date())
    let signInText = CLIOutput.signIn(signIn)
    check(signInText.hasPrefix("Re-sign bob@home.com: waiting for someone to sign in in a browser") && signInText.contains("  https://a")
          && signInText.contains("pixelswitch accounts sign-in code <code>"), "cli output: a sign-in shows both links and what to do with the code", signInText)
    check(CLIOutput.settings(.object(["refreshInterval": .number(300), "autoSwitch.enabled": .bool(true)])) == "refreshInterval = 300\nautoSwitch.enabled = true",
          "cli output: settings print in the window's order")
    check(CLIExit.code(for: .busy) == 3 && CLIExit.code(for: .ambiguous) == 4 && CLIExit.code(for: .notFound) == 4
          && CLIExit.code(for: .invalidValue) == 2 && CLIExit.code(for: .failed) == 1, "cli output: exit codes match the help text")
    check(SettingKeyNames.all == SettingKey.allCases.map(\.rawValue), "cli output: the command-line tool's list of keys matches the app's")

    // An old app still running after an update speaks another protocol version.
    let dir = ControlTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let path = dir.appendingPathComponent("old.sock").path
    let oldApp = ControlServer(path: path, handler: { line, _ in
        let id = (try? ControlCoding.decoder.decode(RPCRequest.self, from: Data(line.utf8)))?.id
        let old = StatusInfo(appVersion: "1.1", protocolVersion: 99, activeAccountId: nil, activeAccountEmail: nil, accountCount: 0,
                             claudeAvailable: true, lastRefresh: nil,
                             autoSwitch: AutoSwitchInfo(enabled: false, defaultThreshold: 90, onFable: true, strategy: "mostRoom", drainEarly: true, drainWithinHours: 24),
                             signIn: nil)
        return RPCResponse.success(id, (try? JSONValue.encode(old)) ?? .null).line
    })
    if (try? oldApp.start()) != nil {
        defer { oldApp.stop() }
        let outcome = ControlTestKit.offMain { () throws -> String in
            let client = ControlClient(path: path)
            try client.connect(launch: false)
            try CLIRunner.checkProtocol(client, timeout: 5)
            return "accepted"
        }
        if case .failure(let error as ControlError)? = outcome {
            check(error.kind == .protocolMismatch && error.message.contains("PixelSwitch 1.1 speaks remote-control protocol 99") && error.message.contains("Quit and reopen PixelSwitch"),
                  "cli output: an app speaking another protocol version is refused with what to do", error.message)
        } else {
            check(false, "cli output: an app speaking another protocol version is refused with what to do", "\(String(describing: outcome))")
        }
    } else {
        check(false, "cli output: a stand-in old app starts for the protocol check")
    }
}

// MARK: - MCP

@MainActor func runControlMCPTests() {
    let calls = ControlTestKit.Box<[String]>([])
    let server = MCPServer(serverVersion: "1.2") { method, params in
        MainActor.assumeIsolated { calls.value.append("\(method.rawValue) \(params?.compactText ?? "nil")") }
        if method == .accountsSwitch, params?["account"] == .string("busy") { throw ControlError(.busy, "A switch is in progress.") }
        return .object(["ok": .bool(true)])
    }
    func reply(_ json: String) -> JSONValue? {
        server.handle(json).flatMap { try? ControlCoding.decoder.decode(JSONValue.self, from: Data($0.utf8)) }
    }
    let modernMeta = #""_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}"#

    let initialize = reply(#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"claude","version":"9"}}}"#)
    check(initialize?["result"]?["protocolVersion"] == .string("2025-06-18") && initialize?["result"]?["capabilities"]?["tools"] == .object([:])
          && initialize?["result"]?["serverInfo"]?["name"] == .string("pixelswitch"), "mcp: a legacy client gets the version it asked for")
    let unknownVersion = reply(#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-01-01"}}"#)
    check(unknownVersion?["result"]?["protocolVersion"] == .string("2025-11-25"), "mcp: an unknown legacy version gets the newest legacy one")
    check(server.handle(#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#) == nil, "mcp: notifications get no reply")

    let discover = reply(#"{"jsonrpc":"2.0","id":"d","method":"server/discover","params":{\#(modernMeta)}}"#)
    check(discover?["result"]?["supportedVersions"]?.arrayValue?.first == .string("2026-07-28") && discover?["result"]?["resultType"] == .string("complete")
          && discover?["result"]?["_meta"]?["io.modelcontextprotocol/serverInfo"]?["version"] == .string("1.2") && discover?["id"] == .string("d"),
          "mcp: server/discover lists the supported versions and who the server is")

    let modernList = reply(#"{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{\#(modernMeta)}}"#)
    let tools = modernList?["result"]?["tools"]?.arrayValue ?? []
    check(tools.count == 18 && modernList?["result"]?["resultType"] == .string("complete"), "mcp: a modern tools/list has all 18 tools and a resultType", "\(tools.count)")
    check(tools.allSatisfy { $0["inputSchema"]?["type"] == .string("object") && $0["description"]?.stringValue?.isEmpty == false },
          "mcp: every tool has a description and an object input schema")
    func tool(_ name: String) -> JSONValue? { tools.first { $0["name"] == .string(name) } }
    let thresholdTool = tool("set_account_threshold")
    check(thresholdTool?["inputSchema"]?["properties"]?["threshold"]?["minimum"] == .number(0)
          && thresholdTool?["inputSchema"]?["properties"]?["threshold"]?["maximum"] == .number(100)
          && thresholdTool?["description"]?.stringValue?.contains("manual only") == true
          && tool("list_accounts")?["description"]?.stringValue?.contains("manualOnly") == true
          && tool("switch_account")?["description"]?.stringValue?.contains("manual-only") == true,
          "mcp: set_account_threshold takes 0–100 and says what 0 means")
    let names = Set(tools.compactMap { $0["name"]?.stringValue })
    check(names == Set(["get_status", "list_accounts", "get_usage", "refresh_usage", "switch_account", "add_current_account", "remove_account",
                        "start_sign_in", "get_sign_in_status", "submit_sign_in_code", "cancel_sign_in", "set_account_label", "set_account_threshold",
                        "set_account_order", "get_settings", "set_setting", "check_for_updates", "quit_app"]),
          "mcp: the tools are the ones the design lists")
    let legacyList = reply(#"{"jsonrpc":"2.0","id":3,"method":"tools/list"}"#)
    check(legacyList?["result"]?["resultType"] == nil && legacyList?["result"]?["tools"]?.arrayValue?.count == 18, "mcp: a legacy tools/list has no resultType")

    let unsupported = reply(#"{"jsonrpc":"2.0","id":4,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"1900-01-01","io.modelcontextprotocol/clientCapabilities":{}}}}"#)
    check(unsupported?["error"]?["code"] == .number(-32022) && unsupported?["error"]?["data"]?["requested"] == .string("1900-01-01"),
          "mcp: an unsupported modern version gets UnsupportedProtocolVersion with the supported list")
    let noCaps = reply(#"{"jsonrpc":"2.0","id":5,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}}"#)
    check(noCaps?["error"]?["code"] == .number(-32602), "mcp: a modern request without client capabilities is invalid params")

    let called = reply(#"{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"switch_account","arguments":{"account":"bob@home.com"}}}"#)
    check(calls.value.last == #"accounts.switch {"account":"bob@home.com"}"# && called?["result"]?["isError"] == .bool(false)
          && called?["result"]?["structuredContent"]?["ok"] == .bool(true), "mcp: a tool call runs the matching control method", calls.value.last ?? "")
    let busy = reply(#"{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"switch_account","arguments":{"account":"busy"}}}"#)
    check(busy?["result"]?["isError"] == .bool(true) && busy?["result"]?["content"]?.arrayValue?.first?["text"] == .string("busy: A switch is in progress."),
          "mcp: a refused call is a tool error the model can read")
    let callsBefore = calls.value.count
    let missing = reply(#"{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"switch_account","arguments":{}}}"#)
    check(missing?["result"]?["isError"] == .bool(true) && calls.value.count == callsBefore, "mcp: a missing required argument is refused before calling the app")
    _ = reply(#"{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_status"}}"#)
    check(calls.value.last == "status.get nil", "mcp: a tool with no arguments sends no params")
    let unknownTool = reply(#"{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"teleport"}}"#)
    check(unknownTool?["error"]?["code"] == .number(-32602), "mcp: an unknown tool is invalid params")
    check(reply(#"{"jsonrpc":"2.0","id":11,"method":"ping"}"#)?["result"] == .object([:]), "mcp: ping answers")
    check(reply(#"{"jsonrpc":"2.0","id":12,"method":"resources/list"}"#)?["error"]?["code"] == .number(-32601), "mcp: an unknown method is method-not-found")
    check(reply("garbage")?["error"]?["code"] == .number(-32700), "mcp: garbage is a parse error")
}

// MARK: - Command-line tool installer

@MainActor func runControlInstallerTests() {
    let dir = ControlTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let app = dir.appendingPathComponent("PixelSwitch.app/Contents/Helpers", isDirectory: true)
    try? FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
    let tool = app.appendingPathComponent("pixelswitch").path
    FileManager.default.createFile(atPath: tool, contents: Data("#!/bin/sh\n".utf8), attributes: [.posixPermissions: 0o755])
    let link = dir.appendingPathComponent("home/.local/bin/pixelswitch").path

    check(CommandLineToolInstaller.status(linkPath: link, toolPath: tool) == .notInstalled, "installer: nothing there reads as not installed")
    check((try? CommandLineToolInstaller.install(linkPath: link, toolPath: tool)) == .installed, "installer: install creates ~/.local/bin and the link")
    check((try? FileManager.default.destinationOfSymbolicLink(atPath: link)) == tool, "installer: the link points into the app, so updates keep it current")
    check((try? CommandLineToolInstaller.install(linkPath: link, toolPath: tool)) == .installed, "installer: installing twice is harmless")

    try? FileManager.default.removeItem(atPath: link)
    try? FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: "/Applications/Old/PixelSwitch.app/Contents/Helpers/pixelswitch")
    check(CommandLineToolInstaller.status(linkPath: link, toolPath: tool) == .linkedToOtherCopy("/Applications/Old/PixelSwitch.app/Contents/Helpers/pixelswitch"),
          "installer: a link to another copy of PixelSwitch is recognised")
    check((try? CommandLineToolInstaller.install(linkPath: link, toolPath: tool)) == .installed, "installer: it is repointed here")

    try? FileManager.default.removeItem(atPath: link)
    try? FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: "/usr/local/bin/something-else")
    check(CommandLineToolInstaller.status(linkPath: link, toolPath: tool) == .occupied, "installer: a link to something else is not PixelSwitch's")
    try? FileManager.default.removeItem(atPath: link)
    FileManager.default.createFile(atPath: link, contents: Data("mine".utf8))
    do {
        try CommandLineToolInstaller.install(linkPath: link, toolPath: tool)
        check(false, "installer: someone else's file is never overwritten")
    } catch let error as CommandLineToolInstaller.InstallError {
        check(error == .occupied(link) && (try? String(contentsOfFile: link, encoding: .utf8)) == "mine", "installer: someone else's file is never overwritten")
    } catch {
        check(false, "installer: someone else's file is never overwritten", "\(error)")
    }
    do {
        try CommandLineToolInstaller.install(linkPath: dir.appendingPathComponent("x/pixelswitch").path, toolPath: dir.appendingPathComponent("missing").path)
        check(false, "installer: a missing tool is reported")
    } catch let error as CommandLineToolInstaller.InstallError {
        if case .toolMissing = error { check(true, "installer: a missing tool is reported") } else { check(false, "installer: a missing tool is reported") }
    } catch {
        check(false, "installer: a missing tool is reported")
    }

    let config = CommandLineToolInstaller.mcpConfiguration(host: "studio.local", linkPath: "/Users/me/.local/bin/pixelswitch")
    let parsed = try? ControlCoding.decoder.decode(JSONValue.self, from: Data(config.utf8))
    check(parsed?["mcpServers"]?["pixelswitch"]?["command"] == .string("ssh")
          && parsed?["mcpServers"]?["pixelswitch"]?["args"] == .array([.string("studio.local"), .string("/Users/me/.local/bin/pixelswitch"), .string("mcp")]),
          "installer: the MCP snippet runs the tool over SSH by its absolute path", config)
    check(CommandLineToolInstaller.sshExample(host: "studio.local", linkPath: "/Users/me/.local/bin/pixelswitch") == "ssh studio.local /Users/me/.local/bin/pixelswitch status",
          "installer: the SSH example is one line")
    check(CommandLineToolInstaller.isPixelSwitchTool("/Applications/PixelSwitch.app/Contents/Helpers/pixelswitch") && !CommandLineToolInstaller.isPixelSwitchTool("/usr/bin/pixelswitch"),
          "installer: only a tool inside an app counts as PixelSwitch's")
}
