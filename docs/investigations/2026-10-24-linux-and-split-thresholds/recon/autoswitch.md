# Recon: auto-switch orchestration and the threshold / priority surface

Read-only recon of PixelSwitch 1.9 (build 30, `project.yml` MARKETING_VERSION "1.9"), repo HEAD `35f8a99`,
read on 2026-10-24. Every claim cites `file:line`. "Not found" means a grep of the whole repo (excluding
`graphify-out/`) came back empty. Paths are relative to the repo root; `PS/` is shorthand for `PixelSwitch/`.
No secret value is described anywhere in this file.

Purpose: evidence for a spec that (1) splits the auto-switch threshold into a 5-hour session threshold and a
7-day weekly threshold (global default and per account), (2) makes account priority explicit and visible, and
(3) must stay behaviour-identical on a Linux port.

---

## 0. Headline facts, surprises and risks

1. **Today there is exactly one threshold per account and one global default.** The engine compares the
   *higher* of the two windows (`bindingUtilization = max(fiveHour, sevenDay)`, `PS/Services/AutoSwitchEngine.swift:89-98`)
   against that one number. Session and weekly cannot be set apart anywhere (engine, `Account`, Settings,
   control API, CLI, MCP, widget).
2. **The split is a small engine change but a wide surface change.** The pure engine is one file
   (`AutoSwitchEngine.swift`, 565 lines, `import Foundation` only). The same single number is mirrored in
   ~14 non-test source files and 5 string tables (section 7).
3. **Equal thresholds can reproduce today's decisions exactly.** With `session == weekly == T`:
   leave when `s >= T || w >= T` equals `max(s,w) >= T`; eligible when `s <= c && w <= c` equals `max(s,w) <= c`;
   `room = min(T-s, T-w)` equals `T - max(s,w)`. That is the property to pin in tests (section 8), and it
   means the ~167 existing checks in `Tests/UnitTests/AutoSwitchRulesTests.swift` can stay as the regression net.
4. **Priority already exists as the accounts-list order**, shown nowhere as a number in the GUI. Only the CLI
   prints it (`#` column, `PixelSwitchCLI/CLIRunner.swift:270` header, `:276` value; `AccountInfo.position`,
   `PS/Control/ControlProtocol.swift:389-390`). It decides the pick only under the "My order" strategy; under
   the other two strategies it is just the final tie-break (`AutoSwitchEngine.swift:421`, `:432`).
5. **Surprise (stale-literal tests).** `AutoSwitchRulesTests.swift` still passes a literal `hysteresisPct: 10`
   at lines 369, 371, 373, 375, 522, 524, 703, although the app passes `AutoSwitchEngine.hysteresis` which is 1
   since 1.8 (`AutoSwitchEngine.swift:51`). Line 377-379 asserts an 85% account "passes as a drain but not as
   a threshold switch" only because of that literal 10. The 1.8 worklog lesson ("pass the app's own constant",
   `docs/worklog/2026-09-29.md:172`) was applied to `Tests/UnitTests/main.swift` but not here.
6. **Surprise (stale comment).** `AutoSwitchEngine.swift:277-278` documents `hysteresisPct` as "(e.g. 10)".
7. **Surprise (log noise).** The "no active subscription, and no other account has room; staying put" warning
   at `PS/AppState.swift:1183` is not de-duplicated (the other stuck-state line is, via `lastStayingPutNote`,
   `:136`, `:1292-1295`), so it logs on every evaluation, about every 5 minutes.
8. **Release risk is real.** The 2026-09-25 line "nobody has the software but me" (`docs/decisions/2026-09-24-remote-control-answers.md:100`)
   is stale: the Beeco work Mac runs PixelSwitch with a 98% default and "Most room left" (same file `:183`).
   Sparkle checks every 6 hours (`project.yml`: `SUScheduledCheckInterval: 21600`) and installs by itself
   (`SUAutomaticallyUpdate: true`), so a settings or account-storage migration reaches that Mac within ~6 hours
   of a release. Migration must be non-destructive and must keep 98%/98% behaviour.
9. **Protocol contract.** `ControlProtocol.version = 2` (`PS/Control/ControlProtocol.swift:13`) and the CLI
   *refuses* an app with a different number (`PixelSwitchCLI/CLIRunner.swift:64-66`). Adding result fields is
   a shape change, so the doc comment at `ControlProtocol.swift:10-12` says bump it. A bump forces app and CLI
   to update together (they ship in one bundle, so fine) and touches the pinned values in tests (section 8).
10. **The CLI target does not compile the app's settings types.** `PixelSwitchCLI` compiles only its own
    folder plus `ControlProtocol.swift` (`project.yml`, target `PixelSwitchCLI` sources). CLI code cannot call
    `AutoSwitchSettings` (comment at `PixelSwitchCLI/CLIParser.swift:161-163`). The unit harness compiles
    everything together and hides violations; only `scripts/typecheck.sh` catches them
    (`docs/SESSION-HANDOVER.md:90`). Any new constant the CLI needs must be a literal or live in `ControlProtocol.swift`.

---

## 1. The founder's decisions on record (verbatim)

Source file: `docs/decisions/2026-09-24-remote-control-answers.md` (224 lines). Quotes are exact.

### 1.1 Priority (added requirements, 2026-09-25, line 30)

> 1. I want to add priority for which account gets selected next because maybe I have a specific order of accounts I want to have selected. I want to be able to define my own priority and I want to be able to have it select priority based on accounts resetting soonest. Let's say I have an account that is resetting in 12 hours for the whole week and I have 5% usage available. I want to be able to have it automatically select that one so that it finishes the usage on it, so it doesn't get reset and I waste that additional usage. Or, for example, I want to set my own priority and I want it to go account 1, then account 4, then account 2, etc. I want to be able to do that.

Line 31 (per-account thresholds, same message):

> 2. I want to be able to set an individual threshold to switch per account. Maybe I want the limits to be 70% for account A and 90% for account B. I want to be able to do that.

Line 34-35 (applies to every new feature): "all this that you're adding needs to work through the GUI that's currently in place or through the CLI/MCP that you're building." The recorded consequence (line 40): "Every new feature above is reachable from both the GUI and the CLI/MCP."

How priority was built (not a founder quote; `docs/worklog/2026-09-25.md:25`): "Priority order = the accounts list order itself, one order everywhere, set by drag in a new Settings → Accounts tab; 'My order' always picks from the top." Spec: `docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md:98`.

### 1.2 Manual only (0%) (2026-09-26)

His words (line 123):

> okay, another update: I want to make it so the amount that you can set for the limit per account can go below 50%. I want it to be able to go all the way down to 0%, and at 0%, it's disabled from being switched to.  And maybe sometimes you don't want accounts to be used, so you want them disabled. Changing it to 0% would basically do that.  A quick way to do it would be to have a checkbox next to it or something that says "Disable auto switch," but something like that. Come up with a better idea.

What was built and the decisions NOT to do things (lines 130-147):

> - **"Manual only (0%)"** is the named last item of each account's threshold menu. There is no checkbox: one control, one stored value (`switchThreshold == 0`).  (line 130)
> - **1–100%** per account. The stepper stops at 1, so nobody slides into Manual only by accident.  (line 135)
> - Below 20%, the room a target needs is half its threshold, so 5% is not a hidden second "never".  (line 136)
> - **Setting Manual only on the account you are using does not move you off it.** ...  (line 142)
> - **A deliberate manual switch to a Manual only account is not undone after the 5-minute cooldown.** ...  (line 143)
> - **No remembered previous threshold** when choosing Manual only. A hidden value would come back into force later; one click on Default or a number turns it back on.  (line 144)

