# Part B: Sign-In Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (Founder, 2026-09-25: work in a single lane with no subagents on this laptop, so use superpowers:executing-plans.)

**Goal:** Adding or re-signing an account no longer throws open the default browser. PixelSwitch runs `claude auth login` itself, catches both sign-in links, and shows them in a small floating window with Open in default browser, Open in (any installed browser), Copy link, and a code box for signing in on another device.

**Architecture:** Claude Code runs `$BROWSER <link>` when `BROWSER` is set, so PixelSwitch points it at a tiny helper script (written fresh into a private temporary folder for each sign-in) that records the automatic (localhost-redirect) link instead of opening a browser. The manual (code-callback) link is read from the CLI's output as it streams. A `SignInSession` state machine (starting → waitingForUser → completing → succeeded/failed/cancelled) owns the process, the links, the 10-second fallback and the 15-minute timeout; it runs against a process-runner protocol and a clock protocol, so the unit harness drives it with fakes. `AppState.startSignIn(_:)` wraps the session in the existing backup-then-capture steps. A `SignInWindowController` shows an `NSPanel` whenever `AppState.currentSignIn` changes, so the sign-in survives the popover closing.

**Tech Stack:** Swift 6, SwiftUI + AppKit (`NSPanel`, `NSWorkspace`, `NSPasteboard`), Foundation `Process`/`Pipe`, Combine, macOS 14+, XcodeGen, the repo's swiftc unit harness (`Tests/run-unit-tests.sh`, not XCTest), `.lproj/Localizable.strings` in five languages.

**Spec:** `docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md` (sections 1, 2, 4, 6, 8 and 9). Founder's verbatim requirement: feature 3 in `docs/decisions/2026-09-24-remote-control-answers.md`. Part A (`docs/superpowers/plans/2026-09-25-part-a-auto-switch-rules.md`) is merged before this part starts; Part C calls the names in section 9 exactly.

**How this plan was checked before it was handed over:** every code block in Tasks 1–7 was written and run in a scratch copy of the repository (commit `aa20916`, before Part A): the unit harness passed (264 = 118 + the 146 checks this plan adds) and `scripts/typecheck.sh` passed with all new files. The per-section check counts below were read from that run. The generator that produced this document asserts that every "replace X with Y" edit reproduces the tested files byte for byte against today's `main`. Part A does not touch any file or function this plan edits except `Tests/UnitTests/main.swift`, `Tests/run-unit-tests.sh`, the five string files (both parts only append) and `SettingsAccountsTab.swift` (which Part A creates and Task 7 extends), so the expected totals assume Part A's final count of 205.

## Global Constraints

