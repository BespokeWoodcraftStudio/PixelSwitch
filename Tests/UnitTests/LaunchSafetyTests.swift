// Launch safety pins (2.3 crashed on every launch, 2026-11-20).
//
// 2.3 asked WebKit to delete the data stores the 2.2 claude.ai window left, at launch, while no
// web view existed in the process. WebKit crashed inside its own data store removal
// (EXC_BAD_ACCESS, KERN_INVALID_ADDRESS 0x48, queue com.apple.WebKit.WebsiteDataStoreIO), and
// because the saved list was only forgotten after success, every launch crashed before the menu
// bar item appeared. These pins keep that from coming back:
//   1. no shipped source imports or names a web framework type;
//   2. the cleanup forgets the saved list before it touches any folder, and a folder that cannot
//      be deleted never brings the list back;
//   3. it only ever deletes a real folder named for a saved identifier inside the stores folder;
//   4. it is started detached, after the menu bar item is installed.
//
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from main.swift.
// Uses the global `check(_:_:_:)` defined there.
import Foundation

@MainActor func runLaunchSafetyTests() {
    launchNoWebFrameworkPins()
    launchCleanupPlanTests()
    launchCleanupRunTests()
    launchCleanupWiringPins()
}

// MARK: - 1. No web framework anywhere

@MainActor private func launchNoWebFrameworkPins() {
    let sources = shippedSources()
    check(sources.count > 100, "launch safety: the shipped sources are found from the repo root (\(sources.count) files)")
    for needle in ["import WebKit", "WKWebsiteDataStore", "WKWebView"] {
        let hits = sources.filter { $0.text.contains(needle) }.map(\.path)
        check(hits.isEmpty, "launch safety: no shipped source contains \(needle)", hits.joined(separator: " | "))
    }
    check(!FileManager.default.fileExists(atPath: "PixelSwitch/Services/LegacyWebDataCleanupRunner.swift"),
          "launch safety: the old web framework cleanup runner is deleted")
    let project = (try? String(contentsOfFile: "project.yml", encoding: .utf8)) ?? ""
    check(!project.isEmpty && !project.contains("WebKit"), "launch safety: project.yml links no web framework")
}

// MARK: - 2. The plan

@MainActor private func launchCleanupPlanTests() {
    typealias W = LegacyWebDataCleanup
    let suite = "ai.pixelventures.pixelswitch.tests.launchsafety.plan.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "launch safety: a throwaway defaults suite could not be made"); return }
    defer { defaults.removePersistentDomain(forName: suite) }

    check(W.plan(defaults: defaults) == [], "launch safety plan: nothing saved means no steps at all")

    let a = UUID(), b = UUID()
    defaults.set(["acct-a": a.uuidString, "acct-b": b.uuidString, "acct-dup": a.uuidString], forKey: W.defaultsKey)
    let plan = W.plan(defaults: defaults)
    check(plan.first == .forgetSavedList, "launch safety plan: the first step is forgetting the saved list, before any folder", "\(plan)")
    check(plan.filter { $0 == .forgetSavedList }.count == 1 && plan.count == 3
          && Set(plan.dropFirst()) == [.removeStoreFolder(a), .removeStoreFolder(b)],
          "launch safety plan: then exactly one folder removal per distinct saved identifier", "\(plan)")

    defaults.set("not a dictionary", forKey: W.defaultsKey)
    check(W.plan(defaults: defaults) == [.forgetSavedList], "launch safety plan: a saved value of the wrong type is still forgotten, with no folder removals")
    defaults.set(["x": "not-a-uuid", "y": "00000000-0000-0000-0000-000000000000"], forKey: W.defaultsKey)
    check(W.plan(defaults: defaults) == [.forgetSavedList], "launch safety plan: a list with no usable identifier is forgotten, with no folder removals")

    let library = URL(fileURLWithPath: "/Users/someone/Library", isDirectory: true)
    check(W.storesFolder(library: library, bundleIdentifier: "ai.pixelventures.pixelswitch").path
          == "/Users/someone/Library/WebKit/ai.pixelventures.pixelswitch/WebsiteDataStore",
          "launch safety plan: the stores folder is Library/WebKit/<bundle id>/WebsiteDataStore")
}

// MARK: - 3. Running it against real folders

/// A file manager that reports every folder removal, and can refuse them all.
private final class ProbeFileManager: FileManager, @unchecked Sendable {
    var onRemove: ((URL) -> Void)?
    var refuse = false
    private(set) var attempts = 0
    override func removeItem(at url: URL) throws {
        attempts += 1
        onRemove?(url)
        if refuse { throw CocoaError(.fileWriteNoPermission) }
        try super.removeItem(at: url)
    }
}

