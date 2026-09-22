import SwiftUI

/// One limit's row on an account's Usage card.
///
/// Which limit a row is and how full it is are shown by different things, on
/// purpose. The chip, its symbol, the label and the row's tint are the limit's
/// identity and never change with usage; the bar fill and the percentage keep
/// their own meaning and turn red as a limit runs out. Before this, a card's
/// three bars were coloured only by how full they were, so Weekly and Fable
/// were both blue, and both red once they were nearly out.
struct UsageLimitRow: View {
    let kind: LimitBarKind
    /// "Session", "Weekly", "Fable".
    let title: LocalizedStringKey
    /// 0–100, how much of the limit is used.
    let utilization: Double
    /// Already-formatted countdown, e.g. "1 hr 39 min"; nil hides the reset text.
    let resetText: String?
    /// True when `resetText` is a weekday and time ("Mon 11:00 AM"), which reads
    /// "Resets Mon 11:00 AM" rather than "Resets in Mon 11:00 AM".
    var resetIsAbsolute: Bool = false
    /// The limit's identity colour (never the low-remaining red).
    let identityColor: Color
    /// The bar's fill colour, which does follow how full the limit is.
    let fillColor: Color

    private var clamped: Double { min(max(utilization, 0), 100) }
    private var percentLeft: Int { Int((100 - clamped).rounded()) }

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            identityChip

            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text(title)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(identityColor)
                        .lineLimit(1)
                        .layoutPriority(1)

                    Text("\(percentLeft)% left")
                        .font(.caption.weight(.medium).monospacedDigit())
                        .foregroundStyle(.primary)
                        .lineLimit(1)

                    Spacer(minLength: 4)

                    if let resetText {
                        Text(resetIsAbsolute ? "Resets \(resetText)" : "Resets in \(resetText)")
                            .font(.caption2)
                            .foregroundStyle(.textSecondary)
                            .lineLimit(1)
                    }
                }

                HStack(spacing: 8) {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.progressTrack)
                                .frame(height: 7)

                            RoundedRectangle(cornerRadius: 3)
                                .fill(fillColor)
                                .frame(width: max(0, geo.size.width * min(clamped / 100.0, 1.0)), height: 7)
                        }
                    }
                    .frame(height: 7)

                    Text("\(Int(clamped))%")
                        .font(.caption.weight(.medium).monospacedDigit())
                        .foregroundStyle(fillColor)
                        .frame(width: 34, alignment: .trailing)
                }
            }
        }
        .padding(.vertical, 7)
        .padding(.horizontal, 8)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(identityColor.opacity(0.08))
        )
    }

    private var identityChip: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(identityColor.opacity(0.18))
            Image(systemName: kind.symbolName)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(identityColor)
        }
        .frame(width: 22, height: 22)
        .accessibilityHidden(true)
    }
}
