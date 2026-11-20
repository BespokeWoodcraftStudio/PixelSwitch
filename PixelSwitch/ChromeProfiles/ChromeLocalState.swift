import Foundation

/// One entry of Chrome's own profile list.
struct ChromeProfile: Equatable, Sendable {
    /// The profile's folder name inside Chrome's data folder, the value
    /// `--profile-directory=` takes: "Default", "Profile 1".
    let directory: String
    /// The name Chrome shows for the profile ("" when it has none).
    let name: String
    /// The Google account the profile is signed in to (Chrome's `user_name`),
    /// "" when the profile is not signed in to one.
    let email: String
}

/// Reads the profile list out of Chrome's `Local State` file.
///
/// Pure and Foundation only, so the unit harness compiles it. The one file PixelSwitch
/// ever reads in Chrome's folders is `Local State` (`relativePath`), and only its
/// profile list: never cookies, saved passwords or anything else Chrome keeps.
/// This type never touches the disk; the caller hands it the file's bytes.
enum ChromeLocalState {
    /// Where `Local State` sits, under the user's Application Support folder.
    static let relativePath = "Google/Chrome/Local State"

    /// The profiles in `Local State`'s `profile.info_cache`, in a fixed order
    /// (`order`). Tolerant on purpose: nil or empty data, malformed JSON, a missing
    /// or oddly shaped `profile` or `info_cache`, an entry that is not an object, a
    /// field of the wrong type and every key it does not know all give fewer
    /// profiles (or none), never a crash. A profile whose folder name could not be a
    /// folder name is skipped (`isUsableDirectory`).
    static func parseProfiles(_ data: Data?) -> [ChromeProfile] {
        guard let data, !data.isEmpty,
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let profile = root["profile"] as? [String: Any],
              let cache = profile["info_cache"] as? [String: Any] else { return [] }
        var found: [ChromeProfile] = []
        for (directory, value) in cache {
            guard isUsableDirectory(directory), let entry = value as? [String: Any] else { continue }
            let name = (entry["name"] as? String) ?? ""
            let email = ((entry["user_name"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            found.append(ChromeProfile(directory: directory, name: name, email: email))
        }
        return found.sorted { order($0.directory, $1.directory) }
    }

    /// True for a name that can be one folder inside Chrome's data folder. It is passed
    /// to Chrome as one argument (never through a shell), but a name with a slash, a
    /// dot-only name or a control character is not a profile folder, so it is skipped.
    static func isUsableDirectory(_ name: String) -> Bool {
        guard !name.isEmpty, name != ".", name != "..", name.utf8.count <= 255 else { return false }
        return !name.unicodeScalars.contains { $0 == "/" || $0 == "\\" || $0.value < 0x20 || $0.value == 0x7F }
    }

    /// The fixed order profiles are tried in: "Default" first, then "Profile 1",
    /// "Profile 2", ... by number (so "Profile 10" follows "Profile 9"), then any
    /// other name alphabetically. Two profiles signed in to one address therefore
    /// always resolve to the same one.
    static func order(_ a: String, _ b: String) -> Bool {
        let (rankA, numberA) = rank(a)
        let (rankB, numberB) = rank(b)
        if rankA != rankB { return rankA < rankB }
        if numberA != numberB { return numberA < numberB }
        let folded = a.lowercased().compare(b.lowercased())
        if folded != .orderedSame { return folded == .orderedAscending }
        return a < b
    }

    private static func rank(_ directory: String) -> (Int, Int) {
        if directory == "Default" { return (0, 0) }
        let prefix = "Profile "
        if directory.hasPrefix(prefix), let number = Int(directory.dropFirst(prefix.count)), number >= 0 { return (1, number) }
        return (2, 0)
    }
}
