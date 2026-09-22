<p align="center">
  <img src="assets/PixelSwitch-icon.png" alt="PixelSwitch icon" width="128" height="128">
</p>

<h1 align="center">PixelSwitch</h1>

<p align="center">
  Switch Claude Code accounts from the macOS menu bar and keep an eye on each account's usage.<br>
  Made by <a href="https://pixelventures.ai">Pixel Ventures</a>.
</p>

<p align="center">
  <a href="https://github.com/BespokeWoodcraftStudio/PixelSwitch/actions/workflows/build.yml"><img src="https://img.shields.io/github/actions/workflow/status/BespokeWoodcraftStudio/PixelSwitch/build.yml?branch=main&label=build" alt="Build Status"></a>
  <a href="https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest"><img src="https://img.shields.io/github/v/release/BespokeWoodcraftStudio/PixelSwitch?label=release&color=0B8F3E" alt="Latest Release"></a>
  <img src="https://img.shields.io/badge/macOS-14.0%2B-000000?logo=apple&logoColor=white" alt="macOS 14.0+">
  <img src="https://img.shields.io/badge/Swift-6.0-F05138?logo=swift&logoColor=white" alt="Swift 6.0">
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/accounts-dark.png">
    <img src="assets/screenshots/accounts-light.png" alt="PixelSwitch showing four Claude accounts, each with its session, weekly and Fable limits and how much is left" width="430">
  </picture>
</p>

<p align="center">
  <sub>Every account, every limit, and what is left of each. Rendered from the app's own views with sample accounts.</sub>
</p>

PixelSwitch is a small menu bar app for people who use more than one Claude Code account. The built-in `claude auth login` flow is destructive: every switch wipes the previous account's credentials and sends you back through the browser. PixelSwitch keeps a backup of each account, swaps the keychain entry and `~/.claude.json` in one step when you switch, and keeps every account ready for a one-click switch back.

It also watches what you have left. Every account's card shows its 5-hour session, its weekly limit and its **weekly Fable allowance**, each with how much is left and when it resets, and PixelSwitch can move you to another account by itself before a limit stops you. Your MCP server logins stay put across a switch, and it proves which account a login belongs to with Anthropic before it touches anything.

## What makes it different

- **It knows about Fable.** Fable has its own weekly allowance, separate from your weekly limit, and it usually runs out first. PixelSwitch shows it on every account and can switch you to an account that still has Fable left. No other Claude account switcher does this today.
- **Your MCP server logins survive a switch.** Claude Code keeps MCP logins (Stripe, Supabase, Linear and the rest) in the same keychain item as your Claude login. Restoring an account's whole item puts back an old copy of them, and those servers ask you to sign in again — measured on one Mac, a switch took 48 stored MCP logins down to 8. PixelSwitch swaps only the Claude login and leaves this Mac's MCP logins alone.
- **A switch cannot mix up two accounts.** Which account is live is proven with Anthropic's own API, not read from `~/.claude.json`, which running Claude Code sessions rewrite from memory. PixelSwitch refuses a switch whose stored login belongs to a different account, and repairs the pairing when it finds one wrong.
- **Light on memory.** Cost and activity are worked out from Claude Code's transcripts, and reading every one of them is expensive: on a heavy-use Mac with about 10,000 transcript files (12 GB), that approach spikes to 3.9 GB at launch and settles near 860 MB. PixelSwitch reads only recent history, the last 24 hours by default, and settles at about 200 MB on the same Mac, with a parse cache of 4.9 MB rather than 70.9 MB. Change the window in **Settings → General → Usage history window**: 24 hours, 3, 7 or 30 days, or all.
- **Its own updates.** PixelSwitch checks its own release feed on this repository, signed with its own update key, so it only ever installs a build published here.

## Install

1. Download **PixelSwitch.dmg** from the [latest release](https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest).
2. Open it and drag **PixelSwitch** into **Applications**.
3. The release is not signed with an Apple Developer ID yet, so macOS blocks the first launch. Clear the download flag once in Terminal:

   ```bash
   xattr -dr com.apple.quarantine /Applications/PixelSwitch.app
   ```

   Or try to open it once, then go to **System Settings → Privacy & Security** and click **Open Anyway**.
4. Open PixelSwitch. It lives in the menu bar; there is no Dock icon.

### On first launch

macOS asks once whether PixelSwitch may use its saved account credentials. Click **Always Allow**. It asks again after an update, because the app's signature changes.

