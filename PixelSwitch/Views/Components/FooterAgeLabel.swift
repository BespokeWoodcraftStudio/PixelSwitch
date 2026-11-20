import SwiftUI

/// What a Usage card's footer shows for the age of its numbers: a clock and one
/// line of text, never shrunk and never cut (`CardFooterRow` keeps it whole in
/// every variant). The screenshot harness draws this same view.
struct FooterAgeLabel: View {
    /// The whole sentence, already in the app's language ("Updated 4 min ago").
    let text: String

    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: "clock")
                .font(.caption2)
            Text(verbatim: text)
                .font(.caption2)
                .lineLimit(1)
        }
        .foregroundStyle(.textSecondary)
    }
}

/// The age of a reading, redrawn once a minute, on the minute (`FooterAge`).
///
/// A date text that counts up by itself changes width between two ticks of the
/// clock, and `CardFooterRow`'s `ViewThatFits` then flips between variants at a fit
/// boundary. This label's text is the same for every instant inside a minute, so
/// the footer's layout can change at most once a minute, when the age really
/// becomes a minute older.
struct LiveFooterAge: View {
    let sampledAt: Date

    var body: some View {
        TimelineView(FooterAgeSchedule(sampledAt: sampledAt)) { context in
            FooterAgeLabel(text: FooterAge.sentence(
                template: String(localized: "Updated %@ ago", bundle: L10n.bundle),
                since: sampledAt, now: context.date, locale: L10n.locale))
        }
    }
}

/// Redraws at the moment the reading turns a whole minute older and at no other
/// time, the same instants `FooterAge.nextChange` names.
struct FooterAgeSchedule: TimelineSchedule {
    let sampledAt: Date

    func entries(from startDate: Date, mode: TimelineScheduleMode) -> some Sequence<Date> {
        let sampledAt = sampledAt
        return sequence(first: startDate) { FooterAge.nextChange(since: sampledAt, after: $0) }
    }
}
