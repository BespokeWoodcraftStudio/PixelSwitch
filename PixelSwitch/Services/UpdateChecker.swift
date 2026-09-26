import Foundation
import AppKit
import Sparkle
import SwiftUI

/// Thin ObservableObject wrapper around Sparkle's `SPUStandardUpdaterController`.
/// Keeps the existing call-site API (`checkForUpdates(manual:)`,
/// `@Published var isChecking`) so `PixelSwitchApp.swift` and `SettingsView.swift`
/// don't need to change. Sparkle owns its own progress UI (download sheet,
/// release-notes window, restart prompt), so `isChecking` is kept for source
/// compatibility but is never flipped — clicking the button always shows
/// Sparkle's UI immediately, which provides its own feedback.
///
/// "Update automatically" (Settings → About, or `updates.automatic` from the
/// command line): Sparkle checks by itself (every 6 hours, SUScheduledCheckInterval)
/// and downloads in the background. Sparkle would then install only when the
/// app quits, and a menu bar app hardly ever quits, so the delegate takes over
/// and relaunches into the new version at once, unless a switch or sign-in is
/// running (`AutoUpdatePolicy`), in which case it waits and looks again.
@MainActor
final class UpdateChecker: NSObject, ObservableObject {
    /// Source-compat shim. Sparkle's UI is responsible for visible progress.
    @Published var isChecking = false
    /// Whether updates are downloaded and installed without asking. Sparkle
    /// keeps the value in user defaults (`SUAutomaticallyUpdate`).
    @Published private(set) var installsAutomatically = false
    /// Whether PixelSwitch is in the middle of something a relaunch would cut
    /// short. Set at launch, once `AppState` exists.
    var isBusy: @MainActor () -> Bool = { false }

    private var controller: SPUStandardUpdaterController!
    private var retryTimer: Timer?
    /// Sparkle's "install and relaunch now" block, held while PixelSwitch is busy.
    private var pendingInstall: (() -> Void)?

    override init() {
        super.init()
        // `startingUpdater: true` enables Sparkle's automatic background checks.
        controller = SPUStandardUpdaterController(
            startingUpdater: true,
            updaterDelegate: self,
            userDriverDelegate: nil
        )
        installsAutomatically = controller.updater.automaticallyDownloadsUpdates
    }

    /// Manual = user-initiated; shows Sparkle's full UI including
    /// "you're up to date" feedback when no update is available.
    /// Background = silent unless an update is found.
    func checkForUpdates(manual: Bool = false) {
        if manual {
            controller.checkForUpdates(nil)
        } else {
            controller.updater.checkForUpdatesInBackground()
        }
    }

    /// Turns automatic updates on (checking by itself as well) or off
    /// (Sparkle still checks and asks before installing, as before).
    func setInstallsAutomatically(_ on: Bool) {
        let updater = controller.updater
        if on { updater.automaticallyChecksForUpdates = true }
        updater.automaticallyDownloadsUpdates = on
        installsAutomatically = updater.automaticallyDownloadsUpdates
        if !on {
            retryTimer?.invalidate()
            retryTimer = nil
            pendingInstall = nil
        }
        if on { updater.checkForUpdatesInBackground() }
    }

    /// Installs a downloaded update now, or once PixelSwitch is no longer busy.
    fileprivate func installWhenIdle(_ install: @escaping () -> Void) {
        pendingInstall = install
        installPendingIfIdle()
    }

    private func installPendingIfIdle() {
        retryTimer?.invalidate()
        retryTimer = nil
        guard installsAutomatically, let install = pendingInstall else { return }
        if !isBusy() {
            pendingInstall = nil
            install()
            return
        }
        retryTimer = Timer.scheduledTimer(withTimeInterval: AutoUpdatePolicy.retryInterval, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.installPendingIfIdle() }
        }
    }
}

extension UpdateChecker: SPUUpdaterDelegate {
    /// Sparkle downloaded an update by itself and would install it at quit.
    /// Returning true takes over: `immediateInstallHandler` installs and relaunches.
    func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem,
                 immediateInstallationBlock immediateInstallHandler: @escaping () -> Void) -> Bool {
        guard installsAutomatically else { return false }
        installWhenIdle(immediateInstallHandler)
        return true
    }
}
