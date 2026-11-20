// Renders PixelSwitch's Settings pieces offscreen, with invented sample data:
// the Accounts row (SettingsAccountRow: Priority, account, Session and Weekly
// menus, and Switch, Re-sign, Edit label, Chrome profile and Remove account as five icons) for equal windows,
// the 90/50 split, a Manual only account, priorities P1, P3 and none, the account in
// use (Switch off), an account whose sign-in ended (Re-sign in the warning colour,
// Switch off) and a busy app; the same icons with a hover label held open on each (the
// label the app draws, HoverLabel.swift: a still has no pointer); the Chrome profile icon with each
// of its choices (the warning line shows while "signed in as" is chosen); a real scrolling List,
// where the first row's label hangs below its icon because there is no room above; and the General
// defaults (SettingsThresholdDefaults).
//
// These are the app's own views, drawn through an AppKit hosting view so the
// menus and sliders are real controls, light and dark, at 2x. Nothing here
// reads a real login or a real usage figure; addresses are on example.com
// (RFC 2606). This is separate from make_screenshots.swift (the README
// pictures), which keeps working on its own.
//
// Run it with `bash Tools/screenshots/run_settings_renders.sh <output folder>`.
// That script compiles this file with the views it draws, wraps the program in
// a small .app with the app's five string tables (so the row reads German,
// French, Japanese and Chinese from the real tables, as the app does), and
// runs it once per language. Language "en" keeps the original file names; the
// others add "-de", "-fr", "-ja", "-zh-Hans".
import SwiftUI
import AppKit

private let defaultsEqual = AutoSwitchEngine.WindowPair(both: 90)
private let defaultsSplit = AutoSwitchEngine.WindowPair(session: 90, weekly: 50)

/// One sample row, built from an Account the way Settings builds it.
@MainActor
private func row(_ account: Account, defaults: AutoSwitchEngine.WindowPair, reset: Text = Text(verbatim: "Weekly resets Mon 11:00 AM"),
                 active: Bool = false, label: String? = nil, help: String = "",
                 signInEnded: Bool = false, busy: Bool = false, previewLabel: SettingsRowIcon? = nil,
                 chrome: ChromeProfileChoice? = nil) -> some View {
    let manual = AutoSwitchSettings.isManualOnly(own: account.switchThreshold)
    // Switch is off for the account in use, for one whose sign-in ended, and while the app is busy:
    // the reasons are the app's own sentences (SettingsAccountsTab.switchOffReason), in the language drawn.
    func said(_ key: String) -> String { String(localized: String.LocalizationValue(key), bundle: L10n.bundle) }
    let off: String? = active ? said("You're using this account now.")
        : (signInEnded ? said("Sign in to this account again before you switch to it.")
           : (busy ? said("Wait for the current switch or sign-in to finish.") : nil))
    return SettingsAccountRow(
        title: label ?? account.email,
        subtitle: label == nil ? nil : account.email,
        isActive: active,
        resetNote: reset,
        manualNote: active && manual
            ? Text(verbatim: "You're using it now. Auto-switch moves you off it at \(Int(defaults.session))% of its session or \(Int(defaults.weekly))% of its week and won't move you back to it.")
            : nil,
        priority: AccountOrder.validPriority(account.priority),
        sessionOwn: AccountRowThresholds.own(.session, of: account),
        weeklyOwn: AccountRowThresholds.own(.weekly, of: account),
        manualOnly: manual,
        defaults: defaults,
        help: Text(verbatim: help),
        customLabel: label ?? "",
        needsResign: signInEnded,
        switchOffReason: off,
        actionsBusy: busy,
        previewLabel: previewLabel,
        chromeChoice: chrome,
        chromeAddress: account.email,
        onPriority: { _ in }, onSession: { _ in }, onWeekly: { _ in },
        onSwitch: {}, onResign: {}, onLabel: { _ in }, onRemove: {}, onChromeChoice: { _ in })
}

