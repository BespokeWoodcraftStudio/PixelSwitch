# Recon: usage and profile requests (PixelSwitch 1.9, build 30)

Read-only recon, 2026-10-24, repo HEAD `35f8a99`. Every claim cites `file:line`. "Not found" means the repo does not say. No token or
secret value was read or is written here; shapes and key names only.

Files read in full: `PixelSwitch/Services/ClaudeService.swift`, `UsageRequestGuard.swift`, `UsageRequestError.swift`,
`PixelSwitch/Models/UsageData.swift`, `docs/investigations/2026-10-16-usage-429.md`, `Tests/UnitTests/UsageGuardTests.swift`.
Read in the relevant parts: `PixelSwitch/AppState.swift` (polling, guard use, switch, auto-switch), `AutoSwitchEngine.swift`
(how a response is turned into a number), `CredentialOwnership.swift`, `PixelSwitchApp.swift`, `SettingsView.swift`,
`Control/SettingsCatalog.swift`, `Views/UsageDashboardView.swift`, `PricingService.swift`.

Paths below are relative to `PixelSwitch/` (the app folder) unless they start with `docs/` or `Tests/`.

---

## 1. Every HTTP request the app makes (a)

All go through `URLSession.shared` (no custom session, no custom `URLSessionConfiguration`, no retry or backoff layer in the HTTP
code). `grep` over `PixelSwitch`, `PixelSwitchCLI`, `Shared`, `PixelSwitchWidget` finds no other network client. The CLI
(`PixelSwitchCLI`) and the widget make no requests of their own; the CLI talks to the running app (`Control/`), the widget reads a
shared data file (`AppState.swift:1774-1805`).

### 1.1 The three that carry an OAuth token

| # | Purpose | Method and URL | Headers set in code | Body | Timeout | Where |
|---|---|---|---|---|---|---|
| 1 | Usage | `GET https://api.anthropic.com/api/oauth/usage` | `Authorization: Bearer <accessToken>`; `anthropic-beta: oauth-2025-04-20` | none | not set, so URLRequest default 60 s | `Services/ClaudeService.swift:235-245` (headers 237-238) |
| 2 | Profile (who owns this token) | `GET https://api.anthropic.com/api/oauth/profile` | `Authorization: Bearer <accessToken>`; `anthropic-beta: oauth-2025-04-20` | none | `request.timeoutInterval = 15` | `ClaudeService.swift:302-308` (timeout 304, headers 305-306) |
| 3 | Refresh a saved login | `POST https://console.anthropic.com/v1/oauth/token` | `Content-Type: application/json` only | JSON `{"grant_type":"refresh_token","refresh_token":"<refresh token>","client_id":"9d1c250a-e61b-44d9-88ed-5944d1962f5e"}` | not set, so 60 s | `ClaudeService.swift:339-340` (constants), `360-380` (request) |

Facts a port must copy exactly:

- The beta value is the literal string `oauth-2025-04-20` and it is sent on BOTH GETs, not on the POST.
- The client id is Claude Code's public PKCE client id (`ClaudeService.swift:338-339`, comment: "not a secret").
- The refresh POST goes to `console.anthropic.com`, the two GETs to `api.anthropic.com`.
- **User-Agent: not set anywhere in the repo** (`grep User-Agent` over all Swift, Markdown, shell and Python returns nothing relevant).
  URLSession therefore sends its own default (`<app>/<build> CFNetwork/<v> Darwin/<v>`); the exact bytes were never recorded.
  `Accept`, `Accept-Language`, `Accept-Encoding` are also left to URLSession. Not found: any evidence that Anthropic keys on them.
- No `Content-Type` on the GETs, no query string, no request id header.
- `URLSession.shared` also brings the default `URLCache` and cookie storage; the usage GET uses the default cache policy
  (`useProtocolCachePolicy`, nothing overrides it at `ClaudeService.swift:236`). A Linux client should not cache these.
- The access token is never logged; logs carry `TokenFingerprint.forLog(...)`, the first 8 hex of the fingerprint
  (`UsageRequestGuard.swift:13`, uses at `ClaudeService.swift:299`, `AppState.swift:1376,1391`).

### 1.2 Other requests (not token-carrying; listed so nothing is missed)

| Purpose | Request | Timeout | Where |
|---|---|---|---|
| Latest Claude Code version (Settings, Claude CLI tab only, not on the poll) | `GET https://downloads.claude.ai/claude-code-releases/latest`, body must be a pure semver | 5 s | `ClaudeService.swift:734-756`; caller `Views/ClaudeCLITabView.swift:136` |
| Model price table, conditional GET with `If-None-Match` (called "every refresh cycle", throttled to 1/min) | `GET https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json` | default | `Services/PricingService.swift:170,280-335`, `minCheckInterval = 60` at `:165` |
| App updates | Sparkle (`SPUStandardUpdaterController`), own schedule | Sparkle | `PixelSwitchApp.swift:49-51` |
| Status page link (opened in the browser, not fetched) | `https://status.claude.com` | n/a | `Services/UsageRequestError.swift:107` |

### 1.3 Subprocess calls on every poll cycle (no HTTP from PixelSwitch, but the CLI may)

- `claude --version`, 30 s timeout (`ClaudeService.swift:216-218`), once per cycle (`AppState.swift:186`).
- `claude auth status`, 30 s timeout (`ClaudeService.swift:204-206`), once per cycle (`AppState.swift:191`). Its JSON is
  `AuthStatus {loggedIn, authMethod, apiProvider, email, orgId, orgName, subscriptionType}` (`Models/Account.swift:127-135`).
- The 2026-10-16 investigation measured `claude auth status` as adding no failing request (median 147 ms with a stale live token,
  so it seems to skip the network when the token is expired) (`docs/investigations/2026-10-16-usage-429.md:82-83`).

---

## 2. Response shapes as decoded (b)

### 2.1 Usage: `GET /api/oauth/usage` (200)

Decoder: `UsageAPIResponse` (`Models/UsageData.swift:5-58`), plain `JSONDecoder()` (`ClaudeService.swift:270`), explicit `CodingKeys`
(snake_case names are spelled out, no key-decoding strategy). Every field is optional; a missing or null field decodes to nil.

