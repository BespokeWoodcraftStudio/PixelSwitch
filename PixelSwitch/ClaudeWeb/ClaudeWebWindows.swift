import AppKit

private let registryLog = FileLog("ClaudeWeb")

/// The API the Resets button calls.
///
/// `open(accountID:email:)` opens that account's own claude.ai usage page in its
/// own PixelSwitch window. Each account keeps its own separate, persistent
/// claude.ai sign-in, so the window always shows the right account. The founder
/// clicks claude.ai's own "Reset for free" button himself: PixelSwitch never
/// clicks, submits or scripts anything on that page and never calls a reset
/// address.
@MainActor
final class ClaudeWebWindows {
    static let shared = ClaudeWebWindows()

    private var controllers: [String: ClaudeWebWindowController] = [:]

    private init() {}

    /// Opens the account's claude.ai usage page, or brings its window to the front
    /// if it is already open (one window per account). `accountID` is the account's
    /// id (`account.id.uuidString`); `email` is its address, used for the window
    /// title, the "Sign in as" bar and the signed-in check.
    func open(accountID: String, email: String) {
        let key = ClaudeWebStoreMap.normalized(accountID)
        guard !key.isEmpty else {
            registryLog.warning("[claudeWeb] open ignored: no account id")
            return
        }
        if let existing = controllers[key] {
            existing.update(email: email)
            existing.show()
            registryLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): window brought to the front")
            return
        }
        let controller = ClaudeWebWindowController(accountID: accountID, email: email)
        controller.onClose = { [weak self] closed in
            guard let self, self.controllers[key] === closed else { return }
            self.controllers[key] = nil
        }
        let index = controllers.count
        controllers[key] = controller
        registryLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): window opened")
        controller.show(cascadeIndex: index)
    }

    /// True while this account's window is open.
    func isOpen(accountID: String) -> Bool {
        controllers[ClaudeWebStoreMap.normalized(accountID)] != nil
    }

    /// Removes the account's claude.ai web sign-in (for an account that is being
    /// removed from PixelSwitch): closes its window, then deletes its data store.
    /// Returns false only if the data could not be removed.
    @discardableResult
    func removeWebSignIn(accountID: String) async -> Bool {
        let key = ClaudeWebStoreMap.normalized(accountID)
        if let controller = controllers[key] {
            controller.closeWindow()
            controllers[key] = nil
            // Give WebKit a moment to let go of the closed window's web view.
            try? await Task.sleep(for: .milliseconds(250))
        }
        return await ClaudeWebSessionStore.removeSignIn(for: accountID)
    }
}
