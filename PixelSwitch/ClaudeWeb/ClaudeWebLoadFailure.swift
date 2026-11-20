import Foundation

/// What the claude.ai window does when a page does not load.
///
/// Without this a failed load left the window blank and silent. The window now
/// shows a banner with a Reload button, except for loads that were stopped on
/// purpose, and when WebKit's page process ends it reloads once before it gives up
/// and shows the same banner.
///
/// Pure and Foundation only, so the unit harness compiles it. Nothing here reads or
/// keeps an address: a failure is told apart by its error domain and code alone,
/// and that pair is all the log ever gets to see.
enum ClaudeWebLoadFailure {
    /// WebKit's own error domain (the constant is not exported to Swift).
    static let webKitDomain = "WebKitErrorDomain"
    /// "Frame load interrupted": a navigation or a response that a policy decision
    /// stopped, or one that turned into a download.
    static let frameLoadInterrupted = 102

    /// True when a failed load deserves the banner. False for a load that was
    /// stopped on purpose, which is not a problem to report:
    /// - `NSURLErrorCancelled`: a new load replaced it, Stop, a reload, or the
    ///   window's own policy refused the address (a link sent to the browser);
    /// - WebKit's "frame load interrupted", which is the same thing from a policy
    ///   decision on a response.
    /// Everything else (offline, timed out, cannot find or reach the host, a bad
    /// certificate) leaves the page blank or broken and does get the banner.
    static func shouldShowBanner(domain: String, code: Int) -> Bool {
        if domain == NSURLErrorDomain && code == NSURLErrorCancelled { return false }
        if domain == webKitDomain && code == frameLoadInterrupted { return false }
        return true
    }

    enum ProcessEndedReaction: Equatable {
        /// Reload the page now.
        case reload
        /// Do not reload again: show the banner and let the person press Reload.
        case showBanner
    }

    /// WebKit ended the page's own process (it crashed, or the system took it back
    /// for memory) and the page is blank. Reload once. If it ends again before a
    /// page has loaded in between, reloading is not helping, so stop and say so
    /// instead of looping.
    static func reaction(toProcessEnding alreadyReloaded: Bool) -> ProcessEndedReaction {
        alreadyReloaded ? .showBanner : .reload
    }
}
