import Foundation
import ServiceManagement

/// Reads and writes every setting in `SettingKey`, and owns each one's side
/// effect, so a change from the command line takes effect exactly as the same
/// change in the Settings window. The window's `onChange` handlers call the
/// same side-effect methods here; `@AppStorage` keeps the window in sync.
@MainActor
final class SettingsStore {
    static let shared = SettingsStore()

    /// Set once at launch (`RemoteControlService.start`).
    weak var appState: AppState?
    /// Set once at launch; owns "Update automatically" (Sparkle keeps the value).
    weak var updateChecker: UpdateChecker?

    private let defaults = UserDefaults.standard

    private init() {}

    // MARK: - Reading

    func value(_ key: SettingKey) -> JSONValue {
        let menuBar = MenuBarConfig.shared
        switch key {
        case .refreshInterval:
            return .number(defaults.object(forKey: key.rawValue) as? Double ?? 300)
        case .transcriptLookbackHours:
            return .number((defaults.object(forKey: key.rawValue) as? NSNumber)?.doubleValue ?? 24)
        case .autoSwitchEnabled:
            return .bool(defaults.bool(forKey: AutoSwitchSettings.enabledKey))
        case .autoSwitchThreshold:
            return .number(AutoSwitchSettings.defaultThreshold)
        case .autoSwitchOnFable:
            return .bool(AutoSwitchFableSetting.isOn)
        case .autoSwitchStrategy:
            return .string(AutoSwitchSettings.strategy.rawValue)
        case .autoSwitchDrainEarly:
            return .bool(AutoSwitchSettings.drainEarly)
        case .autoSwitchDrainWithinHours:
            return .number(AutoSwitchSettings.drainWithinHours)
        case .maskEmailAddresses:
            return .bool(EmailDisplay.isMasked)
        case .colorCodeAccounts:
            return .bool(AccountColorCoding.isOn)
        case .appLanguage:
            return .string(defaults.string(forKey: key.rawValue) ?? "auto")
        case .launchAtLogin:
            return .bool(launchAtLogin)
        case .menuBarShowsHeadIcon:
            return .bool(menuBar.showsHeadIcon)
        case .menuBarModules:
            return .array(menuBar.modules.map { .string($0.rawValue) })
        case .menuBarCustomizesLimitBarColors:
            return .bool(menuBar.customizesLimitBarColors)
        case .menuBarColorSession:
            return .string(menuBar.sessionLimitBarColorHex)
        case .menuBarColorWeekly:
            return .string(menuBar.weeklyLimitBarColorHex)
        case .menuBarColorFable:
            return .string(menuBar.fableLimitBarColorHex)
        case .menuBarColorLowRemaining:
            return .string(menuBar.lowRemainingLimitBarColorHex)
        case .menuBarLowRemainingWarningThreshold:
            return .number(menuBar.lowRemainingWarningThreshold)
        case .claudeBinaryPath:
            return .string(defaults.string(forKey: kClaudeBinaryPathPreferenceKey) ?? "")
        case .autoUpdate:
            // Sparkle's order: the saved choice, else the Info.plist default (on).
            return .bool(updateChecker?.installsAutomatically
                         ?? (defaults.object(forKey: "SUAutomaticallyUpdate") as? Bool
                             ?? Bundle.main.object(forInfoDictionaryKey: "SUAutomaticallyUpdate") as? Bool ?? false))
        }
    }

    // MARK: - Writing

