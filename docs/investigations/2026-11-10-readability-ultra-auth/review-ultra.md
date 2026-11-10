# First-pass review of b0e9521 (merged at 4b2e87b): Ultra / no 5-hour limit

Verdict: SHIP-WITH-FIXES. 0 blocking (P0/P1), 3 x P2, 4 x P3. Read-only: nothing in the repo was touched.

Lens: the launch brief named no Opus seat and there is no `.claude/agents/` or `docs/HOW-WE-WORK.md` in this repo, so I used
the brief's attack list as the lens and its block rule (wrong number, data loss, crash, security hole, interface promising
something it does not do = P0/P1; everything else P2/P3).

## Checks run
- `bash Tests/run-unit-tests.sh` on main (4b2e87b): final line `1614 passed, 0 failed` (full output in
  /private/tmp/claude-501/-Users-ahmed-Documents-GitHub-PixelSwitch/f704c667-ad7d-495a-8dda-60528d78d198/scratchpad/unit-run.txt).
- Read-only reads (no run of claude, no Keychain): `git show b0e9521`, the installed `claude` 2.1.321 binary searched for strings,
  `~/Library/Logs/PixelSwitch-app.log*` searched for usage lines (emails never printed).

## Evidence that bears on the design (read, not assumed)
- `claude auth status` does print `planTier` in 2.1.321: the binary has `u.storedClaudeAiLogin=!0,u.planTier=irr(H,pk())` and
  `function irr(e,n){switch(e){case"pro":return"pro";case"max":switch(n){case"default_claude_max_5x":return"max_5x";case"default_claude_max_20x":return"max_20x";default:return"max_other"}default:return"other"}}`.
  So the CLI emits exactly: pro, max_5x, max_20x, max_other, other. The code treats only max_20x / default_claude_max_20x as Ultra. Good.
- The binary knows no "Ultra" tier string (only default_claude_max_20x, default_claude_max_5x, default_claude_zero), so as of 2.1.321 the
  rebrand is server-side and the tier strings are unchanged. `planDisplayName`, `organizationRateLimitTier`, `userRateLimitTier` do exist in claude.
- The 2026-10-24 Linux recon of Claude Code 2.1.310 lists no `planTier` key in `claude auth status --json`: an older CLI prints none, and the
  backfill (credential rateLimitTier) is what covers that case, including for the active account. Good design.
- The premise holds on the founder's machine: in `PixelSwitch-app.log` (Nov 10) and `.log.1` (Nov 9) there are 0 non-null session readings
  (`getUsageLimits] session=-1` on every line; 2,497 null lines over the three logs). No `[plan]` line exists, so the running app is not this build.

## Findings

### F1 (P2) The hidden session threshold still steers an Ultra account in the engine, so "no effect" and "one number" are not strictly true
- Where: `AutoSwitchEngine.swift:273,304-306` (`relaxed.weekly = max(weekly, session)`), `:527-531` (relaxed pass runs whenever the strict
  pass finds nobody and the strict limit has fired); claims at `CLIRunner.swift:411-412` ("the session value is saved but has no effect"),
  `SettingsAccountsTab.swift:185-186` (one-limit sentence with no overflow clause), `ControlAPI.swift:365-371` (session numbers withheld).
- Scenario (read through the code, not run): an Ultra account with a weekly limit lower than the session value, for example Weekly default 80
  with Session default 91, or `pixelswitch accounts threshold alice --weekly 60 --session 100`. Active Ultra account at weekly 85%, every other
  account over its own strict weekly limit. Strict pass finds no target; overflow holds; relaxed active leave-at = max(80, 91) = 91, so the
  account stays until 91% (or 100% with `--session 100`). The UI says it moves off at 80%, and the CLI says the session value has no effect.
  The same max() raises an Ultra account's arrival ceiling in the relaxed pass. With the founder's own 91/97 defaults weekly >= session, so
  relaxed == strict and nothing differs; it only bites when weekly < session.
- Why it matters: a number the app says is inert changes when the app leaves the account. Narrow, so P2 not P1.
- Fix (sketch): for an account with no 5-hour limit hand the engine `WindowRule(session: weekly, weekly: weekly, ...)` in the `rule:` closure
  (AppState.windowRule(for:)), so relaxed == strict; AutoSwitchEngine.swift and the 150 vectors stay untouched. Or reword the CLI sentence and
  add the overflow clause to the Ultra help text.

