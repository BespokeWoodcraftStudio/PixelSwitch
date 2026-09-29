// What a failed usage request says on the account's card, and whether the last
// reading survives it. Compiled into the unit harness by Tests/run-unit-tests.sh
// and run from main.swift. Uses the global `check(_:_:_:)` defined there.
//
// Founder, 2026-09-29: a 503 from Anthropic showed "Could not fetch usage: The
// operation couldn't be completed. (PixelSwitch.ClaudeService.UsageError
// error 0.)". "If that happens again, the person understands what they need to
// do just by a simple double-click or something like that, or by clicking the
// refresh."
import Foundation

@MainActor func runUsageErrorTests() {
    typealias E = UsageRequestError

    // Status codes to errors.
    check(E.from(status: 401, body: "", retryAfter: nil) == .expired
          && E.from(status: 400, body: #"{"error":{"type":"token_expired"}}"#, retryAfter: nil) == .expired,
          "usage errors: 401, or a body naming token_expired, is an expired token")
    check(E.from(status: 429, body: "", retryAfter: "3474") == .rateLimited(retryAfter: 3474)
          && E.from(status: 429, body: "", retryAfter: nil) == .rateLimited(retryAfter: nil)
          && E.from(status: 429, body: "", retryAfter: "soon") == .rateLimited(retryAfter: nil),
          "usage errors: 429 carries Retry-After when it is a number")
    check(E.from(status: 403, body: "nope", retryAfter: nil) == .forbidden("nope"), "usage errors: 403 is no subscription, as before")
    check([500, 502, 503, 504, 529].allSatisfy { E.from(status: $0, body: "", retryAfter: nil) == .server(status: $0) },
          "usage errors: every 5xx is the server's trouble")
    check(E.from(status: 404, body: "", retryAfter: nil) == .unexpected(status: 404)
          && E.from(status: 418, body: "", retryAfter: nil) == .unexpected(status: 418)
          && E.from(status: 0, body: "", retryAfter: nil) == .unexpected(status: 0),
          "usage errors: any other status is an unexpected reply")

    // A failure to reach the server at all.
    check(E.from(transportError: URLError(.notConnectedToInternet)) == .network(offline: true)
          && E.from(transportError: URLError(.networkConnectionLost)) == .network(offline: true)
          && E.from(transportError: URLError(.timedOut)) == .network(offline: false)
          && E.from(transportError: URLError(.cannotFindHost)) == .network(offline: false),
          "usage errors: no connection and a timeout are network errors, offline told apart")

    // Only the server's trouble and the network keep the last reading.
    check(E.server(status: 503).keepsLastReading && E.network(offline: true).keepsLastReading && E.network(offline: false).keepsLastReading,
          "usage errors: a 5xx or a network failure keeps the last reading, as a 429 does")
    check(!E.expired.keepsLastReading && !E.forbidden("").keepsLastReading
          && !E.unexpected(status: 404).keepsLastReading && !E.decode("x").keepsLastReading,
          "usage errors: an expired token, no subscription or an unreadable reply still clears it")
    check(E.server(status: 503).offersStatusPage && !E.network(offline: true).offersStatusPage && !E.unexpected(status: 404).offersStatusPage,
          "usage errors: only the server's trouble links to the status page")
    check(E.server(status: 503).canRetry && E.network(offline: false).canRetry && E.unexpected(status: 404).canRetry && E.decode("x").canRetry
          && !E.expired.canRetry && !E.forbidden("").canRetry && !E.rateLimited(retryAfter: 60).canRetry,
          "usage errors: Retry is offered unless signing in again or waiting out a rate limit is the only fix")

    // Plain words, never Swift's generic text.
    let all: [E] = [.expired, .network(offline: true), .network(offline: false), .server(status: 503), .unexpected(status: 404),
                    .decode("bad"), .rateLimited(retryAfter: 60), .forbidden("")]
    let generic = all.filter { e in
        let text = (e as Error).localizedDescription
        return text.contains("couldn’t be completed") || text.contains("couldn't be completed") || text.contains("UsageError") || text.isEmpty
    }
    check(generic.isEmpty, "usage errors: every error reads as a plain sentence, never \"UsageError error 0\"", "\(generic)")
    check(E.server(status: 503).localizedDescription.contains("503") && E.server(status: 503).localizedDescription.contains("Your account is fine"),
          "usage errors: a 5xx says the status and that the account is fine", E.server(status: 503).localizedDescription)
    check(E.network(offline: true).localizedDescription.contains("internet connection"),
          "usage errors: offline says to check the internet connection", E.network(offline: true).localizedDescription)
    check(E.unexpected(status: 404).localizedDescription.contains("404"), "usage errors: an unexpected reply names its status")

    // Every new sentence is in all five languages.
    let keys = [
        "Anthropic's usage service isn't responding (HTTP %lld). Your account is fine; PixelSwitch keeps trying.",
        "Can't reach Anthropic. Check your internet connection; PixelSwitch keeps trying.",
        "Can't reach Anthropic right now; PixelSwitch keeps trying.",
        "Unexpected reply from Anthropic (HTTP %lld).",
        "Anthropic sent usage data PixelSwitch couldn't read.",
        "Session expired.",
        "Retry",
        "Status",
        "Try again now",
        "Open status.claude.com",
    ]
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    let missing = keys.filter { key in !tables.allSatisfy { $0[key] != nil } }
    check(missing.isEmpty, "l10n: the usage error sentences exist in all five languages", missing.joined(separator: " | "))
}
