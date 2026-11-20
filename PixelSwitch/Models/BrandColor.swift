import AppKit
import Foundation
import SwiftUI

extension Color {
    /// UI accent (Claude orange), used across the menu and settings.
    // static let brand = Color(red: 0x7C / 255.0, green: 0x3A / 255.0, blue: 0xED / 255.0) // #7C3AED
    static let brand = Color(red: 0xE8 / 255.0, green: 0x6D / 255.0, blue: 0x45 / 255.0) // #E86D45

    /// Pixel Ventures signal green (brand/tokens.json `accent`): #0B8F3E on
    /// light surfaces for contrast, #00FF66 on dark. Used only for the
    /// Pixel Ventures mark, never as a status colour.
    static let pixelVenturesGreen = adaptive(
        light: Color(red: 0x0B / 255.0, green: 0x8F / 255.0, blue: 0x3E / 255.0),
        dark: Color(red: 0x00 / 255.0, green: 0xFF / 255.0, blue: 0x66 / 255.0)
    )

    /// Creates a color that automatically adapts between light and dark appearance.
    static func adaptive(light: Color, dark: Color) -> Color {
        Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(isDark ? dark : light)
        }))
    }

    init?(hexRGB: String) {
        let trimmed = hexRGB.trimmingCharacters(in: .whitespacesAndNewlines)
        let hex = trimmed.hasPrefix("#") ? String(trimmed.dropFirst()) : trimmed
        guard hex.count == 6, let value = UInt64(hex, radix: 16) else { return nil }
        self.init(
            red: Double((value >> 16) & 0xFF) / 255.0,
            green: Double((value >> 8) & 0xFF) / 255.0,
            blue: Double(value & 0xFF) / 255.0
        )
    }

    var hexRGB: String? {
        guard let rgb = NSColor(self).usingColorSpace(.sRGB) else { return nil }

        func channel(_ component: CGFloat) -> Int {
            min(max(Int(round(component * 255)), 0), 255)
        }

        return String(
            format: "#%02X%02X%02X",
            channel(rgb.redComponent),
            channel(rgb.greenComponent),
            channel(rgb.blueComponent)
        )
    }

    /// A solid colour from a 0xRRGGBB value, so the tokens below read as hex.
    private static func rgb(_ value: UInt32) -> Color {
        Color(
            red: Double((value >> 16) & 0xFF) / 255.0,
            green: Double((value >> 8) & 0xFF) / 255.0,
            blue: Double(value & 0xFF) / 255.0
        )
    }

    // MARK: - Popover

    /// The popover's own background. Solid on purpose: the menu bar popover used
    /// to be `.ultraThinMaterial` with see-through cards on top, so what sat
    /// behind the text was the founder's wallpaper and no text colour could be
    /// guaranteed to read. Every text and graphic token below is chosen against
    /// THIS surface and the solid card fills on it (4.5:1 for text, 3:1 for bars,
    /// rings and icons, in both appearances).
    static let popoverBackground = adaptive(light: rgb(0xF2F2F5), dark: rgb(0x1C1C1E))

    // MARK: - Card

    /// Standard card fill. Solid, so the text on it has one known background.
    static let cardFill = adaptive(light: rgb(0xFFFFFF), dark: rgb(0x2C2C2E))
    /// Emphasized card fill (e.g. active account row). The live account is found
    /// by its ring, not by this fill, which is why it only steps a little.
    static let cardFillStrong = adaptive(light: rgb(0xFFFFFF), dark: rgb(0x343437))
    /// The inset tile behind one limit row inside a card, and behind today's row
    /// in the cost history: a step away from the card so the row reads as a unit.
    static let rowTile = adaptive(light: rgb(0xF2F2F5), dark: rgb(0x3A3A3C))
    /// Standard card border.
    static let cardBorder = adaptive(light: rgb(0xC7C7CC), dark: rgb(0x505054))
    /// The ring drawn right round the account that is signed in right now.
    ///
    /// Its own token rather than `.brand`: the brand coral sits next to the
    /// "Max" badge and would read as decoration. This is a bright orange that
    /// appears nowhere else in a card, so the live account is found by glance
    /// instead of by hunting for the small "Active" badge. Deeper in light
    /// appearance and brighter in dark, so it carries on both: 3.3:1 on the
    /// light popover and 3.7:1 on a light card (the earlier #F56A00 was 2.7:1).
    static let activeAccountRing = adaptive(
        light: rgb(0xE05A00),  // #E05A00
        dark: rgb(0xFF942E)    // #FF942E
    )

    // MARK: - Tab Bar

    /// Tab bar background fill.
    static let tabFill = adaptive(light: rgb(0xE6E6EB), dark: rgb(0x2C2C2E))
    /// Tab bar border.
    static let tabBorder = adaptive(light: rgb(0xC7C7CC), dark: rgb(0x505054))

    // MARK: - Text

    /// Primary text color for cards and tab selected state.
    static let textPrimary = adaptive(light: rgb(0x1C1C1E), dark: rgb(0xF5F5F7))
    /// Secondary text color for card labels and tab unselected state. The
    /// system's own secondary grey is 3.95:1 on pure white; this one is at least
    /// 5.9:1 on every light surface and 5.5:1 on every dark one.
    static let textSecondary = adaptive(light: rgb(0x55555C), dark: rgb(0xB4B4BA))
    /// Text on a solid brand or green fill (the plan and "Active" badges, the
    /// selected tab). White on #E86D45 was 3.1:1 and on system green 2.0:1;
    /// near-black is 6.0:1 and 8.5:1 or better.
    static let textOnFill = rgb(0x111111)
    /// The brand colour when it is TEXT or a small icon on a card, where the
    /// plain coral is 3.1:1. Deeper in light, lighter in dark.
    static let brandText = adaptive(light: rgb(0xC2410C), dark: rgb(0xFF8A65))
    /// Green for cost figures and "ok" marks on a card: 5.1:1 on a light card,
    /// 6.9:1 on a dark one. System green is 2.2:1 on white.
    static let costGreen = adaptive(light: rgb(0x1A7F37), dark: rgb(0x30D158))

    // MARK: - Limit bars

    /// A limit bar in its healthy, warning and critical colours (stock palette).
    /// Each is at least 3:1 against the bar's track and the row tile, and 4.5:1
    /// as the percentage text on the tile, in both appearances.
    static let limitOK = costGreen
    static let limitWarn = adaptive(light: rgb(0xA9480A), dark: rgb(0xFF9F0A))
    static let limitCrit = adaptive(light: rgb(0xC62828), dark: rgb(0xFF8A84))

    // MARK: - Plan chips

    /// The plan chip fills, one per plan, drawn under `textOnFill` (#111111). Each
    /// fill holds at least 4.5:1 with that text in light AND dark appearance (the
    /// numbers are pinned in AccountPlanTests, from `PlanChipPalette`, the one table
    /// these tokens are built from). Ultra keeps the brand coral; the rest are a
    /// blue, a green, a violet, a gold and a neutral grey. The green is deeper than
    /// the system green of the "Active" badge so the two never read as one.
    static let planUltra = planChipFill(PlanChipPalette.ultra)
    static let planMax = planChipFill(PlanChipPalette.max)
    static let planPro = planChipFill(PlanChipPalette.pro)
    static let planTeam = planChipFill(PlanChipPalette.team)
    static let planEnterprise = planChipFill(PlanChipPalette.enterprise)
    /// An unrecognised plan, and "No plan" for an account with no subscription.
    static let planNeutral = planChipFill(PlanChipPalette.neutral)

    private static func planChipFill(_ fill: PlanChipFill) -> Color {
        adaptive(light: rgb(fill.light), dark: rgb(fill.dark))
    }

    // MARK: - Subtle Backgrounds

    /// Subtle brand tint for banners and badges. A solid tint, not a see-through one.
    static let subtleBrand = adaptive(light: rgb(0xFDE8DF), dark: rgb(0x3D2A22))
    /// Progress bar track.
    static let progressTrack = adaptive(light: rgb(0xD4D4DA), dark: rgb(0x1C1C1E))
}

