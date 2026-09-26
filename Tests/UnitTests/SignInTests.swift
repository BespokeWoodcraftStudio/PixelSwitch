// Sign-in links (Part B): the output parser, the rules AppState acts on, the
// link-capture helper, the process runner and the SignInSession state machine.
// Called from main.swift. Uses the global `check` defined there.
import Foundation

@MainActor func runSignInTests() {
    runSignInParserTests()
    runSignInRulesTests()
    runSignInEnvironmentTests()
    runSignInCaptureTests()
    runSignInProcessTests()
    runSignInSessionTests()
    runSignInEndToEndTests()
}

/// Shared test data and helpers, in one namespace so nothing here collides
/// with names in main.swift or another part's test file. Later sections add
/// to it with extensions.
enum SignInTestKit {
    /// The manual link as Claude Code 2.1.280 prints it (test values stand in for the secrets).
    static let manualURLString = "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference&code_challenge=TESTCHALLENGE123&code_challenge_method=S256&state=TESTSTATE456"
    /// The automatic link Claude Code hands to $BROWSER.
    static let automaticURLString = "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=http%3A%2F%2Flocalhost%3A54545%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference&code_challenge=TESTCHALLENGE123&code_challenge_method=S256&state=TESTSTATE456"
    /// Exactly what `claude auth login` wrote to stdout on 2026-09-25, prompt included (no trailing newline).
    static let observedOutput = "Opening browser to sign in\u{2026}\nIf the browser didn't open, visit: \(manualURLString)\nPaste code here if prompted > "
    static let accountId = UUID(uuidString: "0B8D2C4E-6F1A-4C3B-9D5E-7A8B9C0D1E2F")!
}

// MARK: - Parser

@MainActor func runSignInParserTests() {
    let manualString = SignInTestKit.manualURLString
    let automaticString = SignInTestKit.automaticURLString
    let manual = URL(string: manualString)!
    let automatic = URL(string: automaticString)!

    check(SignInOutputParser.manualLink(in: SignInTestKit.observedOutput) == manual,
          "sign-in parser: reads the manual link from Claude Code 2.1.280's exact output")

    let osc8 = "Opening browser to sign in\u{2026}\nIf the browser didn't open, visit: \u{1B}]8;;\(manualString)\u{07}\(manualString)\u{1B}]8;;\u{07}\nPaste code here if prompted > "
    check(SignInOutputParser.manualLink(in: osc8) == manual, "sign-in parser: an OSC-8 hyperlink around the link is tolerated")

    let labelled = "If the browser didn't open, visit: \u{1B}]8;;\(manualString)\u{1B}\\this link\u{1B}]8;;\u{1B}\\\nPaste code here if prompted > "
    check(SignInOutputParser.manualLink(in: labelled) == manual, "sign-in parser: an OSC-8 link whose visible text is only a label still yields the link")

    let coloured = "\u{1B}[1mOpening browser to sign in\u{2026}\u{1B}[0m\nIf the browser didn't open, visit: \u{1B}[4;36m\(manualString)\u{1B}[0m\n\u{1B}[2mPaste code here if prompted > \u{1B}[0m"
    check(SignInOutputParser.manualLink(in: coloured) == manual, "sign-in parser: colour codes around the link are stripped")

    check(SignInOutputParser.manualLink(in: "Opening browser to sign in\u{2026}\nPaste code here if prompted > ") == nil,
          "sign-in parser: output with no link yields nothing")

    let reworded = "Sign in with this address: \(manualString)\nThen paste the code > "
    check(SignInOutputParser.manualLink(in: reworded) == manual, "sign-in parser: a reworded line still yields a code-callback link")

    let noisy = "Checking https://docs.claude.com/en/docs/claude-code for news\nWarning: something\nIf the browser didn't open, visit: \(manualString)\nPaste code here if prompted > "
    check(SignInOutputParser.manualLink(in: noisy) == manual, "sign-in parser: extra lines and another URL before it do not confuse it")

    check(SignInOutputParser.manualLink(in: "If the browser didn't open, visit: https://docs.claude.com/en/sign-in\n") == nil,
          "sign-in parser: a visit: line with a URL that is not a sign-in link is not taken")
    check(SignInOutputParser.manualLink(in: "If the browser didn't open, visit: \(automaticString)\n") == nil,
          "sign-in parser: the automatic (localhost) link is never taken for the manual one")

    let redrawn = "\u{280B} Opening\r\u{2819} Opening\rIf the browser didn't open, visit: \(manualString)\r\n"
    check(SignInOutputParser.manualLink(in: redrawn) == manual, "sign-in parser: carriage-return redraws do not glue the link to other text")

    check(SignInOutputParser.automaticLink(inCaptureFile: automaticString + "\n") == automatic,
          "sign-in parser: reads the automatic link from a one-line capture file")
    let several = "https://claude.com/help\n\(manualString)\n\(automaticString)\n\(automaticString.replacingOccurrences(of: "54545", with: "60000"))\n"
    check(SignInOutputParser.automaticLink(inCaptureFile: several) == automatic,
          "sign-in parser: takes the first localhost-redirect link among several lines")
    check(SignInOutputParser.automaticLink(inCaptureFile: "") == nil, "sign-in parser: an empty capture file yields nothing")
    check(SignInOutputParser.automaticLink(inCaptureFile: manualString + "\n") == nil,
          "sign-in parser: a code-callback link is never taken for the automatic one")
    check(SignInOutputParser.automaticLink(inCaptureFile: automaticString) == automatic,
          "sign-in parser: a last line without a newline still counts")

    let stripped = SignInOutputParser.stripTerminalEscapes("\u{1B}[31mred\u{1B}[0m \u{1B}]0;title\u{07}text\u{07}")
    check(stripped == "red text", "sign-in parser: CSI, OSC and a stray BEL are removed", stripped.debugDescription)

    let redactedManual = SignInOutputParser.redacted(manual)
    check(!redactedManual.contains("TESTSTATE456") && !redactedManual.contains("TESTCHALLENGE123") && !redactedManual.contains("="),
          "sign-in parser: the loggable form carries no query value", redactedManual)
    check(redactedManual == "https://claude.com/cai/oauth/authorize (returns to platform.claude.com/oauth/code/callback)",
          "sign-in parser: the loggable form names host, path and where it returns to", redactedManual)
    check(SignInOutputParser.redacted(automatic) == "https://claude.com/cai/oauth/authorize (returns to localhost:54545/callback)",
          "sign-in parser: the loggable form of the automatic link", SignInOutputParser.redacted(automatic))
}

