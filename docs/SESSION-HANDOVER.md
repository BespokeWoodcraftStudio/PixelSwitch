# PixelSwitch: session handover

Read this first. Then read `docs/worklog/INDEX.md` for the full history; the newest entries are in `docs/worklog/2026-11-10.md` (WL-2026-11-10-001 to -005; -005 is the release).
Last updated 2026-11-10 (PST), after 2.1 was built, merged and **released**: readable colours, Ultra accounts, and the sign-in expiry watch. **Released: v2.1 (build 32), tag `v2.1`, release CI run 42850349982, published 2026-11-10T16:05:46Z.** Before that, 2.0 (2026-10-25).

## 2.1 (2026-11-10): read this first

**State: RELEASED and verified.** Tag `v2.1`, build 32, release CI run **42850349982** (all five jobs green; published
2026-11-10T16:05:46Z, marked Latest). `scripts/verify-release.sh v2.1` exited 0: appcast `sparkle:version` 32 and
`shortVersionString` 2.1, appcast length 4811875 = DMG 4811875, EdDSA "Signature Verified Successfully", Info.plist 2.1 / 32,
`SUScheduledCheckInterval` 21600, helper signed (Team LM3P28DNVQ), deep verify OK, DMG and app "Notarized Developer ID", stapler
"The validate action worked!", and every Linux check PASS (assets, version 2.1, build 32, size 231782, SHA-256, Ed25519, installer
key, the notes carry the installer's SHA-256 `31bdbeeb...`). The shipped Localizable.strings carry the new key "No 5-hour limit"
(read with `plutil -p`, in all five languages). The release script pushed `main` to the bump commit `373da3e` (on top of the 16 lane and records commits since
v2.0 and the count-correction docs commit `d316601`) and the tag. `lane/records-2.1` was already merged. **The next step is not a release: it is the founder's hand check of the installed 2.1.**

- **Next session, prove 2.1 installed on the founder's Mac** (read only, no founder action): Sparkle installs it by itself at its next
  scheduled check (every 6 hours) or on an Install click. Confirm with
  `/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" /Applications/PixelSwitch.app/Contents/Info.plist` (expect 2.1) and
  `grep "\[init\]" ~/Library/Logs/PixelSwitch-app.log | tail -2` (the relaunch time); then
  `grep '\[authWatch\]' ~/Library/Logs/PixelSwitch-app.log`. The hand-check page is
  [docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html](decisions/pixelswitch-hand-checks-2.1-2026-11-10.html).
- **CI note:** the push of `main` (run 42850348491, the same commit as the tag) starts an ordinary CI build; only a tag run publishes (every
  release step is gated on `refs/tags/v`), so it cannot race the release. It finished success. The docs-only push that records this
  release starts another `main` build: cancel it with `gh run cancel <id> -R BespokeWoodcraftStudio/PixelSwitch`.

- **What 2.1 is** (his four requests are verbatim in the last section of `docs/decisions/2026-09-24-remote-control-answers.md`):
  1. **Readable colours** (`53bde1c`): opaque popover, solid card, tile and bar-track fills, stronger text tokens, badge
     text `#111111`; every body pair at least 4.5:1 and every graphic at least 3:1 in light and dark, measured on rendered pixels.
  2. **Ultra** (`b0e9521`, `65c0e82`): a Max 20x account is Ultra: the badge says Ultra, no Session row, no session menu-bar
     modules, no widget session views, Settings shows "No 5-hour limit", the CLI's Session cell is `n/a`. A real `five_hour`
     reading from the API always wins over the stored tier (`Account.sessionLimitObserved`). An unknown plan keeps the 5-hour limit.
     Protocol version stays 2, revision 3 to 4 (`AccountInfo.hasSessionLimit`, optional).
  3. **Sign-in expiry** (`f519f40`, `b345bfa`, `6ee54f6`, `3ff3ce2`): new `PixelSwitch/Services/AuthHealth.swift`.
     Every switch target's saved login is renewed first (skipped when this app renewed it within 300 s with the same token
     fingerprint); a server refusal flags the account and the next target is tried. A 30-second watch reads the live login;
     it counts as dead only when Claude Code has blanked it (empty `accessToken` and `refreshToken`, or `expiresAt` 0) AND
     `claude auth status` exits non-zero, confirmed on two reads 15 s or more apart. Then the account is flagged and the
     UNCHANGED engine is asked to switch through `isUnusable`, cooldown bypassed, never to an unusable target. A Manual only
     active account with a dead login IS left; auto-switch off only flags, logs once and shows. An expired token alone, a 401,
     429, 5xx, offline or a timeout never counts. Log tags: `[authWatch]`, and `[autoSwitch] Rule needsSignIn`.