```
{
  "five_hour":            {"utilization": 42.0, "resets_at": "2026-09-25T18:00:00.000000+00:00"},
  "seven_day":            {"utilization": 61.0, "resets_at": "2026-09-30T09:00:00+00:00"},
  "seven_day_oauth_apps": <window|null>,
  "seven_day_opus":       <window|null>,      // "comes back null" per UsageData.swift:15
  "seven_day_sonnet":     <window|null>,      // "comes back null" per UsageData.swift:15
  "seven_day_cowork":     <window|null>,
  "iguana_necktie":       <window|null>,      // purpose not found anywhere in the repo
  "extra_usage": {"is_enabled": false, "monthly_limit": null, "used_credits": null, "utilization": null},
  "limits": [
    {"kind":"weekly_scoped","group":"weekly","percent":10,"severity":"normal",
     "resets_at":"2026-09-30T09:00:00+00:00",
     "scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":true}
  ]
}
```

The block above is the fixture in `Tests/UnitTests/ControlTests.swift:53` plus the extra keys named in the model. The doc comment
for a real `limits` entry is `UsageData.swift:60-63` (it shows `"percent":74` and
`"resets_at":"2026-09-28T18:00:00.177837+00:00"`).

Types and rules:

- **Window** (`UsageWindow`, `UsageData.swift:148-155`): `utilization: Double?` and `resets_at: String?`. `utilization` is a
  PERCENT in 0 to 100 (42.0 means 42 percent), not a fraction. Integers (`"percent":10`) decode into `Double` fine.
- **`resets_at` formats seen**: ISO 8601 with a six-digit fractional second and `+00:00` (`2026-09-25T18:00:00.000000+00:00`,
  `2026-09-28T18:00:00.177837+00:00`), and ISO 8601 with no fraction and `+00:00` (`2026-09-30T09:00:00+00:00`). The parser tries
  `ISO8601DateFormatter` with `[.withInternetDateTime, .withFractionalSeconds]` first, then `[.withInternetDateTime]`
  (`UsageData.swift:157-164`). An unparseable or absent value gives `resetsAtDate == nil`. A `Z` suffix is not shown in any sample
  but the formatter accepts it. A port must accept 0 to 9 fractional digits and both `+00:00` and `Z`.
- **`limits`** (`UsageLimit`, `UsageData.swift:64-115`): keys `kind`, `group`, `percent` (Double), `severity`, `resets_at`,
  `scope.model.{id, display_name}`, `is_active`; all optional. Decoded LENIENTLY (`LenientList`, `UsageData.swift:119-146`): an entry
  of an unexpected shape is skipped, and a `limits` that is not a list reads as empty, so a change there can never cost the session
  and weekly numbers (`UsageData.swift:31-47`, the `try?` at line 46).
- **Per-model weekly allowance** (today only "Fable"): an entry counts when `(kind == "weekly_scoped" || group == "weekly")` AND
  `scope.model.display_name` trims to non-empty AND `percent != nil` (`UsageData.swift:102-104`). Lookup is by display name, case
  insensitive (`UsageData.swift:55-57`); the engine's name is the constant `"Fable"` (`AutoSwitchEngine.swift:42`). `percentLeft`
  is `clamp(100 - percent, 0, 100)` (`:107-109`); `.window` re-wraps it as a `UsageWindow` (`:112-114`).
- **Window lengths** for the elapsed-time bar: `18000` s and `604800` s (`UsageData.swift:226-229`);
  `elapsedPercent = clamp(1 - remaining/windowSeconds, 0, 1) * 100` (`:218-222`).
- **Which fields feed logic**: only `five_hour`, `seven_day` and the Fable entry of `limits` drive auto-switch
  (`AutoSwitchEngine.swift:89-116`). `extra_usage` is shown in the card and exposed by the control API
  (`Views/UsageDashboardView.swift:240`, `Control/ControlAPI.swift:297`). The other `seven_day_*` windows and `iguana_necktie` are
  decoded and not used anywhere outside `UsageData.swift` (grep).
- The log line after a good read prints `session=<five_hour.utilization>%, weekly=<seven_day.utilization>%` and the Fable percent,
  with `-1` for a missing value (`ClaudeService.swift:271`).

How a reading becomes a number (needed by the split-threshold work), `AutoSwitchEngine.swift`:

- `bindingUtilization(usage, asOf, requireKnownWindow)` = `max` of the readings of `five_hour` and `seven_day` (`:89-98`).
  The two windows are collapsed into one number today; splitting thresholds means using the two `reading(...)` values separately.
- `reading(window, asOf, requireKnownWindow)` (`:232-238`): nil if no `utilization`; if `resets_at` is absent or unparseable,
  keep the value when lenient, drop it when strict; if `resets_at < now` the window has reset and the reading is DROPPED. Strict is
  used for the ACTIVE side so a retained high reading with no known expiry cannot trigger a switch long after the window reset
  (`AutoSwitchEngine.swift:80-87`, `AppState.swift:1283`).
- `utilization(usage, limit: .fable)` uses the Fable entry of `limits` the same way (`:112-115`).

### 2.2 Profile: `GET /api/oauth/profile` (200)

Parsed with `JSONSerialization`, not `Codable` (`ClaudeService.swift:317-329`). Only these keys are read:

```
{ "account": { "uuid": "<string>", "email": "<string>"  /* or "email_address" */ , ...anything else is ignored } }
```

- `account.uuid` is read as `AccountIdentity.uuid`; email is `account.email`, falling back to `account.email_address`
  (`ClaudeService.swift:323-324`). If both are nil the call counts as failed (`:325-328`).
- The uuid equals `oauthAccount.accountUuid` in `~/.claude.json` for the same account (verified 2026-09-22, comment at
  `Services/CredentialOwnership.swift:78-81`).
- The full response shape (organization block, etc.) is **not found** in the repo; only `account.uuid` and `account.email` /
  `account.email_address` are used.

### 2.3 Token endpoint: `POST /v1/oauth/token` (200)

