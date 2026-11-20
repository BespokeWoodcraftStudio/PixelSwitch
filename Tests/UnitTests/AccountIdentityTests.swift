// The popover header's name lines, and the rule that the address is never printed twice
// (Account.identityLines in PixelSwitch/Models/Account.swift).
//
// Anthropic names a personal account's organization "<address>'s Organization", so a
// header that led with the organization name and then printed the address under it
// read "ahmed@pixelventures.ai's Organization" over "ahmed@pixelventures.ai". The rule:
// a custom label leads with the address under it; otherwise a REAL organization name
// leads with the address under it; otherwise the address alone, on one line. Which
// lines show is decided on the real values, and masking only changes the text.
//
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from main.swift.
// Uses the global `check(_:_:_:)` defined there.
import Foundation

private let personal = "ahmed@pixelventures.ai"
private let teamName = "Bespoke Woodcraft Studio"

/// An account as the app stores one: `displayName` is `orgName ?? email` unless a test says otherwise.
private func account(_ email: String = personal, org: String? = nil, name: String? = nil, label: String? = nil) -> Account {
    Account(email: email, displayName: name ?? org ?? email, orgName: org, customLabel: label)
}

private func lines(_ primary: String, _ secondary: String?) -> AccountIdentityLines {
    AccountIdentityLines(primary: primary, secondary: secondary)
}

/// How many times the address appears across both lines, ignoring case.
private func appearances(of address: String, in shown: AccountIdentityLines) -> Int {
    let all = (shown.primary + "\n" + (shown.secondary ?? "")).lowercased()
    return all.components(separatedBy: address.lowercased()).count - 1
}

