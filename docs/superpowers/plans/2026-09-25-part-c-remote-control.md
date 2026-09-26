# Part C: Remote Control (CLI + MCP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the founder asked on 2026-09-25 for a single lane with no subagents on this laptop) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An AI (or a person) on another Mac can do everything the PixelSwitch GUI can do, by running `pixelswitch` on this Mac over SSH or plugging `pixelswitch mcp` into Claude as a tool server: read usage live, switch, add, re-sign, rename, reorder and remove accounts, set per-account thresholds, and change every setting. No login token ever leaves the app, and nothing listens on the network.

**Architecture:** The app runs a `ControlServer` on a Unix-domain socket that only this macOS user can open (`~/Library/Application Support/PixelSwitch/control.sock`, folder 0700, socket 0600, peer uid checked) and speaks newline-delimited JSON-RPC 2.0 defined in `ControlProtocol.swift`, which both the app and the tool compile. Requests go to `ControlAPI` (pure: unit-tested against a fake app), then `AppController`, then the same `AppState` methods the GUI uses; `SettingsStore` owns every setting's side effect so a CLI change behaves exactly like a Settings-window change. The `pixelswitch` tool (a `type: tool` target embedded at `Contents/Helpers/pixelswitch`, signed in CI) connects, checks the protocol version, runs one command, and prints readable text or `--json`; `pixelswitch mcp` serves MCP on stdio in both the modern (2026-07-28) and legacy (`initialize`) eras.

**Tech Stack:** Swift 6, Foundation/Darwin POSIX sockets, Combine, SwiftUI + AppKit, ServiceManagement, SystemConfiguration, XcodeGen 2.46 (`type: tool` target + `copy: destination: wrapper, subpath: Contents/Helpers`), GitHub Actions for build/sign/notarize, the repo's swiftc unit harness.

**Spec:** `docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md` (section 5, and section 9 for the Part A and Part B names this part consumes). Founder's answers: `docs/decisions/2026-09-24-remote-control-answers.md` (answer 1: no network listener, SSH; answer 2: always on, everything allowed, no permission levels; answer 4: shell commands and MCP).

**How this plan was checked before it was handed over:** every code block and edit below was written and run in a scratch copy of the repository with Part A and Part B already applied (`scratchpad/cbase`, 351 tests). After the whole part: `505 passed, 0 failed` (the 154 checks this plan adds), `scripts/typecheck.sh` passed for the app (70 files) and the tool (6 files), all five string files lint with identical keys (250 each), `xcodegen generate` produced a tool target and a Copy Files phase with `dstSubfolderSpec = 1` (wrapper) and `dstPath = Contents/Helpers`, the workflow YAML parses, and the real `pixelswitch` binary (built with swiftc) passed a smoke test: help, version, usage errors (exit 2), `remove` without `--yes` refused, an unreachable app (exit 5), and an MCP session (handshake, 18 tools, a tool call reported as a readable error). The generator that produced this document re-checks that each edit reproduces the tested files, and a second script re-applied every task in order to a fresh copy and ran the suite after each (the per-task totals below are from that run).

## Global Constraints

