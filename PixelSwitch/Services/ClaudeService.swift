import Foundation

private let log = FileLog("Claude")

/// UserDefaults key holding the user's preferred Claude CLI path (empty = auto).
let kClaudeBinaryPathPreferenceKey = "claudeBinaryPathPreference"

/// A claude binary discovered on this Mac.
struct DetectedClaudePath: Hashable, Identifiable {
    let path: String
    let label: String
    var id: String { path }
}

/// Interacts with the Claude CLI to get auth status and manage accounts.
final class ClaudeService: @unchecked Sendable {
    static let shared = ClaudeService()

    private let lock = NSLock()
    private var _claudePath: String
    /// Monotonic counter to detect out-of-order setPath completions.
    private var _setPathGeneration: UInt64 = 0

    /// Currently active path. Thread-safe.
    var claudePath: String {
        lock.lock(); defer { lock.unlock() }
        return _claudePath
    }

    private init() {
        let preference = UserDefaults.standard.string(forKey: kClaudeBinaryPathPreferenceKey) ?? ""
        if !preference.isEmpty, FileManager.default.isExecutableFile(atPath: preference) {
            self._claudePath = preference
            log.info("Claude binary path: \(preference) (user preference)")
        } else {
            let auto = Self.autoSelectedPath()
            self._claudePath = auto.path
            log.info("Claude binary path: \(auto.path) (\(auto.source))")
            if !preference.isEmpty {
                log.warning("Saved preference \(preference) is no longer valid; falling back to auto")
            }
        }
    }

    /// Update the runtime claude path. Pass nil or empty to revert to auto-detection.
    /// Does NOT validate — caller (Settings UI) is expected to validate before calling.
    /// Auto-resolution happens outside the lock (can take ~3s via shell PATH lookup);
    /// a generation counter ensures a slower call cannot overwrite a faster, later one.
    func setPath(_ override: String?) {
        lock.lock()
        _setPathGeneration &+= 1
        let myGen = _setPathGeneration
        lock.unlock()

        let resolved: (String, String)
        if let override, !override.isEmpty, FileManager.default.isExecutableFile(atPath: override) {
            resolved = (override, "override")
        } else {
            let auto = Self.autoSelectedPath()
            resolved = (auto.path, "auto/\(auto.source)")
        }

        lock.lock()
        guard myGen == _setPathGeneration else {
            lock.unlock()
            log.info("[setPath] superseded by newer call, discarding \(resolved.0)")
            return
        }
        _claudePath = resolved.0
        lock.unlock()
        log.info("[setPath] \(resolved.1): \(resolved.0)")
    }

    // MARK: - Detection

    /// Today's 3-tier fallback: curated → shell PATH → bare "claude".
    static func autoSelectedPath() -> (path: String, source: String) {
        for candidate in curatedPathCandidates() where FileManager.default.fileExists(atPath: candidate) {
            return (candidate, "curated")
        }
        if let shellPath = shellPathLookup() {
            return (shellPath, "shell PATH")
        }
        return ("claude", "fallback")
    }

    /// All claude binaries that actually exist on this Mac, deduplicated by
    /// resolved symlink target, ordered by discovery source.
    static func detectedPaths() -> [DetectedClaudePath] {
        var result: [DetectedClaudePath] = []
        var seenResolved = Set<String>()

        func add(_ path: String, _ label: String) {
            guard FileManager.default.fileExists(atPath: path) else { return }
            let resolved = URL(fileURLWithPath: path).resolvingSymlinksInPath().path
            guard !seenResolved.contains(resolved) else { return }
            seenResolved.insert(resolved)
            result.append(DetectedClaudePath(path: path, label: label))
        }

        for (path, label) in curatedLabeledCandidates() {
            add(path, label)
        }
        for (path, label) in nvmLabeledCandidates() {
            add(path, label)
        }
        if let shellPath = shellPathLookup() {
            add(shellPath, "From shell PATH")
        }

        return result
    }

    private static func curatedPathCandidates() -> [String] {
        curatedLabeledCandidates().map { $0.0 } + nvmLabeledCandidates().map { $0.0 }
    }

