import SwiftUI

/// Settings → General → Auto-switch: the two default limits, 50–100.
///
/// "Session (5-hour) default" and "Weekly default" each write only their own
/// setting. The numbers shown are the EFFECTIVE defaults (a limit nobody has set
/// follows the base `autoSwitch.threshold`), so a Mac whose base is 98% shows 98
/// on both sliders, never a stand-in for "absent".
///
/// Plain inputs and closures only (no `AppState`, no `@AppStorage`), so the same
/// view Settings shows is rendered offscreen with sample data
/// (`Tools/screenshots/make_settings_renders.swift`).
struct SettingsThresholdDefaults: View {
    let session: Double
    let weekly: Double
    let onSession: @MainActor (Double) -> Void
    let onWeekly: @MainActor (Double) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            slider("Session (5-hour) default", value: session, set: onSession)
            slider("Weekly default", value: weekly, set: onWeekly)
            Text("When no account under its weekly limit can take you, the session limit decides.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Any account can have its own threshold in Settings → Accounts, from 1% to 100%, or be Manual only so auto-switch never moves you to it. 100% uses an account until it is empty.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func slider(_ title: LocalizedStringKey, value: Double, set: @escaping @MainActor (Double) -> Void) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title)
                Spacer()
                Text("\(Int(value))%")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            Slider(value: Binding(get: { value }, set: { set($0) }), in: AutoSwitchSettings.thresholdRange, step: 1)
        }
    }
}
