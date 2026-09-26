// Remote control (Part C): the protocol, the account resolver, the settings
// catalog, the control API against a fake app, the no-token rule, the
// command-line parser and output, the MCP server, the socket server and client
// end to end, and the command-line tool installer. Called from main.swift.
import Foundation

@MainActor func runControlTests() {
    runControlProtocolTests()
    runControlResolverTests()
    runControlSettingsCatalogTests()
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
          && norm(.autoSwitchThreshold, .number(49)) == nil && norm(.autoSwitchThreshold, .number(.infinity)) == nil,
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
