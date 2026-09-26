import AppKit
import Combine

/// The real `AppControlling`: the control API's view of the running app.
/// Every action goes through the same `AppState` methods the popover and the
/// Settings window use, so the GUI shows each change at once. Nothing here
/// reads or returns a login token.
@MainActor
final class AppController: AppControlling {
    private let appState: AppState
    private let settings: SettingsStore
    private let updateChecker: UpdateChecker
    /// Opens a sign-in's automatic link when it arrives, if the caller asked.
    private var openWhenLinkArrives: AnyCancellable?

    init(appState: AppState, settings: SettingsStore = .shared, updateChecker: UpdateChecker) {
        self.appState = appState
        self.settings = settings
        self.updateChecker = updateChecker
    }

    // MARK: - Reading

    var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
    }

    var claudeAvailable: Bool { appState.claudeAvailable }
    var lastUsageRefresh: Date? { appState.lastUsageRefresh }
    var accounts: [Account] { appState.accounts }

    var autoSwitch: AutoSwitchInfo {
        AutoSwitchInfo(
            enabled: UserDefaults.standard.bool(forKey: AutoSwitchSettings.enabledKey),
            defaultThreshold: AutoSwitchSettings.defaultThreshold,
            onFable: AutoSwitchFableSetting.isOn,
            strategy: AutoSwitchSettings.strategy.rawValue,
            drainEarly: AutoSwitchSettings.drainEarly,
            drainWithinHours: AutoSwitchSettings.drainWithinHours
        )
    }

    var machineUsage: MachineUsageInfo {
        let cost = appState.costSummary
        let activity = appState.activityStats
        return MachineUsageInfo(
            todayCost: cost.todayCost,
            totalCost: cost.totalCost,
            conversationTurns: activity.conversationTurns,
            activeCodingMinutes: activity.activeCodingMinutes,
            linesWritten: activity.linesWritten,
            modelUsage: activity.modelUsage
        )
    }

    var signIn: SignInInfo? {
        appState.currentSignIn.map(ControlSnapshots.signInInfo)
    }

    func usage(for id: UUID) -> UsageAPIResponse? { appState.accountUsage[id] }
    func usageSampledAt(for id: UUID) -> Date? { appState.accountUsageSampledAt[id] }
    func usageError(for id: UUID) -> String? { appState.accountUsageErrors[id]?.message }

    func isSwitchable(_ id: UUID) -> Bool {
        guard let account = appState.accounts.first(where: { $0.id == id }) else { return false }
        return appState.isSwitchable(account)
    }

    func effectiveSwitchThreshold(for account: Account) -> Double {
        appState.effectiveSwitchThreshold(for: account)
    }

    // MARK: - Actions

    func switchAccount(to id: UUID) async throws {
        let account = try self.account(id)
        do {
            try await appState.performSwitch(to: account)
        } catch let error as AppState.ActionError {
            throw Self.controlError(error)
        }
    }

    func addCurrentAccount() async throws -> UUID {
        try refuseWhileBusy()
        do {
            return try await appState.performAddCurrentAccount().id
        } catch let error as AppState.ActionError {
            throw Self.controlError(error)
        }
    }

    func startSignIn(_ purpose: SignInPurpose, open: String?) throws -> SignInInfo {
        var browser: BrowserApp?
        var openIt = false
        if let open {
            openIt = true
            if open.caseInsensitiveCompare("default") != .orderedSame {
                let installed = BrowserOpener.installedBrowsers()
                guard let match = installed.first(where: {
                    $0.name.caseInsensitiveCompare(open) == .orderedSame || $0.id.caseInsensitiveCompare(open) == .orderedSame
                }) else {
                    throw ControlError(.invalidValue, "No browser called \"\(open)\" is installed on this Mac.",
                                       candidates: ["default"] + installed.map(\.name))
                }
                browser = match
            }
        }
        let session: SignInSession
        do {
            session = try appState.startSignIn(purpose)
        } catch let error as AppState.SignInStartError {
            switch error {
            case .busy: throw ControlError(.busy, "A switch or sign-in is in progress. Try again in a moment.")
            case .claudeUnavailable: throw ControlError(.claudeUnavailable, "The claude command could not be found on this Mac.")
            }
        }
        if openIt {
            openWhenLinkArrives = session.$automaticLink
                .compactMap { $0 }
                .first()
                .receive(on: DispatchQueue.main)
                .sink { url in MainActor.assumeIsolated { _ = BrowserOpener.open(url, in: browser) } }
        }
        return ControlSnapshots.signInInfo(session)
    }

    func submitSignInCode(_ code: String) throws -> SignInInfo {
        guard let session = appState.currentSignIn, !session.state.isFinished else {
            throw ControlError(.notFound, "No sign-in is running.")
        }
        guard session.state == .waitingForUser, !session.codeSubmitted else {
            throw ControlError(.invalidValue, "This sign-in is not waiting for a code.")
        }
        guard session.submitCode(code) else {
            throw ControlError(.invalidValue, "That isn't the whole code. Copy all of it, including the part after #.")
        }
        return ControlSnapshots.signInInfo(session)
    }

    func cancelSignIn() throws -> SignInInfo {
        guard let session = appState.currentSignIn, !session.state.isFinished else {
            throw ControlError(.notFound, "No sign-in is running.")
        }
        guard session.state != .completing else {
            throw ControlError(.busy, "The account is being saved and cannot be cancelled now.")
        }
        session.cancel()
        return ControlSnapshots.signInInfo(session)
    }

    func removeAccount(_ id: UUID) throws {
        let account = try self.account(id)
        try refuseWhileBusy()
        appState.removeAccount(account)
    }

    func setLabel(_ label: String?, for id: UUID) {
        guard let account = try? self.account(id) else { return }
        appState.updateAccountLabel(account, label: label)
    }

    func setSwitchThreshold(_ threshold: Double?, for id: UUID) {
        guard let account = try? self.account(id) else { return }
        appState.setSwitchThreshold(threshold, for: account)
    }

    func setAccountOrder(_ ids: [UUID]) -> Bool {
        appState.setAccountOrder(ids)
    }

    func refreshUsage() async {
        await appState.refresh()
    }

    func settingValue(_ key: SettingKey) -> JSONValue {
        settings.value(key)
    }

    func setSetting(_ key: SettingKey, to value: JSONValue) throws {
        try settings.set(key, to: value)
    }

    func checkForUpdates() {
        updateChecker.checkForUpdates(manual: true)
    }

    /// Quits after the reply has had time to reach the caller.
    func quit() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
    }

    // MARK: - Helpers

    private func account(_ id: UUID) throws -> Account {
        guard let account = appState.accounts.first(where: { $0.id == id }) else {
            throw ControlError(.notFound, "That account no longer exists.")
        }
        return account
    }

    private func refuseWhileBusy() throws {
        if appState.isSwitching || appState.isLoggingIn {
            throw ControlError(.busy, "A switch or sign-in is in progress. Try again in a moment.")
        }
    }

    private static func controlError(_ error: AppState.ActionError) -> ControlError {
        switch error.kind {
        case .busy: return ControlError(.busy, error.message)
        case .claudeUnavailable: return ControlError(.claudeUnavailable, error.message)
        case .noActiveAccount, .failed: return ControlError(.failed, error.message)
        }
    }
}