- **Decisions and their reasons** (a later session must not undo them): the engine and the 150 frozen conformance vectors were
  deliberately not touched (the Linux port mirrors them; a change is a three-place change); Manual only + dead login is left
  (matches 1.6 `noSubscription`; staying on a dead login blocks agentic coding); auto-switch off never moves; renew EVERY target
  before a switch (validates the refresh grant at the moment it matters, one request per switch; the first 60-minute window let
  a revoked grant with a valid access token through); the API's data beats the stored plan tier; Ultra ships Mac-only; the
  Settings → General "Session (5-hour) default" stays (it still governs other accounts). Full reasons: the decisions file.
- **Tests (the release tree: `main` at `f71f451` minus the docs):** `bash Tests/run-unit-tests.sh` printed
  `1763 passed, 0 failed` and `conformance: 150/150 cases agree with the engine`. (The records branch at `4294327`, before
  `3ff3ce2` and the last lanes merged, printed 1733; that older number is superseded.) Linux suite: `python3 -m unittest discover -s linux/tests -t linux`
  1670 OK, 47 skipped. A real Debug and a real Release build (`xcodegen generate`, then the `xcodebuild` line in "How things work") succeeded for
  the app, widget and CLI on this tree. Not run: the live watch against a real dead login (it cannot be caused on purpose),
  `backfillPlans` against real saved logins, the real popover frame.
- **Release (done).** `RELEASE_NOTES.md` carried the Linux installer's SHA-256 (`31bdbeeb...`, unchanged because `linux/` has not changed
  since v2.0; `release.sh` checks it). Ran `echo y | ./scripts/release.sh 2.1` (build 31 to 32, commit `373da3e`, tag `v2.1`, the tag push
  started run 42850349982 by itself, so no manual dispatch), then `scripts/verify-release.sh v2.1` (passed). Result above.
- **Branch note: `lane/ultra` holds `e538ad5` (the MCP description wording) UNMERGED.** `linux/tests/test_mcp.py` pins the
  Linux MCP text to `PixelSwitchCLI/MCPServer.swift` word for word, so landing it alone fails two Linux tests. Land it together
  with the matching `linux/pixelswitch/mcp.py` edit. The other 2.1 lane branches are merged and already deleted locally; keep `lane/ultra` until `e538ad5`
  is landed or dropped. Any worktree left under `.claude/worktrees/` goes with `git worktree remove` (never a sibling folder).