- Swift 6 language mode (`swiftc -swift-version 6`; `SWIFT_VERSION: "6.0"`). Deployment target macOS 14.0.
- `project.yml` is the only source of truth for the Xcode project; never edit `.pbxproj` or `Info.plist`; run `xcodegen generate`.
- No new package dependencies. Argument parsing and the MCP server are hand-written.
- This Mac has no Xcode. **Never run `xcodebuild`.** Checks: `bash Tests/run-unit-tests.sh` (baseline after Parts A and B: `351 passed, 0 failed`) and `bash scripts/typecheck.sh`. The full build, the embedded tool and signing are checked in CI, in Task 13.
- Only pure files go in the unit harness. `PixelSwitchCLI/PixelSwitchCommand.swift` (it has `@main`) never does: the harness's `main.swift` is top-level code.
- **No TCP listener of any kind** (founder's answer 1). The socket is user-only by file mode and by `getpeereid`.
- **No permission levels, no master switch** (founder's answer 2): anything running as this Mac's user can use every method.
- **No login token crosses the socket.** `ControlAPI` depends only on `AppControlling`; nothing in `PixelSwitch/Control/` calls `KeychainService` or `ClaudeService` credential methods. Task 3's no-token test scans every result.
- Remote commands are logged (`FileLog("Control")`) as method, account id or setting key, and outcome only: never an email address, a link or an argument.
- Uses Part A's `AutoSwitchStrategy`, `AutoSwitchSettings` (and its key names `enabledKey`, `thresholdKey`, `onFableKey`, `strategyKey`, `drainEarlyKey`, `drainWithinHoursKey`, ranges `thresholdRange`, `drainHoursRange`), `AppState.effectiveSwitchThreshold(for:)`, `setSwitchThreshold(_:for:)`, `setAccountOrder(_:)`, `lastAutoSwitch`, `AutoSwitchRecord`; and Part B's `SignInPurpose`, `SignInState`, `SignInSession` (with `notice`, `codeSubmitted`), `BrowserOpener`, `BrowserApp`, `AppState.startSignIn(_:)`, `currentSignIn`, `SignInStartError`.
- Test convention: `Tests/UnitTests/ControlTests.swift` defines `@MainActor func runControlTests()`; `main.swift` calls it after `runSignInTests()`; every harness file is listed in `Tests/run-unit-tests.sh`, before `Tests/UnitTests/ControlTests.swift`.
- Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Branch `part-c-remote-control`, created from `main` after Part B has merged.

## Decisions this plan makes that the spec left open (or changed)

1. **The protocol file is `PixelSwitch/Control/ControlProtocol.swift`, not `Shared/ControlProtocol.swift`** (spec section 9). `Shared/` is also a source folder of the widget target; the widget has no use for it. The tool target lists the file explicitly.
2. **Accounts can also be named by 1-based position** in the priority order, after id, email and label (a label that looks like a number still wins). The founder's own phrasing was "account 1, then account 4, then account 2".
3. **Extra command `pixelswitch accounts sign-in status`**, used by `--wait` and handy on its own.
4. **MCP speaks both eras.** Modern 2026-07-28 (per-request `_meta["io.modelcontextprotocol/protocolVersion"]`, `server/discover`, `resultType: "complete"`, `-32022` for an unsupported version, `-32602` when `clientCapabilities` is missing) and legacy `initialize` (echoes 2025-11-25, 2025-06-18, 2025-03-26 or 2024-11-05, else answers 2025-11-25). A refused action is a tool result with `isError: true` and a readable `kind: message`, so the model can act on it; successful results also carry `structuredContent`.
5. **The CLI checks the protocol with `status.get` before every command** (and MCP once, on first use). A mismatch says to quit and reopen PixelSwitch.
6. **`AppState` gets throwing versions**: `ActionError` (`busy`, `claudeUnavailable`, `noActiveAccount`, `failed`), `performSwitch(to:)` and `performAddCurrentAccount()`. `switchTo(_:)` and `addAccount()` become wrappers that set `errorMessage` exactly as before (busy and "no active account" stay silent, as they always were). `isSwitching` becomes `private(set)` and `isSwitchable(_:)` internal. Two new messages: "No account is active yet, so there is nothing to switch from. Add the current account first." and "A switch or sign-in is in progress. Try again in a moment." (shown only to remote callers).
7. **`SettingsStore` is a main-actor singleton** with a weak `appState`, set at launch. The Settings window's side effects (refresh interval, usage window, language, launch at login) and the Claude CLI tab's binary path now go through it; `@AppStorage` keeps the window in sync when the CLI changes a value.
8. **Two environment variables exist only for testing**: `PIXELSWITCH_CONTROL_SOCKET` points the tool at another socket, `PIXELSWITCH_NO_LAUNCH=1` stops it starting the app.
9. **`app.quit` replies first** and quits 0.3 s later. **Labels** are capped at 100 characters. **Thresholds** set remotely are rounded to whole numbers (the GUI's steppers only reach whole numbers).
10. **The Settings section lives in Settings → Claude CLI** ("Command line & AI"): it is terminal-related, and General is already long.
11. **The tool reads its version from the enclosing app's Info.plist** (shown in MCP's `serverInfo`), so the release script needs no change; the tool target carries the same `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` as the app, which `scripts/release.sh` already rewrites everywhere.
12. **Each connection handles its requests one at a time**, in order; connections do not wait for each other. A line over 1 MiB closes that connection.

## Review Focus

1. **An old app still running after an update** (protocol mismatch). Expected: the tool refuses with "PixelSwitch 1.1 speaks remote-control protocol 99 … Quit and reopen PixelSwitch". Test: Task 5 ("an app speaking another protocol version is refused with what to do").
2. **The app is not running when an SSH command arrives.** Expected: the tool runs `open -g -b ai.pixelventures.pixelswitch` and waits up to 10 s; if the app never answers, exit 5 with the reason. Tests: Task 4 ("with the app not running, the client says it is unreachable"); the launch itself is checked by hand in Task 13.
3. **A client that connects and never sends a newline.** Expected: its connection is cut at 1 MiB and every other client keeps working. Test: Task 4 ("a client that never sends a newline is cut off and others keep working").
4. **Two clients acting at once** (two switches, or a switch during a sign-in). Expected: the second gets `busy` (exit 3), nothing half-done. Tests: Task 3 ("a busy app answers busy"); `AppController.refuseWhileBusy` and `AppState`'s guards (Tasks 8 and 9).
5. **A socket file left behind by a crash, or a second copy of the app.** Expected: a dead socket file is replaced; a live one is never taken over. Tests: Task 4 ("a dead socket file is replaced at start", "a second copy does not take over a live socket").

---

### Task 1: The shared protocol

**Files:**
- Create: `PixelSwitch/Control/ControlProtocol.swift`
- Create: `Tests/UnitTests/ControlTests.swift`
- Modify: `Tests/UnitTests/main.swift` (one call line)
- Modify: `Tests/run-unit-tests.sh` (two file lines)

**Interfaces:**
- Consumes: nothing.
- Produces: `enum ControlProtocol` (`version = 1`, `appBundleIdentifier`, `maxLineBytes`, `socketPath(home:)`, `maxSocketPathBytes`); `enum JSONValue: Codable, Equatable, Sendable` with `subscript(key:)`, `stringValue`, `boolValue`, `doubleValue`, `arrayValue`, `objectValue`, `isNull`, `static func encode(_:)`, `func decode(_:)`, `compactText`, `prettyText`; `enum ControlCoding` (`encoder`, `decoder`, ISO 8601 dates); `enum RPCID`; `struct RPCRequest`, `RPCErrorObject`, `RPCResponse` (`success`, `failure`, `line`), `RPCNotification`; `enum ControlErrorKind` (codes -32700…-32000); `struct ControlError: Error` (`kind`, `message`, `candidates`, `rpc`, `init(rpc:)`); `enum ControlMethod` (19 methods); the parameter structs (`AccountParams`, `SignInStartParams`, `SignInCodeParams`, `LabelParams`, `ThresholdParams`, `OrderParams`, `UsageParams`, `SettingGetParams`, `SettingSetParams`) and result structs (`AutoSwitchInfo`, `SignInInfo`, `StatusInfo`, `WindowInfo`, `ExtraUsageInfo`, `UsageInfo`, `AccountInfo`, `MachineUsageInfo`, `UsageReport`, `ControlEvent`); `enum ControlTestKit` and `@MainActor func runControlTests()`.


- [ ] **Step 0: Create the branch**

```bash
cd /Users/ahamade/Documents/GitHub/PixelSwitch
git checkout main && git pull --ff-only
git checkout -b part-c-remote-control
bash Tests/run-unit-tests.sh | tail -1
```

Expected: `351 passed, 0 failed` (Parts A and B merged).

- [ ] **Step 1: Write the failing test**

Create `Tests/UnitTests/ControlTests.swift` with exactly this content (later tasks add a call line each to `runControlTests()` and append their own sections):

```swift
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
```

In `Tests/UnitTests/main.swift`, replace `runSignInTests()` with:

```swift
runSignInTests()
runControlTests()
```

In `Tests/run-unit-tests.sh`, replace the line `  Tests/UnitTests/SignInTests.swift \` with:

```bash
  Tests/UnitTests/SignInTests.swift \
  PixelSwitch/Control/ControlProtocol.swift \
  Tests/UnitTests/ControlTests.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitch/Control/ControlProtocol.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Control/ControlProtocol.swift`:

```swift
import Foundation

/// The remote-control protocol shared by PixelSwitch.app (the server) and the
/// `pixelswitch` command-line tool (the client): JSON-RPC 2.0, one JSON object
/// per line, over a Unix-domain socket only this Mac's user can open.
///
/// Compiled into the app, the command-line tool and the unit harness, so it
/// is pure Foundation. No type here may ever carry a login token.
enum ControlProtocol {
    /// Bumped whenever a method, parameter or result changes shape. The CLI
    /// refuses to talk to an app with a different number (an old app still
    /// running after an update).
    static let version = 1

    static let appBundleIdentifier = "ai.pixelventures.pixelswitch"

    /// A request or response line longer than this closes the connection.
    static let maxLineBytes = 1 << 20

    /// `~/Library/Application Support/PixelSwitch/control.sock`.
    static func socketPath(home: String = NSHomeDirectory()) -> String {
        home + "/Library/Application Support/PixelSwitch/control.sock"
    }

    /// The longest socket path the kernel accepts (`sockaddr_un.sun_path`,
    /// 104 bytes, including the terminating NUL).
    static let maxSocketPathBytes = 103
}

// MARK: - JSON values

/// Any JSON value. Parameters and results travel as this, and typed structs
/// convert to and from it with `JSONValue.encode(_:)` and `decode(_:)`.
enum JSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([JSONValue].self) { self = .array(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Not a JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    subscript(key: String) -> JSONValue? {
        if case .object(let object) = self { return object[key] }
        return nil
    }

    var stringValue: String? { if case .string(let v) = self { return v }; return nil }
    var boolValue: Bool? { if case .bool(let v) = self { return v }; return nil }
    var doubleValue: Double? { if case .number(let v) = self { return v }; return nil }
    var arrayValue: [JSONValue]? { if case .array(let v) = self { return v }; return nil }
    var objectValue: [String: JSONValue]? { if case .object(let v) = self { return v }; return nil }
    var isNull: Bool { self == .null }

    /// A typed value as JSON (dates as ISO 8601).
    static func encode<T: Encodable>(_ value: T) throws -> JSONValue {
        try ControlCoding.decoder.decode(JSONValue.self, from: ControlCoding.encoder.encode(value))
    }

    /// This JSON as a typed value (dates as ISO 8601).
    func decode<T: Decodable>(_ type: T.Type) throws -> T {
        try ControlCoding.decoder.decode(T.self, from: ControlCoding.encoder.encode(self))
    }

    /// Compact JSON text, keys sorted.
    var compactText: String {
        (try? ControlCoding.encoder.encode(self)).map { String(decoding: $0, as: UTF8.self) } ?? "null"
    }

    /// Indented JSON text, keys sorted, for people.
    var prettyText: String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return (try? encoder.encode(self)).map { String(decoding: $0, as: UTF8.self) } ?? "null"
    }
}

/// The one encoder and decoder every control message goes through.
enum ControlCoding {
    static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

// MARK: - JSON-RPC messages

/// A JSON-RPC id: a number or a string.
enum RPCID: Codable, Hashable, Sendable {
    case int(Int)
    case string(String)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int.self) { self = .int(value) }
        else { self = .string(try container.decode(String.self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .int(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        }
    }
}

struct RPCRequest: Codable, Sendable {
    var jsonrpc = "2.0"
    /// Nil for a notification (no reply expected).
    let id: RPCID?
    let method: String
    let params: JSONValue?
}

struct RPCErrorObject: Codable, Equatable, Sendable {
    let code: Int
    let message: String
    let data: JSONValue?
}

struct RPCResponse: Codable, Sendable {
    var jsonrpc = "2.0"
    let id: RPCID?
    let result: JSONValue?
    let error: RPCErrorObject?

    static func success(_ id: RPCID?, _ result: JSONValue) -> RPCResponse {
        RPCResponse(id: id, result: result, error: nil)
    }

    static func failure(_ id: RPCID?, _ error: ControlError) -> RPCResponse {
        RPCResponse(id: id, result: nil, error: error.rpc)
    }

    /// One line of compact JSON, no trailing newline.
    var line: String {
        (try? ControlCoding.encoder.encode(self)).map { String(decoding: $0, as: UTF8.self) } ?? ""
    }
}

/// A message the app pushes to a subscribed connection (`events.subscribe`).
struct RPCNotification: Codable, Sendable {
    var jsonrpc = "2.0"
    let method: String
    let params: JSONValue?

    var line: String {
        (try? ControlCoding.encoder.encode(self)).map { String(decoding: $0, as: UTF8.self) } ?? ""
    }
}

// MARK: - Errors

/// What went wrong, as the CLI and an AI need to tell it apart.
enum ControlErrorKind: String, Codable, CaseIterable, Sendable {
    case parse, invalidRequest, methodNotFound, invalidParams
    /// A switch or sign-in is in progress; try again shortly.
    case busy
    /// No account (or setting, or sign-in) matches.
    case notFound
    /// More than one account matches; `candidates` lists them.
    case ambiguous
    /// A value is out of range or the wrong shape.
    case invalidValue
    /// The `claude` command could not be found.
    case claudeUnavailable
    /// The app speaks a different protocol version (old app still running).
    case protocolMismatch
    /// Anything else; the message says what.
    case failed

    var code: Int {
        switch self {
        case .parse: return -32700
        case .invalidRequest: return -32600
        case .methodNotFound: return -32601
        case .invalidParams: return -32602
        case .busy: return -32001
        case .notFound: return -32002
        case .ambiguous: return -32003
        case .invalidValue: return -32004
        case .claudeUnavailable: return -32005
        case .protocolMismatch: return -32006
        case .failed: return -32000
        }
    }

    init(code: Int) {
        self = Self.allCases.first { $0.code == code } ?? .failed
    }
}

struct ControlError: Error, Equatable, Sendable {
    let kind: ControlErrorKind
    let message: String
    var candidates: [String] = []

    init(_ kind: ControlErrorKind, _ message: String, candidates: [String] = []) {
        self.kind = kind
        self.message = message
        self.candidates = candidates
    }

    var rpc: RPCErrorObject {
        var data: [String: JSONValue] = ["kind": .string(kind.rawValue)]
        if !candidates.isEmpty { data["candidates"] = .array(candidates.map(JSONValue.string)) }
        return RPCErrorObject(code: kind.code, message: message, data: .object(data))
    }

    init(rpc: RPCErrorObject) {
        let kind = rpc.data?["kind"]?.stringValue.flatMap(ControlErrorKind.init(rawValue:)) ?? ControlErrorKind(code: rpc.code)
        let candidates = rpc.data?["candidates"]?.arrayValue?.compactMap(\.stringValue) ?? []
        self.init(kind, rpc.message, candidates: candidates)
    }
}

// MARK: - Methods

enum ControlMethod: String, CaseIterable, Sendable {
    case statusGet = "status.get"
    case accountsList = "accounts.list"
    case accountsSwitch = "accounts.switch"
    case accountsAddCurrent = "accounts.addCurrent"
    case signInStart = "accounts.signIn.start"
    case signInStatus = "accounts.signIn.status"
    case signInSubmitCode = "accounts.signIn.submitCode"
    case signInCancel = "accounts.signIn.cancel"
    case accountsRemove = "accounts.remove"
    case accountsSetLabel = "accounts.setLabel"
    case accountsSetThreshold = "accounts.setThreshold"
    case accountsSetOrder = "accounts.setOrder"
    case usageGet = "usage.get"
    case usageRefresh = "usage.refresh"
    case settingsGet = "settings.get"
    case settingsSet = "settings.set"
    case eventsSubscribe = "events.subscribe"
    case appCheckForUpdates = "app.checkForUpdates"
    case appQuit = "app.quit"
}

// MARK: - Parameters

/// An account named by id, email, label, or 1-based position in the order.
struct AccountParams: Codable, Equatable, Sendable { let account: String }

struct SignInStartParams: Codable, Equatable, Sendable {
    /// Re-sign this account; absent to add a new one.
    var account: String?
    /// `none` (default), `default` for the default browser, or a browser's name.
    var open: String?
}

struct SignInCodeParams: Codable, Equatable, Sendable { let code: String }

struct LabelParams: Codable, Equatable, Sendable {
    let account: String
    /// Nil or empty clears the label.
    var label: String?
}

struct ThresholdParams: Codable, Equatable, Sendable {
    let account: String
    /// 50–100; nil means the account follows the default threshold.
    var threshold: Double?
}

struct OrderParams: Codable, Equatable, Sendable { let accounts: [String] }

struct UsageParams: Codable, Equatable, Sendable { var account: String? }

struct SettingGetParams: Codable, Equatable, Sendable { var key: String? }

struct SettingSetParams: Codable, Equatable, Sendable {
    let key: String
    let value: JSONValue
}

// MARK: - Results

struct AutoSwitchInfo: Codable, Equatable, Sendable {
    let enabled: Bool
    let defaultThreshold: Double
    let onFable: Bool
    let strategy: String
    let drainEarly: Bool
    let drainWithinHours: Double
}

struct SignInInfo: Codable, Equatable, Sendable {
    let id: String
    /// `newAccount` or `reauthenticate`.
    let purpose: String
    /// The account being re-signed, for `reauthenticate`.
    let accountId: String?
    let email: String?
    /// `starting`, `waitingForUser`, `completing`, `succeeded`, `failed` or `cancelled`.
    let state: String
    /// Why it failed, for `failed`.
    let message: String?
    /// The account saved, for `succeeded`.
    let resultAccountId: String?
    /// Finishes by itself in any browser on the Mac running PixelSwitch.
    let automaticLink: String?
    /// For another device: the page shows a code to hand back with `submitCode`.
    let manualLink: String?
    let notice: String?
    let codeSubmitted: Bool
    let startedAt: Date
}

struct StatusInfo: Codable, Equatable, Sendable {
    let appVersion: String
    let protocolVersion: Int
    let activeAccountId: String?
    let activeAccountEmail: String?
    let accountCount: Int
    let claudeAvailable: Bool
    let lastRefresh: Date?
    let autoSwitch: AutoSwitchInfo
    let signIn: SignInInfo?
}

struct WindowInfo: Codable, Equatable, Sendable {
    /// Percent used, 0–100.
    let utilization: Double?
    /// ISO 8601, as the usage API sent it.
    let resetsAt: String?
}

struct ExtraUsageInfo: Codable, Equatable, Sendable {
    let enabled: Bool?
    let monthlyLimit: Double?
    let usedCredits: Double?
    let utilization: Double?
}

struct UsageInfo: Codable, Equatable, Sendable {
    let session: WindowInfo?
    let weekly: WindowInfo?
    let fable: WindowInfo?
    let extraUsage: ExtraUsageInfo?
    /// When this reading was taken. Non-active accounts are polled in turn, so
    /// a reading can be several refreshes old.
    let sampledAt: Date?
    let error: String?
}

struct AccountInfo: Codable, Equatable, Sendable {
    let id: String
    let email: String
    let label: String?
    let subscription: String?
    let isActive: Bool
    let isSwitchable: Bool
    /// 1-based position in the priority order.
    let position: Int
    /// This account's own threshold; nil when it follows the default.
    let threshold: Double?
    let effectiveThreshold: Double
    let usage: UsageInfo?
}

/// Cost and activity for this Mac as a whole (all accounts together).
struct MachineUsageInfo: Codable, Equatable, Sendable {
    let todayCost: Double
    let totalCost: Double
    let conversationTurns: Int
    let activeCodingMinutes: Int
    let linesWritten: Int
    let modelUsage: [String: Int]
}

struct UsageReport: Codable, Equatable, Sendable {
    let accounts: [AccountInfo]
    let machine: MachineUsageInfo
    let lastRefresh: Date?
}

/// One pushed event. `type` is `activeAccountChanged`, `usageUpdated`,
/// `autoSwitched`, `signInChanged` or `error`.
struct ControlEvent: Codable, Equatable, Sendable {
    let type: String
    let at: Date
    let data: JSONValue?

    static let notificationMethod = "event"
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `363 passed, 0 failed` (+12).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Control/ControlProtocol.swift Tests/UnitTests/ControlTests.swift Tests/UnitTests/main.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: the shared JSON-RPC protocol

Newline-delimited JSON-RPC 2.0 over a user-only Unix socket, compiled into the
app and the tool. Typed errors, the 19 methods, parameters and results; no type
carries a token.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Naming accounts, and every setting's allowed values

**Files:**
- Create: `PixelSwitch/Control/AccountResolver.swift`
- Create: `PixelSwitch/Control/SettingsCatalog.swift`
- Modify: `Tests/UnitTests/ControlTests.swift` (two call lines, two sections)
- Modify: `Tests/run-unit-tests.sh` (three file lines, including the existing `PixelSwitch/Models/MenuBarModule.swift`)

**Interfaces:**
- Consumes: `Account` (in the harness), `AutoSwitchStrategy`, `AutoSwitchSettings` (Part A), `MenuBarModule` (`PixelSwitch/Models/MenuBarModule.swift`, pure, added to the harness here).
- Produces: `enum AccountResolver` (`resolve(_:in:) throws -> Account`, `resolveAll(_:in:)`, `describe(_:)`); `enum SettingKey: String, CaseIterable` (21 keys, `kind`, `summary`, `normalize(_:) throws -> JSONValue`, `static func named(_:) throws -> SettingKey`).


- [ ] **Step 1: Write the failing test**

In `runControlTests()`, add after `    runControlProtocolTests()`:

```swift
    runControlResolverTests()
    runControlSettingsCatalogTests()
```

Append these two sections to the end of `Tests/UnitTests/ControlTests.swift`, each after one blank line:

```swift
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
```

In `Tests/run-unit-tests.sh`, insert before the line `  Tests/UnitTests/ControlTests.swift \`:

```bash
  PixelSwitch/Models/MenuBarModule.swift \
  PixelSwitch/Control/AccountResolver.swift \
  PixelSwitch/Control/SettingsCatalog.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitch/Control/AccountResolver.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Control/AccountResolver.swift`:

```swift
import Foundation

/// Turns what a person or an AI typed ("work", "a@x.com", "2", an id) into one
/// account, or a clear error that lists the choices.
///
/// Tried in order, each level case-insensitive: the account id, the email
/// address, the label, then a 1-based position in the priority order. The
/// first level with any match wins; two matches at that level is ambiguous.
enum AccountResolver {
    static func resolve(_ reference: String, in accounts: [Account]) throws -> Account {
        let ref = reference.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !ref.isEmpty else {
            throw ControlError(.invalidValue, "Name an account: its email, label, id or position.")
        }
        let levels: [(Account) -> Bool] = [
            { $0.id.uuidString.caseInsensitiveCompare(ref) == .orderedSame },
            { $0.email.caseInsensitiveCompare(ref) == .orderedSame },
            { ($0.customLabel ?? "").caseInsensitiveCompare(ref) == .orderedSame }
        ]
        for matches in levels {
            let hits = accounts.filter(matches)
            if hits.count == 1 { return hits[0] }
            if hits.count > 1 {
                throw ControlError(.ambiguous, "\"\(ref)\" matches \(hits.count) accounts. Use the email address or id.",
                                   candidates: hits.map(describe))
            }
        }
        if let position = Int(ref), position >= 1, position <= accounts.count {
            return accounts[position - 1]
        }
        throw ControlError(.notFound, "No account matches \"\(ref)\".", candidates: accounts.map(describe))
    }

    /// Resolves every reference, refusing the whole list if any fails.
    static func resolveAll(_ references: [String], in accounts: [Account]) throws -> [Account] {
        try references.map { try resolve($0, in: accounts) }
    }

    /// How an account is named in an error: its email, then its label if it has one.
    static func describe(_ account: Account) -> String {
        if let label = account.customLabel, !label.isEmpty { return "\(account.email) (\(label))" }
        return account.email
    }
}
```

Create `PixelSwitch/Control/SettingsCatalog.swift`:

```swift
import Foundation

/// Every setting the Settings window offers, by the name the CLI and the
/// control API use, with the values it accepts. Pure: `SettingsStore` reads
/// and writes the real values; this only knows their shape.
///
/// The hysteresis (10 points) and the cooldown (300 s) are not settings, in
/// the window or here.
enum SettingKey: String, CaseIterable, Sendable {
    case refreshInterval = "refreshInterval"
    case transcriptLookbackHours = "transcriptLookbackHours"
    case autoSwitchEnabled = "autoSwitch.enabled"
    case autoSwitchThreshold = "autoSwitch.threshold"
    case autoSwitchOnFable = "autoSwitch.onFable"
    case autoSwitchStrategy = "autoSwitch.strategy"
    case autoSwitchDrainEarly = "autoSwitch.drainEarly"
    case autoSwitchDrainWithinHours = "autoSwitch.drainWithinHours"
    case maskEmailAddresses = "maskEmailAddresses"
    case colorCodeAccounts = "colorCodeAccounts"
    case appLanguage = "appLanguage"
    case launchAtLogin = "launchAtLogin"
    case menuBarShowsHeadIcon = "menuBar.showsHeadIcon"
    case menuBarModules = "menuBar.modules"
    case menuBarCustomizesLimitBarColors = "menuBar.customizesLimitBarColors"
    case menuBarColorSession = "menuBar.color.session"
    case menuBarColorWeekly = "menuBar.color.weekly"
    case menuBarColorFable = "menuBar.color.fable"
    case menuBarColorLowRemaining = "menuBar.color.lowRemaining"
    case menuBarLowRemainingWarningThreshold = "menuBar.lowRemainingWarningThreshold"
    case claudeBinaryPath = "claude.binaryPath"

    enum Kind: Equatable, Sendable {
        case bool
        /// One of these values exactly.
        case choice([JSONValue])
        /// A number in range, rounded to `step` when given.
        case number(ClosedRange<Double>, step: Double?)
        /// An ordered list drawn from these values, no repeats.
        case list([String])
        /// `#RRGGBB`.
        case color
        /// `""` (auto-detect) or an absolute path.
        case path
    }

    var kind: Kind {
        switch self {
        case .refreshInterval: return .choice([15, 30, 60, 300, 600].map(JSONValue.number))
        case .transcriptLookbackHours: return .choice([24, 72, 168, 720, 0].map(JSONValue.number))
        case .autoSwitchEnabled, .autoSwitchOnFable, .autoSwitchDrainEarly, .maskEmailAddresses,
             .colorCodeAccounts, .launchAtLogin, .menuBarShowsHeadIcon, .menuBarCustomizesLimitBarColors:
            return .bool
        case .autoSwitchThreshold: return .number(AutoSwitchSettings.thresholdRange, step: 1)
        case .autoSwitchStrategy: return .choice(AutoSwitchStrategy.allCases.map { .string($0.rawValue) })
        case .autoSwitchDrainWithinHours: return .number(AutoSwitchSettings.drainHoursRange, step: 1)
        case .appLanguage: return .choice(["auto", "en", "zh-Hans", "ja", "de", "fr"].map(JSONValue.string))
        case .menuBarModules: return .list(MenuBarModule.allCases.map(\.rawValue))
        case .menuBarColorSession, .menuBarColorWeekly, .menuBarColorFable, .menuBarColorLowRemaining: return .color
        case .menuBarLowRemainingWarningThreshold: return .number(0...100, step: nil)
        case .claudeBinaryPath: return .path
        }
    }

    /// One line for `pixelswitch settings` and the MCP tool description.
    var summary: String {
        switch kind {
        case .bool: return "true or false"
        case .choice(let values): return "one of " + values.map(\.compactText).joined(separator: ", ")
        case .number(let range, let step):
            return "\(Self.format(range.lowerBound))–\(Self.format(range.upperBound))" + (step == 1 ? ", whole numbers" : "")
        case .list(let values): return "a list drawn from " + values.joined(separator: ", ")
        case .color: return "a colour as #RRGGBB"
        case .path: return "\"\" to auto-detect, or an absolute path"
        }
    }

    /// `value` in the canonical form this setting stores, or `invalidValue`.
    /// Accepts what a person types on a command line too: "true"/"on"/"yes",
    /// numbers as text, a comma-separated list.
    func normalize(_ value: JSONValue) throws -> JSONValue {
        switch kind {
        case .bool:
            if let bool = value.boolValue { return .bool(bool) }
            if let number = value.doubleValue, number == 0 || number == 1 { return .bool(number == 1) }
            if let text = value.stringValue?.lowercased() {
                if ["true", "on", "yes", "1"].contains(text) { return .bool(true) }
                if ["false", "off", "no", "0"].contains(text) { return .bool(false) }
            }
            throw invalid()

        case .choice(let values):
            if let number = Self.number(value), let match = values.first(where: { $0.doubleValue == number }) { return match }
            if let text = value.stringValue,
               let match = values.first(where: { $0.stringValue?.caseInsensitiveCompare(text) == .orderedSame }) {
                return match
            }
            throw invalid()

        case .number(let range, let step):
            guard var number = Self.number(value), number.isFinite else { throw invalid() }
            if let step { number = (number / step).rounded() * step }
            guard range.contains(number) else { throw invalid() }
            return .number(number)

        case .list(let allowed):
            let items: [String]
            if let array = value.arrayValue {
                items = try array.map { item in
                    guard let text = item.stringValue else { throw invalid() }
                    return text
                }
            } else if let text = value.stringValue {
                items = text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            } else {
                throw invalid()
            }
            var seen = Set<String>()
            var result: [JSONValue] = []
            for item in items {
                guard let canonical = allowed.first(where: { $0.caseInsensitiveCompare(item) == .orderedSame }) else { throw invalid() }
                if seen.insert(canonical).inserted { result.append(.string(canonical)) }
            }
            return .array(result)

        case .color:
            guard var text = value.stringValue?.trimmingCharacters(in: .whitespaces) else { throw invalid() }
            if text.hasPrefix("#") { text.removeFirst() }
            guard text.count == 6, text.allSatisfy(\.isHexDigit) else { throw invalid() }
            return .string("#" + text.uppercased())

        case .path:
            guard let text = value.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) else { throw invalid() }
            if text.isEmpty || text.lowercased() == "auto" { return .string("") }
            guard text.hasPrefix("/") else { throw invalid() }
            return .string(text)
        }
    }

    /// Finds a key by its name, case-insensitive, or lists the valid names.
    static func named(_ name: String) throws -> SettingKey {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        if let key = allCases.first(where: { $0.rawValue.caseInsensitiveCompare(trimmed) == .orderedSame }) { return key }
        throw ControlError(.notFound, "There is no setting called \"\(trimmed)\".", candidates: allCases.map(\.rawValue))
    }

    private func invalid() -> ControlError {
        ControlError(.invalidValue, "\(rawValue) must be \(summary).")
    }

    private static func number(_ value: JSONValue) -> Double? {
        if let number = value.doubleValue { return number }
        if let text = value.stringValue?.trimmingCharacters(in: .whitespaces) { return Double(text) }
        return nil
    }

    private static func format(_ number: Double) -> String {
        number.rounded() == number ? String(Int(number)) : String(number)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `387 passed, 0 failed` (+24).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Control/AccountResolver.swift PixelSwitch/Control/SettingsCatalog.swift Tests/UnitTests/ControlTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: account names and the settings catalog

Accounts are named by id, email, label or position; ambiguity and misses list
the choices. Every GUI setting has a CLI name and a normalizer that accepts what
a person types and refuses anything the window would not allow.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The control API, and the no-token rule

**Files:**
- Create: `PixelSwitch/Control/ControlAPI.swift`
- Modify: `Tests/UnitTests/ControlTests.swift` (two call lines, two sections)
- Modify: `Tests/run-unit-tests.sh` (one file line)

**Interfaces:**
- Consumes: Tasks 1–2; `UsageAPIResponse`, `AutoSwitchEngine.fableModelName`, `SignInSession` (all in the harness).
- Produces: `@MainActor protocol AppControlling` (the app's side, exact members in the code below); `@MainActor final class ControlAPI` (`init(controller:log:)`, `handle(_:onSubscribe:) async -> String?`); `enum ControlSnapshots` (`accountInfo`, `usageInfo`, `signInInfo(_ session: SignInSession)`).


- [ ] **Step 1: Write the failing test**

In `runControlTests()`, add after `    runControlSettingsCatalogTests()`:

```swift
    runControlAPITests()
    runControlNoTokenTests()
```

Append these two sections (the first begins with an `extension ControlTestKit` holding the fake app), each after one blank line:

```swift
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
    check(kind(Kit.call(api, "accounts.setThreshold", .object(["account": .string("1"), "threshold": .number(101)]))) == "invalidValue"
          && kind(Kit.call(api, "accounts.setThreshold", .object(["account": .string("1"), "threshold": .number(49)]))) == "invalidValue",
          "api: a threshold outside 50–100 is refused")

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
```

In `Tests/run-unit-tests.sh`, insert before `  Tests/UnitTests/ControlTests.swift \`:

```bash
  PixelSwitch/Control/ControlAPI.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitch/Control/ControlAPI.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Control/ControlAPI.swift`:

```swift
import Foundation

/// What the control API needs from the app. `AppController` is the real one;
/// the unit tests use a fake. Nothing here can hand out a login token: the
/// protocol only ever exposes `AppState`-level data.
@MainActor
protocol AppControlling: AnyObject {
    var appVersion: String { get }
    var claudeAvailable: Bool { get }
    var lastUsageRefresh: Date? { get }
    /// In priority order.
    var accounts: [Account] { get }
    var autoSwitch: AutoSwitchInfo { get }
    var machineUsage: MachineUsageInfo { get }
    /// The sign-in in progress, or the last one until it is dismissed.
    var signIn: SignInInfo? { get }

    func usage(for id: UUID) -> UsageAPIResponse?
    func usageSampledAt(for id: UUID) -> Date?
    func usageError(for id: UUID) -> String?
    func isSwitchable(_ id: UUID) -> Bool
    func effectiveSwitchThreshold(for account: Account) -> Double

    /// Returns once the switch has finished. Throws `ControlError`.
    func switchAccount(to id: UUID) async throws
    /// Saves the account Claude Code is signed in as; returns its id.
    func addCurrentAccount() async throws -> UUID
    /// `open`: nil for none, "default", or a browser's name.
    func startSignIn(_ purpose: SignInPurpose, open: String?) throws -> SignInInfo
    func submitSignInCode(_ code: String) throws -> SignInInfo
    func cancelSignIn() throws -> SignInInfo
    func removeAccount(_ id: UUID) throws
    func setLabel(_ label: String?, for id: UUID)
    func setSwitchThreshold(_ threshold: Double?, for id: UUID)
    func setAccountOrder(_ ids: [UUID]) -> Bool
    func refreshUsage() async
    func settingValue(_ key: SettingKey) -> JSONValue
    /// `value` is already normalized by `SettingKey.normalize`.
    func setSetting(_ key: SettingKey, to value: JSONValue) throws
    func checkForUpdates()
    func quit()
}

/// Turns one request line into one response line. Pure apart from the
/// `AppControlling` it is given, so every method is unit-tested with a fake.
@MainActor
final class ControlAPI {
    private let controller: any AppControlling
    private let log: @MainActor (String) -> Void

    init(controller: any AppControlling, log: @escaping @MainActor (String) -> Void = { _ in }) {
        self.controller = controller
        self.log = log
    }

    /// The response line for `line`, or nil when `line` is a notification.
    /// `onSubscribe` runs when the request is `events.subscribe`.
    func handle(_ line: String, onSubscribe: @MainActor () -> Void = {}) async -> String? {
        let request: RPCRequest
        do {
            request = try ControlCoding.decoder.decode(RPCRequest.self, from: Data(line.utf8))
        } catch {
            log("[control] unreadable request")
            return RPCResponse.failure(nil, ControlError(.parse, "That is not a JSON-RPC request.")).line
        }
        guard request.jsonrpc == "2.0" else {
            return RPCResponse.failure(request.id, ControlError(.invalidRequest, "jsonrpc must be \"2.0\".")).line
        }
        var detail = ""
        do {
            let result = try await dispatch(request, detail: &detail, onSubscribe: onSubscribe)
            log("[control] \(request.method)\(detail) ok")
            return request.id == nil ? nil : RPCResponse.success(request.id, result).line
        } catch let error as ControlError {
            log("[control] \(request.method)\(detail) \(error.kind.rawValue)")
            return request.id == nil ? nil : RPCResponse.failure(request.id, error).line
        } catch {
            log("[control] \(request.method)\(detail) failed")
            return request.id == nil ? nil : RPCResponse.failure(request.id, ControlError(.failed, error.localizedDescription)).line
        }
    }

    // MARK: - Dispatch

    private func dispatch(_ request: RPCRequest, detail: inout String, onSubscribe: @MainActor () -> Void) async throws -> JSONValue {
        guard let method = ControlMethod(rawValue: request.method) else {
            throw ControlError(.methodNotFound, "There is no method called \"\(request.method)\".",
                               candidates: ControlMethod.allCases.map(\.rawValue))
        }
        switch method {
        case .statusGet:
            return try .encode(status())

        case .accountsList:
            return .object(["accounts": try .encode(accountInfos())])

        case .accountsSwitch:
            let target = try resolve(params(request, AccountParams.self).account)
            detail = " \(target.id.uuidString)"
            try await controller.switchAccount(to: target.id)
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsAddCurrent:
            let id = try await controller.addCurrentAccount()
            detail = " \(id.uuidString)"
            return .object(["account": try .encode(info(for: id))])

        case .signInStart:
            let p = try params(request, SignInStartParams.self)
            let purpose: SignInPurpose
            if let reference = p.account {
                let account = try resolve(reference)
                detail = " reauthenticate \(account.id.uuidString)"
                purpose = .reauthenticate(accountId: account.id, email: account.email)
            } else {
                detail = " newAccount"
                purpose = .newAccount
            }
            let open = p.open.map { $0.trimmingCharacters(in: .whitespaces) }.flatMap { $0.isEmpty || $0.lowercased() == "none" ? nil : $0 }
            return .object(["signIn": try .encode(controller.startSignIn(purpose, open: open))])

        case .signInStatus:
            return .object(["signIn": try controller.signIn.map { try JSONValue.encode($0) } ?? JSONValue.null])

        case .signInSubmitCode:
            return .object(["signIn": try .encode(controller.submitSignInCode(params(request, SignInCodeParams.self).code))])

        case .signInCancel:
            return .object(["signIn": try .encode(controller.cancelSignIn())])

        case .accountsRemove:
            let target = try resolve(params(request, AccountParams.self).account)
            detail = " \(target.id.uuidString)"
            let before = info(for: target.id)
            try controller.removeAccount(target.id)
            return .object(["removed": try .encode(before)])

        case .accountsSetLabel:
            let p = try params(request, LabelParams.self)
            let target = try resolve(p.account)
            detail = " \(target.id.uuidString)"
            let label = p.label?.trimmingCharacters(in: .whitespacesAndNewlines)
            if let label, label.count > 100 {
                throw ControlError(.invalidValue, "A label can be at most 100 characters.")
            }
            controller.setLabel(label?.isEmpty == true ? nil : label, for: target.id)
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsSetThreshold:
            let p = try params(request, ThresholdParams.self)
            let target = try resolve(p.account)
            detail = " \(target.id.uuidString)"
            var threshold = p.threshold
            if let value = threshold {
                guard value.isFinite, AutoSwitchSettings.thresholdRange.contains(value.rounded()) else {
                    throw ControlError(.invalidValue, "A threshold must be 50–100, or null to follow the default.")
                }
                threshold = value.rounded()
            }
            controller.setSwitchThreshold(threshold, for: target.id)
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsSetOrder:
            let listed = try AccountResolver.resolveAll(params(request, OrderParams.self).accounts, in: controller.accounts)
            let ids = listed.map(\.id)
            guard Set(ids).count == ids.count, Set(ids) == Set(controller.accounts.map(\.id)) else {
                throw ControlError(.invalidValue, "List every account exactly once, in the order you want.",
                                   candidates: controller.accounts.map(AccountResolver.describe))
            }
            guard controller.setAccountOrder(ids) else {
                throw ControlError(.invalidValue, "The accounts changed while the order was being set. Try again.")
            }
            return .object(["accounts": try .encode(accountInfos())])

        case .usageGet:
            let p = try params(request, UsageParams.self)
            return try .encode(usageReport(only: p.account.map(resolve)))

        case .usageRefresh:
            await controller.refreshUsage()
            return try .encode(usageReport(only: nil))

        case .settingsGet:
            let p = try params(request, SettingGetParams.self)
            let keys = try p.key.map { [try SettingKey.named($0)] } ?? SettingKey.allCases
            detail = p.key.map { " \($0)" } ?? ""
            return .object(["settings": .object(Dictionary(uniqueKeysWithValues: keys.map { ($0.rawValue, controller.settingValue($0)) }))])

        case .settingsSet:
            let p = try params(request, SettingSetParams.self)
            let key = try SettingKey.named(p.key)
            detail = " \(key.rawValue)"
            try controller.setSetting(key, to: key.normalize(p.value))
            return .object(["settings": .object([key.rawValue: controller.settingValue(key)])])

        case .eventsSubscribe:
            onSubscribe()
            return .object(["subscribed": .bool(true)])

        case .appCheckForUpdates:
            controller.checkForUpdates()
            return .object(["ok": .bool(true)])

        case .appQuit:
            controller.quit()
            return .object(["ok": .bool(true)])
        }
    }

    // MARK: - Building results

    private func params<T: Decodable>(_ request: RPCRequest, _ type: T.Type) throws -> T {
        do {
            return try (request.params ?? .object([:])).decode(T.self)
        } catch {
            throw ControlError(.invalidParams, "The parameters for \(request.method) are missing or the wrong shape.")
        }
    }

    private func resolve(_ reference: String) throws -> Account {
        try AccountResolver.resolve(reference, in: controller.accounts)
    }

    private func status() -> StatusInfo {
        let active = controller.accounts.first(where: \.isActive)
        return StatusInfo(
            appVersion: controller.appVersion,
            protocolVersion: ControlProtocol.version,
            activeAccountId: active?.id.uuidString,
            activeAccountEmail: active?.email,
            accountCount: controller.accounts.count,
            claudeAvailable: controller.claudeAvailable,
            lastRefresh: controller.lastUsageRefresh,
            autoSwitch: controller.autoSwitch,
            signIn: controller.signIn
        )
    }

    private func accountInfos() -> [AccountInfo] {
        controller.accounts.enumerated().map { index, account in makeInfo(account, position: index + 1) }
    }

    /// The account's current info, or nil if it is gone.
    private func info(for id: UUID) -> AccountInfo? {
        guard let index = controller.accounts.firstIndex(where: { $0.id == id }) else { return nil }
        return makeInfo(controller.accounts[index], position: index + 1)
    }

    private func makeInfo(_ account: Account, position: Int) -> AccountInfo {
        ControlSnapshots.accountInfo(
            account,
            position: position,
            isSwitchable: controller.isSwitchable(account.id),
            effectiveThreshold: controller.effectiveSwitchThreshold(for: account),
            usage: ControlSnapshots.usageInfo(controller.usage(for: account.id),
                                              sampledAt: controller.usageSampledAt(for: account.id),
                                              error: controller.usageError(for: account.id))
        )
    }

    private func usageReport(only account: Account?) -> UsageReport {
        let all = accountInfos()
        return UsageReport(
            accounts: account.map { a in all.filter { $0.id == a.id.uuidString } } ?? all,
            machine: controller.machineUsage,
            lastRefresh: controller.lastUsageRefresh
        )
    }
}

/// Turns app models into protocol results. Pure, so the no-token rule and the
/// shapes are unit-tested.
enum ControlSnapshots {
    static func accountInfo(_ account: Account, position: Int, isSwitchable: Bool, effectiveThreshold: Double, usage: UsageInfo?) -> AccountInfo {
        AccountInfo(
            id: account.id.uuidString,
            email: account.email,
            label: account.customLabel.flatMap { $0.isEmpty ? nil : $0 },
            subscription: account.displaySubscriptionType,
            isActive: account.isActive,
            isSwitchable: isSwitchable,
            position: position,
            threshold: account.switchThreshold,
            effectiveThreshold: effectiveThreshold,
            usage: usage
        )
    }

    static func usageInfo(_ usage: UsageAPIResponse?, sampledAt: Date?, error: String?) -> UsageInfo? {
        guard usage != nil || error != nil else { return nil }
        func window(_ w: UsageWindow?) -> WindowInfo? {
            guard let w, w.utilization != nil || w.resetsAt != nil else { return nil }
            return WindowInfo(utilization: w.utilization, resetsAt: w.resetsAt)
        }
        let extra = usage?.extraUsage.map {
            ExtraUsageInfo(enabled: $0.isEnabled, monthlyLimit: $0.monthlyLimit, usedCredits: $0.usedCredits, utilization: $0.utilization)
        }
        return UsageInfo(
            session: window(usage?.fiveHour),
            weekly: window(usage?.sevenDay),
            fable: window(usage?.modelWeeklyLimit(named: AutoSwitchEngine.fableModelName)?.window),
            extraUsage: extra,
            sampledAt: sampledAt,
            error: error
        )
    }

    @MainActor
    static func signInInfo(_ session: SignInSession) -> SignInInfo {
        var purpose = "newAccount"
        var accountId: String?
        var email: String?
        if case .reauthenticate(let id, let address) = session.purpose {
            purpose = "reauthenticate"
            accountId = id.uuidString
            email = address
        }
        var state: String
        var message: String?
        var result: String?
        switch session.state {
        case .starting: state = "starting"
        case .waitingForUser: state = "waitingForUser"
        case .completing: state = "completing"
        case .succeeded(let id): state = "succeeded"; result = id.uuidString
        case .failed(let why): state = "failed"; message = why
        case .cancelled: state = "cancelled"
        }
        return SignInInfo(
            id: session.id.uuidString, purpose: purpose, accountId: accountId, email: email,
            state: state, message: message, resultAccountId: result,
            automaticLink: session.automaticLink?.absoluteString, manualLink: session.manualLink?.absoluteString,
            notice: session.notice, codeSubmitted: session.codeSubmitted, startedAt: session.startedAt
        )
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `431 passed, 0 failed` (+44).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Control/ControlAPI.swift Tests/UnitTests/ControlTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: the control API over a fake app, and the no-token rule

Every method maps onto AppControlling with typed errors; results are built by
pure snapshots, and a test scans every result for token fields. The log names
methods, ids, keys and outcomes, never an address.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: The socket server and the client, end to end

**Files:**
- Create: `PixelSwitch/Control/ControlServer.swift`
- Create: `PixelSwitchCLI/ControlClient.swift` (the tool's folder starts here)
- Modify: `scripts/typecheck.sh` (the tool branch)
- Modify: `Tests/UnitTests/ControlTests.swift` (one call line, one section)
- Modify: `Tests/run-unit-tests.sh` (two file lines)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `final class ControlServer: @unchecked Sendable` (`typealias Handler`, `enum StartError { pathTooLong, alreadyRunning, system }`, `init(path:handler:log:)`, `isListening`, `start() throws`, `stop()`, `subscribers()`, `static func address(_:)`, `static func canConnect(to:)`); `final class ControlConnection` (`id`, `isSubscribed`, `markSubscribed()`, `send(_:) -> Bool`, `close()`); `final class ControlClient` (`enum ClientError { unreachable, timedOut, closed }`, `init(path:)`, `connect(launch:wait:) throws`, `disconnect()`, `call(_:_:timeout:) throws -> JSONValue`, `nextEvent(until:) throws -> RPCNotification?`).


- [ ] **Step 1: Write the failing test**

In `runControlTests()`, add after `    runControlNoTokenTests()`:

```swift
    runControlSocketTests()
```

Append this section, after one blank line:

```swift
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
}
```

In `Tests/run-unit-tests.sh`, insert before `  Tests/UnitTests/ControlTests.swift \`:

```bash
  PixelSwitch/Control/ControlServer.swift \
  PixelSwitchCLI/ControlClient.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitch/Control/ControlServer.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Control/ControlServer.swift`:

```swift
import Foundation
import Darwin

/// The app's end of remote control: a Unix-domain socket that only this Mac's
/// user can open, speaking newline-delimited JSON-RPC.
///
/// - The folder is made 0700 and the socket 0600, and every connection's peer
///   is checked with `getpeereid`; a different user is disconnected at once.
/// - There is no TCP listener of any kind. Another Mac reaches this one by
///   running `pixelswitch` here over SSH.
/// - A socket file left by a crash is replaced; one that still answers means
///   another copy of PixelSwitch is running, and this one does not start.
/// - Each connection handles its requests one at a time, in order, on the
///   main actor; connections do not wait for each other.
///
/// No dependency on the rest of the app, so the unit harness runs it for real.
final class ControlServer: @unchecked Sendable {
    typealias Handler = @MainActor @Sendable (_ line: String, _ connection: ControlConnection) async -> String?

    enum StartError: Error, Equatable, CustomStringConvertible {
        case pathTooLong(String)
        case alreadyRunning
        case system(String, Int32)

        var description: String {
            switch self {
            case .pathTooLong(let path): return "The socket path is too long for macOS (\(path.utf8.count) bytes): \(path)"
            case .alreadyRunning: return "Another copy of PixelSwitch is already listening."
            case .system(let call, let code): return "\(call) failed: \(String(cString: strerror(code)))"
            }
        }
    }

    let path: String
    private let handler: Handler
    private let log: @Sendable (String) -> Void
    private let queue = DispatchQueue(label: "ai.pixelventures.pixelswitch.control")
    private let lock = NSLock()
    private var listenFD: Int32 = -1
    private var acceptSource: DispatchSourceRead?
    private var connections: [UUID: ControlConnection] = [:]

    init(path: String, handler: @escaping Handler, log: @escaping @Sendable (String) -> Void = { _ in }) {
        self.path = path
        self.handler = handler
        self.log = log
    }

    var isListening: Bool {
        lock.lock(); defer { lock.unlock() }
        return listenFD >= 0
    }

    /// Opens the socket and starts accepting. Throws `StartError`.
    func start() throws {
        guard path.utf8.count <= ControlProtocol.maxSocketPathBytes else { throw StartError.pathTooLong(path) }
        let folder = (path as NSString).deletingLastPathComponent
        do {
            try FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true)
        } catch {
            throw StartError.system("mkdir", Int32((error as NSError).code))
        }
        chmod(folder, 0o700)

        if FileManager.default.fileExists(atPath: path) {
            if Self.canConnect(to: path) { throw StartError.alreadyRunning }
            unlink(path)
            log("[control] Replaced a socket left behind by an earlier run")
        }

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw StartError.system("socket", errno) }
        var address = Self.address(path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard bound == 0 else { let code = errno; close(fd); throw StartError.system("bind", code) }
        chmod(path, 0o600)
        guard listen(fd, 16) == 0 else { let code = errno; close(fd); unlink(path); throw StartError.system("listen", code) }
        _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)

        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in self?.acceptPending() }
        lock.lock()
        listenFD = fd
        acceptSource = source
        lock.unlock()
        source.resume()
        log("[control] Listening at \(path)")
    }

    /// Stops accepting, closes every connection and removes the socket file.
    func stop() {
        lock.lock()
        let source = acceptSource
        let fd = listenFD
        let open = Array(connections.values)
        acceptSource = nil
        listenFD = -1
        connections = [:]
        lock.unlock()
        guard fd >= 0 else { return }
        source?.setCancelHandler { close(fd) }
        source?.cancel()
        open.forEach { $0.close() }
        unlink(path)
        log("[control] Stopped")
    }

    /// Every connection that asked for events.
    func subscribers() -> [ControlConnection] {
        lock.lock(); defer { lock.unlock() }
        return connections.values.filter(\.isSubscribed)
    }

    // MARK: - Accepting

    private func acceptPending() {
        while true {
            lock.lock(); let fd = listenFD; lock.unlock()
            guard fd >= 0 else { return }
            let client = accept(fd, nil, nil)
            guard client >= 0 else { return }  // EAGAIN: nothing more waiting
            var peerUID: uid_t = 0
            var peerGID: gid_t = 0
            guard getpeereid(client, &peerUID, &peerGID) == 0, peerUID == getuid() else {
                log("[control] Refused a connection from another user (uid \(peerUID))")
                close(client)
                continue
            }
            var on: Int32 = 1
            setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
            _ = fcntl(client, F_SETFL, fcntl(client, F_GETFL) | O_NONBLOCK)
            let connection = ControlConnection(fd: client, queue: queue, onClose: { [weak self] id in self?.forget(id) })
            lock.lock(); connections[connection.id] = connection; lock.unlock()
            connection.start(handler: handler)
        }
    }

    private func forget(_ id: UUID) {
        lock.lock(); connections[id] = nil; lock.unlock()
    }

    // MARK: - Socket helpers

    static func address(_ path: String) -> sockaddr_un {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8.prefix(ControlProtocol.maxSocketPathBytes))
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: bytes)
            raw[bytes.count] = 0
        }
        return address
    }

    /// True when something is listening at `path`.
    static func canConnect(to path: String) -> Bool {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var address = address(path)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        return result == 0
    }
}

/// One client connection. Reads lines off the main thread, handles them one
/// at a time on the main actor, and writes replies and pushed events.
final class ControlConnection: @unchecked Sendable {
    let id = UUID()
    private let fd: Int32
    private let queue: DispatchQueue
    private let onClose: @Sendable (UUID) -> Void
    private let lock = NSLock()
    private var source: DispatchSourceRead?
    private var buffer = Data()
    private var closed = false
    private var subscribed = false
    private var continuation: AsyncStream<String>.Continuation?

    init(fd: Int32, queue: DispatchQueue, onClose: @escaping @Sendable (UUID) -> Void) {
        self.fd = fd
        self.queue = queue
        self.onClose = onClose
    }

    var isSubscribed: Bool {
        lock.lock(); defer { lock.unlock() }
        return subscribed && !closed
    }

    func markSubscribed() {
        lock.lock(); subscribed = true; lock.unlock()
    }

    func start(handler: @escaping ControlServer.Handler) {
        let (lines, continuation) = AsyncStream<String>.makeStream()
        lock.lock(); self.continuation = continuation; lock.unlock()
        Task { @MainActor in
            for await line in lines {
                if let reply = await handler(line, self) { self.send(reply) }
            }
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in self?.readAvailable() }
        lock.lock(); self.source = source; lock.unlock()
        source.resume()
    }

    /// Writes `line` and a newline. False once the connection is closed.
    @discardableResult
    func send(_ line: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !closed else { return false }
        var bytes = Array((line + "\n").utf8)
        var offset = 0
        while offset < bytes.count {
            let written = bytes.withUnsafeMutableBytes { raw in
                write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset)
            }
            if written > 0 { offset += written; continue }
            if written < 0, errno == EAGAIN || errno == EINTR {
                var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
                if poll(&pfd, 1, 2000) <= 0 { break }
                continue
            }
            break
        }
        bytes.removeAll()
        return offset > 0
    }

    func close() {
        lock.lock()
        guard !closed else { lock.unlock(); return }
        closed = true
        let source = self.source
        let continuation = self.continuation
        self.source = nil
        self.continuation = nil
        lock.unlock()
        continuation?.finish()
        let fd = self.fd
        if let source {
            source.setCancelHandler { Darwin.close(fd) }
            source.cancel()
        } else {
            Darwin.close(fd)
        }
        onClose(id)
    }

    private func readAvailable() {
        var chunk = [UInt8](repeating: 0, count: 16 * 1024)
        while true {
            let count = read(fd, &chunk, chunk.count)
            if count > 0 {
                lock.lock(); buffer.append(contentsOf: chunk[0..<count]); lock.unlock()
                continue
            }
            if count < 0, errno == EAGAIN || errno == EINTR { break }
            // 0 = the client closed; < 0 = an error.
            deliverLines()
            close()
            return
        }
        deliverLines()
    }

    private func deliverLines() {
        lock.lock()
        var lines: [String] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            var text = String(decoding: lineData, as: UTF8.self)
            if text.hasSuffix("\r") { text.removeLast() }
            if !text.trimmingCharacters(in: .whitespaces).isEmpty { lines.append(text) }
        }
        let overflow = buffer.count > ControlProtocol.maxLineBytes
        let continuation = self.continuation
        lock.unlock()
        for line in lines { continuation?.yield(line) }
        if overflow { close() }
    }
}
```

Create `PixelSwitchCLI/ControlClient.swift`:

```swift
import Foundation
import Darwin

/// The command-line tool's end of remote control: one connection to the app's
/// socket, blocking calls with a timeout. Starts PixelSwitch if it is not
/// running. Pure Foundation, so the unit harness drives it against a real
/// `ControlServer`.
final class ControlClient {
    enum ClientError: Error, Equatable, CustomStringConvertible {
        /// PixelSwitch is not running and could not be started, or its socket is missing.
        case unreachable(String)
        case timedOut(String)
        case closed

        var description: String {
            switch self {
            case .unreachable(let why): return "PixelSwitch is not reachable: \(why)"
            case .timedOut(let method): return "PixelSwitch did not answer \(method) in time."
            case .closed: return "PixelSwitch closed the connection."
            }
        }
    }

    let path: String
    private var fd: Int32 = -1
    private var buffer = Data()
    private var nextId = 1

    /// `PIXELSWITCH_CONTROL_SOCKET` points the tool at another socket and
    /// `PIXELSWITCH_NO_LAUNCH=1` stops it starting the app; both exist for the
    /// integration script, so it never touches the real app by accident.
    init(path: String = ProcessInfo.processInfo.environment["PIXELSWITCH_CONTROL_SOCKET"] ?? ControlProtocol.socketPath()) {
        self.path = path
    }

    private var launchAllowed: Bool {
        ProcessInfo.processInfo.environment["PIXELSWITCH_NO_LAUNCH"] != "1"
    }

    deinit { disconnect() }

    /// Connects, starting PixelSwitch first when `launch` is true and nothing
    /// answers (`open -g -b ai.pixelventures.pixelswitch`), waiting up to `wait` seconds.
    func connect(launch: Bool = true, wait: TimeInterval = 10) throws {
        if tryConnect() { return }
        guard launch, launchAllowed else { throw ClientError.unreachable("nothing is listening at \(path)") }
        let open = Process()
        open.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        open.arguments = ["-g", "-b", ControlProtocol.appBundleIdentifier]
        open.standardOutput = FileHandle.nullDevice
        open.standardError = FileHandle.nullDevice
        do { try open.run(); open.waitUntilExit() } catch {
            throw ClientError.unreachable("could not start PixelSwitch (\(error.localizedDescription))")
        }
        guard open.terminationStatus == 0 else {
            throw ClientError.unreachable("PixelSwitch is not installed, or no one is signed in to this Mac's desktop")
        }
        let deadline = Date().addingTimeInterval(wait)
        while Date() < deadline {
            if tryConnect() { return }
            usleep(250_000)
        }
        throw ClientError.unreachable("PixelSwitch started but did not open its socket within \(Int(wait)) seconds")
    }

    func disconnect() {
        if fd >= 0 { close(fd); fd = -1 }
        buffer.removeAll()
    }

    /// Sends one request and waits for its reply. Throws `ControlError` for an
    /// error reply, `ClientError` for a transport problem.
    func call(_ method: ControlMethod, _ params: JSONValue? = nil, timeout: TimeInterval = 30) throws -> JSONValue {
        let id = nextId
        nextId += 1
        let request = RPCRequest(id: .int(id), method: method.rawValue, params: params)
        let data = try ControlCoding.encoder.encode(request)
        try send(String(decoding: data, as: UTF8.self))
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            guard let line = try readLine(until: deadline) else { throw ClientError.timedOut(method.rawValue) }
            guard let response = try? ControlCoding.decoder.decode(RPCResponse.self, from: Data(line.utf8)),
                  response.id == .int(id) else { continue }  // a pushed event, or another reply
            if let error = response.error { throw ControlError(rpc: error) }
            return response.result ?? .null
        }
    }

    /// The next pushed event, or nil at the deadline. For `watch`, after `events.subscribe`.
    func nextEvent(until deadline: Date) throws -> RPCNotification? {
        while true {
            guard let line = try readLine(until: deadline) else { return nil }
            if let note = try? ControlCoding.decoder.decode(RPCNotification.self, from: Data(line.utf8)),
               note.method == ControlEvent.notificationMethod {
                return note
            }
        }
    }

    // MARK: - Transport

    private func tryConnect() -> Bool {
        disconnect()
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8.prefix(ControlProtocol.maxSocketPathBytes))
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: bytes)
            raw[bytes.count] = 0
        }
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard result == 0 else { close(fd); return false }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
        self.fd = fd
        return true
    }

    private func send(_ line: String) throws {
        guard fd >= 0 else { throw ClientError.closed }
        var bytes = Array((line + "\n").utf8)
        var offset = 0
        while offset < bytes.count {
            let written = bytes.withUnsafeMutableBytes { raw in write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset) }
            if written > 0 { offset += written } else if errno != EINTR { throw ClientError.closed }
        }
    }

    private func readLine(until deadline: Date) throws -> String? {
        while true {
            if let newline = buffer.firstIndex(of: 0x0A) {
                let line = String(decoding: buffer[buffer.startIndex..<newline], as: UTF8.self)
                buffer.removeSubrange(buffer.startIndex...newline)
                return line
            }
            guard fd >= 0 else { throw ClientError.closed }
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { return nil }
            var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, Int32(min(remaining, 3600) * 1000))
            if ready < 0, errno == EINTR { continue }
            guard ready > 0 else { return nil }
            var chunk = [UInt8](repeating: 0, count: 64 * 1024)
            let count = read(fd, &chunk, chunk.count)
            if count > 0 {
                buffer.append(contentsOf: chunk[0..<count])
                if buffer.count > ControlProtocol.maxLineBytes { throw ClientError.closed }
            } else if count == 0 || errno != EINTR {
                disconnect()
                throw ClientError.closed
            }
        }
    }
}
```

The type-check script's tool branch (it runs once `PixelSwitchCLI/` exists) must use the protocol's real location. In `scripts/typecheck.sh`, replace:

```bash
# Also type-checks the command-line tool when PixelSwitchCLI/ exists.
```

with:

```bash
# Also type-checks the command-line tool (PixelSwitchCLI/ plus the shared
# PixelSwitch/Control/ControlProtocol.swift) when PixelSwitchCLI/ exists.
```

and replace:

```bash
    [ -f Shared/ControlProtocol.swift ] && CLI_SOURCES+=(Shared/ControlProtocol.swift)
