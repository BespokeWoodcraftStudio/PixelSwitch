# Recon: sign-in and adding accounts

Date: 2026-10-24. Reader: read-only. Repo: `/Users/ahamade/Documents/GitHub/PixelSwitch` (HEAD `35f8a99`, 2026-10-16, "Handover: current graphify estimate after the 1.9 AST update").

Purpose: the evidence the Linux spec and the split-threshold/priority spec will be written from. Every behavioural claim cites `file:line`. "Not found" means I looked and the repo does not say. Nothing secret is quoted: shapes and key names only. Paths are relative to the repo root unless they start with `/`.

Two things I did run, both read-only: `claude --version` printed `2.1.307 (Claude Code)`, and `claude auth --help`, `claude auth login --help`, `claude auth status --help`. I did NOT run `claude auth login` (it would start a real sign-in), so the CLI's output on 2.1.307 is unverified (see section 8).

---

## 0. The five-line version

1. `claude auth login [--email <email>]` is run as a child process on pipes (no TTY), with `BROWSER` pointed at a private temp shell script that appends the link it is handed to a file. Two links are read: the **manual** link from stdout (`visit:` line, redirect to `https://platform.claude.com/oauth/code/callback`) and the **automatic** link from the capture file (redirect to `http://localhost:<port>/callback`).
2. Nothing opens by itself. The window offers Open / Open in / Copy for the automatic link, and a collapsed "another device" section with the manual link plus a code box that writes one `code#state` line to the CLI's stdin.
3. When the CLI exits 0, PixelSwitch waits 1 s, runs `claude auth status` (JSON), takes the email from it, proves ownership of the live credential with `GET https://api.anthropic.com/api/oauth/profile`, and saves `{token, oauthAccount}` as a backup entry in one keychain item.
4. If the email is already a saved account, that account's backup is refreshed and it becomes active (`Account already exists - credentials refreshed`, reported as success). A new email creates a new `Account`, appended to the END of the list (lowest priority under "My order").
5. The sign-in logic itself is almost portable; what is macOS-only is the browser opener (`NSWorkspace`), the window (`NSPanel`/SwiftUI), `Combine`, `Darwin.kill`/`F_SETNOSIGPIPE`, the `security` keychain calls, UserDefaults, and the `zsh -ilc` claude lookup.

---

## 1. File map

