# Recon: remote control, CLI and MCP (for split thresholds, priority, and the Linux port)

Read-only recon, 2026-10-24, PixelSwitch 1.9 (build 30; `project.yml:69,113,145`). Every claim cites
`file:line` in `/Users/ahamade/Documents/GitHub/PixelSwitch`. "Not found" means I looked and it is not there.
No repo file was edited and no repo build was run. One check was run outside the repo: a scratch
program in the session scratchpad compiled `PixelSwitch/Control/ControlProtocol.swift` unchanged with a tiny
`main.swift` to confirm Codable wire behaviour (items marked **[probe]** below). No secret was read or
printed; no control path carries one.

Paths below are relative to the repo root. `CP` = `PixelSwitch/Control/ControlProtocol.swift`,
`API` = `PixelSwitch/Control/ControlAPI.swift`, `SRV` = `PixelSwitch/Control/ControlServer.swift`,
`CAT` = `PixelSwitch/Control/SettingsCatalog.swift`, `STO` = `PixelSwitch/Control/SettingsStore.swift`,
`PAR` = `PixelSwitchCLI/CLIParser.swift`, `RUN` = `PixelSwitchCLI/CLIRunner.swift`,
`CLI` = `PixelSwitchCLI/ControlClient.swift`, `MCP` = `PixelSwitchCLI/MCPServer.swift`,
`CT` = `Tests/UnitTests/ControlTests.swift`, `IT` = `Tests/integration/remote-control.sh`.

---

## 0. The architecture in five lines

```
pixelswitch CLI / `pixelswitch mcp` ──▶ Unix socket (JSON-RPC 2.0, 1 object per line)
   ControlServer (SRV) ─▶ ControlAPI (API, pure) ─▶ AppControlling ◀─ AppController ─▶ AppState / SettingsStore
```

* `ControlProtocol.swift` is the only app file the CLI target compiles (`project.yml:133-139`); the CLI never sees
  `Account`, `AutoSwitchSettings` or `AutoSwitchEngine` (`PAR:161-164` says so in a comment). Handover warns the unit
  harness hides this: it compiles both together, only `scripts/typecheck.sh` (lines 29-35) catches a CLI file that
  uses an app-only type (`docs/SESSION-HANDOVER.md`, "The harness can hide CLI mistakes").
* The app owns the socket from launch to quit, always on, no setting (`RemoteControlService.swift:6-9`, founder answer 2
  in `docs/decisions/2026-09-24-remote-control-answers.md`).
* No token crosses the socket: `CT:434-466` scans every result for keys matching
  `token|credential|secret|refresh|password` (except `lastRefresh`, `refreshInterval`) and for `sk-ant-` text.

---

## 1. (a) The wire protocol

### 1.1 Transport and who may connect

| Fact | Value | Source |
|---|---|---|
| Socket path | `~/Library/Application Support/PixelSwitch/control.sock` (home from `NSHomeDirectory()`) | CP:21-23 |
| Max path bytes | 103 (`sun_path` 104 incl. NUL). Server refuses longer (`StartError.pathTooLong`); client silently truncates to 103 | CP:27, SRV:61, CLI:108 |
| Client path override | env `PIXELSWITCH_CONTROL_SOCKET`; env `PIXELSWITCH_NO_LAUNCH=1` stops the client starting the app. Both exist for the integration script. The **server** path is always `ControlProtocol.socketPath()` | CLI:29-38, RemoteControlService.swift:36 |
| Folder / socket modes | folder created with intermediates then `chmod 0700`; socket `chmod 0600` straight after `bind` (not atomic; the 0700 folder covers the gap); `listen` backlog 16; listener non-blocking | SRV:63-68, 83-85 |
| Stale socket | file exists and `connect` succeeds -> `alreadyRunning`, this copy does NOT serve; file exists and connect fails -> `unlink` and replace | SRV:70-74 |
| Start failure | app keeps running without remote control; state `.failed(msg)` | RemoteControlService.swift:42-49 |
| Peer check | `getpeereid(client)`; uid must equal `getuid()` else the connection is closed at once (logged) | SRV:129-135 |
| Network | none. No TCP listener. Remote use = `ssh host ~/.local/bin/pixelswitch ...` | SRV:4-14 |
| SIGPIPE | `SO_NOSIGPIPE` on accepted fd (Darwin only) | SRV:136-137 |

### 1.2 Framing and concurrency

* UTF-8 text, **one JSON object per line**, terminated by `\n`. The server strips one trailing `\r`, and silently skips
  lines that are empty or only spaces/tabs (SRV:283-292).
* **Max line 1 MiB** (`1 << 20`, CP:18). Server: after pulling complete lines out of the buffer, if the leftover partial line is
  still over the limit the connection is closed (SRV:293, 297). Client: buffer over the limit -> `ClientError.closed` (CLI:151).
  Tested: a client that never sends a newline is cut off and others keep working (CT:537-556).
* Each connection: requests are handled **strictly one at a time in arrival order** (an `AsyncStream` consumed on the main
  actor, SRV:206-212). A slow call (`accounts.switch`) delays later requests on the **same** connection only; connections do
  not wait for each other (SRV header comment, lines 13-15). All handlers run on the main actor, so they serialise with the UI.
* Replies and pushed events share the one stream. A reply has the request's `id`; an event is a notification line with no `id`
  (section 1.9). The Swift client skips any line whose `id` is not the one it awaits (CLI:82-83).
* Writes: a line that cannot be fully written within **2000 ms** (`poll(POLLOUT)`) closes the connection; a subscriber that
  stops reading is dropped at its first stalled write (SRV:44-47, 225-244; CT:592-618).
* Either side closing the socket ends the connection; the server also tears everything down at app quit and `unlink`s the file
  (SRV:98-113, RemoteControlService.swift:58-62).

### 1.3 JSON encoding rules a second implementation must match

All three confirmed by **[probe]** unless a line number is given.

1. **Absent optionals are omitted, never `null`.** Swift's synthesized `Encodable` skips nil. `AccountInfo` with no label/usage encodes
   as `{"effectiveThreshold":90,"email":"a@x.com","id":"1","isActive":false,"isSwitchable":true,"manualOnly":false,"position":1}`.
   (CT:114-115 pins `WindowInfo` the same way.) The only `null`s on the wire are ones the code builds explicitly:
   `{"signIn":null}` (API:123), `"label":null` and `"threshold":null` in CLI requests (RUN:144,148), and event data
   `accountId`/`email` null (RemoteControlService.swift:90-91).
2. **Keys sorted, compact, slashes unescaped** (`ControlCoding.encoder`: `.sortedKeys, .withoutEscapingSlashes`, CP:104-108).
   Tests compare whole lines, e.g. `{"id":7,"jsonrpc":"2.0","result":{"ok":true}}` (CT:90-91). A new server should sort keys and use no
   spaces so golden strings still match.
3. **Numbers are doubles on the wire;** integral ones print without a fraction (`300`, `90`, never `90.0`) and fractions print
   plainly (`72.5`). Int-typed fields (`protocolVersion`, `accountCount`, `position`, `conversationTurns`, `activeCodingMinutes`,
   `linesWritten`, `modelUsage` values, RPC `id`) should be emitted as integers. [probe] The current Swift decoder also accepts `1.0` for an
   Int, but that was measured on Swift 6.4 / macOS 27, and the app's deployment floor is macOS 14 (`project.yml:5-6`), so emit integers.
4. **Dates are ISO 8601 UTC with no fractional seconds** (`2026-09-21T14:13:20Z`, CP:107; CT:116-117). A fractional input was accepted by
   the decoder on this toolchain [probe], but do not rely on it on macOS 14. Exception: `WindowInfo.resetsAt` and the usage `resets_at`
   are **plain strings passed through exactly as the usage API sent them** (e.g. `2026-09-25T18:00:00.000000+00:00`, CT:53,307), not
   re-encoded dates.
5. UUID strings are Swift `uuidString`: **uppercase** hyphenated (`11111111-...`). The resolver compares ids case-insensitively
   (AccountResolver.swift:16), so a lowercase id from a client is fine.

