# Deciding review: AUTH-EXPIRY (f519f40 + b345bfa, merged 8cf6e27)

No gate result: there is no `.claude/agent-stack.json` at /Users/ahmed/Documents/GitHub/PixelSwitch (run `pixelos profile init --gate`), so this verdict is **ungated**. The one command the brief allowed was run instead (below).

Reviewer: senior staff reviewer, deciding pass for a reserved-class change (credential path, switch path, auto-switch input).
Tree: HEAD 8cf6e2795db46b1ffd4efbceaa3fdc24c73db081 at the start and again before writing; `git status` showed only `?? .claude/worktrees/` both times. Reviewed `git diff 53bde1c..8cf6e27` plus the code each hunk touches.

## Verdict

**SHIP (ungated).** P0 0, P1 0, P2 4, P3 9. Nothing blocks. The P2s are follow-ups; F1 and F2 are one or two lines each.

First pass: no `pixelos:staff-reviewer-jr` verdict was supplied. I was invoked directly because this is a reserved class, which is a valid reason, so there were no earlier findings to confirm.

## Executable proof

`bash Tests/run-unit-tests.sh` on main at 8cf6e27: **exit 0**, last line `1495 passed, 0 failed` (173 lines mention "auth"). The full log is at
/private/tmp/claude-501/-Users-ahmed-Documents-GitHub-PixelSwitch/f704c667-ad7d-495a-8dda-60528d78d198/scratchpad/unit-auth.log

## What I settled by evidence, beyond the code

1. **What Claude Code writes when it gives up on a login.** I read the installed bundle 2.1.321 read-only, using `strings` on /Users/ahmed/.local/share/claude/versions/2.1.321. Its dead-token clear, `GRo`, does this:
   `mutate(y => { b = y.claudeAiOauth; if (!b || b.refreshToken !== e) return y; return {...y, claudeAiOauth: {...b, refreshToken:"", accessToken:"", expiresAt:0, loginEnded: ...}} })`
   - It blanks the login only when the STORED refresh token is the one the server just refused (compare-guarded).
   - After a failed refresh it first re-reads the store and takes any different access token it finds there ("race recovered").
   Consequences:
   - (a) `AuthHealth.shape` matches the real blank. A blank appears only after a real `invalid_grant` on the very refresh token held live. A blank caused by some other account's stale session is not possible.
   - (b) `settleLeftLiveLogin` normally gets `invalid_grant` back and rotates nothing. When it does rotate, no process can still use that grant: the store now holds the target, and any process holding the old grant re-reads and moves to the target. So settle does not race Claude Code's refresh.
   - (c) The bundle also has a `projects_scope_expansion` refresh that spends the refresh token whether or not the access token has expired. This bears on F3.
2. **The real case** (~/Library/Logs/PixelSwitch-app.log). I read it with emails redacted and printed no secrets.
   - Auto-switch was on: 08:20:37Z auto-switched to 318644E2.
   - 10:35:31Z: live login healthy, 27081 characters.
   - 10:40:31Z: `getAuthStatus failed: exit 1`, live login 26879 characters, `No token for` the active account.
   - Last readings before 10:41:30Z: 89CB3C05 weekly 2% (10:20:34), E051B6B1 weekly 63% (10:40:33). Only 2DB03CFF was flagged (since 02:30:33).
   - What the new logic does with this:
     - The first watch read after the blank sees `.blank` with the CLI failing (`firstSighting`). The next read, 30 s later, confirms.
     - `flagNeedsReauth`, then `evaluateAutoSwitch`, cooldown bypassed, rule 0, one fresh read of 89CB3C05, then the switch (the 13:39:42 switch took about 5 s).
     - Total is about 65 to 75 s after the blank. This assumes 89CB3C05 and E051B6B1 are not Manual only; I did not read their thresholds.
   - Settle then renews 318644E2's saved copy. Per point 1 the server refuses it, so 318644E2 is flagged and a later manual switch back to it is refused.