@MainActor private func launchCleanupRunTests() {
    typealias W = LegacyWebDataCleanup
    let fm = FileManager.default
    let suite = "ai.pixelventures.pixelswitch.tests.launchsafety.run.\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else { check(false, "launch safety run: a throwaway defaults suite could not be made"); return }
    let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true).appendingPathComponent("pixelswitch-launchsafety-\(UUID().uuidString)", isDirectory: true)
    defer {
        defaults.removePersistentDomain(forName: suite)
        try? fm.removeItem(at: root)
    }
    let stores = W.storesFolder(library: root, bundleIdentifier: "ai.pixelventures.pixelswitch")
    let siblingData = stores.deletingLastPathComponent().appendingPathComponent("WebsiteData", isDirectory: true)

    /// A store folder with a file inside, named the way WebKit names it (lower case) or in upper case.
    func makeStore(_ id: UUID, upper: Bool = false) -> URL {
        let folder = stores.appendingPathComponent(upper ? id.uuidString : id.uuidString.lowercased(), isDirectory: true)
        try? fm.createDirectory(at: folder.appendingPathComponent("Cookies", isDirectory: true), withIntermediateDirectories: true)
        fm.createFile(atPath: folder.appendingPathComponent("Cookies/Cookies.binarycookies").path, contents: Data("x".utf8))
        return folder
    }
    func exists(_ url: URL) -> Bool { fm.fileExists(atPath: url.path) }
    func reset() {
        try? fm.removeItem(at: root)
        try? fm.createDirectory(at: stores, withIntermediateDirectories: true)
        try? fm.createDirectory(at: siblingData, withIntermediateDirectories: true)
        fm.createFile(atPath: siblingData.appendingPathComponent("salt").path, contents: Data("x".utf8))
        defaults.removeObject(forKey: W.defaultsKey)
    }

    // A. The normal case: two saved stores (one named in lower case, one in upper), one saved store with no folder,
    //    one unrelated folder. The saved list is already gone when each folder is removed.
    reset()
    let a = UUID(), b = UUID(), absent = UUID(), unrelated = UUID()
    let folderA = makeStore(a), folderB = makeStore(b, upper: true), folderUnrelated = makeStore(unrelated)
    defaults.set(["acct-a": a.uuidString, "acct-b": b.uuidString, "acct-gone": absent.uuidString], forKey: W.defaultsKey)
    let probe = ProbeFileManager()
    var keyStillSavedAtARemoval = false
    probe.onRemove = { _ in if defaults.object(forKey: W.defaultsKey) != nil { keyStillSavedAtARemoval = true } }
    let outcome = W.run(defaults: defaults, storesFolder: stores, fileManager: probe)
    check(outcome == W.Outcome(forgotSavedList: true, foldersRemoved: 2, foldersAbsent: 1, foldersLeft: 0), "launch safety run: two folders removed, one already gone, none left", "\(outcome)")
    check(probe.attempts == 2 && !keyStillSavedAtARemoval, "launch safety run: the saved list was already forgotten at every folder removal", "attempts \(probe.attempts)")
    check(!exists(folderA) && !exists(folderB), "launch safety run: the saved stores' folders are gone, whatever their letter case")
    check(exists(folderUnrelated) && exists(siblingData.appendingPathComponent("salt")) && exists(stores),
          "launch safety run: a folder that is not a saved identifier, the app's own WebsiteData folder and the stores folder are untouched")
    check(defaults.object(forKey: W.defaultsKey) == nil && !W.hasWork(defaults: defaults), "launch safety run: the saved list is gone")
    let again = W.run(defaults: defaults, storesFolder: stores, fileManager: probe)
    check(again == W.Outcome() && probe.attempts == 2 && exists(folderUnrelated), "launch safety run: a second run does nothing")

    // B. A folder that cannot be removed: no error escapes, the list is still gone, the next launch has no work.
    reset()
    let stuckA = makeStore(a), stuckB = makeStore(b)
    defaults.set(["acct-a": a.uuidString, "acct-b": b.uuidString], forKey: W.defaultsKey)
    let refusing = ProbeFileManager()
    refusing.refuse = true
    let refused = W.run(defaults: defaults, storesFolder: stores, fileManager: refusing)
    check(refused == W.Outcome(forgotSavedList: true, foldersRemoved: 0, foldersAbsent: 0, foldersLeft: 2), "launch safety run: two folders that could not be removed are counted, not thrown", "\(refused)")
    check(defaults.object(forKey: W.defaultsKey) == nil && !W.hasWork(defaults: defaults) && W.plan(defaults: defaults).isEmpty,
          "launch safety run: after a failed removal the saved list is still gone, so the next launch repeats nothing")
    check(exists(stuckA) && exists(stuckB), "launch safety run: a folder that could not be removed is left as it was")

    // C. No stores folder known (no bundle id or no Library): the list is still forgotten and nothing is touched.
    reset()
    let kept = makeStore(a)
    defaults.set(["acct-a": a.uuidString], forKey: W.defaultsKey)
    let unknown = W.run(defaults: defaults, storesFolder: nil)
    check(unknown == W.Outcome(forgotSavedList: true, foldersRemoved: 0, foldersAbsent: 0, foldersLeft: 1) && exists(kept) && !W.hasWork(defaults: defaults),
          "launch safety run: with no stores folder the list is forgotten and no folder is touched", "\(unknown)")

    // D. A stores folder that does not exist: every folder counts as already gone.
    reset()
    try? fm.removeItem(at: stores.deletingLastPathComponent())
    defaults.set(["acct-a": a.uuidString], forKey: W.defaultsKey)
    let missing = W.run(defaults: defaults, storesFolder: stores)
    check(missing == W.Outcome(forgotSavedList: true, foldersRemoved: 0, foldersAbsent: 1, foldersLeft: 0) && !W.hasWork(defaults: defaults),
          "launch safety run: with no stores folder on disk the list is forgotten and nothing fails", "\(missing)")

    // E. A link or a plain file in a folder's place is left alone, and a link's target is never reached.
    reset()
    let victim = root.appendingPathComponent("victim", isDirectory: true)
    try? fm.createDirectory(at: victim, withIntermediateDirectories: true)
    fm.createFile(atPath: victim.appendingPathComponent("keep.txt").path, contents: Data("x".utf8))
    let link = stores.appendingPathComponent(a.uuidString.lowercased())
    try? fm.createSymbolicLink(at: link, withDestinationURL: victim)
    let plain = stores.appendingPathComponent(b.uuidString.lowercased())
    fm.createFile(atPath: plain.path, contents: Data("x".utf8))
    defaults.set(["acct-a": a.uuidString, "acct-b": b.uuidString], forKey: W.defaultsKey)
    let guarded = W.run(defaults: defaults, storesFolder: stores)
    check(guarded == W.Outcome(forgotSavedList: true, foldersRemoved: 0, foldersAbsent: 0, foldersLeft: 2), "launch safety run: a link and a plain file are counted as left", "\(guarded)")
    check(exists(victim.appendingPathComponent("keep.txt")) && exists(plain) && (try? fm.destinationOfSymbolicLink(atPath: link.path)) != nil,
          "launch safety run: a link's target, the link itself and a plain file are all untouched")

    // F. Nothing saved: a folder named like an identifier is never touched.
    reset()
    let unsaved = makeStore(a)
    let idle = W.run(defaults: defaults, storesFolder: stores)
    check(idle == W.Outcome() && exists(unsaved), "launch safety run: with no saved list no folder is ever removed")

    // G. The launch entry point logs one line when it ran and none when it had nothing to do.
    var lines: [String] = []
    W.runAtLaunch(defaults: defaults) { lines.append($0) }
    check(lines.isEmpty, "launch safety run: with nothing saved the launch entry point logs nothing")
    defaults.set(["acct-a": UUID().uuidString], forKey: W.defaultsKey)
    W.runAtLaunch(defaults: defaults) { lines.append($0) }
    check(lines.count == 1 && lines[0].hasPrefix("[webCleanup]") && !W.hasWork(defaults: defaults) && !lines[0].contains("/"),
          "launch safety run: with a saved list the launch entry point forgets it and logs one line with no path", lines.joined(separator: " | "))
}

