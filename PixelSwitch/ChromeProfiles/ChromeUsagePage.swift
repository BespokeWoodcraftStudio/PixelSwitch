import AppKit

private let chromeLog = FileLog("ChromeResets")

/// The API the Limit resets button calls.
///
/// `open(accountID:email:)` opens claude.ai's usage page (`ChromeUsageLaunch.usageURLString`)
/// in the Chrome profile that belongs to the account, where the founder is already signed
/// in to claude.ai, and he clicks claude.ai's own Reset button himself. PixelSwitch only
/// opens the page: it never clicks, submits or scripts anything on claude.ai and never calls a
/// reset address (the founder, 2026-11-19 and 2026-11-20: "you absolutly do not execute a
/// reset").
///
/// What it reads: Chrome's `Local State` file, for its profile list and nothing else in
/// Chrome's folders. What it runs: `/usr/bin/open` with an argument array (no shell). With
/// Chrome not installed, the system's default browser opens the page instead.
/// The decisions are in `ChromeUsageLaunch`, `ChromeProfileMatcher` and `ChromeLocalState`
/// (pure and unit-tested); this file is only the side effects.
@MainActor
enum ChromeUsagePage {
    /// Opens the account's usage page in its Chrome profile. `accountID` is
    /// `account.id.uuidString`; `email` is the account's address (it picks the profile
    /// and names a new one; it is never logged).
    static func open(accountID: String, email: String) {
        let tag = shortID(accountID)
        // An account with a stored choice never looks at Chrome's profile list (the choice wins), so
        // Chrome's folder is not even touched for it.
        let read: (data: Data?, failure: ChromeLocalState.ReadFailure?) =
            ChromeProfileChoiceStore.saved(forAccountID: accountID) == nil ? readLocalState() : (data: nil, failure: nil)
        let route = ChromeUsageLaunch.route(accountID: accountID, email: email, localState: read.data,
                                            chromeInstalled: isChromeInstalled(), localStateFailure: read.failure)
        switch route {
        case .defaultBrowser:
            chromeLog.info("[chromeResets] account \(tag): Chrome is not installed, opening the usage page in the default browser")
            openInDefaultBrowser(tag: tag)
        case .chromeSignedInAs(let address):
            chromeLog.info("[chromeResets] account \(tag): opening the usage page in the Chrome profile signed in to this address (the account's saved choice; if Chrome has none, it opens the profile used last)")
            launchChrome(arguments: ChromeUsageLaunch.arguments(profileEmail: address), tag: tag)
        case .chrome(let directory, let source):
            switch source {
            case .matched:
                chromeLog.info("[chromeResets] account \(tag): opening the usage page in Chrome profile \"\(directory)\" (signed in to this account's address)")
            case .chosenDirectory:
                chromeLog.info("[chromeResets] account \(tag): opening the usage page in Chrome profile \"\(directory)\" (the account's saved choice)")
            case .chosenOwn:
                chromeLog.info("[chromeResets] account \(tag): opening the usage page in this account's own PixelSwitch profile (the account's saved choice)")
            case .owned:
                chromeLog.info("[chromeResets] account \(tag): no Chrome profile is signed in to this address, opening the usage page in this account's own PixelSwitch profile")
            case .ownedSignedInAddressUnusable:
                chromeLog.info("[chromeResets] account \(tag): the account's saved choice is the Chrome profile signed in as its address, but the address is blank or not usable, so Chrome could not be asked for it; opening the usage page in this account's own PixelSwitch profile")
            case .ownedProfileListUnreadable(.accessDenied):
                chromeLog.info("[chromeResets] account \(tag): macOS did not let PixelSwitch read Chrome's profile list (App Data access is denied), so no Chrome profile could be matched; opening the usage page in this account's own PixelSwitch profile")
            case .ownedProfileListUnreadable:
                chromeLog.info("[chromeResets] account \(tag): Chrome's profile list could not be read, so no Chrome profile could be matched; opening the usage page in this account's own PixelSwitch profile")
            }
            launchChrome(arguments: ChromeUsageLaunch.arguments(profileDirectory: directory), tag: tag)
        }
    }

    // MARK: - Side effects

    /// Chrome's `Local State`, the one file read from Chrome's folders. `data` is nil when it
    /// could not be read, and `failure` says why (a missing file, a refusal by macOS, or
    /// anything else), so a refusal is told apart from "no profile matches".
    private static func readLocalState() -> (data: Data?, failure: ChromeLocalState.ReadFailure?) {
        guard let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else { return (nil, .other) }
        let file = support.appendingPathComponent(ChromeLocalState.relativePath)
        do {
            return (try Data(contentsOf: file), nil)
        } catch {
            return (nil, ChromeLocalState.readFailure(from: error))
        }
    }

    private static func isChromeInstalled() -> Bool {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: ChromeUsageLaunch.chromeBundleIdentifier) != nil
    }

    /// `/usr/bin/open` with the tested argument array (`ChromeUsageLaunch.arguments`), for
    /// example `-na "Google Chrome" --args --profile-directory=<dir> <usage page>`.
    /// A launcher that cannot start, or that exits with an error, falls back to the
    /// default browser so the button still opens the page.
    private static func launchChrome(arguments: [String], tag: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: ChromeUsageLaunch.openExecutable)
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { finished in
            guard finished.terminationStatus != 0 else { return }
            chromeLog.warning("[chromeResets] account \(tag): open exited with status \(finished.terminationStatus), opening the usage page in the default browser")
            Task { @MainActor in openInDefaultBrowser(tag: tag) }
        }
        do {
            try process.run()
        } catch {
            chromeLog.warning("[chromeResets] account \(tag): could not start open (\(error.localizedDescription)), opening the usage page in the default browser")
            openInDefaultBrowser(tag: tag)
        }
    }

    private static func openInDefaultBrowser(tag: String) {
        guard let url = URL(string: ChromeUsageLaunch.usageURLString) else { return }
        let opened = NSWorkspace.shared.open(url)
        chromeLog.info("[chromeResets] account \(tag): default browser asked to open the usage page: \(opened)")
    }

    /// The first eight characters of an account id: enough to tell accounts apart in the
    /// log, and never an address.
    private static func shortID(_ accountID: String) -> String {
        String(accountID.trimmingCharacters(in: .whitespacesAndNewlines).prefix(8)).uppercased()
    }
}
