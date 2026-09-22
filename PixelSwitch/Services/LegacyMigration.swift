import Foundation

private let log = FileLog("Migration")

/// PixelSwitch began as a fork of CCSwitcher (bundle ID `me.xueshi.ccswitcher`).
/// A new bundle ID means a new, empty preferences domain, so on first launch
/// this copies that app's saved accounts and settings across. Without it,
/// someone moving from CCSwitcher would open PixelSwitch to an empty account
/// list.
///
/// Credentials need no copying here: `KeychainService` reads the old item and
/// copies it into PixelSwitch's own on first launch, leaving the old one in
/// place. macOS asks once whether PixelSwitch may read it.
///
/// The identifier below is the one thing in PixelSwitch that still has to name
/// the app it was forked from, because it is the address the settings are read
/// FROM. It cannot be renamed without silently breaking the import for anyone
/// moving over. Dropping the import entirely is the only way to remove it, and
/// that is a product decision, not a cleanup.
enum LegacyMigration {
    private static let legacyDomain = "me.xueshi.ccswitcher"
    private static let doneKey = "didImportLegacySettings"

    /// Keys that belong to the old app's windows, updater or SwiftUI state
    /// rather than to the user's settings. Sparkle's `SU*` keys in particular
    /// must not come across: they record CCSwitcher's update history.
    /// `AppleLanguages` is derived from `appLanguage` at launch.
    private static let skippedPrefixes = [
        "NSWindow Frame", "NSStatusItem", "com_apple_SwiftUI", "SU", "AppleLanguages",
    ]

    /// Safe to call more than once; only the first call in the life of the
    /// install does anything. A setting already present in PixelSwitch is
    /// never overwritten, which is also why renaming `doneKey` was safe: the
    /// re-run it causes finds every key already set and copies nothing.
    static func runOnce() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: doneKey) else { return }
        defaults.set(true, forKey: doneKey)

        guard let legacy = defaults.persistentDomain(forName: legacyDomain), !legacy.isEmpty else {
            log.info("No legacy settings found; nothing to import")
            return
        }
        var copied: [String] = []
        for (key, value) in legacy where !skippedPrefixes.contains(where: { key.hasPrefix($0) }) {
            guard defaults.object(forKey: key) == nil else { continue }
            defaults.set(value, forKey: key)
            copied.append(key)
        }
        log.info("Copied \(copied.count) settings from \(legacyDomain): \(copied.sorted().joined(separator: ", "))")

        // The flag PixelSwitch used before this key was renamed. It is dead
        // weight now, and it names the app this one was forked from, so it goes.
        defaults.removeObject(forKey: "migratedFromCCSwitcher")
    }
}
