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
//     PixelSwitch/Models/MenuBarConfig.swift PixelSwitch/Models/MenuBarModule.swift \
//     PixelSwitch/Models/AppStyle.swift PixelSwitch/Models/BrandColor.swift \
//     PixelSwitch/Models/AccountPalette.swift PixelSwitch/Services/L10n.swift \
//     Tools/screenshots/make_screenshots.swift && /tmp/pixelshots assets/screenshots
import SwiftUI
import AppKit

private struct Account {
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

private let sample: [Account] = [
    .init(email: "ahmed@pixelventures.ai", swatch: 0, plan: "Max", active: false,
          session: 98, weekly: 62, fable: 87, sessionReset: "21 min", weekReset: "Mon 11:00 AM"),
    .init(email: "claude@pixelventures.ai", swatch: 3, plan: "Max", active: true,
          session: 15, weekly: 6, fable: 8, sessionReset: "2 hr 57 min", weekReset: "Mon 9:00 AM"),
    .init(email: "studio@pixelventures.ai", swatch: 5, plan: "Max", active: false,
          session: 4, weekly: 94, fable: 60, sessionReset: "4 hr 02 min", weekReset: "Fri 9:00 AM"),
    .init(email: "workshop@pixelventures.ai", swatch: 8, plan: "Pro", active: false,
          session: 40, weekly: 30, fable: 20, sessionReset: "1 hr 10 min", weekReset: "Wed 8:00 AM"),
]

private struct Card: View {
    let account: Account
    let config: MenuBarConfig

    private func row(_ kind: LimitBarKind, _ title: LocalizedStringKey, _ used: Double, _ reset: String) -> UsageLimitRow {
        UsageLimitRow(kind: kind, title: title, utilization: used, resetText: reset,
                      resetIsAbsolute: reset.contains(":"),
                      identityColor: config.limitIdentityColor(for: kind),
                      fillColor: config.limitBarColor(for: kind, utilization: used, context: .dashboard))
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(AccountPalette.color(account.swatch))
                .frame(width: 4)
                .frame(maxHeight: .infinity)

            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "brain.head.profile")
                        .font(.subheadline)
                        .foregroundStyle(AccountPalette.color(account.swatch))
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
            }
        }
        .cardStyle()
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

@main
enum Screenshots {
    static func main() {
        let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets/screenshots"
        try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

        MainActor.assumeIsolated {
            let config = MenuBarConfig.shared
            // The stock palette, so the pictures match a fresh install.
            config.customizesLimitBarColors = false

            for scheme in [ColorScheme.light, .dark] {
                let name = scheme == .dark ? "dark" : "light"

                write(VStack(spacing: 14) { ForEach(0..<sample.count, id: \.self) { Card(account: sample[$0], config: config) } },
                      width: 380, scheme: scheme, to: "\(out)/accounts-\(name).png")

                write(Card(account: sample[0], config: config),
                      width: 380, scheme: scheme, to: "\(out)/limits-\(name).png")
            }
        }
    }
}
