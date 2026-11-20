// 2.3 popover tidy: the popover has no Accounts tab, its footer adds an account and every
// icon says what it is for, Settings -> Accounts rows carry Switch, Re-sign, Edit label and
// Remove, and the expired message no longer points at the wrong icon. 2.4: the row's four actions
// are plain icon buttons (no "..." menu), and the footer and the row say what each icon does in a
// hover label the app draws (HoverLabel.swift) instead of relying on the system tooltip.
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
    hoverLabelTests()
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

    // Each has a tooltip and a spoken label; the one component applies both (and the hover label, below).
    check(tidyCount(footer, "help: String(localized:") == 5 && tidyCount(footer, "label: String(localized:") == 5,
          "footer pins: every footer icon is given a tooltip and a spoken label")
    check(button.contains(".help(disabledHelp ?? help)") && button.contains(".accessibilityLabel(label)"),
          "footer pins: the component puts the tooltip on .help (the reason it is off, else what it is for) and the spoken label on .accessibilityLabel")
    // The tooltip and the hover label are on a wrapper, not on the button: a disabled control gets no pointer events.
    let buttonOnly = tidyBlock(button, from: "Button {", to: ".accessibilityLabel(label)")
    check(!buttonOnly.isEmpty && !buttonOnly.contains(".help(") && !buttonOnly.contains(".hoverLabel(")
          && button.contains("        }\n        .contentShape(Rectangle())\n        .help(disabledHelp ?? help)\n        .hoverLabel(disabledHelp ?? help,"),
          "footer pins: the footer icon's tooltip and hover label sit on a wrapper around the button, so they show while the icon is off")
    // Bigger and easier to hit: a 15 pt symbol in a 28 pt square, the whole square clickable, a hover state, the muted tone.
    check(look.contains(".font(.system(size: 15))") && look.contains(".frame(width: 28, height: 28)") && look.contains(".contentShape(Rectangle())"),
          "footer pins: each icon is a 15 pt symbol in a 28 x 28 pt target the whole of which is clickable")
    check(look.contains("Color.textSecondary") && look.contains("hovering && !isDisabled") && button.contains(".disabled(isDisabled)"),
          "footer pins: each icon is in the muted text tone, lights up under the pointer and can be disabled")
    // The highlight comes from the label's always-active tracking area, like the Settings row's icons: no SwiftUI hover is left in the footer file.
    check(!file.contains(".onHover") && !file.contains(".onContinuousHover")
          && button.contains("dismissTrigger: presses, alwaysShown: labelShown, onHover: { if hovering != $0 { hovering = $0 } })")
          && button.contains("FooterIconLabel(symbol: symbol, hovering: hovering || labelShown, isDisabled: isDisabled)"),
          "footer pins: a footer icon's highlight follows the hover label's tracking area (the pointer in any window state), with no SwiftUI onHover left in PopoverFooter.swift")
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

    // The four things only the popover tab had: Switch, Re-sign, Edit label and Remove, as four plain icon
    // buttons (a hand check asked why two of them were behind a "..." menu), and a fifth icon, the Chrome profile menu.
    let actions = tidyBlock(row, from: "    private var actions: some View {", to: "    // MARK: - Label editor")
    let iconButton = tidyBlock(row, from: "private struct RowIconButton: View {", to: "\n}\n")
    check(!actions.isEmpty && !iconButton.isEmpty, "row pins: the row's actions and its icon button are found")
    check(tidyCount(actions, "RowIconButton(") == 4 && tidyCount(actions, "RowIconMenu(") == 1 && !actions.contains("ellipsis") && !actions.contains("Menu {"),
          "row pins: the row's actions are four icon buttons and one Chrome profile menu icon, and no \"...\" menu holds an action")
    check(!row.contains("ellipsis") && !row.contains("More actions"), "row pins: the row has no \"...\" menu and no More actions text any more")
    var order = actions.startIndex
    var inOrder = true
    for symbol in ["arrow.left.arrow.right", "arrow.triangle.2.circlepath", "square.and.pencil", "globe", "trash"] {
        if let found = actions.range(of: "symbol: \"\(symbol)\"", range: order..<actions.endIndex) { order = found.upperBound } else { inOrder = false }
    }
    check(inOrder, "row pins: left to right the icons are Switch, Re-sign, Edit label, Chrome profile and Remove (Remove stays last, at the edge)")

    // Each is a Button with a spoken label, a hover label and a tooltip; the label and tooltip sit on a wrapper around the
    // button, so they show while it is off (a disabled control gets no pointer events, and its reason is the label).
    let buttonOnly = tidyBlock(iconButton, from: "Button(role:", to: ".accessibilityInputLabels([inputLabel])")
    check(!buttonOnly.isEmpty && buttonOnly.contains(".accessibilityLabel(spoken)") && buttonOnly.contains(".disabled(isDisabled)")
          && !buttonOnly.contains(".help(") && !buttonOnly.contains(".hoverLabel("),
          "row pins: each icon is a Button with a spoken label and an off state, and carries no tooltip of its own")
    check(iconButton.contains("        }\n        .contentShape(Rectangle())\n        .help(Text(verbatim: hint))")
          && iconButton.contains(".hoverLabel(hint, anchor: .trailing,") && iconButton.contains("onHover: { hovering = $0 }"),
          "row pins: the hint is the tooltip and the hover label, both on a wrapper around the button")
    check(!iconButton.contains(".onHover") && !iconButton.contains(".onContinuousHover") && iconButton.contains("lit: (hovering || labelShown) && !isDisabled"),
          "row pins: an icon's highlight follows the hover label's tracking area (the pointer in any window state), not SwiftUI's onHover")
    check(actions.contains("spoken: Text(\"Switch to \\(title)\")") && actions.contains("spoken: Text(\"Re-authenticate \\(title)\")")
          && actions.contains("spoken: Text(\"Edit label for \\(title)\")") && actions.contains("spoken: Text(\"Remove account \\(title)\")"),
          "row pins: every icon is spoken with the account's name")
    check(actions.contains("inputLabel: Text(\"Switch\")") && actions.contains("inputLabel: Text(\"Re-sign\")")
          && actions.contains("inputLabel: Text(\"Edit label\")") && actions.contains("inputLabel: Text(\"Remove account\")"),
          "row pins: Voice Control can still say Switch, Re-sign, Edit label and Remove account")

    // The rules behind the off states are the same ones as before: Switch by SwitchAvailability's reason, Re-sign and Remove by the busy rule.
    check(actions.contains("hint: switchOffReason ?? String(localized: \"Switch to this account\", bundle: L10n.bundle)")
          && actions.contains("isDisabled: switchOffReason != nil") && tidyCount(actions, "isDisabled: actionsBusy") == 2,
          "row pins: Switch is off with its reason when SwitchAvailability says so; Re-sign and Remove are off while a switch or a sign-in runs")
    check(tidyCount(actions, "Wait for the current switch or sign-in to finish.") == 2,
          "row pins: Re-sign and Remove say why they wait, in their hover label")
    check(actions.contains("symbol: \"arrow.triangle.2.circlepath\"") && actions.contains("tint: needsResign ? Color.limitWarn : Color.textSecondary"),
          "row pins: Re-sign keeps the card's icon and takes the warning colour once the sign-in has ended")
    check(actions.contains("action: onSwitch") && actions.contains("action: onResign") && actions.contains("action: { beginEditing() }")
          && actions.contains("action: onRemove"),
          "row pins: the icons run the row's own closures: switch, re-sign, edit the label inline, ask to remove")
    // Edit label is still the inline editor, and Remove still only asks (the tab confirms), marked destructive and red under the pointer.
    check(row.contains("TextField(\"Custom label\", text: draftStorage.projectedValue)") && row.contains("onLabel(draftStorage.wrappedValue)"),
          "row pins: Edit label is an inline field that saves what was typed")
    check(actions.contains("destructive: true") && actions.contains("hoverTint: Color.limitCrit") && iconButton.contains("Button(role: destructive ? .destructive : nil)"),
          "row pins: Remove is marked destructive, quiet until the pointer is over it, then red")
    // Icons are small enough that the address keeps its room at the 940 pt window in English, German and French.
    let glyph = tidyBlock(row, from: "private struct RowIconGlyph: View {", to: "\n}\n")
    check(glyph.contains(".frame(width: 22, height: 22)") && iconButton.contains("RowIconGlyph(symbol: symbol") && actions.contains("HStack(spacing: 0) {"),
          "row pins: the five icons are 22 pt wide with no gap, so a 33-character address stays whole beside them in English, German and French")
    let settings = tidySource("PixelSwitch/Views/SettingsView.swift")
    check(settings.contains(".frame(width: 980, height: 520)"),
          "row pins: the Settings window is 980 pt wide, 40 pt more than with four icons, for the fifth icon (the renders at 972 pt content width show the 34-character address whole in en, de and fr)")

    // The Chrome profile icon: a menu with the same look, a hover label that names the current choice, a spoken label and value.
    let menuIcon = tidyBlock(row, from: "private struct RowIconMenu: View {", to: "\n}\n")
    let chromeAction = tidyBlock(actions, from: "RowIconMenu(", to: "onChoose: onChromeChoice)")
    check(!menuIcon.isEmpty && !chromeAction.isEmpty && chromeAction.contains("symbol: \"globe\"")
          && chromeAction.contains("hint: ChromeProfileChoiceControl.hoverText(for: chromeChoice, address: chromeAddress)")
          && chromeAction.contains("spoken: Text(verbatim: ChromeProfileChoiceControl.spokenLabel(accountName: title))")
          && chromeAction.contains("value: Text(verbatim: ChromeProfileChoiceControl.title(for: chromeChoice, address: chromeAddress))")
          && actions.contains("onChoose: onChromeChoice)"),
          "row pins: the Chrome profile icon says \"Chrome profile for Limit resets: <current choice>\" on hover and speaks its account and its choice")
    check(menuIcon.contains("Menu {") && menuIcon.contains("ChromeProfileChoiceControl(choice: choice, address: address, onChoose: onChoose)")
          && menuIcon.contains(".menuIndicator(.hidden)") && menuIcon.contains("RowIconGlyph(symbol: symbol, lit: hovering || labelShown)")
          && menuIcon.contains(".accessibilityLabel(spoken)") && menuIcon.contains(".accessibilityValue(value)")
          && menuIcon.contains(".help(Text(verbatim: hint))") && menuIcon.contains(".hoverLabel(hint, anchor: .trailing, maxWidth: 480, gap: -3")
          && menuIcon.contains("onHover: { hovering = $0 }") && !menuIcon.contains(".onHover"),
          "row pins: the Chrome profile icon is a menu of the control's items with the row's icon look, a tooltip, a hover label, an accessibility label and value, and the tracker's highlight")
    check(row.contains("var chromeChoice: ChromeProfileChoice?") && row.contains("var chromeAddress = \"\"")
          && row.contains("let onChromeChoice: @MainActor (ChromeProfileChoice?) -> Void") && row.contains("case switchTo, resign, editLabel, chromeProfile, remove"),
          "row pins: the row takes the kept choice, the address and one closure for the Chrome profile menu, and plain inputs only")
    // The warning for the signed-in option is a line in the row while it is chosen, not only a tooltip.
    let warning = tidyBlock(row, from: "if chromeChoice == .chromeProfileSignedInAs {", to: ".font(.caption)\n                }")
    check(!warning.isEmpty && warning.contains("Text(verbatim: ChromeProfileChoiceControl.signedInHelp())") && warning.contains("exclamationmark.triangle.fill"),
          "row pins: while the signed-in-as choice is kept, the row shows the warning as a line of text with a warning mark")
    check(tab.contains("chromeChoice: chromeChoice(for: account)") && tab.contains("onChromeChoice: { chooseChromeProfile($0, for: account) }")
          && tab.contains("chromeAddress: account.displayEmail(obfuscated: maskEmails)"),
          "row pins: the tab hands each row its kept choice and what to do with a pick")
    let chooser = tidyBlock(tab, from: "private func chooseChromeProfile(", to: "\n    }\n")
    check(chooser.contains("ChromeProfileChoiceStore.apply(choice, forAccountID: account.id.uuidString)")
          && !chooser.contains("ChromeUsagePage") && !chooser.contains("Process") && !chooser.contains("NSWorkspace"),
          "row pins: a pick only writes the kept choice (or forgets it for Automatic); nothing is opened")

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

