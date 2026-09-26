import Foundation

/// When an update that Sparkle downloaded by itself may be installed.
/// Installing relaunches PixelSwitch, so it waits while an account switch, a
/// login or a sign-in is running: a relaunch then would cut a keychain write
/// or a sign-in in half. Pure, so the unit harness tests it.
enum AutoUpdatePolicy {
    /// How often to look again while PixelSwitch is busy, in seconds.
    static let retryInterval: TimeInterval = 60

    static func mayInstallNow(isSwitching: Bool, isLoggingIn: Bool, signIn: SignInState?) -> Bool {
        if isSwitching || isLoggingIn { return false }
        if let signIn, !signIn.isFinished { return false }
        return true
    }
}
