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
