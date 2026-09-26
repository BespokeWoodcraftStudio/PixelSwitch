import Foundation

// Automatic updates: an update Sparkle downloaded by itself is installed by
// relaunching PixelSwitch, and never while a switch or a sign-in is running.

@MainActor func runAutoUpdateTests() {
    func may(switching: Bool = false, loggingIn: Bool = false, signIn: SignInState? = nil) -> Bool {
        AutoUpdatePolicy.mayInstallNow(isSwitching: switching, isLoggingIn: loggingIn, signIn: signIn)
    }
    check(may(), "auto-update: an idle PixelSwitch installs a downloaded update at once")
    check(!may(switching: true), "auto-update: it waits while an account switch is running")
    check(!may(loggingIn: true), "auto-update: it waits while a login is running")
    check(!may(signIn: .starting) && !may(signIn: .waitingForUser) && !may(signIn: .completing),
          "auto-update: it waits while a sign-in is starting, waiting for the browser, or saving")
    check(may(signIn: .succeeded(accountId: UUID())) && may(signIn: .failed(message: "x")) && may(signIn: .cancelled),
          "auto-update: a finished sign-in does not hold it back")
    check(AutoUpdatePolicy.retryInterval == 60, "auto-update: while busy it looks again every minute")
}
