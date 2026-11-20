// The repo-wide "no reset" pins.
//
// The founder, 2026-11-19: "you absolutly do not execute a reset". PixelSwitch never
// clicks, submits or scripts anything on claude.ai and never calls a reset address; it only
// opens a page in Chrome (PixelSwitch/ChromeProfiles). These pins read the whole shipped
// product, so a reset address, a spoofed browser identity or a script call cannot arrive
// from anywhere: the app, the command line tool, the widget or the Linux port.
//
// Moved here from ClaudeWebTests.swift when 2.3 removed the in-app claude.ai window
// (founder, 2026-11-20, option A). One thing changed: the product used to have exactly one
// script call (the window's read-only account check); it now has none.
//
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from main.swift.
// Uses the global `check(_:_:_:)` defined there.
import Foundation

@MainActor func runNoResetPinTests() {
    noResetRepoWidePins()
}

/// Every source file the shipped product is built from: the app, the command line tool, the shared
/// and widget code and the Linux port, with test files left out. Run from the repo root.
func shippedSources() -> [(path: String, text: String)] {
    let roots = ["PixelSwitch", "PixelSwitchCLI", "Shared", "PixelSwitchWidget", "linux"]
    let extensions: Set<String> = ["swift", "py", "sh", "m", "mm", "h", "js", "cjs", "mjs", "ts"]
    let skippedFolders: Set<String> = ["tests", "Tests", "__pycache__", ".build", "node_modules", ".git"]
    let fileManager = FileManager.default
    var found: [(path: String, text: String)] = []
    for root in roots {
        guard let walker = fileManager.enumerator(atPath: root) else { continue }
        for case let relative as String in walker {
            let parts = relative.split(separator: "/").map(String.init)
            guard let name = parts.last, !parts.dropLast().contains(where: { skippedFolders.contains($0) }) else { continue }
            if name.hasSuffix("Tests.swift") || name.hasSuffix("Test.swift") || name.hasPrefix("test_")
                || name.hasSuffix("_test.py") || name == "conftest.py" { continue }
            let path = "\(root)/\(relative)"
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: path, isDirectory: &isDirectory), !isDirectory.boolValue,
                  let text = try? String(contentsOfFile: path, encoding: .utf8) else { continue }
            let ext = (name as NSString).pathExtension
            // A script with no extension (linux/bin/pixelswitch) counts when it starts with a shebang.
            if ext.isEmpty ? !text.hasPrefix("#!") : !extensions.contains(ext) { continue }
            found.append((path, text))
        }
    }
    return found.sorted { $0.path < $1.path }
}

@MainActor private func noResetRepoWidePins() {
    let sources = shippedSources()
    func count(_ suffix: String) -> Int { sources.filter { $0.path.hasSuffix(suffix) }.count }
    let swiftApp = sources.filter { $0.path.hasPrefix("PixelSwitch/") && $0.path.hasSuffix(".swift") }.count
    let swiftCLI = sources.filter { $0.path.hasPrefix("PixelSwitchCLI/") && $0.path.hasSuffix(".swift") }.count
    let python = sources.filter { $0.path.hasPrefix("linux/") && $0.path.hasSuffix(".py") }.count
    // A wrong working folder or a renamed root must fail loudly here, never pass over an empty list.
    check(swiftApp >= 80 && swiftCLI >= 4 && count("Shared/WidgetData.swift") == 1 && count("PixelSwitchWidget/PixelSwitchWidget.swift") == 1
          && python >= 40 && count("linux/pixelswitch/api.py") == 1 && count("PixelSwitch/ChromeProfiles/ChromeUsagePage.swift") == 1,
          "no-reset pins: the shipped sources of the app, CLI, shared code, widget and Linux port are all found (app \(swiftApp), CLI \(swiftCLI), Shared, widget, Linux \(python))")
    check(!sources.contains { source in ["/tests/", "/Tests/", "Tests.swift", "/test_"].contains { source.path.contains($0) } },
          "no-reset pins: test files are left out of the scan")

    // 1. No reset address or reset grant anywhere. These names are what claude.ai's own page uses for its reset.
    let resetNames = ["reset_rate_limits", "cedar_ember", "juniper_tide", "grant_id", "limit-reset", "limit_reset"]
    let resetHits = sources.flatMap { source in
        resetNames.filter { source.text.range(of: $0, options: .caseInsensitive) != nil }.map { "\(source.path): \($0)" }
    }
    check(resetHits.isEmpty, "no-reset pins: no reset endpoint or grant name appears in any shipped source", resetHits.joined(separator: " | "))

    // 2. No user agent of our own for a web view, and no header that sets one, except the Linux client's
    //    honest name for its own HTTP calls (no browser, no web view).
    let webViewAgent = sources.flatMap { source in
        ["customUserAgent", "applicationNameForUserAgent"].filter { source.text.contains($0) }.map { "\(source.path): \($0)" }
    }
    check(webViewAgent.isEmpty, "no-reset pins: nothing sets a web view's user agent", webViewAgent.joined(separator: " | "))
    let allowedHeaderFile = "linux/pixelswitch/api.py"
    let headerHits = sources.filter { $0.path != allowedHeaderFile && $0.text.range(of: "user-agent", options: .caseInsensitive) != nil }.map(\.path)
    check(headerHits.isEmpty, "no-reset pins: no source writes a User-Agent header, outside the one Linux client file", headerHits.joined(separator: " | "))
    let linuxClient = sources.first { $0.path == allowedHeaderFile }?.text ?? ""
    let clientWrites = linuxClient.components(separatedBy: "\n").filter { $0.contains("\"User-Agent\"") }
    let linuxDaemon = sources.first { $0.path == "linux/pixelswitch/daemon.py" }?.text ?? ""
    check(clientWrites.count == 2 && clientWrites.allSatisfy { $0.contains("self.user_agent") }
          && linuxDaemon.contains("USER_AGENT_FORMAT = \"PixelSwitch/{version} (Linux)\""),
          "no-reset pins: the Linux client's only User-Agent writes send its own name, PixelSwitch/<version> (Linux)", "\(clientWrites.count) write(s)")
    let browserAgent = sources.filter { $0.text.contains("Mozilla/") }.map(\.path)
    check(browserAgent.isEmpty, "no-reset pins: no source carries a browser's user-agent text", browserAgent.joined(separator: " | "))

    // 3. No script call anywhere in the product. 2.2's window ran one read-only script to check which
    //    address it was signed in as; with that window gone, a script call is a new thing, and a new
    //    thing near claude.ai is never added quietly.
    let scriptCalls = sources.compactMap { source -> (String, Int)? in
        let n = source.text.components(separatedBy: "evaluateJavaScript(").count - 1
        return n > 0 ? (source.path, n) : nil
    }
    check(scriptCalls.isEmpty,
          "no-reset pins: the whole product has zero evaluateJavaScript calls",
          scriptCalls.map { "\($0.0) x\($0.1)" }.joined(separator: " | "))
}