/// The inset list Settings puts the rows in, drawn plainly: alternating rows.
private struct SampleList<Content: View>: View {
    let rows: [Content]
    var body: some View {
        VStack(spacing: 0) {
            ForEach(0..<rows.count, id: \.self) { index in
                rows[index]
                    .padding(.horizontal, 10).padding(.vertical, 3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(index % 2 == 0 ? Color(nsColor: .alternatingContentBackgroundColors[0]) : Color(nsColor: .alternatingContentBackgroundColors[1]))
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 0).stroke(Color.primary.opacity(0.12), lineWidth: 0.5))
    }
}

@MainActor
private func accountsSample() -> some View {
    // The founder's example: priorities 3 and 1, 90% for the 5-hour limit, 50% for the week.
    let equal = Account(email: "work@example.com", displayName: "Work", isActive: true, customLabel: "Work", priority: 1)
    let split = Account(email: "studio@example.com", displayName: "Studio", sessionThreshold: 90, weeklyThreshold: 50, priority: 2)
    let both70 = Account(email: "side-project@example.com", displayName: "Side", switchThreshold: 70, priority: 3)
    let manual = Account(email: "archive@example.com", displayName: "Archive", switchThreshold: 0, priority: 4)
    // 34 characters, one more than the founder's longest (claude@bespokewoodcraftstudio.com, 33); .test is reserved (RFC 2606).
    let none = Account(email: "claude@bespokewoodcraftstudio.test", displayName: "Team", priority: nil)
    let widest = Account(email: "overnight-batch@example.com", displayName: "Batch", sessionThreshold: 100, weeklyThreshold: 100, priority: 99)
    return VStack(alignment: .leading, spacing: 10) {
        SampleList(rows: [
            row(equal, defaults: defaultsEqual, active: true, label: "Work"),
            row(split, defaults: defaultsEqual, reset: Text(verbatim: "Weekly resets in 2 days")),
            row(both70, defaults: defaultsEqual),
            row(manual, defaults: defaultsEqual, reset: Text("Weekly reset not known yet")),
            // Its sign-in ended: Re-sign takes the warning colour, Switch is off with its reason.
            row(none, defaults: defaultsEqual, signInEnded: true),
            // The widest content a row can have: 100% in both windows (the longest label with its
            // stepper) and the highest priority.
            row(widest, defaults: defaultsEqual),
        ])
        .frame(height: nil)
        Text("Lower numbers are tried first. Dragging renumbers every account from 1.")
            .font(.caption).foregroundStyle(.secondary)
    }
    .padding(20)
}

/// The four icons with a hover label held open on each in turn, and the states that put a reason in
/// the label: the account in use (Switch off), a busy app (Re-sign and Remove off), and the account
/// whose sign-in ended. Hovering one icon at a time is what a person does; the rows below are four
/// separate stills of it.
@MainActor
private func iconLabelsSample() -> some View {
    let work = Account(email: "work@example.com", displayName: "Work", isActive: true, customLabel: "Work", priority: 1)
    let studio = Account(email: "studio@example.com", displayName: "Studio", priority: 2)
    let team = Account(email: "claude@bespokewoodcraftstudio.test", displayName: "Team", priority: nil)
    return VStack(alignment: .leading, spacing: 10) {
        SampleList(rows: [
            row(studio, defaults: defaultsEqual, previewLabel: .switchTo),
            row(studio, defaults: defaultsEqual, previewLabel: .resign),
            row(studio, defaults: defaultsEqual, previewLabel: .editLabel),
            // The Chrome profile icon: its label names the current choice, here Automatic (nothing kept).
            row(studio, defaults: defaultsEqual, previewLabel: .chromeProfile),
            row(studio, defaults: defaultsEqual, previewLabel: .remove),
            // The account in use: Switch is off and says why.
            row(work, defaults: defaultsEqual, active: true, label: "Work", previewLabel: .switchTo),
            // A switch or sign-in is running: Re-sign and Remove say to wait.
            row(studio, defaults: defaultsEqual, busy: true, previewLabel: .remove),
            // Its sign-in ended: Re-sign in the warning colour, Switch off with its reason.
            row(team, defaults: defaultsEqual, signInEnded: true, previewLabel: .switchTo),
        ])
    }
    .padding(20)
}

/// The Chrome profile choices, one row each, with the hover label held open on the icon: Automatic (nothing kept),
/// its own PixelSwitch profile, the profile signed in as the address (the warning line shows in the row, with the
/// longest address the app is meant to show whole) and a named folder.
@MainActor
private func chromeChoicesSample() -> some View {
    let a = Account(email: "studio@example.com", displayName: "Studio", priority: 1)
    let b = Account(email: "work@example.com", displayName: "Work", customLabel: "Work", priority: 2)
    // 33 characters, the longest address the founder has (claude@bespokewoodcraftstudio.com); .test is reserved (RFC 2606).
    let c = Account(email: "claude@bespokewoodcraftstudio.test", displayName: "Team", isActive: true, priority: 3)
    let d = Account(email: "archive@example.com", displayName: "Archive", priority: 4)
    return VStack(alignment: .leading, spacing: 10) {
        SampleList(rows: [
            row(a, defaults: defaultsEqual, previewLabel: .chromeProfile, chrome: nil),
            row(b, defaults: defaultsEqual, label: "Work", previewLabel: .chromeProfile, chrome: .ownProfile),
            row(c, defaults: defaultsEqual, active: true, previewLabel: .chromeProfile, chrome: .chromeProfileSignedInAs),
            row(d, defaults: defaultsEqual, previewLabel: .chromeProfile, chrome: .directory("Profile 1")),
        ])
    }
    .padding(20)
}

/// A real scrolling List like Settings' (an inset list with alternating rows), so the label's room above is measured
/// against the list's own clip view and the label is drawn among real table cells. `which` holds the Chrome profile
/// label open on that row only, with `chrome` as that row's kept choice (nil is Automatic, whose label is two lines).
/// `longAt` is the row that has the 34-character address, which makes the signed-in-as label two lines as well and
/// the row tall (the warning line shows). The label of a row below the first rises over the row above it.
@MainActor
private func listSample(which: Int, chrome: ChromeProfileChoice?, longAt: Int = 0) -> some View {
    let accounts = [
        Account(email: "studio@example.com", displayName: "Studio", priority: 1),
        Account(email: "archive@example.com", displayName: "Archive", priority: 2),
        Account(email: "work@example.com", displayName: "Work", priority: 3),
    ].enumerated().map { index, account in
        index == longAt ? Account(email: "claude@bespokewoodcraftstudio.test", displayName: "Team", priority: account.priority) : account
    }
    return List {
        ForEach(0..<accounts.count, id: \.self) { index in
            row(accounts[index], defaults: defaultsEqual, previewLabel: index == which ? .chromeProfile : nil, chrome: index == which ? chrome : nil)
        }
    }
    .listStyle(.inset(alternatesRowBackgrounds: true))
    .frame(height: 260)
    .padding(8)
}

/// Rows in a scroll view with no inset at all, the tightest a first row can sit: its top edge is the scroll view's
/// top edge, so there is no room above its icon and its label hangs below it (the 33-character address makes the
/// label wrap to two lines when "signed in as" is chosen, the tallest label there is). The rows below are drawn
/// after it, so the label has to stay in front of them.
@MainActor
private func scrollSample(chrome: ChromeProfileChoice?) -> some View {
    let first = Account(email: "claude@bespokewoodcraftstudio.test", displayName: "Team", priority: 1)
    let second = Account(email: "studio@example.com", displayName: "Studio", priority: 2)
    return ScrollView {
        VStack(spacing: 0) {
            row(first, defaults: defaultsEqual, previewLabel: .chromeProfile, chrome: chrome)
                .padding(.horizontal, 10).padding(.vertical, 3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .alternatingContentBackgroundColors[0]))
            row(second, defaults: defaultsEqual)
                .padding(.horizontal, 10).padding(.vertical, 3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .alternatingContentBackgroundColors[1]))
        }
    }
    .frame(height: 170)
    .padding(8)
}

