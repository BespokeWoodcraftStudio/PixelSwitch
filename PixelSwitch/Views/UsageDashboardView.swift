import SwiftUI

/// Hover tooltip that works inside MenuBarExtra panels (where `.help()` doesn't).
private struct StatWithTooltip<Content: View>: View {
    let tooltip: LocalizedStringKey
    @ViewBuilder let content: Content
    @State private var isHovering = false
    @Environment(\.locale) private var locale

    var body: some View {
        content
            .onHover { isHovering = $0 }
            .popover(isPresented: $isHovering, arrowEdge: .bottom) {
                Text(tooltip)
                    .font(.caption)
                    .padding(8)
                    .frame(width: 200)
                    .environment(\.locale, locale)
            }
    }
}

/// Shows real usage limits from Claude API, one card per account.
struct UsageDashboardView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var menuBarConfig: MenuBarConfig
    @AppStorage(EmailDisplay.key) private var maskEmails = false
    /// Settings → Appearance. Off restores the plain, uncoloured cards.
    @AppStorage(AccountColorCoding.key) private var colorCodeAccounts = true

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                if appState.accounts.isEmpty && appState.isLoading {
                    VStack(spacing: 12) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Loading usage data...")
                            .font(.caption)
                            .foregroundStyle(.textSecondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
                } else if appState.accounts.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "chart.bar.xaxis")
                            .font(.system(size: 32))
                            .foregroundStyle(.textSecondary)
                        Text("Usage data unavailable")
                            .font(.subheadline)
                            .foregroundStyle(.textSecondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
                } else {
                    // Today's cost banner (local parsing, no API needed)
                    todayCostBanner

                    // Today's activity stats
                    todayActivityCard

                    ForEach(appState.accounts) { account in
                        accountUsageCard(
                            account: account,
                            usage: appState.accountUsage[account.id],
                            swatch: accountSwatches[account.id]
                        )
                    }
                }

                // Last updated
                if let lastRefresh = appState.lastUsageRefresh {
                    HStack(spacing: 4) {
                        Spacer()
                        Image(systemName: "arrow.clockwise")
                            .font(.caption2)
                        Text(lastRefresh, style: .relative)
                    }
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                    .padding(.horizontal, 16)
                }
            }
            .padding(.vertical, 12)
            // Report this VStack's intrinsic height to MainMenuView so it can
            // size the popover frame to fit the Usage tab's content exactly.
            // See MainMenuView.swift for the measurement contract.
            .measureUsageContentHeight()
        }
    }

    // MARK: - Today Cost Banner

    private var todayCostBanner: some View {
        let cost = appState.costSummary.todayCost
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "dollarsign.circle")
                    .font(.subheadline)
                    .foregroundStyle(.green)
                Text("Today's API-Equivalent Cost")
                    .font(.subheadline.weight(.medium))
                    .lineLimit(1)
                Spacer()
            }

            StatWithTooltip(tooltip: Self.costDisclaimer) {
                Text(cost >= 1 ? String(format: "$%.2f", cost) : String(format: "$%.4f", cost))
                    .font(.title.weight(.semibold).monospacedDigit())
                    .foregroundStyle(.green)
            }
        }
        .cardStyle()
        .sectionPadding()
    }

    private static let costDisclaimer: LocalizedStringKey = "Estimated API-equivalent cost of your Claude Code usage, for reference only."

    // MARK: - Today Activity Card

    private var todayActivityCard: some View {
        let stats = appState.activityStats
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "waveform.path.ecg")
                    .font(.subheadline)
                    .foregroundStyle(.brand)
                Text("Today's Activity")
                    .font(.subheadline.weight(.medium))
                Spacer()
            }

            // Top stats row
            HStack(spacing: 0) {
                activityStat(icon: "bubble.left.and.bubble.right", value: "\(stats.conversationTurns)", label: "Turns",
                             tooltip: "Messages you sent to Claude Code today")
                activityStat(icon: "clock", value: stats.activeCodingTimeString, label: "Active",
                             tooltip: "Estimated total time Claude worked for you today. Parallel sessions stack. Idle gaps >10 min excluded. This is an approximation based on message timestamps, not exact.")
                activityStat(icon: "doc.text", value: "\(stats.linesWritten)", label: "Lines",
                             tooltip: "Estimated lines of code written by Claude via Edit/Write tools")
            }

            // Model usage row — same style as stats above
            HStack(spacing: 0) {
                modelStat(name: "Fable", count: stats.modelUsage["Fable"] ?? 0,
                          tooltip: "Claude Fable 5 — most powerful model, the new flagship tier")
                modelStat(name: "Opus", count: stats.modelUsage["Opus"] ?? 0,
                          tooltip: "Claude Opus 4 — most capable model, best for complex tasks")
                modelStat(name: "Sonnet", count: stats.modelUsage["Sonnet"] ?? 0,
                          tooltip: "Claude Sonnet 4 — balanced speed and capability")
                modelStat(name: "Haiku", count: stats.modelUsage["Haiku"] ?? 0,
                          tooltip: "Claude Haiku 4 — fastest model, best for simple tasks")
            }
        }
        .cardStyle()
        .sectionPadding()
    }

    private func activityStat(icon: String, value: String, label: LocalizedStringKey, tooltip: LocalizedStringKey) -> some View {
        StatWithTooltip(tooltip: tooltip) {
            VStack(spacing: 3) {
                Text(value)
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                HStack(spacing: 3) {
                    Image(systemName: icon)
                        .font(.caption2)
                        .foregroundStyle(.textSecondary)
                    Text(label)
                        .font(.caption2)
                        .foregroundStyle(.textSecondary)
                }
            }
            .frame(maxWidth: .infinity)
        }
    }

    private func modelStat(name: String, count: Int, tooltip: LocalizedStringKey) -> some View {
        StatWithTooltip(tooltip: tooltip) {
            VStack(spacing: 3) {
                Text("\(count)")
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(count > 0 ? .primary : .quaternary)
                HStack(spacing: 3) {
                    Circle()
                        .fill(modelColor(name))
                        .frame(width: 7, height: 7)
                    Text(name)
                        .font(.caption2)
                        .foregroundStyle(count > 0 ? .tertiary : .quaternary)
                }
            }
            .frame(maxWidth: .infinity)
        }
    }

    private func modelColor(_ name: String) -> Color {
        switch name {
        case "Fable": return .purple
        case "Opus": return .brand
        case "Sonnet": return .blue
        case "Haiku": return .green
        default: return .gray
        }
    }

    // MARK: - Per-Account Card

    /// A colour per account, worked out once per render from the accounts on
    /// screen, so every card differs and each keeps its colour across launches.
    private var accountSwatches: [UUID: Int] {
        AccountPalette.assignment(for: appState.accounts.map(\.id))
    }

    private func accountUsageCard(account: Account, usage: UsageAPIResponse?, swatch: Int?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            if colorCodeAccounts {
                // The account's colour lives here, at full strength, instead of
                // over the text.
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(AccountPalette.color(swatch))
                    .frame(width: 4)
                    .frame(maxHeight: .infinity)
                    .accessibilityHidden(true)
            }

            VStack(alignment: .leading, spacing: 10) {
                accountHeader(account, swatch: swatch)
            if let usage = usage {
                usageBars(usage)
                extraUsageRow(usage.extraUsage)
                sampleAgeLabel(account)
            } else if let errorState = appState.accountUsageErrors[account.id] {
                HStack {
                    Image(systemName: errorState.isRateLimited ? "timer" : (errorState.isExpired ? "exclamationmark.triangle" : "xmark.circle"))
                        .foregroundStyle(errorState.isExpired ? .yellow : .red)
                    Text(errorState.message)
                        .font(.caption)
                        .foregroundStyle(.textSecondary)
                        .lineLimit(2)
                    Spacer()
                }
                .padding(.top, 4)
            } else {
                // No sample yet (accounts are polled round-robin) - this is a
                // waiting state, not an error. The old hardcoded "Token expired"
                // text here made healthy accounts look broken.
                HStack {
                    Image(systemName: "hourglass")
                        .foregroundStyle(.secondary)
                    Text("Waiting for usage data...")
                        .font(.caption)
                        .foregroundStyle(.textSecondary)
                    Spacer()
                }
                .padding(.top, 4)
            }
        }
        }
        // The account you are signed in to right now is ringed in orange, all
        // the way round. The small green "Active" badge is still there, but a
        // badge has to be hunted for; a ring is seen without reading anything.
        .cardStyle(
            border: account.isActive ? .activeAccountRing : .cardBorder,
            borderWidth: account.isActive ? 2 : 1
        )
        .sectionPadding()
    }

    /// Accounts are polled round-robin (active + one other per cycle), so a
    /// card's numbers can be several cycles old — say so instead of letting a
    /// stale percentage render exactly like a live one. Hidden while the sample
    /// is fresh (< 90s); the relative text then counts up by itself.
    @ViewBuilder
    private func sampleAgeLabel(_ account: Account) -> some View {
        if let sampledAt = appState.accountUsageSampledAt[account.id],
           Date().timeIntervalSince(sampledAt) >= 90 {
            HStack(spacing: 3) {
                Image(systemName: "clock")
                    .font(.caption2)
                Text("Updated \(Text(sampledAt, style: .relative)) ago")
                    .font(.caption2)
            }
            .foregroundStyle(.textSecondary)
        }
    }

    @ViewBuilder
    private func accountHeader(_ account: Account, swatch: Int?) -> some View {
        HStack(spacing: 8) {
            AccountGlyph(
                provider: account.provider,
                tint: colorCodeAccounts ? AccountPalette.color(swatch) : (account.isActive ? Color.brand : Color.secondary),
                size: 17
            )

            Text(account.displayEmail(obfuscated: maskEmails))
                .font(.subheadline.weight(.medium))
                .lineLimit(1)

            if account.isActive {
                Badge(text: String(localized: "Active", bundle: L10n.bundle), color: .green)
            }

            Spacer()

            if let sub = account.displaySubscriptionType {
                Badge(text: sub, color: .brand)
            }
        }
    }

    @ViewBuilder
    private func usageBars(_ usage: UsageAPIResponse) -> some View {
        if let session = usage.fiveHour {
            usageRow(title: "Session", resetText: session.resetTimeString,
                     resetIsAbsolute: session.resetIsAbsolute,
                     utilization: session.utilization ?? 0, kind: .session)
        }
        if let weekly = usage.sevenDay {
            usageRow(title: "Weekly", resetText: weekly.resetTimeString,
                     resetIsAbsolute: weekly.resetIsAbsolute,
                     utilization: weekly.utilization ?? 0, kind: .weekly)
        }
        // Per-model weekly allowances (Fable), with their own colour and symbol.
        ForEach(Array(usage.modelWeeklyLimits.enumerated()), id: \.offset) { _, limit in
            if let name = limit.modelName, let used = limit.percent {
                usageRow(title: LocalizedStringKey(name), resetText: limit.window.resetTimeString,
                         resetIsAbsolute: limit.window.resetIsAbsolute,
                         utilization: used, kind: .fable)
            }
        }
    }

    @ViewBuilder
    private func extraUsageRow(_ extra: ExtraUsage?) -> some View {
        if let extra {
            let enabled = extra.isEnabled == true
            let iconColor: Color = enabled ? .orange : .gray
            let statusColor: Color = enabled ? .orange : .gray
            HStack(spacing: 6) {
                Image(systemName: enabled ? "bolt.fill" : "bolt.slash")
                    .font(.caption)
                    .foregroundStyle(iconColor)
                Text("Extra usage")
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                Spacer()
                Text(LocalizedStringKey(enabled ? "On" : "Off"))
                    .font(.caption)
                    .foregroundStyle(statusColor)
            }
        }
    }

    // MARK: - Usage Row

    private func usageRow(title: LocalizedStringKey, resetText: String?, resetIsAbsolute: Bool = false, utilization: Double, kind: LimitBarKind) -> some View {
        UsageLimitRow(
            kind: kind,
            title: title,
            utilization: utilization,
            resetText: resetText,
            resetIsAbsolute: resetIsAbsolute,
            identityColor: menuBarConfig.limitIdentityColor(for: kind),
            fillColor: menuBarConfig.limitBarColor(for: kind, utilization: utilization, context: .dashboard),
            isLow: menuBarConfig.limitIsLow(utilization: utilization)
        )
    }
}
