import Foundation

/// The accounts list's order is the user's priority order ("My order"), and
/// every list in the app shows it. These are the two ways to change it, kept
/// pure so the unit harness can pin them.
enum AccountOrder {

    /// `accounts` rearranged into the order `orderedIds` gives, or nil unless
    /// `orderedIds` names every current account exactly once and nothing else.
    /// An order that leaves an account out, repeats one, or names one that has
    /// been removed is refused as a whole rather than half applied.
    static func reordered(_ accounts: [Account], by orderedIds: [UUID]) -> [Account]? {
        let currentIds = accounts.map(\.id)
        guard orderedIds.count == currentIds.count,
              Set(orderedIds).count == orderedIds.count,
              Set(currentIds).count == currentIds.count,
              Set(orderedIds) == Set(currentIds) else { return nil }
        let byId = Dictionary(accounts.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return orderedIds.compactMap { byId[$0] }
    }

    /// `items` with the elements at `source` moved to `destination`, with the
    /// meaning SwiftUI's `onMove` gives them: `destination` is an index in the
    /// list BEFORE the move. Offsets outside the list are ignored, and a
    /// destination outside it is clamped.
    static func moving<Element>(_ items: [Element], fromOffsets source: IndexSet, toOffset destination: Int) -> [Element] {
        let valid = source.filter { items.indices.contains($0) }
        guard !valid.isEmpty else { return items }
        let moved = valid.map { items[$0] }
        let kept = items.indices.filter { !valid.contains($0) }.map { items[$0] }
        let clamped = min(max(destination, 0), items.count)
        let insertAt = clamped - valid.filter { $0 < clamped }.count
        var result = kept
        result.insert(contentsOf: moved, at: insertAt)
        return result
    }

    /// Where to go when the active account is removed: the first account that
    /// is not Manual only, else the first.
    static func fallback(from remaining: [Account]) -> Account? {
        remaining.first { !AutoSwitchSettings.isManualOnly(own: $0.switchThreshold) } ?? remaining.first
    }
}
