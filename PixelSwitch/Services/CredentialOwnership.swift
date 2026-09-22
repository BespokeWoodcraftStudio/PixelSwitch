import Foundation

/// Works out whose Claude login a credential holds, from the credential itself.
///
/// `~/.claude.json`'s `oauthAccount` (and so the email `claude auth status`
/// prints) is NOT evidence of whose login is live: running Claude Code
/// sessions rewrite that file from memory, so after a switch it can name one
/// account while the Keychain holds another's token. On 2026-09-21 that made
/// PixelSwitch back up account A's login under account B, after which switching
/// to B silently used A's token. Ownership therefore comes from the token:
/// first by lineage (the same OAuth grant as a saved login), then by asking the
/// API whose token it is (`ClaudeService.accountEmail(forAccessToken:)`).
enum CredentialOwnership {
    /// The account-identifying part of a Claude Code credential.
    struct Login: Equatable {
        let accessToken: String
        let refreshToken: String?
    }

    /// The `claudeAiOauth` login inside a credential JSON, or nil.
    static func login(fromCredential json: String) -> Login? {
        guard let data = json.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let oauth = object["claudeAiOauth"] as? [String: Any],
              let access = oauth["accessToken"] as? String, !access.isEmpty else { return nil }
        let refresh = (oauth["refreshToken"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return Login(accessToken: access, refreshToken: refresh)
    }

    /// True when two logins come from the same OAuth grant: the same refresh
    /// token, or the same access token. A refreshed login shares neither with
    /// its older self once the refresh token rotates, so a miss is "unknown",
    /// never "different".
    static func sameGrant(_ a: Login, _ b: Login) -> Bool {
        if a.accessToken == b.accessToken { return true }
        if let ra = a.refreshToken, let rb = b.refreshToken, ra == rb { return true }
        return false
    }

    /// Ids of the saved logins that are the same grant as `login`, sorted.
    static func lineageMatches(_ login: Login, in saved: [String: Login]) -> [String] {
        saved.filter { sameGrant($0.value, login) }.map(\.key).sorted()
    }

    /// Groups of account ids whose saved logins are the same grant. Every
    /// group holds at most one account's real login; the others are wrong.
    static func sharedLogins(_ saved: [String: Login]) -> [[String]] {
        var groups: [[String]] = []
        var assigned = Set<String>()
        for id in saved.keys.sorted() where !assigned.contains(id) {
            guard let login = saved[id] else { continue }
            let group = lineageMatches(login, in: saved)
            if group.count > 1 {
                groups.append(group)
                assigned.formUnion(group)
            }
        }
        return groups
    }
}

extension Optional {
    /// `map` for an async transform.
    func asyncMap<T>(_ transform: (Wrapped) async -> T) async -> T? {
        switch self {
        case .some(let value): return await transform(value)
        case .none: return nil
        }
    }
}
