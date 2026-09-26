// Sign-in links (Part B): the output parser, the rules AppState acts on, the
// link-capture helper, the process runner and the SignInSession state machine.
// Called from main.swift. Uses the global `check` defined there.
import Foundation

@MainActor func runSignInTests() {
    runSignInParserTests()
    runSignInRulesTests()
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
