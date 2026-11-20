import SwiftUI

/// The one control that says which Chrome profile an account's Limit resets button opens.
///
/// A compact menu for one account. It reads and writes the account's stored choice
/// (`ChromeProfileChoiceStore`, in UserDefaults, keyed by the account's id):
/// - "Its own PixelSwitch Chrome profile": a profile of PixelSwitch's own, made for this
///   account and signed out until the person signs in to claude.ai in it once. It is what the
///   menu shows while nothing is stored; picking it stores it, and from then on it is honoured
///   even if Chrome's profile list shows a profile signed in to the address.
/// - "The Chrome profile signed in as <address>": Chrome picks the profile signed in with the
///   account's address. Only right when Chrome has one; if it has none, Chrome opens whichever
///   profile was used last, so the control says so out loud (a menu bar tooltip is unreliable,
///   so the warning is a line of text under the menu while this option is chosen).
/// - "The Chrome profile folder <name>": shown only when that is what is stored (it is set from
///   a shell, see `ChromeProfileChoiceStore`); the control never makes one itself.
///
/// The address is shown through `Account.displayEmail`, so it is masked when masking is on.
/// Nothing here opens Chrome: the Limit resets button does that (`ChromeUsagePage`). Wiring
/// it into a row is a separate step.
struct ChromeProfileChoiceControl: View {
    let account: Account

    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @State private var choice: ChromeProfileChoice

    init(account: Account) {
        self.account = account
        _choice = State(initialValue: ChromeProfileChoiceStore.choice(forAccountID: account.id.uuidString))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Picker(selection: selection) {
                Text(verbatim: Self.ownTitle())
                    .tag(ChromeProfileChoice.ownProfile)
                Text(verbatim: Self.signedInTitle(address: account.displayEmail(obfuscated: maskEmails)))
                    .tag(ChromeProfileChoice.chromeProfileSignedInAs)
                if case .directory(let name) = choice {
                    Text(verbatim: Self.directoryTitle(name: name))
                        .tag(choice)
                }
            } label: {
                Text(verbatim: Self.pickerLabel())
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .fixedSize()
            .help(Self.signedInHelp())
            .accessibilityLabel(Self.pickerLabel())

            if choice == .chromeProfileSignedInAs {
                Text(verbatim: Self.signedInHelp())
                    .font(.caption2)
                    .foregroundStyle(.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The picker's selection, written through to the stored choice.
    private var selection: Binding<ChromeProfileChoice> {
        Binding(
            get: { choice },
            set: { newValue in
                choice = newValue
                ChromeProfileChoiceStore.set(newValue, forAccountID: account.id.uuidString)
            }
        )
    }

    // MARK: - Words (in the app's chosen language)

    static func ownTitle() -> String {
        String(localized: "Its own PixelSwitch Chrome profile", bundle: L10n.bundle)
    }

    static func signedInTitle(address: String) -> String {
        String(format: String(localized: "The Chrome profile signed in as %@", bundle: L10n.bundle), address)
    }

    static func directoryTitle(name: String) -> String {
        String(format: String(localized: "The Chrome profile folder %@", bundle: L10n.bundle), name)
    }

    static func signedInHelp() -> String {
        String(localized: "Choose this only if Chrome has a profile signed in with this address. Otherwise Chrome opens whichever profile you used last.",
               bundle: L10n.bundle)
    }

    static func pickerLabel() -> String {
        String(localized: "Chrome profile for Limit resets", bundle: L10n.bundle)
    }
}
