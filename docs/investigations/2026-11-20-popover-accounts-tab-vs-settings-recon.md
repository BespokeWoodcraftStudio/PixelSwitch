# Recon: move popover Accounts tab into Settings, bigger footer icons, expired message (2026-11-20)

Read-only recon of main working tree. Paths relative to repo root. Nothing edited except this file.

## 1. Popover Accounts tab (AccountSwitcherView.swift) vs Settings Accounts tab

| Function | Popover (file:line) | Settings Accounts (SettingsAccountsTab.swift / SettingsAccountRow.swift / SettingsSignInButtons.swift) | Status |
|---|---|---|---|
| Switch to account | AccountSwitcherView:154-159 `appState.switchTo` (AppState:1224) | none. No Switch button in row (SettingsAccountRow body :124-180) | MISSING |
| Re-authenticate | :162-171 `appState.reauthenticateAccount` (AppState:570), icon arrow.triangle.2.circlepath, .help "Re-authenticate (fix stale token)" | none in Settings rows | MISSING |
| Edit label | :88-103 inline TextField + pencil, `updateAccountLabel` (AppState:868) | none. Row only displays label (SettingsAccountsTab:92-96) | MISSING |
| Remove account | :173-180 `removeAccount` (AppState:1117), no confirm | none | MISSING |
| Priority | read-only PriorityTag :76 | Menu P1-P9/None + Stepper, SettingsAccountRow:~244-270, `setPriority` (AppState:1021) | present (Settings richer) |
| Drag reorder | none in popover | `.onMove` -> `moveAccounts` SettingsAccountsTab:42-44 (AppState:1050) | present (Settings only) |
| Session/Weekly thresholds, Manual only | popover shows only "Manual only" line :139-146 | SettingsAccountRow windowControl :311-352, help text SettingsAccountsTab:152-190 | present |
| Auto-switch enabled toggle / strategy | popover only reads enabledKey :11 | Not on Accounts tab; lives in General tab (SettingsView ~:104). "Use priority" shortcut SettingsAccountsTab:62-65 | present (General) |
| Active badge, plan/provider line, masked email, colour stripe + glyph | :56-62, :71-86, :104-137 | badge yes (:SettingsAccountRow:~137); colour stripe/glyph/plan line NO; masked email yes (:96); weekly reset note yes (:104-113) | partly |
| Empty state ("No Accounts") | :36-52 | :31-39 (same text, no icon) | present |
| Add Current Account (confirm "This will capture the currently logged-in...") | :235-250 inline confirm, :293 button | SettingsSignInButtons:12-13,22-31 (confirmationDialog) | present |
| Sign in new account | :273 `loginNewAccount()` (AppState:559) | SettingsSignInButtons:15-17 | present |
| Signing-in state + "Show sign-in window" | :187-212 posts .pixelswitchShowSignIn | none; buttons just `.disabled(appState.isLoggingIn)` :21 | MISSING |

Biggest gap: Settings rows have NO Switch, Re-sign, Edit label or Remove. Those four must be added to SettingsAccountRow/SettingsAccountsTab (new closures like onSwitch/onReauth/onRemove/onLabel, as onPriority etc. are passed at SettingsAccountsTab:~94-120) before the popover tab can go. Linux already has all four (see 6).
Settings rows also lack the per-account colour (AccountColorCoding) the popover Accounts tab shows; Settings > Appearance text (SettingsView:139) says "Usage and Accounts tabs".

## 2. Popover tabs

