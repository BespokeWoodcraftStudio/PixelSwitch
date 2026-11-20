// Renders PixelSwitch's real views into the README's images, with sample data.
//
// These are the app's own SwiftUI views (UsageLimitRow, Badge, the card chrome,
// the account palette, the limit colours), not mock-ups and not hand-drawn art,
// so a picture in the README cannot drift from what the app draws. Sample
// accounts and numbers are invented; nothing here reads a real login or a real
// usage figure.
//
// Every picture sits on `Color.popoverBackground`, the popover's own solid
// background, so what you see here is the surface the real popover paints. It
// is also the harness for checking contrast: render, then sample the PNG.
//
// Run from the repo root (give the output folder as the last argument; with no
// argument it writes assets/screenshots, which are the README's pictures):
//
//   swiftc -swift-version 6 -target arm64-apple-macos14.0 -parse-as-library \
//     -o /tmp/pixelshots \
//     PixelSwitch/Views/Components/UsageLimitRow.swift \
//     PixelSwitch/Views/Components/AccountGlyph.swift \
//     PixelSwitch/Views/Components/Badge.swift \
//     PixelSwitch/Views/Components/CardFooterRow.swift \
//     PixelSwitch/Models/Account.swift PixelSwitch/Models/AccountPlan.swift PixelSwitch/Models/UsageData.swift \
//     PixelSwitch/Models/String+Obfuscation.swift \
//     PixelSwitch/Models/MenuBarConfig.swift PixelSwitch/Models/MenuBarModule.swift \
//     PixelSwitch/Models/AppStyle.swift PixelSwitch/Models/BrandColor.swift \
//     PixelSwitch/Models/AccountPalette.swift PixelSwitch/Services/L10n.swift \
//     PixelSwitch/Services/AutoSwitchEngine.swift \
//     Tools/screenshots/make_screenshots.swift && /tmp/pixelshots assets/screenshots
import SwiftUI
import AppKit

private struct SampleAccount {
    let email: String
    let swatch: Int
    /// The plan chip, from the real `Account.planChip` (see `chip(_:_:noSubscription:)`),
    /// so the colours here are the ones the app draws. nil draws no chip.
    let chip: PlanChip?
    let active: Bool
    let session: Double
    let weekly: Double
    let fable: Double
    let sessionReset: String
    let weekReset: String
    /// False for an account on a plan with no 5-hour limit (Ultra): no Session row.
    var hasSessionLimit = true
    /// A usage request that left no reading: the sign-in ended (the card offers
    /// Re-sign) or the account has no active subscription (it offers nothing).
    var problem: Problem?
    /// Whether the card offers Limit resets. True for an account with a plan; the
    /// no-subscription sample sets it from the real `Account.showsResetsButton`.
    var showsResets = true

    enum Problem { case expired, noSubscription, unreachable }
}

/// The chip for an account on this plan, built through the real `Account` so the
/// label, the tone and the "No plan" override are the app's own code.
private func chip(_ subscriptionType: String?, _ planTier: String? = nil, noSubscription: Bool = false) -> PlanChip? {
    Account(email: "plan@example.com", displayName: "Plan", subscriptionType: subscriptionType, planTier: planTier)
        .planChip(noSubscription: noSubscription)
}

// Invented accounts on example.com, the domain reserved for documentation
// (RFC 2606). Never put a real address in a picture that ships to a public
// repository: the first version of these screenshots did, and it had to be
// replaced.
private let sample: [SampleAccount] = [
    .init(email: "work@example.com", swatch: 0, chip: chip("max", "max_5x"), active: false,
          session: 98, weekly: 62, fable: 87, sessionReset: "21 min", weekReset: "Mon 11:00 AM"),
    .init(email: "personal@example.com", swatch: 3, chip: chip("max", "max_5x"), active: true,
          session: 15, weekly: 6, fable: 8, sessionReset: "2 hr 57 min", weekReset: "Mon 9:00 AM"),
    .init(email: "studio@example.com", swatch: 5, chip: chip("max", "max_5x"), active: false,
          session: 4, weekly: 94, fable: 60, sessionReset: "4 hr 02 min", weekReset: "Fri 9:00 AM"),
    .init(email: "side-project@example.com", swatch: 8, chip: chip("pro", "default_claude_ai"), active: false,
          session: 40, weekly: 30, fable: 20, sessionReset: "1 hr 10 min", weekReset: "Wed 8:00 AM"),
]