- Swift 6 language mode everywhere (`swiftc -swift-version 6` in the harness; `SWIFT_VERSION: "6.0"` in `project.yml`).
- Deployment target macOS 14.0. No API newer than macOS 14.
- `project.yml` is the only source of truth for the Xcode project. Never edit `.pbxproj` or `Info.plist`. New files under `PixelSwitch/` are picked up automatically; run `xcodegen generate` after adding one.
- No new package dependencies.
- This Mac has no Xcode, only the Command Line Tools. **Never run `xcodebuild`.** The app check is `bash scripts/typecheck.sh` (prints `app: type-check OK (N files)`). The full build, signing and notarization run in CI, once, in Task 8.
- Unit tests: `bash Tests/run-unit-tests.sh`, which prints `N passed, M failed` last. Baseline before Task 1 (after Part A): `205 passed, 0 failed`. Only pure files go in the harness: no `AppState`, no SwiftUI views, no AppKit, no `FileLog`, no WidgetKit.
- Test convention (spec section 9): Part B's tests live in `Tests/UnitTests/SignInTests.swift`, which defines `@MainActor func runSignInTests()` and uses the global `check(_:_:_:)` from `main.swift`. `main.swift` gets exactly one call line, `runSignInTests()`, placed after Part A's `runAutoSwitchRulesTests()` and before the final `print`. Shared test helpers live in `enum SignInTestKit` so nothing collides with names in `main.swift` or Part A's test file.
- **Deviation from the spec's wording, decided by the orchestrator:** the `BROWSER` helper is NOT a script in the app bundle. It is written at sign-in start into a fresh folder under `FileManager.default.temporaryDirectory` named `pixelswitch-signin-<session uuid>` (folder 0700, helper 0700, capture file 0600) and deleted when the session finishes; folders over an hour old left by a crash are removed at the next sign-in. Reason: no bundle resource or code-signing change, and nothing left behind.
- Never print or log a link's `code`, `state` or `code_challenge` values, a pasted code, or any token. Log `SignInOutputParser.redacted(url)` only.
- Names and types fixed by spec section 9, produced exactly: `SignInPurpose`, `SignInState` (with `isFinished`), `SignInSession` (`id`, `purpose`, `startedAt`, `state`, `automaticLink`, `manualLink`, `submitCode(_:) -> Bool`, `cancel()`), `SignInOutputParser.manualLink(in:)` and `.automaticLink(inCaptureFile:)`, `BrowserApp` and `BrowserOpener.installedBrowsers()` / `.open(_:in:)`, and on `AppState`: `currentSignIn`, `SignInStartError { case busy, claudeUnavailable }`, `startSignIn(_:) throws -> SignInSession`. `loginNewAccount()` and `reauthenticateAccount(_:)` remain as wrappers.
- Every commit message ends with a blank line followed by `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Work on the branch `part-b-sign-in-links`, created from `main` after Part A has merged.

## Decisions this plan makes that the spec left open

1. **`loginNewAccount()` and `reauthenticateAccount(_:)` are no longer `async`.** They start a session and return at once; the popover's buttons call them directly instead of `Task { await ... }`. `ClaudeService.login()` is removed (nothing else called it).
2. **`SignInSession` publishes two extra properties** beyond section 9: `notice` (the fallback message) and `codeSubmitted` (a code was handed over). Part C may read them.
3. **The environment every `claude` subprocess gets moves into `ClaudeProcessEnvironment.make(...)`**, used by both `runClaude` and the sign-in, so the two can never drift. Behaviour is unchanged (pinned by tests).
4. **Pure rules are split out** so the harness covers what `AppState` decides: `SignInCode.normalized(_:)` (the `code#state` shape), `SignInGate.decide(...)` (busy / Claude missing / allowed) and `SignInResult` (how `claude auth status` after sign-in is classified).
5. **`AppState.dismissSignIn()`** forgets a finished sign-in (the window's Close). A finished session otherwise stays in `currentSignIn`, so its outcome stays readable.
6. **The sign-in window is a floating `NSPanel`.** It closes itself on success or cancel, stays open with the reason on failure, and closing it while the sign-in runs cancels the sign-in (no hidden process holding the sign-in lock). While the account is being saved (`completing`) closing only hides it.
7. **A new sign-in while one runs is refused** with "A sign-in is already in progress." and brings the running window to the front, from either entry point.
8. **A code is accepted once per sign-in**, and the "another device" box also works on this Mac when a browser cannot reach localhost.
9. **"Add Current Account" in Settings keeps the popover's confirmation dialog.**
10. **The manual acceptance checks** (a real sign-in in two browsers) can be done once on the combined build after Part C instead of on a Part B build, to save the founder a round of installing test builds. Task 8 says which.

## Review Focus

1. **The user closes the sign-in window mid-flow.** Expected: the sign-in is cancelled, the CLI is stopped (SIGTERM, then SIGKILL after 2 s), nothing is left running or on disk, and a new sign-in can start. Tests: Task 4 ("cancel sends SIGTERM…", "SIGKILL follows…", "a cancelled sign-in leaves nothing behind", "end to end: cancel stops a real process"); the window wiring is Task 6's manual check 4.
2. **The user signs in to the WRONG account in the browser while re-authenticating.** Expected: nothing is overwritten and the message names both addresses. Test: Task 2 ("signing in to the wrong account in the browser is caught"); Task 5 keeps the existing email check.
3. **`claude` missing, moved, or a future version that prints something else.** Expected: missing → a clear failure at once; changed output → the 10-second fallback runs today's default-browser behaviour. Tests: Task 4 ("a missing claude binary fails with a clear message", the fallback cases); Task 1 (reworded and noisy output).
4. **The browser cannot reach the CLI's localhost callback** (another device, a locked-down browser). Expected: the manual link plus the code box still finishes the sign-in. Tests: Task 4 ("with the automatic link shown the code still works…"), Task 1 (the automatic link is never taken for the manual one), end-to-end code submit.
5. **A second sign-in started from the other entry point (popover vs Settings, later the CLI) while one runs, or while a switch runs.** Expected: refused, nothing launched. Tests: Task 2 (the gate cases).

---

### Task 1: Read the two sign-in links out of the CLI's output

**Files:**
- Create: `PixelSwitch/Services/SignInOutputParser.swift`
- Create: `Tests/UnitTests/SignInTests.swift`
- Modify: `Tests/UnitTests/main.swift` (one call line before the final `print`)
- Modify: `Tests/run-unit-tests.sh` (two new file lines)

**Interfaces:**
- Consumes: nothing.
- Produces: `enum SignInOutputParser` with `static func manualLink(in output: String) -> URL?`, `static func automaticLink(inCaptureFile contents: String) -> URL?`, `static func stripTerminalEscapes(_:) -> String`, `static func hyperlinkTargets(in:) -> [String]`, `static func httpsURLs(in:) -> [URL]`, `static func redirectURI(of:) -> URL?`, `static func redirectsToLocalhost(_:) -> Bool`, `static func redirectsToCodeCallback(_:) -> Bool`, `static func redacted(_:) -> String`. `@MainActor func runSignInTests()` and `enum SignInTestKit`.

- [ ] **Step 0: Create the branch**

```bash
cd /Users/ahamade/Documents/GitHub/PixelSwitch
git checkout main && git pull --ff-only
git checkout -b part-b-sign-in-links
bash Tests/run-unit-tests.sh | tail -1
```

Expected: `205 passed, 0 failed` (Part A merged).

- [ ] **Step 1: Write the failing test**

Create `Tests/UnitTests/SignInTests.swift` with exactly this content:

```swift
// Sign-in links (Part B): the output parser, the rules AppState acts on, the
// link-capture helper, the process runner and the SignInSession state machine.
// Called from main.swift. Uses the global `check` defined there.
import Foundation

@MainActor func runSignInTests() {
    runSignInParserTests()
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
```

In `Tests/UnitTests/main.swift`, replace:

```swift
runAutoSwitchRulesTests()
```

with:

```swift
runAutoSwitchRulesTests()
runSignInTests()
```

In `Tests/run-unit-tests.sh`, replace the line `  Tests/UnitTests/main.swift \` with:

```bash
  PixelSwitch/Services/SignInOutputParser.swift \
  Tests/UnitTests/SignInTests.swift \
  Tests/UnitTests/main.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors, including `cannot find 'SignInOutputParser' in scope` (the source file does not exist yet, so the script also reports it missing).

- [ ] **Step 3: Implement**

Create `PixelSwitch/Services/SignInOutputParser.swift`:

```swift
import Foundation

/// Reads the two sign-in links out of what `claude auth login` produces.
///
/// Claude Code 2.1.280 prints, in order: `Opening browser to sign in…`, then
/// `If the browser didn't open, visit: <manual link>`, then the prompt
/// `Paste code here if prompted > ` with no newline. The manual link sends the
/// browser back to `https://platform.claude.com/oauth/code/callback`, where the
/// page shows a code to paste back. The automatic link, which Claude Code hands
/// to `$BROWSER`, sends it back to `http://localhost:<port>/callback`, served by
/// the CLI itself. The URL is formatted by a terminal-hyperlink helper, so OSC-8
/// wrapping and colour codes are tolerated even though none were seen.
///
/// Pure: no state, no I/O. Never log what these functions return; log
/// `redacted(_:)` instead, because the query carries `state` and `code_challenge`.
enum SignInOutputParser {

    /// The manual (code-callback) link: the sign-in URL on the `visit:` line
    /// (one with a `redirect_uri` that is not localhost), else the first https
    /// URL anywhere whose `redirect_uri` is a `/oauth/code/callback` URL.
    static func manualLink(in output: String) -> URL? {
        let visible = stripTerminalEscapes(output)
        for line in visible.split(whereSeparator: \.isNewline) {
            guard let marker = line.range(of: "visit:", options: .caseInsensitive) else { continue }
            let onLine = httpsURLs(in: String(line[marker.upperBound...]))
            if let url = onLine.first(where: { redirectURI(of: $0) != nil && !redirectsToLocalhost($0) }) {
                return url
            }
        }
        let candidates = hyperlinkTargets(in: output).compactMap(httpsURL) + httpsURLs(in: visible)
        return candidates.first(where: redirectsToCodeCallback)
    }

    /// The automatic (localhost) link from the capture file the `$BROWSER`
    /// helper appends to: the first URL, in file order, whose `redirect_uri` is
    /// a localhost URL.
    static func automaticLink(inCaptureFile contents: String) -> URL? {
        let visible = stripTerminalEscapes(contents)
        for line in visible.split(whereSeparator: \.isNewline) {
            if let url = httpsURLs(in: String(line)).first(where: redirectsToLocalhost) {
                return url
            }
        }
        return nil
    }

    /// `text` without terminal escape sequences: OSC (ended by BEL or ESC \),
    /// CSI (parameters, then one final byte) and two-character escapes. A lone
    /// carriage return becomes a newline; other control characters are dropped.
    static func stripTerminalEscapes(_ text: String) -> String {
        let scalars = Array(text.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0
        while i < scalars.count {
            let c = scalars[i]
            if c == "\u{1B}" {
                guard i + 1 < scalars.count else { break }
                let next = scalars[i + 1]
                if next == "]" {
                    i += 2
                    while i < scalars.count {
                        if scalars[i] == "\u{07}" { i += 1; break }
                        if scalars[i] == "\u{1B}", i + 1 < scalars.count, scalars[i + 1] == "\\" { i += 2; break }
                        i += 1
                    }
                } else if next == "[" {
                    i += 2
                    while i < scalars.count, !(0x40...0x7E).contains(scalars[i].value) { i += 1 }
                    i += 1
                } else {
                    i += 2
                }
                continue
            }
            if c == "\r" {
                out.append("\n")
            } else if c == "\n" || c == "\t" || c.properties.generalCategory != .control {
                out.append(c)
            }
            i += 1
        }
        return String(out)
    }

    /// The targets of OSC-8 hyperlinks (`ESC ] 8 ; params ; URI ST`), in order.
    static func hyperlinkTargets(in text: String) -> [String] {
        let scalars = Array(text.unicodeScalars)
        var targets: [String] = []
        var i = 0
        while i + 1 < scalars.count {
            guard scalars[i] == "\u{1B}", scalars[i + 1] == "]" else { i += 1; continue }
            var j = i + 2
            var body = String.UnicodeScalarView()
            while j < scalars.count {
                if scalars[j] == "\u{07}" { j += 1; break }
                if scalars[j] == "\u{1B}", j + 1 < scalars.count, scalars[j + 1] == "\\" { j += 2; break }
                body.append(scalars[j])
                j += 1
            }
            let parts = String(body).split(separator: ";", maxSplits: 2, omittingEmptySubsequences: false)
            if parts.count == 3, parts[0] == "8", !parts[2].isEmpty {
                targets.append(String(parts[2]))
            }
            i = j
        }
        return targets
    }

    /// Every https URL in `text`, in order. A URL ends at whitespace, a control
    /// character or a quote or angle bracket; trailing punctuation is dropped.
    static func httpsURLs(in text: String) -> [URL] {
        var urls: [URL] = []
        var rest = Substring(text)
        while let start = rest.range(of: "https://", options: .caseInsensitive) {
            let tail = rest[start.lowerBound...]
            let end = tail.firstIndex { ch in
                ch.isWhitespace || ch.unicodeScalars.contains { $0.properties.generalCategory == .control } || "\"'<>`".contains(ch)
            } ?? tail.endIndex
            var token = String(tail[..<end])
            while let last = token.last, ".,;:!?)]}".contains(last) { token.removeLast() }
            if let url = httpsURL(token) { urls.append(url) }
            rest = tail[end...]
        }
        return urls
    }

    /// Where the sign-in page sends the browser back to, percent-decoded.
    static func redirectURI(of url: URL) -> URL? {
        guard let value = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "redirect_uri" })?.value else { return nil }
        return URL(string: value)
    }

    /// True when the link finishes through Claude Code's own listener on this Mac.
    static func redirectsToLocalhost(_ url: URL) -> Bool {
        guard let redirect = redirectURI(of: url), let scheme = redirect.scheme?.lowercased(),
              scheme == "http" || scheme == "https", let host = redirect.host?.lowercased() else { return false }
        return host == "localhost" || host == "127.0.0.1" || host == "::1" || host == "[::1]"
    }

    /// True when the link ends on the page that shows a code to paste back.
    static func redirectsToCodeCallback(_ url: URL) -> Bool {
        guard let redirect = redirectURI(of: url), redirect.scheme?.lowercased() == "https" else { return false }
        return redirect.path.hasSuffix("/oauth/code/callback")
    }

    /// A form of `url` that is safe to log: scheme, host and path, and where it
    /// sends the browser back to. No query value (`state`, `code_challenge`,
    /// `code`) ever appears in it.
    static func redacted(_ url: URL) -> String {
        var text = "\(url.scheme ?? "?")://\(url.host ?? "?")\(url.path)"
        if let redirect = redirectURI(of: url) {
            let port = redirect.port.map { ":\($0)" } ?? ""
            text += " (returns to \(redirect.host ?? "?")\(port)\(redirect.path))"
        }
        return text
    }

    private static func httpsURL(_ string: String) -> URL? {
        guard let url = URL(string: string), url.scheme?.lowercased() == "https", url.host != nil else { return nil }
        return url
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `224 passed, 0 failed` (205 + 19).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK` (one file more than before).

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/SignInOutputParser.swift Tests/UnitTests/SignInTests.swift Tests/UnitTests/main.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Sign-in links: read both links out of claude auth login's output

The manual link comes from the visit: line (code-callback redirect); the automatic
link from the BROWSER helper's capture file (localhost redirect). Tolerates OSC-8
and colour codes; a redacted form is the only one ever logged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The rules AppState acts on (code shape, busy gate, sign-in result)

**Files:**
- Create: `PixelSwitch/Services/SignInSession.swift` (state types only; Task 4 fills in the session)
- Create: `PixelSwitch/Services/SignInRules.swift`
- Modify: `Tests/UnitTests/SignInTests.swift` (one call line, one appended section)
- Modify: `Tests/run-unit-tests.sh` (two new file lines)

**Interfaces:**
- Consumes: `Account`, `AuthStatus` (`PixelSwitch/Models/Account.swift`, already in the harness).
- Produces: `enum SignInPurpose: Equatable, Sendable { case newAccount, reauthenticate(accountId: UUID, email: String) }`; `enum SignInState: Equatable, Sendable { case starting, waitingForUser, completing, succeeded(accountId: UUID), failed(message: String), cancelled; var isFinished: Bool }`; `enum SignInCode { static func normalized(_ pasted: String) -> String? }`; `enum SignInGate { enum Decision { case allowed, busy, claudeUnavailable }; static func decide(claudeAvailable:isSwitching:isLoggingIn:current:) -> Decision }`; `enum SignInResult` with `NewAccount` (`notLoggedIn`, `noIdentity`, `existing(accountId:)`, `new(email:)`), `Reauthentication` (`notLoggedIn`, `noIdentity`, `matches`, `wrongAccount(signedInAs:)`), `static func newAccount(status:accounts:)` and `static func reauthentication(status:expectedEmail:)`.

- [ ] **Step 1: Write the failing test**

In `Tests/UnitTests/SignInTests.swift`, inside `runSignInTests()`, add the line `    runSignInRulesTests()` after `    runSignInParserTests()`. Then append this section to the end of the file, after one blank line:

```swift
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
```

In `Tests/run-unit-tests.sh`, replace the line `  PixelSwitch/Services/SignInOutputParser.swift \` with:

```bash
  PixelSwitch/Services/SignInOutputParser.swift \
  PixelSwitch/Services/SignInSession.swift \
  PixelSwitch/Services/SignInRules.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors, including `cannot find 'SignInCode' in scope`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Services/SignInSession.swift` with the state types only (Task 4 replaces this file with the full session, keeping these two types unchanged at the top):

```swift
import Foundation

/// Why a sign-in was started.
enum SignInPurpose: Equatable, Sendable {
    case newAccount
    case reauthenticate(accountId: UUID, email: String)
}

/// Where a sign-in is. Moves forward only:
/// starting → waitingForUser → completing → succeeded, failed or cancelled.
enum SignInState: Equatable, Sendable {
    case starting, waitingForUser, completing
    case succeeded(accountId: UUID), failed(message: String), cancelled

    /// Succeeded, failed or cancelled.
    var isFinished: Bool {
        switch self {
        case .succeeded, .failed, .cancelled: return true
        case .starting, .waitingForUser, .completing: return false
        }
    }
}
```

Create `PixelSwitch/Services/SignInRules.swift`:

```swift
import Foundation

/// The code the "another device" page shows. Claude Code reads it from stdin as
/// one `code#state` line and splits it on `#`.
enum SignInCode {
    /// The line to hand to Claude Code, or nil unless `pasted` is one whole
    /// `code#state`: exactly one `#`, text on both sides, and no whitespace or
    /// control character inside (a newline would send two lines).
    static func normalized(_ pasted: String) -> String? {
        let trimmed = pasted.trimmingCharacters(in: .whitespacesAndNewlines)
        let forbidden = CharacterSet.whitespacesAndNewlines.union(.controlCharacters)
        guard !trimmed.isEmpty, !trimmed.unicodeScalars.contains(where: forbidden.contains) else { return nil }
        let halves = trimmed.split(separator: "#", omittingEmptySubsequences: false)
        guard halves.count == 2, !halves[0].isEmpty, !halves[1].isEmpty else { return nil }
        return trimmed
    }
}

/// Whether a sign-in may start now. One credential mutation at a time: a
/// sign-in entering while a switch is suspended mid-swap would back up the
/// WRONG live credential under the old active account's id.
enum SignInGate {
    enum Decision: Equatable, Sendable { case allowed, busy, claudeUnavailable }

    static func decide(claudeAvailable: Bool, isSwitching: Bool, isLoggingIn: Bool, current: SignInState?) -> Decision {
        guard claudeAvailable else { return .claudeUnavailable }
        if isSwitching || isLoggingIn { return .busy }
        if let current, !current.isFinished { return .busy }
        return .allowed
    }
}

/// What `claude auth status` says after Claude Code finished a sign-in,
/// classified the way `AppState` has always acted on it. Emails compare
/// exactly, as they always have.
enum SignInResult {
    enum NewAccount: Equatable, Sendable {
        case notLoggedIn
        /// A credential source outranking the stored claude.ai login hides the identity.
        case noIdentity
        /// The browser signed in to an account PixelSwitch already has.
        case existing(accountId: UUID)
        case new(email: String)
    }

    enum Reauthentication: Equatable, Sendable {
        case notLoggedIn
        case noIdentity
        case matches
        /// The browser signed in to a different account than the one being re-authenticated.
        case wrongAccount(signedInAs: String)
    }

    static func newAccount(status: AuthStatus, accounts: [Account]) -> NewAccount {
        guard status.loggedIn else { return .notLoggedIn }
        guard let email = status.email else { return .noIdentity }
        if let existing = accounts.first(where: { $0.email == email }) { return .existing(accountId: existing.id) }
        return .new(email: email)
    }

    static func reauthentication(status: AuthStatus, expectedEmail: String) -> Reauthentication {
        guard status.loggedIn else { return .notLoggedIn }
        guard let email = status.email else { return .noIdentity }
        return email == expectedEmail ? .matches : .wrongAccount(signedInAs: email)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `251 passed, 0 failed` (+27).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/SignInSession.swift PixelSwitch/Services/SignInRules.swift Tests/UnitTests/SignInTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Sign-in links: the state types and the rules AppState acts on

SignInCode checks the code#state shape Claude Code reads from stdin; SignInGate
refuses a sign-in while a switch or another sign-in runs; SignInResult classifies
claude auth status after the CLI exits, including the wrong-account case.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: One environment for every `claude` subprocess, and the link-capture helper

**Files:**
- Create: `PixelSwitch/Services/ClaudeProcessEnvironment.swift`
- Create: `PixelSwitch/Services/SignInLinkCapture.swift`
- Modify: `PixelSwitch/Services/ClaudeService.swift` (`runClaude`'s environment block, ~lines 779–800, and its doc comment ~761)
- Modify: `Tests/UnitTests/SignInTests.swift` (two call lines, two appended sections)
- Modify: `Tests/run-unit-tests.sh` (two new file lines)

**Interfaces:**
- Consumes: nothing new.
- Produces: `enum ClaudeProcessEnvironment { static func make(claudePath: String, base: [String: String], homeDirectory: String) -> [String: String] }`; `struct SignInLinkCapture: Sendable` with `static let folderPrefix`, `captureFileVariable`, `staleAfter`; `let directory, helperURL, captureFileURL: URL`; `static func helperScript() -> String`; `static func create(in:id:now:) throws -> SignInLinkCapture`; `static func removeStale(in:now:)`; `var environmentOverrides: [String: String]`; `func readCaptured() -> String`; `func remove()`.

- [ ] **Step 1: Write the failing test**

In `runSignInTests()`, add `    runSignInEnvironmentTests()` and `    runSignInCaptureTests()` after `    runSignInRulesTests()`. Append these two sections to the end of `Tests/UnitTests/SignInTests.swift`, each after one blank line:

```swift
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
```

In `Tests/run-unit-tests.sh`, replace the line `  PixelSwitch/Services/SignInRules.swift \` with:

```bash
  PixelSwitch/Services/SignInRules.swift \
  PixelSwitch/Services/ClaudeProcessEnvironment.swift \
  PixelSwitch/Services/SignInLinkCapture.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors, including `cannot find 'ClaudeProcessEnvironment' in scope`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Services/ClaudeProcessEnvironment.swift`:

```swift
import Foundation

/// The environment every `claude` subprocess runs with. A GUI app inherits
/// launchd's bare PATH, so the usual install locations are put in front, plus
/// the resolved binary's own folder, so an NVM-installed `claude` finds `node`.
enum ClaudeProcessEnvironment {
    static func make(claudePath: String, base: [String: String], homeDirectory: String) -> [String: String] {
        var environment = base
        var extraPaths = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "\(homeDirectory)/.local/bin",
            "\(homeDirectory)/.npm-global/bin"
        ]
        // Only for an absolute path; the bare "claude" fallback has no folder.
        // Symlinks are resolved so /usr/local/bin/claude -> ~/.nvm/.../bin/claude
        // yields the NVM bin folder where `node` actually lives.
        if claudePath.contains("/") {
            let resolved = URL(fileURLWithPath: claudePath).resolvingSymlinksInPath().path
            extraPaths.insert(URL(fileURLWithPath: resolved).deletingLastPathComponent().path, at: 0)
        }
        let existingPath = environment["PATH"] ?? "/usr/bin:/bin"
        environment["PATH"] = (extraPaths + [existingPath]).joined(separator: ":")
        environment["HOME"] = homeDirectory
        return environment
    }
}
```

Create `PixelSwitch/Services/SignInLinkCapture.swift`:

```swift
import Foundation

/// A private, per-sign-in folder holding the `$BROWSER` helper and the file it
/// writes the automatic link to. Claude Code runs `$BROWSER <link>` instead of
/// `open <link>` when `BROWSER` is set, so pointing it at the helper records the
/// link and opens nothing. The folder is created fresh for each sign-in (0700,
/// helper 0700, capture file 0600) and removed when the sign-in ends.
///
/// Deliberately NOT a script inside the app bundle (as the design first said):
/// a temporary folder needs no bundle resource and no code-signing change, and
/// leaves nothing behind.
struct SignInLinkCapture: Sendable {
    /// The folder name prefix. One folder per sign-in, named by the session id.
    static let folderPrefix = "pixelswitch-signin-"
    /// The environment variable naming the capture file for the helper.
    static let captureFileVariable = "PIXELSWITCH_SIGNIN_CAPTURE_FILE"
    /// A leftover folder older than this (a crash mid-sign-in) is removed when
    /// the next sign-in starts. Longer than any sign-in can run.
    static let staleAfter: TimeInterval = 60 * 60

    let directory: URL
    let helperURL: URL
    let captureFileURL: URL

    /// The helper script. It records its one argument and exits 0 so Claude
    /// Code treats the browser as opened.
    static func helperScript() -> String {
        """
        #!/bin/sh
        # PixelSwitch sign-in link capture. Claude Code runs this as $BROWSER with
        # the sign-in link as its only argument; it records the link instead of
        # opening a browser.
        file="${\(captureFileVariable):-$(dirname "$0")/captured-links}"
        printf '%s\\n' "$1" >> "$file"
        exit 0

        """
    }

    /// Creates the folder, the helper and an empty capture file under `parent`.
    static func create(in parent: URL, id: UUID, now: Date = Date()) throws -> SignInLinkCapture {
        let fileManager = FileManager.default
        removeStale(in: parent, now: now)
        let directory = parent.appendingPathComponent(folderPrefix + id.uuidString, isDirectory: true)
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: false)
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)

        let helperURL = directory.appendingPathComponent("capture-sign-in-link")
        try Data(helperScript().utf8).write(to: helperURL)
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helperURL.path)

        let captureFileURL = directory.appendingPathComponent("captured-links")
        try Data().write(to: captureFileURL)
        try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: captureFileURL.path)

        return SignInLinkCapture(directory: directory, helperURL: helperURL, captureFileURL: captureFileURL)
    }

    /// Removes sign-in folders under `parent` last modified before `staleAfter` ago.
    static func removeStale(in parent: URL, now: Date = Date()) {
        let fileManager = FileManager.default
        guard let names = try? fileManager.contentsOfDirectory(atPath: parent.path) else { return }
        for name in names where name.hasPrefix(folderPrefix) {
            let path = parent.appendingPathComponent(name).path
            guard let modified = (try? fileManager.attributesOfItem(atPath: path))?[.modificationDate] as? Date,
                  now.timeIntervalSince(modified) > staleAfter else { continue }
            try? fileManager.removeItem(atPath: path)
        }
    }

    /// What Claude Code must run with so the link lands in the capture file.
    var environmentOverrides: [String: String] {
        ["BROWSER": helperURL.path, Self.captureFileVariable: captureFileURL.path]
    }

    /// Everything the helper has written so far ("" if nothing or unreadable).
    func readCaptured() -> String {
        (try? String(contentsOf: captureFileURL, encoding: .utf8)) ?? ""
    }

    /// Deletes the folder and everything in it.
    func remove() {
        try? FileManager.default.removeItem(at: directory)
    }
}
```

Make `runClaude` use the shared environment. In `PixelSwitch/Services/ClaudeService.swift`, replace:

```swift
                var env = ProcessInfo.processInfo.environment
                let homeDir = NSHomeDirectory()
                // Include the parent directory of the discovered claude binary
                // so that `node` is on PATH for NVM-installed scripts.
                // Only add it when claudePath is absolute (skip the bare "claude" fallback).
                var extraPaths = [
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "\(homeDir)/.local/bin",
                    "\(homeDir)/.npm-global/bin"
                ]
                if claudePath.contains("/") {
                    // Resolve symlinks so that e.g. /usr/local/bin/claude -> ~/.nvm/.../bin/claude
                    // yields the NVM bin dir where `node` actually lives
                    let resolved = URL(fileURLWithPath: claudePath).resolvingSymlinksInPath().path
                    let resolvedBinDir = URL(fileURLWithPath: resolved).deletingLastPathComponent().path
                    extraPaths.insert(resolvedBinDir, at: 0)
                }
                let existingPath = env["PATH"] ?? "/usr/bin:/bin"
                env["PATH"] = (extraPaths + [existingPath]).joined(separator: ":")
                env["HOME"] = homeDir
                process.environment = env
