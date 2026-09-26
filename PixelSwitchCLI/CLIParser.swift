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
    /// nil: back to the default; 0: manual only.
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
            guard rest.count == 2 else { throw CLIUsageError("Usage: pixelswitch accounts threshold <account> <1-100|default|manual>") }
            if rest[1].lowercased() == "default" { return .threshold(rest[0], nil) }
            // Manual only is 0. A literal, because this target does not compile
            // AutoSwitchSettings. "off", "never" and "disable" are deliberately
            // not aliases: they read just as well as "never switch away".
            if rest[1].lowercased() == "manual" { return .threshold(rest[0], 0) }
            guard let value = Double(rest[1].replacingOccurrences(of: "%", with: "")) else {
                throw CLIUsageError("The threshold must be a number from 1 to 100, \"default\", or \"manual\" (0 means manual too).")
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
      pixelswitch accounts threshold <account> <1-100|default|manual>
                                                     manual (or 0): never switched to automatically
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
