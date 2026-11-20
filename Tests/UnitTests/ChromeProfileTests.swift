// The Limit resets button's Chrome service (PixelSwitch/ChromeProfiles): Chrome's profile
// list read from Local State, matching an account to a profile, PixelSwitch's own profile
// folder for an account with none, and the exact command line that opens claude.ai's usage
// page. Also the cleanup of the web data the 2.2 in-app window left behind, and the pins that
// keep the service from reading anything else in Chrome's folders or acting on a page.
//
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from main.swift. Uses
// the global `check(_:_:_:)` defined there. Chrome is never launched here: the argument array
// and the decisions are tested, not the launch. Every address in this file is a made-up one
// on example.com, example.org or example.net.
//
// The founder, 2026-11-20 (round 2, option A): the Limit resets button opens the account's
// claude.ai usage page in the Chrome profile that belongs to it; he clicks Reset himself.
import Foundation

@MainActor func runChromeProfileTests() {
    chromeLocalStateTests()
    chromeProfileMatcherTests()
    chromeOwnedProfileTests()
    chromeLaunchTests()
    chromeLegacyCleanupTests()
    chromeSourcePins()
    chromeStringTests()
}

private func chromeJSON(_ text: String) -> Data { Data(text.utf8) }

/// A Local State shaped like a real one with three profiles (field names as Chrome writes
/// them, with fields PixelSwitch never reads left in to prove they are ignored).
private let sampleLocalState = """
{
  "browser": {"enabled_labs_experiments": ["a@1"]},
  "profile": {
    "last_used": "Default",
    "info_cache": {
      "Profile 2": {"name": "gamma@example.net", "user_name": "gamma@example.net",
                    "gaia_id": "3", "avatar_icon": "chrome://theme/IDR_PROFILE_AVATAR_5", "is_ephemeral": false},
      "Default": {"name": "Your Chrome", "user_name": "alpha@example.com", "gaia_id": "1", "active_time": 1.7e9},
      "Profile 1": {"name": "beta@example.org", "user_name": "beta@example.org", "gaia_id": "2", "shortcut_name": "x"}
    },
    "profiles_order": ["Default", "Profile 1", "Profile 2"]
  }
}
"""

// MARK: - Local State parsing