```

with:

```swift
                process.environment = ClaudeProcessEnvironment.make(
                    claudePath: claudePath,
                    base: ProcessInfo.processInfo.environment,
                    homeDirectory: NSHomeDirectory()
                )
```

and replace its doc comment:

```swift
    /// `timeout` ends the process and throws `.timedOut` if it runs longer; nil
    /// waits forever (interactive logins take as long as the user does).
```

with:

```swift
    /// `timeout` ends the process and throws `.timedOut` if it runs longer; nil
    /// waits forever. Sign-in does not come through here: it needs its output
    /// while the process is still running (see `SignInSession`).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `270 passed, 0 failed` (+19).

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/ClaudeProcessEnvironment.swift PixelSwitch/Services/SignInLinkCapture.swift PixelSwitch/Services/ClaudeService.swift Tests/UnitTests/SignInTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Sign-in links: shared claude environment and the BROWSER link-capture helper

runClaude and the sign-in now build the same PATH/HOME in one place. The helper is
written per sign-in into a private temporary folder (0700/0700/0600), records the
link Claude Code hands to $BROWSER, opens nothing, and is deleted afterwards.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: The sign-in session: process runner, clock and state machine

**Files:**
- Create: `PixelSwitch/Services/SignInProcess.swift`
- Modify (replace whole file): `PixelSwitch/Services/SignInSession.swift`
- Modify: `Tests/UnitTests/SignInTests.swift` (three call lines, three appended sections)
- Modify: `Tests/run-unit-tests.sh` (two new file lines)

**Interfaces:**
- Consumes: `SignInOutputParser`, `SignInCode`, `SignInLinkCapture`, `L10n.bundle` (`PixelSwitch/Services/L10n.swift`, added to the harness here).
- Produces: `protocol SignInProcessHandle` (`writeLine(_:) -> Bool`, `terminate()`, `kill()`, `closeInput()`); `protocol SignInProcessRunner` (`start(executable:arguments:environment:onOutput:onExit:) throws -> any SignInProcessHandle`); `struct ProcessSignInRunner`; `@MainActor final class SignInTimer`; `@MainActor protocol SignInScheduler` (`now`, `after(_:_:) -> SignInTimer`); `final class MainQueueSignInScheduler`; `struct SignInLaunch { let executable: URL; let environment: [String: String] }`; `@MainActor final class SignInSession: ObservableObject, Identifiable` with `static let linkWait = 10`, `overallLimit = 900`, `killAfter = 2`, `giveUpAfter = 5`, `capturePollInterval = 0.25`, `outputLimit = 65536`; `init(purpose:launch:runner:scheduler:captureParent:id:log:complete:onFinish:)`; `static func arguments(for:) -> [String]`; `func start()`, `func submitCode(_:) -> Bool`, `func cancel()`; published `state`, `automaticLink`, `manualLink`, `notice`, `codeSubmitted`.

- [ ] **Step 1: Write the failing test**

In `runSignInTests()`, add `    runSignInProcessTests()`, `    runSignInSessionTests()` and `    runSignInEndToEndTests()` after `    runSignInCaptureTests()`. The function now reads:

```swift
@MainActor func runSignInTests() {
    runSignInParserTests()
    runSignInRulesTests()
    runSignInEnvironmentTests()
    runSignInCaptureTests()
    runSignInProcessTests()
    runSignInSessionTests()
    runSignInEndToEndTests()
}
```

Append these three sections to the end of `Tests/UnitTests/SignInTests.swift`, each after one blank line:

```swift
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
```

In `Tests/run-unit-tests.sh`, replace the line `  PixelSwitch/Services/SignInSession.swift \` with:

```bash
  PixelSwitch/Services/L10n.swift \
  PixelSwitch/Services/SignInProcess.swift \
  PixelSwitch/Services/SignInSession.swift \
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors, including `cannot find type 'SignInProcessHandle' in scope`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Services/SignInProcess.swift`:

```swift
import Foundation

// MARK: - Running the CLI

/// A running `claude auth login`. Safe to call from any thread.
protocol SignInProcessHandle: AnyObject, Sendable {
    /// Writes `line` plus a newline to the process's stdin. False if the
    /// process has exited or its stdin is closed; never raises SIGPIPE.
    func writeLine(_ line: String) -> Bool
    /// SIGTERM.
    func terminate()
    /// SIGKILL.
    func kill()
    /// Closes stdin. Idempotent.
    func closeInput()
}

/// Starts processes for `SignInSession`. The fake in the unit tests stands in
/// for this so the session's state machine runs without launching `claude`.
protocol SignInProcessRunner: Sendable {
    /// Starts `executable` with stdout and stderr on one pipe and stdin on
    /// another. `onOutput` receives each chunk as it arrives, and `onExit` the
    /// exit status once, after the last chunk; both run on the main actor.
    func start(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        onOutput: @escaping @MainActor @Sendable (Data) -> Void,
        onExit: @escaping @MainActor @Sendable (Int32) -> Void
    ) throws -> any SignInProcessHandle
}

/// The real runner: `Process` and `Pipe`, read incrementally. The prompt
/// Claude Code prints last has no newline and the process does not exit until
/// the sign-in ends, so reading to end-of-file (as `runClaude` does) would
/// never deliver the links.
struct ProcessSignInRunner: SignInProcessRunner {
    /// How long, after the process exits, to wait for the end of its output.
    /// A grandchild holding the pipe open must not hold up the exit report.
    static let outputDrainTimeout: TimeInterval = 2

    func start(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        onOutput: @escaping @MainActor @Sendable (Data) -> Void,
        onExit: @escaping @MainActor @Sendable (Int32) -> Void
    ) throws -> any SignInProcessHandle {
        let process = Process()
        let output = Pipe()
        let input = Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardOutput = output
        process.standardError = output
        process.standardInput = input

        let outputEnded = DispatchSemaphore(value: 0)
        output.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                outputEnded.signal()
            } else {
                DispatchQueue.main.async { MainActor.assumeIsolated { onOutput(data) } }
            }
        }
        process.terminationHandler = { finished in
            let status = finished.terminationStatus
            // Every chunk was queued to the main queue before end-of-file was
            // signalled, so the exit report queued after it arrives last.
            _ = outputEnded.wait(timeout: .now() + Self.outputDrainTimeout)
            DispatchQueue.main.async { MainActor.assumeIsolated { onExit(status) } }
        }
        do {
            try process.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            throw error
        }
        return ProcessSignInHandle(process: process, input: input.fileHandleForWriting)
    }
}

final class ProcessSignInHandle: SignInProcessHandle, @unchecked Sendable {
    private let process: Process
    private let input: FileHandle
    private let lock = NSLock()
    private var inputClosed = false

    init(process: Process, input: FileHandle) {
        self.process = process
        self.input = input
        // A write after the CLI exits must fail with EPIPE, not kill the app.
        _ = fcntl(input.fileDescriptor, F_SETNOSIGPIPE, 1)
    }

    func writeLine(_ line: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !inputClosed, process.isRunning else { return false }
        do {
            try input.write(contentsOf: Data((line + "\n").utf8))
            return true
        } catch {
            return false
        }
    }

    func terminate() {
        if process.isRunning { process.terminate() }
    }

    func kill() {
        if process.isRunning { _ = Darwin.kill(process.processIdentifier, SIGKILL) }
    }

    func closeInput() {
        lock.lock()
        defer { lock.unlock() }
        guard !inputClosed else { return }
        inputClosed = true
        try? input.close()
    }
}

// MARK: - Timers

/// A scheduled action that can be called off.
@MainActor
final class SignInTimer {
    private(set) var isCancelled = false
    func cancel() { isCancelled = true }
}

/// The session's clock. The unit tests use a fake that fires on demand, so the
/// 10-second and 15-minute limits are tested without waiting.
@MainActor
protocol SignInScheduler: AnyObject {
    var now: Date { get }
    @discardableResult
    func after(_ seconds: TimeInterval, _ action: @escaping @MainActor @Sendable () -> Void) -> SignInTimer
}

/// The real clock: the main queue.
@MainActor
final class MainQueueSignInScheduler: SignInScheduler {
    var now: Date { Date() }

    @discardableResult
    func after(_ seconds: TimeInterval, _ action: @escaping @MainActor @Sendable () -> Void) -> SignInTimer {
        let timer = SignInTimer()
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
            MainActor.assumeIsolated {
                guard !timer.isCancelled else { return }
                action()
            }
        }
        return timer
    }
}
```

Replace the whole content of `PixelSwitch/Services/SignInSession.swift` with:

```swift
import Foundation
import Combine

/// Why a sign-in was started.
enum SignInPurpose: Equatable, Sendable {
    case newAccount
    case reauthenticate(accountId: UUID, email: String)
}

/// Where a sign-in is. Moves forward only:
/// starting → waitingForUser → completing → succeeded, failed or cancelled.
enum SignInState: Equatable, Sendable {
    case starting, waitingForUser, completing
    case succeeded(accountId: UUID), failed(message: String), cancelled

    /// Succeeded, failed or cancelled.
    var isFinished: Bool {
        switch self {
        case .succeeded, .failed, .cancelled: return true
        case .starting, .waitingForUser, .completing: return false
        }
    }
}

/// What `SignInSession` runs: the claude binary and the environment every
/// `claude` subprocess gets (see `ClaudeProcessEnvironment`).
struct SignInLaunch: Sendable {
    let executable: URL
    let environment: [String: String]
}

/// One run of `claude auth login` that shows its links instead of opening a
/// browser. The GUI and (later) the control API both observe it.
///
/// Nothing opens by itself: `BROWSER` points at a helper that records the
/// automatic link (see `SignInLinkCapture`), and the manual link is read from
/// the CLI's output. The state reaches `waitingForUser` as soon as either link
/// is known. When the CLI exits 0 the session is `completing` while `complete`
/// (AppState's capture steps) runs, and its result is the final state.
@MainActor
final class SignInSession: ObservableObject, Identifiable {
    /// If neither link is known this long after the CLI starts, the session
    /// falls back to letting Claude Code open the default browser.
    static let linkWait: TimeInterval = 10
    /// A sign-in nobody finishes is stopped after this long.
    static let overallLimit: TimeInterval = 15 * 60
    /// After SIGTERM, SIGKILL follows this much later if the CLI still runs.
    static let killAfter: TimeInterval = 2
    /// After asking the CLI to stop, the session ends this much later even if
    /// no exit was reported, so a sign-in can never stay busy forever.
    static let giveUpAfter: TimeInterval = 5
    /// How often the capture file is read.
    static let capturePollInterval: TimeInterval = 0.25
    /// Output kept for parsing. The links arrive in the first few hundred bytes.
    static let outputLimit = 64 * 1024

    let id: UUID
    let purpose: SignInPurpose
    let startedAt: Date
    @Published private(set) var state: SignInState = .starting
    /// Localhost redirect: finishes by itself in any browser on this Mac.
    @Published private(set) var automaticLink: URL?
    /// Code-callback redirect: the page shows a code to paste back.
    @Published private(set) var manualLink: URL?
    /// Set when PixelSwitch fell back to letting Claude Code open the default browser.
    @Published private(set) var notice: String?
    /// True once a code was handed to Claude Code, which is now checking it.
    @Published private(set) var codeSubmitted = false

