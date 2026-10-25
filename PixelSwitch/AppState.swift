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
        /// The usage request was refused with 403: the account has no active
        /// subscription, so auto-switch leaves it and never moves you to it.
        var isNoSubscription = false
        /// The card offers Retry (`UsageRequestError.canRetry`).
        var canRetry = false
        /// The card links to status.claude.com (a 5xx from the usage service).
        var offersStatusPage = false
    }
    
    @Published var accountUsageErrors: [UUID: UsageErrorState] = [:]
    /// Accounts whose card's Retry is fetching right now.
    @Published var retryingUsage: Set<UUID> = []
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

    /// Decides, per access token, whether a usage or profile request may be sent
    /// (`UsageRequestGuard`). It also holds the 429 parks, which belong to the
    /// token they were earned on: a freshly re-authenticated account is never
    /// held back by the old token's park. Not persisted across a relaunch on
    /// purpose (founder-facing decision in docs/investigations/2026-10-16-usage-429.md):
    /// a stale park that survived a relaunch is worse than one extra request per
    /// account, because a relaunch was the only way to clear a stuck card.
    private var requestGuard: UsageRequestGuard { claudeService.requestGuard }

    /// UserDefaults key for the accounts whose refresh grant the server refused
    /// (account ids only, never a token).
    private let needsReauthKey = "ai.pixelventures.pixelswitch.needsReauthAccounts"

    /// Accounts whose credential just changed: read next cycle, ahead of the
    /// round-robin, so a fixed account does not wait its turn.
    private var readSoon: Set<UUID> = []

    /// When the current/most recent refresh cycle began. Auto-switch uses it to
    /// tell which usage samples were taken by THIS cycle (already fresh — no
    /// verification request needed) versus retained from earlier ones.
    private var lastCycleStart: Date = .distantPast

    /// One switch (manual or automatic) may mutate live credentials at a time.
    /// `switchTo` suspends across subprocess and keychain work while the UI stays
    /// responsive, so without this a second switch — a user click during an
    /// auto-switch verification, or vice versa — could interleave keychain and
    /// ~/.claude.json writes with the first.
    private(set) var isSwitching = false

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

    /// Minimum gap between two automatic switches, to avoid rapid flip-flopping.
    private let autoSwitchCooldown: TimeInterval = 300

    private var lastAutoSwitchAt: Date?
    private var isEvaluatingAutoSwitch = false
    /// The last "threshold reached, nowhere to go" line logged, so a stuck
    /// state is logged once when it starts rather than on every refresh.
    private var lastStayingPutNote: String?

    /// The most recent automatic switch that completed in this launch, with
    /// the limit and the rule that fired. Nil until one happens. The control
    /// API (Part C) streams it as an event.
    @Published private(set) var lastAutoSwitch: AutoSwitchRecord?

    // MARK: - Initialization

    init() {
        log.info("[init] Loading accounts from UserDefaults...")
        loadAccounts()
        restoreNeedsReauth()
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

    /// Why an action could not be done, for callers that need to know (the
    /// control API). The GUI's wrappers turn it into `errorMessage` as before.
    struct ActionError: Error, Equatable {
        enum Kind: Equatable { case busy, claudeUnavailable, noActiveAccount, failed }
        let kind: Kind
        let message: String
    }

    /// The popover's and Settings' "Add Current Account".
    func addAccount() async {
        do {
            _ = try await performAddCurrentAccount()
        } catch let error as ActionError {
            errorMessage = error.message
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Saves the account Claude Code is signed in as and returns it. Throws
    /// `ActionError` with the same messages the popover has always shown.
    @discardableResult
    func performAddCurrentAccount() async throws -> Account {
        log.info("[addAccount] Starting add current account flow...")
        guard claudeAvailable else {
            log.error("[addAccount] Aborted: Claude CLI not found")
            throw ActionError(kind: .claudeUnavailable, message: String(localized: "Claude CLI not found", bundle: L10n.bundle))
        }

        let status: AuthStatus
        do {
            status = try await claudeService.getAuthStatus()
        } catch {
            log.error("[addAccount] Error: \(error.localizedDescription)")
            throw ActionError(kind: .failed, message: error.localizedDescription)
        }
        guard status.loggedIn else {
            log.error("[addAccount] Aborted: not logged in")
            throw ActionError(kind: .failed, message: String(localized: "Not logged in to Claude. Run 'claude auth login' first.", bundle: L10n.bundle))
        }
        guard let email = status.email else {
            log.error("[addAccount] Aborted: CLI reports authMethod=\(status.authMethod ?? "nil") without an account identity")
            throw ActionError(kind: .failed, message: shadowedIdentityMessage(status))
        }
        log.info("[addAccount] Current auth: logged in, sub=\(status.subscriptionType ?? "nil")")

        if accounts.contains(where: { $0.email == email }) {
            log.warning("[addAccount] Aborted: duplicate account")
            throw ActionError(kind: .failed, message: String(localized: "Account already exists", bundle: L10n.bundle))
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
            log.error("[addAccount] Token capture failed!")
            throw ActionError(kind: .failed, message: String(localized: "Could not capture auth token from keychain", bundle: L10n.bundle))
        }
        log.info("[addAccount] Token captured successfully")

        if accounts.isEmpty {
            account.isActive = true
            activeAccount = account
            log.info("[addAccount] First account, setting as active")
        }

        accounts.append(account)
        sortAccountsByPriority()
        UserDefaults.standard.removeObject(forKey: removedLastAccountKey)
        forgetRemovedLogins()
        saveAccounts()
        log.info("[addAccount] Account saved. Total accounts: \(self.accounts.count)")
        return account
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
            if let current = activeAccount, accounts.contains(where: { $0.id == current.id }) {
                log.info("[loginNewAccount] Step 1: Backing up current account (\(current.email))...")
                let backed = await claudeService.captureCurrentCredentials(for: current)
                log.info("[loginNewAccount] Step 1: Backup result: \(backed)")
                if backed { noteCredentialChanged(for: current) }
            } else {
                log.info("[loginNewAccount] Step 1: No active account on the list, skipping backup")
            }
            log.info("[loginNewAccount] Step 2: Running `claude auth login`...")
        case .reauthenticate(let accountId, _):
            if let current = activeAccount, current.id != accountId, accounts.contains(where: { $0.id == current.id }) {
                log.info("[reauth] Backing up current account before login...")
                if await claudeService.captureCurrentCredentials(for: current) { noteCredentialChanged(for: current) }
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
            if captured { noteCredentialChanged(for: target) }
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
            sortAccountsByPriority()
            UserDefaults.standard.removeObject(forKey: removedLastAccountKey)
            forgetRemovedLogins()
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
            // A new login: forget the old one's park, refusal, cool-down and
            // needs-reauth flag, and read this account on the next cycle.
            if captured { noteCredentialChanged(for: account) }

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

    // MARK: - Per-account thresholds and priority order

    /// The two global defaults as auto-switch reads them now (spec 2.2).
    var windowDefaults: AutoSwitchEngine.WindowPair { AutoSwitchSettings.defaultPair }

    /// `account`'s resolved rule: session and weekly limits, or Manual only.
    /// Read from `accounts` by id, so a stale copy (such as `activeAccount` held
    /// across an await) still gets the values saved most recently.
    func windowRule(for account: Account) -> AutoSwitchEngine.WindowRule {
        let stored = accounts.first(where: { $0.id == account.id }) ?? account
        return AutoSwitchSettings.rule(for: stored, defaults: windowDefaults)
    }

    /// Where auto-switch moves the user off `account`, per window.
    func leaveAt(for account: Account) -> AutoSwitchEngine.WindowPair {
        AutoSwitchSettings.leaveAtPair(rule: windowRule(for: account), defaults: windowDefaults)
    }

    /// The single rule value the 1.9 callers (Views, Control) still read: 0 for
    /// Manual only, else the lower of the account's two window limits. With
    /// equal windows this is exactly what it always was. Callers move to
    /// `windowRule(for:)` and `leaveAt(for:)`.
    func effectiveSwitchThreshold(for account: Account) -> Double {
        let rule = windowRule(for: account)
        return rule.manualOnly ? AutoSwitchSettings.manualOnlyThreshold : min(rule.session, rule.weekly)
    }

    /// The three stored values a threshold write can change (D8's no-op check
    /// compares all three, so a legacy write clears window values even when
    /// the both-windows value is unchanged).
    private func thresholdTriple(_ account: Account) -> [Double?] {
        [account.switchThreshold, account.sessionThreshold, account.weeklyThreshold]
    }

    /// Stores `updated`'s three threshold values on the account at `index`, if
    /// any differ. Saves. Does not re-evaluate auto-switch (by decision,
    /// docs/decisions/2026-09-24-remote-control-answers.md:142).
    @discardableResult
    private func commitThresholds(_ updated: Account, at index: Int, described: String) -> Bool {
        guard thresholdTriple(accounts[index]) != thresholdTriple(updated) else { return false }
        accounts[index].switchThreshold = updated.switchThreshold
        accounts[index].sessionThreshold = updated.sessionThreshold
        accounts[index].weeklyThreshold = updated.weeklyThreshold
        if accounts[index].isActive {
            activeAccount = accounts[index]
        }
        saveAccounts()
        log.info("[thresholds] \(accounts[index].id): \(described)")
        return true
    }

    private func describe(_ account: Account) -> String {
        func percent(_ value: Double?) -> String { value.map { String(format: "%.0f%%", $0) } ?? "default" }
        if AutoSwitchSettings.isManualOnly(own: account.switchThreshold) { return "manual only" }
        if account.sessionThreshold == nil, account.weeklyThreshold == nil { return percent(account.switchThreshold) }
        return "session \(percent(account.sessionThreshold ?? account.switchThreshold)), weekly \(percent(account.weeklyThreshold ?? account.switchThreshold))"
    }

    /// Sets `account`'s own threshold for BOTH windows, a whole number within
    /// 0–100 (0 = Manual only); nil (or a value that is not a number) clears it
    /// back to the default. The legacy write (D8): it also clears the account's
    /// window values, even when the both-windows value is unchanged. Saves.
    func setSwitchThreshold(_ threshold: Double?, for account: Account) {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else {
            log.warning("[setSwitchThreshold] No account \(account.id)")
            return
        }
        let updated = AutoSwitchSettings.applyingBothWindows(threshold, to: accounts[index])
        commitThresholds(updated, at: index, described: describe(updated))
    }

    /// Sets one or both of `account`'s window thresholds (spec 2.1). `.leave`
    /// leaves a window alone, `.clear` makes it follow its default, `.set` gives
    /// it its own whole number 1–100. Applies the split of the both-windows
    /// value and the Manual only rules. False, and nothing changed, for an
    /// unknown account or a `.set` value outside 1–100 once rounded.
    @discardableResult
    func setWindowThresholds(session: AutoSwitchSettings.WindowEdit = .leave, weekly: AutoSwitchSettings.WindowEdit = .leave,
                             for account: Account) -> Bool {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else {
            log.warning("[setWindowThresholds] No account \(account.id)")
            return false
        }
        guard let updated = AutoSwitchSettings.applyingWindowEdits(to: accounts[index], session: session, weekly: weekly) else {
            log.warning("[setWindowThresholds] Refused for \(account.id): a threshold must be 1–100")
            return false
        }
        commitThresholds(updated, at: index, described: describe(updated))
        return true
    }

    /// Settings only (spec 2.4): "Default" chosen in one window's menu. On a
    /// Manual only account it clears Manual only and both windows follow their
    /// defaults (1.9's Default); on any other it makes that window follow its
    /// default. The control API's null leaves Manual only alone and does not call this.
    func chooseDefaultThreshold(_ window: AutoSwitchEngine.Window, for account: Account) {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else { return }
        if AutoSwitchSettings.isManualOnly(own: accounts[index].switchThreshold) {
            commitThresholds(AutoSwitchSettings.clearingManualOnly(accounts[index]), at: index, described: "default (manual only cleared)")
        } else {
            setWindowThresholds(session: window == .session ? .clear : .leave,
                                weekly: window == .weekly ? .clear : .leave, for: account)
        }
    }

    /// Writes one global window default (Settings → General, `autoSwitch.sessionThreshold`
    /// and `.weeklyThreshold`): a whole number within 50–100, or nil to follow
    /// the base default again.
    @discardableResult
    func setDefaultWindowThreshold(_ window: AutoSwitchEngine.Window, to value: Double?) -> Bool {
        let written = AutoSwitchSettings.setDefaultThreshold(window, to: value)
        if written {
            // What was stored: the value rounded to a whole number (it is refused, not clamped, outside 50–100).
            log.info("[thresholds] Default \(window.rawValue) threshold: \(value.map { String(format: "%.0f%%", $0.rounded()) } ?? "follows the base")")
        } else {
            log.warning("[thresholds] Refused a default \(window.rawValue) threshold of \(value.map { String(format: "%g", $0) } ?? "nil"): it must be a whole number from 50 to 100")
        }
        return written
    }

    /// Writes the base default (`autoSwitch.threshold`), 50–100, and removes both
    /// window defaults, so a legacy "both" write is never held back by an old
    /// window value (D8).
    @discardableResult
    func setBaseThreshold(_ value: Double) -> Bool {
        let written = AutoSwitchSettings.setBaseThreshold(value)
        if written {
            log.info("[thresholds] Base default threshold: \(String(format: "%.0f%%", value.rounded())); window defaults cleared")
        } else {
            log.warning("[thresholds] Refused a base default threshold of \(String(format: "%g", value)): it must be a whole number from 50 to 100")
        }
        return written
    }

    /// Sets `account`'s priority number (1–99; nil clears it) and re-sorts the
    /// list: among equal numbers the account set last goes first (spec D2).
    /// False, and nothing changed, for an unknown account or a number outside 1–99.
    @discardableResult
    func setPriority(_ priority: Int?, for account: Account) -> Bool {
        guard let updated = AccountOrder.settingPriority(priority, of: account.id, in: accounts) else {
            log.warning("[setPriority] Refused for \(account.id): a priority is a whole number from 1 to 99, or none")
            return false
        }
        if updated != accounts {
            installAccounts(updated)
            log.info("[setPriority] \(account.id): \(priority.map { "P\($0)" } ?? "none")")
        }
        return true
    }

    /// Puts the accounts list in the order `orderedIds` gives and numbers every
    /// account from 1 in that order, replacing typed numbers (spec D3). That
    /// order is the priority the Priority strategy follows and the order every
    /// list shows. Returns false, and changes nothing, unless `orderedIds`
    /// names every current account exactly once.
    @discardableResult
    func setAccountOrder(_ orderedIds: [UUID]) -> Bool {
        guard let reordered = AccountOrder.reordered(accounts, by: orderedIds) else {
            log.warning("[setAccountOrder] Refused: the order does not name each of the \(self.accounts.count) accounts exactly once")
            return false
        }
        applyOrder(reordered)
        return true
    }

    /// Moves accounts as SwiftUI's `onMove` says (Settings → Accounts) and
    /// renumbers every account from 1. Saves.
    func moveAccounts(fromOffsets source: IndexSet, toOffset destination: Int) {
        applyOrder(AccountOrder.moving(accounts, fromOffsets: source, toOffset: destination))
    }

    private func applyOrder(_ reordered: [Account]) {
        let numbered = AccountOrder.numberedInOrder(reordered)
        guard numbered != accounts else { return }
        installAccounts(numbered)
        log.info("[order] Accounts reordered and numbered from 1: \(numbered.map { $0.id.uuidString.prefix(8) }.joined(separator: ", "))")
    }

    /// Replaces the accounts list (already in the order it should have), keeps
    /// `activeAccount` pointing at the fresh copy, and saves. The widget lists
    /// accounts in this order too.
    private func installAccounts(_ updated: [Account]) {
        accounts = updated
        if let current = activeAccount, let fresh = accounts.first(where: { $0.id == current.id }) { activeAccount = fresh }
        saveAccounts(refreshWidget: true)
    }

    /// Keeps the list sorted by priority after anything appends to it (spec D3).
    private func sortAccountsByPriority() {
        let sorted = AccountOrder.sortedByPriority(accounts)
        guard sorted.map(\.id) != accounts.map(\.id) else { return }
        accounts = sorted
    }

    /// Set once the user removes their last account, so the next refresh does
    /// not quietly make it again from the login Claude Code still holds (spec
    /// D20). Cleared when any account is added. Kept across launches.
    private let removedLastAccountKey = "ai.pixelventures.pixelswitch.removedLastAccount"

    /// Fingerprints (never tokens) of the refresh tokens of the logins of accounts
    /// the user removed: the removed account's saved login and, when it was the
    /// active one, the login Claude Code held at that moment. Lets a switch made
    /// with no active account recognise the live login as the removed account's
    /// and so disposable (spec D20 revision 4). Kept across launches; forgotten
    /// by the next successful switch or add (`forgetRemovedLogins`).
    private let removedLoginsKey = "ai.pixelventures.pixelswitch.removedAccountLoginFingerprints"

    private var removedLoginFingerprints: [String] {
        get { UserDefaults.standard.stringArray(forKey: removedLoginsKey) ?? [] }
        set {
            if newValue.isEmpty { UserDefaults.standard.removeObject(forKey: removedLoginsKey) }
            else { UserDefaults.standard.set(newValue, forKey: removedLoginsKey) }
        }
    }

    private func forgetRemovedLogins() {
        if !removedLoginFingerprints.isEmpty { removedLoginFingerprints = [] }
    }

    /// Removes `account` and its saved login. Removing the account Claude Code
    /// is using also moves Claude Code to another one (spec D20, credentials
    /// recon section 8, option A): nothing is marked active until its switch
    /// has succeeded; if a switch fails the next account in `AccountOrder.fallbackOrder`
    /// is tried; if none works, no account is active and the user is told why.
    /// Removing the last account (active or not) leaves no active account and
    /// does not recreate it from the live login (`AccountRemoval.plan`).
    func removeAccount(_ account: Account) {
        log.info("[removeAccount] Removing account \(account.id)")
        let wasActive = account.isActive || activeAccount?.id == account.id
        // Remember which grants were this account's before its backup goes (spec D20
        // revision 4): its saved login, and the live login when it was the active one.
        removedLoginFingerprints = RemovedLogins.remembering(
            RemovedLogins.fingerprints(ofCredentials: [
                keychain.getAccountBackup(forAccountId: account.id.uuidString)?.token,
                wasActive ? keychain.readClaudeToken() : nil,
            ]),
            existing: removedLoginFingerprints)
        keychain.removeAccountBackup(forAccountId: account.id.uuidString)
        accounts.removeAll { $0.id == account.id }
        // Drop every per-account cache too, or a re-added account inherits the
        // removed one's readings, error banner and rate-limit park.
        accountUsage[account.id] = nil
        accountUsageSampledAt[account.id] = nil
        accountUsageErrors[account.id] = nil
        requestGuard.forget(account: account.id)
        readSoon.remove(account.id)
        persistNeedsReauth()
        let plan = AccountRemoval.plan(removedWasActive: wasActive, remaining: accounts)
        if plan.clearActive {
            // The last account, active or not: nothing is active, and the flag
            // keeps the next refresh from making it again from the live login.
            activeAccount = nil
            UserDefaults.standard.set(true, forKey: removedLastAccountKey)
            log.info("[removeAccount] Removed the last account; no account is active")
        } else if !plan.fallbackIds.isEmpty {
            // No remaining account is marked active until its switch succeeds
            // (`performSwitch` marks it). `activeAccount` stays on the removed
            // account for now: `performSwitch` needs a current account to leave,
            // and the "already live" shortcut must not see the target as current.
            for i in accounts.indices { accounts[i].isActive = false }
            log.info("[removeAccount] Removed active account; switching to the first of \(plan.fallbackIds.count) remaining")
            Task { await switchAfterRemoval(of: account, candidates: plan.fallbackIds) }
        }
        saveAccounts(refreshWidget: true)
        log.info("[removeAccount] Done. Remaining accounts: \(self.accounts.count)")
    }

    /// Switches Claude Code to the first of `candidates` that works (the loop is
    /// `RemovalFallback.run`). It stops as soon as the situation it was started
    /// for no longer holds: before every candidate and after every wait it
    /// checks that the removed account is still the one "active", so a sign-in
    /// or a switch that finished meanwhile is never undone. A busy switch or
    /// sign-in is waited out and, if it never ends, nothing is cleared. Every
    /// failure is logged with its reason. When no candidate could take over, no
    /// account is active and the last failure's sentence is shown (the same
    /// sentence the 1.9 path showed, so no new string).
    private func switchAfterRemoval(of removed: Account, candidates: [UUID]) async {
        let result = await RemovalFallback.run(
            candidates: candidates,
            stillNeeded: { [unowned self] in self.activeAccount?.id == removed.id },
            attempt: { [unowned self] id in
                guard let target = self.accounts.first(where: { $0.id == id }) else { return .gone }
                do {
                    try await self.performSwitch(to: target)
                    // Success, or "already live" (which adopts the account).
                    return self.activeAccount?.id == target.id ? .switched : .failed(message: nil)
                } catch let error as ActionError where error.kind == .busy {
                    return .busy(message: error.message)
                } catch let error as ActionError {
                    return .failed(message: error.message)
                } catch {
                    return .failed(message: error.localizedDescription)
                }
            },
            pause: { try? await Task.sleep(nanoseconds: 1_000_000_000) },
            note: { log.info("[removeAccount] (after removing \(removed.id)) \($0)") })

        switch result {
        case .switched, .stoodDown:
            break
        case .busyGaveUp(let message):
            // Nothing was tried, but the removed account is gone from the list, so it
            // must not stay the current account: a later sign-in would back its login
            // up again under the removed id, and status would report it as active. No
            // remaining account is marked active already. This does not wedge the
            // app: a switch is allowed with no active account, and the live login (the
            // removed account's) is recognised as disposable by the fingerprints
            // recorded at removal. Only if nothing else became active meanwhile.
            if activeAccount?.id == removed.id {
                activeAccount = nil
                for i in accounts.indices { accounts[i].isActive = false }
                saveAccounts()
            }
            errorMessage = message
            log.warning("[removeAccount] Gave up waiting for a switch or sign-in to finish; no fallback was tried, and no account is active. A switch the user starts still works.")
        case .noneWorked(let lastFailure):
            // Only if nothing else made another account active meanwhile
            // (`stillNeeded` was true when the loop ended).
            activeAccount = nil
            for i in accounts.indices { accounts[i].isActive = false }
            if accounts.isEmpty { UserDefaults.standard.set(true, forKey: removedLastAccountKey) }
            saveAccounts()
            errorMessage = lastFailure
            log.warning("[removeAccount] No remaining account could be switched to; no account is active (\(lastFailure ?? "no reason given")). A switch the user starts still works.")
        }
    }

    /// The popover's Switch and double-click, and auto-switch.
    func switchTo(_ account: Account) async {
        do {
            try await performSwitch(to: account)
        } catch let error as ActionError {
            switch error.kind {
            case .failed, .claudeUnavailable: errorMessage = error.message
            case .busy, .noActiveAccount: break  // logged; the GUI has always stayed quiet here
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Switches to `account` and returns once the switch has finished (or was
    /// not needed). Throws `ActionError`; the messages are the ones the popover
    /// has always shown.
    func performSwitch(to account: Account) async throws {
        // With no accounts at all there is nothing to switch to or from. With
        // accounts but none active (the state a removal leaves when no fallback
        // worked, kept across launches) a switch is still allowed: otherwise
        // nothing could ever set an active account again and the user would be
        // stuck until a full sign-in. But nothing is backed up under an unproven
        // owner, so such a switch OVERWRITES the live login without saving it.
        // In that state it therefore goes ahead only when the live login is
        // provably disposable (`LiveLoginDisposal`, spec D20 revision 4, decided
        // below once the API has been asked); otherwise it is refused.
        guard !accounts.isEmpty else {
            log.info("[switchTo] No switch needed (no accounts)")
            throw ActionError(kind: .noActiveAccount, message: String(localized: "No account is active yet, so there is nothing to switch from. Add the current account first.", bundle: L10n.bundle))
        }
        var currentActive = activeAccount

        // One credential mutation at a time: a switch already in flight (its
        // awaits leave the main actor free) or a running login must finish
        // before another switch may touch the keychain and ~/.claude.json.
        guard !isSwitching, !isLoggingIn else {
            log.warning("[switchTo] Skipped: another switch or a login is in progress")
            throw ActionError(kind: .busy, message: String(localized: "A switch or sign-in is in progress. Try again in a moment.", bundle: L10n.bundle))
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
        var liveIdentity: ClaudeService.AccountIdentity?
        if let liveCredential {
            let lookup = await liveLookup(ofCredential: liveCredential, believedAccount: currentActive?.id)
            liveIdentity = lookup.identity
            if let owner = lookup.owner, let identity = lookup.identity { live = (owner, identity) }
        }
        let liveOwner = live?.account

        // No active account and no proven owner (spec D20 revision 4): the login
        // being replaced is not saved anywhere, so decide whether that is safe.
        var adoptsBelievedActive = false
        if currentActive == nil, liveOwner == nil {
            var decision = unownedLiveLoginDecision(credential: liveCredential, identity: liveIdentity)
            if case .treatAsActive(let believedId) = decision {
                if let believed = accounts.first(where: { $0.id == believedId }) {
                    log.warning("[switchTo] No account is active, but the live login is the same grant as \(believed.email)'s saved login; treating \(believed.email) as the active account")
                    currentActive = believed
                    adoptsBelievedActive = true
                } else {
                    decision = .refuse
                }
            }
            switch decision {
            case .proceed(let basis):
                log.warning("[switchTo] No account is active and the live login's owner is not proven; switching anyway (\(basis)), and saving nothing for the login being replaced")
            case .treatAsActive:
                break
            case .refuse:
                log.error("[switchTo] REFUSED: no account is active and the live login cannot be shown to be the removed account's or to be nobody's on the list; nothing was changed")
                throw ActionError(kind: .failed, message: String(localized: "PixelSwitch cannot tell whose login Claude Code holds right now. Use Claude Code once, then try again.", bundle: L10n.bundle))
            }
        }

        if SwitchShortcut.alreadyLive(liveOwnerId: liveOwner?.id, believedActiveId: currentActive?.id, targetId: account.id) {
            log.info("[switchTo] No switch needed: \(account.email)'s login is already live")
            if (liveOwner?.id == account.id && currentActive?.id != account.id) || (adoptsBelievedActive && currentActive?.id == account.id) { adoptActive(account) }
            return
        }
        log.info("[switchTo] ===== Switching from \((liveOwner ?? currentActive)?.email ?? "an unproven login") to \(account.email) =====")

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
            throw ActionError(kind: .failed, message: problem.message)
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
            forgetRemovedLogins()
            saveAccounts()
            // The switch wrote the target's saved login as the live one, and
            // saved the old live login under its owner: both credentials changed.
            noteCredentialChanged(for: account)
            if let liveOwner, liveOwner.id != account.id { noteCredentialChanged(for: liveOwner) }

            await refresh()
            // `refresh()` clears errorMessage, so surface the warning afterwards.
            if let shadowedBy = outcome.shadowedBy {
                errorMessage = String(localized: "Switched to \(account.email), but the Claude CLI is authenticating via \(shadowedBy) instead of the stored login, so it will not use this account.", bundle: L10n.bundle)
            }
            log.info("[switchTo] ===== Switch completed =====")
        } catch {
            isLoading = false
            log.error("[switchTo] Switch failed: \(error.localizedDescription)")
            throw ActionError(kind: .failed, message: error.localizedDescription)
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
        let (uuidHits, emailHits) = accountsNamed(by: identity)
        let byUuid = uuidHits.count == 1 ? uuidHits[0] : nil
        let byEmail = emailHits.count == 1 ? emailHits[0] : nil
        if let byUuid, let byEmail, byUuid.id != byEmail.id { return nil }
        return byUuid ?? byEmail
    }

    /// Every Claude Code account an API identity names, by the permanent account
    /// id and by email. Several in either list, or both lists naming different
    /// accounts, is why `account(matching:)` can return nil for an identity that
    /// does name someone on the list.
    private func accountsNamed(by identity: ClaudeService.AccountIdentity) -> (byUuid: [Account], byEmail: [Account]) {
        let claude = accounts.filter { $0.provider == .claudeCode }
        let backups = keychain.allAccountBackups() ?? [:]
        var byUuid: [Account] = []
        if let uuid = identity.uuid {
            byUuid = claude.filter { (backups[$0.id.uuidString]?.oauthAccount["accountUuid"]?.value as? String) == uuid }
        }
        var byEmail: [Account] = []
        if let email = identity.email {
            byEmail = claude.filter { $0.email.caseInsensitiveCompare(email) == .orderedSame }
        }
        return (byUuid, byEmail)
    }

    /// The account the credential's login belongs to, per Anthropic's API, with
    /// the API's identity for it; nil if the API cannot say (expired token,
    /// network) or names no known account.
    ///
    /// `account` is whose login this is believed to be (the active account for
    /// the live login), so a 401 or 429 from the profile lookup is remembered
    /// against it. A token that is expired, was refused, or is cooling down is
    /// not sent at all.
    private func provenOwner(ofCredential credential: String, believedAccount: UUID?) async -> (account: Account, identity: ClaudeService.AccountIdentity)? {
        let lookup = await liveLookup(ofCredential: credential, believedAccount: believedAccount)
        guard let owner = lookup.owner, let identity = lookup.identity else { return nil }
        return (owner, identity)
    }

    /// What the API said about a credential: the identity it answered with (nil if
    /// it could not say) and the one account on the list that identity names
    /// (nil if it names none or no single one). A 401 or 429 is recorded against
    /// the token whether or not `believedAccount` is known.
    private func liveLookup(ofCredential credential: String, believedAccount: UUID?) async -> (identity: ClaudeService.AccountIdentity?, owner: Account?) {
        guard let login = CredentialOwnership.login(fromCredential: credential),
              let identity = await claudeService.accountIdentity(forAccessToken: login.accessToken, expiresAt: login.expiresAt, account: believedAccount) else { return (nil, nil) }
        return (identity, account(matching: identity))
    }

    /// Spec D20 revision 4: with no active account and no proven owner, whether the
    /// switch may overwrite the live login, which nothing saves. Gathers the facts
    /// and asks `LiveLoginDisposal`.
    private func unownedLiveLoginDecision(credential: String?, identity: ClaudeService.AccountIdentity?) -> LiveLoginDisposal.Decision {
        let usable = CredentialOwnership.isUsableLogin(credential: credential)
        guard usable, let credential, let login = CredentialOwnership.login(fromCredential: credential) else {
            return LiveLoginDisposal.decide(liveLoginUsable: false, api: .couldNotSay, remainingSameGrant: nil, sameGrantAsRemovedLogin: false)
        }
        let api: LiveLoginDisposal.APIAnswer
        if let identity {
            let named = accountsNamed(by: identity)
            api = (named.byUuid.isEmpty && named.byEmail.isEmpty) ? .namesNoRemainingAccount : .namesRemainingAccount
        } else {
            api = .couldNotSay
        }
        let remaining = savedLogins().map { CredentialOwnership.lineageMatches(login, in: $0).compactMap { UUID(uuidString: $0) } }
        let decision = LiveLoginDisposal.decide(
            liveLoginUsable: true, api: api, remainingSameGrant: remaining,
            sameGrantAsRemovedLogin: RemovedLogins.isRemovedLogin(login, fingerprints: removedLoginFingerprints))
        log.info("[switchTo] Live login (token \(TokenFingerprint.forLog(TokenFingerprint.of(login.accessToken)))) with no active account: API \(api), \(remaining.map { String($0.count) } ?? "unreadable") remaining saved login(s) share its grant, \(removedLoginFingerprints.count) removed login(s) remembered, decision \(decision)")
        return decision
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
        // Spec D12: a saved login that is not a usable login is never written to
        // Claude Code's live store; it fails with the re-sign message instead.
        if let reason = LoginGate.targetProblem(backupToken: backup.token) {
            log.error("[switchTo] ABORT: \(account.id)'s saved login is not a usable login (\(reason))")
            return .failure(SwitchProblem(message: String(localized: "The saved login for \(account.email) has expired and could not be renewed. Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
        }
        guard var login = CredentialOwnership.login(fromCredential: backup.token) else {
            return .failure(SwitchProblem(message: String(localized: "The saved login for \(account.email) has expired and could not be renewed. Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle)))
        }

        let notRenewable = SwitchProblem(message: String(localized: "The saved login for \(account.email) has expired and could not be renewed. Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle))
        var identity: ClaudeService.AccountIdentity?

        /// Renews the saved login, then asks the API about the renewed one.
        /// Returns the reason to stop, or nil to carry on.
        func renewAndIdentify() async -> SwitchProblem? {
            switch await refreshBackupInPlace(for: account) {
            case .refreshed(let renewed):
                if let current = keychain.getAccountBackup(forAccountId: account.id.uuidString) { backup = current }
                if let renewedLogin = CredentialOwnership.login(fromCredential: renewed) {
                    login = renewedLogin
                    identity = await claudeService.accountIdentity(forAccessToken: renewedLogin.accessToken, expiresAt: renewedLogin.expiresAt, account: account.id)
                }
                return nil
            case .storeUnavailable, .unreachable:
                // A blip (network, rate limit, locked Keychain): nothing was
                // spent, so go ahead as before; Claude Code renews it on first use.
                log.warning("[switchTo] Could not renew \(account.email)'s expired login right now; switching anyway")
                return nil
            case .belongsToAnotherAccount(let owner):
                return SwitchProblem(message: String(localized: "The login saved for \(account.email) belongs to \(owner). Re-authenticate \(account.email) to fix it.", bundle: L10n.bundle))
            case .grantRejected, .sameGrantAsAnotherLogin, .noBackup, .rotationLost:
                return notRenewable
            }
        }

        // Ask the guard BEFORE the profile lookup: an expired or refused login
        // is renewed first, never sent. The lookup is the call that used to
        // send dead tokens on every switch attempt.
        switch requestGuard.decision(for: account.id, isActive: false, login: login) {
        case .needsReauth:
            return .failure(notRenewable)
        case .refreshFirst:
            if let problem = await renewAndIdentify() { return .failure(problem) }
        case .request:
            identity = await claudeService.accountIdentity(forAccessToken: login.accessToken, expiresAt: login.expiresAt, account: account.id)
            // A 401 just now means the token was refused: renew it and ask again.
            // The guard has already put a cool-down on it, so `decision` would
            // answer "cooling down"; ask what it recorded instead.
            if identity == nil, requestGuard.wasRejected(account: account.id, accessToken: login.accessToken) {
                if let problem = await renewAndIdentify() { return .failure(problem) }
            }
        case .parked, .coolingDown, .waitForClaudeCode:
            // Not asking the usage or profile endpoint now. An expired or
            // refused login can still be renewed (a POST to the token endpoint,
            // outside both limits), as it always was for an expired target.
            if login.isExpired || requestGuard.wasRejected(account: account.id, accessToken: login.accessToken) {
                if let problem = await renewAndIdentify() { return .failure(problem) }
            } else {
                // The lineage check below still refuses a login that is also
                // saved under another account.
                log.info("[switchTo] Not asking the API about \(account.email)'s saved login right now; checking lineage only")
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
        // It now reads its live login, which is not the saved one the guard
        // may have flagged; forget whatever was earned on the old token.
        noteCredentialChanged(for: account)
    }

    /// Keeps PixelSwitch tied to the live login rather than to ~/.claude.json:
    /// follows the login's proven owner, restores that owner's identity in
    /// ~/.claude.json if a session left another account's there, and keeps the
    /// owner's backup current, so switching back restores the newest login
    /// instead of one whose refresh token the CLI has since rotated.
    private func reconcileLiveLogin() async {
        guard !isSwitching, !isLoggingIn, let credential = keychain.readClaudeToken() else { return }
        guard let (owner, ownerIdentity) = await provenOwner(ofCredential: credential, believedAccount: activeAccount?.id) else { return }
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
            let tokenChanged = CredentialOwnership.login(fromCredential: backup.token)?.accessToken
                != CredentialOwnership.login(fromCredential: credential)?.accessToken
            if keychain.saveAccountBackup(token: credential, oauthAccount: identity, forAccountId: owner.id.uuidString) {
                log.info("[reconcile] \(owner.email)'s backup updated from the live login\(tokenChanged ? "" : " (other parts of the credential changed; the Claude token did not)")")
                // Compare the Claude token, not the whole credential: the blob
                // also holds this Mac's MCP logins, which change without the
                // Claude token changing.
                if tokenChanged { noteCredentialChanged(for: owner) }
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
                  !requestGuard.isNeedsReauth(account.id),
                  let identity = await claudeService.accountIdentity(forAccessToken: login.accessToken, expiresAt: login.expiresAt, account: account.id),
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
    func isSwitchable(_ account: Account) -> Bool {
        // The backup must exist and be a usable login (spec D12: a blank token or
        // a missing claudeAiOauth would sign Claude Code out), the server must not
        // have refused its refresh grant, and its card must not say it expired.
        LoginGate.isSwitchable(
            backupToken: keychain.getAccountBackup(forAccountId: account.id.uuidString)?.token,
            needsReauth: requestGuard.isNeedsReauth(account.id),
            cardSaysLoginExpired: accountUsageErrors[account.id]?.isExpired == true)
    }

    /// Whether the account's last usage request was refused for want of an
    /// active subscription (`markNoSubscription`). Kept apart from
    /// `isSwitchable`, whose "cannot be switched to" means "sign in again",
    /// which does not help here.
    private func hasNoSubscription(_ account: Account) -> Bool {
        accountUsageErrors[account.id]?.isNoSubscription == true
    }

    /// Evaluate whether auto-switch should move the user, and if so where.
    /// Called after every completed refresh. Safe to call repeatedly.
    ///
    /// Two rules can fire (`AutoSwitchEngine.planWindowed`): the active account
    /// reaching ITS OWN limit on a watched window (session or weekly, then
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

        // Cooldown: never auto-switch more than once per window, except off an
        // account with no subscription, which nothing can move you back to.
        let activeUnusable = hasNoSubscription(active)
        if AutoSwitchEngine.cooldownHolds(lastSwitchAt: lastAutoSwitchAt, cooldown: autoSwitchCooldown, activeUnusable: activeUnusable) {
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
        let defaults = windowDefaults
        guard let plan = AutoSwitchEngine.planWindowed(
            active: active,
            candidates: candidates,
            usageByAccount: accountUsage,
            isSwitchable: { [unowned self] in self.isSwitchable($0) },
            isUnusable: { [unowned self] in self.hasNoSubscription($0) },
            activeSampledThisCycle: activeSampledThisCycle,
            rule: { [unowned self] in self.windowRule(for: $0) },
            defaults: defaults,
            hysteresisPct: AutoSwitchEngine.hysteresis,
            watchFable: watchFable,
            strategy: strategy,
            drainEarly: drainEarly,
            drainWithin: drainWithin
        ) else {
            if activeUnusable {
                log.warning("[autoSwitch] Active \(active.id) has no active subscription, and no other account has room; staying put")
            } else {
                noteStayingPut(active, sampledThisCycle: activeSampledThisCycle, watchFable: watchFable)
            }
            return
        }
        lastStayingPutNote = nil
        let limit = plan.limit
        let trigger = plan.trigger
        let relaxed = plan.relaxed
        let ranked = plan.targets
        // The active account's weekly reset as the plan saw it. A drain target
        // must still reset before it when its fresh reading is checked.
        let activeWeeklyReset = AutoSwitchEngine.weeklyReset(accountUsage[active.id], limit: .windows)

        isEvaluatingAutoSwitch = true
        defer { isEvaluatingAutoSwitch = false }

        let activeRule = windowRule(for: active)
        let leave = AutoSwitchSettings.leaveAtPair(rule: relaxed ? activeRule.relaxed : activeRule,
                                                   defaults: relaxed ? defaults.relaxed : defaults)
        let manualNote = activeRule.manualOnly ? ", manual only: left at the default" : ""
        let relaxedNote = relaxed ? "; no account under its weekly limit can take you, so the session limit decides" : ""
        if trigger == .noSubscription {
            log.info("[autoSwitch] Rule \(trigger.rawValue), strategy \(strategy.rawValue): active \(active.id) has no active subscription; \(ranked.count) candidate(s)\(relaxedNote)")
        } else {
            let activeUtil: Double
            let onWhat: String
            if let window = plan.window {
                activeUtil = AutoSwitchEngine.windowUtilization(accountUsage[active.id], window: window, requireKnownWindow: !activeSampledThisCycle) ?? -1
                onWhat = "\(limit.rawValue) (\(window.rawValue) window)"
            } else {
                activeUtil = AutoSwitchEngine.utilization(accountUsage[active.id], limit: limit) ?? -1
                onWhat = limit.rawValue
            }
            log.info("[autoSwitch] Rule \(trigger.rawValue), strategy \(strategy.rawValue): active \(active.id) at \(String(format: "%.0f", activeUtil))% on \(onWhat) (\(Self.limitsText(leave))\(manualNote)); \(ranked.count) candidate(s)\(relaxedNote)")
        }

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

            // The same rule, with the same per-account limits and in the same
            // mode (strict or relaxed), that ranked it.
            let targetRule = windowRule(for: target)
            guard let verified = AutoSwitchEngine.eligibleWindowed(
                usage, limit: limit, trigger: trigger, rule: targetRule, relaxed: relaxed,
                hysteresisPct: AutoSwitchEngine.hysteresis,
                activeWeeklyReset: activeWeeklyReset, drainWithin: drainWithin, watchFable: watchFable
            ) else {
                if targetRule.manualOnly {
                    log.info("[autoSwitch] Candidate \(target.id) is manual only now; trying next")
                    continue
                }
                let onLimit = AutoSwitchEngine.utilization(usage, limit: limit).map { String(format: "%.0f%%", $0) } ?? "no reading"
                let onWindows = AutoSwitchEngine.bindingUtilization(usage).map { String(format: "%.0f%%", $0) } ?? "no reading"
                let targetLeave = AutoSwitchSettings.leaveAtPair(rule: relaxed ? targetRule.relaxed : targetRule, defaults: defaults)
                log.info("[autoSwitch] Candidate \(target.id) failed verification for \(trigger.rawValue) (\(limit.rawValue) \(onLimit), windows \(onWindows), \(Self.limitsText(targetLeave, prefix: "its")))\(relaxed ? " in the weekly overflow pass" : ""); trying next")
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

            let verifiedUtil = verified.value(for: limit) ?? -1
            log.info("[autoSwitch] Switching to \(target.id) by rule \(trigger.rawValue) on \(limit.rawValue), verified at \(String(format: "%.0f", verifiedUtil))%\(relaxedNote)")
            lastAutoSwitchAt = Date()
            // switchTo() calls refresh() -> evaluateAutoSwitch() again, but the
            // re-entrancy flag + the freshly-set cooldown make that a no-op.
            // The active account visibly changes in the menu bar as feedback.
            await switchTo(target)
            if activeAccount?.id == target.id {
                lastAutoSwitch = AutoSwitchRecord(from: active.id, to: target.id, limit: limit, trigger: trigger,
                                                  window: plan.window, relaxed: relaxed, at: Date())
            } else {
                log.warning("[autoSwitch] Switch to \(target.id) did not complete")
            }
            return
        }
        switch trigger {
        case .threshold: log.info("[autoSwitch] Threshold reached but no candidate verified; staying put")
        case .drainEarly: log.info("[autoSwitch] Early drain found no verified candidate; staying put")
        case .noSubscription: log.info("[autoSwitch] No subscription on the active account, and no candidate verified; staying put")
        }
    }

    /// "its threshold 98%" when both windows leave at the same number (the 1.9
    /// wording, kept for log readers), else "its limits 90% session, 50% weekly".
    private static func limitsText(_ pair: AutoSwitchEngine.WindowPair, prefix: String = "its") -> String {
        if pair.session == pair.weekly { return String(format: "\(prefix) threshold %.0f%%", pair.session) }
        return String(format: "\(prefix) limits %.0f%% session, %.0f%% weekly", pair.session, pair.weekly)
    }

    /// Log, once per change, that the active account has reached a limit but no
    /// other account has room: before 1.7 this state logged nothing, so a stuck
    /// auto-switch looked the same as one that had nothing to do. Names the
    /// window. When the weekly overflow holds and even the relaxed plan found
    /// nobody (spec 2.3 rule 10), says the account stays until the session limit.
    private func noteStayingPut(_ active: Account, sampledThisCycle: Bool, watchFable: Bool) {
        let rule = windowRule(for: active)
        let defaults = windowDefaults
        let leave = AutoSwitchSettings.leaveAtPair(rule: rule, defaults: defaults)
        let usage = accountUsage[active.id]
        var reached: [String] = []
        for window in AutoSwitchEngine.Window.allCases {
            if let util = AutoSwitchEngine.windowUtilization(usage, window: window, requireKnownWindow: !sampledThisCycle),
               util >= leave.value(for: window) {
                reached.append("\(String(format: "%.0f", util))% on the \(window.rawValue) window")
            }
        }
        if watchFable, AutoSwitchEngine.fableReached(usage, leaveAt: leave, requireKnownWindow: !sampledThisCycle),
           let fable = AutoSwitchEngine.utilization(usage, limit: .fable, requireKnownWindow: !sampledThisCycle) {
            reached.append("\(String(format: "%.0f", fable))% on fable")
        }
        guard !reached.isEmpty else {
            lastStayingPutNote = nil
            return
        }
        // The overflow sentence only while the account is still under its relaxed
        // limits (rule 10); at or past them, equal windows included, 1.9's
        // sentence is the true one.
        let kind = AutoSwitchEngine.stayingPutKind(activeUsage: usage, activeRule: rule, defaults: defaults,
                                                   activeSampledThisCycle: sampledThisCycle, watchFable: watchFable)
        let note: String
        if kind == .waitingForSessionLimit {
            note = "[autoSwitch] Active \(active.id) at \(reached.joined(separator: ", ")) (\(Self.limitsText(leave))), but no account under its weekly limit can take you; staying until the session limit (\(String(format: "%.0f", leave.session))%)"
        } else {
            note = "[autoSwitch] Active \(active.id) at \(reached.joined(separator: ", ")) (\(Self.limitsText(leave))), but no other account has room under its own limits; staying put"
        }
        if note != lastStayingPutNote {
            log.info(note)
            lastStayingPutNote = note
        }
    }

    /// Take one fresh usage reading for an account right now, through the
    /// guard (`readUsage`): a saved login whose access token has expired is
    /// renewed in place first, and nothing is sent for a token that is dead,
    /// parked or cooling down. Returns nil when no reading could be taken.
    private func fetchUsageNow(for account: Account) async -> UsageAPIResponse? {
        await readUsage(for: account)
    }

    // MARK: - Usage, through the guard

    /// The login a usage request for this account would carry: the live one for
    /// the active account, the saved one for every other. Looked up by id, so a
    /// stale copy of the account (held across an await) still reads the right one.
    private func usageLogin(for account: Account) -> CredentialOwnership.Login? {
        let isActive = accounts.first(where: { $0.id == account.id })?.isActive ?? account.isActive
        let credential = isActive
            ? keychain.readClaudeToken()
            : keychain.getAccountBackup(forAccountId: account.id.uuidString)?.token
        return credential.flatMap { CredentialOwnership.login(fromCredential: $0) }
    }

    private func isActiveNow(_ account: Account) -> Bool {
        accounts.first(where: { $0.id == account.id })?.isActive ?? account.isActive
    }

    /// What the account's card says right now, in the terms `UsageNotice` needs.
    private func currentNotice(for account: Account) -> UsageNotice.Current {
        guard let error = accountUsageErrors[account.id] else { return .nothing }
        if error.isNoSubscription { return .noSubscription }
        if error.isExpired { return .resign }
        return .transient
    }

    /// Puts on the card what a decision that sends nothing should say. The last
    /// reading stays for a pause or a wait; an expired or re-sign message is
    /// never replaced by a pause.
    private func showNotice(_ decision: UsageDecision, for account: Account) {
        switch UsageNotice.choose(for: decision, current: currentNotice(for: account)) {
        case .keepCurrent:
            break
        case .paused(let until):
            accountUsageErrors[account.id] = UsageErrorState(
                isExpired: false, isRateLimited: true, message: UsageRequestError.pausedMessage(until: until))
        case .waitingForClaudeCode:
            accountUsageErrors[account.id] = UsageErrorState(
                isExpired: false, isRateLimited: true,
                message: String(localized: "Waiting for Claude Code to renew this login. It renews the next time a session runs.", bundle: L10n.bundle))
        case .resign:
            showResign(for: account)
        }
    }

    /// The account needs signing in again: no reading, the re-sign message.
    private func showResign(for account: Account, message: String? = nil) {
        accountUsage[account.id] = nil
        accountUsageSampledAt[account.id] = nil
        accountUsageErrors[account.id] = UsageErrorState(
            isExpired: true, isRateLimited: false,
            message: message ?? String(localized: "Session expired. Re-authenticate (↻) to fix.", bundle: L10n.bundle))
    }

    private static var belongedToAnotherMessage: String {
        String(localized: "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix.", bundle: L10n.bundle)
    }

    /// One usage reading for `account`. Asks the guard first; on "send nothing"
    /// the card is told why and nil comes back. Every 401 is remembered by
    /// token, so the same token is never sent twice.
    private func readUsage(for account: Account) async -> UsageAPIResponse? {
        guard let login = usageLogin(for: account) else {
            log.warning("[fetchUsage] No token for \(account.email), skipping")
            return nil
        }
        let decision = requestGuard.decision(for: account.id, isActive: isActiveNow(account), login: login)
        switch decision {
        case .request:
            return await requestUsage(for: account, login: login, afterRenewal: false)
        case .refreshFirst:
            log.info("[fetchUsage] \(account.email): saved login (token \(TokenFingerprint.forLog(TokenFingerprint.of(login.accessToken)))) is expired or was refused; renewing it before asking")
            return await renewThenRequest(account)
        case .needsReauth, .parked, .coolingDown, .waitForClaudeCode:
            log.info("[fetchUsage] \(account.email): nothing sent (\(decision))")
            showNotice(decision, for: account)
            return nil
        }
    }

    /// The one place a usage request is made.
    private func requestUsage(for account: Account, login: CredentialOwnership.Login, afterRenewal: Bool) async -> UsageAPIResponse? {
        do {
            return try await claudeService.getUsageLimits(accessToken: login.accessToken)
        } catch ClaudeService.UsageError.expired {
            requestGuard.recordRejected(account: account.id, accessToken: login.accessToken)
            log.warning("[fetchUsage] 401 for \(account.email) (token \(TokenFingerprint.forLog(TokenFingerprint.of(login.accessToken)))); it will not be sent again until it changes")
            if afterRenewal {
                // A token minted a moment ago was refused: nothing more to try.
                recordUsageFailure(ClaudeService.UsageError.expired, for: account)
                return nil
            }
            if isActiveNow(account) {
                // Claude Code owns the live login and renews it itself; say so
                // and keep the last reading.
                showNotice(.waitForClaudeCode, for: account)
                return nil
            }
            return await renewThenRequest(account)
        } catch ClaudeService.UsageError.rateLimited(let retryAfter) {
            let until = requestGuard.recordRateLimited(account: account.id, accessToken: login.accessToken, retryAfter: retryAfter)
            log.warning("[fetchUsage] \(account.id) rate-limited; parked for \(String(format: "%.0f", until.timeIntervalSinceNow))s")
            showNotice(.parked(until: until), for: account)
            return nil
        } catch ClaudeService.UsageError.forbidden {
            // No active Pro/Max subscription on this account (e.g. the plan
            // lapsed) - usage is meaningless until it recovers. Observed as:
            // {"error":{"type":"permission_error","message":"OAuth
            // authentication is currently not allowed for this organization."}}
            log.warning("[fetchUsage] \(account.email) forbidden (no active subscription?)")
            markNoSubscription(account)
            return nil
        } catch {
            log.warning("[fetchUsage] \(account.email): \(error.localizedDescription)")
            recordUsageFailure(error, for: account)
            return nil
        }
    }

    /// Renews a saved login in place, then asks once with the new token. Every
    /// outcome is handled: collapsing a dead grant (or a lost rotation) to a
    /// plain nil would leave the account looking healthy.
    private func renewThenRequest(_ account: Account) async -> UsageAPIResponse? {
        switch await refreshBackupInPlace(for: account) {
        case .refreshed(let refreshed):
            log.info("[fetchUsage] Silently refreshed backup for \(account.email); asking with the new token")
            guard let newLogin = CredentialOwnership.login(fromCredential: refreshed) else { return nil }
            return await requestUsage(for: account, login: newLogin, afterRenewal: true)
        case .grantRejected, .rotationLost, .belongsToAnotherAccount:
            // refreshBackupInPlace has flagged the account and put the re-sign
            // message on its card.
            return nil
        case .sameGrantAsAnotherLogin, .noBackup:
            // Not the server's verdict, so the account is NOT flagged: it only
            // cannot be renewed from here.
            showResign(for: account)
            return nil
        case .unreachable:
            // The token endpoint gave no verdict (offline, a 5xx, a 429): the
            // same honest line the usage request gives, and the reading stays.
            recordUsageFailure(UsageRequestError.network(offline: false), for: account)
            return nil
        case .storeUnavailable:
            // Nothing was spent and nothing is lost; this heals by itself on a
            // later cycle. Keep the stale sample.
            accountUsageErrors[account.id] = UsageErrorState(isExpired: false, isRateLimited: false, message: String(localized: "Could not save the refreshed sign-in; will retry automatically.", bundle: L10n.bundle))
            return nil
        }
    }

    // MARK: - Credential changes and accounts that must sign in again

    /// Called wherever an account's stored or live credential changes: a
    /// sign-in, a renewal, a switch, the live login saved over its backup.
    /// Forgets what was earned on the old token (park, refusal, cool-down,
    /// needs-reauth), drops the card's old complaint and gets the account read
    /// on the next cycle, ahead of the round-robin.
    private func noteCredentialChanged(for account: Account, readNext: Bool = true) {
        let token = usageLogin(for: account)?.accessToken
        requestGuard.credentialChanged(account: account.id, accessToken: token)
        persistNeedsReauth()
        if readNext {
            accountUsageErrors[account.id] = nil
            readSoon.insert(account.id)
        }
    }

    /// The server refused this account's refresh grant (or its saved login is
    /// lost or was someone else's): nothing is sent for it until it signs in again.
    private func flagNeedsReauth(_ account: Account, belongedToAnother: Bool = false) {
        requestGuard.markNeedsReauth(account: account.id, accessToken: usageLogin(for: account)?.accessToken)
        persistNeedsReauth()
        log.warning("[needsReauth] \(account.email) must sign in again; no usage, profile or refresh requests until its login changes")
        showResign(for: account, message: belongedToAnother ? Self.belongedToAnotherMessage : nil)
    }

    private var lastSavedNeedsReauth: Set<UUID> = []

    private func persistNeedsReauth() {
        let current = requestGuard.needsReauthAccounts
        guard current != lastSavedNeedsReauth else { return }
        lastSavedNeedsReauth = current
        UserDefaults.standard.set(current.map(\.uuidString).sorted(), forKey: needsReauthKey)
    }

    private func restoreNeedsReauth() {
        let ids = Set((UserDefaults.standard.stringArray(forKey: needsReauthKey) ?? []).compactMap(UUID.init(uuidString:)))
        let known = ids.filter { id in accounts.contains(where: { $0.id == id }) }
        for id in known {
            // Tie the flag to the dead token the account holds now, so any other
            // token clears it.
            let token = keychain.getAccountBackup(forAccountId: id.uuidString)
                .flatMap { CredentialOwnership.login(fromCredential: $0.token)?.accessToken }
            requestGuard.markNeedsReauth(account: id, accessToken: token)
        }
        lastSavedNeedsReauth = known
        if known != ids { UserDefaults.standard.set(known.map(\.uuidString).sorted(), forKey: needsReauthKey) }
        if !known.isEmpty { log.info("[init] \(known.count) account(s) need to sign in again") }
    }

    /// A 403 on the usage request: the account has no active subscription
    /// ("OAuth authentication is currently not allowed for this organization").
    /// Clears its reading and flags it, so auto-switch leaves it while it is
    /// active and never moves you to it (`hasNoSubscription`).
    private func markNoSubscription(_ account: Account) {
        accountUsage[account.id] = nil
        accountUsageSampledAt[account.id] = nil
        accountUsageErrors[account.id] = UsageErrorState(
            isExpired: false, isRateLimited: false,
            message: String(localized: "No active subscription on this account (OAuth not allowed).", bundle: L10n.bundle),
            isNoSubscription: true)
    }

    /// A usage request that failed for a reason with no path of its own (not
    /// 429, 403 or an expired token): say what happened in plain words, and
    /// keep the last reading when the failure says nothing about the account
    /// (a 5xx or no connection), as a 429 does. Every catch-all goes through
    /// here, so no card shows Swift's "UsageError error 0" again.
    private func recordUsageFailure(_ error: Error, for account: Account) {
        let failure = error as? UsageRequestError
        if failure?.keepsLastReading != true {
            accountUsage[account.id] = nil
            accountUsageSampledAt[account.id] = nil
        }
        accountUsageErrors[account.id] = UsageErrorState(
            isExpired: false, isRateLimited: false,
            message: failure?.message ?? String(localized: "Could not fetch usage: \(error.localizedDescription)", bundle: L10n.bundle),
            canRetry: failure?.canRetry ?? true,
            offersStatusPage: failure?.offersStatusPage ?? false)
    }

    /// The card's Retry: one fresh reading for this account now. The active
    /// account takes a full refresh (its token is refreshed through Claude Code
    /// there); any other takes one request, which a rate-limit park still
    /// holds back. Auto-switch is evaluated on a new reading, as after a cycle.
    func retryUsage(for account: Account) async {
        guard !retryingUsage.contains(account.id) else { return }
        retryingUsage.insert(account.id)
        defer { retryingUsage.remove(account.id) }
        log.info("[retryUsage] \(account.email): retry from the card")
        if account.isActive {
            await refresh()
            return
        }
        guard let usage = await fetchUsageNow(for: account) else { return }
        accountUsage[account.id] = usage
        accountUsageSampledAt[account.id] = Date()
        accountUsageErrors[account.id] = nil
        if !isRefreshing { await evaluateAutoSwitch() }
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
        // Settled once, by the call that started the renewal. What the server
        // said decides what the account is allowed to do next.
        if case .refreshed = outcome {
            noteCredentialChanged(for: account, readNext: false)
        } else if outcome.requiresReauth {
            if case .belongsToAnotherAccount = outcome {
                flagNeedsReauth(account, belongedToAnother: true)
            } else {
                flagNeedsReauth(account)
            }
        }
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

        // A saved login that is not a usable login (spec D12) has nothing to
        // renew with: the account must sign in again. Said before the store probe
        // below, which now refuses to save such a login.
        if let reason = CredentialOwnership.unusableReason(credential: backup.token) {
            log.warning("[refreshBackup] \(account.id)'s saved login is not a usable login (\(reason)); it must sign in again")
            return .grantRejected
        }

        // Never spend a refresh token that is not provably this account's own.
        // If the same grant is live or saved under another account, refreshing
        // it here rotates that account's refresh token and can sign its running
        // sessions out.
        if let login = CredentialOwnership.login(fromCredential: backup.token) {
            let live = keychain.readClaudeToken().flatMap { CredentialOwnership.login(fromCredential: $0) }
            let others = (savedLogins() ?? [:]).filter { $0.key != accountId }
            let isLive = live.map { CredentialOwnership.sameGrant($0, login) } ?? false
            if let refusal = BackupRefreshOutcome.localRefusal(isLive: isLive, sharesLineage: !CredentialOwnership.lineageMatches(login, in: others).isEmpty) {
                log.error("[refreshBackup] \(account.id)'s saved login is the same grant as \(isLive ? "the live login" : "another account's"); not refreshing it")
                return refusal
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
               let identity = await claudeService.accountIdentity(forAccessToken: renewed.accessToken, expiresAt: renewed.expiresAt, account: account.id),
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
            return .unreachable
        }
    }

    private func fetchAllAccountUsage() async {
        // Ask the guard about every account first. An account it says to leave
        // alone (a dead grant, a 429 park, a cool-down, the live login waiting
        // for Claude Code) is never a target, and its card says why.
        var pollable: [Account] = []
        for account in accounts {
            // Spec D12: a saved login that is not a usable login is never asked
            // about; its card says to sign in again and it is not switchable.
            if !account.isActive, let backup = keychain.getAccountBackup(forAccountId: account.id.uuidString),
               !CredentialOwnership.isUsableLogin(credential: backup.token) {
                if requestGuard.isNeedsReauth(account.id) {
                    showNotice(.needsReauth, for: account)
                } else {
                    flagNeedsReauth(account)
                }
                continue
            }
            guard let login = usageLogin(for: account) else {
                if requestGuard.isNeedsReauth(account.id) {
                    showNotice(.needsReauth, for: account)
                } else {
                    pollable.append(account)   // readUsage logs "No token" and skips it
                }
                continue
            }
            let decision = requestGuard.decision(for: account.id, isActive: account.isActive, login: login)
            switch decision {
            case .request, .refreshFirst:
                pollable.append(account)
            case .needsReauth, .parked, .coolingDown, .waitForClaudeCode:
                showNotice(decision, for: account)
            }
        }
        // `decision` clears a flag that had no token when the account turned out
        // to be the active one; keep the saved copy in step.
        persistNeedsReauth()

        // Pick this cycle's targets: the active account (when it may be asked)
        // + one other in round-robin order. An account whose credential just
        // changed goes first. Stale samples for the others are kept. The pace
        // no longer depends on how many accounts are parked: a failing token
        // is held back for 20 minutes by the guard itself.
        let others = pollable.filter { !$0.isActive }
        var targets = pollable.filter { $0.isActive }
        if !others.isEmpty {
            if let fixed = others.first(where: { readSoon.contains($0.id) }) {
                targets.append(fixed)
            } else {
                targets.append(others[usageFetchCursor % others.count])
                usageFetchCursor += 1
            }
        }
        readSoon.subtract(targets.map(\.id))

        // Only clear error state for the accounts we are about to sample;
        // the others keep both their stale usage and their error flags.
        for target in targets {
            accountUsageErrors[target.id] = nil
        }

        // The active account reads the live Keychain login, every other account
        // its saved login (renewed in place first when expired). Nothing here
        // asks Claude Code to renew the live login: it does that itself the
        // next time a session runs, and the account is read again the moment
        // its token changes.
        var isFirstRequest = true
        for account in targets {
            // Stagger requests: a back-to-back burst (one request per account within
            // ~100ms) reliably gets all but one of them 429'd by the usage endpoint.
            if !isFirstRequest {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
            }
            isFirstRequest = false

            if let usage = await readUsage(for: account) {
                accountUsage[account.id] = usage
                accountUsageSampledAt[account.id] = Date()
                accountUsageErrors[account.id] = nil
                log.info("[fetchUsage] \(account.email): session=\(usage.fiveHour?.utilization ?? -1)%, weekly=\(usage.sevenDay?.utilization ?? -1)%")
            }
        }
        persistNeedsReauth()
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
        // Always kept sorted by priority (spec D3), so a list saved by 1.9, or
        // one edited elsewhere, comes up in priority order.
        accounts = AccountOrder.sortedByPriority(decoded)
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
        } else if AccountRemoval.mayCreateFromLiveLogin(accountsAreEmpty: accounts.isEmpty,
                                                        removedLastAccount: UserDefaults.standard.bool(forKey: removedLastAccountKey)) {
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
    /// Which window fired (only for a threshold on `.windows`), 2.0.
    let window: AutoSwitchEngine.Window?
    /// Made by the weekly overflow pass (spec 2.3 rule 9), 2.0.
    let relaxed: Bool
    let at: Date
}
