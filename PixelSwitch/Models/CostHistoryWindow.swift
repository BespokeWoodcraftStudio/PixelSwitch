import Foundation

/// How much cost history actually exists.
///
/// PixelSwitch parses a session file only while it has been touched inside the
/// usage history window (Settings, default 24 hours) and evicts the rest, which
/// is what keeps its memory small. The Costs tab must therefore never claim a
/// period it does not hold: before this, fixed "Last 7 Days" and "Last 30 Days"
/// totals showed the same figure on five days of data and implied a month of
/// history that was never kept.
enum CostHistoryWindow {
    /// Days from the oldest day with data to `today`, inclusive.
    /// 0 when there is no data at all; at least 1 whenever there is any.
    /// Dates are the `yyyy-MM-dd` strings the cost cache stores.
    static func spanDays(dates: [String], today: String, calendar: Calendar = .current) -> Int {
        guard let oldest = dates.min() else { return 0 }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = calendar.timeZone
        guard let oldestDate = formatter.date(from: oldest),
              let todayDate = formatter.date(from: today),
              let days = calendar.dateComponents([.day], from: oldestDate, to: todayDate).day
        else { return 1 }
        return max(1, days + 1)
    }
}
