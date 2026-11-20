import Foundation

/// One web sign-in removal at a time per account.
///
/// "Sign out of claude.ai here" (in the window) and account removal both delete the
/// same account's web data store. If the two ran together, the one that lost the
/// race would fail to remove a store that is already gone, and its fallback, which
/// opens the store by its identifier to wipe it, would create that store afresh:
/// an orphan, with no account mapped to it and nothing left to ever remove it.
///
/// So a second request for an account that already has a removal running does not
/// start another. It waits for the one that is running and takes its answer.
///
/// Foundation only, so the unit harness compiles it. Main actor only, like the
/// store and the windows that call it.
@MainActor
final class ClaudeWebRemovalGate {
    private var running: [String: Task<Bool, Never>] = [:]

    init() {}

    /// True while a removal for this account is in flight.
    func isRunning(accountID: String) -> Bool {
        running[ClaudeWebStoreMap.normalized(accountID)] != nil
    }

    /// Runs `work`, unless a removal for this account is already running. Then it
    /// waits for that one and returns its result, and `work` is never called.
    /// Two spellings of one account id count as one account.
    func run(accountID: String, work: @escaping @MainActor () async -> Bool) async -> Bool {
        let key = ClaudeWebStoreMap.normalized(accountID)
        if let inFlight = running[key] {
            return await inFlight.value
        }
        // The task clears its own entry when the work is done, so no caller that
        // joined it can find a finished removal still listed as running.
        let task = Task { @MainActor [weak self] () -> Bool in
            let result = await work()
            self?.running[key] = nil
            return result
        }
        running[key] = task
        return await task.value
    }
}

/// A `UserDefaults` that may be captured by the closure a removal runs. The class is
/// documented as safe to use from any thread, but the SDK does not mark it
/// `Sendable`, and the removal's closure must be.
struct SendableDefaults: @unchecked Sendable {
    let value: UserDefaults

    init(_ value: UserDefaults) { self.value = value }
}