### 1.3 The 50-100 range of the global default

> - The global default stays 50–100.  (line 138)
> - **The global default cannot go below 50.** A 0% default would make every account Manual only.  (line 147)

Note for the spec: the founder's new example (90% for the 5-hour, 50% for the weekly) lands exactly on the
minimum of this range, so a 50-100 range still admits it. Lowering the floor needs a new decision because of line 147.

### 1.4 The 1.8 "switch at your number" rule (2026-09-29)

His pasted answer (lines 214-216):

> 1. Switch at your number to any account with room [switch-point]
>    ANSWER: Switch at my number to any account with at least a point of room (the recommended one)
>    COMMENT: If I set the limit to 98% or 99% then it needs to switch to any account that is less than that number.

As built (lines 220-224):

> - **The 10-point gap is gone.** When the active account reaches its threshold, a target is any account under ITS OWN threshold by at least a point (`AutoSwitchEngine.hysteresis` 10 → 1). Readings are whole percentages, so "at least a point under 98%" is exactly his "less than that number". For thresholds below 2%, the room is half the threshold, as before.
> - **"That number" is each account's own threshold.** With every account on the 98% default, as on both his Macs, it is his number. An account with its own threshold is judged against it, because auto-switch would leave it again at that number.
> - **It applies to Fable too.** He said "any account that is less than that number", with no exception. ...
> - **Unchanged:** never to a Manual only account, one with no subscription, or one without a saved login or a reading. Every target is re-checked with a fresh reading first. The 5-minute cooldown stays; it and "leave only at or over, go only under" together rule out bouncing between two accounts.

### 1.5 Other decisions the split must not break

- Most room left = room under each account's OWN threshold (build-plans answer, line 77-87; code `AutoSwitchEngine.swift:384-387`, `:409`).
- No-subscription rule (lines 163, 170-171): leaves a dead active account at once, even Manual only, no cooldown; target needs "a point of room under its own threshold".
- Early drain has its own on/off switch, default on, only with "Resets soonest" (line 59, comment at line 54).
- "Setting Manual only on the active account does not move you off it" (142); "No CLI aliases off/never/disable" (145, not quoted above).

---

## 2. The engine today (`PS/Services/AutoSwitchEngine.swift`)

Pure, `import Foundation` only (line 1). The settings enum in the same file reads `UserDefaults.standard`.

### 2.1 Types and constants

| Item | Where | Value / meaning |
|---|---|---|
| `AutoSwitchStrategy` | `:5-15` | `mostRoom` (default), `myOrder`, `resetsSoonest`; raw values are persisted and exposed as setting `autoSwitch.strategy` |
| `Limit` | `:34-39` | `.windows` (5-hour and 7-day together), `.fable` |
| `Trigger` | `:447` | `threshold`, `drainEarly`, `noSubscription`; `exhausted` removed in 1.8 (test `AutoSwitchRulesTests.swift:784`) |
| `fableModelName` | `:42` | `"Fable"` |
| `hysteresis` | `:51` | `1` point (was 10 until 1.8) |
| `drainMinimumRoom` | `:68` | `1.0` |
| `ceiling(threshold:room:)` | `:58-61` | nil if `threshold <= 0` (or NaN); else `threshold - min(room, threshold/2)` |
| setting keys | `:465-470` | `autoSwitchEnabled`, `autoSwitchThreshold`, `autoSwitchOnFable`, `autoSwitchStrategy`, `autoSwitchDrainEarly`, `autoSwitchDrainWithinHours` |
| `thresholdRange` | `:475` | `50.0...100.0`, the global default only |
| `accountThresholdRange` | `:477` | `0.0...100.0` per account; `accountStepperRange` `1.0...100.0` (`:480`); `manualOnlyThreshold = 0.0` (`:483`) |
| `fallbackThreshold` | `:488` | `90.0` |
| `drainHoursRange` / fallback | `:485`, `:490` | `1...72`, `24` |

### 2.2 Readers and helpers

- `bindingUtilization(usage, asOf:, requireKnownWindow:)` `:89-98`: max over `[fiveHour, sevenDay]` of `reading(...)`.
- `reading(window, asOf:, requireKnownWindow:)` `:232-238` (private): nil when no utilization; a window whose
  `resets_at` is in the past is discarded; absent/unparseable `resets_at` keeps the value when lenient and
  discards it when `requireKnownWindow` (strict). Strict is used for the ACTIVE side when its sample was not
  taken by the current cycle (`:323-325`).
- `utilization(usage, limit:)` `:103-116`: `.windows` -> binding max; `.fable` -> the `limits[]` entry whose model
  display name equals "Fable" (case-insensitive, `UsageData.swift:79-81`).
- `eligibleUtilization(usage, limit:, ceiling:)` `:122-133`: util must be `<= ceiling`; for `.fable` the
  windows binding must ALSO be `<= ceiling` (same number).
