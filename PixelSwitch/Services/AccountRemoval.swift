import Foundation

/// The pure rules behind removing an account and moving Claude Code off it
/// (spec D20) and behind the "already live" shortcut of a switch. `AppState`
/// applies them; the unit harness runs them without the app.
enum AccountRemoval {

    /// What removing an account changes, decided from the accounts that remain.
    struct Plan: Equatable {
        /// No account is active afterwards (the list is empty).
        let clearActive: Bool
        /// Remember that the user removed their last account, so a refresh does
        /// not recreate it from the login Claude Code still holds.
        let markRemovedLast: Bool
        /// The accounts to try switching to, in order, when the removed account
        /// was the active one. Empty when nothing needs switching.
        let fallbackIds: [UUID]
    }

    /// Removing the last account always clears the active account and marks the
    /// removal, whether or not that account was marked active (D20; a failed
    /// fallback can leave no account marked active, so the last one removed
    /// later is not active). Otherwise only removing the ACTIVE account starts a
    /// fallback switch.
    static func plan(removedWasActive: Bool, remaining: [Account]) -> Plan {
        if remaining.isEmpty { return Plan(clearActive: true, markRemovedLast: true, fallbackIds: []) }
        if removedWasActive {
            return Plan(clearActive: false, markRemovedLast: false, fallbackIds: AccountOrder.fallbackOrder(from: remaining).map(\.id))
        }
        return Plan(clearActive: false, markRemovedLast: false, fallbackIds: [])
    }

    /// Whether a refresh may create the first account from the login Claude Code
    /// holds: only while the list is empty and the user has not removed their
    /// last account on purpose.
    static func mayCreateFromLiveLogin(accountsAreEmpty: Bool, removedLastAccount: Bool) -> Bool {
        accountsAreEmpty && !removedLastAccount
    }
}

/// The fallback loop of a removal: try the candidates in order and stop as soon
/// as the situation it was started for no longer holds. Pure over closures, so
/// the harness can drive it with fakes.
enum RemovalFallback {

    /// What one try of one candidate came to.
    enum Attempt: Equatable {
        /// The candidate is now live and active.
        case switched
        /// Another switch or a sign-in is running; nothing was tried.
        case busy(message: String)
        /// The switch was tried (or refused before writing) and did not happen.
        /// `message` is the sentence the user would be shown, when there is one.
        case failed(message: String?)
        /// The candidate is no longer on the list.
        case gone
    }

    /// How the loop ended.
    enum Result: Equatable {
        case switched(UUID)
        /// Something else chose an account (or emptied the list) while the loop
        /// ran, so it stopped and changed nothing.
        case stoodDown
        /// A switch or sign-in kept running for the whole wait. Nothing was tried;
        /// the active account is left as it is.
        case busyGaveUp(message: String)
        /// Every candidate was tried or gone and none took over.
        case noneWorked(lastFailure: String?)
    }

    /// How many one-second waits a busy switch or sign-in is given in all. A
    /// sign-in can take minutes in the browser, and nothing is lost by waiting.
    static let defaultMaxBusyWaits = 150

    /// Runs the loop.
    /// - `stillNeeded`: checked before every candidate and after every wait. False
    ///   as soon as anything else made an account active or emptied the list.
    /// - `attempt`: tries to switch to one candidate.
    /// - `pause`: waits about a second.
    /// - `note`: the log, one line per decision, with the reason for every failure.
    @MainActor
    static func run(
        candidates: [UUID],
        maxBusyWaits: Int = defaultMaxBusyWaits,
        stillNeeded: @MainActor () -> Bool,
        attempt: @MainActor (UUID) async -> Attempt,
        pause: @MainActor () async -> Void,
        note: @MainActor (String) -> Void
    ) async -> Result {
        var lastFailure: String?
        var busyWaits = 0
        for id in candidates {
            tries: while true {
                guard stillNeeded() else {
                    note("Another account became active (or none are left); stopping the fallback")
                    return .stoodDown
                }
                switch await attempt(id) {
                case .switched:
                    return .switched(id)
                case .gone:
                    note("Candidate \(id) is no longer on the list; skipping it")
                    break tries
                case .failed(let message):
                    if let message { lastFailure = message }
                    note("Switch to \(id) failed (\(message ?? "no reason given")); trying the next account")
                    break tries
                case .busy(let message):
                    busyWaits += 1
                    if busyWaits > maxBusyWaits {
                        note("A switch or sign-in is still running after \(maxBusyWaits) s; giving up without trying anything or clearing the active account")
                        return .busyGaveUp(message: message)
                    }
                    await pause()
                }
            }
        }
        guard stillNeeded() else {
            note("Another account became active (or none are left); nothing more to do")
            return .stoodDown
        }
        return .noneWorked(lastFailure: lastFailure)
    }
}

/// The "already live" shortcut of `AppState.performSwitch`.
enum SwitchShortcut {
    /// Whether the target's login is already the live one: the live login is
    /// proven to be the target's, or it is unproven and the target is the
    /// account believed active. With no active account and no proven owner
    /// nothing is "already live", so the switch goes ahead.
    static func alreadyLive(liveOwnerId: UUID?, believedActiveId: UUID?, targetId: UUID) -> Bool {
        if let liveOwnerId { return liveOwnerId == targetId }
        return believedActiveId == targetId
    }
}