@MainActor
private func splitDefaultsSample() -> some View {
    // The same list on a Mac whose defaults are split (Session 90, Weekly 50): Default shows each window's own number.
    let a = Account(email: "work@example.com", displayName: "Work", customLabel: "Work", priority: 1)
    let b = Account(email: "studio@example.com", displayName: "Studio", weeklyThreshold: 60, priority: 3)
    let c = Account(email: "archive@example.com", displayName: "Archive", isActive: true, switchThreshold: 0, priority: nil)
    return VStack(alignment: .leading, spacing: 10) {
        SampleList(rows: [
            row(a, defaults: defaultsSplit, label: "Work"),
            row(b, defaults: defaultsSplit),
            row(c, defaults: defaultsSplit, reset: Text("Weekly reset not known yet"), active: true),
        ])
    }
    .padding(20)
}

@MainActor
private func generalSample(session: Double, weekly: Double) -> some View {
    VStack(alignment: .leading, spacing: 8) {
        Text("Auto-switch").font(.headline)
        Toggle("Switch account before hitting the limit", isOn: .constant(true))
        SettingsThresholdDefaults(session: session, weekly: weekly, onSession: { _ in }, onWeekly: { _ in })
        Toggle("Also switch when Fable runs out", isOn: .constant(true))
        Text("Fable uses the weekly limit.").font(.caption).foregroundStyle(.secondary)
    }
    .padding(20)
}