    private static func curatedLabeledCandidates() -> [(String, String)] {
        let home = NSHomeDirectory()
        return [
            ("/usr/local/bin/claude", "/usr/local/bin"),
            ("/opt/homebrew/bin/claude", "Homebrew"),
            ("/opt/local/bin/claude", "MacPorts"),
            ("\(home)/.local/bin/claude", "Anthropic native installer"),
            ("\(home)/.claude/local/claude", "Anthropic migrate installer"),
            ("\(home)/.npm-global/bin/claude", "npm global"),
            ("\(home)/.volta/bin/claude", "Volta"),
            ("\(home)/Library/pnpm/claude", "pnpm"),
            ("\(home)/.bun/bin/claude", "Bun"),
            ("\(home)/.yarn/bin/claude", "Yarn"),
        ]
    }

    /// Discover Claude binaries installed via NVM (Node Version Manager).
    /// NVM stores node versions at ~/.nvm/versions/node/<version>/bin/.
    private static func nvmLabeledCandidates() -> [(String, String)] {
        let nvmDir = "\(NSHomeDirectory())/.nvm/versions/node"
        guard FileManager.default.fileExists(atPath: nvmDir) else { return [] }
        guard let versions = try? FileManager.default.contentsOfDirectory(atPath: nvmDir) else {
            log.warning("[nvmLabeledCandidates] NVM directory exists but could not be read: \(nvmDir)")
            return []
        }
        return versions
            .filter { !$0.hasPrefix(".") }
            .sorted()
            .map { ("\(nvmDir)/\($0)/bin/claude", "NVM \($0)") }
    }

    /// Last-resort lookup: ask the user's interactive login shell where `claude` lives.
    /// Catches install layouts the curated list doesn't enumerate (asdf shims, fnm, n,
    /// pnpm/yarn/bun/Volta with non-default prefixes, custom npm prefixes, etc.).
    /// Bounded by a short timeout so a slow .zshrc can't block app launch.
    private static func shellPathLookup() -> String? {
        let process = Process()
        let stdout = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-ilc", "command -v claude"]
        process.standardOutput = stdout
        process.standardError = Pipe()
        process.environment = ProcessInfo.processInfo.environment

        do {
            try process.run()
        } catch {
            log.warning("[shellPathLookup] Failed to launch /bin/zsh: \(error.localizedDescription)")
            return nil
        }

        // Hard timeout — don't let a heavy shell rc file block forever.
        let deadline = Date().addingTimeInterval(3.0)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if process.isRunning {
            process.terminate()
            log.warning("[shellPathLookup] zsh exceeded 3s timeout; aborting")
            return nil
        }

        guard process.terminationStatus == 0 else { return nil }
        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        let raw = String(data: data, encoding: .utf8) ?? ""
        // `command -v` may emit multiple lines if claude is shadowed; take the first.
        let candidate = raw
            .split(whereSeparator: \.isNewline)
            .first
            .map(String.init)?
            .trimmingCharacters(in: .whitespaces) ?? ""
        guard candidate.hasPrefix("/"),
              FileManager.default.isExecutableFile(atPath: candidate) else {
            return nil
        }
        return candidate
    }

    // MARK: - Auth Status

    func getAuthStatus() async throws -> AuthStatus {
        log.info("[getAuthStatus] Fetching auth status...")
        let output = try await runClaude(args: ["auth", "status"], timeout: 30)
        guard let data = output.data(using: .utf8) else {
            log.error("[getAuthStatus] Invalid output (not UTF-8)")
            throw ClaudeServiceError.invalidOutput
        }
        let status = try JSONDecoder().decode(AuthStatus.self, from: data)
        log.info("[getAuthStatus] loggedIn=\(status.loggedIn), provider=\(status.apiProvider ?? "nil"), sub=\(status.subscriptionType ?? "nil")")
        return status
    }

    func isClaudeAvailable() async -> Bool {
        do {
            let version = try await runClaude(args: ["--version"], timeout: 30)
            log.info("[isClaudeAvailable] YES, version: \(version.trimmingCharacters(in: .whitespacesAndNewlines))")
            return true
        } catch {
            log.error("[isClaudeAvailable] NO, error: \(error.localizedDescription)")
            return false
        }
    }

    // MARK: - Usage API

    enum UsageError: Error {
        case expired
        case network(String)
        case decode(String)
        case rateLimited(retryAfter: TimeInterval?)
        /// 403 permission_error - e.g. "OAuth authentication is currently not
        /// allowed for this organization" (no active Pro/Max subscription).
        case forbidden(String)
    }

