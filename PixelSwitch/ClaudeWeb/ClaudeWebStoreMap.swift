import Foundation

/// Which web data store belongs to which PixelSwitch account.
///
/// Each account keeps its own, separate claude.ai sign-in inside PixelSwitch, so a
/// window opened for one account can never show another account's page. WebKit
/// keeps a persistent data store (cookies, local storage) per identifier; this
/// file decides the identifier. The mapping is created once, saved in
/// UserDefaults, and never changes until that account's web sign-in is removed.
///
/// Pure and Foundation only, so the unit harness compiles it. The WebKit side
/// (`ClaudeWebSessionStore`) turns an identifier into a `WKWebsiteDataStore`.
/// Call it from the main thread: a read-then-write is not atomic.
enum ClaudeWebStoreMap {
    /// Namespaced like the app's other keys. The value is a dictionary of
    /// account id (lowercased) to a UUID string.
    static let defaultsKey = "ai.pixelventures.pixelswitch.claudeWeb.dataStoreIDs"

    /// Account ids are UUIDs and UUIDs compare without regard to case, so two
    /// spellings of one id must land on one data store.
    static func normalized(_ accountID: String) -> String {
        accountID.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// Every saved mapping. An entry whose value is not a UUID is skipped (and
    /// replaced the next time that account asks for an identifier).
    static func mapping(defaults: UserDefaults = .standard) -> [String: UUID] {
        guard let stored = defaults.dictionary(forKey: defaultsKey) as? [String: String] else { return [:] }
        var result: [String: UUID] = [:]
        for (account, text) in stored {
            if let id = UUID(uuidString: text), id != zero { result[account] = id }
        }
        return result
    }

    /// The saved identifier for this account, or nil when it has none yet.
    static func existingIdentifier(for accountID: String, defaults: UserDefaults = .standard) -> UUID? {
        mapping(defaults: defaults)[normalized(accountID)]
    }

    /// The identifier for this account: the saved one, or a new one that is saved
    /// first. A new identifier is never one another account already holds (and
    /// never the all-zero UUID, which WebKit refuses). `makeID` is a seam for tests.
    static func identifier(for accountID: String, defaults: UserDefaults = .standard,
                           makeID: () -> UUID = UUID.init) -> UUID {
        let key = normalized(accountID)
        var all = mapping(defaults: defaults)
        if let existing = all[key] { return existing }
        let taken = Set(all.values)
        var candidate = makeID()
        var attempts = 0
        while (taken.contains(candidate) || candidate == zero) && attempts < 16 {
            candidate = makeID()
            attempts += 1
        }
        all[key] = candidate
        save(all, defaults: defaults)
        return candidate
    }

    /// Drops the mapping and returns the identifier it held, so the caller can
    /// remove that data store. The next `identifier(for:)` makes a new one.
    @discardableResult
    static func forget(_ accountID: String, defaults: UserDefaults = .standard) -> UUID? {
        var all = mapping(defaults: defaults)
        guard let removed = all.removeValue(forKey: normalized(accountID)) else { return nil }
        save(all, defaults: defaults)
        return removed
    }

    private static let zero = UUID(uuid: (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    private static func save(_ all: [String: UUID], defaults: UserDefaults) {
        let text = all.mapValues { $0.uuidString }
        if text.isEmpty {
            defaults.removeObject(forKey: defaultsKey)
        } else {
            defaults.set(text, forKey: defaultsKey)
        }
    }
}