## What you are looking at

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/limits-dark.png">
    <img src="assets/screenshots/limits-light.png" alt="One account card: a green clock for the session limit, a blue calendar for the weekly limit, a purple sparkle for the Fable allowance, each with how much is left and when it resets" width="520">
  </picture>
</p>

One card per account, each with the PixelSwitch mark and a colour of its own down the left edge, so you know whose numbers these are before you read the address. The account you are signed in to right now is ringed in orange, all the way round. Inside, each limit keeps an identity that never changes: a **green clock** for the 5-hour session, a **blue calendar** for the weekly limit, a **purple sparkle** for the weekly Fable allowance. The bar is the other half of the story: it fills and turns red as that limit runs out, which is why two limits that are both nearly gone still read apart at a glance.

The colour is in the chip, the tint and the bar. The words are always plain text, because a label drawn in its own row's colour is a label you cannot read.

This account is nearly out of its session and has 13% of its Fable left. With auto-switch on, PixelSwitch would move to an account that still has room before that stopped anyone.

## Features

- **Non-Interruptive Account Switching**: The native `claude auth logout` clears the current account's credentials, and switching back requires another full OAuth. PixelSwitch keeps a separate backup of each account (keychain token + `~/.claude.json` `oauthAccount` block), atomically swaps both on switch — every added account's credentials stay intact, one-click swap-back, no workflow interruption. Sessions that are already running follow the switch on their next request (see [In-flight sessions](#1-non-interruptive-account-switching)).
- **Multi-Account Management**: Add and switch between different Claude Code accounts with a single click from the macOS menu bar.
- **Usage Dashboard**: Every account's limits in the menu bar dropdown — 5-hour session, weekly, and the weekly **Fable** allowance — each showing how much is left and when it resets, plus today's API-equivalent cost and activity stats (turns, active minutes, lines written, model breakdown). Each limit has its own colour and symbol that never change with usage (green clock, blue calendar, purple sparkle), while the bar itself fills and turns red as the limit runs out, so two limits that are both nearly gone still read apart at a glance.
- **Auto-switch before a limit stops you**: when the active account reaches your threshold on its session, weekly or Fable limit, PixelSwitch switches to the account with the most room left on that limit — and only to one that is also clear of its other limits, so it never has to move you twice. If no account has room, it stays put. Fable switching has its own on/off switch, so you can keep session and weekly switching while leaving Fable as a reading. A 5-minute cooldown and a 10-point hysteresis margin stop any ping-ponging, and the chosen account is re-checked with a fresh reading before the switch happens.
- **A colour per account**: each account has its own colour, shown as a bar down the edge of its card and on its icon, so you know whose numbers you are reading before you read the address. Ten colours, repeating only past ten accounts, and each account keeps its colour between launches. Turn it off in **Settings → Account display**.
- **The live account is ringed in orange**: the account you are signed in to right now carries a bright orange ring right round its card, on the Usage tab and in the Accounts list, so you find it without reading anything.
- **Honest cost history**: the Costs tab totals only the history PixelSwitch actually keeps, which is bounded by your usage history window. A "Last 7 days" or "Last 30 days" total appears only when that much history exists; otherwise one card states the real span.
- **Configurable Menu Bar Modules**: Build your own iStats-style menu bar readout. Choose any combination of the PixelSwitch logo, account name, 5-hour session usage, weekly usage, Fable usage, today's cost, and session/weekly reset countdowns — each rendered as a compact two-line module (label over value, with monochrome progress bars for utilization). Drag to reorder and toggle modules in Settings, with a live preview.
- **Desktop Widgets**: Native macOS desktop widgets in small, medium, and large sizes showing account usage, costs, and activity stats, plus a circular ring variant. Widgets need a build signed with an Apple Developer ID; the public release is not signed yet, so its widgets do not load (see [Install](#install)).
- **In-App Updates**: Powered by [Sparkle 2.x](https://sparkle-project.org/), reading PixelSwitch's own release feed. Every release is signed with the PixelSwitch update key, so the app only accepts updates published here.
- **Dark Mode**: Full light and dark mode support with adaptive colors that follow your system appearance.
- **Internationalization**: Available in English, 简体中文 (Chinese), 日本語 (Japanese), Deutsch (German), and Français (French).
- **Privacy-Focused UI**: Automatically obfuscates email addresses and account names in screenshots or screen recordings to protect your identity.
- **Zero-Interaction Token Refresh**: Intelligently handles Claude's OAuth token expiration by delegating the refresh process to the official CLI in the background.
- **Seamless Login Flow**: Add new accounts without ever opening a terminal. The app silently invokes the CLI and handles the browser OAuth loop for you.
- **System-Native UX**: A clean, native SwiftUI interface that behaves exactly like a first-class macOS menu bar utility, complete with a fully functional settings window.

## Key Features & Architecture

PixelSwitch employs several specific architectural strategies, some uniquely tailored to its operation and others drawing inspiration from the open-source community (notably [CodexBar](https://github.com/steipete/CodexBar)).

### 1. Non-Interruptive Account Switching

The headline feature: **PixelSwitch preserves every added account's credentials, so switching never interrupts your multi-account workflow.**

The native CLI has no clean "switch account" command — `claude auth logout && claude auth login` clears the current account's keychain entry and triggers a full browser OAuth round-trip; switching back to the previous account means another full OAuth. PixelSwitch takes a different path:

- Each previously-added account is stored in PixelSwitch's own per-account backup (`~/.ccswitcher/backups.json`), containing the OAuth token JSON and the matching `oauthAccount` block from `~/.claude.json`.
- A switch changes only the Claude account login. Claude Code stores MCP server logins (`mcpOAuth`) in the same Keychain item; PixelSwitch keeps this Mac's copy of those, so an MCP login made or renewed since that account was last active is never replaced by an older one. Connectors added on claude.ai belong to the Claude account, not to the Mac, so those still differ between accounts.
- When the user picks a different account, PixelSwitch (a) writes the target account's token to the macOS Keychain entry `Claude Code-credentials` through `/usr/bin/security`, the same tool the Claude CLI uses, updating the item in place and reading it back byte for byte before it counts the write as done, and (b) overwrites the `oauthAccount` block in `~/.claude.json`. No destructive logout/login side effects.
- The token reaches `security` on stdin (`security -i`), not on the command line, whenever it fits `security`'s 4,095-byte command line. A credential that also carries MCP server tokens is usually longer than that; it is then passed as hex on the command line, as the Claude CLI does itself. (Writing it through the Security framework instead would move the item out of the partition `/usr/bin/security` can read without a prompt, which breaks the CLI.)
- Result: every added account's credentials stay intact in the backup, available for one-click swap-back without re-OAuth. New `claude` invocations immediately use the newly-selected account.

**In-flight sessions**: a running Claude Code session keeps its token in memory. Before each API request it checks the modification date of its plaintext fallback file, `~/.claude/.credentials.json`: if the file is absent, it re-reads the Keychain (with a 30-second cache); if the file exists and its date has not changed, it keeps the old token for hours, until the access token expires. That file usually does not exist, but a Mac where a Keychain write ever failed can have a stale copy, and on such a Mac running sessions kept billing the old account for 11+ hours after a switch. So after every switch PixelSwitch bumps that file's modification date (it never creates, rewrites or deletes the file), which is the CLI's own signal to re-read the Keychain. Running sessions move to the new account on their next request. If you need an in-flight session to finish on its original account, end it before switching.

**Whose login is this?** `~/.claude.json` names an account, but a running Claude Code session rewrites that file from memory, so it can name one account while the keychain holds another's login. PixelSwitch therefore proves ownership from the login itself: it asks Anthropic's `/api/oauth/profile` who the token belongs to and matches the answer against each stored account. It backs up a login only under the account that owns it, refuses a switch whose stored login belongs to someone else, removes a login saved under the wrong account, and never renews one that is live or stored elsewhere. If `claude auth status` stops answering, it gives up after 30 seconds and checks the store directly rather than leaving a switch half done.

### 2. Auto-Switch, Including the Fable Allowance

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/auto-switch-dark.png">
    <img src="assets/screenshots/auto-switch-light.png" alt="The active account has used all of its Fable allowance; PixelSwitch switches to another account that still has 77% of its Fable left and room on its other limits" width="480">
  </picture>
</p>

Claude accounts have three limits worth watching: the 5-hour session window, the 7-day weekly window, and a weekly per-model allowance for Fable that the usage API reports separately. Fable usually runs out first, and when it does, the account is still perfectly usable for everything else — which is exactly why a switcher has to treat it as its own limit rather than folding it into the weekly figure.

- The session and weekly windows are checked first; Fable is checked when they have not triggered.
- A candidate must sit at least the hysteresis margin below your threshold **on the limit that fired and on the session and weekly windows**, so a Fable switch never lands on an account that is about to hit its weekly limit.
- An account with no reading for the limit that fired is never chosen: on a round-robin poll, "no sample yet" is not the same as "plenty left".
- The same rule that ranks candidates is the one that verifies the chosen account against a fresh reading, so ranking and verification cannot drift apart.
- Fable switching can be turned off on its own in **Settings → Auto-switch**, leaving session and weekly switching untouched.

### 3. Terminal-Free Login Flow (Native `Process` + `Pipe`)

Unlike tools that build complex pseudoterminals (PTYs) to handle CLI login states, PixelSwitch uses a minimalist approach to add new accounts:

- We rely on native `Process` and standard `Pipe()` redirection.
- When `claude auth login` is executed silently in the background, the Claude CLI detects the non-interactive environment and automatically launches the system's default browser to handle the OAuth loop.
- Once the user authorizes in the browser, the background CLI process terminates with exit code 0. PixelSwitch then captures the newly-generated keychain credentials and `oauthAccount` block — the user never opens a terminal.

### 4. Delegated Token Refresh (A Different Path Than CodexBar)

Claude's OAuth access tokens have a short lifespan (~8 hours) and the refresh endpoint is protected by the Claude CLI's internal client signatures and Cloudflare. Third-party apps that want silent auto-refresh have two paths, and PixelSwitch and [CodexBar](https://github.com/steipete/CodexBar) take **fundamentally different** approaches here:

- **CodexBar's approach**: directly POST to Anthropic's non-public OAuth refresh endpoint (`https://platform.claude.com/v1/oauth/token`) with a hardcoded `client_id` (`9d1c250a-…`, extracted from the Claude CLI binary) plus the `refresh_token` from the keychain, then parse the response and write the new tokens back themselves. Pros: no subprocess, fast. Cons: this endpoint and client_id are **not** officially documented by Anthropic — if they rotate the client_id, change the endpoint, or add client attestation, refresh silently breaks until the next app update ships.
- **PixelSwitch's approach**: listen for `HTTP 401: token_expired` from the Anthropic Usage API; when caught, launch a silent background `claude auth status` — a read-only command — which lets the official Claude CLI use **its own, Anthropic-maintained** refresh logic to fetch a new token and write it back to the keychain. PixelSwitch re-reads the keychain and retries the usage fetch.

We deliberately chose the latter, trading a tiny per-refresh subprocess overhead for two real wins:

1. **Safer**: refresh goes through Anthropic's own CLI auth mechanism. PixelSwitch never holds or replays their internal `client_id`. If Anthropic adds stricter client-side checks (e.g. binary attestation), we automatically inherit them with no app update needed.
2. **Future-proof**: endpoint, client_id, token format — none of it is ours to maintain. CLI upgrades automatically deliver new refresh logic.

The user-visible result is the same as CodexBar's: seamless, zero-interaction. The difference is **who's on the hook for keeping up with Anthropic's private OAuth surface** — CodexBar takes that on themselves (faster, riskier); PixelSwitch delegates to the official CLI (small subprocess cost, safer).

### 5. Local JSONL Parse Cache (Performance)

Cost summaries and today's-activity stats are computed from Claude Code's per-session JSONL files under `~/.claude/projects/`. A heavy user's directory can be hundreds of megabytes across thousands of files. Re-parsing the whole tree every 5 minutes pegs the CPU on an idle machine, which is why the window exists.

- PixelSwitch maintains a persistent per-file parse cache at `~/Library/Application Support/PixelSwitch/session-parse-cache.json`, keyed by file mtime.
- On each refresh, files with unchanged mtime are skipped entirely — the cache holds their previously-parsed aggregates and the result is summed in memory.
- Only the actively-modified files (typically just your current Claude Code session) get re-parsed. Steady-state refreshes drop from ~5 seconds of saturated CPU to under 100ms.

### 6. Security-CLI Keychain Reader

Reading from the macOS Keychain via native `Security.framework` (`SecItemCopyMatching`) from a background menu bar app sometimes surfaces a blocking system UI prompt — "PixelSwitch wants to access your keychain". To bypass this, PixelSwitch adopts CodexBar's strategy:

- We execute the system-bundled tool `/usr/bin/security find-generic-password -s "Claude Code-credentials" -w`.
- When macOS prompts the user *the first time*, the user clicks **"Always Allow"**. Because the request comes from a system binary rather than our signed app, the grant persists permanently.
- Subsequent background polling is completely silent.

**About PixelSwitch's own backup keychain entries**: the per-account backup store (`me.xueshi.ccswitcher.backups`) is a keychain entry PixelSwitch creates and owns, so there's no cross-vendor prompt to dodge. We read/write it via the native `Security.framework` (`SecItemCopyMatching` / `SecItemAdd`) — no subprocess, no prompt. In short: **the `/usr/bin/security` subprocess approach is reserved specifically for the cross-vendor read of Claude Code's keychain entry; everything else uses the most direct native API.**

### 7. Team-ID-Prefixed App Group (No "Access Data From Other Apps" Prompt)

macOS 15 Sequoia silently changed the rules for App Group containers: any non-Mac-App-Store, non-TestFlight app whose App Group ID does NOT begin with the developer Team ID triggers a TCC "App Management" prompt on every launch (and again after every auto-update that changes the binary's cdhash). PixelSwitch's App Group is declared as `$(TeamIdentifierPrefix)ai.pixelventures.pixelswitch`, the Team-ID-prefixed form, which macOS auto-authorizes for Developer-ID-signed apps without a provisioning profile. The app reads the group ID from its own signed entitlements at runtime, so it always matches whichever team signed the build. An unsigned or ad-hoc build has no entitlements and never touches a group container at all, which is why it never shows the prompt (and why its widgets stay empty).

### 8. SwiftUI `Settings` Window Lifecycle Keepalive for `LSUIElement`

Because PixelSwitch is a pure menu bar app (`LSUIElement = true`), SwiftUI refuses to present the native `Settings { … }` window — a known macOS quirk where SwiftUI assumes the app has no active scene to attach Settings to. PixelSwitch implements CodexBar's **lifecycle keepalive** workaround:

- On launch, the app creates a `WindowGroup("PixelSwitchKeepalive") { HiddenWindowView() }`.
- `HiddenWindowView` intercepts its underlying `NSWindow` and makes it a 1×1 pixel, completely transparent, click-through window positioned off-screen at `(-5000, -5000)`.
- Because this "ghost window" exists, SwiftUI is convinced the app has an active scene. When the user clicks the gear icon, we post a `Notification` that the ghost window catches to trigger `@Environment(\.openSettings)`, producing a perfectly functioning native Settings window.

## Build from source

The project is generated with [XcodeGen](https://github.com/yonaskolb/XcodeGen); `project.yml` is the only source of truth.

```bash
brew install xcodegen
xcodegen generate
open PixelSwitch.xcodeproj

git config core.hooksPath .githooks   # once per clone: see below
```

That last line turns on `.githooks/pre-push`, which refuses to push anywhere except this repository. PixelSwitch began as a fork, so a remote pointing at the original is one command away from existing again, and a push to it would put this work in someone else's repository. Git does not enable a repository's hooks by itself, so a fresh clone has to opt in.

To sign locally, set your own Apple team as `DEVELOPMENT_TEAM` in `project.yml` (both targets). Without Xcode, push to `main` and the [build workflow](.github/workflows/build.yml) produces a universal DMG as a build artifact; pushing a `v*` tag publishes a release with the DMG and the Sparkle `appcast.xml`. The app icon and the menu-bar mark are both drawn by `Tools/icon/make_icon.py`, so there is one source for the logo. `Tests/run-unit-tests.sh` runs the unit tests with nothing but the Swift compiler (no Xcode needed); CI runs them before every build.

## Credits

PixelSwitch began as a fork of [CCSwitcher](https://github.com/XueshiQiao/CCSwitcher) by [Xueshi Qiao](https://github.com/XueshiQiao), and a majority of this code is still his: measured with `git blame -w -M -C -C` at the v1.0.12 tag, 7,810 of 11,238 Swift lines. Four other people contributed a further 687 lines to that project before this fork existed: [neonwatty](https://github.com/neonwatty), Sandor Bogyo, Kyoube Lyu and AlexDesign420. Thank you, all of you. Several ideas come from [CodexBar](https://github.com/steipete/CodexBar).

**Licensing.** The CCSwitcher repository publishes no license, which under copyright means no rights are granted by default. PixelSwitch therefore carries no license of its own: one cannot be granted over code this project does not own. If CCSwitcher adopts a license, PixelSwitch will follow it.

PixelSwitch changes, the app icon and the Pixel Ventures name and mark are © 2026 Pixel Ventures.
