# Recon: "needs authentication" state for PixelSwitch (never switch TO a dead login; leave an active one that went dead)

**Question asked (founder's words):** "when switching sometimes authentication has expired and the Claude sessions then get
stuck. The PixelSwitch app should check for this and not switch to an account that needs authentication. Furthermore it should
also check throughout the time an account is being used and if it detects that authentication is required again it
automatically switches accounts." (A) never switch TO an account whose stored login is dead; (B) while active, keep checking,
and switch away if it needs authentication.

**Date:** 2026-11-10. **Mode:** read-only. Nothing in the repo was edited, fetched, checked out or stashed.

## 0. READ THIS FIRST: the checkout is stale, and most of (A) already exists

| What | Value | How verified |
|---|---|---|
| Local checkout `/Users/ahmed/Documents/GitHub/PixelSwitch` | HEAD `3036e1f` = **1.8**, `git status` clean | `git log`, `git status` |
| `origin/main` | `50a5243` = **2.0** (104 commits ahead of the checkout; tags v1.9 and v2.0 exist only on the remote) | `git fetch --dry-run` (no state change), `git ls-remote origin main`, `gh api repos/.../compare/3036e1f...main` |
| Installed app on this Mac | **2.0, build 31** | `PlistBuddy -c "Print CFBundleShortVersionString" /Applications/PixelSwitch.app/Contents/Info.plist` |
| Tree I analysed | origin/main `50a5243`, extracted (not cloned) to `/private/tmp/claude-501/-Users-ahmed-Documents-GitHub-PixelSwitch/f704c667-ad7d-495a-8dda-60528d78d198/scratchpad/src-main` | `gh api .../tarball/50a5243` into a new empty dir |

All `AppState.swift` etc. line numbers below are **from that 2.0 tree** (`src-main/...`), not from the stale checkout (where
AppState is 1,792 lines; in 2.0 it is 2,334). Whoever builds this must first bring the checkout to origin/main.

What 2.0 already does for (A): 1.9 added `UsageRequestGuard` and a **persisted `needsReauth` flag per account** (set only when the
server answers `invalid_grant`, a rotation is lost, or the saved login belonged to another account), and `LoginGate.isSwitchable`
refuses such an account as a target (`CredentialOwnership.swift:151`, `AppState.swift:1481-1489`). 2.0 added the usable-login rule
D12 (a blank login is never saved, never written live). So "do not switch to an account the server already refused" is done.

What is NOT done, and is the real work:
- **(B) is wholly missing.** The 1.9 investigation lists it as known limit b: `docs/investigations/2026-10-16-usage-429.md:212-215`
  ("A revoked live login waits forever ... nothing flags the live login needs-reauth. Fix idea: ... when `claude auth status`
  says logged out, show the re-sign message"). I found a **live instance of it on this Mac today** (section 4.1): auto-switch was on,
  the active account's login went dead at 10:40Z, and nothing happened for 3 hours until a manual switch.
- **(A) has two residual holes** (section 2.4): a target whose access token is still valid but whose refresh grant is dead is
  not discovered before the switch; and a failed/unreachable renewal still "switches anyway".

## 1. How a switch works end to end (2.0)

Single funnel: `AppState.performSwitch(to:)` (`AppState.swift:1042`). Callers: `switchTo` (popover/double-click, `:1026`), auto-switch
(`:1644`), `AppController.switchAccount` for CLI/MCP (`Control/AppController.swift:82`), removal fallback (`:981`).

1. Guards: `!isSwitching`, `!isLoggingIn` (`:1061`); `isSwitching` + `switchingTo` set, cleared by `defer` (`:1065-1070`).
2. Who owns the live login: `keychain.readClaudeToken()` then `liveLookup` (`:1075-1082`, `:1249`) which asks Anthropic
   `GET /api/oauth/profile` with the live access token (`ClaudeService.accountIdentity`, `ClaudeService.swift:297`) and matches the
   returned uuid/email to saved identities (`account(matching:)`, `:1205`; uses `oauthAccount.accountUuid` inside each saved backup).
   **Never from `~/.claude.json`** (running sessions rewrite it; `CredentialOwnership.swift:3-13`). If the API cannot answer (token
   expired/refused/blank, guard says do not ask), owner is nil and `activeAccount` stays the "believed" owner.
3. `validatedTargetBackup(for:)` (`:1289`): backup lookup (`.missing`/`.storeUnavailable` are different errors), D12 usable-login check
   (`LoginGate.targetProblem`), then `requestGuard.decision` (`:1338`): `.needsReauth` refuse; `.refreshFirst` renew then ask;
   `.request` profile lookup, and renew only if that was a 401 (`:1343-1350`). Identity must match target; saved identity details must match.
4. `lastAutoSwitchAt = Date()` (cooldown restart for ANY switch, `:1142`).
5. `ClaudeService.switchAccount` (`ClaudeService.swift:471`): Step 1 save the live login under its PROVEN owner only (skipped when
   owner not proven; blank/unusable logins refused by `LoginGate.save` in `KeychainService.saveAccountBackup:220`). Step 3 write target:
   `ClaudeCredentialMerge.credentialForSwitch` puts the target's `claudeAiOauth` over the live item and keeps every other key (`mcpOAuth`)
   (`ClaudeCredentialMerge.swift:20`); written by `KeychainService.writeClaudeToken` -> `ClaudeTokenWriter` (`/usr/bin/security -i`, update-in-place
   then delete+add, read-back verified); then `~/.claude.json` `oauthAccount` replaced (`writeOAuthAccount`, `KeychainService.swift:188`); then
   `touchClaudeCredentialsFile()` bumps the plaintext credentials file mtime so running sessions re-read the Keychain (`:160`).
   Step 4 verify with `claude auth status` (email must equal target; falls back to a credential-store check when CLI shadowed/times out).
6. State: `isActive` flags, `activeAccount`, `saveAccounts()`, `noteCredentialChanged` for target and leaving owner (`:1151-1163`), then `refresh()`.

**What is written where:** live login = Keychain item service `Claude Code-credentials`, account = OS user name (`KeychainService.swift:60`);
identity = `~/.claude.json` key `oauthAccount`; saved logins = ONE Keychain item, service `ai.pixelventures.pixelswitch.backups`, account
`all-accounts`, JSON map `{accountId: {token, oauthAccount}}` (`KeychainService.swift:332,340`), guarded by `storeLock`.
**How the app knows which account owns the live login:** only by the profile API's answer (above), re-proven every refresh cycle in
`reconcileLiveLogin` (`:1399`); it also keeps the owner's backup current from the live login there (so a rotated refresh token is not lost).

## 2. Token refresh, and what happens today when it fails

### 2.1 Refresh mechanics
- Access tokens live **8 h** (`Access token refreshed, valid for 8h 0m`: 70 of 70 refreshes in the three local logs). `expiresAt` is epoch **ms** in the credential.
- Only **saved (non-active)** logins are ever refreshed by PixelSwitch: `ClaudeService.refreshOAuthCredentials` (`ClaudeService.swift:362`) POSTs
  `grant_type=refresh_token` to `https://console.anthropic.com/v1/oauth/token` with Claude Code's public client id; it NEVER touches the Keychain.
  Result: `.success(json)`, `.rejected` (status != 200 AND OAuth `error == "invalid_grant"`, or stored login has no refresh token; `:408`), `.transient` (everything else:
  5xx, 429, network, 200 with unparseable body). Only the allow-listed error code is logged, never the body.
- Wrapped by `renewBackupInPlace` (`AppState.swift:1998`) -> `BackupRefreshOutcome` (`UsageRequestGuard.swift:362-410`): `refreshed`, `grantRejected`,
  `sameGrantAsAnotherLogin` (local refusal, never flags), `unreachable`, `noBackup`, `storeUnavailable`, `belongsToAnotherAccount`, `rotationLost`.
  `requiresReauth` = grantRejected | rotationLost | belongsToAnotherAccount (`:397`). `refreshBackupInPlace` (`:1977`) settles it: `.refreshed` -> `noteCredentialChanged`;
  `requiresReauth` -> `flagNeedsReauth` (`:1885`).
- **The live login is never refreshed by PixelSwitch** (rule f, `UsageRequestGuard.swift:55-56`): Claude Code owns it and renews only when a session makes a model call. The
  1.9 "delegated refresh" through `claude auth status` was removed (13 runs, 0 recoveries; investigation lines 65-67, 152-153). ARCHITECTURE.md does not describe any of this.

### 2.2 How a failure surfaces (per path)
| Situation | What the app does now | Where it shows |
|---|---|---|
| Saved login, refresh answers 400 `invalid_grant` | `.grantRejected` -> `flagNeedsReauth`: guard flag (persisted as account ids in UserDefaults `ai.pixelventures.pixelswitch.needsReauthAccounts`, `AppState.swift:97,1894`), card message "Session expired. Re-authenticate (↻) to fix." (`showResign`, `:1762`, `isExpired: true`), reading cleared, not polled, `isSwitchable == false` | Usage card (yellow triangle, text only, **no button**), `↻` icon on the Accounts tab (`AccountSwitcherView.swift:208-216`), CLI `!` marker + `Note:` line, `pixelswitch accounts reauth <acct>` |
| Saved login, 401 on usage | guard `recordRejected` then renew-first (`requestUsage` `:1800-1814`) | same as above if renewal says `invalid_grant` |
| Saved login, transient (5xx/429/offline) | `.unreachable` -> "Can't reach Anthropic" card, reading kept, NOT flagged (`:1853-1857`) | card |
| **Live login expired or 401** | `.waitForClaudeCode`: nothing sent, card "Waiting for Claude Code to renew this login..." with `isRateLimited: true` (`:1752-1755`, mislabel) | card only |
| **Live login blanked by Claude Code (dead grant)** | `usageLogin(for:)` returns nil -> `readUsage` logs `No token for <email>, skipping` (`:1778-1781`) and returns nil. **No card message, no flag, no auto-switch.** `claude auth status` exits 1 -> `getAuthStatus failed` -> `errorMessage` ("Claude CLI error: exit 1", popover footer `MainMenuView.swift:344`) | log + generic footer |
| 403 on usage | `markNoSubscription` (`:1920`) -> `UsageErrorState.isNoSubscription`, reading cleared | card; engine `isUnusable` |

Existing per-account flags: `requestGuard.isNeedsReauth(id)` (persisted), `UsageErrorState.isExpired` (card says re-sign; transient, in-memory), `UsageErrorState.isNoSubscription`
(in-memory). There is NO per-account "live login is dead" flag.

### 2.3 The 1.6 `noSubscription` pattern (the model to copy), 2.0 form
- Detected at the **one** usage request site now: `requestUsage` (`AppState.swift:1797`; the handover's "three usage paths" is 1.8 wording; 1.9 funnelled polling, verification
  (`fetchUsageNow` -> `readUsage`, `:1713`) and Retry through `readUsage`/`requestUsage`). `.forbidden` -> `markNoSubscription` (`:1820-1827`).
- Consumed by `hasNoSubscription` (`:1495`) -> `evaluateAutoSwitch` passes `isUnusable: { hasNoSubscription($0) }` (`:1541`) and `activeUnusable` to `AutoSwitchEngine.cooldownHolds`
  (`:1522-1523`, `AutoSwitchEngine.swift:192`, bypasses the 5-minute cooldown). Engine: `windowPass` rule 0, `if isUnusable(active)` -> `rankedTargetsWindowed(... trigger: .noSubscription ...)`
  (`AutoSwitchEngine.swift:552-559`); candidates with `isUnusable` are never eligible (`:647`); eligibility = at least `drainMinimumRoom` (1 pt) under the candidate's own limits on session+weekly (`:475-477`).
- Auto-switch OFF: `evaluateAutoSwitch` begins `guard autoSwitchEnabled` (`:1517`), so the rule does nothing (verified in code).
- Manual only ACTIVE: still left; rule 0 runs before the manual-only handling (test `Tests/UnitTests/AutoSwitchRulesTests.swift:674`). Manual only candidate: never a target (`:676-677`).
- No candidate: logs "has no active subscription, and no other account has room; staying put" (`AppState.swift:1551-1552`).

### 2.4 Residual holes in (A) (verified by reading; not yet observed failing)
1. **Dead refresh grant behind a still-valid access token is invisible before the switch.** `validatedTargetBackup` renews only when the guard says `.refreshFirst` (expired by `expiresAt`
   or refused) or after a profile 401 (`AppState.swift:1338-1350`). A target with, say, 5 h of access token left and a revoked grant passes; ~5 h later Claude Code cannot renew, blanks the login
   (section 4) and the sessions stick. Fix point: one place, `validatedTargetBackup` (all switch paths share it).
2. `validatedTargetBackup` treats `.storeUnavailable`/`.unreachable` as "switching anyway" (`:1323-1327`).
3. The switch UI paths do not gate on `isSwitchable`: `UsageDashboardView.canSwitch(to:)` (`Views/UsageDashboardView.swift:349`) ignores it; `performSwitch` still refuses a known-dead target with the re-sign message (`:1339-1340`), so this is cosmetic.
4. The account you just LEFT is not verified. After a switch away from a dead live login its saved copy (same dead grant lineage, but with an unexpired access token) looks perfectly healthy and low-usage, so after the 5-minute cooldown it is the best auto-switch TARGET. It must be flagged (by a server verdict) right after the switch.

## 3. The periodic loop and which checks are safe on a non-active account

- Timer: `AppState.startAutoRefresh(interval:)` (`AppState.swift:232`), started from `PixelSwitchApp.swift:69`; interval = `@AppStorage("refreshInterval")`, default **300 s** (`PixelSwitchApp.swift:28`), choices 15/30/60/300/600 s
  (`Control/SettingsCatalog.swift:54`). (ARCHITECTURE.md's "every 30s" is wrong; the real log cadence is :x0:31 every 5 minutes.) Each tick: `refresh()` = `refreshData()` then `evaluateAutoSwitch()` (`:165-168`).
- `refreshData` (`:171`): `claude --version`, `claude auth status` (`getAuthStatus`, errors only set `errorMessage`), `diagnoseTokenHealth`, `auditSavedLogins`, `reconcileLiveLogin`, `fetchAllAccountUsage`, cost/activity parsing, widget.
- `fetchAllAccountUsage` (`:2079`): asks the guard about every account first; targets = **the active account (every cycle) + ONE inactive account** (a `readSoon` account first, else round-robin `usageFetchCursor`, `:2121-2130`). With N inactive accounts each is visited every N cycles (7 accounts: every 30 min). Stale samples are kept for the others.
- Engine question each tick: `evaluateAutoSwitch` (`:1516`) -> `AutoSwitchEngine.planWindowed(...)` with `isSwitchable`, `isUnusable`, per-account `windowRule`; then verifies the chosen target with at most ONE fresh read (`:1592-1608`).
- Inactive tokens are refreshed **only reactively**: when the guard says `.refreshFirst` (expiresAt within 60 s of now, or the token earned a 401) at that account's round-robin visit, on a `readSoon` visit, during target verification, or inside `validatedTargetBackup`. Never on a timer of its own. Observed: 70 silent refreshes in ~7.5 days of logs.

Safety table for a NON-active account (saved login, PixelSwitch holds the only live copy):
| Check | Safe? | Notes |
|---|---|---|
| Compare `expiresAt` to now | Yes, pure | Normal expiry is not an auth failure (8 h). `Login.isExpired` has 60 s slack (`CredentialOwnership.swift:23`) |
| `refreshTokenExpiresAt` in the past | Yes, pure | Spec 5.3: "Claude Code treats a past value as signed out" ([CC bundle] inference; not verified on a real credential, I did not read secrets) |
| Usage call (`/api/oauth/usage`) with stored access token | Yes, no rotation | A refused call (401) counts toward Anthropic's limiter: ~4th 401 per token/hour earns 429 `Retry-After: 3600`. Only ever via `UsageRequestGuard` |
| Profile call (`/api/oauth/profile`) | Yes, same limiter | same rule |
| Refresh grant (POST token endpoint) | **Yes for a saved login, with conditions** | Rotates the refresh token. Safe because it is persisted first (writable-store probe `:2042`) and only if the login is not the same grant as the live login or another account's (`localRefusal`, `UsageRequestGuard.swift:407`). Outside the usage/profile limiter. A rotation whose save fails twice = `rotationLost` (account must re-sign) |
| Any of the above on the ACTIVE account's refresh grant | **No** | Spending the live refresh token invalidates the copy Claude Code holds. Never do it |
| `claude auth status` | Yes, read-only | 147-279 ms; the investigation infers (from timing) it skips the network when the token is expired. Output shape when logged out is not captured anywhere (see section 4) |

## 4. What "authentication required" looks like, per signal

### 4.1 Live case study from this Mac's own log (verified)
`~/Library/Logs/PixelSwitch-app.log` (UTC), account names/emails deliberately omitted:
- 10:35:31Z last healthy cycle: live credential length 27081, `claude auth status` OK.
- **10:40:31Z first bad cycle:** `runClaude] Failed (exit 1)` -> `getAuthStatus failed: Claude CLI error: exit 1`; live credential read still succeeds but is now **26879 chars (202 shorter)**; `fetchUsage] No token for <active account>, skipping`.
- Same pair repeated every 5 minutes: `getAuthStatus failed` = 36 and `No token for` = 36 (counted with `rg -c`), last failure 13:35:31Z. **No `[autoSwitch]` line after 08:20Z** (the 08:20 auto-switch proves auto-switch was ON). No card message.
- 13:39:42Z a switch ran with no preceding `[autoSwitch]` line (so it was by hand or CLI); 13:45:31Z `getAuthStatus loggedIn=true`.
- Earlier the same day 02:30:33Z, an INACTIVE account's saved login hit `Refresh not applied (HTTP 400, error=invalid_grant, 72 bytes)`, was flagged `[needsReauth] ... must sign in again` and the app carried on correctly. So (A)'s existing detection works when the account is visited.
Reading: Claude Code clears a login it found dead (recon `docs/investigations/2026-10-24-linux-and-split-thresholds/recon/credentials.md:153`, "[CC bundle]": on an `invalid_grant`-class failure it rewrites the live credential with `refreshToken:""`, `accessToken:""`, `expiresAt:0`; spec D12 says the same). The 202-char drop at the exact moment `auth status` starts failing is consistent with that; I did not read the credential to prove it.

### 4.2 Signal table
| Signal | Meaning | Definitive? | Notes / false-positive risk |
|---|---|---|---|
| Live Keychain `claudeAiOauth` blank (accessToken and refreshToken empty, expiresAt 0) = `CredentialOwnership.unusableReason != nil` with the object present | Claude Code itself gave up on the login | **Yes (best signal for B)** | Local, zero network, zero cost. Do NOT use "claudeAiOauth missing": a Mac authenticating by env token has no such object. `readClaudeToken()` returning nil = unknown, never signed out |
| `claude auth status` exit != 0 while the Keychain item exists | CLI sees no usable login | Corroborating | Seen today co-occurring 36/36 with blank. Needs `runClaude` to expose the exit (currently collapses to `cliError("exit 1")` and discards stdout, `ClaudeService.swift` `runClaude`). Exit != 0 alone could be a broken CLI install |
| Refresh endpoint 400 `error=invalid_grant` (saved login) | Grant dead (rotated/revoked) | **Yes** (already `.rejected`) | 5xx, 429, 408, `invalid_request`, network, 200-with-bad-body are all `.transient`, correctly never "needs auth" (`ClaudeService.swift:408`) |
| `refreshTokenExpiresAt` in the past | Claude Code treats as signed out | Likely | Unverified here; add only after reading a real credential |
| HTTP 401 on `/api/oauth/usage` | Access token expired or revoked | **No** | Normal every 8 h; for a live login Claude Code renews it later (idle machine = long expired token, normal: investigation lines 45-50). Saved login: renew first. Body text not logged anywhere; whether it contains "OAuth token has expired" is unverified. `UsageRequestError.from` also maps a body containing `token_expired` (`UsageRequestError.swift`) |
| Stored `expiresAt` in the past | Access token expired | **No** | Idle Claude Code never renews; this is the biggest false-positive trap |
| 429 / 5xx / offline / timeout | Anthropic or network | Never | |
| `~/.claude/` markers | Claude Code writes no separate "dead login" file; `~/.claude/mcp-needs-auth-cache.json` exists but is for MCP servers (name collision). No `isApiErrorMessage` / "Please run /login" lines exist in the 3,219 transcripts modified since 2026-11-01 on this Mac (grep, count 0), so the transcript signal is unverified here | n/a | |

**Recommended minimal set.** (B, active/live): blank live `claudeAiOauth` (object present, both tokens blank) **AND** `claude auth status` failed (exit != 0), seen on **two reads at least ~15 s apart**
(or two consecutive cycles); any healthy read (usable credential, or `auth status` ok with `loggedIn:true`) resets it; a nil/unreadable Keychain read or a CLI timeout/launch failure is neutral (unknown). (A, targets): the existing server verdicts
(`invalid_grant` -> `needsReauth`) plus a **renewal probe before the switch** (section 5.3). Never use usage 401, expired `expiresAt`, 429, 5xx or network errors as "needs authentication".

## 5. Proposal (a proposal, not a decision)

### 5.1 Pure type, new file `PixelSwitch/Services/AuthHealth.swift` (add to `Tests/run-unit-tests.sh`; AppState is not in the unit harness)
```
enum LiveLoginVerdict { case healthy, waitingForClaudeCode, signedOutSeen, unknown }
enum CLIProbe { case loggedIn, failed, unavailable }          // unavailable = timeout / launch failure
AuthHealth.judge(credential: String?, cli: CLIProbe) -> LiveLoginVerdict
struct SignedOutConfirmation { mutating func observe(_ v: LiveLoginVerdict, at: Date) -> Bool }   // true when confirmed
enum TargetRenewalPolicy { static func mustRenewBeforeSwitch(login:, now:) -> Bool ; static func outcome(_ BackupRefreshOutcome) -> .proceed | .refuse(reSign) }
enum LeavingAccountVerdict { static func flag(after outcome: BackupRefreshOutcome) -> Bool }     // == outcome.requiresReauth
```
Rules: blank pair + `cli == .failed` -> signedOutSeen; blank + `.loggedIn` -> healthy (env-token shadowing); blank + `.unavailable` or nil credential -> unknown;
non-blank but expired/refused -> waitingForClaudeCode (the existing behaviour, NOT auth); confirmation needs 2 `signedOutSeen` at least 15 s apart with no healthy/blank-resetting read between.

### 5.2 Where B plugs in (and why)
- Detect in `refreshData` right after the `getAuthStatus` attempt (`AppState.swift:189-197`); capture `.failed` in the `catch` (today only `errorMessage`). Do the second read ~15 s later inside the same cycle so the reaction is at most one cycle plus 15 s (default 5 min). Optional later: a 60 s Keychain-only watcher (local `security` read ~50-100 ms, no network).
- Store the result in its **own** state, e.g. `@Published private(set) var liveLoginSignedOut: UUID?` (the account believed active), **not** only in `accountUsageErrors`: `fetchAllAccountUsage` clears `accountUsageErrors` for every polled account every cycle (`:2135-2137`) and `UsageRequestGuard.decision` deliberately clears a needs-reauth flag that holds no token for an ACTIVE account (`UsageRequestGuard.swift:121`). Clear it in `noteCredentialChanged` / a switch / a sign-in (a different live token appears).
- Engine input: **no engine edit (recommended).** Pass `isUnusable: { hasNoSubscription($0) || needsSignIn($0) }` (`:1541`) and `activeUnusable` likewise for `cooldownHolds` (`:1522`). The existing rule 0 already means exactly "leave this active account now, cooldown or not, even Manual only, for any account with >= 1 point of room under its own limits, ranked by the user's strategy". Cost: the `autoSwitched` event and `AutoSwitchRecord.trigger` still say `noSubscription`; give the AppState log its own line and (optionally) an additive `cause: "needsSignIn"` in the event data. Reason for not adding `Trigger.needsSignIn` now: it would have to be mirrored in `linux/pixelswitch/engine.py` (it already has `NO_SUBSCRIPTION`, `engine.py:62`), needs new conformance vectors (the 150 in `Tests/conformance/autoswitch-vectors.json` are frozen: "Nobody edits this file after lane V") and the frozen `Engine19Oracle`, and the Ultra change is editing the same engine.
- Auto-switch OFF: do nothing (same as 1.6, `:1517`), but still show the card state and the CLI marker. Manual only active: leave it anyway (same as 1.6). No candidate: log once per change "Active X needs sign-in, and no other account has room; staying put" (mirror `:1551`) and rely on the card's Sign in button. **Founder-level question to put on his decisions surface:** when nothing is under its threshold, should a dead login still move you to the least-used switchable account that is over its threshold? (1.6 precedent: no.)
- After the switch away (`:1644-1650`, success branch): run `refreshBackupInPlace(leavingAccount)` once and let the **server's verdict** flag it (`.grantRejected` -> `flagNeedsReauth`, `:1885`); do not flag blindly (the 1.9 decision: needs-reauth only when the server says so, investigation lines 157-160). Without this the account just left is the best target 5 minutes later (section 2.4 item 4). Order matters: flag after the switch so `usageLogin(for:)` reads the saved login, not the blank live one.
- Never save the blank live credential over the leaving account's backup: already enforced by `LoginGate.save` (`KeychainService.swift:220`) and by `switchAccount` step 1 skipping an unproven owner.

### 5.3 Where A plugs in
In `validatedTargetBackup` (`AppState.swift:1338`), before the `.request` branch: if `TargetRenewalPolicy.mustRenewBeforeSwitch` (recommend: always, or when `expiresAt` is less than ~7 h away, i.e. almost always) call `renewAndIdentify()`; `.grantRejected/.rotationLost/.belongsToAnotherAccount` -> refuse with the existing re-sign message and the flag is already set by `refreshBackupInPlace`; `.refreshed` -> proceed with a fresh 8 h token (also gives the session a full lifetime); `.unreachable/.storeUnavailable` -> keep "switch anyway" (offline means Claude Code cannot reach Anthropic either). Cost: one POST to the token endpoint per switch, outside the usage/profile limiter. Add the same probe for the verification read in `evaluateAutoSwitch` only through `performSwitch` (it already funnels there), so no second place.

### 5.4 UI
Card state "Needs sign-in" for ANY account whose state is needs-reauth/live-signed-out: new `UsageErrorState.offersSignIn` (beside `canRetry`, `offersStatusPage`, `AppState.swift:37-48`); button in `usageErrorActions` (`Views/UsageDashboardView.swift:370`) calling `appState.reauthenticateAccount(account)` (`AppState.swift:389`), which opens the existing window by itself because `SignInWindowController` observes `AppState.$currentSignIn` (`Services/SignInWindowController.swift:29`). Active card text e.g. "Claude Code is signed out of this account. PixelSwitch switches you to another account when it can." Copy must warn: a successful re-sign makes that account the live login (`finishReauthentication`, `AppState.swift:577+`; investigation line 187-188). The active card currently shows no message in this state: it keeps the last reading (aging, with its "Updated Xm ago" label) because `readUsage` returns nil without clearing it and the error state was cleared at cycle start (`AppState.swift:2135-2137`); with no reading at all it shows "Waiting for usage data..." (`UsageDashboardView.swift:272-285`). New strings x5 tables (`PixelSwitch/{en,de,fr,ja,zh-Hans}.lproj/Localizable.strings`; sources are UTF-8 in this tree, a unit test checks the five share one key set).

### 5.5 CLI / MCP / protocol
`AccountInfo` (`Control/ControlProtocol.swift:~382`) gets optional `needsSignIn: Bool?` (additive; the file's own rule: additive never bumps `version`, still 2; consider `revision` 3 -> 4 and a `features` entry `needsSignIn`, `ControlProtocol.swift:16-26`). Plumbing: `AppControlling.needsSignIn(_:)` (`ControlAPI.swift:~21`), `AppController`, `ControlSnapshots.accountInfo` (`ControlAPI.swift:351`), `CLIRunner.accounts` (`:328` marker `!`, legend `:339`; add e.g. `x` = signed out for the ACTIVE row), `usage.error` already carries the sentence. `linux/pixelswitch/protocol.py` mirrors. Events: `activeAccountChanged`/`autoSwitched` already fire; `usageUpdated` should fire when the flag changes.

### 5.6 Logging lines (no emails, no links; account ids and 8-char token fingerprints only)
`[authHealth] live login of <id> looks signed out (blank claudeAiOauth, claude auth status exit 1); confirming in 15s` ; `[authHealth] live login of <id> confirmed signed out after 2 reads; auto-switch may leave it` ; `[autoSwitch] Rule noSubscription (cause needsSignIn) ...` ; `[autoSwitch] Active <id> needs sign-in, and no other account has room; staying put` ; `[switchTo] renewing <id>'s saved login before switching (token <fp8>)` ; `[needsReauth] <id> ... (server refused the grant)` (existing line, `:1888`).

### 5.7 Unit tests to write first (pure, in the harness; AppState is not in it)
1. `AuthHealth.judge` table: blank+failed -> signedOutSeen; blank+loggedIn -> healthy; blank+unavailable -> unknown; nil credential -> unknown; missing `claudeAiOauth`+failed -> unknown; expired non-blank -> waitingForClaudeCode.
2. `SignedOutConfirmation`: one observation never confirms; two >= 15 s apart confirm; a healthy read resets; neutral reads neither confirm nor reset; clock injected.
3. Engine reuse, modelled on `AutoSwitchRulesTests.swift:647-700`: dead active left with no reading; cooldown bypass (`cooldownHolds(activeUnusable: true)`); Manual only active still left; dead candidate never a target; dead->dead never; Manual only candidate never a target; strategy ordering preserved.
4. `TargetRenewalPolicy` outcome table: refreshed proceed; grantRejected/rotationLost/belongsToAnother refuse; unreachable/storeUnavailable proceed; `sameGrantAsAnotherLogin` never flags (`BackupRefreshOutcome.localRefusal` test exists, `UsageGuardTests.swift`).
5. Leaving-account verdict: only `requiresReauth` outcomes flag.
6. Guard interplay (`UsageGuardTests.swift`): a flag raised on the INACTIVE account ties to the saved token fingerprint and clears on `credentialChanged`; the active-without-token clearing (`UsageRequestGuard.swift:121`) is not what stores the live-dead state.
7. Control: `AccountInfo.needsSignIn` encodes/decodes optional; the token-field scan test in `ControlTests.swift` still passes; CLI row marker.
8. Strings: key-set test across the five tables.
9. Linux: the same tables ported under `linux/tests/` (`usagecycle.py:209` has the identical "No token ... skipping" gap).
View changes cannot be compiled on this Mac: CI branch build (`gh workflow run build.yml --ref <branch>`, `scripts/verify-ci-artifact.sh`) is the only proof.

## 6. Files this work would touch, and collisions with the pending work (2.0 tree)

(a) **Only this work** (no other pending change should touch them):
- NEW `PixelSwitch/Services/AuthHealth.swift`, NEW `Tests/UnitTests/AuthHealthTests.swift`
- `PixelSwitch/Services/UsageRequestGuard.swift` (only if the live-dead state is kept in the guard; not required)
- `PixelSwitch/Services/ClaudeService.swift` (`runClaude`/`getAuthStatus` to expose exit status; Ultra does not need this)
- `PixelSwitch/Services/CredentialOwnership.swift` (optional helper "blank shape")
- `docs/investigations/2026-10-16-usage-429.md` limit b -> closed; a new investigation note

(b) **Shared with other pending work (collision points):**
| File | This work | Ultra (UsageData, UsageLimitRow, UsageDashboardView, engine session logic, plan display, CLI/MCP, 5 strings) | Colour (Views/*, AppStyle, BrandColor, AccountPalette) |
|---|---|---|---|
| `PixelSwitch/AppState.swift` | `refreshData` ~189-197, `evaluateAutoSwitch` ~1516-1560, `performSwitch`/`validatedTargetBackup` ~1042-1381, post-switch ~1644, new state | `windowRule`/`windowDefaults` ~690-720, `noteStayingPut` ~1672, `updateWidgetData` ~2204-2236 | none |
| `PixelSwitch/Services/AutoSwitchEngine.swift` | **none** if section 5.2 option is taken | yes (session threshold logic) | none |
| `Tests/conformance/autoswitch-vectors.json`, `linux/pixelswitch/engine.py` | none (recommended option) | likely (new session-limit rules; the file is frozen, so Ultra needs a new vector file or an orchestrator decision) | none |
| `Control/ControlProtocol.swift`, `Control/ControlAPI.swift`, `Control/AppController.swift` | `AccountInfo.needsSignIn`, `accountInfo()`, `isSwitchable`-style hook | `subscription` field / plan display | none |
| `PixelSwitchCLI/CLIRunner.swift`, `MCPServer.swift`, `CLIParser.swift` | `accounts()` table marker + legend (`:328,339`) | table/usage output, plan text | none |
| `PixelSwitch/{en,de,fr,ja,zh-Hans}.lproj/Localizable.strings` | ~4 new keys x5 | new keys x5 | none |
| `PixelSwitch/Models/UsageData.swift`, `Models/Account.swift` (plan display), `Views/Components/UsageLimitRow.swift` | none | yes | `Models/*` colour files only |
| `PixelSwitch/Views/UsageDashboardView.swift` | card state + Sign in button (`:244-285`, `:370`) | yes (session bar for Ultra, plan badge `:474`) | yes (colours) |
| `PixelSwitch/Views/AccountSwitcherView.swift`, `MainMenuView.swift` | optional marker only | plan badge (`:166`, `:265`) | yes |
| `Tests/UnitTests/main.swift`, `Tests/run-unit-tests.sh` | register new test + source files | register new tests | none |
| `linux/pixelswitch/{usagecycle,coordinator,protocol}.py`, `linux/tests/*` | same behaviour port | `usage_model.py`, `engine.py`, `protocol.py` | none |
| `docs/SESSION-HANDOVER.md`, `RELEASE_NOTES.md`, `docs/worklog/*`, `project.yml` version | each lane | each lane | each lane |

**Suggested build order (a recommendation to choose from):**
1. **N1 first: needs-auth core, no views** (AuthHealth + tests, AppState, ClaudeService exit status, ControlAPI/CLI field, strings). It is the founder's active pain (a 3-hour stall happened today), it adds no engine edit (so it cannot collide with Ultra's engine work or the frozen conformance file) and its shared-file edits are small and additive. Land it before Ultra, or run it in its own worktree and merge it first.
2. **Ultra second** (rebased on N1): shares AppState (different regions), ControlAPI/CLIRunner, strings and the test registration lines with N1, so serial is cheaper than parallel merges.
3. **Colour and N2 (the card state + Sign in button) together, last**, in one view lane with one CI branch build: N2 is two small hunks in `UsageDashboardView.swift` (and optionally `AccountSwitcherView.swift`), exactly the files the colour change rewrites. If colour must land before Ultra, N2 still goes after both.
Parallel-safe now: N1 and Colour (disjoint files). Not parallel-safe: N1/Ultra on `AppState.swift` + `ControlAPI.swift` + `CLIRunner.swift` + strings + `run-unit-tests.sh`; Ultra/Colour/N2 on `UsageDashboardView.swift`.

## 7. Verified vs inferred

**Verified (read in code, or ran a command and saw the output):** everything with a file:line above; the checkout/remote/installed-app versions; the log facts in 4.1 (counts by `rg -c`, timestamps by reading lines); 70 of 70 refreshes were "valid for 8h 0m"; `auth status` exits 1 and `No token` fires at 10:40:31Z and 36 times; no `[autoSwitch]` line after 08:20Z; `noSubscription` is left for Manual only (test line 674) and does nothing with auto-switch off (`:1517`); ControlProtocol's additive-no-bump rule (`ControlProtocol.swift:8-16`); the strings sources are UTF-8.
**Inferred (not proven):** the blanked live credential is what made `usageLogin` nil today (consistent with a 202-char shrink and with recon [CC bundle] and spec D12, but I did not read the credential); `claude auth status` skips the network when expired (timing inference in the 1.9 investigation); that a refreshed/replaced live login un-sticks running Claude Code sessions (PixelSwitch bumps the credentials-file mtime so they re-read; recovery itself is unobserved); that Claude Code writes `refreshTokenExpiresAt` and treats a past value as signed out ([CC bundle], unverified); what blanked the 10:40Z login (unknown); the body text of a 401.

## 8. Gotchas
1. **Stale checkout (1.8) vs origin/main and the installed app (2.0).** Pull before any lane starts; the brief's 1.6/1.8 references are partly superseded (one usage request site, `UsageRequestGuard`, `needsReauth`).
2. `needsReauth` already exists, so naming the new thing "needsReauth" would collide. Use a distinct name (`needsSignIn`, `liveLoginSignedOut`) or reuse the guard deliberately; note the guard clears an active account's token-less flag (`UsageRequestGuard.swift:121`).
3. "expired" means three things: `Login.isExpired` (expiresAt, 60 s slack), `UsageErrorState.isExpired` (the card says re-sign; also what `isSwitchable` reads, `LoginGate.isSwitchable`), and `UsageError.expired` (HTTP 401). An expired access token on the live login is normal and means "wait", not "sign in".
4. `UsageNotice.waitForClaudeCode` stores `isRateLimited: true` (`AppState.swift:1753`), and `UsageNotice.choose` never overwrites a `.resign` message with a pause (`UsageRequestGuard.swift:342-356`): keep that when adding a state.
5. A successful re-authentication also makes that account the LIVE login (`finishReauthentication`); the button copy must say so.
6. The engine is mirrored in Python and pinned by 150 frozen conformance vectors plus a frozen 1.9 oracle; touching engine behaviour is a three-place change (Swift, Python, vectors) and collides with Ultra.
7. `getAuthStatus` throws on any non-zero exit and drops stdout; the JSON body of a logged-out state is unseen, and no log line shows it. Capture it once (read-only) before relying on `loggedIn:false`.
8. `AppState` is not compiled by the unit harness (`Tests/run-unit-tests.sh` lists files explicitly; new pure files must be added there) and this Mac cannot compile SwiftUI view files (Swift 6.4 CLT lacks the SwiftUI macro plugin): views need a CI branch build; `scripts/typecheck.sh` no longer works (use the reordered script named in the handover).
9. ARCHITECTURE.md is stale: it says backups are `com.pixelswitch.tokens/<UUID>` (real: one item `ai.pixelventures.pixelswitch.backups`/`all-accounts`), "every 30s refresh" (real default 300 s) and has no refresh-token section; its own 2.0 footer says code and the 2.0 spec win. The handover's "three usage paths" is 1.8-era.
10. `validatedTargetBackup` "switching anyway" on `.unreachable/.storeUnavailable`, and the `readSoon` / round-robin cadence (one inactive account per cycle) mean a dead saved login can sit undiscovered for N cycles.
11. The Beeco Mac (`ssh claadmin@100.125.33.35`) starts 2.0 with 3 accounts needing re-sign (handover): a ready-made real data set for A; I did not touch it.
12. Do not run a diagnosis that sends an expired/refused token to the usage or profile endpoint: it re-arms the one-hour 429 block (handover gotcha).

## 9. Line numbers drift
Every line number above is for `50a5243` extracted to `.../scratchpad/src-main`. Another lane writing to `main` moves them within the hour. Re-grep the symbol (`readUsage`, `validatedTargetBackup`, `evaluateAutoSwitch`, `flagNeedsReauth`, `usageErrorActions`, ...) instead of trusting an anchor.

## 10. Search scope
Read in full: ARCHITECTURE.md (1.8 and 2.0 headers), SESSION-HANDOVER.md (1.8 and 2.0), `UsageRequestGuard.swift`, `AutoSwitchEngine.swift` (plan/rank/cooldown), 2.0 `AppState.swift` sections 1-330, 1020-1760, 1728-2200, `CredentialOwnership.swift`, `ClaudeCredentialMerge.swift`, `ClaudeTokenWriter.swift`, `UsageRequestError.swift`, `ClaudeService.swift` 196-420 and the runClaude block, `ControlProtocol.swift` (header, `AccountInfo`), `ControlAPI.swift`, `AppController.swift` 1-130, `CLIRunner.swift` 240-390 (1.8) / grep (2.0), `UsageDashboardView.swift` 200-440 (1.8), `AccountSwitcherView.swift` 120-270 (1.8), the 1.9 investigation 38-237, the 2.0 recon `credentials.md` 60-70 and 140-175, spec D12, conformance README. Grepped only: Linux port (`linux/pixelswitch/*`), tests, strings. Not read: `KeychainService.swift` beyond line 345, `SignInSession.swift`/`SignInWindowController` bodies, `StatusItemController`, widget code, `docs/superpowers/plans/*`. Not run: `claude auth status`, anything that reads a credential, any `security` command, any network call to Anthropic.
