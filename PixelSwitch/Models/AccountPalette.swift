import SwiftUI

/// A colour per account, so a card is recognised before it is read.
///
/// The colour is an accent, not a background. A first attempt washed the whole
/// card in the account's colour; the founder's verdict was that the colours
/// were too alike and the text became hard to read on some of them. So the
/// colour now lives in a solid edge and the account's glyph, where it can be
/// strong and obvious, while the surface you read keeps almost none of it:
/// `wash` is faint enough that text holds its contrast on every swatch, in
/// light and dark.
enum AccountPalette {
    /// Ten hues, spread around the wheel by eye rather than in equal steps,
    /// because equal steps bunch up in the greens and leave amber, ochre and
    /// coral looking like one another. Degrees: red, orange, amber, green,
    /// teal, cyan, blue, indigo, violet, magenta.
    private static let hues: [Double] = [2, 30, 52, 96, 160, 186, 212, 252, 288, 324]

    static var swatchCount: Int { hues.count }

    /// The account's colour at full strength: its edge and its glyph. Deeper in
    /// light appearance, brighter in dark, so it reads on both.
    static func color(_ index: Int?) -> Color {
        guard let index, hues.indices.contains(index) else { return .gray }
        let hue = hues[index] / 360.0
        return Color.adaptive(
            light: Color(hue: hue, saturation: 0.85, brightness: 0.70),
            dark: Color(hue: hue, saturation: 0.62, brightness: 1.00)
        )
    }

    // No card fill and no coloured border on purpose. A first version washed the
    // whole card, and a second kept a faint wash; both read as a coloured box.
    // The founder's call: "you made a mistake and left the full box as colored
    // instead of just the line". The card stays neutral; the line and the glyph
    // carry the colour.

    /// Which swatch each account gets.
    ///
    /// The preference is a stable hash of the account's id, so an account keeps
    /// its colour across launches (Swift's own `Hasher` is seeded per process
    /// and would not). When two accounts prefer the same swatch the later one
    /// takes the next free swatch, so the accounts on screen always differ.
    /// With more accounts than swatches, colours repeat; nothing else breaks.
    static func assignment(for ids: [UUID]) -> [UUID: Int] {
        var taken = Set<Int>()
        var result: [UUID: Int] = [:]
        for id in ids where result[id] == nil {
            let preferred = Int(fnv1a(id.uuidString) % UInt64(swatchCount))
            var pick = preferred
            var step = 0
            while taken.contains(pick), step < swatchCount {
                step += 1
                pick = (preferred + step) % swatchCount
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
}

/// Whether accounts are colour-coded at all. Off gives the plain, uncoloured
/// cards PixelSwitch had before, for anyone who prefers them.
enum AccountColorCoding {
    static let key = "colorCodeAccounts"

    static var isOn: Bool {
        UserDefaults.standard.object(forKey: key) as? Bool ?? true
    }
}
