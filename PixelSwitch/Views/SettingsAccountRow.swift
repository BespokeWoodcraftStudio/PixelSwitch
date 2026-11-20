import SwiftUI
import AppKit

/// What the Session or Weekly menu on an account row can choose.
enum ThresholdMenuChoice: Equatable {
    /// "Default (N%)": this window follows its default again.
    case followDefault
    /// "Manual only": auto-switch never moves you to the account (both windows).
    case manualOnly
    /// "Switch at N%": the window's own limit, 1–100.
    case percent(Double)
}

/// What a row shows about an account's thresholds, from the account alone.
enum AccountRowThresholds {
    /// The menu's choices: 10–40 in 10s, then 50–100 in 5s; the stepper beside
    /// a custom value reaches every whole number from 1 to 100. Manual only (0)
    /// is its own named item, never a stepper stop.
    static let choices: [Double] = [10, 20, 30, 40] + Array(stride(from: 50.0, through: 100.0, by: 5.0))

    /// The account's own value for one window, or nil while that window follows
    /// the default: its window value, else its both-windows value. Nil for a
    /// Manual only account (spec 2.4: the menu then reads "Manual only").
    static func own(_ window: AutoSwitchEngine.Window, of account: Account) -> Double? {
        guard !AutoSwitchSettings.isManualOnly(own: account.switchThreshold) else { return nil }
        let windowValue = window == .session ? account.sessionThreshold : account.weeklyThreshold
        if let value = AutoSwitchSettings.normalizedWindowThreshold(windowValue) { return value }
        if let both = AutoSwitchSettings.normalizedAccountThreshold(account.switchThreshold),
           both > AutoSwitchSettings.manualOnlyThreshold { return both }
        return nil
    }
}

/// How wide the Session and Weekly columns must be for the language in use.
/// The pop-ups size to their text and German or French labels are longer than
/// English, so a fixed width would let them draw over the next column. The
/// width is measured from the widest label ("Switch at 100%" with its
/// stepper, "Default (100%)", "Manual only") in the language's string table,
/// in the pop-up's own font, so it follows the translation and never clips it.
enum WindowColumnMetrics {
    /// What a pop-up button draws around its text: the arrows and the padding
    /// (measured 48 to 49 pt at 13 pt on macOS 14 and later).
    static let menuChrome: CGFloat = 50
    /// The stepper beside a custom value, and the gap before it.
    static let stepperWidth: CGFloat = 20
    static let stepperGap: CGFloat = 4
    /// Never narrower than this, so English rows keep their shape.
    static let minimum: CGFloat = 164

    static func width(bundle: Bundle) -> CGFloat {
        let font = NSFont.systemFont(ofSize: NSFont.systemFontSize)
        func measured(_ key: String) -> CGFloat {
            let template = bundle.localizedString(forKey: key, value: key, table: nil)
            // 100 is the widest number a limit can show.
            let text = template.contains("%") ? String(format: template, Int64(100)) : template
            return (text as NSString).size(withAttributes: [.font: font]).width
        }
        let withStepper = measured("Switch at %lld%%") + stepperGap + stepperWidth
        let bare = max(measured("Default (%lld%%)"), measured("Manual only"))
        return max(minimum, ceil(max(withStepper, bare) + menuChrome))
    }
}

/// One account's row in Settings → Accounts: drag handle, Priority control,
/// the account, and the Session and Weekly menus.
///
/// Plain inputs and closures only (no `AppState`, no `@AppStorage`), so the
/// same view that Settings shows is rendered offscreen with sample data
/// (`Tools/screenshots/run_settings_renders.sh`, in English, German, French,
/// Japanese and Chinese) and nothing can drift. The only things it reads
/// besides its inputs are the language choice (`L10n`) and its own pending
/// priority step and whether the pointer is over the row.
struct SettingsAccountRow: View {
    /// A custom label, else the address.
    let title: String
    /// The address, shown under a label.
    var subtitle: String?
    var isActive = false
    /// The localized word on the green badge.
    var activeBadge = "Active"
    /// "Weekly resets Mon 11:00 AM" and the like.
    let resetNote: Text
    /// The line for a Manual only account that is in use.
    var manualNote: Text?

    /// 1–99, nil = none.
    var priority: Int?
    /// The account's own limit for each window; nil = follows its default.
    var sessionOwn: Double?
    var weeklyOwn: Double?
    var manualOnly = false
    /// False for an account on a plan with no 5-hour limit (Ultra): the Session
    /// menu is replaced by a caption saying so. Defaulted, so every caller that
    /// predates it (and the offscreen renders) compiles as it was.
    var hasSessionLimit = true
    /// What the two windows follow while the account has no value of its own.
    let defaults: AutoSwitchEngine.WindowPair
    /// What the account's setting does, in numbers.
    let help: Text

