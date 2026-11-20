import Foundation

/// What the per-account claude.ai window may load, and which links it will take.
///
/// The Resets button opens an account's own claude.ai usage page. The founder
/// clicks claude.ai's own button on that page. PixelSwitch only shows the page:
/// it never clicks, submits or scripts anything on it. These rules keep the
/// window pointed at Claude (and the sign-in providers Claude uses) and send
/// every other link to the default browser.
///
/// Pure and Foundation only, so the unit harness compiles it.
enum ClaudeWebPolicy {
    /// The page the Resets button opens.
    static let usageURL = URL(string: "https://claude.ai/settings/usage")!

    /// Domains that open inside the window. Each matches itself and every
    /// subdomain (`claude.ai`, `www.claude.ai`), never a look-alike such as
    /// `claude.ai.evil.com` or `evilclaude.ai`.
    static let firstPartyDomains = ["claude.ai", "anthropic.com"]

    /// Sign-in providers claude.ai's login page offers, exact hosts only.
    /// accounts.google.com serves "Continue with Google" (its page and its
    /// pop-up). appleid.apple.com serves Sign in with Apple, and idmsa.apple.com
    /// is the Apple ID form that page loads. A company single sign-on page is
    /// not listed: it belongs to the company's own domain and opens in the
    /// default browser.
    static let signInProviderHosts = ["accounts.google.com", "appleid.apple.com", "idmsa.apple.com"]

    enum Decision: Equatable {
        /// Load it in the window.
        case allow
        /// Hand the link to the default browser and do not load it here.
        case openInBrowser
        /// Do not load it anywhere.
        case block
    }

    // MARK: Navigation

    /// What to do with a navigation the web view is about to make.
    ///
    /// `isMainFrame` is true for the page itself and for a pop-up's own page,
    /// false for a frame embedded in a page (a sign-in provider's form, a
    /// Cloudflare check). An embedded frame cannot take over the window, so it
    /// may load any https address; only the page itself is held to the lists.
    static func decide(url: URL?, isMainFrame: Bool) -> Decision {
        guard let url, let scheme = url.scheme?.lowercased() else { return .block }
        switch scheme {
        case "about":
            // A pop-up starts on about:blank and is then sent to its real address.
            let text = url.absoluteString.lowercased()
            return text == "about:blank" || text == "about:srcdoc" ? .allow : .block
        case "https":
            if url.user != nil || url.password != nil { return .block }
            if isMainFrame {
                return isAllowedHost(url.host) && hasDefaultPort(url) ? .allow : .openInBrowser
            }
            return hasHost(url) ? .allow : .block
        case "http":
            // Plain http is never loaded here. The browser upgrades it if it can.
            return isMainFrame && hasHost(url) ? .openInBrowser : .block
        default:
            // mailto:, file:, data:, blob:, javascript:, another app's scheme.
            return .block
        }
    }

    /// True for claude.ai, anthropic.com and their subdomains, and for the
    /// sign-in providers' exact hosts.
    static func isAllowedHost(_ host: String?) -> Bool {
        guard let host = normalizedHost(host) else { return false }
        if signInProviderHosts.contains(host) { return true }
        return firstPartyDomains.contains { host == $0 || host.hasSuffix("." + $0) }
    }

    /// Lowercased, one trailing dot dropped, and nil for anything that is not a
    /// plain ASCII host name. A host with a space, a percent sign or a letter
    /// from another alphabet (a look-alike such as a Cyrillic "а") is refused.
    static func normalizedHost(_ host: String?) -> String? {
        guard var host = host?.lowercased(), !host.isEmpty else { return nil }
        if host.hasSuffix(".") { host.removeLast() }
        guard !host.isEmpty, !host.hasPrefix("."), !host.contains("..") else { return nil }
        let allowed = Set("abcdefghijklmnopqrstuvwxyz0123456789-.")
        guard host.allSatisfy({ allowed.contains($0) }) else { return nil }
        return host
    }

    private static func hasHost(_ url: URL) -> Bool { normalizedHost(url.host) != nil }

    private static func hasDefaultPort(_ url: URL) -> Bool { url.port == nil || url.port == 443 }

    // MARK: Pasted sign-in link

    enum PastedLink: Equatable {
        case valid(URL)
        case invalid(Problem)
    }

    enum Problem: Equatable {
        /// Nothing was pasted.
        case empty
        /// It is not one whole web address (it has a space or a line break inside, or does not parse).
        case notALink
        /// It is a web address, but not https.
        case notHTTPS
        /// It is https, but not on claude.ai or one of its subdomains.
        case notClaudeAI
    }

    /// Checks the link a person pasted for the emailed sign-in. Only an https link
    /// on claude.ai or a claude.ai subdomain gets through, so the field cannot
    /// be used to load any other site into an account's window. Text around the
    /// link (a trailing line break from the copy) is trimmed.
    static func validatePastedLink(_ text: String) -> PastedLink {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .invalid(.empty) }
        // A link broken across lines, or one with a stray space, is not the whole link.
        let hasBreak = trimmed.unicodeScalars.contains {
            CharacterSet.whitespacesAndNewlines.contains($0) || CharacterSet.controlCharacters.contains($0)
        }
        guard !hasBreak, !trimmed.contains("\\"), let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased() else { return .invalid(.notALink) }
        guard scheme == "https" else {
            return .invalid(scheme == "http" ? .notHTTPS : .notALink)
        }
        guard url.user == nil, url.password == nil, hasDefaultPort(url),
              let host = normalizedHost(url.host),
              host == "claude.ai" || host.hasSuffix(".claude.ai") else { return .invalid(.notClaudeAI) }
        return .valid(url)
    }

    // MARK: Which page this is

    /// True when the page is a sign-in page: claude.ai's own login, magic-link
    /// and sign-up pages, and the Google and Apple sign-in pages. The window
    /// then shows the "Sign in as ..." banner so the right account is signed in.
    static func isSignInPage(_ url: URL?) -> Bool {
        guard let url, url.scheme?.lowercased() == "https", let host = normalizedHost(url.host) else { return false }
        if signInProviderHosts.contains(host) { return true }
        guard host == "claude.ai" || host.hasSuffix(".claude.ai") else { return false }
        let first = firstPathSegment(url)?.lowercased()
        return ["login", "logout", "magic-link", "signup", "sign-up", "sso"].contains(first ?? "")
    }

    // MARK: Logging

    /// A form of `url` that is safe for the log: host and the first path word only.
    /// Never a query, a fragment (the emailed sign-in link carries its token in
    /// one), a user name or any later path piece.
    static func loggable(_ url: URL?) -> String {
        guard let url, let scheme = url.scheme?.lowercased() else { return "(none)" }
        guard scheme == "http" || scheme == "https" else { return "\(scheme):" }
        var text = normalizedHost(url.host) ?? "?"
        if let first = firstPathSegment(url) {
            let word = first.lowercased()
            let plain = word.count <= 24 && word.first.map { $0.isLetter && $0.isASCII } == true
                && word.allSatisfy { ($0.isLetter && $0.isASCII) || $0 == "-" || $0 == "_" }
            text += "/" + (plain ? word : "…")
        }
        return text
    }

    private static func firstPathSegment(_ url: URL) -> String? {
        url.pathComponents.first { $0 != "/" && !$0.isEmpty }
    }
}