@MainActor private func chromeLocalStateTests() {
    typealias L = ChromeLocalState
    let sample = L.parseProfiles(chromeJSON(sampleLocalState))
    check(sample.map(\.directory) == ["Default", "Profile 1", "Profile 2"]
          && sample.map(\.email) == ["alpha@example.com", "beta@example.org", "gamma@example.net"],
          "chrome local state: three profiles come out in a fixed order with their Google addresses", "\(sample)")
    check(sample.first?.name == "Your Chrome" && sample.first?.directory == "Default",
          "chrome local state: a profile's shown name is read too")
    check(L.relativePath == "Google/Chrome/Local State",
          "chrome local state: the one file read is Google/Chrome/Local State under Application Support")

    // Tolerant: every bad shape gives no profiles, never a crash.
    check(L.parseProfiles(nil).isEmpty && L.parseProfiles(Data()).isEmpty, "chrome local state: no data, or empty data, gives no profiles")
    for (label, text) in [("not JSON", "this is not json"), ("truncated JSON", "{\"profile\": {\"info_cache\": {\"Default\": {\"user_name\": \"a@b.c"),
                          ("top level is a list", "[1, 2, 3]"), ("top level is a string", "\"hello\""), ("empty object", "{}"),
                          ("no info_cache", "{\"profile\": {}}"), ("profile is a list", "{\"profile\": []}"),
                          ("info_cache is a list", "{\"profile\": {\"info_cache\": []}}"),
                          ("info_cache is a string", "{\"profile\": {\"info_cache\": \"x\"}}"), ("info_cache is empty", "{\"profile\": {\"info_cache\": {}}}")] {
        check(L.parseProfiles(chromeJSON(text)).isEmpty, "chrome local state: \(label) gives no profiles")
    }
    check(L.parseProfiles(Data([0xFF, 0xFE, 0x00, 0x01, 0x80])).isEmpty, "chrome local state: bytes that are not text give no profiles")

    // Missing and odd fields: the profile stays, the field is empty.
    let odd = L.parseProfiles(chromeJSON("""
    {"profile": {"info_cache": {
      "Default": {},
      "Profile 1": {"name": 7, "user_name": ["a@b.c"]},
      "Profile 2": {"name": "Two"},
      "Profile 3": {"user_name": "  Three@Example.com \\n"},
      "Profile 4": "not an object",
      "Profile 5": null,
      "Profile 6": 12,
      "Profile 7": {"user_name": null, "something_new": {"deep": [1, 2]}}
    }}}
    """))
    check(odd.map(\.directory) == ["Default", "Profile 1", "Profile 2", "Profile 3", "Profile 7"],
          "chrome local state: entries that are not objects are skipped, the rest kept", "\(odd.map(\.directory))")
    check(odd.map(\.email) == ["", "", "", "Three@Example.com", ""] && odd.map(\.name) == ["", "", "Two", "", ""],
          "chrome local state: a missing, null or wrongly typed field is empty; the address is trimmed", "\(odd)")

    // Folder names that cannot be one folder are skipped.
    let bad = L.parseProfiles(chromeJSON("""
    {"profile": {"info_cache": {"": {"user_name": "e@x.com"}, "..": {"user_name": "d@x.com"}, ".": {"user_name": "c@x.com"},
      "a/b": {"user_name": "s@x.com"}, "../Profile 1": {"user_name": "t@x.com"}, "back\\\\slash": {"user_name": "b@x.com"},
      "Profile 1": {"user_name": "ok@x.com"}}}}
    """))
    check(bad.map(\.directory) == ["Profile 1"], "chrome local state: empty, dot, slash and backslash folder names are skipped", "\(bad.map(\.directory))")
    check(L.isUsableDirectory("Default") && L.isUsableDirectory("Profile 12") && L.isUsableDirectory("PixelSwitch a@b.com")
          && !L.isUsableDirectory("") && !L.isUsableDirectory("a\nb") && !L.isUsableDirectory(String(repeating: "x", count: 256)),
          "chrome local state: a usable folder name is non-empty, one component, no control characters, 255 bytes at most")

    // Order: Default, then Profile N by number, then the rest by name.
    let shuffled = ["Profile 10", "Zeta", "Profile 2", "alpha", "Default", "Profile 1", "PixelSwitch x@y.com", "Profile 9"]
    check(shuffled.sorted(by: L.order) == ["Default", "Profile 1", "Profile 2", "Profile 9", "Profile 10", "alpha", "PixelSwitch x@y.com", "Zeta"],
          "chrome local state: order is Default, Profile 1, 2, 9, 10 by number, then other names alphabetically", "\(shuffled.sorted(by: L.order))")
    // Parses of one file always agree (a dictionary has no order of its own).
    let again = (0..<20).map { _ in L.parseProfiles(chromeJSON(sampleLocalState)).map(\.directory) }
    check(again.allSatisfy { $0 == ["Default", "Profile 1", "Profile 2"] }, "chrome local state: twenty parses of one file always give the same order")
}

// MARK: - Matching an account to a profile