// MARK: - Rules AppState acts on

@MainActor func runSignInRulesTests() {
    check(SignInCode.normalized("abc123#state456") == "abc123#state456", "sign-in code: a whole code#state is accepted")
    check(SignInCode.normalized("  abc123#state456 \n") == "abc123#state456", "sign-in code: surrounding whitespace is trimmed")
    for bad in ["", "   ", "abc123", "abc123#", "#state456", "a#b#c", "abc 123#state", "abc123#sta\nte", "abc\u{0}1#x"] {
        check(SignInCode.normalized(bad) == nil, "sign-in code: refuses \(bad.debugDescription)")
    }

    let finished: [SignInState] = [.succeeded(accountId: SignInTestKit.accountId), .failed(message: "x"), .cancelled]
    let running: [SignInState] = [.starting, .waitingForUser, .completing]
    check(finished.allSatisfy(\.isFinished) && !running.contains(where: \.isFinished),
          "sign-in state: only succeeded, failed and cancelled are finished")

    check(SignInGate.decide(claudeAvailable: false, isSwitching: true, isLoggingIn: true, current: .waitingForUser) == .claudeUnavailable,
          "sign-in gate: a missing Claude CLI is reported first, as before")
    check(SignInGate.decide(claudeAvailable: true, isSwitching: false, isLoggingIn: false, current: nil) == .allowed,
          "sign-in gate: nothing running allows a sign-in")
    check(SignInGate.decide(claudeAvailable: true, isSwitching: true, isLoggingIn: false, current: nil) == .busy,
          "sign-in gate: a switch in progress blocks it")
    check(SignInGate.decide(claudeAvailable: true, isSwitching: false, isLoggingIn: true, current: nil) == .busy,
          "sign-in gate: a sign-in in progress blocks another")
    check(SignInGate.decide(claudeAvailable: true, isSwitching: false, isLoggingIn: false, current: .waitingForUser) == .busy,
          "sign-in gate: a second sign-in from the other entry point while one is running is refused")
    check(SignInGate.decide(claudeAvailable: true, isSwitching: false, isLoggingIn: false, current: .completing) == .busy,
          "sign-in gate: nothing starts while the last sign-in's account is being saved")
    check(SignInGate.decide(claudeAvailable: true, isSwitching: false, isLoggingIn: false, current: .failed(message: "x")) == .allowed,
          "sign-in gate: a finished sign-in does not block the next")

    func status(_ loggedIn: Bool, _ email: String?, method: String = "claude.ai") -> AuthStatus {
        AuthStatus(loggedIn: loggedIn, authMethod: method, apiProvider: nil, email: email, orgId: nil, orgName: nil, subscriptionType: nil)
    }
    let a = Account(email: "a@x.com", displayName: "A")
    let b = Account(email: "b@x.com", displayName: "B")
    check(SignInResult.newAccount(status: status(false, nil), accounts: [a]) == .notLoggedIn,
          "sign-in result: not logged in after the CLI finished")
    check(SignInResult.newAccount(status: status(true, nil, method: "apiKeyHelper"), accounts: [a]) == .noIdentity,
          "sign-in result: a shadowing credential source hides the identity")
    check(SignInResult.newAccount(status: status(true, "b@x.com"), accounts: [a, b]) == .existing(accountId: b.id),
          "sign-in result: a new sign-in that lands on an account PixelSwitch already has is recognised")
    check(SignInResult.newAccount(status: status(true, "c@x.com"), accounts: [a, b]) == .new(email: "c@x.com"),
          "sign-in result: a new account is new")
    check(SignInResult.reauthentication(status: status(true, "a@x.com"), expectedEmail: "a@x.com") == .matches,
          "sign-in result: re-authenticating the right account matches")
    check(SignInResult.reauthentication(status: status(true, "b@x.com"), expectedEmail: "a@x.com") == .wrongAccount(signedInAs: "b@x.com"),
          "sign-in result: signing in to the wrong account in the browser is caught")
    check(SignInResult.reauthentication(status: status(false, nil), expectedEmail: "a@x.com") == .notLoggedIn,
          "sign-in result: re-authentication that did not log in")
    check(SignInResult.reauthentication(status: status(true, nil, method: "oauthToken"), expectedEmail: "a@x.com") == .noIdentity,
          "sign-in result: re-authentication hidden by a shadowing credential source")
}

