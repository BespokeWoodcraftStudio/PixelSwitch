import Foundation

/// Which Chrome profile belongs to which PixelSwitch account.
///
/// Pure and Foundation only, so the unit harness compiles it. Three steps, in this order:
/// 1. The account's stored choice (`ChromeProfileChoice`, set in Settings or from a shell)
///    when it has one: an explicit choice, even "its own profile", is always honoured.
/// 2. `directory(forEmail:in:)` looks for a profile Chrome already has that is signed
///    in to the account's Google address (only possible while Chrome's `Local State`
///    can be read, which macOS can refuse).
/// 3. When there is none, `PixelSwitchChromeProfile` gives the account a profile
///    folder of PixelSwitch's own, named from its address and saved so it never changes.
///    Chrome creates that profile the first time it is launched with the name.
enum ChromeProfileMatcher {
    /// An address as compared here: trimmed, lower case.
    static func normalized(_ email: String) -> String {
        email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// The folder of the profile whose Google account equals `email` (case and stray
    /// spaces ignored, the whole address and nothing less), or nil. A blank address
    /// matches nothing, not even a profile that is signed in to no account. When two
    /// profiles share the address, the first in `ChromeLocalState.order` wins.
    static func directory(forEmail email: String, in profiles: [ChromeProfile]) -> String? {
        let wanted = normalized(email)
        guard !wanted.isEmpty else { return nil }
        return profiles
            .sorted { ChromeLocalState.order($0.directory, $1.directory) }
            .first { normalized($0.email) == wanted }?
            .directory
    }
}

/// The Chrome profile folder PixelSwitch makes for an account that has no profile of
/// its own yet, and where that choice is remembered.
enum PixelSwitchChromeProfile {
    /// Every PixelSwitch-made folder starts with this, so they are easy to tell apart
    /// from the founder's own profiles in Chrome's folder.
    static let prefix = "PixelSwitch "

    /// Account id to folder name, in UserDefaults. Namespaced like the app's other keys.
    static let defaultsKey = "ai.pixelventures.pixelswitch.chromeProfiles.ownedDirectories"

    /// Longest part of the address kept in a name. Folder names stop at 255 bytes, and
    /// real addresses are far shorter; a longer one is cut and given a hash (below).
    static let maxEmailCharacters = 100

    /// The folder name for an address: "PixelSwitch " and the address, lower case, with
    /// each character outside letters, digits and . _ - @ + replaced by an underscore.
    /// The same address (any case, stray spaces) always gives the same name. When
    /// the cleaning changed or cut the address, a short hash of the original follows, so
    /// two different addresses cannot land on one folder. A blank address uses the
    /// account id instead, so the name is never empty.
    static func directoryName(forEmail email: String, accountID: String) -> String {
        var source = ChromeProfileMatcher.normalized(email)
        if source.isEmpty { source = ChromeProfileMatcher.normalized(accountID) }
        let (clean, changed) = sanitize(source)
        guard changed else { return prefix + clean }
        return prefix + clean + "-" + shortHash(source)
    }

    /// `text` with every character outside letters, digits and . _ - @ + replaced by an
    /// underscore and cut to `maxEmailCharacters`. `changed` is true when anything was
    /// replaced or cut. Only ASCII letters and digits are kept, so the result is the
    /// same on every file system Chrome runs on.
    static func sanitize(_ text: String) -> (clean: String, changed: Bool) {
        var changed = false
        var out = ""
        for scalar in text.unicodeScalars {
            if out.count >= maxEmailCharacters { changed = true; break }
            switch scalar {
            case "a"..."z", "A"..."Z", "0"..."9", ".", "_", "-", "@", "+":
                out.unicodeScalars.append(scalar)
            default:
                out += "_"
                changed = true
            }
        }
        return (out, changed)
    }

    /// Eight hex digits of an FNV-1a hash of the text's UTF-8 bytes: stable on every
    /// launch and machine (Swift's own hashing is not), and only a tie-breaker.
    static func shortHash(_ text: String) -> String {
        var hash: UInt32 = 2_166_136_261
        for byte in text.utf8 {
            hash ^= UInt32(byte)
            hash = hash &* 16_777_619
        }
        return String(format: "%08x", hash)
    }

