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
// 2.4: macOS refuses PixelSwitch the read of Chrome's profile list, so each account also has a
// stored choice of profile (its own PixelSwitch one, the one Chrome finds for the address, or a
// named folder); `--profile-email` is only ever sent for the one choice that asks for it.
import Foundation

@MainActor func runChromeProfileTests() {
    chromeLocalStateTests()
    chromeProfileMatcherTests()
    chromeOwnedProfileTests()
    chromeOwnedDirectoryRobustnessTests()
    chromeLaunchTests()
    chromeRealShapeTests()
    chromeReadFailureTests()
    chromeChoiceTests()
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

// MARK: - A Local State shaped exactly like a real one

/// The shape of a real Chrome 158 `Local State` on the founder's Mac (read on 2026-11-20):
/// the same field names and value types in `profile.info_cache` (floats, ints, bools, an
/// empty string, a negative colour int, a dotted key), the same other keys under `profile`,
/// and a PixelSwitch-made profile that is not signed in to a Google account (`user_name`
/// empty), as Chrome writes one after `--profile-directory=PixelSwitch ...`. Every name,
/// address and id here is made up; none of it is real data.
private let realShapeLocalState = """
{
  "browser": {"enabled_labs_experiments": ["x@1"], "first_run_finished": true},
  "profile": {
    "info_cache": {
      "Default": {
        "active_time": 1792697420.316911, "ai_subscription_tier": 0, "avatar_icon": "chrome://theme/IDR_PROFILE_AVATAR_26",
        "background_apps": false, "default_avatar_fill_color": -9761, "default_avatar_stroke_color": -20803,
        "enterprise_label": "", "force_signin_profile_locked": false, "gaia_given_name": "Pat", "gaia_id": "100000000000000000001",
        "gaia_name": "Pat Example", "gaia_picture_file_name": "Google Profile Picture.png", "hosted_domain": "NO_HOSTED_DOMAIN",
        "is_consented_primary_account": true, "is_ephemeral": false, "is_glic_eligible": false, "is_managed": 0,
        "is_using_default_avatar": true, "is_using_default_name": false,
        "last_downloaded_gaia_picture_url_with_size": "https://lh3.googleusercontent.example/a/x=s256-c-ns",
        "managed_user_id": "", "metrics_bucket_index": 1, "name": "Your Chrome", "profile_color_seed": -20803,
        "profile_highlight_color": -9761, "signin.with_credential_provider": false, "user_name": "personal@example.com"
      },
      "PixelSwitch work@example.org": {
        "active_time": 1792697400.5, "avatar_icon": "chrome://theme/IDR_PROFILE_AVATAR_5", "background_apps": false,
        "default_avatar_fill_color": -9761, "default_avatar_stroke_color": -20803, "force_signin_profile_locked": false,
        "gaia_id": "", "is_consented_primary_account": false, "is_ephemeral": false, "is_glic_eligible": false,
        "is_using_default_avatar": true, "is_using_default_name": true, "managed_user_id": "", "metrics_bucket_index": 4,
        "name": "Person 1", "profile_color_seed": -20803, "profile_highlight_color": -9761,
        "signin.with_credential_provider": false, "user_name": ""
      },
      "Profile 1": {
        "active_time": 1792697300.0, "ai_subscription_tier": 0, "avatar_icon": "chrome://theme/IDR_PROFILE_AVATAR_28",
        "background_apps": false, "default_avatar_fill_color": -9761, "default_avatar_stroke_color": -20803,
        "enterprise_label": "", "force_signin_profile_locked": false, "gaia_given_name": "Pat", "gaia_id": "100000000000000000002",
        "gaia_name": "Pat Example", "gaia_picture_file_name": "Google Profile Picture.png", "hosted_domain": "example.org",
        "is_consented_primary_account": true, "is_ephemeral": false, "is_glic_eligible": false, "is_managed": 1,
        "is_using_default_avatar": true, "is_using_default_name": false,
        "last_downloaded_gaia_picture_url_with_size": "https://lh3.googleusercontent.example/a/y=s256-c-ns",
        "managed_user_id": "", "metrics_bucket_index": 2, "name": "work@example.org", "profile_color_seed": -20803,
        "profile_highlight_color": -9761, "signin.with_credential_provider": false, "user_accepted_account_management": true,
        "user_name": "work@example.org"
      },
      "Profile 2": {
        "active_time": 1792697200.0, "ai_subscription_tier": 0, "avatar_icon": "chrome://theme/IDR_PROFILE_AVATAR_30",
        "background_apps": false, "default_avatar_fill_color": -9761, "default_avatar_stroke_color": -20803,
        "enterprise_label": "", "force_signin_profile_locked": false, "gaia_given_name": "Pat", "gaia_id": "100000000000000000003",
        "gaia_name": "Pat Example", "gaia_picture_file_name": "Google Profile Picture.png", "hosted_domain": "example.net",
        "is_consented_primary_account": true, "is_ephemeral": false, "is_glic_eligible": false, "is_managed": 1,
        "is_using_default_avatar": true, "is_using_default_name": false,
        "last_downloaded_gaia_picture_url_with_size": "https://lh3.googleusercontent.example/a/z=s256-c-ns",
        "managed_user_id": "", "metrics_bucket_index": 3, "name": "shop@example.net", "profile_color_seed": -20803,
        "profile_highlight_color": -9761, "signin.with_credential_provider": false, "user_accepted_account_management": true,
        "user_name": "shop@example.net"
      }
    },
    "last_active_profiles": ["Default", "Profile 2"],
    "last_used": "Profile 2",
    "metrics": {"next_bucket_index": 5},
    "picker_shown": true,
    "profile_counts_reported": "13439613247444598",
    "profiles_created": 3,
    "profiles_order": ["Default", "Profile 1", "Profile 2", "PixelSwitch work@example.org"]
  },
  "signin": {"active_accounts_last_emitted": "13439613247444598"},
  "was": {"restarted": false}
}
"""

@MainActor private func chromeRealShapeTests() {
    typealias L = ChromeLocalState
    typealias M = ChromeProfileMatcher
    typealias C = ChromeUsageLaunch
    let data = chromeJSON(realShapeLocalState)
    let profiles = L.parseProfiles(data)

    check(profiles.map(\.directory) == ["Default", "Profile 1", "Profile 2", "PixelSwitch work@example.org"],
          "chrome real shape: all four profiles of a real-shaped file are read, in the fixed order", "\(profiles.map(\.directory))")
    check(profiles.map(\.email) == ["personal@example.com", "work@example.org", "shop@example.net", ""],
          "chrome real shape: Google addresses come from user_name; the PixelSwitch profile that is signed in to no account has none", "\(profiles.map(\.email))")
    check(M.directory(forEmail: "personal@example.com", in: profiles) == "Default"
          && M.directory(forEmail: "work@example.org", in: profiles) == "Profile 1"
          && M.directory(forEmail: "shop@example.net", in: profiles) == "Profile 2",
          "chrome real shape: each address finds its own profile (Default, Profile 1, Profile 2)")
    check(M.directory(forEmail: "stranger@example.com", in: profiles) == nil,
          "chrome real shape: an address no profile has still finds nothing")

    // One bad entry never drops the rest, even among real-shaped ones: damage every field type in turn.
    let damaged = realShapeLocalState
        .replacingOccurrences(of: "\"user_name\": \"work@example.org\"", with: "\"user_name\": 12345")
        .replacingOccurrences(of: "\"active_time\": 1792697200.0", with: "\"active_time\": \"not a number\"")
        .replacingOccurrences(of: "\"is_managed\": 0", with: "\"is_managed\": null")
    let afterDamage = L.parseProfiles(chromeJSON(damaged))
    check(afterDamage.map(\.directory) == ["Default", "Profile 1", "Profile 2", "PixelSwitch work@example.org"]
          && afterDamage.map(\.email) == ["personal@example.com", "", "shop@example.net", ""],
          "chrome real shape: a number where an address should be, a string where a float should be and a null drop only that field, never a profile", "\(afterDamage)")
    let oneObjectBroken = realShapeLocalState.replacingOccurrences(of: "\"Profile 2\": {", with: "\"Profile 2\": [{")
        .replacingOccurrences(of: "\"user_name\": \"shop@example.net\"\n      }", with: "\"user_name\": \"shop@example.net\"\n      }]")
    check(L.parseProfiles(chromeJSON(oneObjectBroken)).map(\.directory) == ["Default", "Profile 1", "PixelSwitch work@example.org"],
          "chrome real shape: one entry of the wrong kind (a list, not an object) is skipped and the other three are kept")
    // A byte-order mark, which some tools write, does not hide the file.
    check(L.parseProfiles(Data([0xEF, 0xBB, 0xBF]) + data).count == 4,
          "chrome real shape: a leading byte-order mark does not stop the profiles being read")

    // The saved PixelSwitch folder for an account never beats a profile Chrome already has for the address.
    let suite = "ai.pixelventures.pixelswitch.tests.chromeprecedence.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "chrome precedence: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }
    let id = "55555555-EEEE-4EEE-8EEE-555555555555"
    defaults.set([id.lowercased(): "PixelSwitch work@example.org"], forKey: PixelSwitchChromeProfile.defaultsKey)
    check(PixelSwitchChromeProfile.savedDirectory(forAccountID: id, defaults: defaults) == "PixelSwitch work@example.org",
          "chrome precedence: setup, the account already has a saved PixelSwitch folder (as after the 2.3 run that found none)")
    check(C.route(accountID: id, email: "work@example.org", localState: data, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome precedence: a profile Chrome has for the address wins over the saved PixelSwitch folder")
    check(PixelSwitchChromeProfile.savedDirectory(forAccountID: id, defaults: defaults) == "PixelSwitch work@example.org",
          "chrome precedence: and the saved folder is left as it was, not erased or rewritten")
    check(C.route(accountID: id, email: "WORK@Example.org ", localState: data, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome precedence: it wins whatever the case or spacing of the address")
    // Profile gone later: the saved folder is used again (it was never lost).
    let withoutWork = data.withoutProfile("Profile 1")
    check(C.route(accountID: id, email: "work@example.org", localState: withoutWork, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch work@example.org", source: .owned),
          "chrome precedence: if the matching profile is later removed from Chrome the saved PixelSwitch folder is used again")
    // The PixelSwitch folder itself signed in to the same address as a human profile: the human profile comes first.
    let twinState = realShapeLocalState.replacingOccurrences(of: "\"user_name\": \"\"", with: "\"user_name\": \"work@example.org\"")
    check(C.route(accountID: id, email: "work@example.org", localState: chromeJSON(twinState), chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome precedence: with the PixelSwitch folder signed in to the same address too, the person's own profile still wins")
    // With no human profile, a PixelSwitch folder that is signed in to the address is a match (it is a real profile Chrome has).
    let onlyOwned = chromeJSON("""
    {"profile": {"info_cache": {"PixelSwitch work@example.org": {"name": "Person 1", "user_name": "work@example.org"}}}}
    """)
    check(C.route(accountID: id, email: "work@example.org", localState: onlyOwned, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch work@example.org", source: .matched),
          "chrome precedence: when only the PixelSwitch folder is signed in to the address, it matches like any other profile")
}

private extension Data {
    /// This JSON with one `profile.info_cache` entry taken out, for tests.
    func withoutProfile(_ directory: String) -> Data {
        guard var root = (try? JSONSerialization.jsonObject(with: self)) as? [String: Any],
              var profile = root["profile"] as? [String: Any],
              var cache = profile["info_cache"] as? [String: Any] else { return self }
        cache.removeValue(forKey: directory)
        profile["info_cache"] = cache
        root["profile"] = profile
        return (try? JSONSerialization.data(withJSONObject: root)) ?? self
    }
}

// MARK: - A Local State that could not be read

@MainActor private func chromeReadFailureTests() {
    typealias L = ChromeLocalState
    typealias C = ChromeUsageLaunch

    // The error pairs a refusal or a missing file arrives as.
    check(L.readFailure(domain: NSCocoaErrorDomain, code: 257) == .accessDenied && L.readFailure(domain: NSPOSIXErrorDomain, code: 1) == .accessDenied
          && L.readFailure(domain: NSPOSIXErrorDomain, code: 13) == .accessDenied,
          "chrome read: no-permission, not-permitted and access-denied errors are a refusal")
    check(L.readFailure(domain: NSCocoaErrorDomain, code: 260) == .notFound && L.readFailure(domain: NSCocoaErrorDomain, code: 4) == .notFound
          && L.readFailure(domain: NSPOSIXErrorDomain, code: 2) == .notFound && L.readFailure(domain: NSPOSIXErrorDomain, code: 20) == .notFound,
          "chrome read: no-such-file errors are a missing file")
    check(L.readFailure(domain: NSCocoaErrorDomain, code: 256) == .other && L.readFailure(domain: NSPOSIXErrorDomain, code: 5) == .other
          && L.readFailure(domain: "SomeOtherDomain", code: 1) == .other,
          "chrome read: any other error is other, and a code only counts in its own domain")
    // A Cocoa "no permission" error wraps the POSIX one: either layer is enough, and a refusal outranks a missing file.
    let wrapped = NSError(domain: NSCocoaErrorDomain, code: 256,
                          userInfo: [NSUnderlyingErrorKey: NSError(domain: NSPOSIXErrorDomain, code: 1)])
    check(L.readFailure(from: wrapped) == .accessDenied, "chrome read: a refusal carried by the error underneath is still found")
    let both = NSError(domain: NSCocoaErrorDomain, code: 260, userInfo: [NSUnderlyingErrorKey: NSError(domain: NSPOSIXErrorDomain, code: 13)])
    check(L.readFailure(from: both) == .accessDenied, "chrome read: when a missing file and a refusal both show, the refusal is reported")

    // Real errors from the real Foundation, on files made here.
    let scratch = NSTemporaryDirectory() + "pixelswitch-chrome-read-\(UUID().uuidString)"
    defer { try? FileManager.default.removeItem(atPath: scratch) }
    do { try FileManager.default.createDirectory(atPath: scratch, withIntermediateDirectories: true) } catch {
        check(false, "chrome read: could not make a scratch folder", "\(error)"); return
    }
    let locked = scratch + "/locked"
    FileManager.default.createFile(atPath: locked, contents: Data("{}".utf8), attributes: [.posixPermissions: 0])
    var lockedError: Error?
    do { _ = try Data(contentsOf: URL(fileURLWithPath: locked)) } catch { lockedError = error }
    // A process that is allowed to read anything (root) cannot make the refusal; say so instead of failing.
    if let lockedError {
        check(L.readFailure(from: lockedError) == .accessDenied, "chrome read: a real unreadable file's error is a refusal", "\(lockedError)")
    } else {
        check(true, "chrome read: this process can read a mode-0 file (root), so the real refusal cannot be made here")
    }
    var missingError: Error?
    do { _ = try Data(contentsOf: URL(fileURLWithPath: scratch + "/Local State")) } catch { missingError = error }
    check(missingError.map { L.readFailure(from: $0) } == .notFound, "chrome read: a real missing file's error is a missing file", "\(String(describing: missingError))")

    // What the route does with it: the same folder as before, told apart only by its source.
    let suite = "ai.pixelventures.pixelswitch.tests.chromereadfail.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "chrome read: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }
    let id = "44444444-DDDD-4DDD-8DDD-444444444444"
    check(C.route(accountID: id, email: "u@example.com", localState: nil, chromeInstalled: true, localStateFailure: .accessDenied, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch u@example.com", source: .ownedProfileListUnreadable(.accessDenied)),
          "chrome read route: a refused read opens the account's own folder and says the list was unreadable, not that nothing matched")
    check(C.route(accountID: id, email: "u@example.com", localState: nil, chromeInstalled: true, localStateFailure: .other, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch u@example.com", source: .ownedProfileListUnreadable(.other)),
          "chrome read route: any other failed read is reported as unreadable too")
    check(C.route(accountID: id, email: "u@example.com", localState: nil, chromeInstalled: true, localStateFailure: .notFound, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch u@example.com", source: .owned),
          "chrome read route: a missing Local State (Chrome never run) is the plain no-profile case")
    check(C.route(accountID: id, email: "u@example.com", localState: nil, chromeInstalled: true, defaults: defaults)
          == .chrome(profileDirectory: "PixelSwitch u@example.com", source: .owned),
          "chrome read route: with no failure given the older behaviour is kept")
    check(C.route(accountID: id, email: "work@example.org", localState: chromeJSON(realShapeLocalState), chromeInstalled: true,
                  localStateFailure: .accessDenied, defaults: defaults) == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome read route: a failure that came with data (never happens) cannot beat a match")
    check(C.route(accountID: id, email: "u@example.com", localState: nil, chromeInstalled: false, localStateFailure: .accessDenied, defaults: defaults) == .defaultBrowser,
          "chrome read route: with Chrome not installed a failed read changes nothing")
    check(PixelSwitchChromeProfile.savedDirectory(forAccountID: id, defaults: defaults) == "PixelSwitch u@example.com",
          "chrome read route: the account's own folder is saved the same way as before")
}

// MARK: - The stored choice of profile

@MainActor private func chromeChoiceTests() {
    typealias S = ChromeProfileChoiceStore
    typealias Ch = ChromeProfileChoice
    typealias C = ChromeUsageLaunch
    let url = "https://claude.ai/settings/usage"

    // A folder name that may be sent after --profile-directory=.
    let valid = ["Default", "Profile 1", "Profile 12", "PixelSwitch me@example.com", "Person 1", "My Work", "profile_3", "é"]
    check(valid.allSatisfy { Ch.isValidDirectory($0) }, "chrome choice: ordinary folder names are valid", valid.filter { !Ch.isValidDirectory($0) }.joined(separator: " | "))
    let invalid = ["", ".", "..", "a/b", "a\\b", "../x", "x/..", "/etc", "-x", "--profile-email=a@b.c", "a..b", " Profile 1", "Profile 1 ", "a\nb", "a\u{0}b",
                   String(repeating: "x", count: 256)]
    check(invalid.allSatisfy { !Ch.isValidDirectory($0) },
          "chrome choice: empty, dot, slash, backslash, double dot, leading dash, edge spaces, control characters and 256 bytes are all invalid",
          invalid.filter { Ch.isValidDirectory($0) }.map { $0.debugDescription }.joined(separator: " | "))

    // The text kept, both ways.
    check(S.text(for: .ownProfile) == "own" && S.text(for: .chromeProfileSignedInAs) == "signedInAs"
          && S.text(for: .directory("Profile 1")) == "directory:Profile 1" && S.text(for: .directory("../x")) == nil,
          "chrome choice: the kept text is own, signedInAs or directory:<name>; an invalid name has none")
    check(S.choice(fromText: "own") == .ownProfile && S.choice(fromText: " OWN\n") == .ownProfile
          && S.choice(fromText: "signedInAs") == .chromeProfileSignedInAs && S.choice(fromText: "SIGNEDINAS") == .chromeProfileSignedInAs
          && S.choice(fromText: "directory:Profile 1") == .directory("Profile 1") && S.choice(fromText: "Directory: Default ") == .directory("Default"),
          "chrome choice: the words ignore case and edge spaces; the folder name keeps its own case")
    check(S.choice(fromText: "directory:Profile A") == .directory("Profile A"), "chrome choice: a folder name keeps its capitals")
    for bad in ["", "  ", "garbage", "signed in as", "directory", "directory:", "directory:  ", "directory:../x", "directory:a/b", "directory:-rf", "directory:a..b",
                "profile-email", "--profile-email=a@b.c", "email:a@b.c"] {
        check(S.choice(fromText: bad) == nil, "chrome choice: the text \(bad.debugDescription) is not a choice")
    }
    check(Ch.directory("Profile 1") == Ch.directory("Profile 1") && Ch.directory("Profile 1") != Ch.directory("Profile 2") && Ch.ownProfile != Ch.chromeProfileSignedInAs,
          "chrome choice: choices compare by what they are")

    // Kept per account, in UserDefaults, and read back after a relaunch.
    let suite = "ai.pixelventures.pixelswitch.tests.chromechoice.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "chrome choice: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }
    let a = "AAAAAAAA-1111-4111-8111-AAAAAAAAAAAA", b = "BBBBBBBB-2222-4222-8222-BBBBBBBBBBBB"
    check(S.defaultsKey == "ai.pixelventures.pixelswitch.chromeProfiles.choices", "chrome choice: the key is PixelSwitch-namespaced and fixed", S.defaultsKey)
    check(S.saved(forAccountID: a, defaults: defaults) == nil,
          "chrome choice: an account with nothing kept has no saved choice (the menu calls that Automatic)")
    check(S.set(.chromeProfileSignedInAs, forAccountID: a, defaults: defaults) && S.saved(forAccountID: a, defaults: defaults) == .chromeProfileSignedInAs,
          "chrome choice: a kept choice is read back")
    check(S.saved(forAccountID: a.lowercased(), defaults: defaults) == .chromeProfileSignedInAs && S.saved(forAccountID: " \(a)\n", defaults: defaults) == .chromeProfileSignedInAs,
          "chrome choice: any spelling of the account id finds it")
    check(S.saved(forAccountID: b, defaults: defaults) == nil, "chrome choice: another account is not affected")
    check(S.set(.directory("Profile 1"), forAccountID: b, defaults: defaults) && S.saved(forAccountID: b, defaults: defaults) == .directory("Profile 1")
          && S.saved(forAccountID: a, defaults: defaults) == .chromeProfileSignedInAs,
          "chrome choice: two accounts keep two choices")
    let raw = defaults.dictionary(forKey: S.defaultsKey) as? [String: String] ?? [:]
    check(raw == [a.lowercased(): "signedInAs", b.lowercased(): "directory:Profile 1"],
          "chrome choice: what is kept is the lower case account id to the text, exactly", "\(raw)")
    if let relaunched = UserDefaults(suiteName: suite) {
        check(S.saved(forAccountID: a, defaults: relaunched) == .chromeProfileSignedInAs && S.saved(forAccountID: b, defaults: relaunched) == .directory("Profile 1"),
              "chrome choice: both survive a relaunch (read back from saved defaults)")
    } else {
        check(false, "chrome choice: reopen the suite")
    }
    check(!S.set(.directory("../evil"), forAccountID: b, defaults: defaults) && S.saved(forAccountID: b, defaults: defaults) == .directory("Profile 1")
          && !S.set(.directory(""), forAccountID: b, defaults: defaults) && !S.set(.directory("-x"), forAccountID: b, defaults: defaults),
          "chrome choice: an invalid folder name is refused and the kept choice is left alone")
    check(S.set(.ownProfile, forAccountID: a, defaults: defaults) && S.saved(forAccountID: a, defaults: defaults) == .ownProfile
          && (defaults.dictionary(forKey: S.defaultsKey) as? [String: String] ?? [:]) == [a.lowercased(): "own", b.lowercased(): "directory:Profile 1"],
          "chrome choice: choosing the own profile is a kept choice (own), not the same as having none")
    S.clear(forAccountID: a, defaults: defaults)
    check(S.saved(forAccountID: a, defaults: defaults) == nil
          && (defaults.dictionary(forKey: S.defaultsKey) as? [String: String] ?? [:]) == [b.lowercased(): "directory:Profile 1"],
          "chrome choice: clearing an account's choice leaves it with none, and the other account alone")
    S.clear(forAccountID: b, defaults: defaults)
    S.clear(forAccountID: "never-had-one", defaults: defaults)
    check(defaults.object(forKey: S.defaultsKey) == nil, "chrome choice: with no entries left the key is gone, and clearing an account with none is harmless")
    // A shell can write the documented form; a damaged value or a wrong type counts as no choice.
    defaults.set([a.lowercased(): "directory:Profile 2", b.lowercased(): "nonsense"], forKey: S.defaultsKey)
    check(S.saved(forAccountID: a, defaults: defaults) == .directory("Profile 2") && S.saved(forAccountID: b, defaults: defaults) == nil,
          "chrome choice: a value written from a shell is read, and a damaged one is ignored")
    defaults.set("not a dictionary", forKey: S.defaultsKey)
    check(S.saved(forAccountID: a, defaults: defaults) == nil,
          "chrome choice: a value of the wrong type is no choice")
    check(S.set(.directory("Default"), forAccountID: a, defaults: defaults) && S.saved(forAccountID: a, defaults: defaults) == .directory("Default"),
          "chrome choice: a wrong-typed value is replaced by the next kept choice")

    // One bad entry never hides or overwrites the other accounts' choices (the dictionary is read as [String: Any], String values kept).
    let c = "CCCCCCCC-3333-4333-8333-CCCCCCCCCCCC", d = "DDDDDDDD-4444-4444-8444-DDDDDDDDDDDD"
    defaults.set([a.lowercased(): "signedInAs", b.lowercased(): 7, c.lowercased(): "directory:Profile 1", d.lowercased(): ["nested": "x"]] as [String: Any], forKey: S.defaultsKey)
    check(S.saved(forAccountID: a, defaults: defaults) == .chromeProfileSignedInAs && S.saved(forAccountID: c, defaults: defaults) == .directory("Profile 1")
          && S.saved(forAccountID: b, defaults: defaults) == nil && S.saved(forAccountID: d, defaults: defaults) == nil,
          "chrome choice: with a number and a dictionary among the entries, the String entries are still read and the bad ones count as no choice")
    check(S.storedTexts(defaults: defaults) == [a.lowercased(): "signedInAs", c.lowercased(): "directory:Profile 1"],
          "chrome choice: the texts read are exactly the String entries")
    let other = "EEEEEEEE-5555-4555-8555-EEEEEEEEEEEE"
    check(S.set(.ownProfile, forAccountID: other, defaults: defaults)
          && S.saved(forAccountID: a, defaults: defaults) == .chromeProfileSignedInAs && S.saved(forAccountID: c, defaults: defaults) == .directory("Profile 1")
          && S.saved(forAccountID: other, defaults: defaults) == .ownProfile,
          "chrome choice: keeping one account's choice next to a bad entry leaves the other accounts' choices as they were")
    check((defaults.dictionary(forKey: S.defaultsKey) as? [String: String]) == [a.lowercased(): "signedInAs", c.lowercased(): "directory:Profile 1", other.lowercased(): "own"],
          "chrome choice: and the bad entries are dropped by that write, not kept or turned into anything")
    defaults.set([a.lowercased(): "signedInAs", b.lowercased(): 7, c.lowercased(): "own"] as [String: Any], forKey: S.defaultsKey)
    S.clear(forAccountID: c, defaults: defaults)
    check(S.saved(forAccountID: a, defaults: defaults) == .chromeProfileSignedInAs && S.saved(forAccountID: c, defaults: defaults) == nil
          && (defaults.dictionary(forKey: S.defaultsKey) as? [String: String]) == [a.lowercased(): "signedInAs"],
          "chrome choice: clearing one account next to a bad entry keeps the others, and the bad entry goes")
    defaults.set([b.lowercased(): 7] as [String: Any], forKey: S.defaultsKey)
    S.clear(forAccountID: a, defaults: defaults)
    check(defaults.object(forKey: S.defaultsKey) == nil, "chrome choice: when only bad entries are left, clearing leaves no key at all")
    // What the Settings menu does with a pick: Automatic (nil) forgets, anything else is kept, and the own profile is a kept choice.
    defaults.removeObject(forKey: S.defaultsKey)
    let pickAccount = "FFFFFFFF-6666-4666-8666-FFFFFFFFFFFF"
    check(S.saved(forAccountID: pickAccount, defaults: defaults) == nil, "chrome pick: an account with nothing kept starts as Automatic (no saved choice)")
    check(S.apply(.ownProfile, forAccountID: pickAccount, defaults: defaults) && S.saved(forAccountID: pickAccount, defaults: defaults) == .ownProfile
          && (defaults.dictionary(forKey: S.defaultsKey) as? [String: String]) == [pickAccount.lowercased(): "own"],
          "chrome pick: picking the own profile from Automatic keeps it (as own), so the account is no longer Automatic")
    check(S.apply(.chromeProfileSignedInAs, forAccountID: pickAccount, defaults: defaults) && S.saved(forAccountID: pickAccount, defaults: defaults) == .chromeProfileSignedInAs,
          "chrome pick: picking signed in as keeps it")
    check(S.apply(.directory("Profile 1"), forAccountID: pickAccount, defaults: defaults) && S.saved(forAccountID: pickAccount, defaults: defaults) == .directory("Profile 1"),
          "chrome pick: picking a named folder keeps it")
    check(!S.apply(.directory("../x"), forAccountID: pickAccount, defaults: defaults) && S.saved(forAccountID: pickAccount, defaults: defaults) == .directory("Profile 1"),
          "chrome pick: an invalid folder name changes nothing")
    check(S.apply(nil, forAccountID: pickAccount, defaults: defaults) && S.saved(forAccountID: pickAccount, defaults: defaults) == nil
          && defaults.object(forKey: S.defaultsKey) == nil,
          "chrome pick: picking Automatic forgets the kept choice, and with nothing left the key is gone")
    check(S.apply(nil, forAccountID: pickAccount, defaults: defaults) && S.apply(.ownProfile, forAccountID: pickAccount, defaults: defaults)
          && S.saved(forAccountID: pickAccount, defaults: defaults) == .ownProfile,
          "chrome pick: Automatic, then the own profile again, keeps own: the explicit own profile is a different state from none")
    defaults.removeObject(forKey: S.defaultsKey)
    // The documented form uses lower case keys; an upper case key written from a shell is never found.
    defaults.set([a: "signedInAs"], forKey: S.defaultsKey)
    check(S.saved(forAccountID: a, defaults: defaults) == nil, "chrome choice: an upper case account-id key is not found (the documented keys are lower case)")
    defaults.removeObject(forKey: S.defaultsKey)

    // The three launches, as exact argument arrays.
    check(C.arguments(profileDirectory: "PixelSwitch me@example.com") == ["-na", "Google Chrome", "--args", "--profile-directory=PixelSwitch me@example.com", url],
          "chrome args: the own profile is --profile-directory with the PixelSwitch folder")
    check(C.arguments(profileDirectory: "Profile 1") == ["-na", "Google Chrome", "--args", "--profile-directory=Profile 1", url],
          "chrome args: a named folder is --profile-directory with that name")
    check(C.arguments(profileEmail: "me@example.com") == ["-na", "Google Chrome", "--args", "--profile-email=me@example.com", url],
          "chrome args: the signed-in-as choice is --profile-email with the address and nothing else")
    check(!C.arguments(profileEmail: "me@example.com").contains { $0.hasPrefix("--profile-directory") }
          && !C.arguments(profileDirectory: "Profile 1").contains { $0.hasPrefix("--profile-email") },
          "chrome args: the two flags are never sent together")
    let hostile = C.arguments(profileEmail: "a;b$(c)`d`@x.com")
    check(hostile.count == 5 && hostile[3] == "--profile-email=a;b$(c)`d`@x.com", "chrome args: shell characters in an address stay one plain argument")
    check(["me@example.com", " me@example.com ", "Me+tag@Example.COM", "a@b"].allSatisfy { C.isUsableProfileEmail($0) }
          && ["", "   ", "no-at-sign", "a b@x.com", "a\n@x.com", "a\u{7F}@x.com", "a@b.com\u{0}", String(repeating: "x", count: 330) + "@x.com"].allSatisfy { !C.isUsableProfileEmail($0) },
          "chrome args: an address is usable with an @ and no space or control character, and not blank or absurdly long")

    // Resolution order at the click.
    let state = chromeJSON(sampleLocalState)   // Default alpha@example.com, Profile 1 beta@example.org, Profile 2 gamma@example.net
    let route = { (account: String, email: String, local: Data?, failure: ChromeLocalState.ReadFailure?) -> ChromeUsageLaunch.Route in
        C.route(accountID: account, email: email, localState: local, chromeInstalled: true, localStateFailure: failure, defaults: defaults)
    }
    defaults.removeObject(forKey: S.defaultsKey)
    defaults.removeObject(forKey: PixelSwitchChromeProfile.defaultsKey)
    let id = "CCCCCCCC-3333-4333-8333-CCCCCCCCCCCC"

    // 1. An explicit stored choice, whatever it is, wins over a profile Chrome's list shows for the address.
    let matchingList = ("beta@example.org", Optional(state), Optional<ChromeLocalState.ReadFailure>.none)   // Profile 1 is signed in as beta
    S.set(.directory("Profile 2"), forAccountID: id, defaults: defaults)
    check(route(id, matchingList.0, matchingList.1, matchingList.2) == .chrome(profileDirectory: "Profile 2", source: .chosenDirectory),
          "chrome order: a stored folder beats a readable list that has the address")
    S.set(.ownProfile, forAccountID: id, defaults: defaults)
    check(route(id, matchingList.0, matchingList.1, matchingList.2) == .chrome(profileDirectory: "PixelSwitch beta@example.org", source: .chosenOwn),
          "chrome order: a stored own profile beats a readable list that has the address (and makes and saves the own folder)")
    check(PixelSwitchChromeProfile.savedDirectory(forAccountID: id, defaults: defaults) == "PixelSwitch beta@example.org",
          "chrome order: the own folder an explicit own choice opens is the account's saved PixelSwitch folder")
    S.set(.chromeProfileSignedInAs, forAccountID: id, defaults: defaults)
    check(route(id, matchingList.0, matchingList.1, matchingList.2) == .chromeSignedInAs(email: "beta@example.org"),
          "chrome order: a stored signed-in-as choice beats a readable list too (Chrome is asked for the address, not the folder the list names)")
    // 2. With no stored choice, a profile the list shows wins, even over a PixelSwitch folder saved earlier.
    S.clear(forAccountID: id, defaults: defaults)
    check(route(id, matchingList.0, matchingList.1, matchingList.2) == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome order: with no stored choice a readable list that has the address wins, over the saved PixelSwitch folder too")
    check(S.saved(forAccountID: id, defaults: defaults) == nil && PixelSwitchChromeProfile.savedDirectory(forAccountID: id, defaults: defaults) == "PixelSwitch beta@example.org",
          "chrome order: and neither the saved folder nor the absent choice is changed by it")
    // A stored choice is honoured when the list cannot be read, and makes nothing it does not need.
    defaults.removeObject(forKey: PixelSwitchChromeProfile.defaultsKey)
    S.set(.directory("Profile 2"), forAccountID: id, defaults: defaults)
    check(route(id, "new@example.com", state, nil) == .chrome(profileDirectory: "Profile 2", source: .chosenDirectory)
          && route(id, "new@example.com", nil, .accessDenied) == .chrome(profileDirectory: "Profile 2", source: .chosenDirectory),
          "chrome order: a stored folder is opened whether the list had no match or could not be read")
    check(defaults.dictionary(forKey: PixelSwitchChromeProfile.defaultsKey) == nil,
          "chrome order: a stored folder makes and saves no PixelSwitch folder")
    S.set(.chromeProfileSignedInAs, forAccountID: id, defaults: defaults)
    check(route(id, "new@example.com", nil, .accessDenied) == .chromeSignedInAs(email: "new@example.com")
          && route(id, "  New@Example.com ", state, nil) == .chromeSignedInAs(email: "New@Example.com"),
          "chrome order: the signed-in-as choice sends the (trimmed) address, whether the list was unreadable or just had no match")
    check(defaults.dictionary(forKey: PixelSwitchChromeProfile.defaultsKey) == nil, "chrome order: the signed-in-as choice makes and saves no PixelSwitch folder")
    for (index, unusable) in ["", "   ", "no-at-sign", "a b@x.com"].enumerated() {
        let other = "unusable-\(index)"   // its own account each time: the first own folder an account gets is saved for good
        S.set(.chromeProfileSignedInAs, forAccountID: other, defaults: defaults)
        let ownFolder = PixelSwitchChromeProfile.directoryName(forEmail: unusable, accountID: other)
        check(route(other, unusable, nil, .accessDenied) == .chrome(profileDirectory: ownFolder, source: .ownedSignedInAddressUnusable),
              "chrome order: the signed-in-as choice with the unusable address \(unusable.debugDescription) falls back to the own profile, never a blank flag, and says why (not \"nothing is signed in\", and not \"the list was unreadable\")")
        check(route(other, unusable, state, nil) == .chrome(profileDirectory: ownFolder, source: .ownedSignedInAddressUnusable)
              && route(other, unusable, nil, nil) == .chrome(profileDirectory: ownFolder, source: .ownedSignedInAddressUnusable),
              "chrome order: the same reason whether the list was read with no match or not read at all, for \(unusable.debugDescription)")
    }
    // The fallback is the same folder as before; only the reason differs from a plain "nothing signed in".
    S.set(.chromeProfileSignedInAs, forAccountID: "unusable-folder", defaults: defaults)
    S.clear(forAccountID: "plain-none", defaults: defaults)
    check(route("unusable-folder", "no-at-sign", nil, nil) == .chrome(profileDirectory: "PixelSwitch no-at-sign", source: .ownedSignedInAddressUnusable)
          && route("plain-none", "no-at-sign", nil, nil) == .chrome(profileDirectory: "PixelSwitch no-at-sign", source: .owned)
          && ChromeUsageLaunch.Source.ownedSignedInAddressUnusable != .owned,
          "chrome order: with the same address and folder, a signed-in-as choice reports the unusable address and an account with no choice reports owned")
    // 3. With no stored choice and no match, its own profile.
    S.clear(forAccountID: id, defaults: defaults)
    defaults.removeObject(forKey: PixelSwitchChromeProfile.defaultsKey)
    check(route(id, "new@example.com", state, nil) == .chrome(profileDirectory: "PixelSwitch new@example.com", source: .owned)
          && route(id, "new@example.com", nil, .accessDenied) == .chrome(profileDirectory: "PixelSwitch new@example.com", source: .ownedProfileListUnreadable(.accessDenied)),
          "chrome order: with no stored choice and no match the account's own PixelSwitch profile opens")
    // A damaged stored text is no choice.
    defaults.removeObject(forKey: PixelSwitchChromeProfile.defaultsKey)
    defaults.set([PixelSwitchChromeProfile.normalizedAccountID(id): "directory:../x"], forKey: S.defaultsKey)
    check(route(id, "new@example.com", nil, .accessDenied) == .chrome(profileDirectory: "PixelSwitch new@example.com", source: .ownedProfileListUnreadable(.accessDenied))
          && route(id, "beta@example.org", state, nil) == .chrome(profileDirectory: "Profile 1", source: .matched),
          "chrome order: a damaged stored choice is no choice: the list's match wins, else the own profile opens")
    // Chrome missing: the default browser, whatever is stored.
    for stored in [Ch.ownProfile, .chromeProfileSignedInAs, .directory("Profile 1")] {
        S.set(stored, forAccountID: id, defaults: defaults)
        check(C.route(accountID: id, email: "new@example.com", localState: state, chromeInstalled: false, defaults: defaults) == .defaultBrowser,
              "chrome order: with Chrome not installed the default browser opens, with the choice \(stored)")
    }

    // --profile-email is never sent without the stored choice that asks for it, and an explicit stored
    // choice is always honoured: try every way the click can arrive.
    let emails = ["", "   ", "beta@example.org", "BETA@example.org", "new@example.com", "no-at-sign", "a b@x.com", "x;y@z.com"]
    let locals: [(Data?, ChromeLocalState.ReadFailure?)] = [(state, nil), (nil, nil), (nil, .accessDenied), (nil, .notFound), (nil, .other), (Data(), nil), (chromeJSON("{{{"), nil)]
    var sentEmail: [String] = []
    var notHonoured: [String] = []
    var routes = 0
    for stored in [nil, Ch.ownProfile, .directory("Default"), .directory("Profile 1")] as [Ch?] {
        let key = "never-\(String(describing: stored))"
        defaults.removeObject(forKey: S.defaultsKey)
        defaults.removeObject(forKey: PixelSwitchChromeProfile.defaultsKey)
        if let stored { S.set(stored, forAccountID: key, defaults: defaults) }
        for email in emails { for (local, failure) in locals {
            let r = C.route(accountID: key, email: email, localState: local, chromeInstalled: true, localStateFailure: failure, defaults: defaults)
            routes += 1
            if case .chromeSignedInAs = r { sentEmail.append("\(String(describing: stored)) \(email.debugDescription)") }
            switch stored {
            case .directory(let name)?:
                if r != .chrome(profileDirectory: name, source: .chosenDirectory) { notHonoured.append("\(name) \(email.debugDescription) \(r)") }
            case .ownProfile?:
                // the first click saves the folder named from the first address; it never changes after that
                let wanted = PixelSwitchChromeProfile.directoryName(forEmail: emails[0], accountID: key)
                if r != .chrome(profileDirectory: wanted, source: .chosenOwn) { notHonoured.append("own \(email.debugDescription) \(r)") }
            default:
                break
            }
        } }
    }
    check(routes == 4 * emails.count * locals.count && sentEmail.isEmpty,
          "chrome order: across \(routes) clicks with no stored signed-in-as choice, --profile-email is never chosen", sentEmail.joined(separator: " | "))
    check(notHonoured.isEmpty, "chrome order: across the same clicks a stored own profile or folder is always the one opened, whatever Chrome's list shows",
          notHonoured.joined(separator: " | "))
    check(S.set(.chromeProfileSignedInAs, forAccountID: "only", defaults: defaults)
          && C.route(accountID: "only", email: "beta@example.org", localState: nil, chromeInstalled: true, defaults: defaults) == .chromeSignedInAs(email: "beta@example.org"),
          "chrome order: the one stored choice that asks for it does send it")
}

@MainActor private func chromeOwnedDirectoryRobustnessTests() {
    typealias P = PixelSwitchChromeProfile
    let suite = "ai.pixelventures.pixelswitch.tests.chromeowned.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "chrome owned: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }
    let a = "AAAAAAAA-1111-4111-8111-AAAAAAAAAAAA", b = "BBBBBBBB-2222-4222-8222-BBBBBBBBBBBB", c = "CCCCCCCC-3333-4333-8333-CCCCCCCCCCCC"
    // One entry of another type never hides the other accounts' saved folders.
    defaults.set([a.lowercased(): "PixelSwitch a@example.com", b.lowercased(): 42, c.lowercased(): ["nested": "x"]] as [String: Any], forKey: P.defaultsKey)
    check(P.savedDirectory(forAccountID: a, defaults: defaults) == "PixelSwitch a@example.com"
          && P.savedDirectory(forAccountID: b, defaults: defaults) == nil && P.savedDirectory(forAccountID: c, defaults: defaults) == nil,
          "chrome owned: with a number and a dictionary among the entries, the String entries are still read and the bad ones count as no folder")
    check(P.storedDirectories(defaults: defaults) == [a.lowercased(): "PixelSwitch a@example.com"],
          "chrome owned: the folders read are exactly the String entries")
    // Making a folder for another account next to the bad entries keeps the saved one (its profile holds a sign-in) and drops only the bad ones.
    let made = P.directory(forAccountID: b, email: "b@example.com", defaults: defaults)
    check(made == "PixelSwitch b@example.com" && P.savedDirectory(forAccountID: a, defaults: defaults) == "PixelSwitch a@example.com"
          && P.savedDirectory(forAccountID: b, defaults: defaults) == "PixelSwitch b@example.com"
          && (defaults.dictionary(forKey: P.defaultsKey) as? [String: String]) == [a.lowercased(): "PixelSwitch a@example.com", b.lowercased(): "PixelSwitch b@example.com"],
          "chrome owned: making a folder next to a bad entry never drops another account's saved folder, and the bad entries go", "\(made)")
    // A saved folder is returned as it was and never remade.
    check(P.directory(forAccountID: a, email: "other@example.com", defaults: defaults) == "PixelSwitch a@example.com",
          "chrome owned: an account's saved folder never changes, even with a different address")
    // A value that is not a usable PixelSwitch folder name is still no folder; a value of the wrong type for the whole key is replaced.
    defaults.set([a.lowercased(): "Profile 1"], forKey: P.defaultsKey)
    check(P.savedDirectory(forAccountID: a, defaults: defaults) == nil, "chrome owned: a saved name that is not a PixelSwitch folder is no folder")
    defaults.set("not a dictionary", forKey: P.defaultsKey)
    check(P.savedDirectory(forAccountID: a, defaults: defaults) == nil && P.directory(forAccountID: a, email: "a@example.com", defaults: defaults) == "PixelSwitch a@example.com",
          "chrome owned: a value of the wrong type is no folder and is replaced by the next one made")
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
          && page.contains("process.arguments = arguments")
          && page.contains("launchChrome(arguments: ChromeUsageLaunch.arguments(profileDirectory: directory), tag: tag)")
          && page.contains("launchChrome(arguments: ChromeUsageLaunch.arguments(profileEmail: address), tag: tag)"),
          "chrome pins: one Process, /usr/bin/open, with the tested argument arrays")
    check(all.components(separatedBy: "\"--profile-email=\\(email)\"").count == 2 && !page.contains("--profile-email")
          && all.components(separatedBy: "arguments(profileEmail:").count == 2,
          "chrome pins: the address flag is written once, in the launch file, and used once, for the .chromeSignedInAs route")
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

    // A refused read is told apart from "no profile matched", in the log and in the read.
    check(page.contains("ChromeLocalState.readFailure(from: error)") && page.contains("localStateFailure: read.failure")
          && page.contains("case .ownedProfileListUnreadable(.accessDenied):") && page.contains("macOS did not let PixelSwitch read Chrome's profile list"),
          "chrome pins: a read macOS refused is logged as that, not as no profile being signed in")
    check(page.contains("case .ownedSignedInAddressUnusable:") && page.contains("the address is blank or not usable"),
          "chrome pins: a signed-in-as choice with an unusable address is logged as that, not as no profile being signed in")
    check(page.contains("ChromeProfileChoiceStore.saved(forAccountID: accountID) == nil ? readLocalState() : (data: nil, failure: nil)"),
          "chrome pins: an account with a stored choice never has Chrome's profile list read at all")
    check(page.components(separatedBy: "try? Data(contentsOf").count == 1,
          "chrome pins: the read is never a try? that hides why it failed")

    // The log never carries an address.
    let logLines = page.components(separatedBy: "\n").filter { $0.contains("chromeLog.") }
    let leaks = logLines.filter { line in ["email", "Email", "displayEmail", "\\(accountID)", "absoluteString"].contains { line.contains($0) } }
    check(logLines.count >= 6 && leaks.isEmpty, "chrome pins: log lines name the account by its short id and never an address (\(logLines.count) lines)",
          leaks.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: " | "))

    // The profile choice control: plain inputs, reports what was picked, opens nothing. The tab keeps it through the tested store.
    let control = (try? String(contentsOfFile: "PixelSwitch/Views/Components/ChromeProfileChoice.swift", encoding: .utf8)) ?? ""
    let tab = (try? String(contentsOfFile: "PixelSwitch/Views/SettingsAccountsTab.swift", encoding: .utf8)) ?? ""
    check(control.contains("struct ChromeProfileChoiceControl: View") && control.contains("let choice: ChromeProfileChoice?")
          && control.contains("let address: String") && control.contains("let onChoose: @MainActor (ChromeProfileChoice?) -> Void")
          && !control.contains("@AppStorage") && !control.contains("@State") && !control.contains("let account: Account") && !control.contains("EmailDisplay"),
          "chrome pins: the profile choice control takes the kept choice, the address and one closure, and holds no state of its own")
    check(tab.contains("ChromeProfileChoiceStore.saved(forAccountID: account.id.uuidString)")
          && tab.contains("ChromeProfileChoiceStore.apply(choice, forAccountID: account.id.uuidString)")
          && tab.contains("chromeAddress: account.displayEmail(obfuscated: maskEmails)"),
          "chrome pins: Settings reads the kept choice by account id, keeps what is picked (or forgets it for Automatic) and shows the address through the masking rule")
    check(!control.contains("ChromeProfileChoiceStore.choice("), "chrome pins: the control never shows the stored own choice for an account that has none: nothing kept is Automatic")
    // Nothing kept is its own state, labelled Automatic, and picking the own profile is a different, kept choice.
    check(control.contains("case nil: return automaticTitle()") && control.contains(".tag(ChromeProfileChoice?.none)")
          && control.contains(".tag(Optional(ChromeProfileChoice.ownProfile))") && control.contains(".tag(Optional(ChromeProfileChoice.chromeProfileSignedInAs))")
          && control.contains("String(localized: \"Automatic (its own PixelSwitch profile)\", bundle: L10n.bundle)"),
          "chrome pins: the menu has Automatic (nothing kept), its own profile (kept as own) and signed in as, each its own item")
    check(tab.contains("chromeChoicesRevision += 1") && !tab.contains("ChromeProfileChoiceStore.set(") && !tab.contains("ChromeProfileChoiceStore.clear("),
          "chrome pins: every pick goes through the store's apply, and any pick redraws the rows")
    // The rows read the kept choices again when the tab appears (and when the app comes forward), so a defaults write from outside shows.
    check(tab.contains(".onAppear { chromeChoicesRevision += 1 }") && tab.contains("NSApplication.didBecomeActiveNotification")
          && tab.contains("_ = chromeChoicesRevision"),
          "chrome pins: the Accounts tab reloads the kept choices when it appears and when the app comes forward")
    let controlActs = ["Process()", "NSWorkspace", "ChromeUsagePage.", "FileManager", "Local State", "UserDefaults.standard"].filter { control.contains($0) }
    check(controlActs.isEmpty, "chrome pins: the profile choice control opens nothing and reads no file; the store is the only thing it touches", controlActs.joined(separator: ", "))
    check(!control.contains("\u{2014}"), "chrome pins: no em dash in the control")
    let doc = (try? String(contentsOfFile: "PixelSwitch/ChromeProfiles/ChromeProfileMatcher.swift", encoding: .utf8)) ?? ""
    check(doc.contains("defaults write ai.pixelventures.pixelswitch ai.pixelventures.pixelswitch.chromeProfiles.choices")
          && doc.contains("-dict-add <account-id-lower-case> 'directory:Profile 1'"),
          "chrome pins: the store documents the exact defaults write form")
    check(doc.contains("storedDirectories(defaults: defaults)[normalizedAccountID(accountID)]") && doc.contains("var all = storedDirectories(defaults: defaults)")
          && !doc.contains("as? [String: String]") && !doc.contains("static func choice(forAccountID"),
          "chrome pins: both dictionaries (the choices and the saved PixelSwitch folders) are read as String values only, and the unused choice(forAccountID:) is gone")
    check(doc.contains("The account-id keys must be lower case") && doc.contains("an upper case key is never found"),
          "chrome pins: the documented defaults form says the account-id keys are lower case")

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
    let choiceKeys = ["Its own PixelSwitch Chrome profile", "The Chrome profile signed in as %@", "The Chrome profile folder %@",
                      "Choose this only if Chrome has a profile signed in with this address. Otherwise Chrome opens whichever profile you used last.",
                      "Chrome profile for Limit resets", "Automatic (its own PixelSwitch profile)",
                      "Chrome profile for Limit resets: %@", "Chrome profile for Limit resets of %@"]
    check(choiceKeys.allSatisfy { key in tables.allSatisfy { ($0[key] ?? "").isEmpty == false } },
          "chrome strings: the eight profile choice strings are in all five tables")
    check(choiceKeys.allSatisfy { tables[0][$0] == $0 } && choiceKeys.allSatisfy { key in tables.dropFirst().allSatisfy { $0[key] != key } },
          "chrome strings: their English line is the key, and de, fr, ja and zh-Hans are translated, not copied")
    check(choiceKeys.allSatisfy { key in tables.allSatisfy { !($0[key] ?? "").contains("\u{2014}") } },
          "chrome strings: no em dash in any profile choice string")
    check(["The Chrome profile signed in as %@", "The Chrome profile folder %@", "Chrome profile for Limit resets: %@", "Chrome profile for Limit resets of %@"]
            .allSatisfy { key in tables.allSatisfy { ($0[key] ?? "").components(separatedBy: "%@").count == 2 } },
          "chrome strings: the four strings that take a name, an address or a choice keep exactly one %@ in every language")
    check(tables.allSatisfy { !($0["Automatic (its own PixelSwitch profile)"] ?? "").contains("%") }
          && tables.allSatisfy { ($0["Chrome profile for Limit resets: %@"] ?? "").contains("Chrome") && ($0["Chrome profile for Limit resets of %@"] ?? "").contains("Chrome") },
          "chrome strings: Automatic has no placeholder, and both hover and spoken lines keep the name Chrome in every language")
    check(tables.allSatisfy { $0["Open this account's claude.ai usage page to use a reset"] == nil },
          "chrome strings: the old help text (the window's) is gone from all five tables")

    // The five tables carry the same keys, and no key is written twice in any of them (a merge of several lanes can do both).
    let keyPattern = try? NSRegularExpression(pattern: "^\"((?:[^\"\\\\]|\\\\.)*)\"\\s*=", options: [.anchorsMatchLines])
    var rawKeys: [[String]] = []
    for language in languages {
        let text = (try? String(contentsOfFile: "PixelSwitch/\(language).lproj/Localizable.strings", encoding: .utf8)) ?? ""
        let range = NSRange(text.startIndex..., in: text)
        rawKeys.append((keyPattern?.matches(in: text, range: range) ?? []).compactMap { Range($0.range(at: 1), in: text).map { String(text[$0]) } })
    }
    let duplicated = zip(languages, rawKeys).flatMap { language, keys in
        Dictionary(grouping: keys, by: { $0 }).filter { $0.value.count > 1 }.keys.map { "\(language): \($0)" }
    }
    check(rawKeys.allSatisfy { $0.count > 300 } && duplicated.isEmpty, "chrome strings: no key is written twice in any of the five tables (\(rawKeys.map(\.count)))", duplicated.joined(separator: " | "))
    let keySets = rawKeys.map { Set($0) }
    check(keySets.dropFirst().allSatisfy { $0 == keySets[0] } && keySets.allSatisfy { $0.count == tables[0].count },
          "chrome strings: the five tables have identical key sets", zip(languages, keySets).map { "\($0.0): \($0.1.symmetricDifference(keySets[0]).sorted().joined(separator: ", "))" }.joined(separator: " | "))

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
