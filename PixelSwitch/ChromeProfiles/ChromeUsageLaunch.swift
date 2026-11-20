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
        /// PixelSwitch's own folder for the account (Chrome makes it on first launch),
        /// because Chrome's profile list was read and none of its profiles is signed in
        /// to the account's address (or Chrome has no profile list yet).
        case owned
        /// PixelSwitch's own folder for the account because Chrome's profile list could
        /// NOT be read (macOS refused, or another error), so no match could be tried.
        /// The folder is the same as `.owned`; the difference is only what gets logged,
        /// so a refusal is never reported as "no profile is signed in".
        case ownedProfileListUnreadable(ChromeLocalState.ReadFailure)
        /// The folder the account's stored choice names (`ChromeProfileChoice.directory`).
        case chosenDirectory
    }

    enum Route: Equatable {
        case chrome(profileDirectory: String, source: Source)
        /// The account's stored choice is "the profile signed in as this address": Chrome
        /// finds the profile itself (`--profile-email`). The address is carried so the
        /// argument array can be made; it is never logged.
        case chromeSignedInAs(email: String)
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

    /// The same launch for the profile signed in with `email`: `--profile-email=<email>`
    /// and no `--profile-directory`. Only for an account whose stored choice asks for it
    /// (`route`), because an address no Chrome profile has opens the profile used last.
    static func arguments(profileEmail email: String) -> [String] {
        ["-na", chromeAppName, "--args", "--profile-email=\(email)", usageURLString]
    }

    /// True for an address that may follow `--profile-email=`: something@something with no
    /// space or control character in it. A blank or damaged address is never sent, because
    /// Chrome would not know what to match.
    static func isUsableProfileEmail(_ email: String) -> Bool {
        let trimmed = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.contains("@"), trimmed.utf8.count <= 320 else { return false }
        return !trimmed.unicodeScalars.contains { $0.properties.isWhitespace || $0.value < 0x20 || $0.value == 0x7F }
    }

    /// Where to open the account's page, in this order. With Chrome missing nothing is read
    /// or saved. Otherwise:
    /// 1. A profile Chrome's own list (`localState`) shows signed in to the address wins,
    ///    always, even over a stored choice or a saved PixelSwitch folder (neither is even
    ///    looked at, and both are left as they are). Only possible when the list could be read.
    /// 2. The account's stored choice (`ChromeProfileChoiceStore`): a named folder, or the
    ///    profile signed in as the address (only with a usable address, else it falls
    ///    through to 3).
    /// 3. The account's own PixelSwitch folder (made and saved on first use), which is also
    ///    what an account with no stored choice gets.
    /// `--profile-email` is therefore sent only for the one stored choice that asks for it.
    /// `localStateFailure` is why `localState` is nil when the read failed (nil when it did
    /// not fail): it only changes which `Source` the own folder reports, never the folder.
    static func route(accountID: String, email: String, localState: Data?, chromeInstalled: Bool,
                      localStateFailure: ChromeLocalState.ReadFailure? = nil,
                      defaults: UserDefaults = .standard) -> Route {
        guard chromeInstalled else { return .defaultBrowser }
        let profiles = ChromeLocalState.parseProfiles(localState)
        if let directory = ChromeProfileMatcher.directory(forEmail: email, in: profiles) {
            return .chrome(profileDirectory: directory, source: .matched)
        }
        switch ChromeProfileChoiceStore.choice(forAccountID: accountID, defaults: defaults) {
        case .directory(let name):
            return .chrome(profileDirectory: name, source: .chosenDirectory)
        case .chromeProfileSignedInAs:
            if isUsableProfileEmail(email) {
                return .chromeSignedInAs(email: email.trimmingCharacters(in: .whitespacesAndNewlines))
            }
        case .ownProfile:
            break
        }
        let owned = PixelSwitchChromeProfile.directory(forAccountID: accountID, email: email, defaults: defaults)
        if localState == nil, let failure = localStateFailure, failure != .notFound {
            return .chrome(profileDirectory: owned, source: .ownedProfileListUnreadable(failure))
        }
        return .chrome(profileDirectory: owned, source: .owned)
    }
}
