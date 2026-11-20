import SwiftUI

/// The bottom line of a Usage card: how old the numbers are, the "Limit resets"
/// button beside it, and on the right what clicking would do or what is going on.
///
/// The line has one fixed rule about who gives way when it is short of room, and
/// the rule lives here so the app and the screenshot harness draw the same thing:
///
/// 1. The age ("Updated ... ago") is never truncated and never shrunk, in any
///    language, hovered or not.
/// 2. The hover hint ("Double-click to switch") gives way first: it is dropped.
/// 3. Then the button goes icon-only (its tooltip and spoken label stay).
///
/// `status` (the "Switching…" progress, the "Manual only" note) is never dropped;
/// it is the only piece that may compress, and only in the last, worst case.
///
/// `ViewThatFits` picks the first variant whose natural width fits the card, so
/// the choice follows the real text widths of the language in use.
///
/// The choice is only as steady as the age text it measures. An age that counts
/// up by the second changes width at any moment and the row would flip variants at
/// a fit boundary, so the age handed in here is the whole-minute `FooterAge`
/// (`LiveFooterAge`): its text, and so the chosen variant, holds for a full minute.
struct CardFooterRow<Age: View, Reset: View, ResetIcon: View, Status: View, Hint: View>: View {
    /// "Updated ... ago", or nothing while the reading is fresh.
    let age: Age
    /// The button with its words.
    let reset: Reset
    /// The same button, icon only.
    let resetIconOnly: ResetIcon
    /// Kept in every variant: "Switching…", "Manual only".
    let status: Status
    /// Dropped first: the hover hint.
    let hint: Hint

    var body: some View {
        ViewThatFits(in: .horizontal) {
            line(reset, hint: hint)
            line(reset, hint: EmptyView())
            line(resetIconOnly, hint: EmptyView())
        }
        .frame(minHeight: 14)
    }

    private func line<R: View, H: View>(_ button: R, hint: H) -> some View {
        HStack(spacing: 6) {
            age.fixedSize()
            button
            Spacer(minLength: 4)
            status
            hint.fixedSize()
        }
    }
}
