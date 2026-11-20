// The Resets button's claude.ai window (PixelSwitch/ClaudeWeb): the account to
// data-store mapping, the navigation policy, the pasted sign-in link, the
// signed-in check, and the pins that keep the window from ever clicking,
// submitting or resetting anything.
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from
// main.swift. Uses the global `check(_:_:_:)` defined there.
//
// The founder, 2026-11-19: "go with option A" (a claude.ai window per account,
// each with its own sign-in) and "you absolutly do not execute a reset".
import Foundation

@MainActor func runClaudeWebTests() {
    claudeWebStoreMapTests()
    claudeWebNavigationTests()
    claudeWebPastedLinkTests()
    claudeWebPageKindAndLogTests()
    claudeWebAccountCheckTests()
    claudeWebSourcePins()
    claudeWebRepoWidePins()
    claudeWebStringTests()
}

private func u(_ text: String) -> URL? { URL(string: text) }

// MARK: - Account to data store

@MainActor private func claudeWebStoreMapTests() {
    let suite = "ai.pixelventures.pixelswitch.tests.claudeweb.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else {
        check(false, "claudeWeb store: a throwaway defaults suite could not be made")
        return
    }
    defer { defaults.removePersistentDomain(forName: suite) }

    let a = "11111111-AAAA-4AAA-8AAA-111111111111"
    let b = "22222222-BBBB-4BBB-8BBB-222222222222"

    check(ClaudeWebStoreMap.existingIdentifier(for: a, defaults: defaults) == nil,
          "claudeWeb store: an account with no sign-in has no identifier yet")
    let first = ClaudeWebStoreMap.identifier(for: a, defaults: defaults)
    check(ClaudeWebStoreMap.identifier(for: a, defaults: defaults) == first,
          "claudeWeb store: asking twice for one account gives the same identifier")
    check(ClaudeWebStoreMap.existingIdentifier(for: a, defaults: defaults) == first,
          "claudeWeb store: the identifier is saved once it has been made")

    // Persisted: a second UserDefaults object on the same suite (a relaunch) sees it.
    if let relaunched = UserDefaults(suiteName: suite) {
        check(ClaudeWebStoreMap.identifier(for: a, defaults: relaunched) == first,
              "claudeWeb store: the identifier survives a relaunch (read back from saved defaults)")
    } else {
        check(false, "claudeWeb store: reopen the suite")
    }

    check(ClaudeWebStoreMap.identifier(for: b, defaults: defaults) != first,
          "claudeWeb store: two accounts get two different identifiers")
    check(ClaudeWebStoreMap.identifier(for: a.lowercased(), defaults: defaults) == first
          && ClaudeWebStoreMap.identifier(for: "  \(a)\n", defaults: defaults) == first,
          "claudeWeb store: letter case and stray spaces in an account id do not make a second store")

    // Seven accounts, seven distinct stores.
    var seven = Set<UUID>()
    for index in 0..<7 {
        seven.insert(ClaudeWebStoreMap.identifier(for: "seven-\(index)", defaults: defaults))
    }
    check(seven.count == 7, "claudeWeb store: seven accounts have seven distinct identifiers", "\(seven.count)")

    // A fresh identifier is never one another account already holds.
    let taken = ClaudeWebStoreMap.identifier(for: "holder", defaults: defaults)
    let fresh = UUID()
    var queue = [taken, taken, fresh]
    let sequenced = ClaudeWebStoreMap.identifier(for: "newcomer", defaults: defaults, makeID: { queue.isEmpty ? UUID() : queue.removeFirst() })
    check(sequenced == fresh, "claudeWeb store: a made identifier that another account holds is thrown away", "\(sequenced)")
    let zero = UUID(uuid: (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    var zeroQueue = [zero, zero]
    let notZero = ClaudeWebStoreMap.identifier(for: "zero-case", defaults: defaults, makeID: { zeroQueue.isEmpty ? UUID() : zeroQueue.removeFirst() })
    check(notZero != zero, "claudeWeb store: the all-zero UUID (WebKit refuses it) is never used")

    // The saved value: namespaced key, only account ids and UUIDs, no address.
    check(ClaudeWebStoreMap.defaultsKey.hasPrefix("ai.pixelventures.pixelswitch."),
          "claudeWeb store: the defaults key is PixelSwitch-namespaced", ClaudeWebStoreMap.defaultsKey)
    let raw = defaults.dictionary(forKey: ClaudeWebStoreMap.defaultsKey) as? [String: String] ?? [:]
    check(!raw.isEmpty && raw.values.allSatisfy { UUID(uuidString: $0) != nil } && !raw.keys.contains { $0.contains("@") },
          "claudeWeb store: what is saved is account id to UUID text, never an e-mail address")

    // Forgetting one account frees only that account.
    let removed = ClaudeWebStoreMap.forget(a, defaults: defaults)
    check(removed == first, "claudeWeb store: forget returns the identifier it dropped, so its data store can be removed")
    check(ClaudeWebStoreMap.existingIdentifier(for: a, defaults: defaults) == nil
          && ClaudeWebStoreMap.existingIdentifier(for: b, defaults: defaults) != nil,
          "claudeWeb store: forgetting one account leaves the others")
    check(ClaudeWebStoreMap.identifier(for: a, defaults: defaults) != first,
          "claudeWeb store: after a sign-out the account starts again under a new identifier")
    check(ClaudeWebStoreMap.forget("never-saved", defaults: defaults) == nil,
          "claudeWeb store: forgetting an account with no store does nothing")

    // A damaged entry is replaced, not trusted.
    defaults.set([ClaudeWebStoreMap.normalized("damaged"): "not-a-uuid"], forKey: ClaudeWebStoreMap.defaultsKey)
    let repaired = ClaudeWebStoreMap.identifier(for: "damaged", defaults: defaults)
    check(ClaudeWebStoreMap.existingIdentifier(for: "damaged", defaults: defaults) == repaired,
          "claudeWeb store: a saved value that is not a UUID is replaced with a valid one")
}

// MARK: - Navigation policy

@MainActor private func claudeWebNavigationTests() {
    typealias P = ClaudeWebPolicy
    func main(_ text: String) -> P.Decision { P.decide(url: u(text), isMainFrame: true) }
    func sub(_ text: String) -> P.Decision { P.decide(url: u(text), isMainFrame: false) }

    check(P.usageURL.absoluteString == "https://claude.ai/settings/usage", "claudeWeb policy: the window opens claude.ai/settings/usage")

    let allowed = [
        "https://claude.ai/settings/usage", "https://claude.ai", "https://www.claude.ai/login",
        "https://a.b.claude.ai/x", "https://claude.ai./settings", "HTTPS://CLAUDE.AI/login", "https://claude.ai:443/login",
        "https://anthropic.com", "https://www.anthropic.com/legal", "https://console.anthropic.com/settings",
        "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y",
        "https://appleid.apple.com/auth/authorize", "https://idmsa.apple.com/appleauth/auth/signin",
    ]
    for text in allowed {
        check(main(text) == .allow, "claudeWeb policy: loads in the window: \(text)", "\(main(text))")
    }

    let toBrowser = [
        "https://claude.ai.evil.com/login", "https://evilclaude.ai/login", "https://claude.ai-evil.com/", "https://notclaude.ai/",
        "https://claudeXai/", "https://anthropic.com.evil.com/", "https://evilanthropic.com/", "https://claude.ai.com/",
        "https://evil.com/?next=https://claude.ai/login", "https://evil.com/claude.ai", "https://evil.com#@claude.ai",
        "https://evil.accounts.google.com/", "https://mail.google.com/", "https://google.com/", "https://www.apple.com/",
        "https://example.com/", "https://claude.ai:8443/login", "https://billing.stripe.com/p/session",
    ]
    for text in toBrowser {
        check(main(text) == .openInBrowser, "claudeWeb policy: goes to the default browser: \(text)", "\(main(text))")
    }

    check(main("http://claude.ai/login") == .openInBrowser, "claudeWeb policy: plain http is never loaded in the window")
    check(main("https://claude.ai@evil.com/") == .block && main("https://user:secret@claude.ai/") == .block,
          "claudeWeb policy: a link with a user name in it (claude.ai@evil.com) is refused")
    for text in ["mailto:support@anthropic.com", "file:///etc/hosts", "javascript:alert(1)", "data:text/html,hello",
                 "blob:https://claude.ai/1234", "ftp://claude.ai/", "claude://login", "about:config"] {
        check(main(text) == .block, "claudeWeb policy: refused outright: \(text)", "\(main(text))")
    }
    check(main("about:blank") == .allow, "claudeWeb policy: about:blank (a pop-up's first page) is allowed")
    check(P.decide(url: nil, isMainFrame: true) == .block && P.decide(url: nil, isMainFrame: false) == .block,
          "claudeWeb policy: no address at all is refused")
    // A look-alike letter from another alphabet never matches (Cyrillic а in "clаude").
    check(main("https://cl\u{0430}ude.ai/login") != .allow, "claudeWeb policy: a Cyrillic look-alike of claude.ai is not allowed")

    // Hosts.
    check(P.isAllowedHost("claude.ai") && P.isAllowedHost("Claude.AI") && P.isAllowedHost("x.claude.ai")
          && P.isAllowedHost("anthropic.com") && P.isAllowedHost("accounts.google.com"),
          "claudeWeb policy hosts: claude.ai, anthropic.com, their subdomains and the sign-in providers")
    check(!P.isAllowedHost(nil) && !P.isAllowedHost("") && !P.isAllowedHost(".claude.ai") && !P.isAllowedHost("claude..ai")
          && !P.isAllowedHost("claude.ai evil.com") && !P.isAllowedHost("claude.ai%2eevil.com") && !P.isAllowedHost("claude_ai.com"),
          "claudeWeb policy hosts: empty, malformed and look-alike hosts are refused")
    check(!P.isAllowedHost("google.com") && !P.isAllowedHost("apple.com") && !P.isAllowedHost("evil.appleid.apple.com"),
          "claudeWeb policy hosts: a sign-in provider's other hosts and subdomains are not allowed")

    // A frame embedded in a page (a provider form, a Cloudflare check) may load any https address.
    check(sub("https://challenges.cloudflare.com/cdn-cgi/challenge") == .allow && sub("https://claude.ai/x") == .allow,
          "claudeWeb policy: an embedded frame may load any https address")
    check(sub("http://example.com/") == .block && sub("mailto:a@b.com") == .block && sub("https://claude.ai@evil.com/") == .block,
          "claudeWeb policy: an embedded frame still may not load plain http, other schemes or a user-name link")
}

// MARK: - Pasted sign-in link

@MainActor private func claudeWebPastedLinkTests() {
    typealias P = ClaudeWebPolicy
    func problem(_ text: String) -> P.Problem? {
        if case .invalid(let reason) = P.validatePastedLink(text) { return reason }
        return nil
    }

    // The emailed link carries its token in the fragment.
    let magic = "https://claude.ai/magic-link#abc123def456:ZW1haWxAZXhhbXBsZS5jb20="
    if case .valid(let url) = P.validatePastedLink(magic) {
        check(url.absoluteString == magic, "claudeWeb paste: the emailed link is loaded exactly as pasted, token and all", url.absoluteString)
    } else {
        check(false, "claudeWeb paste: the emailed sign-in link is accepted", "\(P.validatePastedLink(magic))")
    }
    for text in ["  \(magic)\n", "https://www.claude.ai/login?returnTo=%2Fsettings%2Fusage", "HTTPS://claude.ai/x", "https://a.claude.ai/y", "https://claude.ai:443/z", "https://claude.ai./z"] {
        if case .valid = P.validatePastedLink(text) {
            check(true, "claudeWeb paste: accepted \(text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(40))")
        } else {
            check(false, "claudeWeb paste: accepted \(text.prefix(40))", "\(P.validatePastedLink(text))")
        }
    }

    check(problem("") == .empty && problem("   \n ") == .empty, "claudeWeb paste: nothing pasted is reported as empty")
    for text in ["https://evil.com/", "https://claude.ai.evil.com/magic-link#x", "https://evilclaude.ai/", "https://anthropic.com/",
                 "https://accounts.google.com/", "https://claude.ai@evil.com/", "https://user@claude.ai/", "https://claude.ai:8080/", "https://claude.com/"] {
        check(problem(text) == .notClaudeAI, "claudeWeb paste: only claude.ai links load, refused: \(text)", "\(String(describing: problem(text)))")
    }
    check(problem("http://claude.ai/magic-link") == .notHTTPS, "claudeWeb paste: an http link is refused as not https")
    for text in ["claude.ai/magic-link", "javascript:alert(1)", "file:///etc/hosts", "ftp://claude.ai/", "mailto:x@claude.ai",
                 "https://claude.ai/a b", "https://claude.ai/a\nb", "https://claude.ai\\@evil.com/", "https://claude.ai/a\tb", "just some words"] {
        check(problem(text) == .notALink, "claudeWeb paste: not one whole https link, refused: \(text.debugDescription)", "\(String(describing: problem(text)))")
    }
}

// MARK: - Which page it is, and what is safe to log

@MainActor private func claudeWebPageKindAndLogTests() {
    typealias P = ClaudeWebPolicy
    check(P.isSignInPage(u("https://claude.ai/login")) && P.isSignInPage(u("https://claude.ai/login?returnTo=%2Fsettings%2Fusage"))
          && P.isSignInPage(u("https://claude.ai/magic-link#token")) && P.isSignInPage(u("https://claude.ai/logout"))
          && P.isSignInPage(u("https://www.claude.ai/signup")),
          "claudeWeb page: claude.ai's login, magic-link, logout and sign-up pages are sign-in pages")
    check(P.isSignInPage(u("https://accounts.google.com/o/oauth2/v2/auth")) && P.isSignInPage(u("https://appleid.apple.com/auth/authorize")),
          "claudeWeb page: the Google and Apple sign-in pages are sign-in pages")
    check(!P.isSignInPage(u("https://claude.ai/settings/usage")) && !P.isSignInPage(u("https://claude.ai/chat/abc"))
          && !P.isSignInPage(u("https://claude.ai/")) && !P.isSignInPage(nil),
          "claudeWeb page: the usage page, chats and the home page are not sign-in pages")
    check(!P.isSignInPage(u("https://evil.com/login")) && !P.isSignInPage(u("http://claude.ai/login")) && !P.isSignInPage(u("https://claude.ai.evil.com/login")),
          "claudeWeb page: a login page on another site, or over http, does not show the Sign in as bar")

    // Logging: host and first path word, never a query, a fragment, a user name or a token.
    check(P.loggable(u("https://claude.ai/magic-link#SECRETTOKEN:abc")) == "claude.ai/magic-link",
          "claudeWeb log: the fragment of an emailed sign-in link is dropped", P.loggable(u("https://claude.ai/magic-link#SECRETTOKEN:abc")))
    let withQuery = P.loggable(u("https://claude.ai/login?code=SECRETCODE&state=SECRETSTATE&email=a@b.com"))
    check(withQuery == "claude.ai/login" && !withQuery.contains("SECRET") && !withQuery.contains("@"),
          "claudeWeb log: a query string never reaches the log", withQuery)
    check(P.loggable(u("https://claude.ai/ABCDEF1234567890abcdef1234567890")) == "claude.ai/…",
          "claudeWeb log: a first path piece that looks like a token is replaced")
    check(P.loggable(u("https://claude.ai/settings/usage")) == "claude.ai/settings",
          "claudeWeb log: only the first path word is kept")
    let withUser = P.loggable(u("https://me:hunter2@claude.ai/x"))
    check(withUser == "claude.ai/x" && !withUser.contains("hunter2") && !withUser.contains("me:"),
          "claudeWeb log: a user name and password in a link never reach the log", withUser)
    check(P.loggable(u("blob:https://claude.ai/0000")) == "blob:" && P.loggable(nil) == "(none)" && P.loggable(u("https://claude.ai")) == "claude.ai",
          "claudeWeb log: other schemes, no address and a bare host are handled")
}

// MARK: - Signed-in check

@MainActor private func claudeWebAccountCheckTests() {
    typealias C = ClaudeWebAccountCheck
    check(C.emails(in: "Signed in as Ahmed@Example.com. Also ahmed@example.com, other.person+tag@mail.example.co.uk!")
          == ["ahmed@example.com", "other.person+tag@mail.example.co.uk"],
          "claudeWeb check: finds each address once, lowercased, without a sentence's full stop", "\(C.emails(in: "Signed in as Ahmed@Example.com. Also ahmed@example.com, other.person+tag@mail.example.co.uk!"))")
    check(C.emails(in: "").isEmpty && C.emails(in: "no address here, 100% free, @handle").isEmpty,
          "claudeWeb check: text with no address finds none")

    check(C.verdict(expected: "Me@Example.com", pageTexts: ["Account", "signed in as me@example.com"]) == .matches,
          "claudeWeb check: the expected address on the page is a match, whatever its case")
    check(C.verdict(expected: "me@example.com", pageTexts: ["Signed in as other@gmail.com"]) == .mismatch(found: "other@gmail.com"),
          "claudeWeb check: one different address on the page is a mismatch")
    check(C.verdict(expected: "me@example.com", pageTexts: ["help: support@anthropic.com", "Signed in as other@gmail.com"]) == .mismatch(found: "other@gmail.com"),
          "claudeWeb check: a support address of Anthropic's on the page is ignored")
    check(C.verdict(expected: "me@example.com", pageTexts: ["Contact support@anthropic.com or privacy@claude.ai"]) == .unknown,
          "claudeWeb check: only the product's own addresses on the page says nothing")
    check(C.verdict(expected: "me@example.com", pageTexts: ["a@one.com and b@two.com"]) == .unknown,
          "claudeWeb check: two different addresses is not enough to say who is signed in")
    check(C.verdict(expected: "me@example.com", pageTexts: ["a@one.com and me@example.com"]) == .matches,
          "claudeWeb check: the expected address among others is a match")
    check(C.verdict(expected: "me@example.com", pageTexts: []) == .unknown && C.verdict(expected: "me@example.com", pageTexts: ["no address"]) == .unknown,
          "claudeWeb check: nothing found shows nothing")
    check(C.verdict(expected: "me@example.com", pageTexts: ["icon@2x.png"]) == .unknown,
          "claudeWeb check: a file name such as icon@2x.png is not an address")
    check(C.verdict(expected: "", pageTexts: ["other@gmail.com"]) == .unknown,
          "claudeWeb check: with no expected address there is nothing to warn about")

    // The page script only reads. Every word that would click, send, post, open or read secrets is absent.
    let script = C.readScript
    let forbidden = ["fetch", "XMLHttpRequest", "click", "submit", "dispatchEvent", "sendBeacon", "postMessage", "WebSocket",
                     "location", "eval", "Function(", "open(", "document.cookie", "localStorage", "sessionStorage", "indexedDB",
                     "createElement", "appendChild", "innerHTML", "setAttribute", "addEventListener", "focus(", ".value =", ".value=", "textContent =", "import("]
    let present = forbidden.filter { script.contains($0) }
    check(present.isEmpty, "claudeWeb check: the page script contains none of the words that act on a page", present.joined(separator: ", "))
    check(script.contains("innerText") && script.contains("return out"),
          "claudeWeb check: the page script reads visible text and returns it")
}

// MARK: - Source pins

private func claudeWebSources() -> [(name: String, text: String)] {
    let dir = "PixelSwitch/ClaudeWeb"
    let names = ((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).filter { $0.hasSuffix(".swift") }.sorted()
    return names.map { ($0, (try? String(contentsOfFile: "\(dir)/\($0)", encoding: .utf8)) ?? "") }
}

@MainActor private func claudeWebSourcePins() {
    let sources = claudeWebSources()
    check(sources.count >= 6 && sources.allSatisfy { !$0.text.isEmpty }, "claudeWeb pins: the ClaudeWeb sources are found", "\(sources.map(\.name))")
    let all = sources.map(\.text).joined(separator: "\n")

    // PixelSwitch never executes a reset, never acts on the page, never imitates a browser.
    let banned = [
        "reset_rate_limits", "limit-reset", "limit_reset", "cedar_ember", "grant_id",
        "customUserAgent", "applicationNameForUserAgent",
        "WKUserScript", "addUserScript", "WKScriptMessageHandler", "addScriptMessageHandler",
        "URLSession", "httpMethod", "httpBody", "URLRequest(url: url, ", "dispatchEvent", "XMLHttpRequest", "fetch(", ".click(",
        "callAsyncJavaScript", "WKHTTPCookieStore", "httpCookieStore",
    ]
    let found = banned.filter { all.contains($0) }
    check(found.isEmpty, "claudeWeb pins: no reset address, no custom user agent, no injected user script, no request of its own, no click", found.joined(separator: ", "))

    // The one script call reads text with the fixed read-only script, in an isolated world.
    let calls = all.components(separatedBy: "evaluateJavaScript(").count - 1
    let readCall = all.components(separatedBy: "\n").first { $0.contains("evaluateJavaScript(") } ?? ""
    check(calls == 1 && readCall.contains("ClaudeWebAccountCheck.readScript") && readCall.contains("contentWorld: .defaultClient"),
          "claudeWeb pins: the only script run is the read-only page read, in an isolated content world", "\(calls) call(s): \(readCall.trimmingCharacters(in: .whitespaces))")

    // Page loads: only the usage page, a validated pasted link, and the pages the policy allowed.
    let loads = all.components(separatedBy: "\n").filter { $0.contains(".load(") }
    check(loads.count == 2 && loads.contains { $0.contains("ClaudeWebPolicy.usageURL") } && loads.contains { $0.contains("URLRequest(url: url)") },
          "claudeWeb pins: the window loads only the usage page and a link that passed the paste check", loads.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: " | "))
    let pasteFunction = all.range(of: "private func loadPastedLink")
        .map { String(all[$0.lowerBound...].prefix(700)) } ?? ""
    check(pasteFunction.contains("validatePastedLink") && pasteFunction.contains("case .valid(let url)"),
          "claudeWeb pins: a pasted link is loaded only after validatePastedLink accepts it")

    // The data store comes from the per-account identifier, never the shared default store.
    check(all.contains("WKWebsiteDataStore(forIdentifier:") && all.contains("WKWebsiteDataStore.remove(forIdentifier:")
          && !all.contains("WKWebsiteDataStore.default()") && !all.contains("nonPersistent()"),
          "claudeWeb pins: each account uses its own persistent identified data store and sign-out removes it")

    // The log: only the redacted form of an address, never a raw URL, a query, a fragment or an e-mail address.
    let logLines = all.components(separatedBy: "\n").filter { line in
        ["webLog.", "popupLog.", "registryLog.", "storeLog."].contains { line.contains($0) }
            && (line.contains(".info(") || line.contains(".warning(") || line.contains(".error(") || line.contains(".debug("))
    }
    let leaks = logLines.filter { line in
        ["absoluteString", ".query", ".fragment", "\\(url)", "\\(email", "\\(displayEmail", "\\(found", "\\(pasteField", "\\(accountID)"]
            .contains { line.contains($0) }
    }
    check(logLines.count >= 15 && leaks.isEmpty, "claudeWeb pins: log lines name only the redacted form of an address (\(logLines.count) lines)",
          leaks.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: " | "))
    let raw = logLines.filter { $0.contains("\\(url") && !$0.contains("loggable(url") }
    check(raw.isEmpty, "claudeWeb pins: every address interpolated into a log line goes through loggable()", raw.joined(separator: " | "))

    // Sign-out asks first.
    check(all.contains("beginSheetModal(for: window") && all.contains("Sign out of claude.ai here?") && all.contains("alertFirstButtonReturn"),
          "claudeWeb pins: Sign out of claude.ai here asks to confirm before it removes anything")
}

