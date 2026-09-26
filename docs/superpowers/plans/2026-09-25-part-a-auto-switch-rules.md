# Part A: Auto-Switch Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every account its own switch threshold, let the user choose how the next account is picked (most room left, their own order, or resets soonest with optional early switching), and add a Settings → Accounts tab that sets the order and the per-account thresholds.

**Architecture:** All decision logic stays in the pure `AutoSwitchEngine` (unit-tested in the swiftc harness). `plan(...)` takes a per-account `threshold` closure, a `strategy`, and the early-drain settings, and returns the rule that fired (`Trigger`). `AppState` passes the settings in, verifies the chosen account with the same shared eligibility rule the engine ranked with, and publishes `lastAutoSwitch`. The accounts list's own order is the priority order; a pure `AccountOrder` helper reorders it and `AppState` saves it. Two SwiftUI changes: the General tab's auto-switch section and a new `SettingsAccountsTab`.

**Tech Stack:** Swift 6, SwiftUI + AppKit, macOS 14+, XcodeGen, the repo's own swiftc unit harness (`Tests/run-unit-tests.sh`, not XCTest), `.lproj/Localizable.strings` in five languages.

**Spec:** `docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md` (sections 1–3, 6, 8 and 9). Founder's verbatim answers: `docs/decisions/2026-09-24-remote-control-answers.md`. Read both before Task 1. Part B (sign-in links) and Part C (remote control) are built after this part, and Part C calls the names in section 9 exactly.

**How this plan was checked before it was handed over:** every code block below was applied, in task order, to a scratch copy of the repository at commit `aa20916`. After each task the unit harness and `scripts/typecheck.sh` were run, and each "expected" output below is what they printed. The final state passed 205 unit tests with 0 failures, type-checked the app (50 files), and `plutil -lint` passed all five string files with identical keys.

## Global Constraints

- Swift 6 language mode everywhere: the harness compiles with `swiftc -swift-version 6`, and the app with `SWIFT_VERSION: "6.0"` and `SWIFT_STRICT_CONCURRENCY: targeted` (`project.yml`).
- Deployment target macOS 14.0. Use no API newer than macOS 14.
- `project.yml` is the only source of truth for the Xcode project. Never edit `.pbxproj` or `Info.plist`. `PixelSwitch.xcodeproj` is git-ignored and disposable. New files under `PixelSwitch/` are picked up because the target's source is the whole folder; run `xcodegen generate` after adding one.
- No new package dependencies.
- This Mac has no Xcode, only the Command Line Tools. **Never run `xcodebuild`.** The app check is `bash scripts/typecheck.sh`, which prints `app: type-check OK (N files)`. The full build, signing and notarization happen in CI, once, in Task 9.
- Unit tests: `bash Tests/run-unit-tests.sh`, which prints `N passed, M failed` last. Baseline before Task 1: `118 passed, 0 failed`. Only pure files can be compiled into it: no `AppState`, no SwiftUI views, no `FileLog`, no WidgetKit.
- Test convention (spec section 9): Part A's tests live in `Tests/UnitTests/AutoSwitchRulesTests.swift`, which defines `@MainActor func runAutoSwitchRulesTests()` and uses the global `check(_:_:_:)` from `main.swift`. `main.swift` gets exactly one call line, `runAutoSwitchRulesTests()`, immediately before `print("\n\(passed) passed, \(failed) failed")`. Every new source or test file compiled by the harness is added to the `swiftc` list in `Tests/run-unit-tests.sh`.
- `main.swift` is top-level code, so its globals (`passed`, `failed`, `service`, `account`, `fakeCredential`, `hex`, `parse`, `FakeSecurity`) are visible in the new test file. Do not declare file-scope names that clash with them. Everything in the new file is `private` or nested.
- Names and types fixed by spec section 9, to be produced exactly:
  - `Account.switchThreshold: Double?` (50...100; nil = use the default).
  - `enum AutoSwitchStrategy: String, CaseIterable, Codable, Sendable { case mostRoom, myOrder, resetsSoonest }`.
  - `extension AutoSwitchEngine { enum Trigger: String, Sendable { case threshold, drainEarly } }`.
  - `AutoSwitchEngine.plan(...)` returns `(limit: Limit, trigger: Trigger, targets: [Account])?`.
  - `enum AutoSwitchSettings` in `AutoSwitchEngine.swift`, with the keys as `static let` constants and the readers `strategy`, `drainEarly`, `drainWithinHours`, `defaultThreshold`, plus `thresholdRange = 50.0...100.0` and `drainHoursRange = 1.0...72.0`. `AutoSwitchFableSetting` stays.
  - On `AppState` (`@MainActor`, internal): `func effectiveSwitchThreshold(for account: Account) -> Double`, `func setSwitchThreshold(_ threshold: Double?, for account: Account)`, `@discardableResult func setAccountOrder(_ orderedIds: [UUID]) -> Bool`, `func moveAccounts(fromOffsets source: IndexSet, toOffset destination: Int)`, `@Published private(set) var lastAutoSwitch: AutoSwitchRecord?`.
  - `struct AutoSwitchRecord: Equatable, Sendable { let from: UUID; let to: UUID; let limit: AutoSwitchEngine.Limit; let trigger: AutoSwitchEngine.Trigger; let at: Date }`, a top-level type in `AppState.swift`.
  - `struct SettingsAccountsTab: View` in `PixelSwitch/Views/SettingsAccountsTab.swift`, added to `SettingsView`'s `TabView` right after General.
- UserDefaults keys, copied verbatim from spec section 9:

  | Key | Type | Default | Range |
  |---|---|---|---|
  | `autoSwitchEnabled` | Bool | false | existing |
  | `autoSwitchThreshold` | Double | 90 | widened to 50–100 |
  | `autoSwitchOnFable` | Bool | true | existing |
  | `autoSwitchStrategy` | String | `mostRoom` | an `AutoSwitchStrategy` rawValue |
  | `autoSwitchDrainEarly` | Bool | true | |
  | `autoSwitchDrainWithinHours` | Double | 24 | 1–72 |

- The hysteresis (10 points) and the cooldown (300 s) stay fixed and stay in `AppState`, as today.
- Localization: the app ships `en`, `zh-Hans`, `ja`, `de`, `fr`, each as `PixelSwitch/<lang>.lproj/Localizable.strings`, and all five files carry the same keys today. SwiftUI `Text("literal")` looks the literal up as a key; `String(localized:bundle: L10n.bundle)` does the same for strings built in code. Every new key goes into all five files. A key missing from a language shows the English key text, so a missed entry degrades to English and never breaks.
- Every commit message ends with a blank line followed by `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Work on the branch `part-a-auto-switch-rules`, created from `main` in Task 1 (the spec builds each part on its own branch). If you use a worktree, create it inside the repo at `.claude/worktrees/part-a` (the founder's rule), never as a sibling folder.

## Decisions this plan makes that the spec left open

Each is pinned by a test so a later change cannot quietly undo it.

1. **A threshold reached with nowhere to go still counts as reached.** Early draining runs only when the active account has not reached its threshold on any watched limit. Near the limit the 10-point hysteresis stays in charge, as today. (Task 4 test: "drain never fires once the active account has reached its threshold".)
2. **A drain target must also have room on Fable when Fable switching is on.** The spec lists the 5-hour and weekly windows. Without the Fable check, the Fable trigger would move the user off the drained account and the drain would bring them back after every 5-minute cooldown. (Task 4.)
3. **Drain room is `drainMinimumRoom = 1.0` point below the candidate's own threshold, on every watched limit.** It is not the 10-point hysteresis, because the founder's example is an account with 5% left.
4. **"Resets soonest" ties are compared in whole minutes.** The API stamps each reading's `resets_at` with the fetch's own microseconds, so two accounts on the same weekly schedule never compare exactly equal. Without this, "ties go to most room" could never happen with real data. (Task 3.)
5. **A weekly reset in the past means "unknown".** A retained sample from a week that has ended says nothing about when the next week ends. It ranks last under Resets soonest, and it is never a drain target or a drain source. (Tasks 3 and 4.)
6. **"Most room left" still ranks by the lowest raw utilization**, as the spec says ("unchanged"), not by headroom under each account's own threshold. The only change is that equal utilization now keeps the list's order, so the ranking is deterministic. See "Follow-ups" at the end.
7. **The engine's defaults are today's behaviour** (`strategy: .mostRoom`, `drainEarly: false`), so a caller that passes neither gets the old engine. The app always passes the saved settings, whose `drainEarly` default is `true` (spec section 9).
8. **`drainWithin` is in seconds (`TimeInterval`)** in the engine; the setting is in hours. `AppState` multiplies by 3600.
9. **One shared rule for ranking and verifying.** A new overload, `AutoSwitchEngine.eligibleUtilization(_:limit:trigger:threshold:hysteresisPct:activeWeeklyReset:drainWithin:watchFable:asOf:)`, is used by the engine to rank and by `AppState` to verify the fresh reading, the same way `eligibleUtilization(_:limit:ceiling:asOf:)` works today. That older function stays unchanged.
10. **The key constants in `AutoSwitchSettings` are named** `enabledKey`, `thresholdKey`, `onFableKey`, `strategyKey`, `drainEarlyKey`, `drainWithinHoursKey`; the built-in defaults are `fallbackThreshold` (90) and `fallbackDrainWithinHours` (24); the helpers are `clampedThreshold(_:)`, `normalizedAccountThreshold(_:)` and `effectiveThreshold(own:defaultThreshold:)`. Section 9 fixed the readers but not these names. Part C should use them.
11. **`setAccountOrder` returns `true` for the order the list already has.** It is a permutation of the current ids, so it is valid. It returns `false` for anything that leaves an account out, repeats one or names one that is gone.
12. **The per-account threshold control** is a menu (Default, then 50–100 in 5-point steps), plus a 1-point stepper beside it while a custom value is set, so every whole number from 50 to 100 is reachable. A value set elsewhere (the CLI in Part C), such as 73, shows correctly.
13. **The "Resets soonest" picker label is the spec's A2 wording**, "Resets soonest (use quota before it expires)".
14. **The old `"Switch at"` and old auto-switch caption entries stay in the string files.** They become unused, which is harmless, and removing long translated lines by hand risks breaking a file.
15. **Task 2 carries a one-line `AppState` change** (every account on the global threshold) because the unit harness does not compile `AppState`, so the engine's new signature would otherwise break the app build until Task 6.
16. **No sign-in buttons in the Accounts tab.** Spec A5 mentions them, but spec section 6 gives them to Part B, which adds them to this tab.

## Review Focus

The five inputs most likely to bite a real user that the spec does not spell out. Each has a test in the task that owns the code.

1. **An account with no usage sample yet** (just added, or not yet reached by the round-robin poll). Expected: never chosen, under any strategy and by the early drain. Tests: Task 2 ("no usage sample yet is never chosen"), Task 3 (one per strategy), Task 4 ("drain: never to an account with no usage sample yet").
2. **Every candidate ineligible**, including the case where the active account has reached its threshold and only a drain-grade candidate exists (under its threshold minus 1 but over its threshold minus 10). Expected: stay put. The engine must not fall through to the drain. Tests: Task 2 ("when no candidate is eligible the plan is to stay put"), Task 4 ("drain never fires once the active account has reached its threshold").
3. **A threshold of 100 on the ACTIVE account.** Expected: it stays at 99% and moves at 100%. On a candidate, 100 means eligible up to 90%. Tests: Task 2.
4. **Stale non-active samples from a week that has ended** (`resets_at` in the past). Expected: their weekly reset counts as unknown. Resets soonest ranks them last, and they are never a drain target or a drain source. Tests: Task 3 ("a weekly reset in the past counts as unknown"), Task 4 (three "week that has ended" cases).
5. **An order that names a removed account, leaves one out or repeats one**, for example a CLI call in Part C racing a removal in the GUI. Expected: refused whole, nothing changes, the call returns false. Tests: Task 5.

---

### Task 1: Per-account threshold field, the strategy type, and the settings

**Files:**
- Modify: `PixelSwitch/Models/Account.swift:40` (new property), `:93` (init parameter), `:103` (init assignment)
- Modify: `PixelSwitch/Services/AutoSwitchEngine.swift:1-2` (new enum after the import), end of file (new enum after `AutoSwitchFableSetting`, line 218)
- Create: `Tests/UnitTests/AutoSwitchRulesTests.swift`
- Modify: `Tests/UnitTests/main.swift:585` (one call line)
- Modify: `Tests/run-unit-tests.sh:22` (add the test file)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Account.switchThreshold: Double?`, and the init parameter `switchThreshold: Double? = nil` (last parameter).
  - `enum AutoSwitchStrategy: String, CaseIterable, Codable, Sendable { case mostRoom, myOrder, resetsSoonest }`.
  - `enum AutoSwitchSettings` with `static let enabledKey, thresholdKey, onFableKey, strategyKey, drainEarlyKey, drainWithinHoursKey: String`; `static let thresholdRange: ClosedRange<Double>`, `drainHoursRange: ClosedRange<Double>`, `fallbackThreshold: Double` (90), `fallbackDrainWithinHours: Double` (24); `static var defaultThreshold: Double`, `strategy: AutoSwitchStrategy`, `drainEarly: Bool`, `drainWithinHours: Double`; `static func clampedThreshold(_ value: Double) -> Double`, `normalizedAccountThreshold(_ value: Double?) -> Double?`, `effectiveThreshold(own: Double?, defaultThreshold: Double) -> Double`.
  - `@MainActor func runAutoSwitchRulesTests()` in the new test file.

- [ ] **Step 0: Create the branch**

```bash
cd /Users/ahamade/Documents/GitHub/PixelSwitch
git checkout main && git pull --ff-only
git checkout -b part-a-auto-switch-rules
bash Tests/run-unit-tests.sh | tail -1
```

Expected: `118 passed, 0 failed`.

- [ ] **Step 1: Write the failing test**

Create `Tests/UnitTests/AutoSwitchRulesTests.swift` with exactly this content:

```swift
// Part A: per-account thresholds, the next-account strategy and early
// draining. Compiled into the unit harness by Tests/run-unit-tests.sh and run
// from main.swift. Uses the global `check(_:_:_:)` defined there.
import Foundation

@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
}

// MARK: - Settings keys, defaults, ranges; saved accounts

@MainActor private func autoSwitchSettingsTests() {
    // Saved accounts from before per-account thresholds existed.
    let legacy = #"[{"id":"CB7797E5-5257-402B-80D6-ADAE1220D368","email":"a@x.com","displayName":"A","provider":"Claude Code","isActive":true,"customLabel":"Work"}]"#
    let decoded = try? JSONDecoder().decode([Account].self, from: Data(legacy.utf8))
    check(decoded?.count == 1 && decoded?.first?.switchThreshold == nil,
          "rules: saved accounts without switchThreshold decode, with no threshold of their own")
    check(decoded?.first?.customLabel == "Work" && decoded?.first?.isActive == true,
          "rules: an old saved account keeps everything else it had")

    let own = Account(email: "b@x.com", displayName: "B", switchThreshold: 75)
    let roundTrip = (try? JSONEncoder().encode([own])).flatMap { try? JSONDecoder().decode([Account].self, from: $0) }
    check(roundTrip?.first?.switchThreshold == 75, "rules: a per-account threshold survives saving and loading")
    let plainJSON = (try? JSONEncoder().encode([Account(email: "c@x.com", displayName: "C")])).map { String(decoding: $0, as: UTF8.self) } ?? ""
    check(!plainJSON.contains("switchThreshold"),
          "rules: an account on the default threshold saves no switchThreshold key, so an older build reads it unchanged", plainJSON)

    check(AutoSwitchSettings.enabledKey == "autoSwitchEnabled"
          && AutoSwitchSettings.thresholdKey == "autoSwitchThreshold"
          && AutoSwitchSettings.onFableKey == "autoSwitchOnFable"
          && AutoSwitchSettings.strategyKey == "autoSwitchStrategy"
          && AutoSwitchSettings.drainEarlyKey == "autoSwitchDrainEarly"
          && AutoSwitchSettings.drainWithinHoursKey == "autoSwitchDrainWithinHours",
          "rules: the setting keys are the ones the design fixes")
    check(AutoSwitchSettings.thresholdRange == 50.0...100.0 && AutoSwitchSettings.drainHoursRange == 1.0...72.0,
          "rules: thresholds run 50–100 and the drain window 1–72 hours")
    check(AutoSwitchStrategy.allCases.map(\.rawValue) == ["mostRoom", "myOrder", "resetsSoonest"],
          "rules: the three strategies, by the names settings and the CLI use")

    let defaults = UserDefaults.standard
    let keys = [AutoSwitchSettings.thresholdKey, AutoSwitchSettings.strategyKey,
                AutoSwitchSettings.drainEarlyKey, AutoSwitchSettings.drainWithinHoursKey]
    let saved = keys.map { defaults.object(forKey: $0) }
    defer {
        for (key, value) in zip(keys, saved) {
            if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
        }
    }
    keys.forEach { defaults.removeObject(forKey: $0) }
    check(AutoSwitchSettings.defaultThreshold == 90 && AutoSwitchSettings.strategy == .mostRoom
          && AutoSwitchSettings.drainEarly && AutoSwitchSettings.drainWithinHours == 24,
          "rules: untouched settings read as 90%, most room left, early switching on, 24 hours")

    defaults.set(100.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 100, "rules: the default threshold can now be 100%")
    defaults.set(99.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 99, "rules: a threshold saved by the old 50–99 slider still reads the same")
    defaults.set(30.0, forKey: AutoSwitchSettings.thresholdKey)
    check(AutoSwitchSettings.defaultThreshold == 50, "rules: a default threshold below 50 reads as 50")

    defaults.set("resetsSoonest", forKey: AutoSwitchSettings.strategyKey)
    check(AutoSwitchSettings.strategy == .resetsSoonest, "rules: a saved strategy is read back")
    defaults.set("fastest", forKey: AutoSwitchSettings.strategyKey)
    check(AutoSwitchSettings.strategy == .mostRoom, "rules: an unknown strategy name falls back to most room left")

    defaults.set(false, forKey: AutoSwitchSettings.drainEarlyKey)
    check(AutoSwitchSettings.drainEarly == false, "rules: early switching can be turned off")

    defaults.set(0.5, forKey: AutoSwitchSettings.drainWithinHoursKey)
    let low = AutoSwitchSettings.drainWithinHours
    defaults.set(500.0, forKey: AutoSwitchSettings.drainWithinHoursKey)
    let high = AutoSwitchSettings.drainWithinHours
    defaults.set(12, forKey: AutoSwitchSettings.drainWithinHoursKey)
    let whole = AutoSwitchSettings.drainWithinHours
    check(low == 1 && high == 72 && whole == 12, "rules: the drain window is kept within 1–72 hours, and a whole number reads too", "\(low) \(high) \(whole)")

    check(AutoSwitchSettings.clampedThreshold(49) == 50 && AutoSwitchSettings.clampedThreshold(101) == 100
          && AutoSwitchSettings.clampedThreshold(75) == 75 && AutoSwitchSettings.clampedThreshold(.nan) == 90,
          "rules: thresholds are kept within 50–100, and not-a-number reads as 90")
    check(AutoSwitchSettings.normalizedAccountThreshold(nil) == nil
          && AutoSwitchSettings.normalizedAccountThreshold(.nan) == nil
          && AutoSwitchSettings.normalizedAccountThreshold(120) == 100
          && AutoSwitchSettings.normalizedAccountThreshold(70) == 70,
          "rules: a per-account threshold is stored clamped, and nil or not-a-number clears it")
    check(AutoSwitchSettings.effectiveThreshold(own: nil, defaultThreshold: 90) == 90
          && AutoSwitchSettings.effectiveThreshold(own: 70, defaultThreshold: 90) == 70
          && AutoSwitchSettings.effectiveThreshold(own: nil, defaultThreshold: 30) == 50,
          "rules: an account's own threshold wins; otherwise the default applies")
}
```