/// An Ultra account (Max 20x), built through the real `Account`, so its badge and
/// whether it has a Session row come from `AccountPlan`, the same code the app runs.
private let ultraSample: SampleAccount = {
    let account = Account(email: "ultra@example.com", displayName: "Ultra", subscriptionType: "max", planTier: "max_20x")
    return SampleAccount(email: account.email, swatch: 2, chip: account.planChip(), active: false,
                         session: 0, weekly: 42, fable: 74, sessionReset: "", weekReset: "Fri 9:00 AM",
                         hasSessionLimit: account.hasSessionLimit)
}()

/// One account per plan the chip colours tell apart: Ultra, Max, Pro, Team, Enterprise and an
/// unrecognised plan. The subscription strings are the raw ones the app stores.
private let planSamples: [SampleAccount] = [
    ("ultra@example.com", chip("max", "max_20x"), 2, false),
    ("max@example.com", chip("max", "max_5x"), 4, true),
    ("pro@example.com", chip("pro", "default_claude_ai"), 8, true),
    ("team@example.com", chip("team", nil), 6, true),
    ("enterprise@example.com", chip("enterprise", nil), 1, true),
    ("business@example.com", chip("business", nil), 9, true),
].map { email, chip, swatch, hasSession in
    SampleAccount(email: email, swatch: swatch, chip: chip, active: false,
                  session: 22, weekly: 38, fable: 51, sessionReset: "3 hr 12 min", weekReset: "Fri 9:00 AM",
                  hasSessionLimit: hasSession)
}

/// A card whose sign-in has ended (Re-sign), one for an account with no active
/// subscription ("No plan", nothing to click) and an expired Ultra one.
private let expiredSample = SampleAccount(email: "expired@example.com", swatch: 3, chip: chip("max", "max_5x"), active: false,
                                          session: 0, weekly: 0, fable: 0, sessionReset: "", weekReset: "", problem: .expired)
private let expiredUltraSample = SampleAccount(email: "expired-ultra@example.com", swatch: 2, chip: chip("max", "max_20x"), active: false,
                                               session: 0, weekly: 0, fable: 0, sessionReset: "", weekReset: "", hasSessionLimit: false,
                                               problem: .expired)
private let unreachableSample = SampleAccount(email: "offline@example.com", swatch: 7, chip: chip("max", "max_5x"), active: false,
                                              session: 0, weekly: 0, fable: 0, sessionReset: "", weekReset: "", problem: .unreachable)
private let lapsedSample = SampleAccount(email: "lapsed@example.com", swatch: 5, chip: chip("max", "max_20x", noSubscription: true), active: false,
                                         session: 0, weekly: 0, fable: 0, sessionReset: "", weekReset: "", problem: .noSubscription,
                                         // The real rule decides: a lapsed account has no Limit resets button.
                                         showsResets: Account(email: "lapsed@example.com", displayName: "Lapsed",
                                                              subscriptionType: "max", planTier: "max_20x")
                                             .showsResetsButton(noSubscription: true))

/// The footer's words in one language, read from that language's own Localizable.strings
/// (run from the repo root), so the German and French pictures use the shipped translations.
/// "Double-click to switch" and "Switching…" are in no table, so the app shows them in English
/// in every language, and so does this.
private struct FooterStrings {
    let code: String
    let bundle: Bundle
    /// What `Text(date, style: .relative)` prints for a long and for a short age. The system
    /// formats these, so they are written out here as that language prints them.
    let longAge: String
    let shortAge: String
    /// An invented age longer than the app is likely to show, only to prove the last fallback.
    let stressAge: String

