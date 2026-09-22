import SwiftUI
import ServiceManagement

/// Settings window for configuring the app.
struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var updateChecker: UpdateChecker
    @EnvironmentObject private var menuBarConfig: MenuBarConfig
    @AppStorage("refreshInterval") private var refreshInterval: Double = 300
    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @AppStorage(AccountColorCoding.key) private var colorCodeAccounts = true
    @AppStorage("showInDock") private var showInDock = false
    @AppStorage("appLanguage") private var appLanguage = "auto"
    @AppStorage("autoSwitchEnabled") private var autoSwitchEnabled = false
    @AppStorage("autoSwitchThreshold") private var autoSwitchThreshold = 90.0
    @AppStorage(AutoSwitchFableSetting.key) private var autoSwitchOnFable = true
    @AppStorage("transcriptLookbackHours") private var transcriptLookbackHours = 24
    @State private var launchAtLogin = false

    var body: some View {
        TabView {
            generalTab
                .tabItem {
                    Label("General", systemImage: "gear")
                }

            menuBarTab
                .tabItem {
                    Label("Menu Bar", systemImage: "menubar.rectangle")
                }

            ClaudeCLITabView()
                .tabItem {
                    Label("Claude CLI", systemImage: "terminal")
                }

            aboutTab
                .tabItem {
                    Label("About", systemImage: "info.circle")
                }
        }
        .frame(width: 520, height: 440)
        .onAppear {
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
    }

    // MARK: - General Tab

    private var generalTab: some View {
        Form {
            Section("Refresh") {
                Picker("Auto-refresh interval", selection: $refreshInterval) {
                    Text("15 seconds").tag(15.0)
                    Text("30 seconds").tag(30.0)
                    Text("1 minute").tag(60.0)
                    Text("5 minutes").tag(300.0)
                    Text("10 minutes").tag(600.0)
                }
                .onChange(of: refreshInterval) { _, newValue in
                    appState.startAutoRefresh(interval: newValue)
                }
                Picker("Usage history window", selection: $transcriptLookbackHours) {
                    Text("Last 24 hours").tag(24)
                    Text("Last 3 days").tag(72)
                    Text("Last 7 days").tag(168)
                    Text("Last 30 days").tag(720)
                    Text("All history").tag(0)
                }
                .onChange(of: transcriptLookbackHours) { _, _ in
                    Task { await appState.refresh() }
                }
                Text("How far back to parse Claude session transcripts for cost and activity stats. Memory use grows with the window; longer windows also lengthen the first scan after launch.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Auto-switch") {
                Toggle("Switch account before hitting the limit", isOn: $autoSwitchEnabled)
                if autoSwitchEnabled {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("Switch at")
                            Spacer()
                            Text("\(Int(autoSwitchThreshold))%")
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                        Slider(value: $autoSwitchThreshold, in: 50...99, step: 1)
                    }
                    Toggle("Also switch when Fable runs out", isOn: $autoSwitchOnFable)
                    Text("When the active account's 5-hour, weekly or Fable usage reaches this level, PixelSwitch switches to the account with the most room left on that limit. Turn off the Fable switch above to leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute cooldown prevents rapid flip-flopping.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Account display") {
                Toggle("Hide part of each email address", isOn: $maskEmails)
                Text("Off by default, so you can see exactly which account is which. Turn it on to mask addresses (cla*@*.com) before sharing your screen or sending a screenshot.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle("Give each account its own color", isOn: $colorCodeAccounts)
                Text("Each account gets a colored edge and icon on the Usage and Accounts tabs, so you can tell whose numbers you are looking at. Turn it off for plain cards.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Appearance") {
                Picker("Language", selection: $appLanguage) {
                    Text("Automatic").tag("auto")
                    Divider()
                    Text("English").tag("en")
                    Text("中文（简体）").tag("zh-Hans")
                    Text("日本語").tag("ja")
                    Text("Deutsch").tag("de")
                    Text("Français").tag("fr")
                }
                .onChange(of: appLanguage) { _, newValue in
                    applyLanguage(newValue)
                }
            }

            Section("System") {
                Toggle("Launch at login", isOn: $launchAtLogin)
                    .onChange(of: launchAtLogin) { _, newValue in
                        toggleLaunchAtLogin(newValue)
                    }
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    // MARK: - Menu Bar Tab

    private var menuBarTab: some View {
        Form {
            Section("Appearance") {
                Toggle("Show PixelSwitch logo in menu bar", isOn: $menuBarConfig.showsHeadIcon)
            }

            Section("Limit bars") {
                Toggle("Customize limit bar colors", isOn: $menuBarConfig.customizesLimitBarColors)

                // Collapsed while off, so the stock layout keeps the module list
                // and its preview visible without scrolling.
                if menuBarConfig.customizesLimitBarColors {
                    ColorPicker("Session bar color", selection: sessionLimitBarColor, supportsOpacity: false)
                    ColorPicker("Weekly bar color", selection: weeklyLimitBarColor, supportsOpacity: false)
                    ColorPicker("Fable bar color", selection: fableLimitBarColor, supportsOpacity: false)
                    ColorPicker("Low remaining color", selection: lowRemainingLimitBarColor, supportsOpacity: false)

                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("Low remaining threshold")
                            Spacer()
                            Text("\(Int(menuBarConfig.lowRemainingWarningThreshold))%")
                                .foregroundStyle(.secondary)
                                .monospacedDigit()
                        }
                        Slider(value: $menuBarConfig.lowRemainingWarningThreshold, in: 0...100, step: 5)
                        Text("Warns when this much quota or less is left. At 30%, bars turn at 70% used.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section {
                MenuBarModulesSettingsView()
                    .environmentObject(appState)
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    // MARK: - About Tab

    private var aboutTab: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                Image(nsImage: NSApp.applicationIconImage)
                    .resizable()
                    .interpolation(.high)
                    .frame(width: 72, height: 72)

                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: "PixelSwitch")
                        .font(.title.weight(.semibold))
                    Text("Claude Code Account Switcher")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text("Version \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0")")
                        .font(.caption.monospaced())
                        .foregroundStyle(.tertiary)
                }
            }

            Divider()
                .padding(.vertical, 16)

            VStack(alignment: .leading, spacing: 10) {
                aboutPoint("Switch Claude Code accounts from the menu bar in one click.")
                aboutPoint("Reads only recent usage history (24 hours by default), so it stays light on memory.")
                aboutPoint("Can switch for you before an account runs out.")
            }

            Button(updateChecker.isChecking ? "Checking..." : "Check for Updates") {
                updateChecker.checkForUpdates(manual: true)
            }
            .disabled(updateChecker.isChecking)
            .padding(.top, 20)

            Spacer(minLength: 16)

            Divider()
                .padding(.bottom, 12)

            HStack(alignment: .lastTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    pixelVenturesWordmark
                    Text(verbatim: "© 2026 Pixel Ventures")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    // A copyright notice, not a tagline. Most of the code in
                    // this app was written by other people and no licence has
                    // been granted over it, so naming them is not optional and
                    // this line stays until that changes. "Based on" understated
                    // it; this says what is actually true.
                    Link(destination: URL(string: "https://github.com/XueshiQiao/CCSwitcher")!) {
                        Text(verbatim: "Includes code © Xueshi Qiao and contributors")
                    }
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                }

                Spacer()

                Link(destination: URL(string: "https://pixelventures.ai")!) {
                    Text(verbatim: "pixelventures.ai")
                }
                .font(.caption)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    /// One line of the About summary, led by the Pixel Ventures unit square.
    private func aboutPoint(_ text: LocalizedStringKey) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Rectangle()
                .fill(Color.pixelVenturesGreen)
                .frame(width: 6, height: 6)
            Text(text)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// "Pixel Ventures" followed by its signature unit square, the brand's
    /// full stop (pixelventures brand/tokens.json, `mark.square`).
    private var pixelVenturesWordmark: some View {
        HStack(alignment: .lastTextBaseline, spacing: 3) {
            Text(verbatim: "Pixel Ventures")
                .font(.callout.weight(.medium))
            Rectangle()
                .fill(Color.pixelVenturesGreen)
                .frame(width: 4, height: 4)
        }
    }

    // MARK: - Helpers

    private func applyLanguage(_ lang: String) {
        // Set AppleLanguages for next launch; .environment(\.locale) handles live update
        if lang == "auto" {
            UserDefaults.standard.removeObject(forKey: "AppleLanguages")
        } else {
            UserDefaults.standard.set([lang], forKey: "AppleLanguages")
        }
    }

    private func toggleLaunchAtLogin(_ enable: Bool) {
        do {
            if enable {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            launchAtLogin = !enable // revert on failure
        }
    }

    private var sessionLimitBarColor: Binding<Color> {
        colorBinding(
            keyPath: \.sessionLimitBarColorHex,
            fallback: MenuBarConfig.defaultSessionLimitBarColorHex
        )
    }

    private var weeklyLimitBarColor: Binding<Color> {
        colorBinding(
            keyPath: \.weeklyLimitBarColorHex,
            fallback: MenuBarConfig.defaultWeeklyLimitBarColorHex
        )
    }

    private var fableLimitBarColor: Binding<Color> {
        colorBinding(
            keyPath: \.fableLimitBarColorHex,
            fallback: MenuBarConfig.defaultFableLimitBarColorHex
        )
    }

    private var lowRemainingLimitBarColor: Binding<Color> {
        colorBinding(
            keyPath: \.lowRemainingLimitBarColorHex,
            fallback: MenuBarConfig.defaultLowRemainingLimitBarColorHex
        )
    }

    private func colorBinding(keyPath: ReferenceWritableKeyPath<MenuBarConfig, String>, fallback: String) -> Binding<Color> {
        Binding(
            get: {
                Color(hexRGB: menuBarConfig[keyPath: keyPath])
                    ?? Color(hexRGB: fallback)
                    ?? .brand
            },
            set: { color in
                if let hex = color.hexRGB {
                    menuBarConfig[keyPath: keyPath] = hex
                }
            }
        )
    }
}