- MainMenuView.swift:72 `@State selectedTab: Tab = .usage` (NOT persisted: no UserDefaults/SettingsStore key).
- :80-98 `enum Tab { usage, costs, accounts }`, titles + icons (person.2.fill).
- :116-125 content switch; :294-338 tabBar (custom Capsule, indicator width = total/Tab.allCases.count via :303-310, so removing the case re-divides automatically; `.frame(width: 260)` :335).
- No ControlAPI/MCP/CLI key names the popover tab (grep of PixelSwitch/Control: none). No keyboard shortcuts for tabs. No onboarding/empty state sends user to the tab; empty state text is "Add your current Claude Code account" in both views.
- Strings naming "Accounts tab": Localizable.strings "Each account gets a colored edge and icon on the Usage and Accounts tabs..." (en:213, also de/fr/ja/zh-Hans :211) shown at SettingsView:139; "Accounts" tab title key (:4 in all 5). Comments: UsageDashboardView:421, SettingsAccountsTab:101, SettingsAccountsTab:11-13 header ("same sign-in as popover"), SettingsSignInButtons:3. Docs: RELEASE_NOTES.md:5, docs/SESSION-HANDOVER.md:9,86,91, docs/decisions/*2.2* html.
- Tests that break: Tests/UnitTests/AccountPlanTests.swift:972-993 reads AccountSwitcherView.swift and asserts the Re-sign icon/help equal its "arrow.triangle.2.circlepath" / ".help(\"Re-authenticate (fix stale token)\")" (planSource returns empty if the file is deleted, failing :974). Also :1024 string table check. Tools/screenshots/make_screenshots.swift:465 draws a fake 3-tab bar ("Accounts") and :276 the expired string. AccountSwitcherView.swift is in PixelSwitch.xcodeproj/project.pbxproj (lines 55,279,446,666) so deleting it needs pbxproj edits.
- Popover height is sized from Usage content (MainMenuView:180-188), so removing Accounts changes nothing there. Strings only used by AccountSwitcherView ("Login New Account", "Edit label", "Remove account", "Show sign-in window", "Custom label") become orphaned unless reused.

## 3. Footer (MainMenuView.swift footerView :342-392)

- Refresh :356-364 `Image("arrow.clockwise").font(.caption)`, `.help("Refresh")`, calls `appState.refresh()`.
- Settings :366-378 `Image("gear").font(.caption)`, `.help("Settings")`, posts .pixelswitchOpenSettings.
- Quit :380-388 `Image("power").font(.caption)`, `.help("Quit")`, NSApp.terminate.
- All `.buttonStyle(.plain)`, `.focusable(false)`, hit area = glyph only (tiny). .help present on all three; NO .accessibilityLabel on any (icon-only buttons, so VoiceOver gets nothing explicit). Footer padding h16 v8; also holds appState.errorMessage on left (:344-352) so room is limited (360pt wide popover, :134).
- "↻ 25 sec" is NOT in the footer. It is the Usage tab "Last updated" row: UsageDashboardView.swift:82-93, `Image("arrow.clockwise")` + `Text(lastRefresh, style: .relative)`, caption2/caption, no .help.
- Existing actions: `AppState.addAccount() async` (AppState:438; "Add Current Account", wraps `performAddCurrentAccount`, errors -> errorMessage), `AppState.loginNewAccount()` (AppState:559, `startSignIn(.newAccount)`), `reauthenticateAccount(_:)` (:570). Popover calls them at AccountSwitcherView:245-246 (after inline confirm showingAddConfirm) and :273. Settings: SettingsSignInButtons:12-28. Disabled state: `@Published isLoggingIn` (AppState:26), Settings disables both buttons on it; popover swaps the whole block for a spinner. Busy also surfaces as errorMessage "A sign-in is already in progress." (AppState:~590) via reportSignInStartError. Add Current needs the confirm dialog (no icon-only footer equivalent yet; must keep confirm or a popover-safe alternative, a SwiftUI confirmationDialog in a popover is workable but untested here).

## 4. Expired message

- Origin: AppState.swift:2028-2034 `showResign(for:message:)` default `String(localized: "Session expired. Re-authenticate (↻) to fix.")`; called :2023, :2118, :2219. Sibling: :1716 and :2037 `belongedToAnotherMessage` "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix." (same ↻ problem, and Localizable.strings en:192). Other "Re-authenticate %@ to fix it." messages (AppState:1511-1620) carry no glyph.
- Exact key: `Session expired. Re-authenticate (↻) to fix.` in PixelSwitch/{en:180, de:178, fr:178, ja:178, zh-Hans:178}.lproj/Localizable.strings. Second key `Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix.` at en:192, de/fr/ja/zh-Hans:190. Changing the English key text means updating all five files plus Tools/screenshots/make_screenshots.swift:276.
- Linux: linux/pixelswitch/gui/model.py:52 `TEXT_RESIGN_MESSAGE`; detector model.py:167 matches "Re-authenticate" or startswith "Session expired"; tests/gui_fixtures.py:41. Linux button "↻ Re-sign" settings_accounts.py:53 (comment :51-52 says the glyph is deliberately matched). Daemon sentences in coordinator.py:1368,1404,1435 have no glyph.
- Icons: card Re-sign button UsageDashboardView.swift:424-441 uses `systemImage: "arrow.triangle.2.circlepath"` (circle arrows), identical to Accounts tab (AccountSwitcherView:164). The "↻" glyph in the message is arrow.clockwise, the same as footer Refresh (MainMenuView:359) and Retry (UsageDashboardView:407). So the message matches neither Re-sign nor the Accounts button; it matches Refresh, which is the wrong action. Fix options: reword to "Click Re-sign to fix." (no glyph), or change the Re-sign symbol. AccountPlanTests:990-993 pins the Re-sign icon equal to the Accounts tab icon, so that test must change whichever way.
- Note: Re-sign exists only on the expired card; when the Accounts tab goes, the Settings row Re-sign (to add) should reuse this icon and .help/.accessibilityLabel strings ("Re-authenticate (fix stale token)", "Re-authenticate %@").

## 5. Opening Settings

- Popover gear posts `.pixelswitchOpenSettings` (MainMenuView:366-371); defined HiddenWindowView.swift:4; handled :15-18 with `@Environment(\.openSettings)` (SwiftUI Settings scene, plain `openSettings()`, no payload). ARCHITECTURE.md:560.
- SettingsView.swift:27-51 is an uncontrolled `TabView { ... }` (order General, Accounts, Menu Bar, Claude CLI, About) with NO `selection` binding, so opening to Accounts is NOT supported today. To add: a `@SceneStorage`/`@AppStorage` or shared selection state set before `openSettings()`, plus a notification userInfo or a tag + selection binding (tabs need `.tag`). Footer "Add current"/"Sign in" icons could either act directly (call AppState) or open Settings to Accounts; direct calls are simpler. Settings window is 820x520 (:55).

## 6. Linux (linux/pixelswitch/gui)

- No popover. Tray is a StatusNotifierItem menu (tray.py:1-8): account list (click to switch), Auto-switch toggle, Refresh, Settings, "Sign in another account", Quit; actions in tray.py:132-145. Tray has no Accounts tab and no add-current item.
- Settings window (settings.py:413-426) pages: Accounts, General, Sign in, About; `--page accounts|general|sign-in|about` already supported (settings.py:11, tray.py:149-157 `open_settings(page)`).
- Linux Accounts page already has per card Re-sign (settings_accounts.py:53), Switch :56, Remove… :59, editable label :214-221, Priority, limits, plus "Add current account" :269 and "Sign in…" :275 (opens Sign in page). So Linux needs no Accounts-tab move. Optional parity: only the expired message (model.py:52) and, if wanted, tray menu icons/wording. Linux has its own GUI tests (linux/tests/gui_fixtures.py:41).