3. **Operational note, outside this change but urgent for the founder.**
   - 13:39:12Z: the founder re-signed 2DB03CFF. 13:39:42Z: switched by hand to 318644E2, the account Claude Code blanked at 10:40.
   - That switch wrote back 318644E2's saved copy. At 10:35:31 that copy equalled the live login (reconcile logged no update), so it very likely carries the refresh token the server refused at 10:40.
   - The live login has stayed unchanged (27081 characters) through 14:35:31Z, the end of the log.
   - The installed 2.0 has no watch. Expect the same stall the next time Claude Code renews 318644E2.

## Findings

### F1 (P2) The sign-in path does not clear the confirmed-dead state, so a just re-signed account can be switched away at once
- Where:
  - `liveLoginWatch.reset()` is called only in `performSwitch` (PixelSwitch/AppState.swift:1306-1308).
  - Its own doc says it is for "a switch, a sign-in, a relaunch" (PixelSwitch/Services/AuthHealth.swift:202).
  - `finishReauthentication` sets the grace period `lastAutoSwitchAt = Date()` and then calls `await refresh()` (AppState.swift:782-787). `finishNewAccountSignIn` `.existing` does the same at :657.
- Scenario:
  1. Auto-switch is on and X's live login is confirmed dead.
  2. Nothing verified as a target, so PixelSwitch stayed on X.
  3. The user re-signs X with the ↻ button or `pixelswitch accounts reauth`.
  4. In the post-sign-in `refresh()`, `evaluateAutoSwitch` sees `isUnusable(X) == true`, because `confirmedDeadAccount` is still X until the next 30 s look (AppState.swift:1692).
  5. The cooldown is bypassed, so the grace period does nothing, and rule 0 moves the user off the account they just repaired whenever any candidate now verifies.
  6. With no candidate, it logs "needs sign-in ... staying put" right after a successful sign-in.
  A watch tick that happens to land during that refresh hides the bug about 20% of the time.
- Fix: in every sign-in success path (and in `adoptActive`), before the refresh, add `liveLoginWatch.reset(); deadLoginNeedsEvaluation = false`.
- Pin: none exists. A test can only be a source pin today because AppState is not in the harness. Better: route "which events reset the watch" through `LiveLoginWatch` and test that.

### F2 (P2) A failed re-read after a successful renewal writes the spent pair into Claude Code's live login
- Where: AppState.swift:1471 `if let current = keychain.getAccountBackup(...) { backup = current }`. The line itself predates this change. The change sends every target whose access token expires within 60 min through it (:1503-1506, :1523-1525), in addition to the expired targets that used it before.
- Scenario:
  1. The renewal rotates and saves the new pair, then `getAccountBackup` returns nil (the store is briefly unreadable).
  2. `backup` stays at the pre-renewal pair, whose refresh token was just spent, and the switch writes it live.
  3. With the old access token still accepted, `reconcileLiveLogin` (:1598-1607) proves the owner and saves the live (old) pair over the backup's renewed pair. The rotated refresh token is lost and the account must re-sign.
  4. Otherwise Claude Code blanks the login on its next refresh, and the watch and settle recover it after about a minute of stall.
  Rare, but it is lost credential data.
- Fix: `backup = AccountBackup(token: renewed, oauthAccount: backup.oauthAccount)` (`renewed` is exactly what was saved), or fail the switch when the re-read is nil.

### F3 (P2) The founder's "never switch TO an account that needs authentication" is only partly delivered
- Where: `TargetRenewalPolicy.renewWithin = 3600` (AuthHealth.swift:254), used at AppState.swift:1460 and :1503.
- The gap: a target with a revoked or rotated refresh grant and more than 60 min of access token left is still switched to. The 60-min window rests on "Claude Code first tests the grant at access expiry". The bundle's `projects_scope_expansion` refresh spends the refresh token independently of expiry, which may be what blanked 318644E2 at 10:40 with hours of access token left. So such a switch can stall sessions within minutes; the watch then moves them within about a minute.
- Fix (CTO call): renew the target before every switch (recon recommended "always, or within ~7 h"). It costs one POST to the token endpoint per switch, outside the usage and profile limiter. The rotation is safe for a saved login per the bundle's guarded clear.

