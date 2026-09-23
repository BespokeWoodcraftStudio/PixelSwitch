// Renders PixelSwitch's real views into the README's images, with sample data.
//
// These are the app's own SwiftUI views (UsageLimitRow, the card chrome, the
// account palette, the limit colours), not mock-ups and not hand-drawn art, so
// a picture in the README cannot drift from what the app draws. Sample accounts
// and numbers are invented; nothing here reads a real login or a real usage
// figure.
//
// Run from the repo root:
//
//   swiftc -swift-version 6 -target arm64-apple-macos14.0 -parse-as-library \
//     -o /tmp/pixelshots \
//     PixelSwitch/Views/Components/UsageLimitRow.swift \
//     PixelSwitch/Views/Components/AccountGlyph.swift \
//     PixelSwitch/Models/Account.swift PixelSwitch/Models/String+Obfuscation.swift \
//     PixelSwitch/Models/MenuBarConfig.swift PixelSwitch/Models/MenuBarModule.swift \
//     PixelSwitch/Models/AppStyle.swift PixelSwitch/Models/BrandColor.swift \
//     PixelSwitch/Models/AccountPalette.swift PixelSwitch/Services/L10n.swift \
//     Tools/screenshots/make_screenshots.swift && /tmp/pixelshots assets/screenshots
import SwiftUI
import AppKit

private struct SampleAccount {
    let email: String
    let swatch: Int
    let plan: String
    let active: Bool
    let session: Double
    let weekly: Double
    let fable: Double
    let sessionReset: String
    let weekReset: String
}

// Invented accounts on example.com, the domain reserved for documentation
// (RFC 2606). Never put a real address in a picture that ships to a public
// repository: the first version of these screenshots did, and it had to be
// replaced.
private let sample: [SampleAccount] = [
    .init(email: "work@example.com", swatch: 0, plan: "Max", active: false,
          session: 98, weekly: 62, fable: 87, sessionReset: "21 min", weekReset: "Mon 11:00 AM"),
    .init(email: "personal@example.com", swatch: 3, plan: "Max", active: true,
          session: 15, weekly: 6, fable: 8, sessionReset: "2 hr 57 min", weekReset: "Mon 9:00 AM"),
    .init(email: "studio@example.com", swatch: 5, plan: "Max", active: false,
          session: 4, weekly: 94, fable: 60, sessionReset: "4 hr 02 min", weekReset: "Fri 9:00 AM"),
    .init(email: "side-project@example.com", swatch: 8, plan: "Pro", active: false,
          session: 40, weekly: 30, fable: 20, sessionReset: "1 hr 10 min", weekReset: "Wed 8:00 AM"),
]

private struct Card: View {
    let account: SampleAccount
    let config: MenuBarConfig

    let logo: Image
    /// "", "hover" or "switching": the three states a card can be in once
    /// double-click-to-switch exists.
    var interaction: String = ""

    private func row(_ kind: LimitBarKind, _ title: LocalizedStringKey, _ used: Double, _ reset: String) -> UsageLimitRow {
        UsageLimitRow(kind: kind, title: title, utilization: used, resetText: reset,
                      resetIsAbsolute: reset.contains(":"),
                      identityColor: config.limitIdentityColor(for: kind),
                      fillColor: config.limitBarColor(for: kind, utilization: used, context: .dashboard),
                      isLow: config.limitIsLow(utilization: used))
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(AccountPalette.color(account.swatch))
                .frame(width: 4)
                .frame(maxHeight: .infinity)

            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    AccountGlyph(provider: .claudeCode,
                                 tint: AccountPalette.color(account.swatch),
                                 size: 17,
                                 logo: logo)
                    Text(account.email)
                        .font(.subheadline.weight(.medium))
                        .lineLimit(1)
                    if account.active {
                        Text("Active")
                            .font(.caption2.weight(.semibold))
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(Capsule().fill(Color.green.opacity(0.22)))
                            .foregroundStyle(.green)
                    }
                    Spacer(minLength: 4)
                    Text(account.plan)
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Capsule().fill(Color.orange.opacity(0.22)))
                        .foregroundStyle(.orange)
                }
                row(.session, "Session", account.session, account.sessionReset)
                row(.weekly, "Weekly", account.weekly, account.weekReset)
                row(.fable, "Fable", account.fable, account.weekReset)

                HStack(spacing: 6) {
                    HStack(spacing: 3) {
                        Image(systemName: "clock").font(.caption2)
                        Text("Updated 1 min ago").font(.caption2)
                    }
                    .foregroundStyle(.secondary)
                    Spacer(minLength: 4)
                    if interaction == "switching" {
                        HStack(spacing: 4) {
                            Image(systemName: "arrow.triangle.2.circlepath").font(.caption2)
                            Text("Switching…").font(.caption2.weight(.semibold))
                        }
                        .foregroundStyle(Color.activeAccountRing)
                    } else if interaction == "hover" {
                        Text("Double-click to switch")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(minHeight: 14)
            }
        }
        .scaleEffect(interaction == "hover" ? 1.01 : 1.0)
        .cardStyle(
            border: (account.active || interaction == "switching") ? .activeAccountRing : .cardBorder,
            borderWidth: (account.active || interaction == "switching") ? 2 : 1
        )
    }
}

