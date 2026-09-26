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
