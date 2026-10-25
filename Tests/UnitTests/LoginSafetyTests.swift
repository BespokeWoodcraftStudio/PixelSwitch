// 2.0 lane MA: login safety. The usable-login rule (spec D12) against save,
// target validation and "switchable"; the re-read before a switch writes
// (spec 5.3); renewal keeping refreshTokenExpiresAt honest (spec 5.3).
// Uses the global `check`.
import Foundation

@MainActor func runLoginSafetyTests() {
    usableLoginTests()
    gateTests()
    rereadTests()
    renewalExpiryTests()
}

/// A credential JSON with `claudeAiOauth` taken from `oauth` (nil = leave the key out).
private func credential(_ oauth: [String: Any]?, extra: [String: Any] = [:]) -> String {
    var root = extra
    if let oauth { root["claudeAiOauth"] = oauth }
    return String(decoding: try! JSONSerialization.data(withJSONObject: root, options: [.sortedKeys]), as: UTF8.self)
}

private func good(_ overrides: [String: Any] = [:], removing: [String] = []) -> [String: Any] {
    var oauth: [String: Any] = ["accessToken": "access-1", "refreshToken": "refresh-1", "expiresAt": 1_790_000_000_000.0,
                                "scopes": ["user:inference"], "subscriptionType": "max"]
    for (key, value) in overrides { oauth[key] = value }
    for key in removing { oauth.removeValue(forKey: key) }
    return oauth
}

/// The D12 list, as credentials Claude Code or a bad write could leave behind.
private func unusableLogins() -> [(name: String, json: String)] {
    [("a blank accessToken", credential(good(["accessToken": ""]))),
     ("a whitespace accessToken", credential(good(["accessToken": "   "]))),
     ("a blank refreshToken", credential(good(["refreshToken": ""]))),
     ("a missing refreshToken", credential(good(removing: ["refreshToken"]))),
     ("a missing accessToken", credential(good(removing: ["accessToken"]))),
     ("a missing claudeAiOauth", credential(nil, extra: ["mcpOAuth": ["server": "x"]])),
     ("a null claudeAiOauth", #"{"claudeAiOauth":null}"#),
     ("null tokens", credential(good(["accessToken": NSNull(), "refreshToken": NSNull()]))),
     ("a null refreshToken", credential(good(["refreshToken": NSNull()]))),
     ("wrongly typed tokens", credential(good(["accessToken": 12, "refreshToken": true]))),
     ("expiresAt 0", credential(good(["expiresAt": 0]))),
     ("a negative expiresAt", credential(good(["expiresAt": -5]))),
     ("a null expiresAt", credential(good(["expiresAt": NSNull()]))),
     ("a string expiresAt", credential(good(["expiresAt": "soon"]))),
     ("the blanked login Claude Code writes", credential(good(["accessToken": "", "refreshToken": "", "expiresAt": 0]))),
     ("a claudeAiOauth that is not an object", #"{"claudeAiOauth":"nope"}"#),
     ("a credential that is not JSON", "not json"),
     ("a JSON array", "[]"),
     ("an empty string", "")]
}

@MainActor private func usableLoginTests() {
    check(CredentialOwnership.isUsableLogin(credential: credential(good())), "login: a normal login is usable")
    check(CredentialOwnership.isUsableLogin(credential: credential(good(removing: ["expiresAt"]))),
          "login: an expiresAt that is absent is allowed (it is checked only when present)")
    check(CredentialOwnership.isUsableLogin(credential: credential(good(), extra: ["mcpOAuth": ["server": ["accessToken": ""]]])),
          "login: blank tokens elsewhere in the credential (MCP logins) do not matter")
    check(CredentialOwnership.isUsableLogin(credential: credential(good(["expiresAt": 1]))), "login: any positive expiresAt is a number above 0")
    for (name, json) in unusableLogins() {
        check(!CredentialOwnership.isUsableLogin(credential: json), "login: \(name) is not a usable login")
    }
    let reason = CredentialOwnership.unusableReason(credential: credential(good(["accessToken": "sk-ant-SECRET"], removing: ["refreshToken"])))
    check(reason != nil && !(reason ?? "").contains("SECRET"), "login: the reason names a field and never a token", reason ?? "nil")
    check(CredentialOwnership.unusableReason(credential: nil) != nil, "login: no credential at all is not a usable login")
}

@MainActor private func gateTests() {
    let usable = credential(good())
    check(LoginGate.save(token: usable) == .allowed, "gate: save allows a usable login")
    check(LoginGate.targetProblem(backupToken: usable) == nil, "gate: target validation passes a usable login")
    check(LoginGate.isSwitchable(backupToken: usable, needsReauth: false, cardSaysLoginExpired: false),
          "gate: an account with a usable saved login is switchable")
    check(!LoginGate.isSwitchable(backupToken: nil, needsReauth: false, cardSaysLoginExpired: false),
          "gate: an account with no saved login is not switchable")
    check(!LoginGate.isSwitchable(backupToken: usable, needsReauth: true, cardSaysLoginExpired: false)
          && !LoginGate.isSwitchable(backupToken: usable, needsReauth: false, cardSaysLoginExpired: true),
          "gate: a refused grant or an expired-login card still blocks a switch, as before")

    for (name, json) in unusableLogins() {
        var refused = false
        if case .refused(let why) = LoginGate.save(token: json), !why.isEmpty { refused = true }
        check(refused, "gate: save refuses \(name)")
        check(LoginGate.targetProblem(backupToken: json) != nil, "gate: target validation fails for \(name)")
        check(!LoginGate.isSwitchable(backupToken: json, needsReauth: false, cardSaysLoginExpired: false),
              "gate: an account whose saved login is \(name) is not switchable")
    }
}

@MainActor private func rereadTests() {
    let a = credential(good(["accessToken": "A1", "refreshToken": "R1"]))
    check(!CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: a), "reread: the same credential is unchanged")
    check(CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: credential(good(["accessToken": "A2", "refreshToken": "R1"]))),
          "reread: a different access token is a change")
    check(CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: credential(good(["accessToken": "A1", "refreshToken": "R2"]))),
          "reread: a different refresh token is a change")
    check(CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: credential(good(["accessToken": "A2", "refreshToken": "R2"]))),
          "reread: a renewed login (both tokens new) is a change")
    check(!CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: credential(good(["accessToken": "A1", "refreshToken": "R1", "expiresAt": 1_800_000_000_000.0]))),
          "reread: the same tokens with another expiresAt are not a change")
    check(!CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: credential(good(["accessToken": "A1", "refreshToken": "R1"]), extra: ["mcpOAuth": ["s": 1]])),
          "reread: another MCP login in the credential is not a change to the Claude login")
    check(CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: nil) && CredentialOwnership.liveLoginChanged(savedAtStep4: nil, now: a),
          "reread: a credential that appears or disappears is a change")
    check(!CredentialOwnership.liveLoginChanged(savedAtStep4: nil, now: nil), "reread: no credential before and after is unchanged")
    check(CredentialOwnership.liveLoginChanged(savedAtStep4: a, now: "not json") && !CredentialOwnership.liveLoginChanged(savedAtStep4: "x", now: "x"),
          "reread: an unreadable credential is compared as text")
}

