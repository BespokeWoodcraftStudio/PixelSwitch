import SwiftUI
import AppKit
import SystemConfiguration

/// Settings → Claude CLI → "Command line & AI": installs the `pixelswitch`
/// tool, says whether remote control is listening, and gives the MCP snippet
/// for Claude on another Mac.
struct RemoteControlSection: View {
    @ObservedObject private var service = RemoteControlService.shared
    @State private var status = CommandLineToolInstaller.status()
    @State private var installError: String?
    @State private var copied = false

    var body: some View {
        Section("Command line & AI") {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Command-line tool")
                    Text(statusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button(status == .installed ? "Reinstall" : "Install") { install() }
                    .disabled(status == .installed)
            }
            if let installError {
                Label(installError, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            HStack(spacing: 6) {
                Image(systemName: isListening ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(isListening ? .green : .red)
                Text(listeningText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("To let Claude on another Mac control PixelSwitch, add this to its MCP settings. That Mac must be able to reach this one with SSH.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(verbatim: snippet)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
                HStack {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(snippet, forType: .string)
                        copied = true
                    } label: {
                        copied ? Text("Copied") : Text("Copy")
                    }
                    Spacer()
                    Text(verbatim: CommandLineToolInstaller.sshExample(host: host))
                        .font(.caption2.monospaced())
                        .foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
        }
        .onAppear { status = CommandLineToolInstaller.status() }
    }

    private var isListening: Bool {
        if case .listening = service.state { return true }
        return false
    }

    private var statusText: String {
        let path = CommandLineToolInstaller.defaultLinkPath.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        switch status {
        case .installed: return String(localized: "Installed at \(path).", bundle: L10n.bundle)
        case .notInstalled: return String(localized: "Not installed. Install puts `pixelswitch` at \(path).", bundle: L10n.bundle)
        case .linkedToOtherCopy: return String(localized: "\(path) points to another copy of PixelSwitch. Install points it here.", bundle: L10n.bundle)
        case .occupied: return String(localized: "Something else is already at \(path), so it was left alone.", bundle: L10n.bundle)
        }
    }

    private var listeningText: String {
        switch service.state {
        case .listening: return String(localized: "Remote control is on. Only this Mac's user can connect; nothing is open to the network.", bundle: L10n.bundle)
        case .stopped: return String(localized: "Remote control is not running.", bundle: L10n.bundle)
        case .failed(let why): return String(localized: "Remote control could not start: \(why)", bundle: L10n.bundle)
        }
    }

    /// This Mac's Bonjour name, as other Macs on the network reach it.
    private var host: String {
        if let name = SCDynamicStoreCopyLocalHostName(nil) as String? { return name + ".local" }
        return ProcessInfo.processInfo.hostName
    }

    private var snippet: String {
        CommandLineToolInstaller.mcpConfiguration(host: host)
    }

    private func install() {
        do {
            status = try CommandLineToolInstaller.install()
            installError = nil
        } catch let error as CommandLineToolInstaller.InstallError {
            installError = error.description
            status = CommandLineToolInstaller.status()
        } catch {
            installError = error.localizedDescription
        }
    }
}
