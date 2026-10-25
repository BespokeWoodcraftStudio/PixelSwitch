import SwiftUI

/// Settings → Accounts: every account in priority order.
///
/// The order here IS the accounts list's order, which is the priority order
/// (2.0): rows are sorted by their priority number, and dragging a row numbers
/// every account from 1 in the new order. Every list in the app (the popover's
/// Accounts and Usage tabs, the widget) follows it, and "Priority" in Settings →
/// General tries accounts from the lowest number up. Each row (`SettingsAccountRow`)
/// carries the account's priority, its weekly reset, and its Session and Weekly
/// auto-switch limits.
///
/// Add Current Account and Sign In New Account sit under the list
/// (`SettingsSignInButtons`); both open the same sign-in window as the popover.
struct SettingsAccountsTab: View {
    @EnvironmentObject private var appState: AppState
    @AppStorage(EmailDisplay.key) private var maskEmails = false
    // The three stored defaults, read here so the rows redraw when the General
    // sliders move. 0 (the value of an absent key) is "not set" to the readers.
    @AppStorage(AutoSwitchSettings.thresholdKey) private var baseThreshold = 0.0
    @AppStorage(AutoSwitchSettings.sessionThresholdKey) private var sessionDefault = 0.0
    @AppStorage(AutoSwitchSettings.weeklyThresholdKey) private var weeklyDefault = 0.0
    @AppStorage(AutoSwitchSettings.enabledKey) private var autoSwitchEnabled = false
    @AppStorage(AutoSwitchSettings.strategyKey) private var strategyName = AutoSwitchStrategy.mostRoom.rawValue

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

            VStack(alignment: .leading, spacing: 4) {
                Text("Lower numbers are tried first. Dragging renumbers every account from 1.")
                Text("Manual only accounts are never switched to automatically; you can still switch to them yourself.")
                if strategy != .myOrder {
                    HStack(spacing: 8) {
                        Text("Auto-switch follows these numbers only when it chooses by Priority.")
                        Button("Use priority") { strategyName = AutoSwitchStrategy.myOrder.rawValue }
                            .controlSize(.small)
                    }
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            SettingsSignInButtons()
        }
        .padding()
    }

    // MARK: - Row

    private func row(_ account: Account) -> some View {
        SettingsAccountRow(
            title: primaryLabel(for: account),
            subtitle: (account.customLabel ?? "").isEmpty ? nil : account.displayEmail(obfuscated: maskEmails),
            isActive: account.isActive,
            activeBadge: String(localized: "Active", bundle: L10n.bundle),
            resetNote: weeklyReset(for: account),
            manualNote: autoSwitchEnabled && account.isActive && isManualOnly(account) ? manualNote : nil,
            priority: AccountOrder.validPriority(account.priority),
            sessionOwn: AccountRowThresholds.own(.session, of: account),
            weeklyOwn: AccountRowThresholds.own(.weekly, of: account),
            manualOnly: isManualOnly(account),
            defaults: defaults,
            help: thresholdHelp(for: account),
            onPriority: { appState.setPriority($0, for: account) },
            onSession: { choose($0, window: .session, for: account) },
            onWeekly: { choose($0, window: .weekly, for: account) }
        )
    }

    /// A custom label if one is set, otherwise the address (masked when the
    /// user has asked for that), as on the popover's Accounts tab.
    private func primaryLabel(for account: Account) -> String {
        if let label = account.customLabel, !label.isEmpty { return label }
        return account.displayEmail(obfuscated: maskEmails)
    }

    /// When this account's weekly window resets, if a reading says so. A
    /// reading from a week that has already ended says nothing about the next.
    private func weeklyReset(for account: Account) -> Text {
        let usage = appState.accountUsage[account.id]
        if AutoSwitchEngine.weeklyReset(usage, limit: .windows) != nil,
           let window = usage?.sevenDay, let when = window.resetTimeString {
            return window.resetIsAbsolute ? Text("Weekly resets \(when)") : Text("Weekly resets in \(when)")
        }
        return Text("Weekly reset not known yet")
    }