### 1.4 The JSON-RPC envelope

Request (CP:140-146):
`{"jsonrpc":"2.0","id":<int|string>,"method":"<name>","params":{...}}`

* **`jsonrpc` is required** on a request. A request without it fails decoding and gets a parse error [probe].
* `id`: int or string. **`id` absent or `null` = notification**: the call is executed, **no reply is ever sent, even on error**
  (API:73,76,79). A fractional id such as `1.5` makes the whole request undecodable (parse error); `1.0` is accepted as int `1` [probe].
* `params` optional; absent is read as `{}` (API:214). **Unknown parameter keys are silently ignored** [probe]. The only reason a
  misspelt key is `invalidParams` is that the real key is then missing (CT:320).
* Param decode failure -> `invalidParams`, message `The parameters for <method> are missing or the wrong shape.` (API:216).

Response (CP:154-172): `{"id":N,"jsonrpc":"2.0","result":{...}}` or
`{"error":{"code":-32003,"data":{"candidates":["a","b"],"kind":"ambiguous"},"message":"..."},"id":N,"jsonrpc":"2.0"}`.
`result` and `error` are never both present (CT:90-94). **Parse errors and any reply whose id is nil omit `id` entirely** [probe]
(`{"error":{...},"jsonrpc":"2.0"}`), which strict JSON-RPC clients would not like; the Swift client simply never matches it and times out.
`jsonrpc` other than `"2.0"` -> `invalidRequest` with the request's own id (API:66-68).

Error kinds (`ControlErrorKind`, CP:188-224). `data.kind` is the canonical discriminator; `data.candidates` (optional string array)
lists the choices. The client maps by `data.kind`, else by code (CP:243-247):

| kind | code | meaning | CLI exit (RUN:12-19) |
|---|---|---|---|
| parse | -32700 | not JSON-RPC | 1 |
| invalidRequest | -32600 | `jsonrpc` != 2.0 | 1 |
| methodNotFound | -32601 | candidates = all 19 method names (API:87-88) | 1 |
| invalidParams | -32602 | params missing/wrong shape | 2 |
| busy | -32001 | switch or sign-in in progress | 3 |
| notFound | -32002 | no account/setting/sign-in matches | 4 |
| ambiguous | -32003 | >1 account matches; candidates listed | 4 |
| invalidValue | -32004 | out of range / wrong shape | 2 |
| claudeUnavailable | -32005 | `claude` binary not found | 1 |
| protocolMismatch | -32006 | client-side only (never sent by the app) | 1 |
| failed | -32000 | anything else; unknown codes map here | 1 |

### 1.5 Protocol version and how it is negotiated

* `ControlProtocol.version = 2` (CP:13). Doc comment (CP:10-12): "Bumped whenever a method, parameter or result changes shape. The CLI
  refuses to talk to an app with a different number." History: bumped 1 -> 2 in 1.5 when `AccountInfo` gained `manualOnly`
  (ARCHITECTURE.md:575; CT:84-87). So **adding a field has been treated as a version bump before.**
* **There is no handshake.** The app never asks the client anything. The CLI's first call after connecting is `status.get`, and it
  demands **strict equality** `status.protocolVersion == 2` (RUN:62-68). On mismatch: `ControlError(.protocolMismatch)` with
  `PixelSwitch <appVersion> speaks remote-control protocol <N>, but this pixelswitch speaks 2. Quit and reopen PixelSwitch, then run the command again.`
  (exit 1). The text assumes "old app still running after an update".
* `pixelswitch mcp` checks once, on first use (MCP:239-243); on `ClientError.closed` it reconnects and retries **without** re-checking
  (MCP:246-253). Known deferred defect (`docs/SESSION-HANDOVER.md`, "MCP protocol re-check").
* `StatusInfo.appVersion` is the app's `CFBundleShortVersionString` (AppController.swift:24-26). `pixelswitch --version` prints
  `pixelswitch (PixelSwitch remote-control protocol 2)`, not the app version (RUN:37-39; known defect).
* Discovery of capabilities: **not found.** There is no `features`/`capabilities` field anywhere. A client can only probe by
  calling and reading `methodNotFound`/`notFound`.
* `IT:47` asserts `protocolVersion == 2`. Any third-party script that does the same breaks on a bump.

### 1.6 Methods: parameters and results (the full list, 19)

`ControlMethod` (CP:252-272), order pinned by CT:102-106. Parameter structs are CP:277-310; the dispatch is API:85-208.
"account" always means an account reference (section 1.8).

| # | method | params | result | notes |
|---|---|---|---|---|
| 1 | `status.get` | none | `StatusInfo` (1.7) | API:91-92, 224-237 |
| 2 | `accounts.list` | none | `{"accounts":[AccountInfo]}` in priority order | API:94-95 |
| 3 | `accounts.switch` | `{"account":s}` | `{"account":AccountInfo}` after the switch | awaits the full switch; resolver errors first; `busy`/`claudeUnavailable`/`failed` from AppController.swift:75-82, 208-214. CLI waits `max(timeout,120)` s |
| 4 | `accounts.addCurrent` | none | `{"account":AccountInfo}` | saves the account Claude Code is signed in as; `busy` if switching/logging in (AppController.swift:84-91, 202-206) |
| 5 | `accounts.signIn.start` | `{"account"?:s,"open"?:s}` | `{"signIn":SignInInfo}` | no `account` = new account, else re-sign. `open`: absent/blank/`none` = open nothing, `default` = default browser, else a browser name (case-insensitive name or bundle id) else `invalidValue` with candidates `default`+installed names (API:108-120, AppController.swift:93-126). `busy`, `claudeUnavailable` |
| 6 | `accounts.signIn.status` | none | `{"signIn":SignInInfo\|null}` | current or last (until dismissed) |
| 7 | `accounts.signIn.submitCode` | `{"code":s}` | `{"signIn":SignInInfo}` | `notFound` "No sign-in is running."; `invalidValue` if not waiting for a code or "That isn't the whole code. Copy all of it, including the part after #." (AppController.swift:128-139) |
| 8 | `accounts.signIn.cancel` | none | `{"signIn":SignInInfo}` | `notFound` if none; `busy` while `completing` (AppController.swift:141-150) |
| 9 | `accounts.remove` | `{"account":s}` | `{"removed":AccountInfo}` (as it was before removal) | `busy` if switching/logging in; deletes the saved login; if it was active, falls back to first non-manual-only account (AppState.swift:738-760) |
| 10 | `accounts.setLabel` | `{"account":s,"label"?:s\|null}` | `{"account":AccountInfo}` | trimmed; blank/null clears; > 100 chars -> `invalidValue` "A label can be at most 100 characters." (API:138-147) |
| 11 | `accounts.setThreshold` | `{"account":s,"threshold"?:n\|null}` | `{"account":AccountInfo}` | null/absent = follow default; number rounded to a whole number; allowed 0-100 (0 = manual only); else `invalidValue` "A threshold must be 1–100, 0 for manual only, or null to follow the default." (API:149-161) |
| 12 | `accounts.setOrder` | `{"accounts":[s]}` | `{"accounts":[AccountInfo]}` new order | must name every account exactly once; else `invalidValue` "List every account exactly once, in the order you want." with all accounts as candidates; `invalidValue` "The accounts changed while the order was being set. Try again." if the app refused (API:163-173) |
| 13 | `usage.get` | `{"account"?:s}` | `UsageReport` (1.7) | whole list or one account |
| 14 | `usage.refresh` | none | `UsageReport` for all | awaits `appState.refresh()`; CLI waits >= 120 s |
| 15 | `settings.get` | `{"key"?:s}` | `{"settings":{"<key>":value,...}}` | all 22 or one; unknown -> `notFound` + all keys as candidates; key match case-insensitive (CAT:141-145) |
| 16 | `settings.set` | `{"key":s,"value":any}` | `{"settings":{"<key>":newValue}}` | `value` is normalised first (section 2); invalid -> `invalidValue` "<key> must be <summary>." |
| 17 | `events.subscribe` | none | `{"subscribed":true}` | marks this connection; events follow (1.9) |
| 18 | `app.checkForUpdates` | none | `{"ok":true}` | opens the updater window on that Mac (AppController.swift:184-186) |
| 19 | `app.quit` | none | `{"ok":true}` | terminates 0.3 s after the reply (AppController.swift:189-191) |