Read keys (`ClaudeService.swift:382-385,409-413`): `access_token` (String), `expires_in` (Double, seconds), optional
`refresh_token` (String, kept only if non-empty). Written back into the stored credential JSON:
`claudeAiOauth.accessToken`, `claudeAiOauth.expiresAt = Int64(now_ms) + Int64(expires_in * 1000)`, and `claudeAiOauth.refreshToken`
when a new one came. **`scopes` is deliberately never rewritten** from the response, because the CLI (checked against claude 2.1.220)
only recognises a login whose stored `scopes` contains `"user:inference"` (`ClaudeService.swift:414-420`). Error body: an OAuth
`error` string; only `invalid_grant` means the grant is dead (`:393-406`).

### 2.4 The credential JSON the requests are built from

`CredentialOwnership.login(fromCredential:)` (`CredentialOwnership.swift:30-37`) reads `claudeAiOauth.accessToken` (required,
non-empty), `claudeAiOauth.refreshToken` (optional, empty reads as nil) and `claudeAiOauth.expiresAt` (a number, **milliseconds
since 1970**, optional). `Login.isExpired` is `expiresAt/1000 < now + 60` (`:22-26`). The credential blob also holds this Mac's MCP
logins, which change without the Claude token changing (`AppState.swift:1069-1071`).

---

## 3. Polling schedule (c)

### 3.1 The timer

- One repeating `Timer.scheduledTimer(withTimeInterval: interval, repeats: true)` that runs `await self.refresh()`
  (`AppState.swift:232-240`). Default interval **300 s** (`startAutoRefresh(interval: TimeInterval = 300)`; `@AppStorage("refreshInterval") = 300`
  at `PixelSwitchApp.swift:28`, `Views/SettingsView.swift:8`).
- Allowed values: **15, 30, 60, 300, 600 s** (Settings picker `SettingsView.swift:60-66`; control API/CLI `choice` list
  `Control/SettingsCatalog.swift:49`).
- At app start (`PixelSwitchApp.swift:66-70`): one `refresh()` immediately, THEN the timer starts, so the first timer fire is one
  interval later. Changing the interval restarts the timer and does NOT refresh at once (`Control/SettingsStore.swift:152-154`).
- No wake, network-change or app-activation trigger exists (grep for `didWake` and `didBecomeActive` finds nothing). A Mac timer
  does not fire while asleep and fires once after wake; a Linux port should do one cycle on resume.

### 3.2 What else triggers a cycle (`refresh()`)

| Trigger | Where |
|---|---|
| The popover's refresh button | `Views/MainMenuView.swift:353-354` |
| Control API, command line and MCP `refreshUsage` | `Control/AppController.swift:172-174` |
| Changing "Usage history window" | `Control/SettingsStore.swift:157-160` |
| After ANY switch, manual or automatic (the last step of `performSwitch`) | `AppState.swift:853` |
| After a new-account sign-in and after a re-authentication | `AppState.swift:563`, `640` |
| The card's Retry on the ACTIVE account (a full cycle) | `AppState.swift:1545-1548` |

`refresh()` is `refreshData()` then `evaluateAutoSwitch()` (`AppState.swift:165-168`). It does nothing while a login is in progress
(`:172-175`) or when another cycle is already running (`:176-179`, the `isRefreshing` re-entrancy guard), and in those cases auto-switch is
not evaluated either.

Triggers that are NOT a cycle:
- **Card Retry on a non-active account**: one `readUsage` through the guard, then `evaluateAutoSwitch()` if no cycle is running
  (`AppState.swift:1549-1553`). A park or cool-down still holds it back.
- **Auto-switch verification**: at most ONE fresh usage request per evaluation (`freshRequestBudget = 1`, `AppState.swift:1212-1220`);
  a candidate sampled by the current cycle counts as already verified (`:1215-1217`). Rule-gated and behind a 300 s cooldown
  (`autoSwitchCooldown`, `:130`; `cooldownHolds` `AutoSwitchEngine.swift:226-229`).
- **`readSoon`**: `noteCredentialChanged` puts the account in a set, and the next cycle reads that account ahead of the round-robin
  (`AppState.swift:101,1462-1470,1694-1695,1701`). It does not start a cycle by itself.

### 3.3 Order of work inside one cycle (`refreshData`, `AppState.swift:171-230`)

1. `lastCycleStart = Date()` (auto-switch uses it to tell fresh samples from retained ones, `:182,1162,1215`).
2. `claude --version`, then `claude auth status` -> `updateActiveAccount` (`:186-197`).
3. `diagnoseTokenHealth()` (keychain reads only, `:200`).
4. `auditSavedLogins()` (`:205`, `1082-1108`): profile lookup for EVERY saved login once per launch, afterwards only for saved
   logins that share a lineage with another account. Skips needs-reauth accounts; each lookup goes through `mayUse`.
5. `reconcileLiveLogin()` (`:206`, `1037-1075`): a profile lookup for the LIVE token every cycle (`provenOwner` -> `accountIdentity`),
   through `mayUse`.
6. `fetchAllAccountUsage()` (`:209`, `1660-1731`): the usage requests.
7. Local-only work: stats, JSONL cost parsing, widget (`:212-227`).

### 3.4 Which accounts are asked in a cycle (`fetchAllAccountUsage`, `AppState.swift:1660-1731`)

1. For EVERY account, ask the guard (`decision`). `.request` and `.refreshFirst` make the account "pollable"; `.needsReauth`,
   `.parked`, `.coolingDown`, `.waitForClaudeCode` do not, and `showNotice` puts the honest message on the card (`:1665-1681`).
   An account with no usable login that is not flagged needs-reauth stays pollable and `readUsage` logs "No token" and skips it
   (`:1666-1672`, `1367-1370`). The loop does not filter by provider (Gemini and Codex accounts exist in the model,
   `Models/Account.swift:5-9`); they would occupy a slot and return nothing.
2. Targets = the pollable ACTIVE account (if any) + ONE pollable non-active account (`:1691-1700`).
   The one is, in order: the first `others` account that is in `readSoon`, else `others[usageFetchCursor % others.count]` with
   `usageFetchCursor += 1`. The cursor only advances on the round-robin path. `others` is `accounts` order filtered to pollable
   and non-active, so the modulus shrinks when accounts are parked (this was the speed-up bug; the 20-minute cool-down is what
   now holds it back, not the cursor).
3. `readSoon` is cleared for the targets; targets' card errors are cleared before reading (`:1701-1707`). Non-targets keep both
   their stale reading and their error.