    private enum StopReason { case fallback, cancel, timeout }

    private let launch: SignInLaunch
    private let runner: any SignInProcessRunner
    private let scheduler: any SignInScheduler
    private let captureParent: URL
    private let log: @MainActor (String) -> Void
    private let complete: @MainActor (SignInSession) async -> SignInState
    private var onFinish: (@MainActor (SignInSession) -> Void)?

    private var didStart = false
    private var capture: SignInLinkCapture?
    private var handle: (any SignInProcessHandle)?
    private var generation = 0
    private var output = Data()
    private var stopReason: StopReason?
    private var inFallback = false
    private var linkTimer: SignInTimer?
    private var overallTimer: SignInTimer?
    private var pollTimer: SignInTimer?
    private var killTimer: SignInTimer?
    private var giveUpTimer: SignInTimer?

    /// - Parameters:
    ///   - complete: runs after the CLI exits 0; returns the final state.
    ///   - onFinish: called exactly once, when the state becomes finished.
    init(
        purpose: SignInPurpose,
        launch: SignInLaunch,
        runner: any SignInProcessRunner,
        scheduler: any SignInScheduler,
        captureParent: URL = FileManager.default.temporaryDirectory,
        id: UUID = UUID(),
        log: @escaping @MainActor (String) -> Void = { _ in },
        complete: @escaping @MainActor (SignInSession) async -> SignInState,
        onFinish: @escaping @MainActor (SignInSession) -> Void
    ) {
        self.id = id
        self.purpose = purpose
        self.launch = launch
        self.runner = runner
        self.scheduler = scheduler
        self.captureParent = captureParent
        self.log = log
        self.complete = complete
        self.onFinish = onFinish
        self.startedAt = scheduler.now
    }

    /// `claude` arguments for a purpose. Re-authentication pre-fills the email.
    static func arguments(for purpose: SignInPurpose) -> [String] {
        switch purpose {
        case .newAccount: return ["auth", "login"]
        case .reauthenticate(_, let email): return ["auth", "login", "--email", email]
        }
    }

    // MARK: - Starting

    /// Starts the CLI. AppState calls this after backing up the current
    /// account. Does nothing if already started or already finished (a sign-in
    /// cancelled during the backup never launches anything).
    func start() {
        guard !didStart, !state.isFinished else { return }
        didStart = true
        let capture: SignInLinkCapture
        do {
            capture = try SignInLinkCapture.create(in: captureParent, id: id, now: scheduler.now)
        } catch {
            log("[signIn] Could not prepare the link capture: \(error.localizedDescription)")
            finish(.failed(message: String(localized: "Couldn't prepare the sign-in: \(error.localizedDescription)", bundle: L10n.bundle)))
            return
        }
        self.capture = capture
        let environment = launch.environment.merging(capture.environmentOverrides) { _, override in override }
        guard launchProcess(environment: environment) else { return }
        overallTimer = scheduler.after(Self.overallLimit) { [weak self] in self?.overallLimitReached() }
        linkTimer = scheduler.after(Self.linkWait) { [weak self] in self?.linkWaitElapsed() }
        schedulePoll()
    }

    @discardableResult
    private func launchProcess(environment: [String: String]) -> Bool {
        generation += 1
        let current = generation
        output = Data()
        do {
            handle = try runner.start(
                executable: launch.executable,
                arguments: Self.arguments(for: purpose),
                environment: environment,
                onOutput: { [weak self] data in self?.received(data, generation: current) },
                onExit: { [weak self] status in self?.exited(status: status, generation: current) }
            )
        } catch {
            log("[signIn] Could not start Claude Code: \(error.localizedDescription)")
            finish(.failed(message: String(localized: "Couldn't start Claude Code: \(error.localizedDescription)", bundle: L10n.bundle)))
            return false
        }
        log("[signIn] Started `claude auth login`\(inFallback ? " (fallback: Claude Code opens the default browser)" : "")")
        return true
    }

    // MARK: - Links

    private func received(_ data: Data, generation: Int) {
        guard generation == self.generation, stopReason == nil, !state.isFinished else { return }
        if output.count < Self.outputLimit {
            output.append(data.prefix(Self.outputLimit - output.count))
        }
        guard manualLink == nil,
              let link = SignInOutputParser.manualLink(in: String(decoding: output, as: UTF8.self)) else { return }
        manualLink = link
        log("[signIn] Manual link read from the output: \(SignInOutputParser.redacted(link))")
        linkArrived()
    }

    private func schedulePoll() {
        pollTimer = scheduler.after(Self.capturePollInterval) { [weak self] in self?.pollCapture() }
    }

    private func pollCapture() {
        pollTimer = nil
        guard !state.isFinished, stopReason == nil, capture != nil, !captureAutomaticLink() else { return }
        schedulePoll()
    }

    /// Reads the capture file once. True if the automatic link is now known.
    @discardableResult
    private func captureAutomaticLink() -> Bool {
        guard automaticLink == nil, let capture,
              let link = SignInOutputParser.automaticLink(inCaptureFile: capture.readCaptured()) else {
            return automaticLink != nil
        }
        automaticLink = link
        log("[signIn] Automatic link captured: \(SignInOutputParser.redacted(link))")
        linkArrived()
        return true
    }

    private func linkArrived() {
        if state == .starting { state = .waitingForUser }
        linkTimer?.cancel()
        linkTimer = nil
    }

    // MARK: - The code from another device

    /// Hands a pasted `code#state` to Claude Code. False unless the session is
    /// waiting for the user, no code was handed over yet, and the code has the
    /// `code#state` shape (see `SignInCode`).
    @discardableResult
    func submitCode(_ code: String) -> Bool {
        guard state == .waitingForUser, stopReason == nil, !codeSubmitted,
              let line = SignInCode.normalized(code), let handle else { return false }
        guard handle.writeLine(line) else {
            log("[signIn] Could not hand the code to Claude Code")
            return false
        }
        codeSubmitted = true
        log("[signIn] Code handed to Claude Code")
        return true
    }

    // MARK: - Stopping

    /// Stops the sign-in: SIGTERM, then SIGKILL 2 seconds later if needed.
    /// Ignored once Claude Code has exited 0 (`completing`) or the session has
    /// finished, so closing the window never interrupts saving the account.
    func cancel() {
        guard state == .starting || state == .waitingForUser else { return }
        guard didStart else {
            // Cancelled during the backup: nothing was launched.
            log("[signIn] Cancelled before Claude Code started")
            finish(.cancelled)
            return
        }
        switch stopReason {
        case .cancel?, .timeout?:
            return
        case .fallback?:
            // The first CLI is already being stopped; end instead of falling back.
            stopReason = .cancel
        case nil:
            log("[signIn] Cancelled")
            stop(.cancel)
        }
    }

    private func linkWaitElapsed() {
        linkTimer = nil
        guard state == .starting, stopReason == nil, manualLink == nil, !captureAutomaticLink() else { return }
        log("[signIn] Neither link appeared within \(Int(Self.linkWait)) s; falling back to the default browser")
        stop(.fallback)
    }

    private func overallLimitReached() {
        overallTimer = nil
        guard state == .starting || state == .waitingForUser else { return }
        log("[signIn] Timed out after \(Int(Self.overallLimit / 60)) minutes")
        if stopReason == nil {
            stop(.timeout)
        } else if stopReason == .fallback {
            stopReason = .timeout
        }
    }

    private func stop(_ reason: StopReason) {
        guard let handle else { return }
        stopReason = reason
        handle.terminate()
        let current = generation
        killTimer = scheduler.after(Self.killAfter) { [weak self] in self?.forceKill(generation: current) }
        giveUpTimer = scheduler.after(Self.giveUpAfter) { [weak self] in
            self?.log("[signIn] No exit reported after stopping Claude Code; ending the sign-in anyway")
            self?.exited(status: -1, generation: current)
        }
    }

    private func forceKill(generation: Int) {
        killTimer = nil
        guard generation == self.generation, stopReason != nil else { return }
        log("[signIn] Claude Code did not stop after SIGTERM; sending SIGKILL")
        handle?.kill()
    }

    /// Today's behaviour: run `claude auth login` without the helper, so Claude
    /// Code opens the default browser itself. The session still finishes when
    /// the CLI exits.
    private func startFallback() {
        inFallback = true
        capture?.remove()
        capture = nil
        notice = String(localized: "Couldn't read the sign-in link; opened your default browser instead.", bundle: L10n.bundle)
        guard launchProcess(environment: launch.environment) else { return }
        state = .waitingForUser
    }

    // MARK: - Ending

    private func exited(status: Int32, generation: Int) {
        guard generation == self.generation, !state.isFinished else { return }
        killTimer?.cancel()
        killTimer = nil
        giveUpTimer?.cancel()
        giveUpTimer = nil
        handle?.closeInput()
        handle = nil
        let reason = stopReason
        stopReason = nil
        switch reason {
        case .fallback?:
            startFallback()
        case .cancel?:
            finish(.cancelled)
        case .timeout?:
            finish(.failed(message: String(localized: "Sign-in timed out after 15 minutes. Start it again when you're ready.", bundle: L10n.bundle)))
        case nil where status == 0:
            processSucceeded()
        case nil:
            log("[signIn] Claude Code exited with status \(status)")
            finish(.failed(message: String(localized: "Claude Code's sign-in stopped (exit status \(Int(status))).", bundle: L10n.bundle)))
        }
    }

    private func processSucceeded() {
        log("[signIn] Claude Code finished the sign-in; saving it")
        cancelTimers()
        capture?.remove()
        capture = nil
        state = .completing
        Task { @MainActor in
            let outcome = await self.complete(self)
            self.finish(outcome)
        }
    }

    private func finish(_ final: SignInState) {
        guard !state.isFinished else { return }
        cancelTimers()
        handle?.closeInput()
        handle = nil
        capture?.remove()
        capture = nil
        state = final
        log("[signIn] Finished: \(Self.summary(of: final))")
        let callback = onFinish
        onFinish = nil
        callback?(self)
    }

    private func cancelTimers() {
        for timer in [linkTimer, overallTimer, pollTimer, killTimer, giveUpTimer] { timer?.cancel() }
        linkTimer = nil
        overallTimer = nil
        pollTimer = nil
        killTimer = nil
        giveUpTimer = nil
    }