Log line per call (never an email, link or argument): `[control] <method><detail> ok|<kind>|failed` where detail is
` <accountUUID>`, ` reauthenticate <uuid>`, ` newAccount`, or ` <settingKey>` (API:69-80; asserted CT:426-429).

### 1.7 Result types (exact JSON keys, optionality in brackets)

`StatusInfo` (CP:345-355): `appVersion` s, `protocolVersion` int, `activeAccountId` s [opt], `activeAccountEmail` s [opt],
`accountCount` int, `claudeAvailable` b, `lastRefresh` date [opt], `autoSwitch` AutoSwitchInfo, `signIn` SignInInfo [opt].

`AutoSwitchInfo` (CP:314-321): `enabled` b, `defaultThreshold` n, `onFable` b, `strategy` s (`mostRoom|myOrder|resetsSoonest`),
`drainEarly` b, `drainWithinHours` n. Source: AppController.swift:32-41 (`enabled` = `UserDefaults.bool("autoSwitchEnabled")`, default false).

`AccountInfo` (CP:382-401), all non-optional except marked:
`id` s (UUID), `email` s, `label` s [opt, never empty], `subscription` s [opt, first letter capitalised: "Max"/"Pro"],
`isActive` b, `isSwitchable` b (has saved login AND not flagged needs-reauth/expired, AppState.swift:1115-1121),
`position` int (1-based index in the priority list = list order), `threshold` n [opt] (own value 0-100; 0 = manual only; absent = follows default),
`effectiveThreshold` n (where auto-switch moves off it while active: own value, else default; **also the default when manual only**,
API:254-255, AutoSwitchEngine.swift:546-548), `manualOnly` b (own threshold normalises to 0, API:286), `usage` UsageInfo [opt].

`UsageInfo` (CP:371-380): `session` WindowInfo [opt] (5-hour = API field `five_hour`), `weekly` [opt] (`seven_day`), `fable` [opt] (the `limits[]` entry whose
`scope.model.display_name` is "Fable"), `extraUsage` ExtraUsageInfo [opt] `{enabled?,monthlyLimit?,usedCredits?,utilization?}`,
`sampledAt` date [opt] (non-active accounts are polled in turn, so a reading may be several refreshes old), `error` s [opt].
`WindowInfo` = `{utilization?: n (percent 0-100), resetsAt?: s}`; a window is omitted unless it has a utilization or a reset (API:293-296).
The whole `usage` object is omitted when there is neither a reading nor an error (API:292).

`UsageReport` (CP:413-417): `accounts` [AccountInfo], `machine` MachineUsageInfo, `lastRefresh` date [opt].
`MachineUsageInfo` (CP:404-411): `todayCost` n, `totalCost` n, `conversationTurns` int, `activeCodingMinutes` int, `linesWritten` int,
`modelUsage` {string:int}.

`SignInInfo` (CP:323-343): `id` s, `purpose` s (`newAccount|reauthenticate`), `accountId` s [opt], `email` s [opt],
`state` s (`starting|waitingForUser|completing|succeeded|failed|cancelled`), `message` s [opt], `resultAccountId` s [opt],
`automaticLink` s [opt] (finishes by itself in any browser on the Mac running PixelSwitch), `manualLink` s [opt] (shows a code to hand back with
`submitCode`), `notice` s [opt], `codeSubmitted` b, `startedAt` date. (Sign-in internals are covered by `recon/signin.md`.)

### 1.8 Account references (`AccountResolver`, AccountResolver.swift:10-37)

Trimmed, case-insensitive, tried in this order; the first level with any match wins; two matches at one level = `ambiguous`
(candidates `email (label)`):
1. account id (UUID), 2. email, 3. label, 4. 1-based position `1...count` (so a label "2" beats position 2, CT:134-136).
Empty -> `invalidValue` "Name an account: its email, label, id or position."; no match -> `notFound` "No account matches "<ref>"." with every
account as candidates. `describe` = `email` or `email (label)` (line 40-43). `resolveAll` refuses the whole list if any entry fails.

### 1.9 Events

`events.subscribe` marks only the calling connection (`markSubscribed`, SRV:202-204); there is no unsubscribe, no replay, no filter.
The connection still answers further requests. Event line (CP:175-183, 419-428, RemoteControlService.swift:54):
`{"jsonrpc":"2.0","method":"event","params":{"at":"<iso>","data":{...},"type":"<type>"}}`

| type | `data` | emitted when (RemoteControlService.swift) |
|---|---|---|
| `activeAccountChanged` | `{accountId: s\|null, email: s\|null}` | `$activeAccount` id changes, first value dropped (84-93) |
| `usageUpdated` | `{}` | `$lastUsageRefresh` changes (95-99) |
| `autoSwitched` | `{from: uuid, to: uuid, limit: "windows"\|"fable", trigger: "threshold"\|"drainEarly"\|"noSubscription"}` | `$lastAutoSwitch` set after a completed auto switch (101-108; AppState.swift:1263) |
| `signInChanged` | `{signIn: SignInInfo}` | session state/link change (110-133) |
| `error` | `{message: s}` | `$errorMessage` becomes non-nil and differs from last (114-118) |

**Not emitted**: label, threshold, order or settings changes (no hub subscription for them). A watcher cannot see a priority or threshold change.
`pixelswitch watch` prints each event's **`params` only**, compact (`{"at":...,"data":{...},"type":"usageUpdated"}`), RUN:163-169.

---

## 2. (b) Every setting key (`SettingKey`, CAT:9-31)

22 keys, in this order (the order is pinned: `SettingKeyNames.all` in RUN:388-398 must equal `SettingKey.allCases`, tested CT:742).
Defaults are "what `settings.get` returns when nothing was ever saved" (STO:23-74). Store = `UserDefaults.standard`
(bundle `ai.pixelventures.pixelswitch`).

