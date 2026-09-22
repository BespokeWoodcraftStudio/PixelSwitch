import Foundation

/// Builds the credential PixelSwitch writes on a switch.
///
/// Claude Code keeps two unrelated things in one Keychain item:
/// `claudeAiOauth`, the Claude account login, and `mcpOAuth`, the logins for
/// every MCP server on this Mac. Only the first belongs to an account. A switch
/// therefore takes `claudeAiOauth` from the target account's backup and keeps
/// every other key from the live item. Otherwise MCP logins made or renewed
/// since that backup was taken are replaced by older copies, and servers that
/// rotate refresh tokens then ask to sign in again.
enum ClaudeCredentialMerge {
    enum Result: Equatable {
        /// The target's `claudeAiOauth` over the live item's other keys.
        case merged(credential: String, keptKeys: [String])
        /// Could not merge: the target backup verbatim (the behaviour before 1.0.2).
        case targetOnly(credential: String, reason: String)
    }

    static func credentialForSwitch(live: String?, target: String) -> Result {
        guard let targetObject = object(from: target),
              let targetLogin = targetObject["claudeAiOauth"] as? [String: Any] else {
            return .targetOnly(credential: target, reason: "target backup has no readable claudeAiOauth")
        }
        guard let live, var liveObject = object(from: live) else {
            return .targetOnly(credential: target, reason: "live credential missing or not JSON")
        }
        liveObject["claudeAiOauth"] = targetLogin
        guard JSONSerialization.isValidJSONObject(liveObject),
              let data = try? JSONSerialization.data(withJSONObject: liveObject, options: [.withoutEscapingSlashes]),
              let merged = String(data: data, encoding: .utf8) else {
            return .targetOnly(credential: target, reason: "merged credential did not serialize")
        }
        let kept = liveObject.keys.filter { $0 != "claudeAiOauth" }.sorted()
        return .merged(credential: merged, keptKeys: kept)
    }

    private static func object(from json: String) -> [String: Any]? {
        guard let data = json.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
}