    /// For the log: the state's name, never a message's details.
    private static func summary(of state: SignInState) -> String {
        switch state {
        case .starting: return "starting"
        case .waitingForUser: return "waiting for the user"
        case .completing: return "completing"
        case .succeeded: return "succeeded"
        case .failed: return "failed"
        case .cancelled: return "cancelled"
        }
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `351 passed, 0 failed` (+81). The end-to-end section starts real `/bin/sh` scripts and takes a few seconds.

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/SignInProcess.swift PixelSwitch/Services/SignInSession.swift Tests/UnitTests/SignInTests.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Sign-in links: the SignInSession state machine and its process runner

Runs claude auth login with incremental output (the prompt has no newline and the
process does not exit until the sign-in ends), captures both links, accepts one
code#state, and ends on every path: success, failure, cancel (SIGTERM then SIGKILL),
a 15-minute timeout, and a 10-second fallback to today's default-browser behaviour.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: AppState starts sign-ins through the session

**Files:**
- Modify: `PixelSwitch/AppState.swift` (a log line near line 5; a property after `isLoggingIn` ~line 25; replace `loginNewAccount()` ~288–407; delete `reauthenticateAccount(_:)` ~873–960)
- Modify: `PixelSwitch/Services/ClaudeService.swift` (replace `login()` ~628–637 with `signInLaunch()`)
- Modify: `PixelSwitch/Views/AccountSwitcherView.swift` (the two sign-in buttons call the non-async wrappers; the "logging in" block)
- Modify: `PixelSwitch/Views/HiddenWindowView.swift` (a notification name)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces (spec section 9): `@Published private(set) var currentSignIn: SignInSession?`, `enum SignInStartError: Error, Equatable { case busy, claudeUnavailable }` (nested in `AppState`), `func startSignIn(_ purpose: SignInPurpose) throws -> SignInSession`, plus `func loginNewAccount()`, `func reauthenticateAccount(_ account: Account)` (no longer `async`), `func dismissSignIn()`, `ClaudeService.signInLaunch() -> SignInLaunch`, `Notification.Name.pixelswitchShowSignIn`.

`AppState` is not in the harness, so this task's check is the type-check plus the unchanged unit suite; the rules it relies on were tested in Tasks 2 and 4. Every step, message and guard of the old `loginNewAccount` and `reauthenticateAccount` is kept: guards, `isLoggingIn`, backup BEFORE the CLI starts, then after exit 0 the auth-status read, duplicate handling, the re-authentication email check, the auto-switch grace period, `saveAccounts()` and `refresh()`. `isLoggingIn` is cleared on every exit path (`signInFinished` runs once per session, whatever happens).

- [ ] **Step 1: Replace `ClaudeService.login()`**

In `PixelSwitch/Services/ClaudeService.swift`, replace:

```swift
    /// Run `claude auth login` which opens browser for OAuth.
    func login() async throws {
        log.info("[login] Starting `claude auth login`... (will open browser)")
        _ = try await runClaude(args: ["auth", "login"])
        log.info("[login] `claude auth login` process exited")

        // Give keychain a moment to sync after CLI writes
        try await Task.sleep(for: .seconds(1))
        log.info("[login] Post-login delay complete, ready for token capture")
    }
```

with:

```swift
    /// What a `SignInSession` runs: this claude binary, with the same PATH
    /// and HOME as every other CLI call. The session adds `BROWSER`.
    func signInLaunch() -> SignInLaunch {
        let path = claudePath
        return SignInLaunch(
            executable: URL(fileURLWithPath: path),
            environment: ClaudeProcessEnvironment.make(
                claudePath: path,
                base: ProcessInfo.processInfo.environment,
                homeDirectory: NSHomeDirectory()
            )
        )
    }
```

- [ ] **Step 2: AppState**

In `PixelSwitch/AppState.swift`, replace `private let log = FileLog("AppState")` with:

```swift
private let log = FileLog("AppState")
private let signInLog = FileLog("SignIn")
```

Replace `    @Published var isLoggingIn = false` with:

```swift
    @Published var isLoggingIn = false
    /// The sign-in in progress, or the last one until it is dismissed or
    /// replaced, so its outcome stays readable (Part C's status call reads it).
    @Published private(set) var currentSignIn: SignInSession?
```

Replace everything from the line `    func loginNewAccount() async {` down to (not including) the line `    func updateAccountLabel(_ account: Account, label: String?) {` with:

```swift
    // MARK: - Sign-in

    enum SignInStartError: Error, Equatable { case busy, claudeUnavailable }

    /// Starts a sign-in and returns its session at once. The current account
    /// is backed up first (before `claude auth login` can overwrite it), then
    /// the CLI runs; `isLoggingIn` stays true until the session finishes, on
    /// every path. When Claude Code exits 0, the same capture steps as always
    /// run (see `finishNewAccountSignIn` and `finishReauthentication`).
    /// Throws `SignInStartError`.
    func startSignIn(_ purpose: SignInPurpose) throws -> SignInSession {
        switch SignInGate.decide(claudeAvailable: claudeAvailable, isSwitching: isSwitching,
                                 isLoggingIn: isLoggingIn, current: currentSignIn?.state) {
        case .claudeUnavailable: throw SignInStartError.claudeUnavailable
        case .busy: throw SignInStartError.busy
        case .allowed: break
        }
        // One credential mutation at a time (same guard as switchTo). A login
        // entering while a switch is suspended mid-swap would back up the
        // WRONG live credential under the old active account's id — quietly
        // destroying that account's usable backup. Also blocks double-clicks.
        isLoggingIn = true
        errorMessage = nil

        let session = SignInSession(
            purpose: purpose,
            launch: claudeService.signInLaunch(),
            runner: ProcessSignInRunner(),
            scheduler: MainQueueSignInScheduler(),
            log: { signInLog.info($0) },
            complete: { [weak self] session in
                guard let self else { return .failed(message: String(localized: "Login did not complete", bundle: L10n.bundle)) }
                return await self.completeSignIn(session)
            },
            onFinish: { [weak self] session in self?.signInFinished(session) }
        )
        currentSignIn = session
        Task {
            await self.backUpBeforeSignIn(for: purpose)
            session.start()
        }
        return session
    }

    /// The popover's "Login New Account" and Settings' "Sign In New Account".
    func loginNewAccount() {
        log.info("[loginNewAccount] ===== Starting login new account flow =====")
        do {
            _ = try startSignIn(.newAccount)
        } catch {
            reportSignInStartError(error, context: "loginNewAccount")
        }
    }

    /// Re-authenticate an account: a sign-in pre-filled with its email, whose
    /// fresh credentials are captured for it.
    func reauthenticateAccount(_ account: Account) {
        log.info("[reauth] ===== Re-authenticating account \(account.id) (\(account.email)) =====")
        do {
            _ = try startSignIn(.reauthenticate(accountId: account.id, email: account.email))
        } catch {
            reportSignInStartError(error, context: "reauth")
        }
    }

    /// Forgets a finished sign-in (its window's Close button). A running one is kept.
    func dismissSignIn() {
        if currentSignIn?.state.isFinished == true { currentSignIn = nil }
    }

    private func reportSignInStartError(_ error: Error, context: String) {
        switch error as? SignInStartError {
        case .claudeUnavailable?:
            errorMessage = String(localized: "Claude CLI not found", bundle: L10n.bundle)
            log.error("[\(context)] Aborted: Claude CLI not found")
        case .busy?:
            log.warning("[\(context)] Skipped: a switch or another login is in progress")
            if let running = currentSignIn, !running.state.isFinished {
                errorMessage = String(localized: "A sign-in is already in progress.", bundle: L10n.bundle)
                NotificationCenter.default.post(name: .pixelswitchShowSignIn, object: nil)
            } else {
                errorMessage = String(localized: "A switch is in progress. Try again in a moment.", bundle: L10n.bundle)
            }
        case nil:
            errorMessage = error.localizedDescription
        }
    }

    /// Step 1: back up the current account (token + oauthAccount) before the
    /// login overwrites them.
    private func backUpBeforeSignIn(for purpose: SignInPurpose) async {
        switch purpose {
        case .newAccount:
            if let current = activeAccount {
                log.info("[loginNewAccount] Step 1: Backing up current account (\(current.email))...")
                let backed = await claudeService.captureCurrentCredentials(for: current)
                log.info("[loginNewAccount] Step 1: Backup result: \(backed)")
            } else {
                log.info("[loginNewAccount] Step 1: No active account, skipping backup")
            }
            log.info("[loginNewAccount] Step 2: Running `claude auth login`...")
        case .reauthenticate(let accountId, _):
            if let current = activeAccount, current.id != accountId {
                log.info("[reauth] Backing up current account before login...")
                _ = await claudeService.captureCurrentCredentials(for: current)
            }
            log.info("[reauth] Running `claude auth login`...")
        }
    }

    /// Runs after Claude Code exited 0. Returns the sign-in's final state.
    private func completeSignIn(_ session: SignInSession) async -> SignInState {
        // Give keychain a moment to sync after CLI writes.
        try? await Task.sleep(for: .seconds(1))
        switch session.purpose {
        case .newAccount:
            return await finishNewAccountSignIn()
        case .reauthenticate(let accountId, let email):
            return await finishReauthentication(accountId: accountId, expectedEmail: email)
        }
    }

    /// Steps 3 to 6 of adding an account, unchanged from the run-to-exit login.
    private func finishNewAccountSignIn() async -> SignInState {
        log.info("[loginNewAccount] Step 2: Login process completed")

        // 3. Read the new identity from ~/.claude.json
        log.info("[loginNewAccount] Step 3: Reading post-login state...")
        let status: AuthStatus
        do {
            status = try await claudeService.getAuthStatus()
        } catch {
            errorMessage = error.localizedDescription
            isLoggingIn = false
            log.error("[loginNewAccount] Error: \(error.localizedDescription)")
            return .failed(message: error.localizedDescription)
        }

        switch SignInResult.newAccount(status: status, accounts: accounts) {
        case .notLoggedIn:
            let message = String(localized: "Login did not complete", bundle: L10n.bundle)
            errorMessage = message
            log.error("[loginNewAccount] Step 3: Not logged in after login!")
            isLoggingIn = false
            return .failed(message: message)

        case .noIdentity:
            let message = shadowedIdentityMessage(status)
            errorMessage = message
            log.error("[loginNewAccount] Step 3: CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
            isLoggingIn = false
            return .failed(message: message)

        case .existing(let existingId):
            log.info("[loginNewAccount] Step 3: Logged in as \(status.email ?? "?")")
            // 4. Duplicate — refresh its backup and make it the active account.
            // The login DID change what the CLI is authenticated as; returning
            // without updating our model left the menu bar and switcher
            // presenting an account the CLI was no longer using. The capture
            // CAN also fail (e.g. the backup store refuses writes while
            // unreadable); claiming "credentials refreshed" then would leave a
            // stale backup behind an explicit success message.
            log.info("[loginNewAccount] Step 4: Account already exists, refreshing backup and marking it active")
            guard let target = accounts.first(where: { $0.id == existingId }) else {
                isLoggingIn = false
                return .failed(message: String(localized: "Login did not complete", bundle: L10n.bundle))
            }
            let captured = await claudeService.captureCurrentCredentials(for: target)
            // Looked up again: the capture awaited, and the list may have changed.
            guard let existing = accounts.firstIndex(where: { $0.id == existingId }) else {
                let message = String(localized: "That account was removed during the sign-in, so nothing was saved.", bundle: L10n.bundle)
                errorMessage = message
                isLoggingIn = false
                return .failed(message: message)
            }
            for i in accounts.indices {
                accounts[i].isActive = (i == existing)
            }
            accounts[existing].lastUsed = Date()
            activeAccount = accounts[existing]
            // A login is a deliberate account choice; grant it the same
            // auto-switch grace period a manual switch gets.
            lastAutoSwitchAt = Date()
            saveAccounts()
            isLoggingIn = false
            if captured {
                errorMessage = String(localized: "Account already exists - credentials refreshed", bundle: L10n.bundle)
                return .succeeded(accountId: existingId)
            }
            log.error("[loginNewAccount] Step 4: Backup capture FAILED for existing account")
            let message = String(localized: "Could not capture credentials", bundle: L10n.bundle)
            errorMessage = message
            return .failed(message: message)

        case .new(let email):
            log.info("[loginNewAccount] Step 3: Logged in as \(email)")
            // 5. Create new account and capture credentials (token + oauthAccount)
            let account = Account(
                email: email,
                displayName: status.orgName ?? email,
                provider: .claudeCode,
                orgName: status.orgName,
                subscriptionType: status.subscriptionType,
                isActive: true
            )
            log.info("[loginNewAccount] Step 5: Created account, id=\(account.id)")

            let captured = await claudeService.captureCurrentCredentials(for: account)
            if !captured {
                let message = String(localized: "Could not capture credentials", bundle: L10n.bundle)
                errorMessage = message
                log.error("[loginNewAccount] Step 5: Capture failed!")
                isLoggingIn = false
                return .failed(message: message)
            }

            // 6. Mark new account as active
            for i in accounts.indices {
                accounts[i].isActive = false
            }
            accounts.append(account)
            activeAccount = account
            // A login is a deliberate account choice; grant it the same
            // auto-switch grace period a manual switch gets.
            lastAutoSwitchAt = Date()
            saveAccounts()
            log.info("[loginNewAccount] Step 6: New account active. Total: \(self.accounts.count)")

            // refresh() skips while isLoggingIn is true; the session is still
            // `completing`, which keeps a second sign-in out meanwhile.
            isLoggingIn = false
            await refresh()
            log.info("[loginNewAccount] ===== Login completed =====")
            return .succeeded(accountId: account.id)
        }
    }

    /// Steps 3 to 5 of re-authenticating, unchanged from the run-to-exit login.
    private func finishReauthentication(accountId: UUID, expectedEmail: String) async -> SignInState {
        // 3. Verify the login result matches the target account
        let status: AuthStatus
        do {
            status = try await claudeService.getAuthStatus()
        } catch {
            errorMessage = error.localizedDescription
            isLoggingIn = false
            log.error("[reauth] Error: \(error.localizedDescription)")
            return .failed(message: error.localizedDescription)
        }

        switch SignInResult.reauthentication(status: status, expectedEmail: expectedEmail) {
        case .notLoggedIn:
            let message = String(localized: "Login did not complete", bundle: L10n.bundle)
            errorMessage = message
            isLoggingIn = false
            return .failed(message: message)

        case .noIdentity:
            let message = shadowedIdentityMessage(status)
            errorMessage = message
            log.error("[reauth] CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
            isLoggingIn = false
            return .failed(message: message)

        case .wrongAccount(let email):
            let message = String(localized: "Logged in as \(email), but expected \(expectedEmail). Credentials not updated.", bundle: L10n.bundle)
            errorMessage = message
            log.error("[reauth] Email mismatch: got \(email), expected \(expectedEmail)")
            isLoggingIn = false
            return .failed(message: message)

        case .matches:
            guard let account = accounts.first(where: { $0.id == accountId }) else {
                let message = String(localized: "That account was removed during the sign-in, so nothing was saved.", bundle: L10n.bundle)
                errorMessage = message
                log.error("[reauth] Account \(accountId) was removed during the sign-in; nothing captured")
                isLoggingIn = false
                return .failed(message: message)
            }

            // 4. Capture the fresh token
            let captured = await claudeService.captureCurrentCredentials(for: account)
            log.info("[reauth] Token capture result: \(captured)")

            // 5. Update account metadata. Done even when the capture failed —
            // the CLI really is on this account now — but a failed capture must
            // be surfaced, not folded into "completed": the stored backup is
            // still the OLD credential, so a later switch away and back would
            // fail while the UI claimed everything was refreshed.
            if let index = accounts.firstIndex(where: { $0.id == account.id }) {
                accounts[index].orgName = status.orgName
                accounts[index].subscriptionType = status.subscriptionType

                // Mark this account as active (it's what the CLI is now using)
                for i in accounts.indices {
                    accounts[i].isActive = (i == index)
                }
                activeAccount = accounts[index]
                // A re-authentication is a deliberate account choice; grant it
                // the same auto-switch grace period a manual switch gets.
                lastAutoSwitchAt = Date()
                saveAccounts()
            }

            isLoggingIn = false
            await refresh()
            if captured {
                log.info("[reauth] ===== Re-authentication completed =====")
                return .succeeded(accountId: account.id)
            }
            // Set AFTER refresh() — refresh clears errorMessage.
            let message = String(localized: "Could not capture credentials", bundle: L10n.bundle)
            errorMessage = message
            log.error("[reauth] ===== Re-authentication finished, but the backup capture FAILED =====")
            return .failed(message: message)
        }
    }

    /// Runs once per sign-in, on every path: success, failure, cancel, timeout.
    private func signInFinished(_ session: SignInSession) {
        isLoggingIn = false
        switch session.state {
        case .failed(let message):
            // The popover footer shows it too, as it always did.
            errorMessage = message
        case .cancelled:
            log.info("[signIn] Cancelled; nothing was changed")
        case .succeeded, .starting, .waitingForUser, .completing:
            break
        }
    }
```

Delete the whole old `reauthenticateAccount`: from the line `    /// Re-authenticate an account by running \`claude auth login\` and capturing fresh credentials.` down to and including its closing `    }`, the last line before the blank line above `    // MARK: - Usage`. (Its steps now live in `finishReauthentication(accountId:expectedEmail:)` above.)

- [ ] **Step 3: The notification name and the popover buttons**

In `PixelSwitch/Views/HiddenWindowView.swift`, replace:

```swift
    static let pixelswitchOpenSettings = Notification.Name("pixelswitchOpenSettings")
```

with:

```swift
    static let pixelswitchOpenSettings = Notification.Name("pixelswitchOpenSettings")
    /// Brings the running sign-in's window to the front (`SignInWindowController`).
    static let pixelswitchShowSignIn = Notification.Name("pixelswitchShowSignIn")
```

In `PixelSwitch/Views/AccountSwitcherView.swift`, replace:

```swift
            Button {
                Task { await appState.reauthenticateAccount(account) }
            } label: {
```

with:

```swift
            Button {
                appState.reauthenticateAccount(account)
            } label: {
```

In `PixelSwitch/Views/AccountSwitcherView.swift`, replace:

```swift
        if appState.isLoggingIn {
            // Logging in state
            VStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Waiting for browser login...")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                Text("Complete the login in your browser, then return here.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
            }
```

with:

```swift
        if appState.isLoggingIn {
            // A sign-in is running in its own window, which survives this
            // popover closing; this brings it back if it went behind.
            VStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Signing in…")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                Text("Finish in the sign-in window.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
                Button("Show sign-in window") {
                    NotificationCenter.default.post(name: .pixelswitchShowSignIn, object: nil)
                }
                .controlSize(.small)
            }
```

In `PixelSwitch/Views/AccountSwitcherView.swift`, replace:

```swift
                Button {
                    Task { await appState.loginNewAccount() }
                } label: {
```

with:

```swift
                Button {
                    appState.loginNewAccount()
                } label: {
```


- [ ] **Step 4: Verify**

Run: `grep -rn "claudeService.login()\|await appState.loginNewAccount\|await appState.reauthenticateAccount\|func login() async" PixelSwitch`
Expected: no output.

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK`. (The window controller does not exist yet, so nothing shows the session; Task 6 adds it. Do not ship between Task 5 and Task 6.)

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `351 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/AppState.swift PixelSwitch/Services/ClaudeService.swift PixelSwitch/Views/AccountSwitcherView.swift PixelSwitch/Views/HiddenWindowView.swift
git commit -m "$(cat <<'EOF'
Sign-in links: AppState starts sign-ins through SignInSession

startSignIn(_:) backs up the current account, then starts the session; after the
CLI exits 0 the same capture steps as before run. loginNewAccount and
reauthenticateAccount are now wrappers; currentSignIn is what the GUI (and later
the control API) observe. isLoggingIn is cleared on every exit path.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: The sign-in window, the browser list, and the strings

**Files:**
- Create: `PixelSwitch/Services/BrowserOpener.swift`
- Create: `PixelSwitch/Services/SignInWindowController.swift`
- Create: `PixelSwitch/Views/SignInView.swift`
- Modify: `PixelSwitch/PixelSwitchApp.swift` (install and relocalize the window controller)
- Modify: the five `PixelSwitch/<lang>.lproj/Localizable.strings` files (one appended block each)

**Interfaces:**
- Consumes: `AppState.currentSignIn`, `AppState.dismissSignIn()`, `SignInSession`, `Notification.Name.pixelswitchShowSignIn`, `EmailDisplay.key`, `String.maskedAsEmailAddress()`, `Color.brand`.
- Produces: `struct BrowserApp: Identifiable, Hashable, Sendable { let id: String; let name: String; let appURL: URL; let isDefault: Bool }`; `@MainActor enum BrowserOpener { static func installedBrowsers() -> [BrowserApp]; static func open(_ url: URL, in browser: BrowserApp?) -> Bool }`; `@MainActor final class SignInWindowController` (`install(appState:locale:)`, `updateLocale(_:)`); `struct SignInView: View` (`init(session:onClose:)`).

These are AppKit and SwiftUI, so they are checked by the type-check here and by hand in Task 8.

- [ ] **Step 1: The browser list**

Create `PixelSwitch/Services/BrowserOpener.swift`:

```swift
import AppKit

private let browserLog = FileLog("SignIn")

/// A browser installed on this Mac.
struct BrowserApp: Identifiable, Hashable, Sendable {
    /// The bundle identifier (the app's path if it has none).
    let id: String
    let name: String
    let appURL: URL
    let isDefault: Bool
}

/// Lists this Mac's browsers and opens a sign-in link in one of them. Only
/// ever called from a button the user pressed (or a CLI request), never on its own.
@MainActor
enum BrowserOpener {
    /// Every app that opens https links, the default browser first, then by name.
    static func installedBrowsers() -> [BrowserApp] {
        let probe = URL(string: "https://example.com")!
        let defaultURL = NSWorkspace.shared.urlForApplication(toOpen: probe)?.standardizedFileURL
        var seen = Set<String>()
        var browsers: [BrowserApp] = []
        for url in NSWorkspace.shared.urlsForApplications(toOpen: probe).map(\.standardizedFileURL) {
            let bundle = Bundle(url: url)
            let id = bundle?.bundleIdentifier ?? url.path
            guard seen.insert(id).inserted else { continue }
            let name = (bundle?.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String)
                ?? (bundle?.object(forInfoDictionaryKey: "CFBundleName") as? String)
                ?? url.deletingPathExtension().lastPathComponent
            browsers.append(BrowserApp(id: id, name: name, appURL: url, isDefault: url == defaultURL))
        }
        return browsers.sorted {
            if $0.isDefault != $1.isDefault { return $0.isDefault }
            return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    /// Opens `url` in `browser`, or in the default browser when nil.
    @discardableResult
    static func open(_ url: URL, in browser: BrowserApp?) -> Bool {
        guard let browser else {
            let opened = NSWorkspace.shared.open(url)
            browserLog.info("[browser] Default browser asked to open \(SignInOutputParser.redacted(url)): \(opened)")
            return opened
        }
        guard FileManager.default.fileExists(atPath: browser.appURL.path) else {
            browserLog.warning("[browser] \(browser.name) is no longer installed")
            return false
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        let name = browser.name
        NSWorkspace.shared.open([url], withApplicationAt: browser.appURL, configuration: configuration) { _, error in
            if let error {
                browserLog.error("[browser] \(name) could not open the sign-in link: \(error.localizedDescription)")
            }
        }
        browserLog.info("[browser] \(name) asked to open \(SignInOutputParser.redacted(url))")
        return true
    }
}
```

- [ ] **Step 2: The window**

Create `PixelSwitch/Views/SignInView.swift`:

```swift
import SwiftUI
import AppKit

/// The sign-in window: the automatic link for this Mac, the manual link and
/// code field for another device, a status line and Cancel. Nothing opens by
/// itself; every browser action is a button the user presses.
struct SignInView: View {
    @ObservedObject var session: SignInSession
    /// Close for a finished sign-in (the window controller decides what that means).
    let onClose: () -> Void

    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @State private var browsers: [BrowserApp] = []
    @State private var showOtherDevice = false
    @State private var code = ""
    @State private var codeRejected = false
    @State private var copied: CopiedLink?

    private enum CopiedLink { case automatic, manual }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            thisMacSection
            otherDeviceSection
            Divider()
            footer
        }
        .padding(20)
        .frame(width: 460)
        .onAppear { browsers = BrowserOpener.installedBrowsers() }
    }

    private var isWaiting: Bool { session.state == .waitingForUser }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            switch session.purpose {
            case .newAccount:
                Text("Sign in a new account")
                    .font(.headline)
            case .reauthenticate(_, let email):
                Text("Re-authenticate \(displayed(email))")
                    .font(.headline)
                Text("Sign in as \(displayed(email)).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func displayed(_ email: String) -> String {
        maskEmails ? email.maskedAsEmailAddress() : email
    }

    // MARK: This Mac

    private var thisMacSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Sign in on this Mac")
                .font(.subheadline.weight(.semibold))
            if let link = session.automaticLink {
                HStack(spacing: 8) {
                    Button("Open in default browser") {
                        BrowserOpener.open(link, in: nil)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.brand)

                    Menu("Open in") {
                        if browsers.isEmpty {
                            Text("No browsers found")
                        }
                        ForEach(browsers) { browser in
                            Button(browser.name) {
                                BrowserOpener.open(link, in: browser)
                            }
                        }
                    }
                    .fixedSize()

                    Button {
                        copy(link, as: .automatic)
                    } label: {
                        copied == .automatic ? Text("Copied") : Text("Copy link")
                    }
                }
                .disabled(!isWaiting)
                Text("Paste it into any browser on this Mac. It finishes by itself.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if let notice = session.notice {
                Text(verbatim: notice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if !session.state.isFinished {
                Text("Waiting for the link…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Another device

    private var otherDeviceSection: some View {
        DisclosureGroup(isExpanded: $showOtherDevice) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Open the link on the other device and sign in. The page then shows a code: paste it here.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button {
                    if let link = session.manualLink { copy(link, as: .manual) }
                } label: {
                    copied == .manual ? Text("Copied") : Text("Copy link")
                }
                .disabled(session.manualLink == nil || !isWaiting)
                HStack(spacing: 8) {
                    TextField("Paste the code the page shows", text: $code)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(submit)
                    Button("Submit", action: submit)
                        .disabled(code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !isWaiting || session.codeSubmitted)
                }
                if codeRejected {
                    Text("That isn't the whole code. Copy all of it, including the part after #.")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                Text("Also works on this Mac if the page doesn't finish by itself.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding(.top, 6)
        } label: {
            Text("Signing in on another device?")
                .font(.subheadline.weight(.semibold))
        }
    }

    private func submit() {
        guard SignInCode.normalized(code) != nil else {
            codeRejected = true
            return
        }
        codeRejected = false
        if session.submitCode(code) { code = "" }
    }

    private func copy(_ url: URL, as which: CopiedLink) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url.absoluteString, forType: .string)
        copied = which
    }

    // MARK: Status and buttons

    private var footer: some View {
        HStack(alignment: .center, spacing: 8) {
            statusIcon
            statusText
                .font(.caption)
                .foregroundStyle(statusIsError ? Color.red : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
            if session.state.isFinished {
                Button("Close", action: onClose)
                    .keyboardShortcut(.defaultAction)
            } else {
                Button("Cancel") { session.cancel() }
                    .keyboardShortcut(.cancelAction)
                    .disabled(session.state == .completing)
            }
        }
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch session.state {
        case .starting, .waitingForUser, .completing:
            ProgressView().controlSize(.small)
        case .succeeded:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed:
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        case .cancelled:
            Image(systemName: "xmark.circle").foregroundStyle(.secondary)
        }
    }

    private var statusText: Text {
        switch session.state {
        case .starting: return Text("Getting the sign-in link…")
        case .waitingForUser: return session.codeSubmitted ? Text("Checking the code…") : Text("Waiting for you to sign in in a browser…")
        case .completing: return Text("Saving the account…")
        case .succeeded: return Text("Signed in.")
        case .failed(let message): return Text(verbatim: message)
        case .cancelled: return Text("Cancelled. Nothing was changed.")
        }
    }

    private var statusIsError: Bool {
        if case .failed = session.state { return true }
        return false
    }
}
```

Create `PixelSwitch/Services/SignInWindowController.swift`:

```swift
import AppKit
import Combine
import SwiftUI

/// Shows the sign-in window whenever `AppState.currentSignIn` becomes a new
/// session, whoever started it (the popover, Settings, or later the CLI).
///
/// It is its own floating panel, not part of the popover: the popover closes
/// the moment the user switches to a browser, and the sign-in must survive that.
/// The panel closes by itself when the sign-in succeeds or is cancelled; a
/// failed sign-in stays open with its reason until the user closes it.
/// Closing the panel while the sign-in runs cancels it, so no hidden process
/// is left holding the sign-in lock.
@MainActor
final class SignInWindowController: NSObject, NSWindowDelegate {
    private var panel: NSPanel?
    private weak var appState: AppState?
    private var locale: Locale = .autoupdatingCurrent
    private var observers = Set<AnyCancellable>()
    private var stateObserver: AnyCancellable?
    private var shownSessionId: UUID?
    private var installed = false

    func install(appState: AppState, locale: Locale) {
        guard !installed else { return }
        installed = true
        self.appState = appState
        self.locale = locale
        appState.$currentSignIn
            .receive(on: DispatchQueue.main)
            .sink { [weak self] session in
                MainActor.assumeIsolated { self?.sessionChanged(session) }
            }
            .store(in: &observers)
        NotificationCenter.default.publisher(for: .pixelswitchShowSignIn)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                MainActor.assumeIsolated { self?.bringToFront() }
            }
            .store(in: &observers)
    }

    /// Follows the in-app language setting, like `StatusItemController.updateLocale`.
    func updateLocale(_ locale: Locale) {
        self.locale = locale
        if panel?.isVisible == true, let session = appState?.currentSignIn {
            show(session)
        }
    }

    private func sessionChanged(_ session: SignInSession?) {
        guard let session else {
            shownSessionId = nil
            stateObserver = nil
            hidePanel()
            return
        }
        guard session.id != shownSessionId else { return }
        shownSessionId = session.id
        stateObserver = session.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] state in
                MainActor.assumeIsolated { self?.stateChanged(state) }
            }
        show(session)
    }

    private func stateChanged(_ state: SignInState) {
        switch state {
        case .succeeded, .cancelled:
            hidePanel()
        case .failed:
            bringToFront()
        case .starting, .waitingForUser, .completing:
            break
        }
    }

    private func bringToFront() {
        guard let session = appState?.currentSignIn else { return }
        show(session)
    }

    private func show(_ session: SignInSession) {
        let root = SignInView(session: session, onClose: { [weak self] in self?.closeRequested() })
            .environment(\.locale, locale)
        let hosting = NSHostingController(rootView: AnyView(root))
        hosting.sizingOptions = [.preferredContentSize]
        let panel = self.panel ?? makePanel()
        panel.contentViewController = hosting
        panel.title = String(localized: "Sign in to Claude", bundle: L10n.bundle)
        self.panel = panel
        NSApp.activate()
        panel.makeKeyAndOrderFront(nil)
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 380),
            styleMask: [.titled, .closable, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        panel.delegate = self
        panel.center()
        return panel
    }

    private func hidePanel() {
        panel?.orderOut(nil)
        panel?.contentViewController = nil
    }

    // MARK: NSWindowDelegate

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        closeRequested()
        return false
    }

    /// The window's close button, or Close on a finished sign-in.
    private func closeRequested() {
        guard let appState, let session = appState.currentSignIn else {
            hidePanel()
            return
        }
        switch session.state {
        case .succeeded, .failed, .cancelled:
            appState.dismissSignIn()
            hidePanel()
        case .completing:
            // The account is being saved; that cannot be interrupted. Hide the
            // window; it comes back by itself if saving fails.
            panel?.orderOut(nil)
        case .starting, .waitingForUser:
            session.cancel()
        }
    }
}
```

- [ ] **Step 3: Install it at launch**

In `PixelSwitch/PixelSwitchApp.swift`, replace:

```swift
    @State private var statusItemController = StatusItemController()
```

with:

```swift
    @State private var statusItemController = StatusItemController()
    @State private var signInWindowController = SignInWindowController()
```

In `PixelSwitch/PixelSwitchApp.swift`, replace:

```swift
                        locale: currentLocale
                    )
```

with:

```swift
                        locale: currentLocale
                    )
                    signInWindowController.install(appState: appState, locale: currentLocale)
```

In `PixelSwitch/PixelSwitchApp.swift`, replace:

```swift
                    statusItemController.updateLocale(currentLocale)
```

with:

```swift
                    statusItemController.updateLocale(currentLocale)
                    signInWindowController.updateLocale(currentLocale)
```


- [ ] **Step 4: The strings, in all five languages**

The keys match the literals in `SignInView`, `SignInWindowController`, `SignInSession`, `AppState` and the popover, and Task 7's `Sign In New Account`. None of them exists in the files today, and none clashes with Part A's keys (checked when this plan was generated).

Append to the end of `PixelSwitch/en.lproj/Localizable.strings` (after a blank line):

```text
/* Sign-in links (1.2) */
"Couldn't prepare the sign-in: %@" = "Couldn't prepare the sign-in: %@";
"Couldn't start Claude Code: %@" = "Couldn't start Claude Code: %@";
"Sign-in timed out after 15 minutes. Start it again when you're ready." = "Sign-in timed out after 15 minutes. Start it again when you're ready.";
"Claude Code's sign-in stopped (exit status %lld)." = "Claude Code's sign-in stopped (exit status %lld).";
"Couldn't read the sign-in link; opened your default browser instead." = "Couldn't read the sign-in link; opened your default browser instead.";
"A sign-in is already in progress." = "A sign-in is already in progress.";
"A switch is in progress. Try again in a moment." = "A switch is in progress. Try again in a moment.";
"That account was removed during the sign-in, so nothing was saved." = "That account was removed during the sign-in, so nothing was saved.";
"Sign in a new account" = "Sign in a new account";
"Re-authenticate %@" = "Re-authenticate %@";
"Sign in as %@." = "Sign in as %@.";
"Sign in on this Mac" = "Sign in on this Mac";
"Open in default browser" = "Open in default browser";
"Open in" = "Open in";
"No browsers found" = "No browsers found";
"Copied" = "Copied";
"Copy link" = "Copy link";
"Paste it into any browser on this Mac. It finishes by itself." = "Paste it into any browser on this Mac. It finishes by itself.";
"Waiting for the link…" = "Waiting for the link…";
"Open the link on the other device and sign in. The page then shows a code: paste it here." = "Open the link on the other device and sign in. The page then shows a code: paste it here.";
"Paste the code the page shows" = "Paste the code the page shows";
"Submit" = "Submit";
"That isn't the whole code. Copy all of it, including the part after #." = "That isn't the whole code. Copy all of it, including the part after #.";
"Also works on this Mac if the page doesn't finish by itself." = "Also works on this Mac if the page doesn't finish by itself.";
"Signing in on another device?" = "Signing in on another device?";
"Close" = "Close";
"Getting the sign-in link…" = "Getting the sign-in link…";
"Checking the code…" = "Checking the code…";
"Waiting for you to sign in in a browser…" = "Waiting for you to sign in in a browser…";
"Saving the account…" = "Saving the account…";
"Signed in." = "Signed in.";
"Cancelled. Nothing was changed." = "Cancelled. Nothing was changed.";
"Sign in to Claude" = "Sign in to Claude";
"Signing in…" = "Signing in…";
"Finish in the sign-in window." = "Finish in the sign-in window.";
"Show sign-in window" = "Show sign-in window";
"Sign In New Account" = "Sign In New Account";
```

Append to the end of `PixelSwitch/de.lproj/Localizable.strings` (after a blank line):

```text
/* Sign-in links (1.2) */
"Couldn't prepare the sign-in: %@" = "Die Anmeldung konnte nicht vorbereitet werden: %@";
"Couldn't start Claude Code: %@" = "Claude Code konnte nicht gestartet werden: %@";
"Sign-in timed out after 15 minutes. Start it again when you're ready." = "Die Anmeldung wurde nach 15 Minuten abgebrochen. Starten Sie sie erneut, wenn Sie so weit sind.";
"Claude Code's sign-in stopped (exit status %lld)." = "Die Anmeldung von Claude Code wurde beendet (Exit-Status %lld).";
"Couldn't read the sign-in link; opened your default browser instead." = "Der Anmeldelink konnte nicht gelesen werden; stattdessen wurde Ihr Standardbrowser geöffnet.";
"A sign-in is already in progress." = "Es läuft bereits eine Anmeldung.";
"A switch is in progress. Try again in a moment." = "Ein Kontowechsel läuft. Versuchen Sie es gleich noch einmal.";
"That account was removed during the sign-in, so nothing was saved." = "Das Konto wurde während der Anmeldung entfernt, daher wurde nichts gespeichert.";
"Sign in a new account" = "Neues Konto anmelden";
"Re-authenticate %@" = "%@ erneut authentifizieren";
"Sign in as %@." = "Melden Sie sich als %@ an.";
"Sign in on this Mac" = "Auf diesem Mac anmelden";
"Open in default browser" = "Im Standardbrowser öffnen";
"Open in" = "Öffnen in";
"No browsers found" = "Keine Browser gefunden";
"Copied" = "Kopiert";
"Copy link" = "Link kopieren";
"Paste it into any browser on this Mac. It finishes by itself." = "Fügen Sie ihn in einen beliebigen Browser auf diesem Mac ein. Die Anmeldung schließt sich von selbst ab.";
"Waiting for the link…" = "Warten auf den Link…";
"Open the link on the other device and sign in. The page then shows a code: paste it here." = "Öffnen Sie den Link auf dem anderen Gerät und melden Sie sich an. Die Seite zeigt dann einen Code: Fügen Sie ihn hier ein.";
"Paste the code the page shows" = "Code von der Seite hier einfügen";
"Submit" = "Senden";
"That isn't the whole code. Copy all of it, including the part after #." = "Das ist nicht der ganze Code. Kopieren Sie ihn vollständig, einschließlich des Teils nach #.";
"Also works on this Mac if the page doesn't finish by itself." = "Funktioniert auch auf diesem Mac, wenn die Seite nicht von selbst abschließt.";
"Signing in on another device?" = "Anmeldung auf einem anderen Gerät?";
"Close" = "Schließen";
"Getting the sign-in link…" = "Anmeldelink wird abgerufen…";
"Checking the code…" = "Code wird geprüft…";
"Waiting for you to sign in in a browser…" = "Warten auf Ihre Anmeldung im Browser…";
"Saving the account…" = "Konto wird gespeichert…";
"Signed in." = "Angemeldet.";
"Cancelled. Nothing was changed." = "Abgebrochen. Es wurde nichts geändert.";
"Sign in to Claude" = "Bei Claude anmelden";
"Signing in…" = "Anmeldung läuft…";
"Finish in the sign-in window." = "Schließen Sie die Anmeldung im Anmeldefenster ab.";
"Show sign-in window" = "Anmeldefenster anzeigen";
"Sign In New Account" = "Neues Konto anmelden";
```

Append to the end of `PixelSwitch/fr.lproj/Localizable.strings` (after a blank line):

```text
/* Sign-in links (1.2) */
"Couldn't prepare the sign-in: %@" = "Impossible de préparer la connexion : %@";
"Couldn't start Claude Code: %@" = "Impossible de lancer Claude Code : %@";
"Sign-in timed out after 15 minutes. Start it again when you're ready." = "La connexion a expiré après 15 minutes. Relancez-la quand vous serez prêt.";
"Claude Code's sign-in stopped (exit status %lld)." = "La connexion de Claude Code s'est arrêtée (code de sortie %lld).";
"Couldn't read the sign-in link; opened your default browser instead." = "Impossible de lire le lien de connexion ; votre navigateur par défaut a été ouvert à la place.";
"A sign-in is already in progress." = "Une connexion est déjà en cours.";
"A switch is in progress. Try again in a moment." = "Un changement de compte est en cours. Réessayez dans un instant.";
"That account was removed during the sign-in, so nothing was saved." = "Ce compte a été supprimé pendant la connexion, rien n'a donc été enregistré.";
"Sign in a new account" = "Connecter un nouveau compte";
"Re-authenticate %@" = "Réauthentifier %@";
"Sign in as %@." = "Connectez-vous en tant que %@.";
"Sign in on this Mac" = "Se connecter sur ce Mac";
"Open in default browser" = "Ouvrir dans le navigateur par défaut";
"Open in" = "Ouvrir dans";
"No browsers found" = "Aucun navigateur trouvé";
"Copied" = "Copié";
"Copy link" = "Copier le lien";
"Paste it into any browser on this Mac. It finishes by itself." = "Collez-le dans n'importe quel navigateur de ce Mac. La connexion se termine d'elle-même.";
"Waiting for the link…" = "En attente du lien…";
"Open the link on the other device and sign in. The page then shows a code: paste it here." = "Ouvrez le lien sur l'autre appareil et connectez-vous. La page affiche alors un code : collez-le ici.";
"Paste the code the page shows" = "Collez le code affiché par la page";
"Submit" = "Envoyer";
"That isn't the whole code. Copy all of it, including the part after #." = "Ce n'est pas le code complet. Copiez-le en entier, y compris la partie après #.";
"Also works on this Mac if the page doesn't finish by itself." = "Fonctionne aussi sur ce Mac si la page ne termine pas d'elle-même.";
"Signing in on another device?" = "Connexion sur un autre appareil ?";
"Close" = "Fermer";
"Getting the sign-in link…" = "Récupération du lien de connexion…";
"Checking the code…" = "Vérification du code…";
"Waiting for you to sign in in a browser…" = "En attente de votre connexion dans un navigateur…";
"Saving the account…" = "Enregistrement du compte…";
"Signed in." = "Connecté.";
"Cancelled. Nothing was changed." = "Annulé. Rien n'a été modifié.";
"Sign in to Claude" = "Se connecter à Claude";
"Signing in…" = "Connexion en cours…";
"Finish in the sign-in window." = "Terminez dans la fenêtre de connexion.";
"Show sign-in window" = "Afficher la fenêtre de connexion";
"Sign In New Account" = "Connecter un nouveau compte";
```

Append to the end of `PixelSwitch/ja.lproj/Localizable.strings` (after a blank line):

```text
/* Sign-in links (1.2) */
"Couldn't prepare the sign-in: %@" = "サインインを準備できませんでした: %@";
"Couldn't start Claude Code: %@" = "Claude Code を起動できませんでした: %@";
"Sign-in timed out after 15 minutes. Start it again when you're ready." = "15 分経過したためサインインを中止しました。準備ができたら、もう一度開始してください。";
"Claude Code's sign-in stopped (exit status %lld)." = "Claude Code のサインインが終了しました（終了ステータス %lld）。";
"Couldn't read the sign-in link; opened your default browser instead." = "サインインリンクを読み取れなかったため、代わりにデフォルトのブラウザを開きました。";
"A sign-in is already in progress." = "すでにサインインが進行中です。";
"A switch is in progress. Try again in a moment." = "アカウントの切り替え中です。しばらくしてからもう一度お試しください。";
"That account was removed during the sign-in, so nothing was saved." = "サインイン中にこのアカウントが削除されたため、何も保存されませんでした。";
"Sign in a new account" = "新しいアカウントでサインイン";
"Re-authenticate %@" = "%@ を再認証";
"Sign in as %@." = "%@ としてサインインしてください。";
"Sign in on this Mac" = "この Mac でサインイン";
"Open in default browser" = "デフォルトのブラウザで開く";
"Open in" = "ブラウザを選んで開く";
"No browsers found" = "ブラウザが見つかりません";
"Copied" = "コピーしました";
"Copy link" = "リンクをコピー";
"Paste it into any browser on this Mac. It finishes by itself." = "この Mac のどのブラウザに貼り付けても、サインインは自動で完了します。";
"Waiting for the link…" = "リンクを待っています…";
"Open the link on the other device and sign in. The page then shows a code: paste it here." = "別のデバイスでリンクを開いてサインインしてください。ページにコードが表示されたら、ここに貼り付けます。";
"Paste the code the page shows" = "ページに表示されたコードを貼り付け";
"Submit" = "送信";
"That isn't the whole code. Copy all of it, including the part after #." = "コードの一部しかありません。# 以降も含めてすべてコピーしてください。";
"Also works on this Mac if the page doesn't finish by itself." = "ページが自動で完了しない場合は、この Mac でも使えます。";
"Signing in on another device?" = "別のデバイスでサインインしますか？";
"Close" = "閉じる";
"Getting the sign-in link…" = "サインインリンクを取得中…";
"Checking the code…" = "コードを確認中…";
"Waiting for you to sign in in a browser…" = "ブラウザでのサインインを待っています…";
"Saving the account…" = "アカウントを保存中…";
"Signed in." = "サインインしました。";
"Cancelled. Nothing was changed." = "キャンセルしました。何も変更されていません。";
"Sign in to Claude" = "Claude にサインイン";
"Signing in…" = "サインイン中…";
"Finish in the sign-in window." = "サインインウィンドウで完了してください。";
"Show sign-in window" = "サインインウィンドウを表示";
"Sign In New Account" = "新しいアカウントでサインイン";
```

Append to the end of `PixelSwitch/zh-Hans.lproj/Localizable.strings` (after a blank line):

```text
/* Sign-in links (1.2) */
"Couldn't prepare the sign-in: %@" = "无法准备登录：%@";
"Couldn't start Claude Code: %@" = "无法启动 Claude Code：%@";
"Sign-in timed out after 15 minutes. Start it again when you're ready." = "登录已在 15 分钟后超时。准备好后请重新开始。";
"Claude Code's sign-in stopped (exit status %lld)." = "Claude Code 的登录已停止（退出状态 %lld）。";
"Couldn't read the sign-in link; opened your default browser instead." = "无法读取登录链接，已改为打开默认浏览器。";
"A sign-in is already in progress." = "已有登录正在进行。";
"A switch is in progress. Try again in a moment." = "正在切换账户，请稍后再试。";
"That account was removed during the sign-in, so nothing was saved." = "该账户在登录过程中被移除，因此未保存任何内容。";
"Sign in a new account" = "登录新账户";
"Re-authenticate %@" = "重新认证 %@";
"Sign in as %@." = "请以 %@ 登录。";
"Sign in on this Mac" = "在这台 Mac 上登录";
"Open in default browser" = "在默认浏览器中打开";
"Open in" = "打开方式";
"No browsers found" = "未找到浏览器";
"Copied" = "已复制";
"Copy link" = "复制链接";
"Paste it into any browser on this Mac. It finishes by itself." = "粘贴到这台 Mac 上的任意浏览器，登录会自动完成。";
"Waiting for the link…" = "正在等待链接…";
"Open the link on the other device and sign in. The page then shows a code: paste it here." = "在另一台设备上打开链接并登录。页面随后会显示一个代码：请粘贴到这里。";
"Paste the code the page shows" = "粘贴页面显示的代码";
"Submit" = "提交";
"That isn't the whole code. Copy all of it, including the part after #." = "代码不完整。请完整复制，包括 # 后面的部分。";
"Also works on this Mac if the page doesn't finish by itself." = "如果页面没有自动完成，也可以在这台 Mac 上使用。";
"Signing in on another device?" = "在另一台设备上登录？";
"Close" = "关闭";
"Getting the sign-in link…" = "正在获取登录链接…";
"Checking the code…" = "正在验证代码…";
"Waiting for you to sign in in a browser…" = "正在等待你在浏览器中登录…";
"Saving the account…" = "正在保存账户…";
"Signed in." = "已登录。";
"Cancelled. Nothing was changed." = "已取消，未做任何更改。";
"Sign in to Claude" = "登录 Claude";
"Signing in…" = "正在登录…";
"Finish in the sign-in window." = "请在登录窗口中完成。";
"Show sign-in window" = "显示登录窗口";
"Sign In New Account" = "登录新账户";
```


Run: `for l in en de fr ja zh-Hans; do plutil -lint PixelSwitch/$l.lproj/Localizable.strings; done`
Expected: five `OK` lines.

- [ ] **Step 5: Verify**

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK`.

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `351 passed, 0 failed`.

- [ ] **Step 6: Commit**

```bash
git add PixelSwitch/Services/BrowserOpener.swift PixelSwitch/Services/SignInWindowController.swift PixelSwitch/Views/SignInView.swift PixelSwitch/PixelSwitchApp.swift PixelSwitch/*.lproj/Localizable.strings
git commit -m "$(cat <<'EOF'
Sign-in links: the floating sign-in window with Open, Open in and Copy

Its own NSPanel, so it survives the popover closing when the user goes to a
browser. Open in lists every installed browser, default first. Closing it while the
sign-in runs cancels it. Strings in all five languages.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Add Current Account and Sign In New Account in Settings → Accounts

**Files:**
- Create: `PixelSwitch/Views/SettingsSignInButtons.swift`
- Modify: `PixelSwitch/Views/SettingsAccountsTab.swift` (created by Part A Task 8)

**Interfaces:**
- Consumes: `AppState.addAccount()`, `AppState.loginNewAccount()`, `AppState.isLoggingIn`, Part A's `SettingsAccountsTab`.
- Produces: `struct SettingsSignInButtons: View`.

- [ ] **Step 1: The buttons**

Create `PixelSwitch/Views/SettingsSignInButtons.swift`:

```swift
import SwiftUI

/// Settings → Accounts: the same two ways to add an account as the popover.
/// "Sign In New Account" opens the sign-in window; "Add Current Account"
/// saves whoever Claude Code is signed in as, after the same confirmation.
struct SettingsSignInButtons: View {
    @EnvironmentObject private var appState: AppState
    @State private var confirmingAddCurrent = false

    var body: some View {
        HStack(spacing: 12) {
            Button("Add Current Account") {
                confirmingAddCurrent = true
            }
            Button("Sign In New Account") {
                appState.loginNewAccount()
            }
            .buttonStyle(.borderedProminent)
            .tint(.brand)
        }
        .disabled(appState.isLoggingIn)
        .confirmationDialog(
            "This will capture the currently logged-in Claude Code account.",
            isPresented: $confirmingAddCurrent,
            titleVisibility: .visible
        ) {
            Button("Add Account") {
                Task { await appState.addAccount() }
            }
            Button("Cancel", role: .cancel) {}
        }
    }
}
```

- [ ] **Step 2: Put them in the tab**

In `PixelSwitch/Views/SettingsAccountsTab.swift`, replace the doc-comment line:

```swift
/// Sign-in buttons are added here by Part B (sign-in links); this tab has none yet.
```

with:

```swift
/// Add Current Account and Sign In New Account sit under the list
/// (`SettingsSignInButtons`); both open the same sign-in window as the popover.
```

and replace the end of `body` (the explanation text's last modifier, the `VStack`'s closing brace and `.padding()`):

```swift
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding()
    }

    // MARK: - Row
```

with:

```swift
                .fixedSize(horizontal: false, vertical: true)

            SettingsSignInButtons()
        }
        .padding()
    }

    // MARK: - Row
```

- [ ] **Step 3: Verify**

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK`.

- [ ] **Step 4: Commit**

```bash
git add PixelSwitch/Views/SettingsSignInButtons.swift PixelSwitch/Views/SettingsAccountsTab.swift
git commit -m "$(cat <<'EOF'
Sign-in links: Add Current Account and Sign In New Account in Settings

Both sit under the accounts list in Settings → Accounts and use the same actions as
the popover; Add Current Account keeps its confirmation.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Documentation, full verification, the CI build and the manual checks

**Files:**
- Modify: `AGENTS.md` (the Features line)
- Modify: `README.md` (section "3. Terminal-Free Login Flow")
- Modify: `ARCHITECTURE.md` (the two "Step 2: Run `claude auth login`" lines, the data-flow box, a new short section)

- [ ] **Step 1: Documentation**

In `AGENTS.md`, replace:

```text
**Features**: Terminal-free login (Process/Pipe interception), zero-interaction token refresh (`security` CLI workaround), API usage tracking.
```

with:

```text
**Features**: Sign-in links you control (PixelSwitch runs `claude auth login` with `BROWSER` pointed at a per-sign-in capture helper and shows both links with Open / Open in / Copy; nothing opens by itself; `SignInSession`, `SignInWindowController`), zero-interaction token refresh (`security` CLI workaround), API usage tracking.
```

In `README.md`, replace the three bullets under `### 3. Terminal-Free Login Flow (Native \`Process\` + \`Pipe\`)`:

```text
- We rely on native `Process` and standard `Pipe()` redirection.
- When `claude auth login` is executed silently in the background, the Claude CLI detects the non-interactive environment and automatically launches the system's default browser to handle the OAuth loop.
- Once the user authorizes in the browser, the background CLI process terminates with exit code 0. PixelSwitch then captures the newly-generated keychain credentials and `oauthAccount` block — the user never opens a terminal.
```

with:

```text
- PixelSwitch runs `claude auth login` itself, with native `Process` and `Pipe`, and reads its output as it streams.
- Claude Code opens a browser by running `$BROWSER <link>` when `BROWSER` is set, so PixelSwitch points it at a tiny helper, written into a private temporary folder for that one sign-in, that records the link instead. Nothing opens by itself.
- A small window shows the link with **Open in default browser**, **Open in** (every browser installed on the Mac) and **Copy link**. It finishes by itself in any browser on the same Mac, so the right already-signed-in browser can finish it. **Signing in on another device?** gives a second link whose page shows a code to paste back.
- Once Claude Code exits 0, PixelSwitch captures the new keychain credentials and `oauthAccount` block, as before. If a future Claude Code prints something PixelSwitch cannot read within 10 seconds, it falls back to letting Claude Code open the default browser.
```

In `ARCHITECTURE.md`, replace:

```text
Step 2: Run `claude auth login` (opens browser, blocks until complete)

    Claude CLI starts local HTTP server → opens browser → user logs in.
```

with:

```text
Step 2: Run `claude auth login` with BROWSER pointed at the sign-in link
        capture helper (see "Sign-in links" below). Nothing opens by itself.

    Claude CLI starts local HTTP server → hands the link to the helper →
    the user opens it in the browser of their choice → user logs in.
```

replace:

```text
Step 2: Run `claude auth login` (opens browser)
```

with:

```text
Step 2: Run `claude auth login --email A@...` (the same sign-in window)
```

replace:

```text
│  `claude auth login`  ──────┼──► ClaudeService (opens browser)
```

with:

```text
│  `claude auth login`  ──────┼──► SignInSession (shows links; opens nothing)
```

and insert this section immediately before the line `## Login New Account — Complete Token Flow`:

```text
## Sign-in links (1.2)

`claude auth login` produces two links. PixelSwitch shows both and opens neither on its own:

- **Automatic link** (`redirect_uri=http://localhost:<port>/callback`, served by the CLI itself). Claude Code hands it to `$BROWSER`; PixelSwitch sets `BROWSER` to a helper script written into `$TMPDIR/pixelswitch-signin-<uuid>/` (folder 0700, helper 0700, capture file 0600) that appends the link to a file and exits 0. It finishes by itself in any browser on this Mac.
- **Manual link** (`redirect_uri=https://platform.claude.com/oauth/code/callback`), read from the `If the browser didn't open, visit:` line. Its page shows `code#state`, which PixelSwitch writes to the CLI's stdin.

`SignInSession` owns the process: starting → waitingForUser (first link known) → completing (exit 0; `AppState` captures the account) → succeeded, failed or cancelled. Cancel sends SIGTERM, then SIGKILL after 2 s. A sign-in is stopped after 15 minutes. If neither link appears within 10 s, it reruns `claude auth login` without the helper (today's default-browser behaviour). The temporary folder is deleted when the session ends. Link query values (`state`, `code_challenge`) are never logged.

```

- [ ] **Step 2: Full verification**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `351 passed, 0 failed`.

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK`.

Run: `git status --short`
Expected: only the three documentation files modified.

- [ ] **Step 3: Commit the documentation**

```bash
git add AGENTS.md README.md ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
Docs: sign-in links replace the auto-opened browser

AGENTS.md, README and ARCHITECTURE now describe the BROWSER capture helper, the two
links, the sign-in window and the fallback.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: The CI build**

```bash
git push -u origin part-b-sign-in-links
gh workflow run "Build and Notarize macOS App" --repo BespokeWoodcraftStudio/PixelSwitch --ref part-b-sign-in-links
sleep 10
RUN=$(gh run list --repo BespokeWoodcraftStudio/PixelSwitch --branch part-b-sign-in-links --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN" --repo BespokeWoodcraftStudio/PixelSwitch --exit-status
gh run download "$RUN" --repo BespokeWoodcraftStudio/PixelSwitch -n PixelSwitch-macOS -D /tmp/pixelswitch-part-b
```

Expected: the run succeeds (build, sign, notarize, staple) and `/tmp/pixelswitch-part-b/PixelSwitch.dmg` exists.

- [ ] **Step 5: Manual checks (a human at this Mac)**

These need the built app. The founder may do them now on this build, or once on the combined build after Part C (Decision 10); record which in the worklog. Install the DMG over the current app, open PixelSwitch, then:

1. Popover → Accounts → **Login New Account**. A "Sign in to Claude" window appears; **no browser opens by itself**. Within a couple of seconds it shows Open in default browser, Open in, Copy link.
2. **Open in** lists the installed browsers, default first. Pick a browser that is already signed in to the account you want to add. It finishes by itself; the window closes and the account appears in the list, active.
3. **Real sign-in in a second browser:** Sign In New Account again (Settings → Accounts), click **Copy link**, paste it into a DIFFERENT browser signed in to another account. It finishes by itself and that account is added. (This is the founder's multi-browser case; it must be done by a human.)
4. Start a sign-in and **close the window** with its close button. The sign-in is cancelled; the popover no longer says "Signing in…"; a new sign-in can start at once.
5. Start a sign-in, click away so the popover closes, reopen the popover: it says "Signing in…" with **Show sign-in window**, which brings the window back.
6. Re-authenticate (↻) an account: the page is pre-filled with that account's email; signing in to a **different** account instead ends with "Logged in as X, but expected Y. Credentials not updated." and nothing is overwritten.
7. **Signing in on another device?**: Copy link, open it on a phone or another Mac, sign in, paste the code the page shows into the box, Submit. The account is saved.
8. With a sign-in running, press **Sign In New Account** in Settings: it is refused with "A sign-in is already in progress." and the running window comes to the front.
9. `ls "$TMPDIR" | grep pixelswitch-signin-` after the checks: nothing left behind.

- [ ] **Step 6: Record and hand over**

Write the work-journal entry with the `worklog` skill (What / Why / How / Outcome, Lessons, Follow-ups), including which manual checks were done and on which build. Commit and push. Merge `part-b-sign-in-links` into `main` only after the founder's manual checks pass (or, per Decision 10, after they pass on the combined build), then start Part C.

## Self-review against the spec

| Spec item | Where |
|---|---|
| B1 run `claude auth login` with piped stdout/stderr/stdin, `BROWSER` = capture helper | Task 3 (helper), Task 4 (`ProcessSignInRunner`, `start()`), Task 5 (`signInLaunch()`) |
| B1 manual link from the `visit:` line, fallback to the first code-callback URL; strip escapes | Task 1 |
| B1 automatic link from the helper's file | Tasks 1 and 4 (capture poll) |
| B1 `submitCode` writes `code#state` to stdin | Tasks 2 and 4 |
| B1 cancel: SIGTERM then SIGKILL after 2 s; 15-minute timeout | Task 4 |
| B1 states starting → waitingForUser → completing → succeeded/failed/cancelled | Task 2 (types), Task 4 (machine) |
| B1 re-auth passes `--email` | Task 4 (`arguments(for:)`) |
| B1 10-second fallback with the stated message | Task 4 |
| B1 `currentSignIn` observed by GUI and control API; either side can finish or cancel | Task 5 (`currentSignIn`), Task 6 (window follows it); Part C reads the same object |
| B2 sheet contents: Open / Open in / Copy + note; other-device link + code + Submit; status; Cancel; nothing opens by itself | Task 6 (`SignInView`), Task 8 checks 1–7 |
| B2 entry points: popover Login New Account, Re-authenticate; Settings → Accounts buttons | Task 5 (popover), Task 7 (Settings) |
| B2 Add Current Account unchanged | Task 7 keeps the confirmation and `addAccount()` |
| B3 parser cases, state machine against a fake, manual two-browser check | Tasks 1 and 4; Task 8 check 3 |
| Section 9 names and types | Global Constraints; each task's Interfaces |
| Section 9 test convention | Task 1 |
| Review Focus 1–5 | Tasks 2 and 4 tests, Task 8 checks 4, 6, 8 |