4. Targets are read one after another, **1.5 s apart** (`Task.sleep(1_500_000_000)` between requests, `:1716-1720`; comment: a
   back-to-back burst reliably got all but one 429'd). Each read goes through `readUsage` (the guard) and stores
   `accountUsage[id]`, `accountUsageSampledAt[id] = Date()`, clears the error (`:1723-1728`).
5. The active account reads the LIVE credential, every other account its SAVED backup (`usageLogin`, `AppState.swift:1311-1317`).

### 3.5 Requests per hour per account (default 300 s, so 12 cycles an hour; N = number of accounts)

| Token | Usage GET | Profile GET | Notes |
|---|---|---|---|
| Live (active) account | 12 per hour (every cycle) | 12 per hour (`reconcileLiveLogin`, every cycle) | 24 an hour per healthy live token. Plus extra cycles from the table in 3.2. |
| Each saved account | `12 / (N - 1)` per hour: N=2 gives 12, N=3 gives 6, N=4 gives 4, N=5 gives 3 | 0, except once per launch (audit), after a renewal (identity check of the renewed token, `AppState.swift:1633`), and in a switch | Dead tokens cost at most 1 usage request per new token (see section 4). |
| Whole app, one launch | none extra | up to N in the first cycle | The one-per-launch audit of saved logins. |
| One switch | about 2 to 3 profile lookups (live owner `AppState.swift:805`, target `:983`, capture/backup `ClaudeService.swift:475`), then a full cycle (2 usage + 1 profile) | | `performSwitch` ends in `refresh()`. |

At the 15 s and 30 s settings the live token gets 240 or 120 usage requests plus the same number of profile requests an hour, and no
code throttles a healthy token. The investigation only established that healthy tokens were never refused at up to 13 requests an
hour (`docs/investigations/2026-10-16-usage-429.md:25-26`). Untested above that.

---

## 4. UsageRequestGuard: every rule (d)

File: `Services/UsageRequestGuard.swift`. Pure logic: no network, no Keychain, no clock of its own (`now` is a parameter). A class
with an `NSLock` because `ClaudeService` consults it from any thread (`:57-60`). ONE instance for the whole app:
`ClaudeService.shared.requestGuard` (`ClaudeService.swift:23`, read by AppState at `AppState.swift:93`).

### 4.1 Constants (`UsageRequestGuard.swift:62-69`)

| Name | Value | Meaning |
|---|---|---|
| `cooldown` | 20 * 60 s | After a 401 or 429 on a token, nothing else goes out with it for 20 minutes (rule d) |
| `expirySlack` | 60 s | A token with 60 s or less left counts as expired (rule b) |
| `longestPark` | 3600 s | Upper clamp of a 429 park |
| `shortestPark` | 60 s | Lower clamp |
| `defaultPark` | 120 s | Park when the 429 carried no parseable `Retry-After` |
| `refusedWithoutAccountLimit` | 64 | Size of the FIFO of tokens refused where no account was known (`:93`) |
| Fingerprint | n/a | SHA-256 of the token's UTF-8 bytes, first 8 bytes as 16 lowercase hex characters (`:7-11`); a log line shows the first 8 hex (`:13`) |

### 4.2 State, per account id and per token (`:71-92`)

```
State { rejectedFingerprint: String?   // token that got a 401 from /usage or /profile
        lastAuthFailure: Date?
        park: Stamp?                   // Stamp { until: Date, fingerprint: String }  (429, Retry-After)
        cooldown: Stamp?               // 20 minutes after a 401 or 429
        needsReauth: Bool
        needsReauthFingerprint: String? }   // the token the account held when flagged; nil = restored after relaunch
states: [AccountID: State]
refusedWithoutAccount: [String]        // fingerprints, FIFO, max 64
```

Everything earned is keyed by the fingerprint of the exact access token. A different token on the same account forgets it.

### 4.3 The seven rules (`UsageRequestGuard.swift:44-56`)

- **a.** A token that got a 401 is never sent again until the account's stored or live token changes.
- **b.** A token whose `expiresAt` is known and is past, or within 60 s, is never sent. A missing `expiresAt` is allowed (rule a
  stops it after one 401). `isExpired(expiresAt_ms, now) = expiresAt_ms/1000 < now + 60` (`:156-159`).
- **c.** A 429 park holds only while the account still has the token it was earned on.
- **d.** After a 401 or 429 on a token, nothing else is sent with it for 20 minutes, however many accounts are polled.
- **e.** An account the server refused a refresh grant for (`invalid_grant`) sends nothing until its token changes.
- **f.** The live login is never dropped: it waits for Claude Code (which renews it on its next session) and is read the moment its
  token changes.
- (g, from the investigation, not a guard method) A saved login that is expired or refused is renewed in place FIRST, then asked
  once with the new token (`AppState.swift:1427-1453`).

### 4.4 `decision(for:isActive:login:now:)` precedence, exactly (`:103-128`)

```
fp    = fingerprint(login.accessToken)
state = states[account] ?? State()
if isActive and state.needsReauth and state.needsReauthFingerprint == nil: state.needsReauth = false    # :111
dropWhatIsNotAboutThisToken(state, fp)       # forgets rejected/park/cooldown on other tokens; handles needsReauth fingerprint
states[account] = state                      # NOTE: asking creates/updates the entry
if state.needsReauth                                  -> .needsReauth
isDead = isExpired(login.expiresAt, now) or state.rejectedFingerprint == fp or fp in refusedWithoutAccount
if isActive and isDead                                -> .waitForClaudeCode      # before park/cooldown on purpose
if state.park     and park.until     > now            -> .parked(until)
if state.cooldown and cooldown.until > now            -> .coolingDown(until)
if isDead                                             -> .refreshFirst           # saved logins only
                                                      -> .request
```

Boundaries are strict: at exactly `until` the hold is over (`> now`). Tests: cool-down at `+20m-1s` is not `.request`, at `+20m+1s` is
(`Tests/UnitTests/UsageGuardTests.swift:113-114`).

`dropWhatIsNotAboutThisToken` (`:267-284`): clears `rejectedFingerprint` and `lastAuthFailure` if the stored one differs from `fp`;
clears `park` and `cooldown` whose fingerprint differs; for `needsReauth`: if it has a stored fingerprint that differs from `fp`, the flag
and fingerprint are cleared, and if it has NO stored fingerprint it adopts `fp` (a saved login adopts the first token it sees).

### 4.5 Other methods and their behaviour

- `mayUse(accessToken, expiresAt, now) -> Bool` (`:133-144`): for callers with no account (profile lookups). False when the token is
  expired by `expiresAt`, is in `refusedWithoutAccount`, or ANY account's state has `rejectedFingerprint == fp`, or an unexpired
  `park` or `cooldown` stamped with `fp`. It does NOT look at `needsReauth`. The profile endpoint is gated by this at
  `ClaudeService.swift:298`.
- `wasRejected(account, accessToken)` (`:149-152`): lets a caller tell "refused" from a blip after a lookup failed, because
  `decision` answers `.coolingDown` first once a 401 is recorded. The switch uses it (`AppState.swift:987,994`).
- `recordRejected(account: UUID?, accessToken, now)` (`:164-180`): with no account, append to `refusedWithoutAccount` (dedupe, FIFO cap
  64, never cleared by a credential change because tokens are unique). With an account: drop other-token state, set
  `rejectedFingerprint = fp`, `lastAuthFailure = now`, `cooldown = Stamp(now + 20 min, fp)`.
- `recordRateLimited(account, accessToken, retryAfter, now) -> Date` (`:184-195`): `parkFor = min(max(retryAfter ?? 120, 60), 3600)`;
  sets `park = Stamp(now + parkFor, fp)` and `cooldown = Stamp(now + 20 min, fp)`; returns the park end. Tests: nil gives 120, 0 gives
  60, 999999 gives 3600 (`UsageGuardTests.swift:97-100`). There is **no** account-less variant: a 429 on a profile lookup with no
  known account is not recorded (`ClaudeService.swift:315`).
- `markNeedsReauth(account, accessToken?)` (`:200-206`): sets `needsReauth = true`, `needsReauthFingerprint = fp(accessToken)` or nil.
- `credentialChanged(account, accessToken?) -> Bool` (`:212-227`): the hook for "this account's stored or live credential changed".
  With a token: a flag with no stored fingerprint is cleared, then `dropWhatIsNotAboutThisToken(fp)`. With nil: state reset to empty.
  Returns whether anything was forgotten. The SAME token does not lift its park (test `:158-166`).
- `forget(account)` (`:230-233`), `isNeedsReauth`, `needsReauthAccounts`, `restoreNeedsReauth(Set<UUID>)`, `lastAuthFailure(of:)`.

### 4.6 needsReauth persistence

- Persisted in `UserDefaults` under the key `"ai.pixelventures.pixelswitch.needsReauthAccounts"` (`AppState.swift:97`) as a sorted
  array of account UUID strings. IDs only, never a token (`:1483-1488`).
- Written by `persistNeedsReauth()` only when the set changed (`:1483-1488`); called after `noteCredentialChanged`, `flagNeedsReauth`,
  `removeAccount`, and at both ends of `fetchAllAccountUsage` (`:749,1684,1730`).
- Restored in `init()` after `loadAccounts()` (`:145-150`): ids are filtered to accounts that still exist, and each flag is tied to the
  access token the account's SAVED backup holds now (`markNeedsReauth(account:, accessToken: backupToken)`), so any other token clears it
  (`:1490-1503`). If an id was dropped, the stored set is rewritten.
- A flag with NO token (backup removed, "belongs to another account") must not take over a healthy LIVE login: `decision` clears
  it when the account is active (`UsageRequestGuard.swift:111`, review finding P1 in `docs/investigations/2026-10-16-usage-429.md:193-197`).
- NOT persisted, on purpose: parks, cool-downs, rejected fingerprints. A stale park surviving a relaunch is worse than one extra request
  per account, because a relaunch was the only way to clear a stuck card (`docs/investigations/2026-10-16-usage-429.md:149-151`,
  `AppState.swift:86-92`).

### 4.7 Who sets needsReauth (only the server's word)

`BackupRefreshOutcome.requiresReauth` (`UsageRequestGuard.swift:367-372`) is true only for `.grantRejected` (the token endpoint said
`invalid_grant`), `.rotationLost` (rotation succeeded and the store could not be written twice), and `.belongsToAnotherAccount`.
It is false for `.sameGrantAsAnotherLogin` (PixelSwitch's own refusal to spend a refresh token that is the live grant or another
account's, `localRefusal` `:377-379`), `.unreachable`, `.noBackup`, `.storeUnavailable`, `.refreshed`. The decision (with reasons) is in
`docs/investigations/2026-10-16-usage-429.md:157-160`. Known wart: a saved login with NO refresh token returns `.rejected` and is flagged
although the server never refused it; kept on purpose (`:216-220`).

### 4.8 Live vs saved login, in one table

| Situation | Live (active) account | Saved (non-active) account |
|---|---|---|
| Token expired (`expiresAt` within 60 s or past) | `.waitForClaudeCode`: nothing sent, last reading kept, card says "Waiting for Claude Code..." | `.refreshFirst`: POST to the token endpoint, then ONE usage request with the new token |
| Got a 401 | recorded; `showNotice(.waitForClaudeCode)`; no renewal attempt (Claude Code owns this login) (`AppState.swift:1397-1401`) | recorded; renewed in place at once, then one request with the new token (`:1403`) |
| 401 on the token that was JUST renewed | n/a | recorded, card "Session expired.", stop (`:1392-1396`) |
| Token changes (Claude Code renewed it, sign-in, switch) | read again at once; all earned state forgotten | same |
| Flagged needs-reauth | cleared if the flag held no token | `.needsReauth`, nothing sent until the token changes |

The app never refreshes the LIVE token (the "delegated refresh" that ran `claude auth status` then re-asked with the same dead token
was removed: 13 runs, 0 recoveries; `docs/investigations/2026-10-16-usage-429.md:152-153`).

### 4.9 The renewal path for a saved login (`AppState.swift:1566-1658`)

- One renewal in flight per account (`renewalsInFlight`, later callers join the running task) (`:1562-1572`).
- Refuse to spend a refresh token that is the same grant as the live login or as another account's saved login (`localRefusal`,
  `:1606-1614`, `Services/CredentialOwnership.swift:43-47`).
- Prove the store is writable BEFORE the POST, by re-saving what was just read (`:1623-1626`), because a rotation that cannot be
  persisted kills the account.
- After a successful POST: a profile lookup of the renewed token (through the guard) to check the owner, remove the backup if it belongs
  to another account (`:1632-1638`), save, retry the save once after 1 s, else `.rotationLost` (`:1639-1648`).
- Outcome mapping: `.rejected` -> `.grantRejected`; `.transient` -> `.unreachable` (`:1650-1657`). Token-endpoint classification:
  status 200 that fails OUR parse -> transient; non-200 with OAuth `error == "invalid_grant"` -> rejected; everything else (5xx, 429, 408,
  `invalid_request`, network error) -> transient (`ClaudeService.swift:398-407,426-428`). No refresh token stored -> rejected (`:361-368`).

### 4.10 Pure functions a port should mirror (signatures, verbatim)

```swift
// UsageRequestGuard.swift
enum TokenFingerprint { static func of(_ accessToken: String) -> String; static func forLog(_ fingerprint: String) -> String }
enum UsageDecision: Equatable { case request, refreshFirst, waitForClaudeCode, needsReauth, parked(until: Date), coolingDown(until: Date) }
final class UsageRequestGuard {
    static let cooldown: TimeInterval = 20 * 60;  static let expirySlack: TimeInterval = 60
    static let longestPark: TimeInterval = 3600;  static let shortestPark: TimeInterval = 60;  static let defaultPark: TimeInterval = 120
    init(needsReauth: Set<UUID> = [])
    func decision(for account: UUID, isActive: Bool, login: CredentialOwnership.Login, now: Date = Date()) -> UsageDecision
    func mayUse(accessToken: String, expiresAt: Double? = nil, now: Date = Date()) -> Bool
    func wasRejected(account: UUID, accessToken: String) -> Bool
    static func isExpired(_ expiresAt: Double?, now: Date) -> Bool          // expiresAt in ms
    func recordRejected(account: UUID?, accessToken: String, now: Date = Date())
    @discardableResult func recordRateLimited(account: UUID, accessToken: String, retryAfter: TimeInterval?, now: Date = Date()) -> Date
    func markNeedsReauth(account: UUID, accessToken: String? = nil)
    @discardableResult func credentialChanged(account: UUID, accessToken: String?) -> Bool
    func forget(account: UUID);  func isNeedsReauth(_ account: UUID) -> Bool;  var needsReauthAccounts: Set<UUID> { get }
    func restoreNeedsReauth(_ accounts: Set<UUID>);  func lastAuthFailure(of account: UUID) -> Date?
}
enum UsageNotice: Equatable {                       // what a card says while NOT asking the server
    case keepCurrent, paused(until: Date), waitingForClaudeCode, resign
    enum Current: Equatable { case nothing, transient, resign, noSubscription }
    static func choose(for decision: UsageDecision, current: Current) -> UsageNotice
}
enum BackupRefreshOutcome: Equatable { case refreshed(String), grantRejected, sameGrantAsAnotherLogin, unreachable, noBackup,
    storeUnavailable, belongsToAnotherAccount(String), rotationLost
    var requiresReauth: Bool;  static func localRefusal(isLive: Bool, sharesLineage: Bool) -> BackupRefreshOutcome? }
// UsageRequestError.swift
enum UsageRequestError: Error, Equatable { case expired, network(offline: Bool), server(status: Int), unexpected(status: Int),
    decode(String), rateLimited(retryAfter: TimeInterval?), forbidden(String)
    static func from(status: Int, body: String, retryAfter: String?) -> UsageRequestError
    static func from(transportError error: Error) -> UsageRequestError
    var keepsLastReading: Bool; var canRetry: Bool; var offersStatusPage: Bool; var message: String
    static func pausedMessage(until: Date) -> String }
// UsageData.swift / CredentialOwnership.swift
struct CredentialOwnership.Login { let accessToken: String; let refreshToken: String?; var expiresAt: Double? /* ms */; var isExpired: Bool }
static func CredentialOwnership.login(fromCredential json: String) -> Login?
static func CredentialOwnership.sameGrant(_ a: Login, _ b: Login) -> Bool          // same access token OR same refresh token
static func CredentialOwnership.detailsBelong(uuid:email:toOwnerUuid:ownerEmail:) -> Bool   // uuid decides when both have one, else email, else false
UsageWindow.resetsAtDate: Date?;  UsageAPIResponse.modelWeeklyLimit(named:) -> UsageLimit?
AutoSwitchEngine.bindingUtilization(_:asOf:requireKnownWindow:) -> Double?;  AutoSwitchEngine.utilization(_:limit:asOf:requireKnownWindow:) -> Double?
```

`UsageNotice.choose` table (`UsageRequestGuard.swift:312-326`): `needsReauth` gives `.resign` unless the card already says resign;
`parked(until)` gives `.paused(until)` unless the card says resign or no-subscription; `waitForClaudeCode` gives `.waitingForClaudeCode` under
the same exception; `coolingDown`, `request`, `refreshFirst` give `.keepCurrent`. The tests for all of these are
`Tests/UnitTests/UsageGuardTests.swift:316-341`.

---

## 5. How each failure is handled and shown (e)

### 5.1 Usage endpoint

Classification is `UsageRequestError.from(status:body:retryAfter:)` (`UsageRequestError.swift:28-36`). Order matters: **a 401, or ANY
status whose body contains the substring `token_expired`, is `.expired` before the status switch runs** (`:29`). Test:
`Tests/UnitTests/UsageErrorTests.swift:16-18`.

| Reply | Error | Guard action | AppState action and card | Reading kept? | Retry button? |
|---|---|---|---|---|---|
| 200, body decodes | none | none | store reading; `accountUsageSampledAt = now`; clear error (`AppState.swift:1723-1728`) | replaced | n/a |
| 200, body does not decode | `.decode` | none | card: "Anthropic sent usage data PixelSwitch couldn't read." (`:84-85`) | cleared | yes |
| **401** (or `token_expired`) | `.expired` | `recordRejected(account, token)`: remembered by token + 20 min cool-down (`AppState.swift:1390`) | Live: `showNotice(.waitForClaudeCode)` "Waiting for Claude Code to renew this login. It renews the next time a session runs." (timer icon, `isRateLimited=true`, reading kept). Saved: renew in place, then ask once more with the new token. If that one also 401s: card "Session expired." (`:1392-1396`) | live: kept; after renewal 401: cleared (`keepsLastReading` false) | no (`canRetry` false) |
| **403** | `.forbidden(body)` | **none recorded** | `markNoSubscription`: reading cleared, card "No active subscription on this account (OAuth not allowed)." with `isNoSubscription=true`; auto-switch leaves such an active account and never moves to it (`:1509-1516`, `1127-1129`) | cleared | no |
| **429** | `.rateLimited(retryAfter)`, `Retry-After` parsed as a plain number (`Double(string)`); an HTTP-date or junk reads as nil (`UsageRequestError.swift:31`) | `recordRateLimited` (park clamp 60..3600, default 120, plus 20 min cool-down) | card: "Usage reading paused until HH:MM. Claude Code itself is not affected." (HH:MM = local short time of the park end); never overwrites a re-sign or no-subscription message (`UsageNotice.choose`) | kept | no (the app waits it out) |
| **5xx** (500...599) | `.server(status)` | none | card: "Anthropic's usage service isn't responding (HTTP \(status)). Your account is fine; PixelSwitch keeps trying." plus a link to `https://status.claude.com`; next cycle asks again (`recordUsageFailure`, `AppState.swift:1523-1534`) | kept | yes |
| No reply, offline codes `notConnectedToInternet`, `networkConnectionLost`, `dataNotAllowed`, `internationalRoamingOff` | `.network(offline: true)` | none | "Can't reach Anthropic. Check your internet connection; PixelSwitch keeps trying." | kept | yes |
| No reply, any other transport error (timeout, DNS, TLS, cancelled) | `.network(offline: false)` | none | "Can't reach Anthropic right now; PixelSwitch keeps trying." | kept | yes |
| Any other status (400, 404, 418, a non-HTTP response = status 0) | `.unexpected(status)` | none | "Unexpected reply from Anthropic (HTTP \(status))." | cleared | yes |

Notes:
- The old one-short-retry after a 429 with a very short `Retry-After` is gone, to stay inside the 20-minute rule
  (`docs/investigations/2026-10-16-usage-429.md:154-156`).
- 5xx and network failures keep their pre-1.9 behaviour: no guard state, so the next cycle asks again. That is deliberate.
- `Retry-After` on a token-endpoint 429 is not used at all: it is "transient" (`ClaudeService.swift:405-406`).
- A renewal that got no verdict (`.unreachable`) is shown through the network line "Can't reach Anthropic right now..." and keeps the reading
  (`AppState.swift:1442-1446`); `.storeUnavailable` shows "Could not save the refreshed sign-in; will retry automatically." (`:1447-1451`);
  `.sameGrantAsAnotherLogin` and `.noBackup` show the re-sign message WITHOUT flagging needs-reauth (`:1437-1441`); `.grantRejected`,
  `.rotationLost`, `.belongsToAnotherAccount` flag needs-reauth and show the re-sign message ("Session expired. Re-authenticate (↻) to fix."
  or "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix.") (`:1351-1357,1474-1479`).
- Switchability: an account is not switchable when it has no backup, is needs-reauth, or its card error has `isExpired` true
  (`AppState.swift:1115-1121`). `isExpired` is true only for the re-sign messages (`showResign`), NOT for "Session expired." after a
  renewal 401 (that goes through `recordUsageFailure` with `isExpired: false`).

### 5.2 Profile endpoint (`ClaudeService.swift:297-334`)

- Not sent at all when `requestGuard.mayUse(accessToken, expiresAt)` is false (`:298-301`).
- 401: `recordRejected(account: account, ...)`; `account` may be nil (owner not yet known), in which case the token goes into
  `refusedWithoutAccount` (`:311-312`).
- 429: `recordRateLimited` only if `account != nil`; `Retry-After` parsed as a number (`:313-316`).
- Any non-200, parse failure, missing `account` object, or transport error returns nil and logs a warning. **No card message is produced**;
  callers treat nil as "owner unproven" (skip backup, follow-the-login, switch checks).
- No other status (403, 5xx) touches the guard.

### 5.3 Token endpoint

See 4.9. Non-200 is mapped only by the OAuth `error` code; allowlisted codes are logged, bodies are NEVER logged (a 200 whose parse failed
contains a live access token, `ClaudeService.swift:387-396`).

---

## 6. What a Linux port must copy exactly (f)

To avoid re-creating the hourly 429 loop (the loop: 49 of 49 429s had at least one 401 on the same account since its last 200; Anthropic
answers about the fourth refused call within about an hour with `429` and `Retry-After: 3600`, on usage and profile alike, decided in a
median 24 ms before the token is checked; `docs/investigations/2026-10-16-usage-429.md:20-39`):

1. **One choke point.** Every request that carries an access token (usage, profile) is gated by the guard, with NO exceptions, including
   switch validation, the post-renewal identity check, the startup audit, and the live-login ownership check every cycle. The 1.9 fix
   is "every path goes through it" (`docs/investigations/2026-10-16-usage-429.md:130-133`).
2. **Process-wide singleton.** The guard state and the poller live in ONE process; the CLI/GUI/MCP talk to it. Two processes
   polling with separate guards would double the rate and not share cool-downs. (Mac: the CLI makes no HTTP of its own.)
3. **Key everything by token fingerprint** (SHA-256, 16 hex), never by account alone, and never persist a token or fingerprint. A park/cool-down/refusal
   dies with its token (`dropWhatIsNotAboutThisToken`). `mayUse` must check ALL accounts' states, because the live token is also some
   account's saved token.
4. **Constants:** cool-down 20 min after any 401 or 429; park `clamp(Retry-After ?? 120, 60, 3600)`; expiry slack 60 s; `expiresAt` is
   milliseconds; a missing `expiresAt` is allowed; `until > now` strict comparisons; FIFO of 64 for account-less refusals.
5. **Never resend a token that got a 401** until it changes, even if `expiresAt` says it is fine (revoked-early tokens, tokens with no `expiresAt`).
6. **Never send an expired token**, usage or profile. Saved login: renew first, then ask once. Live login: send nothing and wait for the
   CLI to rewrite it; do not refresh it yourself and do not re-ask with the same token (no "delegated refresh").
7. **Do not retry within a cycle** after 401 (other than the one renewed-token request) or 429. No automatic retry loops anywhere.
8. **Round-robin = active + one other per cycle**, ordered by `readSoon` first, 1.5 s between requests in a cycle, pollable filtered
   through the guard FIRST. Do not let the pace depend on the number of parked accounts; the 20-minute cool-down is what protects it.
9. **needsReauth** only on the server's `invalid_grant` (or a lost rotation, or a login owned by another account); persist IDs only; restore
   tied to the saved token; a no-token flag must not capture a healthy live login.
10. **`credentialChanged(...)` hook** at every place the stored or live credential changes (sign-in capture, backup updated from live when the
    Claude token changed, renewal, switch for both accounts, adopting a live login). It must compare the Claude access token, not the whole file
    (the credential also carries other logins that rotate hourly) (`docs/investigations/2026-10-16-usage-429.md:136-139`).
11. **Switch path order:** ask the guard before the profile lookup; an expired or refused target is renewed (`wasRejected`) BEFORE any profile
    request; renew even when parked or cooling down, since the token endpoint is outside both limits (`AppState.swift:974-1001`).
12. **Renewal safety:** one renewal in flight per account; refuse same-grant-as-live/other; prove the store is writable before the POST; one retry
    of the save; never rewrite `scopes`; never log bodies.
13. **Honest messages**, because the card must not lie: use the exact sentences in section 5; a 429 pause must never overwrite a re-sign or
    no-subscription message; "API Rate Limited. Try again later." must not exist (`Tests/UnitTests/UsageGuardTests.swift:352-353`).
14. **Headers:** `Authorization: Bearer <token>` and `anthropic-beta: oauth-2025-04-20` on both GETs; the POST gets JSON only. Set an explicit
    User-Agent (the Mac sends URLSession's default; the correct one is unknown). Disable HTTP caching and cookies. Timeouts: 60 s usage,
    15 s profile, 60 s refresh (or tighter; do not make the cycle wait longer than the Mac does).
15. **Port the conformance tests:** `Tests/UnitTests/UsageGuardTests.swift` (fingerprint; expiry at 59 s / 61 s / nil; 401 never resent for 1 s
    to 3 days; account-less 401; park clamp 120/60/3600; 20-minute cool-down boundaries and the 1-to-5-account round-robin simulation;
    token change clears; same token keeps its park; needs-reauth adoption and the no-token flag; the two measured-history replays
    (dead grant over 3 days gives 1 usage request and 1 refresh; stale live login over 24 h gives 0 requests and 0 profile lookups); the
    `UsageNotice` table) and `Tests/UnitTests/UsageErrorTests.swift` (status mapping, `keepsLastReading`, `canRetry`).
16. **Sign-ins must be separate per machine.** Do NOT copy the Mac's saved logins to the Linux laptop. Two machines presenting the same
    refresh grant is a candidate cause of the two revoked grants on 2026-10-10/12 (refresh-token reuse detection, unverified;
    `docs/investigations/2026-10-16-usage-429.md:173-177`), and `refreshOAuthCredentials` rotates the refresh token. Each machine needs
    its own sign-in for each account.

---

## 7. Surprises, risks and gaps found

1. **403 is not recorded by the guard.** A no-subscription account is asked again every cycle (live) or on each round-robin visit
   (saved), indefinitely (`AppState.swift:1409-1416`; `decision` has no 403 state). Whether 403s count toward the limiter is not known.
   Decide whether the Linux port copies this or adds a cool-down (this would be a behaviour change from the Mac).
2. **`token_expired` is matched in ANY body before the status** (`UsageRequestError.swift:29`), so a 429 or 5xx whose body contains the
   substring is treated as an expired token.
3. **Whether the server counts per token or per account is unsettled** (medium to low confidence for per token; three samples),
   and the real threshold and window are unknown (`docs/investigations/2026-10-16-usage-429.md:164-170,178`). The guard works either way
   because it spends at most one request per new token.
4. **No request throttle for healthy tokens**: 24 an hour on the live token at 300 s, 480 an hour at 15 s. Only 13 an hour was measured
   safe. The Settings picker allows 15 s and 30 s.
5. **"Paused until" can be early** when `Retry-After` is under 20 minutes (cool-down outlasts the park), never observed since all 49
   values were 3600 (known limit a, `:208-211`). A revoked live login waits forever with "Waiting for Claude Code" (limit b, `:212-215`).
6. **"Session expired." after a 401 on a just-renewed token** is `isExpired: false`, clears the reading, and leaves the account switchable
   (`AppState.swift:1392-1396`, `1523-1534`), unlike `showResign`. Not flagged needs-reauth, so the next visit goes through the guard
   (rejected fingerprint) to `refreshFirst` after the cool-down.
7. **No explicit timeout on the usage GET and the refresh POST** (default 60 s) and the cycle is serial, so one hung request holds the
   whole cycle (and later timer ticks are skipped by `isRefreshing`) for up to a minute.
8. **The poll does not filter by provider** (`fetchAllAccountUsage` iterates all `accounts`, `AppState.swift:1665`). Harmless on the Mac today.
9. **`active` is set from `claude auth status` every cycle** (`updateActiveAccount`, `AppState.swift:1858-1887`) even though ownership is
   proven by the profile endpoint; known limit e (`docs/investigations/2026-10-16-usage-429.md:223-226`). The Linux port needs both.
10. **The cursor is a plain counter modulo a list that changes size** (`AppState.swift:1697-1698`); fairness is approximate by design.
11. **Not found in the repo:** the full profile response, token lifetime (`expires_in` is logged, tests use 8 h only as a fixture), the exact
    User-Agent the Mac sends, and any evidence on whether Anthropic's limiter keys on IP or User-Agent. A Linux laptop on the same network
    could share an IP-keyed limiter; nothing in the repo tests that.
12. **Split thresholds hook:** today `bindingUtilization` collapses the two windows into one number with `max` (`AutoSwitchEngine.swift:89-98`),
    and the "reached" test on the active side and the candidate ceiling both use it (`AppState.swift:1200-1202,1232-1236,1283`). Splitting
    needs the two `reading(...)` values kept apart; the staleness rules (drop a window whose `resets_at` is in the past; strict on the active
    side) in section 2.1 must apply to each window separately.