### F2 (P2, for the founder) The strip throws away a real five_hour object, so a returning or partial 5-hour limit is invisible
- Where: `AccountPlan.swift:141-147`, `AppState.swift:1989-1990`.
- `droppingSession()` removes any `five_hour` object for an account resolved as Ultra, including one with a real utilization (test
  `AccountPlanTests.swift:169-174` pins this on purpose). The server is then the one saying a 5-hour window exists and the app overrides it.
  If Anthropic keeps the window on some 20x logins or brings it back, the engine never switches on it and agentic coding hits the rate limit
  with no warning. Today every account on the founder's Mac returns null (log evidence above), so there is no live harm.
- Why it matters: this is a product call (founder's order was to remove the limit), so it is filed for him, not as a block.
- Fix (sketch): strip only a `five_hour` whose `utilization` is null (the "0%" case the commit wants to avoid); pass a real reading through.
  This also removes the need for the plan to be right (see F3).

### F3 (P2) A tier is only as fresh as the last time the account was active; a 20x to 5x downgrade is not seen on a non-active account
- Where: `AppState.swift:2071-2082` (backfill fills blanks only), `:778,2525` (the only places a reported tier replaces a stored one: the active account).
- Scenario: account stored as max_20x is downgraded to Max 5x while it is not active. The API now returns a real `five_hour`; `requestUsage`
  strips it; the engine sees an account with a missing session window ("not judged"), ranks it as a target with room, and switches to it while
  its 5-hour is exhausted. After the switch `claude auth status` reports max_5x, the next refresh restores the session reading and the engine
  wants to leave again, but the 5-minute cooldown holds first, so the user sits on a rate-limited account for a few minutes. The other direction
  (5x to 20x) fails safe: the badge stays Max until the account is active. If the CLI later reports no tier, `updatedTier` keeps the stored one (AccountPlan.swift:106-108): fine.
- Fix (sketch): the F2 fix (a real `five_hour` is never stripped) closes this; or clear the stored tier when a non-null five_hour arrives for an Ultra account.

### F4 (P3) `updatedTier` lets the CLI's vague `max_other` / `other` erase a known 20x tier
- Where: `AccountPlan.swift:106-108` fed from `AppState.swift:778,2525`.
- `irr` returns `max_other` for any Max credential whose `rateLimitTier` is missing or new (an older backup swapped in, or a future Ultra tier
  string). `updatedTier(current: "max_20x", reported: "max_other")` returns "max_other": the account stops being Ultra (badge back to Max,
  Session menu back in Settings, widget draws Session bars with no number) until a credential with the tier is live again. Fails safe, self-heals.
- Fix (sketch): treat `max_other` and `other` as "no information" when a recognised tier is stored; `subscriptionType` already moves the account off Max when the plan really changes.

### F5 (P3) `backfillPlans` marks accounts tried before it knows the read worked
- Where: `AppState.swift:2073` (`planBackfillTried.insert`) before `:2075` (`allAccountBackups()` returns nil when the store is unreadable).
- Scenario: the first refresh after launch finds the Keychain store unreadable (locked, prompt denied): every blank-tier account is marked tried and
  not retried until restart, so non-active Ultra accounts keep showing Max with a Session menu. Fails safe.
- Fix (sketch): insert into the set only after `allAccountBackups()` returned non-nil.

### F6 (P3) Tests: what would stay green if the behaviour broke
Behavioural (real code, would go red): resolve inputs, badge, stored plan round trip, `droppingSession`, `AccountPlan.tier(fromCredential:)`,
engine with and without a session window, `ControlSnapshots.accountInfo`, `AccountFields`, `CLIOutput` (accounts, usage, thresholdSet, old-app reply),
`MenuBarModule.visibleModules`, `WidgetAccountData`. Source-text pins only (AppState and the views are not in the harness), each survives a
broken implementation:
- `AccountPlanTests.swift:440-441`: requestUsage only has to contain the words `droppingSession()` and `hasSessionLimit`. Inverting it
  (`stored.hasSessionLimit ? usage.droppingSession() : usage`, which strips every non-Ultra account and keeps Ultra's) passes 1614/0.
- `:466`: the menu bar pin stops at `activeHasSessionLimit:`, so `?? false` or a hard-coded argument passes.
- `CLIRunner.swift:169` `sessionWasSet: session != nil` is unpinned (passing `false` always passes).
- `:453-455`: backfill (blanks only, once per launch, `activeAccount` sync, `saveAccounts`) is substring-pinned only.
- Fix (sketch): move `requestUsage`'s decision into a pure function next to `droppingSession` (for example `usage.applying(plan:)`) and call that from
  AppState, so the inversion becomes a behavioural test; same for the backfill loop body.

### F7 (P3) Surfaces the change did not touch
- `PixelSwitchCLI/MCPServer.swift:59,61,84,87`: tool text still says effectiveThreshold is "the lower of the two" and describes the 5-hour
  session limit with no mention of `hasSessionLimit` (deliberate, "next commit" per the commit message; the Linux test pins it).
- `SettingsThresholdDefaults.swift:21-23`: "Session (5-hour) default" slider and "the session limit decides" caption are unchanged (fine for mixed accounts; wrong if every account is Ultra).
- `ControlProtocol.swift:386` doc still says `revision` (3). `AppState.swift:1850-1886` log lines (`limitsText`, "staying until the session limit") still name the session for an Ultra account (log only).
- `linux/` shows Session for Ultra rows and has no plan tier (follow-up in the commit message).
- b0e9521 carries no worklog entry and no `docs/SESSION-HANDOVER.md` change (CLAUDE.md names the handover as the start page).
- `Tools/screenshots/run_settings_renders.sh` compiles `Account.swift` without `AccountPlan.swift`; I did not compile it (nothing in its file list references the moved members by grep, but it is unproven).

## Attack list, answered
1. Misclassification. Ultra only when (subscription nil or "max") and (tier in {max_20x, default_claude_max_20x} or display name "Ultra"), after
   trim + lowercase; a 5x tier beats the display name; pro/team/enterprise are never Ultra (checked every shape in the brief: nil, "", "  ",
   "MAX", " MAX_20X ", max_5x, "Ultra Plus", pro+Ultra, team+20x, 5x+Ultra). Production `Account.plan` never passes a display name; Ultra by display
   name reaches it only through the backfill turning "Ultra" into the canonical tier. Unknown stays on the 5-hour limit. Staleness: F3, F4.
2. Strip. `{"five_hour":null}`, absent: returned as is; object and object-with-null-utilization: dropped; all nine fields are copied, so weekly, Fable
   (`limits`), extra usage and the oauth-apps/opus/sonnet/cowork windows survive (the memberwise init would not compile if a field were added).
   No 0% or divide-by-zero found: the only `?? 0` on a session value is inside `showsSession` (dashboard 483-486) or the guarded widget ring (597-598).
3. backfillPlans. Reads credential `claudeAiOauth.rateLimitTier`, account-block `organizationRateLimitTier`/`planDisplayName` only; returns text; nothing logged but a count;
   the read runs in `Task.detached` off the main actor (JSON parse of the small credential is on main); once per account per launch (F5 for the failure case);
   writes only the accounts list to UserDefaults when something was filled; reads the same Keychain item `savedLogins()` and `accountsNamed(by:)` already read each cycle, so no new prompt;
   a missing backup or corrupt credential gives nil and is skipped.
4. Protocol/CLI. New CLI on old app: field absent means "has the limit" (`CLIRunner.swift:353`), pinned by test. Old CLI on new app: unknown key ignored, Session
   cell shows a dash, one number in Switches at. Revision 4 is read by nothing (grep), only reported. `effectiveThreshold` for Ultra = `leaveAt.weekly`
   (`ControlAPI.swift:365`), a manual-only Ultra account = the weekly default. `--session N` on Ultra: stored, hidden, reply says no effect (F1).
5. Tests. See F6. Final line 1614 passed, 0 failed.
6. Linux. `linux/pixelswitch/cli.py` reads AccountInfo with `.get`, its validator ignores unknown keys, and it never reads or compares `protocolRevision`; Linux serves its own
   protocol at revision 3 (`protocol.py:45`, pinned by its own `test_protocol.py:206`), and no Linux test reads `ControlProtocol.swift`. The Mac and Linux apps never talk to each
   other. No Linux misbehaviour from `hasSessionLimit` or revision 4. I did not re-run the Linux suite (brief: already 1670 OK).

## Not reviewed
The running app, a real Keychain, `backfillPlans` against real backups, a real `claude auth status` call, the Xcode build, the screenshot tools, the wording of the five translations, the Linux suite re-run.