@MainActor
private func writePNG<V: View>(_ content: V, width: CGFloat, dark: Bool, language: String, to path: String) {
    let appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
    let root = content
        .frame(width: width, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor))
        .environment(\.colorScheme, dark ? .dark : .light)
        .environment(\.locale, Locale(identifier: language))
    let host = NSHostingView(rootView: root)
    host.appearance = appearance
    let size = host.fittingSize
    host.frame = NSRect(origin: .zero, size: size)
    let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
    window.appearance = appearance
    window.contentView = host
    host.layoutSubtreeIfNeeded()
    RunLoop.main.run(until: Date().addingTimeInterval(0.4))
    host.layoutSubtreeIfNeeded()
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * 2), pixelsHigh: Int(size.height * 2),
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else {
        print("could not make a bitmap for \(path)"); return
    }
    rep.size = size
    NSGraphicsContext.saveGraphicsState()
    host.cacheDisplay(in: host.bounds, to: rep)
    NSGraphicsContext.restoreGraphicsState()
    guard let png = rep.representation(using: .png, properties: [:]) else { print("could not encode \(path)"); return }
    do {
        try png.write(to: URL(fileURLWithPath: path))
        print("wrote \(path) (\(Int(size.width * 2)) x \(Int(size.height * 2)))")
    } catch {
        print("could not write \(path): \(error)")
    }
}

@main
enum SettingsRenders {
    /// Usage: pixelrenders <output folder> [language], language one of en, de, fr, ja, zh-Hans (default en).
    static func main() {
        let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
        let language = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "en"
        let suffix = language == "en" ? "" : "-\(language)"
        // The row reads the language the way the app does: the appLanguage default and the locale.
        UserDefaults.standard.set(language, forKey: "appLanguage")
        try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
        MainActor.assumeIsolated {
            _ = NSApplication.shared
            NSApp.setActivationPolicy(.prohibited)
            for dark in [false, true] {
                let name = dark ? "dark" : "light"
                // 972 wide: the Settings window (980) less its padding and the list's insets.
                writePNG(accountsSample(), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-rows\(suffix)-\(name).png")
                writePNG(splitDefaultsSample(), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-split-defaults\(suffix)-\(name).png")
                writePNG(iconLabelsSample(), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-icon-labels\(suffix)-\(name).png")
                writePNG(chromeChoicesSample(), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-chrome-profile\(suffix)-\(name).png")
                // The label's room in the real List, first row, then the rows below it (two-line labels: Automatic, and signed in as a long address).
                writePNG(listSample(which: 0, chrome: nil), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-list-first-row-label\(suffix)-\(name).png")
                writePNG(listSample(which: 1, chrome: nil), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-list-second-row-label\(suffix)-\(name).png")
                writePNG(listSample(which: 2, chrome: nil), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-list-third-row-label\(suffix)-\(name).png")
                writePNG(listSample(which: 1, chrome: .chromeProfileSignedInAs, longAt: 1), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-list-second-row-label-long\(suffix)-\(name).png")
                writePNG(listSample(which: 2, chrome: .chromeProfileSignedInAs, longAt: 2), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-list-third-row-label-long\(suffix)-\(name).png")
                writePNG(scrollSample(chrome: nil), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-scroll-first-row-label\(suffix)-\(name).png")
                writePNG(scrollSample(chrome: .chromeProfileSignedInAs), width: 972, dark: dark, language: language, to: "\(out)/settings-accounts-scroll-first-row-label-long\(suffix)-\(name).png")
                if language == "en" {
                    writePNG(generalSample(session: 90, weekly: 50), width: 560, dark: dark, language: language, to: "\(out)/settings-general-defaults-split-\(name).png")
                    writePNG(generalSample(session: 98, weekly: 98), width: 560, dark: dark, language: language, to: "\(out)/settings-general-defaults-equal-\(name).png")
                }
            }
        }
    }
}