@MainActor private func renewalExpiryTests() {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let nowMs = 1_800_000_000_000.0
    let stale = good(["refreshTokenExpiresAt": nowMs - 60_000])
    let renewed = CredentialOwnership.applyingRenewalExpiry(to: stale, refreshTokenExpiresIn: 7_776_000, now: now)
    check((renewed["refreshTokenExpiresAt"] as? Int64) == Int64(nowMs) + 7_776_000_000,
          "renewal: a positive refresh_token_expires_in sets refreshTokenExpiresAt to now plus that, in milliseconds")
    check(renewed["accessToken"] as? String == "access-1" && renewed["scopes"] as? [String] == ["user:inference"] && renewed["subscriptionType"] as? String == "max",
          "renewal: nothing else changes, scopes included")
    check(CredentialOwnership.applyingRenewalExpiry(to: good(), refreshTokenExpiresIn: 100, now: now)["refreshTokenExpiresAt"] as? Int64 == Int64(nowMs) + 100_000,
          "renewal: the key is added when the login had none")

    let removed = CredentialOwnership.applyingRenewalExpiry(to: stale, refreshTokenExpiresIn: nil, now: now)
    check(removed["refreshTokenExpiresAt"] == nil, "renewal: no refresh_token_expires_in and a stored value in the past removes the key")
    check(CredentialOwnership.applyingRenewalExpiry(to: stale, refreshTokenExpiresIn: 0, now: now)["refreshTokenExpiresAt"] == nil
          && CredentialOwnership.applyingRenewalExpiry(to: stale, refreshTokenExpiresIn: -5, now: now)["refreshTokenExpiresAt"] == nil,
          "renewal: a refresh_token_expires_in of 0 or below counts as none")
    let future = good(["refreshTokenExpiresAt": nowMs + 86_400_000])
    check((CredentialOwnership.applyingRenewalExpiry(to: future, refreshTokenExpiresIn: nil, now: now)["refreshTokenExpiresAt"] as? Double) == nowMs + 86_400_000,
          "renewal: no refresh_token_expires_in and a stored value still in the future leaves it alone")
    check(CredentialOwnership.applyingRenewalExpiry(to: good(), refreshTokenExpiresIn: nil, now: now)["refreshTokenExpiresAt"] == nil,
          "renewal: no refresh_token_expires_in and no stored value changes nothing")
}
