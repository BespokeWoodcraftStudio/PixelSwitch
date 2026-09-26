import Foundation

/// What the control API needs from the app. `AppController` is the real one;
/// the unit tests use a fake. Nothing here can hand out a login token: the
/// protocol only ever exposes `AppState`-level data.
@MainActor
protocol AppControlling: AnyObject {
    var appVersion: String { get }
    var claudeAvailable: Bool { get }
    var lastUsageRefresh: Date? { get }
    /// In priority order.
    var accounts: [Account] { get }
    var autoSwitch: AutoSwitchInfo { get }
    var machineUsage: MachineUsageInfo { get }
    /// The sign-in in progress, or the last one until it is dismissed.
    var signIn: SignInInfo? { get }

    func usage(for id: UUID) -> UsageAPIResponse?
    func usageSampledAt(for id: UUID) -> Date?
    func usageError(for id: UUID) -> String?
    func isSwitchable(_ id: UUID) -> Bool
    func effectiveSwitchThreshold(for account: Account) -> Double

    /// Returns once the switch has finished. Throws `ControlError`.
    func switchAccount(to id: UUID) async throws
    /// Saves the account Claude Code is signed in as; returns its id.
    func addCurrentAccount() async throws -> UUID
    /// `open`: nil for none, "default", or a browser's name.
    func startSignIn(_ purpose: SignInPurpose, open: String?) throws -> SignInInfo
    func submitSignInCode(_ code: String) throws -> SignInInfo
    func cancelSignIn() throws -> SignInInfo
    func removeAccount(_ id: UUID) throws
    func setLabel(_ label: String?, for id: UUID)
    func setSwitchThreshold(_ threshold: Double?, for id: UUID)
    func setAccountOrder(_ ids: [UUID]) -> Bool
    func refreshUsage() async
    func settingValue(_ key: SettingKey) -> JSONValue
    /// `value` is already normalized by `SettingKey.normalize`.
    func setSetting(_ key: SettingKey, to value: JSONValue) throws
    func checkForUpdates()
    func quit()
}

/// Turns one request line into one response line. Pure apart from the
/// `AppControlling` it is given, so every method is unit-tested with a fake.
@MainActor
final class ControlAPI {
    private let controller: any AppControlling
    private let log: @MainActor (String) -> Void

    init(controller: any AppControlling, log: @escaping @MainActor (String) -> Void = { _ in }) {
        self.controller = controller
        self.log = log
    }

    /// The response line for `line`, or nil when `line` is a notification.
    /// `onSubscribe` runs when the request is `events.subscribe`.
    func handle(_ line: String, onSubscribe: @MainActor () -> Void = {}) async -> String? {
        let request: RPCRequest
        do {
            request = try ControlCoding.decoder.decode(RPCRequest.self, from: Data(line.utf8))
        } catch {
            log("[control] unreadable request")
            return RPCResponse.failure(nil, ControlError(.parse, "That is not a JSON-RPC request.")).line
        }
        guard request.jsonrpc == "2.0" else {
            return RPCResponse.failure(request.id, ControlError(.invalidRequest, "jsonrpc must be \"2.0\".")).line
        }
        var detail = ""
        do {
            let result = try await dispatch(request, detail: &detail, onSubscribe: onSubscribe)
            log("[control] \(request.method)\(detail) ok")
            return request.id == nil ? nil : RPCResponse.success(request.id, result).line
        } catch let error as ControlError {
            log("[control] \(request.method)\(detail) \(error.kind.rawValue)")
            return request.id == nil ? nil : RPCResponse.failure(request.id, error).line
        } catch {
            log("[control] \(request.method)\(detail) failed")
            return request.id == nil ? nil : RPCResponse.failure(request.id, ControlError(.failed, error.localizedDescription)).line
        }
    }

    // MARK: - Dispatch

