import SwiftUI

/// What the Session or Weekly menu on an account row can choose.
enum ThresholdMenuChoice: Equatable {
    /// "Default (N%)": this window follows its default again.
    case followDefault
    /// "Manual only": auto-switch never moves you to the account (both windows).
    case manualOnly
    /// "Switch at N%": the window's own limit, 1–100.
    case percent(Double)
}

/// What a row shows about an account's thresholds, from the account alone.
enum AccountRowThresholds {
    /// The menu's choices: 10–40 in 10s, then 50–100 in 5s; the stepper beside
    /// a custom value reaches every whole number from 1 to 100. Manual only (0)
    /// is its own named item, never a stepper stop.
    static let choices: [Double] = [10, 20, 30, 40] + Array(stride(from: 50.0, through: 100.0, by: 5.0))

    /// The account's own value for one window, or nil while that window follows
    /// the default: its window value, else its both-windows value. Nil for a
    /// Manual only account (spec 2.4: the menu then reads "Manual only").
    static func own(_ window: AutoSwitchEngine.Window, of account: Account) -> Double? {
        guard !AutoSwitchSettings.isManualOnly(own: account.switchThreshold) else { return nil }
        let windowValue = window == .session ? account.sessionThreshold : account.weeklyThreshold
        if let value = AutoSwitchSettings.normalizedWindowThreshold(windowValue) { return value }
        if let both = AutoSwitchSettings.normalizedAccountThreshold(account.switchThreshold),
           both > AutoSwitchSettings.manualOnlyThreshold { return both }
        return nil
    }
}

/// One account's row in Settings → Accounts: drag handle, Priority control,
/// the account, and the Session and Weekly menus.
///
/// Plain inputs and closures only (no `AppState`, no `@AppStorage`), so the
/// same view that Settings shows is rendered offscreen with sample data
/// (`Tools/screenshots/make_settings_renders.swift`) and nothing can drift.
struct SettingsAccountRow: View {
    /// A custom label, else the address.
    let title: String
    /// The address, shown under a label.
    var subtitle: String?
    var isActive = false
    /// The localized word on the green badge.
    var activeBadge = "Active"
    /// "Weekly resets Mon 11:00 AM" and the like.
    let resetNote: Text
    /// The line for a Manual only account that is in use.
    var manualNote: Text?

    /// 1–99, nil = none.
    var priority: Int?
    /// The account's own limit for each window; nil = follows its default.
    var sessionOwn: Double?
    var weeklyOwn: Double?
    var manualOnly = false
    /// What the two windows follow while the account has no value of its own.
    let defaults: AutoSwitchEngine.WindowPair
    /// What the account's setting does, in numbers.
    let help: Text

    let onPriority: @MainActor (Int?) -> Void
    let onSession: @MainActor (ThresholdMenuChoice) -> Void
    let onWeekly: @MainActor (ThresholdMenuChoice) -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)

            priorityControl
                .frame(width: 92, alignment: .leading)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(verbatim: title)
                        .font(.body.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    if isActive {
                        Badge(text: activeBadge, color: .green)
                            .fixedSize()
                    }
                }
                if let subtitle {
                    Text(verbatim: subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                resetNote
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let manualNote {
                    manualNote
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            windowControl(.session, own: sessionOwn, caption: Text("Session"), choose: onSession)
            windowControl(.weekly, own: weeklyOwn, caption: Text("Weekly"), choose: onWeekly)
        }
        .padding(.vertical, 4)
    }

    // MARK: - Priority

    private var priorityControl: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Priority")
                .font(.caption2)
                .foregroundStyle(.secondary)
            HStack(spacing: 4) {
                Menu {
                    Button("None") { onPriority(nil) }
                    Divider()
                    ForEach(1...9, id: \.self) { number in
                        Button { onPriority(number) } label: { Text(verbatim: "P\(number)") }
                    }
                } label: {
                    if let priority {
                        Text(verbatim: "P\(priority)").monospacedDigit()
                    } else {
                        Text("None")
                    }
                }
                .fixedSize()
                .help(Text("Auto-switch tries accounts with lower numbers first when it chooses by Priority. Accounts with no number come last."))

                if let priority {
                    Stepper("", onIncrement: { if priority < 99 { onPriority(priority + 1) } },
                            onDecrement: { if priority > 1 { onPriority(priority - 1) } })
                        .labelsHidden()
                }
            }
        }
    }

    // MARK: - Session and Weekly

    private func windowControl(_ window: AutoSwitchEngine.Window, own: Double?, caption: Text,
                               choose: @escaping @MainActor (ThresholdMenuChoice) -> Void) -> some View {
        let shownDefault = Int(defaults.value(for: window))
        return VStack(alignment: .leading, spacing: 2) {
            caption
                .font(.caption2)
                .foregroundStyle(.secondary)
            HStack(spacing: 4) {
                Menu {
                    Button("Default (\(shownDefault)%)") { choose(.followDefault) }
                    Divider()
                    Button("Manual only (\(Int(AutoSwitchSettings.manualOnlyThreshold))%)") { choose(.manualOnly) }
                    Divider()
                    ForEach(AccountRowThresholds.choices, id: \.self) { value in
                        Button { choose(.percent(value)) } label: { Text(verbatim: "\(Int(value))%") }
                    }
                } label: {
                    if manualOnly {
                        Text("Manual only")
                    } else if let own {
                        Text("Switch at \(Int(own))%")
                    } else {
                        Text("Default (\(shownDefault)%)")
                    }
                }
                .fixedSize()
                .help(help)

                if let own, !manualOnly {
                    Stepper("", onIncrement: { if own < AutoSwitchSettings.accountStepperRange.upperBound { choose(.percent(own + 1)) } },
                            onDecrement: { if own > AutoSwitchSettings.accountStepperRange.lowerBound { choose(.percent(own - 1)) } })
                        .labelsHidden()
                }
            }
        }
        // One width for both columns on every row, so Session and Weekly line up
        // down the list whether or not a row shows a stepper.
        .frame(width: Self.windowColumnWidth, alignment: .leading)
    }

    /// Wide enough for "Switch at 100%" or "Default (100%)" and its stepper.
    private static let windowColumnWidth: CGFloat = 164
}
