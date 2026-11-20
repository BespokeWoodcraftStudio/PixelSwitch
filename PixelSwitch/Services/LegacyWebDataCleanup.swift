import Foundation

/// The 2.2 claude.ai window kept one persistent web data store per account (cookies and local
/// storage), and saved which identifier belonged to which account in UserDefaults. 2.3 has no
/// such window, so a launch looks for what 2.2 left behind and tidies it, once.
///
/// This cleanup uses no web framework at all. 2.3 asked WebKit to delete the stores while no
/// web view existed in the process, and WebKit crashed on every launch (EXC_BAD_ACCESS inside
/// its own data store removal, 2026-11-20). So the cleanup is plain file work: forget the saved
/// list first, then delete the leftover store folders by name. Each store lives in
/// `~/Library/WebKit/<bundle id>/WebsiteDataStore/<identifier in lower case>`, which was
/// confirmed on disk for the two stores the 2.2 window made.
///
/// The cleanup can never block or break a launch: it runs off the main thread after the menu
/// bar item is up, it forgets the saved list before it touches any folder (so a failure can
/// never repeat on the next launch), and it catches every error.
///
/// Foundation only, so the unit harness compiles it.
enum LegacyWebDataCleanup {
    /// The key 2.2 saved the mapping under: a dictionary of account id to data store UUID text.
    static let defaultsKey = "ai.pixelventures.pixelswitch.claudeWeb.dataStoreIDs"

    /// True while the mapping key is still saved, which is the whole "has this run" flag:
    /// the cleanup removes the key first, so later launches do nothing.
    static func hasWork(defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: defaultsKey) != nil
    }

    /// The data store identifiers 2.2 saved, each once, in a fixed order. A saved value that
    /// is not a UUID, and the all-zero UUID, are left out.
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

    // MARK: - The plan

    /// One thing the cleanup does, in order.
    enum Step: Hashable {
        /// Remove the saved mapping key. Always the first step, so nothing that follows can make
        /// the cleanup run again at the next launch.
        case forgetSavedList
        /// Delete the leftover folder of one data store.
        case removeStoreFolder(UUID)
    }

    /// The steps for what is saved right now: nothing when no key is saved, otherwise the key's
    /// removal first and then one folder removal per saved identifier.
    static func plan(defaults: UserDefaults = .standard) -> [Step] {
        guard hasWork(defaults: defaults) else { return [] }
        return [.forgetSavedList] + pendingIdentifiers(defaults: defaults).map { Step.removeStoreFolder($0) }
    }

    /// Where the data stores of the app with this bundle identifier live:
    /// `<library>/WebKit/<bundle id>/WebsiteDataStore`.
    static func storesFolder(library: URL, bundleIdentifier: String) -> URL {
        library.appendingPathComponent("WebKit", isDirectory: true)
            .appendingPathComponent(bundleIdentifier, isDirectory: true)
            .appendingPathComponent("WebsiteDataStore", isDirectory: true)
    }

    /// The folder for this running app, or nil when the bundle identifier or the user's Library
    /// folder cannot be read (the folders are then left alone).
    static func defaultStoresFolder(fileManager: FileManager = .default) -> URL? {
        guard let bundleIdentifier = Bundle.main.bundleIdentifier,
              !bundleIdentifier.isEmpty, !bundleIdentifier.contains("/"), bundleIdentifier != "..",
              let library = fileManager.urls(for: .libraryDirectory, in: .userDomainMask).first else { return nil }
        return storesFolder(library: library, bundleIdentifier: bundleIdentifier)
    }

    // MARK: - Doing it

    /// What a run did, for the log line.
    struct Outcome: Equatable {
        /// The saved mapping key was removed.
        var forgotSavedList = false
        /// Leftover store folders that were deleted.
        var foldersRemoved = 0
        /// Saved identifiers that had no folder on disk, which is the normal case after the
        /// first run on a Mac where a store was already removed.
        var foldersAbsent = 0
        /// Folders that could not be deleted, or that were left on purpose (a link or a plain
        /// file in the folder's place).
        var foldersLeft = 0
    }

    /// Runs the plan in order. Every error is caught, so this never throws and never traps.
    ///
    /// Only a direct child of `storesFolder` whose name is one of the saved identifiers (any
    /// letter case) is ever deleted, and only when it is a real folder: a link or a plain file
    /// with that name is left alone, so nothing outside the folder can be reached.
    @discardableResult
    static func run(defaults: UserDefaults = .standard, storesFolder: URL?, fileManager: FileManager = .default) -> Outcome {
        var outcome = Outcome()
        for step in plan(defaults: defaults) {
            switch step {
            case .forgetSavedList:
                defaults.removeObject(forKey: defaultsKey)
                outcome.forgotSavedList = true
            case .removeStoreFolder(let id):
                guard let storesFolder else { outcome.foldersLeft += 1; continue }
                switch removeFolder(of: id, in: storesFolder, fileManager: fileManager) {
                case .removed: outcome.foldersRemoved += 1
                case .absent: outcome.foldersAbsent += 1
                case .left: outcome.foldersLeft += 1
                }
            }
        }
        return outcome
    }

    private enum FolderResult { case removed, absent, left }

    private static func removeFolder(of id: UUID, in storesFolder: URL, fileManager: FileManager) -> FolderResult {
        guard let names = try? fileManager.contentsOfDirectory(atPath: storesFolder.path) else { return .absent }
        let wanted = id.uuidString.lowercased()
        guard let name = names.first(where: { $0.lowercased() == wanted }) else { return .absent }
        let folder = storesFolder.appendingPathComponent(name, isDirectory: true)
        // attributesOfItem does not follow a final link, so a link shows up as a link.
        guard let type = (try? fileManager.attributesOfItem(atPath: folder.path))?[.type] as? FileAttributeType,
              type == .typeDirectory else { return .left }
        do {
            try fileManager.removeItem(at: folder)
            return .removed
        } catch {
            return .left
        }
    }

    /// The launch entry point. With nothing saved it does nothing and logs nothing; otherwise it
    /// forgets the saved list, deletes the leftover folders and passes one line to `log`.
    static func runAtLaunch(defaults: UserDefaults = .standard, log: (String) -> Void) {
        guard hasWork(defaults: defaults) else { return }
        let outcome = run(defaults: defaults, storesFolder: defaultStoresFolder())
        log("[webCleanup] forgot the saved claude.ai window list left by 2.2; "
            + "removed \(outcome.foldersRemoved) leftover data folder(s), \(outcome.foldersAbsent) already gone, \(outcome.foldersLeft) left in place")
    }
}
