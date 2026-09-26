import Foundation

/// Links `~/.local/bin/pixelswitch` to the tool inside PixelSwitch.app
/// (`Contents/Helpers/pixelswitch`). A link, not a copy, so every Sparkle
/// update keeps it current. No admin password: `~/.local/bin` is the user's.
///
/// Never overwrites a file that is not PixelSwitch's own: a regular file, or
/// a link to something other than a PixelSwitch tool, is left alone.
enum CommandLineToolInstaller {
    enum Status: Equatable, Sendable {
        case notInstalled
        case installed
        /// A link to another copy of PixelSwitch's tool (an app that moved).
        case linkedToOtherCopy(String)
        /// Something that is not PixelSwitch's is already at that path.
        case occupied
    }

    enum InstallError: Error, Equatable, CustomStringConvertible {
        case toolMissing(String)
        case occupied(String)

        var description: String {
            switch self {
            case .toolMissing(let path): return "The command-line tool is missing from the app (\(path)). Reinstall PixelSwitch."
            case .occupied(let path): return "Something else is already at \(path). Move it away, then install again."
            }
        }
    }

    static var defaultLinkPath: String { NSHomeDirectory() + "/.local/bin/pixelswitch" }

    static func bundledToolPath(appBundle: URL = Bundle.main.bundleURL) -> String {
        appBundle.appendingPathComponent("Contents/Helpers/pixelswitch").path
    }

    static func status(linkPath: String = defaultLinkPath, toolPath: String = bundledToolPath()) -> Status {
        let files = FileManager.default
        if let destination = try? files.destinationOfSymbolicLink(atPath: linkPath) {
            if destination == toolPath { return .installed }
            return isPixelSwitchTool(destination) ? .linkedToOtherCopy(destination) : .occupied
        }
        return files.fileExists(atPath: linkPath) ? .occupied : .notInstalled
    }

    /// Creates (or repoints) the link. Returns the resulting status.
    @discardableResult
    static func install(linkPath: String = defaultLinkPath, toolPath: String = bundledToolPath()) throws -> Status {
        let files = FileManager.default
        guard files.isExecutableFile(atPath: toolPath) else { throw InstallError.toolMissing(toolPath) }
        switch status(linkPath: linkPath, toolPath: toolPath) {
        case .installed:
            return .installed
        case .occupied:
            throw InstallError.occupied(linkPath)
        case .linkedToOtherCopy:
            try files.removeItem(atPath: linkPath)
        case .notInstalled:
            break
        }
        try files.createDirectory(atPath: (linkPath as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        try files.createSymbolicLink(atPath: linkPath, withDestinationPath: toolPath)
        return status(linkPath: linkPath, toolPath: toolPath)
    }

    static func isPixelSwitchTool(_ path: String) -> Bool {
        path.hasSuffix(".app/Contents/Helpers/pixelswitch")
    }

    /// What to paste into Claude's MCP configuration on the other Mac. The
    /// path is absolute because a non-interactive SSH shell may not have
    /// `~/.local/bin` on its PATH.
    static func mcpConfiguration(host: String, linkPath: String = defaultLinkPath) -> String {
        let config: JSONValue = .object(["mcpServers": .object(["pixelswitch": .object([
            "command": .string("ssh"),
            "args": .array([.string(host), .string(linkPath), .string("mcp")])
        ])])])
        return config.prettyText
    }

    /// A one-line check to run on the other Mac.
    static func sshExample(host: String, linkPath: String = defaultLinkPath) -> String {
        "ssh \(host) \(linkPath) status"
    }
}