    /// Writes an already normalized value (`SettingKey.normalize`) and applies
    /// its side effect. Throws `ControlError` when the value cannot be applied.
    func set(_ key: SettingKey, to value: JSONValue) throws {
        let menuBar = MenuBarConfig.shared
        switch key {
        case .refreshInterval:
            guard let seconds = value.doubleValue else { throw wrongShape(key) }
            defaults.set(seconds, forKey: key.rawValue)
            refreshIntervalChanged(seconds)
        case .transcriptLookbackHours:
            guard let hours = value.doubleValue else { throw wrongShape(key) }
            defaults.set(Int(hours), forKey: key.rawValue)
            lookbackChanged()
        case .autoSwitchEnabled:
            defaults.set(try bool(value, key), forKey: AutoSwitchSettings.enabledKey)
        case .autoSwitchThreshold:
            guard let number = value.doubleValue else { throw wrongShape(key) }
            defaults.set(number, forKey: AutoSwitchSettings.thresholdKey)
        case .autoSwitchOnFable:
            defaults.set(try bool(value, key), forKey: AutoSwitchSettings.onFableKey)
        case .autoSwitchStrategy:
            guard let name = value.stringValue else { throw wrongShape(key) }
            defaults.set(name, forKey: AutoSwitchSettings.strategyKey)
        case .autoSwitchDrainEarly:
            defaults.set(try bool(value, key), forKey: AutoSwitchSettings.drainEarlyKey)
        case .autoSwitchDrainWithinHours:
            guard let hours = value.doubleValue else { throw wrongShape(key) }
            defaults.set(hours, forKey: AutoSwitchSettings.drainWithinHoursKey)
        case .maskEmailAddresses:
            defaults.set(try bool(value, key), forKey: EmailDisplay.key)
        case .colorCodeAccounts:
            defaults.set(try bool(value, key), forKey: AccountColorCoding.key)
        case .appLanguage:
            guard let language = value.stringValue else { throw wrongShape(key) }
            defaults.set(language, forKey: key.rawValue)
            applyLanguage(language)
        case .launchAtLogin:
            try setLaunchAtLogin(try bool(value, key))
        case .menuBarShowsHeadIcon:
            menuBar.showsHeadIcon = try bool(value, key)
        case .menuBarModules:
            guard let names = value.arrayValue?.compactMap(\.stringValue) else { throw wrongShape(key) }
            menuBar.set(names.compactMap(MenuBarModule.init(rawValue:)))
        case .menuBarCustomizesLimitBarColors:
            menuBar.customizesLimitBarColors = try bool(value, key)
        case .menuBarColorSession:
            menuBar.sessionLimitBarColorHex = try string(value, key)
        case .menuBarColorWeekly:
            menuBar.weeklyLimitBarColorHex = try string(value, key)
        case .menuBarColorFable:
            menuBar.fableLimitBarColorHex = try string(value, key)
        case .menuBarColorLowRemaining:
            menuBar.lowRemainingLimitBarColorHex = try string(value, key)
        case .menuBarLowRemainingWarningThreshold:
            guard let number = value.doubleValue else { throw wrongShape(key) }
            menuBar.lowRemainingWarningThreshold = number
        case .claudeBinaryPath:
            let path = try string(value, key)
            guard path.isEmpty || FileManager.default.isExecutableFile(atPath: path) else {
                throw ControlError(.invalidValue, "No executable file at \(path).")
            }
            applyClaudeBinaryPath(path)
        case .autoUpdate:
            let on = try bool(value, key)
            guard let updateChecker else {
                throw ControlError(.failed, "The updater is not running yet. Try again in a moment.")
            }
            updateChecker.setInstallsAutomatically(on)
        }
    }

    // MARK: - Side effects, shared with the Settings window

    /// Settings → General → Auto-refresh interval.
    func refreshIntervalChanged(_ seconds: Double) {
        appState?.startAutoRefresh(interval: seconds)
    }

    /// Settings → General → Usage history window.
    func lookbackChanged() {
        guard let appState else { return }
        Task { await appState.refresh() }
    }

    /// Settings → General → Language: takes effect in the menu bar at once
    /// (`PixelSwitchApp` follows the key) and everywhere else on next launch.
    func applyLanguage(_ language: String) {
        if language == "auto" {
            defaults.removeObject(forKey: "AppleLanguages")
        } else {
            defaults.set([language], forKey: "AppleLanguages")
        }
    }

    var launchAtLogin: Bool {
        SMAppService.mainApp.status == .enabled
    }

    /// Settings → General → Launch at login.
    func setLaunchAtLogin(_ enable: Bool) throws {
        guard enable != launchAtLogin else { return }
        do {
            if enable {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            throw ControlError(.failed, "macOS refused to change Launch at login: \(error.localizedDescription)")
        }
    }

    /// Settings → Claude CLI → Binary path. "" means auto-detect.
    func applyClaudeBinaryPath(_ path: String) {
        defaults.set(path, forKey: kClaudeBinaryPathPreferenceKey)
        ClaudeService.shared.setPath(path.isEmpty ? nil : path)
        lookbackChanged()
    }

    // MARK: - Helpers

    private func bool(_ value: JSONValue, _ key: SettingKey) throws -> Bool {
        guard let bool = value.boolValue else { throw wrongShape(key) }
        return bool
    }

    private func string(_ value: JSONValue, _ key: SettingKey) throws -> String {
        guard let text = value.stringValue else { throw wrongShape(key) }
        return text
    }

    private func wrongShape(_ key: SettingKey) -> ControlError {
        ControlError(.invalidValue, "\(key.rawValue) must be \(key.summary).")
    }
}
