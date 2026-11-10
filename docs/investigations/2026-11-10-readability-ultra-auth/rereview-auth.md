# Re-review: AUTH-FIXES (6ee54f6, merged 81b021c)

No gate result: there is no `.claude/agent-stack.json` in /Users/ahmed/Documents/GitHub/PixelSwitch, so this verdict is **ungated**.

**Tree.** HEAD 81b021c95cfae512bea5fd62d7bdd6f4d36c7225 at the start and again before writing. The range 8cf6e27..81b021c also contains the ultra merge (4b2e87b); I reviewed only 6ee54f6. Read-only throughout.

**Proof.** `bash Tests/run-unit-tests.sh` exit 0, `1660 passed, 0 failed` (as expected). Log:
/private/tmp/claude-501/-Users-ahmed-Documents-GitHub-PixelSwitch/f704c667-ad7d-495a-8dda-60528d78d198/scratchpad/unit-auth-fixes.log

## Verdict

**SHIP.** P0 0, P1 0. New findings are all P3 (5 of them). F4 stays partly open as a P2-class test gap.

## Status of each earlier finding

### F1 (P2): fixed
- `liveLoginReplaced()` (AppState.swift:369-372) now runs in every place the live login is replaced, each time before the grace period or the refresh:
  - `.existing` sign-in (:692)
  - `.new` sign-in (:740)
  - re-authenticate (:809)
  - `adoptActive` (:1621)
  - `performSwitch` (:1354)
- A truly dead login is never lost. The reset only clears memory; the next 30-second look re-reads the Keychain and confirms again within about 60 s.
- Nothing repeats the reset fast enough to starve the confirmation. `adoptActive` runs only when the API proves a different owner, which it cannot do for a blank login.

### F2 (P2): fixed
- AppState.swift:1526-1529 with `TargetRenewalPolicy.savedLoginAfterRenewal` (AuthHealth.swift:396).
- `renewed` is the credential `renewBackupInPlace` saved. `saveAccountBackup` runs `LoginGate.save` (KeychainService.swift:244), and `.refreshed` is returned only after that save succeeds. So `renewed` is always a complete, non-blank, usable credential (the whole credential with `claudeAiOauth` replaced, ClaudeService refresh at :445-464).

### F3 (P2): fixed as the CTO decided
- `mustRenewBeforeSwitch` (AuthHealth.swift:336) is used at AppState.swift:1510-1511, :1561-1564 and :1581-1583. `.refreshFirst` always renews; `.needsReauth` refuses.
- The skip rule holds up:
  - It needs the same saved access-token fingerprint and an age between 0 and 300 s.
  - A rotation, a sign-in or a clock jump in either direction forces a renewal.
  - The worst skip case: the leaving login's step-1 save was skipped and Claude Code rotated meanwhile. That is the existing R3 loss; the watch catches it either way, so the outcome is unchanged.

### F4 (P2): partly fixed
- Real behavioural tests now exist for the extracted pure functions:
  - `reaction`
  - `mayEvaluateNow`
  - `probe(after:)`
  - `mustRenewBeforeSwitch`
  - the `step` table
  - `savedLoginAfterRenewal`
  - `afterSwitchAttempt`
  The probe test also replays two looks.
- The AppState and ClaudeService call sites are still text pins. These mutations stay green:
  1. Gate inverted at AppState.swift:1561 or :1581 (`if !mustRenew`). The pin only looks for the call with its arguments.
  2. `leavesActive: !leavesDeadLogin(active)` at :349. The pin checks only `DeadLoginPolicy.mayEvaluateNow(` and `evaluationsLeft:`.
  3. `renewed: backup.token` at :1527. The pin checks only that `savedLoginAfterRenewal(` and the `backup = AccountBackup(...)` line exist.
  4. The settle condition `leftDeadLogin != account.id` dropped or inverted at :1355. Not pinned.
  5. Any `contains` pin is also satisfied by a commented-out line, e.g. `// liveLoginWatch.reset()`.
  6. `ClaudeService.authStatusFailure` (ClaudeService.swift:235) never executes in the harness. Only its text is pinned; that does catch the specific `.timedOut` mapping line, and the exhaustive switch does the rest.
- The builder's 7 mutation checks cover the pure layer only.

### P3 items from the first review
- **Dead code** (`StayReason.manualOnly`, `activeIsManualOnly`, the unreachable branch, "not Manual only" comments): fixed (AuthHealth.swift:245-246; AppState.swift:321-340, :357-358).
- **Wrong cause:** the log is fixed (`ruleName` at AppState.swift:1812, :1815, :1878; the staying-put line at :1906). Not fixed by decision: the `autoSwitched` event still sends `trigger: noSubscription` (:1887).
- **Settle comment:** fixed (:394-399). New small inaccuracy at :402-404: "a switch back to this account renews it first and joins the renewal in flight". In fact the flagged account is refused at the `.needsReauth` decision (:1554-1555) before any renewal.
- **Settle holding the switching flag:** fixed (:1355 runs it as its own Task). It brings a new P3, N1 below.
- **Card staying unswitchable:** fixed (:411, `noteCredentialChanged(for: left)` with `readNext`).
- **Slow tails:** fixed for a failed verification (5 tries, :342-353, AuthHealth.swift:268). Not fixed: when auto-switch is turned on after a confirmation made while it was off, `evaluations` was set to 0 at confirmation, so it still waits for the 5-minute refresh.
- **`.noBackup`:** fixed (AuthHealth.swift:381, refuses in both cases). The other P3.9 items (`account_on_hold`, past `refreshTokenExpiresAt`, Linux) are untouched and out of scope.

