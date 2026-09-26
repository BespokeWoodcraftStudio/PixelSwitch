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

    /// The thresholds the menu offers, in 5-point steps; the stepper beside a
    /// custom value reaches every whole number in between.
    private static let thresholdChoices: [Double] = Array(stride(from: 50.0, through: 100.0, by: 5.0))

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

            Text("Drag an account to change the order. \"My order\" in Settings → General tries accounts from the top down, and every account list follows this order. A threshold other than the default applies to that account only.")
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

    private func thresholdControl(for account: Account) -> some View {
        HStack(spacing: 4) {
            Menu {
                Button("Default (\(Int(defaultThreshold))%)") {
                    appState.setSwitchThreshold(nil, for: account)
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
                if let own = account.switchThreshold {
                    Text("Switch at \(Int(own))%")
                } else {
                    Text("Default (\(Int(defaultThreshold))%)")
                }
            }
            .fixedSize()
            .help("When this account's usage reaches this level, auto-switch moves you to another account.")

            if account.switchThreshold != nil {
                Stepper("", value: customThreshold(for: account), in: AutoSwitchSettings.thresholdRange, step: 1)
                    .labelsHidden()
            }
        }
    }

    /// The stepper's binding: the account's own threshold, saved on change.
    private func customThreshold(for account: Account) -> Binding<Double> {
        Binding(
            get: { appState.effectiveSwitchThreshold(for: account) },
            set: { appState.setSwitchThreshold($0, for: account) }
        )
    }
}
