import Foundation

/// Whether addresses are masked on screen.
///
/// PixelSwitch used to mask every address by default, so a card read
/// `cla*@*.com` and two accounts on the same domain were told apart by
/// guessing. The founder's verdict, 2026-09-22: *"I want it to show the whole
/// email address because then I know exactly which account is which, not
/// partial. There's no reason to hide part of the email address."* So the app
/// now shows addresses in full and masking is something you turn ON.
///
/// **The key is new on purpose.** The old one, `showFullEmail`, was written to
/// disk as `false` on installs that never touched it, because `false` was the
/// default. Reusing it would have made every existing install keep masking and
/// the change would have reached nobody. A new key means the new default
/// applies everywhere, and anyone who wants masking turns it on once.
///
/// Masking is still worth having. It is what kept real addresses out of the
/// screenshots that get shared around, so it stays one switch away in
/// Settings rather than being deleted.
enum EmailDisplay {
    static let key = "maskEmailAddresses"

    /// False unless someone has deliberately asked for masking.
    static var isMasked: Bool {
        UserDefaults.standard.object(forKey: key) as? Bool ?? false
    }
}