    init(_ code: String, longAge: String, shortAge: String, stressAge: String) {
        self.code = code
        self.longAge = longAge
        self.shortAge = shortAge
        self.stressAge = stressAge
        self.bundle = Bundle(path: "PixelSwitch/\(code).lproj") ?? .main
    }

    func t(_ key: String) -> String { bundle.localizedString(forKey: key, value: key, table: nil) }
    func age(_ relative: String) -> String { String(format: t("Updated %@ ago"), relative) }
    var resets: String { t("Limit resets") }
    var manualOnly: String { t("Manual only") }

    static let en = FooterStrings("en", longAge: "12 hr, 59 min", shortAge: "4 min", stressAge: "1 day, 23 hr, 59 min")
    static let de = FooterStrings("de", longAge: "12 Std., 59 Min.", shortAge: "4 Min.", stressAge: "1 Tag, 23 Std., 59 Min.")
    static let fr = FooterStrings("fr", longAge: "12 h, 59 min", shortAge: "4 min", stressAge: "1 j, 23 h, 59 min")
    static let ja = FooterStrings("ja", longAge: "12時間59分", shortAge: "4分", stressAge: "1日23時間59分")
    static let zh = FooterStrings("zh-Hans", longAge: "12小时59分钟", shortAge: "4分钟", stressAge: "1天23小时59分钟")
    static let all = [en, de, fr, ja, zh]
}

/// The pieces of a card's footer as the app draws them, as looks (ImageRenderer cannot draw a
/// real Button). The layout rule is the app's own `CardFooterRow`.
private struct FooterLine: View {
    let strings: FooterStrings
    /// The relative age ("4 min"); nil draws no age, as for a fresh reading.
    var relative: String?
    var showsResets = true
    var hover = false
    var switching = false
    var manualOnly = false

    private func resets(iconOnly: Bool) -> some View {
        Group {
            if showsResets {
                Group {
                    if iconOnly {
                        Label(strings.resets, systemImage: "arrow.counterclockwise.circle").labelStyle(.iconOnly)
                    } else {
                        Label(strings.resets, systemImage: "arrow.counterclockwise.circle")
                    }
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.brandText)
            }
        }
    }

    var body: some View {
        CardFooterRow(
            age: Group {
                if let relative {
                    HStack(spacing: 3) {
                        Image(systemName: "clock").font(.caption2)
                        Text(verbatim: strings.age(relative)).font(.caption2).lineLimit(1)
                    }
                    .foregroundStyle(.textSecondary)
                }
            },
            reset: resets(iconOnly: false),
            resetIconOnly: resets(iconOnly: true),
            status: Group {
                if switching {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.triangle.2.circlepath").font(.caption2)
                        Text("Switching…").font(.caption2.weight(.semibold))
                    }
                    .foregroundStyle(Color.brandText)
                } else if manualOnly && !hover {
                    Label(strings.manualOnly, systemImage: "hand.raised")
                        .font(.caption2).foregroundStyle(.textSecondary).lineLimit(1)
                }
            },
            hint: Group {
                if hover && !switching {
                    Text("Double-click to switch").font(.caption2).foregroundStyle(.textSecondary).lineLimit(1)
                }
            }
        )
    }
}

private struct Card: View {
    let account: SampleAccount
    let config: MenuBarConfig

    let logo: Image
    /// "", "hover" or "switching": the three states a card can be in once
    /// double-click-to-switch exists.
    var interaction: String = ""
    /// The footer's relative age. The app prints "Updated <relative time> ago" and only
    /// once a reading is 90 seconds old; a long one is the tightest case for the row.
    var relative: String = "1 min"
    var strings = FooterStrings.en

