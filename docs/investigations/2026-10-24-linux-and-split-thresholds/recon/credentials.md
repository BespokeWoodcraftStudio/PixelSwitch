# Recon: credential lifecycle and account switching

Read-only recon of PixelSwitch at commit `35f8a99` (1.9, clean tree), 2026-10-24. Every claim cites `file:line`
(paths relative to `PixelSwitch/` unless they start with `Tests/`, `docs/` or `ARCHITECTURE.md`). "Not found" means searched and absent.
No secret value was read or written down. Where a claim comes from somewhere other than this repo it is labelled
**[CC bundle]** (strings read out of the installed Claude Code 2.1.307 binary, `~/.local/share/claude/versions/2.1.307`, minified JS,
so inferred, not exercised), **[this Mac]** (key names and file existence only), or **[log]** (counts from `~/Library/Logs/PixelSwitch-app.log*`).

Files read in full: `Services/KeychainService.swift`, `ClaudeCredentialsFile.swift`, `ClaudeCredentialMerge.swift`, `ClaudeTokenWriter.swift`,
`CredentialOwnership.swift`, `LegacyMigration.swift`, `UsageRequestGuard.swift`, `Models/Account.swift`, `Models/AccountOrder.swift`,
and in `AppState.swift` lines 1-1300 and 1300-1896 (everything touching credentials), plus `Services/ClaudeService.swift`
204-660 and 780-908, `docs/SESSION-HANDOVER.md`, `ARCHITECTURE.md`.

---

## 0. The ten facts the spec most needs

1. **Two stores PixelSwitch does not own, one it does.** Claude Code's login = macOS Keychain item `Claude Code-credentials` (account = `NSUserName()`), `KeychainService.swift:60-61`. Claude Code's identity = `oauthAccount` object inside `~/.claude.json`, `:85`. PixelSwitch's own copy of every account = ONE Keychain item, service `ai.pixelventures.pixelswitch.backups`, account `all-accounts`, holding one JSON object `{ "<account UUID>": {"token": "<credential JSON as a string>", "oauthAccount": {...}} }`, `:322,330,562-596`.
2. **"token" everywhere in this code means the WHOLE credential JSON string**, not an access token. It contains `claudeAiOauth` and also `mcpOAuth` (MCP logins for the whole Mac) and anything else Claude Code stored. A switch replaces only `claudeAiOauth` and keeps every other live key, `ClaudeCredentialMerge.swift:20-36`.
3. **Who is live is decided by Anthropic's API, never by `~/.claude.json`.** `GET https://api.anthropic.com/api/oauth/profile` with the live access token, matched to a saved account by `account.uuid` and/or email, `ClaudeService.swift:297-334`, `AppState.swift:893-923`. Reason on record: running Claude Code sessions rewrite `~/.claude.json` from memory (`CredentialOwnership.swift:3-13`).
4. **Refresh request**: `POST https://console.anthropic.com/v1/oauth/token`, JSON `{"grant_type":"refresh_token","refresh_token":...,"client_id":"9d1c250a-e61b-44d9-88ed-5944d1962f5e"}`, header `Content-Type: application/json` only, `ClaudeService.swift:339-340,370-377`. Only NON-active accounts' saved copies are refreshed this way; the live login is renewed by Claude Code itself.
5. **PixelSwitch never touches the live login's refresh token.** It captures what Claude Code did: `reconcileLiveLogin` every refresh cycle (default 300 s, `PixelSwitchApp.swift:28,69`) copies the live credential into the proven owner's backup, `AppState.swift:1037-1075`; and a switch backs the live login up first, `ClaudeService.swift:466-501`.
6. **Switch order** (details in section 3): prove live owner -> validate/renew target backup -> save live login under its owner -> write Keychain item (merged) -> write `~/.claude.json` `oauthAccount` -> bump mtime of `~/.claude/.credentials.json` if it exists -> verify with `claude auth status` (fallback: compare the stores) -> update app state -> refresh.
7. **Linux**: Claude Code keeps the login in `<configDir>/.credentials.json`, mode 0600, written with mkdir + write + chmod **[CC bundle]**; `configDir` is `~/.claude` unless `CLAUDE_CONFIG_DIR` (or `CLAUDE_SECURESTORAGE_CONFIG_DIR`) is set. `~/.claude/.credentials.json` does not exist on this Mac **[this Mac]**. `secret-tool` appears in the bundle only in a sandbox deny list, not as a store **[CC bundle]**. PixelSwitch already has the file's path and a touch routine (`ClaudeCredentialsFile.swift`) and a file-format backup reader (`KeychainService.swift:448-487`), which are the nearest existing Linux building blocks.
8. **Defect (g) is real and I traced it to the line**: `AppState.swift:751-757` marks the fallback active BEFORE `performSwitch` runs, and `:808` then returns "No switch needed" (section 8). A second, related defect: removing the LAST account leaves `activeAccount` pointing at the removed one and the next refresh silently re-creates it (`:1870-1883`, section 8.3).
9. **Surprise: Claude Code 2.1.307 refreshes against `https://platform.claude.com/v1/oauth/token`** (same client id) and sends a `scope` field; PixelSwitch posts to `console.anthropic.com` without `scope`. PixelSwitch's refreshes still succeed today **[log]** (success lines up to 2026-10-24T19:26Z), so the old host still answers. Re-check before the Linux build copies it (section 5).
10. **Surprise: `ARCHITECTURE.md` token-flow sections are stale** (per-account items `com.pixelswitch.tokens`, a pre-switch `claude auth status` check, "refresh every 30 s", delete-then-add). The code is authoritative; section 10 lists the differences. Do not write the spec from that document.

---

## 1. Stores: every read and write

### 1.1 Table