- **Waiting on the founder:** the 2.1 hand-check page,
  [docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html](decisions/pixelswitch-hand-checks-2.1-2026-11-10.html)
  (card 1: is the popover readable, light and dark; card 2: does an Ultra account show Ultra and no 5-hour row; card 3: sign-in
  expiry, information only). He pastes the Copy block back. Record his answers verbatim in `docs/decisions/`, mark the page
  answered (the template's `answered` field), fix any failure test-first and ship it as 2.2. The 2.0 page
  (`pixelswitch-2.0-2026-10-24.html`) and the 2026-09-25 page are still open; do not touch them except to record answers.
- **Live-evidence note.** On 2026-11-10 the founder's Mac had a Claude login die at 10:40Z and the app's 2.0 sat on it until
  he switched by hand at 13:39Z, back to the account with id prefix `318644E2`, which is **ahamade@gmail.com** (found with
  `/Applications/PixelSwitch.app/Contents/Helpers/pixelswitch accounts --json`, which prints no token). Its saved copy likely
  carries the refresh token the server refused (inferred: it equalled the live login at 10:35Z). 2.1's watch leaves it within
  about a minute if it dies again, and the account then shows "Session expired. Re-authenticate (↻) to fix." (CLI: `!`, "cannot
  be switched to until it is re-signed"); fix it with the Accounts tab refresh button or `pixelswitch accounts reauth <account>`.
  Read-only check next session: `grep '\[authWatch\]' ~/Library/Logs/PixelSwitch-app.log` after 2.1 installs.
- **Follow-ups (none blocks the release):**
  - The Linux port has no live-login watch and no Ultra handling (its rows still show Session for an Ultra account).
  - The MCP description commit `e538ad5` (above).
  - The Usage card for a flagged account has no sign-in button (the Accounts tab refresh button and `pixelswitch accounts reauth` work).
  - A Manual only Ultra account whose weekly default is below the session default still has `defaults.relaxed` raise its weekly
    leave-at (needs an engine change, which is a three-place change; no effect at the founder's 91 / 97 defaults).
  - Log lines (`limitsText`, "staying until the session limit") still say "session" for an Ultra account.
  - `MenuBarStripView.swift` should call `MenuBarModule.visibleModules(_:activeAccount:)` (one line plus its pin).
  - `borderedProminent` button labels (white on coral, 3.1:1) were not touched by the colours change; `assets/screenshots` (the
    README pictures) were not regenerated.
  - Not covered by the watch: Claude Code's `account_on_hold` outcome and a past `refreshTokenExpiresAt` do not blank a login.
  - The `autoSwitched` event still reports `trigger: noSubscription` for a dead-login switch (the engine is frozen); only the log says `needsSignIn`.
  - `loginNewAccount`'s `.existing` branch (`AppState.swift` ~682-696) does not update `planTier` from the sign-in status; harmless because `updateActiveAccount` runs on the next refresh; the Ultra pin counts only the other two sites.
- **Cold start, in this order:** this section; `docs/investigations/2026-11-10-readability-ultra-auth/README.md` (index of the
  recons, the three reviews, the renders and every commit); WL-2026-11-10-001 to -004; the last section of
  `docs/decisions/2026-09-24-remote-control-answers.md`; then the code: `PixelSwitch/Services/AuthHealth.swift`,
  `PixelSwitch/Models/AccountPlan.swift`, `PixelSwitch/Models/BrandColor.swift`, and in `PixelSwitch/AppState.swift`
  `startAuthWatch`, `authWatchTick`, `validatedTargetBackup`, `settleLeftLiveLogin`, `windowRule(for:)`, `requestUsage`, `backfillPlans`.
- **Before anything else in a new session: `git fetch`.** The checkout was 104 commits behind when this session started.

## 2.0 (2026-10-25): released earlier

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
- **Macs: both run 2.0, installed by Sparkle on their own** (checked 2026-10-25 07:25 UTC): the founder's Mac
  relaunched into 2.0 at 07:16:49 UTC (5 accounts), the Beeco Mac (`ssh claadmin@100.125.33.35`) at 06:50:00 UTC
  (8 accounts). No error, warning, 401 or 429 in either log since. The Beeco Mac starts with "3 account(s) need to sign
  in again": persisted by 1.9 before the update (1 on 2026-10-19, 2 on 2026-10-21), so not caused by 2.0; those three
  Beeco accounts need re-signing on that Mac.
- **Waiting on the founder:** `docs/decisions/pixelswitch-2.0-2026-10-24.html` (four choices that ship with defaults,
  three hand checks). Record his pasted answers verbatim in `docs/decisions/`, mark the page answered, and ship any
  behaviour change in the next version (2.1 was built without them, so 2.2).
- **Tests:** Mac `bash Tests/run-unit-tests.sh` 1352/1352 (150/150 conformance, the frozen 1.9 engine oracle). (Corrected
  2026-11-10: this Mac's Command Line Tools cannot compile SwiftUI view files, but the installed Xcode 27.2 builds the whole app;
  see "Building and type-checking on this Mac" under Current state.)

## Current state

- **Released: 2.1** (build 32, tag `v2.1`, run 42850349982, 2026-11-10T16:05:46Z; `project.yml` says 2.1 build 32), see the 2.1 section. Before it: **2.0** (build 31, tag `v2.0`, run 40167290828, 2026-10-25T06:01:50Z), see the 2.0 section below, and 1.9 (build 30, tag `v1.9`, run 38924948392, 2026-10-16).
- **Installed:** as last checked, the founder's Mac and the Beeco Mac ran 2.0 (Sparkle installed it by itself, 2026-10-25); the Linux laptop runs 2.0. 2.1 reaches the Macs through Sparkle (about 6 hours or an Install click; not yet confirmed installed); the Linux version is unchanged in 2.1 (the 2.1 Linux tarball is the same code, rebuilt at version 2.1).
- **Next session, prove 1.9 on the founder's Mac** (no founder action needed; read, never ask): `/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" /Applications/PixelSwitch.app/Contents/Info.plist` gives 1.9; then count, from the `[init]` of 1.9 onwards, `grep -c 'rate-limited; parked' ~/Library/Logs/PixelSwitch-app.log` (expected 0 for a healthy set of logins) and the 401s per login per hour (the scripts in `docs/investigations/2026-10-16-usage-429/scripts/`). Any 429 on a login that had no 401 in the hour before it would be new evidence: read the investigation's "Still unknown" first.
- **Branches:** `main` holds everything except `lane/ultra`'s `e538ad5` (the MCP description wording, deliberately unmerged: see the 2.1 section). `part-a-auto-switch-rules`, `part-b-sign-in-links`, `part-c-remote-control`, `auto-update`, `auto-update-default`, `manual-only`, `no-subscription-switch`, `exhausted-switch-and-usage-errors`, `switch-at-threshold` and `fix/usage-429-dead-tokens` are fully merged and can be deleted.
- **Tests:** `bash Tests/run-unit-tests.sh`: 1763 passed, 0 failed, conformance 150/150 on the 2.1 release tree (`main` at `f71f451` minus the docs; the older 1733 was the records branch at `4294327`, before the last lanes merged); see the 2.1 section for what it does and does not include. Linux: `python3 -m unittest discover -s linux/tests -t linux` (1670 OK, 47 skipped).
- **Building and type-checking on this Mac (corrected 2026-11-10).** Xcode 27.2 IS installed here (an older note said it was not). The real build is the proof: `xcodegen generate`, then `xcodebuild -project PixelSwitch.xcodeproj -scheme PixelSwitch -configuration Debug -derivedDataPath <dir> CODE_SIGNING_ALLOWED=NO build` builds the app, the widget and the CLI, views included. The local typecheck scripts are weaker than they look: `bash scripts/typecheck.sh` stops on baseline errors in `PixelSwitchApp.swift` (the SwiftUIMacros plugin of the Command Line Tools) and then checks nothing after them; `typecheck-reordered.sh` (in `docs/investigations/2026-10-16-usage-429/scripts/`) checks all non-view files but does NOT check the function bodies of `AppState.swift`, so a clean run there is false assurance for the file where most 2.1 behaviour lives. `scripts/typecheck.sh` also looks for Sparkle 2.9.0 in `$HOME/Library/Caches/pixelswitch-dev/`, which does not exist on this Mac (the builders used their own Sparkle 2.9.0 copy). A CI branch build (`gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>`, then `scripts/verify-ci-artifact.sh <run-id>`) remains the check for the signed, notarized product.
- **Users:** the founder is the only one. Release through the updater, then hand-check (memory: sole user). Versions are two-part: 1.5, then 1.6, …, 2.0, 2.1.

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
| 2.1 | **Readable colours** (opaque popover, solid cards, tiles and bars, stronger text; light and dark), **Ultra** (Max 20x: badge says Ultra, no 5-hour limit anywhere, `AccountInfo.hasSessionLimit`, protocol 2 revision 4), and **sign-in expiry** (every switch target's saved login is renewed first and a refusal skips it; a 30-second watch leaves a live login Claude Code has blanked, with auto-switch on, within about a minute). Built and merged on 2026-11-10 and released the same day (v2.1, build 32). Decisions: `docs/decisions/2026-09-24-remote-control-answers.md`, last section; evidence: `docs/investigations/2026-11-10-readability-ultra-auth/`. |

## Waiting on the founder

1. **2.1 hand checks** (three cards: the popover by eye in light and dark, an Ultra account by eye, and the sign-in expiry as information): [docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html](decisions/pixelswitch-hand-checks-2.1-2026-11-10.html). He pastes the Copy block back; record it verbatim in `docs/decisions/`, mark the page answered, fix any failure test-first and ship it as 2.2. Nothing blocks anything: 2.1 is already released and the hand check runs on the installed app, as every release since 1.2.
2. **2.0 decisions and hand checks** (four choices that ship with defaults; checks on his Mac, the laptop and the CLI): [docs/decisions/pixelswitch-2.0-2026-10-24.html](decisions/pixelswitch-2.0-2026-10-24.html). He pastes the Copy block back; record it verbatim in `docs/decisions/`, mark the page answered, ship behaviour changes in the next version after 2.1.
3. **Hand checks** (15 cards, one round, on 1.5 to 1.8; card 4 is the no-subscription fix, card 5 is 1.8's switch at your number, card 6 is 1.7's Retry): [docs/decisions/pixelswitch-hand-checks-2026-09-25.html](decisions/pixelswitch-hand-checks-2026-09-25.html). He pastes the Copy block back. Then:
   - record his answers verbatim in `docs/decisions/`;
   - mark the page answered;
   - fix any failure test-first and ship it as the next minor version.

   Regenerate the page with `python3 scripts/hand-checks/gen-hand-checks.py`, and render it with `node scripts/hand-checks/render-hand-checks.cjs <out-dir>`.
4. **Optional, his call:** a paid graphify refresh of this repo, now estimated at $6.75 to $21.38 (355 files would be sent, after 2.0; the free AST update ran 2026-10-25: 7,595 nodes, 17,123 edges); earlier estimate $3.10 to $10.43 (163 files) by `~/.claude/graphify-smart/fleet-audit.py .` on 2026-10-16. Only the free AST update has been run (latest 2026-10-16 after 1.9: 2,325 nodes, 5,271 edges).

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

- **Project and build.** `project.yml` is the only project source (XcodeGen); never edit `.pbxproj` or `Info.plist`. Xcode 27.2 is installed on this Mac: `xcodegen generate` then `xcodebuild ... CODE_SIGNING_ALLOWED=NO build` is the local build (full line in "Current state"); signing and notarization run only in GitHub Actions. Neither typecheck script is a substitute for that build.
- **Release.** Write `RELEASE_NOTES.md` first and commit it, then `echo y | ./scripts/release.sh X.Y` (the script stops at a `Proceed? [y/N]` prompt). Verify with `scripts/verify-release.sh vX.Y`. A branch build (no release) can be checked with `scripts/verify-ci-artifact.sh <run-id>`.
- **Auto-switch rules.** `PixelSwitch/Services/AutoSwitchEngine.swift` is pure and fully unit-tested. `AutoSwitchEngine.ceiling(threshold:room:)` is the one rule for whether an account may be switched TO (nil for Manual only). The 1.5 spec is at `docs/superpowers/specs/2026-09-26-manual-only-spec.json`.
- **Remote control.** See AGENTS.md "Remote control" and ARCHITECTURE.md; the specs and plans are under `docs/superpowers/`.
- **Founder decisions, verbatim:** `docs/decisions/2026-09-24-remote-control-answers.md` (every answer since 2026-09-24, including the decisions NOT to do things).

## Gotchas that cost time (so the next session doesn't relearn them)

- **(2.1) `git fetch` before anything.** This session's checkout was 104 commits behind (local `main` at 1.8, `origin/main` at 2.0) and its own CLAUDE.md and handover said 1.8; `git status -sb` showed no "behind" count because the tracking ref had never been fetched. Compare `git rev-parse main origin/main` before believing a file.
- **(2.1) Opaque surface tokens and text tokens change together.** Make a fill opaque without re-measuring the text on it (or the reverse) and an unmeasured pair is left. The README screenshots were rendered on their own solid backdrop, so the earlier readability check never saw that the real popover was see-through; the harness (`Tools/screenshots/make_screenshots.swift`) now paints the app's `popoverBackground`. Sample rendered pixels from `bitmapData`, not `NSBitmapImageRep.colorAt` (it shifts values). `ImageRenderer` cannot draw the real popover frame.
- **(2.1) Claude Code blanks a login only when the server refused its refresh token** (read from Claude Code 2.1.321's own bundle). A blank therefore means a dead grant, and the saved copy of that login likely carries the same refused token. An expired token alone is normal and Claude Code renews it.
- **(2.1) Behaviour in `AppState.swift` is pinned only by source text** (it is not in the unit harness). Put a decision in a pure function next to its tests (`AuthHealth.swift`, `AccountPlan.swift`), or a pin passes on an inverted implementation.
- **(2.1) The hand-check generator and render script (`scripts/hand-checks/gen-hand-checks.py`, `render-hand-checks.cjs`) hard-code `/Users/ahamade/...`**, which does not exist on this Mac (home is `/Users/ahmed`). The 2.1 page has its own portable pair (`gen-hand-checks-2.1.py`, `render-hand-checks-2.1.cjs`); the old two are unchanged.
- **(2.1) `Tools/screenshots/make_settings_renders.sh` was already broken** (an `AssetColorStubs` clash) before this session.
- **(2.1) A lane never writes the worklog:** `worklog.sh` refuses inside `.claude/worktrees/`; the records lane sets `WORKLOG_LANE_OK=1`.
- **(2.0, superseded by 2.1: the real Xcode build compiles views here) This Mac's Command Line Tools cannot compile SwiftUI view files** (missing SwiftUI macro plugin since its Swift 6.4 tools): the unit
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