    /// The account's own label, "" when it has none: where the inline editor starts.
    var customLabel = ""
    /// The account's sign-in has ended, so Re-sign is the fix: its icon takes the
    /// warning colour instead of the quiet one.
    var needsResign = false
    /// nil while Switch is on; otherwise why it is off, which is also its tooltip.
    var switchOffReason: String?
    /// A switch or a sign-in is running: Re-sign and Remove account wait too (the Usage card's
    /// rule, and the control API's).
    var actionsBusy = false

    let onPriority: @MainActor (Int?) -> Void
    let onSession: @MainActor (ThresholdMenuChoice) -> Void
    let onWeekly: @MainActor (ThresholdMenuChoice) -> Void
    /// The four things the popover's Accounts tab did and Settings did not.
    /// Switch to this account.
    let onSwitch: @MainActor () -> Void
    /// Re-sign: the same sign-in the Usage card's Re-sign button starts.
    let onResign: @MainActor () -> Void
    /// Save a new label (the text as typed; the app trims it, and empty clears it).
    let onLabel: @MainActor (String) -> Void
    /// Ask to remove the account. The row only asks; the tab confirms and removes.
    let onRemove: @MainActor () -> Void

    /// The priority a person is stepping to with the arrows, not yet saved. The
    /// list sorts by priority, so saving every click would move the row out
    /// from under the pointer and the next click would land on another
    /// account. It is saved when the pointer leaves the row, when the row goes
    /// away, when the app is about to quit or loses focus, and, for a person who
    /// is not using the pointer (keyboard, VoiceOver), after a short pause with
    /// no click. While the pointer is over the row it is held so the row cannot
    /// move between slow clicks, but never longer than `hoverHold` after the last
    /// click: a pointer that rests on the row with no exit event (the row was
    /// re-sorted from under it, or the app deactivated) cannot hold a step
    /// forever. A step still waiting is dropped when the saved number changes underneath
    /// it (a drag, or a change from the command line or an MCP client), so it
    /// never overwrites a newer value.
    ///
    /// `State` is written out instead of with `@State`: the offscreen render
    /// (`Tools/screenshots`) compiles this file on a Mac without Xcode, where the
    /// `@State` macro's plugin is missing. It is the same storage.
    private let pendingStorage = State<Int?>(initialValue: nil)
    private var pendingPriority: Int? {
        get { pendingStorage.wrappedValue }
        nonmutating set { pendingStorage.wrappedValue = newValue }
    }
    private static let priorityPause: Duration = .milliseconds(800)
    /// The longest a step is held after the last click while the pointer is
    /// over the row.
    private static let hoverHold: Duration = .seconds(10)

    /// Whether the pointer is over the row (written out like `pendingStorage`).
    private let hoverStorage = State<Bool>(initialValue: false)
    private var hovering: Bool {
        get { hoverStorage.wrappedValue }
        nonmutating set { hoverStorage.wrappedValue = newValue }
    }

    /// The label editor: whether it is open, the text being typed, and its focus
    /// (written out like `pendingStorage`, for the same reason).
    private let editingStorage = State<Bool>(initialValue: false)
    private let draftStorage = State<String>(initialValue: "")
    private let focusStorage = FocusState<Bool>()
    private var editing: Bool {
        get { editingStorage.wrappedValue }
        nonmutating set { editingStorage.wrappedValue = newValue }
    }

