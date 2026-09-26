import AppKit
import Combine
import SwiftUI

/// Shows the sign-in window whenever `AppState.currentSignIn` becomes a new
/// session, whoever started it (the popover, Settings, or later the CLI).
///
/// It is its own floating panel, not part of the popover: the popover closes
/// the moment the user switches to a browser, and the sign-in must survive that.
/// The panel closes by itself when the sign-in succeeds or is cancelled; a
/// failed sign-in stays open with its reason until the user closes it.
/// Closing the panel while the sign-in runs cancels it, so no hidden process
/// is left holding the sign-in lock.
@MainActor
final class SignInWindowController: NSObject, NSWindowDelegate {
    private var panel: NSPanel?
    private weak var appState: AppState?
    private var locale: Locale = .autoupdatingCurrent
    private var observers = Set<AnyCancellable>()
    private var stateObserver: AnyCancellable?
    private var shownSessionId: UUID?
    private var installed = false

    func install(appState: AppState, locale: Locale) {
        guard !installed else { return }
        installed = true
        self.appState = appState
        self.locale = locale
        appState.$currentSignIn
            .receive(on: DispatchQueue.main)
            .sink { [weak self] session in
                MainActor.assumeIsolated { self?.sessionChanged(session) }
            }
            .store(in: &observers)
        NotificationCenter.default.publisher(for: .pixelswitchShowSignIn)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                MainActor.assumeIsolated { self?.bringToFront() }
            }
            .store(in: &observers)
    }

    /// Follows the in-app language setting, like `StatusItemController.updateLocale`.
    func updateLocale(_ locale: Locale) {
        self.locale = locale
        if panel?.isVisible == true, let session = appState?.currentSignIn {
            show(session)
        }
    }

    private func sessionChanged(_ session: SignInSession?) {
        guard let session else {
            shownSessionId = nil
            stateObserver = nil
            hidePanel()
            return
        }
        guard session.id != shownSessionId else { return }
        shownSessionId = session.id
        stateObserver = session.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] state in
                MainActor.assumeIsolated { self?.stateChanged(state) }
            }
        show(session)
    }

    private func stateChanged(_ state: SignInState) {
        switch state {
        case .succeeded, .cancelled:
            hidePanel()
        case .failed:
            bringToFront()
        case .starting, .waitingForUser, .completing:
            break
        }
    }

    private func bringToFront() {
        guard let session = appState?.currentSignIn else { return }
        show(session)
    }

    private func show(_ session: SignInSession) {
        let root = SignInView(session: session, onClose: { [weak self] in self?.closeRequested() })
            .environment(\.locale, locale)
        let hosting = NSHostingController(rootView: AnyView(root))
        hosting.sizingOptions = [.preferredContentSize]
        let panel = self.panel ?? makePanel()
        panel.contentViewController = hosting
        panel.title = String(localized: "Sign in to Claude", bundle: L10n.bundle)
        self.panel = panel
        NSApp.activate()
        panel.makeKeyAndOrderFront(nil)
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 380),
            styleMask: [.titled, .closable, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        panel.delegate = self
        panel.center()
        return panel
    }

    private func hidePanel() {
        panel?.orderOut(nil)
        panel?.contentViewController = nil
    }

    // MARK: NSWindowDelegate

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        closeRequested()
        return false
    }

    /// The window's close button, or Close on a finished sign-in.
    private func closeRequested() {
        guard let appState, let session = appState.currentSignIn else {
            hidePanel()
            return
        }
        switch session.state {
        case .succeeded, .failed, .cancelled:
            appState.dismissSignIn()
            hidePanel()
        case .completing:
            // The account is being saved; that cannot be interrupted. Hide the
            // window; it comes back by itself if saving fails.
            panel?.orderOut(nil)
        case .starting, .waitingForUser:
            session.cancel()
        }
    }
}
