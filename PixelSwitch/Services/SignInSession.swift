import Foundation
import Combine

/// Why a sign-in was started.
enum SignInPurpose: Equatable, Sendable {
    case newAccount
    case reauthenticate(accountId: UUID, email: String)
}

/// Where a sign-in is. Moves forward only:
/// starting → waitingForUser → completing → succeeded, failed or cancelled.
enum SignInState: Equatable, Sendable {
    case starting, waitingForUser, completing
    case succeeded(accountId: UUID), failed(message: String), cancelled

    /// Succeeded, failed or cancelled.
    var isFinished: Bool {
        switch self {
        case .succeeded, .failed, .cancelled: return true
        case .starting, .waitingForUser, .completing: return false
        }
    }
}

/// What `SignInSession` runs: the claude binary and the environment every
/// `claude` subprocess gets (see `ClaudeProcessEnvironment`).
struct SignInLaunch: Sendable {
    let executable: URL
    let environment: [String: String]
}

/// One run of `claude auth login` that shows its links instead of opening a
/// browser. The GUI and (later) the control API both observe it.
///
/// Nothing opens by itself: `BROWSER` points at a helper that records the
/// automatic link (see `SignInLinkCapture`), and the manual link is read from
/// the CLI's output. The state reaches `waitingForUser` as soon as either link
/// is known. When the CLI exits 0 the session is `completing` while `complete`
/// (AppState's capture steps) runs, and its result is the final state.
@MainActor
final class SignInSession: ObservableObject, Identifiable {
    /// If neither link is known this long after the CLI starts, the session
    /// falls back to letting Claude Code open the default browser.
    static let linkWait: TimeInterval = 10
    /// A sign-in nobody finishes is stopped after this long.
    static let overallLimit: TimeInterval = 15 * 60
    /// After SIGTERM, SIGKILL follows this much later if the CLI still runs.
    static let killAfter: TimeInterval = 2
    /// After asking the CLI to stop, the session ends this much later even if
    /// no exit was reported, so a sign-in can never stay busy forever.
    static let giveUpAfter: TimeInterval = 5
    /// How often the capture file is read.
    static let capturePollInterval: TimeInterval = 0.25
    /// Output kept for parsing. The links arrive in the first few hundred bytes.
    static let outputLimit = 64 * 1024

    let id: UUID
    let purpose: SignInPurpose
    let startedAt: Date
    @Published private(set) var state: SignInState = .starting
    /// Localhost redirect: finishes by itself in any browser on this Mac.
    @Published private(set) var automaticLink: URL?
    /// Code-callback redirect: the page shows a code to paste back.
    @Published private(set) var manualLink: URL?
    /// Set when PixelSwitch fell back to letting Claude Code open the default browser.
    @Published private(set) var notice: String?
    /// True once a code was handed to Claude Code, which is now checking it.
    @Published private(set) var codeSubmitted = false

    private enum StopReason { case fallback, cancel, timeout }

    private let launch: SignInLaunch
    private let runner: any SignInProcessRunner
    private let scheduler: any SignInScheduler
    private let captureParent: URL
    private let log: @MainActor (String) -> Void
    private let complete: @MainActor (SignInSession) async -> SignInState
    private var onFinish: (@MainActor (SignInSession) -> Void)?

    private var didStart = false
    private var capture: SignInLinkCapture?
    private var handle: (any SignInProcessHandle)?
    private var generation = 0
    private var output = Data()
    private var stopReason: StopReason?
    private var inFallback = false
    private var linkTimer: SignInTimer?
    private var overallTimer: SignInTimer?
    private var pollTimer: SignInTimer?
    private var killTimer: SignInTimer?
    private var giveUpTimer: SignInTimer?

    /// - Parameters:
    ///   - complete: runs after the CLI exits 0; returns the final state.
    ///   - onFinish: called exactly once, when the state becomes finished.
    init(
        purpose: SignInPurpose,
        launch: SignInLaunch,
        runner: any SignInProcessRunner,
        scheduler: any SignInScheduler,
        captureParent: URL = FileManager.default.temporaryDirectory,
        id: UUID = UUID(),
        log: @escaping @MainActor (String) -> Void = { _ in },
        complete: @escaping @MainActor (SignInSession) async -> SignInState,
        onFinish: @escaping @MainActor (SignInSession) -> Void
    ) {
        self.id = id
        self.purpose = purpose
        self.launch = launch
        self.runner = runner
        self.scheduler = scheduler
        self.captureParent = captureParent
        self.log = log
        self.complete = complete
        self.onFinish = onFinish
        self.startedAt = scheduler.now
    }

