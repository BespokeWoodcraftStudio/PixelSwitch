import AppKit
import Combine
import WebKit

private let webLog = FileLog("ClaudeWeb")

/// One account's claude.ai window.
///
/// It shows https://claude.ai/settings/usage with that account's own persistent
/// web sign-in, so the page is always the right account's. The founder reads the
/// page and clicks claude.ai's own buttons himself. This window never clicks,
/// submits, posts or scripts anything on the page, and never calls a reset
/// address. Its only script reads text that is already on the page, to compare
/// the signed-in address with the account (`ClaudeWebAccountCheck`).
///
/// It does not set a user agent of its own: the page sees the web view as it is.
@MainActor
final class ClaudeWebWindowController: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, NSTextFieldDelegate {
    let accountID: String
    private(set) var email: String
    /// Called once, after the window has closed.
    var onClose: ((ClaudeWebWindowController) -> Void)?

    private let window: ClaudeWebWindow
    private var webView: WKWebView?
    private var popups: [ClaudeWebPopupController] = []
    private var observers = Set<AnyCancellable>()
    private var checkTask: Task<Void, Never>?
    private var isSigningOut = false
    private var isClosed = false
    /// False until the current web view has shown a page. A page that never loaded has nothing
    /// to reload, so Reload then loads the usage page again.
    private var hasCommittedPage = false
    /// True after the one automatic reload that follows the page process ending; cleared when a
    /// page finishes loading, so the next, separate crash gets its own reload.
    private var reloadedAfterProcessEnded = false

    // Chrome
    private let contentContainer = NSView()
    private let topStack = NSStackView()
    private let backButton = NSButton()
    private let reloadButton = NSButton()
    private let browserButton = NSButton()
    private let pasteButton = NSButton()
    private let signOutButton = NSButton()
    private let signInBanner = ClaudeWebBannerView(style: .info, symbol: "person.crop.circle")
    private let mismatchBanner = ClaudeWebBannerView(style: .warning, symbol: "exclamationmark.triangle.fill")
    private let loadFailureBanner = ClaudeWebBannerView(style: .warning, symbol: "wifi.exclamationmark")
    private let pasteBox = NSStackView()
    private let pasteField = NSTextField()
    private let pasteError = NSTextField(labelWithString: "")