    /// The app's Limit resets button, as a look (see the note beside Re-sign below):
    /// the same Label, symbol and font, in `.brandText`.
    private var resetsLabel: some View {
        Label(strings.resets, systemImage: "arrow.counterclockwise.circle")
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.brandText)
            .fixedSize()
    }

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
                        Badge(text: "Active", color: .green)
                    }
                    Spacer(minLength: 4)
                    if let chip = account.chip {
                        Badge(text: chip.label, color: chip.tone.fill)
                    }
                }
                if let problem = account.problem {
                    // Hand copy of the Usage card's no-reading branch (UsageDashboardView):
                    // keep it in step by hand. Re-sign only when the sign-in has ended.
                    // Like the app's, this kind of card has no footer: Limit resets sits in a row
                    // of its own under the message, and only when `showsResets`.
                    HStack(alignment: .top) {
                        Image(systemName: problem == .expired ? "exclamationmark.triangle" : "xmark.circle")
                            .foregroundStyle(problem == .expired ? .limitWarn : .limitCrit)
                        Text(problem == .expired ? "Session expired. Re-authenticate (↻) to fix."
                             : (problem == .noSubscription ? "No active subscription on this account (OAuth not allowed)."
                                                           : "Can't reach Anthropic right now; PixelSwitch keeps trying."))
                            .font(.caption)
                            .foregroundStyle(.textSecondary)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 4)
                        // The app's buttons are real borderless Buttons; ImageRenderer cannot
                        // draw a Button (it paints a prohibition sign), so these are their look:
                        // the same Label and font, in `.brandText` as the app sets it.
                        if problem == .expired {
                            Label("Re-sign", systemImage: "arrow.triangle.2.circlepath")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.brandText)
                                .fixedSize()
                        } else if problem == .unreachable {
                            Label("Retry", systemImage: "arrow.clockwise")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.brandText)
                                .fixedSize()
                        }
                    }
                    .padding(.top, 4)
                } else {
                    if account.hasSessionLimit {
                        row(.session, "Session", account.session, account.sessionReset)
                    }
                    row(.weekly, "Weekly", account.weekly, account.weekReset)
                    row(.fable, "Fable", account.fable, account.weekReset)
                }

                if account.problem == nil {
                    // The footer row of a card with a reading (the app's `CardFooterRow`).
                    FooterLine(strings: strings, relative: relative, showsResets: account.showsResets,
                               hover: interaction == "hover", switching: interaction == "switching")
                } else if account.showsResets {
                    HStack(spacing: 6) {
                        resetsLabel
                        Spacer(minLength: 4)
                    }
                    .frame(minHeight: 14)
                }
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
        .background(Color.popoverBackground)
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
    let exhausted = SampleAccount(email: "work@example.com", swatch: 0, chip: chip("max", "max_5x"), active: true,
                            session: 12, weekly: 64, fable: 100,
                            sessionReset: "3 hr 40 min", weekReset: "Mon 11:00 AM")
    let chosen = SampleAccount(email: "studio@example.com", swatch: 5, chip: chip("max", "max_5x"), active: false,
                         session: 8, weekly: 41, fable: 23,
                         sessionReset: "4 hr 02 min", weekReset: "Fri 9:00 AM")

    return VStack(alignment: .leading, spacing: 10) {
        Text("Fable ran out on the account you were using")
            .font(.caption.weight(.semibold))
            .foregroundStyle(.textSecondary)
        Card(account: exhausted, config: config, logo: logo)

        HStack(spacing: 8) {
            Image(systemName: "arrow.down")
                .font(.caption.weight(.bold))
            Text("PixelSwitch switches to the account with the most Fable left, and room on its other limits")
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(.textSecondary)
        .padding(.vertical, 2)

        Card(account: chosen, config: config, logo: logo)
    }
}

