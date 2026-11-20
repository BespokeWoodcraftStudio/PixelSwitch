import Foundation

/// The 2.2 claude.ai window kept one persistent WebKit data store per account
/// (cookies and local storage), and saved which identifier belonged to which account in
/// UserDefaults. 2.3 has no such window, so each launch looks for what 2.2 left behind and
/// removes it (`LegacyWebDataCleanupRunner` does the WebKit side). This file is the pure
/// half: read the saved identifiers, and settle the saved mapping afterwards.
///
/// Foundation only, so the unit harness compiles it.
enum LegacyWebDataCleanup {
    /// The key 2.2 saved the mapping under: a dictionary of account id to data store UUID text.
    static let defaultsKey = "ai.pixelventures.pixelswitch.claudeWeb.dataStoreIDs"

    /// True while the mapping key is still saved, which is the whole "has this run" flag:
    /// once every store is gone the key is removed and later launches do nothing.
    static func hasWork(defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: defaultsKey) != nil
    }

    /// The data store identifiers 2.2 saved, each once, in a fixed order. A saved value that
    /// is not a UUID, and the all-zero UUID WebKit refuses, are left out.
    static func pendingIdentifiers(defaults: UserDefaults = .standard) -> [UUID] {
        guard let stored = defaults.dictionary(forKey: defaultsKey) as? [String: String] else { return [] }
        let zero = UUID(uuid: (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        var seen = Set<UUID>()
        var result: [UUID] = []
        for text in stored.values.sorted() {
            if let id = UUID(uuidString: text), id != zero, seen.insert(id).inserted { result.append(id) }
        }
        return result
    }

    /// Records the outcome: the mapping key is removed, except for the entries whose store
    /// could not be removed (`failed`), which stay so the next launch tries again.
    static func settle(failed: [UUID], defaults: UserDefaults = .standard) {
        let keep = Set(failed)
        let stored = defaults.dictionary(forKey: defaultsKey) as? [String: String] ?? [:]
        let remaining = stored.filter { entry in UUID(uuidString: entry.value).map(keep.contains) ?? false }
        if remaining.isEmpty {
            defaults.removeObject(forKey: defaultsKey)
        } else {
            defaults.set(remaining, forKey: defaultsKey)
        }
    }
}