    /// An account id as stored: UUID text compares without regard to case.
    static func normalizedAccountID(_ accountID: String) -> String {
        accountID.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// The saved folder for this account, or nil when it has none yet. A saved value
    /// that is not a usable folder name counts as none.
    static func savedDirectory(forAccountID accountID: String, defaults: UserDefaults = .standard) -> String? {
        let saved = defaults.dictionary(forKey: defaultsKey) as? [String: String]
        guard let value = saved?[normalizedAccountID(accountID)], value.hasPrefix(prefix),
              ChromeLocalState.isUsableDirectory(value) else { return nil }
        return value
    }

    /// The account's PixelSwitch-made folder: the saved one, or one made from its
    /// address and saved first. Once saved it never changes, even if the account's
    /// address does, because the profile Chrome keeps under it holds the sign-ins.
    /// Call it from the main thread: a read then a write is not atomic.
    static func directory(forAccountID accountID: String, email: String, defaults: UserDefaults = .standard) -> String {
        if let saved = savedDirectory(forAccountID: accountID, defaults: defaults) { return saved }
        let made = directoryName(forEmail: email, accountID: accountID)
        var all = defaults.dictionary(forKey: defaultsKey) as? [String: String] ?? [:]
        all[normalizedAccountID(accountID)] = made
        defaults.set(all, forKey: defaultsKey)
        return made
    }
}

/// Which Chrome profile one account's Limit resets button opens when PixelSwitch cannot
/// find the profile for itself (it cannot read Chrome's profile list while macOS refuses
/// that read), chosen per account in Settings or set from a shell.
///
/// Tested live on 2026-11-20 (`docs/investigations/2026-11-20-chrome-profile-email-test.md`):
/// `--profile-directory` always wins and Chrome makes the folder when it is missing;
/// `--profile-email` opens the profile signed in with that address (any case), but an
/// address no profile has silently opens the profile used last, which can be a
/// different account. So the address flag is only ever sent when the choice says so.
enum ChromeProfileChoice: Equatable, Hashable, Sendable {
    /// PixelSwitch's own folder for the account (`PixelSwitchChromeProfile`), opened with
    /// `--profile-directory`. Chrome makes it on first use; it is signed out until the
    /// person signs in to claude.ai there once. Kept as a choice, it is honoured even when
    /// Chrome's profile list shows a profile signed in to the address. What an account with
    /// no kept choice gets is the same folder unless that list does show such a profile.
    case ownProfile
    /// Chrome's profile that is signed in with the account's address, found by Chrome
    /// itself (`--profile-email=<address>` and nothing else). Only right when such a
    /// profile exists; otherwise Chrome opens whichever profile was used last.
    case chromeProfileSignedInAs
    /// One existing Chrome profile folder by name ("Default", "Profile 1"), opened with
    /// `--profile-directory`. The name passes `isValidDirectory`.
    case directory(String)