    init(accountID: String, email: String) {
        self.accountID = accountID
        self.email = email
        self.window = ClaudeWebWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1000, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        super.init()
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 640, height: 480)
        window.delegate = self
        window.reloadAction = { [weak self] in self?.reload() }
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        buildChrome()
        refreshTitleAndBanners()
        installWebView()
        loadUsagePage()
    }

    // MARK: Showing

    /// Brings the window to the front and activates the app (PixelSwitch has no Dock icon).
    func show(cascadeIndex: Int = 0) {
        if !window.isVisible {
            window.center()
            if cascadeIndex > 0 {
                var origin = window.frame.origin
                origin.x += CGFloat(cascadeIndex) * 28
                origin.y -= CGFloat(cascadeIndex) * 28
                window.setFrameOrigin(origin)
            }
        }
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
        if let webView { window.makeFirstResponder(webView) }
    }

    /// Updates the address the window expects, for a window that is already open.
    func update(email: String) {
        guard email != self.email else { return }
        self.email = email
        refreshTitleAndBanners()
    }

    /// Closes the window and every pop-up it opened. The saved web sign-in stays.
    func closeWindow() {
        window.close()
    }

    var displayEmail: String {
        EmailDisplay.isMasked ? email.maskedAsEmailAddress() : email
    }

    private func refreshTitleAndBanners() {
        window.title = "\(displayEmail) · claude.ai"
        signInBanner.setText(String(format: String(localized: "Sign in as %@", bundle: L10n.bundle), displayEmail))
        pageChanged()
    }

    // MARK: Chrome

    private func configure(_ button: NSButton, title: String, symbol: String, action: Selector) {
        button.title = title
        button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: title)
        button.imagePosition = .imageLeading
        button.bezelStyle = .texturedRounded
        button.target = self
        button.action = action
        button.setAccessibilityLabel(title)
    }

    private func buildChrome() {
        configure(backButton, title: String(localized: "Back", bundle: L10n.bundle), symbol: "chevron.left", action: #selector(goBack))
        configure(reloadButton, title: String(localized: "Reload", bundle: L10n.bundle), symbol: "arrow.clockwise", action: #selector(reloadPressed))
        configure(browserButton, title: String(localized: "Open in browser", bundle: L10n.bundle), symbol: "arrow.up.right.square", action: #selector(openInBrowser))
        configure(pasteButton, title: String(localized: "Paste sign-in link", bundle: L10n.bundle), symbol: "link", action: #selector(togglePasteBox))
        configure(signOutButton, title: String(localized: "Sign out of claude.ai here", bundle: L10n.bundle), symbol: "rectangle.portrait.and.arrow.right", action: #selector(confirmSignOut))
        backButton.isEnabled = false

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let toolbar = NSStackView(views: [backButton, reloadButton, browserButton, pasteButton, spacer, signOutButton])
        toolbar.orientation = .horizontal
        toolbar.spacing = 8
        toolbar.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)

        // The paste row: a field that takes only an https claude.ai link.
        pasteField.placeholderString = String(localized: "Paste the sign-in link from your email", bundle: L10n.bundle)
        pasteField.delegate = self
        pasteField.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let loadButton = NSButton(title: String(localized: "Load", bundle: L10n.bundle), target: self, action: #selector(loadPastedLink))
        loadButton.bezelStyle = .texturedRounded
        loadButton.keyEquivalent = ""
        let cancelButton = NSButton(title: String(localized: "Cancel", bundle: L10n.bundle), target: self, action: #selector(hidePasteBox))
        cancelButton.bezelStyle = .texturedRounded
        let pasteRow = NSStackView(views: [pasteField, loadButton, cancelButton])
        pasteRow.orientation = .horizontal
        pasteRow.spacing = 8
        pasteError.textColor = .systemRed
        pasteError.font = .systemFont(ofSize: 12)
        pasteError.isHidden = true
        pasteBox.setViews([pasteRow, pasteError], in: .top)
        pasteBox.orientation = .vertical
        pasteBox.alignment = .leading
        pasteBox.spacing = 4
        pasteBox.edgeInsets = NSEdgeInsets(top: 0, left: 12, bottom: 8, right: 12)
        pasteRow.translatesAutoresizingMaskIntoConstraints = false
        pasteRow.widthAnchor.constraint(equalTo: pasteBox.widthAnchor, constant: -24).isActive = true
        pasteBox.isHidden = true

        signInBanner.isHidden = true
        mismatchBanner.isHidden = true
        loadFailureBanner.setText(String(localized: "Couldn't load claude.ai. Check your connection.", bundle: L10n.bundle))
        loadFailureBanner.setAction(title: String(localized: "Reload", bundle: L10n.bundle), target: self, action: #selector(reloadFromBanner))
        loadFailureBanner.isHidden = true

        topStack.setViews([toolbar, signInBanner, mismatchBanner, loadFailureBanner, pasteBox], in: .top)
        topStack.orientation = .vertical
        topStack.alignment = .leading
        topStack.spacing = 0
        topStack.translatesAutoresizingMaskIntoConstraints = false
        contentContainer.addSubview(topStack)
        NSLayoutConstraint.activate([
            topStack.topAnchor.constraint(equalTo: contentContainer.topAnchor),
            topStack.leadingAnchor.constraint(equalTo: contentContainer.leadingAnchor),
            topStack.trailingAnchor.constraint(equalTo: contentContainer.trailingAnchor),
        ])
        for view in [toolbar, signInBanner, mismatchBanner, loadFailureBanner, pasteBox] as [NSView] {
            view.translatesAutoresizingMaskIntoConstraints = false
            view.widthAnchor.constraint(equalTo: topStack.widthAnchor).isActive = true
        }
        window.contentView = contentContainer
    }

    // MARK: The web view

    private func installWebView() {
        hasCommittedPage = false
        reloadedAfterProcessEnded = false
        loadFailureBanner.isHidden = true
        let config = WKWebViewConfiguration()
        config.websiteDataStore = ClaudeWebSessionStore.dataStore(for: accountID)
        let view = WKWebView(frame: .zero, configuration: config)
        view.translatesAutoresizingMaskIntoConstraints = false
        view.navigationDelegate = self
        view.uiDelegate = self
        view.allowsBackForwardNavigationGestures = true
        contentContainer.addSubview(view)
        NSLayoutConstraint.activate([
            view.topAnchor.constraint(equalTo: topStack.bottomAnchor),
            view.leadingAnchor.constraint(equalTo: contentContainer.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: contentContainer.trailingAnchor),
            view.bottomAnchor.constraint(equalTo: contentContainer.bottomAnchor),
        ])
        webView = view
        observe(view)
    }

    private func observe(_ view: WKWebView) {
        observers.removeAll()
        view.publisher(for: \.url)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in MainActor.assumeIsolated { self?.pageChanged(rescan: true) } }
            .store(in: &observers)
        view.publisher(for: \.canGoBack)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in MainActor.assumeIsolated { self?.pageChanged() } }
            .store(in: &observers)
    }

    /// Drops the web view completely, so WebKit lets go of the account's data store.
    private func removeWebView() {
        checkTask?.cancel()
        checkTask = nil
        observers.removeAll()
        guard let view = webView else { return }
        view.stopLoading()
        view.navigationDelegate = nil
        view.uiDelegate = nil
        view.removeFromSuperview()
        webView = nil
    }

    private func loadUsagePage() {
        webView?.load(URLRequest(url: ClaudeWebPolicy.usageURL))
    }

    // MARK: Page state

    private func pageChanged(rescan: Bool = false) {
        backButton.isEnabled = webView?.canGoBack ?? false
        let url = webView?.url
        let onSignIn = ClaudeWebPolicy.isSignInPage(url)
        signInBanner.isHidden = !onSignIn
        if onSignIn { mismatchBanner.isHidden = true }
        if rescan { scheduleAccountCheck() }
    }

    /// Reads the loaded page a few times while it draws (it is a script-built page,
    /// so the address may appear a moment after the load finishes), and stops as
    /// soon as it can tell. Reading the page sends nothing.
    private func scheduleAccountCheck() {
        checkTask?.cancel()
        guard let url = webView?.url, !ClaudeWebPolicy.isSignInPage(url), isClaudePage(url) else {
            mismatchBanner.isHidden = true
            checkTask = nil
            return
        }
        checkTask = Task { @MainActor [weak self] in
            for delay in [0.8, 2.5, 6.0, 12.0] {
                try? await Task.sleep(for: .seconds(delay))
                guard !Task.isCancelled, let self else { return }
                if await self.readPageOnce() { return }
            }
        }
    }

    private func isClaudePage(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "https", let host = ClaudeWebPolicy.normalizedHost(url.host) else { return false }
        return host == "claude.ai" || host.hasSuffix(".claude.ai")
    }

    /// One read of the loaded page. True when the answer is settled (a match or a mismatch).
    private func readPageOnce() async -> Bool {
        guard let view = webView, let url = view.url, !ClaudeWebPolicy.isSignInPage(url), isClaudePage(url) else { return false }
        let result = try? await view.evaluateJavaScript(ClaudeWebAccountCheck.readScript, contentWorld: .defaultClient)
        guard let texts = result as? [String] else { return false }
        let verdict = ClaudeWebAccountCheck.verdict(expected: email, pageTexts: texts)
        applyAccountVerdict(verdict)
        return verdict != .unknown
    }

    /// Shows the "signed in as someone else" bar for a mismatch and nothing otherwise.
    func applyAccountVerdict(_ verdict: ClaudeWebAccountCheck.Verdict) {
        switch verdict {
        case .mismatch(let found):
            let shownFound = EmailDisplay.isMasked ? found.maskedAsEmailAddress() : found
            let template = String(localized: "This window is signed in as %1$@, not %2$@. Sign out here and sign in as %2$@.", bundle: L10n.bundle)
            mismatchBanner.setText(String(format: template, shownFound, displayEmail))
            mismatchBanner.isHidden = false
            webLog.warning("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): the page is signed in as a different address than expected")
        case .matches:
            mismatchBanner.isHidden = true
            webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): the page is signed in as the expected address")
        case .unknown:
            mismatchBanner.isHidden = true
        }
    }

    // MARK: Toolbar actions

    @objc private func goBack() {
        webView?.goBack()
    }

    @objc private func reloadPressed() { reload() }

    func reload() {
        guard let view = webView else { return }
        loadFailureBanner.isHidden = true
        // WebKit's reload does nothing for a page that never loaded, so start the usage page over.
        if view.url == nil || !hasCommittedPage { loadUsagePage() } else { view.reload() }
    }

    @objc private func reloadFromBanner() { reload() }

    @objc private func openInBrowser() {
        guard let url = webView?.url, let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http" else { return }
        let opened = NSWorkspace.shared.open(url)
        webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): asked the default browser to open \(ClaudeWebPolicy.loggable(url)): \(opened)")
    }

    @objc private func togglePasteBox() {
        if pasteBox.isHidden { showPasteBox() } else { hidePasteBox() }
    }

    private func showPasteBox() {
        pasteError.isHidden = true
        pasteBox.isHidden = false
        window.makeFirstResponder(pasteField)
    }

    @objc private func hidePasteBox() {
        pasteField.stringValue = ""
        pasteError.isHidden = true
        pasteBox.isHidden = true
        if let webView { window.makeFirstResponder(webView) }
    }

    @objc private func loadPastedLink() {
        switch ClaudeWebPolicy.validatePastedLink(pasteField.stringValue) {
        case .valid(let url):
            webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): loading a pasted sign-in link (\(ClaudeWebPolicy.loggable(url)))")
            webView?.load(URLRequest(url: url))
            hidePasteBox()
        case .invalid(let problem):
            switch problem {
            case .empty, .notALink:
                pasteError.stringValue = String(localized: "That is not one whole link. Paste the full link from your email.", bundle: L10n.bundle)
            case .notHTTPS, .notClaudeAI:
                pasteError.stringValue = String(localized: "Only a link on claude.ai loads here. Paste the sign-in link from your email.", bundle: L10n.bundle)
            }
            pasteError.isHidden = false
            webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): a pasted link was refused (\(String(describing: problem)))")
        }
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        if commandSelector == #selector(NSResponder.insertNewline(_:)) {
            loadPastedLink()
            return true
        }
        if commandSelector == #selector(NSResponder.cancelOperation(_:)) {
            hidePasteBox()
            return true
        }
        return false
    }

    // MARK: Sign out

    @objc private func confirmSignOut() {
        guard !isSigningOut else { return }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = String(localized: "Sign out of claude.ai here?", bundle: L10n.bundle)
        alert.informativeText = String(format: String(localized: "This removes the claude.ai sign-in that PixelSwitch keeps for %@. Your browser and your other accounts are not affected.", bundle: L10n.bundle), displayEmail)
        alert.addButton(withTitle: String(localized: "Sign out", bundle: L10n.bundle))
        alert.addButton(withTitle: String(localized: "Cancel", bundle: L10n.bundle))
        alert.beginSheetModal(for: window) { [weak self] response in
            guard response == .alertFirstButtonReturn else { return }
            MainActor.assumeIsolated { self?.beginSignOut() }
        }
    }

    private func beginSignOut() {
        guard !isSigningOut else { return }
        isSigningOut = true
        Task { @MainActor [weak self] in
            await self?.performSignOut()
        }
    }

    /// Removes this account's data store and reloads. The web view and every
    /// pop-up are released first, because WebKit will not remove a store that
    /// a web view still uses.
    private func performSignOut() async {
        webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): sign-out confirmed")
        closePopups()
        removeWebView()
        mismatchBanner.isHidden = true
        try? await Task.sleep(for: .milliseconds(150))
        let removed = await ClaudeWebSessionStore.removeSignIn(for: accountID)
        isSigningOut = false
        // The window may have been closed while the data store was being removed.
        guard !isClosed else { return }
        installWebView()
        loadUsagePage()
        if !removed {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = String(localized: "PixelSwitch could not remove this sign-in. Quit and reopen PixelSwitch, then try again.", bundle: L10n.bundle)
            alert.addButton(withTitle: String(localized: "Close", bundle: L10n.bundle))
            alert.beginSheetModal(for: window, completionHandler: nil)
        }
    }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        decisionHandler(applyPolicy(to: navigationAction, in: self))
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        hasCommittedPage = true
        loadFailureBanner.isHidden = true
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        reloadedAfterProcessEnded = false
        pageChanged(rescan: true)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loadFailed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        loadFailed(error)
    }

    /// The page process ended and the page is blank. Reload once; if it ends again before a page
    /// has loaded, show the banner instead of looping.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        let tag = ClaudeWebSessionStore.shortID(accountID)
        switch ClaudeWebLoadFailure.reaction(toProcessEnding: reloadedAfterProcessEnded) {
        case .reload:
            reloadedAfterProcessEnded = true
            webLog.warning("[claudeWeb] account \(tag): the page's process ended; reloading once")
            reload()
        case .showBanner:
            webLog.warning("[claudeWeb] account \(tag): the page's process ended again; showing the reload banner")
            loadFailureBanner.isHidden = false
        }
    }

    /// A load that did not finish. Loads stopped on purpose (a new load, a refused address) say
    /// nothing; any other failure shows the banner. The log gets the error's domain and code only,
    /// never an address, so no query string can reach it.
    private func loadFailed(_ error: Error) {
        let failure = error as NSError
        guard ClaudeWebLoadFailure.shouldShowBanner(domain: failure.domain, code: failure.code) else { return }
        webLog.warning("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): a page did not load (\(failure.domain) \(failure.code))")
        loadFailureBanner.isHidden = false
    }

    // MARK: WKUIDelegate

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        makePopup(configuration: configuration, navigationAction: navigationAction, windowFeatures: windowFeatures)
    }

    /// A page asked for a new window (Google sign-in uses one). The new web view
    /// must share this one's configuration, which is also what keeps it in the
    /// same account's data store. It opens in a small window on top of this one.
    func makePopup(configuration: WKWebViewConfiguration, navigationAction: WKNavigationAction,
                   windowFeatures: WKWindowFeatures) -> WKWebView? {
        let url = navigationAction.request.url
        // A pop-up opened with no address starts blank and is sent on later; that
        // later navigation is checked by the pop-up's own delegate.
        let decision = url == nil ? ClaudeWebPolicy.Decision.allow : ClaudeWebPolicy.decide(url: url, isMainFrame: true)
        switch decision {
        case .allow:
            let popup = ClaudeWebPopupController(configuration: configuration, features: windowFeatures, owner: self, parent: window)
            popups.append(popup)
            webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): pop-up opened for \(ClaudeWebPolicy.loggable(url))")
            popup.show()
            return popup.webView
        case .openInBrowser:
            openExternally(url)
            return nil
        case .block:
            webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): pop-up refused for \(ClaudeWebPolicy.loggable(url))")
            return nil
        }
    }

    func popupDidClose(_ popup: ClaudeWebPopupController) {
        popups.removeAll { $0 === popup }
        webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): pop-up closed")
    }

    private func closePopups() {
        for popup in popups { popup.close() }
        popups.removeAll()
    }

    func openExternally(_ url: URL?) {
        guard let url else { return }
        let opened = NSWorkspace.shared.open(url)
        webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): policy sent \(ClaudeWebPolicy.loggable(url)) to the default browser: \(opened)")
    }

    // MARK: NSWindowDelegate

    func windowWillClose(_ notification: Notification) {
        isClosed = true
        webLog.info("[claudeWeb] account \(ClaudeWebSessionStore.shortID(accountID)): window closed")
        closePopups()
        removeWebView()
        window.delegate = nil
        let finished = onClose
        onClose = nil
        // Let the close finish before the owner lets go of this controller.
        Task { @MainActor [self] in finished?(self) }
    }
}

