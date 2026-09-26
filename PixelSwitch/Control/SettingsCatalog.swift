import Foundation

/// Every setting the Settings window offers, by the name the CLI and the
/// control API use, with the values it accepts. Pure: `SettingsStore` reads
/// and writes the real values; this only knows their shape.
///
/// The hysteresis (10 points) and the cooldown (300 s) are not settings, in
/// the window or here.
enum SettingKey: String, CaseIterable, Sendable {
    case refreshInterval = "refreshInterval"
    case transcriptLookbackHours = "transcriptLookbackHours"
    case autoSwitchEnabled = "autoSwitch.enabled"
    case autoSwitchThreshold = "autoSwitch.threshold"
    case autoSwitchOnFable = "autoSwitch.onFable"
    case autoSwitchStrategy = "autoSwitch.strategy"
    case autoSwitchDrainEarly = "autoSwitch.drainEarly"
    case autoSwitchDrainWithinHours = "autoSwitch.drainWithinHours"
    case maskEmailAddresses = "maskEmailAddresses"
    case colorCodeAccounts = "colorCodeAccounts"
    case appLanguage = "appLanguage"
    case launchAtLogin = "launchAtLogin"
    case menuBarShowsHeadIcon = "menuBar.showsHeadIcon"
    case menuBarModules = "menuBar.modules"
    case menuBarCustomizesLimitBarColors = "menuBar.customizesLimitBarColors"
    case menuBarColorSession = "menuBar.color.session"
    case menuBarColorWeekly = "menuBar.color.weekly"
    case menuBarColorFable = "menuBar.color.fable"
    case menuBarColorLowRemaining = "menuBar.color.lowRemaining"
    case menuBarLowRemainingWarningThreshold = "menuBar.lowRemainingWarningThreshold"
    case claudeBinaryPath = "claude.binaryPath"

    enum Kind: Equatable, Sendable {
        case bool
        /// One of these values exactly.
        case choice([JSONValue])
        /// A number in range, rounded to `step` when given.
        case number(ClosedRange<Double>, step: Double?)
        /// An ordered list drawn from these values, no repeats.
        case list([String])
        /// `#RRGGBB`.
        case color
        /// `""` (auto-detect) or an absolute path.
        case path
    }

    var kind: Kind {
        switch self {
        case .refreshInterval: return .choice([15, 30, 60, 300, 600].map(JSONValue.number))
        case .transcriptLookbackHours: return .choice([24, 72, 168, 720, 0].map(JSONValue.number))
        case .autoSwitchEnabled, .autoSwitchOnFable, .autoSwitchDrainEarly, .maskEmailAddresses,
             .colorCodeAccounts, .launchAtLogin, .menuBarShowsHeadIcon, .menuBarCustomizesLimitBarColors:
            return .bool
        case .autoSwitchThreshold: return .number(AutoSwitchSettings.thresholdRange, step: 1)
        case .autoSwitchStrategy: return .choice(AutoSwitchStrategy.allCases.map { .string($0.rawValue) })
        case .autoSwitchDrainWithinHours: return .number(AutoSwitchSettings.drainHoursRange, step: 1)
        case .appLanguage: return .choice(["auto", "en", "zh-Hans", "ja", "de", "fr"].map(JSONValue.string))
        case .menuBarModules: return .list(MenuBarModule.allCases.map(\.rawValue))
        case .menuBarColorSession, .menuBarColorWeekly, .menuBarColorFable, .menuBarColorLowRemaining: return .color
        case .menuBarLowRemainingWarningThreshold: return .number(0...100, step: nil)
        case .claudeBinaryPath: return .path
        }
    }

    /// One line for `pixelswitch settings` and the MCP tool description.
    var summary: String {
        switch kind {
        case .bool: return "true or false"
        case .choice(let values): return "one of " + values.map(\.compactText).joined(separator: ", ")
        case .number(let range, let step):
            return "\(Self.format(range.lowerBound))–\(Self.format(range.upperBound))" + (step == 1 ? ", whole numbers" : "")
        case .list(let values): return "a list drawn from " + values.joined(separator: ", ")
        case .color: return "a colour as #RRGGBB"
        case .path: return "\"\" to auto-detect, or an absolute path"
        }
    }

    /// `value` in the canonical form this setting stores, or `invalidValue`.
    /// Accepts what a person types on a command line too: "true"/"on"/"yes",
    /// numbers as text, a comma-separated list.
    func normalize(_ value: JSONValue) throws -> JSONValue {
        switch kind {
        case .bool:
            if let bool = value.boolValue { return .bool(bool) }
            if let number = value.doubleValue, number == 0 || number == 1 { return .bool(number == 1) }
            if let text = value.stringValue?.lowercased() {
                if ["true", "on", "yes", "1"].contains(text) { return .bool(true) }
                if ["false", "off", "no", "0"].contains(text) { return .bool(false) }
            }
            throw invalid()

        case .choice(let values):
            if let number = Self.number(value), let match = values.first(where: { $0.doubleValue == number }) { return match }
            if let text = value.stringValue,
               let match = values.first(where: { $0.stringValue?.caseInsensitiveCompare(text) == .orderedSame }) {
                return match
            }
            throw invalid()

        case .number(let range, let step):
            guard var number = Self.number(value), number.isFinite else { throw invalid() }
            if let step { number = (number / step).rounded() * step }
            guard range.contains(number) else { throw invalid() }
            return .number(number)

        case .list(let allowed):
            let items: [String]
            if let array = value.arrayValue {
                items = try array.map { item in
                    guard let text = item.stringValue else { throw invalid() }
                    return text
                }
            } else if let text = value.stringValue {
                items = text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            } else {
                throw invalid()
            }
            var seen = Set<String>()
            var result: [JSONValue] = []
            for item in items {
                guard let canonical = allowed.first(where: { $0.caseInsensitiveCompare(item) == .orderedSame }) else { throw invalid() }
                if seen.insert(canonical).inserted { result.append(.string(canonical)) }
            }
            return .array(result)

        case .color:
            guard var text = value.stringValue?.trimmingCharacters(in: .whitespaces) else { throw invalid() }
            if text.hasPrefix("#") { text.removeFirst() }
            guard text.count == 6, text.allSatisfy(\.isHexDigit) else { throw invalid() }
            return .string("#" + text.uppercased())

        case .path:
            guard let text = value.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) else { throw invalid() }
            if text.isEmpty || text.lowercased() == "auto" { return .string("") }
            guard text.hasPrefix("/") else { throw invalid() }
            return .string(text)
        }
    }

    /// Finds a key by its name, case-insensitive, or lists the valid names.
    static func named(_ name: String) throws -> SettingKey {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        if let key = allCases.first(where: { $0.rawValue.caseInsensitiveCompare(trimmed) == .orderedSame }) { return key }
        throw ControlError(.notFound, "There is no setting called \"\(trimmed)\".", candidates: allCases.map(\.rawValue))
    }

    private func invalid() -> ControlError {
        ControlError(.invalidValue, "\(rawValue) must be \(summary).")
    }

    private static func number(_ value: JSONValue) -> Double? {
        if let number = value.doubleValue { return number }
        if let text = value.stringValue?.trimmingCharacters(in: .whitespaces) { return Double(text) }
        return nil
    }

    private static func format(_ number: Double) -> String {
        number.rounded() == number ? String(Int(number)) : String(number)
    }
}