### F4 (P2) The core AppState wiring and the false-positive guard are pinned only by source text, or not at all
- Not pinned anywhere:
  - That `authWatchTick` calls `flagNeedsReauth(active)` and `evaluateAutoSwitch()`. Deleting either (the whole "switch within a minute") leaves the suite green.
  - `ClaudeService.probeAuthStatus` (ClaudeService.swift:221-230) mapping `.timedOut`/`.processLaunchFailed`/decode errors to `.unavailable` and only `.cliError` to `.failed`. This is the guard that keeps a CLI timeout from counting as signed out. Its only check is a signature text pin (Tests/UnitTests/AuthHealthTests.swift:564).
- Pins that prove nothing about behaviour:
  - AuthHealthTests.swift:558-559: the second conjunct `"trying next"` already existed before this change (AppState.swift:1793, :1799), so it is vacuous.
  - :519-536, :545-553: string `contains` and string-order checks only.
  - :526-528 says "never switches" but the tick does switch, indirectly, through `evaluateAutoSwitch`.
- Behavioural and good (they would go red if AuthHealth regressed): shape, judge, watch and policy tables, the renewal step table, and the engine tests (which use the real engine). The "replay" (:252-270) replays the real sequence through a test-local copy of the tick, not through AppState, so it pins `AuthHealth`/`LiveLoginWatch` only.
- Fix:
  - Make the CLI mapping a pure `AuthHealth.probe(from error:)` and test it.
  - Extract the tick's decision ("given the change and the settings: flag? evaluate?") into a pure function and test it.

### P3, stale code and comments, plus smaller items (none block)
1. Unreachable branch: `case .stayAndShow(.manualOnly)` and its log line "is Manual only, so PixelSwitch does not move you off it" (AppState.swift:323-324). It contradicts the policy.
2. Stale doc "(`DeadLoginPolicy`: auto-switch on, not Manual only)" (AppState.swift:345).
3. Vestigial API kept "so the switch in AppState needs no edit": `StayReason.manualOnly` (AuthHealth.swift:225-227) and the ignored `activeIsManualOnly` parameter (AuthHealth.swift:235-244, and its callers AppState.swift:317, :348).
4. Misleading cause in output:
   - The log "No subscription on the active account, and no candidate verified; staying put" (AppState.swift:1837) also fires for a dead login.
   - `lastAutoSwitch`/`autoSwitched` events carry `trigger: noSubscription` for a dead-login switch (AppState.swift:1820), so CLI and MCP consumers are told "no subscription". The engine-freeze trade-off accepts this, but no consumer is told.
5. Settle comment vs. code: "Not assumed: the server decides" (AppState.swift:368-373), but no answer (offline, 5xx, store) leaves the flag on permanently (:381-382). It is persisted and never retried, because `needsReauth` blocks renewal. Per the bundle the local evidence is strong, so the outcome is almost always right. The comment and the "server decides" claim should be reconciled, or the check retried once.
6. Settle runs inside `performSwitch` while `isSwitching` is held (AppState.swift:1309). The refresh POST has no timeout set, so the URLSession default of 60 s applies, plus a profile lookup with a 15 s timeout. The UI can sit on "Switching..." and refuse switches and sign-ins for up to about 75 s after the Keychain is already written.
7. A successful settle clears the guard flag but not the card's `isExpired` resign state (`noteCredentialChanged(readNext: false)`, AppState.swift:2168). `isSwitchable` stays false until the account's round-robin visit, up to 7 cycles x 5 min. This is the conservative direction.
8. Reaction-time tails:
   - One fresh verification per evaluation (AppState.swift:1766), and `deadLoginNeedsEvaluation` is cleared before evaluating (:339). If the top candidate's read fails, the next try is the 5-min refresh.
   - Turning auto-switch on after a confirmation also waits for the 5-min refresh.