```

with:

```bash
    CLI_SOURCES+=(PixelSwitch/Control/ControlProtocol.swift)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `444 passed, 0 failed` (+13).
The socket section runs a real server on a temporary socket and takes a few seconds.

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)` and `cli: type-check OK (2 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Control/ControlServer.swift PixelSwitchCLI/ControlClient.swift scripts/typecheck.sh Tests/UnitTests/ControlTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: the user-only socket server and the client

Folder 0700, socket 0600, peer uid checked, no TCP. A dead socket file is
replaced, a live one is never taken over, an endless line is cut off at 1 MiB,
and subscribed connections receive pushed events. The client starts the app with
open -g -b when nothing answers.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The command-line tool: parser, runner and output

**Files:**
- Create: `PixelSwitchCLI/CLIParser.swift`
- Create: `PixelSwitchCLI/CLIRunner.swift`
- Modify: `Tests/UnitTests/ControlTests.swift` (two call lines, two sections)
- Modify: `Tests/run-unit-tests.sh` (two file lines)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `enum CLIOpenChoice`, `enum CLICommand` (every command), `struct CLIInvocation` (`command`, `json`, `timeout`), `struct CLIUsageError`, `enum CLIParser` (`parse(_:) throws -> CLIInvocation`, `helpText`); `enum CLIExit` (0/1/2/3/4/5, `code(for:)`); `struct CLIRunner` (`run(_:) -> Int32`, `static func checkProtocol(_:timeout:)`, `static func value(from:)`); `enum CLIOutput`; `enum SettingKeyNames` (the tool's copy of the 21 keys, checked against `SettingKey`).


- [ ] **Step 1: Write the failing test**

In `runControlTests()`, add after `    runControlSocketTests()`:

```swift
    runControlCLIParserTests()
    runControlCLIOutputTests()
```

Append these two sections, each after one blank line:

```swift
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
    let usage = UsageInfo(session: WindowInfo(utilization: 42.4, resetsAt: nil), weekly: WindowInfo(utilization: 61, resetsAt: nil),
                          fable: nil, extraUsage: nil, sampledAt: nil, error: nil)
    let accounts = [
        AccountInfo(id: "1", email: "alice@work.com", label: "Work", subscription: "Max", isActive: true, isSwitchable: true, position: 1,
                    threshold: nil, effectiveThreshold: 90, usage: usage),
        AccountInfo(id: "2", email: "bob@home.com", label: nil, subscription: nil, isActive: false, isSwitchable: false, position: 2,
                    threshold: 70, effectiveThreshold: 70, usage: nil)
    ]
    let table = CLIOutput.accounts(accounts)
    let lines = table.split(separator: "\n").map(String.init)
    check(lines.count == 4 && lines[1].hasPrefix("*  1  alice@work.com (Work)") && lines[1].contains("42%") && lines[1].contains("90% (default)"),
          "cli output: the active account is starred, with its usage and default threshold", table)
    check(lines[2].hasPrefix("!  2  bob@home.com") && lines[2].contains("70%") && !lines[2].contains("default"),
          "cli output: an account that cannot be switched to is marked, with its own threshold", table)
    check(CLIOutput.accounts([]).contains("add-current"), "cli output: no accounts says how to add one")
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
```

In `Tests/run-unit-tests.sh`, insert before `  Tests/UnitTests/ControlTests.swift \`:

```bash
  PixelSwitchCLI/CLIParser.swift \
  PixelSwitchCLI/CLIRunner.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitchCLI/CLIParser.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitchCLI/CLIParser.swift`:

```swift
import Foundation

/// Where a new sign-in's link should open, if anywhere.
enum CLIOpenChoice: Equatable, Sendable {
    case none
    case defaultBrowser
    case browser(String)

    /// The `open` parameter `accounts.signIn.start` takes.
    var parameter: String? {
        switch self {
        case .none: return nil
        case .defaultBrowser: return "default"
        case .browser(let name): return name
        }
    }
}

/// One `pixelswitch` command, parsed.
enum CLICommand: Equatable, Sendable {
    case help
    case version
    case status
    case listAccounts
    case switchAccount(String)
    case addCurrent
    /// `account` nil: add a new account; set: re-sign that account.
    case signIn(account: String?, open: CLIOpenChoice, wait: Bool)
    case signInStatus
    case signInCode(String)
    case signInCancel
    case remove(String)
    case label(String, String?)
    /// nil threshold: back to the default.
    case threshold(String, Double?)
    case order([String])
    case usage(String?)
    case refresh
    case watch
    case settingsGet(String?)
    case settingsSet(String, String)
    case updateCheck
    case quit
    case mcp
}

struct CLIInvocation: Equatable, Sendable {
    var command: CLICommand
    var json = false
    /// Seconds to wait for each reply.
    var timeout: TimeInterval = 30
}

struct CLIUsageError: Error, Equatable, Sendable {
    let message: String
    init(_ message: String) { self.message = message }
}

/// Parses `pixelswitch`'s arguments. Pure, and unit-tested.
enum CLIParser {
    static func parse(_ arguments: [String]) throws -> CLIInvocation {
        var json = false
        var timeout: TimeInterval = 30
        var words: [String] = []
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--json":
                json = true
            case "--timeout":
                index += 1
                guard index < arguments.count, let seconds = TimeInterval(arguments[index]), seconds > 0 else {
                    throw CLIUsageError("--timeout needs a number of seconds.")
                }
                timeout = seconds
            case "-h", "--help":
                words = ["help"]
                index = arguments.count
                continue
            case "--version":
                words = ["version"]
                index = arguments.count
                continue
            default:
                words.append(argument)
            }
            index += 1
        }
        return CLIInvocation(command: try command(words), json: json, timeout: timeout)
    }

    private static func command(_ words: [String]) throws -> CLICommand {
        guard let first = words.first else { return .help }
        let rest = Array(words.dropFirst())
        switch first {
        case "help": return .help
        case "version": return .version
        case "status": try none(rest, "status"); return .status
        case "switch":
            return .switchAccount(try one(rest, "switch <account>"))
        case "accounts":
            return try accounts(rest)
        case "usage":
            guard rest.count <= 1 else { throw CLIUsageError("Usage: pixelswitch usage [<account>]") }
            return .usage(rest.first)
        case "refresh": try none(rest, "refresh"); return .refresh
        case "watch": try none(rest, "watch"); return .watch
        case "settings":
            if rest.first == "set" {
                guard rest.count >= 3 else { throw CLIUsageError("Usage: pixelswitch settings set <key> <value>") }
                return .settingsSet(rest[1], rest.dropFirst(2).joined(separator: " "))
            }
            guard rest.count <= 1 else { throw CLIUsageError("Usage: pixelswitch settings [<key>]") }
            return .settingsGet(rest.first)
        case "update-check": try none(rest, "update-check"); return .updateCheck
        case "quit": try none(rest, "quit"); return .quit
        case "mcp": try none(rest, "mcp"); return .mcp
        default:
            throw CLIUsageError("Unknown command \"\(first)\". Run `pixelswitch help`.")
        }
    }

    private static func accounts(_ words: [String]) throws -> CLICommand {
        guard let sub = words.first else { return .listAccounts }
        let rest = Array(words.dropFirst())
        switch sub {
        case "list": try none(rest, "accounts list"); return .listAccounts
        case "add-current": try none(rest, "accounts add-current"); return .addCurrent
        case "sign-in":
            switch rest.first {
            case "code": return .signInCode(try one(Array(rest.dropFirst()), "accounts sign-in code <code>"))
            case "cancel": try none(Array(rest.dropFirst()), "accounts sign-in cancel"); return .signInCancel
            case "status": try none(Array(rest.dropFirst()), "accounts sign-in status"); return .signInStatus
            default:
                let (open, wait, leftover) = try signInOptions(rest)
                guard leftover.isEmpty else { throw CLIUsageError("Usage: pixelswitch accounts sign-in [--open | --open-in <browser>] [--wait]") }
                return .signIn(account: nil, open: open, wait: wait)
            }
        case "reauth":
            let (open, wait, leftover) = try signInOptions(rest)
            guard leftover.count == 1 else { throw CLIUsageError("Usage: pixelswitch accounts reauth <account> [--open | --open-in <browser>] [--wait]") }
            return .signIn(account: leftover[0], open: open, wait: wait)
        case "remove":
            let confirmed = rest.contains("--yes")
            let names = rest.filter { $0 != "--yes" }
            guard names.count == 1 else { throw CLIUsageError("Usage: pixelswitch accounts remove <account> --yes") }
            guard confirmed else {
                throw CLIUsageError("Removing an account deletes its saved login. Add --yes to confirm: pixelswitch accounts remove \(names[0]) --yes")
            }
            return .remove(names[0])
        case "label":
            guard let account = rest.first, rest.count >= 2 else {
                throw CLIUsageError("Usage: pixelswitch accounts label <account> <text> | --clear")
            }
            let text = rest.dropFirst().joined(separator: " ")
            return .label(account, text == "--clear" ? nil : text)
        case "threshold":
            guard rest.count == 2 else { throw CLIUsageError("Usage: pixelswitch accounts threshold <account> <50-100|default>") }
            if rest[1].lowercased() == "default" { return .threshold(rest[0], nil) }
            guard let value = Double(rest[1].replacingOccurrences(of: "%", with: "")) else {
                throw CLIUsageError("The threshold must be a number from 50 to 100, or \"default\".")
            }
            return .threshold(rest[0], value)
        case "order":
            guard !rest.isEmpty else { throw CLIUsageError("Usage: pixelswitch accounts order <account> <account> ...") }
            return .order(rest)
        default:
            throw CLIUsageError("Unknown command \"accounts \(sub)\". Run `pixelswitch help`.")
        }
    }

    private static func signInOptions(_ words: [String]) throws -> (CLIOpenChoice, Bool, [String]) {
        var open = CLIOpenChoice.none
        var wait = false
        var leftover: [String] = []
        var index = 0
        while index < words.count {
            switch words[index] {
            case "--open": open = .defaultBrowser
            case "--open-in":
                index += 1
                guard index < words.count else { throw CLIUsageError("--open-in needs a browser's name, like Safari.") }
                open = .browser(words[index])
            case "--wait": wait = true
            default: leftover.append(words[index])
            }
            index += 1
        }
        return (open, wait, leftover)
    }

    private static func none(_ words: [String], _ usage: String) throws {
        guard words.isEmpty else { throw CLIUsageError("Usage: pixelswitch \(usage)") }
    }

    private static func one(_ words: [String], _ usage: String) throws -> String {
        guard words.count == 1 else { throw CLIUsageError("Usage: pixelswitch \(usage)") }
        return words[0]
    }

    static let helpText = """
    pixelswitch: control PixelSwitch from the command line, or from an AI.

    Accounts
      pixelswitch status                             What is active, auto-switch, any sign-in
      pixelswitch accounts                           Every account, in priority order
      pixelswitch switch <account>                   Switch to an account
      pixelswitch accounts add-current               Save the account Claude Code is signed in as
      pixelswitch accounts sign-in [--open | --open-in <browser>] [--wait]
                                                     Sign in a new account (prints the links)
      pixelswitch accounts reauth <account> [--open | --open-in <browser>] [--wait]
      pixelswitch accounts sign-in status | cancel
      pixelswitch accounts sign-in code <code>       The code the page shows, from another device
      pixelswitch accounts remove <account> --yes    Deletes the account's saved login
      pixelswitch accounts label <account> <text> | --clear
      pixelswitch accounts threshold <account> <50-100|default>
      pixelswitch accounts order <account> <account> ...   The full priority order

    Usage
      pixelswitch usage [<account>]                  Session, weekly and Fable use; cost today
      pixelswitch refresh                            Refresh now
      pixelswitch watch                              Live events, one JSON object per line

    Settings and the app
      pixelswitch settings [<key>]                   Every setting, or one
      pixelswitch settings set <key> <value>
      pixelswitch update-check
      pixelswitch quit
      pixelswitch mcp                                Run as an MCP server on stdin/stdout

    <account> is an email address, a label, an id, or a position (1, 2, ...).
    Add --json for exact JSON, --timeout <seconds> to wait longer.
    Exit codes: 0 ok, 1 failed, 2 usage, 3 busy, 4 no such account, 5 PixelSwitch unreachable.
    """
}
```

Create `PixelSwitchCLI/CLIRunner.swift`:

```swift
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
    var out: (String) -> Void = { print($0) }
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `475 passed, 0 failed` (+31).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)` and `cli: type-check OK (4 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitchCLI/CLIParser.swift PixelSwitchCLI/CLIRunner.swift Tests/UnitTests/ControlTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: pixelswitch commands, readable output and exit codes

Every command in the design, --json and --timeout anywhere, remove needs --yes,
exit codes 0/1/2/3/4/5, and a protocol check that tells an old app to restart.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: MCP mode and the tool's entry point

**Files:**
- Create: `PixelSwitchCLI/MCPServer.swift`
- Create: `PixelSwitchCLI/PixelSwitchCommand.swift` (the `@main` entry; not in the harness)
- Modify: `Tests/UnitTests/ControlTests.swift` (one call line, one section)
- Modify: `Tests/run-unit-tests.sh` (one file line)

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: `final class MCPServer` (`modernVersion`, `legacyVersions`, `supportedVersions`, `instructions`, `struct Tool`, `static let tools` (18), `toolList()`, `init(serverVersion:call:)`, `handle(_:) -> String?`, `run(_:_:)`); `enum MCPStdio` (`run(serverVersion:) -> Int32`); `@main struct PixelSwitchCommand`.


- [ ] **Step 1: Write the failing test**

In `runControlTests()`, add after `    runControlCLIOutputTests()`:

```swift
    runControlMCPTests()
```

Append this section, after one blank line:

```swift
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
```

In `Tests/run-unit-tests.sh`, insert before `  Tests/UnitTests/ControlTests.swift \`:

```bash
  PixelSwitchCLI/MCPServer.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitchCLI/MCPServer.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitchCLI/MCPServer.swift`:

```swift
import Foundation

/// `pixelswitch mcp`: PixelSwitch as an MCP server on stdin/stdout, so Claude
/// on another Mac (over `ssh … pixelswitch mcp`) sees each action as a tool.
///
/// Speaks both MCP eras:
/// - **Modern** (2026-07-28): stateless; every request carries its version in
///   `_meta["io.modelcontextprotocol/protocolVersion"]`; `server/discover`
///   lists what is supported; results carry `resultType`.
/// - **Legacy** (2025-11-25 and earlier): an `initialize` handshake, then the
///   same `tools/list` and `tools/call`.
///
/// Pure: the transport and the app call are injected, so it is unit-tested.
/// It writes nothing but protocol messages; diagnostics go to stderr.
final class MCPServer {
    static let modernVersion = "2026-07-28"
    static let legacyVersions = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
    static var supportedVersions: [String] { [modernVersion] + legacyVersions }
    static let serverName = "pixelswitch"

    static let instructions = """
    Controls PixelSwitch, the Claude Code account switcher on the Mac this server runs on. \
    list_accounts and get_usage show every account with its session, weekly and Fable usage and reset times; \
    switch_account changes which Claude login Claude Code uses on that Mac; the set_* tools change order, \
    thresholds, labels and settings. start_sign_in adds or re-signs an account and returns links: a person must \
    open one in a browser and sign in to Anthropic (an AI cannot finish that step); poll get_sign_in_status until \
    it reports succeeded, failed or cancelled. Accounts are named by email address, label, id, or position (1, 2, …).
    """

    /// One MCP tool and the control method behind it.
    struct Tool: Sendable {
        let name: String
        let description: String
        let properties: [String: JSONValue]
        let required: [String]
        let method: ControlMethod
        /// Builds the control call's params from the tool's arguments.
        let params: @Sendable ([String: JSONValue]) -> JSONValue?
    }

    private let serverVersion: String
    private let call: (ControlMethod, JSONValue?) throws -> JSONValue

    init(serverVersion: String, call: @escaping (ControlMethod, JSONValue?) throws -> JSONValue) {
        self.serverVersion = serverVersion
        self.call = call
    }

    // MARK: - Tools

    static let tools: [Tool] = {
        let account = ["account": prop("string", "Email address, label, id, or position (1, 2, …) of the account.")]
        let pass: @Sendable ([String: JSONValue]) -> JSONValue? = { .object($0) }
        return [
            Tool(name: "get_status", description: "PixelSwitch's version, the active account, auto-switch settings and any sign-in in progress.",
                 properties: [:], required: [], method: .statusGet, params: { _ in nil }),
            Tool(name: "list_accounts", description: "Every account in priority order, with its usage, its switch threshold, and whether it can be switched to.",
                 properties: [:], required: [], method: .accountsList, params: { _ in nil }),
            Tool(name: "get_usage", description: "Session (5-hour), weekly and Fable usage with reset times for every account, or one; plus this Mac's cost and activity today. Non-active accounts are read in turn, so check sampledAt.",
                 properties: ["account": prop("string", "Optional: only this account.")], required: [], method: .usageGet, params: pass),
            Tool(name: "refresh_usage", description: "Refresh usage now, then return it like get_usage.",
                 properties: [:], required: [], method: .usageRefresh, params: { _ in nil }),
            Tool(name: "switch_account", description: "Make this account the one Claude Code uses on that Mac. Returns when the switch is done.",
                 properties: account, required: ["account"], method: .accountsSwitch, params: pass),
            Tool(name: "add_current_account", description: "Save the account Claude Code is currently signed in as on that Mac.",
                 properties: [:], required: [], method: .accountsAddCurrent, params: { _ in nil }),
            Tool(name: "remove_account", description: "Remove an account and delete its saved login. Signing it back in needs a person at a browser.",
                 properties: account, required: ["account"], method: .accountsRemove, params: pass),
            Tool(name: "start_sign_in", description: "Start signing in a new account, or re-signing one (pass account). Returns links: automaticLink finishes by itself in any browser on that Mac; manualLink works on any device and shows a code to pass to submit_sign_in_code. A person must sign in; poll get_sign_in_status.",
                 properties: ["account": prop("string", "Optional: re-sign this account instead of adding a new one."),
                              "open": prop("string", "Optional: \"default\" to open the link in the default browser on that Mac, or a browser's name such as \"Safari\". Omit to open nothing.")],
                 required: [], method: .signInStart, params: pass),
            Tool(name: "get_sign_in_status", description: "The current or last sign-in: its state (starting, waitingForUser, completing, succeeded, failed, cancelled) and links.",
                 properties: [:], required: [], method: .signInStatus, params: { _ in nil }),
            Tool(name: "submit_sign_in_code", description: "Hand Claude Code the code#state the sign-in page showed on another device.",
                 properties: ["code": prop("string", "The whole code, including the part after #.")], required: ["code"], method: .signInSubmitCode, params: pass),
            Tool(name: "cancel_sign_in", description: "Cancel the sign-in in progress. Nothing is changed.",
                 properties: [:], required: [], method: .signInCancel, params: { _ in nil }),
            Tool(name: "set_account_label", description: "Give an account a label, or clear it with an empty or null label.",
                 properties: account.merging(["label": .object(["type": .array([.string("string"), .string("null")]), "description": .string("The label; empty or null clears it.")])]) { a, _ in a },
                 required: ["account"], method: .accountsSetLabel, params: pass),
            Tool(name: "set_account_threshold", description: "Set the usage percentage (50–100) at which auto-switch moves off this account. Null or omitted: follow the default threshold. 100 uses the account until it is empty.",
                 properties: account.merging(["threshold": .object(["type": .array([.string("number"), .string("null")]), "minimum": .number(50), "maximum": .number(100)])]) { a, _ in a },
                 required: ["account"], method: .accountsSetThreshold, params: pass),
            Tool(name: "set_account_order", description: "Set the priority order: list every account exactly once, first choice first. \"My order\" auto-switching follows it.",
                 properties: ["accounts": .object(["type": .string("array"), "items": .object(["type": .string("string")]), "description": .string("Every account, in order.")])],
                 required: ["accounts"], method: .accountsSetOrder, params: pass),
            Tool(name: "get_settings", description: "Every setting and its value, or one (key). Keys include autoSwitch.enabled, autoSwitch.threshold, autoSwitch.strategy (mostRoom, myOrder, resetsSoonest), autoSwitch.drainEarly, autoSwitch.drainWithinHours, refreshInterval, launchAtLogin, menuBar.modules.",
                 properties: ["key": prop("string", "Optional: one setting.")], required: [], method: .settingsGet, params: pass),
            Tool(name: "set_setting", description: "Change one setting. The value is checked; an invalid one is refused with the allowed values.",
                 properties: ["key": prop("string", "The setting's name, as get_settings lists it."),
                              "value": .object(["description": .string("The new value: true/false, a number, a string, or a list, as the setting takes.")])],
                 required: ["key", "value"], method: .settingsSet, params: pass),
            Tool(name: "check_for_updates", description: "Ask PixelSwitch to check for an update (its window opens on that Mac).",
                 properties: [:], required: [], method: .appCheckForUpdates, params: { _ in nil }),
            Tool(name: "quit_app", description: "Quit PixelSwitch on that Mac. The next tool call starts it again.",
                 properties: [:], required: [], method: .appQuit, params: { _ in nil })
        ]
    }()

    private static func prop(_ type: String, _ description: String) -> JSONValue {
        .object(["type": .string(type), "description": .string(description)])
    }

    static func toolList() -> JSONValue {
        .array(tools.map { tool in
            .object([
                "name": .string(tool.name),
                "description": .string(tool.description),
                "inputSchema": .object([
                    "type": .string("object"),
                    "properties": .object(tool.properties),
                    "required": .array(tool.required.map(JSONValue.string)),
                    "additionalProperties": .bool(false)
                ])
            ])
        })
    }

    // MARK: - Handling one message

    /// The reply line for one incoming line, or nil (notifications get none).
    func handle(_ line: String) -> String? {
        guard let message = try? ControlCoding.decoder.decode(JSONValue.self, from: Data(line.utf8)),
              case .object(let object) = message, let method = object["method"]?.stringValue else {
            return Self.errorLine(id: nil, code: -32700, message: "Parse error")
        }
        let id = object["id"]
        guard let id, !id.isNull else { return nil }  // a notification, e.g. notifications/initialized
        let params = object["params"]?.objectValue ?? [:]
        let meta = params["_meta"]?.objectValue ?? [:]
        let requestedVersion = meta["io.modelcontextprotocol/protocolVersion"]?.stringValue
        let modern = requestedVersion != nil

        if let requested = requestedVersion, method != "server/discover" {
            guard Self.supportedVersions.contains(requested) else {
                return Self.errorLine(id: id, code: -32022, message: "Unsupported protocol version",
                                      data: .object(["supported": .array(Self.supportedVersions.map(JSONValue.string)), "requested": .string(requested)]))
            }
            guard meta["io.modelcontextprotocol/clientCapabilities"] != nil else {
                return Self.errorLine(id: id, code: -32602, message: "Missing io.modelcontextprotocol/clientCapabilities in _meta")
            }
        }

        switch method {
        case "initialize":
            let asked = params["protocolVersion"]?.stringValue ?? ""
            let chosen = Self.legacyVersions.contains(asked) ? asked : Self.legacyVersions[0]
            return Self.resultLine(id: id, .object([
                "protocolVersion": .string(chosen),
                "capabilities": .object(["tools": .object([:])]),
                "serverInfo": serverInfo,
                "instructions": .string(Self.instructions)
            ]))

        case "server/discover":
            return Self.resultLine(id: id, .object([
                "resultType": .string("complete"),
                "supportedVersions": .array(Self.supportedVersions.map(JSONValue.string)),
                "capabilities": .object(["tools": .object([:])]),
                "_meta": .object(["io.modelcontextprotocol/serverInfo": serverInfo]),
                "instructions": .string(Self.instructions)
            ]))

        case "ping":
            return Self.resultLine(id: id, decorate(.object([:]), modern: modern))

        case "tools/list":
            return Self.resultLine(id: id, decorate(.object(["tools": Self.toolList()]), modern: modern))

        case "tools/call":
            guard let name = params["name"]?.stringValue, let tool = Self.tools.first(where: { $0.name == name }) else {
                return Self.errorLine(id: id, code: -32602, message: "Unknown tool: \(params["name"]?.stringValue ?? "none given")")
            }
            let arguments = params["arguments"]?.objectValue ?? [:]
            return Self.resultLine(id: id, decorate(run(tool, arguments), modern: modern))

        default:
            return Self.errorLine(id: id, code: -32601, message: "Method not found: \(method)")
        }
    }

    /// Runs a tool. A failure is a tool result with `isError`, so the model sees
    /// why (busy, no such account, invalid value) and can act on it.
    func run(_ tool: Tool, _ arguments: [String: JSONValue]) -> JSONValue {
        for key in tool.required where arguments[key] == nil || arguments[key] == .null {
            return Self.toolResult("invalidParams: \(tool.name) needs \(key).", isError: true)
        }
        do {
            let result = try call(tool.method, tool.params(arguments))
            return .object([
                "content": .array([.object(["type": .string("text"), "text": .string(result.prettyText)])]),
                "structuredContent": result.objectValue.map(JSONValue.object) ?? .object(["value": result]),
                "isError": .bool(false)
            ])
        } catch let error as ControlError {
            var text = "\(error.kind.rawValue): \(error.message)"
            if !error.candidates.isEmpty { text += "\nChoices: " + error.candidates.joined(separator: ", ") }
            return Self.toolResult(text, isError: true)
        } catch let error as ControlClient.ClientError {
            return Self.toolResult("unreachable: \(error.description)", isError: true)
        } catch {
            return Self.toolResult("failed: \(error.localizedDescription)", isError: true)
        }
    }

    private var serverInfo: JSONValue {
        .object(["name": .string(Self.serverName), "version": .string(serverVersion)])
    }

    private func decorate(_ result: JSONValue, modern: Bool) -> JSONValue {
        guard modern, case .object(var object) = result else { return result }
        object["resultType"] = .string("complete")
        object["_meta"] = .object(["io.modelcontextprotocol/serverInfo": serverInfo])
        return .object(object)
    }

    private static func toolResult(_ text: String, isError: Bool) -> JSONValue {
        .object(["content": .array([.object(["type": .string("text"), "text": .string(text)])]), "isError": .bool(isError)])
    }

    private static func resultLine(id: JSONValue, _ result: JSONValue) -> String {
        JSONValue.object(["jsonrpc": .string("2.0"), "id": id, "result": result]).compactText
    }

    private static func errorLine(id: JSONValue?, code: Int, message: String, data: JSONValue? = nil) -> String {
        var error: [String: JSONValue] = ["code": .number(Double(code)), "message": .string(message)]
        if let data { error["data"] = data }
        return JSONValue.object(["jsonrpc": .string("2.0"), "id": id ?? .null, "error": .object(error)]).compactText
    }
}

/// Runs `MCPServer` over stdin and stdout, connecting to the app on first use.
enum MCPStdio {
    static func run(serverVersion: String) -> Int32 {
        let client = ControlClient()
        var connected = false
        let server = MCPServer(serverVersion: serverVersion) { method, params in
            if !connected {
                try client.connect()
                try CLIRunner.checkProtocol(client, timeout: 30)
                connected = true
            }
            do {
                return try client.call(method, params, timeout: method == .usageRefresh || method == .accountsSwitch ? 120 : 30)
            } catch let error as ControlClient.ClientError {
                // The app may have quit or restarted: reconnect once and retry.
                connected = false
                if case .closed = error {
                    try client.connect()
                    connected = true
                    return try client.call(method, params, timeout: 120)
                }
                throw error
            }
        }
        setvbuf(stdout, nil, _IOLBF, 0)
        while let line = readLine(strippingNewline: true) {
            guard !line.trimmingCharacters(in: .whitespaces).isEmpty else { continue }
            if let reply = server.handle(line) {
                FileHandle.standardOutput.write(Data((reply + "\n").utf8))
            }
        }
        return 0
    }
}
```

Create `PixelSwitchCLI/PixelSwitchCommand.swift`:

```swift
import Foundation

/// `pixelswitch`, the command-line tool inside PixelSwitch.app
/// (`Contents/Helpers/pixelswitch`). Settings → Claude CLI → Install links it
/// into `~/.local/bin`. Not compiled into the unit harness (it has `@main`);
/// everything it calls is.
@main
struct PixelSwitchCommand {
    /// The version of the PixelSwitch.app this tool ships in, read from the
    /// app's Info.plist beside `Contents/Helpers/` (the link in ~/.local/bin
    /// is followed first). "unknown" when run outside the app.
    static var version: String {
        guard let executable = Bundle.main.executableURL?.resolvingSymlinksInPath() else { return "unknown" }
        let plist = executable.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Info.plist")
        let info = NSDictionary(contentsOf: plist)
        return info?["CFBundleShortVersionString"] as? String ?? "unknown"
    }

    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        let invocation: CLIInvocation
        do {
            invocation = try CLIParser.parse(arguments)
        } catch let error as CLIUsageError {
            FileHandle.standardError.write(Data(("error: " + error.message + "\n").utf8))
            exit(CLIExit.usage)
        } catch {
            exit(CLIExit.usage)
        }
        if invocation.command == .mcp {
            exit(MCPStdio.run(serverVersion: version))
        }
        exit(CLIRunner(client: ControlClient()).run(invocation))
    }
}
```

- [ ] **Step 4: Run the tests, then smoke-test the real binary**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `493 passed, 0 failed` (+18).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)` and `cli: type-check OK (6 files)`.

