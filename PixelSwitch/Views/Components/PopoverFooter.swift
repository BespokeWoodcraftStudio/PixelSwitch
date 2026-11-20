import SwiftUI

// The popover's footer and the two bars that can sit above it. Plain inputs and closures
// only (no `AppState`), so the screenshot harness (`Tools/screenshots/make_screenshots.swift`)
// draws the very views the popover shows and nothing can drift; `MainMenuView` supplies the
// state and the actions and measures each as chrome.

/// The footer: five icons, left to right: Add current account, Sign in a new account | Refresh,
/// Settings, Quit. Each is a 28 pt target with a tooltip that says what it is for and a spoken
/// label (`FooterIconButton`). The two add icons are off while a switch or a sign-in runs
/// (`busyReason` is non-nil, and is their tooltip then: the control API refuses both in that
/// state). A message from the app (an error) takes the room on the left.
struct PopoverFooter: View {
    var errorMessage: String?
    /// Why the two add icons are off, or nil while they work.
    var busyReason: String?
    let onAddCurrent: () -> Void
    let onSignInNew: () -> Void
    let onRefresh: () -> Void
    let onSettings: () -> Void
    let onQuit: () -> Void

    var body: some View {
        HStack(spacing: 2) {
            if let errorMessage {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.limitWarn)
                    .font(.caption)
                    .padding(.leading, 6)
                Text(errorMessage)
                    .font(.caption2)
                    .foregroundStyle(.textSecondary)
                    .lineLimit(1)
                    // The icons leave less room than the old caption glyphs did: the whole message is on hover.
                    .help(errorMessage)
            }

            Spacer(minLength: 8)

            FooterIconButton(
                symbol: "plus.circle",
                help: String(localized: "Add current account: save the one Claude Code is signed in to now", bundle: L10n.bundle),
                label: String(localized: "Add Current Account", bundle: L10n.bundle),
                disabledHelp: busyReason,
                action: onAddCurrent
            )

            FooterIconButton(
                symbol: "person.badge.plus",
                help: String(localized: "Sign in a new account: open the sign-in window", bundle: L10n.bundle),
                label: String(localized: "Sign In New Account", bundle: L10n.bundle),
                disabledHelp: busyReason,
                action: onSignInNew
            )

            // The two ways to add an account, then the three about the app itself.
            Divider()
                .frame(height: 16)
                .padding(.horizontal, 4)

            FooterIconButton(
                symbol: "arrow.clockwise",
                help: String(localized: "Refresh", bundle: L10n.bundle),
                label: String(localized: "Refresh", bundle: L10n.bundle),
                action: onRefresh
            )

            FooterIconButton(
                symbol: "gear",
                help: String(localized: "Settings", bundle: L10n.bundle),
                label: String(localized: "Settings", bundle: L10n.bundle),
                action: onSettings
            )

            FooterIconButton(
                symbol: "power",
                help: String(localized: "Quit", bundle: L10n.bundle),
                label: String(localized: "Quit", bundle: L10n.bundle),
                action: onQuit
            )
        }
        // The icons carry their own 28 pt of room, so the footer's edge padding is smaller than it was.
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
    }
}

/// What a footer icon looks like: a 15 pt symbol in the muted text tone inside a 28 x 28 pt
/// square, the whole of which is the target (`contentShape`). Under the pointer it gets a soft
/// highlight and the full text colour; disabled it is dimmed. A plain view with no button in it,
/// so the screenshot harness can draw it (`ImageRenderer` cannot draw a `Button`).
struct FooterIconLabel: View {
    let symbol: String
    var hovering = false
    var isDisabled = false

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 15))
            .frame(width: 28, height: 28)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(hovering && !isDisabled ? Color.primary.opacity(0.10) : Color.clear)
            )
            .foregroundStyle(hovering && !isDisabled ? Color.textPrimary : Color.textSecondary)
            .opacity(isDisabled ? 0.4 : 1)
            .contentShape(Rectangle())
    }
}

/// One icon in the popover's footer: `FooterIconLabel` in a plain button, with a tooltip that
/// says what it is for and a spoken label for VoiceOver. With `disabledHelp` set the icon is off,
/// dimmed, and the tooltip says why instead. The tooltip is on a wrapper around the button, not
/// the button: macOS may not show a tooltip on a disabled control.
struct FooterIconButton: View {
    let symbol: String
    /// The tooltip: what the icon is for.
    let help: String
    /// What VoiceOver reads.
    let label: String
    /// Non-nil turns the icon off and is its tooltip then.
    var disabledHelp: String?
    let action: () -> Void

    @State private var hovering = false

    private var isDisabled: Bool { disabledHelp != nil }

    var body: some View {
        HStack(spacing: 0) {
            Button(action: action) {
                FooterIconLabel(symbol: symbol, hovering: hovering, isDisabled: isDisabled)
            }
            .buttonStyle(.plain)
            .focusable(false)
            .disabled(isDisabled)
            .onHover { hovering = $0 }
            .accessibilityLabel(label)
        }
        .contentShape(Rectangle())
        .help(disabledHelp ?? help)
    }
}

/// A sign-in is running in its own window, which survives the popover closing; this says so and
/// brings the window back if it went behind another one.
struct SigningInBar: View {
    let onShowWindow: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                ProgressView()
                    .controlSize(.small)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Signing in…")
                        .font(.caption.weight(.medium))
                    Text("Finish in the sign-in window.")
                        .font(.caption2)
                        .foregroundStyle(.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Button("Show sign-in window", action: onShowWindow)
                    .controlSize(.small)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            Divider()
        }
        .background(Color.cardFillStrong)
    }
}

/// The confirmation "Add current account" always had, kept inline: a system confirmation dialog
/// inside a transient popover is not something to rely on.
struct AddCurrentConfirmBar: View {
    let onCancel: () -> Void
    let onAdd: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 8) {
                Text("This will capture the currently logged-in Claude Code account.")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    Button("Cancel", action: onCancel)
                        .buttonStyle(.bordered)
                        .controlSize(.small)

                    Button("Add Account", action: onAdd)
                        .buttonStyle(.borderedProminent)
                        .tint(.brand)
                        .controlSize(.small)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            Divider()
        }
        .background(Color.cardFillStrong)
    }
}