    var body: some View {
        // Measured once per row from the language in use (see WindowColumnMetrics).
        let columnWidth = WindowColumnMetrics.width(bundle: L10n.bundle)
        return HStack(spacing: 10) {
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.textSecondary)
                .accessibilityHidden(true)

            priorityControl
                .frame(width: 92, alignment: .leading)

            VStack(alignment: .leading, spacing: 2) {
                if editing {
                    labelEditor
                } else {
                    HStack(spacing: 6) {
                        Text(verbatim: title)
                            .font(.body.weight(.medium))
                            .lineLimit(1)
                            .truncationMode(.tail)
                        if isActive {
                            Badge(text: activeBadge, color: .green)
                                .fixedSize()
                        }
                    }
                }
                if let subtitle {
                    Text(verbatim: subtitle)
                        .font(.caption)
                        .foregroundStyle(.textSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                resetNote
                    .font(.caption)
                    .foregroundStyle(.textSecondary)
                if let manualNote {
                    manualNote
                        .font(.caption)
                        .foregroundStyle(.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if hasSessionLimit {
                windowControl(.session, own: sessionOwn, caption: Text("Session"), spoken: Text("Session limit for \(title)"),
                              width: columnWidth, choose: onSession)
            } else {
                noSessionLimit(width: columnWidth)
            }
            windowControl(.weekly, own: weeklyOwn, caption: Text("Weekly"), spoken: Text("Weekly limit for \(title)"),
                          width: columnWidth, choose: onWeekly)

            actions
        }
        .padding(.vertical, 4)
        // A step waits while the pointer is over the row, and is saved when it
        // leaves (`commitPriority` does nothing when no step is waiting).
        .onHover { inside in
            hovering = inside
            if !inside { commitPriority() }
        }
        // The saved number changed underneath a waiting step: the newer value wins.
        .onChange(of: priority) { _, _ in pendingPriority = nil }
        // A waiting step must not be lost when the app stops or loses focus with
        // the pointer still resting on the row (a remote quit, an update
        // relaunch, logout); neither fires a hover exit or a disappear.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
            commitPriority()
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
            commitPriority()
        }
    }

    // MARK: - Actions

    /// Switch over two small icons, Re-sign and a "..." menu (Edit label, Remove account), in one
    /// narrow column at the row's end. Stacked rather than side by side because the row has no
    /// width to spare: the address must keep as much of the row as it can.
    /// Switch and Re-sign are one click; the two that change the account itself are one menu away.
    /// Switch, Re-sign and Remove wait while a switch or a sign-in runs (the control API refuses
    /// them then too).
    ///
    /// The tooltips sit on a plain wrapper around each button, not on the button: macOS may not
    /// show a tooltip on a disabled control, and the tooltip is how Switch says why it is off.
    private var actions: some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 0) {
                Button { onSwitch() } label: { Text("Switch") }
                    .controlSize(.small)
                    .disabled(switchOffReason != nil)
                    .accessibilityLabel(Text("Switch to \(title)"))
                    // Voice Control: "Click Switch" still works with the spoken label naming the account.
                    .accessibilityInputLabels([Text("Switch")])
            }
            .contentShape(Rectangle())
            // The reason it is off, else what it does.
            .help(switchOffReason.map { Text(verbatim: $0) } ?? Text("Switch to this account"))

            HStack(spacing: 4) {
                // The Usage card's Re-sign icon and tooltip, so it is the same button everywhere.
                HStack(spacing: 0) {
                    Button { onResign() } label: {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .foregroundStyle(needsResign ? Color.limitWarn : Color.textSecondary)
                            .frame(width: 24, height: 20)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(actionsBusy)
                    .accessibilityLabel(Text("Re-authenticate \(title)"))
                    .accessibilityInputLabels([Text("Re-sign")])
                }
                .contentShape(Rectangle())
                .help(actionsBusy ? Text("Wait for the current switch or sign-in to finish.") : Text("Re-authenticate (fix stale token)"))

                Menu {
                    Button("Edit label") { beginEditing() }
                    Divider()
                    Button("Remove account", role: .destructive) { onRemove() }
                        .disabled(actionsBusy)
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .foregroundStyle(Color.textSecondary)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help(Text("More actions"))
                .accessibilityLabel(Text("More actions for \(title)"))
            }
        }
        .fixedSize()
    }

    // MARK: - Label editor

    /// The popover's inline edit: a text field with Save and Cancel. Return saves.
    private var labelEditor: some View {
        HStack(spacing: 4) {
            TextField("Custom label", text: draftStorage.projectedValue)
                .textFieldStyle(.roundedBorder)
                .focused(focusStorage.projectedValue)
                .onSubmit { commitLabel() }
                .onExitCommand { editing = false }
                .onAppear { focusStorage.wrappedValue = true }

            Button { commitLabel() } label: {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.costGreen)
            }
            .buttonStyle(.plain)
            .help(Text("Save label"))
            .accessibilityLabel(Text("Save label"))

            Button { editing = false } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(Color.textSecondary)
            }
            .buttonStyle(.plain)
            .help(Text("Cancel"))
            .accessibilityLabel(Text("Cancel"))
        }
    }

    private func beginEditing() {
        draftStorage.wrappedValue = customLabel
        editing = true
    }

    private func commitLabel() {
        onLabel(draftStorage.wrappedValue)
        editing = false
    }

    // MARK: - Priority

    /// What the row shows: the number being stepped to, else the saved one.
    private var shownPriority: Int? { pendingPriority ?? priority }

    /// One click of an arrow: moves the shown number only; `.task(id:)` below
    /// saves it once the clicks stop (each new number restarts the pause).
    private func stepPriority(by delta: Int) {
        let next = (shownPriority ?? 0) + delta
        guard (1...99).contains(next) else { return }
        pendingPriority = next
    }

    /// Saves the number being stepped to. The saved value is handed over before
    /// the pending one is cleared, so the row never flashes the old number.
    private func commitPriority() {
        guard let value = pendingPriority else { return }
        onPriority(value)
        pendingPriority = nil
    }

