# PixelSwitch: session handover

Read this first. Then read `docs/worklog/INDEX.md` for the full history; the newest entries are at the bottom of `docs/worklog/2026-09-26.md`.
Last updated 2026-09-26, at the end of the session that built 1.2 to 1.5.

## Current state

- **Released: 1.5** (build 26, tag `v1.5`, CI run 36268925156), verified with `scripts/verify-release.sh v1.5`.
- **Branches:** `main` holds everything. `part-a-auto-switch-rules`, `part-b-sign-in-links`, `part-c-remote-control`, `auto-update`, `auto-update-default` and `manual-only` are fully merged and can be deleted.
- **Tests:** `bash Tests/run-unit-tests.sh` gives 576/576. `bash scripts/typecheck.sh` is clean for the app and the CLI, and also runs the target-name guard.
- **Users:** the founder is the only one. Release through the updater, then hand-check (memory: sole user). Versions are two-part: 1.5, then 1.6, …, 2.0.

| Version | What it added |
|---|---|
| 1.2 | Auto-switch rules: per-account thresholds, next-account strategy (most room / my order / resets soonest), switch early. Sign-in links (Open, Open in, Copy link, code box). Remote control: the `pixelswitch` CLI and `pixelswitch mcp` over a user-only Unix socket. |
| 1.3 | Settings → About → **Update automatically** (Sparkle downloads silently; the updater delegate relaunches into the update when no switch or sign-in is running). Checks every 6 h. |
| 1.4 | Update automatically is **on by default** (`SUAutomaticallyUpdate: true`). |
| 1.5 | **Manual only (0%)** and per-account thresholds **1–100%**. Remote-control protocol 2 (`AccountInfo.manualOnly`). |

## Waiting on the founder

1. **Hand checks** (12 cards, one round, on 1.5): [docs/decisions/pixelswitch-hand-checks-2026-09-25.html](decisions/pixelswitch-hand-checks-2026-09-25.html). He pastes the Copy block back. Then:
   - record his answers verbatim in `docs/decisions/`;
   - mark the page answered;
   - fix any failure test-first and ship it as the next minor version.

   Regenerate the page with `python3 scripts/hand-checks/gen-hand-checks.py`, and render it with `node scripts/hand-checks/render-hand-checks.cjs <out-dir>`.
2. **Optional, his call:** a paid graphify refresh of this repo, estimated at about $2.38 to $8.27 by `~/.claude/graphify-smart/fleet-audit.py .`. Only the free AST update has been run.

## Waiting on time: the first proof of automatic updates

- **Expected:** the founder's Mac is on 1.4 with the box ticked (`defaults read ai.pixelventures.pixelswitch SUAutomaticallyUpdate` gives 1). The last update check was at 18:36 UTC on 2026-09-26, so the next scheduled check is at about **00:36 UTC on 2026-09-27 (5:36 PM PDT on the 26th)**. PixelSwitch should restart into 1.5 by itself.
- **Check:** `/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" /Applications/PixelSwitch.app/Contents/Info.plist` should give 1.5.
  - **Automatic install:** the log shows `Autoupdate`/`Updater` with no WebKit `WebContent` (release-notes window) just before it. Query: `log show --start "<local time>" --end "<local time>" --predicate 'process == "Autoupdate" OR process == "Updater" OR process == "PixelSwitch"' --style compact`.
  - **Manual install:** the release-notes WebView appears first. 1.3 to 1.4 was installed this way, at 11:36 PDT.
- **If it didn't happen:** read `UpdateChecker.updater(_:willInstallUpdateOnQuit:immediateInstallationBlock:)` and `AutoUpdatePolicy`, and check SULastCheckTime.

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
- **Workflows only while ultracode is on.** Multi-agent workflows are allowed only while the founder has ultracode on, and even then they stay small and read-only; otherwise work single lane (memory: single lane).