// MARK: - The hover label

@MainActor private func hoverLabelTests() {
    let hover = tidySource("PixelSwitch/Views/Components/HoverLabel.swift")
    let file = tidySource("PixelSwitch/Views/Components/PopoverFooter.swift")
    let footer = tidyBlock(file, from: "struct PopoverFooter: View {", to: "/// What a footer icon looks like")
    let button = tidyBlock(file, from: "struct FooterIconButton: View {", to: "/// A sign-in is running")
    let row = tidySource("PixelSwitch/Views/SettingsAccountRow.swift")
    check(!hover.isEmpty && !footer.isEmpty && !button.isEmpty && !row.isEmpty, "hover label pins: the component, the footer and the row are found")

    // Drawn by the app, not the system: it appears after 0.3 s of pointer rest.
    check(hover.contains("static let delay: Duration = .milliseconds(300)") && hover.contains("Task.sleep(for: HoverLabelMetrics.delay)"),
          "hover label pins: the label shows after a 0.3 s pause")
    // The pointer is found with an always-active AppKit tracking area, not SwiftUI's hover (which only reports in the key window of the
    // active app: a menu bar popover is often neither, which is how the system tooltip came to work "sometimes").
    let tracking = tidyBlock(hover, from: "final class HoverTrackingView: NSView {", to: "\n}\n")
    check(!tracking.isEmpty && tracking.contains("options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways, .inVisibleRect]")
          && tracking.contains("override func mouseEntered") && tracking.contains("override func mouseMoved") && tracking.contains("override func mouseExited"),
          "hover label pins: the pointer is tracked by an NSTrackingArea that is .activeAlways, so it reports in a popover that is not key and an app that is not active")
    check(!hover.contains(".onHover") && !hover.contains(".onContinuousHover") && hover.contains("HoverTracker(onChange: { over in")
          && hover.contains("onHover?(over)"),
          "hover label pins: the label's pointer detection is the tracking area alone, with no SwiftUI hover in it, and it hands the same reports to the icon's highlight")
    // The label goes when nobody can see its icon: the window closes or stops being key or main, the view leaves its window, a menu opens.
    check(tracking.contains("NSWindow.willCloseNotification") && tracking.contains("NSWindow.didResignKeyNotification") && tracking.contains("NSWindow.didResignMainNotification")
          && tracking.contains("object: window)") && tracking.contains("@objc private func windowStoppedShowing(_ notification: Notification) { onChange?(false) }"),
          "hover label pins: the tracker reports \"outside\" when its window closes or resigns key or main")
    let leaving = tidyBlock(tracking, from: "override func viewWillMove(toWindow newWindow: NSWindow?) {", to: "super.viewWillMove(toWindow: newWindow)")
    check(!leaving.isEmpty && leaving.contains("if window != nil, let report = onChange { Task { @MainActor in report(false) } }")
          && leaving.contains("NotificationCenter.default.removeObserver(self)"),
          "hover label pins: the tracker reports \"outside\" when its view leaves its window, and stops listening to the old window")
    check(hover.contains(".onReceive(NotificationCenter.default.publisher(for: NSMenu.didBeginTrackingNotification)) { _ in\n                quiet = true\n                disarm()\n            }"),
          "hover label pins: a menu opening hides the label until the pointer leaves")
    // Where there is no room above the icon (the first row, right under the scroll view's top edge) the label hangs below it.
    check(tracking.contains("var roomAbove: CGFloat {") && tracking.contains("!(view is NSClipView)") && tracking.contains("clip.convert(clip.bounds, to: nil).maxY")
          && tracking.contains("return top - convert(bounds, to: nil).maxY"),
          "hover label pins: the tracker measures the room above its view inside the nearest scroll view's visible area, else the window")
    check(hover.contains("static func showsBelow(roomAbove: CGFloat, text: String, maxWidth: CGFloat, gap: CGFloat) -> Bool {\n        roomAbove < roomNeeded(for: text, maxWidth: maxWidth, gap: gap)")
          && hover.contains("static func roomNeeded(for text: String, maxWidth: CGFloat, gap: CGFloat) -> CGFloat {\n        height(for: text, maxWidth: maxWidth) + gap + shadowMargin")
          && hover.contains("onRoomAbove: { room in roomAbove = room })"),
          "hover label pins: the label needs its own height (less what it tucks into the icon) plus a shadow margin above the icon, else it shows below")
    check(hover.contains("if (shown || alwaysShown) && !text.isEmpty && !below {") && hover.contains("if (shown || alwaysShown) && !text.isEmpty && below {")
          && hover.contains(".overlay(alignment: .bottom) {") && hover.contains(".offset(y: gap)") && hover.contains(".offset(y: -gap)"),
          "hover label pins: there is one label, hung above the icon or below it, never both")
    check(tracking.contains("override func hitTest(_ point: NSPoint) -> NSView? { nil }") && hover.contains(".allowsHitTesting(false)\n            }"),
          "hover label pins: the tracking view returns nil from hitTest and is not hit-testable in SwiftUI, so a click still reaches the button under it")
    check(hover.contains(".overlay(alignment: .top) {") && hover.contains(".allowsHitTesting(false)") && hover.contains(".frame(height: 0)"),
          "hover label pins: the label is an overlay hung from a zero-height strip, so it takes no room and cannot move the layout, and it ignores clicks")
    check(hover.contains("Color.cardFillStrong") && hover.contains("Color.cardBorder") && hover.contains("Color.textPrimary"),
          "hover label pins: it is drawn in the card fill, border and text tokens, which have a light and a dark value")
    // Not clipped by the popover edge: the anchor lets an edge icon's label grow inward, and a long reason wraps at a maximum width.
    check(hover.contains("case center") && hover.contains("case leading") && hover.contains("case trailing")
          && hover.contains("guard ceil(line) + chrome > maxWidth else { return ceil(line) + chrome }")
          && hover.contains("return min(maxWidth, ceil(wrapped.width) + chrome)"),
          "hover label pins: a label can grow inward from either edge, fits its text on one line and wraps at a maximum width as wide as its longest line")
    check(tidyBlock(file, from: "symbol: \"gear\"", to: "action: onSettings").contains("anchor: .trailing")
          && tidyBlock(file, from: "symbol: \"power\"", to: "action: onQuit").contains("anchor: .trailing"),
          "hover label pins: Settings and Quit, at the popover's right edge, grow their labels inward")
    check(button.contains("maxWidth: 230"), "hover label pins: a footer label is at most 230 pt wide, so the longest reason wraps inside the 360 pt popover")
    // Pressing the icon hides the label until the pointer leaves, so the confirmation bar a press opens is never covered.
    check(hover.contains(".onChange(of: dismissTrigger)") && button.contains("dismissTrigger: presses") && button.contains("presses += 1"),
          "hover label pins: pressing a footer icon hides its label until the pointer leaves")

    // All five footer icons use it, with the reason when they are off; .help stays for accessibility tools.
    check(tidyCount(footer, "FooterIconButton(") == 5 && button.contains(".hoverLabel(disabledHelp ?? help, anchor: anchor,")
          && button.contains(".help(disabledHelp ?? help)") && tidyCount(button, ".hoverLabel(") == 1,
          "hover label pins: all five footer icons go through the one component that draws the label (the reason when off) and keeps .help")
    // The Settings row's four icons use it too.
    check(tidyCount(row, ".hoverLabel(") == 2 && tidyCount(row, ".help(Text(verbatim: hint))") == 2 && tidyCount(row, "RowIconButton(") == 4 && tidyCount(row, "RowIconMenu(") == 1,
          "hover label pins: the Settings row's four click icons and its Chrome profile menu icon all draw the label through the one modifier and keep .help")

    // The harnesses draw the label held open: on the footer's first and last icons, and on each of the row's icons.
    let shots = tidySource("Tools/screenshots/make_screenshots.swift")
    let renders = tidySource("Tools/screenshots/make_settings_renders.swift")
    let runner = tidySource("Tools/screenshots/run_settings_renders.sh")
    check(shots.contains("labelled(.addCurrent)") && shots.contains("labelled(.quit)") && shots.contains("labelled(.signInNew, loggingIn: true)")
          && shots.contains("HoverLabel.swift"),
          "hover label pins: the footer fixture shows the label on the first icon, the last icon and an icon that is off")
    check(["switchTo", "resign", "editLabel", "chromeProfile", "remove"].allSatisfy { renders.contains("previewLabel: .\($0)") }
          && renders.contains("settings-accounts-icon-labels") && runner.contains("HoverLabel.swift"),
          "hover label pins: the Settings fixture shows the label on each of the row's five icons")
    check(renders.contains("settings-accounts-chrome-profile") && renders.contains("settings-accounts-scroll-first-row-label") && renders.contains("settings-accounts-list-first-row-label")
          && runner.contains("ChromeProfileChoice.swift") && runner.contains("ChromeProfileMatcher.swift") && runner.contains("ChromeLocalState.swift"),
          "hover label pins: the fixtures draw each Chrome profile choice, a real List and a tight scroll view whose first row's label hangs below its icon")
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
        "Remove %@?",
        "PixelSwitch forgets this account and deletes its saved login. If you're using it now, PixelSwitch switches you to another account.",
        "Usage last refreshed at %@. It refreshes on its own.",
        "Edit label for %@",
        "Remove account %@",
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
    check(tables.allSatisfy { $0["More actions"] == nil && $0["More actions for %@"] == nil },
          "tidy strings: the orphaned More actions keys (only the deleted \"...\" menu used them) are gone from all five tables")
    check(tables.allSatisfy { $0["Login New Account"] == nil }, "tidy strings: the orphaned Login New Account key (only the deleted Accounts tab used it) is gone from all five tables")
    check(tables.allSatisfy { Set($0.keys) == Set(tables[0].keys) }, "tidy strings: the five tables still have exactly the same keys")

    // The words the new row and footer reuse are in the tables.
    for key in ["Switch", "Edit label", "Remove account", "Custom label", "Show sign-in window", "Signing in…", "Finish in the sign-in window.",
                "Add Current Account", "Sign In New Account", "Refresh", "Settings", "Quit", "Cancel", "Add Account",
                "This will capture the currently logged-in Claude Code account."] {
        check(tables.allSatisfy { ($0[key] ?? "").isEmpty == false }, "tidy strings: the reused '\(key.prefix(40))' is in all five tables")
    }
}
