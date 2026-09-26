import SwiftUI
import Combine
import WidgetKit

private let log = FileLog("AppState")
private let signInLog = FileLog("SignIn")

/// Central app state managing accounts, usage data, and active sessions.
@MainActor
final class AppState: ObservableObject {
    // MARK: - Published State

    @Published var accounts: [Account] = []
    @Published var activeAccount: Account?
    @Published var accountUsage: [UUID: UsageAPIResponse] = [:]
    /// When each account's usage sample was actually taken. Accounts are polled
    /// round-robin (active + one other per cycle), so a card can be showing a
    /// reading several cycles old; without this the UI would render a stale
    /// percentage exactly like a live one, and auto-switch could not tell
    /// which samples this cycle actually verified.
    @Published var accountUsageSampledAt: [UUID: Date] = [:]
    @Published var usageSummary: UsageSummary = .empty
    @Published var recentActivity: [DailyActivity] = []
    @Published var activeSessions: [SessionInfo] = []
    @Published var isLoading = false
    @Published var isLoggingIn = false
    /// The sign-in in progress, or the last one until it is dismissed or
    /// replaced, so its outcome stays readable (Part C's status call reads it).
    @Published private(set) var currentSignIn: SignInSession?
    @Published var errorMessage: String?
    @Published var claudeAvailable = false
    @Published var lastUsageRefresh: Date?
    @Published var costSummary: CostSummary = .empty
    @Published var activityStats: ActivityStats = .empty

    // Store errors as special struct to surface in UI
    struct UsageErrorState {
        let isExpired: Bool
        let isRateLimited: Bool
        let message: String
    }
    
    @Published var accountUsageErrors: [UUID: UsageErrorState] = [:]
    /// The account a switch is currently moving to, so a card can show that it
    /// is the one being switched to. Nil when nothing is in flight. `isSwitching`
    /// below is the guard; this is purely what the interface reads.
    @Published var switchingTo: UUID?

    // MARK: - Services

    private let claudeService = ClaudeService.shared
    private let statsParser = StatsParser.shared
    private let costParser = CostParser.shared
    private let activityParser = ActivityParser.shared
    private let keychain = KeychainService.shared

    /// Where the account list is stored. PixelSwitch's own key.
    private let accountsKey = "ai.pixelventures.pixelswitch.accounts"
    /// The key the forked-from app used, and the one `LegacyMigration` copies
    /// across. Read as a fallback and never written, so an older build and a
    /// fresh migration both still find their accounts.
    private let legacyAccountsKey = "com.ccswitcher.accounts"
    private var refreshTimer: Timer?

    // MARK: - Usage polling state

    /// Re-entrancy guard: overlapping refreshes (timer + manual button + post-switch)
    /// would each burst per-account usage requests and trip the endpoint's rate limit.
    private var isRefreshing = false

    /// Round-robin cursor over non-active accounts: each refresh cycle fetches usage
    /// for the active account plus ONE other, instead of all of them. The usage
    /// endpoint's rate limit is tight and shared with every running Claude Code
    /// session's own polling, so fewer requests per cycle beats a full sweep.
    private var usageFetchCursor = 0

    /// Per-account "leave it alone until" timestamps. The usage endpoint enforces a
    /// long-window per-account quota - observed Retry-After values run into tens of
    /// minutes - so once an account is rate-limited, polling it again before the
    /// server-given deadline just burns more quota. Stale samples are kept meanwhile.
    private var usageRetryNotBefore: [UUID: Date] = [:]

    /// When the current/most recent refresh cycle began. Auto-switch uses it to
    /// tell which usage samples were taken by THIS cycle (already fresh — no
    /// verification request needed) versus retained from earlier ones.
    private var lastCycleStart: Date = .distantPast

    /// One switch (manual or automatic) may mutate live credentials at a time.
    /// `switchTo` suspends across subprocess and keychain work while the UI stays
    /// responsive, so without this a second switch — a user click during an
    /// auto-switch verification, or vice versa — could interleave keychain and
    /// ~/.claude.json writes with the first.
    private var isSwitching = false

    // MARK: - Auto-switch

    /// Whether proactive auto-switch is on (written by SettingsView via @AppStorage).
    private var autoSwitchEnabled: Bool {
        UserDefaults.standard.bool(forKey: AutoSwitchSettings.enabledKey)
    }

    /// Settings → Auto-switch. On (the default) means the weekly Fable
    /// allowance can move the user too; off leaves session and weekly untouched
    /// and Fable purely informational.
    private var autoSwitchOnFable: Bool {
        AutoSwitchFableSetting.isOn
    }

    /// A threshold-triggered candidate must sit at least this far below its
    /// own threshold to be eligible, so two accounts hovering at the line
    /// never ping-pong. (An early drain uses `AutoSwitchEngine.drainMinimumRoom`.)
    private let autoSwitchHysteresis: Double = 10

    /// Minimum gap between two automatic switches, to avoid rapid flip-flopping.
    private let autoSwitchCooldown: TimeInterval = 300

    private var lastAutoSwitchAt: Date?
    private var isEvaluatingAutoSwitch = false

    /// The most recent automatic switch that completed in this launch, with
    /// the limit and the rule that fired. Nil until one happens. The control
    /// API (Part C) streams it as an event.
    @Published private(set) var lastAutoSwitch: AutoSwitchRecord?

    // MARK: - Initialization

    init() {
        log.info("[init] Loading accounts from UserDefaults...")
        loadAccounts()
        log.info("[init] Loaded \(self.accounts.count) accounts, active: \(self.activeAccount?.id.uuidString ?? "none")")
    }

    // MARK: - Refresh

    /// Refresh everything, then decide whether to auto-switch.
    ///
    /// The two halves are deliberately separate: `refreshData()` holds the
    /// `isRefreshing` re-entrancy guard for its whole body, so evaluating
    /// auto-switch *inside* it would mean the `switchTo() -> refresh()` that
    /// follows an automatic switch gets swallowed by that guard — leaving the
    /// spinner stuck and the new active account showing pre-switch numbers.
    /// And when `refreshData()` did NOT run (login in progress, another refresh
    /// already running), auto-switch must not be evaluated either: it would act
    /// on state mid-mutation — swapping credentials during a login, or deciding
    /// on samples a concurrent refresh is rewriting.
    func refresh() async {
        guard await refreshData() else { return }
        await evaluateAutoSwitch()
    }

    /// Returns true only when a full refresh actually ran.
    private func refreshData() async -> Bool {
        guard !isLoggingIn else {
            log.info("[refresh] Skipping: login in progress")
            return false
        }
        guard !isRefreshing else {
            log.info("[refresh] Skipping: refresh already in progress")
            return false
        }
        isRefreshing = true
        defer { isRefreshing = false }
        lastCycleStart = Date()
        isLoading = true
        errorMessage = nil

        claudeAvailable = await claudeService.isClaudeAvailable()
        log.info("[refresh] Claude available: \(self.claudeAvailable)")

        if claudeAvailable {
            do {
                let status = try await claudeService.getAuthStatus()
                updateActiveAccount(from: status)
            } catch {
                log.error("[refresh] getAuthStatus failed: \(error.localizedDescription)")
                errorMessage = error.localizedDescription
            }
        }

        // Passive token health check (no CLI calls, keychain reads only)
        diagnoseTokenHealth()

        // Ownership comes from the token (via Anthropic's API), not from
        // ~/.claude.json, which running sessions rewrite: first check saved
        // logins, then follow the live login's proven owner.
        await auditSavedLogins()
        await reconcileLiveLogin()

        // Fetch usage limits for all accounts
        await fetchAllAccountUsage()
        lastUsageRefresh = Date()

        usageSummary = statsParser.getUsageSummary()
        recentActivity = statsParser.getRecentActivity(days: 7)
        activeSessions = statsParser.getActiveSessions()

        // JSONL parsing: walk filesystem once via the shared cache, then
        // pull aggregated outputs. The actor's executor is off the main
        // thread, so awaiting these does not block the UI.
        await SessionParseCacheV2.shared.refreshFromFilesystem()
        let cost = await costParser.getCostSummary()
        let activity = await activityParser.getTodayStats()
        costSummary = cost
        activityStats = activity

        log.info("[refresh] Usage: weekly=\(self.usageSummary.weeklyMessages) msgs, \(self.activeSessions.count) active sessions, today=$\(String(format: "%.2f", cost.todayCost)) turns=\(activity.conversationTurns)")

        updateWidgetData()
        isLoading = false
        return true
    }