| # | Store | Location / name | Format | Read by | Written by | Owner |
|---|---|---|---|---|---|---|
| S1 | Claude Code's live login | Keychain generic password, service `Claude Code-credentials`, account `NSUserName()` (`KeychainService.swift:60-61`) | credential JSON string (`claudeAiOauth` + other keys, 1.2) | `readClaudeToken()` -> `/usr/bin/security find-generic-password -s ... -a ... -w` (`:112-129`); exact-bytes variant `readClaudeTokenExact()` (`:656-675`) | `writeClaudeToken()` -> `ClaudeTokenWriter` -> `/usr/bin/security add-generic-password -U` (`:133-155`; `ClaudeTokenWriter.swift:50-82`) | Claude Code. PixelSwitch writes it only on a switch |
| S2 | Claude Code's identity | `~/.claude.json`, key `oauthAccount` (`KeychainService.swift:85,175-209`) | JSON object, whole file parsed to `[String: AnyCodable]` and re-encoded `.prettyPrinted,.sortedKeys`, `.atomic` write (`:195-201`) | `readOAuthAccount()` | `writeOAuthAccount()` replaces ONLY the `oauthAccount` key; every other key (this file has 86 top-level keys on this Mac **[this Mac]**) round-trips through `AnyCodable` | Claude Code. PixelSwitch writes it on a switch and in `reconcileLiveLogin` |
| S3 | Claude Code's plaintext fallback | `~/.claude/.credentials.json` (`ClaudeCredentialsFile.swift:25-27`) | same JSON as S1 **[CC bundle]** | never read | **mtime bumped only**, via `setAttributes([.modificationDate: now])`, never created/read/rewritten/deleted (`:34-43`) | Claude Code |
| S4 | PixelSwitch backups (current) | Keychain generic password, service `ai.pixelventures.pixelswitch.backups`, account `all-accounts` (`:322,330`), through the Security framework (`SecItemCopyMatching/Update/Add`, `:360-370,562-596`) | one JSON object, `[accountUUIDString: AccountBackup]`; `AccountBackup = {token: String, oauthAccount: [String: AnyCodable]}` (`:7-10`) | `loadBackupStore()` (`:404-491`) | `saveBackupStore()` (`:562-596`) | PixelSwitch |
| S5 | Legacy backup item | Keychain service `me.xueshi.ccswitcher.backups`, account `all-accounts` (`:328,330`) | same as S4 | read once if S4 is empty (`:426-437`) | never written; deleted once S4 is verified (`:509-530,544-559`) | forked-from app |
| S6 | Legacy backup file | `~/.pixelswitch/backups.json`, else `~/.ccswitcher/backups.json` (`:78-84,450-452`) | same JSON as S4, plain file | read if S4 and S5 are empty (`:454-487`) | never written; moved aside as `.unreadable-<epoch>` if undecodable (`:468-476`), deleted after migration into S4 (`:484`) | pre-keychain PixelSwitch |
| S7 | Account list | `UserDefaults` key `ai.pixelventures.pixelswitch.accounts` (`AppState.swift:67,1848-1856`); legacy key `com.ccswitcher.accounts` read as fallback (`:71,1813-1814`) | `JSONEncoder()` of `[Account]` (default date strategy) | `loadAccounts()` (`:1809-1846`) | `saveAccounts()` | PixelSwitch |
| S8 | needs-reauth flags | `UserDefaults` key `ai.pixelventures.pixelswitch.needsReauthAccounts` (`:97,1483-1503`) | `[String]` of account UUIDs, sorted | `restoreNeedsReauth()` | `persistNeedsReauth()` | PixelSwitch |
| S9 | Settings (not credentials) | `UserDefaults`; `LegacyMigration` copies the forked app's domain `me.xueshi.ccswitcher` once (`LegacyMigration.swift:20-56`) | plist | - | - | PixelSwitch |

Also read, not stored: `claude auth status` stdout, a JSON object decoded as `AuthStatus` (`Models/Account.swift:127-134`): `loggedIn`, `authMethod`, `apiProvider`, `email`, `orgId`, `orgName`, `subscriptionType`. `email` is present only when `authMethod == "claude.ai"` (`:138-146`; `ARCHITECTURE.md:233-243`).

In-memory only (lost on relaunch, deliberately): the 401/429 guard state (`UsageRequestGuard`, `AppState.swift:86-93`); only the needs-reauth set is persisted.

Network endpoints used on the credential path: `POST https://console.anthropic.com/v1/oauth/token` (refresh), `GET https://api.anthropic.com/api/oauth/profile` (identity), `GET https://api.anthropic.com/api/oauth/usage` (usage; other recon covers it). Both GETs send `Authorization: Bearer <accessToken>` and `anthropic-beta: oauth-2025-04-20` (`ClaudeService.swift:236-238,303-306`); the profile call also sets a 15 s timeout (`:304`).

### 1.2 Credential JSON shape (S1, S3, `AccountBackup.token`)

Sample in `ARCHITECTURE.md:43-60` (values masked there):

```
{ "claudeAiOauth": { "accessToken": "sk-ant-oat01-...", "refreshToken": "sk-ant-ort01-...",
                     "expiresAt": <ms since 1970, number>, "scopes": ["user:file_upload","user:inference","user:mcp_servers",
                     "user:profile","user:sessions:claude_code"], "subscriptionType": "pro", "rateLimitTier": "default_claude_ai" },
  "mcpOAuth": { ... }   // present when MCP servers are logged in; key named in ClaudeCredentialMerge.swift:5-6
}
```

PixelSwitch's code reads exactly four fields of `claudeAiOauth`: `accessToken` (must be non-empty), `refreshToken` (empty counts as absent), `expiresAt` (as `NSNumber`, milliseconds), and on refresh writes `accessToken`, `expiresAt`, `refreshToken` (`CredentialOwnership.swift:30-37`; `ClaudeService.swift:409-413`). Every other key is carried through untouched, so unknown fields survive.

**[CC bundle]** Claude Code 2.1.307 writes more than the sample shows: `accessToken, refreshToken, expiresAt, refreshTokenExpiresAt, scopes, subscriptionType, subscriptionTypeSource, subscriptionTypeVerifiedAt, rateLimitTier, clientId`. PixelSwitch's own refresh never updates `refreshTokenExpiresAt`, `subscriptionType*` or `clientId`, and ignores the response's `refresh_token_expires_in`. Whether a stale `refreshTokenExpiresAt` in a PixelSwitch-refreshed backup matters to Claude Code is **not known**; flagged as risk R7.

### 1.3 `oauthAccount` shape (S2 and `AccountBackup.oauthAccount`)

PixelSwitch treats it as an opaque dictionary and copies it whole. It reads only two keys: `accountUuid` and `emailAddress` (`AppState.swift:898,927-928`; `ClaudeService.swift:487-488,614-615,591`). Key names on this Mac **[this Mac]** (values not read): `accountUuid, emailAddress, organizationUuid, hasExtraUsageEnabled, billingType, accountCreatedAt, subscriptionCreatedAt, ccOnboardingFlags (object), claudeCodeTrialEndsAt, claudeCodeTrialDurationDays, seatTier, displayName, fullName, profileFetchedAt (number), organizationRole, workspaceRole, organizationName, organizationType, organizationRateLimitTier, userRateLimitTier`. Null values occur, so the port must preserve JSON `null`, bool, int, double, string, array, object (the `AnyCodable` set, `KeychainService.swift:17-51`). Verified equality on 2026-09-22 between `/api/oauth/profile` `account.uuid` and `oauthAccount.accountUuid` (`CredentialOwnership.swift:77-80`).