/// The rest of the popover's colours: header, promo banner, tab bar, an Accounts
/// row, a cost-history row and the footer error. MainMenuView, AccountSwitcherView
/// and CostDetailView need AppState, so this is hand-copied markup that uses the
/// same tokens; it exists to check those colours, not to be shipped as a picture.
/// Keep it in step with those views by hand.
private struct Chrome: View {
    private func tab(_ symbol: String, _ title: String, selected: Bool) -> some View {
        VStack(spacing: 2) {
            Image(systemName: symbol).font(.system(size: 12))
            Text(title).font(.caption2.weight(.semibold))
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .foregroundStyle(selected ? .textOnFill : .textSecondary)
    }

    private func accountRow(_ email: String, active: Bool) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(email).font(.subheadline.weight(.medium))
                    if active { Badge(text: "Active", color: .green) }
                }
                Text("Max").font(.caption2).foregroundStyle(.textSecondary)
            }
            Spacer()
            Image(systemName: "arrow.triangle.2.circlepath").font(.caption).foregroundStyle(.limitWarn)
            Image(systemName: "trash").font(.caption).foregroundStyle(.red)
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(active ? .cardFillStrong : .cardFill)
                .strokeBorder(active ? Color.activeAccountRing : .cardBorder, lineWidth: active ? 2 : 1)
                .shadow(color: AppStyle.cardShadowColor, radius: AppStyle.cardShadowRadius, x: 0, y: AppStyle.cardShadowY)
        )
    }

    private func costRow(_ date: String, _ amount: String, ratio: Double, today: Bool) -> some View {
        HStack(spacing: 8) {
            Text(date).font(.caption2.monospacedDigit())
                .foregroundStyle(today ? .brandText : .textSecondary).frame(width: 40, alignment: .leading)
            Text(amount).font(.caption2.weight(.medium).monospacedDigit())
                .foregroundStyle(today ? .brandText : .primary).frame(width: 56, alignment: .trailing)
            GeometryReader { geo in
                RoundedRectangle(cornerRadius: 2)
                    .fill(today ? Color.brandText : Color.blue.opacity(0.85))
                    .frame(width: max(2, geo.size.width * ratio), height: 8)
            }.frame(height: 8)
        }
        .padding(.vertical, 4).padding(.horizontal, 8)
        .background(today ? .rowTile : .clear)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 6) {
                Text(verbatim: "work@example.com").font(.headline)
                Badge(text: "Max", color: PlanChipTone.max.fill)
                Spacer()
            }
            HStack {
                Image(systemName: "gift.fill").foregroundStyle(.brandText)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Double Usage Active").font(.caption).fontWeight(.medium)
                    Text("Until 5:00 PM today").font(.caption2).foregroundStyle(.textSecondary)
                }
                Spacer()
            }
            .padding(.horizontal, 16).padding(.vertical, 8)
            .background(.subtleBrand)

            ZStack {
                Capsule().fill(.tabFill).overlay(Capsule().stroke(.tabBorder, lineWidth: 1))
                GeometryReader { geo in
                    Capsule().fill(Color.brand).padding(2).frame(width: geo.size.width / 3)
                }
                HStack(spacing: 0) {
                    tab("chart.bar", "Usage", selected: true)
                    tab("person.2", "Accounts", selected: false)
                    tab("dollarsign.circle", "Cost", selected: false)
                }
            }
            .frame(width: 260, height: 44)

            accountRow("personal@example.com", active: true)
            accountRow("studio@example.com", active: false)

            VStack(spacing: 4) {
                costRow("Nov 10", "$12.40", ratio: 0.9, today: true)
                costRow("Nov 9", "$7.10", ratio: 0.5, today: false)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: AppStyle.cardCornerRadius).fill(.cardFill)
                    .strokeBorder(.cardBorder, lineWidth: 1)
            )

            HStack(spacing: 6) {
                Image(systemName: "dollarsign.circle").font(.subheadline).foregroundStyle(.costGreen)
                Text("$12.40").font(.title.weight(.semibold).monospacedDigit()).foregroundStyle(.costGreen)
                Spacer()
                Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.limitWarn)
                Text("Could not reach the server").font(.caption2).foregroundStyle(.textSecondary)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: AppStyle.cardCornerRadius).fill(.cardFill)
                    .strokeBorder(.cardBorder, lineWidth: 1)
            )
        }
    }
}

/// One footer line inside the card's own chrome (swatch, padding, border), so the line is given exactly
/// the width a real card gives it: a 328 pt card leaves 290 pt.
private struct FooterCard: View {
    let line: FooterLine
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 2, style: .continuous).fill(AccountPalette.color(3)).frame(width: 4).frame(maxHeight: .infinity)
            line
        }
        .cardStyle()
    }
}

