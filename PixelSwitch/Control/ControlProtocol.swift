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
    static let version = 2

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
    /// 1–100; 0 = manual only (auto-switch never switches to it); nil
    /// follows the default threshold.
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
    /// This account's own threshold: 1–100, or 0 for manual only; nil when it
    /// follows the default.
    let threshold: Double?
    /// Where auto-switch moves off this account while it is active: its own
    /// threshold, else the default (also when manualOnly).
    let effectiveThreshold: Double
    /// True when auto-switch never switches to it; a person or
    /// accounts.switch still can.
    let manualOnly: Bool
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
