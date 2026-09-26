import Foundation

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
