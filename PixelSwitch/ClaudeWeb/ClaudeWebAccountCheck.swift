import Foundation

/// Best-effort check that a claude.ai window is signed in as the account it was
/// opened for. Read-only: it only looks at text the page has already drawn.
///
/// The window runs `readScript` once the page has loaded. The script returns the
/// page's visible text and a few labels, nothing is sent anywhere, and nothing on
/// the page is touched. `verdict(expected:pageTexts:)` then looks for e-mail
/// addresses in that text. If it cannot tell, the window shows nothing.
///
/// Pure and Foundation only, so the unit harness compiles it.
enum ClaudeWebAccountCheck {
    /// The result of one read.
    enum Verdict: Equatable {
        /// No address, or several different ones: nothing to say.
        case unknown
        /// The expected address is on the page.
        case matches
        /// Exactly one other address is on the page, and it is not the expected one.
        case mismatch(found: String)
    }

    /// Runs inside the page in its own isolated script world and only reads. It
    /// returns an array of strings that contain an "@": the title, meta values,
    /// labels and the visible text. It does not call the network, click, type,
    /// submit, post a message, open a window or read cookies or storage. A unit
    /// test scans this text for every one of those words.
    static let readScript = """
    (function () {
      var out = [];
      function add(value) {
        if (typeof value === 'string' && value.indexOf('@') !== -1) { out.push(value.slice(0, 2000)); }
      }
      try { add(document.title); } catch (e) {}
      try {
        var metas = document.getElementsByTagName('meta');
        for (var i = 0; i < metas.length && i < 200; i++) { add(metas[i].getAttribute('content')); }
      } catch (e) {}
      try {
        var labelled = document.querySelectorAll('[aria-label*="@"], [title*="@"], [alt*="@"], [data-email]');
        for (var j = 0; j < labelled.length && j < 200; j++) {
          var node = labelled[j];
          add(node.getAttribute('aria-label'));
          add(node.getAttribute('title'));
          add(node.getAttribute('alt'));
          add(node.getAttribute('data-email'));
        }
      } catch (e) {}
      try { if (document.body) { add(document.body.innerText.slice(0, 200000)); } } catch (e) {}
      return out;
    })()
    """

    /// Domains whose addresses are the product's own (a support or privacy
    /// contact on the page), never the signed-in person.
    static let ignoredDomains = ["anthropic.com", "claude.ai", "claude.com"]

    /// Compares what the page shows with the address the window was opened for.
    /// Case does not matter. `pageTexts` is whatever `readScript` returned.
    static func verdict(expected: String, pageTexts: [String]) -> Verdict {
        let wanted = expected.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var found = Set<String>()
        for text in pageTexts { found.formUnion(emails(in: text)) }
        if !wanted.isEmpty, found.contains(wanted) { return .matches }
        let others = found.filter { !isIgnored($0) }
        if others.count == 1, let only = others.first, !wanted.isEmpty { return .mismatch(found: only) }
        return .unknown
    }

    /// Every e-mail address in `text`, lowercased, each once.
    static func emails(in text: String) -> [String] {
        guard !text.isEmpty, let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        var seen = Set<String>()
        var result: [String] = []
        for match in regex.matches(in: text, range: range) {
            guard let r = Range(match.range, in: text) else { continue }
            let address = String(text[r]).lowercased()
            if seen.insert(address).inserted { result.append(address) }
        }
        return result
    }

    /// Letters, digits and the usual punctuation before the @; a dotted domain
    /// that ends in letters after it. A trailing full stop of a sentence is not
    /// part of the address because the pattern must end on letters.
    private static let pattern = #"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"#

    /// File names that look like an address ("icon@2x.png"), never a person.
    private static let assetExtensions = ["png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "css", "js", "json"]

    private static func isIgnored(_ address: String) -> Bool {
        guard let at = address.lastIndex(of: "@") else { return false }
        let domain = String(address[address.index(after: at)...])
        if let last = domain.split(separator: ".").last, assetExtensions.contains(String(last)) { return true }
        return ignoredDomains.contains { domain == $0 || domain.hasSuffix("." + $0) }
    }
}
