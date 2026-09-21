import Foundation

private let log = FileLog("Migration")

/// PixelSwitch began as a fork of CCSwitcher (bundle ID `me.xueshi.ccswitcher`).
/// A new bundle ID means a new, empty preferences domain, so on first launch
/// this copies that app's saved accounts and settings across. Without it,
/// someone moving from CCSwitcher would open PixelSwitch to an empty account
/// list.
///
/// Credentials need no copying: the per-account backups live in one keychain
/// item whose service name (`me.xueshi.ccswitcher.backups`) PixelSwitch keeps
/// on purpose (see `KeychainService`). macOS asks once whether PixelSwitch may
/// read it.
enum LegacyMigration {
    private static let legacyDomain = "me.xueshi.ccswitcher"
    private static let doneKey = "migratedFromCCSwitcher"

    /// Keys that belong to the old app's windows, updater or SwiftUI state
    /// rather than to the user's settings. Sparkle's `SU*` keys in particular
    /// must not come across: they record CCSwitcher's update history.
    /// `AppleLanguages` is derived from `appLanguage` at launch.
    private static let skippedPrefixes = [
        "NSWindow Frame", "NSStatusItem", "com_apple_SwiftUI", "SU", "AppleLanguages",
    ]

    /// Safe to call more than once; only the first call in the life of the
    /// install does anything. A setting already present in PixelSwitch is
    /// never overwritten.
    static func runOnce() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: doneKey) else { return }
        defaults.set(true, forKey: doneKey)

        guard let legacy = defaults.persistentDomain(forName: legacyDomain), !legacy.isEmpty else {
            log.info("No CCSwitcher settings found; nothing to migrate")
            return
        }
        var copied: [String] = []
        for (key, value) in legacy where !skippedPrefixes.contains(where: { key.hasPrefix($0) }) {
            guard defaults.object(forKey: key) == nil else { continue }
            defaults.set(value, forKey: key)
            copied.append(key)
        }
        log.info("Copied \(copied.count) settings from \(legacyDomain): \(copied.sorted().joined(separator: ", "))")
    }
}
