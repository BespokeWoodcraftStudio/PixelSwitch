import Foundation

/// The accounts list's order is the user's priority order, and every list in
/// the app shows it (2.0, spec D2 and D3). An account's stored `priority` (1–99,
/// lower first) decides it; the array is kept sorted by it. These are the pure
/// operations on it, kept in the unit harness.
enum AccountOrder {

    /// The priority numbers an account may carry.
    static let priorityRange = 1...99

    /// `priority` if it is a number a user may set, else nil.
    static func validPriority(_ priority: Int?) -> Int? {
        guard let priority, priorityRange.contains(priority) else { return nil }
        return priority
    }

    /// `accounts` in priority order, stable. Numbered accounts first, lowest
    /// number first; among equal numbers the account whose number was set most
    /// recently (`lastSet`, higher = more recent) goes first, one that was
    /// never set comes after every one that was, and otherwise the stored
    /// order stands. Unnumbered accounts come last in stored order.
    static func sortedByPriority(
        _ accounts: [Account],
        lastSet: (Account) -> Double? = { $0.prioritySetAt?.timeIntervalSince1970 }
    ) -> [Account] {
        accounts.enumerated().sorted { a, b in
            let pa = validPriority(a.element.priority), pb = validPriority(b.element.priority)
            switch (pa, pb) {
            case let (x?, y?):
                if x != y { return x < y }
                switch (lastSet(a.element), lastSet(b.element)) {
                case let (s?, t?) where s != t: return s > t
                case (.some, nil): return true
                case (nil, .some): return false
                default: return a.offset < b.offset
                }
            case (.some, nil): return true
            case (nil, .some): return false
            case (nil, nil): return a.offset < b.offset
            }
        }.map(\.element)
    }

    /// `accounts` with `id`'s priority set to `priority` (nil clears it), sorted.
    /// Nil for an unknown id, or a number outside 1–99. Setting a number
    /// stamps `prioritySetAt` with a time later than any already on the list,
    /// so the account set last goes first among equals. Setting the number an
    /// account already has changes nothing.
    static func settingPriority(_ priority: Int?, of id: UUID, in accounts: [Account], at now: Date = Date()) -> [Account]? {
        guard let index = accounts.firstIndex(where: { $0.id == id }) else { return nil }
        if let priority, !priorityRange.contains(priority) { return nil }
        guard accounts[index].priority != priority else { return sortedByPriority(accounts) }
        var result = accounts
        result[index].priority = priority
        if priority == nil {
            result[index].prioritySetAt = nil
        } else {
            let latest = accounts.compactMap(\.prioritySetAt).max()
            result[index].prioritySetAt = latest.map { max(now, $0.addingTimeInterval(0.001)) } ?? now
        }
        return sortedByPriority(result)
    }

    /// `accounts` numbered 1...N in the order given, replacing every typed
    /// number (a whole-list reorder or a drag, spec D3). Numbers stop at 99.
    static func numberedInOrder(_ accounts: [Account]) -> [Account] {
        accounts.enumerated().map { offset, account in
            var numbered = account
            numbered.priority = min(offset + 1, priorityRange.upperBound)
            numbered.prioritySetAt = nil
            return numbered
        }
    }

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

    /// Where to go when the active account is removed, best first: the
    /// accounts that are not Manual only in priority order, then the Manual
    /// only ones. The caller tries them in turn until a switch succeeds.
    static func fallbackOrder(from remaining: [Account]) -> [Account] {
        let sorted = sortedByPriority(remaining)
        return sorted.filter { !AutoSwitchSettings.isManualOnly(own: $0.switchThreshold) }
            + sorted.filter { AutoSwitchSettings.isManualOnly(own: $0.switchThreshold) }
    }

    /// The first of `fallbackOrder`: the first account in priority order that
    /// is not Manual only, else the first.
    static func fallback(from remaining: [Account]) -> Account? {
        fallbackOrder(from: remaining).first
    }
}