| key | type / accepted (CAT:47-63) | default | UserDefaults key | side effect on set (STO:80-147) |
|---|---|---|---|---|
| `refreshInterval` | choice number: 15, 30, 60, 300, 600 (seconds) | 300 | `refreshInterval` | `appState.startAutoRefresh(interval:)` |
| `transcriptLookbackHours` | choice: 24, 72, 168, 720, 0 (0 = all history) | 24 | `transcriptLookbackHours` (stored as Int) | `appState.refresh()` |
| `autoSwitch.enabled` | bool | false | `autoSwitchEnabled` | none |
| `autoSwitch.threshold` | number 50-100, step 1 (rounded, then range-checked) | 90 (unset or stored 0 reads 90; stored value clamped to 50-100 on read, AutoSwitchEngine.swift:494-497) | `autoSwitchThreshold` | none |
| `autoSwitch.onFable` | bool | true | `autoSwitchOnFable` | none |
| `autoSwitch.strategy` | choice string: `mostRoom`, `myOrder`, `resetsSoonest` (case-insensitive in, canonical out) | `mostRoom` | `autoSwitchStrategy` | none |
| `autoSwitch.drainEarly` | bool | true | `autoSwitchDrainEarly` | none |
| `autoSwitch.drainWithinHours` | number 1-72, step 1 | 24 | `autoSwitchDrainWithinHours` | none |
| `maskEmailAddresses` | bool | false | `maskEmailAddresses` | none |
| `colorCodeAccounts` | bool | true | `colorCodeAccounts` | none |
| `appLanguage` | choice: `auto`, `en`, `zh-Hans`, `ja`, `de`, `fr` | `auto` | `appLanguage` | writes/removes `AppleLanguages` |
| `launchAtLogin` | bool | live `SMAppService.mainApp.status == .enabled` | (system) | register/unregister the login item; failure -> `failed` |
| `menuBar.showsHeadIcon` | bool | true | `menuBarShowsHeadIcon` | none |
| `menuBar.modules` | list, no repeats, drawn from `account, sessionBar, sessionBarPlain, weeklyBar, weeklyBarPlain, fableBar, dailyCost, sessionReset, weeklyReset` (array or comma text; case-insensitive; unknown name refused) | `[account, sessionBarPlain, weeklyBarPlain]` for new installs (migration, MenuBarModule.swift:78-92; upgraders who hid the account name get `[]`) | `menuBarModules` (JSON `[String]` as Data) | none |
| `menuBar.customizesLimitBarColors` | bool | false | `customizesLimitBarColors` | none |
| `menuBar.color.session` | colour `#RRGGBB` (leading `#` optional, stored upper-case) | `#34C759` | `sessionLimitBarColor` | none |
| `menuBar.color.weekly` | colour | `#0A84FF` | `weeklyLimitBarColor` | none |
| `menuBar.color.fable` | colour | `#AF52DE` | `fableLimitBarColor` | none |
| `menuBar.color.lowRemaining` | colour | `#FF3B30` | `lowRemainingLimitBarColor` | none |
| `menuBar.lowRemainingWarningThreshold` | number 0-100, no step (fractions kept) | 30 | `lowRemainingWarningThreshold` | none |
| `claude.binaryPath` | `""`/`auto` = auto-detect, else an absolute path to an executable file (`invalidValue` "No executable file at <path>.") | `""` | `claudeBinaryPathPreference` | `ClaudeService.setPath`, then `refresh()` |
| `updates.automatic` | bool | Sparkle's: saved `SUAutomaticallyUpdate`, else Info.plist (true), else false | Sparkle's | `updateChecker.setInstallsAutomatically`; `failed` "The updater is not running yet..." if called too early |

Normalisation (`SettingKey.normalize`, CAT:81-138), all case-insensitive where text:
* bool accepts `true/on/yes/1`, `false/off/no/0` as text, `0`/`1` as number, real bools.
* choice: a number matches exactly (so `45` fails, `"60"` as text works), a string matches case-insensitively.
* number: accepts numeric text; `NaN`/infinity refused; with `step` it rounds half away from zero (`(n/step).rounded()*step`) **then** range-checks
  (so `72.4` -> `72`, `49.6` -> `50` is accepted, `49` refused).
* list: array of strings or comma-separated text; duplicates dropped; canonical casing.
* path: trimmed; `auto` or empty -> `""`; must start with `/`.
* Error text: `<rawValue> must be <summary>.` with summaries (CAT:66-76): `true or false`; `one of 15, 30, 60, 300, 600` (string choices are JSON-quoted:
  `one of "auto", "en", ...`); `50–100, whole numbers` (an en dash); `0–100`; `a list drawn from account, sessionBar, ...`; `a colour as #RRGGBB`;
  `"" to auto-detect, or an absolute path`.

---

## 3. (c) The CLI and the MCP server

### 3.1 CLI parsing (`CLIParser`, PAR:61-122)

* Global flags, anywhere: `--json`; `--timeout <seconds>` (positive number, default 30); `-h`/`--help` (replaces everything with `help`); `--version`.
  Everything else is a word. No words -> help.
* Exit codes: 0 ok, 1 failed, 2 usage/invalid value, 3 busy, 4 no such (or ambiguous) account, 5 PixelSwitch unreachable (RUN:4-20, help PAR:239).
  A parser error prints `error: <message>` to stderr and exits 2 (PixelSwitchCommand.swift:24-26).
* Connection: `connect()` tries the socket; if nothing answers and launching is allowed it runs `/usr/bin/open -g -b ai.pixelventures.pixelswitch`,
  then polls every 250 ms up to 10 s (CLI:44-64). Failure text: `PixelSwitch is not reachable: ...`; transport failures are exit 5 for `unreachable`, 1 for timeout/closed.
* Errors print `error: <message>` and, when candidates exist, `  choices: a, b, c` (RUN:47-50), both on stderr.

### 3.2 Every command (syntax -> control call -> human output)

`--json` prints the **method's raw `result`** pretty-printed (indented, sorted keys, unescaped slashes, CP:95-99) with the exceptions noted.

| Syntax (PAR) | Calls | Human output (RUN) |
|---|---|---|
| `help` / no args / `-h` | none | the help text (PAR:206-240) |
| `version` / `--version` | none | `pixelswitch (PixelSwitch remote-control protocol 2)` |
| `status` | `status.get` | block below |
| `accounts` / `accounts list` | `accounts.list` | table below |
| `switch <account>` | `accounts.switch` (slow) | `Switched to <name>.` + if manual only ` It is manual only: auto-switch moves you off it at <N>% and won't move you back to it.` |
| `accounts add-current` | `accounts.addCurrent` (slow) | `Saved <name>.` |
| `accounts sign-in [--open \| --open-in <browser>] [--wait]` | `accounts.signIn.start`, then polls `signIn.status` every 0.5 s up to 15 s while state is `starting` | `CLIOutput.signIn` (below). With `--wait`: prints it, then `… <state words>` on each state change, polls every 1 s up to 16 min; exit 0 only if `succeeded`, else 1 |
| `accounts reauth <account> [same flags]` | same, with `account` | same |
| `accounts sign-in status` | `accounts.signIn.status` | sign-in text, or `No sign-in has been started.` |
| `accounts sign-in code <code>` | `accounts.signIn.submitCode` | `Code handed to Claude Code; it is checking it now.` |
| `accounts sign-in cancel` | `accounts.signIn.cancel` | `Sign-in cancelled.` |
| `accounts remove <account> --yes` | `accounts.remove` | `Removed <name>.` Without `--yes`: usage error (exit 2) "Removing an account deletes its saved login. Add --yes to confirm: ..." |
| `accounts label <account> <text...>` or `... --clear` | `accounts.setLabel` | `Label set: <name>.` / `Label cleared.` (text = remaining words joined by one space) |
| `accounts threshold <account> <1-100\|default\|manual>` | `accounts.setThreshold` | see `thresholdSet` below. `75%` accepted (percent sign stripped); `default` -> null; `manual` or `0` -> 0; `off`/`never`/`disable` deliberately not aliases (PAR:161-168) |
| `accounts order <account> <account> ...` | `accounts.setOrder` | the accounts table in the new order |
| `usage [<account>]` | `usage.get` | usage text below |
| `refresh` | `usage.refresh` (slow) | usage text |
| `watch` | `events.subscribe`, then loops | stderr `Watching PixelSwitch. Press Control-C to stop.` (not with `--json`), then one compact `params` JSON per event on stdout, unbuffered; ends with `error: PixelSwitch closed the connection.` (exit 1) if the app goes away |
| `settings [<key>]` | `settings.get` | lines `key = <compact JSON>` in `SettingKeyNames.all` order, unknown keys after, alphabetical |
| `settings set <key> <value...>` | `settings.set` | same format for the changed key. The value words are joined by a space, then parsed as JSON if they parse (`true`, `90`, `["a","b"]`, `"x"`), else sent as a string (RUN:192-195) |
| `update-check` | `app.checkForUpdates` | `PixelSwitch is checking for updates; its window opens on that Mac.` |
| `quit` | `app.quit` | `PixelSwitch is quitting.` |
| `mcp` | none (runs the MCP server) | stdin/stdout MCP, section 3.4 |

"slow" calls wait `max(--timeout, 120)` s, others `--timeout` (RUN:73-75). Unknown command / wrong arity -> `Usage: pixelswitch ...` and exit 2.
`--json` oddities: `accounts sign-in` / `reauth` print the **unwrapped** `SignInInfo` (RUN:112,124), while `accounts sign-in status --json` prints the
**wrapped** `{"signIn":{...}}` (RUN:129); `IT:90,100` depends on both. `--json` for `accounts remove` etc. prints the raw `{"removed":{...}}`.