Build the tool with swiftc and try it without any app (the two environment variables keep it away from the real PixelSwitch):

```bash
swiftc -swift-version 6 -O PixelSwitchCLI/*.swift PixelSwitch/Control/ControlProtocol.swift -o /tmp/pixelswitch-smoke
export PIXELSWITCH_CONTROL_SOCKET=/tmp/pixelswitch-none.sock PIXELSWITCH_NO_LAUNCH=1
/tmp/pixelswitch-smoke --help | head -1; echo "exit $?"
/tmp/pixelswitch-smoke teleport; echo "exit $?"
/tmp/pixelswitch-smoke accounts remove 2; echo "exit $?"
/tmp/pixelswitch-smoke status; echo "exit $?"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}' '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_status"}}' | /tmp/pixelswitch-smoke mcp | wc -l
unset PIXELSWITCH_CONTROL_SOCKET PIXELSWITCH_NO_LAUNCH
```

Expected, in order: `pixelswitch: control PixelSwitch from the command line, or from an AI.` then `exit 0`; `error: Unknown command "teleport"…` then `exit 2`; `error: Removing an account deletes its saved login. Add --yes…` then `exit 2`; `error: PixelSwitch is not reachable: nothing is listening at /tmp/pixelswitch-none.sock` then `exit 5`; `3` (three replies: the notification gets none).

