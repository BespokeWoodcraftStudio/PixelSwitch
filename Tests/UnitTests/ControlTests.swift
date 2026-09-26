// Remote control (Part C): the protocol, the account resolver, the settings
// catalog, the control API against a fake app, the no-token rule, the
// command-line parser and output, the MCP server, the socket server and client
// end to end, and the command-line tool installer. Called from main.swift.
import Foundation

@MainActor func runControlTests() {
    runControlProtocolTests()
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
