import Foundation

/// Claude Code's plaintext credential fallback, `~/.claude/.credentials.json`.
///
/// A running Claude Code session (2.1.x) keeps its OAuth token in memory and
/// decides whether to re-read the Keychain by `stat()`ing this file before
/// each API request:
/// - file present, modification date unchanged: nothing is invalidated, so the
///   session keeps the old account until its access token expires (hours);
/// - file present, modification date changed: the caches are cleared and the
///   Keychain is re-read on the next request;
/// - file absent: the Keychain is re-read (30 s cache).
/// Bumping the modification date after a switch is the CLI's own invalidation
/// signal, so running sessions follow a switch whether or not the file exists.
enum ClaudeCredentialsFile {
    enum TouchResult: Equatable {
        case bumped(path: String)
        case absent(path: String)
        case failed(path: String, reason: String)
    }

    /// `~/.claude/.credentials.json`. `CLAUDE_CONFIG_DIR` is not honored:
    /// PixelSwitch is a GUI app, it does not inherit shell exports, and
    /// nothing else in PixelSwitch reads that variable.
    static var defaultURL: URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".claude/.credentials.json")
    }

    /// Sets the file's modification date to `now` if the file exists.
    ///
    /// Never creates, reads, rewrites or deletes it. Absent is the best state,
    /// and on a Mac where a Keychain write once failed, this file is Claude
    /// Code's real credential store.
    static func touch(_ url: URL = defaultURL, now: Date = Date(), fileManager: FileManager = .default) -> TouchResult {
        let path = url.path
        guard fileManager.fileExists(atPath: path) else { return .absent(path: path) }
        do {
            try fileManager.setAttributes([.modificationDate: now], ofItemAtPath: path)
            return .bumped(path: path)
        } catch {
            return .failed(path: path, reason: error.localizedDescription)
        }
    }
}
