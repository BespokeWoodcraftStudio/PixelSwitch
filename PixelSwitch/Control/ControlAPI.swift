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
    /// The two global defaults as auto-switch reads them now.
    var windowDefaults: AutoSwitchEngine.WindowPair { get }
    /// Where auto-switch moves off `account` while it is active, per window.
    func leaveAt(for account: Account) -> AutoSwitchEngine.WindowPair

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
    /// The legacy "both windows" write: clears the account's window values too.
    func setSwitchThreshold(_ threshold: Double?, for id: UUID)
    /// One or both windows (`.leave` = not in the request). False when the
    /// account is gone.
    func setWindowThresholds(session: AutoSwitchSettings.WindowEdit, weekly: AutoSwitchSettings.WindowEdit, for id: UUID) -> Bool
    /// 1–99, or nil to clear. Re-sorts the list. False when the account is gone.
    func setPriority(_ priority: Int?, for id: UUID) -> Bool
    /// Puts the accounts in this order and numbers them 1...N.
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

        case .accountsSetThresholds:
            let reference = try params(request, AccountParams.self).account
            let object = request.params?.objectValue ?? [:]
            guard object["session"] != nil || object["weekly"] != nil else {
                throw ControlError(.invalidParams, "The parameters for \(request.method) are missing or the wrong shape.")
            }
            let target = try resolve(reference)
            detail = " \(target.id.uuidString)"
            let session = try windowEdit(object["session"])
            let weekly = try windowEdit(object["weekly"])
            guard controller.setWindowThresholds(session: session, weekly: weekly, for: target.id) else {
                throw ControlError(.notFound, "That account no longer exists.")
            }
            return .object(["account": try .encode(info(for: target.id))])

        case .accountsSetPriority:
            let reference = try params(request, AccountParams.self).account
            guard let raw = request.params?.objectValue?["priority"] else {
                throw ControlError(.invalidParams, "The parameters for \(request.method) are missing or the wrong shape.")
            }
            let target = try resolve(reference)
            detail = " \(target.id.uuidString)"
            let priority: Int?
            switch raw {
            case .null:
                priority = nil
            case .number(let value):
                guard value.isFinite, value == value.rounded(), AccountOrder.priorityRange.contains(Int(exactly: value) ?? 0) else {
                    throw Self.badPriority
                }
                priority = Int(value)
            default:
                throw Self.badPriority
            }
            guard controller.setPriority(priority, for: target.id) else {
                throw ControlError(.notFound, "That account no longer exists.")
            }
            return .object(["accounts": try .encode(accountInfos())])

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

    private static let badPriority = ControlError(.invalidValue, "A priority must be a whole number from 1 to 99, or null for none.")

    /// One window of an `accounts.setThresholds` request: absent leaves it, null
    /// follows the default, a number (rounded half away from zero) is its own
    /// value and must come to 1–100.
    private func windowEdit(_ value: JSONValue?) throws -> AutoSwitchSettings.WindowEdit {
        guard let value else { return .leave }
        let refusal = ControlError(.invalidValue, "A threshold must be 1–100, or null to follow the default. For manual only use accounts.setThreshold with 0.")
        switch value {
        case .null: return .clear
        case .number(let number):
            guard number.isFinite, (1.0...100.0).contains(number.rounded()) else { throw refusal }
            return .set(number.rounded())
        default: throw refusal
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
            signIn: controller.signIn,
            protocolRevision: ControlProtocol.revision,
            features: ControlProtocol.features,
            platform: ControlProtocol.platform
        )
    }

    /// The data of an `accountsChanged` event: the list as `accounts.list`
    /// would answer it now.
    func accountsChangedData() -> JSONValue {
        (try? .object(["accounts": .encode(accountInfos())])) ?? .object([:])
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
            leaveAt: controller.leaveAt(for: account),
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
    /// `leaveAt`: where auto-switch moves off the account while it is active,
    /// per window. `effectiveThreshold` (what a 1.9 reader sees) is the lower.
    static func accountInfo(_ account: Account, position: Int, isSwitchable: Bool, leaveAt: AutoSwitchEngine.WindowPair, usage: UsageInfo?) -> AccountInfo {
        AccountInfo(
            id: account.id.uuidString,
            email: account.email,
            label: account.customLabel.flatMap { $0.isEmpty ? nil : $0 },
            subscription: account.displaySubscriptionType,
            isActive: account.isActive,
            isSwitchable: isSwitchable,
            position: position,
            threshold: account.switchThreshold,
            effectiveThreshold: min(leaveAt.session, leaveAt.weekly),
            manualOnly: AutoSwitchSettings.isManualOnly(own: account.switchThreshold),
            usage: usage,
            priority: AccountOrder.validPriority(account.priority),
            sessionThreshold: AutoSwitchSettings.normalizedWindowThreshold(account.sessionThreshold),
            weeklyThreshold: AutoSwitchSettings.normalizedWindowThreshold(account.weeklyThreshold),
            effectiveSessionThreshold: leaveAt.session,
            effectiveWeeklyThreshold: leaveAt.weekly
        )
    }

    /// What `AccountInfo` carries apart from usage, as stored on the list. A
    /// change in this (and only this) is an `accountsChanged` event; a usage
    /// reading is not part of it. The list of these also changes when the
    /// order does.
    struct AccountFields: Equatable {
        let id: UUID, email: String, label: String?, subscription: String?, isActive: Bool
        let threshold: Double?, session: Double?, weekly: Double?, priority: Int?
        init(_ account: Account) {
            id = account.id; email = account.email; label = account.customLabel; subscription = account.subscriptionType
            isActive = account.isActive; threshold = account.switchThreshold; session = account.sessionThreshold
            weekly = account.weeklyThreshold; priority = account.priority
        }
    }

    /// The global defaults every account's effective thresholds depend on; a
    /// change here is also an `accountsChanged` event.
    struct DefaultFields: Equatable {
        let base: Double, session: Double, weekly: Double
    }

    /// The data of an `autoSwitched` event. `window` is left out unless a
    /// window's limit fired; `relaxed` is always sent.
    static func autoSwitchedData(from: UUID, to: UUID, limit: AutoSwitchEngine.Limit, trigger: AutoSwitchEngine.Trigger,
                                 window: AutoSwitchEngine.Window?, relaxed: Bool) -> [String: JSONValue] {
        var data: [String: JSONValue] = ["from": .string(from.uuidString), "to": .string(to.uuidString),
                                         "limit": .string(limit.rawValue), "trigger": .string(trigger.rawValue),
                                         "relaxed": .bool(relaxed)]
        if let window { data["window"] = .string(window.rawValue) }
        return data
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