@MainActor private func chromeProfileMatcherTests() {
    typealias M = ChromeProfileMatcher
    let profiles = ChromeLocalState.parseProfiles(chromeJSON(sampleLocalState))

    check(M.directory(forEmail: "alpha@example.com", in: profiles) == "Default"
          && M.directory(forEmail: "beta@example.org", in: profiles) == "Profile 1"
          && M.directory(forEmail: "gamma@example.net", in: profiles) == "Profile 2",
          "chrome match: three addresses find Default, Profile 1 and Profile 2")
    check(M.directory(forEmail: "  ALPHA@Example.COM \n", in: profiles) == "Default"
          && M.directory(forEmail: "Beta@Example.ORG", in: profiles) == "Profile 1",
          "chrome match: case and stray spaces around the address do not matter")
    let spaced = [ChromeProfile(directory: "Profile 4", name: "", email: " Me@Example.com ")]
    check(M.directory(forEmail: "me@example.com", in: spaced) == "Profile 4",
          "chrome match: the profile's own address is compared trimmed and lower case too")

    check(M.directory(forEmail: "someone@else.com", in: profiles) == nil, "chrome match: an address no profile has finds nothing")
    check(M.directory(forEmail: "beta@example.or", in: profiles) == nil && M.directory(forEmail: "eta@example.org", in: profiles) == nil
          && M.directory(forEmail: "x.beta@example.org", in: profiles) == nil && M.directory(forEmail: "beta", in: profiles) == nil
          && M.directory(forEmail: "example.org", in: profiles) == nil,
          "chrome match: only the whole address matches, never a part of one")
    let signedOut = [ChromeProfile(directory: "Default", name: "", email: ""), ChromeProfile(directory: "Profile 1", name: "", email: "  ")]
    check(M.directory(forEmail: "", in: signedOut) == nil && M.directory(forEmail: "   ", in: signedOut) == nil && M.directory(forEmail: "", in: profiles) == nil,
          "chrome match: a blank address matches nothing, not even a profile that is signed in to no account")
    check(M.directory(forEmail: "a@b.com", in: []) == nil, "chrome match: with no profiles at all nothing matches")

    let twins = [ChromeProfile(directory: "Profile 3", name: "", email: "twin@x.com"), ChromeProfile(directory: "Profile 1", name: "", email: "TWIN@x.com"),
                 ChromeProfile(directory: "Profile 2", name: "", email: "twin@x.com")]
    check(M.directory(forEmail: "twin@x.com", in: twins) == "Profile 1" && M.directory(forEmail: "twin@x.com", in: twins.reversed()) == "Profile 1",
          "chrome match: two profiles on one address resolve to the first in the fixed order, whatever order they come in")
    let gmailVariants = [ChromeProfile(directory: "Default", name: "", email: "a.b@gmail.com")]
    check(M.directory(forEmail: "ab@gmail.com", in: gmailVariants) == nil && M.directory(forEmail: "a.b+x@gmail.com", in: gmailVariants) == nil,
          "chrome match: Gmail's dot and plus aliases are not guessed at; the address must be the same")
}

// MARK: - PixelSwitch's own profile folder

