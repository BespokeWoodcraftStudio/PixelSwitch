// Renders PixelSwitch 2.0's Settings pieces offscreen, with invented sample data:
// the Accounts row (SettingsAccountRow: Priority, account, Session and Weekly
// menus) for equal windows, the 90/50 split, a Manual only account and
// priorities P1, P3 and none; and the General defaults (SettingsThresholdDefaults).
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
                 active: Bool = false, label: String? = nil, help: String = "") -> some View {
    let manual = AutoSwitchSettings.isManualOnly(own: account.switchThreshold)
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
        onPriority: { _ in }, onSession: { _ in }, onWeekly: { _ in })
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
    let none = Account(email: "studio-team-accounts@example.com", displayName: "Team", priority: nil)
    let widest = Account(email: "overnight-batch@example.com", displayName: "Batch", sessionThreshold: 100, weeklyThreshold: 100, priority: 99)
    return VStack(alignment: .leading, spacing: 10) {
        SampleList(rows: [
            row(equal, defaults: defaultsEqual, active: true, label: "Work"),
            row(split, defaults: defaultsEqual, reset: Text(verbatim: "Weekly resets in 2 days")),
            row(both70, defaults: defaultsEqual),
            row(manual, defaults: defaultsEqual, reset: Text("Weekly reset not known yet")),
            row(none, defaults: defaultsEqual),
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
                // 812 wide: the Settings window (820) less its padding and the list's insets.
                writePNG(accountsSample(), width: 812, dark: dark, language: language, to: "\(out)/settings-accounts-rows\(suffix)-\(name).png")
                writePNG(splitDefaultsSample(), width: 812, dark: dark, language: language, to: "\(out)/settings-accounts-split-defaults\(suffix)-\(name).png")
                if language == "en" {
                    writePNG(generalSample(session: 90, weekly: 50), width: 560, dark: dark, language: language, to: "\(out)/settings-general-defaults-split-\(name).png")
                    writePNG(generalSample(session: 98, weekly: 98), width: 560, dark: dark, language: language, to: "\(out)/settings-general-defaults-equal-\(name).png")
                }
            }
        }
    }
}