In `Tests/UnitTests/main.swift`, replace the last two lines of the file:

```swift
print("\n\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
```

with:

```swift
runAutoSwitchRulesTests()

print("\n\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
```

In `Tests/run-unit-tests.sh`, replace the line `  Tests/UnitTests/main.swift \` with these two lines:

```bash
  Tests/UnitTests/AutoSwitchRulesTests.swift \
  Tests/UnitTests/main.swift \
```

Also change the script's comment on line 2 to:

```bash
# Compiles the credential-writing helpers, the usage model, the auto-switch engine and its rules with Tests/UnitTests/*.swift and runs them.
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors, including `error: cannot find 'AutoSwitchSettings' in scope` (and `extra argument 'switchThreshold' in call`).

- [ ] **Step 3: Implement**

In `PixelSwitch/Models/Account.swift`, after the line `    var customLabel: String?` (line 40), insert:

```swift
    /// This account's own auto-switch threshold, 50–100 percent. nil means it
    /// uses the default (global) threshold. Saved accounts from before this
    /// existed decode it as nil, so they behave exactly as they did.
    var switchThreshold: Double?   // 50...100; nil = use the default (global) threshold
```

In the same file's `init`, replace:

```swift
        customLabel: String? = nil
    ) {
```

with:

```swift
        customLabel: String? = nil,
        switchThreshold: Double? = nil
    ) {
```

and replace:

```swift
        self.customLabel = customLabel
    }
```

with:

```swift
        self.customLabel = customLabel
        self.switchThreshold = switchThreshold
    }
```

Leave `Account`'s `Codable` synthesized. A synthesized decoder reads a missing optional as nil, and a synthesized encoder leaves a nil optional out of the JSON, which the first two tests pin.

In `PixelSwitch/Services/AutoSwitchEngine.swift`, directly after `import Foundation` and its blank line (before `/// Pure, UI-agnostic auto-switch decision logic.`), insert:

```swift
/// How auto-switch picks the next account among the eligible ones
/// (Settings → General → Auto-switch → "Choose the next account by").
enum AutoSwitchStrategy: String, CaseIterable, Codable, Sendable {
    /// Fable room first, then the lowest utilization. Today's behaviour, and the default.
    case mostRoom
    /// The accounts list's own order, always from the top: a higher account
    /// that has freed up again beats the next one down.
    case myOrder
    /// The earliest weekly reset first, so quota about to expire is used
    /// before it is lost. Ties go to most room; an unknown reset ranks last.
    case resetsSoonest
}
```

At the end of the same file, after the closing `}` of `enum AutoSwitchFableSetting`, add a blank line and then:

