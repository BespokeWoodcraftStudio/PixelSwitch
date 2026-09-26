import AppKit

private let browserLog = FileLog("SignIn")

/// A browser installed on this Mac.
struct BrowserApp: Identifiable, Hashable, Sendable {
    /// The bundle identifier (the app's path if it has none).
    let id: String
    let name: String
    let appURL: URL
    let isDefault: Bool
}

/// Lists this Mac's browsers and opens a sign-in link in one of them. Only
/// ever called from a button the user pressed (or a CLI request), never on its own.
@MainActor
enum BrowserOpener {
    /// Every app that opens https links, the default browser first, then by name.
    static func installedBrowsers() -> [BrowserApp] {
        let probe = URL(string: "https://example.com")!
        let defaultURL = NSWorkspace.shared.urlForApplication(toOpen: probe)?.standardizedFileURL
        var seen = Set<String>()
        var browsers: [BrowserApp] = []
        for url in NSWorkspace.shared.urlsForApplications(toOpen: probe).map(\.standardizedFileURL) {
            let bundle = Bundle(url: url)
            let id = bundle?.bundleIdentifier ?? url.path
            guard seen.insert(id).inserted else { continue }
            let name = (bundle?.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String)
                ?? (bundle?.object(forInfoDictionaryKey: "CFBundleName") as? String)
                ?? url.deletingPathExtension().lastPathComponent
            browsers.append(BrowserApp(id: id, name: name, appURL: url, isDefault: url == defaultURL))
        }
        return browsers.sorted {
            if $0.isDefault != $1.isDefault { return $0.isDefault }
            return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    /// Opens `url` in `browser`, or in the default browser when nil.
    @discardableResult
    static func open(_ url: URL, in browser: BrowserApp?) -> Bool {
        guard let browser else {
            let opened = NSWorkspace.shared.open(url)
            browserLog.info("[browser] Default browser asked to open \(SignInOutputParser.redacted(url)): \(opened)")
            return opened
        }
        guard FileManager.default.fileExists(atPath: browser.appURL.path) else {
            browserLog.warning("[browser] \(browser.name) is no longer installed")
            return false
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        let name = browser.name
        NSWorkspace.shared.open([url], withApplicationAt: browser.appURL, configuration: configuration) { _, error in
            if let error {
                browserLog.error("[browser] \(name) could not open the sign-in link: \(error.localizedDescription)")
            }
        }
        browserLog.info("[browser] \(name) asked to open \(SignInOutputParser.redacted(url))")
        return true
    }
}