@MainActor private func chromeOwnedProfileTests() {
    typealias P = PixelSwitchChromeProfile
    let id = "11111111-AAAA-4AAA-8AAA-111111111111"

    check(P.directoryName(forEmail: "person@example.com", accountID: id) == "PixelSwitch person@example.com",
          "chrome owned: a plain address makes PixelSwitch, a space, the address", P.directoryName(forEmail: "person@example.com", accountID: id))
    check(P.directoryName(forEmail: "  Person@Example.COM \n", accountID: id) == P.directoryName(forEmail: "person@example.com", accountID: id),
          "chrome owned: case and stray spaces give the same name")
    check(P.directoryName(forEmail: "a.b_c-d+e@sub.example.co.uk", accountID: id) == "PixelSwitch a.b_c-d+e@sub.example.co.uk",
          "chrome owned: letters, digits and . _ - @ + are kept")
    check(P.directoryName(forEmail: "x@example.com", accountID: id) == P.directoryName(forEmail: "x@example.com", accountID: "other"),
          "chrome owned: the name comes from the address, not the account id")

    // Sanitizing: nothing Chrome or the file system could trip on survives, and different addresses stay different.
    let nasty = ["a/b@x.com", "a\\b@x.com", "a b@x.com", "a:b@x.com", "a*b?@x.com", "é@x.com", "名前@x.com", "a\u{0}b@x.com", "a\nb@x.com", "<a>@x.com",
                 "a'b\"c@x.com", "a;b@x.com", "a$(b)@x.com", "a`b`@x.com", "a|b@x.com", "a&b@x.com", "..@x.com", "-rf@x.com", "😀@x.com"]
    let names = nasty.map { P.directoryName(forEmail: $0, accountID: id) }
    let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-@+")
    check(names.allSatisfy { $0.hasPrefix("PixelSwitch ") && $0.allSatisfy { allowed.contains($0) } && ChromeLocalState.isUsableDirectory($0) },
          "chrome owned: every name is PixelSwitch and a space, then only letters, digits and . _ - @ +", names.joined(separator: " | "))
    check(Set(names).count == nasty.count, "chrome owned: nineteen different awkward addresses give nineteen different names", "\(Set(names).count)")
    check(P.directoryName(forEmail: "a/b@x.com", accountID: id) != P.directoryName(forEmail: "a_b@x.com", accountID: id),
          "chrome owned: an address that had to be cleaned never collides with the clean address it resembles")
    check(P.directoryName(forEmail: "a/b@x.com", accountID: id) == P.directoryName(forEmail: "A/B@X.COM ", accountID: id),
          "chrome owned: a cleaned name is still the same on every call")
    let long = String(repeating: "x", count: 300) + "@example.com"
    let longName = P.directoryName(forEmail: long, accountID: id)
    check(longName.utf8.count <= 255 && longName.hasPrefix("PixelSwitch ") && ChromeLocalState.isUsableDirectory(longName)
          && longName != P.directoryName(forEmail: String(repeating: "x", count: 300) + "@example.org", accountID: id),
          "chrome owned: an absurdly long address is cut to a usable name and still differs from a neighbour", "\(longName.utf8.count) bytes")
    check(P.shortHash("abc") == P.shortHash("abc") && P.shortHash("abc") != P.shortHash("abd") && P.shortHash("abc").count == 8
          && P.shortHash("") == "811c9dc5",
          "chrome owned: the tie-break hash is a fixed eight hex digits (FNV-1a, not Swift's per-launch hashing)")
    let blank = P.directoryName(forEmail: "  ", accountID: "AAAA-BBBB")
    check(blank == "PixelSwitch aaaa-bbbb", "chrome owned: with no address the account id names the folder, so a name is never empty", blank)

    // Remembered per account in UserDefaults, and never changing.
    let suite = "ai.pixelventures.pixelswitch.tests.chromeprofiles.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "chrome owned: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }
    let other = "22222222-BBBB-4BBB-8BBB-222222222222"

    check(P.savedDirectory(forAccountID: id, defaults: defaults) == nil, "chrome owned: an account with no folder yet has none saved")
    let first = P.directory(forAccountID: id, email: "me@example.com", defaults: defaults)
    check(first == "PixelSwitch me@example.com" && P.savedDirectory(forAccountID: id, defaults: defaults) == first,
          "chrome owned: the first use makes the folder name and saves it", first)
    check(P.directory(forAccountID: id, email: "changed@example.com", defaults: defaults) == first
          && P.directory(forAccountID: id.lowercased(), email: "", defaults: defaults) == first
          && P.directory(forAccountID: " \(id)\n", email: "x@y.z", defaults: defaults) == first,
          "chrome owned: once saved it never changes, even if the account's address does, and any spelling of the id finds it")
    if let relaunched = UserDefaults(suiteName: suite) {
        check(P.directory(forAccountID: id, email: "new@example.com", defaults: relaunched) == first,
              "chrome owned: it survives a relaunch (read back from saved defaults)")
    } else {
        check(false, "chrome owned: reopen the suite")
    }
    let second = P.directory(forAccountID: other, email: "you@example.com", defaults: defaults)
    check(second == "PixelSwitch you@example.com" && second != first && P.savedDirectory(forAccountID: id, defaults: defaults) == first,
          "chrome owned: a second account gets its own folder and the first is left alone")
    check(P.defaultsKey.hasPrefix("ai.pixelventures.pixelswitch."), "chrome owned: the defaults key is PixelSwitch-namespaced", P.defaultsKey)
    let raw = defaults.dictionary(forKey: P.defaultsKey) as? [String: String] ?? [:]
    check(raw.count == 2 && raw.keys.allSatisfy { $0 == $0.lowercased() } && raw.values.allSatisfy { $0.hasPrefix("PixelSwitch ") },
          "chrome owned: what is saved is account id (lower case) to folder name", "\(raw)")

    // A damaged entry is replaced, not trusted.
    for damaged in ["", "Profile 1", "../PixelSwitch evil", "PixelSwitch a/b", "Default"] {
        defaults.set([P.normalizedAccountID("damaged"): damaged], forKey: P.defaultsKey)
        let fixed = P.directory(forAccountID: "damaged", email: "fix@example.com", defaults: defaults)
        check(fixed == "PixelSwitch fix@example.com" && P.savedDirectory(forAccountID: "damaged", defaults: defaults) == fixed,
              "chrome owned: a saved value of \(damaged.debugDescription) is replaced with a valid PixelSwitch folder")
    }
    defaults.set("not a dictionary", forKey: P.defaultsKey)
    check(P.directory(forAccountID: id, email: "me@example.com", defaults: defaults) == "PixelSwitch me@example.com",
          "chrome owned: a saved value of the wrong type is replaced")
}