- [ ] **Step 5: Commit**

```bash
git add PixelSwitchCLI/MCPServer.swift PixelSwitchCLI/PixelSwitchCommand.swift Tests/UnitTests/ControlTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: pixelswitch mcp, for Claude on another Mac

18 tools over stdio in both MCP eras (modern 2026-07-28 with server/discover and
per-request _meta; legacy initialize). Refusals are tool errors the model can
read. The tool's entry point reads its version from the enclosing app.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Installing the tool into ~/.local/bin

**Files:**
- Create: `PixelSwitch/Control/CommandLineToolInstaller.swift`
- Modify: `Tests/UnitTests/ControlTests.swift` (one call line, one section)
- Modify: `Tests/run-unit-tests.sh` (one file line)

**Interfaces:**
- Consumes: `JSONValue` (Task 1).
- Produces: `enum CommandLineToolInstaller` (`enum Status { notInstalled, installed, linkedToOtherCopy(String), occupied }`, `enum InstallError { toolMissing, occupied }`, `defaultLinkPath`, `bundledToolPath(appBundle:)`, `status(linkPath:toolPath:)`, `install(linkPath:toolPath:) throws -> Status`, `isPixelSwitchTool(_:)`, `mcpConfiguration(host:linkPath:)`, `sshExample(host:linkPath:)`).


- [ ] **Step 1: Write the failing test**

In `runControlTests()`, add after `    runControlMCPTests()`:

```swift
    runControlInstallerTests()
```

Append this section, after one blank line:

```swift
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
```

In `Tests/run-unit-tests.sh`, insert before `  Tests/UnitTests/ControlTests.swift \`:

```bash
  PixelSwitch/Control/CommandLineToolInstaller.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error opening input file 'PixelSwitch/Control/CommandLineToolInstaller.swift'`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Control/CommandLineToolInstaller.swift`:

```swift
import Foundation

/// Links `~/.local/bin/pixelswitch` to the tool inside PixelSwitch.app
/// (`Contents/Helpers/pixelswitch`). A link, not a copy, so every Sparkle
/// update keeps it current. No admin password: `~/.local/bin` is the user's.
///
/// Never overwrites a file that is not PixelSwitch's own: a regular file, or
/// a link to something other than a PixelSwitch tool, is left alone.
enum CommandLineToolInstaller {
    enum Status: Equatable, Sendable {
        case notInstalled
        case installed
        /// A link to another copy of PixelSwitch's tool (an app that moved).
        case linkedToOtherCopy(String)
        /// Something that is not PixelSwitch's is already at that path.
        case occupied
    }

    enum InstallError: Error, Equatable, CustomStringConvertible {
        case toolMissing(String)
        case occupied(String)

        var description: String {
            switch self {
            case .toolMissing(let path): return "The command-line tool is missing from the app (\(path)). Reinstall PixelSwitch."
            case .occupied(let path): return "Something else is already at \(path). Move it away, then install again."
            }
        }
    }

    static var defaultLinkPath: String { NSHomeDirectory() + "/.local/bin/pixelswitch" }

    static func bundledToolPath(appBundle: URL = Bundle.main.bundleURL) -> String {
        appBundle.appendingPathComponent("Contents/Helpers/pixelswitch").path
    }

    static func status(linkPath: String = defaultLinkPath, toolPath: String = bundledToolPath()) -> Status {
        let files = FileManager.default
        if let destination = try? files.destinationOfSymbolicLink(atPath: linkPath) {
            if destination == toolPath { return .installed }
            return isPixelSwitchTool(destination) ? .linkedToOtherCopy(destination) : .occupied
        }
        return files.fileExists(atPath: linkPath) ? .occupied : .notInstalled
    }

    /// Creates (or repoints) the link. Returns the resulting status.
    @discardableResult
    static func install(linkPath: String = defaultLinkPath, toolPath: String = bundledToolPath()) throws -> Status {
        let files = FileManager.default
        guard files.isExecutableFile(atPath: toolPath) else { throw InstallError.toolMissing(toolPath) }
        switch status(linkPath: linkPath, toolPath: toolPath) {
        case .installed:
            return .installed
        case .occupied:
            throw InstallError.occupied(linkPath)
        case .linkedToOtherCopy:
            try files.removeItem(atPath: linkPath)
        case .notInstalled:
            break
        }
        try files.createDirectory(atPath: (linkPath as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        try files.createSymbolicLink(atPath: linkPath, withDestinationPath: toolPath)
        return status(linkPath: linkPath, toolPath: toolPath)
    }

    static func isPixelSwitchTool(_ path: String) -> Bool {
        path.hasSuffix(".app/Contents/Helpers/pixelswitch")
    }

    /// What to paste into Claude's MCP configuration on the other Mac. The
    /// path is absolute because a non-interactive SSH shell may not have
    /// `~/.local/bin` on its PATH.
    static func mcpConfiguration(host: String, linkPath: String = defaultLinkPath) -> String {
        let config: JSONValue = .object(["mcpServers": .object(["pixelswitch": .object([
            "command": .string("ssh"),
            "args": .array([.string(host), .string(linkPath), .string("mcp")])
        ])])])
        return config.prettyText
    }

    /// A one-line check to run on the other Mac.
    static func sshExample(host: String, linkPath: String = defaultLinkPath) -> String {
        "ssh \(host) \(linkPath) status"
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `505 passed, 0 failed` (+12).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (N files)` and `cli: type-check OK (6 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Control/CommandLineToolInstaller.swift Tests/UnitTests/ControlTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Remote control: link ~/.local/bin/pixelswitch into the app

A link, so Sparkle updates keep it current; someone else's file is never
overwritten; a link to another copy of PixelSwitch is repointed. Also builds the
MCP snippet and the SSH example for the Settings window.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: AppState reports why a switch or add failed

**Files:**
- Modify: `PixelSwitch/AppState.swift` (`isSwitching`, `isSwitchable`, `addAccount`, `switchTo`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `struct AppState.ActionError: Error, Equatable { enum Kind { busy, claudeUnavailable, noActiveAccount, failed }; kind; message }`; `func performAddCurrentAccount() async throws -> Account` (`@discardableResult`); `func performSwitch(to account: Account) async throws`; `private(set) var isSwitching`; internal `func isSwitchable(_ account: Account) -> Bool`. `addAccount()` and `switchTo(_:)` keep their signatures and their GUI behaviour.


`AppState` is not in the harness; this task is checked by the type-check and by the unchanged suite. Read the old and new `switchTo` side by side: every message and every silent case is kept; the only difference is that the body now throws instead of setting `errorMessage`, and the wrapper sets it.

- [ ] **Step 1: The edits**

In `PixelSwitch/AppState.swift`, replace:

```swift
    private var isSwitching = false
```

with:

```swift
    private(set) var isSwitching = false