private func identitySource(_ path: String) -> String {
    (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
}

@MainActor func runAccountIdentityTests() {
    accountIdentityDefaultNameTests()
    accountIdentityRealNameTests()
    accountIdentityLabelTests()
    accountIdentityMaskingTests()
    accountIdentitySourcePins()
}

// MARK: - Anthropic's default name, a missing name, the address as the name

@MainActor private func accountIdentityDefaultNameTests() {
    let alone = lines(personal, nil)

    check(account(org: "ahmed@pixelventures.ai's Organization").identityLines(obfuscated: false) == alone,
          "identity: the default organization name for this address shows the address once")
    check(account(org: "ahmed@pixelventures.ai\u{2019}s Organization").identityLines(obfuscated: false) == alone,
          "identity: the default name with a curly apostrophe shows the address once")
    check(account(org: "ahmed@pixelventures.ai\u{2018}s Organization").identityLines(obfuscated: false) == alone,
          "identity: the default name with a left single quote shows the address once")
    check(account(org: "AHMED@PixelVentures.AI'S ORGANIZATION").identityLines(obfuscated: false) == alone,
          "identity: the default name in a different case shows the address once")
    check(account(org: "  ahmed@pixelventures.ai's   Organization \n").identityLines(obfuscated: false) == alone,
          "identity: the default name with extra spaces shows the address once")

    check(account(org: nil).identityLines(obfuscated: false) == alone,
          "identity: no organization name shows the address once")
    check(account(org: "", name: "").identityLines(obfuscated: false) == alone,
          "identity: an empty organization name and an empty saved name show the address once")
    check(account(org: "   ").identityLines(obfuscated: false) == alone,
          "identity: a blank organization name shows the address once")
    check(account(org: personal).identityLines(obfuscated: false) == alone,
          "identity: an organization name that is the address shows the address once")
    check(account(org: "Ahmed@PixelVentures.ai ").identityLines(obfuscated: false) == alone,
          "identity: an organization name that is the address in another case shows the address once")

    // Saved before `orgName` was stored: only `displayName` holds the name.
    check(account(org: nil, name: "ahmed@pixelventures.ai's Organization").identityLines(obfuscated: false) == alone,
          "identity: a saved name that is the default, with no organization name stored, shows the address once")

    // The first name that is not empty decides, so a live default name is not rescued by a stale saved one.
    check(account(org: "ahmed@pixelventures.ai's Organization", name: teamName).identityLines(obfuscated: false) == alone,
          "identity: the live organization name decides over the name the account was saved with")

    // The check itself.
    check(Account.isPlaceholderOrganizationName("ahmed@pixelventures.ai's Organization", forEmail: personal),
          "identity: isPlaceholderOrganizationName accepts the default")
    check(!Account.isPlaceholderOrganizationName("ahmed@pixelventures.ai's Organizations", forEmail: personal)
          && !Account.isPlaceholderOrganizationName("ahmed@pixelventures.ai Organization", forEmail: personal)
          && !Account.isPlaceholderOrganizationName("Organization", forEmail: personal),
          "identity: isPlaceholderOrganizationName does not accept near misses")
}

// MARK: - A real organization name

@MainActor private func accountIdentityRealNameTests() {
    check(account(org: teamName).identityLines(obfuscated: false) == lines(teamName, personal),
          "identity: a real organization name leads and the address sits under it")
    check(account(org: "  \(teamName)  ").identityLines(obfuscated: false) == lines(teamName, personal),
          "identity: a real organization name is shown without its edge spaces")
    check(account(org: nil, name: teamName).identityLines(obfuscated: false) == lines(teamName, personal),
          "identity: a real name saved before orgName was stored still leads")
    check(account(org: teamName, name: personal).identityLines(obfuscated: false) == lines(teamName, personal),
          "identity: a real organization name wins over a saved name that is the address")

    // Another address's default is a real name here, not this account's placeholder.
    check(account(org: "someone-else@example.com's Organization").identityLines(obfuscated: false)
            == lines("someone-else@example.com's Organization", personal),
          "identity: a default-looking name for a different address is a real name, so both lines show")
    check(account(personal, org: "ahmed@pixelventures.ai's Organization").identityLines(obfuscated: false) == lines(personal, nil)
          && account("other@pixelventures.ai", org: "ahmed@pixelventures.ai's Organization").identityLines(obfuscated: false)
            == lines("ahmed@pixelventures.ai's Organization", "other@pixelventures.ai"),
          "identity: the same name is a placeholder for its own address only")

    // Unchanged behaviour for a real name: the lines are what the old header printed.
    let real = account(org: teamName)
    check(real.identityLines(obfuscated: false).primary == real.effectiveDisplayName(obfuscated: false)
          && real.identityLines(obfuscated: false).secondary == real.displayEmail(obfuscated: false),
          "identity: a real organization name gives the old header's two lines")
}

// MARK: - A custom label

@MainActor private func accountIdentityLabelTests() {
    check(account(org: "ahmed@pixelventures.ai's Organization", label: "Work").identityLines(obfuscated: false) == lines("Work", personal),
          "identity: a custom label leads with the address under it, over a default organization name")
    check(account(org: teamName, label: "Work").identityLines(obfuscated: false) == lines("Work", personal),
          "identity: a custom label leads with the address under it, over a real organization name")
    check(account(org: nil, label: "Work").identityLines(obfuscated: false) == lines("Work", personal),
          "identity: a custom label leads with the address under it when there is no organization name")
    check(account(org: teamName, label: "").identityLines(obfuscated: false) == lines(teamName, personal),
          "identity: an empty custom label is no label")
    check(account(org: nil, label: "").identityLines(obfuscated: false) == lines(personal, nil),
          "identity: an empty custom label and no organization name shows the address once")
    check(account(label: "AHMED@pixelventures.ai").identityLines(obfuscated: false) == lines(personal, nil),
          "identity: a label that is only the address shows the address once")

    let labelled = account(org: teamName, label: "Work")
    check(labelled.identityLines(obfuscated: false).primary == labelled.effectiveDisplayName(obfuscated: false)
          && labelled.identityLines(obfuscated: false).secondary == labelled.displayEmail(obfuscated: false),
          "identity: a custom label gives the old header's two lines")
}

// MARK: - Masking

@MainActor private func accountIdentityMaskingTests() {
    let masked = personal.maskedAsEmailAddress()
    check(masked == "ahm*@*.ai", "identity: the mask this suite expects is the app's short form")

    let defaultName = account(org: "ahmed@pixelventures.ai's Organization").identityLines(obfuscated: true)
    check(defaultName == lines(masked, nil),
          "identity: with masking on, a default organization name gives the masked address once")

    check(account(org: nil).identityLines(obfuscated: true) == lines(masked, nil),
          "identity: with masking on, no organization name gives the masked address once")

    let real = account(org: teamName).identityLines(obfuscated: true)
    check(real == lines(teamName, masked),
          "identity: with masking on, a real organization name leads and the masked address sits under it")

    check(account(org: teamName, label: "Work").identityLines(obfuscated: true) == lines("Work", masked),
          "identity: with masking on, a custom label leads and the masked address sits under it")

    check(account(label: personal).identityLines(obfuscated: true) == lines(masked, nil),
          "identity: with masking on, a label that is only the address gives the masked address once")

    // An address inside a real name is masked the way the old header masked it.
    let embedded = account(org: "ahmed@pixelventures.ai Team")
    check(embedded.identityLines(obfuscated: true).primary == embedded.effectiveDisplayName(obfuscated: true)
          && embedded.identityLines(obfuscated: true).primary == "ahm*@*.ai Team",
          "identity: with masking on, an address inside a real name is masked as before")

    // Whatever the case, the real address never shows while masking is on, and never twice while it is off.
    let cases: [Account] = [
        account(org: "ahmed@pixelventures.ai's Organization"), account(org: nil), account(org: personal), account(org: teamName),
        account(org: teamName, label: "Work"), account(org: nil, label: "Work"), account(label: personal),
        account(org: "ahmed@pixelventures.ai\u{2019}s Organization"), account(org: nil, name: "ahmed@pixelventures.ai's Organization"),
    ]
    var leaked = 0, doubled = 0
    for each in cases {
        if appearances(of: personal, in: each.identityLines(obfuscated: true)) != 0 { leaked += 1 }
        if appearances(of: personal, in: each.identityLines(obfuscated: false)) > 1 { doubled += 1 }
        if appearances(of: masked, in: each.identityLines(obfuscated: true)) > 1 { doubled += 1 }
    }
    check(leaked == 0, "identity: with masking on the real address is in none of \(cases.count) cases")
    check(doubled == 0, "identity: the address, masked or not, is on one line only in all \(cases.count) cases")
}

// MARK: - Where it is used

@MainActor private func accountIdentitySourcePins() {
    let menu = identitySource("PixelSwitch/Views/MainMenuView.swift")
    let header = identitySource("PixelSwitch/Views/Components/AccountIdentityHeader.swift")
    let shots = identitySource("Tools/screenshots/make_screenshots.swift")
    let script = identitySource("Tests/run-unit-tests.sh")
    check(!menu.isEmpty && !header.isEmpty && !shots.isEmpty && !script.isEmpty,
          "identity pins: the sources are found from the repo root")

    // The popover header takes both lines from the one rule, never from the two accessors that
    // printed the address twice for a personal account.
    guard let start = menu.range(of: "private var headerView: some View {"),
          let end = menu.range(of: "// MARK: - Tab Bar", range: start.upperBound..<menu.endIndex) else {
        check(false, "identity pins: headerView is found in MainMenuView")
        return
    }
    let body = String(menu[start.lowerBound..<end.lowerBound])
    check(body.contains("account.identityLines(obfuscated: maskEmails)") && body.contains("AccountIdentityHeader("),
          "identity pins: the popover header takes its lines from identityLines")
    check(!body.contains("effectiveDisplayName(") && !body.contains("displayEmail("),
          "identity pins: the popover header no longer pairs the organization name with the address")

    check(header.contains("if let secondary = identity.secondary"),
          "identity pins: the header draws the second line only when there is one")

    check(shots.contains("AccountIdentityHeader(") && shots.contains(".identityLines(obfuscated:")
          && shots.contains("PixelSwitch/Views/Components/AccountIdentityHeader.swift"),
          "identity pins: the screenshot harness draws the app's AccountIdentityHeader from the real rule")
    check(script.contains("Tests/UnitTests/AccountIdentityTests.swift"),
          "identity pins: the unit harness compiles these tests")
}