// MARK: - Repo-wide: no reset, no user-agent, one script

/// Every source file the shipped product is built from: the app, the command line tool, the shared
/// and widget code and the Linux port, with test files left out. Run from the repo root.
private func shippedSources() -> [(path: String, text: String)] {
    let roots = ["PixelSwitch", "PixelSwitchCLI", "Shared", "PixelSwitchWidget", "linux"]
    let extensions: Set<String> = ["swift", "py", "sh", "m", "mm", "h", "js", "cjs", "mjs", "ts"]
    let skippedFolders: Set<String> = ["tests", "Tests", "__pycache__", ".build", "node_modules", ".git"]
    let fileManager = FileManager.default
    var found: [(path: String, text: String)] = []
    for root in roots {
        guard let walker = fileManager.enumerator(atPath: root) else { continue }
        for case let relative as String in walker {
            let parts = relative.split(separator: "/").map(String.init)
            guard let name = parts.last, !parts.dropLast().contains(where: { skippedFolders.contains($0) }) else { continue }
            if name.hasSuffix("Tests.swift") || name.hasSuffix("Test.swift") || name.hasPrefix("test_")
                || name.hasSuffix("_test.py") || name == "conftest.py" { continue }
            let path = "\(root)/\(relative)"
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: path, isDirectory: &isDirectory), !isDirectory.boolValue,
                  let text = try? String(contentsOfFile: path, encoding: .utf8) else { continue }
            let ext = (name as NSString).pathExtension
            // A script with no extension (linux/bin/pixelswitch) counts when it starts with a shebang.
            if ext.isEmpty ? !text.hasPrefix("#!") : !extensions.contains(ext) { continue }
            found.append((path, text))
        }
    }
    return found.sorted { $0.path < $1.path }
}