/// The policy applied to one navigation, shared by the window and its pop-ups.
/// Returns what to tell WebKit; a link that belongs in the browser is sent there.
@MainActor
func applyPolicy(to navigationAction: WKNavigationAction, in owner: ClaudeWebWindowController) -> WKNavigationActionPolicy {
    let url = navigationAction.request.url
    let tag = ClaudeWebSessionStore.shortID(owner.accountID)
    // No target frame means "open in a new window": the window request that follows
    // (createWebViewWith) makes the real decision, so only a refusal is final here.
    guard let target = navigationAction.targetFrame else {
        return ClaudeWebPolicy.decide(url: url, isMainFrame: true) == .block ? .cancel : .allow
    }
    let decision = ClaudeWebPolicy.decide(url: url, isMainFrame: target.isMainFrame)
    switch decision {
    case .allow:
        if target.isMainFrame { webLog.info("[claudeWeb] account \(tag): allow \(ClaudeWebPolicy.loggable(url))") }
        return .allow
    case .openInBrowser:
        owner.openExternally(url)
        return .cancel
    case .block:
        webLog.info("[claudeWeb] account \(tag): blocked \(ClaudeWebPolicy.loggable(url))")
        return .cancel
    }
}

// MARK: - Window with the standard editing keys

/// PixelSwitch has no Dock icon and no menu bar of its own while a window is up,
/// so the usual Command keys may reach no menu. This window answers them itself.
final class ClaudeWebWindow: NSWindow {
    var reloadAction: (() -> Void)?

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if super.performKeyEquivalent(with: event) { return true }
        guard event.type == .keyDown,
              event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
              let key = event.charactersIgnoringModifiers?.lowercased() else { return false }
        switch key {
        case "v": return NSApp.sendAction(#selector(NSText.paste(_:)), to: nil, from: self)
        case "c": return NSApp.sendAction(#selector(NSText.copy(_:)), to: nil, from: self)
        case "x": return NSApp.sendAction(#selector(NSText.cut(_:)), to: nil, from: self)
        case "a": return NSApp.sendAction(#selector(NSText.selectAll(_:)), to: nil, from: self)
        case "w":
            performClose(nil)
            return true
        case "r":
            reloadAction?()
            return reloadAction != nil
        default: return false
        }
    }
}

// MARK: - A colored bar with one line of text

/// A full-width bar: an icon and a line of text on a tinted background.
final class ClaudeWebBannerView: NSView {
    enum Style { case info, warning }

    private let style: Style
    private let label = NSTextField(wrappingLabelWithString: "")
    private let actionButton = NSButton()

    init(style: Style, symbol: String) {
        self.style = style
        super.init(frame: .zero)
        wantsLayer = true
        let icon = NSImageView()
        icon.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        icon.contentTintColor = style == .warning ? .systemOrange : .controlAccentColor
        icon.setContentHuggingPriority(.required, for: .horizontal)
        label.font = .systemFont(ofSize: 13, weight: .medium)
        label.textColor = .labelColor
        label.maximumNumberOfLines = 3
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        actionButton.bezelStyle = .texturedRounded
        actionButton.setContentHuggingPriority(.required, for: .horizontal)
        actionButton.setContentCompressionResistancePriority(.required, for: .horizontal)
        actionButton.isHidden = true
        let row = NSStackView(views: [icon, label, actionButton])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        row.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
        row.translatesAutoresizingMaskIntoConstraints = false
        addSubview(row)
        NSLayoutConstraint.activate([
            row.topAnchor.constraint(equalTo: topAnchor),
            row.bottomAnchor.constraint(equalTo: bottomAnchor),
            row.leadingAnchor.constraint(equalTo: leadingAnchor),
            row.trailingAnchor.constraint(equalTo: trailingAnchor),
        ])
        setAccessibilityRole(.group)
    }

    required init?(coder: NSCoder) { nil }

    func setText(_ text: String) {
        label.stringValue = text
        setAccessibilityLabel(text)
    }

    /// Adds a button at the end of the bar (Reload on the load-failure bar).
    func setAction(title: String, target: AnyObject, action: Selector) {
        actionButton.title = title
        actionButton.target = target
        actionButton.action = action
        actionButton.setAccessibilityLabel(title)
        actionButton.isHidden = false
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        applyColors()
    }

    private func applyColors() {
        let color: NSColor = style == .warning
            ? NSColor.systemOrange.withAlphaComponent(0.22)
            : NSColor.controlAccentColor.withAlphaComponent(0.16)
        effectiveAppearance.performAsCurrentDrawingAppearance {
            layer?.backgroundColor = color.cgColor
        }
    }
}
