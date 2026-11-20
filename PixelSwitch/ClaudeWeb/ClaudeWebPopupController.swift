import AppKit
import WebKit

private let popupLog = FileLog("ClaudeWeb")

/// The small window for a page's `window.open` (Google sign-in uses one).
///
/// Its web view is created from the configuration WebKit hands over, so it shares
/// the opener's account data store and the page can talk back to its opener. It
/// follows the same navigation policy as the main window and closes when the page
/// closes it (`webViewDidClose`) or when its own close button is pressed.
@MainActor
final class ClaudeWebPopupController: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let webView: WKWebView
    private let window: NSWindow
    private weak var owner: ClaudeWebWindowController?
    private weak var parent: NSWindow?
    private var isClosed = false

    init(configuration: WKWebViewConfiguration, features: WKWindowFeatures, owner: ClaudeWebWindowController, parent: NSWindow) {
        self.owner = owner
        self.parent = parent
        self.webView = WKWebView(frame: .zero, configuration: configuration)
        let width = min(max(CGFloat(features.width?.doubleValue ?? 480), 360), 900)
        let height = min(max(CGFloat(features.height?.doubleValue ?? 640), 420), 900)
        self.window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered,
            defer: false
        )
        super.init()
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = webView
        window.title = "claude.ai"
        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    func show() {
        if let parent {
            let frame = parent.frame
            window.setFrameOrigin(NSPoint(x: frame.midX - window.frame.width / 2, y: frame.midY - window.frame.height / 2))
            parent.addChildWindow(window, ordered: .above)
        } else {
            window.center()
        }
        window.makeKeyAndOrderFront(nil)
    }

    /// Closes the window. Safe to call twice.
    func close() {
        guard !isClosed else { return }
        isClosed = true
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
        parent?.removeChildWindow(window)
        window.delegate = nil
        window.orderOut(nil)
        window.contentView = nil
        owner?.popupDidClose(self)
    }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let owner else {
            decisionHandler(.cancel)
            return
        }
        let policy = applyPolicy(to: navigationAction, in: owner)
        decisionHandler(policy)
        // A pop-up that never got anywhere (its first address went to the browser or was refused) has nothing to show.
        if policy == .cancel, navigationAction.targetFrame?.isMainFrame == true, webView.url == nil {
            Task { @MainActor [weak self] in self?.close() }
        }
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        if let host = ClaudeWebPolicy.normalizedHost(webView.url?.host) { window.title = host }
    }

    // MARK: WKUIDelegate

    /// The page closed its own pop-up (`window.close()`), as Google's sign-in does when it is done.
    func webViewDidClose(_ webView: WKWebView) {
        popupLog.info("[claudeWeb] pop-up closed by the page")
        close()
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        owner?.makePopup(configuration: configuration, navigationAction: navigationAction, windowFeatures: windowFeatures)
    }

    // MARK: NSWindowDelegate

    func windowWillClose(_ notification: Notification) {
        Task { @MainActor [weak self] in self?.close() }
    }
}
