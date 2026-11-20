// 2.3 popover tidy: the popover has no Accounts tab, its footer adds an account and every
// icon says what it is for, Settings -> Accounts rows carry Switch, Re-sign, Edit label and
// Remove, and the expired message no longer points at the wrong icon.
//
// What is pinned here: the Switch rule (pure, `SwitchAvailability`), then the wiring and the
// strings a view test cannot compile, read from source as the other source pins do.
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from AccountPlanTests.
// Uses the global `check`.
import Foundation

private func tidySource(_ path: String) -> String {
    (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
}

/// The text from `start` up to (not including) the first `end` after it, or "" when either is missing.
private func tidyBlock(_ source: String, from start: String, to end: String) -> String {
    guard let a = source.range(of: start), let b = source.range(of: end, range: a.upperBound..<source.endIndex) else { return "" }
    return String(source[a.lowerBound..<b.lowerBound])
}

private func tidyCount(_ source: String, _ needle: String) -> Int {
    source.components(separatedBy: needle).count - 1
}

@MainActor func runPopoverTidyTests() {
    switchAvailabilityTests()
    popoverTabWiringTests()
    footerWiringTests()
    settingsRowWiringTests()
    expiredMessageTests()
    tidyStringTests()
}

// MARK: - The Switch rule

@MainActor private func switchAvailabilityTests() {
    func of(active: Bool = false, ended: Bool = false, switching: Bool = false, loggingIn: Bool = false) -> SwitchAvailability {
        SwitchAvailability.of(isActive: active, signInEnded: ended, isSwitching: switching, isLoggingIn: loggingIn)
    }
    check(of() == .available && of().isAvailable, "switch rule: an ordinary account can be switched to")
    check(of(active: true) == .alreadyActive && !of(active: true).isAvailable, "switch rule: the account in use cannot be switched to")
    check(of(ended: true) == .needsSignIn && !of(ended: true).isAvailable, "switch rule: an account whose sign-in ended cannot be switched to until it is signed in again")
    check(of(switching: true) == .busy && of(loggingIn: true) == .busy, "switch rule: while a switch or a sign-in runs, Switch waits")
    check(of(active: true, ended: true, switching: true, loggingIn: true) == .alreadyActive,
          "switch rule: the first reason that applies wins, and being on the account comes first")
    check(of(ended: true, switching: true) == .needsSignIn, "switch rule: an ended sign-in is named before a busy app")

    check(!SwitchAvailability.isBusy(isSwitching: false, isLoggingIn: false) && SwitchAvailability.isBusy(isSwitching: true, isLoggingIn: false)
          && SwitchAvailability.isBusy(isSwitching: false, isLoggingIn: true) && SwitchAvailability.isBusy(isSwitching: true, isLoggingIn: true),
          "busy rule: busy means a switch or a sign-in is running")
    check(SwitchAvailability.canAddOrRemove(isSwitching: false, isLoggingIn: false), "busy rule: Add, Sign in, Re-sign and Remove are on when nothing is running")
    check(!SwitchAvailability.canAddOrRemove(isSwitching: true, isLoggingIn: false) && !SwitchAvailability.canAddOrRemove(isSwitching: false, isLoggingIn: true),
          "busy rule: they all wait for a running switch or sign-in, as the control API's own requests do")
    check(SwitchAvailability.signInEnded(isExpired: true, isNoSubscription: false)
          && !SwitchAvailability.signInEnded(isExpired: true, isNoSubscription: true)
          && !SwitchAvailability.signInEnded(isExpired: false, isNoSubscription: false),
          "switch rule: a sign-in has ended when the card says expired, and not for no active subscription (signing in again does not fix that)")
}

// MARK: - The popover has no Accounts tab

@MainActor private func popoverTabWiringTests() {
    let menu = tidySource("PixelSwitch/Views/MainMenuView.swift")
    check(!menu.isEmpty, "tab pins: MainMenuView is found from the repo root")

    let tabEnum = tidyBlock(menu, from: "enum Tab: String, CaseIterable {", to: "    var body: some View {")
    check(!tabEnum.isEmpty && tabEnum.contains("case usage, costs") && !tabEnum.contains("accounts") && !tabEnum.contains("person.2"),
          "tab pins: the popover's tab enum is Usage and Costs, with no Accounts case or icon")
    check(!menu.contains("AccountSwitcherView") && !menu.contains("case .accounts"), "tab pins: the popover's content switch has no Accounts branch")
    check(!FileManager.default.fileExists(atPath: "PixelSwitch/Views/AccountSwitcherView.swift"),
          "tab pins: AccountSwitcherView.swift is deleted")

    // Nothing in the app, the tools or the tests still names the deleted view, except a note that it is gone.
    var stragglers: [String] = []
    for folder in ["PixelSwitch", "PixelSwitchCLI", "PixelSwitchWidget", "Shared", "Tools"] {
        guard let walker = FileManager.default.enumerator(atPath: folder) else { continue }
        for case let path as String in walker where path.hasSuffix(".swift") {
            if tidySource("\(folder)/\(path)").contains("AccountSwitcherView(") { stragglers.append("\(folder)/\(path)") }
        }
    }
    check(stragglers.isEmpty, "tab pins: nothing builds an AccountSwitcherView any more", "\(stragglers)")

    let settings = tidySource("PixelSwitch/Views/SettingsView.swift")
    check(!settings.contains("Usage and Accounts tabs") && settings.contains("on the Usage tab"),
          "tab pins: the Appearance text no longer names an Accounts tab on the popover")
    let shots = tidySource("Tools/screenshots/make_screenshots.swift")
    check(!shots.contains("tab(\"person.2\", \"Accounts\""), "tab pins: the screenshot harness's tab bar has no Accounts tab")

    // The popover header keeps a long address on one line.
    let header = tidySource("PixelSwitch/Views/Components/AccountIdentityHeader.swift")
    let name = tidyBlock(header, from: "Text(identity.primary)", to: "if let chip {")
    check(name.contains(".lineLimit(1)") && name.contains(".minimumScaleFactor(0.85)") && name.contains(".truncationMode(.middle)") && name.contains(".help(identity.primary)"),
          "header pins: the popover header's name is one line, shrinks a little, loses its middle and has the whole address on hover")
    let longAddress = "build@bespokewoodcraftstudio.test"
    check(shots.contains("account(\"\(longAddress)\"") && longAddress.count == 33 && !longAddress.contains("-"),
          "header pins: the screenshot harness draws a 33-character address with no hyphen in the header")

    // Settings opens on the tab last used.
    check(settings.contains("@AppStorage(\"settingsSelectedTab\") private var selectedTab: SettingsTab = .general")
          && settings.contains("TabView(selection: $selectedTab)") && tidyCount(settings, ".tag(SettingsTab.") == 5
          && settings.contains("enum SettingsTab: String {"),
          "settings tab pins: Settings keeps the last tab in a stored selection and tags all five tabs")
}

// MARK: - The footer

@MainActor private func footerWiringTests() {
    let menu = tidySource("PixelSwitch/Views/MainMenuView.swift")
    let file = tidySource("PixelSwitch/Views/Components/PopoverFooter.swift")
    let footer = tidyBlock(file, from: "struct PopoverFooter: View {", to: "/// What a footer icon looks like")
    let look = tidyBlock(file, from: "struct FooterIconLabel: View {", to: "/// One icon in the popover's footer")
    let button = tidyBlock(file, from: "struct FooterIconButton: View {", to: "/// A sign-in is running")
    check(!menu.isEmpty && !footer.isEmpty && !look.isEmpty && !button.isEmpty, "footer pins: the footer, its icon and the popover are found")

    // Five icons, in order, through the one component.
    check(tidyCount(footer, "FooterIconButton(") == 5, "footer pins: the footer has five icons, each a FooterIconButton")
    let symbols = ["plus.circle", "person.badge.plus", "arrow.clockwise", "gear", "power"]
    var last = footer.startIndex
    var inOrder = true
    for symbol in symbols {
        if let found = footer.range(of: "symbol: \"\(symbol)\"", range: last..<footer.endIndex) { last = found.upperBound } else { inOrder = false }
    }
    check(inOrder, "footer pins: left to right they are Add current account, Sign in a new account, Refresh, Settings, Quit")

    // Each has a tooltip and a spoken label; the one component applies both.
    check(tidyCount(footer, "help: String(localized:") == 5 && tidyCount(footer, "label: String(localized:") == 5,
          "footer pins: every footer icon is given a tooltip and a spoken label")
    check(button.contains(".help(disabledHelp ?? help)") && button.contains(".accessibilityLabel(label)"),
          "footer pins: the component puts the tooltip on .help (the reason it is off, else what it is for) and the spoken label on .accessibilityLabel")
    // The tooltip is on a wrapper, not on the button: macOS may not show one on a disabled control.
    let buttonOnly = tidyBlock(button, from: "Button(action: action) {", to: ".accessibilityLabel(label)")
    check(!buttonOnly.isEmpty && !buttonOnly.contains(".help(") && button.contains("        }\n        .contentShape(Rectangle())\n        .help(disabledHelp ?? help)"),
          "footer pins: the footer icon's tooltip sits on a wrapper around the button, so it shows while the icon is off")
    // Bigger and easier to hit: a 15 pt symbol in a 28 pt square, the whole square clickable, a hover state, the muted tone.
    check(look.contains(".font(.system(size: 15))") && look.contains(".frame(width: 28, height: 28)") && look.contains(".contentShape(Rectangle())"),
          "footer pins: each icon is a 15 pt symbol in a 28 x 28 pt target the whole of which is clickable")
    check(look.contains("Color.textSecondary") && look.contains("hovering && !isDisabled") && button.contains(".onHover") && button.contains(".disabled(isDisabled)"),
          "footer pins: each icon is in the muted text tone, lights up under the pointer and can be disabled")
    check(!footer.contains(" Button {") && !footer.contains(" Button("),
          "footer pins: the footer draws no buttons of its own, only FooterIconButton")

    // The actions the icons run, and the two add icons off while a sign-in runs.
    let actions = tidyBlock(menu, from: "private var footerView: some View {", to: "// MARK: - Sign-in and add-account bars")
    check(actions.contains("appState.loginNewAccount()") && actions.contains("confirmingAddCurrent = true") && actions.contains("await appState.refresh()")
          && actions.contains(".pixelswitchOpenSettings") && actions.contains("NSApplication.shared.terminate(nil)"),
          "footer pins: the icons start the sign-in, ask to add the current account, refresh, open Settings and quit")
    check(tidyCount(footer, "disabledHelp: busyReason") == 2, "footer pins: both add icons are off, with the reason as their tooltip, while the popover says busy")
    check(actions.contains("busyReason: addIconsBusy") && menu.contains("!SwitchAvailability.canAddOrRemove(isSwitching: appState.switchingTo != nil, isLoggingIn: appState.isLoggingIn)"),
          "footer pins: the popover is busy while a switch or a sign-in runs, by the control API's own rule")
    let controller = tidySource("PixelSwitch/Control/AppController.swift")
    check(controller.contains("if appState.isSwitching || appState.isLoggingIn {") && controller.contains("throw ControlError(.busy, \"A switch or sign-in is in progress."),
          "footer pins: the control API still refuses while a switch or a sign-in runs, the rule the buttons copy")

    // The confirmation the popover's Add Current Account always had, and the sign-in state, are in the popover.
    let confirm = tidyBlock(file, from: "struct AddCurrentConfirmBar: View {", to: "\n}\n")
    check(confirm.contains("This will capture the currently logged-in Claude Code account.") && confirm.contains("Button(\"Add Account\", action: onAdd)"),
          "footer pins: the confirmation bar asks the same question and has Cancel and Add Account")
    let addBar = tidyBlock(menu, from: "private var addCurrentConfirmBar: some View {", to: "\n}\n")
    check(addBar.contains("await appState.addAccount()") && addBar.contains("confirmingAddCurrent = false"),
          "footer pins: Add current account is only done after the confirmation, which only its Add Account button gives")
    if let guardRange = addBar.range(of: "guard !addIconsBusy else {"), let add = addBar.range(of: "await appState.addAccount()") {
        check(guardRange.lowerBound < add.lowerBound, "footer pins: the confirmation re-checks busy before it adds the account")
    } else {
        check(false, "footer pins: the confirmation re-checks busy before it adds the account")
    }
    check(!actions.contains("addAccount()"), "footer pins: the icon alone never adds the account")
    let signingIn = tidyBlock(file, from: "struct SigningInBar: View {", to: "/// The confirmation \"Add current account\" always had")
    check(signingIn.contains("Text(\"Signing in…\")") && signingIn.contains("Finish in the sign-in window.")
          && signingIn.contains("Button(\"Show sign-in window\", action: onShowWindow)")
          && menu.contains("NotificationCenter.default.post(name: .pixelswitchShowSignIn, object: nil)"),
          "footer pins: while signing in the popover shows Signing in… and a button back to the sign-in window")
    check(menu.contains("if appState.isLoggingIn {\n                signingInBar\n                    .measureChromeHeight()\n            } else if confirmingAddCurrent {\n                addCurrentConfirmBar\n                    .measureChromeHeight()")
          && menu.contains("tabBar\n                .measureChromeHeight()") && menu.contains("footerView\n                .measureChromeHeight()"),
          "footer pins: the bars are measured chrome, so the popover is sized to them and the footer")

    // The Usage tab's last-updated row has a tooltip.
    let dashboard = tidySource("PixelSwitch/Views/UsageDashboardView.swift")
    let updated = tidyBlock(dashboard, from: "// Last updated", to: ".padding(.vertical, 12)")
    check(updated.contains(".help(String(format: String(localized: \"Usage last refreshed at %@. It refreshes on its own.\", bundle: L10n.bundle)"),
          "footer pins: the last-updated row says on hover what it is and that it refreshes on its own")
}

// MARK: - The Settings row

@MainActor private func settingsRowWiringTests() {
    let row = tidySource("PixelSwitch/Views/SettingsAccountRow.swift")
    let tab = tidySource("PixelSwitch/Views/SettingsAccountsTab.swift")
    let buttons = tidySource("PixelSwitch/Views/SettingsSignInButtons.swift")
    check(!row.isEmpty && !tab.isEmpty && !buttons.isEmpty, "row pins: the Settings sources are found from the repo root")

    // The four things only the popover tab had: Switch, Re-sign, Edit label and Remove.
    check(row.contains("Button { onSwitch() } label: { Text(\"Switch\") }") && row.contains(".disabled(switchOffReason != nil)")
          && row.contains(".help(switchOffReason.map { Text(verbatim: $0) } ?? Text(\"Switch to this account\"))"),
          "row pins: the row has a Switch button that is off with its reason as the tooltip")
    // The tooltip is on a wrapper around each disabled-able button: macOS may not show one on a disabled control.
    let switchButton = tidyBlock(row, from: "Button { onSwitch() } label: { Text(\"Switch\") }", to: ".accessibilityInputLabels([Text(\"Switch\")])")
    let resignButton = tidyBlock(row, from: "Button { onResign() } label: {", to: ".accessibilityInputLabels([Text(\"Re-sign\")])")
    check(!switchButton.isEmpty && !switchButton.contains(".help(") && !resignButton.isEmpty && !resignButton.contains(".help(")
          && row.contains("            }\n            .contentShape(Rectangle())\n            // The reason it is off, else what it does.\n            .help(switchOffReason")
          && row.contains("                .contentShape(Rectangle())\n                .help(actionsBusy ? Text(\"Wait for the current switch or sign-in to finish.\")"),
          "row pins: Switch's reason and Re-sign's tooltip sit on wrappers around their buttons, so they show while the button is off")
    let removeItem = tidyBlock(row, from: "Button(\"Remove account\", role: .destructive) { onRemove() }", to: "} label: {")
    check(removeItem.contains(".disabled(actionsBusy)"), "row pins: Remove account is off while a switch or a sign-in runs")
    check(row.contains("Button { onResign() }") && row.contains("Image(systemName: \"arrow.triangle.2.circlepath\")") && row.contains(".disabled(actionsBusy)"),
          "row pins: the row has Re-sign, with the card's icon, off while a switch or sign-in runs")
    check(row.contains("Button(\"Edit label\") { beginEditing() }") && row.contains("TextField(\"Custom label\", text: draftStorage.projectedValue)")
          && row.contains("onLabel(draftStorage.wrappedValue)"),
          "row pins: the row has Edit label, an inline field that saves what was typed")
    check(row.contains("Button(\"Remove account\", role: .destructive) { onRemove() }"), "row pins: the row has Remove account, marked destructive")
    check(row.contains("Menu {") && row.contains("Image(systemName: \"ellipsis.circle\")") && row.contains(".help(Text(\"More actions\"))")
          && row.contains(".accessibilityLabel(Text(\"More actions for \\(title)\"))"),
          "row pins: Edit label and Remove sit in a More actions menu that has a tooltip and a spoken label")
    check(row.contains(".accessibilityLabel(Text(\"Switch to \\(title)\"))") && row.contains(".accessibilityLabel(Text(\"Re-authenticate \\(title)\"))"),
          "row pins: Switch and Re-sign are spoken with the account's name")

    // The row stays plain inputs and closures (the offscreen renders compile it without the app).
    check(!row.contains("appState.") && !row.contains("@AppStorage(") && !row.contains("@EnvironmentObject private"),
          "row pins: the row still reads no app state, so the renders can draw it")

    // The tab hands each action the same AppState path the popover used.
    check(tab.contains("onSwitch: { Task { await appState.switchTo(account) } }"), "row pins: Switch calls appState.switchTo")
    check(tab.contains("onResign: { appState.reauthenticateAccount(account) }"), "row pins: Re-sign calls appState.reauthenticateAccount")
    check(tab.contains("onLabel: { appState.updateAccountLabel(account, label: $0) }"), "row pins: Edit label saves through appState.updateAccountLabel")
    check(tab.contains("onRemove: { pendingRemoval = account }") && tab.contains("Button(\"Remove account\", role: .destructive) {")
          && tidyCount(tab, "appState.removeAccount(account)") == 1 && tab.contains(".confirmationDialog("),
          "row pins: Remove asks first and only the confirmation calls appState.removeAccount")
    check(tab.contains("switch appState.switchAvailability(for: account) {") && tab.contains("SwitchAvailability.canAddOrRemove("),
          "row pins: the tab turns the pure Switch rule into the row's inputs")
    let confirmAction = tidyBlock(tab, from: "Button(\"Remove account\", role: .destructive) {", to: "Button(\"Cancel\", role: .cancel) {}")
    check(confirmAction.contains("guard SwitchAvailability.canAddOrRemove(isSwitching: appState.switchingTo != nil, isLoggingIn: appState.isLoggingIn) else {")
          && confirmAction.contains("appState.removeAccount(account)")
          && (confirmAction.range(of: "guard SwitchAvailability")?.lowerBound ?? confirmAction.endIndex) < (confirmAction.range(of: "appState.removeAccount(account)")?.lowerBound ?? confirmAction.startIndex),
          "row pins: the Remove confirmation re-checks busy before it removes, like the control API")
    check(tab.contains("actionsBusy: !SwitchAvailability.canAddOrRemove("), "row pins: Re-sign and Remove are off by the same busy rule")
    check(tab.contains("onPriority:") && tab.contains(".onMove {") && tab.contains("onSession:") && tab.contains("onWeekly:") && tab.contains("SettingsSignInButtons()"),
          "row pins: priority, drag reorder, thresholds and the add buttons are all still there")

    // The signing-in state the popover tab had.
    check(buttons.contains("if appState.isLoggingIn {") && buttons.contains("Text(\"Signing in…\")") && buttons.contains("Button(\"Show sign-in window\")")
          && buttons.contains(".pixelswitchShowSignIn"),
          "row pins: while signing in, Settings shows Signing in… and a button back to the sign-in window")
    check(buttons.contains("let switching = appState.switchingTo != nil") && buttons.contains(".disabled(switching)")
          && buttons.contains(".help(switching ? String(localized: \"Wait for the current switch or sign-in to finish.\", bundle: L10n.bundle) : \"\")"),
          "row pins: while a switch runs, Settings' two add buttons are off and the wrapper says why")
    if let guardRange = buttons.range(of: "guard SwitchAvailability.canAddOrRemove("), let add = buttons.range(of: "await appState.addAccount()") {
        check(guardRange.lowerBound < add.lowerBound, "row pins: Settings' Add Account confirmation re-checks busy before it adds the account")
    } else {
        check(false, "row pins: Settings' Add Account confirmation re-checks busy before it adds the account")
    }

    // One rule for Switch and the card's double-click.
    let dashboard = tidySource("PixelSwitch/Views/UsageDashboardView.swift")
    let canSwitch = tidyBlock(dashboard, from: "private func canSwitch(to account: Account) -> Bool {", to: "\n    }\n")
    check(canSwitch.contains("appState.switchAvailability(for: account).isAvailable") && !canSwitch.contains("account.isActive"),
          "row pins: a double-click on a Usage card follows the rule Settings' Switch follows, so an expired account cannot be switched to by double-click")
    check(dashboard.contains(".help(canSwitch(to: account) ? String(localized: \"Double-click to switch\", bundle: L10n.bundle) : \"\")"),
          "row pins: the card's Double-click to switch tooltip appears only when the double-click would work")
    let app = tidySource("PixelSwitch/AppState.swift")
    let rule = tidyBlock(app, from: "func switchAvailability(for account: Account) -> SwitchAvailability {", to: "\n    }\n")
    check(rule.contains("SwitchAvailability.of(") && rule.contains("isSwitching: switchingTo != nil") && rule.contains("isLoggingIn: isLoggingIn")
          && rule.contains("SwitchAvailability.signInEnded(isExpired: card?.isExpired == true, isNoSubscription: card?.isNoSubscription == true)"),
          "row pins: AppState answers Switch from what it publishes, the card's state and the busy flags")
}

// MARK: - The expired message

@MainActor private func expiredMessageTests() {
    let app = tidySource("PixelSwitch/AppState.swift")
    check(!app.isEmpty && !app.contains("(↻)"), "message pins: no message in AppState points at the circle-arrow icon")
    check(tidyCount(app, "Session expired. Click Re-sign to fix.") == 1
          && tidyCount(app, "Its saved login belonged to another account and was removed. Click Re-sign to fix.") == 2,
          "message pins: the expired and the belonged-to-another messages both say Click Re-sign to fix.")

    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    let expired = "Session expired. Click Re-sign to fix."
    let belonged = "Its saved login belonged to another account and was removed. Click Re-sign to fix."
    for (index, table) in tables.enumerated() {
        let name = languages[index]
        check(table.keys.allSatisfy { !$0.contains("Re-authenticate (↻)") } && table.values.allSatisfy { !$0.contains("(↻)") && !$0.contains("（↻）") },
              "message strings: \(name) has no message that points at ↻")
        let word = table["Re-sign"] ?? ""
        check(!word.isEmpty && (table[expired] ?? "").contains(word) && (table[belonged] ?? "").contains(word),
              "message strings: \(name) names the button by the table's own word for Re-sign (\(word))")
    }
    check(tables.dropFirst().allSatisfy { $0[expired] != expired && $0[belonged] != belonged },
          "message strings: de, fr, ja and zh-Hans are translated, not copied")

    // Linux says the same.
    let cycle = tidySource("linux/pixelswitch/usagecycle.py")
    let model = tidySource("linux/pixelswitch/gui/model.py")
    check(cycle.contains("MESSAGE_RESIGN = \"\(expired)\"") && cycle.contains("MESSAGE_BELONGED = \"\(belonged)\"")
          && model.contains("TEXT_RESIGN_MESSAGE = \"\(expired)\"") && model.contains("\"Re-sign\" in message"),
          "message pins: the Linux daemon and its settings window use the same two sentences, and still recognise them as needing a sign-in")
}

// MARK: - The new strings

@MainActor private func tidyStringTests() {
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    let keys = [
        "Add current account: save the one Claude Code is signed in to now",
        "Sign in a new account: open the sign-in window",
        "Switch to this account",
        "Switch to %@",
        "You're using this account now.",
        "Sign in to this account again before you switch to it.",
        "Wait for the current switch or sign-in to finish.",
        "Save label",
        "More actions",
        "More actions for %@",
        "Remove %@?",
        "PixelSwitch forgets this account and deletes its saved login. If you're using it now, PixelSwitch switches you to another account.",
        "Usage last refreshed at %@. It refreshes on its own.",
        "Each account gets a colored edge and icon on the Usage tab, so you can tell whose numbers you are looking at. Turn it off for plain cards.",
    ]
    for key in keys {
        check(tables.allSatisfy { ($0[key] ?? "").isEmpty == false }, "tidy strings: '\(key.prefix(48))' is in all five tables")
        check(tables[0][key] == key && zip(tables.dropFirst(), languages.dropFirst()).allSatisfy { $0.0[key] != key },
              "tidy strings: the English line of '\(key.prefix(48))' is the key and de, fr, ja and zh-Hans are translated, not copied")
    }
    // A key with a placeholder keeps it in every language.
    for key in keys where key.contains("%@") {
        check(tables.allSatisfy { ($0[key] ?? "").contains("%@") }, "tidy strings: every translation of '\(key.prefix(32))' keeps its %@")
    }
    check(tables.allSatisfy { $0["Each account gets a colored edge and icon on the Usage and Accounts tabs, so you can tell whose numbers you are looking at. Turn it off for plain cards."] == nil },
          "tidy strings: the old Usage and Accounts tabs line is gone from all five tables")
    check(tables.allSatisfy { $0["Login New Account"] == nil }, "tidy strings: the orphaned Login New Account key (only the deleted Accounts tab used it) is gone from all five tables")
    check(tables.allSatisfy { Set($0.keys) == Set(tables[0].keys) }, "tidy strings: the five tables still have exactly the same keys")

    // The words the new row and footer reuse are in the tables.
    for key in ["Switch", "Edit label", "Remove account", "Custom label", "Show sign-in window", "Signing in…", "Finish in the sign-in window.",
                "Add Current Account", "Sign In New Account", "Refresh", "Settings", "Quit", "Cancel", "Add Account",
                "This will capture the currently logged-in Claude Code account."] {
        check(tables.allSatisfy { ($0[key] ?? "").isEmpty == false }, "tidy strings: the reused '\(key.prefix(40))' is in all five tables")
    }
}