    /// `claude` arguments for a purpose. Re-authentication pre-fills the email.
    static func arguments(for purpose: SignInPurpose) -> [String] {
        switch purpose {
        case .newAccount: return ["auth", "login"]
        case .reauthenticate(_, let email): return ["auth", "login", "--email", email]
        }
    }

    // MARK: - Starting

    /// Starts the CLI. AppState calls this after backing up the current
    /// account. Does nothing if already started or already finished (a sign-in
    /// cancelled during the backup never launches anything).
    func start() {
        guard !didStart, !state.isFinished else { return }
        didStart = true
        let capture: SignInLinkCapture
        do {
            capture = try SignInLinkCapture.create(in: captureParent, id: id, now: scheduler.now)
        } catch {
            log("[signIn] Could not prepare the link capture: \(error.localizedDescription)")
            finish(.failed(message: String(localized: "Couldn't prepare the sign-in: \(error.localizedDescription)", bundle: L10n.bundle)))
            return
        }
        self.capture = capture
        let environment = launch.environment.merging(capture.environmentOverrides) { _, override in override }
        guard launchProcess(environment: environment) else { return }
        overallTimer = scheduler.after(Self.overallLimit) { [weak self] in self?.overallLimitReached() }
        linkTimer = scheduler.after(Self.linkWait) { [weak self] in self?.linkWaitElapsed() }
        schedulePoll()
    }

    @discardableResult
    private func launchProcess(environment: [String: String]) -> Bool {
        generation += 1
        let current = generation
        output = Data()
        do {
            handle = try runner.start(
                executable: launch.executable,
                arguments: Self.arguments(for: purpose),
                environment: environment,
                onOutput: { [weak self] data in self?.received(data, generation: current) },
                onExit: { [weak self] status in self?.exited(status: status, generation: current) }
            )
        } catch {
            log("[signIn] Could not start Claude Code: \(error.localizedDescription)")
            finish(.failed(message: String(localized: "Couldn't start Claude Code: \(error.localizedDescription)", bundle: L10n.bundle)))
            return false
        }
        log("[signIn] Started `claude auth login`\(inFallback ? " (fallback: Claude Code opens the default browser)" : "")")
        return true
    }

    // MARK: - Links

    private func received(_ data: Data, generation: Int) {
        guard generation == self.generation, stopReason == nil, !state.isFinished else { return }
        if output.count < Self.outputLimit {
            output.append(data.prefix(Self.outputLimit - output.count))
        }
        guard manualLink == nil,
              let link = SignInOutputParser.manualLink(in: String(decoding: output, as: UTF8.self)) else { return }
        manualLink = link
        log("[signIn] Manual link read from the output: \(SignInOutputParser.redacted(link))")
        linkArrived()
    }

    private func schedulePoll() {
        pollTimer = scheduler.after(Self.capturePollInterval) { [weak self] in self?.pollCapture() }
    }

    private func pollCapture() {
        pollTimer = nil
        guard !state.isFinished, stopReason == nil, capture != nil, !captureAutomaticLink() else { return }
        schedulePoll()
    }

    /// Reads the capture file once. True if the automatic link is now known.
    @discardableResult
    private func captureAutomaticLink() -> Bool {
        guard automaticLink == nil, let capture,
              let link = SignInOutputParser.automaticLink(inCaptureFile: capture.readCaptured()) else {
            return automaticLink != nil
        }
        automaticLink = link
        log("[signIn] Automatic link captured: \(SignInOutputParser.redacted(link))")
        linkArrived()
        return true
    }

    private func linkArrived() {
        if state == .starting { state = .waitingForUser }
        linkTimer?.cancel()
        linkTimer = nil
    }

    // MARK: - The code from another device

    /// Hands a pasted `code#state` to Claude Code. False unless the session is
    /// waiting for the user, no code was handed over yet, and the code has the
    /// `code#state` shape (see `SignInCode`).
    @discardableResult
    func submitCode(_ code: String) -> Bool {
        guard state == .waitingForUser, stopReason == nil, !codeSubmitted,
              let line = SignInCode.normalized(code), let handle else { return false }
        guard handle.writeLine(line) else {
            log("[signIn] Could not hand the code to Claude Code")
            return false
        }
        codeSubmitted = true
        log("[signIn] Code handed to Claude Code")
        return true
    }

    // MARK: - Stopping