// MARK: - The environment every claude subprocess gets

extension SignInTestKit {
    static func makeTempDirectory() -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("pixelswitch-signin-tests-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// Writes an executable /bin/sh script.
    static func writeScript(in directory: URL, named name: String, _ body: String) -> URL {
        let url = directory.appendingPathComponent(name)
        try? Data(body.utf8).write(to: url)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        return url
    }
}

@MainActor func runSignInEnvironmentTests() {
    let base = ["PATH": "/usr/bin:/bin", "HOME": "/wrong", "KEEP": "1"]
    let bare = ClaudeProcessEnvironment.make(claudePath: "claude", base: base, homeDirectory: "/Users/me")
    check(bare["PATH"] == "/opt/homebrew/bin:/usr/local/bin:/Users/me/.local/bin:/Users/me/.npm-global/bin:/usr/bin:/bin",
          "claude environment: the usual install folders go in front of the inherited PATH", bare["PATH"] ?? "nil")
    check(bare["HOME"] == "/Users/me" && bare["KEEP"] == "1", "claude environment: HOME is set and everything else is kept")
    check(ClaudeProcessEnvironment.make(claudePath: "claude", base: [:], homeDirectory: "/Users/me")["PATH"]?.hasSuffix(":/usr/bin:/bin") == true,
          "claude environment: no inherited PATH falls back to /usr/bin:/bin")

    let dir = SignInTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let realBin = dir.appendingPathComponent("node-bin", isDirectory: true)
    let linkBin = dir.appendingPathComponent("links", isDirectory: true)
    try? FileManager.default.createDirectory(at: realBin, withIntermediateDirectories: true)
    try? FileManager.default.createDirectory(at: linkBin, withIntermediateDirectories: true)
    let realClaude = SignInTestKit.writeScript(in: realBin, named: "claude", "#!/bin/sh\nexit 0\n")
    let linkedClaude = linkBin.appendingPathComponent("claude")
    try? FileManager.default.createSymbolicLink(at: linkedClaude, withDestinationURL: realClaude)
    let linked = ClaudeProcessEnvironment.make(claudePath: linkedClaude.path, base: base, homeDirectory: "/Users/me")
    let first = linked["PATH"]?.split(separator: ":").first.map(String.init) ?? ""
    check(first.hasSuffix("/node-bin"), "claude environment: a symlinked claude puts its real folder first, so an NVM claude finds node", first)
}

// MARK: - The link-capture helper

extension SignInTestKit {
    static func permissions(_ url: URL) -> Int {
        ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.posixPermissions] as? NSNumber)?.intValue ?? -1
    }

    /// Runs a program to completion and returns its exit status.
    static func run(_ executable: URL, arguments: [String], environment: [String: String]) -> Int32 {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return -1 }
        process.waitUntilExit()
        return process.terminationStatus
    }
}

@MainActor func runSignInCaptureTests() {
    let automatic = SignInTestKit.automaticURLString
    let parent = SignInTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: parent) }
    let id = UUID()
    do {
        let capture = try SignInLinkCapture.create(in: parent, id: id)
        check(capture.directory.lastPathComponent == "pixelswitch-signin-\(id.uuidString)", "capture: one folder per sign-in, named by its id")
        check(SignInTestKit.permissions(capture.directory) == 0o700, "capture: the folder is private (0700)",
              String(SignInTestKit.permissions(capture.directory), radix: 8))
        check(SignInTestKit.permissions(capture.helperURL) == 0o700, "capture: the helper is private and executable (0700)",
              String(SignInTestKit.permissions(capture.helperURL), radix: 8))
        check(SignInTestKit.permissions(capture.captureFileURL) == 0o600, "capture: the capture file is private (0600)",
              String(SignInTestKit.permissions(capture.captureFileURL), radix: 8))
        check(capture.readCaptured() == "", "capture: the capture file starts empty")
        check(capture.environmentOverrides == ["BROWSER": capture.helperURL.path, SignInLinkCapture.captureFileVariable: capture.captureFileURL.path],
              "capture: Claude Code is given BROWSER and the capture file's name")
        check(SignInLinkCapture.helperScript().hasPrefix("#!/bin/sh\n"), "capture: the helper is a /bin/sh script")

        // Run the helper exactly as Claude Code does: `$BROWSER <link>`, with its environment.
        let environment = capture.environmentOverrides.merging(["PATH": "/usr/bin:/bin"]) { _, new in new }
        check(SignInTestKit.run(capture.helperURL, arguments: [automatic], environment: environment) == 0,
              "capture: the helper exits 0, so Claude Code thinks the browser opened")
        check(capture.readCaptured() == automatic + "\n", "capture: the helper records the link on its own line")
        _ = SignInTestKit.run(capture.helperURL, arguments: ["https://example.com/second"], environment: environment)
        check(capture.readCaptured() == automatic + "\nhttps://example.com/second\n", "capture: a second call appends")
        check(SignInTestKit.permissions(capture.captureFileURL) == 0o600, "capture: the file stays 0600 after the helper wrote to it")
        _ = SignInTestKit.run(capture.helperURL, arguments: ["https://example.com/third"], environment: ["PATH": "/usr/bin:/bin"])
        check(capture.readCaptured().hasSuffix("https://example.com/third\n"), "capture: without the variable the helper still writes beside itself")

        capture.remove()
        check(!FileManager.default.fileExists(atPath: capture.directory.path), "capture: removing it leaves nothing behind")

        let stale = parent.appendingPathComponent(SignInLinkCapture.folderPrefix + "stale", isDirectory: true)
        let recent = parent.appendingPathComponent(SignInLinkCapture.folderPrefix + "recent", isDirectory: true)
        let unrelated = parent.appendingPathComponent("not-a-sign-in", isDirectory: true)
        for folder in [stale, recent, unrelated] { try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true) }
        let twoHoursAgo = Date().addingTimeInterval(-2 * 3600)
        try FileManager.default.setAttributes([.modificationDate: twoHoursAgo], ofItemAtPath: stale.path)
        try FileManager.default.setAttributes([.modificationDate: twoHoursAgo], ofItemAtPath: unrelated.path)
        let next = try SignInLinkCapture.create(in: parent, id: UUID())
        check(!FileManager.default.fileExists(atPath: stale.path), "capture: a leftover sign-in folder over an hour old is removed")
        check(FileManager.default.fileExists(atPath: recent.path) && FileManager.default.fileExists(atPath: unrelated.path),
              "capture: a recent sign-in folder and unrelated folders are kept")
        next.remove()
    } catch {
        check(false, "capture: set up", "\(error)")
    }
}