```swift
/// The auto-switch settings, their UserDefaults keys, defaults and ranges.
/// The Settings window binds to the keys with `@AppStorage`; `AppState` and
/// (later) the control API read them through the typed readers here, so the
/// defaults live in one place.
enum AutoSwitchSettings {
    static let enabledKey = "autoSwitchEnabled"
    static let thresholdKey = "autoSwitchThreshold"
    static let onFableKey = AutoSwitchFableSetting.key
    static let strategyKey = "autoSwitchStrategy"
    static let drainEarlyKey = "autoSwitchDrainEarly"
    static let drainWithinHoursKey = "autoSwitchDrainWithinHours"

    /// A switch threshold, global or per account, in percent.
    static let thresholdRange = 50.0...100.0
    /// How soon a weekly reset must be for an early drain, in hours.
    static let drainHoursRange = 1.0...72.0

    /// The default threshold when none has been saved.
    static let fallbackThreshold = 90.0
    /// The drain window when none has been saved.
    static let fallbackDrainWithinHours = 24.0

    /// The global default threshold (Settings → General → Default threshold).
    /// Unset (read as 0) means 90, as before; anything else is kept in range.
    static var defaultThreshold: Double {
        let stored = UserDefaults.standard.double(forKey: thresholdKey)
        return stored == 0 ? fallbackThreshold : clampedThreshold(stored)
    }

    /// The next-account strategy. Unset or unrecognised reads as `.mostRoom`.
    static var strategy: AutoSwitchStrategy {
        UserDefaults.standard.string(forKey: strategyKey).flatMap(AutoSwitchStrategy.init(rawValue:)) ?? .mostRoom
    }

    /// "Switch early to use quota before it resets". On unless turned off.
    static var drainEarly: Bool {
        UserDefaults.standard.object(forKey: drainEarlyKey) as? Bool ?? true
    }

    /// The early-drain window in hours, kept in 1–72. Unset reads as 24.
    static var drainWithinHours: Double {
        guard let stored = UserDefaults.standard.object(forKey: drainWithinHoursKey) as? Double, stored.isFinite else {
            return fallbackDrainWithinHours
        }
        return min(max(stored, drainHoursRange.lowerBound), drainHoursRange.upperBound)
    }

    /// `value` kept within 50–100; a value that is not a number reads as 90.
    static func clampedThreshold(_ value: Double) -> Double {
        guard value.isFinite else { return fallbackThreshold }
        return min(max(value, thresholdRange.lowerBound), thresholdRange.upperBound)
    }

    /// What a per-account threshold is stored as: nil (use the default) for
    /// nil or a value that is not a number, else the value kept within 50–100.
    static func normalizedAccountThreshold(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        return clampedThreshold(value)
    }

    /// The threshold that applies to an account: its own when set, else the
    /// default, always within 50–100.
    static func effectiveThreshold(own: Double?, defaultThreshold: Double) -> Double {
        normalizedAccountThreshold(own) ?? clampedThreshold(defaultThreshold)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `136 passed, 0 failed` (118 before, 18 new).

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK (48 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Models/Account.swift PixelSwitch/Services/AutoSwitchEngine.swift \
        Tests/UnitTests/AutoSwitchRulesTests.swift Tests/UnitTests/main.swift Tests/run-unit-tests.sh
git commit -m "$(cat <<'EOF'
Auto-switch rules: per-account threshold field, strategy type and settings

Account gains switchThreshold (nil = the default). AutoSwitchStrategy and
AutoSwitchSettings hold the keys, defaults and ranges from spec section 9;
the default threshold now reads 50-100. Old saved accounts decode unchanged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Per-account thresholds in the engine, and the rule that fired

**Files:**
- Modify: `PixelSwitch/Services/AutoSwitchEngine.swift`: replace `plan` and `rankedTargets` (the region starting at the doc comment `    /// Which limit to act on and where to go, or nil to stay put.`, originally lines 103–207, now shifted down by Task 1's insert) and add the `Trigger` extension after the enum's closing brace.
- Modify: `PixelSwitch/AppState.swift:752-760` (one call-site change inside `evaluateAutoSwitch`)
- Modify: `Tests/UnitTests/main.swift:315-320` (the existing auto-switch tests' `plan` and `names` helpers)
- Modify: `Tests/UnitTests/AutoSwitchRulesTests.swift`

**Interfaces:**
- Consumes: `Account.switchThreshold`, `AutoSwitchSettings.effectiveThreshold(own:defaultThreshold:)` (Task 1).
- Produces:
  - `extension AutoSwitchEngine { enum Trigger: String, Sendable { case threshold, drainEarly } }`
  - `static func plan(active: Account, candidates: [Account], usageByAccount: [UUID: UsageAPIResponse], isSwitchable: (Account) -> Bool, activeSampledThisCycle: Bool, threshold: (Account) -> Double, hysteresisPct: Double, watchFable: Bool = true, asOf now: Date = Date()) -> (limit: Limit, trigger: Trigger, targets: [Account])?`
  - `static func rankedTargets(active:candidates:usageByAccount:isSwitchable:threshold:hysteresisPct:limit:asOf:) -> [Account]`, where `threshold` is `(Account) -> Double`.
  - Tasks 3 and 4 add parameters to both, with defaults on `plan`.

- [ ] **Step 1: Write the failing test**

In `Tests/UnitTests/AutoSwitchRulesTests.swift`, replace the function `runAutoSwitchRulesTests()` with:

```swift
@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
}
```

Directly after that function (before `// MARK: - Settings keys, defaults, ranges; saved accounts`), insert the shared fixtures:

```swift
/// Fixtures shared by every case in this file.
private enum Rules {
    /// The fixed clock every case uses.
    static let now = ISO8601DateFormatter().date(from: "2026-09-22T17:00:00Z")!

    /// An ISO 8601 time `hours` from `now` (negative is in the past).
    static func iso(_ hours: Double) -> String {
        ISO8601DateFormatter().string(from: now.addingTimeInterval(hours * 3600))
    }

    typealias Plan = (limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger, targets: [Account])

    /// The engine as AppState calls it, with each account's effective threshold.
    static func plan(active: Account, candidates: [Account], _ usage: [UUID: UsageAPIResponse],
                     defaultThreshold: Double = 90, watchFable: Bool = true,
                     switchable: (Account) -> Bool = { _ in true }, sampled: Bool = true) -> Plan? {
        AutoSwitchEngine.plan(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: switchable, activeSampledThisCycle: sampled,
            threshold: { AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: defaultThreshold) },
            hysteresisPct: 10, watchFable: watchFable, asOf: now)
    }

    /// "stay", or "<limit>/<trigger>: <targets in order>".
    static func describe(_ p: Plan?) -> String {
        guard let p else { return "stay" }
        return "\(p.limit.rawValue)/\(p.trigger.rawValue): " + p.targets.map(\.displayName).joined(separator: ",")
    }
}

/// A usage reading for these cases: session and weekly use in percent (nil is
/// sent as JSON null), every `...ResetsIn` in hours from `Rules.now` (negative
/// is in the past, nil leaves `resets_at` out), and an optional Fable allowance.
private func sample(_ session: Double?, _ weekly: Double?,
                    weeklyResetsIn: Double? = 100, sessionResetsIn: Double? = 2,
                    fable: Double? = nil, fableResetsIn: Double? = 100) -> UsageAPIResponse {
    func window(_ util: Double?, _ resetsIn: Double?) -> String {
        let u = util.map { #""utilization":\#($0)"# } ?? #""utilization":null"#
        let r = resetsIn.map { #","resets_at":"\#(Rules.iso($0))""# } ?? ""
        return "{" + u + r + "}"
    }
    var json = #"{"five_hour":"# + window(session, sessionResetsIn) + #","seven_day":"# + window(weekly, weeklyResetsIn)
    if let fable {
        let r = fableResetsIn.map { #","resets_at":"\#(Rules.iso($0))""# } ?? ""
        json += #","limits":[{"kind":"weekly_scoped","group":"weekly","percent":\#(fable)\#(r),"scope":{"model":{"id":null,"display_name":"Fable"}}}]"#
    }
    return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data((json + "}").utf8))
}
```

At the end of the file, append:

```swift
// MARK: - Per-account thresholds (trigger and eligibility)

@MainActor private func perAccountThresholdTests() {
    let a70 = Account(email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 70)
    let aDefault = Account(email: "a@x.com", displayName: "A", isActive: true)
    let a100 = Account(email: "a@x.com", displayName: "A", isActive: true, switchThreshold: 100)
    let b70 = Account(email: "b@x.com", displayName: "B", switchThreshold: 70)
    let c = Account(email: "c@x.com", displayName: "C")
    let d100 = Account(email: "d@x.com", displayName: "D", switchThreshold: 100)
    let fresh = Account(email: "e@x.com", displayName: "E")

    // The ACTIVE account's own threshold decides when to move.
    let at72: [UUID: UsageAPIResponse] = [a70.id: sample(72, 40), c.id: sample(10, 10)]
    check(Rules.describe(Rules.plan(active: a70, candidates: [c], at72)) == "windows/threshold: C",
          "rules: an account with its own 70% threshold switches at 72%", Rules.describe(Rules.plan(active: a70, candidates: [c], at72)))
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [c], [aDefault.id: sample(72, 40), c.id: sample(10, 10)])) == "stay",
          "rules: the same 72% on an account using the default 90% stays put")
    check(Rules.describe(Rules.plan(active: a70, candidates: [c], [a70.id: sample(69, 40), c.id: sample(10, 10)])) == "stay",
          "rules: 69% under a 70% threshold stays put")

    // Each CANDIDATE is judged against its own threshold minus the 10-point hysteresis.
    let mixed: [UUID: UsageAPIResponse] = [
        aDefault.id: sample(95, 40),
        b70.id: sample(65, 20),   // over its own 60% ceiling
        c.id: sample(75, 20),     // under the default 80% ceiling
    ]
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [b70, c], mixed)) == "windows/threshold: C",
          "rules: a candidate with its own 70% threshold must be at or under 60%", Rules.describe(Rules.plan(active: aDefault, candidates: [b70, c], mixed)))
    let b70AtCeiling: [UUID: UsageAPIResponse] = [aDefault.id: sample(95, 40), b70.id: sample(60, 20)]
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [b70], b70AtCeiling)) == "windows/threshold: B",
          "rules: exactly at its own ceiling is still eligible")

    // Fable triggers on the active account's own threshold too.
    let fableAt75: [UUID: UsageAPIResponse] = [a70.id: sample(10, 20, fable: 75), c.id: sample(5, 10, fable: 20)]
    check(Rules.describe(Rules.plan(active: a70, candidates: [c], fableAt75)) == "fable/threshold: C",
          "rules: Fable at 75% moves an account whose own threshold is 70%", Rules.describe(Rules.plan(active: a70, candidates: [c], fableAt75)))

    // Threshold 100: use the account until it is empty.
    check(Rules.describe(Rules.plan(active: a100, candidates: [c], [a100.id: sample(99, 50), c.id: sample(10, 10)])) == "stay",
          "rules: an active account on 100% stays at 99%")
    check(Rules.describe(Rules.plan(active: a100, candidates: [c], [a100.id: sample(100, 50), c.id: sample(10, 10)])) == "windows/threshold: C",
          "rules: an active account on 100% moves once it is empty")
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [d100], [aDefault.id: sample(95, 40), d100.id: sample(90, 90)])) == "windows/threshold: D",
          "rules: a candidate on 100% is eligible up to 90%")
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [d100], [aDefault.id: sample(95, 40), d100.id: sample(91, 20)])) == "stay",
          "rules: a candidate on 100% at 91% is not")

    // Review focus: an account with no usage sample yet, and nobody eligible.
    check(Rules.describe(Rules.plan(active: aDefault, candidates: [fresh], [aDefault.id: sample(95, 40)])) == "stay",
          "rules: an account with no usage sample yet is never chosen")
    let nobody: [UUID: UsageAPIResponse] = [aDefault.id: sample(95, 40), b70.id: sample(61, 20), c.id: sample(81, 20)]
    check(Rules.plan(active: aDefault, candidates: [b70, c], nobody) == nil,
          "rules: when no candidate is eligible the plan is to stay put")
}
```

In `Tests/UnitTests/main.swift`, in the section `// MARK: - Auto-switch on session/weekly and on Fable`, replace the two helper functions:

```swift
    func plan(_ byAccount: [UUID: UsageAPIResponse], candidates: [Account]? = nil, switchable: @escaping (Account) -> Bool = { _ in true }, sampled: Bool = true, watchFable: Bool = true) -> (limit: AutoSwitchEngine.Limit, targets: [Account])? {
        AutoSwitchEngine.plan(active: active, candidates: candidates ?? [b, c, d, e], usageByAccount: byAccount,
                              isSwitchable: switchable, activeSampledThisCycle: sampled,
                              threshold: 98, hysteresisPct: 10, watchFable: watchFable, asOf: now)
    }
    func names(_ p: (limit: AutoSwitchEngine.Limit, targets: [Account])?) -> String {
```

with:

```swift
    func plan(_ byAccount: [UUID: UsageAPIResponse], candidates: [Account]? = nil, switchable: @escaping (Account) -> Bool = { _ in true }, sampled: Bool = true, watchFable: Bool = true) -> (limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger, targets: [Account])? {
        AutoSwitchEngine.plan(active: active, candidates: candidates ?? [b, c, d, e], usageByAccount: byAccount,
                              isSwitchable: switchable, activeSampledThisCycle: sampled,
                              threshold: { _ in 98 }, hysteresisPct: 10, watchFable: watchFable, asOf: now)
    }
    func names(_ p: (limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger, targets: [Account])?) -> String {
```

Leave the body of `names` and every expectation in that section unchanged. Those 19 existing cases must still pass untouched: that is the proof that an install with no per-account thresholds behaves exactly as before.

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors including `error: 'Trigger' is not a member type of enum 'main.AutoSwitchEngine'` and `error: closure passed to parameter of type 'Double' that does not accept a closure`.

- [ ] **Step 3: Implement**

In `PixelSwitch/Services/AutoSwitchEngine.swift`, replace everything from the line `    /// Which limit to act on and where to go, or nil to stay put.` down to and including the closing `    }` of `rankedTargets` (the line just before the `}` that closes `enum AutoSwitchEngine`) with:

```swift
    /// Which limit to act on, by which rule, and where to go; nil to stay put.
    /// Session and weekly are checked first; Fable only when they have not
    /// triggered a switch, since a Fable target must have session and weekly
    /// room anyway. Each account is judged against its OWN threshold.
    ///
    /// `watchFable` is the user's setting: off means Fable is shown but never
    /// moves anyone, while session and weekly keep working exactly as before.
    ///
    /// - Parameters:
    ///   - active: the currently active account.
    ///   - candidates: every other account (already filtered to the same provider).
    ///   - usageByAccount: latest usage sample per account id.
    ///   - isSwitchable: whether an account can actually be switched to right now
    ///     (has a stored backup token and isn't flagged expired).
    ///   - activeSampledThisCycle: whether the active account's sample was taken
    ///     by the refresh cycle that is asking. Fresh readings are trusted even
    ///     without a parseable `resets_at`; only RETAINED readings need the
    ///     strict expiry check.
    ///   - threshold: each account's effective switch threshold (its own, else
    ///     the default), 50–100.
    ///   - hysteresisPct: a candidate must sit at least this far below its own
    ///     threshold (e.g. 10).
    ///   - now: injected clock, for window-expiry checks and testability.
    static func plan(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        activeSampledThisCycle: Bool,
        threshold: (Account) -> Double,
        hysteresisPct: Double,
        watchFable: Bool = true,
        asOf now: Date = Date()
    ) -> (limit: Limit, trigger: Trigger, targets: [Account])? {
        let activeThreshold = threshold(active)
        for limit in (watchFable ? [Limit.windows, .fable] : [Limit.windows]) {
            // Only act once the active account has reached its threshold on a
            // limit we watch. Unknown active usage -> do nothing (can't decide).
            // The trigger must never rest on a RETAINED reading whose expiry we
            // cannot establish — but a reading this very cycle fetched is
            // current whether or not the endpoint sent a parseable resets_at.
            guard let activeUtil = utilization(usageByAccount[active.id], limit: limit, asOf: now,
                                               requireKnownWindow: !activeSampledThisCycle),
                  activeUtil >= activeThreshold else { continue }
            let targets = rankedTargets(
                active: active, candidates: candidates, usageByAccount: usageByAccount,
                isSwitchable: isSwitchable, threshold: threshold, hysteresisPct: hysteresisPct,
                limit: limit, asOf: now
            )
            if !targets.isEmpty { return (limit, .threshold, targets) }
        }
        return nil
    }

    /// Rank the accounts worth switching to for one limit, best first.
    ///
    /// The result is a list of *proposals*, not decisions: it is computed from
    /// whatever samples the caller happens to hold, which round-robin polling can
    /// leave several cycles old. `AppState` verifies a candidate's usage before
    /// committing to a switch, and falls through the list when one fails.
    ///
    /// A candidate is eligible when it sits at or below ITS OWN threshold minus
    /// `hysteresisPct`. A candidate with no usable reading is NOT eligible:
    /// accounts are polled round-robin, so "no sample" usually means "not
    /// reached yet" rather than "idle" — treating it as a fallback let an
    /// automatic switch land on an account that was itself maxed out.
    static func rankedTargets(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        threshold: (Account) -> Double,
        hysteresisPct: Double,
        limit: Limit,
        asOf now: Date = Date()
    ) -> [Account] {
        candidates
            .compactMap { candidate -> (account: Account, util: Double, keepsFable: Bool)? in
                let usage = usageByAccount[candidate.id]
                let ceiling = threshold(candidate) - hysteresisPct
                // The usage check comes first: `isSwitchable` reads the Keychain.
                guard candidate.id != active.id,
                      let util = eligibleUtilization(usage, limit: limit, ceiling: ceiling, asOf: now),
                      isSwitchable(candidate) else { return nil }
                let keepsFable = utilization(usage, limit: .fable, asOf: now).map { $0 <= ceiling } ?? false
                return (candidate, util, keepsFable)
            }
            // Accounts with Fable to spare first, so a session/weekly switch
            // does not land on an account that is out of Fable and force a
            // second switch minutes later; then most headroom first (lowest
            // known utilization). Every `.fable` candidate keeps Fable, so
            // those rank purely by Fable headroom.
            .sorted { ($0.keepsFable ? 0 : 1, $0.util) < ($1.keepsFable ? 0 : 1, $1.util) }
            .map(\.account)
    }
```

Then, directly after the `}` that closes `enum AutoSwitchEngine` (before `/// Whether auto-switch also acts on the weekly Fable allowance.`), insert:

```swift
extension AutoSwitchEngine {
    /// The rule that produced a plan: the active account reached its
    /// threshold, or (Resets soonest only) another account's weekly quota is
    /// about to reset unused.
    enum Trigger: String, Sendable { case threshold, drainEarly }
}
```

In `PixelSwitch/AppState.swift`, inside `evaluateAutoSwitch()`, replace:

```swift
        let activeSampledThisCycle = (accountUsageSampledAt[active.id] ?? .distantPast) >= lastCycleStart
        guard let plan = AutoSwitchEngine.plan(
            active: active,
            candidates: candidates,
            usageByAccount: accountUsage,
            isSwitchable: { [unowned self] in self.isSwitchable($0) },
            activeSampledThisCycle: activeSampledThisCycle,
            threshold: autoSwitchThreshold,
```

with:

```swift
        let activeSampledThisCycle = (accountUsageSampledAt[active.id] ?? .distantPast) >= lastCycleStart
        // Every account on the one global threshold until per-account
        // thresholds are wired in (Part A, Task 6).
        let globalThreshold = autoSwitchThreshold
        guard let plan = AutoSwitchEngine.plan(
            active: active,
            candidates: candidates,
            usageByAccount: accountUsage,
            isSwitchable: { [unowned self] in self.isSwitchable($0) },
            activeSampledThisCycle: activeSampledThisCycle,
            threshold: { _ in globalThreshold },
```

Nothing else in `AppState` changes in this task. `plan.limit` and `plan.targets` still read by label, and the verification ceiling (`autoSwitchThreshold - autoSwitchHysteresis`) still agrees with the ranking because every account is on the same threshold.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `148 passed, 0 failed`.

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK (48 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/AutoSwitchEngine.swift PixelSwitch/AppState.swift \
        Tests/UnitTests/AutoSwitchRulesTests.swift Tests/UnitTests/main.swift
git commit -m "$(cat <<'EOF'
Auto-switch engine: each account judged against its own threshold

plan() takes a per-account threshold closure: the active account triggers
at its own threshold (Fable too) and a candidate must sit 10 points under
its own. plan() now also returns the rule that fired (Trigger). AppState
passes the global threshold for every account until Task 6 wires it in.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Choosing the next account (most room, my order, resets soonest)

**Files:**
- Modify: `PixelSwitch/Services/AutoSwitchEngine.swift`: insert `weeklyReset` before `private static func reading`; replace `plan` and `rankedTargets` again.
- Modify: `Tests/UnitTests/AutoSwitchRulesTests.swift`

**Interfaces:**
- Consumes: `AutoSwitchStrategy` (Task 1); `plan`, `rankedTargets`, `Trigger` (Task 2).
- Produces:
  - `static func weeklyReset(_ usage: UsageAPIResponse?, limit: Limit, asOf now: Date = Date()) -> Date?`. The 7-day reset for `.windows` and the Fable weekly reset for `.fable`, nil unless there is a reading with a reset still in the future. Task 6 and Task 8 call it.
  - `plan(...)` gains `strategy: AutoSwitchStrategy = .mostRoom` after `watchFable`.
  - `rankedTargets(...)` gains `strategy: AutoSwitchStrategy` after `limit`.

- [ ] **Step 1: Write the failing test**

In `Tests/UnitTests/AutoSwitchRulesTests.swift`, replace `runAutoSwitchRulesTests()` with:

```swift
@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
    strategyTests()
}
```

Replace the whole `private enum Rules { ... }` (from `/// Fixtures shared by every case in this file.` to its closing `}`) with this version, which passes the strategy through:

```swift
/// Fixtures shared by every case in this file.
private enum Rules {
    /// The fixed clock every case uses.
    static let now = ISO8601DateFormatter().date(from: "2026-09-22T17:00:00Z")!

    /// An ISO 8601 time `hours` from `now` (negative is in the past).
    static func iso(_ hours: Double) -> String {
        ISO8601DateFormatter().string(from: now.addingTimeInterval(hours * 3600))
    }

    typealias Plan = (limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger, targets: [Account])

    /// The engine as AppState calls it, with each account's effective threshold.
    static func plan(active: Account, candidates: [Account], _ usage: [UUID: UsageAPIResponse],
                     defaultThreshold: Double = 90, strategy: AutoSwitchStrategy = .mostRoom,
                     watchFable: Bool = true,
                     switchable: (Account) -> Bool = { _ in true }, sampled: Bool = true) -> Plan? {
        AutoSwitchEngine.plan(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: switchable, activeSampledThisCycle: sampled,
            threshold: { AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: defaultThreshold) },
            hysteresisPct: 10, watchFable: watchFable, strategy: strategy, asOf: now)
    }

    /// "stay", or "<limit>/<trigger>: <targets in order>".
    static func describe(_ p: Plan?) -> String {
        guard let p else { return "stay" }
        return "\(p.limit.rawValue)/\(p.trigger.rawValue): " + p.targets.map(\.displayName).joined(separator: ",")
    }
}
```

At the end of the file, append:

```swift
// MARK: - Choosing the next account

@MainActor private func strategyTests() {
    let a = Account(email: "a@x.com", displayName: "A", isActive: true)
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    let d = Account(email: "d@x.com", displayName: "D")
    let e = Account(email: "e@x.com", displayName: "E")
    func plan(_ usage: [UUID: UsageAPIResponse], _ strategy: AutoSwitchStrategy, active: Account? = nil, candidates: [Account]? = nil) -> String {
        Rules.describe(Rules.plan(active: active ?? a, candidates: candidates ?? [b, c, d], usage, strategy: strategy))
    }

    // Review focus: an account with no usage sample yet is never chosen, whatever the strategy.
    let fresh = Account(email: "f@x.com", displayName: "F")
    for strategy in AutoSwitchStrategy.allCases {
        check(plan([a.id: sample(95, 40)], strategy, candidates: [fresh]) == "stay",
              "rules: an account with no usage sample yet is never chosen (\(strategy.rawValue))")
    }

    // Three eligible accounts, listed B, C, D.
    let three: [UUID: UsageAPIResponse] = [
        a.id: sample(95, 40),
        b.id: sample(10, 50),   // binding 50, weekly resets in 100 h
        c.id: sample(10, 20, weeklyResetsIn: 30),    // binding 20, resets in 30 h
        d.id: sample(10, 40, weeklyResetsIn: 10),    // binding 40, resets in 10 h
    ]
    check(plan(three, .mostRoom) == "windows/threshold: C,D,B", "rules: most room left ranks by lowest use", plan(three, .mostRoom))
    check(plan(three, .myOrder) == "windows/threshold: B,C,D", "rules: my order keeps the list's order", plan(three, .myOrder))
    check(plan(three, .resetsSoonest) == "windows/threshold: D,C,B", "rules: resets soonest ranks by the weekly reset", plan(three, .resetsSoonest))

    // Resets soonest: a tie (same minute) goes to most room; an unknown reset ranks last.
    let ties: [UUID: UsageAPIResponse] = [
        a.id: sample(95, 40),
        b.id: sample(10, 60, weeklyResetsIn: 30),
        c.id: sample(10, 30, weeklyResetsIn: 30 + 20.0 / 3600),   // 20 seconds later: same minute
        d.id: sample(5, 5, weeklyResetsIn: nil),                  // no weekly reset time at all
        e.id: sample(5, 5, weeklyResetsIn: -3),                   // retained from a week that has ended
    ]
    let tied = plan(ties, .resetsSoonest, candidates: [b, c, d, e])
    check(tied == "windows/threshold: C,B,D,E", "rules: resets soonest breaks a tie by most room, and puts unknown resets last", tied)
    check(AutoSwitchEngine.weeklyReset(ties[e.id], limit: .windows, asOf: Rules.now) == nil,
          "rules: a weekly reset in the past counts as unknown, not as soonest")

    // My order: always from the top, never "the next one after the active account".
    let fromTop: [UUID: UsageAPIResponse] = [a.id: sample(10, 10), c.id: sample(95, 40), b.id: sample(10, 10), d.id: sample(10, 5)]
    check(plan(fromTop, .myOrder, active: c, candidates: [a, b, d]) == "windows/threshold: A,B,D",
          "rules: my order starts from the top of the list, not after the active account", plan(fromTop, .myOrder, active: c, candidates: [a, b, d]))
    let freedUp: [UUID: UsageAPIResponse] = [d.id: sample(95, 40), a.id: sample(99, 99), b.id: sample(30, 30), c.id: sample(5, 5)]
    check(plan(freedUp, .myOrder, active: d, candidates: [a, b, c]) == "windows/threshold: B,C",
          "rules: a higher account that has freed up again comes before the next one down", plan(freedUp, .myOrder, active: d, candidates: [a, b, c]))

    // Fable: most room keeps preferring Fable room; the other two honour the user strictly.
    let fableSpare: [UUID: UsageAPIResponse] = [a.id: sample(95, 40, fable: 50), b.id: sample(10, 10, fable: 100), c.id: sample(20, 20, fable: 10)]
    check(plan(fableSpare, .mostRoom, candidates: [b, c]) == "windows/threshold: C,B", "rules: most room left still prefers an account with Fable to spare")
    check(plan(fableSpare, .myOrder, candidates: [b, c]) == "windows/threshold: B,C", "rules: my order keeps its order even when the first account is out of Fable")

    // A Fable switch under resets soonest ranks by the FABLE weekly reset.
    let fableReset: [UUID: UsageAPIResponse] = [
        a.id: sample(10, 20, fable: 95),
        b.id: sample(5, 10, weeklyResetsIn: 5, fable: 10, fableResetsIn: 50),     // weekly resets in 5 h, Fable in 50 h
        c.id: sample(5, 10, fable: 30, fableResetsIn: 20),   // weekly in 100 h, Fable in 20 h
    ]
    check(plan(fableReset, .resetsSoonest, candidates: [b, c]) == "fable/threshold: C,B",
          "rules: a Fable switch under resets soonest uses the Fable weekly reset", plan(fableReset, .resetsSoonest, candidates: [b, c]))
    check(plan(fableReset, .mostRoom, candidates: [b, c]) == "fable/threshold: B,C", "rules: the same Fable switch under most room left ranks by Fable room")

    // Most room: equal use keeps the list's order, so the result never flickers.
    let equal: [UUID: UsageAPIResponse] = [a.id: sample(95, 40), b.id: sample(10, 30), c.id: sample(10, 30)]
    check(plan(equal, .mostRoom, candidates: [c, b]) == "windows/threshold: C,B", "rules: most room left breaks a tie by the list's order")
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors including `error: extra argument 'strategy' in call` and `error: type 'AutoSwitchEngine' has no member 'weeklyReset'`.

- [ ] **Step 3: Implement**

In `PixelSwitch/Services/AutoSwitchEngine.swift`, directly before `    /// One window's utilization, or nil if it has none or its window has reset.`, insert:

```swift
    /// The weekly reset "Resets soonest" ranks by: the 7-day window for
    /// `.windows`, the Fable weekly allowance for `.fable`. Never the 5-hour
    /// window, which resets every few hours and would make the choice
    /// meaningless.
    ///
    /// Nil unless that window has a reading AND a reset time still in the
    /// future. A reset in the past means the sample describes a week that has
    /// already ended (round-robin polling keeps old samples), so when the NEXT
    /// week ends is unknown.
    static func weeklyReset(_ usage: UsageAPIResponse?, limit: Limit, asOf now: Date = Date()) -> Date? {
        let window: UsageWindow?
        switch limit {
        case .windows: window = usage?.sevenDay
        case .fable: window = usage?.modelWeeklyLimit(named: fableModelName)?.window
        }
        guard let window, window.utilization != nil,
              let resets = window.resetsAtDate, resets > now else { return nil }
        return resets
    }
```

Then replace everything from `    /// Which limit to act on, by which rule, and where to go; nil to stay put.` down to and including the closing `    }` of `rankedTargets` with:

```swift
    /// Which limit to act on, by which rule, and where to go; nil to stay put.
    /// Session and weekly are checked first; Fable only when they have not
    /// triggered a switch, since a Fable target must have session and weekly
    /// room anyway. Each account is judged against its OWN threshold, and the
    /// eligible ones are ordered by `strategy`.
    ///
    /// `watchFable` is the user's setting: off means Fable is shown but never
    /// moves anyone, while session and weekly keep working exactly as before.
    ///
    /// - Parameters:
    ///   - active: the currently active account.
    ///   - candidates: every other account (already filtered to the same
    ///     provider), IN THE ACCOUNTS LIST'S ORDER: `.myOrder` ranks by it.
    ///   - usageByAccount: latest usage sample per account id.
    ///   - isSwitchable: whether an account can actually be switched to right now
    ///     (has a stored backup token and isn't flagged expired).
    ///   - activeSampledThisCycle: whether the active account's sample was taken
    ///     by the refresh cycle that is asking. Fresh readings are trusted even
    ///     without a parseable `resets_at`; only RETAINED readings need the
    ///     strict expiry check.
    ///   - threshold: each account's effective switch threshold (its own, else
    ///     the default), 50–100.
    ///   - hysteresisPct: a candidate must sit at least this far below its own
    ///     threshold (e.g. 10).
    ///   - strategy: how eligible candidates are ordered. The default is
    ///     today's behaviour.
    ///   - now: injected clock, for window-expiry checks and testability.
    static func plan(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        activeSampledThisCycle: Bool,
        threshold: (Account) -> Double,
        hysteresisPct: Double,
        watchFable: Bool = true,
        strategy: AutoSwitchStrategy = .mostRoom,
        asOf now: Date = Date()
    ) -> (limit: Limit, trigger: Trigger, targets: [Account])? {
        let activeThreshold = threshold(active)
        for limit in (watchFable ? [Limit.windows, .fable] : [Limit.windows]) {
            // Only act once the active account has reached its threshold on a
            // limit we watch. Unknown active usage -> do nothing (can't decide).
            // The trigger must never rest on a RETAINED reading whose expiry we
            // cannot establish — but a reading this very cycle fetched is
            // current whether or not the endpoint sent a parseable resets_at.
            guard let activeUtil = utilization(usageByAccount[active.id], limit: limit, asOf: now,
                                               requireKnownWindow: !activeSampledThisCycle),
                  activeUtil >= activeThreshold else { continue }
            let targets = rankedTargets(
                active: active, candidates: candidates, usageByAccount: usageByAccount,
                isSwitchable: isSwitchable, threshold: threshold, hysteresisPct: hysteresisPct,
                limit: limit, strategy: strategy, asOf: now
            )
            if !targets.isEmpty { return (limit, .threshold, targets) }
        }
        return nil
    }

    /// Rank the accounts worth switching to for one limit, best first.
    ///
    /// The result is a list of *proposals*, not decisions: it is computed from
    /// whatever samples the caller happens to hold, which round-robin polling can
    /// leave several cycles old. `AppState` verifies a candidate's usage before
    /// committing to a switch, and falls through the list when one fails.
    ///
    /// A candidate is eligible when it sits at or below ITS OWN threshold minus
    /// `hysteresisPct`. A candidate with no usable reading is NOT eligible:
    /// accounts are polled round-robin, so "no sample" usually means "not
    /// reached yet" rather than "idle" — treating it as a fallback let an
    /// automatic switch land on an account that was itself maxed out.
    static func rankedTargets(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        threshold: (Account) -> Double,
        hysteresisPct: Double,
        limit: Limit,
        strategy: AutoSwitchStrategy,
        asOf now: Date = Date()
    ) -> [Account] {
        struct Ranked {
            let account: Account
            /// Position in `candidates`, i.e. in the user's order.
            let position: Int
            let util: Double
            let keepsFable: Bool
            /// The weekly reset in whole minutes since 1970, or nil when unknown.
            /// Whole minutes because the API stamps each reading with the
            /// fetch's own microseconds, so two accounts on the same weekly
            /// schedule never compare exactly equal.
            let resetMinute: Double?
        }

        let eligible = candidates.enumerated().compactMap { position, candidate -> Ranked? in
            let usage = usageByAccount[candidate.id]
            let ceiling = threshold(candidate) - hysteresisPct
            // The usage check comes first: `isSwitchable` reads the Keychain.
            guard candidate.id != active.id,
                  let util = eligibleUtilization(usage, limit: limit, ceiling: ceiling, asOf: now),
                  isSwitchable(candidate) else { return nil }
            let keepsFable = utilization(usage, limit: .fable, asOf: now).map { $0 <= ceiling } ?? false
            let resetMinute = weeklyReset(usage, limit: limit, asOf: now)
                .map { ($0.timeIntervalSince1970 / 60).rounded(.down) }
            return Ranked(account: candidate, position: position, util: util,
                          keepsFable: keepsFable, resetMinute: resetMinute)
        }

        let ordered: [Ranked]
        switch strategy {
        case .mostRoom:
            // Accounts with Fable to spare first, so a session/weekly switch
            // does not land on an account that is out of Fable and force a
            // second switch minutes later; then most headroom first (lowest
            // known utilization); then the user's order, so a tie is stable.
            ordered = eligible.sorted {
                ($0.keepsFable ? 0 : 1, $0.util, $0.position) < ($1.keepsFable ? 0 : 1, $1.util, $1.position)
            }
        case .myOrder:
            // Already in the user's order: always from the top.
            ordered = eligible
        case .resetsSoonest:
            ordered = eligible.sorted { a, b in
                switch (a.resetMinute, b.resetMinute) {
                case let (x?, y?) where x != y: return x < y
                case (.some, nil): return true
                case (nil, .some): return false
                default: return (a.util, a.position) < (b.util, b.position)
                }
            }
        }
        return ordered.map(\.account)
    }
```

`AppState` needs no change in this task: it does not pass `strategy`, so it gets `.mostRoom`, which is today's behaviour.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `163 passed, 0 failed`.

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK (48 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/AutoSwitchEngine.swift Tests/UnitTests/AutoSwitchRulesTests.swift
git commit -m "$(cat <<'EOF'
Auto-switch engine: most room, my order, or resets soonest

Ranks eligible accounts by the chosen strategy. My order follows the
accounts list from the top; resets soonest uses the weekly reset (the
Fable weekly reset for a Fable switch), ties by most room within the same
minute, unknown or past resets last. Most room is unchanged, with ties now
kept in list order.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Switching early to use quota before it resets

**Files:**
- Modify (full replacement): `PixelSwitch/Services/AutoSwitchEngine.swift`
- Modify: `Tests/UnitTests/AutoSwitchRulesTests.swift`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces:
  - `static let drainMinimumRoom: Double = 1.0`
  - `static func drainEligibleUtilization(_ usage: UsageAPIResponse?, threshold: Double, activeWeeklyReset: Date, drainWithin: TimeInterval, watchFable: Bool, asOf now: Date = Date()) -> Double?`
  - `static func eligibleUtilization(_ usage: UsageAPIResponse?, limit: Limit, trigger: Trigger, threshold: Double, hysteresisPct: Double, activeWeeklyReset: Date?, drainWithin: TimeInterval, watchFable: Bool, asOf now: Date = Date()) -> Double?`: the one rule shared by ranking and by `AppState`'s verification (Task 6).
  - `plan(...)` gains `drainEarly: Bool = false, drainWithin: TimeInterval = 24 * 3600` after `strategy`, and can now return `trigger == .drainEarly` (always with `limit == .windows`).
  - `rankedTargets(...)` gains `trigger:`, `activeWeeklyReset:`, `drainWithin:` and `watchFable:`.

- [ ] **Step 1: Write the failing test**

In `Tests/UnitTests/AutoSwitchRulesTests.swift`, replace `runAutoSwitchRulesTests()` with:

```swift
@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
    strategyTests()
    earlyDrainTests()
}
```

Replace the whole `private enum Rules { ... }` with this final version, which also passes the drain settings:

```swift
/// Fixtures shared by every case in this file.
private enum Rules {
    /// The fixed clock every case uses.
    static let now = ISO8601DateFormatter().date(from: "2026-09-22T17:00:00Z")!

    /// An ISO 8601 time `hours` from `now` (negative is in the past).
    static func iso(_ hours: Double) -> String {
        ISO8601DateFormatter().string(from: now.addingTimeInterval(hours * 3600))
    }

    typealias Plan = (limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger, targets: [Account])

    /// The engine as AppState calls it, with each account's effective threshold.
    static func plan(active: Account, candidates: [Account], _ usage: [UUID: UsageAPIResponse],
                     defaultThreshold: Double = 90, strategy: AutoSwitchStrategy = .mostRoom,
                     drainEarly: Bool = false, drainWithinHours: Double = 24, watchFable: Bool = true,
                     switchable: (Account) -> Bool = { _ in true }, sampled: Bool = true) -> Plan? {
        AutoSwitchEngine.plan(
            active: active, candidates: candidates, usageByAccount: usage,
            isSwitchable: switchable, activeSampledThisCycle: sampled,
            threshold: { AutoSwitchSettings.effectiveThreshold(own: $0.switchThreshold, defaultThreshold: defaultThreshold) },
            hysteresisPct: 10, watchFable: watchFable, strategy: strategy,
            drainEarly: drainEarly, drainWithin: drainWithinHours * 3600, asOf: now)
    }

    /// "stay", or "<limit>/<trigger>: <targets in order>".
    static func describe(_ p: Plan?) -> String {
        guard let p else { return "stay" }
        return "\(p.limit.rawValue)/\(p.trigger.rawValue): " + p.targets.map(\.displayName).joined(separator: ",")
    }
}
```

At the end of the file, append:

```swift
// MARK: - Switching early to use quota before it resets

@MainActor private func earlyDrainTests() {
    let a = Account(email: "a@x.com", displayName: "A", isActive: true)
    let y = Account(email: "y@x.com", displayName: "Y")
    let y100 = Account(email: "y@x.com", displayName: "Y", switchThreshold: 100)
    let z = Account(email: "z@x.com", displayName: "Z")
    func drain(_ usage: [UUID: UsageAPIResponse], active: Account? = nil, candidates: [Account]? = nil,
               strategy: AutoSwitchStrategy = .resetsSoonest, drainEarly: Bool = true, within: Double = 24,
               watchFable: Bool = true, switchable: (Account) -> Bool = { _ in true }) -> String {
        Rules.describe(Rules.plan(active: active ?? a, candidates: candidates ?? [y], usage, strategy: strategy,
                                  drainEarly: drainEarly, drainWithinHours: within, watchFable: watchFable, switchable: switchable))
    }
    // A is well under its threshold and resets in 100 h.
    let activeA = sample(30, 40)

    // The founder's example: resets in 12 hours with 5% left.
    check(drain([a.id: activeA, y100.id: sample(10, 95, weeklyResetsIn: 12)], candidates: [y100]) == "windows/drainEarly: Y",
          "rules: drain: an account on 100% with 5% left that resets in 12 h is used first")
    check(drain([a.id: activeA, y.id: sample(10, 95, weeklyResetsIn: 12)]) == "stay",
          "rules: drain: the same 95% is past a 90% threshold, so it is not a target")

    let yIn12 = sample(10, 80, weeklyResetsIn: 12)
    check(drain([a.id: activeA, y.id: yIn12]) == "windows/drainEarly: Y", "rules: drain fires for an account resetting in 12 h with room")
    check(drain([a.id: activeA, y.id: sample(10, 80, weeklyResetsIn: 30)]) == "stay", "rules: drain: a reset 30 h away is outside a 24 h window")
    check(drain([a.id: activeA, y.id: sample(10, 80, weeklyResetsIn: 30)], within: 48) == "windows/drainEarly: Y", "rules: drain: ...and inside a 48 h one")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: 10), y.id: yIn12]) == "stay", "rules: drain: never to an account that resets later than the active one")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: 12), y.id: yIn12]) == "stay", "rules: drain: never to one that resets at the same time")
    check(drain([a.id: activeA, y.id: sample(10, 89.5, weeklyResetsIn: 12)]) == "stay", "rules: drain: 89.5% under a 90% threshold is less than 1 point of room")
    check(drain([a.id: activeA, y.id: sample(10, 89, weeklyResetsIn: 12)]) == "windows/drainEarly: Y", "rules: drain: 89% under a 90% threshold is enough room")
    check(drain([a.id: activeA, y.id: sample(95, 20, weeklyResetsIn: 12)]) == "stay", "rules: drain: no room on the 5-hour window means no drain")
    check(drain([a.id: activeA, y.id: yIn12], strategy: .mostRoom) == "stay", "rules: drain: never under most room left")
    check(drain([a.id: activeA, y.id: yIn12], strategy: .myOrder) == "stay", "rules: drain: never under my order")
    check(drain([a.id: activeA, y.id: yIn12], drainEarly: false) == "stay", "rules: drain: never with the switch off")
    check(drain([a.id: activeA, y.id: yIn12], switchable: { $0.id != y.id }) == "stay", "rules: drain: never to an account that cannot be switched to")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: nil), y.id: yIn12]) == "stay", "rules: drain: never when the active account's weekly reset is unknown")
    check(drain([a.id: sample(30, 40, weeklyResetsIn: -1), y.id: yIn12]) == "stay", "rules: drain: never when the active account's sample is from a week that has ended")
    check(drain([a.id: activeA, y.id: sample(10, 5, weeklyResetsIn: -1)]) == "stay", "rules: drain: never to an account whose weekly sample is from a week that has ended")
    check(drain([a.id: activeA]) == "stay", "rules: drain: never to an account with no usage sample yet")

    // Never on Fable: the drain looks at the 7-day window only.
    check(drain([a.id: activeA, y.id: sample(10, 20, fable: 10, fableResetsIn: 5)]) == "stay", "rules: drain: a Fable reset coming up soon never drains")
    // ...but a drain target must not be at its Fable threshold, or the Fable
    // trigger would move you off it and the drain would bring you back.
    check(drain([a.id: activeA, y.id: sample(10, 20, weeklyResetsIn: 12, fable: 95)]) == "stay", "rules: drain: not to an account out of Fable while Fable switching is on")
    check(drain([a.id: activeA, y.id: sample(10, 20, weeklyResetsIn: 12, fable: 95)], watchFable: false) == "windows/drainEarly: Y", "rules: drain: ...but yes with Fable switching off")

    // Two targets: the one that resets first.
    check(drain([a.id: activeA, y.id: yIn12, z.id: sample(10, 50, weeklyResetsIn: 6)], candidates: [y, z]) == "windows/drainEarly: Z,Y",
          "rules: drain ranks targets by the soonest reset")

    // Review focus: a threshold reached with nowhere to go is still "reached": the drain stays out of it.
    check(drain([a.id: sample(92, 40), y.id: sample(10, 85, weeklyResetsIn: 12)]) == "stay",
          "rules: drain never fires once the active account has reached its threshold")
    check(drain([a.id: sample(92, 40), y.id: sample(10, 85, weeklyResetsIn: 12), z.id: sample(10, 70, weeklyResetsIn: 50)], candidates: [y, z]) == "windows/threshold: Z",
          "rules: at the threshold the threshold rule, with its hysteresis, decides")

    // Ping-pong. Drained onto Y; Y reaches its threshold; the threshold rule moves you on...
    let yFull: [UUID: UsageAPIResponse] = [y.id: sample(10, 90, weeklyResetsIn: 12), a.id: activeA]
    let yActive = Account(id: y.id, email: "y@x.com", displayName: "Y", isActive: true)
    let aIdle = Account(id: a.id, email: "a@x.com", displayName: "A")
    check(drain(yFull, active: yActive, candidates: [aIdle]) == "windows/threshold: A",
          "rules: ping-pong: the drained account reaching its threshold moves you on")
    // ...and the drain cannot move you straight back: Y has no room left.
    check(drain(yFull, active: a, candidates: [y]) == "stay",
          "rules: ping-pong: the drain does not bring you back to an account at its threshold")
    // Once Y's 5-hour window resets, its weekly quota still expires first, so
    // using it again is the point of the feature, not a ping-pong.
    let ySessionReset: [UUID: UsageAPIResponse] = [y.id: sample(90, 50, weeklyResetsIn: 12, sessionResetsIn: -0.1), a.id: activeA]
    check(drain(ySessionReset, active: a, candidates: [y]) == "windows/drainEarly: Y",
          "rules: drain uses an account again once the 5-hour window that stopped it has reset")

    // The shared rule AppState verifies with.
    let fresh = sample(10, 85, weeklyResetsIn: 12)
    let reset = Rules.now.addingTimeInterval(100 * 3600)
    let asDrain = AutoSwitchEngine.eligibleUtilization(fresh, limit: .windows, trigger: .drainEarly, threshold: 90, hysteresisPct: 10,
                                                       activeWeeklyReset: reset, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let asThreshold = AutoSwitchEngine.eligibleUtilization(fresh, limit: .windows, trigger: .threshold, threshold: 90, hysteresisPct: 10,
                                                           activeWeeklyReset: nil, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let noActiveReset = AutoSwitchEngine.eligibleUtilization(fresh, limit: .windows, trigger: .drainEarly, threshold: 90, hysteresisPct: 10,
                                                             activeWeeklyReset: nil, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    let onFable = AutoSwitchEngine.eligibleUtilization(fresh, limit: .fable, trigger: .drainEarly, threshold: 90, hysteresisPct: 10,
                                                       activeWeeklyReset: reset, drainWithin: 24 * 3600, watchFable: true, asOf: Rules.now)
    check(asDrain == 85 && asThreshold == nil && noActiveReset == nil && onFable == nil,
          "rules: verification: 85% passes as a drain (1-point room) but not as a threshold switch (10-point), and a drain needs the active reset and the windows limit",
          "\(String(describing: asDrain)) \(String(describing: asThreshold)) \(String(describing: noActiveReset)) \(String(describing: onFable))")
    check(AutoSwitchEngine.drainMinimumRoom == 1.0, "rules: the drain's minimum room is 1 percentage point")
}
```

What the ping-pong cases prove: a drain moves you to account Y because Y's week ends first. When Y reaches its own threshold, the threshold rule moves you on. The drain cannot move you straight back, because Y no longer has 1 point of room. Once the 5-hour window that stopped Y has reset, Y has room again and its week still ends first, so the drain using it again is the feature working. That return is bounded by the 5-hour window and the 300-second cooldown.

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: compile errors including `error: type 'AutoSwitchEngine' has no member 'drainMinimumRoom'` and `error: extra arguments at positions #3, #4, #5, #6, #7, #8 in call`.

- [ ] **Step 3: Implement**

Replace the entire contents of `PixelSwitch/Services/AutoSwitchEngine.swift` with:

```swift
import Foundation

/// How auto-switch picks the next account among the eligible ones
/// (Settings → General → Auto-switch → "Choose the next account by").
enum AutoSwitchStrategy: String, CaseIterable, Codable, Sendable {
    /// Fable room first, then the lowest utilization. Today's behaviour, and the default.
    case mostRoom
    /// The accounts list's own order, always from the top: a higher account
    /// that has freed up again beats the next one down.
    case myOrder
    /// The earliest weekly reset first, so quota about to expire is used
    /// before it is lost. Ties go to most room; an unknown reset ranks last.
    case resetsSoonest
}

/// Pure, UI-agnostic auto-switch decision logic.
///
/// Mirrors the proven design in `claude-swap` (threshold + hysteresis): when the
/// active account's *binding window* (the higher of its 5h / weekly utilization)
/// reaches ITS OWN threshold, pick a same-provider account that sits at least
/// `hysteresisPct` below ITS OWN threshold, so two accounts hovering at the line
/// never ping-pong. Which eligible account comes first is the user's
/// `AutoSwitchStrategy`. All guardrails that need state (cooldown, re-entrancy,
/// verification) live in `AppState`; this stays a pure function.
///
/// Two limits are watched, in order: the session/weekly pair (`.windows`), then
/// the weekly Fable allowance (`.fable`), which runs out on its own schedule.
/// With "Resets soonest" and early switching on, one more rule can move the
/// user before any threshold is reached: see `drainEligibleUtilization`.
enum AutoSwitchEngine {

    /// A limit auto-switch watches.
    enum Limit: String, Sendable {
        /// The 5-hour session and 7-day weekly windows (the higher of the two).
        case windows
        /// The weekly Fable allowance, from the usage response's `limits` list.
        case fable
    }

    /// The model whose weekly allowance `.fable` watches, as the API names it.
    static let fableModelName = "Fable"

    /// How far below its own threshold an early-drain target must sit on every
    /// watched limit, in percentage points. Deliberately not the 10-point
    /// hysteresis: the founder's example is an account with 5% left that
    /// should still be used up before its week resets.
    static let drainMinimumRoom: Double = 1.0

    /// The binding utilization for an account = max of the windows we watch.
    /// We watch the 5-hour (session) and 7-day (weekly-all) windows — the two
    /// that the `/api/oauth/usage` endpoint still populates as top-level fields.
    ///
    /// A window reading whose `resets_at` lies in the past is discarded: accounts
    /// are polled round-robin, so a retained sample can outlive the window it
    /// described — the quota it reports as consumed no longer exists. Within an
    /// unexpired window, consumption only grows, so an unexpired reading is a
    /// valid lower bound even when it is several cycles old.
    ///
    /// `requireKnownWindow` controls what happens when `resets_at` is absent or
    /// unparseable (API omission, format change). Lenient (default) keeps the
    /// reading — right for candidates, where a stale-high reading merely
    /// excludes them and anything chosen is re-verified with a fresh fetch.
    /// Strict discards it — required for the ACTIVE side, where a retained
    /// high reading with no known expiry could otherwise TRIGGER a switch
    /// long after the real window reset.
    ///
    /// Returns nil when no usable reading exists for the account.
    static func bindingUtilization(
        _ usage: UsageAPIResponse?,
        asOf now: Date = Date(),
        requireKnownWindow: Bool = false
    ) -> Double? {
        guard let usage else { return nil }
        return [usage.fiveHour, usage.sevenDay]
            .compactMap { reading($0, asOf: now, requireKnownWindow: requireKnownWindow) }
            .max()
    }

    /// The utilization for `limit`, under the same expiry rules as
    /// `bindingUtilization`. Nil when there is no usable reading, which for
    /// `.fable` includes an account with no separate Fable allowance.
    static func utilization(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        asOf now: Date = Date(),
        requireKnownWindow: Bool = false
    ) -> Double? {
        switch limit {
        case .windows:
            return bindingUtilization(usage, asOf: now, requireKnownWindow: requireKnownWindow)
        case .fable:
            guard let window = usage?.modelWeeklyLimit(named: fableModelName)?.window else { return nil }
            return reading(window, asOf: now, requireKnownWindow: requireKnownWindow)
        }
    }

    /// A candidate's utilization on `limit` when it may be switched to for that
    /// limit, else nil. A switch made for Fable must also leave room on the
    /// session and weekly windows, or the next refresh would move the user
    /// straight off the account it just chose.
    static func eligibleUtilization(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        ceiling: Double,
        asOf now: Date = Date()
    ) -> Double? {
        guard let util = utilization(usage, limit: limit, asOf: now), util <= ceiling else { return nil }
        if limit == .fable {
            guard let windows = bindingUtilization(usage, asOf: now), windows <= ceiling else { return nil }
        }
        return util
    }

    /// The weekly reset "Resets soonest" ranks by: the 7-day window for
    /// `.windows`, the Fable weekly allowance for `.fable`. Never the 5-hour
    /// window, which resets every few hours and would make the choice
    /// meaningless.
    ///
    /// Nil unless that window has a reading AND a reset time still in the
    /// future. A reset in the past means the sample describes a week that has
    /// already ended (round-robin polling keeps old samples), so when the NEXT
    /// week ends is unknown.
    static func weeklyReset(_ usage: UsageAPIResponse?, limit: Limit, asOf now: Date = Date()) -> Date? {
        let window: UsageWindow?
        switch limit {
        case .windows: window = usage?.sevenDay
        case .fable: window = usage?.modelWeeklyLimit(named: fableModelName)?.window
        }
        guard let window, window.utilization != nil,
              let resets = window.resetsAtDate, resets > now else { return nil }
        return resets
    }

    /// A candidate's session/weekly utilization when an early drain may move
    /// the user to it, else nil. All of these must hold:
    /// - its weekly window has a known reset within `drainWithin` of now,
    /// - that reset is strictly earlier than the active account's weekly reset,
    /// - it sits at least `drainMinimumRoom` below its own `threshold` on the
    ///   5-hour and weekly windows,
    /// - and, when Fable switching is on, on Fable too. Without that last rule
    ///   the Fable trigger would move the user straight off the account and
    ///   the drain would bring them back after every cooldown.
    static func drainEligibleUtilization(
        _ usage: UsageAPIResponse?,
        threshold: Double,
        activeWeeklyReset: Date,
        drainWithin: TimeInterval,
        watchFable: Bool,
        asOf now: Date = Date()
    ) -> Double? {
        guard let resets = weeklyReset(usage, limit: .windows, asOf: now),
              resets < activeWeeklyReset,
              resets.timeIntervalSince(now) <= drainWithin else { return nil }
        let ceiling = threshold - drainMinimumRoom
        guard let util = eligibleUtilization(usage, limit: .windows, ceiling: ceiling, asOf: now) else { return nil }
        if watchFable, let fable = utilization(usage, limit: .fable, asOf: now), fable > ceiling { return nil }
        return util
    }

    /// The one rule that decides whether a candidate may be switched to, for
    /// the rule that fired. `plan` ranks with it and `AppState` verifies the
    /// chosen account's fresh reading with it, so ranking and verification
    /// cannot disagree.
    ///
    /// - `threshold` is the CANDIDATE's own effective threshold.
    /// - `.threshold`: at or below its threshold minus `hysteresisPct` on the
    ///   limit that fired (and on session/weekly for a Fable switch).
    /// - `.drainEarly`: `drainEligibleUtilization`; only ever on `.windows`,
    ///   and never without the active account's weekly reset.
    static func eligibleUtilization(
        _ usage: UsageAPIResponse?,
        limit: Limit,
        trigger: Trigger,
        threshold: Double,
        hysteresisPct: Double,
        activeWeeklyReset: Date?,
        drainWithin: TimeInterval,
        watchFable: Bool,
        asOf now: Date = Date()
    ) -> Double? {
        switch trigger {
        case .threshold:
            return eligibleUtilization(usage, limit: limit, ceiling: threshold - hysteresisPct, asOf: now)
        case .drainEarly:
            guard limit == .windows, let activeWeeklyReset else { return nil }
            return drainEligibleUtilization(usage, threshold: threshold, activeWeeklyReset: activeWeeklyReset,
                                            drainWithin: drainWithin, watchFable: watchFable, asOf: now)
        }
    }

    /// One window's utilization, or nil if it has none or its window has reset.
    private static func reading(_ window: UsageWindow?, asOf now: Date, requireKnownWindow: Bool) -> Double? {
        guard let window, let util = window.utilization else { return nil }
        guard let resets = window.resetsAtDate else {
            return requireKnownWindow ? nil : util
        }
        return resets < now ? nil : util
    }

    /// Which limit to act on, by which rule, and where to go; nil to stay put.
    ///
    /// 1. Threshold: session and weekly are checked first, then Fable (only
    ///    when `watchFable`), each against the ACTIVE account's own threshold.
    ///    A Fable target must have session and weekly room anyway.
    /// 2. Early drain: only when no threshold was reached at all (reached but
    ///    with nowhere to go still counts as reached, so the hysteresis stays
    ///    in charge near the limit), the strategy is `.resetsSoonest` and
    ///    `drainEarly` is on. It needs the active account's weekly reset.
    ///
    /// The defaults for `strategy` and `drainEarly` are today's behaviour, so a
    /// caller that passes neither gets exactly the pre-strategy engine.
    ///
    /// - Parameters:
    ///   - active: the currently active account.
    ///   - candidates: every other account (already filtered to the same
    ///     provider), IN THE ACCOUNTS LIST'S ORDER: `.myOrder` ranks by it.
    ///   - usageByAccount: latest usage sample per account id.
    ///   - isSwitchable: whether an account can actually be switched to right now
    ///     (has a stored backup token and isn't flagged expired).
    ///   - activeSampledThisCycle: whether the active account's sample was taken
    ///     by the refresh cycle that is asking. Fresh readings are trusted even
    ///     without a parseable `resets_at`; only RETAINED readings need the
    ///     strict expiry check.
    ///   - threshold: each account's effective switch threshold (its own, else
    ///     the default), 50–100.
    ///   - hysteresisPct: a threshold-triggered candidate must sit at least this
    ///     far below its own threshold (e.g. 10).
    ///   - watchFable: the user's Fable setting.
    ///   - strategy: how eligible candidates are ordered.
    ///   - drainEarly: the "Switch early to use quota before it resets" setting.
    ///   - drainWithin: how soon, in seconds, a candidate's weekly reset must be
    ///     for an early drain.
    ///   - now: injected clock, for window-expiry checks and testability.
    static func plan(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        activeSampledThisCycle: Bool,
        threshold: (Account) -> Double,
        hysteresisPct: Double,
        watchFable: Bool = true,
        strategy: AutoSwitchStrategy = .mostRoom,
        drainEarly: Bool = false,
        drainWithin: TimeInterval = 24 * 3600,
        asOf now: Date = Date()
    ) -> (limit: Limit, trigger: Trigger, targets: [Account])? {
        let activeThreshold = threshold(active)
        var thresholdReached = false
        for limit in (watchFable ? [Limit.windows, .fable] : [Limit.windows]) {
            // Only act once the active account has reached its threshold on a
            // limit we watch. Unknown active usage -> do nothing (can't decide).
            // The trigger must never rest on a RETAINED reading whose expiry we
            // cannot establish — but a reading this very cycle fetched is
            // current whether or not the endpoint sent a parseable resets_at.
            guard let activeUtil = utilization(usageByAccount[active.id], limit: limit, asOf: now,
                                               requireKnownWindow: !activeSampledThisCycle),
                  activeUtil >= activeThreshold else { continue }
            thresholdReached = true
            let targets = rankedTargets(
                active: active, candidates: candidates, usageByAccount: usageByAccount,
                isSwitchable: isSwitchable, threshold: threshold, hysteresisPct: hysteresisPct,
                limit: limit, trigger: .threshold, strategy: strategy,
                activeWeeklyReset: nil, drainWithin: drainWithin, watchFable: watchFable, asOf: now
            )
            if !targets.isEmpty { return (limit, .threshold, targets) }
        }

        guard !thresholdReached, strategy == .resetsSoonest, drainEarly,
              let activeWeeklyReset = weeklyReset(usageByAccount[active.id], limit: .windows, asOf: now) else {
            return nil
        }
        let targets = rankedTargets(
            active: active, candidates: candidates, usageByAccount: usageByAccount,
            isSwitchable: isSwitchable, threshold: threshold, hysteresisPct: hysteresisPct,
            limit: .windows, trigger: .drainEarly, strategy: strategy,
            activeWeeklyReset: activeWeeklyReset, drainWithin: drainWithin, watchFable: watchFable, asOf: now
        )
        return targets.isEmpty ? nil : (.windows, .drainEarly, targets)
    }

    /// Rank the accounts worth switching to for one limit and rule, best first.
    ///
    /// The result is a list of *proposals*, not decisions: it is computed from
    /// whatever samples the caller happens to hold, which round-robin polling can
    /// leave several cycles old. `AppState` verifies a candidate's usage before
    /// committing to a switch, and falls through the list when one fails.
    ///
    /// A candidate with no usable reading is NOT eligible: accounts are polled
    /// round-robin, so "no sample" usually means "not reached yet" rather than
    /// "idle" — treating it as a fallback let an automatic switch land on an
    /// account that was itself maxed out.
    static func rankedTargets(
        active: Account,
        candidates: [Account],
        usageByAccount: [UUID: UsageAPIResponse],
        isSwitchable: (Account) -> Bool,
        threshold: (Account) -> Double,
        hysteresisPct: Double,
        limit: Limit,
        trigger: Trigger,
        strategy: AutoSwitchStrategy,
        activeWeeklyReset: Date?,
        drainWithin: TimeInterval,
        watchFable: Bool,
        asOf now: Date = Date()
    ) -> [Account] {
        struct Ranked {
            let account: Account
            /// Position in `candidates`, i.e. in the user's order.
            let position: Int
            let util: Double
            let keepsFable: Bool
            /// The weekly reset in whole minutes since 1970, or nil when unknown.
            /// Whole minutes because the API stamps each reading with the
            /// fetch's own microseconds, so two accounts on the same weekly
            /// schedule never compare exactly equal.
            let resetMinute: Double?
        }

        let eligible = candidates.enumerated().compactMap { position, candidate -> Ranked? in
            let usage = usageByAccount[candidate.id]
            let own = threshold(candidate)
            // The usage check comes first: `isSwitchable` reads the Keychain.
            guard candidate.id != active.id,
                  let util = eligibleUtilization(usage, limit: limit, trigger: trigger, threshold: own,
                                                 hysteresisPct: hysteresisPct, activeWeeklyReset: activeWeeklyReset,
                                                 drainWithin: drainWithin, watchFable: watchFable, asOf: now),
                  isSwitchable(candidate) else { return nil }
            let keepsFable = utilization(usage, limit: .fable, asOf: now).map { $0 <= own - hysteresisPct } ?? false
            let resetMinute = weeklyReset(usage, limit: limit, asOf: now)
                .map { ($0.timeIntervalSince1970 / 60).rounded(.down) }
            return Ranked(account: candidate, position: position, util: util,
                          keepsFable: keepsFable, resetMinute: resetMinute)
        }

        let ordered: [Ranked]
        switch strategy {
        case .mostRoom:
            // Accounts with Fable to spare first, so a session/weekly switch
            // does not land on an account that is out of Fable and force a
            // second switch minutes later; then most headroom first (lowest
            // known utilization); then the user's order, so a tie is stable.
            ordered = eligible.sorted {
                ($0.keepsFable ? 0 : 1, $0.util, $0.position) < ($1.keepsFable ? 0 : 1, $1.util, $1.position)
            }
        case .myOrder:
            // Already in the user's order: always from the top.
            ordered = eligible
        case .resetsSoonest:
            ordered = eligible.sorted { a, b in
                switch (a.resetMinute, b.resetMinute) {
                case let (x?, y?) where x != y: return x < y
                case (.some, nil): return true
                case (nil, .some): return false
                default: return (a.util, a.position) < (b.util, b.position)
                }
            }
        }
        return ordered.map(\.account)
    }
}

extension AutoSwitchEngine {
    /// The rule that produced a plan: the active account reached its
    /// threshold, or (Resets soonest only) another account's weekly quota is
    /// about to reset unused.
    enum Trigger: String, Sendable { case threshold, drainEarly }
}

/// Whether auto-switch also acts on the weekly Fable allowance. On by default;
/// off leaves Fable as a reading only, and session and weekly switching alone.
enum AutoSwitchFableSetting {
    static let key = "autoSwitchOnFable"

    static var isOn: Bool {
        UserDefaults.standard.object(forKey: key) as? Bool ?? true
    }
}

/// The auto-switch settings, their UserDefaults keys, defaults and ranges.
/// The Settings window binds to the keys with `@AppStorage`; `AppState` and
/// (later) the control API read them through the typed readers here, so the
/// defaults live in one place.
enum AutoSwitchSettings {
    static let enabledKey = "autoSwitchEnabled"
    static let thresholdKey = "autoSwitchThreshold"
    static let onFableKey = AutoSwitchFableSetting.key
    static let strategyKey = "autoSwitchStrategy"
    static let drainEarlyKey = "autoSwitchDrainEarly"
    static let drainWithinHoursKey = "autoSwitchDrainWithinHours"

    /// A switch threshold, global or per account, in percent.
    static let thresholdRange = 50.0...100.0
    /// How soon a weekly reset must be for an early drain, in hours.
    static let drainHoursRange = 1.0...72.0

    /// The default threshold when none has been saved.
    static let fallbackThreshold = 90.0
    /// The drain window when none has been saved.
    static let fallbackDrainWithinHours = 24.0

    /// The global default threshold (Settings → General → Default threshold).
    /// Unset (read as 0) means 90, as before; anything else is kept in range.
    static var defaultThreshold: Double {
        let stored = UserDefaults.standard.double(forKey: thresholdKey)
        return stored == 0 ? fallbackThreshold : clampedThreshold(stored)
    }

    /// The next-account strategy. Unset or unrecognised reads as `.mostRoom`.
    static var strategy: AutoSwitchStrategy {
        UserDefaults.standard.string(forKey: strategyKey).flatMap(AutoSwitchStrategy.init(rawValue:)) ?? .mostRoom
    }

    /// "Switch early to use quota before it resets". On unless turned off.
    static var drainEarly: Bool {
        UserDefaults.standard.object(forKey: drainEarlyKey) as? Bool ?? true
    }

    /// The early-drain window in hours, kept in 1–72. Unset reads as 24.
    static var drainWithinHours: Double {
        guard let stored = UserDefaults.standard.object(forKey: drainWithinHoursKey) as? Double, stored.isFinite else {
            return fallbackDrainWithinHours
        }
        return min(max(stored, drainHoursRange.lowerBound), drainHoursRange.upperBound)
    }

    /// `value` kept within 50–100; a value that is not a number reads as 90.
    static func clampedThreshold(_ value: Double) -> Double {
        guard value.isFinite else { return fallbackThreshold }
        return min(max(value, thresholdRange.lowerBound), thresholdRange.upperBound)
    }

    /// What a per-account threshold is stored as: nil (use the default) for
    /// nil or a value that is not a number, else the value kept within 50–100.
    static func normalizedAccountThreshold(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        return clampedThreshold(value)
    }

    /// The threshold that applies to an account: its own when set, else the
    /// default, always within 50–100.
    static func effectiveThreshold(own: Double?, defaultThreshold: Double) -> Double {
        normalizedAccountThreshold(own) ?? clampedThreshold(defaultThreshold)
    }
}
```

Compared with the end of Task 3, this adds:
- the type's doc comment paragraph on early switching;
- `drainMinimumRoom`;
- `drainEligibleUtilization` and the trigger-aware `eligibleUtilization` overload (both before `reading`);
- in `plan`, the `thresholdReached` flag and the drain branch;
- in `rankedTargets`, the `trigger`, `activeWeeklyReset`, `drainWithin` and `watchFable` parameters, and the switch to the shared rule;
- `keepsFable` now uses `own - hysteresisPct`, the same value as before.

`Limit`, `bindingUtilization`, `utilization`, `eligibleUtilization(_:limit:ceiling:asOf:)`, `weeklyReset`, `reading`, `Trigger`, `AutoSwitchFableSetting` and `AutoSwitchSettings` are unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `192 passed, 0 failed`.

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK (48 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Services/AutoSwitchEngine.swift Tests/UnitTests/AutoSwitchRulesTests.swift
git commit -m "$(cat <<'EOF'
Auto-switch engine: switch early to use quota before it resets

With Resets soonest and early switching on, and no threshold reached,
plan() moves to an account whose weekly quota resets within the window and
before the active account's, with at least 1 point of room under its own
threshold on every watched limit. One shared rule ranks and verifies.
Ping-pong cases are pinned.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The priority order and per-account thresholds on AppState

**Files:**
- Create: `PixelSwitch/Models/AccountOrder.swift`
- Modify: `PixelSwitch/AppState.swift`: insert a new section after `updateAccountLabel(_:label:)` (originally ending at line 419)
- Modify: `Tests/run-unit-tests.sh` (add `AccountOrder.swift`)
- Modify: `Tests/UnitTests/AutoSwitchRulesTests.swift`

**Interfaces:**
- Consumes: `AutoSwitchSettings.effectiveThreshold(own:defaultThreshold:)`, `AutoSwitchSettings.normalizedAccountThreshold(_:)`, `AutoSwitchSettings.defaultThreshold` (Task 1).
- Produces:
  - `enum AccountOrder { static func reordered(_ accounts: [Account], by orderedIds: [UUID]) -> [Account]?; static func moving<Element>(_ items: [Element], fromOffsets source: IndexSet, toOffset destination: Int) -> [Element] }`
  - On `AppState`, exactly as spec section 9 names them: `func effectiveSwitchThreshold(for account: Account) -> Double`, `func setSwitchThreshold(_ threshold: Double?, for account: Account)`, `@discardableResult func setAccountOrder(_ orderedIds: [UUID]) -> Bool`, `func moveAccounts(fromOffsets source: IndexSet, toOffset destination: Int)`.

- [ ] **Step 1: Write the failing test**

In `Tests/UnitTests/AutoSwitchRulesTests.swift`, replace `runAutoSwitchRulesTests()` with:

```swift
@MainActor func runAutoSwitchRulesTests() {
    autoSwitchSettingsTests()
    perAccountThresholdTests()
    strategyTests()
    earlyDrainTests()
    accountOrderTests()
}
```

At the end of the file, append:

```swift
// MARK: - The accounts list's order

@MainActor private func accountOrderTests() {
    let a = Account(email: "a@x.com", displayName: "A")
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    let d = Account(email: "d@x.com", displayName: "D")
    let removed = Account(email: "gone@x.com", displayName: "Gone")
    let list = [a, b, c, d]
    func names(_ accounts: [Account]?) -> String { accounts.map { $0.map(\.displayName).joined() } ?? "refused" }

    check(names(AccountOrder.reordered(list, by: [c.id, a.id, d.id, b.id])) == "CADB", "order: a full permutation is applied")
    check(names(AccountOrder.reordered(list, by: list.map(\.id))) == "ABCD", "order: the same order is accepted unchanged")
    check(names(AccountOrder.reordered(list, by: [a.id, b.id, c.id])) == "refused", "order: leaving an account out is refused")
    check(names(AccountOrder.reordered(list, by: [a.id, b.id, c.id, removed.id])) == "refused", "order: naming a removed account is refused")
    check(names(AccountOrder.reordered(list, by: [a.id, b.id, c.id, d.id, removed.id])) == "refused", "order: an extra account is refused")
    check(names(AccountOrder.reordered(list, by: [a.id, a.id, c.id, d.id])) == "refused", "order: repeating an account is refused")
    check(names(AccountOrder.reordered([], by: [])) == "", "order: no accounts and an empty order is fine")

    func moved(_ from: [Int], to: Int) -> String { AccountOrder.moving(list, fromOffsets: IndexSet(from), toOffset: to).map(\.displayName).joined() }
    check(moved([0], to: 3) == "BCAD", "order: dragging the first account down to above the last", moved([0], to: 3))
    check(moved([3], to: 0) == "DABC", "order: dragging the last account to the top", moved([3], to: 0))
    check(moved([1, 2], to: 4) == "ADBC", "order: dragging two accounts to the bottom", moved([1, 2], to: 4))
    check(moved([1], to: 2) == "ABCD" && moved([1], to: 1) == "ABCD", "order: dropping an account where it already is changes nothing")
    check(moved([9], to: 0) == "ABCD", "order: an offset outside the list is ignored")
    check(moved([0], to: 99) == "BCDA", "order: a destination past the end moves to the bottom")
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash Tests/run-unit-tests.sh 2>&1 | grep error: | head -3`
Expected: `error: cannot find 'AccountOrder' in scope`.

- [ ] **Step 3: Implement**

Create `PixelSwitch/Models/AccountOrder.swift`:

```swift
import Foundation

/// The accounts list's order is the user's priority order ("My order"), and
/// every list in the app shows it. These are the two ways to change it, kept
/// pure so the unit harness can pin them.
enum AccountOrder {

    /// `accounts` rearranged into the order `orderedIds` gives, or nil unless
    /// `orderedIds` names every current account exactly once and nothing else.
    /// An order that leaves an account out, repeats one, or names one that has
    /// been removed is refused as a whole rather than half applied.
    static func reordered(_ accounts: [Account], by orderedIds: [UUID]) -> [Account]? {
        let currentIds = accounts.map(\.id)
        guard orderedIds.count == currentIds.count,
              Set(orderedIds).count == orderedIds.count,
              Set(currentIds).count == currentIds.count,
              Set(orderedIds) == Set(currentIds) else { return nil }
        let byId = Dictionary(accounts.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return orderedIds.compactMap { byId[$0] }
    }

    /// `items` with the elements at `source` moved to `destination`, with the
    /// meaning SwiftUI's `onMove` gives them: `destination` is an index in the
    /// list BEFORE the move. Offsets outside the list are ignored, and a
    /// destination outside it is clamped.
    static func moving<Element>(_ items: [Element], fromOffsets source: IndexSet, toOffset destination: Int) -> [Element] {
        let valid = source.filter { items.indices.contains($0) }
        guard !valid.isEmpty else { return items }
        let moved = valid.map { items[$0] }
        let kept = items.indices.filter { !valid.contains($0) }.map { items[$0] }
        let clamped = min(max(destination, 0), items.count)
        let insertAt = clamped - valid.filter { $0 < clamped }.count
        var result = kept
        result.insert(contentsOf: moved, at: insertAt)
        return result
    }
}
```

In `Tests/run-unit-tests.sh`, replace the line `  PixelSwitch/Models/Account.swift \` with:

```bash
  PixelSwitch/Models/Account.swift \
  PixelSwitch/Models/AccountOrder.swift \
```

In `PixelSwitch/AppState.swift`, directly after the closing `}` of `func updateAccountLabel(_ account: Account, label: String?)` and before `func removeAccount(_ account: Account) {`, insert:

```swift
    // MARK: - Per-account threshold and priority order

    /// The threshold auto-switch applies to `account`: its own when set, else
    /// the default from Settings → General. Read from `accounts` by id, so a
    /// stale copy (such as `activeAccount` held across an await) still gets
    /// the value saved most recently.
    func effectiveSwitchThreshold(for account: Account) -> Double {
        let stored = accounts.first(where: { $0.id == account.id }) ?? account
        return AutoSwitchSettings.effectiveThreshold(own: stored.switchThreshold,
                                                     defaultThreshold: AutoSwitchSettings.defaultThreshold)
    }

    /// Sets `account`'s own threshold, kept within 50–100; nil (or a value that
    /// is not a number) clears it back to the default. Saves.
    func setSwitchThreshold(_ threshold: Double?, for account: Account) {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else {
            log.warning("[setSwitchThreshold] No account \(account.id)")
            return
        }
        let value = AutoSwitchSettings.normalizedAccountThreshold(threshold)
        guard accounts[index].switchThreshold != value else { return }
        accounts[index].switchThreshold = value
        if accounts[index].isActive {
            activeAccount = accounts[index]
        }
        saveAccounts()
        log.info("[setSwitchThreshold] \(account.id): \(value.map { String(format: "%.0f%%", $0) } ?? "default")")
    }

    /// Puts the accounts list in the order `orderedIds` gives. That order is
    /// the priority "My order" follows and the order every list shows.
    /// Returns false, and changes nothing, unless `orderedIds` names every
    /// current account exactly once.
    @discardableResult
    func setAccountOrder(_ orderedIds: [UUID]) -> Bool {
        guard let reordered = AccountOrder.reordered(accounts, by: orderedIds) else {
            log.warning("[setAccountOrder] Refused: the order does not name each of the \(self.accounts.count) accounts exactly once")
            return false
        }
        applyOrder(reordered)
        return true
    }

    /// SwiftUI's `onMove` for the Settings → Accounts list. Saves.
    func moveAccounts(fromOffsets source: IndexSet, toOffset destination: Int) {
        applyOrder(AccountOrder.moving(accounts, fromOffsets: source, toOffset: destination))
    }

    private func applyOrder(_ reordered: [Account]) {
        guard reordered.map(\.id) != accounts.map(\.id) else { return }
        accounts = reordered
        // The widget lists accounts in this order too.
        saveAccounts(refreshWidget: true)
        log.info("[order] Accounts reordered: \(reordered.map { $0.id.uuidString.prefix(8) }.joined(separator: ", "))")
    }
```

New accounts keep going to the bottom: `addAccount`, `loginNewAccount` and `updateActiveAccount(from:)` already use `accounts.append(...)`. Leave them alone. `removeAccount` already makes the first remaining account active when the active one is removed, which is now the top of the user's order.

The popover follows the new order with no change. `AccountSwitcherView` (line 22) and `UsageDashboardView` (line 71) both iterate `ForEach(appState.accounts)`, and `updateWidgetData()` maps `accounts` in order, which is why `applyOrder` saves with `refreshWidget: true`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `205 passed, 0 failed`.

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: XcodeGen prints `Created project at .../PixelSwitch.xcodeproj`, then `app: type-check OK (49 files)`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Models/AccountOrder.swift PixelSwitch/AppState.swift \
        Tests/run-unit-tests.sh Tests/UnitTests/AutoSwitchRulesTests.swift
git commit -m "$(cat <<'EOF'
Accounts: priority order and per-account thresholds on AppState

effectiveSwitchThreshold(for:), setSwitchThreshold(_:for:),
setAccountOrder(_:) and moveAccounts(fromOffsets:toOffset:), as spec
section 9 names them. An order that leaves out, repeats or names a removed
account is refused whole. The pure AccountOrder helper is unit-tested.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire the rules into AppState's auto-switch

**Files:**
- Modify: `PixelSwitch/AppState.swift`:
  - the auto-switch properties (originally lines 93–113, from `/// Whether proactive auto-switch is on` to `private let autoSwitchHysteresis: Double = 10`);
  - `lastAutoSwitchAt` / `isEvaluatingAutoSwitch` (originally lines 118–119);
  - `evaluateAutoSwitch()` with its doc comment (originally lines 730–819, shifted by Tasks 2 and 5);
  - the end of the file (new top-level `AutoSwitchRecord`).

**Interfaces:**
- Consumes: `AutoSwitchSettings.strategy`, `.drainEarly`, `.drainWithinHours`, `.enabledKey` (Task 1); `AutoSwitchEngine.plan(... strategy:drainEarly:drainWithin:)`, `AutoSwitchEngine.eligibleUtilization(_:limit:trigger:threshold:hysteresisPct:activeWeeklyReset:drainWithin:watchFable:asOf:)`, `AutoSwitchEngine.weeklyReset(_:limit:asOf:)` (Tasks 3–4); `effectiveSwitchThreshold(for:)` (Task 5).
- Produces: `@Published private(set) var lastAutoSwitch: AutoSwitchRecord?` and `struct AutoSwitchRecord: Equatable, Sendable { let from: UUID; let to: UUID; let limit: AutoSwitchEngine.Limit; let trigger: AutoSwitchEngine.Trigger; let at: Date }`. Part C streams it as the "auto-switched" event.

`AppState` cannot be compiled into the unit harness (it imports SwiftUI and WidgetKit and uses `FileLog`), so this task has no new harness test. What it depends on is already pinned: Task 4's "verification" case proves the shared rule accepts a drain candidate at 85% under a 90% threshold and rejects the same reading as a threshold switch, needs the active account's reset, and refuses the Fable limit for a drain. The gates here are the type-check, the full suite, and the reviewer checklist in Step 3.

- [ ] **Step 1: Confirm the rule this task relies on is pinned**

Run: `bash Tests/run-unit-tests.sh | grep -c "PASS  rules: verification"`
Expected: `1`.

- [ ] **Step 2: Implement**

In `PixelSwitch/AppState.swift`, replace the block from `    /// Whether proactive auto-switch is on (written by SettingsView via @AppStorage).` down to and including `    private let autoSwitchHysteresis: Double = 10` with:

```swift
    /// Whether proactive auto-switch is on (written by SettingsView via @AppStorage).
    private var autoSwitchEnabled: Bool {
        UserDefaults.standard.bool(forKey: AutoSwitchSettings.enabledKey)
    }

    /// Settings → Auto-switch. On (the default) means the weekly Fable
    /// allowance can move the user too; off leaves session and weekly untouched
    /// and Fable purely informational.
    private var autoSwitchOnFable: Bool {
        AutoSwitchFableSetting.isOn
    }

    /// A threshold-triggered candidate must sit at least this far below its
    /// own threshold to be eligible, so two accounts hovering at the line
    /// never ping-pong. (An early drain uses `AutoSwitchEngine.drainMinimumRoom`.)
    private let autoSwitchHysteresis: Double = 10
```

This removes the private `autoSwitchThreshold` property. Its one reader, `evaluateAutoSwitch`, is replaced next and uses `effectiveSwitchThreshold(for:)` instead.

Replace:

```swift
    private var lastAutoSwitchAt: Date?
    private var isEvaluatingAutoSwitch = false
```

with:

```swift
    private var lastAutoSwitchAt: Date?
    private var isEvaluatingAutoSwitch = false

    /// The most recent automatic switch that completed in this launch, with
    /// the limit and the rule that fired. Nil until one happens. The control
    /// API (Part C) streams it as an event.
    @Published private(set) var lastAutoSwitch: AutoSwitchRecord?
```

Replace the whole of `evaluateAutoSwitch()`, from its doc comment line `    /// Evaluate whether the active account has reached the threshold on a watched` down to its closing `    }` (the line before `    /// Take one fresh usage reading for an account right now,`), with:

```swift
    /// Evaluate whether auto-switch should move the user, and if so where.
    /// Called after every completed refresh. Safe to call repeatedly.
    ///
    /// Two rules can fire (`AutoSwitchEngine.plan`): the active account
    /// reaching ITS OWN threshold on a watched limit (session/weekly, then
    /// Fable), or, with "Resets soonest" and early switching on, another
    /// account's weekly quota about to reset unused. Candidates are ordered by
    /// the user's strategy.
    ///
    /// Candidates are ranked from whatever samples we hold, then the chosen one
    /// is VERIFIED before committing, with the same rule that ranked it:
    /// round-robin polling can leave a candidate's sample several cycles old,
    /// and quota may have been consumed on it from another device meanwhile. A
    /// sample taken by this very cycle counts as verified; otherwise one fresh
    /// reading is taken — a single request per (rare, rule-gated,
    /// cooldown-gated) switch attempt, not the per-cycle burst the round-robin
    /// exists to prevent.
    private func evaluateAutoSwitch() async {
        guard autoSwitchEnabled, !isEvaluatingAutoSwitch else { return }
        guard !isLoggingIn, !isSwitching, let active = activeAccount else { return }

        // Cooldown: never auto-switch more than once per window.
        if let last = lastAutoSwitchAt, Date().timeIntervalSince(last) < autoSwitchCooldown {
            return
        }

        // Only consider same-provider accounts (a Claude switch never touches
        // Codex/Gemini), in the accounts list's order: "My order" ranks by it.
        let candidates = accounts.filter { $0.provider == active.provider && $0.id != active.id }
        let activeSampledThisCycle = (accountUsageSampledAt[active.id] ?? .distantPast) >= lastCycleStart
        let strategy = AutoSwitchSettings.strategy
        let drainEarly = AutoSwitchSettings.drainEarly
        let drainWithin = AutoSwitchSettings.drainWithinHours * 3600
        let watchFable = autoSwitchOnFable
        guard let plan = AutoSwitchEngine.plan(
            active: active,
            candidates: candidates,
            usageByAccount: accountUsage,
            isSwitchable: { [unowned self] in self.isSwitchable($0) },
            activeSampledThisCycle: activeSampledThisCycle,
            threshold: { [unowned self] in self.effectiveSwitchThreshold(for: $0) },
            hysteresisPct: autoSwitchHysteresis,
            watchFable: watchFable,
            strategy: strategy,
            drainEarly: drainEarly,
            drainWithin: drainWithin
        ) else { return }
        let limit = plan.limit
        let trigger = plan.trigger
        let ranked = plan.targets
        // The active account's weekly reset as the plan saw it. A drain target
        // must still reset before it when its fresh reading is checked.
        let activeWeeklyReset = AutoSwitchEngine.weeklyReset(accountUsage[active.id], limit: .windows)

        isEvaluatingAutoSwitch = true
        defer { isEvaluatingAutoSwitch = false }

        let activeUtil = AutoSwitchEngine.utilization(accountUsage[active.id], limit: limit) ?? -1
        let activeThreshold = effectiveSwitchThreshold(for: active)
        log.info("[autoSwitch] Rule \(trigger.rawValue), strategy \(strategy.rawValue): active \(active.id) at \(String(format: "%.0f", activeUtil))% on \(limit.rawValue) (its threshold \(String(format: "%.0f", activeThreshold))%); \(ranked.count) candidate(s)")

        // At most ONE fresh verification request per evaluation. Later ranked
        // candidates only qualify via samples this cycle already took.
        var freshRequestBudget = 1
        for target in ranked {
            let usage: UsageAPIResponse?
            if let sampledAt = accountUsageSampledAt[target.id], sampledAt >= lastCycleStart {
                // Sampled by this very cycle — that IS a fresh reading.
                usage = accountUsage[target.id]
            } else if freshRequestBudget > 0 {
                freshRequestBudget -= 1
                usage = await fetchUsageNow(for: target)
                if let usage {
                    accountUsage[target.id] = usage
                    accountUsageSampledAt[target.id] = Date()
                    accountUsageErrors[target.id] = nil
                }
            } else {
                continue
            }

            // The same rule, with the same per-account threshold, that ranked it.
            let targetThreshold = effectiveSwitchThreshold(for: target)
            guard let verifiedUtil = AutoSwitchEngine.eligibleUtilization(
                usage, limit: limit, trigger: trigger,
                threshold: targetThreshold, hysteresisPct: autoSwitchHysteresis,
                activeWeeklyReset: activeWeeklyReset, drainWithin: drainWithin, watchFable: watchFable
            ) else {
                let onLimit = AutoSwitchEngine.utilization(usage, limit: limit).map { String(format: "%.0f%%", $0) } ?? "no reading"
                let onWindows = AutoSwitchEngine.bindingUtilization(usage).map { String(format: "%.0f%%", $0) } ?? "no reading"
                log.info("[autoSwitch] Candidate \(target.id) failed verification for \(trigger.rawValue) (\(limit.rawValue) \(onLimit), windows \(onWindows), its threshold \(String(format: "%.0f", targetThreshold))%); trying next")
                continue
            }

            // Re-check volatile state: the verification await above can span a
            // login starting, a timer-tick refresh beginning, or a manual switch
            // the user just clicked (which updates `activeAccount` only after
            // its subprocess work finishes — hence the explicit isSwitching).
            guard !isLoggingIn, !isRefreshing, !isSwitching, activeAccount?.id == active.id else {
                log.info("[autoSwitch] State changed during verification; standing down")
                return
            }

            log.info("[autoSwitch] Switching to \(target.id) by rule \(trigger.rawValue) on \(limit.rawValue), verified at \(String(format: "%.0f", verifiedUtil))%")
            lastAutoSwitchAt = Date()
            // switchTo() calls refresh() -> evaluateAutoSwitch() again, but the
            // re-entrancy flag + the freshly-set cooldown make that a no-op.
            // The active account visibly changes in the menu bar as feedback.
            await switchTo(target)
            if activeAccount?.id == target.id {
                lastAutoSwitch = AutoSwitchRecord(from: active.id, to: target.id, limit: limit, trigger: trigger, at: Date())
            } else {
                log.warning("[autoSwitch] Switch to \(target.id) did not complete")
            }
            return
        }
        switch trigger {
        case .threshold: log.info("[autoSwitch] Threshold reached but no candidate verified; staying put")
        case .drainEarly: log.info("[autoSwitch] Early drain found no verified candidate; staying put")
        }
    }
```

At the very end of the file, after the closing `}` of `final class AppState`, add a blank line and then:

```swift
/// One automatic switch that completed: from which account to which, on which
/// limit, by which rule, and when. Published as `AppState.lastAutoSwitch`.
struct AutoSwitchRecord: Equatable, Sendable {
    let from: UUID; let to: UUID
    let limit: AutoSwitchEngine.Limit; let trigger: AutoSwitchEngine.Trigger
    let at: Date
}
```

- [ ] **Step 3: Verify**

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK (49 files)`.

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `205 passed, 0 failed`.

Run: `grep -n "autoSwitchThreshold\|\"autoSwitch" PixelSwitch/AppState.swift`
Expected: no output. `AppState` reads every auto-switch setting through `AutoSwitchSettings` or `AutoSwitchFableSetting`.

Reviewer checklist, by reading the diff:
- The verification uses `effectiveSwitchThreshold(for: target)`, the target's own threshold, with the same `trigger`, `limit`, `drainWithin` and `watchFable` the plan used, and `activeWeeklyReset` read from the same `accountUsage[active.id]`.
- The 300-second cooldown guard, the `isEvaluatingAutoSwitch` re-entrancy flag, the one-fresh-request budget and the "state changed during verification" re-check are unchanged.
- `lastAutoSwitch` is set only when `activeAccount?.id == target.id` after `switchTo`, so a failed switch never publishes a record.
- The log names the rule and the strategy: `[autoSwitch] Rule threshold, strategy mostRoom: ...` or `Rule drainEarly, strategy resetsSoonest: ...`.

- [ ] **Step 4: Commit**

```bash
git add PixelSwitch/AppState.swift
git commit -m "$(cat <<'EOF'
Auto-switch: per-account thresholds, strategy and early drain in AppState

evaluateAutoSwitch passes each account's effective threshold, the strategy
and the drain settings to the engine, verifies the chosen account with the
same shared rule and its own threshold, logs which rule fired, and
publishes lastAutoSwitch (AutoSwitchRecord) when an automatic switch lands.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Settings → General: default threshold, strategy picker, early switching

**Files:**
- Modify: `PixelSwitch/Views/SettingsView.swift:14-16` (the `@AppStorage` lines), `:78-96` (the `Section("Auto-switch")`), `:275` (a new helper under `// MARK: - Helpers`)
- Modify: `PixelSwitch/en.lproj/Localizable.strings`, `PixelSwitch/zh-Hans.lproj/Localizable.strings`, `PixelSwitch/ja.lproj/Localizable.strings`, `PixelSwitch/de.lproj/Localizable.strings`, `PixelSwitch/fr.lproj/Localizable.strings` (append a block to each)

**Interfaces:**
- Consumes: `AutoSwitchSettings` keys, ranges and fallbacks (Task 1); `AutoSwitchStrategy` (Task 1).
- Produces: UI only. `@AppStorage` writes the keys that `AutoSwitchSettings` reads, so `AppState` picks the change up on the next evaluation with no other wiring.

SwiftUI views cannot run in the unit harness. The gates are the type-check, the string-file lint and key parity, and the manual checks collected in Task 9.

- [ ] **Step 1: Implement the view**

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
    @AppStorage("autoSwitchEnabled") private var autoSwitchEnabled = false
    @AppStorage("autoSwitchThreshold") private var autoSwitchThreshold = 90.0
    @AppStorage(AutoSwitchFableSetting.key) private var autoSwitchOnFable = true
```

with:

```swift
    @AppStorage(AutoSwitchSettings.enabledKey) private var autoSwitchEnabled = false
    @AppStorage(AutoSwitchSettings.thresholdKey) private var autoSwitchThreshold = AutoSwitchSettings.fallbackThreshold
    @AppStorage(AutoSwitchSettings.onFableKey) private var autoSwitchOnFable = true
    @AppStorage(AutoSwitchSettings.strategyKey) private var autoSwitchStrategy: AutoSwitchStrategy = .mostRoom
    @AppStorage(AutoSwitchSettings.drainEarlyKey) private var autoSwitchDrainEarly = true
    @AppStorage(AutoSwitchSettings.drainWithinHoursKey) private var autoSwitchDrainWithinHours = AutoSwitchSettings.fallbackDrainWithinHours
```

Replace the whole `Section("Auto-switch") { ... }` (from `            Section("Auto-switch") {` down to its closing brace, the line before `            Section("Account display") {`) with:

```swift
            Section("Auto-switch") {
                Toggle("Switch account before hitting the limit", isOn: $autoSwitchEnabled)
                if autoSwitchEnabled {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("Default threshold")
                            Spacer()
                            Text("\(Int(autoSwitchThreshold))%")
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                        Slider(value: $autoSwitchThreshold, in: AutoSwitchSettings.thresholdRange, step: 1)
                        Text("Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Toggle("Also switch when Fable runs out", isOn: $autoSwitchOnFable)
                    Picker("Choose the next account by", selection: $autoSwitchStrategy) {
                        Text("Most room left").tag(AutoSwitchStrategy.mostRoom)
                        Text("My order").tag(AutoSwitchStrategy.myOrder)
                        Text("Resets soonest (use quota before it expires)").tag(AutoSwitchStrategy.resetsSoonest)
                    }
                    Text(strategyExplanation)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if autoSwitchStrategy == .resetsSoonest {
                        Toggle("Switch early to use quota before it resets", isOn: $autoSwitchDrainEarly)
                        if autoSwitchDrainEarly {
                            Stepper(value: $autoSwitchDrainWithinHours, in: AutoSwitchSettings.drainHoursRange, step: 1) {
                                Text("Within \(Int(autoSwitchDrainWithinHours)) hours")
                                    .monospacedDigit()
                            }
                            Text("Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Text("When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
```

Directly after the line `    // MARK: - Helpers`, insert:

```swift
    /// One line under the strategy picker saying what the chosen strategy does.
    private var strategyExplanation: LocalizedStringKey {
        switch autoSwitchStrategy {
        case .mostRoom:
            return "Moves you to the account with the most room left, preferring one that still has Fable to spare."
        case .myOrder:
            return "Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top."
        case .resetsSoonest:
            return "Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost."
        }
    }
```

- [ ] **Step 2: Add the strings in all five languages**

Run each block exactly as written. The quoted heredoc (`<<'EOF'`) keeps every backslash and `%` literal.

```bash
cat >> PixelSwitch/en.lproj/Localizable.strings <<'EOF'

/* Auto-switch rules: Settings → General (1.2) */
"Default threshold" = "Default threshold";
"Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty." = "Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty.";
"Choose the next account by" = "Choose the next account by";
"Most room left" = "Most room left";
"My order" = "My order";
"Resets soonest (use quota before it expires)" = "Resets soonest (use quota before it expires)";
"Moves you to the account with the most room left, preferring one that still has Fable to spare." = "Moves you to the account with the most room left, preferring one that still has Fable to spare.";
"Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top." = "Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top.";
"Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost." = "Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost.";
"Switch early to use quota before it resets" = "Switch early to use quota before it resets";
"Within %lld hours" = "Within %lld hours";
"Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room." = "Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room.";
"When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping." = "When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping.";
EOF
cat >> PixelSwitch/zh-Hans.lproj/Localizable.strings <<'EOF'

/* Auto-switch rules: Settings → General (1.2) */
"Default threshold" = "默认阈值";
"Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty." = "每个账户都可以在设置 → 账户中设置自己的阈值。设为 100% 表示用完为止。";
"Choose the next account by" = "下一个账户的选择方式";
"Most room left" = "剩余额度最多";
"My order" = "我的顺序";
"Resets soonest (use quota before it expires)" = "最早重置（在额度过期前用完）";
"Moves you to the account with the most room left, preferring one that still has Fable to spare." = "切换到剩余额度最多的账户，并优先选择仍有 Fable 余量的账户。";
"Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top." = "切换到你的顺序（设置 → 账户）中排在最前且有余量的账户。始终从顶部开始查找。";
"Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost." = "切换到每周额度最早重置的账户，让即将过期的额度在失效前用掉。";
"Switch early to use quota before it resets" = "提前切换，在重置前用完额度";
"Within %lld hours" = "%lld 小时内";
"Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room." = "当另一个账户的每周额度在这段时间内重置、早于当前账户，且该账户仍有余量时，会在当前账户达到阈值之前切换过去。";
"When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping." = "当活跃账户的 5 小时、每周或 Fable 用量达到其阈值时，PixelSwitch 会切换到另一个比其自身阈值至少低 10 个百分点的账户。关闭上方的 Fable 开关后，Fable 仅作为显示，5 小时与每周限额仍会触发切换。每次刷新时检查；5 分钟冷却时间可防止频繁来回切换。";
EOF
cat >> PixelSwitch/ja.lproj/Localizable.strings <<'EOF'

/* Auto-switch rules: Settings → General (1.2) */
"Default threshold" = "デフォルトのしきい値";
"Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty." = "各アカウントには、設定 → アカウントで個別のしきい値を設定できます。100% にすると使い切るまで使います。";
"Choose the next account by" = "次のアカウントの選び方";
"Most room left" = "残りが最も多い";
"My order" = "自分の順番";
"Resets soonest (use quota before it expires)" = "リセットが最も早い（期限切れ前に使い切る）";
"Moves you to the account with the most room left, preferring one that still has Fable to spare." = "残りが最も多いアカウントに切り替えます。Fable に余裕のあるアカウントを優先します。";
"Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top." = "自分の順番（設定 → アカウント）で最も上にある、余裕のあるアカウントに切り替えます。常に一番上から探します。";
"Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost." = "週間の上限が最も早くリセットされるアカウントに切り替え、期限切れ間近の分を無駄にせず使います。";
"Switch early to use quota before it resets" = "リセット前に使い切るため早めに切り替える";
"Within %lld hours" = "%lld 時間以内";
"Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room." = "別のアカウントの週間上限がこの時間内に、かつ現在のアカウントより早くリセットされ、そのアカウントにまだ余裕がある場合、現在のアカウントがしきい値に達する前に切り替えます。";
"When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping." = "アクティブなアカウントの 5 時間、週間、または Fable の使用量がそのしきい値に達すると、PixelSwitch は自身のしきい値より 10 ポイント以上低い別のアカウントに切り替えます。上の Fable の切り替えをオフにすると、Fable は表示のみとなり、5 時間と週間の上限では引き続き切り替わります。更新のたびにチェックされ、5 分間のクールダウンで頻繁な切り替えを防ぎます。";
EOF
cat >> PixelSwitch/de.lproj/Localizable.strings <<'EOF'

/* Auto-switch rules: Settings → General (1.2) */
"Default threshold" = "Standardschwelle";
"Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty." = "Jedes Konto kann unter Einstellungen → Konten eine eigene Schwelle haben. 100% nutzt ein Konto, bis es aufgebraucht ist.";
"Choose the next account by" = "Nächstes Konto wählen nach";
"Most room left" = "Größter Rest";
"My order" = "Meine Reihenfolge";
"Resets soonest (use quota before it expires)" = "Früheste Zurücksetzung (Kontingent vor dem Verfall nutzen)";
"Moves you to the account with the most room left, preferring one that still has Fable to spare." = "Wechselt zum Konto mit dem größten Rest und bevorzugt eines, das noch Fable übrig hat.";
"Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top." = "Wechselt zum obersten Konto deiner Reihenfolge (Einstellungen → Konten), das noch Spielraum hat. Es beginnt immer ganz oben.";
"Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost." = "Wechselt zum Konto, dessen Wochenkontingent am frühesten zurückgesetzt wird, damit bald verfallendes Kontingent nicht verloren geht.";
"Switch early to use quota before it resets" = "Früher wechseln, um Kontingent vor der Zurücksetzung zu nutzen";
"Within %lld hours" = "Innerhalb von %lld Stunden";
"Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room." = "Wechselt schon vor Erreichen der Schwelle des aktiven Kontos, wenn das Wochenkontingent eines anderen Kontos innerhalb dieser Stunden und früher als das des aktiven Kontos zurückgesetzt wird und dieses Konto noch Spielraum hat.";
"When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping." = "Erreicht die 5-Stunden-, Wochen- oder Fable-Nutzung des aktiven Kontos dessen Schwelle, wechselt PixelSwitch zu einem anderen Konto, das mindestens 10 Punkte unter seiner eigenen Schwelle liegt. Schalte den Fable-Wechsel oben aus, damit Fable nur angezeigt wird, während 5-Stunden- und Wochenlimit weiter wechseln. Wird bei jeder Aktualisierung geprüft; eine 5-Minuten-Abklingzeit verhindert schnelles Hin- und Herwechseln.";
EOF
cat >> PixelSwitch/fr.lproj/Localizable.strings <<'EOF'

/* Auto-switch rules: Settings → General (1.2) */
"Default threshold" = "Seuil par défaut";
"Any account can have its own threshold in Settings → Accounts. 100% uses an account until it is empty." = "Chaque compte peut avoir son propre seuil dans Réglages → Comptes. 100% utilise un compte jusqu'à épuisement.";
"Choose the next account by" = "Choisir le compte suivant selon";
"Most room left" = "Plus de marge restante";
"My order" = "Mon ordre";
"Resets soonest (use quota before it expires)" = "Réinitialisation la plus proche (utiliser le quota avant qu'il n'expire)";
"Moves you to the account with the most room left, preferring one that still has Fable to spare." = "Bascule vers le compte qui a le plus de marge, en privilégiant un compte à qui il reste du Fable.";
"Moves you to the highest account in your order (Settings → Accounts) that has room. It always starts from the top." = "Bascule vers le compte le plus haut dans votre ordre (Réglages → Comptes) qui a de la marge. La recherche part toujours du haut.";
"Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost." = "Bascule vers le compte dont le quota hebdomadaire se réinitialise le plus tôt, pour utiliser le quota avant qu'il ne soit perdu.";
"Switch early to use quota before it resets" = "Basculer plus tôt pour utiliser le quota avant sa réinitialisation";
"Within %lld hours" = "Dans les %lld heures";
"Switches before the active account reaches its threshold when another account's weekly quota resets within this many hours, sooner than the active account's, and that account still has room." = "Bascule avant que le compte actif n'atteigne son seuil lorsque le quota hebdomadaire d'un autre compte se réinitialise dans ce délai, plus tôt que celui du compte actif, et qu'il reste de la marge sur ce compte.";
"When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account that is at least 10 points under its own threshold. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping." = "Lorsque l'utilisation sur 5 heures, hebdomadaire ou Fable du compte actif atteint son seuil, PixelSwitch bascule vers un autre compte situé au moins 10 points sous son propre seuil. Désactivez le bascule Fable ci-dessus pour que Fable reste une simple information, les limites 5 heures et hebdomadaire continuant de basculer. Vérifié à chaque actualisation ; un délai de 5 minutes évite les allers-retours rapides.";
EOF
```

The keys are the exact English literals in the view, with SwiftUI's interpolation mapping (`\(Int(x))` becomes `%lld`). A key missing from a language would show the English text, not break.

- [ ] **Step 3: Verify**

Run: `plutil -lint PixelSwitch/*.lproj/Localizable.strings`
Expected: five lines ending in `OK`.

Run:

```bash
for l in de fr ja zh-Hans; do diff <(grep -o '^"[^"]*"' PixelSwitch/en.lproj/Localizable.strings | sort) <(grep -o '^"[^"]*"' PixelSwitch/$l.lproj/Localizable.strings | sort) && echo "$l: same keys as en"; done
```

Expected: `de: same keys as en`, `fr: same keys as en`, `ja: same keys as en`, `zh-Hans: same keys as en`.

Run: `bash scripts/typecheck.sh`
Expected: `app: type-check OK (49 files)`.

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `205 passed, 0 failed`.

- [ ] **Step 4: Commit**

```bash
git add PixelSwitch/Views/SettingsView.swift PixelSwitch/*.lproj/Localizable.strings
git commit -m "$(cat <<'EOF'
Settings: default threshold 50-100, next-account picker, early switching

General -> Auto-switch gains "Default threshold" (now up to 100%), "Choose
the next account by" (Most room left / My order / Resets soonest), and,
under Resets soonest, "Switch early to use quota before it resets" with a
1-72 hour window. Strings in all five languages.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Settings → Accounts tab (order and per-account thresholds)

**Files:**
- Create: `PixelSwitch/Views/SettingsAccountsTab.swift`
- Modify: `PixelSwitch/Views/SettingsView.swift:22-25` (add the tab after General)
- Modify: the five `Localizable.strings` files (append a block to each)

**Interfaces:**
- Consumes: `appState.accounts`, `appState.accountUsage`, `appState.moveAccounts(fromOffsets:toOffset:)`, `appState.setSwitchThreshold(_:for:)`, `appState.effectiveSwitchThreshold(for:)` (Task 5); `AutoSwitchEngine.weeklyReset(_:limit:asOf:)` (Task 3); `EmailDisplay.key`, `Badge`, `L10n.bundle` (existing).
- Produces: `struct SettingsAccountsTab: View`. Part B adds its sign-in buttons to this view. There are none yet.

- [ ] **Step 1: Create the tab**

Create `PixelSwitch/Views/SettingsAccountsTab.swift`:

```swift
import SwiftUI

/// Settings → Accounts: every account in priority order.
///
/// The order here IS the accounts list's order: dragging a row changes the
/// order "My order" follows and the order every list in the app shows (the
/// popover's Accounts and Usage tabs, the widget). Each row also carries the
/// account's weekly reset and its own auto-switch threshold.
///
/// Sign-in buttons are added here by Part B (sign-in links); this tab has none yet.
struct SettingsAccountsTab: View {
    @EnvironmentObject private var appState: AppState
    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @AppStorage(AutoSwitchSettings.thresholdKey) private var defaultThreshold = AutoSwitchSettings.fallbackThreshold

    /// The thresholds the menu offers, in 5-point steps; the stepper beside a
    /// custom value reaches every whole number in between.
    private static let thresholdChoices: [Double] = Array(stride(from: 50.0, through: 100.0, by: 5.0))

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if appState.accounts.isEmpty {
                VStack(spacing: 8) {
                    Text("No Accounts")
                        .font(.headline)
                    Text("Add your current Claude Code account to get started.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(appState.accounts) { account in
                        row(account)
                    }
                    .onMove { source, destination in
                        appState.moveAccounts(fromOffsets: source, toOffset: destination)
                    }
                }
                .listStyle(.inset(alternatesRowBackgrounds: true))
            }

            Text("Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding()
    }

    // MARK: - Row

    private func row(_ account: Account) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(primaryLabel(for: account))
                        .font(.body.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    if account.isActive {
                        Badge(text: String(localized: "Active", bundle: L10n.bundle), color: .green)
                            .fixedSize()
                    }
                }
                if let label = account.customLabel, !label.isEmpty {
                    Text(account.displayEmail(obfuscated: maskEmails))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                weeklyReset(for: account)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            thresholdControl(for: account)
        }
        .padding(.vertical, 4)
    }

    /// A custom label if one is set, otherwise the address (masked when the
    /// user has asked for that), as on the popover's Accounts tab.
    private func primaryLabel(for account: Account) -> String {
        if let label = account.customLabel, !label.isEmpty { return label }
        return account.displayEmail(obfuscated: maskEmails)
    }

    /// When this account's weekly window resets, if a reading says so. A
    /// reading from a week that has already ended says nothing about the next.
    @ViewBuilder
    private func weeklyReset(for account: Account) -> some View {
        let usage = appState.accountUsage[account.id]
        if AutoSwitchEngine.weeklyReset(usage, limit: .windows) != nil,
           let window = usage?.sevenDay, let when = window.resetTimeString {
            if window.resetIsAbsolute {
                Text("Weekly resets \(when)")
            } else {
                Text("Weekly resets in \(when)")
            }
        } else {
            Text("Weekly reset not known yet")
        }
    }

    // MARK: - Threshold

    private func thresholdControl(for account: Account) -> some View {
        HStack(spacing: 4) {
            Menu {
                Button("Default (\(Int(defaultThreshold))%)") {
                    appState.setSwitchThreshold(nil, for: account)
                }
                Divider()
                ForEach(Self.thresholdChoices, id: \.self) { value in
                    Button {
                        appState.setSwitchThreshold(value, for: account)
                    } label: {
                        Text(verbatim: "\(Int(value))%")
                    }
                }
            } label: {
                if let own = account.switchThreshold {
                    Text("Switch at \(Int(own))%")
                } else {
                    Text("Default (\(Int(defaultThreshold))%)")
                }
            }
            .fixedSize()
            .help("When this account's usage reaches this level, auto-switch moves you to another account.")

            if account.switchThreshold != nil {
                Stepper("", value: customThreshold(for: account), in: AutoSwitchSettings.thresholdRange, step: 1)
                    .labelsHidden()
            }
        }
    }

    /// The stepper's binding: the account's own threshold, saved on change.
    private func customThreshold(for account: Account) -> Binding<Double> {
        Binding(
            get: { appState.effectiveSwitchThreshold(for: account) },
            set: { appState.setSwitchThreshold($0, for: account) }
        )
    }
}
```

- [ ] **Step 2: Add it to the Settings window, right after General**

In `PixelSwitch/Views/SettingsView.swift`, replace:

```swift
            generalTab
                .tabItem {
                    Label("General", systemImage: "gear")
                }
```

with:

```swift
            generalTab
                .tabItem {
                    Label("General", systemImage: "gear")
                }

            SettingsAccountsTab()
                .tabItem {
                    Label("Accounts", systemImage: "person.2")
                }
```

`"Accounts"`, `"Active"`, `"No Accounts"` and `"Add your current Claude Code account to get started."` are already in all five string files.

- [ ] **Step 3: Add the strings in all five languages**

```bash
cat >> PixelSwitch/en.lproj/Localizable.strings <<'EOF'

/* Settings → Accounts (1.2) */
"Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only." = "Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only.";
"Weekly resets %@" = "Weekly resets %@";
"Weekly resets in %@" = "Weekly resets in %@";
"Weekly reset not known yet" = "Weekly reset not known yet";
"Default (%lld%%)" = "Default (%lld%%)";
"Switch at %lld%%" = "Switch at %lld%%";
"When this account's usage reaches this level, auto-switch moves you to another account." = "When this account's usage reaches this level, auto-switch moves you to another account.";
EOF
cat >> PixelSwitch/zh-Hans.lproj/Localizable.strings <<'EOF'

/* Settings → Accounts (1.2) */
"Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only." = "拖动账户即可调整顺序。设置 → 通用中的“我的顺序”会从上到下依次尝试账户，所有账户列表也都按此顺序显示。非默认的阈值只对该账户生效。";
"Weekly resets %@" = "每周：%@ 重置";
"Weekly resets in %@" = "每周：%@ 后重置";
"Weekly reset not known yet" = "尚不知道每周重置时间";
"Default (%lld%%)" = "默认（%lld%%）";
"Switch at %lld%%" = "%lld%% 时切换";
"When this account's usage reaches this level, auto-switch moves you to another account." = "当此账户的用量达到该水平时，自动切换会把你转到另一个账户。";
EOF
cat >> PixelSwitch/ja.lproj/Localizable.strings <<'EOF'

/* Settings → Accounts (1.2) */
"Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only." = "アカウントをドラッグして順番を変更します。設定 → 一般の「自分の順番」は上から順に試し、すべてのアカウント一覧もこの順番で表示されます。デフォルト以外のしきい値はそのアカウントにだけ適用されます。";
"Weekly resets %@" = "週間: %@ にリセット";
"Weekly resets in %@" = "週間: %@ 後にリセット";
"Weekly reset not known yet" = "週間のリセット時刻はまだ不明";
"Default (%lld%%)" = "デフォルト（%lld%%）";
"Switch at %lld%%" = "%lld%% で切り替え";
"When this account's usage reaches this level, auto-switch moves you to another account." = "このアカウントの使用量がこのレベルに達すると、自動切り替えで別のアカウントに移ります。";
EOF
cat >> PixelSwitch/de.lproj/Localizable.strings <<'EOF'

/* Settings → Accounts (1.2) */
"Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only." = "Ziehe ein Konto, um die Reihenfolge zu ändern. „Meine Reihenfolge“ unter Einstellungen → Allgemein probiert die Konten von oben nach unten, und jede Kontenliste folgt dieser Reihenfolge. Eine andere als die Standardschwelle gilt nur für dieses Konto.";
"Weekly resets %@" = "Wöchentlich: Zurücksetzen %@";
"Weekly resets in %@" = "Wöchentlich: Zurücksetzen in %@";
"Weekly reset not known yet" = "Wöchentliches Zurücksetzen noch unbekannt";
"Default (%lld%%)" = "Standard (%lld%%)";
"Switch at %lld%%" = "Wechseln bei %lld%%";
"When this account's usage reaches this level, auto-switch moves you to another account." = "Erreicht die Nutzung dieses Kontos diesen Wert, wechselt der automatische Wechsel zu einem anderen Konto.";
EOF
cat >> PixelSwitch/fr.lproj/Localizable.strings <<'EOF'

/* Settings → Accounts (1.2) */
"Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only." = "Faites glisser un compte pour changer l'ordre. « Mon ordre » dans Réglages → Général essaie les comptes de haut en bas, et chaque liste de comptes suit cet ordre. Un seuil autre que celui par défaut ne s'applique qu'à ce compte.";
"Weekly resets %@" = "Hebdomadaire : réinitialisation %@";
"Weekly resets in %@" = "Hebdomadaire : réinitialisation dans %@";
"Weekly reset not known yet" = "Réinitialisation hebdomadaire encore inconnue";
"Default (%lld%%)" = "Par défaut (%lld %%)";
"Switch at %lld%%" = "Changer à %lld %%";
"When this account's usage reaches this level, auto-switch moves you to another account." = "Lorsque l'utilisation de ce compte atteint ce niveau, le changement automatique vous fait passer à un autre compte.";
EOF
```

- [ ] **Step 4: Verify**

Run: `plutil -lint PixelSwitch/*.lproj/Localizable.strings`
Expected: five lines ending in `OK`.

Run the key-parity loop from Task 7, Step 3.
Expected: all four `same keys as en` lines.

Run: `xcodegen generate && bash scripts/typecheck.sh`
Expected: `app: type-check OK (50 files)`.

Run: `bash Tests/run-unit-tests.sh | tail -1`
Expected: `205 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add PixelSwitch/Views/SettingsAccountsTab.swift PixelSwitch/Views/SettingsView.swift \
        PixelSwitch/*.lproj/Localizable.strings
git commit -m "$(cat <<'EOF'
Settings: Accounts tab with drag-to-reorder and per-account thresholds

Lists every account in priority order (the order My order follows and every
list shows); drag to reorder. Each row shows the label or address (masking
respected), its weekly reset, and a threshold menu: Default (NN%) or a
custom 50-100. Sign-in buttons come with Part B.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Documentation, full verification, CI build and the manual checks

**Files:**
- Modify: `README.md:73` (the auto-switch feature bullet), `README.md:119-123` (the bullets under "### 2. Auto-Switch, Including the Fable Allowance")
- `ARCHITECTURE.md` needs no change: `grep -n -i "threshold\|auto-switch" ARCHITECTURE.md` returns nothing (it describes keychain and token flow only).

**Interfaces:**
- Consumes: everything above.
- Produces: a CI-built, signed app artifact on the branch, and the founder-facing README text.

- [ ] **Step 1: Update the README**

In `README.md`, replace line 73 (the bullet that begins `- **Auto-switch before a limit stops you**:`) with:

```markdown
- **Auto-switch before a limit stops you**: when the active account reaches its threshold on its session, weekly or Fable limit, PixelSwitch switches to another account with room on that limit, and only to one that is also clear of its other limits, so it never has to move you twice. Every account can have its own threshold from 50% to 100% (100% uses it until it is empty); the rest use the default. You choose how the next account is picked: most room left, your own order (drag accounts in **Settings → Accounts**), or the account whose weekly quota resets soonest, which can also switch early so quota about to expire is used before it is lost. If no account has room, it stays put. Fable switching has its own on/off switch, so you can keep session and weekly switching while leaving Fable as a reading. A 5-minute cooldown and a 10-point hysteresis margin stop any ping-ponging, and the chosen account is re-checked with a fresh reading before the switch happens.
```

Replace lines 119–123 (the five bullets from `- The session and weekly windows are checked first;` to `- Fable switching can be turned off on its own in **Settings → Auto-switch**, leaving session and weekly switching untouched.`) with:

```markdown
- The session and weekly windows are checked first; Fable is checked when they have not triggered. Each is measured against the active account's **own threshold**, or the default when it has none.
- A candidate must sit at least the hysteresis margin below **its own** threshold **on the limit that fired and on the session and weekly windows**, so a Fable switch never lands on an account that is about to hit its weekly limit.
- An account with no reading for the limit that fired is never chosen: on a round-robin poll, "no sample yet" is not the same as "plenty left".
- **Choosing the next account** (Settings → Auto-switch): *Most room left* (the default: Fable room first, then the lowest usage), *My order* (the highest account in the Settings → Accounts order that has room, always starting from the top), or *Resets soonest* (the earliest weekly reset first, the Fable weekly reset for a Fable switch; ties go to most room, and unknown resets come last).
- **Switching early** (Resets soonest only; on by default, with its own switch): before the active account reaches its threshold, PixelSwitch moves to an account whose weekly quota resets within the chosen window (24 hours by default) and sooner than the active account's, as long as it has at least 1 point of room under its own threshold on every limit being watched. When that account reaches its threshold, the normal rule moves you on, and the early switch cannot bring you straight back.
- The same rule that ranks candidates is the one that verifies the chosen account against a fresh reading, so ranking and verification cannot drift apart.
- Fable switching can be turned off on its own in **Settings → Auto-switch**, leaving session and weekly switching untouched.
```

- [ ] **Step 2: Run the full local verification**

```bash
bash Tests/run-unit-tests.sh | tail -1
xcodegen generate && bash scripts/typecheck.sh
plutil -lint PixelSwitch/*.lproj/Localizable.strings
git status --short
```

Expected: `205 passed, 0 failed`; `app: type-check OK (50 files)`; five `OK` lines; `git status` shows only `README.md` modified.

- [ ] **Step 3: Commit the docs**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
README: per-account thresholds, next-account strategy, early switching

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Build, sign and notarize in CI (the only full build)**

```bash
git push -u origin part-a-auto-switch-rules
gh workflow run "Build and Notarize macOS App" --repo BespokeWoodcraftStudio/PixelSwitch --ref part-a-auto-switch-rules
sleep 10
RUN_ID=$(gh run list --repo BespokeWoodcraftStudio/PixelSwitch --workflow "Build and Notarize macOS App" \
         --branch part-a-auto-switch-rules --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')
echo "run $RUN_ID"
gh run watch "$RUN_ID" --repo BespokeWoodcraftStudio/PixelSwitch --exit-status
```

Expected: the run ends green. Its "Unit Tests" step runs the same harness (205 passed). If it fails, read `gh run view "$RUN_ID" --repo BespokeWoodcraftStudio/PixelSwitch --log-failed`, fix the problem on the branch, and repeat this step.

- [ ] **Step 5: Download the built app**

```bash
mkdir -p /tmp/pixelswitch-part-a && cd /tmp/pixelswitch-part-a
gh run download "$RUN_ID" --repo BespokeWoodcraftStudio/PixelSwitch -n PixelSwitch-macOS
open PixelSwitch.dmg
```

Quit any running PixelSwitch first. Drag the app from the disk image to a scratch folder such as `/tmp/pixelswitch-part-a/app/` and open it from there, so the installed copy in `/Applications` is left alone.

- [ ] **Step 6: Manual checks in the built app (a person does these)**

Settings → General → Auto-switch (turn "Switch account before hitting the limit" on first):
1. The first control reads **Default threshold**. Dragging the slider fully right shows **100%**, fully left **50%**.
2. **Choose the next account by** offers Most room left, My order, and Resets soonest (use quota before it expires). The caption under it changes with each choice.
3. Picking **Resets soonest** shows **Switch early to use quota before it resets**, already on, with **Within 24 hours** below it. The stepper stops at 1 and at 72.
4. Turning the switch off hides the hours control. Picking Most room left or My order hides both.
5. Quit and reopen PixelSwitch: every choice is still there.

Settings → Accounts (the second tab, right after General):
6. It lists every account in the same order as the menu bar popover's Accounts tab. The active one has the green **Active** badge.
7. Drag the bottom account to the top. Open the menu bar popover: the Accounts tab and the Usage tab both show the new order. Quit and reopen: the order is kept.
8. Each row shows **Weekly resets in …** or **Weekly resets Mon …**, or **Weekly reset not known yet** for an account whose usage has not been read this launch.
9. The threshold menu reads **Default (90%)**. Pick 70%: it reads **Switch at 70%**, and a stepper appears beside it. Step up once: **Switch at 71%**. Pick Default: back to **Default (90%)**, and the stepper goes away.
10. Move General's Default threshold to 85%: every row on the default now reads **Default (85%)**.
11. Turn on Settings → General → Account display → **Hide part of each email address**: the addresses in the Accounts tab are masked.
12. Set Language to **Deutsch**: the new controls read "Standardschwelle", "Nächstes Konto wählen nach", "Meine Reihenfolge", and the tab reads "Konten".

Optional live check. This really switches accounts.
13. With auto-switch on, set the active account's own threshold below its current 5-hour or weekly usage, and make sure another account is at least 10 points under its own threshold. Within one refresh (and after any 5-minute cooldown) PixelSwitch switches, and `grep "\[autoSwitch\]" ~/Library/Logs/PixelSwitch-app.log | tail -3` shows `Rule threshold, strategy ...` and `Switching to ... by rule threshold`.

- [ ] **Step 7: Record the work**

Write the work-journal entry with the `worklog` skill (What / Why / How / Outcome, plus Lessons and Follow-ups, including the follow-ups below). Update `docs/SESSION-HANDOVER.md` if the repo has one. Commit and push both. Part A merges into `main` before Part B starts (spec section 6).

## Follow-ups (not in Part A)

- **"Most room left" and per-account thresholds.** Ranking stays on raw utilization, as the spec says. An account at 60% with its own 70% threshold (10 points left) ranks above one at 65% with a 100% threshold (35 points left), so it can trigger again sooner. Ranking by headroom under each account's own threshold would fix that. It changes "Most room left" behaviour, so it is a founder decision.
- **`resetTimeString` is English-only** ("3 hr 5 min", "now"). It predates this part and now also appears in the Accounts tab.
- Part B adds Add Current Account and Sign In New Account buttons to `SettingsAccountsTab`. Part C exposes `autoSwitch.strategy`, `autoSwitch.drainEarly`, `autoSwitch.drainWithinHours`, `accounts.setThreshold` and `accounts.setOrder` through `AutoSwitchSettings`, `setSwitchThreshold(_:for:)` and `setAccountOrder(_:)`, and streams `lastAutoSwitch`.

## Self-review against the spec

| Spec item | Where |
|---|---|
| A1 per-account threshold, 50–100, 100 = until empty, nil = default | Task 1 (field, clamping), Task 2 (trigger and eligibility), Task 5 (setter), Task 8 (control) |
| A1 global default slider widened to 50–100 | Task 1 (`defaultThreshold` clamps to 50–100), Task 7 (slider `in: AutoSwitchSettings.thresholdRange`) |
| A1 trigger on own threshold, Fable too | Task 2 tests "switches at 72%", "Fable at 75% moves an account whose own threshold is 70%" |
| A1 eligibility = own threshold − 10 | Task 2 tests "at or under 60%", "exactly at its own ceiling" |
| A2 three strategies and their labels | Task 1 (type), Task 3 (ranking), Task 7 (picker, spec A2 labels) |
| A2 My order always from the top | Task 3 tests "starts from the top", "freed up again" |
| A2 Resets soonest: weekly, Fable weekly for Fable, ties most room, unknown last, never 5-hour | Task 3 tests |
| A2 Fable preference only in Most room | Task 3 tests "still prefers an account with Fable to spare", "keeps its order even when the first account is out of Fable" |
| A2 one order everywhere; new accounts at the bottom | Task 5 (order is the list; `append` kept), Task 8 (drag), Task 9 checks 6–7 |
| A3 early drain rule, on by default, own switch, shown only under Resets soonest, 1–72 h default 24 | Task 1 (settings), Task 4 (rule and tests), Task 6 (wiring), Task 7 (UI) |
| A3 verified fresh before switching; cooldown; no switch or sign-in in progress | Task 6 (shared rule for verification; guards unchanged) |
| A3 after draining, the threshold rule moves the user on | Task 4 ping-pong tests |
| A4 engine inputs `threshold(for:)`, `strategy`, `drainEarly`, `drainWithin`; logs which rule fired | Tasks 2–4, Task 6 |
| A5 General tab controls; Accounts tab rows (label/email, weekly reset, threshold) | Tasks 7 and 8 (sign-in buttons deferred to Part B, spec section 6) |
| A5 popover unchanged apart from order | Task 5 note (`ForEach(appState.accounts)` in both popover views) |
| A6 every listed test, plus threshold 100 and backward compatibility | Tasks 1–5. The 19 existing auto-switch cases in `main.swift` pass untouched (Task 2) |
| Section 9 names and types | Global Constraints; each task's Interfaces block |
| Section 9 test convention | Task 1 (file, call line, script entry) |