| File | Lines | Role |
|---|---|---|
| `PixelSwitch/Services/SignInSession.swift` | 381 | State machine for one `claude auth login` run: timers, links, code, cancel, fallback |
| `PixelSwitch/Services/SignInProcess.swift` | 162 | `SignInProcessRunner`/`Handle` protocols, the real `Process` + `Pipe` runner, `SignInTimer`, `SignInScheduler` |
| `PixelSwitch/Services/SignInOutputParser.swift` | 163 | Pure parsing of the two links; escape stripping; log redaction |
| `PixelSwitch/Services/SignInLinkCapture.swift` | 85 | The per-sign-in temp folder, the `$BROWSER` helper script, the capture file |
| `PixelSwitch/Services/SignInRules.swift` | 66 | `SignInCode` (code#state), `SignInGate` (may a sign-in start), `SignInResult` (classify `auth status`) |
| `PixelSwitch/Services/BrowserOpener.swift` | 62 | macOS only: list browsers, open a URL in one (`AppKit`) |
| `PixelSwitch/Services/SignInWindowController.swift` | 145 | macOS only: floating `NSPanel` hosting `SignInView` |
| `PixelSwitch/Views/SignInView.swift` | 210 | macOS only: SwiftUI sign-in window |
| `PixelSwitch/AppState.swift` | 1896 | `startSignIn` 340-372, `backUpBeforeSignIn` 420-439, `completeSignIn` 442-451, `finishNewAccountSignIn` 454-567, `finishReauthentication` 570-651, `signInFinished` 654-665, `performAddCurrentAccount` 271-328 |
| `Tests/UnitTests/SignInTests.swift` | 810 | Parser, rules, environment, capture, process, session and end-to-end tests (all pure Swift, no AppKit) |

Also read because the questions need them: `PixelSwitch/Services/ClaudeService.swift` (`captureCurrentCredentials` 603-647, `signInLaunch` 651-661, `getAuthStatus` 204-214, `accountIdentity` 297-334, claude lookup 83-200), `PixelSwitch/Services/ClaudeProcessEnvironment.swift`, `PixelSwitch/Services/KeychainService.swift`, `PixelSwitch/Services/ClaudeCredentialsFile.swift`, `PixelSwitch/Services/CredentialOwnership.swift`, `PixelSwitch/Models/Account.swift`, `PixelSwitch/Control/AppController.swift`.

---

## 2. (a) How `claude auth login` is run

### 2.1 Entry points (who starts a sign-in)

| Entry | Code |
|---|---|
| Popover "Login New Account" | `Views/AccountSwitcherView.swift:319` calls `appState.loginNewAccount()` |
| Popover per-account re-authenticate (the circular arrows) | `Views/AccountSwitcherView.swift:209` calls `appState.reauthenticateAccount(account)` |
| Settings "Sign In New Account" | `Views/SettingsSignInButtons.swift:16` calls `appState.loginNewAccount()` |
| Control API / CLI / MCP `accounts.signIn.start` | `Control/ControlAPI.swift:120` -> `Control/AppController.swift:93-126` -> `appState.startSignIn(purpose)` |

All four reach `AppState.startSignIn(_:)` (`AppState.swift:340-372`). "Add Current Account" does NOT run the CLI (section 3.5).

### 2.2 The gate

`SignInGate.decide(claudeAvailable:isSwitching:isLoggingIn:current:)` (`SignInRules.swift:25-30`):

- `claudeAvailable == false` -> `.claudeUnavailable` (reported first, even if busy).
- `isSwitching || isLoggingIn` -> `.busy`.
- A `currentSignIn` whose state is not finished -> `.busy`.
- Otherwise `.allowed`.

On `.allowed`, `startSignIn` sets `isLoggingIn = true` and `errorMessage = nil` (`AppState.swift:351-352`) before anything awaits. `refresh()` returns early while `isLoggingIn` (`AppState.swift:172-175`) and `refresh()` only evaluates auto-switch if the refresh ran (`AppState.swift:165-168`), so auto-switch is paused for the whole sign-in. `switchTo` refuses while `isLoggingIn` (`AppState.swift:788-791`).

### 2.3 Order of events at the start

`startSignIn` (`AppState.swift:354-371`) builds a `SignInSession`, publishes it as `currentSignIn` (so the window shows at once, in state `.starting`), then in a `Task`:

1. `await backUpBeforeSignIn(for: purpose)` (`AppState.swift:420-439`), then
2. `session.start()` (the CLI is launched only now).

So the CLI never starts before the current account's login is saved. `cancel()` during the backup ends the session at once and `start()` then does nothing (`SignInSession.swift:132-134, 241-246`; test `SignInTests.swift:654-661`).

`backUpBeforeSignIn`:
- `.newAccount`: if there is an `activeAccount`, `claudeService.captureCurrentCredentials(for: current)`; if that returned true, `noteCredentialChanged(for: current)`. No active account: skip.
- `.reauthenticate(accountId, _)`: same capture, but ONLY when the active account is a different account from the one being re-signed.

### 2.4 Arguments

`SignInSession.arguments(for:)` (`SignInSession.swift:120-125`):

- new account: `["auth", "login"]`
- re-authenticate: `["auth", "login", "--email", "<account email>"]` (pre-fills the login page's email field)

`claude auth login --help` on this Mac (2.1.307): options `--claudeai` (default), `--console`, `--email <email>`, `--sso`. PixelSwitch uses only `--email`. Not used and not found anywhere in the repo: `--claudeai`, `--console`, `--sso`.

### 2.5 Executable

`ClaudeService.signInLaunch()` (`ClaudeService.swift:651-661`) returns `SignInLaunch(executable: URL(fileURLWithPath: claudePath), environment: ClaudeProcessEnvironment.make(claudePath:base:homeDirectory:))`.

`claudePath` comes from `autoSelectedPath()` (`ClaudeService.swift:83-91`): the first existing file in a curated list (`ClaudeService.swift:124-138`, in order: `/usr/local/bin/claude`, `/opt/homebrew/bin/claude`, `/opt/local/bin/claude`, `~/.local/bin/claude`, `~/.claude/local/claude`, `~/.npm-global/bin/claude`, `~/.volta/bin/claude`, `~/Library/pnpm/claude`, `~/.bun/bin/claude`, `~/.yarn/bin/claude`), then NVM folders (`~/.nvm/versions/node/<v>/bin/claude`, `ClaudeService.swift:142-153`), then `/bin/zsh -ilc "command -v claude"` with a 3 s limit (`ClaudeService.swift:159-200`), then the bare string `"claude"` (`ClaudeService.swift:90`). A user preference (`claudeBinaryPathPreference` in UserDefaults, `ClaudeService.swift:6, 37-41`) overrides it.

### 2.6 Environment

`ClaudeProcessEnvironment.make(claudePath:base:homeDirectory:)` (`ClaudeProcessEnvironment.swift:7-26`):

- Starts from `base` = the WHOLE inherited environment of the PixelSwitch app (`ProcessInfo.processInfo.environment`, `ClaudeService.swift:657`).
- Prepends to `PATH`: the resolved (symlink-followed) directory of the claude binary (only if `claudePath` contains `/`), then `/opt/homebrew/bin`, `/usr/local/bin`, `<home>/.local/bin`, `<home>/.npm-global/bin`, then the existing `PATH` (default `/usr/bin:/bin`). Test: `SignInTests.swift:159-180`.
- Sets `HOME` to `NSHomeDirectory()`.

`SignInSession.start()` then merges `capture.environmentOverrides` over that (override wins, `SignInSession.swift:144`):

| Variable | Value |
|---|---|
| `BROWSER` | absolute path of the helper script `<tmp>/pixelswitch-signin-<session uuid>/capture-sign-in-link` |
| `PIXELSWITCH_SIGNIN_CAPTURE_FILE` | absolute path `<tmp>/pixelswitch-signin-<session uuid>/captured-links` |

(`SignInLinkCapture.swift:16, 72-74`.) `<tmp>` is `FileManager.default.temporaryDirectory` (`SignInSession.swift:101`). Nothing is removed from the inherited environment, so an inherited `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_PROFILE` stays in force (see the `noIdentity` outcome, section 3.3).

The working directory is not set (`Process.currentDirectoryURL` untouched, `SignInProcess.swift:49-57`), so the child inherits the app's.

### 2.7 Process wiring

`ProcessSignInRunner.start` (`SignInProcess.swift:42-83`):

- `standardOutput` and `standardError` are the SAME `Pipe` (one merged stream); `standardInput` is a second `Pipe`. No pseudo-terminal is allocated.
- Output is read incrementally with `readabilityHandler` (`SignInProcess.swift:60-68`), because the last thing Claude Code prints is a prompt with no newline and the process then waits, so reading to end-of-file would never deliver the links (`SignInProcess.swift:33-36`). Each chunk is queued with `DispatchQueue.main.async { MainActor.assumeIsolated { onOutput(data) } }`.
- The exit is reported from `terminationHandler` after waiting up to `outputDrainTimeout = 2` s for the pipe to close (`SignInProcess.swift:40, 69-75`), also via the main queue, so it arrives after the last chunk.
- A write after the CLI has exited must not kill the app: `fcntl(input.fileDescriptor, F_SETNOSIGPIPE, 1)` (`SignInProcess.swift:96`). `F_SETNOSIGPIPE` is Darwin-only.
- `terminate()` is `Process.terminate()` (SIGTERM); `kill()` is `Darwin.kill(pid, SIGKILL)` (`SignInProcess.swift:111-117`). Both signal the direct child only, not a process group.
- `writeLine` appends `"\n"` and writes under a lock; it returns false if stdin was closed or the process is not running (`SignInProcess.swift:99-109`).

### 2.8 The `$BROWSER` capture helper

`SignInLinkCapture` (`SignInLinkCapture.swift`) creates, per sign-in:

- a folder `<tmp>/pixelswitch-signin-<uuid>`, created with `createDirectory(withIntermediateDirectories: false)` and then `chmod 0700` (`SignInLinkCapture.swift:44-46`);
- the helper `capture-sign-in-link`, mode 0700 (`:48-50`);
- an empty `captured-links`, mode 0600 (`:52-54`).

The helper is a POSIX shell script (`SignInLinkCapture.swift:27-38`), exactly:

```
#!/bin/sh
# PixelSwitch sign-in link capture. ...
file="${PIXELSWITCH_SIGNIN_CAPTURE_FILE:-$(dirname "$0")/captured-links}"
printf '%s\n' "$1" >> "$file"
exit 0
```

It records its first argument on its own line and exits 0 so Claude Code believes a browser opened. Claude Code runs `$BROWSER <link>` instead of `open <link>` when `BROWSER` is set (design spec `docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md:62`; `SignInLinkCapture.swift:3-6`). The design first put the script in the app bundle; it was deliberately moved to a temp folder so no bundle resource or signing change is needed (`SignInLinkCapture.swift:9-11`).

Cleanup: the folder is removed when the sign-in ends (`finish`, `processSucceeded`, `startFallback`: `SignInSession.swift:301-302, 337-339, 352-353`). A crashed run's folder is removed on the next sign-in if it is older than `staleAfter = 60 * 60` s by modification date (`SignInLinkCapture.swift:19, 60-69`); only names starting `pixelswitch-signin-` are touched. Test: `SignInTests.swift:236-247`.

### 2.9 Reading the links

Output handling (`SignInSession.swift:175-185`): chunks are appended to `output` up to `outputLimit = 64 * 1024` bytes (`:55, 177-179`); after each chunk, until a manual link is known, the whole buffer is decoded as UTF-8 (`String(decoding:as:)`, so a chunk split mid-character is repaired) and re-parsed. Chunks from an old process generation, or after a stop was requested, are ignored (`:176`).

**Manual link** = `SignInOutputParser.manualLink(in:)` (`SignInOutputParser.swift:21-32`):

1. Strip terminal escapes (OSC ended by BEL or `ESC \`, CSI, two-character escapes; a lone `\r` becomes `\n`; other control characters dropped; `:50-83`).
2. For each line containing `visit:` (case-insensitive), take the https URLs after it and return the first that HAS a `redirect_uri` query item and whose redirect is NOT localhost.
3. Else gather OSC-8 hyperlink targets (`hyperlinkTargets`, `:86-107`) from the raw output plus all https URLs in the visible text, and return the first whose `redirect_uri` is https and whose path ends in `/oauth/code/callback` (`redirectsToCodeCallback`, `:142-145`).

A URL ends at whitespace, a control character, or one of `"'<>` and backtick; trailing `.,;:!?)]}` is trimmed (`httpsURLs`, `:111-125`).

**Automatic link** = `SignInOutputParser.automaticLink(inCaptureFile:)` (`:37-45`): strip escapes, then for each line return the first https URL whose `redirect_uri` is `http`/`https` with host `localhost`, `127.0.0.1`, `::1` or `[::1]` (`redirectsToLocalhost`, `:135-139`). Only a `localhost` redirect counts; a code-callback link in the capture file is ignored (test `SignInTests.swift:72-73`).

The capture file is polled every `capturePollInterval = 0.25` s by reading the whole file (`SignInSession.swift:53, 187-208`). The poll stops when the automatic link is found, the state is finished, or a stop is pending.

**Exact shapes** (the test kit, `SignInTests.swift:19-27`; values `TESTCHALLENGE123`/`TESTSTATE456` stand in for secrets):

```
Opening browser to sign in…\n
If the browser didn't open, visit: <manual link>\n
Paste code here if prompted > <no trailing newline>
```

Manual link:
`https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference&code_challenge=<S256 challenge>&code_challenge_method=S256&state=<state>`

Automatic link (handed to `$BROWSER`): the same URL with `redirect_uri=http%3A%2F%2Flocalhost%3A54545%2Fcallback` (port varies). The `client_id` equals `ClaudeService.oauthClientID` (`ClaudeService.swift:339`). The localhost listener is served by the CLI process itself, so it finishes in any browser on the same machine (spec lines 61, `docs/worklog/2026-09-25.md:18, 33`).

The state moves `starting -> waitingForUser` as soon as EITHER link is known (`linkArrived`, `SignInSession.swift:210-214`); the manual link often arrives first (stdout), the automatic one after the next 0.25 s poll.

Logging never carries a link's query: `SignInOutputParser.redacted(_:)` prints scheme, host, path and where it returns to (`:150-157`; test `SignInTests.swift:80-86`).

### 2.10 The code box (another device)

`SignInCode.normalized(_:)` (`SignInRules.swift:9-16`) accepts only one whole `code#state`: trimmed, non-empty, no whitespace or control character inside, exactly one `#`, text on both sides. Tests: `SignInTests.swift:92-96`.

`SignInSession.submitCode(_:)` (`SignInSession.swift:222-232`) returns false unless: state is `.waitingForUser`, no stop is pending, no code was already submitted, the code normalizes, and a process handle exists. It writes the normalized line plus `"\n"` to the CLI's stdin (`handle.writeLine`) and sets `codeSubmitted = true`. A second code is refused (`SignInTests.swift:550`). There is no way to retry a wrong code short of cancelling and starting again; if the CLI rejects a code without exiting, the session stays `waitingForUser` with the Submit button disabled (`SignInView.swift:127`) until cancel or the 15-minute limit. What the real CLI does on a bad code is NOT in the repo (the fake CLI in the end-to-end test exits 1 with "Invalid code", `SignInTests.swift:757-760`, which is a test double, not an observation).

UI: `SignInView.submit()` (`SignInView.swift:145-152`) shows a red hint, "That isn't the whole code. Copy all of it, including the part after #.", when `normalized` fails, and clears the field after a successful hand-over. The field is inside the collapsed "Signing in on another device?" section (`:109-143`). With the automatic link also shown, the code still works (test `SignInTests.swift:553-562`).

### 2.11 Timers and limits

| Constant | Value | Where | Meaning |
|---|---|---|---|
| `linkWait` | 10 s | `SignInSession.swift:44` | No link of either kind within 10 s of launch: fall back to Claude Code opening the default browser |
| `overallLimit` | 15 min | `:46` | Whole sign-in, measured from the first launch; covers the fallback run too (test `SignInTests.swift:712-722`) |
| `killAfter` | 2 s | `:48` | After SIGTERM, SIGKILL if still running |
| `giveUpAfter` | 5 s | `:51` | After a stop request, end the session anyway as if the process exited with status -1 |
| `capturePollInterval` | 0.25 s | `:53` | Reads the capture file |
| `outputLimit` | 64 KiB | `:55` | Output kept for parsing |
| `outputDrainTimeout` | 2 s | `SignInProcess.swift:40` | Wait for pipe EOF after exit before reporting the exit |
| `staleAfter` | 3600 s | `SignInLinkCapture.swift:19` | Age at which an orphan capture folder is deleted |
| 1 s pause | 1 s | `AppState.swift:444` | Before reading identity after exit 0 ("Give keychain a moment to sync") |

All timers go through `SignInScheduler`; the tests use a fake clock (`SignInTests.swift:399-421`).

### 2.12 The 10-second fallback

`linkWaitElapsed` (`SignInSession.swift:259-264`): if still `.starting`, no manual link, and one last look at the capture file finds nothing, it logs and stops the process with reason `.fallback`. When that process exits, `startFallback` (`:299-306`) removes the capture folder, sets `notice` to "Couldn't read the sign-in link; opened your default browser instead.", relaunches `claude auth login` with `launch.environment` (NO `BROWSER`, no capture variable) so Claude Code opens the default browser itself, and goes straight to `.waitingForUser` with neither link shown. The session still finishes when the CLI exits. Test: `SignInTests.swift:676-700`. The same arguments are used (`:690`).

On macOS Claude Code then opens the browser with `open`; on Linux it would use whatever its own opener is (the task brief says `xdg-open`; not verifiable from this repo).

### 2.13 States and endings

`SignInState` (`SignInSession.swift:12-23`): `starting -> waitingForUser -> completing -> succeeded(accountId) | failed(message) | cancelled`; "finished" = the last three.

`exited(status:generation:)` (`:310-333`), the single place a process end is interpreted:

| Stop reason | Result |
|---|---|
| `.fallback` | `startFallback()` |
| `.cancel` | `finish(.cancelled)` |
| `.timeout` | `finish(.failed("Sign-in timed out after 15 minutes. Start it again when you're ready."))` |
| none, exit status 0 | `processSucceeded()` -> `.completing`, remove capture folder, run `complete` (AppState's save steps), `finish(outcome)` |
| none, any other status | `finish(.failed("Claude Code's sign-in stopped (exit status N)."))` |

A launch failure gives `.failed("Couldn't start Claude Code: <error>")` (`:164-168`); a capture-folder failure gives `.failed("Couldn't prepare the sign-in: ...")` (`:137-141`). `finish` runs `onFinish` exactly once (`:347-359`); `AppState.signInFinished` then clears `isLoggingIn` and copies a failure message into `errorMessage` (`AppState.swift:654-665`).

### 2.14 Cancellation

`cancel()` (`SignInSession.swift:239-257`):

- Only acts in `.starting` or `.waitingForUser`. Ignored in `.completing` ("closing the window never interrupts saving the account", `:236-238`; test `SignInTests.swift:572-573`).
- Not started yet (cancelled during the backup): `finish(.cancelled)` immediately.
- Otherwise `stop(.cancel)`: `handle.terminate()` (SIGTERM), arm the 2 s kill timer and the 5 s give-up timer (`:277-287`). The state stays `waitingForUser` until the exit report (or give-up) arrives, then `.cancelled`.
- Cancel during a pending fallback converts it to a cancel, so no second CLI is launched (`:250-252`; test `SignInTests.swift:702-710`).
- A late exit report after the give-up is ignored (`generation` check, test `SignInTests.swift:643-652`).

Window behaviour (macOS): closing the panel while the sign-in runs cancels it; closing a finished one dismisses it; closing during `.completing` just hides it (`SignInWindowController.swift:128-144`). Success and cancel close the panel by themselves; failure stays open (`:68-77`). A cancelled sign-in changes nothing: "Cancelled. Nothing was changed." (`SignInView.swift:202`); Claude Code writes credentials only when a sign-in succeeds (spec line 64).

---

## 3. (b) How the new account's identity is learned and its token saved

### 3.1 After the CLI exits 0

`AppState.completeSignIn` (`AppState.swift:442-451`) sleeps 1 s, then calls `finishNewAccountSignIn()` or `finishReauthentication(...)`.

### 3.2 Identity: `claude auth status`

`ClaudeService.getAuthStatus()` (`ClaudeService.swift:204-214`) runs `claude auth status` (default `--json`, confirmed by `claude auth status --help`: `--json` (default) / `--text`) through `runClaude` with a 30 s timeout (`:788-848`), same environment as section 2.6, stdout and stderr merged. The output is decoded into `AuthStatus` (`Models/Account.swift:127-147`):

| JSON key | Type | Notes |
|---|---|---|
| `loggedIn` | Bool | required |
| `authMethod` | String? | `"claude.ai"` when the stored login is the credential source in use |
| `apiProvider` | String? | |
| `email` | String? | ONLY present when `authMethod == "claude.ai"` |
| `orgId`, `orgName` | String? | same condition |
| `subscriptionType` | String? | same condition (`"max"` etc.) |

Because `runClaude` merges stderr into the JSON text, any warning the CLI prints to stderr would break decoding; this has not been a problem so far (not found in the repo as an incident).

`SignInResult.newAccount(status:accounts:)` (`SignInRules.swift:54-59`):

- `loggedIn == false` -> `.notLoggedIn`
- no `email` -> `.noIdentity`
- `accounts.first(where: { $0.email == email })` -> `.existing(accountId)` (EXACT, case-sensitive string comparison)
- else `.new(email)`

### 3.3 Failure outcomes

| Outcome | State | Message |
|---|---|---|
| `.notLoggedIn` | `.failed` | "Login did not complete" (`AppState.swift:471-475`) |
| `.noIdentity` | `.failed` | `shadowedIdentityMessage`: "Claude CLI is authenticating via <method> instead of a stored claude.ai login, so it reports no account. Unset ANTHROPIC_AUTH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_PROFILE and remove any apiKeyHelper, then try again." (`AppState.swift:1739-1742`) |
| `auth status` throws | `.failed` | the error's `localizedDescription` (`AppState.swift:461-467`) |

Each also sets `errorMessage` and clears `isLoggingIn`.

### 3.4 New account: create and capture (`AppState.swift:526-566`)

1. `Account(email, displayName: status.orgName ?? email, provider: .claudeCode, orgName, subscriptionType, isActive: true)` with a fresh `UUID` (`AppState.swift:529-536`; `Account` fields at `Models/Account.swift:31-45`, including optional `switchThreshold`, `customLabel`, `lastUsed`).
2. `claudeService.captureCurrentCredentials(for: account)` (`ClaudeService.swift:603-647`):
   - Reads the live credential string: `KeychainService.readClaudeToken()` runs `/usr/bin/security find-generic-password -s "Claude Code-credentials" -a <NSUserName()> -w` and trims whitespace (`KeychainService.swift:60-61, 112-129`). This is the whole credential JSON blob; it carries `claudeAiOauth` (keys used: `accessToken`, `refreshToken`, `expiresAt` in ms; `scopes` is also referenced, `ClaudeService.swift:414-416`) and possibly `mcpOAuth` and other keys (`CredentialOwnership.swift:30-37`; `ClaudeCredentialMerge.swift:3-10`).
   - Reads the live identity: `KeychainService.readOAuthAccount()` reads `~/.claude.json`, key `oauthAccount`, a JSON object whose `accountUuid` and `emailAddress` are used (`KeychainService.swift:175-186`; `ClaudeService.swift:614-615`). Other keys are carried through opaquely as `AnyCodable`.
   - Proves whose login it is: if the access token is usable (not known expired/refused), `GET https://api.anthropic.com/api/oauth/profile` with headers `Authorization: Bearer <access token>` and `anthropic-beta: oauth-2025-04-20`, 15 s timeout (`ClaudeService.swift:302-306`); the response's `account.uuid` and `account.email` (or `email_address`) are the identity (`:317-329`). It REFUSES if that email differs (case-insensitively) from `account.email`, or if `~/.claude.json` does not describe the same owner (`CredentialOwnership.detailsBelong`: uuids if both present, else emails; `CredentialOwnership.swift:81-85`).
   - If the API cannot answer (token expired, 401/429 guard, network): falls back to requiring `~/.claude.json`'s `emailAddress` to equal the account's, and refuses to replace a DIFFERENT grant already saved for that account id (`ClaudeService.swift:626-642`).
   - Saves: `KeychainService.saveAccountBackup(token:oauthAccount:forAccountId:)` (`KeychainService.swift:213-232`).
3. Where the backup lives: ONE keychain generic-password item, service `ai.pixelventures.pixelswitch.backups`, account `all-accounts` (`KeychainService.swift:322, 330`), whose data is JSON `{ "<account uuid>": { "token": "<credential blob>", "oauthAccount": { ... } }, ... }` (`AccountBackup` at `KeychainService.swift:7-10`; `saveBackupStore` `:562-596`). Every mutation is load-whole-store, mutate, save-whole-store under `storeLock`, and refuses to write if the load failed (`:213-232` and `:349-353`). Older homes that are still READ and migrated: keychain service `me.xueshi.ccswitcher.backups` (`:328`), file `~/.pixelswitch/backups.json` (`:78-79`), file `~/.ccswitcher/backups.json` (`:83`).
4. On capture failure: state `.failed("Could not capture credentials")`, `isLoggingIn = false`, the new account is NOT added, and the old active account's `isActive` is not touched (`AppState.swift:539-546`). Note the CLI has by then already replaced the live login, so Claude Code runs as the new, unsaved account until the next refresh reconciles (`updateActiveAccount` logs "Logged-in account not in our list (might be new)", `AppState.swift:1858-1880`).
5. On success: every existing account `isActive = false`; `accounts.append(account)`; `activeAccount = account`; `lastAutoSwitchAt = Date()` (a sign-in is "a deliberate account choice", so it gets the auto-switch cooldown a manual switch gets, `AppState.swift:554-556`); `saveAccounts()`; `isLoggingIn = false`; `await refresh()`; return `.succeeded(accountId)` (`:549-565`).

The accounts list itself is persisted by `saveAccounts()` as JSON-encoded `[Account]` in UserDefaults key `ai.pixelventures.pixelswitch.accounts` (`AppState.swift:67, 1848-1856`). Not found: any file-based account store.

### 3.5 "Add Current Account" (no CLI run)

`performAddCurrentAccount` (`AppState.swift:271-328`): requires `claudeAvailable`; runs `auth status`; refuses on `!loggedIn` ("Not logged in to Claude. Run 'claude auth login' first.") or no email (`shadowedIdentityMessage`); refuses a DUPLICATE (`accounts.contains { $0.email == email }` -> "Account already exists"); otherwise builds an `Account` (`isActive: accounts.isEmpty`), captures credentials, sets `activeAccount` only if it is the first account, and `accounts.append(account)` (`:324`). The popover and Settings put a confirmation in front ("This will capture the currently logged-in Claude Code account.", `Views/SettingsSignInButtons.swift:21-33`). The Control API variant is `AppController.addCurrentAccount` (`Control/AppController.swift:84-91`).

### 3.6 Re-authenticate (`AppState.swift:570-651`)

`SignInResult.reauthentication(status:expectedEmail:)` (`SignInRules.swift:61-65`): `.matches` if the email equals the account's exactly, else `.wrongAccount(signedInAs:)`. On `.wrongAccount`: `.failed("Logged in as <X>, but expected <Y>. Credentials not updated.")` (`AppState.swift:596-601`) and nothing is captured, even though the CLI has already switched the live login to X. On `.matches`: capture for that account, `noteCredentialChanged` (clears park, refusal, cooldown and needs-reauth flags and queues a read), update `orgName` and `subscriptionType`, make it the only active account, set `lastAutoSwitchAt`, `await refresh()`; a failed capture still marks it active but returns `.failed("Could not capture credentials")` (`:624-649`).

### 3.7 Where new accounts land in the order (relevant to the priority feature)

Both add paths `append` to the END of `accounts` (`AppState.swift:324, 552`). "My order" priority is the list order (spec: "Priority order = the accounts list order itself", `docs/worklog/2026-09-25.md`, Decisions; `AppState.setAccountOrder` `AppState.swift:716-`). So a newly signed-in account is lowest priority until the founder drags it. Three places build an `Account(...)` and will need any new per-window threshold fields defaulted: `AppState.swift:300` (add current), `:529` (sign-in), and the first-account branch of `updateActiveAccount` (`:1858-1880`).

---

## 4. (c) When the signed-in account already exists

`SignInResult.newAccount` returns `.existing(accountId)` (`SignInRules.swift:57`) and `finishNewAccountSignIn` takes branch `AppState.swift:484-524`:

1. Log "Step 4: Account already exists, refreshing backup and marking it active".
2. Look the account up by id; if gone: `.failed("Login did not complete")`.
3. `captured = await captureCurrentCredentials(for: target)` (same ownership proof as 3.4). If true: `noteCredentialChanged(for: target)`.
4. Look it up AGAIN (the capture awaited; the account may have been removed): if gone, `.failed("That account was removed during the sign-in, so nothing was saved.")`.
5. Make it the only active account; `lastUsed = Date()`; `activeAccount = ...`; `lastAutoSwitchAt = Date()`; `saveAccounts()`; `isLoggingIn = false`. This happens EVEN IF the capture failed, because the CLI really is signed in as this account now.
6. Result: if `captured`, `errorMessage = "Account already exists - credentials refreshed"` and the session ends `.succeeded(existingId)`; the window closes as for any success (`SignInWindowController.swift:70-71`). That message is informational: it shows in the popover footer although the sign-in succeeded. If not captured, `errorMessage` and `.failed("Could not capture credentials")` ("Step 4: Backup capture FAILED").

Differences from the new-account branch: no `refresh()` call, no new `Account`, order unchanged. The duplicate test is by exact email; ownership is checked case-insensitively, so a case-only difference between `auth status` and the stored email would create a second `Account` for the same person (theoretical; both come from the same source).

The earlier "Add Current Account" path treats a duplicate as an ERROR ("Account already exists"), while the sign-in path treats it as a refresh. Test: `SignInTests.swift:127-128` pins the classification.

---

## 5. (d) What is macOS-specific, and how the same flow works on Linux

### 5.1 Inventory

| Piece | macOS today | Portable? | What Linux needs |
|---|---|---|---|
| `SignInOutputParser` | pure Foundation (`SignInOutputParser.swift`) | Yes | Reuse as is. Uses `URLComponents`, `Character.properties`, `unicodeScalars`; no Apple API |
| `SignInLinkCapture` | Foundation + `/bin/sh` helper | Yes | Reuse; change the parent folder (see 5.3). `printf`, `dirname` exist on Ubuntu. Mode 0700/0600 work the same |
| `SignInRules` (`SignInCode`, `SignInGate`, `SignInResult`) | pure | Yes | Reuse as is |
| `SignInSession` | `import Combine` (`ObservableObject`, `@Published`), `String(localized:bundle:)` with `L10n.bundle` | Mostly | Combine does not exist on Linux (OpenCombine, or replace the `@Published` properties with callbacks or an `AsyncStream`). `L10n.bundle` reads `UserDefaults` and `Bundle.main` `.lproj` folders (`L10n.swift:6-14`); the message strings need a Linux localization route. State machine logic itself is plain |
| `SignInProcess` (`ProcessSignInRunner`) | `Foundation.Process`, `Pipe`, `DispatchQueue.main`, `fcntl(F_SETNOSIGPIPE)`, `Darwin.kill` | Partly | `Process`/`Pipe`/`readabilityHandler` exist in swift-corelibs-foundation (not verified here). `F_SETNOSIGPIPE` and `Darwin.kill` do not exist: use `Glibc.kill`, and ignore SIGPIPE process-wide (`signal(SIGPIPE, SIG_IGN)`) or write with `write(2)` and handle EPIPE. `DispatchQueue.main.async` only runs if something drives the main queue (a GTK/GLib loop, `dispatchMain()` or `RunLoop.main`): the Linux app must pick one |
| `ClaudeProcessEnvironment` | PATH extras include `/opt/homebrew/bin`; HOME via `NSHomeDirectory()` | Yes | Drop the Homebrew entry; keep `~/.local/bin`, `~/.npm-global/bin`, `/usr/local/bin`, plus the resolved claude folder |
| claude binary lookup | curated list + `/bin/zsh -ilc "command -v claude"` + bare `"claude"` (`ClaudeService.swift:83-200`) | No | Ubuntu has no zsh by default, so the shell lookup silently fails and the result is the bare string `"claude"`, which `Process`/`URL(fileURLWithPath:)` cannot resolve to an executable (relative to the working directory). Linux needs a PATH search (and `~/.local/bin/claude` is already first-class in the list) |
| Browser opener | `BrowserOpener` (`import AppKit`, `NSWorkspace`) | No | See 5.4 |
| Window | `NSPanel` + `NSHostingController` + SwiftUI | No | Whatever UI the Linux app uses; the session object is UI-agnostic |
| Clipboard ("Copy link") | `NSPasteboard` (`SignInView.swift:154-158`) | No | GTK clipboard, or `wl-copy`/`xclip`; not in this repo |
| Identity | `claude auth status` JSON | Yes | Same CLI, same JSON (`--json` default). Unverified on Linux |
| Live token read | `security find-generic-password` (`KeychainService.swift:112-129`) | No | Read `~/.claude/.credentials.json` (see 5.2) |
| Live token write (switch) | `ClaudeTokenWriter` -> `security` | No | Not a sign-in concern |
| Identity file | `~/.claude.json` key `oauthAccount` | Yes, if Claude Code does the same on Linux | Unverified (see 5.2) |
| Backup store | one keychain item (`KeychainService.swift:322, 562-596`) | No | Needs a Linux secret store: libsecret/Secret Service, or an encrypted/0600 file. Not decided in this repo. The file-based predecessor is `~/.pixelswitch/backups.json` (`:79`), plaintext JSON in the same `{id: {token, oauthAccount}}` shape |
| Accounts list | UserDefaults key `ai.pixelventures.pixelswitch.accounts` (`AppState.swift:67, 1848`) | No | A JSON file under `~/.config/pixelswitch/` or similar |
| Ownership proof | `GET https://api.anthropic.com/api/oauth/profile` | Yes | Same HTTP call. `URLSession` exists on Linux via FoundationNetworking (separate module) |
| Sleep, timers | `DispatchQueue.main.asyncAfter` (`SignInProcess.swift:154`) | Needs a driven main queue | Same as above |

### 5.2 Credentials on Linux

What this repo knows:

- Claude Code on macOS keeps the login in the keychain; `~/.claude/.credentials.json` is its plaintext FALLBACK store, and PixelSwitch only bumps the modification date when the file exists, never reads, creates, rewrites or deletes it (`ClaudeCredentialsFile.swift:1-44`). The comment says, "on a Mac where a Keychain write once failed, this file is Claude Code's real credential store" (`:31-33`).
- `ClaudeCredentialsFile.defaultURL` is `~/.claude/.credentials.json` and states `CLAUDE_CONFIG_DIR` "is not honored" because a GUI app does not inherit shell exports (`:22-27`).
- A live running session decides whether to re-read credentials by `stat()`-ing that file's mtime (`:5-14`).
- On this Mac `~/.claude/.credentials.json` does not exist (checked), consistent with the keychain being the real store here.

What the repo does NOT contain (all "not found"): the file's JSON shape on Linux, its permissions, whether Linux Claude Code also writes `~/.claude.json` `oauthAccount`, whether `CLAUDE_CONFIG_DIR` relocates both, or any Linux run of the CLI. The task brief states the file is `~/.claude/.credentials.json` on Linux; the expected shape (to be confirmed on the laptop) is the same JSON object the keychain holds, `{"claudeAiOauth": {"accessToken", "refreshToken", "expiresAt", ...}, ...}`, because `CredentialOwnership.login(fromCredential:)` and `ClaudeCredentialMerge` already treat the keychain string as that object (`CredentialOwnership.swift:30-37`; `ClaudeCredentialMerge.swift:3-10`).

Consequences for the sign-in capture steps on Linux:

- `readClaudeToken()` becomes "read the whole file as a string"; the backup `token` field would hold the same blob.
- `touchClaudeCredentialsFile()` semantics invert: on Linux the file IS the store, so a switch writes it (atomically, 0600) rather than touching it.
- The 1 s post-exit pause (`AppState.swift:444`) exists for the keychain; a file written by the CLI is normally there already, but keep the pause until proven unnecessary.

### 5.3 The capture folder on Linux

`FileManager.default.temporaryDirectory` is `/tmp` (or `$TMPDIR`) on Linux, which is shared between users, where the folder is created with default permissions and then `chmod`ed (`SignInLinkCapture.swift:44-46`). The window between those two calls is not a concern for the secret (the helper and capture file are written after the chmod), but `$XDG_RUNTIME_DIR` (`/run/user/<uid>`, mode 0700 on a systemd desktop) is the natural parent on Ubuntu, falling back to `/tmp`. The capture file holds the sign-in link, whose `state` and `code_challenge` are one-time values, not tokens.

The helper must be executable by Claude Code, so the parent must not be mounted `noexec`. This repo does not say; verify on the laptop.

### 5.4 Opening links on Linux

Today the user presses a button and `BrowserOpener` calls `NSWorkspace` (`BrowserOpener.swift:41-61`). Nothing opens by itself, and that is a founder requirement (section 6). On Linux the same three buttons need:

- Open in default browser: `xdg-open <url>` (the task brief says this is the opener on Linux; the repo has no Linux code).
- "Open in" list: enumerate `.desktop` files that handle `x-scheme-handler/https` (system, `~/.local/share/applications`, and, on Ubuntu, the snap and flatpak export folders), mark the default (`xdg-settings get default-web-browser`), and launch the chosen one with the URL (`gtk-launch`/`gio launch`, or the `Exec=` line with `%u`/`%U` expanded). This is design work for the Linux spec; nothing exists to reuse. `BrowserApp` (`BrowserOpener.swift:6-12`) is a good model (id, name, default flag).
- Control API `open` parameter ("default" or a browser name, `AppController.swift:96-108`) uses `BrowserOpener.installedBrowsers()` and `BrowserOpener.open`, so the same abstraction must exist on Linux.

### 5.5 The same flow, step by step, on Linux

1. Gate and backup of the current account: unchanged (backup source = `.credentials.json` instead of the keychain).
2. Launch `claude auth login [--email ...]` with `BROWSER=<helper>` and `PIXELSWITCH_SIGNIN_CAPTURE_FILE=<file>`: unchanged, given an absolute path to `claude`. Claude Code's opener honouring `BROWSER` on Linux is stated by the brief, and is the same assumption the macOS design rests on; it was OBSERVED only on macOS 2.1.280 (`docs/worklog/2026-09-25.md:18`). Spike it on the laptop first.
3. Parse the manual link from stdout and the automatic link from the capture file: unchanged. The localhost callback is served by the CLI process, so it works in any browser on the same machine. For a headless or remote Linux box the code-box (manual) path is the one that works.
4. Cancel/timeouts/fallback: unchanged. In the fallback (10 s with no link), Claude Code opens the browser itself, so `DISPLAY`/`WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS` and `XDG_*` must be in the inherited environment (they are, if the app is started in the user's desktop session; they are not if it is started by `ssh` or a bare systemd unit).
5. After exit 0: `claude auth status` -> email; read credentials from the file; ownership proof by the profile API; save the backup in the Linux store; add or refresh the account.

---

## 6. (e) What the founder said about sign-in links

Verbatim, from `docs/decisions/2026-09-24-remote-control-answers.md` (section "Added requirements, same message", lines 26-34), written 2026-09-24/25. This is the source of the whole feature:

> 3. When adding a new account, I want to have the functionality where I can either:
> 1. Click a button which would open the link in a web browser, and then I do the authentication.
> 2. Have a copy-and-paste button. I copy it, then I can go and open a different browser than my default browser where I'm already authenticated with the different account, and then it finishes the authentication.  The reason is that if I have multiple accounts, maybe I have multiple web browsers I'm logged into Claude from. Maybe I have Safari, I'm logged into account A, and in Chrome I'm logged into account B, and in Firefox I'm logged into account C. Because of this, I want to be able to click a copy button, then I go and paste it into the right web browser, and I don't have to log in from that browser because I'm already logged in.  I want those features added to the product. Mind you, all this that you're adding needs to work through the GUI that's currently in place or through the CLI/MCP that you're building.

Same file, line 17-18 (answer to "How should the AI add an account?", question `adding-accounts`):

> ANSWER: Both: save the current account, and start a new sign-in that I finish in the browser (the recommended one)

Same file, line 39-40, what follows from the answers (assistant's summary of his words, not a quote): "Every new feature above is reachable from both the GUI and the CLI/MCP."

Design approval (2026-09-25, `docs/decisions/2026-09-24-remote-control-answers.md`, "Design review, answered 2026-09-25"): "1. Approve the design ... ANSWER: Approve as written (the recommended one)". The design text he approved (card `approve-design`, `docs/decisions/pixelswitch-design-review-2026-09-25.html:154`, the assistant's wording, tags stripped): "Sign-in links. Adding or re-signing an account no longer throws open your default browser. A small window shows the link with Open, Open in Safari / Chrome / Firefox (every browser you have installed) and Copy. Whichever browser on this Mac you use, it should finish by itself ... A real sign-in in two different browsers is tested before release. A second link covers signing in on another device, where the page shows a code you paste back."

What that fixes as requirements for any port: (1) nothing opens a browser unless the user presses a button; (2) an Open-in-a-specific-browser choice exists; (3) a Copy-link button exists whose link finishes by itself in whichever browser it is pasted into (that is why the AUTOMATIC link is the one copied); (4) it must work from the GUI and from the CLI/MCP.

Related, same file, line 100: "nobody has the software but me. I'm the only one using it." (2026-09-25, about releasing before hand checks). The current brief says downstream clients exist; reconcile that with this when scoping the release.

Not found: any later founder statement about sign-in links. The sign-in hand-check cards (`docs/decisions/pixelswitch-hand-checks-2026-09-25.html:378-439, 540-546`: "Sign in with Open in, and nothing opens by itself", "Copy the link into a different browser", the window's behaviour, re-authenticate wrong-account, another-device code) have no recorded answers in `docs/`.

---

## 7. Tests that can be reused for a Linux port

`Tests/UnitTests/SignInTests.swift` has no AppKit dependency. `Tests/run-unit-tests.sh` compiles with plain `swiftc` the files `SignInOutputParser`, `L10n`, `SignInProcess`, `SignInSession`, `SignInRules`, `ClaudeProcessEnvironment`, `SignInLinkCapture` with `SignInTests.swift` and `main.swift` (script lines 24-31), so those files already compile as a standalone module on the Mac. They are the natural seed for a shared core package.

| Section (line) | What it pins |
|---|---|
| Parser (31-87) | Manual link from the exact 2.1.280 output; OSC-8; colour codes; no link; reworded line; noise; a `visit:` line with a non-sign-in URL; localhost never taken as manual; CR redraws; first localhost link in capture file; redaction |
| Rules (91-139) | Code shapes; gate decisions; `SignInResult` classification |
| Environment (159-180) | PATH order, HOME, symlinked claude puts its real folder first |
| Capture (203-251) | 0700/0600 modes, helper run as Claude runs it, append behaviour, stale-folder cleanup |
| Process (274-341) | A prompt without newline arrives while running; exit after last output; write after exit fails; SIGPIPE safe; missing binary throws; timers fire/cancel |
| Session (470-737) | Every state transition: links, code, completing, exit codes, cancel/kill/give-up, 15 minutes, 10-second fallback |
| End to end (741-810) | A `/bin/sh` stand-in `claude` driving the real runner and real helper; cancel stops a real process |

The end-to-end test uses `#!/bin/sh` scripts and `chmod 0755`, so it should run as is on Ubuntu once the Darwin-only calls are replaced.

---

## 8. Surprises and risks

1. **The CLI changed since the parser was written.** The parser, tests and spec are pinned to Claude Code 2.1.280 (`SignInOutputParser.swift:5`; spec line 59; `SignInTests.swift:24-25`, "2026-09-25"). This Mac now has 2.1.307. I did not run a sign-in, so whether the three-line output and the `$BROWSER` call are unchanged is unverified. The 10-second fallback (section 2.12) is what keeps sign-in working if they changed; on Linux that fallback depends on the desktop environment variables.
2. **`claude` path resolution is the likeliest first Linux failure.** The bare-`"claude"` fallback (`ClaudeService.swift:90`) is not launchable by `Process`, and the lookup shells out to `zsh` (`:162-163`), which a stock Ubuntu lacks. Sign-in cannot even start without an absolute path.
3. **`F_SETNOSIGPIPE` and `Darwin.kill` will not compile on Linux** (`SignInProcess.swift:96, 116`). A write to the CLI's stdin after it exits would raise SIGPIPE and kill the app unless SIGPIPE is ignored (test `SignInTests.swift:307-322` pins the Mac behaviour).
4. **`Combine` in `SignInSession`** (`SignInSession.swift:2`): the state machine is portable, its observation mechanism is not. The Control path also uses a Combine sink to open the browser when the automatic link arrives (`AppController.swift:119-123`).
5. **Main-queue dependence.** Output, exit, and every timer are delivered through `DispatchQueue.main` with `MainActor.assumeIsolated` (`SignInProcess.swift:66, 74, 154-158`). If the Linux app does not run the main dispatch queue (a GTK loop does not, by default; `dispatchMain()` or `RunLoop.main` does), no link and no timer will ever fire. Decide the loop first.
6. **No process-group handling.** SIGTERM/SIGKILL go to the single child. If Linux Claude Code is a wrapper (a script or `node` launching the real process), the listener could survive a cancel and hold its localhost port; the 15-minute limit comment says "the CLI's localhost listener lives until the process ends" (spec line 169). Not observed on any platform.
7. **A wrong code cannot be retried** (section 2.10).
8. **Wrong-account and capture-failure paths leave the live login changed.** After re-authenticate `.wrongAccount`, or a failed capture for a brand-new account, the CLI is already signed in as someone PixelSwitch did not save (`AppState.swift:596-601, 539-546`); recovery is left to the next `refresh()` (`reconcileLiveLogin`). A port should keep the same behaviour deliberately, not by accident.
9. **Exact-versus-case-insensitive email comparison** (3.2 versus 3.4). Harmless today.
10. **Environment pass-through.** The whole app environment is passed to the CLI. On Linux that is likelier to contain exports from a terminal launch (`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`), which produce the `noIdentity` failure; the message already tells the user what to unset.
11. **Credential file location.** `CLAUDE_CONFIG_DIR` is explicitly not honoured (`ClaudeCredentialsFile.swift:22-24`). A Linux user who sets it (common on servers) would have Claude Code writing somewhere PixelSwitch does not look. Decide whether the Linux build honours it.
12. **Localization.** Every message is a localized string in five languages via `String(localized:bundle:)` (e.g. `SignInSession.swift:140, 166, 303, 326, 331`; `AppState.swift:471, 502, 518`). The Linux build needs the same catalog or an agreed English-only first release.
13. **"Only one user" versus "all downstream clients".** The founder's 2026-09-25 statement says nobody else has the software (section 6); the current request says to push to production for downstream clients. The release path (Sparkle appcast on macOS) has no Linux equivalent in this subsystem.

---

## 9. Questions the spec should settle (not answered by the repo)

1. Linux backup store: libsecret (Secret Service) or a 0600 file? (Section 5.1.)
2. Linux UI toolkit and event loop, which decides how `SignInSession` is observed and how timers are driven. (Risks 4 and 5.)
3. Browser enumeration and launch on Ubuntu 26.04 (snap/flatpak Firefox, Chrome): which Linux mechanism, and is "Open in" required for the first Linux release? (Section 5.4.)
4. Does Linux Claude Code honour `BROWSER`, still print the `visit:` line, and still write `~/.claude.json` `oauthAccount`? One recorded spike on the laptop (start with `BROWSER` pointed at the helper, cancel after 8 s, confirm the credential file's fingerprint is unchanged) answers all three, exactly as the 2026-09-25 spike did on macOS (`docs/worklog/2026-09-25.md:18`).
5. Should a newly signed-in account be appended at the end of the priority order (current behaviour) or placed somewhere else once explicit priority exists? (Section 3.7.)
6. Honour `CLAUDE_CONFIG_DIR` on Linux? (Risk 11.)
