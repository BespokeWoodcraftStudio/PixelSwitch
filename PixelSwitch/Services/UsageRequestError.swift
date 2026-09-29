import Foundation

/// Why a usage request (`/api/oauth/usage`) failed, in words the account's
/// card can show as they are, and what the app does about it.
///
/// Before 1.7 any status other than 401, 403 or 429 became a `network` case
/// carrying "HTTP 503", and with no `LocalizedError` conformance Swift showed
/// "The operation couldn't be completed. (PixelSwitch.ClaudeService.UsageError
/// error 0.)" (founder, 2026-09-29). `ClaudeService.UsageError` is this type.
enum UsageRequestError: Error, Equatable {
    /// 401, or a body naming `token_expired`: the access token needs refreshing.
    case expired
    /// The request never got a reply: no connection (`offline`), a timeout, a
    /// host that could not be found.
    case network(offline: Bool)
    /// 5xx: Anthropic's service is in trouble. Nothing is wrong with the account.
    case server(status: Int)
    /// Any other status the app has no rule for.
    case unexpected(status: Int)
    /// A 200 whose body could not be read as usage.
    case decode(String)
    case rateLimited(retryAfter: TimeInterval?)
    /// 403 permission_error, e.g. "OAuth authentication is currently not
    /// allowed for this organization" (no active Pro/Max subscription).
    case forbidden(String)

    /// The error for a reply other than 200.
    static func from(status: Int, body: String, retryAfter: String?) -> UsageRequestError {
        if status == 401 || body.contains("token_expired") { return .expired }
        switch status {
        case 429: return .rateLimited(retryAfter: retryAfter.flatMap(TimeInterval.init))
        case 403: return .forbidden(body)
        case 500...599: return .server(status: status)
        default: return .unexpected(status: status)
        }
    }

    /// The error for a request that got no reply at all.
    static func from(transportError error: Error) -> UsageRequestError {
        let offlineCodes: Set<URLError.Code> = [.notConnectedToInternet, .networkConnectionLost, .dataNotAllowed, .internationalRoamingOff]
        let code = (error as? URLError)?.code
        return .network(offline: code.map(offlineCodes.contains) ?? false)
    }

    /// Whether the account's last reading stays on its card, as it does for a
    /// 429: the failure says nothing about the account, and a reading with its
    /// "Updated Xm ago" label beats an empty card.
    var keepsLastReading: Bool {
        switch self {
        case .server, .network: return true
        default: return false
        }
    }

    /// Whether the card offers Retry. Not when only signing in again helps
    /// (expired, no subscription), nor for a rate limit, whose deadline the
    /// app already waits out.
    var canRetry: Bool {
        switch self {
        case .server, .network, .unexpected, .decode: return true
        case .expired, .forbidden, .rateLimited: return false
        }
    }

    /// Whether the card links to Anthropic's status page.
    var offersStatusPage: Bool {
        if case .server = self { return true }
        return false
    }

    /// The sentence the account's card shows.
    var message: String {
        switch self {
        case .expired:
            return String(localized: "Session expired.", bundle: L10n.bundle)
        case .network(offline: true):
            return String(localized: "Can't reach Anthropic. Check your internet connection; PixelSwitch keeps trying.", bundle: L10n.bundle)
        case .network(offline: false):
            return String(localized: "Can't reach Anthropic right now; PixelSwitch keeps trying.", bundle: L10n.bundle)
        case .server(let status):
            return String(localized: "Anthropic's usage service isn't responding (HTTP \(status)). Your account is fine; PixelSwitch keeps trying.", bundle: L10n.bundle)
        case .unexpected(let status):
            return String(localized: "Unexpected reply from Anthropic (HTTP \(status)).", bundle: L10n.bundle)
        case .decode:
            return String(localized: "Anthropic sent usage data PixelSwitch couldn't read.", bundle: L10n.bundle)
        case .rateLimited:
            return String(localized: "API Rate Limited. Try again later.", bundle: L10n.bundle)
        case .forbidden:
            return String(localized: "No active subscription on this account (OAuth not allowed).", bundle: L10n.bundle)
        }
    }

    /// The link a server outage offers.
    static let statusPage = URL(string: "https://status.claude.com")!
}

extension UsageRequestError: LocalizedError {
    var errorDescription: String? { message }
}