9. Not covered (outside the brief; say so in release notes):
   - The bundle's `account_on_hold` refresh outcome and a past `refreshTokenExpiresAt` do not blank the login, so the watch cannot see them.
   - The Linux port has no watch (0 hits under linux/).
   - `TargetRenewalPolicy.step(.noBackup, loginIsDead: false)` returns `.switchWithoutRenewal` (AuthHealth.swift:300-301), which proceeds with an in-memory copy of a backup that has just vanished. The old path refused it.

## Answers to the brief's questions

1. **Credential safety: no P0 or P1 found.**
   - The watch only reads, with `security find-generic-password -w`, the same command and the same prompt behaviour as the existing reads, and no log line.
   - The CLI runs only on the blank shape: 2 runs before confirmation, then 1 per 5 min while dead.
   - Nothing in the change writes the live login except the existing `performSwitch`. A blank is never saved (`LoginGate.save`), and a blank live login has no proven owner, so step 1 is skipped.
   - No token is logged: account ids and 8-character SHA-256 fingerprints only.
   - Lost rotation: only F2.
   - Race with Claude Code's own refresh: none (bundle points 1a and 1b).
2. **False positives.**
   - Two reads at least 15 s apart are required. A timer burst after wake cannot confirm in under 15 s, and a firstSighting before sleep plus one read after wake is a true positive (blank is persistent).
   - These all read `.unknown`: CLI timeout or not installed (`.unavailable`), a Keychain item that is missing or locked (`.unreadable`), an environment-token Mac (`.noOAuthLogin`).
   - An expired token, 401, 429, 5xx or no network never reach the verdict.
   - A clock moving backwards restarts the confirmation.
   - Mid-refresh: Claude Code never writes a blank except on a server refusal of the stored token.
   - Two Macs: each Mac has its own grant, so nothing new.
   - Persistent wrong flags: only the narrow cases in P3.5 and F1.
3. **False negatives.** The real 10:40Z case is caught within about 1 min (see "What I settled", point 2). The classes not covered are in P3.9.
4. **Switching.**
   - Every path guards on `isSwitching`/`isLoggingIn`.
   - The cooldown bypass lasts only while the watch holds a confirmation. `performSwitch` resets it, so each bypass needs a new 2-read confirmation; at most one bypassing switch about every 60 s, bounded by the number of accounts.
   - No loop: the account left is flagged by the server's refusal.
   - Never stranded: zero healthy accounts means staying on the already-dead login, with "needs sign-in" shown once in the log.
   - The new `continue` in the candidate loop (AppState.swift:1822-1828) is bounded by the ranked list.
5. **Tests:** see F4.
6. **Left-over:** see P3.1 to P3.4.

On the two UI questions:
- **The Usage card has no sign-in button** (UsageDashboardView.swift:370-395). The user is not left unable to recover: the Accounts tab has ↻ re-authenticate on every row, including the active one (AccountSwitcherView.swift:209-217), the card text points at ↻, and `pixelswitch accounts reauth` exists. Discoverability only, so P3.
- **`UsageDashboardView.canSwitch` ignores `isSwitchable`** (:349-351). A manual switch cannot land on a needsReauth account: `performSwitch` → `validatedTargetBackup` refuses at the `.needsReauth` decision (AppState.swift:1495-1497), after the D12 check, before anything is written. The card only invites a double-click that then shows an error. Cosmetic, P3.

## Regression pins
- Detection logic: AuthHealthTests.swift:252-270 ("auth replay: 10:40:31 ...", "the next read (30 seconds later) confirms it") and the watch and shape tables.
- Engine reaction: AuthHealthTests.swift:382-433.
- No deterministic check exists for: the tick triggering flag+evaluate, the CLI error mapping, or the watch reset on sign-in (F1, F4).

## What I did not review
- The Linux port.
- The view files beyond the two questions asked.
- The ultra worktree.
- Whether 89CB3C05 and E051B6B1 are Manual only on this Mac (I did not read the account settings).
- Claude Code's logout behaviour (whether `claude auth logout` blanks or deletes the item). This decides whether a deliberate logout with auto-switch on gets you switched to another account.
