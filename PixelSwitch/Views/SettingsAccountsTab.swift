import SwiftUI

/// Settings → Accounts: every account in priority order.
///
/// The order here IS the accounts list's order: dragging a row changes the
/// order "My order" follows and the order every list in the app shows (the
/// popover's Accounts and Usage tabs, the widget). Each row also carries the
/// account's weekly reset and its own auto-switch threshold.
///
/// Add Current Account and Sign In New Account sit under the list
/// (`SettingsSignInButtons`); both open the same sign-in window as the popover.
struct SettingsAccountsTab: View {
    @EnvironmentObject private var appState: AppState
    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @AppStorage(AutoSwitchSettings.thresholdKey) private var defaultThreshold = AutoSwitchSettings.fallbackThreshold
    @AppStorage(AutoSwitchSettings.enabledKey) private var autoSwitchEnabled = false

    /// The thresholds the menu offers: 10–40 in 10s, then 50–100 in 5s; the
    /// stepper beside a custom value reaches every whole number from 1 to 100.
    /// Manual only (0) is its own named item, never a stepper stop.
    private static let thresholdChoices: [Double] = [10, 20, 30, 40] + Array(stride(from: 50.0, through: 100.0, by: 5.0))

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if appState.accounts.isEmpty {
                VStack(spacing: 8) {
                    Text("No Accounts")
                        .font(.headline)
                    Text("Add your current Claude Code account to get started.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(appState.accounts) { account in
                        row(account)
                    }
                    .onMove { source, destination in
                        appState.moveAccounts(fromOffsets: source, toOffset: destination)
                    }
                }
                .listStyle(.inset(alternatesRowBackgrounds: true))
            }

            if nowhereToGo {
                Text("Every account except the one you're using is Manual only, so auto-switch has nowhere to move you.")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only. Manual only accounts are never switched to automatically; you can still switch to them yourself.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            SettingsSignInButtons()
        }
        .padding()
    }

    // MARK: - Row

    private func row(_ account: Account) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(primaryLabel(for: account))
                        .font(.body.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    if account.isActive {
                        Badge(text: String(localized: "Active", bundle: L10n.bundle), color: .green)
                            .fixedSize()
                    }
                }
                if let label = account.customLabel, !label.isEmpty {
                    Text(account.displayEmail(obfuscated: maskEmails))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                weeklyReset(for: account)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if autoSwitchEnabled && account.isActive && isManualOnly(account) {
                    Text("You're using it now. Auto-switch moves you off it at \(Self.percent(shownDefault)) and won't move you back to it.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            thresholdControl(for: account)
        }
        .padding(.vertical, 4)
    }

    /// A custom label if one is set, otherwise the address (masked when the
    /// user has asked for that), as on the popover's Accounts tab.
    private func primaryLabel(for account: Account) -> String {
        if let label = account.customLabel, !label.isEmpty { return label }
        return account.displayEmail(obfuscated: maskEmails)
    }

    /// When this account's weekly window resets, if a reading says so. A
    /// reading from a week that has already ended says nothing about the next.
    @ViewBuilder
    private func weeklyReset(for account: Account) -> some View {
        let usage = appState.accountUsage[account.id]
        if AutoSwitchEngine.weeklyReset(usage, limit: .windows) != nil,
           let window = usage?.sevenDay, let when = window.resetTimeString {
            if window.resetIsAbsolute {
                Text("Weekly resets \(when)")
            } else {
                Text("Weekly resets in \(when)")
            }
        } else {
            Text("Weekly reset not known yet")
        }
    }

    // MARK: - Threshold

    /// The default threshold as auto-switch reads it (unset or 0 is 90; kept
    /// within 50–100). Reads the @AppStorage value so rows redraw when the
    /// General slider moves.
    private var shownDefault: Double {
        AutoSwitchSettings.clampedThreshold(defaultThreshold == 0 ? AutoSwitchSettings.fallbackThreshold : defaultThreshold)
    }

    private func isManualOnly(_ account: Account) -> Bool {
        AutoSwitchSettings.isManualOnly(own: account.switchThreshold)
    }

    /// Auto-switch on, an account active, and every other account Manual only.
    private var nowhereToGo: Bool {
        guard autoSwitchEnabled, appState.accounts.count >= 2, appState.accounts.contains(where: \.isActive) else { return false }
        return appState.accounts.filter { !$0.isActive }.allSatisfy(isManualOnly)
    }

    /// A percentage with at most one decimal, in the user's locale ("5%", "2.5%").
    private static func percent(_ value: Double) -> String {
        (value / 100).formatted(.percent.precision(.fractionLength(0...1)))
    }

    private func thresholdControl(for account: Account) -> some View {
        let own = AutoSwitchSettings.normalizedAccountThreshold(account.switchThreshold)
        return HStack(spacing: 4) {
            Menu {
                Button("Default (\(Int(shownDefault))%)") {
                    appState.setSwitchThreshold(nil, for: account)
                }
                Divider()
                Button("Manual only (\(Int(AutoSwitchSettings.manualOnlyThreshold))%)") {
                    appState.setSwitchThreshold(AutoSwitchSettings.manualOnlyThreshold, for: account)
                }
                Divider()
                ForEach(Self.thresholdChoices, id: \.self) { value in
                    Button {
                        appState.setSwitchThreshold(value, for: account)
                    } label: {
                        Text(verbatim: "\(Int(value))%")
                    }
                }
            } label: {
                if own == AutoSwitchSettings.manualOnlyThreshold {
                    Text("Manual only")
                } else if let own {
                    Text("Switch at \(Int(own))%")
                } else {
                    Text("Default (\(Int(shownDefault))%)")
                }
            }
            .fixedSize()
            .help(thresholdHelp(own: own))

            if let own, own > AutoSwitchSettings.manualOnlyThreshold {
                Stepper("", value: customThreshold(for: account), in: AutoSwitchSettings.accountStepperRange, step: 1)
                    .labelsHidden()
            }
        }
    }

    /// What the account's setting does, in numbers from the engine's own rule.
    private func thresholdHelp(own: Double?) -> Text {
        if own == AutoSwitchSettings.manualOnlyThreshold {
            return Text("Auto-switch never moves you to this account. You can still switch to it yourself; while you're on it, auto-switch moves you off it at the default threshold (\(Self.percent(shownDefault))).")
        }
        let leaveAt = own ?? shownDefault
        let arriveAt = AutoSwitchEngine.ceiling(threshold: leaveAt, room: AutoSwitchEngine.hysteresis) ?? 0
        return Text("Auto-switch moves you off this account at \(Self.percent(leaveAt)), and moves you to it only while it is at \(Self.percent(arriveAt)) or less.")
    }

    /// The stepper's binding: the account's own threshold, saved on change.
    private func customThreshold(for account: Account) -> Binding<Double> {
        Binding(
            get: { appState.effectiveSwitchThreshold(for: account) },
            set: { appState.setSwitchThreshold($0, for: account) }
        )
    }
}