- `weeklyReset(usage, limit:)` `:144-153`: the 7-day window (or Fable's) reset, only if future-dated and the window has a reading.
- `drainEligibleUtilization` `:165-180`, `eligibleUtilization(... trigger:)` `:197-220` (the one rule shared by ranking and the app's verification).
- `cooldownHolds(lastSwitchAt:, cooldown:, activeUnusable:)` `:226-229`: false when `activeUnusable`; else `now - last < cooldown`.

### 2.3 Settings readers (all `UserDefaults.standard`)

| Reader | Line | Behaviour |
|---|---|---|
| `AutoSwitchFableSetting.isOn` | `:455-457` | default true |
| `AutoSwitchSettings.defaultThreshold` | `:494-497` | `double(forKey:) == 0` means unset -> 90, else `clampedThreshold` (50-100). Cannot tell "unset" from "0" |
| `strategy` | `:500-502` | unknown or unset -> `.mostRoom` |
| `drainEarly` | `:505-507` | default true |
| `drainWithinHours` | `:510-515` | default 24, kept 1-72 |
| `clampedThreshold(_:)` | `:518-521` | NaN -> 90; kept 50-100 |
| `normalizedAccountThreshold(_:)` | `:526-530` | nil/NaN -> nil; else rounded to a whole number in 0-100; never -0 |
| `isManualOnly(own:)` | `:533-535` | normalized own == 0 |
| `effectiveThreshold(own:defaultThreshold:)` | `:540-542` | own (0-100, 0 = Manual only) else clamped default |
| `leaveAtThreshold(rule:defaultThreshold:)` | `:546-548` | rule if > 0 else clamped default (Manual only is left at the default) |
| `explanation(own:defaultThreshold:strategy:drainEarly:)` | `:555-564` | returns `(leaveAt, arriveAt, drainArriveAt?)` or nil for Manual only; `arriveAt = ceiling(rule, hysteresis)`; `drainArriveAt` only when it differs (never since 1.8, both rooms are 1) |

### 2.4 `plan(...)` flow (`:285-347`)

Inputs: `active`, `candidates` (same provider, in accounts-list order), `usageByAccount`, `isSwitchable`,
`isUnusable`, `activeSampledThisCycle`, `threshold: (Account) -> Double` (rule value, 0 = Manual only),
`defaultThreshold: Double`, `hysteresisPct`, `watchFable`, `strategy`, `drainEarly`, `drainWithin`, `asOf`.
Returns `(limit, trigger, targets)?`; nil = stay put.

1. **Rule 0, no subscription** (`:301-309`): if `isUnusable(active)`, rank with `.noSubscription` on `.windows`; empty -> nil.
2. **Threshold** (`:310-334`): `activeRule = threshold(active)`; `activeIsManualOnly = !(activeRule > 0)` (`:314`);
   `activeThreshold = leaveAtThreshold(rule:, default:)` (`:315`). Loops `[.windows, .fable]` (`.fable` only if
   `watchFable`): needs a usable active reading (`requireKnownWindow: !activeSampledThisCycle`, `:323-324`) and
   `activeUtil >= activeThreshold`. Sets `thresholdReached = true` even when no target exists. First limit with
   non-empty targets wins and is returned as `.threshold`.
3. **Early drain** (`:336-346`): only when `!thresholdReached && !activeIsManualOnly && strategy == .resetsSoonest && drainEarly`
   and the active account has a future 7-day reset. Targets must reset sooner than the active account and within `drainWithin`.

### 2.5 Ranking (`rankedTargets`, `:363-437`)

Per candidate: not the active one, not unusable, `eligibleUtilization(... trigger ...)` non-nil (usage first,
because `isSwitchable` reads the Keychain, `:399`), then `isSwitchable`. `Ranked` carries `position` (index in
`candidates`, i.e. the list order), `util`, `room = own - util` (`:409`), `keepsFable` (Fable util `<=
ceiling(own, hysteresisPct)`, `:405-406`), `resetMinute` (whole minutes of the weekly reset, `:407-408`).

| Strategy | Sort key | Lines |
|---|---|---|
| `mostRoom` | `(keepsFable ? 0 : 1, -room, position)` | `:420-422` |
| `myOrder` | none; keeps `candidates` order | `:423-425` |
| `resetsSoonest` | earlier `resetMinute` first, unknown last, ties `(-room, position)` | `:426-434` |

An account with no usable reading is never eligible (`:359-362`, tests `AutoSwitchRulesTests.swift:204-205`, `:223-228`).

---

## 3. The orchestration cycle (`PS/AppState.swift`)

### 3.1 Trigger and state

- `refresh()` `:165-168` = `refreshData()` then `evaluateAutoSwitch()`; evaluation is skipped when no full refresh ran
  (login in progress, refresh already running), so it never acts on a half-written state (comment `:154-164`).
- Timer: `startAutoRefresh(interval: 300)` `:232-239`; user choices 15/30/60/300/600 s (`SettingsView.swift:60-66`,
  `SettingsCatalog.swift`: `refreshInterval` choice list). Non-active accounts are polled round-robin, **one
  other account per cycle** plus the active one (`:80-84`), so candidate readings can be several cycles old.
- Also evaluated after the card's Retry fetch: `retryUsage` `:1553`.
- `autoSwitchEnabled` = `UserDefaults.bool(forKey: "autoSwitchEnabled")` `:118-120` (false when unset).
- `autoSwitchOnFable` `:125-127`; `autoSwitchCooldown = 300` s constant `:130` (not a setting; `SettingsCatalog.swift:7-8` says so).
- `lastAutoSwitchAt` `:132` is set by: login of an existing account `:514`, new account `:556`, re-auth `:635`,
  every `performSwitch` `:832` (manual or automatic, so a manual pick also gets a 5-minute grace), and just
  before an automatic `switchTo` `:1257`.
- `isEvaluatingAutoSwitch` re-entrancy flag `:133`, `:1149`, `:1197-1198`.

### 3.2 `evaluateAutoSwitch()` (`:1148-1274`) step by step

1. Guards (`:1149-1150`): enabled, not already evaluating, not logging in, not switching, an active account exists.
2. **Cooldown** (`:1152-1157`): `AutoSwitchEngine.cooldownHolds(lastSwitchAt:, cooldown: 300, activeUnusable: hasNoSubscription(active))`.
   `hasNoSubscription` = `accountUsageErrors[id]?.isNoSubscription == true` `:1127-1129`; set by a 403 on any usage path (`markNoSubscription`, `:1509-1516`, called from `:1415`).
3. **Candidates and settings** (`:1159-1166`): `accounts.filter { provider == active.provider && id != active.id }` in the
   list order; `activeSampledThisCycle = accountUsageSampledAt[active.id] >= lastCycleStart`; reads `strategy`, `drainEarly`,
   `drainWithin` (hours x 3600), `watchFable`.
4. **`plan(...)`** (`:1167-1181`) with `isSwitchable` (`:1115-1121`: has a saved login backup, not flagged needs-reauth, not an expired-login error),
   `isUnusable`, `threshold: effectiveSwitchThreshold(for:)`, `defaultThreshold: AutoSwitchSettings.defaultThreshold`, `hysteresisPct: AutoSwitchEngine.hysteresis`.
5. **No plan** (`:1181-1188`): if the active account is unusable, a (non-deduped) warning `:1183`; otherwise `noteStayingPut` `:1279-1296`,
   which logs once per change "Active X at N% on windows (its threshold T%), but no other account has room under its own threshold; staying put" and resets `lastStayingPutNote` when nothing is reached (`:1287-1290`).
6. **Log the decision** (`:1200-1208`): "Rule R, strategy S: active X at N% on LIMIT (its threshold T%[, manual only: left at the default]); K candidate(s)". `parse.py` in the 429 investigation filters on `[autoSwitch]` (`docs/investigations/2026-10-16-usage-429/scripts/syn/parse.py:49`).
7. **Verification loop** (`:1210-1268`): **at most one fresh usage request per evaluation** (`freshRequestBudget = 1`, `:1212`).
   For each ranked target in order: a sample taken by THIS cycle (`accountUsageSampledAt >= lastCycleStart`) counts as fresh;
   else, if budget remains, `fetchUsageNow(for:)` (`:1302`, through `readUsage` -> `UsageRequestGuard`); else the target is skipped (`:1226-1228`).
   A fetched reading overwrites `accountUsage`/`accountUsageSampledAt` and clears the account's error (`:1221-1225`).
   Then the **same rule** re-checks it: `AutoSwitchEngine.eligibleUtilization(usage, limit:, trigger:, threshold: effectiveSwitchThreshold(target), hysteresisPct:, activeWeeklyReset:, drainWithin:, watchFable:)` (`:1230-1236`).
   A failure is logged ("failed verification ... its threshold T%"; "is manual only now" if its threshold became 0) and the loop **falls through** to the next target (`:1237-1245`).
8. **Stand-down check** (`:1247-1254`): after the awaits, `!isLoggingIn && !isRefreshing && !isSwitching && activeAccount?.id == active.id`, else "State changed during verification; standing down".
9. **Switch** (`:1256-1267`): log, `lastAutoSwitchAt = Date()`, `await switchTo(target)`; on success sets `lastAutoSwitch = AutoSwitchRecord(from:, to:, limit:, trigger:, at:)` (`:1263`); else warns "did not complete".
   `AutoSwitchRecord` is `AppState.swift:1892-1896`; it reaches subscribers as the `autoSwitched` event with `limit` = `windows|fable` and `trigger` (`PS/Control/RemoteControlService.swift:101-107`).
10. If every target failed verification: per-trigger "staying put" line (`:1269-1273`).

### 3.3 Threshold and order mutators (`AppState.swift:679-736`)

- `effectiveSwitchThreshold(for:)` `:686-690` -> `AutoSwitchSettings.effectiveThreshold(own: stored.switchThreshold, defaultThreshold: AutoSwitchSettings.defaultThreshold)`; reads the account from `accounts` by id so a stale copy still gets the latest value.
- `setSwitchThreshold(_:for:)` `:695-709`: normalizes, no-ops when equal, writes `accounts[index].switchThreshold`, refreshes `activeAccount` if active, `saveAccounts()`. It does not re-evaluate auto-switch (by decision, `...answers.md:142`).
- `setAccountOrder(_ orderedIds:) -> Bool` `:716-723` (refuses unless every account appears exactly once, `AccountOrder.reordered`), `moveAccounts(fromOffsets:toOffset:)` `:726-728`, `applyOrder` `:730-736` (`saveAccounts(refreshWidget: true)` because the widget lists accounts in this order).
- `removeAccount` fallback `:750-757` -> `AccountOrder.fallback(from:)` = first account that is not Manual only, else first.
- Persistence: `accounts` is a `[Account]` JSON blob under UserDefaults key `ai.pixelventures.pixelswitch.accounts` (`:67`), legacy `com.ccswitcher.accounts` (`:71`), `loadAccounts` `:1809-1846`, `saveAccounts` `:1848+`. The array order IS the stored priority.

---

## 4. Where the single threshold and the order are surfaced today

### 4.1 Data model (`PS/Models/Account.swift`)

`Account: Identifiable, Codable, Hashable` with synthesized Codable. `var switchThreshold: Double?` `:45` ("0...100; 0 = Manual only; nil = use the default"), init param `:99`, assignment `:110`.
A nil optional is omitted from the encoded JSON by the synthesized encoder, which a test pins (`AutoSwitchRulesTests.swift:89-91`: a default account saves no `switchThreshold` key, "so an older build reads it unchanged"). Adding another optional field keeps that property and keeps old saved data decoding (`:78-84`).
No `priority` field exists. Order = array order.

### 4.2 Settings window (`PS/Views/`)

- `SettingsView.swift` (window `.frame(width: 520, height: 440)` `:49`): `@AppStorage(thresholdKey) autoSwitchThreshold` `:14`; "Default threshold" row and `Slider(... in: AutoSwitchSettings.thresholdRange, step: 1)` `:88-100`; caption `:97`; Fable toggle `:101`; strategy picker `:102-106` ("Most room left" / "My order" / "Resets soonest ..."); `strategyExplanation` `:326-335` (`.myOrder` text at `:330-331`); drain controls `:110-121`; the long explanation `:122` and the no-subscription sentence `:125`.
- `SettingsAccountsTab.swift`: list in priority order with drag (`ForEach` `:39`, `.onMove` -> `appState.moveAccounts` `:42-44`), drag handle icon `:70`, no rank number; per-row weekly-reset line `:119-131`; manual-only active-row note `:95-100`; `shownDefault` `:138-140`; `isManualOnly` `:142-144`; `nowhereToGo` `:147-150`; threshold menu `thresholdControl` `:157-193` (choices `[10,20,30,40] + 50...100 step 5`, `:23`; "Default (N%)", "Manual only (0%)", label "Switch at N%" `:180`, stepper `:188-191` `accountStepperRange`); help text `thresholdHelp` `:196-207` (calls `explanation`); stepper binding `customThreshold` `:210-215`; caption about order `:56`.
- Popover: `AccountSwitcherView.swift:23` (`ForEach(appState.accounts)`), "Manual only" line `:183`; `UsageDashboardView.swift:72` (list), `:426` ("Manual only" label). Neither shows a rank or a threshold number.
- Widget: `AppState.updateWidgetData` `:1774-1805` + `Shared/WidgetData.swift:8-20`: per-account session and weekly utilization, reset strings; no thresholds, no rank; order = accounts order.

### 4.3 Control API, CLI, MCP

- Protocol structs (`PS/Control/ControlProtocol.swift`): `ThresholdParams {account, threshold: Double?}` `:294-299`; `OrderParams {accounts}` `:301`; `AutoSwitchInfo {enabled, defaultThreshold, onFable, strategy, drainEarly, drainWithinHours}` `:314-321`; `AccountInfo {..., position, threshold, effectiveThreshold, manualOnly, usage}` `:382-401`; `UsageInfo` has `session`/`weekly`/`fable` windows `:371-380`; method names `accounts.setThreshold`, `accounts.setOrder` `:262-263`; event doc `:419-421`.
- `ControlAPI.swift`: protocol `AppControlling` `effectiveSwitchThreshold(for:)` `:22`, `setSwitchThreshold` `:34`, `setAccountOrder` `:35`; `accounts.setThreshold` handler `:149-161` (accepts 0-100 after rounding; error text "A threshold must be 1–100, 0 for manual only, or null to follow the default." `:156`); `accounts.setOrder` `:163-173`; `makeInfo` `:249-260` (`effectiveThreshold = leaveAtThreshold(rule: effectiveSwitchThreshold, default: autoSwitch.defaultThreshold)` `:254-255`); `ControlSnapshots.accountInfo` `:275-289` (`manualOnly: isManualOnly(own:)` `:286`).
- `AppController.swift`: `autoSwitch` info `:30-38` (`defaultThreshold: AutoSwitchSettings.defaultThreshold` `:35`), `effectiveSwitchThreshold` `:69-71`, `setSwitchThreshold` `:163-166`, `setAccountOrder` `:168-170`.
- Settings keys: `SettingsCatalog.swift:13` `autoSwitch.threshold`, `:54` kind `.number(thresholdRange, step: 1)`; `SettingsStore.swift:32-33` (read -> `defaultThreshold`), `:93-95` (write -> `UserDefaults thresholdKey`). `SettingKeyNames.all` in `PixelSwitchCLI/CLIRunner.swift:388-397` mirrors `SettingKey.allCases` and a test pins the match (`ControlTests.swift:742`).
- CLI: `PixelSwitchCLI/CLIParser.swift:34-36` (`.threshold(String, Double?)`, `.order([String])`), parse `:158-171` (`default`, `manual`, number with optional `%`), help `:211`, `:221-223`; `CLIRunner.swift:147-153` (calls), `:242-248` `strategyWords` ("my order" at `:244`), `:256` status line, `:268-292` accounts table with `#` and "Switches at" columns, `:294-296` `switchesAt`, `:298-318` messages (`thresholdSet`, `switched`).
- MCP: `PixelSwitchCLI/MCPServer.swift:57` (`list_accounts` description), `:63` (`switch_account`), `:82-84` (`set_account_threshold`, schema min 0 / max 100), `:85-87` (`set_account_order`), `:88` (`get_settings` key list). A test pins 18 tools and their names (`ControlTests.swift:803`, `:816-817`, `:820`).

### 4.4 Localization

Five tables, each 314 lines (en 316): `PS/{en,de,fr,ja,zh-Hans}.lproj/Localizable.strings`. A test requires identical key sets (`AutoSwitchRulesTests.swift:612-616`) and asserts that specific sentences exist in all five and that retired ones are gone (`:617-640`, `:720-723`, `:786-794`). Keys touched by this work (en line numbers; de/fr/ja/zh-Hans are 2 lower for every line from 183 on):

| Key (shortened) | en line |
|---|---|
| `"Switch at"` and `"When the active account's ... reaches this level ..."` (both unused by any Swift source, grep found none) | 183, 184 |
| `"Default threshold"` | 217 |
| `"My order"` | 220 |
| `"Moves you to the highest account in your order (Settings → Accounts) that has room. ..."` | 223 |
| `"Default (%lld%%)"`, `"Switch at %lld%%"` | 233, 234 |
| `"Manual only (%lld%%)"`, `"Manual only"` | 293, 294 |
| `"Auto-switch moves you off this account at %@, and moves you to it only while it is at %@ or less."` (+ drain variant) | 295, 304 |
| `"Auto-switch never moves you to this account. ... default threshold (%@)."` | 296 |
| `"You're using it now. Auto-switch moves you off it at %@ ..."` | 297 |
| `"Every account except the one you're using is Manual only, ..."` | 298 |
| `"Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, ..."` | 299 |
| `"Auto-switch never moves you to this account. You can still switch to it here."` | 300 |
| `"Any account can have its own threshold in Settings → Accounts, from 1% to 100%, ..."` | 301 |
| `"When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account ..."` | 302 |
| `"If the account you're using has no active subscription, ..."` | 303 |

The widget has its own `PixelSwitchWidget/{lang}.lproj/Localizable.strings`; grep for threshold/order there: not found.

### 4.5 Docs that describe the behaviour

`README.md:73` (feature bullet), `:125-137` (auto-switch rules, strategies, early switching); `docs/SESSION-HANDOVER.md:17-23` (version table), `:81` (engine note); `ARCHITECTURE.md:575` (protocol version 2 since 1.5); `RELEASE_NOTES.md` (per-release); `AGENTS.md` has no threshold text; `docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md:98`, `:137`, `:227`, `:235`; hand-check page generator `scripts/hand-checks/gen-hand-checks.py` -> `docs/decisions/pixelswitch-hand-checks-2026-09-25.html` (Manual only card at ~`:206-215`, 1.8 card `:264-268`).

---

## 5. Change map, feature 1: separate session (5-hour) and weekly (7-day) thresholds

### 5.1 Recommended semantics (derived from section 0 item 3 and the founder's rules in section 1)

Let `ts`, `tw` be an account's effective session and weekly thresholds; `ceil(t) = ceiling(t, room)`.

| Decision point | Today | Split |
|---|---|---|
| Active account leaves (`.windows`) | `max(s,w) >= T` | `s >= ts_active` **or** `w >= tw_active`; each window judged with `reading(...)` on its own (strict expiry per window when not sampled this cycle) |
| Candidate eligible (threshold rule) | `max(s,w) <= ceil(T)` | `s <= ceil(ts_cand)` **and** `w <= ceil(tw_cand)` |
| Candidate `room` (most room left) | `T - max(s,w)` | `min(ts - s, tw - w)` (equal thresholds reduces to today) |
| Fable (active leaves) | `fable >= T_active` | open (D3): recommend `tw` (Fable is a weekly allowance) |
| Fable (candidate) | `fable <= ceil(T)` and windows `<= ceil(T)` | `fable <= ceil(tw)`; windows judged per window as above |
| `keepsFable` | `fable <= ceil(T)` | `fable <= ceil(tw)` |
| Early drain eligibility | both windows `>= 1` point under `T` | each window `>= 1` point under its own threshold |
| No-subscription rule | windows `>= 1` point under `T` | each window `>= 1` point under its own threshold |
| Manual only | `switchThreshold == 0` | stays account-wide (both windows); weekly can never be 0 |
| Active Manual only | left at the default | left at the default `ts`/`tw` (both) |
| `leaveAt` display for Manual only | the default | the default session and weekly |

Anti ping-pong still holds: you leave on "at or over" in either window and enter only on "strictly under" in
both, so the account you just left can never be a valid target while the condition that made you leave persists
(same argument as `...answers.md:224`). Pin it with a split-threshold ping-pong test.

The founder's example (90% session, 50% weekly) means: leave when the 5-hour hits 90% **or** the week hits 50%;
only move to an account at <= 89% session **and** <= 49% weekly. If all other accounts sit above 49% weekly the
engine stays put and `noteStayingPut` logs it. That is correct but should be called out in the spec and in the Settings text.

### 5.2 Open decisions the spec must settle (each with a recommendation)

- **D1 storage of the new defaults.** New keys `autoSwitchSessionThreshold` and `autoSwitchWeeklyThreshold`; when absent, both read the legacy
  `autoSwitchThreshold` (so Beeco's 98% becomes 98%/98% with no write). Read with `object(forKey:)`, not `double(forKey:)`, because the
  current "0 means unset" trick (`AutoSwitchEngine.swift:495-496`) cannot tell unset from 0. Do not mirror back to the legacy key (Sparkle never downgrades).
- **D2 per-account storage.** Keep `switchThreshold` and `0 = Manual only` as is, and add `weeklySwitchThreshold: Double?`. The migration question is what a
  pre-split `switchThreshold = 70` means for the weekly side: it must stay 70 to keep behaviour. Recommend a one-time, idempotent load-time
  migration (copy `switchThreshold` into `weeklySwitchThreshold` for accounts with 1...100 and no weekly, guarded by a UserDefaults flag such as
  `ai.pixelventures.pixelswitch.splitThresholdsMigrated`), after which `nil` means "default" for each window independently. Alternative (no
  migration): weekly follows `switchThreshold` when set; rejected because it cannot express "session custom, weekly default". Meaning of `switchThreshold` after the split: the **session** threshold (and 0 = Manual only for the account).
- **D3 Fable's threshold.** Recommend the weekly threshold. Must be stated in the Settings sentence because the 1.8 rule says Fable uses "that number" (`...answers.md:222`).
- **D4 `Limit` enum.** Keep `.windows`/`.fable` so the `autoSwitched` event's `limit` value (`RemoteControlService.swift:105`) and `AutoSwitchRecord` do not change shape; add a separate `Window { session, weekly }` for the readings and an optional `firedWindows` for logs. Splitting `.windows` into `.session`/`.weekly` would change a protocol-visible string and `weeklyReset(limit: .windows)` (used at `AppState.swift:1195`, `SettingsAccountsTab.swift:121`).
- **D5 engine API.** Introduce `struct AutoSwitchThresholds { session: Double; weekly: Double }` (0 = Manual only on both) and change `threshold: (Account) -> Double` to `(Account) -> AutoSwitchThresholds` and `defaultThreshold: Double` to `AutoSwitchThresholds`. Keep a `Double` convenience initializer and a `Double`-based `plan`/`eligibleUtilization` overload for the existing tests (they call these ~60 times).
- **D6 protocol.** Bump `ControlProtocol.version` to 3 (shape change). New optional/defaulted fields so existing memberwise inits in tests still compile (use `var x: T? = nil`): `AccountInfo.weeklyThreshold`, `effectiveSessionThreshold`, `effectiveWeeklyThreshold`; `AutoSwitchInfo.defaultSessionThreshold`, `defaultWeeklyThreshold`; `ThresholdParams.window` (`"session"|"weekly"|"both"`, default both) so old callers keep meaning "both". Keep `AccountInfo.effectiveThreshold` and `AutoSwitchInfo.defaultThreshold` populated (session value) for old readers.
- **D7 global range.** Keep 50-100 for both defaults (founder, `...answers.md:138`, `:147`); 50 admits his example.
- **D8 UI shape.** The Settings window is fixed 520 x 440 (`SettingsView.swift:49`) and each Accounts row is already full (label, reset line, menu, stepper). Two threshold controls per row need a layout decision (two compact menus, or one menu with a disclosure). The General tab is a scrolling grouped Form (`:164`), so a second slider is cheap.

### 5.3 Site-by-site

Engine, `PS/Services/AutoSwitchEngine.swift`:

| Lines | Change |
|---|---|
| `:1-30` header, `:44-51`, `:277-278` | doc comments: describe two windows; fix the stale "(e.g. 10)" |
| `:58-61` `ceiling` | unchanged (pure scalar); add a thin per-window helper |
| `:89-98` `bindingUtilization` | keep (used for display/logs, `:1242`, drain, Fable's window check); add `utilization(usage, window:, asOf:, requireKnownWindow:)` exposing the private `reading` (`:232-238`) per window |
| `:122-133` `eligibleUtilization(ceiling:)` | takes `(sessionCeiling, weeklyCeiling)`; Fable uses weekly ceiling (D3); windows checked per window |
| `:165-180` `drainEligibleUtilization` | per-window ceilings with `drainMinimumRoom` |
| `:197-220` `eligibleUtilization(trigger:)` | `threshold: Double` -> `AutoSwitchThresholds`; Manual-only test is `thresholds.isManualOnly`; this is the single rule app verification shares, so changing it changes ranking and verification together |
| `:285-347` `plan` | `threshold`/`defaultThreshold` types; active check per window (`:317-326`); Manual-only active (`:314-315`) leaves at default thresholds |
| `:363-437` `rankedTargets` | `own` (`:398`) becomes thresholds; `room` (`:409`) = min over windows; `keepsFable` (`:405-406`) uses weekly ceiling |
| `:464-565` `AutoSwitchSettings` | new keys, readers (`defaultSessionThreshold`, `defaultWeeklyThreshold`, legacy fallback), `effectiveThresholds(own:ownWeekly:default:)`, `leaveAtThresholds`, `explanation` returning per-window numbers; keep `clampedThreshold`, `normalizedAccountThreshold`, `isManualOnly` |

AppState, `PS/AppState.swift`:

| Lines | Change |
|---|---|
| `:686-690` `effectiveSwitchThreshold` | return thresholds (both windows) |
| `:695-709` `setSwitchThreshold` | add a window parameter (or a second setter for weekly); log text `:707-708` |
| `:1167-1181` plan call; `:1201-1203`; `:1207`; `:1231-1235`; `:1243`; `:1280-1281`; `:1291` | pass thresholds; log lines must name the window that fired and both numbers (keep the current wording when session == weekly so log parsers such as `parse.py:49` and the founder's habit of reading `[autoSwitch]` lines still work) |
| `:1200` `activeUtil` | log the fired window's reading, not the max |
| `:1892-1896` `AutoSwitchRecord` | optionally add `window`; the `autoSwitched` event stays `limit`/`trigger` plus an optional `window` |
| `:1809-1846` `loadAccounts` | place for the D2 one-time migration (before the first `saveAccounts`) |

Model: `PS/Models/Account.swift:45`, `:99`, `:110` add `weeklySwitchThreshold: Double?`; `PS/Models/AccountOrder.swift:41` (`fallback`) unaffected unless Manual-only representation changes.

Settings window: `SettingsView.swift:14`, `:88-100` (two sliders or one slider per window), `:97` and `:122` captions; `SettingsAccountsTab.swift:15` (`@AppStorage`), `:23`, `:95-100`, `:138-140`, `:157-215` (two controls or a disclosure), `:196-207` help text with both numbers.

Popover / usage list: `AccountSwitcherView.swift:183`, `UsageDashboardView.swift:426`: nothing to change for Manual only; optionally surface "5h N% / wk N%" thresholds (not required by the request).

Control API / CLI / MCP: `ControlProtocol.swift:13`, `:294-299`, `:314-321`, `:382-401`; `ControlAPI.swift:22`, `:34`, `:149-161`, `:249-260`, `:275-289`; `AppController.swift:30-38`, `:69-71`, `:163-166`; `SettingsCatalog.swift:13`, `:54` plus two new keys `autoSwitch.sessionThreshold` / `autoSwitch.weeklyThreshold` (keep `autoSwitch.threshold` as the legacy "both" setter); `SettingsStore.swift:32-33`, `:93-95`; `PixelSwitchCLI/CLIParser.swift:34-36`, `:158-168`, `:221-222` (suggested syntax `accounts threshold <account> [session|weekly] <1-100|default|manual>`), `CLIRunner.swift:147-149`, `:256`, `:268-296`, `:298-318`, `:388-397`; `MCPServer.swift:57`, `:82-84`, `:88`.

Localization: add/replace sentences in all five tables (en `:183-184` are dead keys that could be dropped), keeping key sets identical and updating the `added`/`retired` lists at `AutoSwitchRulesTests.swift:617-636` and `:786-794`. Sentences that name "its threshold" or "this level": en `:295`, `:296`, `:297`, `:301`, `:302`, `:304`.

Docs: `README.md:73`, `:125-137`; `docs/SESSION-HANDOVER.md:17-23`, `:81`; `RELEASE_NOTES.md`; `ARCHITECTURE.md:575` (protocol number); `docs/decisions/2026-09-24-remote-control-answers.md` (append the new founder words verbatim, per its own convention).

---

## 6. Change map, feature 2: explicit, visible account priority

### 6.1 What exists

- One order everywhere: `appState.accounts` array order (popover Accounts tab `AccountSwitcherView.swift:23`, Usage tab `UsageDashboardView.swift:72`, Settings -> Accounts `SettingsAccountsTab.swift:39`, widget via `updateWidgetData`, CLI `#`, control API `position`).
- Mutators: GUI drag `moveAccounts` (`AppState.swift:726`) from `SettingsAccountsTab.swift:42-44`; API `accounts.setOrder` -> `AppController.setAccountOrder:168` -> `AppState.setAccountOrder:716`; CLI `accounts order <a> <b> ...` (`CLIParser.swift:169-171`); MCP `set_account_order` (`MCPServer.swift:85-87`). New accounts are appended at the end (`AppState.swift:552`).
- Effect on selection: only `AutoSwitchStrategy.myOrder` ("My order") uses it as the ranking: always from the top, never "the next after the active one" (`AutoSwitchEngine.swift:423-425`; tests `AutoSwitchRulesTests.swift:254-260`). For `mostRoom` and `resetsSoonest` it is the last tie-break (`:421`, `:432`). Removal falls back to the first non-Manual-only account in the order (`AccountOrder.fallback`, `AccountOrder.swift:40-42`, `AppState.swift:750-757`).
- Visibility gaps: no number in any GUI row; Settings explains it only in the caption `SettingsAccountsTab.swift:56` and the strategy line `SettingsView.swift:330-331`; the CLI/API expose it.

### 6.2 Two ways to make it explicit (spec picks one)

**P1, recommended: keep the list order as the priority and make it visible and named.** No data migration, no protocol shape change beyond what D6 already does.
- `SettingsAccountsTab.swift:70` (drag handle row): add a rank badge "1, 2, 3 ..." beside/instead of the handle; `:56` caption says what the rank does and that it only decides the pick under the priority strategy (or, with the other strategies, only ties).
- `AccountSwitcherView.swift` (row near `:183`) and `UsageDashboardView.swift` (card near `:426`): optional small rank number; the address already truncates on the 360 pt panel (`...answers.md:146`), so use the existing quiet third line rather than a badge beside the name.
- `SettingsView.swift:104`, `:330-331` and strings `"My order"` (en `:220`, de/fr/ja/zh-Hans `:218`) and `:223/:221`: relabel to "Priority order" in display only. **Do not rename the raw value `myOrder`**: it is persisted in `autoSwitchStrategy` (`AutoSwitchEngine.swift:468`, `:500-502`), accepted by `autoSwitch.strategy` (`SettingsCatalog.swift:55`), printed by `CLIRunner.swift:244`, listed in `MCPServer.swift:88`, pinned by tests (`AutoSwitchRulesTests.swift:102`). The README string `README.md:133` also says "My order".
- CLI: `CLIRunner.swift:268-292` already prints `#` (`:270`, `:276`); add nothing, or print the strategy next to it. API: `AccountInfo.position` already is the rank.
- Possible small addition: a "move to top" / up-down affordance next to the drag handle, calling `moveAccounts`.

**P2: a separate numeric priority field.** `Account.priority: Int?` (new Codable optional, same compatibility story as `switchThreshold`), ranking sorts by `(priority ?? .max, position)`, `AccountOrder`, `reordered/moving/fallback` (`AccountOrder.swift:12-42`), `setAccountOrder`, `accounts.setOrder` and the CLI need a second meaning ("order" vs "priority"), and the two could disagree on screen. More surface, more tests, no extra capability beyond P1 unless priority must differ from list order. Not recommended.

**Open question for the spec (D9):** the founder's new wording is "assign priority for accounts to cycle through". "Cycle" may mean top-down every time (today's `myOrder`, test `AutoSwitchRulesTests.swift:256-257`) or round-robin from the active account; his 2026-09-25 words ("account 1, then account 4, then account 2, etc.", section 1.1) match top-down. Also decide whether priority should rank under all strategies (e.g. a "priority first, then most room" mode) or stay a strategy of its own; today the three strategies are mutually exclusive (`AutoSwitchStrategy`, `:5-15`).

### 6.3 Sites for either option

`AccountOrder.swift:3-6` (doc comment), `:12-20`, `:26-36`, `:40-42`; `AppState.swift:711-736`; `AppController.swift:168-170`; `ControlAPI.swift:35`, `:163-173`, `:240`, `:246` (position source); `ControlProtocol.swift:301`, `:389-390`; `CLIParser.swift:36`, `:169-171`, `:211`, `:223`; `CLIRunner.swift:151-153`, `:270`, `:276`; `MCPServer.swift:57`, `:85-87`; tests `AutoSwitchRulesTests.swift:385-409` (order), `:453-455` (fallback), `:254-260` (my order); `ControlTests.swift:131`, `:262-267`, `:306`, `:378-388`.

---

## 7. Symbol index (every site for the requested greps)

Counts exclude `graphify-out/`, `docs/worklog/`. "T:" = test files. Line numbers verified by grep on 2026-10-24.

**`switchThreshold`**: `PS/Models/Account.swift:45,99,110`; `PS/Models/AccountOrder.swift:41`; `PS/AppState.swift:688,701,702`; `PS/Views/SettingsAccountsTab.swift:143,158`; `PS/Views/AccountSwitcherView.swift:183`; `PS/Views/UsageDashboardView.swift:426`; `PS/Control/ControlAPI.swift:284,286`. T: `AutoSwitchRulesTests.swift` 43,81,82,86,88,90,91,159,161,162,164,284,285,299,432,434,435,437,439,440,449,450,498,499,563,576,586-589,651,652,755; `ControlTests.swift` 49,216,260.

**`thresholdKey`**: `AutoSwitchEngine.swift:466,495`; `SettingsView.swift:14`; `SettingsAccountsTab.swift:15`; `SettingsStore.swift:95`. T: `AutoSwitchRulesTests.swift` 94,106,119,121,123,444,445,447.

**`defaultThreshold`**: `AutoSwitchEngine.swift:274,275,293,315,494,540,541,546,547,555,557`; `AppState.swift:689,1175,1202,1281`; `SettingsAccountsTab.swift:15,139,201`; `ControlProtocol.swift:316`; `SettingsStore.swift:33`; `ControlAPI.swift:255`; `AppController.swift:35`; `PixelSwitchCLI/CLIRunner.swift:256`. T: `AutoSwitchRulesTests.swift` 36,43,44,115,120,122,124,150-152,424-426,439,446,538,539,745-781; `ControlTests.swift` 198,216,728,752; `Tests/UnitTests/main.swift:318`.

**`effectiveThreshold`** (field and function): `AutoSwitchEngine.swift:272,540,557`; `AppState.swift:688`; `ControlProtocol.swift:396`; `ControlAPI.swift:254,275,285`; `MCPServer.swift:57`; `CLIRunner.swift:294,303,308,309,316`. T: `ControlTests.swift` 85,309,353,357,374,697,699,709,711; `AutoSwitchRulesTests.swift` 43,150-152,424-426,439.

**`effectiveSwitchThreshold`**: `AppState.swift:686,1174,1201,1231,1280`; `AppController.swift:69,70`; `ControlAPI.swift:22,254`. T: `ControlTests.swift:216`.

**`leaveAtThreshold`**: `AutoSwitchEngine.swift:315,539,546`; `AppState.swift:1202,1280`; `ControlAPI.swift:254`. T: `AutoSwitchRulesTests.swift:428`.

**`explanation(`**: `AutoSwitchEngine.swift:555`; `SettingsAccountsTab.swift:201`. T: `AutoSwitchRulesTests.swift:459` (`let e = AutoSwitchSettings.explanation`, called at 460-468; the grep `explanation(` finds no test line because it is aliased).

**`ceiling(`**: `AutoSwitchEngine.swift:58,173,210,217,406,558,562`; `docs/SESSION-HANDOVER.md:81`. T: `AutoSwitchRulesTests.swift` 473-492 (`let ceiling = AutoSwitchEngine.ceiling`, calls 475-492).

**`manualOnly`** (field, constant, doc): `AutoSwitchEngine.swift:59,314,483,534,547`; `AppState.swift:707,1203,1237`; `SettingsAccountsTab.swift:142,165,166,177,188,197`; `ControlProtocol.swift:395,399`; `ControlAPI.swift:286`; `MCPServer.swift:57`; `CLIRunner.swift:286,294,300,315`; `ARCHITECTURE.md:575`; `docs/SESSION-HANDOVER.md:20`. T: `ControlTests.swift` 85-87,353,358,362,373-375,697,699,709,711,811.

**`isManualOnly`**: `AutoSwitchEngine.swift:533`; `AccountOrder.swift:41`; `AccountSwitcherView.swift:183`; `UsageDashboardView.swift:426`; `SettingsAccountsTab.swift:143`; `ControlAPI.swift:286`.

**`myOrder`**: `AutoSwitchEngine.swift:11,260,423`; `SettingsView.swift:104,330`; `CLIRunner.swift:244`; `MCPServer.swift:88`. T: `AutoSwitchRulesTests.swift` 102,238,256-260,265,326,466,504,653,695.

**`My order`** (user-visible string): `AppState.swift:712,1160` (comments); `AccountOrder.swift:3`; `SettingsView.swift:104`; `SettingsAccountsTab.swift:6,56`; `MCPServer.swift:85`; `README.md:133`; strings en `:220,:299`, de/fr/ja/zh-Hans `:218,:297`. T: `AutoSwitchRulesTests.swift:254,625,633`.

**`setAccountOrder`**: `AppState.swift:716,718`; `ControlAPI.swift:35,170`; `AppController.swift:168,169`. T: `ControlTests.swift:262`.

**`moveAccounts`**: `AppState.swift:726`; `SettingsAccountsTab.swift:43`. T: none.

**Other threshold-adjacent symbols touched by feature 1**: `normalizedAccountThreshold` (`AutoSwitchEngine.swift:526`, `AppState.swift:700`, `SettingsAccountsTab.swift:158`); `clampedThreshold` (`:518`, `SettingsAccountsTab.swift:139`); `accountThresholdRange` (`:477`, `ControlAPI.swift:155`); `accountStepperRange` (`:480`, `SettingsAccountsTab.swift:189`); `thresholdRange` (`:475`, `SettingsView.swift:96`, `SettingsCatalog.swift:54`); `fallbackThreshold` (`:488`, `SettingsView.swift:14`, `SettingsAccountsTab.swift:15,139`); `bindingUtilization` (`AppState.swift:1242`); `AutoSwitchEngine.hysteresis` (`AppState.swift:1176,1234`).

---

## 8. Test impact (`Tests/run-unit-tests.sh`, run in CI at `.github/workflows/build.yml:42`)

The harness compiles the engine, `Account`, `AccountOrder`, `UsageData`, the control layer and the CLI sources together (file list in `Tests/run-unit-tests.sh`), so signature changes break compilation in these places at once:

| Test file / function | Lines | Impact |
|---|---|---|
| `Rules.plan` helper | `AutoSwitchRulesTests.swift:35-47` | single choke point; `threshold: { effectiveThreshold(own:default:) }` and `defaultThreshold:`; if D5 keeps a `Double` overload this compiles unchanged |
| `main.swift` plan helper | `Tests/UnitTests/main.swift:313-319` | `threshold: { _ in 98 }, defaultThreshold: 90` |
| `autoSwitchSettingsTests` | `:77-154` | key names (`:93-99`), defaults (`:115-124`), clamps (`:142-153`); add tests for new keys, legacy fallback, unset vs 0 |
| `perAccountThresholdTests` | `:158-209` | regression net for per-account behaviour |
| `strategyTests` | `:213-292` | includes my-order ranking `:254-260`, own-room ranking `:281-291` |
| `earlyDrainTests` | `:296-381` | literal `hysteresisPct: 10` at `:369-376`; fix to the constant (item 5 in section 0) |
| `accountOrderTests` | `:385-409` | order operations; unaffected by P1 |
| `manualOnlySettingsTests` | `:413-456` | `AccountOrder.fallback` `:453-455` |
| `tooltipNumbersTests` | `:458-470` | `explanation` tuple shape changes with per-window numbers |
| `ceilingTests` | `:472-494` | unaffected if `ceiling` stays scalar |
| `manualOnlyEngineTests` | `:496-559` | literal 10 at `:522-525` |
| `lowThresholdTests` | `:561-608` | |
| `thresholdStringsTests` | `:611-641` | `added`/`retired` sentence lists must be edited with the strings |
| `noSubscriptionTests` | `:647-724` | literal 10 at `:703`; sentence key `:720` |
| `switchAtThresholdTests` | `:731-795` | Beeco 98/99 cases; sentence key `:786` |
| `ControlTests` | `:84-87` (`AccountInfo` memberwise + `version == 2`), `:154-156`, `:185` (error text "autoSwitch.threshold must be 50–100, whole numbers."), `:198`, `:216`, `:260`, `:301`, `:309`, `:353-375`, `:696-711`, `:726-728`, `:742`, `:752`, `:803-817` | memberwise inits break unless new fields are defaulted; protocol bump touches `:86`; `SettingKey` additions touch `:178`, `:398`, `:742` |
| `Tests/integration/remote-control.sh:47` | | asserts `protocolVersion` = 2 against a real app |

167 `check(` calls in `AutoSwitchRulesTests.swift`, 186 in `ControlTests.swift`.

Tests to add for the split: equal-threshold equivalence sweep (every old case reproduces under `ts == tw`), the founder example (90/50), each window triggers independently, a candidate failing only one window, per-window strict expiry for the active account, Fable follows the weekly threshold, drain and no-subscription per window, `room = min(...)`, ping-pong with split thresholds, Manual only stays account-wide, migration (legacy `switchThreshold` N maps to N/N, legacy key maps to default/default), decode of old saved accounts, encode omits nil weekly key, protocol v3 round trip, CLI parse of `session|weekly|both`.

---

## 9. What this means for the Linux build (auto-switch scope only)

- Foundation-only and therefore portable in principle: `AutoSwitchEngine.swift`, `Account.swift`, `AccountOrder.swift`, `UsageData.swift`, `ControlProtocol.swift`, `ControlAPI.swift`, `SettingsCatalog.swift`, `AccountResolver.swift`, `CLIParser.swift`, `CLIRunner.swift`, `MCPServer.swift` (each `import Foundation` only; verified by grep). Two harness files are not: `PixelSwitchCLI/ControlClient.swift` imports `Darwin`, and `PS/Services/UsageRequestGuard.swift` imports `CryptoKit`; both are in `Tests/run-unit-tests.sh`'s file list. Not examined further here (other subsystems).
- The engine's settings readers use `UserDefaults.standard` (`AutoSwitchEngine.swift:455-457`, `:494-515`) and `Account` persistence is a UserDefaults JSON blob (`AppState.swift:67`, `:1848+`). `UserDefaults` exists in swift-corelibs-foundation but its storage location and `@AppStorage` change-notification behaviour are Apple-specific; this recon did not test it on Linux. Recommendation: make the engine settings a plain value (`AutoSwitchConfig { enabled, defaultThresholds, onFable, strategy, drainEarly, drainWithinHours }`) passed into `plan`/`evaluate`, with a macOS adapter reading UserDefaults and a Linux adapter reading its own config file. That also removes global state from the tests.
- All orchestration logic that matters for "exactly where it matters" sits in `AppState.evaluateAutoSwitch` (`:1148-1274`), which is `@MainActor`, Combine/SwiftUI-bound (`@Published`), and calls `keychain`, `claudeService`, `UsageRequestGuard`. A Linux port should lift the pure parts of that function (cooldown, candidate filter by provider, one-fresh-fetch budget, verification with the same rule, stand-down check, logging) into a UI-free coordinator and keep the engine untouched. Do the engine change first so both platforms share it.
- Cooldown 300 s, 1 fresh verification fetch per evaluation, round-robin of one non-active account per cycle, refresh interval default 300 s are behavioural constants a Linux build must reproduce (`AppState.swift:130`, `:1212`, `:80-84`, `:232`).

---

## 10. Risks, ambiguities, not found

- **Not found:** any UI or API that sets session and weekly separately; any per-account priority field; any rank number in the GUI; any threshold text in the widget strings; any consumer of `Account.switchThreshold` outside the files listed in section 7; any script that parses the "(its threshold N%)" wording other than `parse.py:49`, which matches only the `[autoSwitch]` tag.
- **Behaviour change the founder may not expect:** with a weekly threshold lower than the session one, an account can be left while its 5-hour window is nearly empty. That is the point of the feature, but "Most room left" and `noteStayingPut` text must say which window.
- **Active-account strictness per window:** when the active account's sample was not taken this cycle, each window must pass `requireKnownWindow` separately (a retained high 7-day reading with no `resets_at` must not trigger a switch, `AutoSwitchEngine.swift:76-86`, `:323-325`). Easy to lose if the two windows are folded into one `max` too early.
- **Verification and ranking must keep sharing one rule** (`AutoSwitchEngine.swift:182-186`); a per-window rule changed in `rankedTargets` only would let verification and ranking drift.
- **`explanation(...)` and `leaveAtThreshold` feed user-facing numbers** (`SettingsAccountsTab.swift:201`, `ControlAPI.swift:254`); with two windows each returns two numbers, and the help string in all five languages needs new sentences with `%@` placeholders.
- **The CLI binary and app protocol versions must move together** (item 9); a CLI at version 2 talking to a v3 app prints the "Quit and reopen PixelSwitch" mismatch (`CLIRunner.swift:64-66`). Fine inside one bundle, but the Linux CLI must ship with the same number.
- **Interplay with early drain and Resets soonest** (`:336-346`): drain eligibility uses the same thresholds; confirm in the spec that a split weekly threshold of 50% does not make "use the quota that is about to reset" (the founder's 5% example, section 1.1) harder; with `tw = 50` an account at 95% weekly is not a drain target.
- **Open founder-level ambiguities** are D1-D9 above; none is blocked by a missing fact in the repo.
