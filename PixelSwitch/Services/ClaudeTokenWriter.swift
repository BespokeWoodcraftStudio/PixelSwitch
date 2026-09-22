import Foundation

/// Writes Claude Code's Keychain item (`Claude Code-credentials`) the way the
/// Claude Code CLI does: through `/usr/bin/security`, so the item keeps the
/// access list and partition the CLI needs to read it without prompts.
///
/// The secret goes to `security -i` on stdin, never on the command line,
/// whenever the whole command fits `security`'s interactive line (4,095 bytes;
/// measured on macOS 26: 4,095 works, 4,097 fails). A credential longer than
/// about 2,000 characters does not fit, typically because Claude Code keeps MCP
/// server tokens in the same item; it is then passed as hex on the command
/// line, which is what the Claude CLI itself does in that case.
///
/// Rejected alternative, tested 2026-09-21: updating the item's data through
/// the Security framework (`SecItemUpdate`) succeeds silently, but moves the
/// item out of the `apple-tool:` partition, after which `/usr/bin/security`
/// (which the CLI shells out to) blocks on a Keychain prompt.
struct ClaudeTokenWriter {
    struct Invocation: Equatable {
        var arguments: [String]
        var stdin: Data?
    }

    enum Transport: String, Equatable {
        case stdin = "stdin (security -i)"
        case argv = "hex on the command line (too long for security -i)"
    }

    enum Path: String, Equatable {
        case updateInPlace = "update in place (-U)"
        case deleteAndAdd = "delete and add"
    }

    enum Outcome: Equatable {
        case written(path: Path, transport: Transport)
        case failed(reason: String)
    }

    /// `security -i` reads each command with a 4,096-byte line buffer.
    static let interactiveLineLimit = 4095

    let service: String
    let account: String
    /// Runs `/usr/bin/security` with the invocation and returns its exit status.
    var run: (Invocation) -> Int32
    /// The stored secret read back with `find-generic-password -w`, or nil.
    var readBack: () -> String?

    /// The `add-generic-password` invocation for `secret`.
    static func addInvocation(service: String, account: String, secret: String, updateInPlace: Bool) -> (Invocation, Transport) {
        let hex = secret.utf8.map { String(format: "%02x", $0) }.joined()
        let flags = updateInPlace ? ["-U"] : []
        let line = (["add-generic-password"] + flags + ["-s", quoted(service), "-a", quoted(account), "-X", hex])
            .joined(separator: " ") + "\n"
        if isQuotable(service), isQuotable(account), line.utf8.count <= interactiveLineLimit {
            return (Invocation(arguments: ["-i"], stdin: Data(line.utf8)), .stdin)
        }
        return (Invocation(arguments: ["add-generic-password"] + flags + ["-s", service, "-a", account, "-X", hex], stdin: nil), .argv)
    }

    /// Updates the item in place first, so it never goes missing: a CLI read in
    /// that gap would fall through to the plaintext file. Falls back to delete
    /// and add only if the update does not read back. Success means the stored
    /// bytes equal `secret`.
    func write(_ secret: String) -> Outcome {
        let (update, transport) = Self.addInvocation(service: service, account: account, secret: secret, updateInPlace: true)
        _ = run(update)
        if readBack() == secret {
            return .written(path: .updateInPlace, transport: transport)
        }

        _ = run(Invocation(arguments: ["delete-generic-password", "-s", service, "-a", account], stdin: nil))
        let (add, addTransport) = Self.addInvocation(service: service, account: account, secret: secret, updateInPlace: false)
        let status = run(add)
        guard let stored = readBack() else {
            return .failed(reason: "item missing after delete and add (security exit \(status))")
        }
        guard stored == secret else {
            return .failed(reason: "read-back does not match: wrote \(secret.utf8.count) bytes, read back \(stored.utf8.count)")
        }
        return .written(path: .deleteAndAdd, transport: addTransport)
    }

    private static func quoted(_ value: String) -> String { "\"" + value + "\"" }

    /// `security -i` splits on spaces and honours double quotes; anything that
    /// could break its tokenizer goes the command-line route instead.
    private static func isQuotable(_ value: String) -> Bool {
        !value.contains("\"") && !value.contains("\\") && !value.contains("\n")
    }
}