**`status` text** (RUN:250-266), literal spacing:
```
PixelSwitch 1.9 (remote-control protocol 2)
Active:        <email or "none">
Accounts:      <n>
Auto-switch:   <off | on, default 90%, next by <most room left|my order|resets soonest>[, Fable ignored][, switches early within 24 h | , no early switching]>
Claude CLI:    found | not found
Last refresh:  2026-10-24 17:08          (local time yyyy-MM-dd HH:mm, "—" when none)
Sign-in:       <state words>              (only when a sign-in exists)
```
(`Fable ignored` only when on and `onFable` false; the early-switching clause only when strategy is `resetsSoonest`.)

**`accounts` table** (RUN:268-290): header row `["", "#", "Account", "Session", "Weekly", "Fable", "Switches at"]`; each row: marker (`*` active,
`!` not switchable, else empty), `position`, `email` or `email (label)`, `session%`, `weekly%`, `fable%` (whole percent, `—` when unknown), and the
"Switches at" cell = `<effectiveThreshold>%` + ` (manual only)` or ` (default)` (when `threshold` is absent) or nothing (RUN:293-295). Columns padded to the widest
cell (character count), joined by two spaces, trailing blanks trimmed. Then the legend line
`* active   ! cannot be switched to until it is re-signed (pixelswitch accounts reauth <account>)` and, if any account is manual only,
`manual only: auto-switch never moves you to it; switch to it yourself with pixelswitch switch <account>`. Empty list:
`No accounts yet. Add one: pixelswitch accounts add-current`. **Gotcha:** the accounts list is decoded with `try?` and falls back to `[]` (RUN:197-199), so if
`AccountInfo` ever fails to decode, the CLI prints "No accounts yet" instead of an error (this matters for section 4).

**`thresholdSet`** (RUN:298-310): manual only -> `<email> is now manual only: auto-switch never switches to it. You can still switch to it yourself.` (+ ` You're using it now: auto-switch moves you off it at <N>% and won't move you back to it.` if active); no own threshold -> `<email> now follows the default threshold (<N>%).`; else `<email> now switches at <N>%.`

**`usage` text** (RUN:321-339):
```
1. alice@work.com (Work)  [active]
   Session 42% (resets 2026-09-25 11:00)
   Weekly  61% (resets 2026-09-30 02:00)
   Fable   10% (resets ...)            (only if present)
   Note: <usage.error>                 (only if present)
   Read at 2026-09-25 12:00            (only if sampledAt)
   No reading yet.                     (when the account has no usage object)
This Mac today: $12.50, 30 turns, 95 min coding, 400 lines written
```
`resets` strings are parsed as ISO 8601 (with or without fractional seconds) and shown in **local** time `yyyy-MM-dd HH:mm`.

**sign-in text** (RUN:341-367): first line `New account: <words>` or `Re-sign <email>: <words>`; state words: `starting` "getting the sign-in link",
`waitingForUser` "waiting for someone to sign in in a browser" (or "checking the code" once a code was sent), `completing` "saving the account", `succeeded`
"signed in", `failed` "failed: <message>", `cancelled` "cancelled". Then, if present: `On the Mac running PixelSwitch, open this in any browser (it finishes by itself):` + `  <automaticLink>`;
`On another device, open this; the page then shows a code:` + `  <manualLink>` + `  then run: pixelswitch accounts sign-in code <code>`; then `notice`.

### 3.3 Install and remote use

`CommandLineToolInstaller` links `~/.local/bin/pixelswitch` -> `<app>/Contents/Helpers/pixelswitch` (a link, so Sparkle updates keep it current), never overwrites
a file that is not PixelSwitch's (CommandLineToolInstaller.swift:31-64). MCP config for another Mac:
`{"mcpServers":{"pixelswitch":{"command":"ssh","args":["<host>","/Users/<u>/.local/bin/pixelswitch","mcp"]}}}` (same file:73-79; absolute path because a non-interactive SSH shell may lack `~/.local/bin`).
Because the CLI always runs **on the same machine as the app** (over ssh), CLI and app versions match except for the window after an update when the old app is still running.

### 3.4 MCP server (`pixelswitch mcp`, MCP:15-266)

* Transport: newline-delimited JSON-RPC on stdin/stdout, blank lines skipped, nothing but protocol messages on stdout (MCP:257-263). Notifications (no/null `id`) get no reply (MCP:128-129).
  Parse error reply: `{"id":null,"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"}}`.
* Two eras. **Modern** (`2026-07-28`): the request carries `params._meta["io.modelcontextprotocol/protocolVersion"]`. Unsupported -> error `-32022`
  "Unsupported protocol version" with `data:{supported:[...],requested:"..."}`; missing `params._meta["io.modelcontextprotocol/clientCapabilities"]` -> `-32602`
  (MCP:135-143). Results get `resultType:"complete"` and `_meta:{"io.modelcontextprotocol/serverInfo":{name,version}}` (MCP:211-216). `server/discover` returns
  `{resultType,supportedVersions,capabilities:{tools:{}},_meta,instructions}`. **Legacy** (`2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`): `initialize` returns the
  asked version if known else `2025-11-25`, `capabilities:{tools:{}}`, `serverInfo:{name:"pixelswitch",version}`, and the `instructions` string (MCP:21-28, 146-155).
  Methods: `initialize`, `server/discover`, `ping` (`{}`), `tools/list`, `tools/call`; anything else `-32601` "Method not found: <m>".
* `serverInfo.version` = the app version read from the Info.plist next to `Contents/Helpers` (PixelSwitchCommand.swift:12-17), `unknown` outside the app.
* **A tool's `arguments` object is forwarded verbatim as the control `params`** (`pass`, MCP:53), so tool property names ARE control parameter names. Tools with no arguments
  send no params.
* Result of a call: `{"content":[{"type":"text","text":"<pretty JSON of the control result>"}],"structuredContent":<result object, or {"value":result}>,"isError":false}`.
  Failure is a normal result with `isError:true` and text `<kind>: <message>` (+ `\nChoices: a, b`), or `unreachable: PixelSwitch is not reachable: ...`, or `failed: ...`
  (MCP:185-205). Missing required argument (absent or null): `invalidParams: <tool> needs <key>.` without calling the app. Unknown tool: JSON-RPC error `-32602` "Unknown tool: <name>".
* Timeouts: 120 s for `usage.refresh` and `accounts.switch`, 30 s for everything else (MCP:245) — **`add_current_account` gets only 30 s although the CLI gives it 120**.
  On `ClientError.closed` it reconnects and retries once with 120 s (MCP:246-253).
* `inputSchema` for every tool: `{"type":"object","properties":{...},"required":[...],"additionalProperties":false}` (MCP:105-118); the server does not enforce `additionalProperties`.

The 18 tools (MCP:55-97; set pinned by CT:815-817, count by CT:803,820 and IT:119):

| tool | control method | input properties (required in bold) |
|---|---|---|
| `get_status` | status.get | none |
| `list_accounts` | accounts.list | none |
| `get_usage` | usage.get | `account` string (optional) |
| `refresh_usage` | usage.refresh | none |
| `switch_account` | accounts.switch | **`account`** string |
| `add_current_account` | accounts.addCurrent | none |
| `remove_account` | accounts.remove | **`account`** string |
| `start_sign_in` | accounts.signIn.start | `account` string (opt), `open` string (opt; "default" or a browser name) |
| `get_sign_in_status` | accounts.signIn.status | none |
| `submit_sign_in_code` | accounts.signIn.submitCode | **`code`** string |
| `cancel_sign_in` | accounts.signIn.cancel | none |
| `set_account_label` | accounts.setLabel | **`account`** string; `label` type `["string","null"]` |
| `set_account_threshold` | accounts.setThreshold | **`account`** string; `threshold` type `["number","null"]`, `minimum` 0, `maximum` 100 |
| `set_account_order` | accounts.setOrder | **`accounts`** array of string |
| `get_settings` | settings.get | `key` string (opt) |
| `set_setting` | settings.set | **`key`** string, **`value`** (no type; any JSON) |
| `check_for_updates` | app.checkForUpdates | none |
| `quit_app` | app.quit | none |

