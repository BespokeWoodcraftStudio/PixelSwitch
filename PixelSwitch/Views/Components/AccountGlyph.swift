import SwiftUI

/// The mark shown beside an account.
///
/// A Claude Code account gets the PixelSwitch mark, in the account's colour;
/// the inherited head glyph was CCSwitcher's. Other providers keep their own
/// symbol until they have a mark of their own.
///
/// `logo` exists so `Tools/screenshots/make_screenshots.swift` can hand in the
/// same artwork loaded from disk: it renders these views outside the app
/// bundle, where an asset-catalog name does not resolve.
struct AccountGlyph: View {
    let provider: AIProviderType
    let tint: Color
    var size: CGFloat = 16
    var logo: Image = Image("MenuBarLogo")

    var body: some View {
        Group {
            if provider == .claudeCode {
                logo
                    .renderingMode(.template)
                    .resizable()
                    .aspectRatio(2, contentMode: .fit)
                    .frame(width: size, height: size / 2)
            } else {
                Image(systemName: provider.iconName)
                    .font(.system(size: size * 0.8))
            }
        }
        .foregroundStyle(tint)
        .accessibilityHidden(true)
    }
}
