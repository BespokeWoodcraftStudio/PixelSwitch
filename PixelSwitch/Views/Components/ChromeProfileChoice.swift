import SwiftUI

/// The one control that says which Chrome profile an account's Limit resets button opens.
///
/// The items of a menu for one account (the Settings row opens it from its Chrome profile icon, see
/// `SettingsAccountRow`). Plain inputs and one closure, with no state of its own and no property wrappers,
/// so the same view is what the app shows and what the offscreen renders draw. The Settings tab
/// reads the account's kept choice (`ChromeProfileChoiceStore.saved(forAccountID:)`, in UserDefaults
/// keyed by the account's id) each time it draws, hands it in as `choice`, and writes what is picked
/// back through `ChromeProfileChoiceStore` (`set`, or `clear` for Automatic).
///
/// What each item means, and what is kept for it:
/// - "Automatic (a matching Chrome profile, else its own PixelSwitch profile)": nothing is kept (`choice` is nil). Limit resets uses a
///   Chrome profile Chrome's own list shows signed in to the account's address when it can read that
///   list, else a profile of PixelSwitch's own for the account. It is what an account starts with, and
///   it is a state of its own: it is NOT the same as picking the next item.
/// - "Its own PixelSwitch Chrome profile": a profile of PixelSwitch's own, made for this account and
///   signed out until the person signs in to claude.ai in it once. Picking it keeps `own`, and from then
///   on it is honoured even if Chrome's profile list shows a profile signed in to the address.
/// - "The Chrome profile signed in as <address>": Chrome picks the profile signed in with the account's
///   address. Only right when Chrome has one; if it has none, Chrome opens whichever profile was used
///   last, so the row says so out loud while this is chosen (a menu bar tooltip is unreliable, so the
///   warning is a line of text in the row, `signedInHelp`).
/// - "The Chrome profile folder <name>": shown only when that is what is kept (it is set from a shell,
///   see `ChromeProfileChoiceStore`); the control never makes one itself.
///
/// The address is the account's address as shown (`Account.displayEmail`), so it is masked when
/// masking is on. Nothing here opens Chrome: the Limit resets button does that (`ChromeUsagePage`).
struct ChromeProfileChoiceControl: View {
    /// What is kept for the account; nil while nothing is kept (Automatic).
    let choice: ChromeProfileChoice?
    /// The account's address as shown, for the signed-in-as item.
    let address: String
    /// Called with what was picked: nil for Automatic (the kept choice is forgotten).
    let onChoose: @MainActor (ChromeProfileChoice?) -> Void

    var body: some View {
        Picker(selection: selection) {
            Text(verbatim: Self.automaticTitle())
                .tag(ChromeProfileChoice?.none)
            Text(verbatim: Self.ownTitle())
                .tag(Optional(ChromeProfileChoice.ownProfile))
            Text(verbatim: Self.signedInTitle(address: address))
                .tag(Optional(ChromeProfileChoice.chromeProfileSignedInAs))
            if case .directory(let name)? = choice {
                Text(verbatim: Self.directoryTitle(name: name))
                    .tag(choice)
            }
        } label: {
            Text(verbatim: Self.pickerLabel())
        }
        .pickerStyle(.inline)
        .labelsHidden()
    }

    /// The picker's selection, written through to `onChoose`.
    private var selection: Binding<ChromeProfileChoice?> {
        Binding(get: { choice }, set: { newValue in onChoose(newValue) })
    }

    // MARK: - Words (in the app's chosen language)

    /// What the menu and the hover label call a choice (nil is Automatic).
    static func title(for choice: ChromeProfileChoice?, address: String) -> String {
        switch choice {
        case nil: return automaticTitle()
        case .ownProfile?: return ownTitle()
        case .chromeProfileSignedInAs?: return signedInTitle(address: address)
        case .directory(let name)?: return directoryTitle(name: name)
        }
    }

    /// "Chrome profile for Limit resets: <the choice>", the Chrome profile icon's hover label.
    static func hoverText(for choice: ChromeProfileChoice?, address: String) -> String {
        String(format: String(localized: "Chrome profile for Limit resets: %@", bundle: L10n.bundle), title(for: choice, address: address))
    }

    /// What VoiceOver reads for the icon, naming the account.
    static func spokenLabel(accountName: String) -> String {
        String(format: String(localized: "Chrome profile for Limit resets of %@", bundle: L10n.bundle), accountName)
    }

    static func automaticTitle() -> String {
        String(localized: "Automatic (a matching Chrome profile, else its own PixelSwitch profile)", bundle: L10n.bundle)
    }

    static func ownTitle() -> String {
        String(localized: "Its own PixelSwitch Chrome profile", bundle: L10n.bundle)
    }

    static func signedInTitle(address: String) -> String {
        String(format: String(localized: "The Chrome profile signed in as %@", bundle: L10n.bundle), address)
    }

    static func directoryTitle(name: String) -> String {
        String(format: String(localized: "The Chrome profile folder %@", bundle: L10n.bundle), name)
    }

    /// The warning for the signed-in-as choice: shown in the row while it is chosen, and as its tooltip.
    static func signedInHelp() -> String {
        String(localized: "Choose this only if Chrome has a profile signed in with this address. Otherwise Chrome opens whichever profile you used last.",
               bundle: L10n.bundle)
    }

    static func pickerLabel() -> String {
        String(localized: "Chrome profile for Limit resets", bundle: L10n.bundle)
    }
}
