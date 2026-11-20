import Foundation

/// What the Limit resets button does, decided without touching anything: which Chrome
/// profile to open claude.ai's usage page in, and the exact command line for it.
///
/// Pure and Foundation only, so the unit harness compiles it and the command line is
/// tested without launching Chrome. `ChromeUsagePage` does the opening.
///
/// The founder, 2026-11-20: PixelSwitch only OPENS the page in the Chrome profile that
/// belongs to the account, where he is already signed in to claude.ai, and he clicks
/// claude.ai's own Reset button himself. Nothing is clicked, submitted or called here.
enum ChromeUsageLaunch {
    /// The page every account's button opens.
    static let usageURLString = "https://claude.ai/settings/usage"

    /// The macOS launcher, always by its full path.
    static let openExecutable = "/usr/bin/open"

    /// Chrome's bundle identifier, used to see whether it is installed.
    static let chromeBundleIdentifier = "com.google.Chrome"

    /// The app name `open -a` takes.
    static let chromeAppName = "Google Chrome"

    /// Where the profile for the launch came from.
    enum Source: Equatable {
        /// Chrome already had a profile signed in to the account's address.
        case matched
        /// PixelSwitch's own folder for the account (Chrome makes it on first launch).
        case owned
    }

    enum Route: Equatable {
        case chrome(profileDirectory: String, source: Source)
        /// Chrome is not installed: the system's default browser opens the page.
        case defaultBrowser
    }

    /// The exact arguments to `/usr/bin/open`, one array element each (no shell is
    /// involved, so a folder name with a space stays one argument). `-n` starts Chrome's
    /// own launch even when it is already running, and Chrome hands the page to the
    /// running copy in the named profile.
    static func arguments(profileDirectory: String) -> [String] {
        ["-na", chromeAppName, "--args", "--profile-directory=\(profileDirectory)", usageURLString]
    }

    /// Where to open the account's page. With Chrome missing nothing is read or saved.
    /// Otherwise the profile Chrome already has for the address wins; failing that, the
    /// account's own PixelSwitch folder (made and saved on first use).
    static func route(accountID: String, email: String, localState: Data?, chromeInstalled: Bool,
                      defaults: UserDefaults = .standard) -> Route {
        guard chromeInstalled else { return .defaultBrowser }
        let profiles = ChromeLocalState.parseProfiles(localState)
        if let directory = ChromeProfileMatcher.directory(forEmail: email, in: profiles) {
            return .chrome(profileDirectory: directory, source: .matched)
        }
        let owned = PixelSwitchChromeProfile.directory(forAccountID: accountID, email: email, defaults: defaults)
        return .chrome(profileDirectory: owned, source: .owned)
    }
}
