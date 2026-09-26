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