// MARK: - The real process runner and the main-queue clock

extension SignInTestKit {
    /// Mutable state a `@Sendable` callback can write to on the main actor.
    @MainActor final class Box<Value> {
        var value: Value
        init(_ value: Value) { self.value = value }
    }

    /// Runs the main run loop (which drains the main queue and main-actor
    /// tasks) until `condition` holds or `timeout` passes.
    @MainActor @discardableResult
    static func spin(until condition: () -> Bool, timeout: TimeInterval = 5) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition(), Date() < deadline {
            RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.01))
        }
        return condition()
    }
}

@MainActor func runSignInProcessTests() {
    let dir = SignInTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }
    let environment = ["PATH": "/usr/bin:/bin"]
    let runner = ProcessSignInRunner()

    let echo = SignInTestKit.writeScript(in: dir, named: "prompt-and-echo", """
        #!/bin/sh
        printf 'first line\\n'
        printf 'prompt > '
        IFS= read -r line || exit 3
        printf 'got:%s\\n' "$line"
        exit 7

        """)
    let output = SignInTestKit.Box(Data())
    let status = SignInTestKit.Box<Int32?>(nil)
    do {
        let handle = try runner.start(executable: echo, arguments: [], environment: environment,
                                      onOutput: { output.value.append($0) }, onExit: { status.value = $0 })
        let text = { String(decoding: output.value, as: UTF8.self) }
        check(SignInTestKit.spin(until: { text().contains("prompt > ") }),
              "process runner: a prompt with no newline arrives while the process is still running", text().debugDescription)
        check(status.value == nil, "process runner: the output arrives before any exit")
        check(handle.writeLine("abc#def"), "process runner: a line reaches the process's stdin")
        check(SignInTestKit.spin(until: { status.value != nil }), "process runner: the exit is reported")
        check(status.value == 7 && text().contains("got:abc#def"), "process runner: the exit status comes after the last output",
              "\(status.value.map(String.init) ?? "nil") \(text().debugDescription)")
        check(!handle.writeLine("again"), "process runner: writing after the process exited fails")
    } catch {
        check(false, "process runner: starts a script", "\(error)")
    }

    // Without F_SETNOSIGPIPE this write kills the whole test binary (and, in
    // the app, PixelSwitch itself) with SIGPIPE.
    let closesInput = SignInTestKit.writeScript(in: dir, named: "closes-stdin", "#!/bin/sh\nexec 0<&-\nprintf 'closed\\n'\nexec sleep 5\n")
    let closedOutput = SignInTestKit.Box(Data())
    let closedStatus = SignInTestKit.Box<Int32?>(nil)
    do {
        let handle = try runner.start(executable: closesInput, arguments: [], environment: environment,
                                      onOutput: { closedOutput.value.append($0) }, onExit: { closedStatus.value = $0 })
        _ = SignInTestKit.spin(until: { String(decoding: closedOutput.value, as: UTF8.self).contains("closed") })
        check(!handle.writeLine("abc#def"), "process runner: writing to a process that closed its stdin fails instead of raising SIGPIPE")
        handle.terminate()
        check(SignInTestKit.spin(until: { closedStatus.value != nil }), "process runner: terminate() stops a running process")
        check((closedStatus.value ?? 0) != 0, "process runner: a stopped process does not report success")
    } catch {
        check(false, "process runner: starts a script that closes stdin", "\(error)")
    }

    do {
        _ = try runner.start(executable: dir.appendingPathComponent("missing"), arguments: [], environment: environment,
                             onOutput: { _ in }, onExit: { _ in })
        check(false, "process runner: a missing binary throws")
    } catch {
        check(true, "process runner: a missing binary throws instead of hanging")
    }

    let scheduler = MainQueueSignInScheduler()
    let fired = SignInTestKit.Box(false)
    let cancelledFired = SignInTestKit.Box(false)
    scheduler.after(0.05) { fired.value = true }
    let cancelled = scheduler.after(0.05) { cancelledFired.value = true }
    cancelled.cancel()
    check(SignInTestKit.spin(until: { fired.value }), "main-queue clock: a timer fires")
    SignInTestKit.spin(until: { false }, timeout: 0.1)
    check(!cancelledFired.value, "main-queue clock: a cancelled timer never fires")
}