## Attacking what the fixes added

### 1. Rotation safety
- **Persist before live write: yes, always.** `renewBackupInPlace` does the store-write probe (:2335), then the POST (:2340), then the save (:2351/:2356). `.refreshed` is returned only after a successful save. `validatedTargetBackup` returns before `switchAccount` writes the live login. A switch that fails after the renewal leaves the renewed pair saved, so nothing is lost.
- **N2 (P3): crash window, existing order, now hit on every switch.** Between the POST's success and the save there is a profile lookup (:2345, up to 15 s). A crash or Quit in that window loses the only valid refresh token, and `.rotationLost` likewise discards a rotated pair the switch was about to write. Both existed before; renewing every target multiplies how often the window is crossed (it was only expired targets and round-robin). Fix: save the rotated pair first, then check identity and remove the entry if it belongs to another account.
- **N3 (P3): no timeout on the token POST.** `refreshOAuthCredentials` sets no timeout, so the URLSession default of 60 s applies (the 15 s at ClaudeService.swift:338 is another request). Every switch, including the dead-login switch that is meant to unblock sessions, can now hang "Switching..." for up to 60 s before going ahead on `.unreachable`. Fix: a 15 s timeout.
- **Existing hazard, amplified (P3).** A 200 response whose body fails to parse is treated as `.transient` (ClaudeService.swift:441), and the switch then goes ahead with the pre-renewal pair. If the server did rotate in that response, the refresh token written live is spent. This matters only if Anthropic changes the response format, and the round-robin renewals were already exposed to it.

### 2. Harm from renewing every target
- **429 or 5xx:** `.unreachable` → switch without renewal. There is no retry loop: one POST per switch attempt, at most 2 if the profile lookup then sees a 401. The 5 dead-login tries are bounded, and refused targets are flagged and skipped.
- **Another machine:** each Mac's sign-in is its own grant, and nothing syncs or exports the backups (grep found nothing), so a renewal on one Mac cannot touch a login live on another.
- **Running Claude Code processes on this Mac** that still hold an older copy of the target's grant: safe. Per the 2.1.321 bundle they re-read the store under the lock, the blank write is guarded on the stored refresh token, and they recover on a store change.
- **Same grant as the live login:** `localRefusal` (isLive) still prevents renewing it. An unproven owner falls back to `.switchWithoutRenewal`, so nothing is rotated.
- **Two renewals racing:** they join through `renewalsInFlight`. The verification read's renewal plus the switch is handled by the 300 s skip.

### 3. Settle as its own Task (N1, P3)
- **What is safe:**
  - A switch back to the account just left is refused while it is flagged.
  - The 30-second look watches the new login and never touches the account just left.
  - Settle runs on the main actor. `performSwitch`'s own refresh still holds `isSwitching`, so the audit and reconcile skip, and the account just left sends nothing while flagged.
- **What can now race, because settle no longer holds `isSwitching`:**
  - **(a) Late refusal after a quick re-sign.** The user re-signs the account just left while settle's POST is still in flight, and the `invalid_grant` arrives after the re-sign. `flagNeedsReauth` takes the fingerprint from `usageLogin(left)`, which is now the NEW live token. The freshly repaired active account is then flagged: the card says re-sign, its usage is not read (so threshold auto-switch is blind), and this lasts until its token changes (up to 8 h).
  - **(b) Late success after a quick re-sign.** The renewal saves the old-grant pair over the freshly captured backup (:2351 does no compare-and-swap). Reconcile or the next switch's step 1 restores it from the live login.
  - **(c) Auto-update relaunch.** `AutoUpdatePolicy.mayInstallNow` no longer waits for the renewal, so a relaunch in the N2 window can land during settle.
  - **(d) Removal during settle.** Removing the account leaves an orphan renewed credential in the backup store.
- All four need settle's renewal to outlast a full browser sign-in, or to coincide with a relaunch or a removal. Online the POST takes under a second, so this is rare.
- Fix:
  - In `refreshBackupInPlace`/`renewBackupInPlace`, re-read the stored backup after the POST. Only save, or only flag, if it is still the token that was renewed.
  - Skip the flag if the account became active meanwhile.

### 4. `liveLoginReplaced()`
No path resets a real dead login without re-detecting it (see F1).

### 5. Tests that stay green when the behaviour is inverted
See the list under F4. All are AppState or ClaudeService wiring pins; the pure-function tests would go red.

## Not reviewed
- The ultra lane (b0e9521).
- The views and Localizable.strings from that range.
- Linux.