    /// Fetch usage for a specific access token
    func getUsageLimits(accessToken: String) async throws -> UsageAPIResponse {
        guard let url = URL(string: "https://api.anthropic.com/api/oauth/usage") else { throw UsageError.network("invalid url") }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")

        log.debug("[getUsageLimits] REQUEST URL: \(url.absoluteString)")

        let (responseData, response) = try await URLSession.shared.data(for: request)
        let httpResponse = response as? HTTPURLResponse
        guard httpResponse?.statusCode == 200 else {
            let responseString = String(data: responseData, encoding: .utf8) ?? ""
            log.error("[getUsageLimits] HTTP \(httpResponse?.statusCode ?? 0)")

            if httpResponse?.statusCode == 401 || responseString.contains("token_expired") {
                throw UsageError.expired
            }
            if httpResponse?.statusCode == 429 {
                let retryAfter = httpResponse?.value(forHTTPHeaderField: "Retry-After")
                    .flatMap(TimeInterval.init)
                log.warning("[getUsageLimits] 429, Retry-After: \(retryAfter.map { String(format: "%.0f", $0) } ?? "none")s")
                throw UsageError.rateLimited(retryAfter: retryAfter)
            }
            if httpResponse?.statusCode == 403 {
                // Typically: no active Pro/Max subscription on the account.
                log.warning("[getUsageLimits] 403 permission error: \(responseString.prefix(200))")
                throw UsageError.forbidden(responseString)
            }
            throw UsageError.network("HTTP \(httpResponse?.statusCode ?? 0)")
        }
        
        do {
            let usage = try JSONDecoder().decode(UsageAPIResponse.self, from: responseData)
            log.info("[getUsageLimits] session=\(usage.fiveHour?.utilization ?? -1)%, weekly=\(usage.sevenDay?.utilization ?? -1)%")
            return usage
        } catch {
            log.error("[getUsageLimits] Decode Error: \(error.localizedDescription)")
            throw UsageError.decode(error.localizedDescription)
        }
    }

    // MARK: - Whose login is this?

    /// Who an access token belongs to, per the API.
    struct AccountIdentity: Sendable, Equatable {
        /// The permanent account id (`accountUuid` in ~/.claude.json's `oauthAccount`).
        let uuid: String?
        let email: String?
    }