// MARK: - SignInSession against a fake CLI

extension SignInTestKit {
    final class FakeHandle: SignInProcessHandle, @unchecked Sendable {
        let onOutput: @MainActor @Sendable (Data) -> Void
        let onExit: @MainActor @Sendable (Int32) -> Void
        var linesWritten: [String] = []
        var terminateCalls = 0
        var killCalls = 0
        var inputClosed = false

        init(onOutput: @escaping @MainActor @Sendable (Data) -> Void, onExit: @escaping @MainActor @Sendable (Int32) -> Void) {
            self.onOutput = onOutput
            self.onExit = onExit
        }

        func writeLine(_ line: String) -> Bool {
            guard !inputClosed else { return false }
            linesWritten.append(line)
            return true
        }
        func terminate() { terminateCalls += 1 }
        func kill() { killCalls += 1 }
        func closeInput() { inputClosed = true }

        @MainActor func emit(_ text: String) { onOutput(Data(text.utf8)) }
        @MainActor func emit(bytes: Data) { onOutput(bytes) }
        @MainActor func exit(_ status: Int32) { onExit(status) }
    }

    struct Launched {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
    }

    final class FakeRunner: SignInProcessRunner, @unchecked Sendable {
        var launches: [Launched] = []
        var handles: [FakeHandle] = []
        var failure: (any Error)?

        func start(
            executable: URL,
            arguments: [String],
            environment: [String: String],
            onOutput: @escaping @MainActor @Sendable (Data) -> Void,
            onExit: @escaping @MainActor @Sendable (Int32) -> Void
        ) throws -> any SignInProcessHandle {
            if let failure { throw failure }
            launches.append(Launched(executable: executable, arguments: arguments, environment: environment))
            let handle = FakeHandle(onOutput: onOutput, onExit: onExit)
            handles.append(handle)
            return handle
        }
    }

    /// A clock that only moves when told to, firing due actions in order.
    @MainActor final class FakeScheduler: SignInScheduler {
        private(set) var now = Date()
        private var pending: [(due: Date, timer: SignInTimer, action: @MainActor @Sendable () -> Void)] = []

        func after(_ seconds: TimeInterval, _ action: @escaping @MainActor @Sendable () -> Void) -> SignInTimer {
            let timer = SignInTimer()
            pending.append((now.addingTimeInterval(seconds), timer, action))
            return timer
        }

        func advance(by seconds: TimeInterval) {
            let target = now.addingTimeInterval(seconds)
            while true {
                pending.removeAll { $0.timer.isCancelled }
                guard let next = pending.indices.filter({ pending[$0].due <= target }).min(by: { pending[$0].due < pending[$1].due }) else { break }
                let item = pending.remove(at: next)
                now = item.due
                item.action()
            }
            now = target
        }
    }

    /// A session wired to the fakes, counting `complete` and `onFinish` calls.
    @MainActor final class SessionRig {
        let runner = FakeRunner()
        let scheduler = FakeScheduler()
        let parent = SignInTestKit.makeTempDirectory()
        let completes = Box(0)
        let finishes = Box(0)
        let logs = Box<[String]>([])
        private(set) var session: SignInSession!

        init(purpose: SignInPurpose = .newAccount, result: SignInState = .succeeded(accountId: SignInTestKit.accountId)) {
            let completes = self.completes, finishes = self.finishes, logs = self.logs
            session = SignInSession(
                purpose: purpose,
                launch: SignInLaunch(executable: URL(fileURLWithPath: "/usr/local/bin/claude"),
                                     environment: ["PATH": "/usr/bin:/bin", "HOME": "/Users/test"]),
                runner: runner,
                scheduler: scheduler,
                captureParent: parent,
                log: { logs.value.append($0) },
                complete: { _ in
                    completes.value += 1
                    return result
                },
                onFinish: { _ in finishes.value += 1 }
            )
        }

        var handle: FakeHandle { runner.handles[runner.handles.count - 1] }

        var captureFolders: [String] {
            ((try? FileManager.default.contentsOfDirectory(atPath: parent.path)) ?? []).filter { $0.hasPrefix(SignInLinkCapture.folderPrefix) }
        }

        /// Appends to the capture file the way the helper does.
        func writeCapture(_ text: String) {
            guard let path = runner.launches.first?.environment[SignInLinkCapture.captureFileVariable],
                  let file = FileHandle(forWritingAtPath: path) else { return }
            _ = try? file.seekToEnd()
            try? file.write(contentsOf: Data(text.utf8))
            try? file.close()
        }

        func cleanUp() { try? FileManager.default.removeItem(at: parent) }
    }
}