    /// A pick from the menu is one deliberate action: it replaces any pending
    /// step and is saved at once.
    private func choosePriority(_ value: Int?) {
        pendingPriority = nil
        onPriority(value)
    }

    private var priorityControl: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Priority")
                .font(.caption2)
                .foregroundStyle(.textSecondary)
                .accessibilityHidden(true)
            HStack(spacing: 4) {
                Menu {
                    Button("None") { choosePriority(nil) }
                    Divider()
                    ForEach(1...9, id: \.self) { number in
                        Button { choosePriority(number) } label: { Text(verbatim: "P\(number)") }
                    }
                } label: {
                    if let shown = shownPriority {
                        Text(verbatim: "P\(shown)").monospacedDigit()
                    } else {
                        Text("None")
                    }
                }
                .fixedSize()
                .help(Text("Auto-switch tries accounts with lower numbers first when it chooses by Priority. Accounts with no number come last."))
                .accessibilityLabel(Text("Priority for \(title)"))
                .accessibilityValue(shownPriority.map { Text(verbatim: "P\($0)") } ?? Text("None"))

                if let shown = shownPriority {
                    Stepper(onIncrement: { stepPriority(by: 1) }, onDecrement: { stepPriority(by: -1) }) {
                        Text("Priority for \(title)")
                    }
                    .labelsHidden()
                    .accessibilityLabel(Text("Priority for \(title)"))
                    .accessibilityValue(Text(verbatim: "P\(shown)"))
                }
            }
        }
        // Restarts whenever the number being stepped to changes, so the save
        // waits for a pause after the LAST click; cancelled with the row. With
        // the pointer over the row it waits for the pointer to leave, but saves
        // anyway once `hoverHold` has passed since the last click.
        .task(id: pendingPriority) {
            guard pendingPriority != nil else { return }
            try? await Task.sleep(for: Self.priorityPause)
            guard !Task.isCancelled else { return }
            if hovering {
                try? await Task.sleep(for: Self.hoverHold - Self.priorityPause)
                guard !Task.isCancelled else { return }
            }
            commitPriority()
        }
        .onDisappear {
            commitPriority()
            hovering = false
        }
    }

    // MARK: - Session and Weekly

    /// Where the Session menu would be, for an account with no 5-hour limit.
    /// Static text, read by VoiceOver as one line ("Session, No 5-hour limit").
    private func noSessionLimit(width: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Session")
                .font(.caption2)
                .foregroundStyle(.textSecondary)
            Text("No 5-hour limit")
                .font(.callout)
                .foregroundStyle(.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .accessibilityElement(children: .combine)
        .frame(width: width, alignment: .leading)
    }

    private func windowControl(_ window: AutoSwitchEngine.Window, own: Double?, caption: Text, spoken: Text, width: CGFloat,
                               choose: @escaping @MainActor (ThresholdMenuChoice) -> Void) -> some View {
        let shownDefault = Int(defaults.value(for: window))
        // What the pop-up shows; also what VoiceOver reads as its value.
        let shown: Text
        if manualOnly {
            shown = Text("Manual only")
        } else if let own {
            shown = Text("Switch at \(Int(own))%")
        } else {
            shown = Text("Default (\(shownDefault)%)")
        }
        return VStack(alignment: .leading, spacing: 2) {
            caption
                .font(.caption2)
                .foregroundStyle(.textSecondary)
                .accessibilityHidden(true)
            HStack(spacing: 4) {
                Menu {
                    Button("Default (\(shownDefault)%)") { choose(.followDefault) }
                    Divider()
                    Button("Manual only (\(Int(AutoSwitchSettings.manualOnlyThreshold))%)") { choose(.manualOnly) }
                    Divider()
                    ForEach(AccountRowThresholds.choices, id: \.self) { value in
                        Button { choose(.percent(value)) } label: { Text(verbatim: "\(Int(value))%") }
                    }
                } label: {
                    shown
                }
                .fixedSize()
                .help(help)
                .accessibilityLabel(spoken)
                .accessibilityValue(shown)

                if let own, !manualOnly {
                    Stepper(onIncrement: { if own < AutoSwitchSettings.accountStepperRange.upperBound { choose(.percent(own + 1)) } },
                            onDecrement: { if own > AutoSwitchSettings.accountStepperRange.lowerBound { choose(.percent(own - 1)) } }) {
                        spoken
                    }
                    .labelsHidden()
                    .accessibilityLabel(spoken)
                    .accessibilityValue(shown)
                }
            }
        }
        // One width for both columns on every row, so Session and Weekly line up
        // down the list whether or not a row shows a stepper. It is measured for
        // the language in use (`WindowColumnMetrics`), so no label is clipped.
        .frame(width: width, alignment: .leading)
    }
}