@MainActor
private func write<V: View>(_ view: V, width: CGFloat, scheme: ColorScheme, to path: String) {
    let framed = view
        .padding(14)
        .frame(width: width)
        .background(scheme == .dark ? Color(white: 0.11) : Color(white: 0.97))
        .environment(\.colorScheme, scheme)

    let renderer = ImageRenderer(content: framed)
    renderer.scale = 2
    guard let image = renderer.nsImage,
          let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else {
        print("could not render \(path)")
        return
    }
    do {
        try png.write(to: URL(fileURLWithPath: path))
        print("wrote \(path)")
    } catch {
        print("could not write \(path): \(error)")
    }
}

/// What auto-switch does, drawn with the same cards the app draws: the account
/// that has run out of Fable, and the one PixelSwitch moves to.
@MainActor
private func autoSwitch(config: MenuBarConfig, logo: Image, scheme: ColorScheme) -> some View {
    let exhausted = SampleAccount(email: "work@example.com", swatch: 0, plan: "Max", active: true,
                            session: 12, weekly: 64, fable: 100,
                            sessionReset: "3 hr 40 min", weekReset: "Mon 11:00 AM")
    let chosen = SampleAccount(email: "studio@example.com", swatch: 5, plan: "Max", active: false,
                         session: 8, weekly: 41, fable: 23,
                         sessionReset: "4 hr 02 min", weekReset: "Fri 9:00 AM")

    return VStack(alignment: .leading, spacing: 10) {
        Text("Fable ran out on the account you were using")
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
        Card(account: exhausted, config: config, logo: logo)

        HStack(spacing: 8) {
            Image(systemName: "arrow.down")
                .font(.caption.weight(.bold))
            Text("PixelSwitch switches to the account with the most Fable left, and room on its other limits")
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(.secondary)
        .padding(.vertical, 2)

        Card(account: chosen, config: config, logo: logo)
    }
}

@main
enum Screenshots {
    static func main() {
        let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets/screenshots"
        try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

        // The mark, loaded from the asset catalog's file: this tool runs outside
        // the app bundle, where Image("MenuBarLogo") would not resolve.
        let logoPath = "PixelSwitch/Resources/Assets.xcassets/MenuBarLogo.imageset/menubar-logo@3x.png"
        guard let logoImage = NSImage(contentsOfFile: logoPath) else {
            print("could not load \(logoPath); run this from the repository root")
            return
        }
        let logo = Image(nsImage: logoImage)

        MainActor.assumeIsolated {
            let config = MenuBarConfig.shared
            // The stock palette, so the pictures match a fresh install.
            config.customizesLimitBarColors = false

            for scheme in [ColorScheme.light, .dark] {
                let name = scheme == .dark ? "dark" : "light"

                write(VStack(spacing: 14) { ForEach(0..<sample.count, id: \.self) { Card(account: sample[$0], config: config, logo: logo) } },
                      width: 380, scheme: scheme, to: "\(out)/accounts-\(name).png")

                write(Card(account: sample[0], config: config, logo: logo),
                      width: 380, scheme: scheme, to: "\(out)/limits-\(name).png")

                write(autoSwitch(config: config, logo: logo, scheme: scheme),
                      width: 420, scheme: scheme, to: "\(out)/auto-switch-\(name).png")

                write(VStack(alignment: .leading, spacing: 12) {
                        Text("Resting").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        Card(account: sample[2], config: config, logo: logo)
                        Text("Pointer over it").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        Card(account: sample[2], config: config, logo: logo, interaction: "hover")
                        Text("Double-clicked").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        Card(account: sample[2], config: config, logo: logo, interaction: "switching")
                      },
                      width: 400, scheme: scheme, to: "\(out)/switch-states-\(name).png")
            }
        }
    }
}
