import SwiftUI

/// The popover header's name block: the account's primary line (with its priority
/// and plan chip) and, only when it adds something, the address under it.
///
/// The lines come from `Account.identityLines(obfuscated:)`, so the address is
/// never printed twice. With no second line the block is one row tall; the header's
/// icon row centres it, so nothing is left where the second line used to be.
struct AccountIdentityHeader: View {
    let identity: AccountIdentityLines
    /// The account's priority number, already validated; nil draws no tag.
    var priority: Int?
    /// The plan chip; nil draws none.
    var chip: PlanChip?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                if let priority {
                    PriorityTag(priority: priority)
                }
                Text(identity.primary)
                    .font(.headline)
                    // One line, never broken mid-word: a long address (33 characters is 242 pt bold)
                    // shrinks a little, then loses its middle, so the start and the domain both
                    // stay. The whole of it is on hover.
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                    .truncationMode(.middle)
                    .help(identity.primary)
                if let chip {
                    Badge(text: chip.label, color: chip.tone.fill)
                }
            }
            if let secondary = identity.secondary {
                Text(secondary)
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
            }
        }
    }
}
