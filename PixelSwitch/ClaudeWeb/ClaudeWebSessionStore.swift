import Foundation
import WebKit

private let storeLog = FileLog("ClaudeWeb")

/// One persistent claude.ai web sign-in per PixelSwitch account.
///
/// WebKit keeps cookies and local storage in a data store per identifier
/// (macOS 14). `ClaudeWebStoreMap` gives each account its own identifier, so
/// signing in to claude.ai inside one account's window never touches another
/// account's window, and the sign-in survives quitting PixelSwitch.
@MainActor
enum ClaudeWebSessionStore {
    /// The account's data store, created on first use.
    static func dataStore(for accountID: String, defaults: UserDefaults = .standard) -> WKWebsiteDataStore {
        let id = ClaudeWebStoreMap.identifier(for: accountID, defaults: defaults)
        return WKWebsiteDataStore(forIdentifier: id)
    }

    /// True when this account has a saved web sign-in store.
    static func hasStore(for accountID: String, defaults: UserDefaults = .standard) -> Bool {
        ClaudeWebStoreMap.existingIdentifier(for: accountID, defaults: defaults) != nil
    }

    /// One removal per account at a time, whoever asks: the window's "Sign out of
    /// claude.ai here" and account removal both come through `removeSignIn`.
    private static let removals = ClaudeWebRemovalGate()

    /// Removes the account's web sign-in: deletes its data store and forgets the
    /// identifier, so the next window starts signed out under a new one.
    ///
    /// Every web view that uses the store must be released first (WebKit refuses
    /// otherwise), so the caller closes the account's window before calling this.
    /// If WebKit still refuses after one retry, the store's data is wiped in
    /// place and the identifier is kept. Returns false only if neither worked.
    ///
    /// A second call for an account whose removal is already running does not start
    /// another: it waits and returns that one's answer (`ClaudeWebRemovalGate`), so
    /// two removals can never race each other into recreating a deleted store.
    @discardableResult
    static func removeSignIn(for accountID: String, defaults: UserDefaults = .standard) async -> Bool {
        let saved = SendableDefaults(defaults)
        return await removals.run(accountID: accountID) {
            await performRemoval(for: accountID, defaults: saved.value)
        }
    }

    private static func performRemoval(for accountID: String, defaults: UserDefaults) async -> Bool {
        let tag = shortID(accountID)
        guard let id = ClaudeWebStoreMap.existingIdentifier(for: accountID, defaults: defaults) else {
            storeLog.info("[claudeWeb] sign-out for account \(tag): no web sign-in was saved")
            return true
        }
        for attempt in 1...2 {
            do {
                try await WKWebsiteDataStore.remove(forIdentifier: id)
                ClaudeWebStoreMap.forget(accountID, defaults: defaults)
                storeLog.info("[claudeWeb] sign-out for account \(tag): data store removed")
                return true
            } catch {
                storeLog.warning("[claudeWeb] sign-out for account \(tag): removing the data store failed on try \(attempt) (\(error.localizedDescription))")
                if attempt == 1 { try? await Task.sleep(for: .milliseconds(400)) }
            }
        }
        // Still in use or refused. Wipe what it holds; the identifier stays. But only if the
        // account still owns this identifier: opening a store by an identifier nobody holds
        // would create an orphan that nothing could ever remove.
        guard ClaudeWebStoreMap.isCurrent(id, for: accountID, defaults: defaults) else {
            storeLog.info("[claudeWeb] sign-out for account \(tag): the data store was already removed; nothing to wipe")
            return true
        }
        let store = WKWebsiteDataStore(forIdentifier: id)
        await store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)
        let remaining = await store.dataRecords(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes())
        let wiped = remaining.isEmpty
        storeLog.info("[claudeWeb] sign-out for account \(tag): data wiped in place, \(wiped ? "nothing left" : "\(remaining.count) records left")")
        return wiped
    }

    /// The first eight characters of an account id: enough to tell accounts apart in
    /// the log, and never an address.
    static func shortID(_ accountID: String) -> String {
        String(accountID.trimmingCharacters(in: .whitespacesAndNewlines).prefix(8)).uppercased()
    }
}
