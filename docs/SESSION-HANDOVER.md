# PixelSwitch: session handover

Read this first. Then read `docs/worklog/INDEX.md` for the full history; the newest entries are at the bottom of `docs/worklog/2026-10-16.md`.
Last updated 2026-10-25 (PDT), after 2.0: separate session and weekly limits, priority numbers, and PixelSwitch for Linux.

## 2.0 (2026-10-25): read this first

Released **v2.0 (build 31)**, Mac and Linux, tag `v2.0`, release CI run 40167290828, published 2026-10-25T06:01:50Z.
`scripts/verify-release.sh v2.0` passes every Mac check (Latest, feed 2.0/31, appcast length = DMG, EdDSA, notarized,
stapled) and every Linux check (assets, versions, size, SHA-256, Ed25519, the installer's SHA-256 in the notes).
The founder asked for it on 2026-10-24 (his words verbatim in spec section 0). **Do not touch the PixelDirector repo**
(his other session worked there that night).

- **What 2.0 is:** Session and Weekly limits (defaults 50-100, per account 1-100), priority numbers shown as P1 (lists
  sorted by them; "Priority (lowest number first)" strategy), weekly overflow (D19), login-safety fixes D12/D20, the CLI
  and MCP additions (protocol stays 2), and the Linux version. Contract: `docs/superpowers/specs/2026-10-24-2.0-spec.md`
  (revision 4, D1-D20). Lanes: `docs/superpowers/plans/2026-10-24-2.0-lanes.md`. Every review, re-review and finding:
  `docs/investigations/2026-10-24-linux-and-split-thresholds/` (`review-*.json`, `rereview-*.json`, `final-check-*`,
  `orchestrator-findings.md` O1-O4, `laptop-farm.md`, `evidence/` renders). Worklog WL-2026-10-24-001 and -002.
- **Linux:** `linux/` (Python 3.12+, stdlib daemon/CLI/MCP, PyGObject tray and settings window). Design, Claude Code's
  three locks, and how a running Claude Code notices a rewrite: `linux/README.md`. Tests:
  `python3 -m unittest discover -s linux/tests -t linux` (1670); CI runs them on Ubuntu 24.04 and 26.04 and renders the
  settings window (`linux-settings-renders-*` artifacts). Release assets: `pixelswitch-linux-2.0.tar.gz`,
  `install-linux.sh` (SHA-256 31bdbeeb70388cf40da5797b881455ef0f7c1489e66ebbb4e07c5b9de03247a0), `latest-linux.json`.
- **The founder's laptop** (`ssh ahmed@ahmed-aorus-15p-xd.tail19d9f2.ts.net`): 2.0 installed from the release on
  2026-10-25 ~06:15 UTC; service `pixelswitch.service` active; one account (ahmed@pixelventures.ai) added; auto-switch
  OFF; tray registered; log clean. Its CabDesign farm's `claude` jobs use the same login (`laptop-farm.md`), so switching
  there moves them too; the founder page says so. Uninstall: `~/.local/share/pixelswitch/install-linux.sh --uninstall`.
- **Macs:** at release time both the founder's Mac and the Beeco Mac ran 1.9 with automatic updates on; next Sparkle
  checks were due ~06:50 UTC (Beeco) and ~07:16 UTC (founder). Confirm with PlistBuddy (2.0) and `[init]` in
  `~/Library/Logs/PixelSwitch-app.log` (Beeco: `ssh claadmin@100.125.33.35`).
- **Waiting on the founder:** `docs/decisions/pixelswitch-2.0-2026-10-24.html` (four choices that ship with defaults,
  three hand checks). Record his pasted answers verbatim in `docs/decisions/`, mark the page answered, and ship any
  behaviour change as 2.1.
- **Tests:** Mac `bash Tests/run-unit-tests.sh` 1352/1352 (150/150 conformance, the frozen 1.9 engine oracle). This Mac
  cannot compile SwiftUI view files: CI is the only compile proof for views (see the gotchas).

## Current state

- **Released: 2.0** (build 31, tag `v2.0`, run 40167290828, 2026-10-25T06:01:50Z): see the 2.0 section above. Before it, 1.9 (build 30, tag `v1.9`, run 38924948392, 2026-10-16).
- **Installed:** the Linux laptop (2.0, 2026-10-25). The Macs install 2.0 by themselves at their next Sparkle check (see the 2.0 section).
- **Next session, prove 1.9 on the founder's Mac** (no founder action needed; read, never ask): `/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" /Applications/PixelSwitch.app/Contents/Info.plist` gives 1.9; then count, from the `[init]` of 1.9 onwards, `grep -c 'rate-limited; parked' ~/Library/Logs/PixelSwitch-app.log` (expected 0 for a healthy set of logins) and the 401s per login per hour (the scripts in `docs/investigations/2026-10-16-usage-429/scripts/`). Any 429 on a login that had no 401 in the hour before it would be new evidence: read the investigation's "Still unknown" first.
- **Branches:** `main` holds everything. `part-a-auto-switch-rules`, `part-b-sign-in-links`, `part-c-remote-control`, `auto-update`, `auto-update-default`, `manual-only`, `no-subscription-switch`, `exhausted-switch-and-usage-errors`, `switch-at-threshold` and `fix/usage-429-dead-tokens` are fully merged and can be deleted.
- **Tests:** `bash Tests/run-unit-tests.sh` gives 706/706. **`bash scripts/typecheck.sh` no longer works on this Mac** (since its Command Line Tools moved to Swift 6.4, by 2026-10-16): it fails on main with 4 baseline errors in `PixelSwitchApp.swift` (the SwiftUIMacros plugin is missing), and swiftc then stops, so later files are never checked. Use `bash docs/investigations/2026-10-16-usage-429/scripts/typecheck-reordered.sh .` (SwiftUI view files last; checks all 55 non-view files; the only errors on main are 5 in `SignInView.swift`), the CLI line in that investigation's build notes, and a CI branch build (`gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>`, then `scripts/verify-ci-artifact.sh <run-id>`) as the real compile proof.
- **Users:** the founder is the only one. Release through the updater, then hand-check (memory: sole user). Versions are two-part: 1.5, then 1.6, …, 2.0.

| Version | What it added |
|---|---|
| 1.2 | Auto-switch rules: per-account thresholds, next-account strategy (most room / my order / resets soonest), switch early. Sign-in links (Open, Open in, Copy link, code box). Remote control: the `pixelswitch` CLI and `pixelswitch mcp` over a user-only Unix socket. |
| 1.3 | Settings → About → **Update automatically** (Sparkle downloads silently; the updater delegate relaunches into the update when no switch or sign-in is running). Checks every 6 h. |
| 1.4 | Update automatically is **on by default** (`SUAutomaticallyUpdate: true`). |
| 1.5 | **Manual only (0%)** and per-account thresholds **1–100%**. Remote-control protocol 2 (`AccountInfo.manualOnly`). |
| 1.6 | An active account whose usage request gets 403 ("OAuth authentication is currently not allowed for this organization", no active subscription) is left at the next refresh by rule `noSubscription`, cooldown or not, and is never a target. Decisions: `docs/decisions/2026-09-24-remote-control-answers.md`, last section. |
| 1.7 | Rule `exhausted`: an active account at 100% of its session or week is left for any account a point under its own threshold when none is 10 points under. Usage errors in plain words (`UsageRequestError`), the last reading kept on a 5xx or no connection, **Retry** on the card, **Status** for a 5xx. |
| 1.8 | Auto-switch moves you **at your number** to any account at least a point under its own threshold (`AutoSwitchEngine.hysteresis` 10 → 1), on session, weekly and Fable; 1.7's `exhausted` rule removed. The founder's answer on `docs/decisions/pixelswitch-switch-point-2026-09-29.html` (answered). |
| 1.9 | PixelSwitch **stops causing the usage 429s** ("API Rate Limited. Try again later.") and says what is really happening. `UsageRequestGuard` (pure, unit-tested) gates every usage and profile request: a token that got a 401 is never sent again until it changes, an expired one is renewed first (saved login) or left alone (live login: "Waiting for Claude Code to renew this login"), 20 minutes' rest after any 401 or 429 whatever the number of accounts, a server-refused grant is `needsReauth` (persisted, not polled, not switchable), and the park belongs to the token, so a re-sign clears the card at once. Delegated `claude auth status` refresh removed (0 recoveries in 13). Full story: `docs/investigations/2026-10-16-usage-429.md`. |
| 2.0 | **Separate Session and Weekly limits** (defaults 50-100, per account 1-100), **priority numbers** (P1; lists sorted; "Priority (lowest number first)"), weekly overflow (when no account under its weekly limit can take you, the session limit decides), login-safety fixes (blank logins never saved or written; re-read before write; honest refreshTokenExpiresAt; removing the active account really switches; disposable-login rule; 429 recorded without an account), CLI/MCP additions (protocol stays 2, 21 methods, 20 tools, 24 keys), and **PixelSwitch for Linux** (Ubuntu 24.04/26.04). Spec `docs/superpowers/specs/2026-10-24-2.0-spec.md`. |

## Waiting on the founder

1. **Hand checks** (15 cards, one round, on 1.5 to 1.8; card 4 is the no-subscription fix, card 5 is 1.8's switch at your number, card 6 is 1.7's Retry): [docs/decisions/pixelswitch-hand-checks-2026-09-25.html](decisions/pixelswitch-hand-checks-2026-09-25.html). He pastes the Copy block back. Then:
   - record his answers verbatim in `docs/decisions/`;
   - mark the page answered;
   - fix any failure test-first and ship it as the next minor version.

   Regenerate the page with `python3 scripts/hand-checks/gen-hand-checks.py`, and render it with `node scripts/hand-checks/render-hand-checks.cjs <out-dir>`.
2. **Optional, his call:** a paid graphify refresh of this repo, estimated at $3.10 to $10.43 (163 files would be sent) by `~/.claude/graphify-smart/fleet-audit.py .` on 2026-10-16. Only the free AST update has been run (latest 2026-10-16 after 1.9: 2,325 nodes, 5,271 edges).

## Automatic updates: proven, and 1.6 is the second run

- **Proven 2026-09-26:** at the scheduled check (17:36:35 PDT) the running 1.4 handed off to Sparkle's `Autoupdate` and `Updater` (17:36:37) and 1.5 was running at 17:36:38. There was no WebKit `WebContent` process (no release-notes window) and no click.
- **Next:** 1.6 should install itself at the next scheduled check, about 06:36 UTC on 2026-09-27 (11:36 PM PDT on the 26th), or soon after the Mac wakes. Confirm with `/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" /Applications/PixelSwitch.app/Contents/Info.plist` (1.6), and `grep "\[init\]" ~/Library/Logs/PixelSwitch-app.log | tail -2` for the relaunch time.
- **How to read the system log:** `/usr/bin/log show --start "<local time>" --end "<local time>" --style ndjson` and tally `processImagePath` (Autoupdate, Updater, WebContent, PixelSwitch). Use the full path: in zsh, bare `log` is a shell builtin and fails with "too many arguments".
- **If it didn't happen:** read `UpdateChecker.updater(_:willInstallUpdateOnQuit:immediateInstallationBlock:)` and `AutoUpdatePolicy`, and check SULastCheckTime.

## After 1.6 installs: prove the no-subscription fix on the real account

- claude@pixelventures.ai was answering 403 as of 2026-09-27 03:06 UTC. Hand-check card 4 asks the founder to switch to it by hand; within about 5 minutes the app log (`~/Library/Logs/PixelSwitch-app.log`) should show `[autoSwitch] Rule noSubscription` and then `Switching to`.
- If the founder pastes a time, read the log around it. If instead the log shows `has no active subscription, and no other account has room; staying put`, every other account was at or over its own threshold (a real state, not a bug).

## 1.7 (2026-09-29): a used-up account is left; usage errors in plain words, with Retry

Both built the same day, on the founder's word. Decisions and his words, verbatim: `docs/decisions/2026-09-24-remote-control-answers.md`, last section.

- **Superseded by 1.8 the same day:** the founder chose to drop the 10-point gap entirely, so the `exhausted` rule below was removed. Kept here for the history.
- **The auto-switch bug (Beeco Mac).** vkwok@gobeeco.com sat at 100% of its session. Every other account was 92–99% of its week, none 10 points under the 98% default, so `AutoSwitchEngine.plan` returned nil and nothing was logged. New rule **`exhausted`**: when the 10-point rule finds nothing and the active account is at 100% of its session or week, any account a point under its own threshold is a target. Not for Fable. A stuck "threshold reached, nowhere to go" state is now logged once per change (`noteStayingPut`).
- **The error text.** `UsageRequestError` (its own file, unit-tested; `ClaudeService.UsageError` is a typealias to it) maps 5xx → `server`, no reply → `network(offline:)`, other status → `unexpected`. Each has a plain sentence (`message`, also its `LocalizedError` text). `AppState.recordUsageFailure` is the one catch-all. A 5xx or network failure keeps the last reading, as a 429 does. The card shows **Retry** (`AppState.retryUsage`) and, for a 5xx, **Status** → status.claude.com.
- **Reaching the Beeco Mac:** `ssh claadmin@100.125.33.35` (Tailscale, `claadmins-mac-mini`). Its log: `~/Library/Logs/PixelSwitch-app.log`. Its CLI: `/Applications/PixelSwitch.app/Contents/Helpers/pixelswitch` (`accounts`, `status`, `switch`).

## 1.9 (2026-10-16): the "API Rate Limited" banner was PixelSwitch's own doing

The founder: "on my computer, I'm getting this API rate-limited message, because it's driving me crazy." Diagnosed by a read-only investigation (five lanes, an Opus synthesis, two Opus skeptics), then built, reviewed and released the same day. **Read `docs/investigations/2026-10-16-usage-429.md`** (mechanism, measurements, what was refuted, every decision with its reason, known limits a to f, and the evidence scripts that re-run against `~/Library/Logs/PixelSwitch-app.log*`).

- **In one line:** Anthropic answers about the fourth refused (401) call on a login within an hour with 429 and Retry-After 3600 (all 49 measured values). PixelSwitch kept resending dead logins (an expired live login while Claude Code was idle, via the usage poll and the 5-minute profile lookup; saved logins whose refresh grant was already refused), every hour, forever. Volume was never the cause: a healthy login read 748 times with no 429.
- **Refuted, do not "fix" it again:** "the server got stricter on 2026-10-16". Today's surge was PixelSwitch's round-robin polling the remaining dead logins faster whenever one was parked (every 5 to 15 minutes instead of 20), set off by the 06:53Z auto-switch's extra cycle.
- **Ruled out:** request volume, bursts, Claude Code or Claude.app polling the endpoint on a timer, onWatch (not running), and the Beeco Mac (it holds none of the founder's five accounts; its own 429s were on other accounts and stopped by themselves on 2026-10-12). The earlier note here that blargart@gmail.com on Beeco is refused at every attempt is **not supported** by the logs that still exist there (the older ones have rotated away).
- **Claude Code itself was never rate limited** by any of this; only PixelSwitch's usage and profile lookups were.
- **Still unknown:** whether Anthropic counts per token or per account (per token fits better, medium to low confidence), and who revoked the refresh grants of ahmed@pixelventures.ai (about 2026-10-12 15:53Z) and ahamade@gmail.com (about 2026-10-10 19:01Z). The founder re-signed both on 2026-10-16 morning; all five accounts read normally from 14:48Z.

## Deferred defects (all minor; found by the 1.5 review, evidence in `docs/superpowers/reviews/2026-09-26-manual-only-review.json`)

- **Removing the active account doesn't switch the live login.** This predates 1.5. `removeAccount` marks the fallback active before calling `switchTo`. `performSwitch` then sees the live owner as unknown (the account was just removed) and returns "No switch needed". Claude Code stays on the removed account's login, and the fallback is credited with its usage until the next real switch. Fix: mark the fallback active only after the switch, or skip the "already live" shortcut when the owner is unknown.
- **Race windows of about a second.** If Manual only is set during an auto-switch verification fetch, an already-decided switch can still move you off the active account at its old threshold. If it is set during `performSwitch`'s awaits, the switch can still land on that account.
- **CLI wording.** `thresholdSet`/`switched` say "auto-switch moves you off it at 90%" even when auto-switch is off.
- **Popover help on the active row.** The help on the "Manual only" label says "You can still switch to it here" on the active row too.
- **Usage card while it has no data.** The card shows no "Manual only" line while it waits for data or shows an error.
- **MCP protocol re-check.** `pixelswitch mcp` reconnects after an app restart without re-checking the protocol version.
- **`--version`.** `pixelswitch --version` prints the protocol version, not the app version.

## How things work (pointers, not copies)

- **Project and build.** `project.yml` is the only project source (XcodeGen); never edit `.pbxproj` or `Info.plist`. There is no Xcode on this Mac: full builds, signing and notarization run only in GitHub Actions. `bash scripts/typecheck.sh` is the local compile check.
- **Release.** Write `RELEASE_NOTES.md` first and commit it, then `echo y | ./scripts/release.sh X.Y` (the script stops at a `Proceed? [y/N]` prompt). Verify with `scripts/verify-release.sh vX.Y`. A branch build (no release) can be checked with `scripts/verify-ci-artifact.sh <run-id>`.
- **Auto-switch rules.** `PixelSwitch/Services/AutoSwitchEngine.swift` is pure and fully unit-tested. `AutoSwitchEngine.ceiling(threshold:room:)` is the one rule for whether an account may be switched TO (nil for Manual only). The 1.5 spec is at `docs/superpowers/specs/2026-09-26-manual-only-spec.json`.
- **Remote control.** See AGENTS.md "Remote control" and ARCHITECTURE.md; the specs and plans are under `docs/superpowers/`.
- **Founder decisions, verbatim:** `docs/decisions/2026-09-24-remote-control-answers.md` (every answer since 2026-09-24, including the decisions NOT to do things).

## Gotchas that cost time (so the next session doesn't relearn them)

- **(2.0) This Mac cannot compile SwiftUI view files** (missing SwiftUI macro plugin since its Swift 6.4 tools): the unit
  harness and every local typecheck skip or stop before views. A lane dropped `import SwiftUI` and only CI caught it.
  Run `gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>` and `scripts/verify-ci-artifact.sh`
  before any view change reaches main. Standalone view components can be rendered offscreen
  (`Tools/screenshots/run_settings_renders.sh`), which is how the 2.0 rows were eyeballed.
- **(2.0) Wait on CI with an exit status that follows the run's conclusion** (`gh run watch ... --exit-status`, then
  `gh run view --json conclusion` and exit non-zero unless success). `gh run watch ...; echo` exits 0 on failure and a
  failed build was once reported as passing.
- **(2.0) The GitHub API quota (5,000/hour) is shared by every session on this account.** Polling every 20 s plus lanes'
  `gh` calls exhausted it on 2026-10-24. Poll every 120 s or more; keep lanes off the GitHub API.
- **(2.0) Lanes must not install anything on the founder's machines.** A `brew install pygobject3` pulled in python@3.15
  and made it the default `python3` (restored with `brew unlink python@3.15; brew unlink python@3.14 && brew link
  python@3.14`).
- **(2.0) Ubuntu 24.04 reuses a freed inode at once and stamps directories coarsely:** any "is this still my lock"
  check must compare exact values (statx birth time in ns, an mtime we set with a random ns part), never a tolerance.

- **The repository is no longer a GitHub fork** (`gh repo view --json isFork` gives false). Every push to `main` starts a full CI build, and a tag push starts the release build by itself. `release.sh` now dispatches by hand only if no tag-push run appears within a minute. Two release builds race to upload the DMG and the appcast, and a mismatched pair breaks every update. Cancel docs-only `main` builds with `gh run cancel <id> -R BespokeWoodcraftStudio/PixelSwitch`.
- **Branch pushes don't start CI.** Use `gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>`.
- **CI's disk ignores case.** Two targets whose names or module names differ only by case share a build folder. The CLI target is `PixelSwitchCLI` with `productName: pixelswitch`; `scripts/check-target-names.sh` guards this.
- **The harness can hide CLI mistakes.** The unit harness compiles engine and CLI sources together, so a CLI file using app-only types (such as `AutoSwitchSettings`) passes the tests but breaks the real CLI target. Only `scripts/typecheck.sh` catches it.
- **The .strings files are UTF-16.** The compiled `.strings` in the shipped app bundle can't be grepped; use `plutil -p`. The five source tables must share one key set, and a unit test checks this.
- **Sparkle's settings live in the app's user defaults** (`SUAutomaticallyUpdate`, `SUEnableAutomaticChecks`, `SULastCheckTime`). The Info.plist keys are only defaults.
- **zsh's `log` builtin.** `log show …` fails with "too many arguments"; call `/usr/bin/log`.
- **A 429 with Retry-After 3600 on the usage or profile request means "you sent a refused login too often", not "you polled too much".** Never resend an access token that got a 401; every new usage or profile path must go through `UsageRequestGuard` (1.9), or the hourly 429 loop comes back. And never make a diagnosis experiment that calls the endpoint with an expired token: it spends the quota and re-arms the one-hour block.
- **A 403 on the usage request means no active subscription.** It clears the reading (so no threshold can fire) and is flagged `isNoSubscription` on all three usage paths via `AppState.markNoSubscription`; the engine's `isUnusable` input acts on that flag. Any new usage path must go through it, or the stuck-account bug comes back.
- **Workflows only while ultracode is on.** Multi-agent workflows are allowed only while the founder has ultracode on, and even then they stay small and read-only; otherwise work single lane (memory: single lane).