/// The founder, 2026-11-19: "you absolutly do not execute a reset". The ClaudeWeb pins above read only
/// that folder; this one reads the whole shipped product, so a reset address, a spoofed browser
/// identity or a second script call cannot arrive from anywhere else (the app, the CLI, the widget,
/// the Linux port).
@MainActor private func claudeWebRepoWidePins() {
    let sources = shippedSources()
    func count(_ suffix: String) -> Int { sources.filter { $0.path.hasSuffix(suffix) }.count }
    let swiftApp = sources.filter { $0.path.hasPrefix("PixelSwitch/") && $0.path.hasSuffix(".swift") }.count
    let swiftCLI = sources.filter { $0.path.hasPrefix("PixelSwitchCLI/") && $0.path.hasSuffix(".swift") }.count
    let python = sources.filter { $0.path.hasPrefix("linux/") && $0.path.hasSuffix(".py") }.count
    // A wrong working folder or a renamed root must fail loudly here, never pass over an empty list.
    check(swiftApp >= 80 && swiftCLI >= 4 && count("Shared/WidgetData.swift") == 1 && count("PixelSwitchWidget/PixelSwitchWidget.swift") == 1
          && python >= 40 && count("linux/pixelswitch/api.py") == 1 && count("PixelSwitch/ClaudeWeb/ClaudeWebWindowController.swift") == 1,
          "no-reset pins: the shipped sources of the app, CLI, shared code, widget and Linux port are all found (app \(swiftApp), CLI \(swiftCLI), Shared, widget, Linux \(python))")
    check(!sources.contains { source in ["/tests/", "/Tests/", "Tests.swift", "/test_"].contains { source.path.contains($0) } },
          "no-reset pins: test files are left out of the scan")

    // 1. No reset address or reset grant anywhere. These names are what claude.ai's own page uses for its reset.
    let resetNames = ["reset_rate_limits", "cedar_ember", "juniper_tide", "grant_id", "limit-reset", "limit_reset"]
    let resetHits = sources.flatMap { source in
        resetNames.filter { source.text.range(of: $0, options: .caseInsensitive) != nil }.map { "\(source.path): \($0)" }
    }
    check(resetHits.isEmpty, "no-reset pins: no reset endpoint or grant name appears in any shipped source", resetHits.joined(separator: " | "))

    // 2. No user agent of our own for a web view, and no header that sets one, except the Linux client's
    //    honest name for its own HTTP calls (no browser, no web view).
    let webViewAgent = sources.flatMap { source in
        ["customUserAgent", "applicationNameForUserAgent"].filter { source.text.contains($0) }.map { "\(source.path): \($0)" }
    }
    check(webViewAgent.isEmpty, "no-reset pins: nothing sets a web view's user agent", webViewAgent.joined(separator: " | "))
    let allowedHeaderFile = "linux/pixelswitch/api.py"
    let headerHits = sources.filter { $0.path != allowedHeaderFile && $0.text.range(of: "user-agent", options: .caseInsensitive) != nil }.map(\.path)
    check(headerHits.isEmpty, "no-reset pins: no source writes a User-Agent header, outside the one Linux client file", headerHits.joined(separator: " | "))
    let linuxClient = sources.first { $0.path == allowedHeaderFile }?.text ?? ""
    let clientWrites = linuxClient.components(separatedBy: "\n").filter { $0.contains("\"User-Agent\"") }
    let linuxDaemon = sources.first { $0.path == "linux/pixelswitch/daemon.py" }?.text ?? ""
    check(clientWrites.count == 2 && clientWrites.allSatisfy { $0.contains("self.user_agent") }
          && linuxDaemon.contains("USER_AGENT_FORMAT = \"PixelSwitch/{version} (Linux)\""),
          "no-reset pins: the Linux client's only User-Agent writes send its own name, PixelSwitch/<version> (Linux)", "\(clientWrites.count) write(s)")
    let browserAgent = sources.filter { $0.text.contains("Mozilla/") }.map(\.path)
    check(browserAgent.isEmpty, "no-reset pins: no source carries a browser's user-agent text", browserAgent.joined(separator: " | "))

    // 3. Exactly one script call in the whole product: the read-only account check.
    let scriptCalls = sources.compactMap { source -> (String, Int)? in
        let n = source.text.components(separatedBy: "evaluateJavaScript(").count - 1
        return n > 0 ? (source.path, n) : nil
    }
    check(scriptCalls.count == 1 && scriptCalls[0].0 == "PixelSwitch/ClaudeWeb/ClaudeWebWindowController.swift" && scriptCalls[0].1 == 1,
          "no-reset pins: the whole product has exactly one evaluateJavaScript call, the read-only account check",
          scriptCalls.map { "\($0.0) x\($0.1)" }.joined(separator: " | "))
}