@MainActor func runSignInSessionTests() {
    typealias Rig = SignInTestKit.SessionRig
    let manual = URL(string: SignInTestKit.manualURLString)!
    let automatic = URL(string: SignInTestKit.automaticURLString)!
    let exitMessage = "Claude Code's sign-in stopped (exit status 1)."
    let timedOut = "Sign-in timed out after 15 minutes. Start it again when you're ready."
    let fallbackNotice = "Couldn't read the sign-in link; opened your default browser instead."

    check(SignInSession.arguments(for: .newAccount) == ["auth", "login"], "session: a new sign-in runs `claude auth login`")
    check(SignInSession.arguments(for: .reauthenticate(accountId: SignInTestKit.accountId, email: "a@x.com")) == ["auth", "login", "--email", "a@x.com"],
          "session: re-authentication pre-fills the account's email")

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        check(rig.session.state == .starting && rig.runner.launches.isEmpty, "session: nothing runs until start(), so AppState's backup comes first")
        rig.session.start()
        let launch = rig.runner.launches.first
        check(rig.runner.launches.count == 1 && launch?.arguments == ["auth", "login"], "session: start() runs the CLI once")
        check(launch?.executable.path == "/usr/local/bin/claude", "session: the configured claude binary is run")
        check(launch?.environment["PATH"] == "/usr/bin:/bin" && launch?.environment["HOME"] == "/Users/test",
              "session: the CLI keeps the PATH and HOME it was given")
        let browser = launch?.environment["BROWSER"] ?? ""
        check(browser.hasSuffix("/capture-sign-in-link") && FileManager.default.isExecutableFile(atPath: browser),
              "session: BROWSER is the capture helper, so no browser opens", browser)
        check(launch?.environment[SignInLinkCapture.captureFileVariable]?.hasPrefix(rig.parent.path) == true,
              "session: the capture file lives in the session's own folder")
        check(rig.captureFolders.count == 1, "session: one capture folder while it runs")
        check(rig.session.state == .starting, "session: still starting while no link is known")
        rig.session.start()
        check(rig.runner.launches.count == 1, "session: a second start() launches nothing")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.handle.emit(SignInTestKit.observedOutput)
        check(rig.session.manualLink == manual && rig.session.state == .waitingForUser,
              "session: the manual link alone moves it to waitingForUser")
        rig.scheduler.advance(by: SignInSession.linkWait + 1)
        check(rig.handle.terminateCalls == 0 && rig.runner.launches.count == 1, "session: once a link is known the 10-second fallback never fires")
        rig.writeCapture(SignInTestKit.automaticURLString + "\n")
        rig.scheduler.advance(by: SignInSession.capturePollInterval)
        check(rig.session.automaticLink == automatic, "session: the automatic link may arrive after the manual one")
        let logged = rig.logs.value.joined(separator: "\n")
        check(!logged.contains("TESTSTATE456") && !logged.contains("TESTCHALLENGE123"), "session: the log never carries a link's state or code_challenge", logged)
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.writeCapture(SignInTestKit.automaticURLString + "\n")
        check(rig.session.automaticLink == nil, "session: the capture file is read on the next poll")
        rig.scheduler.advance(by: SignInSession.capturePollInterval)
        check(rig.session.automaticLink == automatic && rig.session.manualLink == nil && rig.session.state == .waitingForUser,
              "session: the automatic link alone moves it to waitingForUser")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        let bytes = Data(SignInTestKit.observedOutput.utf8)
        let ellipsis = bytes.firstIndex(of: 0xE2)!
        let cuts = [0, ellipsis + 1, ellipsis + 80, bytes.count]
        for (from, to) in zip(cuts, cuts.dropFirst()) { rig.handle.emit(bytes: bytes.subdata(in: from..<to)) }
        check(rig.session.manualLink == manual, "session: output split mid-character and mid-link is put back together")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        check(!rig.session.submitCode("abc#def"), "session: no code is accepted before any link is shown")
        rig.handle.emit(SignInTestKit.observedOutput)
        check(!rig.session.submitCode("abcdef") && rig.handle.linesWritten.isEmpty, "session: a code without # is refused and nothing is sent")
        check(rig.session.submitCode("  abc#def \n") && rig.handle.linesWritten == ["abc#def"] && rig.session.codeSubmitted,
              "session: a whole code#state is sent as one trimmed line")
        check(!rig.session.submitCode("xyz#uvw") && rig.handle.linesWritten.count == 1, "session: a second code is refused")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.writeCapture(SignInTestKit.automaticURLString + "\n")
        rig.scheduler.advance(by: SignInSession.capturePollInterval)
        rig.handle.emit(SignInTestKit.observedOutput)
        check(rig.session.submitCode("abc#def") && rig.handle.linesWritten == ["abc#def"],
              "session: with the automatic link shown the code still works, for a browser that cannot reach localhost")
    }

    do {
        let rig = Rig(result: .succeeded(accountId: SignInTestKit.accountId))
        defer { rig.cleanUp() }
        rig.session.start()
        rig.handle.emit(SignInTestKit.observedOutput)
        rig.handle.exit(0)
        check(rig.session.state == .completing, "session: exit 0 moves it to completing while the account is saved")
        check(rig.captureFolders.isEmpty, "session: the capture folder is removed as soon as Claude Code has finished")
        rig.session.cancel()
        check(rig.session.state == .completing && rig.handle.terminateCalls == 0, "session: cancel is ignored while the account is being saved")
        check(SignInTestKit.spin(until: { rig.session.state.isFinished }), "session: the save step runs")
        check(rig.session.state == .succeeded(accountId: SignInTestKit.accountId) && rig.completes.value == 1 && rig.finishes.value == 1,
              "session: the save step's result is the final state, and finishing is reported once")
        rig.session.cancel()
        check(rig.session.state == .succeeded(accountId: SignInTestKit.accountId) && rig.finishes.value == 1, "session: cancel after success changes nothing")
    }

    do {
        let rig = Rig(result: .failed(message: "Could not capture credentials"))
        defer { rig.cleanUp() }
        rig.session.start()
        rig.handle.emit(SignInTestKit.observedOutput)
        rig.handle.exit(0)
        SignInTestKit.spin(until: { rig.session.state.isFinished })
        check(rig.session.state == .failed(message: "Could not capture credentials") && rig.finishes.value == 1,
              "session: a failed save step fails the sign-in")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.handle.emit(SignInTestKit.observedOutput)
        rig.handle.exit(1)
        check(rig.session.state == .failed(message: exitMessage) && rig.completes.value == 0 && rig.finishes.value == 1,
              "session: a non-zero exit fails without running the save step", "\(rig.session.state)")
        check(rig.captureFolders.isEmpty && rig.handle.inputClosed, "session: a failed sign-in leaves nothing behind")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.runner.failure = CocoaError(.fileNoSuchFile)
        rig.session.start()
        if case .failed(let message) = rig.session.state {
            check(message.hasPrefix("Couldn't start Claude Code:"), "session: a missing claude binary fails with a clear message", message)
        } else {
            check(false, "session: a missing claude binary fails with a clear message", "\(rig.session.state)")
        }
        check(rig.finishes.value == 1 && rig.captureFolders.isEmpty, "session: a launch failure finishes once and leaves nothing behind")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.handle.emit(SignInTestKit.observedOutput)
        rig.session.cancel()
        check(rig.handle.terminateCalls == 1 && rig.session.state == .waitingForUser, "session: cancel sends SIGTERM and waits for the exit")
        check(!rig.session.submitCode("abc#def"), "session: no code is accepted while stopping")
        rig.scheduler.advance(by: SignInSession.killAfter)
        check(rig.handle.killCalls == 1, "session: SIGKILL follows 2 seconds later if the CLI is still running")
        rig.handle.exit(137)
        check(rig.session.state == .cancelled && rig.finishes.value == 1 && rig.completes.value == 0, "session: the exit after cancel ends it as cancelled")
        check(rig.captureFolders.isEmpty && rig.handle.inputClosed, "session: a cancelled sign-in leaves nothing behind")
        rig.session.cancel()
        check(rig.handle.terminateCalls == 1 && rig.finishes.value == 1, "session: cancelling twice does nothing more")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.session.cancel()
        rig.handle.exit(143)
        rig.scheduler.advance(by: 10)
        check(rig.session.state == .cancelled && rig.handle.killCalls == 0, "session: no SIGKILL when SIGTERM was enough")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.session.cancel()
        rig.scheduler.advance(by: SignInSession.giveUpAfter)
        check(rig.session.state == .cancelled && rig.finishes.value == 1, "session: with no exit reported it still ends 5 seconds after cancel")
        rig.handle.exit(0)
        check(rig.session.state == .cancelled && rig.completes.value == 0 && rig.finishes.value == 1, "session: a late exit report is ignored")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.cancel()
        check(rig.session.state == .cancelled && rig.finishes.value == 1, "session: cancelling during the backup ends it at once")
        rig.session.start()
        check(rig.runner.launches.isEmpty && rig.captureFolders.isEmpty, "session: a sign-in cancelled during the backup never launches anything")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.handle.emit(SignInTestKit.observedOutput)
        rig.scheduler.advance(by: SignInSession.overallLimit - 1)
        check(rig.handle.terminateCalls == 0, "session: still waiting just before 15 minutes")
        rig.scheduler.advance(by: 1)
        check(rig.handle.terminateCalls == 1, "session: stopped at 15 minutes")
        rig.handle.exit(143)
        check(rig.session.state == .failed(message: timedOut) && rig.finishes.value == 1, "session: a 15-minute timeout fails with its own message", "\(rig.session.state)")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.scheduler.advance(by: SignInSession.linkWait - 0.5)
        check(rig.handle.terminateCalls == 0, "session: no fallback before 10 seconds")
        rig.scheduler.advance(by: 0.5)
        let first = rig.runner.handles[0]
        check(first.terminateCalls == 1 && rig.runner.launches.count == 1, "session: at 10 seconds with no link the first CLI is stopped before anything else runs")
        first.exit(143)
        check(rig.runner.launches.count == 2, "session: then `claude auth login` runs again")
        let second = rig.runner.launches.count == 2 ? rig.runner.launches[1] : nil
        check(second?.environment["BROWSER"] == nil && second?.environment[SignInLinkCapture.captureFileVariable] == nil && second?.environment["PATH"] == "/usr/bin:/bin",
              "session: the fallback runs it without the helper, so Claude Code opens the default browser")
        check(second?.arguments == ["auth", "login"], "session: the fallback keeps the same arguments")
        check(rig.session.state == .waitingForUser && rig.session.automaticLink == nil && rig.session.manualLink == nil && rig.session.notice == fallbackNotice,
              "session: the fallback shows neither link and says why")
        check(rig.captureFolders.isEmpty, "session: the fallback leaves no capture folder")
        first.emit(SignInTestKit.observedOutput)
        check(rig.session.manualLink == nil, "session: late output from the stopped CLI is ignored")
        rig.handle.exit(0)
        SignInTestKit.spin(until: { rig.session.state.isFinished })
        check(rig.session.state == .succeeded(accountId: SignInTestKit.accountId) && rig.finishes.value == 1,
              "session: the fallback still completes when the CLI exits")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.scheduler.advance(by: SignInSession.linkWait)
        rig.session.cancel()
        rig.runner.handles[0].exit(143)
        check(rig.session.state == .cancelled && rig.runner.launches.count == 1, "session: cancelling while the fallback is pending runs nothing more")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.scheduler.advance(by: SignInSession.linkWait)
        rig.runner.handles[0].exit(143)
        rig.scheduler.advance(by: SignInSession.overallLimit)
        check(rig.runner.handles.count == 2 && rig.runner.handles[1].terminateCalls == 1, "session: the 15-minute limit covers the fallback too")
        rig.runner.handles[1].exit(143)
        check(rig.session.state == .failed(message: timedOut), "session: the fallback times out with the timeout message")
    }

    do {
        let rig = Rig()
        defer { rig.cleanUp() }
        rig.session.start()
        rig.scheduler.advance(by: SignInSession.linkWait)
        rig.runner.failure = CocoaError(.fileNoSuchFile)
        rig.runner.handles[0].exit(143)
        if case .failed(let message) = rig.session.state {
            check(message.hasPrefix("Couldn't start Claude Code:") && rig.finishes.value == 1, "session: a fallback that cannot start fails once", message)
        } else {
            check(false, "session: a fallback that cannot start fails once", "\(rig.session.state)")
        }
    }
}