    /// True for a folder name that may be put after `--profile-directory=`: one folder
    /// name (`ChromeLocalState.isUsableDirectory`: not empty, not "." or "..", no slash,
    /// backslash or control character, 255 bytes at most) that also has no ".." in it,
    /// does not start with a dash and has no space at either end.
    static func isValidDirectory(_ name: String) -> Bool {
        guard ChromeLocalState.isUsableDirectory(name), !name.hasPrefix("-"), !name.contains("..") else { return false }
        return name == name.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Where each account's `ChromeProfileChoice` is kept, and the one text format it is
/// kept in, which is also what a shell can write.
///
/// A dictionary in UserDefaults under `defaultsKey`: the account id (lower case UUID text)
/// to one of
///   `own`                   the account's own PixelSwitch profile, even if Chrome's list shows a
///                           profile signed in to the address
///   `signedInAs`            the Chrome profile signed in with the account's address
///   `directory:<name>`      the Chrome profile folder `<name>`, for example `directory:Profile 1`
/// No entry at all means no choice ("Automatic" in Settings): a profile Chrome's list shows for
/// the address if the list can be read, else the account's own PixelSwitch profile.
/// The keywords ignore case. A value that is none of these, or a `directory:` name that
/// is not a valid folder name, counts as no entry. From a shell, for one account:
///   defaults write ai.pixelventures.pixelswitch ai.pixelventures.pixelswitch.chromeProfiles.choices \
///       -dict-add <account-id-lower-case> 'directory:Profile 1'
/// The account-id keys must be lower case (an account's id is shown upper case in most tools):
/// an upper case key is never found.
/// Only String values are read. An entry of any other type is ignored (and dropped the next time a
/// choice is kept), and never hides or overwrites the other accounts' choices.
enum ChromeProfileChoiceStore {
    /// Account id to choice text, in UserDefaults. Namespaced like the app's other keys.
    static let defaultsKey = "ai.pixelventures.pixelswitch.chromeProfiles.choices"

    static let ownValue = "own"
    static let signedInAsValue = "signedInAs"
    static let directoryPrefix = "directory:"

    /// The text kept for a choice, or nil for a `.directory` whose name is not valid.
    static func text(for choice: ChromeProfileChoice) -> String? {
        switch choice {
        case .ownProfile: return ownValue
        case .chromeProfileSignedInAs: return signedInAsValue
        case .directory(let name): return ChromeProfileChoice.isValidDirectory(name) ? directoryPrefix + name : nil
        }
    }

    /// The choice a kept text stands for, or nil when it is none of the three forms.
    static func choice(fromText text: String) -> ChromeProfileChoice? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        switch trimmed.lowercased() {
        case ownValue: return .ownProfile
        case signedInAsValue.lowercased(): return .chromeProfileSignedInAs
        default: break
        }
        guard trimmed.lowercased().hasPrefix(directoryPrefix) else { return nil }
        let name = String(trimmed.dropFirst(directoryPrefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
        return ChromeProfileChoice.isValidDirectory(name) ? .directory(name) : nil
    }

    /// The choice kept for the account, or nil when it has none (or a damaged one).
    static func saved(forAccountID accountID: String, defaults: UserDefaults = .standard) -> ChromeProfileChoice? {
        guard let text = storedTexts(defaults: defaults)[PixelSwitchChromeProfile.normalizedAccountID(accountID)] else { return nil }
        return choice(fromText: text)
    }

    /// Every kept text by account id, from the dictionary read as `[String: Any]` with only its String
    /// values kept. A single entry of another type (a hand-made `defaults write` with a number, say)
    /// is skipped by itself, so it can never make every account look as if it had no choice, and it
    /// is never the reason the other accounts' entries are lost on the next write.
    static func storedTexts(defaults: UserDefaults = .standard) -> [String: String] {
        var texts: [String: String] = [:]
        for (key, value) in defaults.dictionary(forKey: defaultsKey) ?? [:] {
            if let text = value as? String { texts[key] = text }
        }
        return texts
    }

    /// The account's choice as a menu shows it: the kept one, else its own PixelSwitch profile.
    /// (Whether a choice was kept is `saved(forAccountID:)`, which the click uses.)
    static func choice(forAccountID accountID: String, defaults: UserDefaults = .standard) -> ChromeProfileChoice {
        saved(forAccountID: accountID, defaults: defaults) ?? .ownProfile
    }

    /// Keeps a choice for the account, `.ownProfile` included (an explicit own profile is a
    /// choice, kept as `own`). Returns false, and changes nothing, for a `.directory` with an
    /// invalid name. Call it from the main thread: a read then a write is not atomic.
    @discardableResult
    static func set(_ choice: ChromeProfileChoice, forAccountID accountID: String, defaults: UserDefaults = .standard) -> Bool {
        guard let text = text(for: choice) else { return false }
        var all = storedTexts(defaults: defaults)
        all[PixelSwitchChromeProfile.normalizedAccountID(accountID)] = text
        defaults.set(all, forKey: defaultsKey)
        return true
    }

    /// What the Settings menu does with a pick: keeps the choice, or forgets the kept one when it is nil
    /// ("Automatic"). Picking the account's own PixelSwitch profile is a choice, so it is kept (as `own`);
    /// only Automatic leaves the account with none. Returns false, and changes nothing, for a `.directory`
    /// with an invalid name.
    @discardableResult
    static func apply(_ choice: ChromeProfileChoice?, forAccountID accountID: String, defaults: UserDefaults = .standard) -> Bool {
        guard let choice else {
            clear(forAccountID: accountID, defaults: defaults)
            return true
        }
        return set(choice, forAccountID: accountID, defaults: defaults)
    }

    /// Forgets the account's kept choice, so it has none again.
    static func clear(forAccountID accountID: String, defaults: UserDefaults = .standard) {
        var all = storedTexts(defaults: defaults)
        all.removeValue(forKey: PixelSwitchChromeProfile.normalizedAccountID(accountID))
        if all.isEmpty { defaults.removeObject(forKey: defaultsKey) } else { defaults.set(all, forKey: defaultsKey) }
    }
}
