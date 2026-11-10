# Recon: popover / Settings colours and readability (PixelSwitch)

**Question:** why is the menu-bar popover hard to read, where is every background / material / tint / low-contrast text colour set, what is the smallest fix, and how can it be verified without CI.
**Tree:** /Users/ahmed/Documents/GitHub/PixelSwitch, branch main, HEAD 3036e1f (1.8, 2026-09-29), `git status` clean. Date 2026-11-10.
**Searched:** every `.swift` under PixelSwitch/, PixelSwitchCLI/, Shared/, Tests/, Tools/screenshots/ (grep for background/material/VisualEffect/appearance/colorScheme/opacity/Color(/.secondary/.tertiary/.quaternary), all view files read in full except SettingsView lines 170-387 (grep only), ClaudeCLITabView, RemoteControlSection, MenuBarModulesSettingsView, SignInView (grep only). **Not searched in depth:** PixelSwitchWidget/ (separate target with its own `brandColor`, own tints; not part of the popover), the CLI, AppState.swift logic (only grepped for plan display).
**Tools run (none touched the repo):** python3 contrast scripts and tiny swiftc programs, all in the scratchpad: `/private/tmp/claude-501/-Users-ahmed-Documents-GitHub-PixelSwitch/f704c667-ad7d-495a-8dda-60528d78d198/scratchpad/{contrast.py,propose.py,propose2.py,nscolor/}`.

LINE NUMBERS DRIFT. Every file:line below is at HEAD 3036e1f. Re-grep the key (shown in backticks) before editing; another lane may move them within the hour.

---------------------------------------------------------------------------------------------------

## 0. Answer in short

1. The popover is translucent twice over. `NSPopover` (system chrome, created at `StatusItemController.swift:66`, no appearance/background set) is see-through by itself, and `MainMenuView.swift:151` adds `.background(.ultraThinMaterial)`. Every card token on top of it is a *low-alpha* colour (`white@0.20` light, `black@0.21` dark, `BrandColor.swift:55-77,91`), so the surface behind the text is whatever the wallpaper is (pink/purple in the founder's case). No text contrast can be guaranteed against an unknown backdrop.
2. The text tokens are weak even in the best case: `textSecondary` = system `secondaryLabelColor` (black@0.498) in light = **3.95:1 on pure white** (fails 4.5), and the stock bar colours used as percentage text are **1.98:1 to 3.16:1** on their own tinted tiles in the README render, which has an opaque backdrop. Badges are white text on `#E86D45` (3.13:1) and on system green (2.22:1 light, 2.02:1 dark).
3. Smallest fix: make ~10 adaptive tokens in `BrandColor.swift` opaque with computed hex values (table in section 5), swap one line in `MainMenuView.swift:151` for an opaque `Color.popoverBackground`, collapse the two-layer tint in `UsageLimitRow.swift:92-99` to one solid tile, change `Badge.swift:11` text colour, and point `MenuBarConfig.stockColor(.dashboard)` at AA-safe adaptive colours. Every proposed pair below passes (4.5:1 text, 3:1 graphics) in both appearances.

---------------------------------------------------------------------------------------------------

## 1. Every place a background / material / fill / tint is set

### 1a. Popover window and its host (VERIFIED by reading)
- `PixelSwitch/Services/StatusItemController.swift:66-86`: `let popover = NSPopover()`, `.behavior = .transient`, `.animates = false`, content is `NSHostingController(rootView: AnyView(MainMenuView()...))` with `sizingOptions = [.preferredContentSize]` (line 77-81). **No `popover.appearance`, no NSVisualEffectView, no window background, no `NSApp.appearance` anywhere.** `grep -rn "VisualEffect"` over the whole repo returns nothing.
- `PixelSwitch/Views/MainMenuView.swift:134` `.frame(width: 360, height: popoverHeight)`, then `:136-150` a `GeometryReader` probe (`Color.clear`, not visual), then **`:151 .background(.ultraThinMaterial)`** (the only SwiftUI Material in the repo; the only hit of `grep -rn Material`).
- `PixelSwitch/PixelSwitchApp.swift:44-78` hidden keepalive window; `HiddenWindowView.swift:27-29` sets `isOpaque=false`, `alphaValue=0`, `backgroundColor=.clear` on **that 1x1 off-screen window only** (not the popover, not Settings).
- `PixelSwitch/PixelSwitchApp.swift:80-86` Settings scene: plain `SettingsView()`; SettingsView is a native `TabView` (`SettingsView.swift:23`), tabs are `Form{}.formStyle(.grouped)` (`:58,:164,:171,:208`; `ClaudeCLITabView.swift:27,105`), 520x440 (`:49`). Native opaque window background; no custom background set.
- `PixelSwitch/Services/SignInWindowController.swift:97-113` `NSPanel` `[.titled,.closable,.utilityWindow]`, floating; no background/appearance set; `SignInView` has no `.background`.
- `UsageDashboardView.swift:13` `StatWithTooltip` uses SwiftUI `.popover` (system chrome, default).

### 1b. Popover content: what draws a surface (VERIFIED)
| Where | What |
|---|---|
| `MainMenuView.swift:151` | `.background(.ultraThinMaterial)` (whole popover body) |
| `MainMenuView.swift:210` | promo banner `.background(.subtleBrand)` (`brand@0.12` light, `brand@0.28` dark) |
| `MainMenuView.swift:294-296` | tab bar capsule `.fill(.tabFill)` + `.stroke(.tabBorder)` (white@0.15 / black@0.21; white@0.40 / white@0.20) |
| `MainMenuView.swift:304` | selected-tab indicator `Capsule().fill(Color.brand)` |
| `AppStyle.swift:38-52` `CardStyleModifier` | `RoundedRectangle.fill(.cardFill).strokeBorder(.cardBorder).shadow(black@0.10, r5, y6)`; the one card style used by `.cardStyle()` at `UsageDashboardView.swift:123,165,291`, `CostDetailView.swift:71,149,264` |
| `UsageDashboardView.swift:226-234` | 4pt coloured edge bar `AccountPalette.color(swatch)` (full strength, hues 2,30,52,96,160,186,212,252,288,324; `AccountPalette.swift:17,23-30`) |
| `UsageDashboardView.swift:291-294,342-345` | card border = `.activeAccountRing` (2pt) when active/switching, else `.cardBorder` (1pt). This is the "coloured border" the founder sees |
| `UsageLimitRow.swift:92-99` | row tile = two layers: `.fill(.cardFill)` then `.fill(identityColor.opacity(0.10))` (green #34C759 / blue #0A84FF / purple #AF52DE tints) |
| `UsageLimitRow.swift:73,77` | bar track `.progressTrack` (gray@0.18 light / white@0.15 dark), fill `fillColor` |
| `UsageLimitRow.swift:105,108` | identity chip `identityColor.opacity(0.18)` with glyph in `identityColor` |
| `Badge.swift:11,14` | white text on solid `color` capsule; callers: Max/plan `Badge(color: .brand)` at `MainMenuView.swift:263`, `UsageDashboardView.swift:471`; Active `Badge(color: .green)` at `UsageDashboardView.swift:465`, `AccountSwitcherView.swift:140`, `SettingsAccountsTab.swift:81` |
| `AccountSwitcherView.swift:229-237` | account row: `.fill(account.isActive ? .cardFillStrong : .clear)`, border `activeAccountRing`/`cardBorder`, shadow. **Inactive rows have NO fill (`.clear`)** |
| `AccountSwitcherView.swift:278-283, 309-314` | "signing in" and "add current?" boxes `.cardFillStrong` + `.cardBorder` |
| `AccountSwitcherView.swift:326` | "Login New Account" `Color.white` fill (fixed, any appearance), text `AppStyle.buttonTextColor` `#424242` (`AppStyle.swift:25`) |
| `AccountSwitcherView.swift:339-347` | outline: `colorScheme == .dark ? gray@0.4 : white@0.22` (the only `colorScheme` read in the app; `white@0.22` is invisible on a light surface) |
| `CostDetailView.swift:230` | today's history row `.cardFillStrong`, else `.clear` |
| `CostDetailView.swift:216` | history bar `Color.brand` (today) / `Color.blue.opacity(0.6)` |
| `UsageChartView.swift:46` | `.brand` / `.blue.opacity(0.7)`. **UsageChartView is not referenced anywhere outside its own file** (grep), dead code |
| `MenuBarModulesSettingsView.swift:101-104` | Settings preview strip fill `Color(nsColor: .underPageBackgroundColor)` (system) |
| `ClaudeCLITabView.swift:88` | `.orange.opacity(0.2)` capsule with `.orange` text |
| `RemoteControlSection.swift:53` | `.quaternary.opacity(0.5)` fill |
| `MenuBarStripView.swift:48,64` | status-bar strip, template/`.primary` (system-driven, not part of the popover) |

### 1c. Settings window and sign-in window: effectively system-native
Opaque system window background, grouped Form. Text is `.secondary` (system) at: `SettingsView.swift:82,94,99,109,119,124,127,135,139,192,198,227,257` (+ `.tertiary` at 230,274,284), `SettingsAccountsTab.swift:33,58,88,94,98` (+ `.tertiary` 71, `.orange` 52, `Badge .green` 81), `ClaudeCLITabView.swift:54,63,74,81`, `MenuBarModulesSettingsView.swift:22,31(.tertiary),62,70`, `RemoteControlSection.swift:21,39,46,65(.tertiary)`, `SignInView.swift:49,93,97,102,114,136(.tertiary),167,191`. Measured system `secondaryLabelColor` is 3.95:1 on white (light) and 5.92:1 on #1E1E1E (dark). Not the founder's complaint (he describes the popover), listed so a builder can decide.

---------------------------------------------------------------------------------------------------

## 2. Colour tokens: adaptive vs fixed (VERIFIED, `BrandColor.swift`, `AppStyle.swift`, `AccountPalette.swift`, `MenuBarConfig.swift`)

`Color.adaptive(light:dark:)` (`BrandColor.swift:19-24`) wraps an `NSColor(name:nil,dynamicProvider:)` and picks by `appearance.bestMatch(from:[.darkAqua,.aqua])`.

| Token (file:line) | Light | Dark | Kind |
|---|---|---|---|
| `cardFill` BrandColor:55 | white@0.20 | black@0.21 | adaptive, **translucent** |
| `cardFillStrong` :57 | white@0.25 | black@0.28 | adaptive, translucent |
| `cardBorder` :59 | white@0.40 | white@0.20 | adaptive, translucent (invisible on a light surface) |
| `tabFill` :75 / `tabBorder` :77 | white@0.15 / white@0.40 | black@0.21 / white@0.20 | adaptive, translucent |
| `textPrimary` :82 | `.primary` | white | adaptive, **unused** (`grep` finds no call site outside BrandColor.swift) |
| `textSecondary` :84 | `Color.secondary` (labelColor black@0.498) | white@0.55 | adaptive; **44 call sites** (UsageDashboardView 13, CostDetailView 16, AccountSwitcherView 9, MainMenuView 5, UsageLimitRow 1) |
| `subtleBrand` :89 | brand@0.12 | brand@0.28 | adaptive, translucent |
| `progressTrack` :91 | gray@0.18 | white@0.15 | adaptive, translucent |
| `activeAccountRing` :67 | #F56A00 | #FF942E | adaptive, solid |
| `pixelVenturesGreen` :13 | #0B8F3E | #00FF66 | adaptive, solid (Pixel Ventures mark only) |
| `AccountPalette.color` AccountPalette:23 | HSB(h,.85,.70) | HSB(h,.62,1.0) | adaptive, solid |
| `brand` BrandColor:8 | #E86D45 | #E86D45 | **fixed** (used as fill, tint and text) |
| `AppStyle.buttonTextColor` :25 | #424242 | #424242 | **fixed** |
| `AppStyle.cardShadowColor` :12 | black@0.10 | black@0.10 | fixed (fine) |
| session/weekly/fable identity hex `MenuBarConfig.swift:44-46` | #34C759 / #0A84FF / #AF52DE | same | **fixed**, user-editable, persisted, exposed via remote control (`SettingsStore.swift:57-63`) |
| stock bar fill, dashboard `MenuBarConfig.swift:236-241` | `.green` <60, `.orange` 60-89, `.red` >=90 | same call | system colours, adaptive (measured: #34C759/#FF8D28/#FF383C light; #30D158/#FF9230/#FF4245 dark) |
| `.menuBar` stock `MenuBarConfig.swift:233-235` | `.primary` / `.red` >90 | | system |

Measured on this Mac (macOS 27.0.1, tiny NSColor dump, `scratchpad/nscolor/dump.swift`): labelColor black/white @0.847; **secondaryLabelColor black@0.498 / white@0.549**; tertiary @0.259/0.247; quaternary @0.098; windowBackgroundColor **#FFFFFF** light, #1E1E1E dark; systemGreen/Orange/Red as above.
macOS text sizes (measured via `NSFont.preferredFont`): `.caption` and `.caption2` = **10pt**, `.subheadline` 11, body/headline 13, title3 15, `.title` 22. Almost all popover text is 10-11pt, so the **4.5:1 small-text bar applies**; only the 36pt/22pt cost figures and nothing else count as "large" (3:1).

---------------------------------------------------------------------------------------------------

## 3. Every low-contrast text style (VERIFIED by grep)

`.secondary` / `.tertiary` / `.quaternary` direct uses in the popover files (not via `.textSecondary`):
- `UsageDashboardView.swift:192` count `.primary : .quaternary`; `:199` model name `.tertiary : .quaternary` (quaternary text = 1.25:1 on white measured; tertiary 1.88:1)
- `UsageDashboardView.swift:278` hourglass icon `.secondary`; `:456` glyph tint `Color.secondary` when colour coding is off
- `AccountSwitcherView.swift:79` glyph tint `Color.secondary`; `:161,166` "·" separators `.quaternary` (decorative); `:269` "Finish in the sign-in window." `.tertiary` (real text, 1.88:1)
- `CostDetailView.swift:206` date `isToday ? .brand : .secondary`; `:211` `.brand` on today's amount (brand on white = 3.13:1)

`.textSecondary` 44 call sites, 10pt text, mostly: `UsageLimitRow.swift:64` "Resets in 6 hr 17 min"; `UsageDashboardView.swift:447` "Updated 14 min ago", `:510` "Extra usage", `:424,429` hover/"Manual only"; `MainMenuView.swift:203,268,274,347`; `AccountSwitcherView.swift:177,186`; `CostDetailView.swift` x16.

Coloured text on tiles:
- `UsageLimitRow.swift:85` `isLow ? fillColor : Color.primary` (percentage). `isLow` = used>=60 on the stock palette (`MenuBarConfig.swift:210`). So orange text (60-89%) and red text (>=90%) sit on the tinted tile.
- `UsageDashboardView.swift:110,120` and `CostDetailView.swift:41` cost figures `.green` (36pt rounded and 22pt: large text, 3:1 needed; system green on white is 2.22:1); `CostDetailView.swift:256` `.green` icon
- `UsageDashboardView.swift:502-514` Extra usage: icon and "On" `.orange` (2.31:1 on white), "Off" `.gray` (3.26:1)
- `UsageDashboardView.swift:247,262` `.yellow` warning icon (1.51:1 on white); `AccountSwitcherView.swift:97` `.green`, `:213` `.orange`, `:223` `.red` icons; `MainMenuView.swift:343` `.orange` footer icon
- `MainMenuView.swift:322` selected tab label `.white` on `Color.brand` (3.13:1)
- `Badge.swift:11` `.white` on `.brand` (3.13:1) and on system green (2.22:1 light, 2.02:1 dark)

---------------------------------------------------------------------------------------------------

## 4. Hard-coded appearance / ignoring system light-dark (VERIFIED by grep)

- **No** `preferredColorScheme`, `NSApp.appearance`, `window.appearance`, `popover.appearance`, or `.environment(\.colorScheme` in app code (the last appears only in `Tools/screenshots/make_screenshots.swift:137`). The popover follows whatever appearance NSPopover/SwiftUI resolve.
- Fixed regardless of appearance: `Color.brand`, `AppStyle.buttonTextColor`, `Color.white` Login button (`AccountSwitcherView.swift:326`), `Badge` white text, `.white` selected-tab label, the three identity hex colours, `cardShadowColor`.
- One explicit light/dark branch: `AccountSwitcherView.swift:14` (`@Environment(\.colorScheme)`) used at `:342`.
- All "adaptive" tokens adapt only in alpha and tint; none adapt in a way that makes the surface opaque, which is the real defect.
- **INFERRED, not verified, low confidence:** the popover's effective appearance comes from NSPopover (positioning view = the status-item button). On pre-26 macOS the menu-bar button's appearance follows the menu-bar tint (can differ from the system setting on a dark wallpaper). If that applies on macOS 27, the popover could render the dark tokens while the Mac is in light mode. Check on screen once; setting `popover.appearance = NSApp.effectiveAppearance` at show time (`StatusItemController.togglePopover`, line ~102) would make it deterministic. This Mac is currently in Light mode (`defaults read -g AppleInterfaceStyle` absent) and PixelSwitch is running (pid 1317).

---------------------------------------------------------------------------------------------------

## 5. Proposal (smallest change set), with computed contrast

All ratios are WCAG relative-luminance contrast computed by `scratchpad/propose2.py` (alpha composited properly). Needs: 4.5 for the 10-11pt text, 3.0 for graphical objects (bar fill, ring) and for large text.

### 5a. Token table (new values; `BrandColor.swift` unless noted)
| Token | Light | Dark | Replaces |
|---|---|---|---|
| `popoverBackground` (NEW) | #F2F2F5 | #1C1C1E | `.ultraThinMaterial` at MainMenuView:151 |
| `cardFill` | #FFFFFF | #2C2C2E | white@0.20 / black@0.21 |
| `cardFillStrong` | #FFFFFF | #343437 | white@0.25 / black@0.28 (active row is marked by the ring, not the fill) |
| `rowTile` (NEW) | #F2F2F5 | #3A3A3C | the two-layer cardFill + identity@0.10 in UsageLimitRow |
| `cardBorder` | #C7C7CC | #505054 | white@0.40 / white@0.20 |
| `tabFill` / `tabBorder` | #E6E6EB / #C7C7CC | #2C2C2E / #505054 | translucent pair |
| `progressTrack` | #D4D4DA | #1C1C1E | gray@0.18 / white@0.15 |
| `textSecondary` | #55555C | #B4B4BA | `Color.secondary` / white@0.55 (fixes all 44 call sites at once) |
| `textPrimary` | #1C1C1E | #F5F5F7 | unused today; `.primary` (labelColor) is fine, 14.9:1 on white |
| `subtleBrand` (promo banner) | #FDE8DF | #3D2A22 | brand@0.12 / @0.28 |
| `limitOK` / `limitWarn` / `limitCrit` (NEW, replace `.green/.orange/.red` in `MenuBarConfig.stockColor(.dashboard)`) | #1A7F37 / #A9480A / #C62828 | #30D158 / #FF9F0A / #FF8A84 | system colours |
| `activeAccountRing` | #E05A00 | #FF942E (unchanged) | #F56A00 light (2.71:1 vs the new popover bg, below 3) |
| `brandText` (NEW, for `.brand`-coloured TEXT: CostDetailView:206,211) | #C2410C | #FF8A65 | `Color.brand` as text |
| badge text (`Badge.swift:11`) | #111111 | #111111 | `.white`; fills stay `.brand` / `.green` |

### 5b. Computed ratios (all PASS)
Light, body text needs 4.5:
- primary #1C1C1E on popover #F2F2F5 15.23, card #FFFFFF 17.01, tile #F2F2F5 15.23, tab #E6E6EB 13.68, promo #FDE8DF 14.41
- secondary #55555C on popover 6.62, card 7.39, tile 6.62, tab 5.94, promo 6.26
- `.primary` (labelColor, black@0.847) stays at 14.94 on white
Dark:
- primary #F5F5F7 on popover #1C1C1E 15.63, card #2C2C2E 12.80, strong #343437 11.40, tile #3A3A3C 10.42, tab 12.80, promo 12.42
- secondary #B4B4BA on popover 8.25, card 6.75, strong 6.01, tile 5.50, tab 6.75, promo 6.56

Usage bar (graphics need 3.0), light / dark:
- track vs tile: #D4D4DA on #F2F2F5 = 1.32 (a groove, decorative) / #1C1C1E on #3A3A3C = 1.50
- OK fill vs track / tile / card: light #1A7F37 = 3.44 / 4.55 / 5.08; dark #30D158 = 8.42 / 5.61 / 6.89
- Warn fill: light #A9480A = 3.93 / 5.19 / 5.80; dark #FF9F0A = 8.28 / 5.52 / 6.78
- Crit fill: light #C62828 = 3.81 / 5.03 / 5.62; dark #FF8A84 = 7.47 / 4.98 / 6.12

Percentage text when isLow (needs 4.5, on tile): light warn 5.19, crit 5.03; dark warn 5.52, crit 4.98. (Option B if the colour-in-text is not wanted: render `isLow` figures in `.primary` and rely on the bar colour; then nothing to compute.)

Badges (small bold 10pt text, 4.5): #111111 on `Color.brand` #E86D45 = **6.03** (white today 3.13); on system green light #34C759 = **8.51** (white 2.22), dark #30D158 = **9.34** (white 2.02). Alternative keeping white text: deepen the fills to #C2410C (5.18) and #1A7F37 (5.08); white on #B93B0B 5.69.
Selected tab label: #111111 on #E86D45 6.03, or white on #C2410C 5.18 (today white on #E86D45 3.13). `.borderedProminent .tint(.brand)` buttons (AccountSwitcherView.swift:203-205, 303-305; SignInView.swift:70; SettingsSignInButtons.swift:19) also have a system white label on #E86D45 = 3.13:1; same fix as the tab if wanted.

Edges: active ring light #E05A00 vs card 3.73, vs popover 3.34 (F56A00: 3.02 / 2.71); dark #FF942E vs card 6.32, vs popover 7.72. Card border vs popover 1.51 light, 2.12 dark (decorative; the card/popover step is 1.12 light, 1.22 dark, so borders and the existing shadow carry the card edge).
Other call sites (light, white card): system green 36pt cost 2.22 -> `#1A7F37` 5.08; system orange "On" 2.31; system gray "Off" 3.26; system yellow icon 1.51; brand as text 3.13 -> `brandText` 5.18.

### 5c. Evidence of how bad "today" is (computed)
- Best case, opaque surfaces, measured NSColor values: secondary text 3.95:1 on #FFFFFF and 3.92:1 on #F6F6F6 (light) = FAIL 4.5; tertiary 1.88:1; quaternary 1.25:1.
- README light render (opaque #F9F9F9 backdrop; sampled tiles #E8F4EC, #E5EFF9, #F3ECF7 from `assets/screenshots/limits-light.png`): percentage text red #FF383C on session tile **3.16:1**, orange #FF8D28 on weekly tile **1.98:1**, on Fable tile **2.00:1**; "Resets" secondary text 3.83-3.88:1.
- ILLUSTRATIVE ONLY (the real backdrop is unknowable, and these ignore the material's own tint/blur): light secondary text on a `white@0.20` card over assumed pink/purple backdrops #E8B4D8 / #C77DBA / #9B6BD6 = 3.58 / 3.19 / 2.99. Dark secondary text on `black@0.21` over the same = 1.84 / 2.50 / 2.89. The point is the spread, not the values: with a translucent card the ratio is a function of the founder's wallpaper.

### 5d. Edit list for the proposal (re-grep the key before editing)
1. `BrandColor.swift`: tokens `cardFill`, `cardFillStrong`, `cardBorder`, `tabFill`, `tabBorder`, `textSecondary`, `subtleBrand`, `progressTrack`, `activeAccountRing`; add `popoverBackground`, `rowTile`, `limitOK/Warn/Crit`, `brandText`; add the `ShapeStyle where Self == Color` shims (`:94-106` pattern).
2. `MainMenuView.swift`: `.background(.ultraThinMaterial)` -> `.background(Color.popoverBackground)`; `:322` tab label colour (if badge option 1 is used).
3. `UsageLimitRow.swift:92-99`: replace the `ZStack` of two fills with `RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.rowTile)`. `:85` needs no change if `limit*` are the fills. Chip (`:105,108`) left as is; decorative (`accessibilityHidden`), glyph/chip contrast 1.92 (session), 2.93 (weekly), 3.30 (fable); say so if the founder cares.
4. `Badge.swift:11`: `.white` -> `Color(red: 0x11/255, green: 0x11/255, blue: 0x11/255)`.
5. `MenuBarConfig.swift:236-241`: `.dashboard` case returns `Color.limitCrit / .limitWarn / .limitOK`. `.menuBar` case and the custom-colour path untouched. Note the stock palette's doc comment "kept verbatim ... pixel-identical" (`:228-230`) becomes false; this is a deliberate repaint.
6. `AccountSwitcherView.swift:231` `.clear` -> `.cardFill` (inactive rows need a fill once the popover is opaque and Usage cards are solid); `:339-347` stroke -> `.cardBorder` (drops the `colorScheme` branch and `:14`); `:269` `.tertiary` -> `.textSecondary`.
7. Optional call-site clean-ups: `UsageDashboardView.swift:192,199` (zero counts, quaternary), `:110,120` and `CostDetailView.swift:41` (`.green` -> a `costGreen` token #1A7F37 / #30D158), `:502-514` (Extra usage On/Off), `:247,262` (yellow), `CostDetailView.swift:206,211` (`brandText`), `:216` (blue@0.6 bar, 2.22:1), `:223` is `.system(size: 8)` text (size, not colour).
8. Optional Settings: swap `.secondary` for `.textSecondary` in the Settings files listed in 1c.

### 5e. Alternatives (not recommended, for the record)
- `.regularMaterial` / `.thickMaterial`: still translucent, ratio still wallpaper-dependent.
- `Color(nsColor: .windowBackgroundColor)` as popover bg: opaque and adaptive, but measured value is pure #FFFFFF in light on this macOS (cards then need borders/shadow to separate), and `secondaryLabelColor` on it is still 3.95:1.
- A custom `NSPanel` instead of `NSPopover`: full control of chrome, much larger change (lifecycle, positioning, dismissal).

---------------------------------------------------------------------------------------------------

## 6. How to verify without CI

### 6a. Toolchain on this Mac (VERIFIED, by commands)
- `swiftc --version` -> Apple Swift 6.4.2 (swiftlang-6.4.2.1.9), target arm64-apple-macosx27.0.0.
- `xcrun --show-sdk-path` -> `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk` -> MacOSX27.0.sdk (has SwiftUI.framework, AppKit.framework). `xcrun --sdk macosx --show-sdk-path` -> `/Applications/Xcode.app/.../MacOSX27.2.sdk`.
- **`/Applications/Xcode.app` EXISTS: `xcodebuild -version` -> Xcode 27.2, Build 27B52** (dir dated Nov 4). `xcodegen` is at /opt/homebrew/bin/xcodegen. This contradicts `docs/SESSION-HANDOVER.md:70` and CLAUDE.md-level wording "There is no Xcode on this Mac". I did not try an xcodebuild build, so a local signed build is unproven; releases still go through CI.
- `scripts/typecheck.sh` uses plain `xcrun --show-sdk-path` (the CLT SDK) with `-typecheck -swift-version 6 -target arm64-apple-macos14.0`, so it works either way.

### 6b. A harness ALREADY EXISTS: `Tools/screenshots/make_screenshots.swift` (231 lines)
It is the thing that produced `assets/screenshots/*-{light,dark}.png`. It uses `ImageRenderer` (`:139`), scale 2 (`:140`), `.environment(\.colorScheme, scheme)` (`:137`), and writes PNGs through `NSBitmapImageRep` (`:141-146`). Compile line is in its header (`:11-19`), run from the repo root (it loads the logo by relative path at `:195`):
```
swiftc -swift-version 6 -target arm64-apple-macos14.0 -parse-as-library -o $SCRATCH/pixelshots \
  PixelSwitch/Views/Components/UsageLimitRow.swift PixelSwitch/Views/Components/AccountGlyph.swift \
  PixelSwitch/Models/Account.swift PixelSwitch/Models/String+Obfuscation.swift \
  PixelSwitch/Models/MenuBarConfig.swift PixelSwitch/Models/MenuBarModule.swift \
  PixelSwitch/Models/AppStyle.swift PixelSwitch/Models/BrandColor.swift \
  PixelSwitch/Models/AccountPalette.swift PixelSwitch/Services/L10n.swift \
  Tools/screenshots/make_screenshots.swift && $SCRATCH/pixelshots $SCRATCH/out
```
Pass an output dir under the scratchpad as `argv[1]` (default is `assets/screenshots`, which would overwrite README images). I did NOT build or run it (instruction), so "it compiles today on Swift 6.4.2/SDK 27" is unverified. The commit message of 7e093dc says it was used to measure contrast ("measured on the rendered cards", 12.9-13.3:1) so the method works.

What the harness must change to be a valid check of THIS fix:
1. `make_screenshots.swift:136` paints its own solid backdrop (`Color(white: 0.11)` / `0.97`). That is why the README pictures looked readable while the real popover is not. Replace with `Color.popoverBackground` once it exists (and optionally render the same card over a pink/purple gradient to prove the opaque design ignores the wallpaper).
2. Its `Card` (`:50-129`) is a hand-copied re-implementation, NOT the app's `accountUsageCard` (that is `private` in `UsageDashboardView.swift:224` and needs `AppState` + environment objects). It draws its own tinted capsule badges (`:83-96`, `Color.green.opacity(0.22)` with green text) instead of the app's `Badge` (solid fill, white text), and uses `.secondary` (`:106,117`) instead of `.textSecondary`. To verify the Max/Active fix the harness must instantiate `Badge` (add `PixelSwitch/Views/Components/Badge.swift` to the compile list) and `.textSecondary`. If the builder wants a faithful check, extract the card body into a component taking plain values; larger change, not needed for colours.
3. Compile set: the 10 files above + `Badge.swift` (+ `AppStyle.swift` already in). New tokens in `BrandColor.swift` need nothing more. `Tests/run-unit-tests.sh:16-17` also compiles `BrandColor.swift` and `AccountPalette.swift` standalone, so any new token there must stay self-contained (SwiftUI/AppKit only). No test asserts a colour value (only `AccountPalette.assignment`/`fnv1a`, `Tests/UnitTests/main.swift:253-263`).
4. Limits: `ImageRenderer` has no window-server backdrop, so `.ultraThinMaterial` and NSPopover chrome (arrow, corner radius, glass edge) cannot be reproduced (INFERRED, not tested). It can only verify the opaque design, which is the point. To see the real popover: push a branch, `gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>`, `scripts/verify-ci-artifact.sh <run-id>`, open it, click the status item, `screencapture -R`. That needs Screen Recording and Accessibility permission for whatever drives it (unverified). Measure contrast by sampling the rendered PNG: `scratchpad/nscolor/px.swift` shows the pattern (NSBitmapImageRep.colorAt).

---------------------------------------------------------------------------------------------------

## 7. File set for a colour-only change

Pure colour/token, safe to give one builder:
- `PixelSwitch/Models/BrandColor.swift` (also in the unit-test compile; no colour tests)
- `PixelSwitch/Views/Components/Badge.swift`
- `PixelSwitch/Views/Components/UsageLimitRow.swift`
- `Tools/screenshots/make_screenshots.swift` (harness) and then regenerated `assets/screenshots/*.png`
- `PixelSwitch/Models/AppStyle.swift` only if the shadow or card padding is touched (not needed)

Colour edits inside files that ALSO hold logic other work will touch (coordinate or serialise):
- `PixelSwitch/Views/MainMenuView.swift`: popover height measurement (`:4-190`), tab state, promo, footer errors. **Plan-tier display overlaps:** header `:262-264` (`Badge(text: sub ...)`).
- `PixelSwitch/Views/UsageDashboardView.swift`: double-click switch + cursor stack (`:325-364`), usage error states and Retry (`:244-285,369-395`), `cardFooter` (`:408-435`), auto-switch Manual-only label (`:426-432`). **Plan-tier display overlaps directly:** `accountHeader` `:452-474` (plan `Badge` at `:470-472`).
- `PixelSwitch/Views/AccountSwitcherView.swift`: label editing, switch/reauth/remove actions, sign-in states (`:258-283`), auto-switch "Manual only" (`:183-189`). **Plan-tier overlaps:** `:163-168`, and auth overlaps: `:208-226`, `:258-283`.
- `PixelSwitch/Models/MenuBarConfig.swift`: persisted settings, `limitIsLow` threshold (`>=60`), module list; also read/written by `Control/SettingsStore.swift`, `Control/SettingsCatalog.swift`, `PixelSwitchCLI/CLIRunner.swift` (remote control). Only edit `stockColor` `.dashboard` branch (`:236-241`). Leave the `default*Hex` constants (`:44-47`) alone: they are persisted settings values exposed over the control socket.
- `PixelSwitch/Views/CostDetailView.swift`: cost maths/history window, no auth/switch logic (low risk).
- `PixelSwitch/Views/SettingsAccountsTab.swift` (threshold + auto-switch editing, `Badge` at `:81`) and the other Settings files: only if the optional Settings swap is wanted.
- `PixelSwitch/Services/StatusItemController.swift`: only if the optional `popover.appearance` fix is chosen; it holds install/lifecycle.
Release ritual if shipped: `RELEASE_NOTES.md` first, then `scripts/release.sh`; two-part version (1.9). `PixelSwitchWidget/` has its own colours and is NOT covered by any token change here.

---------------------------------------------------------------------------------------------------

## 8. Verified vs inferred

**Verified (read, grepped, or computed):** all file:line anchors; the absence of any appearance override or NSVisualEffectView; `.ultraThinMaterial` is the only Material; token definitions and alphas; call-site counts; AppKit colour values on this Mac (macOS 27.0.1); text point sizes; Xcode 27.2 presence; the existence and content of the screenshot harness; all contrast ratios (python, WCAG 2.x formula); README tile colours sampled from the PNG.
**Inferred / not measured:** (a) that NSPopover's own chrome is translucent on macOS 27 (standard behaviour; not seen on screen); (b) that an opaque SwiftUI fill covers the popover body fully, leaving only the arrow/border as system material; (c) that `ImageRenderer` cannot render materials; (d) the popover-appearance-follows-menu-bar risk in section 4; (e) any real contrast over the founder's actual wallpaper; (f) that the existing harness still compiles; (g) that the proposed hex values look good; they pass the numbers only, nobody has looked at them.

## 9. Gotchas

1. **The README screenshots are not what the popover looks like.** They sit on a solid backdrop (harness line 136) and use a re-implemented card with different badges. The last "readability" fix (7e093dc, 2026-09-22) measured contrast on those renders, so it could not see the translucency problem. Do not trust harness pictures as evidence about the live popover until the backdrop is the new opaque token.
2. **`.textSecondary` light is just `Color.secondary`, which already fails 4.5 even on white (3.95:1).** Changing only the background to opaque white will still leave every "Resets in", "Updated ... ago", "Extra usage" line failing in light mode. The text token and the background must change together.
3. **Dark `textSecondary` (white@0.55) is fine on an opaque dark surface (5.5-8.3:1) but the dark `cardFill` (black@0.21) is nearly transparent**, so dark mode over a light wallpaper is the worst case (illustrative 1.8:1).
4. **`MenuBarConfig.swift:228-230` comment says the stock palette is kept "pixel-identical"**; changing `.dashboard` stock colours is a visible repaint (justified here, but the comment and `customizesLimitBarColors` doc ("never silently repaints") will contradict it). User-chosen custom colours are fixed hex and cannot be made AA by this change.
5. **Inactive Accounts-tab rows are `.clear`** (`AccountSwitcherView.swift:231`): fine on a translucent popover, a floating border on an opaque one; give them `.cardFill`.
6. **`Badge` is used with `.green` in 3 files and `.brand` in 2**; any token that changes `.brand` itself also changes the tab indicator, tinted prominent buttons and the promo icon. Prefer changing the Badge text colour, not `.brand`.
7. **`UsageChartView.swift` is dead code** (no references); do not spend effort on it.
8. **`AccountPalette.swift:5-11,32-36` comments** mention a faint "wash"; there is no wash function any more. The per-account colour is only the 4pt edge and the glyph; the "coloured border" the founder sees is the orange `activeAccountRing`, not the palette.
9. **The handover's "no Xcode" is stale** (Xcode 27.2 is installed). Useful, but unverified for a local build.
10. **Settings and the sign-in window are already opaque** and native; only the popover has the see-through problem. Settings text is system `.secondary` (3.95:1 light), a milder issue.
11. **Widget (`PixelSwitchWidget/PixelSwitchWidget.swift`) has its own `brandColor` and tints** (`:6,135,246,421`); out of scope, not touched by `BrandColor.swift`.