// MARK: - End to end: the real runner, the real helper, a stand-in claude

@MainActor func runSignInEndToEndTests() {
    let manual = URL(string: SignInTestKit.manualURLString)!
    let automatic = URL(string: SignInTestKit.automaticURLString)!
    let dir = SignInTestKit.makeTempDirectory()
    defer { try? FileManager.default.removeItem(at: dir) }

    // Behaves like Claude Code 2.1.280's `claude auth login`: prints the three
    // lines, hands the automatic link to $BROWSER, then waits for code#state.
    let fakeClaude = SignInTestKit.writeScript(in: dir, named: "claude", """
        #!/bin/sh
        [ "$1 $2" = "auth login" ] || exit 64
        printf 'Opening browser to sign in\\342\\200\\246\\n'
        printf "If the browser didn't open, visit: %s\\n" "$SIGNIN_TEST_MANUAL"
        printf 'Paste code here if prompted > '
        "$BROWSER" "$SIGNIN_TEST_AUTOMATIC" || exit 65
        IFS= read -r line || exit 66
        case "$line" in
          ?*#?*) printf '\\nLogin successful.\\n'; exit 0 ;;
          *) printf 'Invalid code. Please make sure the full code was copied.\\n'; exit 1 ;;
        esac

        """)
    let environment = ClaudeProcessEnvironment.make(claudePath: fakeClaude.path, base: ProcessInfo.processInfo.environment, homeDirectory: NSHomeDirectory())
        .merging(["SIGNIN_TEST_MANUAL": SignInTestKit.manualURLString, "SIGNIN_TEST_AUTOMATIC": SignInTestKit.automaticURLString]) { _, new in new }

    let finishes = SignInTestKit.Box(0)
    let session = SignInSession(
        purpose: .newAccount,
        launch: SignInLaunch(executable: fakeClaude, environment: environment),
        runner: ProcessSignInRunner(),
        scheduler: MainQueueSignInScheduler(),
        captureParent: dir,
        complete: { _ in .succeeded(accountId: SignInTestKit.accountId) },
        onFinish: { _ in finishes.value += 1 }
    )
    session.start()
    check(SignInTestKit.spin(until: { session.manualLink != nil && session.automaticLink != nil }),
          "end to end: both links arrive from a real process, the automatic one through the real helper")
    check(session.manualLink == manual && session.automaticLink == automatic && session.state == .waitingForUser,
          "end to end: they are the right links, and the session waits for the user")
    check(session.submitCode("abc#def"), "end to end: the code reaches the process")
    check(SignInTestKit.spin(until: { session.state.isFinished }), "end to end: the process exits and the session finishes")
    check(session.state == .succeeded(accountId: SignInTestKit.accountId) && finishes.value == 1, "end to end: it succeeds, reported once")
    let leftovers = ((try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []).filter { $0.hasPrefix(SignInLinkCapture.folderPrefix) }
    check(leftovers.isEmpty, "end to end: nothing is left behind", "\(leftovers)")

    let waits = SignInTestKit.writeScript(in: dir, named: "claude-waits", """
        #!/bin/sh
        printf "If the browser didn't open, visit: %s\\n" "$SIGNIN_TEST_MANUAL"
        IFS= read -r line
        exit 0

        """)
    let cancelled = SignInSession(
        purpose: .newAccount,
        launch: SignInLaunch(executable: waits, environment: environment),
        runner: ProcessSignInRunner(),
        scheduler: MainQueueSignInScheduler(),
        captureParent: dir,
        complete: { _ in .succeeded(accountId: SignInTestKit.accountId) },
        onFinish: { _ in }
    )
    cancelled.start()
    _ = SignInTestKit.spin(until: { cancelled.state == .waitingForUser })
    let cancelAt = Date()
    cancelled.cancel()
    check(SignInTestKit.spin(until: { cancelled.state.isFinished }), "end to end: cancel stops a real process")
    check(cancelled.state == .cancelled && Date().timeIntervalSince(cancelAt) < SignInSession.giveUpAfter,
          "end to end: the process's own exit ends it as cancelled", "\(cancelled.state)")
}
