import Foundation

/// The environment every `claude` subprocess runs with. A GUI app inherits
/// launchd's bare PATH, so the usual install locations are put in front, plus
/// the resolved binary's own folder, so an NVM-installed `claude` finds `node`.
enum ClaudeProcessEnvironment {
    static func make(claudePath: String, base: [String: String], homeDirectory: String) -> [String: String] {
        var environment = base
        var extraPaths = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "\(homeDirectory)/.local/bin",
            "\(homeDirectory)/.npm-global/bin"
        ]
        // Only for an absolute path; the bare "claude" fallback has no folder.
        // Symlinks are resolved so /usr/local/bin/claude -> ~/.nvm/.../bin/claude
        // yields the NVM bin folder where `node` actually lives.
        if claudePath.contains("/") {
            let resolved = URL(fileURLWithPath: claudePath).resolvingSymlinksInPath().path
            extraPaths.insert(URL(fileURLWithPath: resolved).deletingLastPathComponent().path, at: 0)
        }
        let existingPath = environment["PATH"] ?? "/usr/bin:/bin"
        environment["PATH"] = (extraPaths + [existingPath]).joined(separator: ":")
        environment["HOME"] = homeDirectory
        return environment
    }
}