    // MARK: - Thresholds

    /// The two defaults as auto-switch reads them (spec 2.2), from the stored
    /// values this view watches.
    private var defaults: AutoSwitchEngine.WindowPair {
        AutoSwitchSettings.windowDefaults(base: baseThreshold, session: sessionDefault, weekly: weeklyDefault)
    }

    private var strategy: AutoSwitchStrategy { AutoSwitchStrategy(rawValue: strategyName) ?? .mostRoom }

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

    /// What a Session or Weekly menu choice does. Default on a Manual only row
    /// clears Manual only; Manual only in either menu makes the whole account
    /// Manual only; a number sets just that window.
    private func choose(_ choice: ThresholdMenuChoice, window: AutoSwitchEngine.Window, for account: Account) {
        switch choice {
        case .followDefault:
            appState.chooseDefaultThreshold(window, for: account)
        case .manualOnly:
            appState.setSwitchThreshold(AutoSwitchSettings.manualOnlyThreshold, for: account)
        case .percent(let value):
            appState.setWindowThresholds(session: window == .session ? .set(value) : .leave,
                                         weekly: window == .weekly ? .set(value) : .leave, for: account)
        }
    }

    /// The line under a Manual only account that is in use.
    private var manualNote: Text {
        if defaults.session == defaults.weekly {
            return Text("You're using it now. Auto-switch moves you off it at \(Self.percent(defaults.session)) and won't move you back to it.")
        }
        return Text("You're using it now. Auto-switch moves you off it at \(Self.percent(defaults.session)) of its session or \(Self.percent(defaults.weekly)) of its week and won't move you back to it.")
    }

    /// What the account's setting does, in numbers from the engine's own rule.
    /// With equal windows it is 1.9's sentence, word for word.
    private func thresholdHelp(for account: Account) -> Text {
        let rule = AutoSwitchSettings.rule(for: account, defaults: defaults)
        if rule.manualOnly {
            if defaults.session == defaults.weekly {
                return Text("Auto-switch never moves you to this account. You can still switch to it yourself; while you're on it, auto-switch moves you off it at the default threshold (\(Self.percent(defaults.session))).")
            }
            return Text("Auto-switch never moves you to this account. You can still switch to it yourself; while you're on it, auto-switch moves you off it at the default thresholds (\(Self.percent(defaults.session)) of its session or \(Self.percent(defaults.weekly)) of its week).")
        }
        guard let numbers = AutoSwitchSettings.windowExplanation(rule: rule, defaults: defaults) else { return Text(verbatim: "") }
        if rule.session == rule.weekly {
            return Text("Auto-switch moves you off this account at \(Self.percent(numbers.leaveAt.session)), and moves you to it only while it is at \(Self.percent(numbers.arriveAt.session)) or less.")
        }
        // The overflow clause only when it names a different number: the weekly
        // limit is raised to the session limit, so it differs only when the
        // weekly limit is the lower of the two.
        if numbers.relaxedArriveWeekly > numbers.arriveAt.weekly {
            return Text("Auto-switch moves you off this account at \(Self.percent(numbers.leaveAt.session)) of its session or \(Self.percent(numbers.leaveAt.weekly)) of its week, and moves you to it only while it is at \(Self.percent(numbers.arriveAt.session)) or less of its session and \(Self.percent(numbers.arriveAt.weekly)) or less of its week (up to \(Self.percent(numbers.relaxedArriveWeekly)) of its week when no account under its weekly limit can take you).")
        }
        return Text("Auto-switch moves you off this account at \(Self.percent(numbers.leaveAt.session)) of its session or \(Self.percent(numbers.leaveAt.weekly)) of its week, and moves you to it only while it is at \(Self.percent(numbers.arriveAt.session)) or less of its session and \(Self.percent(numbers.arriveAt.weekly)) or less of its week.")
    }
}