private struct FooterMatrix: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(FooterStrings.all, id: \.code) { strings in
                Text(verbatim: strings.code).font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                FooterCard(line: FooterLine(strings: strings, relative: strings.shortAge, hover: true))
                FooterCard(line: FooterLine(strings: strings, relative: strings.longAge, hover: true))
                FooterCard(line: FooterLine(strings: strings, relative: strings.longAge, manualOnly: true))
                FooterCard(line: FooterLine(strings: strings, relative: strings.stressAge, hover: true))
            }
        }
    }
}

/// Prints each piece's natural width as SwiftUI lays it out, per language, against the 290 pt a
/// card gives the line, and which variant of `CardFooterRow` that picks (the same order it tries).
@MainActor
private func reportFooterWidths() {
    func width<V: View>(_ view: V) -> CGFloat { NSHostingView(rootView: view.fixedSize()).fittingSize.width }
    let hint = width(Text("Double-click to switch").font(.caption2))
    let available: CGFloat = 328 - 2 * AppStyle.cardPadding - 14
    print("footer widths (points); the line gets \(available) pt; spacing 6, spacer 4; hint \(hint)")
    for strings in FooterStrings.all {
        for (name, relative) in [("long age", strings.longAge), ("short age", strings.shortAge), ("stress age", strings.stressAge)] {
            let age = width(HStack(spacing: 3) { Image(systemName: "clock").font(.caption2)
                                                  Text(verbatim: strings.age(relative)).font(.caption2) })
            let full = width(Label(strings.resets, systemImage: "arrow.counterclockwise.circle").font(.caption2.weight(.semibold)))
            let icon = width(Label(strings.resets, systemImage: "arrow.counterclockwise.circle").labelStyle(.iconOnly).font(.caption2.weight(.semibold)))
            let withHint = age + 6 + full + 6 + 4 + 6 + hint
            let noHint = age + 6 + full + 6 + 4
            let iconOnly = age + 6 + icon + 6 + 4
            let pick = withHint <= available ? "hint + words" : (noHint <= available ? "words, hint dropped" : "icon only")
            print(String(format: "  %@ %@: age %.0f, button %.0f (icon %.0f); with hint %.0f, without %.0f, icon-only %.0f -> %@",
                         strings.code, name, age, full, icon, withHint, noHint, iconOnly, pick))
        }
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
            reportFooterWidths()
            let config = MenuBarConfig.shared
            // The stock palette, so the pictures match a fresh install.
            config.customizesLimitBarColors = false

            for scheme in [ColorScheme.light, .dark] {
                let name = scheme == .dark ? "dark" : "light"

                write(VStack(spacing: 14) { ForEach(0..<sample.count, id: \.self) { Card(account: sample[$0], config: config, logo: logo) } },
                      width: 380, scheme: scheme, to: "\(out)/accounts-\(name).png")

                write(Card(account: sample[0], config: config, logo: logo),
                      width: 380, scheme: scheme, to: "\(out)/limits-\(name).png")

                write(Chrome(), width: 360, scheme: scheme, to: "\(out)/chrome-\(name).png")

                // Ultra has no 5-hour limit: its card has the Ultra badge and no Session row,
                // next to a Max account that keeps one.
                write(VStack(alignment: .leading, spacing: 12) {
                        Text("Ultra (Max 20x): no 5-hour limit").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: ultraSample, config: config, logo: logo)
                        Text("Max: Session, Weekly and Fable").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: sample[1], config: config, logo: logo)
                      },
                      width: 380, scheme: scheme, to: "\(out)/ultra-\(name).png")

                // Every plan's chip, drawn the way a card and the popover header draw it: on
                // the card fill and on the popover background, then one card per plan.
                write(VStack(alignment: .leading, spacing: 12) {
                        Text("Plan chips: Ultra, Max, Pro, Team, Enterprise, other, No plan")
                            .font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        ForEach([PlanChipTone.ultra, .max, .pro, .team, .enterprise, .other, .noPlan], id: \.self) { tone in
                            let sampleChip: PlanChip? = {
                                switch tone {
                                case .ultra: return chip("max", "max_20x")
                                case .max: return chip("max", "max_5x")
                                case .pro: return chip("pro")
                                case .team: return chip("team")
                                case .enterprise: return chip("enterprise")
                                case .other: return chip("business")
                                case .noPlan: return chip("max", "max_20x", noSubscription: true)
                                }
                            }()
                            HStack(spacing: 10) {
                                if let sampleChip { Badge(text: sampleChip.label, color: sampleChip.tone.fill) }
                                HStack { Spacer(); if let sampleChip { Badge(text: sampleChip.label, color: sampleChip.tone.fill) } }
                                    .padding(8).frame(width: 110)
                                    .background(RoundedRectangle(cornerRadius: 8).fill(.cardFill).strokeBorder(.cardBorder, lineWidth: 1))
                                Spacer()
                            }
                        }
                        ForEach(0..<planSamples.count, id: \.self) { Card(account: planSamples[$0], config: config, logo: logo) }
                      },
                      width: 380, scheme: scheme, to: "\(out)/plans-\(name).png")

                // The Usage card when its sign-in has ended: Re-sign beside the message. The
                // no-subscription card has no button and says No plan.
                write(VStack(alignment: .leading, spacing: 12) {
                        Text("Sign-in ended: Re-sign").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: expiredSample, config: config, logo: logo)
                        Card(account: expiredUltraSample, config: config, logo: logo)
                        Text("Usage service unreachable: Retry").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: unreachableSample, config: config, logo: logo)
                        Text("No active subscription: no button").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: lapsedSample, config: config, logo: logo)
                      },
                      width: 380, scheme: scheme, to: "\(out)/resign-\(name).png")

                // The Limit resets button at the popover's real width (a 360 pt popover leaves a card
                // 328 pt wide; the pictures above are wider): a normal card, an expired card with
                // Re-sign beside the message and Limit resets under it, and the lapsed card with neither.
                write(VStack(alignment: .leading, spacing: 12) {
                        Text("Normal card: Limit resets beside the age").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: sample[1], config: config, logo: logo, relative: "4 min")
                        Text("Sign-in ended: Re-sign stays, Limit resets under it").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: expiredSample, config: config, logo: logo)
                        Text("No active subscription: no Limit resets").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: lapsedSample, config: config, logo: logo)
                      },
                      width: 356, scheme: scheme, to: "\(out)/resets-\(name).png")

                // The tightest the footer gets: the longest age text, the pointer over the card, at the
                // real card width, in English, German and French (their own shipped strings).
                for strings in [FooterStrings.en, .de, .fr] {
                    write(VStack(alignment: .leading, spacing: 12) {
                            Text("Pointer over a card, longest age (\(strings.code))").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                            Card(account: sample[2], config: config, logo: logo, interaction: "hover", relative: strings.longAge, strings: strings)
                          },
                          width: 356, scheme: scheme, to: "\(out)/resets-hover-\(strings.code)-\(name).png")
                }

                // Every state of the footer line in every language, at the real card width: the age
                // is whole in every row; the hint goes first, then the words of the button.
                write(FooterMatrix(), width: 356, scheme: scheme, to: "\(out)/footer-matrix-\(name).png")

                write(autoSwitch(config: config, logo: logo, scheme: scheme),
                      width: 420, scheme: scheme, to: "\(out)/auto-switch-\(name).png")

                write(VStack(alignment: .leading, spacing: 12) {
                        Text("Resting").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: sample[2], config: config, logo: logo)
                        Text("Pointer over it").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: sample[2], config: config, logo: logo, interaction: "hover")
                        Text("Double-clicked").font(.caption.weight(.semibold)).foregroundStyle(.textSecondary)
                        Card(account: sample[2], config: config, logo: logo, interaction: "switching")
                      },
                      width: 400, scheme: scheme, to: "\(out)/switch-states-\(name).png")
            }
        }
    }
}
