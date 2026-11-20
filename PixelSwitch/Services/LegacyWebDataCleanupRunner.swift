import Foundation
import WebKit

private let cleanupLog = FileLog("ClaudeWeb")

/// Removes the WebKit data stores the 2.2 claude.ai window left on this Mac, once.
///
/// It only deletes: it creates no web view, loads no page and runs no script. The choice of
/// what to delete (`LegacyWebDataCleanup`) is pure and unit-tested; this is the WebKit call.
@MainActor
enum LegacyWebDataCleanupRunner {
    /// Called once at launch. With nothing saved from 2.2 it does nothing and logs nothing;
    /// otherwise it removes each saved data store, forgets the saved mapping and writes one
    /// log line. A store that cannot be removed keeps its mapping entry and is tried again
    /// at the next launch.
    static func runOnce(defaults: UserDefaults = .standard) async {
        guard LegacyWebDataCleanup.hasWork(defaults: defaults) else { return }
        let identifiers = LegacyWebDataCleanup.pendingIdentifiers(defaults: defaults)
        var failed: [UUID] = []
        for id in identifiers {
            do {
                try await WKWebsiteDataStore.remove(forIdentifier: id)
            } catch {
                failed.append(id)
            }
        }
        LegacyWebDataCleanup.settle(failed: failed, defaults: defaults)
        let removed = identifiers.count - failed.count
        if failed.isEmpty {
            cleanupLog.info("[webCleanup] removed \(removed) claude.ai window data store(s) left by 2.2 and forgot their saved list")
        } else {
            cleanupLog.warning("[webCleanup] removed \(removed) claude.ai window data store(s) left by 2.2; \(failed.count) could not be removed and will be tried again at the next launch")
        }
    }
}