```

In `PixelSwitch/AppState.swift`, replace:

```swift
    private func isSwitchable(_ account: Account) -> Bool {
```

with:

```swift
    func isSwitchable(_ account: Account) -> Bool {
```

In `PixelSwitch/AppState.swift`, replace:

```swift
    func addAccount() async {
        log.info("[addAccount] Starting add current account flow...")
        guard claudeAvailable else {
            errorMessage = String(localized: "Claude CLI not found", bundle: L10n.bundle)
            log.error("[addAccount] Aborted: Claude CLI not found")
            return
        }

        do {
            let status = try await claudeService.getAuthStatus()
            guard status.loggedIn else {
                errorMessage = String(localized: "Not logged in to Claude. Run 'claude auth login' first.", bundle: L10n.bundle)
                log.error("[addAccount] Aborted: not logged in")
                return
            }
            guard let email = status.email else {
                errorMessage = shadowedIdentityMessage(status)
                log.error("[addAccount] Aborted: CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
                return
            }
            log.info("[addAccount] Current auth: logged in, sub=\(status.subscriptionType ?? "nil")")

            if accounts.contains(where: { $0.email == email }) {
                errorMessage = String(localized: "Account already exists", bundle: L10n.bundle)
                log.warning("[addAccount] Aborted: duplicate account")
                return
            }

            var account = Account(
                email: email,
                displayName: status.orgName ?? email,
                provider: .claudeCode,
                orgName: status.orgName,
                subscriptionType: status.subscriptionType,
                isActive: accounts.isEmpty
            )
            log.info("[addAccount] Created account model, id=\(account.id)")

            log.info("[addAccount] Capturing token from keychain...")
            let captured = await claudeService.captureCurrentCredentials(for: account)
            if !captured {
                errorMessage = String(localized: "Could not capture auth token from keychain", bundle: L10n.bundle)
                log.error("[addAccount] Token capture failed!")
                return
            }
            log.info("[addAccount] Token captured successfully")

            if accounts.isEmpty {
                account.isActive = true
                activeAccount = account
                log.info("[addAccount] First account, setting as active")
            }

            accounts.append(account)
            saveAccounts()
            log.info("[addAccount] Account saved. Total accounts: \(self.accounts.count)")
        } catch {
            errorMessage = error.localizedDescription
            log.error("[addAccount] Error: \(error.localizedDescription)")
        }
    }
```

with:

```swift
    /// Why an action could not be done, for callers that need to know (the
    /// control API). The GUI's wrappers turn it into `errorMessage` as before.
    struct ActionError: Error, Equatable {
        enum Kind: Equatable { case busy, claudeUnavailable, noActiveAccount, failed }
        let kind: Kind
        let message: String
    }

    /// The popover's and Settings' "Add Current Account".
    func addAccount() async {
        do {
            _ = try await performAddCurrentAccount()
        } catch let error as ActionError {
            errorMessage = error.message
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Saves the account Claude Code is signed in as and returns it. Throws
    /// `ActionError` with the same messages the popover has always shown.
    @discardableResult
    func performAddCurrentAccount() async throws -> Account {
        log.info("[addAccount] Starting add current account flow...")
        guard claudeAvailable else {
            log.error("[addAccount] Aborted: Claude CLI not found")
            throw ActionError(kind: .claudeUnavailable, message: String(localized: "Claude CLI not found", bundle: L10n.bundle))
        }

        let status: AuthStatus
        do {
            status = try await claudeService.getAuthStatus()
        } catch {
            log.error("[addAccount] Error: \(error.localizedDescription)")
            throw ActionError(kind: .failed, message: error.localizedDescription)
        }
        guard status.loggedIn else {
            log.error("[addAccount] Aborted: not logged in")
            throw ActionError(kind: .failed, message: String(localized: "Not logged in to Claude. Run 'claude auth login' first.", bundle: L10n.bundle))
        }
        guard let email = status.email else {
            log.error("[addAccount] Aborted: CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
            throw ActionError(kind: .failed, message: shadowedIdentityMessage(status))
        }
        log.info("[addAccount] Current auth: logged in, sub=\(status.subscriptionType ?? "nil")")

        if accounts.contains(where: { $0.email == email }) {
            log.warning("[addAccount] Aborted: duplicate account")
            throw ActionError(kind: .failed, message: String(localized: "Account already exists", bundle: L10n.bundle))
        }

        var account = Account(
            email: email,
            displayName: status.orgName ?? email,
            provider: .claudeCode,
            orgName: status.orgName,
            subscriptionType: status.subscriptionType,
            isActive: accounts.isEmpty
        )
        log.info("[addAccount] Created account model, id=\(account.id)")

        log.info("[addAccount] Capturing token from keychain...")
        let captured = await claudeService.captureCurrentCredentials(for: account)
        if !captured {
            log.error("[addAccount] Token capture failed!")
            throw ActionError(kind: .failed, message: String(localized: "Could not capture auth token from keychain", bundle: L10n.bundle))
        }
        log.info("[addAccount] Token captured successfully")

        if accounts.isEmpty {
            account.isActive = true
            activeAccount = account
            log.info("[addAccount] First account, setting as active")
        }

        accounts.append(account)
        saveAccounts()
        log.info("[addAccount] Account saved. Total accounts: \(self.accounts.count)")
        return account
    }
```

In `PixelSwitch/AppState.swift`, replace:

```swift
    func switchTo(_ account: Account) async {
        guard let currentActive = activeAccount else {
            log.info("[switchTo] No switch needed (no active account)")
            return
        }

        // One credential mutation at a time: a switch already in flight (its
        // awaits leave the main actor free) or a running login must finish
        // before another switch may touch the keychain and ~/.claude.json.
        guard !isSwitching, !isLoggingIn else {
            log.warning("[switchTo] Skipped: another switch or a login is in progress")
            return
        }
```

with:

```swift
    /// The popover's Switch and double-click, and auto-switch.
    func switchTo(_ account: Account) async {
        do {
            try await performSwitch(to: account)
        } catch let error as ActionError {
            switch error.kind {
            case .failed, .claudeUnavailable: errorMessage = error.message
            case .busy, .noActiveAccount: break  // logged; the GUI has always stayed quiet here
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Switches to `account` and returns once the switch has finished (or was
    /// not needed). Throws `ActionError`; the messages are the ones the popover
    /// has always shown.
    func performSwitch(to account: Account) async throws {
        guard let currentActive = activeAccount else {
            log.info("[switchTo] No switch needed (no active account)")
            throw ActionError(kind: .noActiveAccount, message: String(localized: "No account is active yet, so there is nothing to switch from. Add the current account first.", bundle: L10n.bundle))
        }

        // One credential mutation at a time: a switch already in flight (its
        // awaits leave the main actor free) or a running login must finish
        // before another switch may touch the keychain and ~/.claude.json.
        guard !isSwitching, !isLoggingIn else {
            log.warning("[switchTo] Skipped: another switch or a login is in progress")
            throw ActionError(kind: .busy, message: String(localized: "A switch or sign-in is in progress. Try again in a moment.", bundle: L10n.bundle))
        }
```

In `PixelSwitch/AppState.swift`, replace:

```swift
        case .failure(let problem):
            log.error("[switchTo] ABORT: \(problem.message)")
            errorMessage = problem.message
            return
        }
```

with:

```swift
        case .failure(let problem):
            log.error("[switchTo] ABORT: \(problem.message)")
            throw ActionError(kind: .failed, message: problem.message)
        }
```

In `PixelSwitch/AppState.swift`, replace:

```swift
            log.info("[switchTo] ===== Switch completed =====")
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
            log.error("[switchTo] Switch failed: \(error.localizedDescription)")
        }
    }
```

with:

```swift
            log.info("[switchTo] ===== Switch completed =====")
        } catch {
            isLoading = false
            log.error("[switchTo] Switch failed: \(error.localizedDescription)")
            throw ActionError(kind: .failed, message: error.localizedDescription)
        }
    }
```


- [ ] **Step 2: Verify**

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK` and `cli: type-check OK (6 files)`.

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `505 passed, 0 failed`.

- [ ] **Step 3: Commit**

```bash
git add PixelSwitch/AppState.swift
git commit -m "$(cat <<'EOF'
AppState: throwing switch and add-current-account for remote callers

performSwitch(to:) and performAddCurrentAccount() throw ActionError with the
messages the popover has always shown; switchTo and addAccount are wrappers, so
the GUI behaves exactly as before.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Settings store, app controller, events, and starting it all at launch

**Files:**
- Create: `PixelSwitch/Control/SettingsStore.swift`
- Create: `PixelSwitch/Control/AppController.swift`
- Create: `PixelSwitch/Control/RemoteControlService.swift` (also holds `ControlEventHub`)
- Modify: `PixelSwitch/PixelSwitchApp.swift` (start remote control)
- Modify: `PixelSwitch/Views/SettingsView.swift` (side effects through `SettingsStore`)
- Modify: `PixelSwitch/Views/ClaudeCLITabView.swift` (`applyPreference`)

**Interfaces:**
- Consumes: Tasks 1–8; Part A's and Part B's `AppState` members (Global Constraints); `UpdateChecker`, `MenuBarConfig`, `EmailDisplay`, `AccountColorCoding`, `ClaudeService.setPath`, `kClaudeBinaryPathPreferenceKey`.
- Produces: `@MainActor final class SettingsStore` (`shared`, `weak var appState`, `value(_:)`, `set(_:to:) throws`, `refreshIntervalChanged(_:)`, `lookbackChanged()`, `applyLanguage(_:)`, `launchAtLogin`, `setLaunchAtLogin(_:) throws`, `applyClaudeBinaryPath(_:)`); `@MainActor final class AppController: AppControlling`; `@MainActor final class RemoteControlService: ObservableObject` (`shared`, `enum State { stopped, listening(path:), failed(String) }`, `@Published state`, `start(appState:updateChecker:)`, `stop()`); `@MainActor final class ControlEventHub`.


These are app-side (AppKit, SwiftUI, ServiceManagement, `AppState`), so they are checked by the type-check here and by the integration script in Task 13.

- [ ] **Step 1: The settings store**

Create `PixelSwitch/Control/SettingsStore.swift`:

```swift
import Foundation
import ServiceManagement

/// Reads and writes every setting in `SettingKey`, and owns each one's side
/// effect, so a change from the command line takes effect exactly as the same
/// change in the Settings window. The window's `onChange` handlers call the
/// same side-effect methods here; `@AppStorage` keeps the window in sync.
@MainActor
final class SettingsStore {
    static let shared = SettingsStore()

    /// Set once at launch (`RemoteControlService.start`).
    weak var appState: AppState?

    private let defaults = UserDefaults.standard

    private init() {}

    // MARK: - Reading

    func value(_ key: SettingKey) -> JSONValue {
        let menuBar = MenuBarConfig.shared
        switch key {
        case .refreshInterval:
            return .number(defaults.object(forKey: key.rawValue) as? Double ?? 300)
        case .transcriptLookbackHours:
            return .number((defaults.object(forKey: key.rawValue) as? NSNumber)?.doubleValue ?? 24)
        case .autoSwitchEnabled:
            return .bool(defaults.bool(forKey: AutoSwitchSettings.enabledKey))
        case .autoSwitchThreshold:
            return .number(AutoSwitchSettings.defaultThreshold)
        case .autoSwitchOnFable:
            return .bool(AutoSwitchFableSetting.isOn)
        case .autoSwitchStrategy:
            return .string(AutoSwitchSettings.strategy.rawValue)
        case .autoSwitchDrainEarly:
            return .bool(AutoSwitchSettings.drainEarly)
        case .autoSwitchDrainWithinHours:
            return .number(AutoSwitchSettings.drainWithinHours)
        case .maskEmailAddresses:
            return .bool(EmailDisplay.isMasked)
        case .colorCodeAccounts:
            return .bool(AccountColorCoding.isOn)
        case .appLanguage:
            return .string(defaults.string(forKey: key.rawValue) ?? "auto")
        case .launchAtLogin:
            return .bool(launchAtLogin)
        case .menuBarShowsHeadIcon:
            return .bool(menuBar.showsHeadIcon)
        case .menuBarModules:
            return .array(menuBar.modules.map { .string($0.rawValue) })
        case .menuBarCustomizesLimitBarColors:
            return .bool(menuBar.customizesLimitBarColors)
        case .menuBarColorSession:
            return .string(menuBar.sessionLimitBarColorHex)
        case .menuBarColorWeekly:
            return .string(menuBar.weeklyLimitBarColorHex)
        case .menuBarColorFable:
            return .string(menuBar.fableLimitBarColorHex)
        case .menuBarColorLowRemaining:
            return .string(menuBar.lowRemainingLimitBarColorHex)
        case .menuBarLowRemainingWarningThreshold:
            return .number(menuBar.lowRemainingWarningThreshold)
        case .claudeBinaryPath:
            return .string(defaults.string(forKey: kClaudeBinaryPathPreferenceKey) ?? "")
        }
    }

    // MARK: - Writing

    /// Writes an already normalized value (`SettingKey.normalize`) and applies
    /// its side effect. Throws `ControlError` when the value cannot be applied.
    func set(_ key: SettingKey, to value: JSONValue) throws {
        let menuBar = MenuBarConfig.shared
        switch key {
        case .refreshInterval:
            guard let seconds = value.doubleValue else { throw wrongShape(key) }
            defaults.set(seconds, forKey: key.rawValue)
            refreshIntervalChanged(seconds)
        case .transcriptLookbackHours:
            guard let hours = value.doubleValue else { throw wrongShape(key) }
            defaults.set(Int(hours), forKey: key.rawValue)
            lookbackChanged()
        case .autoSwitchEnabled:
            defaults.set(try bool(value, key), forKey: AutoSwitchSettings.enabledKey)
        case .autoSwitchThreshold:
            guard let number = value.doubleValue else { throw wrongShape(key) }
            defaults.set(number, forKey: AutoSwitchSettings.thresholdKey)
        case .autoSwitchOnFable:
            defaults.set(try bool(value, key), forKey: AutoSwitchSettings.onFableKey)
        case .autoSwitchStrategy:
            guard let name = value.stringValue else { throw wrongShape(key) }
            defaults.set(name, forKey: AutoSwitchSettings.strategyKey)
        case .autoSwitchDrainEarly:
            defaults.set(try bool(value, key), forKey: AutoSwitchSettings.drainEarlyKey)
        case .autoSwitchDrainWithinHours:
            guard let hours = value.doubleValue else { throw wrongShape(key) }
            defaults.set(hours, forKey: AutoSwitchSettings.drainWithinHoursKey)
        case .maskEmailAddresses:
            defaults.set(try bool(value, key), forKey: EmailDisplay.key)
        case .colorCodeAccounts:
            defaults.set(try bool(value, key), forKey: AccountColorCoding.key)
        case .appLanguage:
            guard let language = value.stringValue else { throw wrongShape(key) }
            defaults.set(language, forKey: key.rawValue)
            applyLanguage(language)
        case .launchAtLogin:
            try setLaunchAtLogin(try bool(value, key))
        case .menuBarShowsHeadIcon:
            menuBar.showsHeadIcon = try bool(value, key)
        case .menuBarModules:
            guard let names = value.arrayValue?.compactMap(\.stringValue) else { throw wrongShape(key) }
            menuBar.set(names.compactMap(MenuBarModule.init(rawValue:)))
        case .menuBarCustomizesLimitBarColors:
            menuBar.customizesLimitBarColors = try bool(value, key)
        case .menuBarColorSession:
            menuBar.sessionLimitBarColorHex = try string(value, key)
        case .menuBarColorWeekly:
            menuBar.weeklyLimitBarColorHex = try string(value, key)
        case .menuBarColorFable:
            menuBar.fableLimitBarColorHex = try string(value, key)
        case .menuBarColorLowRemaining:
            menuBar.lowRemainingLimitBarColorHex = try string(value, key)
        case .menuBarLowRemainingWarningThreshold:
            guard let number = value.doubleValue else { throw wrongShape(key) }
            menuBar.lowRemainingWarningThreshold = number
        case .claudeBinaryPath:
            let path = try string(value, key)
            guard path.isEmpty || FileManager.default.isExecutableFile(atPath: path) else {
                throw ControlError(.invalidValue, "No executable file at \(path).")
            }
            applyClaudeBinaryPath(path)
        }
    }

    // MARK: - Side effects, shared with the Settings window

    /// Settings → General → Auto-refresh interval.
    func refreshIntervalChanged(_ seconds: Double) {
        appState?.startAutoRefresh(interval: seconds)
    }

    /// Settings → General → Usage history window.
    func lookbackChanged() {
        guard let appState else { return }
        Task { await appState.refresh() }
    }

    /// Settings → General → Language: takes effect in the menu bar at once
    /// (`PixelSwitchApp` follows the key) and everywhere else on next launch.
    func applyLanguage(_ language: String) {
        if language == "auto" {
            defaults.removeObject(forKey: "AppleLanguages")
        } else {
            defaults.set([language], forKey: "AppleLanguages")
        }
    }

    var launchAtLogin: Bool {
        SMAppService.mainApp.status == .enabled
    }

    /// Settings → General → Launch at login.
    func setLaunchAtLogin(_ enable: Bool) throws {
        guard enable != launchAtLogin else { return }
        do {
            if enable {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            throw ControlError(.failed, "macOS refused to change Launch at login: \(error.localizedDescription)")
        }
    }

    /// Settings → Claude CLI → Binary path. "" means auto-detect.
    func applyClaudeBinaryPath(_ path: String) {
        defaults.set(path, forKey: kClaudeBinaryPathPreferenceKey)
        ClaudeService.shared.setPath(path.isEmpty ? nil : path)
        lookbackChanged()
    }

    // MARK: - Helpers

    private func bool(_ value: JSONValue, _ key: SettingKey) throws -> Bool {
        guard let bool = value.boolValue else { throw wrongShape(key) }
        return bool
    }

    private func string(_ value: JSONValue, _ key: SettingKey) throws -> String {
        guard let text = value.stringValue else { throw wrongShape(key) }
        return text
    }

    private func wrongShape(_ key: SettingKey) -> ControlError {
        ControlError(.invalidValue, "\(key.rawValue) must be \(key.summary).")
    }
}
```

- [ ] **Step 2: The controller**

Create `PixelSwitch/Control/AppController.swift`:

```swift
import AppKit
import Combine

/// The real `AppControlling`: the control API's view of the running app.
/// Every action goes through the same `AppState` methods the popover and the
/// Settings window use, so the GUI shows each change at once. Nothing here
/// reads or returns a login token.
@MainActor
final class AppController: AppControlling {
    private let appState: AppState
    private let settings: SettingsStore
    private let updateChecker: UpdateChecker
    /// Opens a sign-in's automatic link when it arrives, if the caller asked.
    private var openWhenLinkArrives: AnyCancellable?

    init(appState: AppState, settings: SettingsStore = .shared, updateChecker: UpdateChecker) {
        self.appState = appState
        self.settings = settings
        self.updateChecker = updateChecker
    }

    // MARK: - Reading

    var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
    }

    var claudeAvailable: Bool { appState.claudeAvailable }
    var lastUsageRefresh: Date? { appState.lastUsageRefresh }
    var accounts: [Account] { appState.accounts }

    var autoSwitch: AutoSwitchInfo {
        AutoSwitchInfo(
            enabled: UserDefaults.standard.bool(forKey: AutoSwitchSettings.enabledKey),
            defaultThreshold: AutoSwitchSettings.defaultThreshold,
            onFable: AutoSwitchFableSetting.isOn,
            strategy: AutoSwitchSettings.strategy.rawValue,
            drainEarly: AutoSwitchSettings.drainEarly,
            drainWithinHours: AutoSwitchSettings.drainWithinHours
        )
    }

    var machineUsage: MachineUsageInfo {
        let cost = appState.costSummary
        let activity = appState.activityStats
        return MachineUsageInfo(
            todayCost: cost.todayCost,
            totalCost: cost.totalCost,
            conversationTurns: activity.conversationTurns,
            activeCodingMinutes: activity.activeCodingMinutes,
            linesWritten: activity.linesWritten,
            modelUsage: activity.modelUsage
        )
    }

    var signIn: SignInInfo? {
        appState.currentSignIn.map(ControlSnapshots.signInInfo)
    }

    func usage(for id: UUID) -> UsageAPIResponse? { appState.accountUsage[id] }
    func usageSampledAt(for id: UUID) -> Date? { appState.accountUsageSampledAt[id] }
    func usageError(for id: UUID) -> String? { appState.accountUsageErrors[id]?.message }

    func isSwitchable(_ id: UUID) -> Bool {
        guard let account = appState.accounts.first(where: { $0.id == id }) else { return false }
        return appState.isSwitchable(account)
    }

    func effectiveSwitchThreshold(for account: Account) -> Double {
        appState.effectiveSwitchThreshold(for: account)
    }

    // MARK: - Actions

    func switchAccount(to id: UUID) async throws {
        let account = try self.account(id)
        do {
            try await appState.performSwitch(to: account)
        } catch let error as AppState.ActionError {
            throw Self.controlError(error)
        }
    }

    func addCurrentAccount() async throws -> UUID {
        try refuseWhileBusy()
        do {
            return try await appState.performAddCurrentAccount().id
        } catch let error as AppState.ActionError {
            throw Self.controlError(error)
        }
    }

    func startSignIn(_ purpose: SignInPurpose, open: String?) throws -> SignInInfo {
        var browser: BrowserApp?
        var openIt = false
        if let open {
            openIt = true
            if open.caseInsensitiveCompare("default") != .orderedSame {
                let installed = BrowserOpener.installedBrowsers()
                guard let match = installed.first(where: {
                    $0.name.caseInsensitiveCompare(open) == .orderedSame || $0.id.caseInsensitiveCompare(open) == .orderedSame
                }) else {
                    throw ControlError(.invalidValue, "No browser called \"\(open)\" is installed on this Mac.",
                                       candidates: ["default"] + installed.map(\.name))
                }
                browser = match
            }
        }
        let session: SignInSession
        do {
            session = try appState.startSignIn(purpose)
        } catch let error as AppState.SignInStartError {
            switch error {
            case .busy: throw ControlError(.busy, "A switch or sign-in is in progress. Try again in a moment.")
            case .claudeUnavailable: throw ControlError(.claudeUnavailable, "The claude command could not be found on this Mac.")
            }
        }
        if openIt {
            openWhenLinkArrives = session.$automaticLink
                .compactMap { $0 }
                .first()
                .receive(on: DispatchQueue.main)
                .sink { url in MainActor.assumeIsolated { _ = BrowserOpener.open(url, in: browser) } }
        }
        return ControlSnapshots.signInInfo(session)
    }

    func submitSignInCode(_ code: String) throws -> SignInInfo {
        guard let session = appState.currentSignIn, !session.state.isFinished else {
            throw ControlError(.notFound, "No sign-in is running.")
        }
        guard session.state == .waitingForUser, !session.codeSubmitted else {
            throw ControlError(.invalidValue, "This sign-in is not waiting for a code.")
        }
        guard session.submitCode(code) else {
            throw ControlError(.invalidValue, "That isn't the whole code. Copy all of it, including the part after #.")
        }
        return ControlSnapshots.signInInfo(session)
    }

    func cancelSignIn() throws -> SignInInfo {
        guard let session = appState.currentSignIn, !session.state.isFinished else {
            throw ControlError(.notFound, "No sign-in is running.")
        }
        guard session.state != .completing else {
            throw ControlError(.busy, "The account is being saved and cannot be cancelled now.")
        }
        session.cancel()
        return ControlSnapshots.signInInfo(session)
    }

    func removeAccount(_ id: UUID) throws {
        let account = try self.account(id)
        try refuseWhileBusy()
        appState.removeAccount(account)
    }

    func setLabel(_ label: String?, for id: UUID) {
        guard let account = try? self.account(id) else { return }
        appState.updateAccountLabel(account, label: label)
    }

    func setSwitchThreshold(_ threshold: Double?, for id: UUID) {
        guard let account = try? self.account(id) else { return }
        appState.setSwitchThreshold(threshold, for: account)
    }

    func setAccountOrder(_ ids: [UUID]) -> Bool {
        appState.setAccountOrder(ids)
    }

    func refreshUsage() async {
        await appState.refresh()
    }

    func settingValue(_ key: SettingKey) -> JSONValue {
        settings.value(key)
    }

    func setSetting(_ key: SettingKey, to value: JSONValue) throws {
        try settings.set(key, to: value)
    }

    func checkForUpdates() {
        updateChecker.checkForUpdates(manual: true)
    }

    /// Quits after the reply has had time to reach the caller.
    func quit() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
    }

    // MARK: - Helpers

    private func account(_ id: UUID) throws -> Account {
        guard let account = appState.accounts.first(where: { $0.id == id }) else {
            throw ControlError(.notFound, "That account no longer exists.")
        }
        return account
    }

    private func refuseWhileBusy() throws {
        if appState.isSwitching || appState.isLoggingIn {
            throw ControlError(.busy, "A switch or sign-in is in progress. Try again in a moment.")
        }
    }

    private static func controlError(_ error: AppState.ActionError) -> ControlError {
        switch error.kind {
        case .busy: return ControlError(.busy, error.message)
        case .claudeUnavailable: return ControlError(.claudeUnavailable, error.message)
        case .noActiveAccount, .failed: return ControlError(.failed, error.message)
        }
    }
}
```

- [ ] **Step 3: The service and the event hub**

Create `PixelSwitch/Control/RemoteControlService.swift`:

```swift
import AppKit
import Combine

private let controlLog = FileLog("Control")

/// Starts remote control at launch and stops it at quit: the socket server,
/// the API behind it, and the events pushed to `pixelswitch watch`. Always on
/// (the founder's answer, 2026-09-25): anything running as this Mac's user can
/// use it; nothing outside this Mac can reach it.
@MainActor
final class RemoteControlService: ObservableObject {
    static let shared = RemoteControlService()

    enum State: Equatable {
        case stopped
        case listening(path: String)
        case failed(String)
    }

    @Published private(set) var state: State = .stopped

    private var server: ControlServer?
    private var api: ControlAPI?
    private var hub: ControlEventHub?
    private var quitObserver: NSObjectProtocol?

    private init() {}

    func start(appState: AppState, updateChecker: UpdateChecker) {
        guard server == nil else { return }
        SettingsStore.shared.appState = appState
        let api = ControlAPI(controller: AppController(appState: appState, updateChecker: updateChecker),
                             log: { controlLog.info($0) })
        let server = ControlServer(
            path: ControlProtocol.socketPath(),
            handler: { line, connection in
                await api.handle(line, onSubscribe: { connection.markSubscribed() })
            },
            log: { controlLog.info($0) }
        )
        do {
            try server.start()
        } catch {
            let message = (error as? ControlServer.StartError)?.description ?? error.localizedDescription
            controlLog.error("[control] Not started: \(message)")
            state = .failed(message)
            return
        }
        self.api = api
        self.server = server
        hub = ControlEventHub(appState: appState) { [weak server] event in
            guard let server, let params = try? JSONValue.encode(event) else { return }
            let line = RPCNotification(method: ControlEvent.notificationMethod, params: params).line
            server.subscribers().forEach { $0.send(line) }
        }
        state = .listening(path: server.path)
        quitObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { _ in
            MainActor.assumeIsolated { RemoteControlService.shared.stop() }
        }
    }

    func stop() {
        server?.stop()
        server = nil
        api = nil
        hub = nil
        state = .stopped
    }
}

/// Pushes what changes in the app to every `events.subscribe` connection.
@MainActor
final class ControlEventHub {
    private var subscriptions = Set<AnyCancellable>()
    private var signInSubscription: AnyCancellable?
    private let send: @MainActor (ControlEvent) -> Void

    init(appState: AppState, send: @escaping @MainActor (ControlEvent) -> Void) {
        self.send = send

        appState.$activeAccount
            .map { $0?.id }
            .removeDuplicates()
            .dropFirst()
            .sink { [weak self, weak appState] id in
                let email = appState?.accounts.first(where: { $0.id == id })?.email
                self?.emit("activeAccountChanged", ["accountId": id.map { .string($0.uuidString) } ?? .null,
                                                    "email": email.map(JSONValue.string) ?? .null])
            }
            .store(in: &subscriptions)

        appState.$lastUsageRefresh
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] _ in self?.emit("usageUpdated", [:]) }
            .store(in: &subscriptions)

        appState.$lastAutoSwitch
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] record in
                self?.emit("autoSwitched", ["from": .string(record.from.uuidString), "to": .string(record.to.uuidString),
                                            "limit": .string(record.limit.rawValue), "trigger": .string(record.trigger.rawValue)])
            }
            .store(in: &subscriptions)

        appState.$currentSignIn
            .sink { [weak self] session in self?.follow(session) }
            .store(in: &subscriptions)

        appState.$errorMessage
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] message in self?.emit("error", ["message": .string(message)]) }
            .store(in: &subscriptions)
    }

    /// One `signInChanged` per change of state or link, read after the change lands.
    private func follow(_ session: SignInSession?) {
        guard let session else { signInSubscription = nil; return }
        signInSubscription = session.objectWillChange
            .receive(on: DispatchQueue.main)
            .map { [weak session] _ in session.map(ControlSnapshots.signInInfo) }
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] info in
                guard let data = try? JSONValue.encode(info) else { return }
                self?.emit("signInChanged", ["signIn": data])
            }
    }

    private func emit(_ type: String, _ data: [String: JSONValue]) {
        send(ControlEvent(type: type, at: Date(), data: .object(data)))
    }
}
```

- [ ] **Step 4: Start it at launch, and route the Settings window's side effects through the store**

In `PixelSwitch/PixelSwitchApp.swift`, replace:

```swift
                    signInWindowController.install(appState: appState, locale: currentLocale)