    /// The account an access token belongs to, from the API
    /// (`/api/oauth/profile`), or nil if the token is expired or the call fails.
    /// This is the only proof of ownership; see `CredentialOwnership`.
    func accountIdentity(forAccessToken accessToken: String) async -> AccountIdentity? {
        guard let url = URL(string: "https://api.anthropic.com/api/oauth/profile") else { return nil }
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard status == 200,
                  let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let account = object["account"] as? [String: Any] else {
                log.warning("[accountIdentity] Profile lookup failed (HTTP \(status))")
                return nil
            }
            let uuid = account["uuid"] as? String
            let email = account["email"] as? String ?? account["email_address"] as? String
            guard uuid != nil || email != nil else {
                log.warning("[accountIdentity] Profile response names no account")
                return nil
            }
            return AccountIdentity(uuid: uuid, email: email)
        } catch {
            log.warning("[accountIdentity] Profile lookup failed: \(error.localizedDescription)")
            return nil
        }
    }

    // MARK: - OAuth refresh (direct token endpoint, no keychain swap)

    /// Claude Code's public OAuth client id (PKCE public client - not a secret).
    private static let oauthClientID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    private static let oauthTokenURL = "https://console.anthropic.com/v1/oauth/token"

    /// Outcome of an OAuth refresh attempt. The `rejected`/`transient`
    /// distinction is load-bearing for the caller: `rejected` means the refresh
    /// token is dead and only re-authentication helps, while `transient` says
    /// nothing about the token at all — showing "re-authenticate" for a network
    /// blip would send the user through a pointless login.
    enum OAuthRefreshResult {
        case success(String)
        /// The endpoint (or the stored credential itself) says this grant can
        /// never work: no refresh token stored, or a 4xx rejection.
        case rejected
        /// Network trouble or a server-side error; the token's validity is unknown.
        case transient
    }

    /// Silently refresh a credential JSON using its refresh token, via a direct
    /// POST to the OAuth token endpoint. Never touches the keychain - safe to run
    /// for non-active accounts while Claude Code sessions are working, because
    /// only PixelSwitch holds these backups (rotation cannot race anyone).
    func refreshOAuthCredentials(_ credentialsJSON: String) async -> OAuthRefreshResult {
        guard var root = (try? JSONSerialization.jsonObject(with: Data(credentialsJSON.utf8))) as? [String: Any],
              var oauth = root["claudeAiOauth"] as? [String: Any],
              let refreshToken = oauth["refreshToken"] as? String, !refreshToken.isEmpty,
              let url = URL(string: Self.oauthTokenURL)
        else {
            log.warning("[refreshOAuth] Stored credential has no refresh token")
            return .rejected
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: [
            "grant_type": "refresh_token",
            "refresh_token": refreshToken,
            "client_id": Self.oauthClientID,
        ])

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard status == 200,
                  let resp = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let accessToken = resp["access_token"] as? String,
                  let expiresIn = resp["expires_in"] as? Double
            else {
                // NEVER log the response body. This guard also fires when the
                // status IS 200 but a field failed to parse (schema change) —
                // in that case the body is a *successful* token response whose
                // first bytes are a live access token, headed for a log file
                // that outlives rotation and gets attached to bug reports.
                // Log the status plus an allowlisted OAuth error code only.
                let oauthError = ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?["error"] as? String
                let knownCodes = ["invalid_grant", "invalid_request", "invalid_client", "unauthorized_client", "unsupported_grant_type", "invalid_scope"]
                let loggedCode = oauthError.map { knownCodes.contains($0) ? $0 : "unrecognized" } ?? "none"
                log.warning("[refreshOAuth] Refresh not applied (HTTP \(status), error=\(loggedCode), \(data.count) bytes)")

                // A 200 that failed OUR parse is a schema change, not a dead
                // grant. Otherwise only a grant-terminal OAuth error means the
                // refresh token is dead (RFC 6749 §5.2: `invalid_grant` covers
                // expired/revoked/invalid refresh tokens). Everything else —
                // 5xx, 429, 408, an `invalid_request` from a request-shape
                // mismatch — says nothing about the token, and demanding
                // re-authentication for it would be a pointless login.
                if status == 200 { return .transient }
                return oauthError == "invalid_grant" ? .rejected : .transient
            }

            oauth["accessToken"] = accessToken
            oauth["expiresAt"] = Int64(Date().timeIntervalSince1970 * 1000) + Int64(expiresIn * 1000)
            if let newRefresh = resp["refresh_token"] as? String, !newRefresh.isEmpty {
                oauth["refreshToken"] = newRefresh
            }
            // `scopes` is deliberately NOT rewritten from the refresh response.
            // The CLI (verified against claude 2.1.220) decides whether it
            // recognises a stored login by checking that `scopes` contains
            // "user:inference"; a refresh response carrying a narrower scope
            // string would make this credential invisible to `claude auth
            // status`, and the account would read as not-logged-in with no
            // visible cause. The login-time value is the one to keep.
            root["claudeAiOauth"] = oauth
            let out = try JSONSerialization.data(withJSONObject: root)
            log.info("[refreshOAuth] Access token refreshed, valid for \(Int(expiresIn / 3600))h \(Int(expiresIn.truncatingRemainder(dividingBy: 3600) / 60))m")
            guard let json = String(data: out, encoding: .utf8) else { return .transient }
            return .success(json)
        } catch {
            log.warning("[refreshOAuth] Refresh failed: \(error.localizedDescription)")
            return .transient
        }
    }

    /// Extract access token string from a token JSON (keychain format)
    static func extractAccessToken(from tokenJSON: String) -> String? {
        guard let data = tokenJSON.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let oauth = json["claudeAiOauth"] as? [String: Any],
              let accessToken = oauth["accessToken"] as? String else {
            return nil
        }
        return accessToken
    }

    // MARK: - Account Switching

    /// Result of a completed switch.
    struct SwitchOutcome {
        /// Set when the swap succeeded but `claude auth status` could not confirm
        /// it, because this credential source shadows the stored claude.ai login.
        let shadowedBy: String?
    }

    /// `targetBackup` is resolved by the caller (which can distinguish a
    /// missing backup from a briefly unreadable store) and handed down so no
    /// second, ambiguity-collapsing lookup happens here.
    @discardableResult
    /// `liveOwner` is the account the live login was proven (by the API) to
    /// belong to, and `verifiedLiveCredential` the exact credential that proof
    /// was about. Nothing is backed up unless both are known and the Keychain
    /// still holds that same login: saving an unproven login under an account
    /// is how one account's backup came to hold another's token.
    func switchAccount(liveOwner: Account?, verifiedLiveCredential: String?, to targetAccount: Account, targetBackup: AccountBackup) async throws -> SwitchOutcome {
        let keychain = KeychainService.shared

        log.info("[switchAccount] Switching to \(targetAccount.id); live login belongs to \(liveOwner?.id.uuidString ?? "an unproven account")")

        // 1. Back up the live login under the account it belongs to.
        log.info("[switchAccount] Step 1: Backing up the live login...")
        let freshCredential = keychain.readClaudeToken()
        let stillTheCheckedLogin: Bool = {
            guard let verified = verifiedLiveCredential.flatMap({ CredentialOwnership.login(fromCredential: $0) }),
                  let fresh = freshCredential.flatMap({ CredentialOwnership.login(fromCredential: $0) }) else { return false }
            return CredentialOwnership.sameGrant(verified, fresh)
        }()
        if let owner = liveOwner, let liveToken = freshCredential, stillTheCheckedLogin {
            // Take the identity from ~/.claude.json only if it names the same
            // account; running sessions can leave another account's there.
            let fileIdentity = keychain.readOAuthAccount()
            let fileEmail = fileIdentity?["emailAddress"]?.value as? String
            let identity = (fileEmail == owner.email ? fileIdentity : nil)
                ?? keychain.getAccountBackup(forAccountId: owner.id.uuidString)?.oauthAccount
            if let identity {
                let saved = keychain.saveAccountBackup(token: liveToken, oauthAccount: identity, forAccountId: owner.id.uuidString)
                log.info("[switchAccount] Step 1: Backup saved for its owner: \(saved)")
            } else {
                log.warning("[switchAccount] Step 1: No identity on record for the owner; skipping backup")
            }
        } else {
            log.warning("[switchAccount] Step 1: The live login's owner is not proven (or it changed since it was checked); skipping the backup so no account's backup is overwritten with another's login")
        }

        // 2. Target backup was resolved and validated by the caller.
        log.info("[switchAccount] Step 2: Using caller-resolved backup for target account")

        // 3. Write target token to keychain + target oauthAccount to ~/.claude.json.
        // Only the Claude login comes from the backup; this Mac's MCP server
        // logins (and any other key) stay as they are in the live item.
        log.info("[switchAccount] Step 3: Writing target credentials...")
        let credential: String
        switch ClaudeCredentialMerge.credentialForSwitch(live: keychain.readClaudeToken(), target: targetBackup.token) {
        case .merged(let merged, let kept):
            credential = merged
            log.info("[switchAccount] Step 3: claudeAiOauth from the target backup; kept this Mac's \(kept.isEmpty ? "(nothing else)" : kept.joined(separator: ", ")) from the live credential")
        case .targetOnly(let target, let reason):
            credential = target
            log.warning("[switchAccount] Step 3: Writing the target backup verbatim (\(reason)); MCP logins come from the backup")
        }
        guard keychain.writeClaudeToken(credential) else {
            log.error("[switchAccount] Step 3: Failed to write token to keychain!")
            throw ClaudeServiceError.keychainWriteFailed
        }
        guard keychain.writeOAuthAccount(targetBackup.oauthAccount) else {
            log.error("[switchAccount] Step 3: Failed to write oauthAccount to ~/.claude.json!")
            throw ClaudeServiceError.oauthAccountWriteFailed
        }
        log.info("[switchAccount] Step 3: Both token and oauthAccount written")
        // Running Claude Code sessions only re-read the Keychain when this
        // file's mtime changes (or it is absent); without this they keep
        // billing the old account for hours.
        keychain.touchClaudeCredentialsFile()

        // 4. Verify. The credentials are already written, so a CLI that does not
        // answer must not strand the switch half-done: check the store directly.
        log.info("[switchAccount] Step 4: Verifying with `claude auth status`...")
        let status: AuthStatus
        do {
            status = try await getAuthStatus()
        } catch ClaudeServiceError.timedOut {
            log.warning("[switchAccount] Step 4: `claude auth status` timed out; verifying against the credential store instead")
            guard credentialsOnDiskMatch(backup: targetBackup, email: targetAccount.email) else {
                throw ClaudeServiceError.switchVerificationFailed
            }
            log.info("[switchAccount] Step 4: Switch verified against the credential store (CLI did not answer)")
            return SwitchOutcome(shadowedBy: nil)
        }
        guard status.loggedIn else {
            log.error("[switchAccount] Step 4: Not logged in after switch!")
            throw ClaudeServiceError.switchVerificationFailed
        }

        if let email = status.email {
            guard email == targetAccount.email else {
                log.error("[switchAccount] Step 4: Logged in as \(email) instead of \(targetAccount.email)")
                throw ClaudeServiceError.switchWrongAccount(expected: targetAccount.email, actual: email)
            }
            log.info("[switchAccount] Step 4: Switch verified — logged in as \(email)")
            return SwitchOutcome(shadowedBy: nil)
        }

        // No `email` in the status output. The CLI is logged in, but resolves to a
        // credential source that outranks the stored claude.ai login (env token,
        // apiKeyHelper, OAuth token file, Anthropic profile, third-party provider),
        // so it omits the identity block entirely. That says nothing about whether
        // our swap worked, so verify against the credentials we just wrote instead
        // of reporting the target account as "wrong" (issue #18).
        let shadowedBy = status.shadowingAuthMethod ?? "unknown"
        log.warning("[switchAccount] Step 4: CLI reports authMethod=\(shadowedBy) and omits the account identity; verifying against the credential store instead")
        guard credentialsOnDiskMatch(backup: targetBackup, email: targetAccount.email) else {
            throw ClaudeServiceError.switchVerificationFailed
        }
        log.info("[switchAccount] Step 4: Switch verified against the credential store (CLI identity hidden by \(shadowedBy))")
        return SwitchOutcome(shadowedBy: shadowedBy)
    }

    /// Ground-truth check that does not depend on `claude auth status`: both
    /// halves of a switch — the keychain token and the `~/.claude.json` identity —
    /// must hold the target account.
    private func credentialsOnDiskMatch(backup: AccountBackup, email: String) -> Bool {
        let keychain = KeychainService.shared

        guard let liveToken = keychain.readClaudeToken(),
              let liveAccessToken = Self.extractAccessToken(from: liveToken),
              let expectedAccessToken = Self.extractAccessToken(from: backup.token),
              liveAccessToken == expectedAccessToken else {
            log.error("[switchAccount] Keychain token does not match the target account's backup")
            return false
        }

        guard let liveOAuth = keychain.readOAuthAccount(),
              (liveOAuth["emailAddress"]?.value as? String) == email else {
            log.error("[switchAccount] ~/.claude.json identity does not match \(email)")
            return false
        }

        return true
    }

    /// Capture the current Claude auth token + oauthAccount and associate with an account
    func captureCurrentCredentials(forAccountId accountId: String) -> Bool {
        log.info("[capture] Capturing credentials for account \(accountId)...")
        let keychain = KeychainService.shared
        guard let token = keychain.readClaudeToken() else {
            log.error("[capture] Failed: no token found in keychain")
            return false
        }
        guard let oauthAccount = keychain.readOAuthAccount() else {
            log.error("[capture] Failed: no oauthAccount found in ~/.claude.json")
            return false
        }
        let email = (oauthAccount["emailAddress"]?.value as? String) ?? "?"
        log.info("[capture] Token + oauthAccount found (email=\(email)), saving backup...")
        let result = keychain.saveAccountBackup(token: token, oauthAccount: oauthAccount, forAccountId: accountId)
        log.info("[capture] Save result: \(result)")
        return result
    }

    /// Run `claude auth login` which opens browser for OAuth.
    func login() async throws {
        log.info("[login] Starting `claude auth login`... (will open browser)")
        _ = try await runClaude(args: ["auth", "login"])
        log.info("[login] `claude auth login` process exited")

        // Give keychain a moment to sync after CLI writes
        try await Task.sleep(for: .seconds(1))
        log.info("[login] Post-login delay complete, ready for token capture")
    }

    /// Run `claude auth logout`
    func logout() async throws {
        log.info("[logout] Running `claude auth logout`...")
        _ = try await runClaude(args: ["auth", "logout"])
        log.info("[logout] Logout complete")
    }

    // MARK: - Version

    /// Run `<path> --version` and return the first semver-looking token.
    /// Returns nil on launch failure, non-zero exit, or no version found.
    static func readVersion(at path: String) async -> String? {
        guard FileManager.default.isExecutableFile(atPath: path) else { return nil }
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                let stdout = Pipe()
                process.executableURL = URL(fileURLWithPath: path)
                process.arguments = ["--version"]
                process.standardOutput = stdout
                process.standardError = Pipe()

                // Inject same PATH augmentation as runClaude so NVM-installed
                // claude can find `node` when invoked here.
                var env = ProcessInfo.processInfo.environment
                let homeDir = NSHomeDirectory()
                var extraPaths = [
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "\(homeDir)/.local/bin",
                    "\(homeDir)/.npm-global/bin"
                ]
                let resolved = URL(fileURLWithPath: path).resolvingSymlinksInPath().path
                let resolvedBinDir = URL(fileURLWithPath: resolved).deletingLastPathComponent().path
                extraPaths.insert(resolvedBinDir, at: 0)
                let existingPath = env["PATH"] ?? "/usr/bin:/bin"
                env["PATH"] = (extraPaths + [existingPath]).joined(separator: ":")
                env["HOME"] = homeDir
                process.environment = env

                do {
                    try process.run()
                } catch {
                    log.warning("[readVersion] launch failed for \(path): \(error.localizedDescription)")
                    continuation.resume(returning: nil)
                    return
                }

                let deadline = Date().addingTimeInterval(5.0)
                while process.isRunning && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.05)
                }
                if process.isRunning {
                    process.terminate()
                    log.warning("[readVersion] timed out for \(path)")
                    continuation.resume(returning: nil)
                    return
                }

                guard process.terminationStatus == 0 else {
                    continuation.resume(returning: nil)
                    return
                }
                let data = stdout.fileHandleForReading.readDataToEndOfFile()
                let raw = String(data: data, encoding: .utf8) ?? ""
                continuation.resume(returning: Self.extractSemver(from: raw))
            }
        }
    }

    /// Fetch the latest published claude-code version. Returns nil on any failure.
    static func fetchLatestVersion() async -> String? {
        guard let url = URL(string: "https://downloads.claude.ai/claude-code-releases/latest") else {
            return nil
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5.0
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                log.warning("[fetchLatestVersion] non-200 response")
                return nil
            }
            let raw = String(data: data, encoding: .utf8) ?? ""
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            // Strict: the entire body must BE a semver. Protects against the CDN
            // returning an HTML error page with 200 status that happens to contain
            // dotted numbers (CSS dimensions, version strings in copy, etc.).
            return Self.isPureSemver(trimmed) ? trimmed : nil
        } catch {
            log.warning("[fetchLatestVersion] error: \(error.localizedDescription)")
            return nil
        }
    }

    /// True if the whole string is ASCII digits separated by dots (e.g. "1.0.42", "2.1.139").
    private static func isPureSemver(_ s: String) -> Bool {
        guard !s.isEmpty, s.count <= 32 else { return false }
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count >= 2 else { return false }
        for p in parts {
            guard !p.isEmpty else { return false }
            for ch in p {
                guard ch.isASCII, ch.isNumber else { return false }
            }
        }
        return true
    }

    /// Extract the first dotted-ASCII-numeric token from a string (e.g. "1.0.42" out of "1.0.42 (Claude Code)").
    private static func extractSemver(from text: String) -> String? {
        let allowed: (Character) -> Bool = { $0.isASCII && ($0.isNumber || $0 == ".") }
        for token in text.split(whereSeparator: { !allowed($0) }) {
            let s = String(token)
            guard isPureSemver(s) else { continue }
            return s
        }
        return nil
    }

    // MARK: - CLI Runner

    /// `timeout` ends the process and throws `.timedOut` if it runs longer; nil
    /// waits forever (interactive logins take as long as the user does).
    private func runClaude(args: [String], timeout: TimeInterval? = nil) async throws -> String {
        let claudePath = self.claudePath
        log.debug("[runClaude] Running: \(claudePath) \(args.joined(separator: " "))")
        return try await withCheckedThrowingContinuation { continuation in
            // Resumed exactly once: by the reader when the process ends, or by
            // the deadline if it never does.
            let once = ResumeOnce(continuation)
            DispatchQueue.global(qos: .userInitiated).async { [claudePath] in
                let process = Process()
                let pipe = Pipe()

                process.executableURL = URL(fileURLWithPath: claudePath)
                process.arguments = args
                process.standardOutput = pipe
                process.standardError = pipe

                var env = ProcessInfo.processInfo.environment
                let homeDir = NSHomeDirectory()
                // Include the parent directory of the discovered claude binary
                // so that `node` is on PATH for NVM-installed scripts.
                // Only add it when claudePath is absolute (skip the bare "claude" fallback).
                var extraPaths = [
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "\(homeDir)/.local/bin",
                    "\(homeDir)/.npm-global/bin"
                ]
                if claudePath.contains("/") {
                    // Resolve symlinks so that e.g. /usr/local/bin/claude -> ~/.nvm/.../bin/claude
                    // yields the NVM bin dir where `node` actually lives
                    let resolved = URL(fileURLWithPath: claudePath).resolvingSymlinksInPath().path
                    let resolvedBinDir = URL(fileURLWithPath: resolved).deletingLastPathComponent().path
                    extraPaths.insert(resolvedBinDir, at: 0)
                }
                let existingPath = env["PATH"] ?? "/usr/bin:/bin"
                env["PATH"] = (extraPaths + [existingPath]).joined(separator: ":")
                env["HOME"] = homeDir
                process.environment = env

                let command = "claude \(args.joined(separator: " "))"
                do {
                    try process.run()
                    if let timeout {
                        let pid = process.processIdentifier
                        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                            guard process.isRunning else { return }
                            log.error("[runClaude] Timed out after \(Int(timeout)) s: \(command); stopping it")
                            // Release the caller now, whatever the process does next:
                            // a grandchild holding the pipe open must not hang a switch.
                            once.resume(throwing: ClaudeServiceError.timedOut(command))
                            process.terminate()
                            // A process that ignores SIGTERM (a blocked event loop) is killed.
                            DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
                                if process.isRunning { kill(pid, SIGKILL) }
                            }
                        }
                    }
                    // Read before waiting: a full pipe would otherwise block the child forever.
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    let output = String(data: data, encoding: .utf8) ?? ""

                    if once.isResumed {
                        return  // the deadline already answered
                    } else if process.terminationStatus == 0 {
                        log.debug("[runClaude] Success (exit 0), output length: \(output.count)")
                        once.resume(returning: output)
                    } else {
                        log.error("[runClaude] Failed (exit \(process.terminationStatus))")
                        once.resume(throwing: ClaudeServiceError.cliError("exit \(process.terminationStatus)"))
                    }
                } catch {
                    log.error("[runClaude] Process launch failed: \(error.localizedDescription)")
                    once.resume(throwing: ClaudeServiceError.processLaunchFailed(error))
                }
            }
        }
    }
}