Descriptions (copy verbatim from MCP:55-97 for parity) matter: `list_accounts` documents `manualOnly`/`effectiveThreshold`; `set_account_threshold` documents 0 = manual only,
null/omitted = follow default, "100 uses the account until it is empty"; `get_settings` lists example keys including `autoSwitch.strategy (mostRoom, myOrder, resetsSoonest)`.

---

## 4. (d) What must change to expose split thresholds and an explicit priority

Goal: separate 5-hour (session) and 7-day (weekly) leave-at thresholds, global and per account, plus a set/show priority number, **without** breaking a protocol-2
client, the old CLI syntax, or the old settings keys.

### 4.1 Facts that constrain the design

1. **Strict version equality** (RUN:62-68). If the new app reports `protocolVersion: 3`, every already-shipped CLI (and `IT:47`, and any third-party script that checks it) refuses it.
   Therefore: **keep `ControlProtocol.version = 2` and make every change additive.** Introduce a separate, optional capability signal instead (see 4.3).
2. **The synthesized decoders reject a missing non-optional key.** A new CLI talking to an old app (the update-skew window) decodes `AccountInfo` with `try?` and prints
   "No accounts yet" if a new `let` field is missing (RUN:197-199). **Every new field must be Optional in the Swift struct** (and the app always sends it). Also declare new
   stored properties as `var x: T?` (not `let`): a `var` optional gets an implicit `nil` default in the synthesized memberwise init, so the many existing test call sites that build
   `AccountInfo(...)`, `StatusInfo(...)`, `AutoSwitchInfo(...)` (CT:84,198,696-711,726-729,750-752) keep compiling. A `let` optional would force every call site to change.
3. **Unknown params are silently ignored by the old app** [probe]. If the new CLI sends `{"account":"2","threshold":60,"window":"weekly"}` to an old app via the existing
   `accounts.setThreshold`, the old app sets the single legacy threshold to 60 for both windows: a silent wrong result. **New behaviour must be new methods** (an old app answers `methodNotFound`
   with its 19-name candidate list, API:87-88: loud and safe), not new parameters on old methods.
4. **`Double?` cannot tell "absent" from "null"** (`ThresholdParams.threshold`, CP:298; [probe] both decode to nil), and null already means "follow default". New methods that need "leave unchanged"
   vs "clear" must read the raw `request.params` object for key presence (`JSONValue` supports that, CP:67-70) rather than decode into a `Double?` struct.
