import Foundation

/// The age line on a Usage card ("Updated 4 min ago"), made so that the footer's
/// layout choice holds still.
///
/// `CardFooterRow` picks its variant (button and hint, button only, icon only) by
/// the width of the age text. A text that counts up live (`Text(date, style:
/// .relative)` redraws every second, in proportional digits) changes width at any
/// second, so at a fit boundary the footer used to flip between variants as the
/// seconds ticked. Here the age is a whole number of minutes, and the text is a
/// pure function of that number. Between two minute boundaries every instant
/// gives the same text, so the width, and with it the variant, can change at most
/// once a minute and only when the age really passes into the next minute.
///
/// Pure and Foundation only, so the unit harness compiles it and the screenshot
/// harness draws the very same text the app does.
enum FooterAge {
    private static let minute: TimeInterval = 60
    /// A boundary computed as `sampledAt + 60 * k` can read back a hair under
    /// k minutes in floating point; this much slack keeps it at k.
    private static let tolerance: TimeInterval = 0.001
    private static let ceiling = 10_000_000

    /// Whole minutes between the reading and `now`. Never negative: a reading
    /// stamped after `now` (a clock that moved back) is 0 minutes old.
    static func minutes(since sampledAt: Date, now: Date) -> Int {
        let elapsed = now.timeIntervalSince(sampledAt)
        guard elapsed > 0 else { return 0 }
        return min(ceiling, Int(((elapsed + tolerance) / minute).rounded(.down)))
    }

    /// The first instant after `now` at which the text can differ: the moment the
    /// age reaches its next whole minute. Always later than `now`.
    static func nextChange(since sampledAt: Date, after now: Date) -> Date {
        let next = minutes(since: sampledAt, now: now) + 1
        return sampledAt.addingTimeInterval(TimeInterval(next) * minute)
    }

    /// The age as the language prints it: two units at most, never seconds, and
    /// cut rather than rounded, so an age is never shown older than it is. In
    /// English: "4 min", "12 hr, 59 min", "1 day, 23 hr". It is the wording the
    /// system's own relative date text uses, in every language the app ships.
    static func text(minutes: Int, locale: Locale) -> String {
        let whole = max(0, minutes)
        let days = whole / 1440
        let hours = (whole % 1440) / 60
        var parts = DateComponents()
        if days > 0 {
            parts.day = days
            parts.hour = hours
        } else {
            parts.hour = hours
            parts.minute = whole % 60
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = locale
        let formatter = DateComponentsFormatter()
        formatter.calendar = calendar
        formatter.unitsStyle = .short
        formatter.allowedUnits = [.day, .hour, .minute]
        formatter.maximumUnitCount = 2
        formatter.zeroFormattingBehavior = .dropAll
        return formatter.string(from: parts) ?? "\(whole)"
    }

    /// The age at `now`: the same text for every instant inside one minute.
    static func text(since sampledAt: Date, now: Date, locale: Locale) -> String {
        text(minutes: minutes(since: sampledAt, now: now), locale: locale)
    }

    /// The whole line, "Updated 4 min ago", from the localized template that holds a
    /// single %@ for the age.
    static func sentence(template: String, since sampledAt: Date, now: Date, locale: Locale) -> String {
        String(format: template, text(since: sampledAt, now: now, locale: locale))
    }
}