// MARK: - Errors

enum ClaudeServiceError: LocalizedError {
    case invalidOutput
    case cliError(String)
    case processLaunchFailed(Error)
    case noTokenForAccount(String)
    case keychainWriteFailed
    case oauthAccountWriteFailed
    case switchVerificationFailed
    case switchWrongAccount(expected: String, actual: String)
    case timedOut(String)
    case targetLoginBelongsElsewhere(target: String, owner: String)

    var errorDescription: String? {
        switch self {
        case .invalidOutput:
            return "Invalid output from Claude CLI"
        case .cliError(let msg):
            return "Claude CLI error: \(msg)"
        case .processLaunchFailed(let error):
            return "Failed to launch Claude: \(error.localizedDescription)"
        case .noTokenForAccount:
            return "No stored backup for target account"
        case .keychainWriteFailed:
            return "Failed to write token to keychain"
        case .oauthAccountWriteFailed:
            return "Failed to write oauthAccount to ~/.claude.json"
        case .switchVerificationFailed:
            return "Account switch verification failed"
        case .switchWrongAccount(let expected, let actual):
            return "Switch failed: expected \(expected) but got \(actual). Try removing and re-adding the account."
        case .timedOut(let what):
            return "Claude CLI did not answer in time (\(what))"
        case .targetLoginBelongsElsewhere(let target, let owner):
            return "The login saved for \(target) belongs to \(owner). Re-authenticate \(target) to fix it."
        }
    }
}

/// Resumes a checked continuation exactly once, from whichever path gets there first.
private final class ResumeOnce<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Error>?

    init(_ continuation: CheckedContinuation<T, Error>) { self.continuation = continuation }

    var isResumed: Bool { lock.lock(); defer { lock.unlock() }; return continuation == nil }
    func resume(returning value: T) { take()?.resume(returning: value) }
    func resume(throwing error: Error) { take()?.resume(throwing: error) }

    private func take() -> CheckedContinuation<T, Error>? {
        lock.lock(); defer { lock.unlock() }
        let taken = continuation
        continuation = nil
        return taken
    }
}
