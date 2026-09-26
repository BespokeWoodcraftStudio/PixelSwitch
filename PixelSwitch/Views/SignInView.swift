import SwiftUI
import AppKit

/// The sign-in window: the automatic link for this Mac, the manual link and
/// code field for another device, a status line and Cancel. Nothing opens by
/// itself; every browser action is a button the user presses.
struct SignInView: View {
    @ObservedObject var session: SignInSession
    /// Close for a finished sign-in (the window controller decides what that means).
    let onClose: () -> Void

    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @State private var browsers: [BrowserApp] = []
    @State private var showOtherDevice = false
    @State private var code = ""
    @State private var codeRejected = false
    @State private var copied: CopiedLink?

    private enum CopiedLink { case automatic, manual }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            thisMacSection
            otherDeviceSection
            Divider()
            footer
        }
        .padding(20)
        .frame(width: 460)
        .onAppear { browsers = BrowserOpener.installedBrowsers() }
    }

    private var isWaiting: Bool { session.state == .waitingForUser }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            switch session.purpose {
            case .newAccount:
                Text("Sign in a new account")
                    .font(.headline)
            case .reauthenticate(_, let email):
                Text("Re-authenticate \(displayed(email))")
                    .font(.headline)
                Text("Sign in as \(displayed(email)).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func displayed(_ email: String) -> String {
        maskEmails ? email.maskedAsEmailAddress() : email
    }

    // MARK: This Mac

    private var thisMacSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Sign in on this Mac")
                .font(.subheadline.weight(.semibold))
            if let link = session.automaticLink {
                HStack(spacing: 8) {
                    Button("Open in default browser") {
                        BrowserOpener.open(link, in: nil)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.brand)

                    Menu("Open in") {
                        if browsers.isEmpty {
                            Text("No browsers found")
                        }
                        ForEach(browsers) { browser in
                            Button(browser.name) {
                                BrowserOpener.open(link, in: browser)
                            }
                        }
                    }
                    .fixedSize()

                    Button {
                        copy(link, as: .automatic)
                    } label: {
                        copied == .automatic ? Text("Copied") : Text("Copy link")
                    }
                }
                .disabled(!isWaiting)
                Text("Paste it into any browser on this Mac. It finishes by itself.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if let notice = session.notice {
                Text(verbatim: notice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if !session.state.isFinished {
                Text("Waiting for the link…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Another device

    private var otherDeviceSection: some View {
        DisclosureGroup(isExpanded: $showOtherDevice) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Open the link on the other device and sign in. The page then shows a code: paste it here.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button {
                    if let link = session.manualLink { copy(link, as: .manual) }
                } label: {
                    copied == .manual ? Text("Copied") : Text("Copy link")
                }
                .disabled(session.manualLink == nil || !isWaiting)
                HStack(spacing: 8) {
                    TextField("Paste the code the page shows", text: $code)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(submit)
                    Button("Submit", action: submit)
                        .disabled(code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !isWaiting || session.codeSubmitted)
                }
                if codeRejected {
                    Text("That isn't the whole code. Copy all of it, including the part after #.")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                Text("Also works on this Mac if the page doesn't finish by itself.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding(.top, 6)
        } label: {
            Text("Signing in on another device?")
                .font(.subheadline.weight(.semibold))
        }
    }

    private func submit() {
        guard SignInCode.normalized(code) != nil else {
            codeRejected = true
            return
        }
        codeRejected = false
        if session.submitCode(code) { code = "" }
    }

    private func copy(_ url: URL, as which: CopiedLink) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url.absoluteString, forType: .string)
        copied = which
    }

    // MARK: Status and buttons

    private var footer: some View {
        HStack(alignment: .center, spacing: 8) {
            statusIcon
            statusText
                .font(.caption)
                .foregroundStyle(statusIsError ? Color.red : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
            if session.state.isFinished {
                Button("Close", action: onClose)
                    .keyboardShortcut(.defaultAction)
            } else {
                Button("Cancel") { session.cancel() }
                    .keyboardShortcut(.cancelAction)
                    .disabled(session.state == .completing)
            }
        }
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch session.state {
        case .starting, .waitingForUser, .completing:
            ProgressView().controlSize(.small)
        case .succeeded:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed:
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        case .cancelled:
            Image(systemName: "xmark.circle").foregroundStyle(.secondary)
        }
    }

    private var statusText: Text {
        switch session.state {
        case .starting: return Text("Getting the sign-in link…")
        case .waitingForUser: return session.codeSubmitted ? Text("Checking the code…") : Text("Waiting for you to sign in in a browser…")
        case .completing: return Text("Saving the account…")
        case .succeeded: return Text("Signed in.")
        case .failed(let message): return Text(verbatim: message)
        case .cancelled: return Text("Cancelled. Nothing was changed.")
        }
    }

    private var statusIsError: Bool {
        if case .failed = session.state { return true }
        return false
    }
}
