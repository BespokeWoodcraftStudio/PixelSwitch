import Foundation

/// Works out whose Claude login a credential holds, from the credential itself.
///
/// `~/.claude.json`'s `oauthAccount` (and so the email `claude auth status`
/// prints) is NOT evidence of whose login is live: running Claude Code
/// sessions rewrite that file from memory, so after a switch it can name one
/// account while the Keychain holds another's token. On 2026-09-21 that made
/// PixelSwitch back up account A's login under account B, after which switching
/// to B silently used A's token. Ownership is therefore proven by asking the
/// API whose token it is (`ClaudeService.accountIdentity(forAccessToken:)`).
/// Lineage (the same OAuth grant as a saved login) is only a warning sign, used
/// to refuse, because one wrongly saved login makes it point the wrong way.
enum CredentialOwnership {
    /// The account-identifying part of a Claude Code credential.
    struct Login: Equatable {
        let accessToken: String
        let refreshToken: String?
        /// Access-token expiry in milliseconds since 1970, as Claude Code stores it.
        var expiresAt: Double? = nil

        /// True when the access token has expired or will within a minute.
        var isExpired: Bool {
            guard let expiresAt else { return false }
            return expiresAt / 1000 < Date().timeIntervalSince1970 + 60
        }
    }

    /// The `claudeAiOauth` login inside a credential JSON, or nil.
    static func login(fromCredential json: String) -> Login? {
        guard let data = json.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let oauth = object["claudeAiOauth"] as? [String: Any],
              let access = oauth["accessToken"] as? String, !access.isEmpty else { return nil }
        let refresh = (oauth["refreshToken"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return Login(accessToken: access, refreshToken: refresh, expiresAt: (oauth["expiresAt"] as? NSNumber)?.doubleValue)
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


extension CredentialOwnership {
    /// Whether saved account details (an `oauthAccount` block's `accountUuid`
    /// and `emailAddress`) describe the proven owner of a token. The permanent
    /// id decides when both sides have one; otherwise the email does. Missing
    /// information on both sides is never a match.
    ///
    /// Verified 2026-09-22 on the founder's Mac: `/api/oauth/profile`'s
    /// `account.uuid` equals `oauthAccount.accountUuid` in ~/.claude.json for
    /// the same account (1e4226c6-…), so the two ids are comparable.
    static func detailsBelong(uuid: String?, email: String?, toOwnerUuid ownerUuid: String?, ownerEmail: String?) -> Bool {
        if let uuid, let ownerUuid { return uuid == ownerUuid }
        if let email, let ownerEmail { return email.caseInsensitiveCompare(ownerEmail) == .orderedSame }
        return false
    }
}

// MARK: - Usable logins (2.0, spec D12)

extension CredentialOwnership {
    /// Why a credential is not a usable login, or nil when it is one.
    ///
    /// Claude Code blanks a login it has found dead (`accessToken` and
    /// `refreshToken` empty, `expiresAt` 0) and leaves the file in place. Saving
    /// that over a good backup, or writing a backup like it into Claude Code's
    /// live store, would sign Claude Code out. A credential is usable only if it
    /// parses as a JSON object whose `claudeAiOauth` is an object with an
    /// `accessToken` and a `refreshToken` that are non-empty strings, and an
    /// `expiresAt`, when present, that is a number above 0. Missing, null or
    /// wrongly typed values count as blank. The reason names the field, never a value.
    static func unusableReason(credential json: String?) -> String? {
        guard let json, let data = json.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return "the credential is not a JSON object"
        }
        guard let oauth = object["claudeAiOauth"] as? [String: Any] else { return "it has no claudeAiOauth object" }
        func nonEmptyString(_ key: String) -> Bool {
            guard let value = oauth[key] as? String else { return false }
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        guard nonEmptyString("accessToken") else { return "its accessToken is blank" }
        guard nonEmptyString("refreshToken") else { return "its refreshToken is blank" }
        if let expires = oauth["expiresAt"] {
            guard let number = expires as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue > 0 else { return "its expiresAt is not a positive number" }
        }
        return nil
    }

    /// True when `json` is a usable login (`unusableReason` is nil).
    static func isUsableLogin(credential json: String?) -> Bool {
        unusableReason(credential: json) == nil
    }
}

/// The three places the usable-login rule (D12) is enforced, as pure decisions
/// the unit harness can test; the Keychain and `AppState` call them.
enum LoginGate {
    enum SaveDecision: Equatable {
        case allowed
        /// Refused; the reason names a field, never a value.
        case refused(reason: String)
    }

    /// (a) Whether a credential may be saved as an account's login. Asked by
    /// `KeychainService.saveAccountBackup`, the one save function every path uses.
    static func save(token: String) -> SaveDecision {
        CredentialOwnership.unusableReason(credential: token).map { .refused(reason: $0) } ?? .allowed
    }

    /// (b) The reason a saved login must not be written to Claude Code's live
    /// store, or nil when it may be. Asked by target validation, which then
    /// fails with the re-sign message.
    static func targetProblem(backupToken: String) -> String? {
        CredentialOwnership.unusableReason(credential: backupToken)
    }

    /// (c) Whether an account can be switched to: it has a saved login that is
    /// usable, the server has not refused its refresh grant, and its card does
    /// not say the login expired.
    static func isSwitchable(backupToken: String?, needsReauth: Bool, cardSaysLoginExpired: Bool) -> Bool {
        guard let backupToken, CredentialOwnership.isUsableLogin(credential: backupToken) else { return false }
        return !needsReauth && !cardSaysLoginExpired
    }
}

// MARK: - Re-read before write (2.0, spec 5.3)

extension CredentialOwnership {
    /// The two tokens of a credential's `claudeAiOauth`.
    private struct TokenPair: Equatable {
        let access: String?
        let refresh: String?
    }

    private static func tokens(in credential: String?) -> TokenPair? {
        guard let credential, let data = credential.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let oauth = object["claudeAiOauth"] as? [String: Any] else { return nil }
        return TokenPair(access: oauth["accessToken"] as? String, refresh: oauth["refreshToken"] as? String)
    }

    /// Whether the live credential changed between the moment the switch saved
    /// the leaving login (step 4) and the moment it is about to write: the
    /// `claudeAiOauth` access or refresh token differs. Claude Code may have
    /// renewed the login in between, and the copy saved under the owner would
    /// then hold a refresh token the server has already rotated. A credential
    /// that does not parse is compared as text.
    static func liveLoginChanged(savedAtStep4 saved: String?, now: String?) -> Bool {
        if saved == now { return false }
        guard let a = tokens(in: saved), let b = tokens(in: now) else {
            // One side unreadable: a difference unless both are unreadable and equal (handled above).
            return true
        }
        return a != b
    }
}

// MARK: - Renewal keeps refreshTokenExpiresAt honest (2.0, spec 5.3)

extension CredentialOwnership {
    /// `oauth` (a credential's `claudeAiOauth`) after a successful renewal.
    /// A positive `refresh_token_expires_in` in the response sets
    /// `refreshTokenExpiresAt` to now plus that, in milliseconds. A response
    /// without one removes a stored `refreshTokenExpiresAt` that is already in
    /// the past (Claude Code treats a past value as signed out, so a renewed
    /// login must not carry it). Nothing else changes; `scopes` is untouched.
    static func applyingRenewalExpiry(to oauth: [String: Any], refreshTokenExpiresIn: Double?, now: Date = Date()) -> [String: Any] {
        var result = oauth
        let nowMs = now.timeIntervalSince1970 * 1000
        if let seconds = refreshTokenExpiresIn, seconds.isFinite, seconds > 0 {
            result["refreshTokenExpiresAt"] = Int64(nowMs) + Int64(seconds * 1000)
        } else if let stored = (oauth["refreshTokenExpiresAt"] as? NSNumber)?.doubleValue, stored <= nowMs {
            result.removeValue(forKey: "refreshTokenExpiresAt")
        }
        return result
    }
}
