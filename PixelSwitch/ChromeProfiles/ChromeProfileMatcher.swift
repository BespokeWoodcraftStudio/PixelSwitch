import Foundation

/// Which Chrome profile belongs to which PixelSwitch account.
///
/// Pure and Foundation only, so the unit harness compiles it. Two steps:
/// 1. `directory(forEmail:in:)` looks for a profile Chrome already has that is signed
///    in to the account's Google address.
/// 2. When there is none, `PixelSwitchChromeProfile` gives the account a profile
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
