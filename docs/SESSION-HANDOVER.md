# PixelSwitch: session handover

Read this first. Then read `docs/worklog/INDEX.md` for the full history; the newest entries are at the bottom of `docs/worklog/2026-09-29.md`.
Last updated 2026-09-29 (PDT), after 1.8: auto-switch moves you at your number to any account under its own threshold.

## Current state

- **Released: 1.8** (build 29, tag `v1.8`, CI run 36615489903), verified with `scripts/verify-release.sh v1.8`; the new Settings sentence is in the shipped strings and the 10-point one is gone. The Beeco Mac installed 1.7 by itself; at about 16:40 UTC `pixelswitch update-check` opened its 1.8 window (one click on Install), or it installs itself within about 6 hours.
- **Branches:** `main` holds everything. `part-a-auto-switch-rules`, `part-b-sign-in-links`, `part-c-remote-control`, `auto-update`, `auto-update-default`, `manual-only`, `no-subscription-switch`, `exhausted-switch-and-usage-errors` and `switch-at-threshold` are fully merged and can be deleted.
- **Tests:** `bash Tests/run-unit-tests.sh` gives 628/628. `bash scripts/typecheck.sh` is clean for the app and the CLI, and also runs the target-name guard.
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

## Waiting on the founder

1. **Hand checks** (15 cards, one round, on 1.5 to 1.8; card 4 is the no-subscription fix, card 5 is 1.8's switch at your number, card 6 is 1.7's Retry): [docs/decisions/pixelswitch-hand-checks-2026-09-25.html](decisions/pixelswitch-hand-checks-2026-09-25.html). He pastes the Copy block back. Then:
   - record his answers verbatim in `docs/decisions/`;
   - mark the page answered;
   - fix any failure test-first and ship it as the next minor version.

   Regenerate the page with `python3 scripts/hand-checks/gen-hand-checks.py`, and render it with `node scripts/hand-checks/render-hand-checks.cjs <out-dir>`.
2. **Optional, his call:** a paid graphify refresh of this repo, estimated at about $2.38 to $8.27 by `~/.claude/graphify-smart/fleet-audit.py .`. Only the free AST update has been run.

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

## Open: blargart@gmail.com on the Beeco Mac never reads

Account `04CADD08-EB89-400D-B727-42C23E97C2B9` has been refused by the usage endpoint with a 429 and a Retry-After of about an hour at every attempt since at least 2026-09-27 (45 times in the log), so it never has a reading and is never a target. Its saved credential is 651 characters, where the others are 1,201 or 3,846, so it may be a different kind of login (for example a long-lived setup token). Next: `pixelswitch usage blargart@gmail.com` on Beeco; compare the stored credential's shape (never print the token) with a working one; if it isn't a normal OAuth login, re-authenticate it (`pixelswitch accounts reauth blargart@gmail.com`). Not urgent: the other accounts cover it.

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
- **A 403 on the usage request means no active subscription.** It clears the reading (so no threshold can fire) and is flagged `isNoSubscription` on all three usage paths via `AppState.markNoSubscription`; the engine's `isUnusable` input acts on that flag. Any new usage path must go through it, or the stuck-account bug comes back.
- **Workflows only while ultracode is on.** Multi-agent workflows are allowed only while the founder has ultracode on, and even then they stay small and read-only; otherwise work single lane (memory: single lane).