    /// Stops the sign-in: SIGTERM, then SIGKILL 2 seconds later if needed.
    /// Ignored once Claude Code has exited 0 (`completing`) or the session has
    /// finished, so closing the window never interrupts saving the account.
    func cancel() {
        guard state == .starting || state == .waitingForUser else { return }
        guard didStart else {
            // Cancelled during the backup: nothing was launched.
            log("[signIn] Cancelled before Claude Code started")
            finish(.cancelled)
            return
        }
        switch stopReason {
        case .cancel?, .timeout?:
            return
        case .fallback?:
            // The first CLI is already being stopped; end instead of falling back.
            stopReason = .cancel
        case nil:
            log("[signIn] Cancelled")
            stop(.cancel)
        }
    }

    private func linkWaitElapsed() {
        linkTimer = nil
        guard state == .starting, stopReason == nil, manualLink == nil, !captureAutomaticLink() else { return }
        log("[signIn] Neither link appeared within \(Int(Self.linkWait)) s; falling back to the default browser")
        stop(.fallback)
    }

    private func overallLimitReached() {
        overallTimer = nil
        guard state == .starting || state == .waitingForUser else { return }
        log("[signIn] Timed out after \(Int(Self.overallLimit / 60)) minutes")
        if stopReason == nil {
            stop(.timeout)
        } else if stopReason == .fallback {
            stopReason = .timeout
        }
    }

    private func stop(_ reason: StopReason) {
        guard let handle else { return }
        stopReason = reason
        handle.terminate()
        let current = generation
        killTimer = scheduler.after(Self.killAfter) { [weak self] in self?.forceKill(generation: current) }
        giveUpTimer = scheduler.after(Self.giveUpAfter) { [weak self] in
            self?.log("[signIn] No exit reported after stopping Claude Code; ending the sign-in anyway")
            self?.exited(status: -1, generation: current)
        }
    }

    private func forceKill(generation: Int) {
        killTimer = nil
        guard generation == self.generation, stopReason != nil else { return }
        log("[signIn] Claude Code did not stop after SIGTERM; sending SIGKILL")
        handle?.kill()
    }

    /// Today's behaviour: run `claude auth login` without the helper, so Claude
    /// Code opens the default browser itself. The session still finishes when
    /// the CLI exits.
    private func startFallback() {
        inFallback = true
        capture?.remove()
        capture = nil
        notice = String(localized: "Couldn't read the sign-in link; opened your default browser instead.", bundle: L10n.bundle)
        guard launchProcess(environment: launch.environment) else { return }
        state = .waitingForUser
    }

    // MARK: - Ending

    private func exited(status: Int32, generation: Int) {
        guard generation == self.generation, !state.isFinished else { return }
        killTimer?.cancel()
        killTimer = nil
        giveUpTimer?.cancel()
        giveUpTimer = nil
        handle?.closeInput()
        handle = nil
        let reason = stopReason
        stopReason = nil
        switch reason {
        case .fallback?:
            startFallback()
        case .cancel?:
            finish(.cancelled)
        case .timeout?:
            finish(.failed(message: String(localized: "Sign-in timed out after 15 minutes. Start it again when you're ready.", bundle: L10n.bundle)))
        case nil where status == 0:
            processSucceeded()
        case nil:
            log("[signIn] Claude Code exited with status \(status)")
            finish(.failed(message: String(localized: "Claude Code's sign-in stopped (exit status \(Int(status))).", bundle: L10n.bundle)))
        }
    }

    private func processSucceeded() {
        log("[signIn] Claude Code finished the sign-in; saving it")
        cancelTimers()
        capture?.remove()
        capture = nil
        state = .completing
        Task { @MainActor in
            let outcome = await self.complete(self)
            self.finish(outcome)
        }
    }

    private func finish(_ final: SignInState) {
        guard !state.isFinished else { return }
        cancelTimers()
        handle?.closeInput()
        handle = nil
        capture?.remove()
        capture = nil
        state = final
        log("[signIn] Finished: \(Self.summary(of: final))")
        let callback = onFinish
        onFinish = nil
        callback?(self)
    }

    private func cancelTimers() {
        for timer in [linkTimer, overallTimer, pollTimer, killTimer, giveUpTimer] { timer?.cancel() }
        linkTimer = nil
        overallTimer = nil
        pollTimer = nil
        killTimer = nil
        giveUpTimer = nil
    }

    /// For the log: the state's name, never a message's details.
    private static func summary(of state: SignInState) -> String {
        switch state {
        case .starting: return "starting"
        case .waitingForUser: return "waiting for the user"
        case .completing: return "completing"
        case .succeeded: return "succeeded"
        case .failed: return "failed"
        case .cancelled: return "cancelled"
        }
    }
}