```

with:

```swift
                    signInWindowController.install(appState: appState, locale: currentLocale)
                    RemoteControlService.shared.start(appState: appState, updateChecker: updateChecker)
```

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
import SwiftUI
import ServiceManagement
```

with:

```swift
import SwiftUI
```

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
        .onAppear {
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
```

with:

```swift
        .onAppear {
            launchAtLogin = SettingsStore.shared.launchAtLogin
        }
```

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
                .onChange(of: refreshInterval) { _, newValue in
                    appState.startAutoRefresh(interval: newValue)
                }
```

with:

```swift
                .onChange(of: refreshInterval) { _, newValue in
                    SettingsStore.shared.refreshIntervalChanged(newValue)
                }
```

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
                .onChange(of: transcriptLookbackHours) { _, _ in
                    Task { await appState.refresh() }
                }
```

with:

```swift
                .onChange(of: transcriptLookbackHours) { _, _ in
                    SettingsStore.shared.lookbackChanged()
                }
```

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
                .onChange(of: appLanguage) { _, newValue in
                    applyLanguage(newValue)
                }
```

with:

```swift
                .onChange(of: appLanguage) { _, newValue in
                    SettingsStore.shared.applyLanguage(newValue)
                }
```

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
    private func applyLanguage(_ lang: String) {
        // Set AppleLanguages for next launch; .environment(\.locale) handles live update
        if lang == "auto" {
            UserDefaults.standard.removeObject(forKey: "AppleLanguages")
        } else {
            UserDefaults.standard.set([lang], forKey: "AppleLanguages")
        }
    }

    private func toggleLaunchAtLogin(_ enable: Bool) {
        do {
            if enable {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            launchAtLogin = !enable // revert on failure
        }
    }
```

with:

```swift
    private func toggleLaunchAtLogin(_ enable: Bool) {
        do {
            try SettingsStore.shared.setLaunchAtLogin(enable)
        } catch {
            launchAtLogin = !enable // revert on failure
        }
    }
```

In `PixelSwitch/Views/ClaudeCLITabView.swift`, replace:

```swift
    /// Write the preference, push to ClaudeService, refresh app + versions.
    /// `newValue == ""` means revert to auto.
    private func applyPreference(_ newValue: String) {
        preference = newValue
        ClaudeService.shared.setPath(newValue.isEmpty ? nil : newValue)
        Task {
            await reloadVersions()
            await appState.refresh()
        }
    }
```

with:

```swift
    /// Write the preference, push to ClaudeService, refresh app + versions.
    /// `newValue == ""` means revert to auto. `SettingsStore` does the same
    /// for a change made from the command line.
    private func applyPreference(_ newValue: String) {
        preference = newValue
        SettingsStore.shared.applyClaudeBinaryPath(newValue)
        Task { await reloadVersions() }
    }
```


- [ ] **Step 5: Verify**

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK` and `cli: type-check OK (6 files)`.

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `505 passed, 0 failed`.

Run: `grep -rn "KeychainService\|readClaudeToken\|getAccountBackup\|refreshOAuthCredentials" PixelSwitch/Control PixelSwitchCLI`
Expected: no output (nothing in remote control can reach a credential).

- [ ] **Step 6: Commit**

```bash
git add PixelSwitch/Control/SettingsStore.swift PixelSwitch/Control/AppController.swift PixelSwitch/Control/RemoteControlService.swift PixelSwitch/PixelSwitchApp.swift PixelSwitch/Views/SettingsView.swift PixelSwitch/Views/ClaudeCLITabView.swift
git commit -m "$(cat <<'EOF'
Remote control: the app side, always on at launch

AppController maps the API onto AppState; SettingsStore owns every setting and
its side effect, which the Settings window now shares; ControlEventHub pushes
switches, refreshes, auto-switches, sign-in changes and errors to subscribers.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Settings → Claude CLI → Command line & AI, and the strings

**Files:**
- Create: `PixelSwitch/Views/RemoteControlSection.swift`
- Modify: `PixelSwitch/Views/ClaudeCLITabView.swift` (add the section)
- Modify: the five `PixelSwitch/<lang>.lproj/Localizable.strings` files

**Interfaces:**
- Consumes: `RemoteControlService`, `CommandLineToolInstaller`, `L10n`.
- Produces: `struct RemoteControlSection: View`.


- [ ] **Step 1: The section**

Create `PixelSwitch/Views/RemoteControlSection.swift`:

```swift
import SwiftUI
import AppKit
import SystemConfiguration

/// Settings → Claude CLI → "Command line & AI": installs the `pixelswitch`
/// tool, says whether remote control is listening, and gives the MCP snippet
/// for Claude on another Mac.
struct RemoteControlSection: View {
    @ObservedObject private var service = RemoteControlService.shared
    @State private var status = CommandLineToolInstaller.status()
    @State private var installError: String?
    @State private var copied = false

    var body: some View {
        Section("Command line & AI") {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Command-line tool")
                    Text(statusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button(status == .installed ? "Reinstall" : "Install") { install() }
                    .disabled(status == .installed)
            }
            if let installError {
                Label(installError, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            HStack(spacing: 6) {
                Image(systemName: isListening ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(isListening ? .green : .red)
                Text(listeningText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(verbatim: snippet)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
                HStack {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(snippet, forType: .string)
                        copied = true
                    } label: {
                        copied ? Text("Copied") : Text("Copy")
                    }
                    Spacer()
                    Text(verbatim: CommandLineToolInstaller.sshExample(host: host))
                        .font(.caption2.monospaced())
                        .foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
        }
        .onAppear { status = CommandLineToolInstaller.status() }
    }

    private var isListening: Bool {
        if case .listening = service.state { return true }
        return false
    }

    private var statusText: String {
        let path = CommandLineToolInstaller.defaultLinkPath.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        switch status {
        case .installed: return String(localized: "Installed at \(path).", bundle: L10n.bundle)
        case .notInstalled: return String(localized: "Not installed. Install puts `pixelswitch` at \(path).", bundle: L10n.bundle)
        case .linkedToOtherCopy: return String(localized: "\(path) points to another copy of PixelSwitch. Install points it here.", bundle: L10n.bundle)
        case .occupied: return String(localized: "Something else is already at \(path), so it was left alone.", bundle: L10n.bundle)
        }
    }

    private var listeningText: String {
        switch service.state {
        case .listening: return String(localized: "Remote control is on. Only this Mac's user can connect; nothing is open to the network.", bundle: L10n.bundle)
        case .stopped: return String(localized: "Remote control is not running.", bundle: L10n.bundle)
        case .failed(let why): return String(localized: "Remote control could not start: \(why)", bundle: L10n.bundle)
        }
    }

    /// This Mac's Bonjour name, as other Macs on the network reach it.
    private var host: String {
        if let name = SCDynamicStoreCopyLocalHostName(nil) as String? { return name + ".local" }
        return ProcessInfo.processInfo.hostName
    }

    private var snippet: String {
        CommandLineToolInstaller.mcpConfiguration(host: host)
    }

    private func install() {
        do {
            status = try CommandLineToolInstaller.install()
            installError = nil
        } catch let error as CommandLineToolInstaller.InstallError {
            installError = error.description
            status = CommandLineToolInstaller.status()
        } catch {
            installError = error.localizedDescription
        }
    }
}
```

- [ ] **Step 2: Add it to the tab**

In `PixelSwitch/Views/ClaudeCLITabView.swift`, replace:

```swift
                .disabled(isLoading)
            }
        }
        .formStyle(.grouped)
```

with:

```swift
                .disabled(isLoading)
            }

            RemoteControlSection()
        }
        .formStyle(.grouped)
```


- [ ] **Step 3: The strings, in all five languages**

None of these keys exists today, and none clashes with Part A's or Part B's (checked when this plan was generated). The last two are Task 8's messages.

Append to the end of `PixelSwitch/en.lproj/Localizable.strings`, after one blank line:

```text
/* Remote control (1.2) */
"Command line & AI" = "Command line & AI";
"Command-line tool" = "Command-line tool";
"Install" = "Install";
"Reinstall" = "Reinstall";
"Copy" = "Copy";
"Installed at %@." = "Installed at %@.";
"Not installed. Install puts `pixelswitch` at %@." = "Not installed. Install puts `pixelswitch` at %@.";
"%@ points to another copy of PixelSwitch. Install points it here." = "%@ points to another copy of PixelSwitch. Install points it here.";
"Something else is already at %@, so it was left alone." = "Something else is already at %@, so it was left alone.";
"Remote control is on. Only this Mac's user can connect; nothing is open to the network." = "Remote control is on. Only this Mac's user can connect; nothing is open to the network.";
"Remote control is not running." = "Remote control is not running.";
"Remote control could not start: %@" = "Remote control could not start: %@";
"To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH." = "To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH.";
"No account is active yet, so there is nothing to switch from. Add the current account first." = "No account is active yet, so there is nothing to switch from. Add the current account first.";
"A switch or sign-in is in progress. Try again in a moment." = "A switch or sign-in is in progress. Try again in a moment.";
```

Append to the end of `PixelSwitch/de.lproj/Localizable.strings`, after one blank line:

```text
/* Remote control (1.2) */
"Command line & AI" = "Befehlszeile & KI";
"Command-line tool" = "Befehlszeilenwerkzeug";
"Install" = "Installieren";
"Reinstall" = "Neu installieren";
"Copy" = "Kopieren";
"Installed at %@." = "Installiert unter %@.";
"Not installed. Install puts `pixelswitch` at %@." = "Nicht installiert. „Installieren“ legt `pixelswitch` unter %@ ab.";
"%@ points to another copy of PixelSwitch. Install points it here." = "%@ verweist auf eine andere Kopie von PixelSwitch. „Installieren“ verweist es hierher.";
"Something else is already at %@, so it was left alone." = "Unter %@ liegt bereits etwas anderes, daher wurde es nicht angetastet.";
"Remote control is on. Only this Mac's user can connect; nothing is open to the network." = "Die Fernsteuerung ist aktiv. Nur der Benutzer dieses Macs kann sich verbinden; nichts ist zum Netzwerk hin offen.";
"Remote control is not running." = "Die Fernsteuerung läuft nicht.";
"Remote control could not start: %@" = "Die Fernsteuerung konnte nicht starten: %@";
"To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH." = "Damit Claude auf einem anderen Mac PixelSwitch steuern kann, fügen Sie dies zu dessen MCP-Einstellungen hinzu. Dieser Mac muss diesen hier per SSH erreichen können.";
"No account is active yet, so there is nothing to switch from. Add the current account first." = "Es ist noch kein Konto aktiv, daher gibt es nichts, von dem gewechselt werden kann. Fügen Sie zuerst das aktuelle Konto hinzu.";
"A switch or sign-in is in progress. Try again in a moment." = "Ein Wechsel oder eine Anmeldung läuft. Versuchen Sie es gleich noch einmal.";
```

Append to the end of `PixelSwitch/fr.lproj/Localizable.strings`, after one blank line:

```text
/* Remote control (1.2) */
"Command line & AI" = "Ligne de commande et IA";
"Command-line tool" = "Outil en ligne de commande";
"Install" = "Installer";
"Reinstall" = "Réinstaller";
"Copy" = "Copier";
"Installed at %@." = "Installé dans %@.";
"Not installed. Install puts `pixelswitch` at %@." = "Non installé. « Installer » place `pixelswitch` dans %@.";
"%@ points to another copy of PixelSwitch. Install points it here." = "%@ pointe vers une autre copie de PixelSwitch. « Installer » le fait pointer ici.";
"Something else is already at %@, so it was left alone." = "Un autre élément se trouve déjà dans %@ ; il a été laissé tel quel.";
"Remote control is on. Only this Mac's user can connect; nothing is open to the network." = "Le contrôle à distance est activé. Seul l’utilisateur de ce Mac peut s’y connecter ; rien n’est ouvert sur le réseau.";
"Remote control is not running." = "Le contrôle à distance n’est pas actif.";
"Remote control could not start: %@" = "Le contrôle à distance n’a pas pu démarrer : %@";
"To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH." = "Pour que Claude sur un autre Mac puisse contrôler PixelSwitch, ajoutez ceci à ses réglages MCP. Ce Mac doit pouvoir joindre celui-ci en SSH.";
"No account is active yet, so there is nothing to switch from. Add the current account first." = "Aucun compte n’est encore actif, il n’y a donc rien à changer. Ajoutez d’abord le compte actuel.";
"A switch or sign-in is in progress. Try again in a moment." = "Un changement ou une connexion est en cours. Réessayez dans un instant.";
```

Append to the end of `PixelSwitch/ja.lproj/Localizable.strings`, after one blank line:

```text
/* Remote control (1.2) */
"Command line & AI" = "コマンドラインと AI";
"Command-line tool" = "コマンドラインツール";
"Install" = "インストール";
"Reinstall" = "再インストール";
"Copy" = "コピー";
"Installed at %@." = "%@ にインストール済みです。";
"Not installed. Install puts `pixelswitch` at %@." = "インストールされていません。「インストール」で `pixelswitch` を %@ に配置します。";
"%@ points to another copy of PixelSwitch. Install points it here." = "%@ は別の PixelSwitch を指しています。「インストール」でこちらを指すようにします。";
"Something else is already at %@, so it was left alone." = "%@ には別のものがあるため、そのままにしました。";
"Remote control is on. Only this Mac's user can connect; nothing is open to the network." = "リモート操作はオンです。この Mac のユーザーだけが接続でき、ネットワークには何も公開していません。";
"Remote control is not running." = "リモート操作は実行されていません。";
"Remote control could not start: %@" = "リモート操作を開始できませんでした: %@";
"To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH." = "別の Mac の Claude から PixelSwitch を操作するには、その MCP 設定にこれを追加してください。その Mac から SSH でこの Mac に接続できる必要があります。";
"No account is active yet, so there is nothing to switch from. Add the current account first." = "まだアクティブなアカウントがないため、切り替え元がありません。先に現在のアカウントを追加してください。";
"A switch or sign-in is in progress. Try again in a moment." = "切り替えまたはサインインの処理中です。しばらくしてからもう一度お試しください。";
```

Append to the end of `PixelSwitch/zh-Hans.lproj/Localizable.strings`, after one blank line:

```text
/* Remote control (1.2) */
"Command line & AI" = "命令行与 AI";
"Command-line tool" = "命令行工具";
"Install" = "安装";
"Reinstall" = "重新安装";
"Copy" = "复制";
"Installed at %@." = "已安装在 %@。";
"Not installed. Install puts `pixelswitch` at %@." = "未安装。“安装”会将 `pixelswitch` 放在 %@。";
"%@ points to another copy of PixelSwitch. Install points it here." = "%@ 指向另一份 PixelSwitch。“安装”会将其指向这里。";
"Something else is already at %@, so it was left alone." = "%@ 已有其他文件，因此未作改动。";
"Remote control is on. Only this Mac's user can connect; nothing is open to the network." = "远程控制已开启。只有这台 Mac 的用户可以连接，没有任何端口向网络开放。";
"Remote control is not running." = "远程控制未在运行。";
"Remote control could not start: %@" = "远程控制无法启动：%@";
"To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH." = "若要让另一台 Mac 上的 Claude 控制 PixelSwitch，请将此内容添加到它的 MCP 设置中。那台 Mac 必须能通过 SSH 连接到这台 Mac。";
"No account is active yet, so there is nothing to switch from. Add the current account first." = "尚无活跃账户，因此无从切换。请先添加当前账户。";
"A switch or sign-in is in progress. Try again in a moment." = "正在切换或登录。请稍后再试。";
```


Run: `for l in en de fr ja zh-Hans; do plutil -lint PixelSwitch/$l.lproj/Localizable.strings; done`
Expected: five `OK` lines.

Run: `python3 -c "import re,glob; s=[frozenset(re.findall(r'^\"((?:[^\"\\\\]|\\\\.)*)\" = ', open(f).read(), re.M)) for f in sorted(glob.glob('PixelSwitch/*.lproj/Localizable.strings'))]; print(len(set(s)) == 1, len(s[0]))"`
Expected: `True` and the same key count for all five files.

- [ ] **Step 4: Verify**

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK` and `cli: type-check OK (6 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Views/RemoteControlSection.swift PixelSwitch/Views/ClaudeCLITabView.swift PixelSwitch/*.lproj/Localizable.strings
git commit -m "$(cat <<'EOF'
Remote control: Install button, status and MCP snippet in Settings

Settings → Claude CLI → Command line & AI installs ~/.local/bin/pixelswitch,
says whether remote control is listening, and gives Claude on another Mac a
ready-to-copy MCP entry with this Mac's .local name. Strings in five languages.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: The tool target in project.yml, and signing it in CI

**Files:**
- Modify: `project.yml` (the app's dependencies; a new `pixelswitch` target at the end)
- Modify: `.github/workflows/build.yml` (the "Sign App" step)

**Interfaces:**
- Consumes: `PixelSwitchCLI/`, `PixelSwitch/Control/ControlProtocol.swift`.
- Produces: the `pixelswitch` tool target, copied into the app at `Contents/Helpers/pixelswitch`, signed with hardened runtime and identifier `ai.pixelventures.pixelswitch.cli` before the widget and the app.


- [ ] **Step 1: project.yml**

In `project.yml`, replace:

```yaml
    dependencies:
      - target: PixelSwitchWidgetExtension
        embed: true
        codeSign: true
      - package: Sparkle
```

with:

```yaml
    dependencies:
      - target: PixelSwitchWidgetExtension
        embed: true
        codeSign: true
      # The `pixelswitch` command-line tool, copied to Contents/Helpers.
      # Not linked; CI signs it (see .github/workflows/build.yml).
      - target: pixelswitch
        embed: true
        link: false
        codeSign: false
        copy:
          destination: wrapper
          subpath: Contents/Helpers
      - package: Sparkle
```

Then append this target to the end of `project.yml`. Use the SAME `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` values the app target has at that moment (`grep -m1 MARKETING_VERSION project.yml`); `scripts/release.sh` rewrites every occurrence, so all three targets move together. With the app at 1.1 build 22, as when this plan was written:

```yaml

  # `pixelswitch`: remote control from the command line or an AI (MCP).
  pixelswitch:
    type: tool
    platform: macOS
    sources:
      - path: PixelSwitchCLI
      - path: PixelSwitch/Control/ControlProtocol.swift
    settings:
      base:
        PRODUCT_NAME: pixelswitch
        PRODUCT_BUNDLE_IDENTIFIER: ai.pixelventures.pixelswitch.cli
        MARKETING_VERSION: "1.1"
        CURRENT_PROJECT_VERSION: "22"
        SWIFT_VERSION: "6.0"
        MACOSX_DEPLOYMENT_TARGET: "14.0"
        CODE_SIGN_STYLE: Automatic
        DEVELOPMENT_TEAM: ""
        SKIP_INSTALL: true
```

- [ ] **Step 2: Sign the tool in CI**

In `.github/workflows/build.yml`, replace:

```yaml
          # Widget extension with its own entitlements file
```

with:

```yaml
          # The pixelswitch command-line tool (Contents/Helpers). Signed
          # before the app so the app's seal covers the signed tool.
          codesign --force --options runtime --timestamp \
            --sign "$IDENTITY" \
            --identifier ai.pixelventures.pixelswitch.cli \
            "$APP/Contents/Helpers/pixelswitch"

          # Widget extension with its own entitlements file
```

The ad-hoc branch (no Developer ID) already signs with `--deep`, which covers `Contents/Helpers`.

- [ ] **Step 3: Verify**

Run: `xcodegen generate && grep -n "dstPath = Contents/Helpers;\|dstSubfolderSpec = 1;\|com.apple.product-type.tool" PixelSwitch.xcodeproj/project.pbxproj`
Expected: the three lines (a Copy Files phase to the wrapper's `Contents/Helpers`, and the tool product type).

Run: `ruby -ryaml -e 'YAML.load_file(".github/workflows/build.yml"); puts "ok"'`
Expected: `ok`.

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK` and `cli: type-check OK (6 files)`.

- [ ] **Step 4: Commit**

```bash
git add project.yml .github/workflows/build.yml
git commit -m "$(cat <<'EOF'
Build the pixelswitch tool into Contents/Helpers and sign it in CI

A type: tool target copied into the app bundle (not linked); CI signs it with
hardened runtime and its own identifier before the widget and the app.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: The integration script and the documentation

**Files:**
- Create: `Tests/integration/remote-control.sh` (executable)
- Modify: `README.md` (a new section before "## Key Features & Architecture")
- Modify: `AGENTS.md` (a bullet after the Views line)
- Modify: `ARCHITECTURE.md` (a section appended at the end)

**Interfaces:**
- Consumes: a built and installed PixelSwitch 1.2 (for running the script, in Task 13).
- Produces: the documentation of remote control, and a safe check against the real app.


- [ ] **Step 1: The integration script**

Create `Tests/integration/remote-control.sh` and make it executable (`chmod +x`). It never switches, removes, relabels, reorders or re-thresholds an account; its header says so.

```bash
#!/bin/bash
# Remote-control integration check against the REAL running PixelSwitch.
#
# Safe on a Mac with real accounts. It NEVER switches, removes, relabels,
# reorders or re-thresholds an account. It only:
#   - reads status, accounts and usage;
#   - changes two harmless settings and puts each back (also on Ctrl-C);
#   - watches events while asking for one refresh;
#   - starts a sign-in and cancels it (starting and cancelling
#     `claude auth login` changes no login; the sign-in window appears on the
#     Mac for a moment). Skip that part with SKIP_SIGN_IN=1;
#   - talks to `pixelswitch mcp` over stdio.
#
# Needs PixelSwitch 1.2 or later running, and the command-line tool installed
# (Settings → Claude CLI → Install), or PIXELSWITCH_CLI pointing at it.
set -uo pipefail

CLI="${PIXELSWITCH_CLI:-$HOME/.local/bin/pixelswitch}"
WORK=$(mktemp -d)
pass=0
fail=0
ok()  { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1${2:+: $2}"; fail=$((fail + 1)); }
# field KEY [KEY...]: prints the JSON value at that path ("" for null); "#" prints a list's length.
field() {
    python3 -c '
import json, sys
d = json.load(sys.stdin)
for key in sys.argv[1:]:
    d = len(d) if key == "#" else (d[int(key)] if isinstance(d, list) else d.get(key))
print("" if d is None else d)' "$@"
}

[ -x "$CLI" ] || { echo "No pixelswitch at $CLI. Install it from Settings → Claude CLI, or set PIXELSWITCH_CLI."; exit 2; }

ORIGINAL_DRAIN=""
ORIGINAL_LOW=""
restore() {
    [ -n "$ORIGINAL_DRAIN" ] && "$CLI" settings set autoSwitch.drainWithinHours "$ORIGINAL_DRAIN" >/dev/null 2>&1
    [ -n "$ORIGINAL_LOW" ] && "$CLI" settings set menuBar.lowRemainingWarningThreshold "$ORIGINAL_LOW" >/dev/null 2>&1
    rm -rf "$WORK"
}
trap restore EXIT

# 1. Status and protocol
if status=$("$CLI" --json status); then
    [ "$(echo "$status" | field protocolVersion)" = "1" ] && ok "status answers, protocol 1" || bad "status protocol" "$status"
else
    bad "status" "exit $?"
fi

# 2. Accounts and usage (read only)
accounts=$("$CLI" --json accounts) && count=$(echo "$accounts" | field accounts '#') \
    && ok "accounts lists $count account(s)" || bad "accounts"
usage=$("$CLI" --json usage) && echo "$usage" | field machine todayCost >/dev/null \
    && ok "usage reports every account and this Mac's cost" || bad "usage"
"$CLI" usage nobody-at-all-xyz >/dev/null 2>&1; code=$?
[ "$code" = "4" ] && ok "an unknown account exits 4" || bad "unknown account exit code" "$code"

# 3. Two harmless settings, each changed and put back
ORIGINAL_DRAIN=$("$CLI" --json settings autoSwitch.drainWithinHours | field settings autoSwitch.drainWithinHours)
target=13; [ "${ORIGINAL_DRAIN%.*}" = "13" ] && target=14
now=$("$CLI" --json settings set autoSwitch.drainWithinHours "$target" | field settings autoSwitch.drainWithinHours)
[ "${now%.*}" = "$target" ] && ok "a setting changes (autoSwitch.drainWithinHours → $target)" || bad "setting change" "$now"
"$CLI" settings set autoSwitch.drainWithinHours "$ORIGINAL_DRAIN" >/dev/null
back=$("$CLI" --json settings autoSwitch.drainWithinHours | field settings autoSwitch.drainWithinHours)
[ "$back" = "$ORIGINAL_DRAIN" ] && ok "and is put back ($ORIGINAL_DRAIN)" || bad "setting restore" "$back"

ORIGINAL_LOW=$("$CLI" --json settings menuBar.lowRemainingWarningThreshold | field settings menuBar.lowRemainingWarningThreshold)
"$CLI" settings set menuBar.lowRemainingWarningThreshold 35 >/dev/null && \
"$CLI" settings set menuBar.lowRemainingWarningThreshold "$ORIGINAL_LOW" >/dev/null && \
[ "$("$CLI" --json settings menuBar.lowRemainingWarningThreshold | field settings menuBar.lowRemainingWarningThreshold)" = "$ORIGINAL_LOW" ] \
    && ok "a menu bar setting round-trips" || bad "menu bar setting round trip"

"$CLI" settings set refreshInterval 7 >/dev/null 2>&1; code=$?
[ "$code" = "2" ] && ok "an invalid value is refused (exit 2)" || bad "invalid value exit code" "$code"

# 4. Events while one refresh runs
"$CLI" watch > "$WORK/events" 2>/dev/null &
watcher=$!
sleep 1
"$CLI" refresh >/dev/null 2>&1
sleep 3
kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
grep -q '"usageUpdated"' "$WORK/events" && ok "watch reports usageUpdated after a refresh" || bad "watch" "$(head -c 300 "$WORK/events")"

# 5. Sign-in: start, then cancel
if [ "${SKIP_SIGN_IN:-0}" != "1" ]; then
    started=$("$CLI" --json accounts sign-in)
    state=$(echo "$started" | field state)
    link=$(echo "$started" | field automaticLink)
    case "$state" in
        waitingForUser|starting) ok "a sign-in starts ($state)";;
        *) bad "sign-in start" "$state";;
    esac
    [[ "$link" == https://*localhost* || "$link" == https://*redirect_uri=http%3A%2F%2Flocalhost* ]] \
        && ok "the automatic link finishes on this Mac (localhost redirect)" || bad "automatic link" "${link:0:80}"
    "$CLI" accounts sign-in cancel >/dev/null
    for _ in 1 2 3 4 5 6 7 8; do
        state=$("$CLI" --json accounts sign-in status | field signIn state)
        [ "$state" = "cancelled" ] && break
        sleep 1
    done
    [ "$state" = "cancelled" ] && ok "the sign-in is cancelled and nothing changed" || bad "sign-in cancel" "$state"
fi

# 6. MCP over stdio
printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"it","version":"1"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_status"}}' \
    | "$CLI" mcp > "$WORK/mcp"
python3 - "$WORK/mcp" <<'PY' && ok "MCP: handshake, 18 tools, and a working tool call" || bad "MCP" "$(head -c 300 "$WORK/mcp")"
import json, sys
replies = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
assert len(replies) == 3
assert replies[0]["result"]["protocolVersion"] == "2025-11-25"
assert len(replies[1]["result"]["tools"]) == 18
assert replies[2]["result"]["isError"] is False
PY

echo
echo "$pass passed, $fail failed"
[ "$fail" = "0" ]
```

Run: `bash -n Tests/integration/remote-control.sh && echo ok`
Expected: `ok`. (It runs against the real app in Task 13.)

- [ ] **Step 2: Documentation**

In `README.md`, replace:

```markdown
## Key Features & Architecture
```

with:

```markdown
## Remote control (command line and AI)

PixelSwitch can be driven from the command line, or by an AI on another Mac. Everything the app does is available: read usage, switch, add, re-sign, rename, reorder and remove accounts, set per-account thresholds, and change every setting.

1. In **Settings → Claude CLI → Command line & AI**, click **Install**. It links `~/.local/bin/pixelswitch` to the tool inside the app, so updates keep it current.
2. Try it: `pixelswitch status`, `pixelswitch accounts`, `pixelswitch usage`, `pixelswitch switch 2`. Add `--json` for exact JSON. `pixelswitch help` lists everything.
3. From another Mac, run it over SSH: `ssh this-mac.local ~/.local/bin/pixelswitch status`.
4. For Claude on another Mac, copy the MCP snippet from the same Settings section into that Claude's MCP settings. It runs `ssh <this-mac>.local /Users/<you>/.local/bin/pixelswitch mcp`, and Claude sees each action as a tool.

The tool talks to the app over a socket in `~/Library/Application Support/PixelSwitch/` that only your macOS user can open. Nothing listens on the network, and no command ever returns a login token. Every remote command is written to `~/Library/Logs/PixelSwitch-app.log`. Adding a brand-new account still needs a person to sign in to Anthropic in a browser; `pixelswitch accounts sign-in` prints the links.

## Key Features & Architecture
```

In `AGENTS.md`, replace:

```markdown
- **Views**: `MainMenuView.swift` (dropdown), `SettingsView.swift` (native window), `HiddenWindowView.swift` (LSUIElement keepalive workaround).
```

with:

```markdown
- **Views**: `MainMenuView.swift` (dropdown), `SettingsView.swift` (native window), `HiddenWindowView.swift` (LSUIElement keepalive workaround).
- **Remote control** (`PixelSwitch/Control/`, `PixelSwitchCLI/`): the `pixelswitch` tool (`Contents/Helpers/pixelswitch`, a `type: tool` target in `project.yml`, signed in CI) talks to the app over a user-only Unix socket (`~/Library/Application Support/PixelSwitch/control.sock`, newline-delimited JSON-RPC, `ControlProtocol.swift` shared by both). `ControlAPI` (pure, unit-tested) → `AppController` → `AppState`; `SettingsStore` owns every setting's side effect; `pixelswitch mcp` is an MCP server (modern 2026-07-28 and legacy handshake). No token ever crosses the socket; no TCP listener.
```

Append to the end of `ARCHITECTURE.md`:

```markdown


---

## Remote control (1.2)

```
other Mac ──ssh──▶ pixelswitch <command> ─┐
other Mac ──ssh──▶ pixelswitch mcp ───────┤  ~/Library/Application Support/PixelSwitch/control.sock
                                          ▼  (folder 0700, socket 0600, peer uid checked)
              PixelSwitch.app: ControlServer ─▶ ControlAPI ─▶ AppController ─▶ AppState / SettingsStore
```

- **Transport.** A Unix-domain socket, newline-delimited JSON-RPC 2.0 (`PixelSwitch/Control/ControlProtocol.swift`, compiled into the app and the tool, protocol version 1). `ControlServer` refuses a peer whose uid is not this user's, replaces a socket file left by a crash, and will not take over one another copy of the app still answers on. There is no TCP listener.
- **API.** `ControlAPI` maps the 19 methods (`status.get`, `accounts.*`, `usage.*`, `settings.*`, `events.subscribe`, `app.*`) onto `AppControlling`, which `AppController` implements with the same `AppState` methods the GUI uses. It never touches `KeychainService` or `ClaudeService` credential methods, and a unit test scans every result for token fields. Accounts are named by id, email, label or position (`AccountResolver`).
- **Settings.** `SettingKey` (pure) knows every setting's shape; `SettingsStore` reads and writes the real values and owns their side effects, which the Settings window's `onChange` handlers also call.
- **Events.** `ControlEventHub` pushes `activeAccountChanged`, `usageUpdated`, `autoSwitched`, `signInChanged` and `error` to subscribed connections (`pixelswitch watch`).
- **Tool.** `PixelSwitchCLI/` (`pixelswitch`): hand-rolled argument parsing, readable output or `--json`, exit codes 0 ok, 1 failed, 2 usage, 3 busy, 4 no such account, 5 unreachable. Starts the app with `open -g -b` if it is not running. `pixelswitch mcp` serves MCP on stdio in both eras: modern (2026-07-28, per-request `_meta`, `server/discover`) and legacy (`initialize`).
- **Logging.** Each remote command is logged as its method, account id or setting key, and outcome. No email address, link or argument is logged.
```

- [ ] **Step 3: Commit**

```bash
git add Tests/integration/remote-control.sh README.md AGENTS.md ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
Remote control: integration script and documentation

A check against the real app that only reads, round-trips two settings, watches
one refresh and starts then cancels a sign-in. README, AGENTS and ARCHITECTURE
describe the socket, the tool, MCP and the security rules.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Full verification, the CI build, and the checks on the real app

**Files:** none new.

- [ ] **Step 1: Everything local**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `505 passed, 0 failed`.

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK` and `cli: type-check OK (6 files)`.

Run: `git status --short`
Expected: clean.

- [ ] **Step 2: The CI build (build, sign, notarize, staple)**

```bash
git push -u origin part-c-remote-control
gh workflow run "Build and Notarize macOS App" --repo BespokeWoodcraftStudio/PixelSwitch --ref part-c-remote-control
sleep 10
RUN=$(gh run list --repo BespokeWoodcraftStudio/PixelSwitch --branch part-c-remote-control --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN" --repo BespokeWoodcraftStudio/PixelSwitch --exit-status
rm -rf /tmp/pixelswitch-part-c && gh run download "$RUN" --repo BespokeWoodcraftStudio/PixelSwitch -n PixelSwitch-macOS -D /tmp/pixelswitch-part-c
MP=$(hdiutil attach -nobrowse -readonly /tmp/pixelswitch-part-c/PixelSwitch.dmg | awk -F'\t' '/\/Volumes/{print $NF}')
ls -l "$MP/PixelSwitch.app/Contents/Helpers/pixelswitch"
codesign --verify --strict --verbose=1 "$MP/PixelSwitch.app/Contents/Helpers/pixelswitch"
codesign -dv "$MP/PixelSwitch.app/Contents/Helpers/pixelswitch" 2>&1 | grep -E "^Identifier|flags|TeamIdentifier"
codesign --verify --deep --strict "$MP/PixelSwitch.app" && echo "app seal ok"
spctl -a -vv "$MP/PixelSwitch.app"
"$MP/PixelSwitch.app/Contents/Helpers/pixelswitch" --version
hdiutil detach "$MP" -quiet
```

Expected: the run succeeds; `Contents/Helpers/pixelswitch` exists and is executable; it verifies, with `Identifier=ai.pixelventures.pixelswitch.cli`, `flags=0x10000(runtime)` and `TeamIdentifier=LM3P28DNVQ`; `app seal ok`; `spctl` says `accepted, source=Notarized Developer ID`; the tool prints its version line.

- [ ] **Step 3: On the real app (a person at this Mac; the founder's single round of hand checks, per his answer on the plans page)**

Install the DMG over the current app and open PixelSwitch. Then:

1. Settings → Claude CLI → **Command line & AI**: "Remote control is on" with a green tick; click **Install**; the row reads "Installed at ~/.local/bin/pixelswitch".
2. In Terminal: `~/.local/bin/pixelswitch status`, `~/.local/bin/pixelswitch accounts`, `~/.local/bin/pixelswitch usage`. Each prints readable output matching the popover.
3. Run the integration script: `bash Tests/integration/remote-control.sh`. Expected: every line `PASS`, ending `N passed, 0 failed`. (The sign-in window appears for a moment and closes: that is the start-then-cancel check.)
4. Switch from the command line: `~/.local/bin/pixelswitch switch <another account>`. The menu bar changes to that account; `pixelswitch status` agrees. Switch back the same way.
5. Quit PixelSwitch, then run `~/.local/bin/pixelswitch status`: the app starts by itself and the command answers within about 10 seconds.
6. From the other Mac: `ssh <this-mac>.local ~/.local/bin/pixelswitch status` works. Then paste the MCP snippet from Settings into that Mac's Claude MCP settings, and ask Claude to "list my PixelSwitch accounts and their weekly usage". It calls `list_accounts` / `get_usage` and answers.
7. `ls ~/Library/Application\ Support/PixelSwitch/control.sock` exists while the app runs and is gone after quitting it.

- [ ] **Step 4: Record and hand over**

Write the work-journal entry with the `worklog` skill: What / Why / How / Outcome, Lessons (at least: the protocol file's location and why; the two MCP eras; the no-Xcode type-check), Follow-ups, and which of the Step 3 checks passed. Commit and push. Merge `part-c-remote-control` into `main` only after Step 3 passes, then release as 1.2 with `/release` (release notes: remote control, per-account thresholds, next-account strategy, sign-in links).

## Self-review against the spec

| Spec item | Where |
|---|---|
| C1 CLI inside the app at `Contents/Helpers/pixelswitch`; Install links `~/.local/bin/pixelswitch`; CI signs it before the app | Tasks 7, 10, 11, 13 |
| C1 user-only socket, 0700/0600, `getpeereid`, no TCP | Task 4 (tests: permissions; peer check in `acceptPending`); Global Constraints |
| C1 CLI starts the app if not running, waits up to 10 s | Task 4 (`ControlClient.connect`), Task 13 check 5 |
| C2 newline-delimited JSON-RPC 2.0, `protocolVersion`, refuse mismatch | Tasks 1, 5 |
| C2 account references id / email / label (+ position), ambiguity lists matches | Task 2 |
| C2 typed errors busy / notFound / ambiguous / invalidValue / claudeUnavailable / failed | Task 1 (kinds), Task 3 (API), Task 5 (exit codes) |
| C2 every method in the table | Task 3 (`ControlAPI`, one test per method), Task 9 (`AppController`) |
| C2 every setting in "Settings exposed", same allowed values; hysteresis and cooldown not exposed | Task 2 (`SettingKey`), Task 9 (`SettingsStore`) |
| C3 `AppController`; `AppState` throwing versions with the GUI unchanged; `busy` instead of silent | Tasks 8, 9 |
| C3 `SettingsStore` takes side effects out of the views | Task 9 |
| C3 `ControlAPI` pure and unit-tested with a fake | Task 3 |
| C3 remote commands logged, ids and keys only | Task 3 (log test), Task 9 (`FileLog("Control")`) |
| C4 commands, `--json`, exit codes, `remove --yes` | Task 5, Task 6 (smoke test) |
| C5 MCP mode, tools mirroring methods, JSON schemas, snippet with absolute path | Task 6, Task 7 (`mcpConfiguration`), Task 10 |
| C6 no token crosses; user-only; logged | Task 3 (no-token test), Task 4, Task 9 (grep for credential methods) |
| C7 unit tests: protocol, API with fake, MCP, no-token; integration script; manual run from the other Mac | Tasks 1–7, 12, 13 |
| Review Focus 1–5 | Tasks 3, 4, 5, 13 |