### 1.4 `Account` record (S7)

`Models/Account.swift:31-46`, synthesized Codable, so keys equal property names and nil optionals are omitted: `id` (UUID string), `email`, `displayName`, `provider` (raw value `"Claude Code"|"Gemini"|"Codex"`, `:5-8`), `orgName`, `subscriptionType`, `isActive`, `lastUsed` (Date), `customLabel`, `switchThreshold` (Double, 0 = Manual only, nil = default). **Gotcha for any cross-platform import/export:** `JSONEncoder()` with no strategy encodes `Date` as a Double of seconds since 2001-01-01 (`AppState.swift:1849`). **The array order IS the priority order** ("My order"; `Models/AccountOrder.swift:3-6`, `AppState.swift:711-736`).

---

## 2. Who is the live owner (answer to b)

Two layers, in this order each refresh cycle (`AppState.swift:171-209`):

**Layer 1, weak, drives `isActive` only.** `claude auth status` (spawned `claude auth status`, 30 s timeout, `ClaudeService.swift:204-214`) -> `updateActiveAccount(from:)` (`AppState.swift:1858-1887`) flips `isActive` to the account whose `email` equals the CLI's `email` (exact string match, `:1861`). The CLI derives that email from `~/.claude.json` (`CredentialOwnership.swift:5-6`), which running sessions can leave wrong. If the CLI email matches no account and accounts exist it only logs (`:1884-1886`). If `accounts` is EMPTY it auto-creates the first account from the CLI and spawns `captureCurrentCredentials` (`:1870-1883`).

**Layer 2, authoritative, the only thing that licenses a credential write.** `provenOwner(ofCredential:believedAccount:)` (`:918-923`):
1. parse the live credential's `claudeAiOauth` (`CredentialOwnership.login`, `:30-37`);
2. `accountIdentity(forAccessToken:expiresAt:account:)` = `GET /api/oauth/profile`, but only if `UsageRequestGuard.mayUse` allows it, i.e. the token is not expired (or within 60 s of it), was not 401'd, and is not inside a 429 park or 20-minute cool-down (`ClaudeService.swift:297-301`; `UsageRequestGuard.swift:133-144`). Response read: `account.uuid`, `account.email` or `account.email_address` (`:317-329`);
3. `account(matching:)` (`AppState.swift:893-908`) finds the one Claude Code account whose saved backup's `oauthAccount.accountUuid` equals the API uuid, and/or whose `email` equals the API email (case-insensitive). If both exist and name different accounts, or either is ambiguous, the answer is nil.

If any step fails the owner is **unknown (nil)**, not "different". Unknown blocks every write that depends on proof (section 6) but does not block the switch itself (see section 3, step 4, and risk R2).

`reconcileLiveLogin()` (`:1037-1075`) applies layer 2 every cycle: if the proven owner differs from `activeAccount` it calls `adoptActive(owner)` (`:1048-1051`, `1023-1030`), so the app follows the Keychain even when the user ran `claude auth login` by hand.

Account matching never uses lineage (same refresh token) as proof: lineage can only REFUSE something (`AppState.swift:866-871`).

---

## 3. Switch, step by step, in order (`performSwitch`, `AppState.swift:779-864`; `ClaudeService.switchAccount`, `:461-574`)

| Step | What | Where | Reads | Writes |
|---|---|---|---|---|
| 0 | Preconditions (1.9): an `activeAccount` must exist (else `ActionError.noActiveAccount`). **Superseded in 2.0 by spec D20 revision 4:** only an empty account list refuses; with no active account a switch may overwrite an unproven live login only when it is provably disposable (see the spec and `LiveLoginDisposal` in `PixelSwitch/Services/AccountRemoval.swift`); `!isSwitching && !isLoggingIn` (else `.busy`); set `isSwitching`, `switchingTo` | `AppState.swift:780-797` | - | in-memory |
| 1 | Read live credential, prove owner (layer 2). If owner is the target, or owner unknown and the target is already `activeAccount`: log "No switch needed", adopt if needed, return | `:802-812` | S1, profile API | - |
| 2 | `validatedTargetBackup(for:)`: look up the target's backup (`missing` vs `storeUnavailable` are different errors); check the guard; renew if expired/refused; ask the API whose login it is; compare to the target; compare saved `oauthAccount` details; else lineage check against other accounts' saved logins | `:821-827,936-1020` | S4, token endpoint (maybe), profile API (maybe) | S4 only if a renewal happens |
| 3 | Set `lastAutoSwitchAt = now` (restarts the 300 s auto-switch cooldown, `:129-130,832`); `isLoading = true` | `:832-834` | - | in-memory |
| 4 | **Save the leaving login under its owner** (only if owner proven AND the Keychain still holds the credential that was proven, or the API re-proves the new one). Identity details saved are `~/.claude.json`'s if they belong to the owner, else the owner's existing saved ones, else skip | `ClaudeService.switchAccount` Step 1, `:466-501` | S1 again, S2, S4, profile API (only if the credential changed in between) | S4 |
| 5 | Build the credential to write: `claudeAiOauth` from the target backup + every OTHER key from the live item (`mcpOAuth` etc.). If the live item is missing/not JSON or the target has no `claudeAiOauth`, write the target backup verbatim | `ClaudeCredentialMerge.swift:20-36`; `ClaudeService.swift:509-518` | S1 (third read) | - |
| 6 | **Write the Keychain item** (`writeClaudeToken`): `security -i` on stdin when the whole command line fits 4,095 bytes, else hex on argv; `add-generic-password -U` first, read back and compare bytes; if the read-back differs, `delete-generic-password` then `add-generic-password`, read back again | `KeychainService.swift:133-155`; `ClaudeTokenWriter.swift:65-82` | S1 (read-back) | S1 |
| 7 | **Write `oauthAccount`** from the target backup into `~/.claude.json` (whole file re-encoded) | `ClaudeService.swift:523-526`; `KeychainService.swift:188-209` | S2 | S2 |
| 8 | Bump mtime of `~/.claude/.credentials.json` if it exists (so running sessions drop their cache and re-read) | `ClaudeService.swift:531`; `ClaudeCredentialsFile.swift:34-43` | S3 stat | S3 mtime |
| 9 | Verify: `claude auth status`. Logged in + `email == target.email` = success. Logged in but no `email` (a higher-priority credential source shadows the stored login) = compare Keychain access token and `~/.claude.json` email to the target's, success with `shadowedBy` warning. Timeout (30 s) = same store comparison. Wrong email = `switchWrongAccount` error | `:533-573,579-597` | CLI, S1, S2 | - |
| 10 | App state: every `isActive` flag, `lastUsed`, `activeAccount`, `saveAccounts()`; `noteCredentialChanged` for the target and for the old owner; `await refresh()` | `AppState.swift:840-853` | - | S7 |

