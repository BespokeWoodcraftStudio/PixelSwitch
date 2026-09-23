import SwiftUI

/// Lists all configured accounts with switching and management.
struct AccountSwitcherView: View {
    /// Settings → Appearance. Off restores the plain, uncoloured rows.
    @AppStorage(AccountColorCoding.key) private var colorCodeAccounts = true
    @EnvironmentObject private var appState: AppState
    @AppStorage(EmailDisplay.key) private var maskEmails = false
    @State private var showingAddConfirm = false
    @State private var editingAccountId: UUID?
    @State private var editingLabel = ""

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(spacing: 12) {
                    if appState.accounts.isEmpty {
                        emptyState
                    } else {
                        ForEach(appState.accounts) { account in
                            accountRow(account, swatch: accountSwatches[account.id])
                        }
                    }
                }
                .padding(16)
            }

            addAccountButtons
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
                .padding(.top, 8)
        }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "person.crop.circle.badge.plus")
                .font(.system(size: 40))
                .foregroundStyle(.textSecondary)

            Text("No Accounts")
                .font(.headline)

            Text("Add your current Claude Code account to get started.")
                .font(.caption)
                .foregroundStyle(.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    // MARK: - Account Row

    /// The same colour per account as the Usage tab, so an account looks the
    /// same wherever it appears.
    private var accountSwatches: [UUID: Int] {
        AccountPalette.assignment(for: appState.accounts.map(\.id))
    }

    private func accountRow(_ account: Account, swatch: Int?) -> some View {
        HStack(spacing: 12) {
            if colorCodeAccounts {
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(AccountPalette.color(swatch))
                    .frame(width: 4)
                    .frame(maxHeight: .infinity)
                    .accessibilityHidden(true)
            }

            // The PixelSwitch mark, in the account's colour
            AccountGlyph(
                provider: account.provider,
                tint: colorCodeAccounts ? AccountPalette.color(swatch) : (account.isActive ? Color.brand : Color.secondary),
                size: 20
            )
            .frame(width: 22, height: 22)

            // Account info
            VStack(alignment: .leading, spacing: 2) {
                if editingAccountId == account.id {
                    HStack(spacing: 4) {
                        TextField("Custom label", text: $editingLabel)
                            .textFieldStyle(.roundedBorder)
                            .font(.subheadline)
                            .onSubmit { commitLabelEdit(account) }

                        Button {
                            commitLabelEdit(account)
                        } label: {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        }
                        .buttonStyle(.plain)

                        Button {
                            editingAccountId = nil
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.textSecondary)
                        }
                        .buttonStyle(.plain)
                    }
                } else {
                    // The address, and nothing above it.
                    //
                    // This row used to lead with the organisation name, which
                    // Anthropic returns as "<your address>'s Organization", and
                    // then print the address again underneath. On a 360pt panel
                    // with a Switch button and two icons beside it, that left
                    // the text about 130pt wide and it wrapped mid-word:
                    // "ahmed@be-spokewood-craftstudio.-com's Organization", with
                    // even the Max badge splitting into "Ma" and "x". The
                    // founder's verdict: "We don't need the email address or
                    // organization. It should just be the email address."
                    HStack(spacing: 6) {
                        Text(primaryLabel(for: account))
                            .font(.subheadline.weight(.medium))
                            .lineLimit(1)
                            .truncationMode(.tail)

                        Button {
                            editingLabel = account.customLabel ?? ""
                            editingAccountId = account.id
                        } label: {
                            Image(systemName: "pencil")
                                .font(.caption2)
                                .foregroundStyle(.textSecondary)
                        }
                        .buttonStyle(.plain)
                        .help("Edit label")
                        .fixedSize()

                        if account.isActive {
                            Badge(text: String(localized: "Active", bundle: L10n.bundle), color: .green)
                                .fixedSize()
                        }
                    }
                }

                // One quiet line underneath: the plan and the provider, plus the
                // address when a custom label has taken its place above.
                // `fixedSize` on each piece is what stops a two-letter word
                // breaking in half when the column is narrow.
                HStack(spacing: 5) {
                    let hasLabel = !(account.customLabel ?? "").isEmpty
                    if hasLabel {
                        // The address matters more than anything else on this
                        // line, so it truncates last. Without the priority the
                        // fixed-size pieces beside it win and it collapses to
                        // "so...", which identifies nothing.
                        Text(account.displayEmail(obfuscated: maskEmails))
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .layoutPriority(1)
                        Text(verbatim: "·").foregroundStyle(.quaternary).fixedSize()
                    }
                    if let sub = account.displaySubscriptionType {
                        Text(sub).fixedSize()
                        if !hasLabel {
                            Text(verbatim: "·").foregroundStyle(.quaternary).fixedSize()
                        }
                    }
                    // Dropped when a label has pushed the address down here:
                    // every account is a Claude Code account today, so it is the
                    // first thing worth giving up for room.
                    if !hasLabel {
                        Text(account.provider.rawValue).fixedSize()
                    }
                }
                .font(.caption2)
                .foregroundStyle(.textSecondary)
                .lineLimit(1)
            }
            // Takes every point the buttons do not need. There is deliberately
            // NO Spacer after this: a Spacer expands too, so the two of them
            // split the leftover space between them and the text truncated at
            // about half the width available to it, cutting even a short
            // address ("short@example.c..." with 50pt of empty row beside it).
            .frame(maxWidth: .infinity, alignment: .leading)

            // Actions
            if !account.isActive {
                Button("Switch") {
                    Task { await appState.switchTo(account) }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .tint(.brand)
            }

            Button {
                Task { await appState.reauthenticateAccount(account) }
            } label: {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            .buttonStyle(.plain)
            .help("Re-authenticate (fix stale token)")

            Button {
                appState.removeAccount(account)
            } label: {
                Image(systemName: "trash")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            .buttonStyle(.plain)
            .help("Remove account")
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(account.isActive ? .cardFillStrong : .clear)
                // Same orange ring as the Usage tab, so "this is the one you
                // are signed in to" looks identical wherever you are looking.
                .strokeBorder(account.isActive ? Color.activeAccountRing : .cardBorder,
                              lineWidth: account.isActive ? 2 : 1)
                .shadow(color: AppStyle.cardShadowColor, radius: AppStyle.cardShadowRadius, x: 0, y: AppStyle.cardShadowY)
        )
    }

    /// What the row leads with: a custom label if one has been set, otherwise
    /// the address. Never the organisation name, which Anthropic returns as
    /// "<address>'s Organization" and which therefore says nothing the address
    /// does not already say, at three times the width.
    private func primaryLabel(for account: Account) -> String {
        if let label = account.customLabel, !label.isEmpty { return label }
        return account.displayEmail(obfuscated: maskEmails)
    }

    private func commitLabelEdit(_ account: Account) {
        appState.updateAccountLabel(account, label: editingLabel)
        editingAccountId = nil
    }

    // MARK: - Add Account Buttons

    @ViewBuilder
    private var addAccountButtons: some View {
        if appState.isLoggingIn {
            // Logging in state
            VStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Waiting for browser login...")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                Text("Complete the login in your browser, then return here.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(.cardFillStrong)
                    .strokeBorder(.cardBorder, lineWidth: 1)
                    .shadow(color: AppStyle.cardShadowColor, radius: AppStyle.cardShadowRadius, x: 0, y: AppStyle.cardShadowY)
            )
        } else if showingAddConfirm {
            // Inline confirmation for "Add Current"
            VStack(spacing: 8) {
                Text("This will capture the currently logged-in Claude Code account.")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                    .multilineTextAlignment(.center)

                HStack(spacing: 12) {
                    Button("Cancel") {
                        withAnimation { showingAddConfirm = false }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)

                    Button("Add Account") {
                        showingAddConfirm = false
                        Task { await appState.addAccount() }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.brand)
                    .controlSize(.small)
                }
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(.cardFillStrong)
                    .strokeBorder(.cardBorder, lineWidth: 1)
                    .shadow(color: AppStyle.cardShadowColor, radius: AppStyle.cardShadowRadius, x: 0, y: AppStyle.cardShadowY)
            )
        } else {
            VStack(spacing: 8) {
                // Primary: Login new account via browser
                Button {
                    Task { await appState.loginNewAccount() }
                } label: {
                    Label("Login New Account", systemImage: "person.badge.plus")
                        .font(.subheadline.weight(.medium))
                        .foregroundColor(AppStyle.buttonTextColor)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                        .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)

                // Secondary: Capture already-logged-in account
                Button {
                    withAnimation { showingAddConfirm = true }
                } label: {
                    Label("Add Current Account", systemImage: "plus.circle")
                        .font(.caption)
                        .foregroundStyle(.textSecondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .strokeBorder(
                                    colorScheme == .dark
                                        ? Color.gray.opacity(0.4)
                                        : Color.white.opacity(0.22),
                                    lineWidth: 1
                                )
                        )
                }
                .buttonStyle(.plain)
            }
        }
    }
}
