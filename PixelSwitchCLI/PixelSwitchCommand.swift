import Foundation

/// `pixelswitch`, the command-line tool inside PixelSwitch.app
/// (`Contents/Helpers/pixelswitch`). Settings → Claude CLI → Install links it
/// into `~/.local/bin`. Not compiled into the unit harness (it has `@main`);
/// everything it calls is.
@main
struct PixelSwitchCommand {
    /// The version of the PixelSwitch.app this tool ships in, read from the
    /// app's Info.plist beside `Contents/Helpers/` (the link in ~/.local/bin
    /// is followed first). "unknown" when run outside the app.
    static var version: String {
        guard let executable = Bundle.main.executableURL?.resolvingSymlinksInPath() else { return "unknown" }
        let plist = executable.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Info.plist")
        let info = NSDictionary(contentsOf: plist)
        return info?["CFBundleShortVersionString"] as? String ?? "unknown"
    }

    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        let invocation: CLIInvocation
        do {
            invocation = try CLIParser.parse(arguments)
        } catch let error as CLIUsageError {
            FileHandle.standardError.write(Data(("error: " + error.message + "\n").utf8))
            exit(CLIExit.usage)
        } catch {
            exit(CLIExit.usage)
        }
        if invocation.command == .mcp {
            exit(MCPStdio.run(serverVersion: version))
        }
        exit(CLIRunner(client: ControlClient()).run(invocation))
    }
}