    private func dispatch(_ request: RPCRequest, detail: inout String, onSubscribe: @MainActor () -> Void) async throws -> JSONValue {
        guard let method = ControlMethod(rawValue: request.method) else {
            throw ControlError(.methodNotFound, "There is no method called \"\(request.method)\".",
                               candidates: ControlMethod.allCases.map(\.rawValue))
        }
        switch method {
        case .statusGet:
            return try .encode(status())

        case .accountsList:
            return .object(["accounts": try .encode(accountInfos())])

        case .accountsSwitch:
            let target = try resolve(params(request, AccountParams.self).account)
            detail = " \(target.id.uuidString)"
            try await controller.switchAccount(to: target.id)
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsAddCurrent:
            let id = try await controller.addCurrentAccount()
            detail = " \(id.uuidString)"
            return .object(["account": try .encode(info(for: id))])

        case .signInStart:
            let p = try params(request, SignInStartParams.self)
            let purpose: SignInPurpose
            if let reference = p.account {
                let account = try resolve(reference)
                detail = " reauthenticate \(account.id.uuidString)"
                purpose = .reauthenticate(accountId: account.id, email: account.email)
            } else {
                detail = " newAccount"
                purpose = .newAccount
            }
            let open = p.open.map { $0.trimmingCharacters(in: .whitespaces) }.flatMap { $0.isEmpty || $0.lowercased() == "none" ? nil : $0 }
            return .object(["signIn": try .encode(controller.startSignIn(purpose, open: open))])

        case .signInStatus:
            return .object(["signIn": try controller.signIn.map { try JSONValue.encode($0) } ?? JSONValue.null])

        case .signInSubmitCode:
            return .object(["signIn": try .encode(controller.submitSignInCode(params(request, SignInCodeParams.self).code))])

        case .signInCancel:
            return .object(["signIn": try .encode(controller.cancelSignIn())])

        case .accountsRemove:
            let target = try resolve(params(request, AccountParams.self).account)
            detail = " \(target.id.uuidString)"
            let before = info(for: target.id)
            try controller.removeAccount(target.id)
            return .object(["removed": try .encode(before)])

        case .accountsSetLabel:
            let p = try params(request, LabelParams.self)
            let target = try resolve(p.account)
            detail = " \(target.id.uuidString)"
            let label = p.label?.trimmingCharacters(in: .whitespacesAndNewlines)
            if let label, label.count > 100 {
                throw ControlError(.invalidValue, "A label can be at most 100 characters.")
            }
            controller.setLabel(label?.isEmpty == true ? nil : label, for: target.id)
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsSetThreshold:
            let p = try params(request, ThresholdParams.self)
            let target = try resolve(p.account)
            detail = " \(target.id.uuidString)"
            var threshold = p.threshold
            if let value = threshold {
                guard value.isFinite, AutoSwitchSettings.accountThresholdRange.contains(value.rounded()) else {
                    throw ControlError(.invalidValue, "A threshold must be 1–100, 0 for manual only, or null to follow the default.")
                }
                threshold = value.rounded()
            }
            controller.setSwitchThreshold(threshold, for: target.id)
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsSetOrder:
            let listed = try AccountResolver.resolveAll(params(request, OrderParams.self).accounts, in: controller.accounts)
            let ids = listed.map(\.id)
            guard Set(ids).count == ids.count, Set(ids) == Set(controller.accounts.map(\.id)) else {
                throw ControlError(.invalidValue, "List every account exactly once, in the order you want.",
                                   candidates: controller.accounts.map(AccountResolver.describe))
            }
            guard controller.setAccountOrder(ids) else {
                throw ControlError(.invalidValue, "The accounts changed while the order was being set. Try again.")
            }
            return .object(["accounts": try .encode(accountInfos())])

        case .usageGet:
            let p = try params(request, UsageParams.self)
            return try .encode(usageReport(only: p.account.map(resolve)))

        case .usageRefresh:
            await controller.refreshUsage()
            return try .encode(usageReport(only: nil))

        case .settingsGet:
            let p = try params(request, SettingGetParams.self)
            let keys = try p.key.map { [try SettingKey.named($0)] } ?? SettingKey.allCases
            detail = p.key.map { " \($0)" } ?? ""
            return .object(["settings": .object(Dictionary(uniqueKeysWithValues: keys.map { ($0.rawValue, controller.settingValue($0)) }))])

        case .settingsSet:
            let p = try params(request, SettingSetParams.self)
            let key = try SettingKey.named(p.key)
            detail = " \(key.rawValue)"
            try controller.setSetting(key, to: key.normalize(p.value))
            return .object(["settings": .object([key.rawValue: controller.settingValue(key)])])

        case .eventsSubscribe:
            onSubscribe()
            return .object(["subscribed": .bool(true)])

        case .appCheckForUpdates:
            controller.checkForUpdates()
            return .object(["ok": .bool(true)])

        case .appQuit:
            controller.quit()
            return .object(["ok": .bool(true)])
        }
    }

    // MARK: - Building results

    private func params<T: Decodable>(_ request: RPCRequest, _ type: T.Type) throws -> T {
        do {
            return try (request.params ?? .object([:])).decode(T.self)
        } catch {
            throw ControlError(.invalidParams, "The parameters for \(request.method) are missing or the wrong shape.")
        }
    }

    private func resolve(_ reference: String) throws -> Account {
        try AccountResolver.resolve(reference, in: controller.accounts)
    }