// MARK: - Strings

@MainActor private func claudeWebStringTests() {
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    // Every localized string the ClaudeWeb sources ask for is in all five tables.
    var keys = Set<String>()
    if let regex = try? NSRegularExpression(pattern: #"String\(localized: "((?:[^"\\]|\\.)*)""#) {
        for source in claudeWebSources() {
            let text = source.text
            for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                if let range = Range(match.range(at: 1), in: text) { keys.insert(String(text[range])) }
            }
        }
    }
    check(keys.count >= 14, "claudeWeb strings: the sources ask for their strings by key (\(keys.count) found)")
    let missing = keys.sorted().filter { key in !tables.allSatisfy { ($0[key] ?? "").isEmpty == false } }
    check(missing.isEmpty, "claudeWeb strings: every string the window uses is in all five tables", missing.joined(separator: " | "))
    let copied = keys.sorted().filter { key in key.count > 12 && tables[1...].contains { $0[key] == key } }
    check(copied.isEmpty, "claudeWeb strings: the long strings are translated in de, fr, ja and zh-Hans", copied.joined(separator: " | "))
    check(tables[0]["Sign in as %@"] == "Sign in as %@" && tables[0]["Paste sign-in link"] == "Paste sign-in link"
          && tables[0]["Open in browser"] == "Open in browser" && tables[0]["Sign out of claude.ai here"] == "Sign out of claude.ai here",
          "claudeWeb strings: the English lines are the ones the founder's brief names")
    let mismatch = "This window is signed in as %1$@, not %2$@. Sign out here and sign in as %2$@."
    check(tables.allSatisfy { table in
              let text = table[mismatch] ?? ""
              return text.contains("%1$@") && text.contains("%2$@")
          },
          "claudeWeb strings: the signed-in-as-someone-else bar names both addresses in every language")
    check(Set(tables.map { Set($0.keys) }).count == 1, "claudeWeb strings: the five tables still have exactly the same keys")
    // Words: edgebanding is the word elsewhere; here no em dash may appear in any new English line.
    let english = (try? String(contentsOfFile: "PixelSwitch/en.lproj/Localizable.strings", encoding: .utf8)) ?? ""
    let block = english.components(separatedBy: "/* Resets button").last ?? ""
    check(!block.isEmpty && !block.contains("\u{2014}"), "claudeWeb strings: no em dash in the new English lines")
}
