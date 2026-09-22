import SwiftUI

/// A colour per account, so a card is recognised before it is read.
///
/// The colours deliberately avoid the limit colours that sit inside a card
/// (green Session, blue Weekly, purple Fable): an account is the background, a
/// limit is the content, and the two must never be mistaken for each other.
enum AccountPalette {
    /// Ten washes that stay apart from each other in light and dark. Ten covers
    /// far more accounts than anyone runs; beyond that they repeat, which is
    /// the founder's call ("10 color options and then it repeats").
    static let swatches: [Color] = [
        Color(red: 1.00, green: 0.62, blue: 0.04),   // amber
        Color(red: 1.00, green: 0.18, blue: 0.47),   // pink
        Color(red: 0.17, green: 0.70, blue: 0.64),   // teal
        Color(red: 0.37, green: 0.36, blue: 0.90),   // indigo
        Color(red: 0.64, green: 0.52, blue: 0.37),   // clay
        Color(red: 0.20, green: 0.68, blue: 0.90),   // sky
        Color(red: 0.85, green: 0.72, blue: 0.00),   // ochre
        Color(red: 0.38, green: 0.78, blue: 0.51),   // sage
        Color(red: 1.00, green: 0.45, blue: 0.33),   // coral
        Color(red: 0.49, green: 0.55, blue: 0.69),   // slate
    ]

    /// Which swatch each account gets.
    ///
    /// The preference is a stable hash of the account's id, so an account keeps
    /// its colour across launches (Swift's own `Hasher` is seeded per process
    /// and would not). When two accounts prefer the same swatch the later one
    /// takes the next free swatch, so the cards on screen differ even though
    /// the preference is only a hash. With more accounts than swatches, colours
    /// repeat; nothing else breaks.
    static func assignment(for ids: [UUID]) -> [UUID: Int] {
        var taken = Set<Int>()
        var result: [UUID: Int] = [:]
        for id in ids where result[id] == nil {
            let preferred = Int(fnv1a(id.uuidString) % UInt64(swatches.count))
            var pick = preferred
            var step = 0
            while taken.contains(pick), step < swatches.count {
                step += 1
                pick = (preferred + step) % swatches.count
            }
            taken.insert(pick)
            result[id] = pick
        }
        return result
    }

    /// FNV-1a, so the same account maps to the same colour on every launch and
    /// on every Mac.
    static func fnv1a(_ text: String) -> UInt64 {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in text.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x0000_0100_0000_01B3
        }
        return hash
    }

    /// An account with no colour yet (it is not on screen with the others) gets
    /// a neutral grey rather than borrowing another account's colour.
    static func color(_ index: Int?) -> Color {
        guard let index, swatches.indices.contains(index) else { return .gray }
        return swatches[index]
    }

    /// The card's wash. Light enough that the limit rows inside stay legible.
    static func fill(_ index: Int?) -> Color { color(index).opacity(0.16) }

    /// A firmer edge, so neighbouring cards separate even when scrolled.
    static func border(_ index: Int?) -> Color { color(index).opacity(0.40) }
}
