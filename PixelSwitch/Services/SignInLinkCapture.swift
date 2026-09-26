import Foundation

/// A private, per-sign-in folder holding the `$BROWSER` helper and the file it
/// writes the automatic link to. Claude Code runs `$BROWSER <link>` instead of
/// `open <link>` when `BROWSER` is set, so pointing it at the helper records the
/// link and opens nothing. The folder is created fresh for each sign-in (0700,
/// helper 0700, capture file 0600) and removed when the sign-in ends.
///
/// Deliberately NOT a script inside the app bundle (as the design first said):
/// a temporary folder needs no bundle resource and no code-signing change, and
/// leaves nothing behind.
struct SignInLinkCapture: Sendable {
    /// The folder name prefix. One folder per sign-in, named by the session id.
    static let folderPrefix = "pixelswitch-signin-"
    /// The environment variable naming the capture file for the helper.
    static let captureFileVariable = "PIXELSWITCH_SIGNIN_CAPTURE_FILE"
    /// A leftover folder older than this (a crash mid-sign-in) is removed when
    /// the next sign-in starts. Longer than any sign-in can run.
    static let staleAfter: TimeInterval = 60 * 60

    let directory: URL
    let helperURL: URL
    let captureFileURL: URL

    /// The helper script. It records its one argument and exits 0 so Claude
    /// Code treats the browser as opened.
    static func helperScript() -> String {
        """
        #!/bin/sh
        # PixelSwitch sign-in link capture. Claude Code runs this as $BROWSER with
        # the sign-in link as its only argument; it records the link instead of
        # opening a browser.
        file="${\(captureFileVariable):-$(dirname "$0")/captured-links}"
        printf '%s\\n' "$1" >> "$file"
        exit 0

        """
    }

    /// Creates the folder, the helper and an empty capture file under `parent`.
    static func create(in parent: URL, id: UUID, now: Date = Date()) throws -> SignInLinkCapture {
        let fileManager = FileManager.default
        removeStale(in: parent, now: now)
        let directory = parent.appendingPathComponent(folderPrefix + id.uuidString, isDirectory: true)
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: false)
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)

        let helperURL = directory.appendingPathComponent("capture-sign-in-link")
        try Data(helperScript().utf8).write(to: helperURL)
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helperURL.path)

        let captureFileURL = directory.appendingPathComponent("captured-links")
        try Data().write(to: captureFileURL)
        try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: captureFileURL.path)

        return SignInLinkCapture(directory: directory, helperURL: helperURL, captureFileURL: captureFileURL)
    }

    /// Removes sign-in folders under `parent` last modified before `staleAfter` ago.
    static func removeStale(in parent: URL, now: Date = Date()) {
        let fileManager = FileManager.default
        guard let names = try? fileManager.contentsOfDirectory(atPath: parent.path) else { return }
        for name in names where name.hasPrefix(folderPrefix) {
            let path = parent.appendingPathComponent(name).path
            guard let modified = (try? fileManager.attributesOfItem(atPath: path))?[.modificationDate] as? Date,
                  now.timeIntervalSince(modified) > staleAfter else { continue }
            try? fileManager.removeItem(atPath: path)
        }
    }

    /// What Claude Code must run with so the link lands in the capture file.
    var environmentOverrides: [String: String] {
        ["BROWSER": helperURL.path, Self.captureFileVariable: captureFileURL.path]
    }

    /// Everything the helper has written so far ("" if nothing or unreadable).
    func readCaptured() -> String {
        (try? String(contentsOf: captureFileURL, encoding: .utf8)) ?? ""
    }

    /// Deletes the folder and everything in it.
    func remove() {
        try? FileManager.default.removeItem(at: directory)
    }
}