// MARK: - 4. How the app starts it

@MainActor private func launchCleanupWiringPins() {
    let app = (try? String(contentsOfFile: "PixelSwitch/PixelSwitchApp.swift", encoding: .utf8)) ?? ""
    let cleanup = (try? String(contentsOfFile: "PixelSwitch/Services/LegacyWebDataCleanup.swift", encoding: .utf8)) ?? ""
    check(!app.isEmpty && !cleanup.isEmpty, "launch safety wiring: the sources are found from the repo root")

    let call = app.range(of: "LegacyWebDataCleanup.runAtLaunch")
    let install = app.range(of: "statusItemController.install(")
    check(call != nil && install != nil && install!.lowerBound < call!.lowerBound,
          "launch safety wiring: the cleanup starts after the menu bar item is installed")
    // The nearest task opener before the call is a detached one, and nothing between them hops back to the main actor.
    let beforeCall = call.map { String(app[app.startIndex..<$0.lowerBound]) } ?? ""
    let opener = beforeCall.range(of: "Task", options: .backwards).map { String(beforeCall[$0.lowerBound...]) } ?? ""
    check(opener.hasPrefix("Task.detached(priority: .utility)") && !opener.contains("MainActor") && !opener.contains("await"),
          "launch safety wiring: the cleanup runs in a detached, low priority task, off the main thread", opener)
    check(app.components(separatedBy: "LegacyWebDataCleanup.").count == 2, "launch safety wiring: the app's launch makes exactly one call into the cleanup")

    // Inside the run loop the saved list's removal is written before the folder removal.
    let forget = cleanup.range(of: "case .forgetSavedList:\n                defaults.removeObject(forKey: defaultsKey)")
    let folders = cleanup.range(of: "case .removeStoreFolder(let id):")
    check(forget != nil && folders != nil && forget!.lowerBound < folders!.lowerBound,
          "launch safety wiring: the run loop forgets the saved list before it removes a folder")
    check(cleanup.components(separatedBy: "try fileManager.removeItem(at: folder)").count == 2 && !cleanup.contains("try!") && !cleanup.contains("fatalError")
          && !cleanup.contains("exit(") && !cleanup.contains("as!") && !cleanup.contains("]!"),
          "launch safety wiring: one folder removal, wrapped in a catch, and nothing in the cleanup can trap")
}
