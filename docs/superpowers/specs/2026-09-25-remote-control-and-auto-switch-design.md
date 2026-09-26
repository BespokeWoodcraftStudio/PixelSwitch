# Remote control, smarter auto-switch, and sign-in links: design

Author: Claude (Opus 5.5), for the founder. Date: 2026-09-25. Status: APPROVED by the founder, 2026-09-25 ("Approve as written"). A3 resolved as option (a) with its own Settings switch.
Founder's requirements and answers, verbatim: [docs/decisions/2026-09-24-remote-control-answers.md](../../decisions/2026-09-24-remote-control-answers.md).

## In plain words

Three things get added to PixelSwitch, and every one of them works from the app and from a new command called `pixelswitch`.

1. **Smarter auto-switch.**
   - Each account can have its own switch threshold, for example 70% for one and 90% for another.
   - You choose how the next account is picked. The options are most room left (today's behaviour), your own order, or the account whose weekly quota resets soonest. The last one means quota about to expire is used before it is lost.
2. **Sign-in links you control.** Adding or re-signing an account no longer throws open your default browser. PixelSwitch shows the sign-in link with three buttons:
   - **Open**, in your default browser.
   - **Open in**, a specific browser such as Safari, Chrome or Firefox.
   - **Copy**.

   Whichever browser on this Mac you use, the sign-in finishes by itself. A second link covers signing in on another device, where the page shows a code you paste back.
3. **Remote control for an AI.** A `pixelswitch` command-line tool ships inside the app. An AI on your other Mac runs it over the SSH-style connection it already uses to run work here. It can also plug it into Claude as a tool server (MCP). It can do everything you can do in the app:
   - read usage and switch accounts
   - add, re-sign, rename, reorder and remove accounts
   - change every setting and watch usage live

   It can never read a login token, and nothing is opened onto your network.

## 1. Requirements

From the founder, 2026-09-24/25:

| # | Requirement | Source |
|---|---|---|
| R1 | An AI on another Mac on the same network can do everything the human can do in the GUI: switch, add and remove accounts, change all settings, monitor usage | request |
| R2 | The other Mac reaches this one by running commands here (SSH or similar); no network listener | answer 1 |
| R3 | Always on, everything allowed, no permission switch | answer 2 |
| R4 | The AI can save the current account and start a new sign-in that a human finishes in a browser | answer 3 |
| R5 | Both plain shell commands and an MCP mode | answer 4 |
| R6 | A user-defined priority order for the next account | added feature 1 |
| R7 | A "resets soonest" choice that uses quota about to expire so it is not wasted | added feature 1 |
| R8 | A per-account switch threshold | added feature 2 |
| R9 | New-account sign-in offers "open in a browser" and "copy the link" so the right already-signed-in browser can finish it | added feature 3 |
| R10 | Every new feature works from the GUI and from the CLI/MCP | added, closing line |

## 2. Facts this design rests on

Each was checked in the code or by a test on 2026-09-25.

- `AppState` is a `@MainActor ObservableObject`. It owns all actions: `switchTo`, `addAccount`, `loginNewAccount`, `reauthenticateAccount`, `removeAccount`, `updateAccountLabel` and `refresh`.
  - They return `Void` and report failure only through `errorMessage`.
  - Their guards (`isSwitching`, `isLoggingIn`) return silently.
- `AutoSwitchEngine` (`PixelSwitch/Services/AutoSwitchEngine.swift`) is a pure function.
  - It triggers when the active account's binding window (the higher of 5-hour and weekly) reaches one global threshold (50–99, default 90).
  - It then ranks candidates at or below `threshold − 10`, preferring Fable room first and then most headroom.
  - `AppState.evaluateAutoSwitch` verifies the chosen candidate with a fresh read and enforces a 300-second cooldown.
  - It is covered by the swiftc unit harness (`Tests/run-unit-tests.sh`).
- Non-active accounts are polled round-robin, one per cycle. A non-active reading can be several cycles old.
  - A weekly `resets_at` does not change inside a window, so ranking by reset time tolerates stale samples.
  - Utilization is re-verified before any switch, as today.
- `Account` uses synthesized `Codable`. A new optional field decodes as nil from existing saved data.
- **Sign-in.** `claude auth login` (Claude Code 2.1.280) behaves as follows. This was observed by starting it with a link-capturing `BROWSER` and cancelling after 8 seconds; the live credential's fingerprint was unchanged.
  - It prints `If the browser didn't open, visit: <manual link>`. That link's `redirect_uri` is `https://platform.claude.com/oauth/code/callback`, where the page shows a code.
  - It hands `<automatic link>` to the browser opener. That link's `redirect_uri` is `http://localhost:<port>/callback`, served by the CLI itself, so it finishes by itself in any browser on this Mac.
  - The opener runs `$BROWSER <link>` if `BROWSER` is set, else `open <link>`. Setting `BROWSER` to a helper that records the link suppresses the automatic browser.
  - It reads `code#state` lines from stdin, which is how the manual flow's code is entered.
  - Credentials are written only when the sign-in succeeds. Starting and cancelling changes nothing.
- No external control exists today.
  - The registered `pixelswitch://` URL scheme has no handler.
  - The main app is not sandboxed (its entitlements hold only the App Group), so it can host a Unix-domain socket.
- No `AppState` published property holds a token.
  - Tokens live in the keychain services `Claude Code-credentials` and `ai.pixelventures.pixelswitch.backups`.
  - `KeychainService` and `ClaudeService` expose credential-returning methods, which must never be reachable from outside.
- **Settings side effects live in views today.** A change made through UserDefaults alone would update the GUI through `@AppStorage`, but not trigger these effects:
  - `startAutoRefresh` after `refreshInterval` changes
  - `SMAppService` for launch at login
  - `ClaudeService.setPath` for the CLI path
  - `MenuBarConfig.set`

## 3. Part A: auto-switch rules

### A1. Per-account threshold (R8)

- `Account` gains `switchThreshold: Double?`. `nil` means use the default, which is the existing global threshold.
  - The range is 50–100.
  - 100 means "use this account until it is empty".
  - The global default slider also widens to 50–100.
- **Trigger:** the active account switches when its binding utilization reaches its own threshold. This applies to the Fable limit too.
- **Eligibility for a threshold-triggered switch:** a candidate must sit at or below its own threshold minus the hysteresis (10), exactly as today but per account.

### A2. How the next account is chosen (R6, R7)

There is a new setting `autoSwitchStrategy` with three values:

| Value | Label in the app | Ranking among eligible candidates |
|---|---|---|
| `mostRoom` (default, today's behaviour) | Most room left | Fable room first, then lowest utilization (unchanged) |
| `myOrder` | My order | Highest in the user's order first. Always from the top: a higher account that has freed up again is preferred over the next one down |
| `resetsSoonest` | Resets soonest (use quota before it expires) | Earliest weekly `resets_at` first. Ties go to most room. Candidates with no known weekly reset rank last |

- **The order** is the order of the accounts list itself. There is one order everywhere: the popover's Accounts tab and the new Settings → Accounts tab show accounts in priority order.
  - It is set by dragging in Settings → Accounts, or with the CLI.
  - A newly added account goes to the bottom.
- **"Resets soonest" uses the weekly window**, the 7-day window, and the Fable weekly window for a Fable-triggered switch. It never uses the 5-hour window, which resets every few hours and would make the choice meaningless.
- **Fable preference.** "Most room left" keeps today's preference for accounts with Fable to spare. The other two strategies honour the user's intent strictly. The eligibility rules still apply: a Fable-triggered switch must land on an account with Fable room.

### A3. Switching early to use expiring quota (R7). Decided: option (a), with its own switch

The founder's example is an account whose weekly quota resets in 12 hours with some quota left, which should be used first so that quota is not wasted. Waiting for the current account to hit its threshold does not achieve that, so this mode can switch early.

**The rule, as recommended.** It applies only when the strategy is `resetsSoonest` and a new setting `autoSwitchDrainEarly` is on (default on in that strategy). PixelSwitch switches before the active account reaches its threshold when all of these hold:
- another switchable account's weekly quota resets within `autoSwitchDrainWithinHours` (default 24, range 1–72)
- that account resets earlier than the active account's weekly window
- it has room below its own threshold on both the 5-hour and weekly windows
- its utilization is verified fresh before the switch
- the 300-second cooldown has passed, and no switch or sign-in is in progress

After it drains, the normal threshold trigger moves the user on.

**Decided 2026-09-25: option (a).** The founder's words: "Feature that should be enabled or disabled Via s switch in the configuration settings." So `autoSwitchDrainEarly` is a visible on/off switch in Settings → General → Auto-switch. It is shown whenever "Resets soonest" is picked, it is on by default, and it is settable from the CLI/MCP as `autoSwitch.drainEarly`. "My order" never switches early (option (c) was not chosen).

### A4. Engine changes

`AutoSwitchEngine.plan` stays a pure function. It gains these inputs:
- `threshold(for: Account) -> Double`, which replaces the single `threshold`
- `strategy`
- `drainEarly` and `drainWithin`

`evaluateAutoSwitch` passes the per-account threshold and uses it for the verification ceiling. It logs which rule fired: threshold or early drain.

### A5. UI

- **Settings → General → Auto-switch.** Existing:
  - the toggle, and the threshold, relabelled "Default threshold"
  - the Fable toggle

  New:
  - a "Choose the next account by" picker
  - when "Resets soonest" is picked, an on/off switch "Switch early to use quota before it resets" (A3) and, while it is on, "within [24] hours"
- **New Settings → Accounts tab.** It lists every account in priority order, with drag to reorder. Each row shows:
  - the label or email
  - its weekly reset time
  - a threshold control, either "Default (90%)" or a custom value from 50 to 100

  The tab also has Add Current Account and Sign In New Account buttons, which open the same sign-in sheet as the popover (B2).
- **The popover** is unchanged, apart from following the new order.

### A6. Tests

These are unit cases in the existing harness against the pure engine:
- per-account trigger, and per-account eligibility
- the three strategies' orderings, including ties and unknown reset times
- the "always from the top" rule for My order
- the early drain firing, and not firing when: outside the window, the target resets later than the active account, the target has no room, or the strategy is not `resetsSoonest`
- a threshold of 100

Backward compatibility: saved accounts without `switchThreshold` decode and behave exactly as today.

## 4. Part B: sign-in links (R4, R9)

### B1. Mechanism

`ClaudeService` gains a long-running sign-in session, replacing the run-to-exit `login()`.

- **Starting it.** It starts `claude auth login` with stdout, stderr and stdin as pipes. The environment sets `BROWSER` to a small helper script in the app bundle, `Contents/Resources/capture-sign-in-link`, which appends its argument to a private file (mode 0600) named in another variable and exits 0.
- **Reading the links.**
  - It streams stdout and takes the **manual link** from the `visit:` line. The fallback is the first `https://` URL whose `redirect_uri` is the code callback.
  - It watches the helper's file for the **automatic link**.
  - It strips any terminal escape sequences before parsing.
- **Finishing the manual flow.** `submitCode(_:)` writes the pasted `code#state` line to the CLI's stdin.
- **Cancelling.** `cancel()` terminates the process, and SIGKILLs it after 2 seconds if needed.
  - A session is also cancelled after 15 minutes. Today there is no timeout, and the CLI's localhost listener lives until the process ends.
- **States:** starting, then waiting (with one or both links), then completing, then one of succeeded, failed (with a reason) or cancelled.
- **Re-signing an account** passes `--email <account email>` so the page is pre-filled.
- **If neither link can be read within 10 seconds**, for example because a future CLI changes its output, the session cancels itself and PixelSwitch falls back to today's behaviour: run `claude auth login` normally and let it open the default browser. It shows "Couldn't read the sign-in link; opened your default browser instead."

`AppState.loginNewAccount` and `reauthenticateAccount` keep their backup-then-capture steps. They now publish a `currentSignIn` session object the GUI and the control API both observe, instead of blocking on `login()`. Whichever side starts a sign-in, both see it, and either can finish or cancel it.

### B2. UI: the sign-in sheet

"Login New Account" (popover) and Re-authenticate open a small sheet. Nothing opens by itself.

- **Sign in on this Mac, using the automatic link:**
  - Open in default browser
  - Open in ▾, listing installed browsers from `NSWorkspace.urlsForApplications(toOpen:)`, default first
  - Copy link

  The note reads: "Paste it into any browser on this Mac. It finishes by itself."
- **Signing in on another device?** This is a collapsed section with Copy link (the manual link), then "Paste the code the page shows" with a Submit button.
- **Status line and Cancel.** On success the sheet closes and the account appears, as today.

"Add Current Account" is unchanged: it saves whoever Claude is signed in as.

### B3. Tests

- **Parsing.** Unit cases feed recorded CLI output into the parser: a plain `visit:` line, OSC-8 wrapped links, no link, a changed wording with a code-callback URL, and extra lines.
- **Session state machine.** Unit cases run it against a fake process: link capture, code submit, cancel, timeout and fallback.
- **Manual check.** One real sign-in in each of two browsers before release.

## 5. Part C: remote control (R1–R5, R10)

### C1. Shape

```
other Mac ──ssh──▶ this Mac: pixelswitch <command>      ─┐
other Mac ──ssh──▶ this Mac: pixelswitch mcp (stdio)    ─┤  Unix socket, user-only
                                                          ▼
                         PixelSwitch.app: ControlServer ──▶ AppController ──▶ AppState (GUI sees every change)
```

- **The CLI.** `pixelswitch` is a new Swift command-line target in `project.yml`. It is built into the app bundle at `Contents/Helpers/pixelswitch`.
  - Settings gets an **Install command-line tool** button, which links `~/.local/bin/pixelswitch` to it. No admin password is needed, and Sparkle updates keep it current because it is a link.
  - CI signs it with hardened runtime before the app (added to the nested list in `.github/workflows/build.yml`).
- **The server.** `ControlServer`, inside the app, listens on `~/Library/Application Support/PixelSwitch/control.sock`.
  - The directory is 0700 and the socket 0600.
  - Every connection is checked with `getpeereid`, and one from any other user is closed.
  - There is no TCP listener of any kind (R2).
- **If the app is not running,** the CLI launches it (`open -b ai.pixelventures.pixelswitch`) and waits up to 10 seconds for the socket. Over SSH this needs the user to be signed in to this Mac's desktop, which the app needs anyway.

### C2. Protocol

- **Format.** It is newline-delimited JSON-RPC 2.0. Request and response types live in `Shared/ControlProtocol.swift`, compiled into the app, the CLI and the unit harness.
- **Version check.** `status.get` returns `protocolVersion`, and the CLI refuses a mismatch with a clear message. The case is an old app still running after an update.
- **Account references** accept an id, an email or a label, case-insensitive. Ambiguous or unknown references are errors that list the matches.
- **Errors** are typed: `busy` (a switch or sign-in is in progress), `notFound`, `ambiguous`, `invalidValue`, `claudeUnavailable`, `failed(message)`.

| Method | Does | GUI equivalent |
|---|---|---|
| `status.get` | App and protocol version, active account, auto-switch settings, sign-in in progress, `claudeAvailable`, last refresh | menu bar |
| `accounts.list` | Accounts in priority order: id, email, label, subscription, active, switchable, threshold (own and effective), current usage summary | Accounts tab |
| `accounts.switch` | Switch to an account; returns once the switch is verified | Switch / double-click |
| `accounts.addCurrent` | Save the account Claude is signed in as | Add Current Account |
| `accounts.signIn.start` | New sign-in, or re-sign an account (optional `open`: `none`, `default` or a browser name); returns both links | Login New Account / Re-authenticate |
| `accounts.signIn.status` / `.submitCode` / `.cancel` | Follow, finish or cancel the current sign-in | sign-in sheet |
| `accounts.remove` | Remove an account and its saved login | Remove |
| `accounts.setLabel` | Set or clear the label | edit label |
| `accounts.setThreshold` | Set the per-account threshold, or `null` for the default | Settings → Accounts |
| `accounts.setOrder` | Set the full priority order | drag in Settings → Accounts |
| `usage.get` | Per account: session, weekly and Fable utilization and reset times, extra usage, sample time, errors. Plus machine-wide cost and activity | Usage and Costs tabs |
| `usage.refresh` | Refresh now | Refresh |
| `settings.get` / `settings.set` | Every setting (table below), validated by type and range | Settings window |
| `events.subscribe` | Stream: active account changed, usage updated, auto-switched (with the rule that fired), sign-in progress, error | the live UI |
| `app.checkForUpdates` / `app.quit` | As named | About / Quit |

**Settings exposed.** This is every GUI setting, with the same allowed values:
- `refreshInterval` and `transcriptLookbackHours`
- `autoSwitch.enabled`, `autoSwitch.threshold`, `autoSwitch.onFable`, `autoSwitch.strategy`, `autoSwitch.drainEarly` and `autoSwitch.drainWithinHours`
- `maskEmailAddresses`, `colorCodeAccounts`, `appLanguage` and `launchAtLogin`
- `menuBar.showsHeadIcon`, `menuBar.modules` and `menuBar.customizesLimitBarColors`
- `menuBar.color.session`, `.weekly`, `.fable` and `.lowRemaining`
- `menuBar.lowRemainingWarningThreshold` and `claude.binaryPath`

The hysteresis and cooldown stay fixed, as in the GUI.

### C3. The action layer (a targeted improvement to `AppState`)

- **`AppController`** is a new `@MainActor` type. It is the one place both the GUI and the control server call. Each action returns a result or throws a typed error.
  - `AppState`'s actions get throwing versions. The existing methods become thin wrappers that catch the error and set `errorMessage`, so the GUI behaves exactly as now.
  - The silent `isSwitching` and `isLoggingIn` returns become a thrown `busy` for callers that need to know.
- **`SettingsStore`** takes the side effects out of the views (listed in section 2), so a change made from the CLI takes effect exactly as one made in the window. The views bind to it.
- **`ControlAPI`** maps requests to `AppController` calls. It is pure apart from that dependency, which is a protocol, so it is unit-testable with a fake.
- **Logging.** Every remote command is logged to `~/Library/Logs/PixelSwitch-app.log`, for example `[control] accounts.switch → <id> ok`, so "what did the AI do?" always has an answer. Arguments are never logged beyond account ids and setting names.

### C4. The CLI

The default output is readable; with `--json` it is exact JSON for scripts and AIs. Exit codes: 0 means ok, 2 means usage error, 3 means busy, 4 means not found or ambiguous, 5 means the app is unreachable, and 1 means anything else.

```
pixelswitch status
pixelswitch accounts                          # list, in priority order
pixelswitch switch <account>
pixelswitch accounts add-current
pixelswitch accounts sign-in [--open | --open-in <browser>] [--wait]
pixelswitch accounts reauth <account> [--open | --open-in <browser>] [--wait]
pixelswitch accounts sign-in code <code>
pixelswitch accounts sign-in cancel
pixelswitch accounts remove <account> --yes
pixelswitch accounts label <account> <text|--clear>
pixelswitch accounts threshold <account> <50-100|default>
pixelswitch accounts order <account> <account> ...
pixelswitch usage [<account>]
pixelswitch refresh
pixelswitch watch                             # live events, one JSON object per line
pixelswitch settings [<key>]
pixelswitch settings set <key> <value>
pixelswitch update-check
pixelswitch quit
pixelswitch mcp                               # MCP server on stdin/stdout
```

`remove` needs `--yes`. That is not a permission, which R3 rules out. It stops a typo from deleting a saved login.

### C5. MCP mode

`pixelswitch mcp` speaks MCP (JSON-RPC over stdio: `initialize`, `tools/list`, `tools/call`). It is written by hand, with no new dependency.
- **Tools** mirror the methods one to one, with JSON schemas:
  - `get_status`, `list_accounts`, `get_usage`, `refresh_usage`
  - `switch_account`, `add_current_account`, `remove_account`
  - `start_sign_in`, `get_sign_in_status`, `submit_sign_in_code`, `cancel_sign_in`
  - `set_account_label`, `set_account_threshold`, `set_account_order`
  - `get_settings`, `set_setting`
  - `check_for_updates`, `quit_app`
- **Monitoring** is done by calling `get_usage`, since MCP tools are request and response.
- **The Settings snippet.** Settings shows a ready-to-copy block for the other Mac's Claude, with this Mac's name and an absolute path. The absolute path matters because a non-interactive SSH shell may not have `~/.local/bin` on `PATH`:

```json
{ "mcpServers": { "pixelswitch": { "command": "ssh", "args": ["<this-mac>.local", "/Users/<you>/.local/bin/pixelswitch", "mcp"] } } }
```

### C6. Security

This follows the founder's answers. The channel never leaves this Mac, and anything running as this Mac's user can use all of it (R3).
- **No secrets cross the socket.** No method returns an access token, refresh token or credential JSON. `ControlAPI` depends only on `AppController`, never on `KeychainService` or `ClaudeService` credential methods, and a unit check asserts no response type contains a token field.
- **The socket** is user-only by file mode and by peer-uid check.
- **Remote commands are logged** (C3).

### C7. Tests

- **Unit, in the harness:** `ControlProtocol` encoding and decoding, and `ControlAPI` against a fake controller. The fake covers every method, account reference resolution, typed errors and settings validation.
  - Also the MCP request handling: `initialize`, `tools/list` shape and `tools/call` mapping.
  - Also the no-token assertion.
- **Integration:** a script builds the app, starts it, and drives the CLI through status, list, usage, settings get/set round-trips, a sign-in start and cancel, and `watch`.
- **Manual:** one run from the other Mac over SSH in MCP mode.

## 6. Build order

The parts are built one after another: **A, then B, then C**, each on its own branch and merged before the next starts.

This replaces the earlier "A and B in parallel". Both A and B change `AppState.swift`, `Tests/run-unit-tests.sh`, `Tests/UnitTests/main.swift` and the Settings → Accounts tab. The founder's standing rule is that two writing agents never touch the same file. Changed 2026-09-25, before any code existed.

1. **Part A: auto-switch rules.**
2. **Part B: sign-in links.** It adds its buttons to the Settings → Accounts tab that A creates.
3. **Part C: remote control.** It exposes everything A and B add.
4. **Release as 1.2** once all three pass their tests and the manual checks.

Each part has its own implementation plan in `docs/superpowers/plans/`.

## 7. Out of scope

- A network listener, TLS or pairing codes (R2).
- Permission levels or a master switch (R3).
- Letting an AI finish an Anthropic sign-in with nobody present. Anthropic's sign-in needs a person at a browser, and no design removes that.
- Exposing the hysteresis or cooldown.
- Controlling PixelSwitch on a Mac where nobody is signed in to the desktop.

## 8. Risks

- **Claude Code's sign-in output or opener could change.** B1's 10-second fallback keeps sign-in working the old way. The parser's tests pin today's format.
- **Early draining switches accounts while you work.** A3 limits it to the drain window, the cooldown and verified room. The founder decides whether to have it at all.
- **A 15-minute sign-in blocks auto-switch for its duration.** That is today's behaviour, now bounded by the timeout.
- **`AppState` is 1,410 lines, and Part C touches many of its actions.** The throwing-version approach keeps each change local, and the GUI's behaviour is unchanged by construction.

## 9. Interfaces between the parts

These names and types are fixed here so the three plans agree. A later part relies on exactly these. Implementation details not listed are each plan's own.

### Tests: the convention all three parts follow

- Each part adds pure-logic tests in its own file, `Tests/UnitTests/<Name>Tests.swift`, that defines `@MainActor func run<Name>Tests()`. It calls the existing global `check(_:_:_:)` from `main.swift`.
- `main.swift` gets one call line per file, placed immediately before the final `print("\n\(passed) passed, \(failed) failed")`.
- Every new source or test file is added to the `swiftc` list in `Tests/run-unit-tests.sh`.
- The files are: A `AutoSwitchRulesTests.swift` (`runAutoSwitchRulesTests()`), B `SignInTests.swift` (`runSignInTests()`), C `ControlTests.swift` (`runControlTests()`).

### Produced by Part A

In `PixelSwitch/Models/Account.swift`:
```swift
var switchThreshold: Double?   // 50...100; nil = use the default (global) threshold
```

In `PixelSwitch/Services/AutoSwitchEngine.swift`, pure and in the test harness:
```swift
enum AutoSwitchStrategy: String, CaseIterable, Codable, Sendable { case mostRoom, myOrder, resetsSoonest }
extension AutoSwitchEngine {
    enum Trigger: String, Sendable { case threshold, drainEarly }
}
// plan(...) returns (limit: Limit, trigger: Trigger, targets: [Account])?
```

UserDefaults keys and defaults:

| Key | Type | Default | Range |
|---|---|---|---|
| `autoSwitchEnabled` | Bool | false | existing |
| `autoSwitchThreshold` | Double | 90 | widened to 50–100 |
| `autoSwitchOnFable` | Bool | true | existing |
| `autoSwitchStrategy` | String | `mostRoom` | an `AutoSwitchStrategy` rawValue |
| `autoSwitchDrainEarly` | Bool | true | |
| `autoSwitchDrainWithinHours` | Double | 24 | 1–72 |

`enum AutoSwitchSettings` (in `AutoSwitchEngine.swift`) holds these keys as `static let` constants plus typed readers:
- `strategy`, `drainEarly`, `drainWithinHours`, `defaultThreshold`
- `thresholdRange = 50.0...100.0` and `drainHoursRange = 1.0...72.0`

The existing `AutoSwitchFableSetting` stays.

On `AppState`, all `@MainActor` and internal:
```swift
func effectiveSwitchThreshold(for account: Account) -> Double          // own threshold, else the default
func setSwitchThreshold(_ threshold: Double?, for account: Account)     // clamps to 50...100; nil clears; saves
@discardableResult func setAccountOrder(_ orderedIds: [UUID]) -> Bool  // false (and no change) unless a permutation of current ids
func moveAccounts(fromOffsets source: IndexSet, toOffset destination: Int)  // for SwiftUI onMove; saves
@Published private(set) var lastAutoSwitch: AutoSwitchRecord?
struct AutoSwitchRecord: Equatable, Sendable {           // top-level type in AppState.swift
    let from: UUID; let to: UUID
    let limit: AutoSwitchEngine.Limit; let trigger: AutoSwitchEngine.Trigger
    let at: Date
}
```

Settings → Accounts is a new tab in `PixelSwitch/Views/SettingsAccountsTab.swift` (`struct SettingsAccountsTab: View`), added to `SettingsView`'s `TabView` after General.

### Produced by Part B

In `PixelSwitch/Services/SignInSession.swift`:
```swift
enum SignInPurpose: Equatable, Sendable { case newAccount, reauthenticate(accountId: UUID, email: String) }
enum SignInState: Equatable, Sendable {
    case starting, waitingForUser, completing
    case succeeded(accountId: UUID), failed(message: String), cancelled
    var isFinished: Bool { get }   // succeeded, failed or cancelled
}
@MainActor final class SignInSession: ObservableObject, Identifiable {
    let id: UUID; let purpose: SignInPurpose; let startedAt: Date
    @Published private(set) var state: SignInState
    @Published private(set) var automaticLink: URL?   // localhost redirect: finishes by itself in any browser on this Mac
    @Published private(set) var manualLink: URL?      // code-callback redirect: the page shows a code to paste back
    @discardableResult func submitCode(_ code: String) -> Bool   // false unless waitingForUser and the code has the "code#state" shape
    func cancel()
}
```

In `PixelSwitch/Services/SignInOutputParser.swift`, pure and in the test harness:
```swift
enum SignInOutputParser {
    static func manualLink(in output: String) -> URL?
    static func automaticLink(inCaptureFile contents: String) -> URL?
}
```

In `PixelSwitch/Services/BrowserOpener.swift`:
```swift
struct BrowserApp: Identifiable, Hashable, Sendable { let id: String /* bundle id */; let name: String; let appURL: URL; let isDefault: Bool }
@MainActor enum BrowserOpener {
    static func installedBrowsers() -> [BrowserApp]            // default first
    @discardableResult static func open(_ url: URL, in browser: BrowserApp?) -> Bool   // nil = default browser
}
```

On `AppState`:
```swift
@Published private(set) var currentSignIn: SignInSession?
enum SignInStartError: Error, Equatable { case busy, claudeUnavailable }   // nested in AppState
func startSignIn(_ purpose: SignInPurpose) throws -> SignInSession   // throws SignInStartError
```

`loginNewAccount()` and `reauthenticateAccount(_:)` remain, as wrappers that call `startSignIn` and set `errorMessage` on a thrown error.

### Produced by Part C

- `AppController`, `SettingsStore`, `ControlServer`, `ControlAPI` and `Shared/ControlProtocol.swift`.
- The `pixelswitch` CLI target, including MCP mode.

C relies only on the names above from A and B.

## 10. Changes made while writing the plans (2026-09-25)

None of these changes behaviour the founder approved. Each is also listed, with its reason, in the plan that makes it.

- **Part B:** the `BROWSER` helper is written per sign-in into a private temporary folder, not shipped in the app bundle. That means no bundle resource and no signing change, and nothing is left behind. `loginNewAccount()` and `reauthenticateAccount(_:)` are no longer `async`, and `SignInSession` also publishes `notice` and `codeSubmitted`.
- **Part C:** the protocol file is `PixelSwitch/Control/ControlProtocol.swift`, not `Shared/ControlProtocol.swift`, because `Shared/` is also compiled into the widget.
  - Accounts can also be named by position (1, 2, …).
  - There is an extra `pixelswitch accounts sign-in status` command.
  - MCP mode speaks both the modern 2026-07-28 revision (stateless, `server/discover`) and the legacy `initialize` handshake.
  - Two test-only environment variables (`PIXELSWITCH_CONTROL_SOCKET`, `PIXELSWITCH_NO_LAUNCH`) keep the integration checks away from the real app.
- **All parts:** built one at a time, in a single lane with no subagents (founder, 2026-09-25: "limit the utilization on this laptop").
