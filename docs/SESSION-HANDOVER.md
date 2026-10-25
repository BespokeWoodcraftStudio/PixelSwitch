# PixelSwitch: session handover

Read this first. Then read `docs/worklog/INDEX.md` for the full history; the newest entries are at the bottom of `docs/worklog/2026-10-16.md`.
Last updated 2026-10-16 (PDT), after 1.9: PixelSwitch stops causing the usage 429s ("API Rate Limited") and says what is really happening.

## 2.0 IN PROGRESS (2026-10-24): read this first

The founder asked for separate session and weekly auto-switch limits (example 90% / 50%), priority numbers per account
("ahmed@gmail.com = 3, ahmed@pixelventures.ai = 1"), and a Linux version tested on his Ubuntu laptop, then a release to
every client. Builders are Sonnet, QA is Opus. **Do not touch the PixelDirector repo** (his other session works there).

- **Branch `release-2.0`** (pushed to origin; `main` is untouched until release). Contract:
  `docs/superpowers/specs/2026-10-24-2.0-spec.md` (revision 3, decisions D1-D20). Lanes, file ownership, checks:
  `docs/superpowers/plans/2026-10-24-2.0-lanes.md`. Recon, reviews and findings:
  `docs/investigations/2026-10-24-linux-and-split-thresholds/`.
- **Merged into `release-2.0`:** lane V (150 shared cases, `Tests/conformance/`), lane MA (Mac engine, AppState rules,
  D12 usable-login rule, re-read before write, renewal expiry, D20 remove fix; 1039/1039 unit tests; CI branch build
  40151307161 passed `scripts/verify-ci-artifact.sh`), lane LA (Linux pure core in `linux/`; 672 tests on 3.14, 3.12 and
  the laptop).
- **Running when this was written:** lane MB (Mac views, control, CLI, MCP, strings, docs) in `.claude/worktrees/mb-mac-ui`,
  lane LB (Linux daemon) in `.claude/worktrees/lb-linux-daemon`, Opus reviews of MA and LA. Still to come: lane LC (Linux
  CLI, MCP, tray, settings window, packaging, CI jobs), Opus reviews and fix rounds, the founder page
  `docs/decisions/pixelswitch-2.0-2026-10-24.html`, release notes, `scripts/release.sh 2.0`, `scripts/verify-release.sh v2.0`,
  the laptop install from the real release (auto-switch off).
- **If a lane died:** its worktree and branch hold its commits; re-brief a new lane from the lanes file, never edit the
  conformance vectors, and run the lane's checks yourself before merging.
- **Laptop:** `ssh ahmed@ahmed-aorus-15p-xd.tail19d9f2.ts.net` (key auth). It runs the CabDesign farm, whose `claude`
  jobs use the same login (`laptop-farm.md`). Every test there follows spec 5.7 (scratch HOME under
  `~/.cache/pixelswitch-dev/`, test mode, fabricated logins, no systemctl, no sudo).

## Current state

- **Released: 1.9** (build 30, tag `v1.9`, release CI run 38924948392, published 2026-10-16T15:37:20Z), verified with `scripts/verify-release.sh v1.9`: Latest, feed 1.9/30, appcast length equals the DMG, EdDSA signature verified, notarized and stapled. The two new messages are in all five shipped string tables (`plutil -p`), and "API Rate Limited" is gone. The branch build before it (run 38924140452) passed `scripts/verify-ci-artifact.sh`. The main-push build of the bump commit (run 38924944037) was cancelled as redundant, as on every release.
- **Not yet installed anywhere** at 08:40 PDT on 2026-10-16: both Macs run 1.8 and install 1.9 by themselves within about 6 hours (or Settings → About → Check for Updates).
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