// MARK: - The launch

@MainActor private func chromeLaunchTests() {
    typealias C = ChromeUsageLaunch
    check(C.usageURLString == "https://claude.ai/settings/usage" && URL(string: C.usageURLString)?.host == "claude.ai",
          "chrome launch: the page is https://claude.ai/settings/usage")
    check(C.openExecutable == "/usr/bin/open" && C.chromeAppName == "Google Chrome" && C.chromeBundleIdentifier == "com.google.Chrome",
          "chrome launch: /usr/bin/open by full path, the app Google Chrome, bundle id com.google.Chrome")
    check(C.arguments(profileDirectory: "Profile 1") == ["-na", "Google Chrome", "--args", "--profile-directory=Profile 1", "https://claude.ai/settings/usage"],
          "chrome launch: the exact argument array for a profile whose name has a space", "\(C.arguments(profileDirectory: "Profile 1"))")
    check(C.arguments(profileDirectory: "Default") == ["-na", "Google Chrome", "--args", "--profile-directory=Default", "https://claude.ai/settings/usage"],
          "chrome launch: the exact argument array for the Default profile")
    check(C.arguments(profileDirectory: "PixelSwitch me@example.com").count == 5
          && C.arguments(profileDirectory: "PixelSwitch me@example.com")[3] == "--profile-directory=PixelSwitch me@example.com",
          "chrome launch: a folder name with spaces and an @ stays one argument, with no quoting added")
    let hostile = C.arguments(profileDirectory: "x; rm -rf ~ $(whoami) `id` && \"'")
    check(hostile.count == 5 && hostile[3] == "--profile-directory=x; rm -rf ~ $(whoami) `id` && \"'" && hostile[4] == "https://claude.ai/settings/usage",
          "chrome launch: shell characters in a name are passed as plain text in one argument (an array, never a shell line)")

    let suite = "ai.pixelventures.pixelswitch.tests.chromelaunch.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "chrome launch: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }
    let state = chromeJSON(sampleLocalState)
    let id = "33333333-CCCC-4CCC-8CCC-333333333333"

    check(C.route(accountID: id, email: "beta@example.org", localState: state, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome route: an address Chrome has a profile for opens in that profile")
    check(C.route(accountID: id, email: "ALPHA@example.com ", localState: state, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "Default", source: .matched),
          "chrome route: the match ignores case and stray spaces (Default for the first account)")
    check(defaults.dictionary(forKey: PixelSwitchChromeProfile.defaultsKey) == nil,
          "chrome route: a matched profile saves nothing")
    check(C.route(accountID: id, email: "new@example.com", localState: state, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch new@example.com", source: .owned),
          "chrome route: an address with no profile gets the account's own PixelSwitch profile")
    check(PixelSwitchChromeProfile.savedDirectory(forAccountID: id, defaults: defaults) == "PixelSwitch new@example.com",
          "chrome route: that profile name is saved against the account id")
    check(C.route(accountID: id, email: "renamed@example.com", localState: state, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch new@example.com", source: .owned),
          "chrome route: the next time the same account gets the same profile, even with a different address")
    for (label, data) in [("no Local State file", nil), ("an empty file", Data()), ("a damaged file", chromeJSON("{{{"))] as [(String, Data?)] {
        check(C.route(accountID: "plain-\(label.count)", email: "u@example.com", localState: data, chromeInstalled: true, defaults: defaults)
              == .chrome(profileDirectory: "PixelSwitch u@example.com", source: .owned),
              "chrome route: with \(label) the account gets its own PixelSwitch profile")
    }
    let before = defaults.dictionary(forKey: PixelSwitchChromeProfile.defaultsKey) as? [String: String] ?? [:]
    check(C.route(accountID: "no-chrome", email: "beta@example.org", localState: state, chromeInstalled: false, defaults: defaults) == .defaultBrowser
          && C.route(accountID: "no-chrome", email: "nobody@x.com", localState: nil, chromeInstalled: false, defaults: defaults) == .defaultBrowser,
          "chrome route: with Chrome not installed the default browser opens the page")
    check((defaults.dictionary(forKey: PixelSwitchChromeProfile.defaultsKey) as? [String: String] ?? [:]) == before,
          "chrome route: with Chrome not installed no profile name is saved")
    check(C.route(accountID: id, email: "", localState: state, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch new@example.com", source: .owned),
          "chrome route: an account with a saved profile and now no address keeps its saved profile")
    check(C.route(accountID: "blank-account", email: "", localState: state, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch blank-account", source: .owned),
          "chrome route: an account with no address and no saved profile gets one named from its id, and is never matched to a profile")
}

// MARK: - The data the 2.2 window left behind

@MainActor private func chromeLegacyCleanupTests() {
    typealias W = LegacyWebDataCleanup
    let suite = "ai.pixelventures.pixelswitch.tests.legacyweb.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "legacy web cleanup: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }

    check(W.defaultsKey == "ai.pixelventures.pixelswitch.claudeWeb.dataStoreIDs",
          "legacy web cleanup: it reads the key 2.2 saved its data store identifiers under")
    check(!W.hasWork(defaults: defaults) && W.pendingIdentifiers(defaults: defaults).isEmpty,
          "legacy web cleanup: with nothing saved there is no work and no identifier")
    check(W.plan(defaults: defaults).isEmpty, "legacy web cleanup: with nothing saved the plan is empty")

    let a = UUID(), b = UUID(), c = UUID()
    let zero = "00000000-0000-0000-0000-000000000000"
    defaults.set(["acct-a": a.uuidString, "acct-b": b.uuidString, "acct-dup": a.uuidString, "acct-bad": "not-a-uuid", "acct-zero": zero, "acct-c": c.uuidString],
                 forKey: W.defaultsKey)
    let pending = W.pendingIdentifiers(defaults: defaults)
    check(W.hasWork(defaults: defaults) && Set(pending) == [a, b, c] && pending.count == 3,
          "legacy web cleanup: every saved identifier once; a duplicate, a non-UUID and the all-zero UUID are left out", "\(pending)")
    check(pending == W.pendingIdentifiers(defaults: defaults), "legacy web cleanup: the order of the identifiers is fixed")

    defaults.set("not a dictionary", forKey: W.defaultsKey)
    check(W.hasWork(defaults: defaults) && W.pendingIdentifiers(defaults: defaults).isEmpty, "legacy web cleanup: a saved value of the wrong type has no identifiers")
}

// MARK: - Source pins

private func chromeSource(_ name: String) -> String {
    (try? String(contentsOfFile: "PixelSwitch/ChromeProfiles/\(name)", encoding: .utf8)) ?? ""
}

@MainActor private func chromeSourcePins() {
    let dir = "PixelSwitch/ChromeProfiles"
    let names = ((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).filter { $0.hasSuffix(".swift") }.sorted()
    check(names == ["ChromeLocalState.swift", "ChromeProfileMatcher.swift", "ChromeUsageLaunch.swift", "ChromeUsagePage.swift"],
          "chrome pins: the service is these four files", "\(names)")
    let page = chromeSource("ChromeUsagePage.swift")
    let all = names.map { chromeSource($0) }.joined(separator: "\n")
    let pure = ["ChromeLocalState.swift", "ChromeProfileMatcher.swift", "ChromeUsageLaunch.swift"].map { chromeSource($0) }.joined(separator: "\n")

    // The pure files stay pure: no disk, no process, no AppKit.
    let impure = ["import AppKit", "import WebKit", "NSWorkspace", "Process()", "FileManager", "Data(contentsOf", "contentsOfFile", "FileHandle", "FileLog"]
        .filter { pure.contains($0) }
    check(impure.isEmpty, "chrome pins: the decision files only compute; reading, launching and logging live in ChromeUsagePage", impure.joined(separator: ", "))

    // Reading: Chrome's Local State is the only file read, and nothing else in Chrome's folders is named.
    check(page.components(separatedBy: "Data(contentsOf").count == 2 && page.contains("appendingPathComponent(ChromeLocalState.relativePath)"),
          "chrome pins: exactly one file is read, Chrome's Local State")
    let sensitive = ["\"Cookies\"", "Login Data", "Web Data", "History", "Preferences", "Secure Preferences", "Keychain", "SecItem", "sqlite", "Safe Storage", "Network/"]
        .filter { all.contains($0) }
    check(sensitive.isEmpty, "chrome pins: nothing names Chrome's cookies, saved logins or other private files", sensitive.joined(separator: ", "))
    check(page.components(separatedBy: "contentsOfDirectory").count == 1 && page.components(separatedBy: "enumerator(").count == 1,
          "chrome pins: Chrome's folders are never listed or walked")

    // Launching: the argument array through Process, no shell, with a default-browser fallback.
    check(page.components(separatedBy: "Process()").count == 2
          && page.contains("process.executableURL = URL(fileURLWithPath: ChromeUsageLaunch.openExecutable)")
          && page.contains("process.arguments = ChromeUsageLaunch.arguments(profileDirectory: profileDirectory)"),
          "chrome pins: one Process, /usr/bin/open, with the tested argument array")
    let shellWords = ["/bin/sh", "/bin/zsh", "/bin/bash", "\"-c\"", "system(", "popen(", "NSAppleScript", "osascript", "do shell script"].filter { all.contains($0) }
    check(shellWords.isEmpty, "chrome pins: no shell, no AppleScript", shellWords.joined(separator: ", "))
    check(page.contains("NSWorkspace.shared.urlForApplication(withBundleIdentifier: ChromeUsageLaunch.chromeBundleIdentifier)")
          && page.contains("NSWorkspace.shared.open(url)") && page.components(separatedBy: "NSWorkspace.shared.open(").count == 2,
          "chrome pins: Chrome's presence is checked by bundle id, and the one other way a page opens is the default browser")

    // Acting on the page: never. It opens one fixed address and that is all.
    let acting = ["WKWebView", "WKUserScript", "evaluateJavaScript", "callAsyncJavaScript", "URLSession", "URLRequest", "httpMethod", "httpBody", "XMLHttpRequest", "fetch(", ".click(",
                  "customUserAgent", "applicationNameForUserAgent"].filter { all.contains($0) }
    check(acting.isEmpty, "chrome pins: the service never loads, scripts, clicks, submits or sends anything on claude.ai", acting.joined(separator: ", "))
    check(all.components(separatedBy: "claude.ai/").count == all.components(separatedBy: "claude.ai/settings/usage").count
          && all.components(separatedBy: "https://claude.ai").count == 2,
          "chrome pins: the only claude.ai address in the service is the usage page, written once")

    // The log never carries an address.
    let logLines = page.components(separatedBy: "\n").filter { $0.contains("chromeLog.") }
    let leaks = logLines.filter { line in ["email", "Email", "displayEmail", "\\(accountID)", "absoluteString"].contains { line.contains($0) } }
    check(logLines.count >= 6 && leaks.isEmpty, "chrome pins: log lines name the account by its short id and never an address (\(logLines.count) lines)",
          leaks.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: " | "))

    // The 2.2 window is gone, and no web framework remains anywhere (LaunchSafetyTests pins that and the cleanup's order).
    let sources = shippedSources()
    check(!FileManager.default.fileExists(atPath: "PixelSwitch/ClaudeWeb") && !FileManager.default.fileExists(atPath: "Tests/UnitTests/ClaudeWebTests.swift"),
          "chrome pins: the 2.2 in-app claude.ai window's folder and its tests are deleted")
    let leftovers = sources.filter { $0.path.hasPrefix("PixelSwitch") && ($0.text.contains("ClaudeWebWindows") || $0.text.contains("ClaudeWebPolicy")
                                                                         || $0.text.contains("ClaudeWebSessionStore") || $0.text.contains("ClaudeWebStoreMap")
                                                                         || $0.text.contains("removeWebSignIn")) }.map(\.path)
    check(leftovers.isEmpty, "chrome pins: nothing in the shipped product still names the 2.2 window's types", leftovers.joined(separator: " | "))
    let app = (try? String(contentsOfFile: "PixelSwitch/PixelSwitchApp.swift", encoding: .utf8)) ?? ""
    check(app.components(separatedBy: "LegacyWebDataCleanup.runAtLaunch").count == 2,
          "chrome pins: the cleanup runs from one place, the app's launch")
    let state = (try? String(contentsOfFile: "PixelSwitch/AppState.swift", encoding: .utf8)) ?? ""
    check(!state.contains("ClaudeWeb") && !state.contains("WebSignIn"), "chrome pins: removing an account no longer touches any claude.ai web sign-in")
}

// MARK: - Strings

@MainActor private func chromeStringTests() {
    let languages = ["en", "de", "fr", "ja", "zh-Hans"]
    let tables = languages.map { NSDictionary(contentsOfFile: "PixelSwitch/\($0).lproj/Localizable.strings") as? [String: String] ?? [:] }
    let help = "Open this account's claude.ai usage page in Chrome to use a reset"
    check(tables.allSatisfy { ($0[help] ?? "").isEmpty == false }, "chrome strings: the new help text is in all five tables")
    check(tables[0][help] == help && zip(tables.dropFirst(), languages.dropFirst()).allSatisfy { $0.0[help] != help },
          "chrome strings: its English line is the key, and de, fr, ja and zh-Hans are translated, not copied")
    check(tables.allSatisfy { ($0[help] ?? "").contains("Chrome") && ($0[help] ?? "").contains("claude.ai") },
          "chrome strings: every language keeps the names Chrome and claude.ai")
    check(tables.allSatisfy { !($0[help] ?? "").contains("\u{2014}") }, "chrome strings: no em dash in any language")
    check(tables.allSatisfy { $0["Open this account's claude.ai usage page to use a reset"] == nil },
          "chrome strings: the old help text (the window's) is gone from all five tables")

    // Every string only the 2.2 window used is gone from every table.
    let windowKeys = ["Back", "Reload", "Open in browser", "Paste sign-in link", "Sign out of claude.ai here", "Sign in as %@",
                      "Paste the sign-in link from your email", "Load",
                      "That is not one whole link. Paste the full link from your email.",
                      "Only a link on claude.ai loads here. Paste the sign-in link from your email.",
                      "Sign out of claude.ai here?",
                      "This removes the claude.ai sign-in that PixelSwitch keeps for %@. Your browser and your other accounts are not affected.",
                      "Sign out",
                      "This window is signed in as %1$@, not %2$@. Sign out here and sign in as %2$@.",
                      "PixelSwitch could not remove this sign-in. Quit and reopen PixelSwitch, then try again.",
                      "Couldn't load claude.ai. Check your connection."]
    let stillThere = windowKeys.filter { key in tables.contains { $0[key] != nil } }
    check(stillThere.isEmpty, "chrome strings: the 2.2 window's strings are gone from all five tables", stillThere.joined(separator: " | "))
    // The keys the button still needs stay.
    let kept = ["Limit resets", "Limit resets for %@", "Cancel", "Close"]
    check(kept.allSatisfy { key in tables.allSatisfy { ($0[key] ?? "").isEmpty == false } }, "chrome strings: the strings the button and other screens still use are kept")
    check(Set(tables.map { Set($0.keys) }).count == 1, "chrome strings: the five tables still have exactly the same keys")
}