Failure behaviour: Step 6 or 7 failure throws (`keychainWriteFailed`, `oauthAccountWriteFailed`) with **no rollback**: if S1 was written and S2 fails, Claude Code holds the new login and `~/.claude.json` still names the old account (the next refresh's `reconcileLiveLogin` restores the identity from the backup, `:1058-1061`). Step 9 wrong-account also leaves the new credentials in place.

Concurrency rules: `isSwitching` is the single mutex for credential mutation across manual switch, auto-switch, and sign-in (`AppState.swift:108-113,341-352,788-797`); `KeychainService.storeLock` serialises read-modify-write of the whole S4 item (`:67-72`); `renewalsInFlight` makes concurrent renewals of one saved login join one task (`:1562-1572`).

---

## 4. Other writers of the stores

### 4.1 `addCurrentAccount` (`performAddCurrentAccount`, `AppState.swift:271-328`)
Requires Claude installed (`:273`), `claude auth status` logged in with an `email` (`:285-292`), email not already present by exact match (`:295`). Builds an `Account` (new UUID), then `captureCurrentCredentials(for:)` (`ClaudeService.swift:603-647`): reads S1 and S2, then (a) if the API can name the token's owner, the owner's email must equal `account.email` (case-insensitive) AND `~/.claude.json`'s `accountUuid`/`emailAddress` must belong to that owner, else REFUSE (`:616-625`); (b) if the API cannot answer, `~/.claude.json`'s email must equal `account.email` and an existing different-grant backup for this account must not be replaced (`:626-641`). Then `saveAccountBackup`. First account becomes active (`:318-322`).

### 4.2 Sign-in (`claude auth login`), `AppState.swift:340-651`
`backUpBeforeSignIn` (`:420-439`) first captures the CURRENT active account's login (new-account purpose, or re-auth of a different account), because `claude auth login` overwrites S1/S2. The CLI runs with `BROWSER` pointed at a helper script (`ARCHITECTURE.md:259`); a 1 s sleep lets the Keychain settle (`:444`). Afterwards `finishNewAccountSignIn` (`:454-567`) classifies `claude auth status` as notLoggedIn / noIdentity / existing / new (`Services/SignInRules.swift`), captures the credential for the account (refusing wrong owner via 4.1), marks it active, grants a fresh 300 s auto-switch cooldown (`:514,556`). `finishReauthentication` (`:570-651`) requires the logged-in email to equal the expected email EXACTLY (`SignInRules.swift`, `.wrongAccount`), then captures. `noteCredentialChanged` clears the old token's park/refusal/needs-reauth (`:1462-1470`).

### 4.3 `removeAccount` (`AppState.swift:738-760`)
Deletes the backup (`:740`), removes the account, drops its caches and guard state (`:744-749`), persists needs-reauth, and if it was active picks `AccountOrder.fallback(from:)` (first account that is not Manual only, else first; `Models/AccountOrder.swift:40-42`) and schedules `switchTo`. GUI trash button has no confirmation and no busy check (`Views/AccountSwitcherView.swift:218-227`); the control API checks `refuseWhileBusy` (`Control/AppController.swift:154`). See section 8 for the defect.

### 4.4 Reconcile, audit, renewal (every refresh cycle, `AppState.swift:199-209`)
- `diagnoseTokenHealth()` (`:1745-1770`): logging only (backup existence, email in the live `oauthAccount`).
- `auditSavedLogins()` (`:1082-1108`): once per launch for every saved login, afterwards only logins that share a grant: ask the API who owns each saved login; if it provably belongs to a DIFFERENT known account, DELETE that backup and show the re-sign message. Skips accounts flagged needs-reauth.
- `reconcileLiveLogin()` (`:1037-1075`): see 2 and 5.
- `renewBackupInPlace` (`:1587-1658`): see 5.

---

## 5. Token refresh and rotation (answers c and d)

### 5.1 Capturing a token that Claude Code refreshed (answer c)

PixelSwitch has no file watcher, no Keychain notification and no hook into Claude Code. Capture is **polling plus three event-driven snapshots**:

1. **Every refresh cycle** (`Timer` with `refreshInterval`, default 300 s, `PixelSwitchApp.swift:28,69`; `AppState.startAutoRefresh` default 300, `:232`): `reconcileLiveLogin` (`:1037-1075`). It reads S1, proves the owner via the API, re-checks that S1 is unchanged after the await (`:1043-1047`), then if the owner's saved `token` string differs from the live credential it saves the live credential under the owner (`:1063-1074`) with identity details taken from `~/.claude.json` if they belong to the owner, else from the existing backup if they do (`:1064`). The comparison is on the whole string (`backup.token != credential`), so a change only in `mcpOAuth` also re-saves (logged as "other parts changed", `:1068`). When the Claude access token changed it calls `noteCredentialChanged(for:)` so the account is read first next cycle.
2. **Before every switch** (Step 4 above): the freshest copy of the leaving login is saved right before it is overwritten.
3. **Before a sign-in** (`backUpBeforeSignIn`, `:420-439`).
4. After a sign-in/re-auth (`captureCurrentCredentials`, 4.1/4.2).

Gaps, all from the code (inferred, not observed):
- **Reconcile and the switch snapshot both need the API to prove the owner.** An expired live access token is never sent (`UsageRequestGuard.mayUse`), so with Claude Code idle the owner is unknown, reconcile returns at `:1039`, and a switch at that moment skips Step 4 ("owner not proven", `ClaudeService.swift:499-501`). If Claude Code had refreshed and rotated the refresh token since the last reconcile (up to one cycle, 300 s), the leaving account's backup keeps the OLD refresh token (risk R2).
- **No coordination with Claude Code's refresh lock.** **[CC bundle]** Claude Code takes a `proper-lockfile`-style lock `<configDir>/.oauth_refresh.lock` (stale 60 s, update 5 s, plus a legacy `.lock` file and a `.oauth_refresh.lock.owner` file) around its refresh, re-reads the stored credentials under the lock, and saves the result with a compare-and-swap that only writes if the stored refresh token is `""` or equals the one it posted; otherwise it "adopts the newer write" and drops its own result. PixelSwitch does not take that lock. A PixelSwitch switch that lands inside Claude Code's refresh therefore does not corrupt the file (the CAS refuses), but the refresh token Claude Code just spent on the leaving account is lost: the backup saved in Step 4 holds the pre-refresh token that the server has now rotated (risk R3).
- **[CC bundle]** On an `invalid_grant`-class failure Claude Code marks the refresh token dead and rewrites the live credential with `refreshToken:""`, `accessToken:""`, `expiresAt:0` ("cleared on disk"). `CredentialOwnership.login(fromCredential:)` returns nil for an empty `accessToken` (`:34`), so reconcile skips it. But `captureCurrentCredentials` takes its "API cannot answer" branch for such a credential (`login(fromCredential:)` is nil, `ClaudeService.swift:616` falls to `:626`), and the same-grant protection at `:635-641` requires `liveLogin` to be non-nil, so it is skipped: a blank live credential can be saved over a good backup if `~/.claude.json` still names the account (risk R4).

### 5.2 Refresh-token rotation for saved (non-active) logins

`refreshBackupInPlace` (`:1566-1585`) wraps `renewBackupInPlace` (`:1587-1658`) with one in-flight task per account. Order inside `renewBackupInPlace`:

1. Look up the backup. `missing` -> `.noBackup`; store unreadable -> `.storeUnavailable` (`:1591-1600`).
2. **Never spend a refresh token that is not provably this account's own** (`:1602-1614`): if the saved login is the same grant as the LIVE login (same access token or same refresh token, `CredentialOwnership.sameGrant`, `CredentialOwnership.swift:43-47`) or as any other account's saved login, return `.sameGrantAsAnotherLogin` without any request (`BackupRefreshOutcome.localRefusal`, `UsageRequestGuard.swift:377-379`). This outcome does NOT flag needs-reauth.
3. **Prove the store is writable BEFORE spending the token** (`:1616-1626`): re-save the backup just read; if that fails return `.storeUnavailable`. Rationale in the comment: a rotation that cannot be persisted kills the account.
4. POST the refresh (5.3).
5. `.success(refreshed)`: ask the API who owns the NEW access token; if it belongs to a different known account, delete this backup and return `.belongsToAnotherAccount(email)` (`:1632-1638`). Else `saveAccountBackup(token: refreshed, oauthAccount: backup.oauthAccount, ...)`; on failure sleep 1 s and retry once (`:1639-1646`); if both fail return `.rotationLost` (the old refresh token is spent, the new one is gone, only re-authentication helps; deliberately not kept in memory, `UsageRequestGuard.swift:356-362`).
6. `.rejected` -> `.grantRejected`; `.transient` -> `.unreachable` (`:1650-1656`).

Settling (`:1575-1583`): `.refreshed` -> `noteCredentialChanged(readNext: false)`; any outcome with `requiresReauth` (`.grantRejected`, `.rotationLost`, `.belongsToAnotherAccount`, `UsageRequestGuard.swift:367-372`) -> `flagNeedsReauth` -> `UsageRequestGuard.markNeedsReauth` + persisted (S8) + the re-sign message on the card (`:1474-1479`). A needs-reauth account sends nothing and is not switchable (`isSwitchable`, `:1115-1121`); the flag clears when its stored token changes (`UsageRequestGuard.swift:267-284`).

When a renewal happens: only from `readUsage` when the guard says `.refreshFirst` (saved login expired or 401'd, `UsageRequestGuard.swift:124-126`; `AppState.swift:1375-1377`) or after a 401 for a non-active account (`:1397-1403`), and from `validatedTargetBackup` before a switch (`:953-1001`). Never on a timer of its own, never for the live login (rule f, `UsageRequestGuard.swift:55-56`).

### 5.3 The refresh request (answer d), `ClaudeService.refreshOAuthCredentials`, `:360-430`

| Item | Value |
|---|---|
| Method, URL | `POST https://console.anthropic.com/v1/oauth/token` (`:340`) |
| Client id | `9d1c250a-e61b-44d9-88ed-5944d1962f5e` (`:339`, "Claude Code's public OAuth client id", PKCE public client) |
| Headers | `Content-Type: application/json` only (`:372`). No `anthropic-beta`, no `User-Agent` set by the code (URLSession's default is sent) |
| Body (JSON) | `{"grant_type":"refresh_token","refresh_token":<claudeAiOauth.refreshToken>,"client_id":<id>}` (`:373-377`). **No `scope` field.** |
| Precondition | the stored credential parses, has `claudeAiOauth.refreshToken` non-empty, else returns `.rejected` immediately with no request (`:361-368`) |
| Timeout | none set (URLSession default 60 s) |
| Success test | HTTP 200 AND JSON with `access_token` (String) AND `expires_in` (read as `Double`) (`:382-385`) |
| What is saved | into a COPY of the stored credential JSON: `claudeAiOauth.accessToken = access_token`; `claudeAiOauth.expiresAt = Int64(now_ms) + Int64(expires_in * 1000)`; `claudeAiOauth.refreshToken = refresh_token` ONLY if the response has a non-empty one (`:409-413`); everything else untouched, including `scopes` (deliberately NOT rewritten, because Claude Code decides it recognises a login by `scopes` containing `user:inference`, comment `:414-420`), `mcpOAuth`, `subscriptionType`, `rateLimitTier` |
| Output | re-serialised with plain `JSONSerialization.data(withJSONObject:)` (`:422`), so key order and `/` escaping may differ from Claude Code's own output |
| Failure classification | status != 200 or unparseable: log status + allow-listed OAuth `error` code only, never the body (`:387-396`). `200` that fails OUR parse -> `.transient`. Non-200: `error == "invalid_grant"` -> `.rejected`; anything else (5xx, 429, 408, `invalid_request`...) -> `.transient` (`:405-406`). Network error -> `.transient` (`:426-429`) |
| Observed | **[log]** recent `Access token refreshed, valid for ...` lines through 2026-10-24T19:26Z, and one `HTTP 400 error=invalid_grant` at 2026-10-22T09:18Z. The endpoint works today |

**[CC bundle]** What Claude Code 2.1.307 itself sends: `POST` to `TOKEN_URL = https://platform.claude.com/v1/oauth/token`, JSON `{grant_type:"refresh_token", refresh_token, client_id (CLIENT_ID = 9d1c250a-e61b-44d9-88ed-5944d1962f5e, same), scope: scopes.join(" ")}`, `Content-Type: application/json`, 30 s timeout; reads `access_token`, `refresh_token` (falling back to the old one if absent), `expires_in`, `refresh_token_expires_in`, `scope`. The string `console.anthropic.com/v1/oauth/token` does not occur in the binary. So for the Linux build: same client id, same JSON, but prefer to confirm which host to use (R5).

---

## 6. Every guard against saving one account's token under another (answer e)

Numbered so the spec can cite them. "Proof" = Anthropic's API named the token's owner.

| # | Guard | Where | Effect |
|---|---|---|---|
| G1 | **Switch snapshot needs proof**: the leaving login is saved only if the live owner was proven AND the Keychain still holds the verified credential (same grant), or the API re-proves the changed one | `ClaudeService.swift:466-501` | else Step 4 is skipped; nothing overwritten |
| G2 | **Identity details must belong to the owner** before being saved with the token: `~/.claude.json`'s `oauthAccount` if its `accountUuid` (if both sides have one) else its `emailAddress` matches; else the existing saved details if they match; else no backup | `:484-498`; rule `CredentialOwnership.detailsBelong`, `CredentialOwnership.swift:81-85` (permanent id decides when both sides have one; missing info on both sides is never a match) | stops "account A's token with account B's `oauthAccount`" |
| G3 | **`captureCurrentCredentials` refuses** a live login whose API owner's email differs from the account, or whose `~/.claude.json` does not describe that owner | `ClaudeService.swift:616-625` | add-account, sign-in, re-auth |
| G4 | **Without the API**, capture requires `~/.claude.json`'s email to equal the account's, and refuses to replace an existing DIFFERENT-grant backup | `:626-641` | has the blank-credential hole R4 |
| G5 | **Reconcile saves only for the proven owner**, re-checking that S1 did not change during the API call and that no switch/login started | `AppState.swift:1039-1047,1063-1074` | |
| G6 | **Target validation before a switch**: API names the target backup's owner; must equal the target account; its saved `oauthAccount` must belong too; if the API cannot answer, a login that is also saved under ANOTHER account (lineage) is refused | `:1003-1019` | error text "belongs to ... Re-authenticate" |
| G7 | **Audit** deletes any saved login the API proves belongs to a different known account | `:1082-1108` | re-checks nothing moved, same grant, before acting |
| G8 | **Refresh never spends a token that is the live login's or another account's** (`sameGrant` / `lineageMatches`) | `:1606-1614` | no request sent |
| G9 | **Post-refresh ownership check** on the new access token; delete the backup if it belongs to someone else | `:1632-1638` | |
| G10 | **Proven-owner-or-nothing in the lineage helpers**: lineage "only ever REFUSES, never writes" | `:866-871`; `CredentialOwnership.swift:39-68` | a refreshed login shares neither token with its older self, so a miss is "unknown", never "different" (`:39-42`) |
| G11 | **Store-write guard**: `saveAccountBackup`/`removeAccountBackup` REFUSE when the store was unreadable, because writing a mutated copy would replace every account's credentials | `KeychainService.swift:219-227,298-303`; three-way `BackupStoreLoad` (`:349-353`) | |
| G12 | **Legacy-source guard**: an unreadable legacy item/file returns `.failed`, never `.empty` | `:438-446,454-477` | |
| G13 | **Single mutation lane** (`isSwitching`, `isLoggingIn`, `storeLock`, `renewalsInFlight`) | `AppState.swift:108-113`; `KeychainService.swift:72`; `AppState.swift:1562` | a sign-in entering mid-switch once backed up the wrong credential |
| G14 | **Merge guard**: only `claudeAiOauth` comes from the backup | `ClaudeCredentialMerge.swift:20-36` | MCP logins are not overwritten with older copies |
| G15 | **Verification after the write**: byte read-back of S1 (`ClaudeTokenWriter.swift:67-81`), `claude auth status` email, or store comparison | `ClaudeService.swift:533-597` | |

Incident that justifies G1-G6 (recorded in `CredentialOwnership.swift:7-10`): on 2026-09-21 PixelSwitch backed up account A's login under account B, because a running session had left `~/.claude.json` naming one account while the Keychain held another's token; switching to B then silently used A's token.

---

## 7. macOS-specific versus portable (answer f)

### 7.1 macOS-only, needs a Linux replacement

| macOS thing | Where | Linux equivalent / note |
|---|---|---|
| Read live login via `/usr/bin/security find-generic-password -s "Claude Code-credentials" -a <user> -w` | `KeychainService.swift:112-129,600-624,656-675` | Read `~/.claude/.credentials.json` (JSON text, same object). Respect `CLAUDE_CONFIG_DIR`? PixelSwitch ignores it by design on Mac (`ClaudeCredentialsFile.swift:22-24`); on Linux decide explicitly |
| Write live login via `security -i` stdin / hex argv, `-U` then delete+add, read-back | `ClaudeTokenWriter.swift:1-91` | Atomic file write (temp in same dir + `rename`) at mode 0600, then read-back and compare, same contract (`write -> read back equals secret`). The 4,095-byte `security -i` limit and the hex transport do not apply |
| Keychain ACL/partition (`apple-tool:`) concerns | `ClaudeTokenWriter.swift:14-17` | n/a |
| Mtime bump of `.credentials.json` to make running sessions re-read | `ClaudeCredentialsFile.swift:3-14,34-43` | On Linux the file IS the store and the switch write changes its mtime, so the bump is implicit; **verify on the laptop** that a running `claude` re-reads after an external rewrite (the Mac comment describes 2.1.x `stat()` behaviour; I could not locate the staleness check in the minified bundle) |
| Backup store in the Keychain via `SecItem*` | `KeychainService.swift:360-370,509-596` | Options: Secret Service (libsecret/`secret-tool`) needs an unlocked D-Bus keyring session, which an SSH/Tailscale login to the test laptop will not have; or a 0600 file. The legacy-file format `[uuid: {token, oauthAccount}]` is already parsed by `loadBackupStore` (`:454-487`) and is what Claude Code itself does for its own secret (plaintext 0600 **[CC bundle]**) |
| `UserDefaults` for accounts, needs-reauth, settings | `AppState.swift:67,97,1809-1856` | JSON file under `~/.config/pixelswitch/` or similar. Mind the `Date` encoding (1.4) and that array order = priority |
| `NSUserName()`, `NSHomeDirectory()` | `KeychainService.swift:75-77` | `$USER` and `$HOME`; only the Keychain account name used the user name |
| Legacy migrations from `me.xueshi.ccswitcher` | `KeychainService.swift:74-108,423-487`; `LegacyMigration.swift`; `AppState.swift:1813-1845` | Not needed on Linux (no installed base) |
| `claude` discovery via launchd-bare PATH fixups, `/opt/homebrew/bin` | `ClaudeProcessEnvironment.swift:7-27`; `ClaudeService.swift:83-200` | `~/.local/bin`, `~/.npm-global/bin`, `/usr/local/bin`, `~/.nvm/...`; the extra-PATH idea carries over because a systemd user service or `.desktop` launch also has a bare PATH |
| BROWSER helper script for sign-in links | `ARCHITECTURE.md:259` | Same mechanism works with `xdg-open` semantics; `claude auth login` is the same CLI **[CC bundle shows it present]** |

### 7.2 Portable logic that should be reproduced exactly

`CredentialOwnership` (all of it, pure), `ClaudeCredentialMerge`, `UsageRequestGuard` + `BackupRefreshOutcome` + `UsageNotice` (pure), `AccountOrder`, the refresh request and response handling (5.3), the profile-based owner proof (section 2), the switch order and G1-G15 (sections 3, 6), `SignInRules`, `AuthStatus` decoding. `Tests/UnitTests/*` already pin these (the `PIXELSWITCH_KEYCHAIN_TESTS=1` round trip is the only Keychain-bound test, `Tests/UnitTests/main.swift:394-`).

### 7.3 Linux facts from Claude Code itself **[CC bundle]**
- Plaintext store `<storageDir>/.credentials.json`; `storageDir` = `CLAUDE_SECURESTORAGE_CONFIG_DIR` if set, else the config dir (`CLAUDE_CONFIG_DIR` or `~/.claude`). Written as `mkdir`, write with mode `384` (0o600), then `chmod 384`. Warning text "Storing credentials in plaintext." Read = `JSON.parse` of the file.
- Global config: `<CLAUDE_CONFIG_DIR or $HOME>/.claude.json`, so on Linux with no override it is `~/.claude.json`, same as the Mac.
- Updates are read-modify-write of the whole object (`mutate((q) => ({...q, claudeAiOauth: ...}))`), so other top-level keys are preserved by Claude Code too.
- Refresh serialisation lock at `<configDir>/.oauth_refresh.lock` (+ `.lock`, `.oauth_refresh.lock.owner`).
- `secret-tool`/`kwallet-query` appear only in the Bash sandbox's deny list; no libsecret storage.
- To verify live on the Linux laptop before the spec is final (read-only checks): file exists after `claude auth login`; its key names; mode; whether a running session re-reads after an external rewrite; which refresh host the installed CLI version uses; whether `oauthAccount` is in `~/.claude.json` on Linux with the same key names.

---

## 8. The deferred defect: "removing the active account doesn't switch the live login" (answer g)

Source: `docs/SESSION-HANDOVER.md:69`. Confirmed against current code; the review quoted in `docs/superpowers/reviews/2026-09-26-manual-only-review.json` cites older line numbers.

### 8.1 Trace (current line numbers)
1. `removeAccount(X)` (`AppState.swift:738`) deletes X's backup (`:740`) and removes X from `accounts` (`:741`).
2. `:751-757`: `if account.isActive, let fallback = AccountOrder.fallback(from: accounts)`: sets `accounts[index].isActive = true` and `activeAccount = accounts[index]` (F) **before** the switch, then `Task { await switchTo(fallback) }`.
3. `performSwitch(to: F)`: `currentActive = activeAccount` is already F (`:780`).
4. `liveCredential` is still X's login (`:802`). `provenOwner` asks the API, which names X, but `account(matching:)` finds no match: X is no longer in `accounts` (email lookup) and X's backup (the `accountUuid` lookup) was just deleted (`:893-908`). `liveOwner = nil`.
5. `:808`: `liveOwner?.id == account.id || (liveOwner == nil && currentActive.id == account.id)` is **true** (second clause: nil owner and F already "active"). Logs "No switch needed" (`:809`) and returns. Nothing is written.
6. `reconcileLiveLogin` returns at `:1039` for the same nil owner; `updateActiveAccount` only logs "not in our list" (`:1884-1886`).
7. `usageLogin(for: F)` reads the Keychain for the active account (`:1311-1316`), so F's card shows X's numbers and auto-switch decides from them until the next real switch.
Data loss: none. Step 4 of the next switch is skipped for an unknown owner (G1), so F's backup is never overwritten with X's login. Impact: the user thinks they are on F while Claude Code bills X.

### 8.2 Where the fix goes, and what each option implies
- **Option A (handover's first)**: do not mark F active / do not assign `activeAccount = F` in `removeAccount`; leave `activeAccount` pointing at the removed account and let `performSwitch` assign F on success (`:840-846`). In `performSwitch`, with `currentActive` = X: `liveOwner == nil`, `currentActive.id (X) != F.id`, so the shortcut at `:808` is skipped and the switch proceeds: `validatedTargetBackup(F)`, `switchAccount(liveOwner: nil, ...)` skips the leaving-login backup (X is gone anyway), writes F's credential merged with the live keys, writes F's `oauthAccount`, verifies. Side effects to design for: until the switch finishes no account in `accounts` has `isActive`; if the switch FAILS (F has no backup, store unavailable, needs-reauth) PixelSwitch is left with `activeAccount` = a removed account, so the failure path must pick the next candidate or clear `activeAccount`.
- **Option B (handover's second)**: skip the "already live" shortcut when the owner is unknown, i.e. require `liveOwner?.id == account.id` for "no switch needed" and treat `liveOwner == nil` as "must write". Cheapest and local to `:808`, but it changes meaning for the normal case where the API is unreachable (expired token): `liveOwner == nil && currentActive.id == account.id` is today the legitimate "we are already on this account, API just can't say" shortcut, so B would make a user click on the already-active account rewrite the credential. A combined fix is safer: in `removeAccount` pass an explicit "live login belongs to a removed account" fact to `performSwitch` (e.g. a flag or capturing the removed account's `accountUuid` before deleting the backup so `account(matching:)` can say "owner removed"), and keep the shortcut for everything else.
- Not covered by either: `removeAccount` runs `switchTo` in a detached `Task` after `saveAccounts()`, with no busy check on the GUI path (`Views/AccountSwitcherView.swift:218-219`); if a sign-in or switch is in flight `performSwitch` throws `.busy` silently (`switchTo` swallows `.busy`, `:769`) and the removal has already happened.

### 8.3 Related defect found while tracing (not in the handover)
Removing the LAST account: `AccountOrder.fallback(from: [])` is nil (`Models/AccountOrder.swift:40-42`), so `activeAccount` stays set to the removed account (`activeAccount = nil` appears nowhere in `PixelSwitch/`; grep returned nothing), and on the next refresh `updateActiveAccount` sees `accounts.isEmpty` and **auto-creates a new account from the live CLI login** and captures it (`AppState.swift:1870-1883`). So "remove my only account" is undone within one cycle if Claude Code is still logged in. May be intended (it is the first-run path), but it is not documented as such.

---

## 9. Surprises and risks (ranked)

| Id | Risk / surprise | Evidence | Confidence |
|---|---|---|---|
| R1 | **Defect (g)** and its sibling 8.3 | section 8 | confirmed by trace; not run |
| R2 | A switch while the live access token is expired (Claude Code idle) cannot prove the owner, skips the leaving-login backup, and overwrites S1. If Claude Code rotated the refresh token since the last reconcile (up to 300 s), that account's backup is stale and will `invalid_grant` later | `ClaudeService.swift:461-501,483`; `AppState.swift:802-812`; `UsageRequestGuard.swift:133-144` | inferred; the 12 stale-live-token runs in the 429 investigation did not show Claude Code working on an expired token (`docs/investigations/2026-10-16-usage-429.md`) so the window may be rare |
| R3 | PixelSwitch does not take Claude Code's `.oauth_refresh.lock`; a switch inside Claude Code's refresh loses the spent refresh token of the leaving account (Claude Code's CAS refuses to overwrite, so no file corruption) | **[CC bundle]** `iGr` and `VQn` | inferred from minified code; small window |
| R4 | `captureCurrentCredentials` can save a blank credential (Claude Code blanks dead tokens) over a good backup when the API cannot answer and `~/.claude.json` still names the account | `ClaudeService.swift:616,626-641` with **[CC bundle]** `pGr` | inferred; needs a dead grant first |
| R5 | Refresh host differs from Claude Code 2.1.307's (`console.anthropic.com` vs `platform.claude.com`), and Claude Code sends `scope`; PixelSwitch does not | `ClaudeService.swift:340,373-377`; **[CC bundle]** | works today **[log]**; could change without notice |
| R6 | `ARCHITECTURE.md` token-flow text is stale | section 10 | confirmed |
| R7 | PixelSwitch-refreshed backups keep a stale `refreshTokenExpiresAt` and never record `refresh_token_expires_in` | `ClaudeService.swift:409-413`; **[CC bundle]** `Fw` | effect on Claude Code unknown |
| R8 | The backup store is ONE item/one JSON blob holding every account's full credential (plus a copy of this Mac's `mcpOAuth` per account); a write failure mid-update loses nothing (single atomic item) but every mutation rewrites everyone's secrets, and a Linux file store must reproduce the lock + atomic-rename discipline | `KeychainService.swift:67-72,562-596` | design note |
| R9 | `updateActiveAccount` trusts the CLI email (derived from `~/.claude.json`) to flip `isActive` every cycle, before the API-proven reconcile can correct it; between the two a transient wrong `isActive` can exist and is saved (`saveAccounts`, `:1868`) | `AppState.swift:192,1858-1869` vs `:206` | observed in code |
| R10 | Step 6 succeeded + Step 7 failed leaves a split state with no rollback; reconcile heals the identity only if the owner can be proven | `ClaudeService.swift:519-526`; `AppState.swift:1058-1061` | inferred |
| R11 | Switch Step 9 verifies with `claude auth status` (a process spawn, 30 s timeout) on the critical path; on Linux the CLI startup cost is unknown | `ClaudeService.swift:204-214,538-546` | note |
| R12 | `Account.lastUsed` date encoding (reference-date Double) breaks naive cross-platform account import | `AppState.swift:1849` | confirmed |

---

## 10. `ARCHITECTURE.md` versus the code (do not spec from the document)

| Document says | Code does |
|---|---|
| Backups are per-account Keychain items, service `com.pixelswitch.tokens`, account = UUID (`ARCHITECTURE.md:22-30,529`) | One item `ai.pixelventures.pixelswitch.backups` / `all-accounts`, via the Security framework, not the `security` CLI (`KeychainService.swift:322-330,360-370`). The CLI is used only for Claude's own item (`:598`) |
| Step 0 compares backup fingerprints, Step 1 verifies the source with `claude auth status` (`ARCHITECTURE.md:155-189`) | Ownership is proved by the profile API, not `claude auth status` (sections 2, 3) |
| Switch step 3 "Delete LIVE entry, then Write" (`:198`) | `add-generic-password -U` in place first, delete+add only if the read-back differs (`ClaudeTokenWriter.swift:61-82`) |
| Passive health check "every 30s refresh" with fingerprint comparison (`:460`) | Default refresh 300 s (`PixelSwitchApp.swift:28`); `diagnoseTokenHealth` only logs (`AppState.swift:1745-1770`) |
| Token JSON has no `mcpOAuth`; the whole item is replaced | The switch merges: only `claudeAiOauth` is replaced (`ClaudeCredentialMerge.swift`) |
| No mention of the 401/429 guard, needs-reauth, `~/.claude/.credentials.json` mtime bump, or in-place renewal of saved logins | Added in 1.2 to 1.9 (`docs/SESSION-HANDOVER.md:15-24`) |

`docs/SESSION-HANDOVER.md` and the code comments are accurate on these points.

---

## 11. Not found (searched, absent)

- Any file watcher, FSEvents/kqueue or Keychain notification for the live credential: none; capture is polling only.
- Any use of `CLAUDE_CONFIG_DIR` or `CLAUDE_SECURESTORAGE_CONFIG_DIR` in PixelSwitch: none (documented as intentional, `ClaudeCredentialsFile.swift:22-24`).
- Any code that reads or writes `~/.claude/.credentials.json` contents: none; mtime only.
- Any refresh of the LIVE login by PixelSwitch: removed in 1.9 ("delegated refresh", 0 recoveries in 13, `docs/investigations/2026-10-16-usage-429.md`).
- Any persistence of the 401/429 guard across launches: none by design.
- Any code that sets `activeAccount` to nil: none.
- A unit test for `removeAccount` or for `performSwitch`'s shortcut: none (`Tests/UnitTests/*` cover `AccountOrder.fallback` at `AutoSwitchRulesTests.swift:453-454` but not the removal flow).
- Another place that POSTs to the token endpoint besides `ClaudeService.refreshOAuthCredentials`: none.
- The Linux CLI's behaviour: not observed here (no Linux host was touched by this recon).