    func startAutoRefresh(interval: TimeInterval = 300) {
        stopAutoRefresh()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                await self.refresh()
            }
        }
    }

    func stopAutoRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }

    // MARK: - Account Management

    func addAccount() async {
        log.info("[addAccount] Starting add current account flow...")
        guard claudeAvailable else {
            errorMessage = String(localized: "Claude CLI not found", bundle: L10n.bundle)
            log.error("[addAccount] Aborted: Claude CLI not found")
            return
        }

        do {
            let status = try await claudeService.getAuthStatus()
            guard status.loggedIn else {
                errorMessage = String(localized: "Not logged in to Claude. Run 'claude auth login' first.", bundle: L10n.bundle)
                log.error("[addAccount] Aborted: not logged in")
                return
            }
            guard let email = status.email else {
                errorMessage = shadowedIdentityMessage(status)
                log.error("[addAccount] Aborted: CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
                return
            }
            log.info("[addAccount] Current auth: logged in, sub=\(status.subscriptionType ?? "nil")")

            if accounts.contains(where: { $0.email == email }) {
                errorMessage = String(localized: "Account already exists", bundle: L10n.bundle)
                log.warning("[addAccount] Aborted: duplicate account")
                return
            }

            var account = Account(
                email: email,
                displayName: status.orgName ?? email,
                provider: .claudeCode,
                orgName: status.orgName,
                subscriptionType: status.subscriptionType,
                isActive: accounts.isEmpty
            )
            log.info("[addAccount] Created account model, id=\(account.id)")

            log.info("[addAccount] Capturing token from keychain...")
            let captured = await claudeService.captureCurrentCredentials(for: account)
            if !captured {
                errorMessage = String(localized: "Could not capture auth token from keychain", bundle: L10n.bundle)
                log.error("[addAccount] Token capture failed!")
                return
            }
            log.info("[addAccount] Token captured successfully")

            if accounts.isEmpty {
                account.isActive = true
                activeAccount = account
                log.info("[addAccount] First account, setting as active")
            }

            accounts.append(account)
            saveAccounts()
            log.info("[addAccount] Account saved. Total accounts: \(self.accounts.count)")
        } catch {
            errorMessage = error.localizedDescription
            log.error("[addAccount] Error: \(error.localizedDescription)")
        }
    }

    // MARK: - Sign-in

    enum SignInStartError: Error, Equatable { case busy, claudeUnavailable }

    /// Starts a sign-in and returns its session at once. The current account
    /// is backed up first (before `claude auth login` can overwrite it), then
    /// the CLI runs; `isLoggingIn` stays true until the session finishes, on
    /// every path. When Claude Code exits 0, the same capture steps as always
    /// run (see `finishNewAccountSignIn` and `finishReauthentication`).
    /// Throws `SignInStartError`.
    func startSignIn(_ purpose: SignInPurpose) throws -> SignInSession {
        switch SignInGate.decide(claudeAvailable: claudeAvailable, isSwitching: isSwitching,
                                 isLoggingIn: isLoggingIn, current: currentSignIn?.state) {
        case .claudeUnavailable: throw SignInStartError.claudeUnavailable
        case .busy: throw SignInStartError.busy
        case .allowed: break
        }
        // One credential mutation at a time (same guard as switchTo). A login
        // entering while a switch is suspended mid-swap would back up the
        // WRONG live credential under the old active account's id — quietly
        // destroying that account's usable backup. Also blocks double-clicks.
        isLoggingIn = true
        errorMessage = nil

        let session = SignInSession(
            purpose: purpose,
            launch: claudeService.signInLaunch(),
            runner: ProcessSignInRunner(),
            scheduler: MainQueueSignInScheduler(),
            log: { signInLog.info($0) },
            complete: { [weak self] session in
                guard let self else { return .failed(message: String(localized: "Login did not complete", bundle: L10n.bundle)) }
                return await self.completeSignIn(session)
            },
            onFinish: { [weak self] session in self?.signInFinished(session) }
        )
        currentSignIn = session
        Task {
            await self.backUpBeforeSignIn(for: purpose)
            session.start()
        }
        return session
    }

    /// The popover's "Login New Account" and Settings' "Sign In New Account".
    func loginNewAccount() {
        log.info("[loginNewAccount] ===== Starting login new account flow =====")
        do {
            _ = try startSignIn(.newAccount)
        } catch {
            reportSignInStartError(error, context: "loginNewAccount")
        }
    }

    /// Re-authenticate an account: a sign-in pre-filled with its email, whose
    /// fresh credentials are captured for it.
    func reauthenticateAccount(_ account: Account) {
        log.info("[reauth] ===== Re-authenticating account \(account.id) (\(account.email)) =====")
        do {
            _ = try startSignIn(.reauthenticate(accountId: account.id, email: account.email))
        } catch {
            reportSignInStartError(error, context: "reauth")
        }
    }

    /// Forgets a finished sign-in (its window's Close button). A running one is kept.
    func dismissSignIn() {
        if currentSignIn?.state.isFinished == true { currentSignIn = nil }
    }

    private func reportSignInStartError(_ error: Error, context: String) {
        switch error as? SignInStartError {
        case .claudeUnavailable?:
            errorMessage = String(localized: "Claude CLI not found", bundle: L10n.bundle)
            log.error("[\(context)] Aborted: Claude CLI not found")
        case .busy?:
            log.warning("[\(context)] Skipped: a switch or another login is in progress")
            if let running = currentSignIn, !running.state.isFinished {
                errorMessage = String(localized: "A sign-in is already in progress.", bundle: L10n.bundle)
                NotificationCenter.default.post(name: .pixelswitchShowSignIn, object: nil)
            } else {
                errorMessage = String(localized: "A switch is in progress. Try again in a moment.", bundle: L10n.bundle)
            }
        case nil:
            errorMessage = error.localizedDescription
        }
    }

    /// Step 1: back up the current account (token + oauthAccount) before the
    /// login overwrites them.
    private func backUpBeforeSignIn(for purpose: SignInPurpose) async {
        switch purpose {
        case .newAccount:
            if let current = activeAccount {
                log.info("[loginNewAccount] Step 1: Backing up current account (\(current.email))...")
                let backed = await claudeService.captureCurrentCredentials(for: current)
                log.info("[loginNewAccount] Step 1: Backup result: \(backed)")
            } else {
                log.info("[loginNewAccount] Step 1: No active account, skipping backup")
            }
            log.info("[loginNewAccount] Step 2: Running `claude auth login`...")
        case .reauthenticate(let accountId, _):
            if let current = activeAccount, current.id != accountId {
                log.info("[reauth] Backing up current account before login...")
                _ = await claudeService.captureCurrentCredentials(for: current)
            }
            log.info("[reauth] Running `claude auth login`...")
        }
    }

    /// Runs after Claude Code exited 0. Returns the sign-in's final state.
    private func completeSignIn(_ session: SignInSession) async -> SignInState {
        // Give keychain a moment to sync after CLI writes.
        try? await Task.sleep(for: .seconds(1))
        switch session.purpose {
        case .newAccount:
            return await finishNewAccountSignIn()
        case .reauthenticate(let accountId, let email):
            return await finishReauthentication(accountId: accountId, expectedEmail: email)
        }
    }

    /// Steps 3 to 6 of adding an account, unchanged from the run-to-exit login.
    private func finishNewAccountSignIn() async -> SignInState {
        log.info("[loginNewAccount] Step 2: Login process completed")

        // 3. Read the new identity from ~/.claude.json
        log.info("[loginNewAccount] Step 3: Reading post-login state...")
        let status: AuthStatus
        do {
            status = try await claudeService.getAuthStatus()
        } catch {
            errorMessage = error.localizedDescription
            isLoggingIn = false
            log.error("[loginNewAccount] Error: \(error.localizedDescription)")
            return .failed(message: error.localizedDescription)
        }

        switch SignInResult.newAccount(status: status, accounts: accounts) {
        case .notLoggedIn:
            let message = String(localized: "Login did not complete", bundle: L10n.bundle)
            errorMessage = message
            log.error("[loginNewAccount] Step 3: Not logged in after login!")
            isLoggingIn = false
            return .failed(message: message)

        case .noIdentity:
            let message = shadowedIdentityMessage(status)
            errorMessage = message
            log.error("[loginNewAccount] Step 3: CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
            isLoggingIn = false
            return .failed(message: message)

        case .existing(let existingId):
            log.info("[loginNewAccount] Step 3: Logged in as \(status.email ?? "?")")
            // 4. Duplicate — refresh its backup and make it the active account.
            // The login DID change what the CLI is authenticated as; returning
            // without updating our model left the menu bar and switcher
            // presenting an account the CLI was no longer using. The capture
            // CAN also fail (e.g. the backup store refuses writes while
            // unreadable); claiming "credentials refreshed" then would leave a
            // stale backup behind an explicit success message.
            log.info("[loginNewAccount] Step 4: Account already exists, refreshing backup and marking it active")
            guard let target = accounts.first(where: { $0.id == existingId }) else {
                isLoggingIn = false
                return .failed(message: String(localized: "Login did not complete", bundle: L10n.bundle))
            }
            let captured = await claudeService.captureCurrentCredentials(for: target)
            // Looked up again: the capture awaited, and the list may have changed.
            guard let existing = accounts.firstIndex(where: { $0.id == existingId }) else {
                let message = String(localized: "That account was removed during the sign-in, so nothing was saved.", bundle: L10n.bundle)
                errorMessage = message
                isLoggingIn = false
                return .failed(message: message)
            }
            for i in accounts.indices {
                accounts[i].isActive = (i == existing)
            }
            accounts[existing].lastUsed = Date()
            activeAccount = accounts[existing]
            // A login is a deliberate account choice; grant it the same
            // auto-switch grace period a manual switch gets.
            lastAutoSwitchAt = Date()
            saveAccounts()
            isLoggingIn = false
            if captured {
                errorMessage = String(localized: "Account already exists - credentials refreshed", bundle: L10n.bundle)
                return .succeeded(accountId: existingId)
            }
            log.error("[loginNewAccount] Step 4: Backup capture FAILED for existing account")
            let message = String(localized: "Could not capture credentials", bundle: L10n.bundle)
            errorMessage = message
            return .failed(message: message)

        case .new(let email):
            log.info("[loginNewAccount] Step 3: Logged in as \(email)")
            // 5. Create new account and capture credentials (token + oauthAccount)
            let account = Account(
                email: email,
                displayName: status.orgName ?? email,
                provider: .claudeCode,
                orgName: status.orgName,
                subscriptionType: status.subscriptionType,
                isActive: true
            )
            log.info("[loginNewAccount] Step 5: Created account, id=\(account.id)")

            let captured = await claudeService.captureCurrentCredentials(for: account)
            if !captured {
                let message = String(localized: "Could not capture credentials", bundle: L10n.bundle)
                errorMessage = message
                log.error("[loginNewAccount] Step 5: Capture failed!")
                isLoggingIn = false
                return .failed(message: message)
            }

            // 6. Mark new account as active
            for i in accounts.indices {
                accounts[i].isActive = false
            }
            accounts.append(account)
            activeAccount = account
            // A login is a deliberate account choice; grant it the same
            // auto-switch grace period a manual switch gets.
            lastAutoSwitchAt = Date()
            saveAccounts()
            log.info("[loginNewAccount] Step 6: New account active. Total: \(self.accounts.count)")

            // refresh() skips while isLoggingIn is true; the session is still
            // `completing`, which keeps a second sign-in out meanwhile.
            isLoggingIn = false
            await refresh()
            log.info("[loginNewAccount] ===== Login completed =====")
            return .succeeded(accountId: account.id)
        }
    }

    /// Steps 3 to 5 of re-authenticating, unchanged from the run-to-exit login.
    private func finishReauthentication(accountId: UUID, expectedEmail: String) async -> SignInState {
        // 3. Verify the login result matches the target account
        let status: AuthStatus
        do {
            status = try await claudeService.getAuthStatus()
        } catch {
            errorMessage = error.localizedDescription
            isLoggingIn = false
            log.error("[reauth] Error: \(error.localizedDescription)")
            return .failed(message: error.localizedDescription)
        }

        switch SignInResult.reauthentication(status: status, expectedEmail: expectedEmail) {
        case .notLoggedIn:
            let message = String(localized: "Login did not complete", bundle: L10n.bundle)
            errorMessage = message
            isLoggingIn = false
            return .failed(message: message)

        case .noIdentity:
            let message = shadowedIdentityMessage(status)
            errorMessage = message
            log.error("[reauth] CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
            isLoggingIn = false
            return .failed(message: message)

        case .wrongAccount(let email):
            let message = String(localized: "Logged in as \(email), but expected \(expectedEmail). Credentials not updated.", bundle: L10n.bundle)
            errorMessage = message
            log.error("[reauth] Email mismatch: got \(email), expected \(expectedEmail)")
            isLoggingIn = false
            return .failed(message: message)

        case .matches:
            guard let account = accounts.first(where: { $0.id == accountId }) else {
                let message = String(localized: "That account was removed during the sign-in, so nothing was saved.", bundle: L10n.bundle)
                errorMessage = message
                log.error("[reauth] Account \(accountId) was removed during the sign-in; nothing captured")
                isLoggingIn = false
                return .failed(message: message)
            }

            // 4. Capture the fresh token
            let captured = await claudeService.captureCurrentCredentials(for: account)
            log.info("[reauth] Token capture result: \(captured)")

            // 5. Update account metadata. Done even when the capture failed —
            // the CLI really is on this account now — but a failed capture must
            // be surfaced, not folded into "completed": the stored backup is
            // still the OLD credential, so a later switch away and back would
            // fail while the UI claimed everything was refreshed.
            if let index = accounts.firstIndex(where: { $0.id == account.id }) {
                accounts[index].orgName = status.orgName
                accounts[index].subscriptionType = status.subscriptionType

                // Mark this account as active (it's what the CLI is now using)
                for i in accounts.indices {
                    accounts[i].isActive = (i == index)
                }
                activeAccount = accounts[index]
                // A re-authentication is a deliberate account choice; grant it
                // the same auto-switch grace period a manual switch gets.
                lastAutoSwitchAt = Date()
                saveAccounts()
            }

            isLoggingIn = false
            await refresh()
            if captured {
                log.info("[reauth] ===== Re-authentication completed =====")
                return .succeeded(accountId: account.id)
            }
            // Set AFTER refresh() — refresh clears errorMessage.
            let message = String(localized: "Could not capture credentials", bundle: L10n.bundle)
            errorMessage = message
            log.error("[reauth] ===== Re-authentication finished, but the backup capture FAILED =====")
            return .failed(message: message)
        }
    }

    /// Runs once per sign-in, on every path: success, failure, cancel, timeout.
    private func signInFinished(_ session: SignInSession) {
        isLoggingIn = false
        switch session.state {
        case .failed(let message):
            // The popover footer shows it too, as it always did.
            errorMessage = message
        case .cancelled:
            log.info("[signIn] Cancelled; nothing was changed")
        case .succeeded, .starting, .waitingForUser, .completing:
            break
        }
    }

    func updateAccountLabel(_ account: Account, label: String?) {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else { return }
        let trimmed = label?.trimmingCharacters(in: .whitespaces)
        accounts[index].customLabel = (trimmed?.isEmpty == true) ? nil : trimmed
        if accounts[index].isActive {
            activeAccount = accounts[index]
        }
        saveAccounts()
        updateWidgetData()
        log.info("[updateAccountLabel] Set label for \(account.email): \(trimmed ?? "nil")")
    }

    // MARK: - Per-account threshold and priority order

    /// The threshold auto-switch applies to `account`: its own when set, else
    /// the default from Settings → General. Read from `accounts` by id, so a
    /// stale copy (such as `activeAccount` held across an await) still gets
    /// the value saved most recently.
    func effectiveSwitchThreshold(for account: Account) -> Double {
        let stored = accounts.first(where: { $0.id == account.id }) ?? account
        return AutoSwitchSettings.effectiveThreshold(own: stored.switchThreshold,
                                                     defaultThreshold: AutoSwitchSettings.defaultThreshold)
    }

    /// Sets `account`'s own threshold, kept within 50–100; nil (or a value that
    /// is not a number) clears it back to the default. Saves.
    func setSwitchThreshold(_ threshold: Double?, for account: Account) {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else {
            log.warning("[setSwitchThreshold] No account \(account.id)")
            return
        }
        let value = AutoSwitchSettings.normalizedAccountThreshold(threshold)
        guard accounts[index].switchThreshold != value else { return }
        accounts[index].switchThreshold = value
        if accounts[index].isActive {
            activeAccount = accounts[index]
        }
        saveAccounts()
        log.info("[setSwitchThreshold] \(account.id): \(value.map { String(format: "%.0f%%", $0) } ?? "default")")
    }

    /// Puts the accounts list in the order `orderedIds` gives. That order is
    /// the priority "My order" follows and the order every list shows.
    /// Returns false, and changes nothing, unless `orderedIds` names every
    /// current account exactly once.
    @discardableResult
    func setAccountOrder(_ orderedIds: [UUID]) -> Bool {
        guard let reordered = AccountOrder.reordered(accounts, by: orderedIds) else {
            log.warning("[setAccountOrder] Refused: the order does not name each of the \(self.accounts.count) accounts exactly once")
            return false
        }
        applyOrder(reordered)
        return true
    }

    /// SwiftUI's `onMove` for the Settings → Accounts list. Saves.
    func moveAccounts(fromOffsets source: IndexSet, toOffset destination: Int) {
        applyOrder(AccountOrder.moving(accounts, fromOffsets: source, toOffset: destination))
    }

    private func applyOrder(_ reordered: [Account]) {
        guard reordered.map(\.id) != accounts.map(\.id) else { return }
        accounts = reordered
        // The widget lists accounts in this order too.
        saveAccounts(refreshWidget: true)
        log.info("[order] Accounts reordered: \(reordered.map { $0.id.uuidString.prefix(8) }.joined(separator: ", "))")
    }

    func removeAccount(_ account: Account) {
        log.info("[removeAccount] Removing account \(account.id)")
        keychain.removeAccountBackup(forAccountId: account.id.uuidString)
        accounts.removeAll { $0.id == account.id }
        // Drop every per-account cache too, or a re-added account inherits the
        // removed one's readings, error banner and rate-limit park.
        accountUsage[account.id] = nil
        accountUsageSampledAt[account.id] = nil
        accountUsageErrors[account.id] = nil
        usageRetryNotBefore[account.id] = nil
        if account.isActive, let first = accounts.first {
            accounts[accounts.startIndex].isActive = true
            activeAccount = accounts.first
            log.info("[removeAccount] Removed active account, switching to first remaining")
            Task { await switchTo(first) }
        }
        saveAccounts()
        log.info("[removeAccount] Done. Remaining accounts: \(self.accounts.count)")
    }

    func switchTo(_ account: Account) async {
        guard let currentActive = activeAccount else {
            log.info("[switchTo] No switch needed (no active account)")
            return
        }

        // One credential mutation at a time: a switch already in flight (its
        // awaits leave the main actor free) or a running login must finish
        // before another switch may touch the keychain and ~/.claude.json.
        guard !isSwitching, !isLoggingIn else {
            log.warning("[switchTo] Skipped: another switch or a login is in progress")
            return
        }
        isSwitching = true
        switchingTo = account.id
        defer {
            isSwitching = false
            switchingTo = nil
        }

        // Whose login is live, proven by Anthropic's API from the token (never
        // from ~/.claude.json, which running sessions rewrite). If it already
        // is the target's, there is nothing to switch; just make sure we know it.
        let liveCredential = keychain.readClaudeToken()
        var live: (account: Account, identity: ClaudeService.AccountIdentity)?
        if let liveCredential {
            live = await provenOwner(ofCredential: liveCredential)
        }
        let liveOwner = live?.account
        if liveOwner?.id == account.id || (liveOwner == nil && currentActive.id == account.id) {
            log.info("[switchTo] No switch needed: \(account.email)'s login is already live")
            if liveOwner?.id == account.id, currentActive.id != account.id { adoptActive(account) }
            return
        }

        log.info("[switchTo] ===== Switching from \((liveOwner ?? currentActive).email) to \(account.email) =====")

        // Resolve the target's backup ONCE, check that its login really is the
        // target's (renewing it first if it has expired), and hand it down.
        // "No backup", "store briefly unreadable" and "this login is someone
        // else's" are different problems with different fixes.
        let targetBackup: AccountBackup
        switch await validatedTargetBackup(for: account) {
        case .success(let backup):
            targetBackup = backup
        case .failure(let problem):
            log.error("[switchTo] ABORT: \(problem.message)")
            errorMessage = problem.message
            return
        }

        // Any switch, deliberate or automatic, restarts the auto-switch cooldown:
        // a user who knowingly picks an account sitting at 95% must not be
        // auto-switched away from it seconds later by the refresh that follows.
        lastAutoSwitchAt = Date()

        isLoading = true
        do {
            let outcome = try await claudeService.switchAccount(
                liveOwner: liveOwner, liveOwnerIdentity: live?.identity, verifiedLiveCredential: liveCredential,
                to: account, targetBackup: targetBackup)

            for i in accounts.indices {
                accounts[i].isActive = (accounts[i].id == account.id)
                if accounts[i].id == account.id {
                    accounts[i].lastUsed = Date()
                }
            }
            activeAccount = account
            saveAccounts()

            await refresh()
            // `refresh()` clears errorMessage, so surface the warning afterwards.
            if let shadowedBy = outcome.shadowedBy {
                errorMessage = String(localized: "Switched to \(account.email), but the Claude CLI is authenticating via \(shadowedBy) instead of the stored login, so it will not use this account.", bundle: L10n.bundle)
            }
            log.info("[switchTo] ===== Switch completed =====")
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
            log.error("[switchTo] Switch failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Login ownership
    //
    // Ownership is proven ONLY by Anthropic's API (whose access token is this).
    // Lineage against saved logins is not proof: one wrongly saved login makes
    // it point at the wrong account (it did on 2026-09-21), so lineage is only
    // ever used to REFUSE something, never to write anything.

    struct SwitchProblem: Error { let message: String }

    /// Set after the first full audit of saved logins in this app launch.
    private var didAuditSavedLogins = false

    /// Claude Code accounts' saved logins, keyed by account id; nil if the
    /// backup store is unreadable right now.
    private func savedLogins() -> [String: CredentialOwnership.Login]? {
        guard let backups = keychain.allAccountBackups() else { return nil }
        let claudeIds = Set(accounts.filter { $0.provider == .claudeCode }.map { $0.id.uuidString })
        var logins: [String: CredentialOwnership.Login] = [:]
        for (id, backup) in backups where claudeIds.contains(id) {
            if let login = CredentialOwnership.login(fromCredential: backup.token) { logins[id] = login }
        }
        return logins
    }

    /// The one Claude Code account an API identity names: by the permanent
    /// account id (`accountUuid` in that account's saved identity) and by
    /// email. nil if neither names exactly one account, or if they disagree.
    private func account(matching identity: ClaudeService.AccountIdentity) -> Account? {
        let claude = accounts.filter { $0.provider == .claudeCode }
        let backups = keychain.allAccountBackups() ?? [:]
        var byUuid: Account?
        if let uuid = identity.uuid {
            let hits = claude.filter { (backups[$0.id.uuidString]?.oauthAccount["accountUuid"]?.value as? String) == uuid }
            if hits.count == 1 { byUuid = hits[0] }
        }
        var byEmail: Account?
        if let email = identity.email {
            let hits = claude.filter { $0.email.caseInsensitiveCompare(email) == .orderedSame }
            if hits.count == 1 { byEmail = hits[0] }
        }
        if let byUuid, let byEmail, byUuid.id != byEmail.id { return nil }
        return byUuid ?? byEmail
    }

    /// The account the credential's login belongs to, per Anthropic's API, with
    /// the API's identity for it; nil if the API cannot say (expired token,
    /// network) or names no known account.
    private func provenOwner(ofCredential credential: String) async -> (account: Account, identity: ClaudeService.AccountIdentity)? {
        guard let login = CredentialOwnership.login(fromCredential: credential),
              let identity = await claudeService.accountIdentity(forAccessToken: login.accessToken),
              let owner = account(matching: identity) else { return nil }
        return (owner, identity)
    }

    /// Whether saved identity details describe the owner the API named.
    private func details(_ details: [String: AnyCodable]?, belongTo identity: ClaudeService.AccountIdentity) -> Bool {
        CredentialOwnership.detailsBelong(uuid: details?["accountUuid"]?.value as? String,
                                          email: details?["emailAddress"]?.value as? String,
                                          toOwnerUuid: identity.uuid, ownerEmail: identity.email)
    }

    /// The target's backup, if its login provably is the target's. An expired
    /// login is renewed first (with the usage refresh's own guards) so the API
    /// can be asked; if the API still cannot say, only a login that is also
    /// saved under another account is refused.
    private func validatedTargetBackup(for account: Account) async -> Result<AccountBackup, SwitchProblem> {
        var backup: AccountBackup
        switch keychain.lookupAccountBackup(forAccountId: account.id.uuidString) {
        case .found(let found):
            backup = found
        case .missing:
            return .failure(SwitchProblem(message: String(localized: "No stored credentials for \(account.email). Use re-authenticate to fix.", bundle: L10n.bundle)))
        case .storeUnavailable:
            return .failure(SwitchProblem(message: String(localized: "Credential storage is temporarily unavailable. Try again shortly.", bundle: L10n.bundle)))
        }
        guard var login = CredentialOwnership.login(fromCredential: backup.token) else { return .success(backup) }

        var identity = await claudeService.accountIdentity(forAccessToken: login.accessToken)
        if identity == nil, login.isExpired {
            switch await refreshBackupInPlace(for: account) {
            case .refreshed(let renewed):
                if let current = keychain.getAccountBackup(forAccountId: account.id.uuidString) { backup = current }
                if let renewedLogin = CredentialOwnership.login(fromCredential: renewed) {
                    login = renewedLogin
                    identity = await claudeService.accountIdentity(forAccessToken: renewedLogin.accessToken)
                }
            case .storeUnavailable:
                // A blip (network, rate limit, locked Keychain): nothing was
                // spent, so go ahead as before; Claude Code renews it on first use.
                log.warning("[switchTo] Could not renew \(account.email)'s expired login right now; switching anyway")
            case .belongsToAnotherAccount(let owner):
                return .failure(SwitchProblem(message: String(localized: "The login saved for \(account.email) belongs to \(owner). Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
            case .grantRejected, .noBackup, .rotationLost:
                return .failure(SwitchProblem(message: String(localized: "The saved login for \(account.email) has expired and could not be renewed. Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
            }
        }

        if let identity {
            guard let owner = self.account(matching: identity), owner.id == account.id else {
                let ownerName = self.account(matching: identity)?.email ?? identity.email ?? "another account"
                return .failure(SwitchProblem(message: String(localized: "The login saved for \(account.email) belongs to \(ownerName). Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
            }
            // The token is the target's; its saved identity details must be too,
            // or the switch would pair this login with another account's details.
            guard details(backup.oauthAccount, belongTo: identity) else {
                return .failure(SwitchProblem(message: String(localized: "The saved details for \(account.email) belong to another account. Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
            }
            return .success(backup)
        }
        let others = (savedLogins() ?? [:]).filter { $0.key != account.id.uuidString }
        if !CredentialOwnership.lineageMatches(login, in: others).isEmpty {
            return .failure(SwitchProblem(message: String(localized: "The login saved for \(account.email) is also saved under another account. Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
        }
        return .success(backup)
    }

    /// Marks `account` as the active one in PixelSwitch's own records.
    private func adoptActive(_ account: Account) {
        for i in accounts.indices { accounts[i].isActive = (accounts[i].id == account.id) }
        activeAccount = accounts.first { $0.id == account.id } ?? account
        saveAccounts()
    }

    /// Keeps PixelSwitch tied to the live login rather than to ~/.claude.json:
    /// follows the login's proven owner, restores that owner's identity in
    /// ~/.claude.json if a session left another account's there, and keeps the
    /// owner's backup current, so switching back restores the newest login
    /// instead of one whose refresh token the CLI has since rotated.
    private func reconcileLiveLogin() async {
        guard !isSwitching, !isLoggingIn, let credential = keychain.readClaudeToken() else { return }
        guard let (owner, ownerIdentity) = await provenOwner(ofCredential: credential) else { return }
        // The API call left the main actor free: act only if no switch or login
        // started meanwhile and the live login is still the one that was checked.
        // Everything below is synchronous, so nothing can slip in between.
        guard !isSwitching, !isLoggingIn, keychain.readClaudeToken() == credential else {
            log.info("[reconcile] The login changed while its owner was being checked; skipping this cycle")
            return
        }

        if activeAccount?.id != owner.id {
            log.warning("[reconcile] The live login belongs to \(owner.email), not \(activeAccount?.email ?? "none"); following the login")
            adoptActive(owner)
        }

        let fileIdentity = keychain.readOAuthAccount()
        let fileEmail = fileIdentity?["emailAddress"]?.value as? String
        let fileBelongs = details(fileIdentity, belongTo: ownerIdentity)
        let savedBackup = keychain.getAccountBackup(forAccountId: owner.id.uuidString)
        let backupBelongs = details(savedBackup?.oauthAccount, belongTo: ownerIdentity)
        if !fileBelongs, backupBelongs, let identity = savedBackup?.oauthAccount {
            log.warning("[reconcile] ~/.claude.json named \(fileEmail ?? "nobody") while \(owner.email)'s login is live; restoring its identity")
            _ = keychain.writeOAuthAccount(identity)
        }

        if let backup = savedBackup, backup.token != credential,
           let identity = fileBelongs ? fileIdentity : (backupBelongs ? backup.oauthAccount : nil) {
            if keychain.saveAccountBackup(token: credential, oauthAccount: identity, forAccountId: owner.id.uuidString) {
                log.info("[reconcile] \(owner.email)'s backup updated from the live login")
            }
        }
    }

    /// Checks saved logins against Anthropic's API and removes any that
    /// provably belongs to a DIFFERENT known account, flagging that account
    /// for re-authentication (on 2026-09-21 account A's login was saved under
    /// B, and switching to B then used A's token). Every saved login is checked
    /// once per launch; after that, only logins saved under two accounts.
    private func auditSavedLogins() async {
        guard !isSwitching, !isLoggingIn, let saved = savedLogins() else { return }
        let shared = Set(CredentialOwnership.sharedLogins(saved).flatMap { $0 })
        let toCheck = didAuditSavedLogins ? saved.filter { shared.contains($0.key) } : saved
        guard !toCheck.isEmpty else { return }
        didAuditSavedLogins = true

        for (id, login) in toCheck.sorted(by: { $0.key < $1.key }) {
            guard let account = accounts.first(where: { $0.id.uuidString == id }),
                  let identity = await claudeService.accountIdentity(forAccessToken: login.accessToken),
                  let owner = self.account(matching: identity), owner.id != account.id else { continue }
            // Act only if nothing moved during the call and this account still
            // holds that same login.
            guard !isSwitching, !isLoggingIn,
                  let current = keychain.getAccountBackup(forAccountId: id),
                  let currentLogin = CredentialOwnership.login(fromCredential: current.token),
                  CredentialOwnership.sameGrant(currentLogin, login) else { continue }
            if keychain.removeAccountBackup(forAccountId: id) {
                log.error("[audit] \(account.email)'s saved login belongs to \(owner.email); removed it")
                accountUsage[account.id] = nil
                accountUsageErrors[account.id] = UsageErrorState(
                    isExpired: true, isRateLimited: false,
                    message: String(localized: "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix.", bundle: L10n.bundle))
            }
        }
    }

    // MARK: - Auto-switch

    /// Whether an account can be switched to right now: it must have a stored
    /// backup token and not be flagged expired (an expired backup would fail the
    /// switch verification, or silently swap in a dead session).
    private func isSwitchable(_ account: Account) -> Bool {
        guard keychain.getAccountBackup(forAccountId: account.id.uuidString) != nil else { return false }
        if let error = accountUsageErrors[account.id], error.isExpired { return false }
        return true
    }

    /// Evaluate whether auto-switch should move the user, and if so where.
    /// Called after every completed refresh. Safe to call repeatedly.
    ///
    /// Two rules can fire (`AutoSwitchEngine.plan`): the active account
    /// reaching ITS OWN threshold on a watched limit (session/weekly, then
    /// Fable), or, with "Resets soonest" and early switching on, another
    /// account's weekly quota about to reset unused. Candidates are ordered by
    /// the user's strategy.
    ///
    /// Candidates are ranked from whatever samples we hold, then the chosen one
    /// is VERIFIED before committing, with the same rule that ranked it:
    /// round-robin polling can leave a candidate's sample several cycles old,
    /// and quota may have been consumed on it from another device meanwhile. A
    /// sample taken by this very cycle counts as verified; otherwise one fresh
    /// reading is taken — a single request per (rare, rule-gated,
    /// cooldown-gated) switch attempt, not the per-cycle burst the round-robin
    /// exists to prevent.
    private func evaluateAutoSwitch() async {
        guard autoSwitchEnabled, !isEvaluatingAutoSwitch else { return }
        guard !isLoggingIn, !isSwitching, let active = activeAccount else { return }

        // Cooldown: never auto-switch more than once per window.
        if let last = lastAutoSwitchAt, Date().timeIntervalSince(last) < autoSwitchCooldown {
            return
        }

        // Only consider same-provider accounts (a Claude switch never touches
        // Codex/Gemini), in the accounts list's order: "My order" ranks by it.
        let candidates = accounts.filter { $0.provider == active.provider && $0.id != active.id }
        let activeSampledThisCycle = (accountUsageSampledAt[active.id] ?? .distantPast) >= lastCycleStart
        let strategy = AutoSwitchSettings.strategy
        let drainEarly = AutoSwitchSettings.drainEarly
        let drainWithin = AutoSwitchSettings.drainWithinHours * 3600
        let watchFable = autoSwitchOnFable
        guard let plan = AutoSwitchEngine.plan(
            active: active,
            candidates: candidates,
            usageByAccount: accountUsage,
            isSwitchable: { [unowned self] in self.isSwitchable($0) },
            activeSampledThisCycle: activeSampledThisCycle,
            threshold: { [unowned self] in self.effectiveSwitchThreshold(for: $0) },
            hysteresisPct: autoSwitchHysteresis,
            watchFable: watchFable,
            strategy: strategy,
            drainEarly: drainEarly,
            drainWithin: drainWithin
        ) else { return }
        let limit = plan.limit
        let trigger = plan.trigger
        let ranked = plan.targets
        // The active account's weekly reset as the plan saw it. A drain target
        // must still reset before it when its fresh reading is checked.
        let activeWeeklyReset = AutoSwitchEngine.weeklyReset(accountUsage[active.id], limit: .windows)

        isEvaluatingAutoSwitch = true
        defer { isEvaluatingAutoSwitch = false }

        let activeUtil = AutoSwitchEngine.utilization(accountUsage[active.id], limit: limit) ?? -1
        let activeThreshold = effectiveSwitchThreshold(for: active)
        log.info("[autoSwitch] Rule \(trigger.rawValue), strategy \(strategy.rawValue): active \(active.id) at \(String(format: "%.0f", activeUtil))% on \(limit.rawValue) (its threshold \(String(format: "%.0f", activeThreshold))%); \(ranked.count) candidate(s)")

        // At most ONE fresh verification request per evaluation. Later ranked
        // candidates only qualify via samples this cycle already took.
        var freshRequestBudget = 1
        for target in ranked {
            let usage: UsageAPIResponse?
            if let sampledAt = accountUsageSampledAt[target.id], sampledAt >= lastCycleStart {
                // Sampled by this very cycle — that IS a fresh reading.
                usage = accountUsage[target.id]
            } else if freshRequestBudget > 0 {
                freshRequestBudget -= 1
                usage = await fetchUsageNow(for: target)
                if let usage {
                    accountUsage[target.id] = usage
                    accountUsageSampledAt[target.id] = Date()
                    accountUsageErrors[target.id] = nil
                }
            } else {
                continue
            }

            // The same rule, with the same per-account threshold, that ranked it.
            let targetThreshold = effectiveSwitchThreshold(for: target)
            guard let verifiedUtil = AutoSwitchEngine.eligibleUtilization(
                usage, limit: limit, trigger: trigger,
                threshold: targetThreshold, hysteresisPct: autoSwitchHysteresis,
                activeWeeklyReset: activeWeeklyReset, drainWithin: drainWithin, watchFable: watchFable
            ) else {
                let onLimit = AutoSwitchEngine.utilization(usage, limit: limit).map { String(format: "%.0f%%", $0) } ?? "no reading"
                let onWindows = AutoSwitchEngine.bindingUtilization(usage).map { String(format: "%.0f%%", $0) } ?? "no reading"
                log.info("[autoSwitch] Candidate \(target.id) failed verification for \(trigger.rawValue) (\(limit.rawValue) \(onLimit), windows \(onWindows), its threshold \(String(format: "%.0f", targetThreshold))%); trying next")
                continue
            }

            // Re-check volatile state: the verification await above can span a
            // login starting, a timer-tick refresh beginning, or a manual switch
            // the user just clicked (which updates `activeAccount` only after
            // its subprocess work finishes — hence the explicit isSwitching).
            guard !isLoggingIn, !isRefreshing, !isSwitching, activeAccount?.id == active.id else {
                log.info("[autoSwitch] State changed during verification; standing down")
                return
            }

            log.info("[autoSwitch] Switching to \(target.id) by rule \(trigger.rawValue) on \(limit.rawValue), verified at \(String(format: "%.0f", verifiedUtil))%")
            lastAutoSwitchAt = Date()
            // switchTo() calls refresh() -> evaluateAutoSwitch() again, but the
            // re-entrancy flag + the freshly-set cooldown make that a no-op.
            // The active account visibly changes in the menu bar as feedback.
            await switchTo(target)
            if activeAccount?.id == target.id {
                lastAutoSwitch = AutoSwitchRecord(from: active.id, to: target.id, limit: limit, trigger: trigger, at: Date())
            } else {
                log.warning("[autoSwitch] Switch to \(target.id) did not complete")
            }
            return
        }
        switch trigger {
        case .threshold: log.info("[autoSwitch] Threshold reached but no candidate verified; staying put")
        case .drainEarly: log.info("[autoSwitch] Early drain found no verified candidate; staying put")
        }
    }

    /// Take one fresh usage reading for an account right now, refreshing its
    /// stored credential in place first if the access token has expired.
    /// Returns nil when no reading could be taken. 429s are parked with the same
    /// scheme the polling loop uses, so verification can never leak around it.
    private func fetchUsageNow(for account: Account) async -> UsageAPIResponse? {
        if let notBefore = usageRetryNotBefore[account.id], notBefore > Date() {
            log.info("[fetchUsageNow] \(account.id) is rate-limit parked; no fresh reading available")
            return nil
        }

        let tokenJSON = account.isActive
            ? keychain.readClaudeToken()
            : keychain.getAccountBackup(forAccountId: account.id.uuidString)?.token
        guard let tokenJSON, let accessToken = ClaudeService.extractAccessToken(from: tokenJSON) else {
            return nil
        }

        do {
            return try await claudeService.getUsageLimits(accessToken: accessToken)
        } catch ClaudeService.UsageError.expired where !account.isActive {
            // Handle every refresh outcome, not just success: collapsing a dead
            // grant (or a lost rotation) to a plain nil here would leave the
            // account looking healthy — stale usage still shown, still eligible
            // for auto-switch — until a later polling pass happened to notice.
            switch await refreshBackupInPlace(for: account) {
            case .refreshed(let refreshed):
                guard let newToken = ClaudeService.extractAccessToken(from: refreshed) else { return nil }
                return await usageRespectingParking(accessToken: newToken, account: account)
            case .grantRejected, .noBackup, .rotationLost:
                accountUsage[account.id] = nil
                accountUsageSampledAt[account.id] = nil
                accountUsageErrors[account.id] = UsageErrorState(isExpired: true, isRateLimited: false, message: String(localized: "Session expired. Re-authenticate (↻) to fix.", bundle: L10n.bundle))
                return nil
            case .belongsToAnotherAccount:
                accountUsage[account.id] = nil
                accountUsageSampledAt[account.id] = nil
                accountUsageErrors[account.id] = UsageErrorState(isExpired: true, isRateLimited: false, message: String(localized: "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix.", bundle: L10n.bundle))
                return nil
            case .storeUnavailable:
                // Nothing spent, nothing lost; keep the stale sample and retry
                // on a later cycle.
                return nil
            }
        } catch ClaudeService.UsageError.rateLimited(let retryAfter) {
            park(account, retryAfter: retryAfter)
            return nil
        } catch {
            log.warning("[fetchUsageNow] \(account.id): \(error.localizedDescription)")
            return nil
        }
    }

    // MARK: - Usage

    /// Fetch usage with a single retry on 429 - but only when Retry-After is short.
    /// Observed Retry-After values run into tens of minutes (long-window per-account
    /// quota); retrying against those just burns more quota, so we rethrow instead
    /// and let the caller park the account until the deadline.
    private func fetchUsageWithRetry(accessToken: String) async throws -> UsageAPIResponse {
        do {
            return try await claudeService.getUsageLimits(accessToken: accessToken)
        } catch ClaudeService.UsageError.rateLimited(let retryAfter) {
            let delay = retryAfter ?? 15
            guard delay <= 30 else {
                throw ClaudeService.UsageError.rateLimited(retryAfter: retryAfter)
            }
            // Floor of 3s: "Retry-After: 0" is a momentary burst limiter, and an
            // immediate (~1s) retry was observed to fail again.
            log.warning("[fetchUsage] Rate-limited, retrying in \(String(format: "%.0f", max(delay, 3)))s...")
            try? await Task.sleep(nanoseconds: UInt64(max(delay, 3) * 1_000_000_000))
            return try await claudeService.getUsageLimits(accessToken: accessToken)
        }
    }

    /// Park an account until the server-given deadline (floor 60s — "Retry-After:
    /// 0" burst rejections must still park; cap 1h; default 2min when no
    /// Retry-After was sent). Every path that observes a 429 goes through here.
    private func park(_ account: Account, retryAfter: TimeInterval?) {
        let parkFor = min(max(retryAfter ?? 120, 60), 3600)
        usageRetryNotBefore[account.id] = Date().addingTimeInterval(parkFor)
        log.warning("[fetchUsage] \(account.id) rate-limited; parked for \(String(format: "%.0f", parkFor))s")
    }

    /// Fetch usage, honouring a 429 by parking the account. The post-refresh
    /// recovery paths previously wrapped this call in `try?`, which swallowed a
    /// 429 without parking it — leaking around the parking scheme and re-hitting
    /// a rate-limited account every cycle.
    private func usageRespectingParking(accessToken: String, account: Account) async -> UsageAPIResponse? {
        do {
            return try await claudeService.getUsageLimits(accessToken: accessToken)
        } catch ClaudeService.UsageError.rateLimited(let retryAfter) {
            park(account, retryAfter: retryAfter)
            return nil
        } catch {
            log.warning("[fetchUsage] Post-refresh retry failed for \(account.id): \(error.localizedDescription)")
            return nil
        }
    }

    private enum BackupRefreshOutcome {
        /// New credential JSON, persisted to the store.
        case refreshed(String)
        /// The endpoint decided the grant is invalid: the refresh token is dead
        /// (rotated elsewhere or revoked). Only re-authentication fixes this.
        case grantRejected
        /// No stored backup exists for this account at all.
        case noBackup
        /// The store cannot be read or written right now. Nothing was spent;
        /// heals by itself on a later cycle.
        case storeUnavailable
        /// The renewed login belongs to the named other account; the backup
        /// was removed. Only re-authentication fixes this.
        case belongsToAnotherAccount(String)
        /// Worst case: the rotation succeeded but the result could not be
        /// persisted even after a retry. The old refresh token is spent and the
        /// new credential is gone — only re-authentication brings this account
        /// back. Deliberately NOT held in memory: a credential that exists only
        /// in volatile state while some paths read the (dead) stored one is the
        /// split-brain that broke the previous attempt at this feature.
        case rotationLost
    }

    /// Refresh a non-active account's stored credential in place via the OAuth
    /// token endpoint — no keychain swap, so no race with running Claude Code
    /// sessions.
    /// Renewals already running, by account id. Two renewals of one saved
    /// login from the same refresh token would race: the loser gets
    /// invalid_grant, and a server that detects the reuse may revoke the grant.
    private var renewalsInFlight: [String: Task<BackupRefreshOutcome, Never>] = [:]

    /// Renews a saved login in place, joining a renewal already running for
    /// the same account instead of starting a second one.
    private func refreshBackupInPlace(for account: Account) async -> BackupRefreshOutcome {
        let accountId = account.id.uuidString
        if let running = renewalsInFlight[accountId] { return await running.value }
        let renewal = Task { await self.renewBackupInPlace(for: account) }
        renewalsInFlight[accountId] = renewal
        let outcome = await renewal.value
        renewalsInFlight[accountId] = nil
        return outcome
    }

    private func renewBackupInPlace(for account: Account) async -> BackupRefreshOutcome {
        let accountId = account.id.uuidString

        let backup: AccountBackup
        switch keychain.lookupAccountBackup(forAccountId: accountId) {
        case .found(let found):
            backup = found
        case .missing:
            log.warning("[refreshBackup] No stored backup for \(account.id)")
            return .noBackup
        case .storeUnavailable:
            log.warning("[refreshBackup] Store unreadable for \(account.id); trying again next cycle")
            return .storeUnavailable
        }

        // Never spend a refresh token that is not provably this account's own.
        // If the same grant is live or saved under another account, refreshing
        // it here rotates that account's refresh token and can sign its running
        // sessions out.
        if let login = CredentialOwnership.login(fromCredential: backup.token) {
            let live = keychain.readClaudeToken().flatMap { CredentialOwnership.login(fromCredential: $0) }
            let others = (savedLogins() ?? [:]).filter { $0.key != accountId }
            let isLive = live.map { CredentialOwnership.sameGrant($0, login) } ?? false
            if isLive || !CredentialOwnership.lineageMatches(login, in: others).isEmpty {
                log.error("[refreshBackup] \(account.id)'s saved login is the same grant as \(isLive ? "the live login" : "another account's"); not refreshing it")
                return .grantRejected
            }
        }

        // Prove the store is writable BEFORE spending the refresh token: the
        // endpoint can rotate it, and a rotation that cannot be persisted kills
        // the account (old token dead server-side, new one lost, manual re-login
        // the only way back). Re-saving what was just read is idempotent, and
        // the failures that matter here (locked keychain, denied prompt) are
        // conditions rather than blips, so this turns them into a harmless
        // "try again next cycle".
        guard keychain.saveAccountBackup(token: backup.token, oauthAccount: backup.oauthAccount, forAccountId: accountId) else {
            log.error("[refreshBackup] Store not writable; skipping refresh for \(account.id) so its refresh token stays valid")
            return .storeUnavailable
        }

        switch await claudeService.refreshOAuthCredentials(backup.token) {
        case .success(let refreshed):
            // A renewed token is fresh, so the API can say whose it is. A login
            // that belongs to another account must not stay under this one.
            if let renewed = CredentialOwnership.login(fromCredential: refreshed),
               let identity = await claudeService.accountIdentity(forAccessToken: renewed.accessToken),
               let owner = self.account(matching: identity), owner.id != account.id {
                log.error("[refreshBackup] \(account.id)'s saved login belongs to \(owner.email); removing it")
                keychain.removeAccountBackup(forAccountId: accountId)
                return .belongsToAnotherAccount(owner.email)
            }
            if keychain.saveAccountBackup(token: refreshed, oauthAccount: backup.oauthAccount, forAccountId: accountId) {
                return .refreshed(refreshed)
            }
            // The probe passed moments ago, so this is likely a blip — one retry.
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if keychain.saveAccountBackup(token: refreshed, oauthAccount: backup.oauthAccount, forAccountId: accountId) {
                return .refreshed(refreshed)
            }
            log.error("[refreshBackup] Rotation succeeded but the store write failed twice for \(account.id); the account needs re-authentication")
            return .rotationLost

        case .rejected:
            log.warning("[refreshBackup] Refresh grant rejected for \(account.id); the refresh token is dead")
            return .grantRejected

        case .transient:
            log.warning("[refreshBackup] Refresh attempt for \(account.id) failed transiently (network/server); will retry")
            return .storeUnavailable
        }
    }

    private func fetchAllAccountUsage() async {
        // Pick this cycle's targets: the active account (always) + one non-active
        // account in round-robin order. Stale samples for the others are kept.
        // Accounts parked by a server-given Retry-After deadline are skipped.
        let now = Date()
        let eligible = accounts.filter { (usageRetryNotBefore[$0.id] ?? .distantPast) <= now }
        let others = eligible.filter { !$0.isActive }
        var targets = eligible.filter { $0.isActive }
        if !others.isEmpty {
            targets.append(others[usageFetchCursor % others.count])
            usageFetchCursor += 1
        }

        // Only clear error state for the accounts we are about to sample;
        // the others keep both their stale usage and their error flags.
        for target in targets {
            accountUsageErrors[target.id] = nil
        }

        // For active account: use live keychain token (with delegated refresh on expiry)
        // For other accounts: use backup token (refreshed in place when expired)
        var isFirstRequest = true
        for account in targets {
            // Stagger requests: a back-to-back burst (one request per account within
            // ~100ms) reliably gets all but one of them 429'd by the usage endpoint.
            if !isFirstRequest {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
            }
            isFirstRequest = false

            let tokenJSON: String?
            if account.isActive {
                tokenJSON = keychain.readClaudeToken()
            } else {
                tokenJSON = keychain.getAccountBackup(forAccountId: account.id.uuidString)?.token
            }
            guard let tokenJSON, let accessToken = ClaudeService.extractAccessToken(from: tokenJSON) else {
                log.warning("[fetchUsage] No token for \(account.email), skipping")
                continue
            }
            do {
                let usage = try await fetchUsageWithRetry(accessToken: accessToken)
                accountUsage[account.id] = usage
                accountUsageSampledAt[account.id] = Date()
                accountUsageErrors[account.id] = nil
                log.info("[fetchUsage] \(account.email): session=\(usage.fiveHour?.utilization ?? -1)%, weekly=\(usage.sevenDay?.utilization ?? -1)%")
            } catch ClaudeService.UsageError.forbidden {
                // No active Pro/Max subscription on this account (e.g. the plan
                // lapsed) - usage is meaningless until it recovers. Observed as:
                // {"error":{"type":"permission_error","message":"OAuth
                // authentication is currently not allowed for this organization."}}
                log.warning("[fetchUsage] \(account.email) forbidden (no active subscription?)")
                accountUsage[account.id] = nil
                accountUsageSampledAt[account.id] = nil
                accountUsageErrors[account.id] = UsageErrorState(isExpired: false, isRateLimited: false, message: String(localized: "No active subscription on this account (OAuth not allowed).", bundle: L10n.bundle))
            } catch ClaudeService.UsageError.rateLimited(let retryAfter) {
                // Rate-limited: park the account until the server-given deadline
                // and keep the last known sample - a stale percentage carrying
                // its "Updated Xm ago" label beats an error banner. The sample
                // timestamp is deliberately NOT bumped, so the UI keeps telling
                // the truth about how old the number is.
                park(account, retryAfter: retryAfter)
                if accountUsage[account.id] == nil {
                    accountUsageErrors[account.id] = UsageErrorState(isExpired: false, isRateLimited: true, message: String(localized: "API Rate Limited. Try again later.", bundle: L10n.bundle))
                }
            } catch ClaudeService.UsageError.expired {
                log.warning("[fetchUsage] Token expired for \(account.email)")
                if account.isActive {
                    // Active account: delegated refresh via `claude auth status` is safe (no keychain swap)
                    do {
                        _ = try await claudeService.getAuthStatus()
                        log.info("[fetchUsage] Delegated refresh completed for active account.")
                        // Re-read refreshed token and retry
                        if let refreshedJSON = keychain.readClaudeToken(),
                           let refreshedToken = ClaudeService.extractAccessToken(from: refreshedJSON),
                           let usage = await usageRespectingParking(accessToken: refreshedToken, account: account) {
                            accountUsage[account.id] = usage
                            accountUsageSampledAt[account.id] = Date()
                            accountUsageErrors[account.id] = nil
                            log.info("[fetchUsage] Recovered \(account.email) via delegated refresh.")
                        }
                    } catch {
                        log.error("[fetchUsage] Delegated refresh failed for active account: \(error.localizedDescription)")
                        accountUsage[account.id] = nil
                        accountUsageSampledAt[account.id] = nil
                        accountUsageErrors[account.id] = UsageErrorState(isExpired: true, isRateLimited: false, message: String(localized: "Token expired. Switch to refresh.", bundle: L10n.bundle))
                    }
                } else {
                    // Non-active account: refresh the backup credential in place via
                    // the OAuth token endpoint - no keychain swap, so no race with
                    // running Claude Code sessions. Access tokens only live a few
                    // hours, so without this every non-active account would sit in
                    // a permanent "Token expired" state between switches.
                    switch await refreshBackupInPlace(for: account) {
                    case .refreshed(let refreshed):
                        log.info("[fetchUsage] Silently refreshed backup for \(account.email); retrying usage")
                        if let newToken = ClaudeService.extractAccessToken(from: refreshed),
                           let usage = await usageRespectingParking(accessToken: newToken, account: account) {
                            accountUsage[account.id] = usage
                            accountUsageSampledAt[account.id] = Date()
                            accountUsageErrors[account.id] = nil
                            log.info("[fetchUsage] \(account.email): session=\(usage.fiveHour?.utilization ?? -1)%, weekly=\(usage.sevenDay?.utilization ?? -1)%")
                        }
                    case .grantRejected, .noBackup, .rotationLost:
                        // Only re-authentication mints a new refresh token or a
                        // new backup — an honest dead end until the user acts.
                        // (.rotationLost is the loudest of the three; its log
                        // line already says exactly what was lost and why.)
                        accountUsage[account.id] = nil
                        accountUsageSampledAt[account.id] = nil
                        accountUsageErrors[account.id] = UsageErrorState(isExpired: true, isRateLimited: false, message: String(localized: "Session expired. Re-authenticate (↻) to fix.", bundle: L10n.bundle))
                    case .belongsToAnotherAccount:
                        accountUsage[account.id] = nil
                        accountUsageSampledAt[account.id] = nil
                        accountUsageErrors[account.id] = UsageErrorState(isExpired: true, isRateLimited: false, message: String(localized: "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix.", bundle: L10n.bundle))
                    case .storeUnavailable:
                        // Nothing was spent and nothing is lost; this heals by
                        // itself on a later cycle. Keep the stale sample.
                        accountUsageErrors[account.id] = UsageErrorState(isExpired: false, isRateLimited: false, message: String(localized: "Could not save the refreshed sign-in; will retry automatically.", bundle: L10n.bundle))
                    }
                }
            } catch {
                log.error("[fetchUsage] Failed to get usage for \(account.email): \(error.localizedDescription)")
                accountUsage[account.id] = nil
                accountUsageSampledAt[account.id] = nil
                accountUsageErrors[account.id] = UsageErrorState(isExpired: false, isRateLimited: false, message: String(localized: "Could not fetch usage: \(error.localizedDescription)", bundle: L10n.bundle))
            }
        }
    }

    // MARK: - Diagnostics

    /// Message for the case where the CLI *is* authenticated but reports no
    /// account, because a credential source outranking the stored claude.ai login
    /// is in play. Without this the user only saw "Not logged in" / "Login did not
    /// complete" and re-authorized in a loop that could never help (issue #18).
    private func shadowedIdentityMessage(_ status: AuthStatus) -> String {
        let method = status.shadowingAuthMethod ?? "unknown"
        return String(localized: "Claude CLI is authenticating via \(method) instead of a stored claude.ai login, so it reports no account. Unset ANTHROPIC_AUTH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_PROFILE and remove any apiKeyHelper, then try again.", bundle: L10n.bundle)
    }

    /// Passive health check — verifies backup existence and identity consistency.
    private func diagnoseTokenHealth() {
        guard !accounts.isEmpty else { return }

        log.info("[diagnose] === Health Check ===")
        log.info("[diagnose] Accounts: \(self.accounts.count), active: \(self.activeAccount?.email ?? "none")")

        // Check live oauthAccount identity
        if let liveOAuth = keychain.readOAuthAccount() {
            let liveEmail = (liveOAuth["emailAddress"]?.value as? String) ?? "?"
            log.info("[diagnose] Live oauthAccount: \(liveEmail)")
        } else {
            log.warning("[diagnose] Live oauthAccount: MISSING")
        }

        // Check each account has a backup
        for account in accounts {
            if let backup = keychain.getAccountBackup(forAccountId: account.id.uuidString) {
                let backupEmail = (backup.oauthAccount["emailAddress"]?.value as? String) ?? "?"
                log.info("[diagnose] Backup [\(account.email)]: OK (email=\(backupEmail))")
            } else {
                log.warning("[diagnose] Backup [\(account.email)]: MISSING — switch will fail")
            }
        }

        log.info("[diagnose] === End Health Check ===")
    }

    // MARK: - Widget

    private func updateWidgetData() {
        let widgetAccounts = accounts.map { account in
            let usage = accountUsage[account.id]
            let error = accountUsageErrors[account.id]
            return WidgetAccountData(
                email: account.displayEmail(obfuscated: EmailDisplay.isMasked),
                displayName: account.effectiveDisplayName(obfuscated: EmailDisplay.isMasked),
                subscriptionType: account.displaySubscriptionType,
                isActive: account.isActive,
                sessionUtilization: usage?.fiveHour?.utilization,
                sessionResetTime: usage?.fiveHour?.resetTimeString,
                weeklyUtilization: usage?.sevenDay?.utilization,
                weeklyResetTime: usage?.sevenDay?.resetTimeString,
                extraUsageEnabled: usage?.extraUsage?.isEnabled,
                hasError: error != nil,
                errorMessage: error?.message
            )
        }

        let data = WidgetData(
            accounts: widgetAccounts,
            todayCost: costSummary.todayCost,
            conversationTurns: activityStats.conversationTurns,
            activeCodingTime: activityStats.activeCodingTimeString,
            linesWritten: activityStats.linesWritten,
            modelUsage: activityStats.modelUsage,
            lastUpdated: Date()
        )
        data.save()
        WidgetCenter.shared.reloadAllTimelines()
        log.debug("[updateWidgetData] Widget data saved and timelines reloaded")
    }

    // MARK: - Persistence

    private func loadAccounts() {
        // Current key first, then the one inherited from the forked-from app.
        // The fallback is a READ only: the old key is left alone, so rolling
        // back to an older build finds the same accounts.
        let data = UserDefaults.standard.data(forKey: accountsKey)
            ?? UserDefaults.standard.data(forKey: legacyAccountsKey)
        guard let data,
              let decoded = try? JSONDecoder().decode([Account].self, from: data) else {
            log.info("[loadAccounts] No saved accounts found")
            return
        }
        let cameFromLegacyKey = UserDefaults.standard.data(forKey: accountsKey) == nil
        // Same hole as the keychain item had: if the current key is already
        // populated, the legacy one is redundant no matter how it got there,
        // and the migration branch below would never run to clear it. An older
        // build run after migrating re-creates it.
        if !cameFromLegacyKey, UserDefaults.standard.data(forKey: legacyAccountsKey) != nil {
            UserDefaults.standard.removeObject(forKey: legacyAccountsKey)
            UserDefaults.standard.removeObject(forKey: "migratedFromCCSwitcher")
            log.info("[loadAccounts] Current key is populated; removed the redundant legacy key")
        }
        accounts = decoded
        activeAccount = accounts.first(where: \.isActive)
        log.info("[loadAccounts] Loaded \(decoded.count) accounts\(cameFromLegacyKey ? " from the legacy key" : "")")
        // Write them under the current key straight away, so the fallback is
        // needed exactly once rather than on every launch, then clear the old
        // key. Read it back first: nothing is removed until the accounts are
        // provably readable from where they now live.
        if cameFromLegacyKey {
            saveAccounts()
            if UserDefaults.standard.data(forKey: accountsKey) != nil {
                UserDefaults.standard.removeObject(forKey: legacyAccountsKey)
                log.info("[loadAccounts] Accounts verified under the current key; legacy key removed")
            } else {
                log.error("[loadAccounts] Could not read accounts back under the current key; keeping the legacy key")
            }
        }
    }

    private func saveAccounts(refreshWidget: Bool = false) {
        if let data = try? JSONEncoder().encode(accounts) {
            UserDefaults.standard.set(data, forKey: accountsKey)
            log.debug("[saveAccounts] Saved \(self.accounts.count) accounts to UserDefaults")
        }
        if refreshWidget {
            updateWidgetData()
        }
    }

    private func updateActiveAccount(from status: AuthStatus) {
        guard status.loggedIn, let email = status.email else { return }

        if let index = accounts.firstIndex(where: { $0.email == email }) {
            for i in accounts.indices {
                accounts[i].isActive = (i == index)
            }
            accounts[index].orgName = status.orgName
            accounts[index].subscriptionType = status.subscriptionType
            activeAccount = accounts[index]
            saveAccounts()
            log.info("[updateActiveAccount] Matched existing account at index \(index)")
        } else if accounts.isEmpty {
            let account = Account(
                email: email,
                displayName: status.orgName ?? email,
                provider: .claudeCode,
                orgName: status.orgName,
                subscriptionType: status.subscriptionType,
                isActive: true
            )
            accounts.append(account)
            activeAccount = account
            Task { _ = await self.claudeService.captureCurrentCredentials(for: account) }
            saveAccounts()
            log.info("[updateActiveAccount] Auto-created first account, id=\(account.id)")
        } else {
            log.info("[updateActiveAccount] Logged-in account not in our list (might be new)")
        }
    }
}

/// One automatic switch that completed: from which account to which, on which
/// limit, by which rule, and when. Published as `AppState.lastAutoSwitch`.
struct AutoSwitchRecord: Equatable, Sendable {
    let from: UUID; let to: UUID
    let limit: AutoSwitchEngine.Limit; let trigger: AutoSwitchEngine.Trigger
    let at: Date
}
