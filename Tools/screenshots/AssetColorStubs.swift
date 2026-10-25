// The app's asset catalog gives Color its cardFill and cardBorder symbols; the
// offscreen renders compile without the catalog, so AppStyle.swift needs these
// two stand-ins. Nothing the renders draw uses them (only the Badge's padding
// constants are read from AppStyle).
import SwiftUI
import AppKit

extension Color {
    static let cardFill = Color(nsColor: .controlBackgroundColor)
    static let cardBorder = Color(nsColor: .separatorColor)
}