// MARK: - Plan chip palette

/// One plan chip fill as 0xRRGGBB for each appearance.
struct PlanChipFill: Equatable, Sendable {
    let light: UInt32
    let dark: UInt32
}

/// The plan chip fills as numbers. `Color.planUltra` and its siblings are built
/// from this table, and the unit tests measure the contrast of THESE numbers
/// against `textOnFill`, so the tokens and the pinned ratios cannot drift apart.
enum PlanChipPalette {
    static let ultra = PlanChipFill(light: 0xE86D45, dark: 0xE86D45)       // the brand coral
    static let max = PlanChipFill(light: 0x4A86E8, dark: 0x5E9BFF)         // blue
    static let pro = PlanChipFill(light: 0xC4920A, dark: 0xE3B341)         // gold
    static let team = PlanChipFill(light: 0x2E9E5B, dark: 0x36B56F)        // green
    static let enterprise = PlanChipFill(light: 0x9573E6, dark: 0xA688F5)  // violet
    static let neutral = PlanChipFill(light: 0x8E8E93, dark: 0x9A9AA0)     // grey

    static func fill(for tone: PlanChipTone) -> PlanChipFill {
        switch tone {
        case .ultra: return ultra
        case .max: return max
        case .pro: return pro
        case .team: return team
        case .enterprise: return enterprise
        case .other, .noPlan: return neutral
        }
    }
}

extension PlanChipTone {
    /// The chip's fill, an adaptive token (light and dark).
    var fill: Color {
        switch self {
        case .ultra: return .planUltra
        case .max: return .planMax
        case .pro: return .planPro
        case .team: return .planTeam
        case .enterprise: return .planEnterprise
        case .other, .noPlan: return .planNeutral
        }
    }
}

extension ShapeStyle where Self == Color {
    static var brand: Color { .brand }
    static var popoverBackground: Color { .popoverBackground }
    static var cardFill: Color { .cardFill }
    static var cardFillStrong: Color { .cardFillStrong }
    static var rowTile: Color { .rowTile }
    static var cardBorder: Color { .cardBorder }
    static var activeAccountRing: Color { .activeAccountRing }
    static var tabFill: Color { .tabFill }
    static var tabBorder: Color { .tabBorder }
    static var textPrimary: Color { .textPrimary }
    static var textSecondary: Color { .textSecondary }
    static var textOnFill: Color { .textOnFill }
    static var brandText: Color { .brandText }
    static var costGreen: Color { .costGreen }
    static var limitOK: Color { .limitOK }
    static var limitWarn: Color { .limitWarn }
    static var limitCrit: Color { .limitCrit }
    static var subtleBrand: Color { .subtleBrand }
    static var progressTrack: Color { .progressTrack }
}