    private func status() -> StatusInfo {
        let active = controller.accounts.first(where: \.isActive)
        return StatusInfo(
            appVersion: controller.appVersion,
            protocolVersion: ControlProtocol.version,
            activeAccountId: active?.id.uuidString,
            activeAccountEmail: active?.email,
            accountCount: controller.accounts.count,
            claudeAvailable: controller.claudeAvailable,
            lastRefresh: controller.lastUsageRefresh,
            autoSwitch: controller.autoSwitch,
            signIn: controller.signIn
        )
    }

    private func accountInfos() -> [AccountInfo] {
        controller.accounts.enumerated().map { index, account in makeInfo(account, position: index + 1) }
    }

    /// The account's current info, or nil if it is gone.
    private func info(for id: UUID) -> AccountInfo? {
        guard let index = controller.accounts.firstIndex(where: { $0.id == id }) else { return nil }
        return makeInfo(controller.accounts[index], position: index + 1)
    }

    private func makeInfo(_ account: Account, position: Int) -> AccountInfo {
        ControlSnapshots.accountInfo(
            account,
            position: position,
            isSwitchable: controller.isSwitchable(account.id),
            effectiveThreshold: AutoSwitchSettings.leaveAtThreshold(rule: controller.effectiveSwitchThreshold(for: account),
                                                                    defaultThreshold: controller.autoSwitch.defaultThreshold),
            usage: ControlSnapshots.usageInfo(controller.usage(for: account.id),
                                              sampledAt: controller.usageSampledAt(for: account.id),
                                              error: controller.usageError(for: account.id))
        )
    }

    private func usageReport(only account: Account?) -> UsageReport {
        let all = accountInfos()
        return UsageReport(
            accounts: account.map { a in all.filter { $0.id == a.id.uuidString } } ?? all,
            machine: controller.machineUsage,
            lastRefresh: controller.lastUsageRefresh
        )
    }
}

/// Turns app models into protocol results. Pure, so the no-token rule and the
/// shapes are unit-tested.
enum ControlSnapshots {
    static func accountInfo(_ account: Account, position: Int, isSwitchable: Bool, effectiveThreshold: Double, usage: UsageInfo?) -> AccountInfo {
        AccountInfo(
            id: account.id.uuidString,
            email: account.email,
            label: account.customLabel.flatMap { $0.isEmpty ? nil : $0 },
            subscription: account.displaySubscriptionType,
            isActive: account.isActive,
            isSwitchable: isSwitchable,
            position: position,
            threshold: account.switchThreshold,
            effectiveThreshold: effectiveThreshold,
            manualOnly: AutoSwitchSettings.isManualOnly(own: account.switchThreshold),
            usage: usage
        )
    }

    static func usageInfo(_ usage: UsageAPIResponse?, sampledAt: Date?, error: String?) -> UsageInfo? {
        guard usage != nil || error != nil else { return nil }
        func window(_ w: UsageWindow?) -> WindowInfo? {
            guard let w, w.utilization != nil || w.resetsAt != nil else { return nil }
            return WindowInfo(utilization: w.utilization, resetsAt: w.resetsAt)
        }
        let extra = usage?.extraUsage.map {
            ExtraUsageInfo(enabled: $0.isEnabled, monthlyLimit: $0.monthlyLimit, usedCredits: $0.usedCredits, utilization: $0.utilization)
        }
        return UsageInfo(
            session: window(usage?.fiveHour),
            weekly: window(usage?.sevenDay),
            fable: window(usage?.modelWeeklyLimit(named: AutoSwitchEngine.fableModelName)?.window),
            extraUsage: extra,
            sampledAt: sampledAt,
            error: error
        )
    }

    @MainActor
    static func signInInfo(_ session: SignInSession) -> SignInInfo {
        var purpose = "newAccount"
        var accountId: String?
        var email: String?
        if case .reauthenticate(let id, let address) = session.purpose {
            purpose = "reauthenticate"
            accountId = id.uuidString
            email = address
        }
        var state: String
        var message: String?
        var result: String?
        switch session.state {
        case .starting: state = "starting"
        case .waitingForUser: state = "waitingForUser"
        case .completing: state = "completing"
        case .succeeded(let id): state = "succeeded"; result = id.uuidString
        case .failed(let why): state = "failed"; message = why
        case .cancelled: state = "cancelled"
        }
        return SignInInfo(
            id: session.id.uuidString, purpose: purpose, accountId: accountId, email: email,
            state: state, message: message, resultAccountId: result,
            automaticLink: session.automaticLink?.absoluteString, manualLink: session.manualLink?.absoluteString,
            notice: session.notice, codeSubmitted: session.codeSubmitted, startedAt: session.startedAt
        )
    }
}