5. **`AccountInfo.effectiveThreshold` is a single number** that the old CLI prints as "Switches at" and scripts read. With two thresholds there is no single truth; pick a stable summary (4.2).
6. The engine currently has one threshold per account compared against the **max of 5-hour and weekly** (`bindingUtilization`, AutoSwitchEngine.swift:89-98; `plan` loops `[.windows, .fable]`, 317-326;
   candidates' ceiling uses the same single threshold, 58-61). Splitting needs the engine, `AppState.effectiveSwitchThreshold` (686-690) and `setSwitchThreshold` (695-709), and `Account`
   (Account.swift:45) changed. That is another recon's subject; this file only lists what the **control layer** needs from them.
7. Priority already exists as list order: `position` is the 1-based index (API:240, 245-246), it is set only by `accounts.setOrder` (a full list) and used by `AutoSwitchStrategy.myOrder`
   (top-down) and as the tie-break in the other two strategies (AutoSwitchEngine.swift:416-433). There is no "set this account's priority number" call and no field named priority. Priority only
   changes behaviour when `autoSwitch.strategy` is `myOrder` (default is `mostRoom`).

### 4.2 Recommended data model (what the wire must be able to express)

Layering, most specific wins: **account window value -> account both-value (`threshold`, existing) -> global window value -> global both-value (`autoSwitch.threshold`, existing) -> 90**.

* Per account: existing `threshold` (0-100, 0 = manual only, null = follow) stays and means "both windows". New optional `sessionThreshold` and `weeklyThreshold` (1-100 or null = follow the next layer).
  Manual only stays account-level (`threshold == 0`) and wins over any window value (open question Q3).
* Global: existing `autoSwitch.threshold` (50-100) = "both"; new `autoSwitch.sessionThreshold`, `autoSwitch.weeklyThreshold` (override the base; absent = follow base).
* Backward-compatible readers (an old CLI or script): `threshold` unchanged; `effectiveThreshold` = **the lower of the two effective leave-at values** (so with no split values it is byte-identical
  to today, and with a split an old reader sees the earliest trigger rather than a number that overstates headroom); `AutoSwitchInfo.defaultThreshold` = the base `autoSwitch.threshold` (today's meaning).
* Backward-compatible writers (a legacy call): `accounts.setThreshold` and `settings.set autoSwitch.threshold` mean "set both windows", so after the call both effective values equal what was written: **clear
  the account's / global window overrides in the same call.** Otherwise a legacy "set 80" would silently not apply to a window that has an override.
* Priority: `priority` == `position` (single source of truth is list order, which the widget and every GUI list also use). New call "move this account to priority N" shifts the others.

### 4.3 Wire additions (all additive; `version` stays 2)

New Optional fields (always populated by the new app and by the Linux daemon):

| struct | new fields |
|---|---|
| `StatusInfo` | `protocolRevision` int? (3), `features` [string]? (e.g. `splitThresholds`, `priority`), optionally `platform` string? (`macos`/`linux`) |
| `AutoSwitchInfo` | `defaultSessionThreshold` n?, `defaultWeeklyThreshold` n? (effective globals; `defaultThreshold` stays the base) |
| `AccountInfo` | `sessionThreshold` n? (own override, absent = follows), `weeklyThreshold` n?, `effectiveSessionThreshold` n?, `effectiveWeeklyThreshold` n?, `priority` int? |

New methods (old app -> `methodNotFound`, which is the desired fail-loud behaviour):

* `accounts.setThresholds` params `{"account":s, "session"?: n|null, "weekly"?: n|null}`. A key that is **present** is applied (number 1-100 sets, `null` clears to follow); a key that is **absent** is left alone;
  neither present -> `invalidParams`. Result `{"account":AccountInfo}`. Out of range -> `invalidValue` ("A threshold must be 1–100, or null to follow the default. For manual only use accounts.setThreshold with 0.").
  Detect presence from `request.params.objectValue` (fact 4), not from a `Double?` struct.
* `accounts.setPriority` params `{"account":s, "priority":int}` (1...count; else `invalidValue`). Result `{"accounts":[AccountInfo]}` (like `setOrder`, API:173).
  Pure helper to add in `AccountOrder` (AccountOrder.swift): `moved(accounts, id:, toPosition:) -> [Account]?`. Trap: the existing `moving(fromOffsets:toOffset:)` takes a destination index **before** the move
  (AccountOrder.swift:23-34), so for a final 0-based position `p` of an item at `i` the destination is `p + 1` when `p > i`, else `p`. Unit-test both directions and the last position.

New settings keys (generic path, no new method): `autoSwitch.sessionThreshold`, `autoSwitch.weeklyThreshold` (number 50-100 step 1 like the base, plus an input that clears to "follow the base": accept `null` or
the text `default`; this needs a new `SettingKey.Kind` or a special case because `.number` rejects null, CAT:100-104). `settings.get` should return the **effective number** (never null) so scripts always get a number.
UserDefaults keys `autoSwitchSessionThreshold` / `autoSwitchWeeklyThreshold` (absent = follow `autoSwitchThreshold`). Unset behaviour = today's for every existing user.
Decide whether the weekly global floor stays 50 (`AutoSwitchSettings.thresholdRange`, AutoSwitchEngine.swift:475). The founder's example weekly 50 sits exactly on the floor; per-account values already go down to 1
(`accountThresholdRange` 0-100, line 477), which is asymmetric (Q4).

Event additions (optional): `autoSwitched.data` gains `window: "session"|"weekly"` when a window threshold fired, **keeping** `limit: "windows"` so existing watchers keep working; an `accountsChanged` event so a
watcher sees threshold/priority/label edits (none exists today, 1.9).

### 4.4 Compatibility matrix under this design

| Situation | Result |
|---|---|
| Old CLI/script -> new app: `status`, `accounts`, `usage`, `--json` | unknown keys ignored; `threshold`, `effectiveThreshold`, `manualOnly`, `defaultThreshold` keep their meaning; table prints the lower effective value |
| Old CLI -> new app: `accounts threshold 2 70`, `settings set autoSwitch.threshold 80` | set both windows, clear window overrides: same visible result as before |
| Old CLI `settings` listing | the two new keys appear after the known ones (unknown keys sort last, RUN:372) |
| Old MCP client | same 18 tools, same schemas; new tools simply extra |
| New CLI -> old app: `status`/`accounts` | decodes (new fields Optional) and falls back to legacy columns |
| New CLI -> old app: `accounts threshold 2 --weekly 50` or `accounts priority` | old app: `methodNotFound`. Better: CLI checks `status.features` first and prints "this PixelSwitch (<ver>) cannot set separate thresholds; update it" |
| Protocol check | unchanged (`protocolVersion == 2` both ways) |
| Rollback to an older build | stored `Account` JSON gains two optional keys that old builds ignore and drop on next save; effective values revert to the "both" layer. Keep `switchThreshold` coherent |

### 4.5 File-by-file change list (control layer only)

Wire/app:
* `CP`: add the Optional fields and `var` declarations above; add `protocolRevision`/features constants; add `ControlMethod.accountsSetThresholds = "accounts.setThresholds"` and `.accountsSetPriority = "accounts.setPriority"`
  (21 methods); add `SetThresholdsParams`/`SetPriorityParams`. Update the doc comment at CP:10-12 to say additive Optional fields do not bump `version`.
* `API`: two dispatch cases next to API:149-173; extend `makeInfo`/`ControlSnapshots.accountInfo` (API:249-289) with the new fields (needs the per-window effective values from the controller); `status()` fills `features`.
* `AppControlling` (API:7-42) + `AppController` (AppController.swift:69-71, 163-170): per-window effective thresholds, `setWindowThresholds(session:weekly:for:)` with presence semantics, `setAccountPriority`;
  `autoSwitch` info (AppController.swift:32-41) gets the global per-window values.
* `AppState` (686-723): per-window `effectiveSwitchThreshold`, setter, and a priority-move built on a new pure `AccountOrder.moved`. `Account` (Account.swift:45) gains two optional fields (decode-safe: optionals).
  Legacy `setSwitchThreshold` must clear that account's window overrides.
* `CAT` + `STO`: two new `SettingKey` cases (append **after** `autoSwitchThreshold` to keep related keys together, and update `SettingKeyNames.all` in the same order), their `kind`, `value`, `set`;
  `STO:93-95` (`.autoSwitchThreshold` set) must also remove the two override keys. The Settings window's slider writes `autoSwitchThreshold` via `@AppStorage` directly (SettingsView.swift:14,92-96;
  SettingsAccountsTab.swift:15) and bypasses `SettingsStore`, so the GUI needs its own matching change or a legacy slider move will not clear overrides.
* `RemoteControlService` hub: optional `window` in `autoSwitched`; optional `accountsChanged`.

CLI:
* `PAR`: keep `accounts threshold <account> <1-100|default|manual>` exactly. Add flag form `accounts threshold <account> [--session <1-100|default>] [--weekly <1-100|default>]` (at least one flag; `manual` refused with the same
  hint text as PAR:166) and `accounts priority <account> <n>`; add `CLICommand.windowThresholds(...)` and `.priority(...)` as **new cases** so existing `.threshold(String, Double?)` comparisons in tests stay valid. Update `helpText`.
  `settings set autoSwitch.sessionThreshold 90` needs no parser change.
* `RUN`: execute the two new commands; `thresholdSet`/`switchesAt` (RUN:293-310) print today's exact text when session == weekly (or the new fields are absent), and a two-part text only when they differ,
  for example `5h 90% / 7d 50%`, so parsers of the old table keep working; `status` line likewise; `SettingKeyNames.all` extended (RUN:388-398). Feature check against `status.features` before sending a new method.
* `MCP`: add `set_account_thresholds` (**account**, `session` `["number","null"]` 1-100, `weekly` same) and `set_account_priority` (**account**, **priority** integer >= 1) -> 20 tools. Argument names equal control param names (verbatim pass-through),
  so use `session`/`weekly`/`priority`. Update the description text of `set_account_threshold` ("sets both windows"), `list_accounts`, `get_settings`, and the instructions string.

Tests/docs that pin the old shape and must be updated in the same change:
* `CT:102-106` (exact method list), `CT:742` (key list), `CT:803,815-817,820` (18 tools and their names), `CT:703-705,716-724,731` (exact table/threshold/status strings; keep them passing by design above),
  `CT:309-310,345-375` (threshold API behaviour), plus new tests for both methods, presence semantics, `AccountOrder.moved`, legacy-clears-overrides, and a **new-CLI-decodes-old-app-JSON** test using a literal pre-change payload.
* `IT:47` (stays 2), `IT:114,119` (18 -> 20 tools).
* `ARCHITECTURE.md:566-580` ("19 methods"), `README.md:88-98`, `AGENTS.md` remote-control line, `RELEASE_NOTES.md`, `PAR:206-240` help text.
* Run `bash scripts/typecheck.sh` (the harness will not catch a CLI file that uses an app-only type).

---

## 5. (e) What a Linux daemon must implement so the same CLI and MCP work

Context from `recon/laptop.md`: the Ubuntu 26.04.1 laptop has Python 3.14.4 and PyGObject, **no `swift`/`swiftc`**. So both ends are reimplemented (Python is the natural choice); nothing below needs more than the standard library.

### 5.1 Server contract (everything in section 1, with these platform mappings)

| Mac | Linux equivalent |
|---|---|
| `~/Library/Application Support/PixelSwitch/control.sock` | `$XDG_RUNTIME_DIR/pixelswitch/control.sock` (typically `/run/user/<uid>/pixelswitch/control.sock`, about 38 bytes). `sun_path` is 108 on Linux, so the limit can be 107; keep a defined maximum and refuse longer. Fallback if `XDG_RUNTIME_DIR` is unset: a 0700 directory under `~/.local/state/pixelswitch/` |
| folder 0700, socket 0600 | same. Create with `umask(0o077)` around `bind` to avoid the chmod gap |
| `getpeereid` | `SO_PEERCRED` (`struct ucred`), uid must equal `os.getuid()` |
| `SO_NOSIGPIPE` | `MSG_NOSIGNAL` on `send`, or ignore `SIGPIPE` |
| `DispatchSource` + main actor | one asyncio/selector loop; handlers for one connection run sequentially in arrival order; different connections independent; a long switch must not block other connections' reads |
| stale-socket replace / refuse second instance | identical: connect test, then `unlink`; refuse if something answers |
| `open -g -b <bundle id>` to start the app | the CLI's `connect()` should try `systemctl --user start pixelswitch.service` (or rely on a systemd **socket-activated** unit listening on the same path, which makes start-on-demand native); keep `PIXELSWITCH_NO_LAUNCH=1` and `PIXELSWITCH_CONTROL_SOCKET` |

Must reproduce exactly: line framing and the 1 MiB / 2 s limits (1.2); the encoding rules, especially **omit absent optionals, sorted compact keys, integers without fraction, ISO 8601 seconds-precision UTC dates, uppercase UUIDs** (1.3);
the envelope rules (`jsonrpc` required, id null/absent = silent notification, parse error without `id`) (1.4); the error kinds, codes and `data.kind`/`data.candidates` (1.4); all 19 methods with identical validation and message text (1.6; the
messages are user-visible and tested); the resolver order (1.8); the five event types (1.9); the log-line format without emails (1.6).

`status.get` on Linux: `appVersion` = package version; `protocolVersion: 2` (so the unchanged CLI accepts it); `claudeAvailable` = `claude` found (or `claude.binaryPath`); `autoSwitch` from the same settings; plus the new optional fields in section 4.3.

### 5.2 Settings on Linux

All 22 keys must exist, validate identically and round-trip (scripts and `IT:61-76` depend on `autoSwitch.drainWithinHours`, `menuBar.lowRemainingWarningThreshold`, and `refreshInterval 7` being refused with exit 2).
GUI-only keys can be stored without effect: `menuBar.*` (7 keys) unless the tray menu implements them; `maskEmailAddresses`, `colorCodeAccounts`, `appLanguage`. Real mappings: `launchAtLogin` -> an XDG autostart `.desktop` file (default false); `updates.automatic` -> whatever the Linux updater is
(report `false` and refuse `true` with `failed` and a clear message if there is none, rather than lying); `claude.binaryPath` -> same rule (absolute executable or `""`); `refreshInterval`/`transcriptLookbackHours` -> real timers. Persist in one JSON file under `~/.config/pixelswitch/`.

### 5.3 Behaviour behind the methods that is not protocol but must exist

`accounts.switch` (credentials swap, ownership proof, the 429 guard), `usage.get/refresh` (per-account polling), `accounts.signIn.*` (claude login capture), `accounts.addCurrent`/`remove` and the auto-switch loop that emits `autoSwitched`
are the other subsystems' scope (`recon/signin.md`, `recon/usage.md`, `recon/laptop.md`); the control layer only needs these seams: `accounts` list in persisted order with `isActive`/`isSwitchable`/`subscription`/`label`/own thresholds; per-account usage
`{session,weekly,fable,extraUsage,sampledAt,error}`; machine usage `{todayCost,totalCost,conversationTurns,activeCodingMinutes,linesWritten,modelUsage}` (the CLI prints "This Mac today"; keep the same string or add `platform` and branch);
`busy` while a switch or sign-in runs; `claudeUnavailable`.

### 5.4 The Linux CLI (`pixelswitch`) and MCP server

Reimplement `CLIParser`/`CLIRunner`/`MCPServer` from sections 3.1-3.4. Do not paraphrase: the output strings in 3.2 are pinned by tests (CT:703-742) and by users' scripts. Specific points a rewrite tends to get wrong:
* the unwrapped-vs-wrapped `--json` difference for sign-in (3.2); `watch` prints `params` only; `settings set` JSON-or-string value parsing; `accounts label` joins remaining words; `%` stripped from thresholds;
* `--timeout` semantics and the slow-call floor of 120 s; exit codes 0-5; stderr for errors, with the `  choices:` line;
* time is shown in **local** time `yyyy-MM-dd HH:mm`; percentages are `Int(value.rounded())` (round half away from zero; `99.6` -> `100%`);
* the table pads by character count and trims trailing whitespace;
* MCP: both eras, `_meta` handling, `-32022`/`-32602` errors, verbatim argument pass-through, the 120 s/30 s timeouts (consider using 120 s for `add_current_account`, the Mac's 30 s looks like an oversight), `structuredContent`, `isError`.
Packaging: install a `~/.local/bin/pixelswitch` entry point (the Mac does it with a symlink, `CommandLineToolInstaller`); the MCP snippet is `ssh <host> /home/<u>/.local/bin/pixelswitch mcp`.

### 5.5 A ready-made conformance suite

* `Tests/integration/remote-control.sh` is plain bash + `python3`, takes `PIXELSWITCH_CLI`, and exercises status/protocol, accounts, usage, an unknown account (exit 4), two settings round trips, an invalid value (exit 2), `watch` receiving `usageUpdated` after `refresh`, a sign-in
  start/cancel, and MCP handshake + 18 tools + a tool call. It never switches/removes/relabels/reorders. Run it unchanged against the Linux daemon (after the 18 -> 20 edit).
* `CT:80-466` and `CT:621-844` are executable golden vectors for the envelope, resolver, settings normalisation, API behaviour, parser, output and MCP; port them to Python `unittest` and compare byte-for-byte.
* If someone wants the Swift CLI on Linux anyway: `ControlProtocol.swift`, `CLIParser.swift`, `CLIRunner.swift`, `MCPServer.swift` are Foundation-only apart from the default `NSHomeDirectory()` path (CP:21); **not portable** as written:
  `ControlClient.swift` (`import Darwin`, `SO_NOSIGPIPE`, `/usr/bin/open`, CLI:2,48,118), `ControlServer.swift` (`Darwin`, `getpeereid`, `SO_NOSIGPIPE`, SRV:2,131,137), and `PixelSwitchCommand.swift` (`Bundle.main` + `NSDictionary(contentsOf:)` plist, lines 13-16). That needs a Swift toolchain on Ubuntu, which the laptop lacks.

---

## 6. Surprises and risks

1. **Strict `protocolVersion` equality** makes any bump a hard break for every shipped CLI and for `IT:47`; precedent (1 -> 2 for one added field) says the team bumps. This work should break that habit deliberately: additive Optional fields, new methods, no bump.
2. **Silent mis-set risk:** extending `accounts.setThreshold` with new params would be ignored by an old app and set both windows. Use new methods.
3. **Non-optional new field => new CLI prints "No accounts yet"** against an old app (RUN:197-199), a misleading failure.
4. **`Double?` conflates null and absent**; presence-based methods must read the raw params.
5. **No capability discovery exists**; add `features` (Optional) or the CLI cannot be loud about old apps.
6. **Pinned counts everywhere:** 19 methods (CT:102-106, ARCHITECTURE.md:576), 18 tools (CT:803,815-820, IT:119, plus IT:114 text), 22 keys (CT:742). Adding any of them touches all three plus docs.
7. **Harness hides CLI errors** (CLI target compiles only `PixelSwitchCLI/*` and `ControlProtocol.swift`); `scripts/typecheck.sh` is the real check.
8. **Parse-error replies omit `id`** and `jsonrpc` is mandatory on requests; a Python server must copy this or strict clients will behave differently from the Swift client.
9. **`--json` shapes differ** between `accounts sign-in` (unwrapped) and `accounts sign-in status` (wrapped); easy to "fix" and break `IT`.
10. **Events don't cover threshold/priority/label changes**; a GUI-less watcher is blind to the new settings unless an event is added.
11. **MCP gaps already known:** no protocol re-check on reconnect; `add_current_account` has a 30 s timeout where the CLI uses 120; `--version` prints the protocol version, not the app's (handover list).
12. **The GUI's default-threshold slider writes `UserDefaults` directly** (`@AppStorage`), bypassing `SettingsStore`; a legacy-write-clears-overrides rule will not hold for a slider move unless the GUI is changed with it.
13. **Priority only matters under `myOrder`** (default strategy is `mostRoom`, where position is just a tie-break). Showing a priority number without saying so would mislead; the CLI/MCP output should state the active strategy when it prints or sets priority.
14. **The downstream clients are on Sparkle auto-update** (`project.yml:53-59`, 6-hourly checks, on by default), so the app updates itself while the already-running old process may keep serving until restart; that is exactly the CLI-new/app-old window the compatibility design targets.

## 7. Open questions the spec must settle (each has a recommendation above)

* Q1. Legacy `effectiveThreshold` and `defaultThreshold`: lower-of-two (recommended) vs session-only vs weekly-only.
* Q2. Does a legacy write (`accounts.setThreshold`, `settings.set autoSwitch.threshold`) clear window overrides (recommended) or leave them?
* Q3. May a single window be "manual only" (0)? Recommended: no; manual only stays per account and wins.
* Q4. Global weekly floor: keep 50 (`thresholdRange`) or allow lower, given per-account already allows 1-100?
* Q5. What does "cycle through accounts by priority" mean for the engine: today's `myOrder` is top-down from the first account that has room; a round-robin "next after the active one" would be a different strategy. This decides whether priority needs a new strategy value (a new `autoSwitch.strategy` choice is itself an additive wire change, but `normalize` rejects unknown values on old apps).
* Q6. Should the default strategy become `myOrder` once priorities are set, or stay `mostRoom`?
