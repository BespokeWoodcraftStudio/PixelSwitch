import SwiftUI
import AppKit

// A hover label the app draws itself.
//
// The system tooltip (`.help`) shows unreliably in a menu bar popover, and not at all on a disabled
// control, which is how a hand check of the footer icons came back as "works sometimes". This is
// the label that always shows: a small caption that appears above the pointed-at icon after a short
// pause and goes when the pointer leaves. `.help` stays on the same views, for accessibility tools.
//
// The pointer is found with an AppKit tracking area that is active always (`HoverTracker`), not with
// SwiftUI's `onHover`: PixelSwitch is an LSUIElement app, and a menu bar popover is often not the key
// window and the app often not active, and SwiftUI's hover only reports while they are. A tracking
// area with `.activeAlways` reports in every state.
//
// It is an overlay, so it takes no room and cannot move anything; it ignores the pointer (the tracking
// view returns nil from `hitTest`), so it never blocks a click; and it is drawn in the card colours,
// so it reads in light and in dark. Plain views only (no `AppState`), so the screenshot harnesses
// compile this file and draw the very label the app shows (`alwaysShown` holds it open for a still).
//
// A label that cannot be seen the moment it is wanted goes away or moves: it hides when the window
// closes or stops being key or main, when the view leaves its window, and when any menu opens; and an
// icon whose label would be cut off above (the first row of a scrolling list, where the scroll view
// ends right above the row) shows it below the icon instead.

/// Where the label sits against the icon it names. An icon at the edge of its window uses `.trailing`
/// (or `.leading`), so the label grows inward from that edge and is never cut off by it.
enum HoverLabelAnchor {
    /// Centred over the icon.
    case center
    /// The label's leading edge on the icon's leading edge (it grows to the right).
    case leading
    /// The label's trailing edge on the icon's trailing edge (it grows to the left).
    case trailing

    var horizontal: HorizontalAlignment {
        switch self {
        case .center: .center
        case .leading: .leading
        case .trailing: .trailing
        }
    }
}

enum HoverLabelMetrics {
    /// How long the pointer rests on an icon before its label shows.
    static let delay: Duration = .milliseconds(300)
    static let fontSize: CGFloat = 11
    static let horizontalPadding: CGFloat = 8
    static let verticalPadding: CGFloat = 3

    /// The bubble's width: the text on one line when it fits, else as wide as its longest wrapped line
    /// (never more than `maxWidth`), so a wrapped label has no empty strip beside its text.
    /// Measured with the same system font the label draws in, so a line that fits is never wrapped.
    static func width(for text: String, maxWidth: CGFloat) -> CGFloat {
        let font = NSFont.systemFont(ofSize: fontSize)
        let line = (text as NSString).size(withAttributes: [.font: font]).width
        let chrome = 2 * horizontalPadding + 2
        guard ceil(line) + chrome > maxWidth else { return ceil(line) + chrome }
        let wrapped = (text as NSString).boundingRect(with: NSSize(width: maxWidth - chrome, height: .greatestFiniteMagnitude),
                                                      options: [.usesLineFragmentOrigin], attributes: [.font: font])
        return min(maxWidth, ceil(wrapped.width) + chrome)
    }

    /// The bubble's height for its text at that width: the wrapped text and the padding above and below it.
    static func height(for text: String, maxWidth: CGFloat) -> CGFloat {
        let font = NSFont.systemFont(ofSize: fontSize)
        let inner = max(1, width(for: text, maxWidth: maxWidth) - 2 * horizontalPadding)
        let box = (text as NSString).boundingRect(with: NSSize(width: inner, height: .greatestFiniteMagnitude),
                                                  options: [.usesLineFragmentOrigin], attributes: [.font: font])
        return ceil(box.height) + 2 * verticalPadding
    }

    /// Room that must be free on the side the label grows toward: the bubble, less what it tucks into the
    /// icon's own padding (`-gap`), plus a margin for its shadow.
    static func roomNeeded(for text: String, maxWidth: CGFloat, gap: CGFloat) -> CGFloat {
        height(for: text, maxWidth: maxWidth) + gap + shadowMargin
    }

    static let shadowMargin: CGFloat = 4

    /// True when the label should hang below the icon: there is not room for it above, inside what
    /// clips the icon (the scroll view's top edge, else the window's).
    static func showsBelow(roomAbove: CGFloat, text: String, maxWidth: CGFloat, gap: CGFloat) -> Bool {
        roomAbove < roomNeeded(for: text, maxWidth: maxWidth, gap: gap)
    }
}

/// The label itself: the card's fill and border with a soft shadow, in the primary text colour.
struct HoverLabelBubble: View {
    let text: String
    var maxWidth: CGFloat = 240

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 6, style: .continuous)
        Text(verbatim: text)
            .font(.system(size: HoverLabelMetrics.fontSize))
            .foregroundStyle(Color.textPrimary)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, HoverLabelMetrics.horizontalPadding)
            .padding(.vertical, HoverLabelMetrics.verticalPadding)
            .frame(width: HoverLabelMetrics.width(for: text, maxWidth: maxWidth))
            .background(shape.fill(Color.cardFillStrong))
            .overlay(shape.strokeBorder(Color.cardBorder, lineWidth: 1))
            .shadow(color: Color.black.opacity(0.18), radius: 4, x: 0, y: 1)
            // The view it hangs on already has its own spoken label.
            .accessibilityHidden(true)
    }
}

/// Reports whether the pointer is over the view it is laid over, in any window or app state.
///
/// An `NSTrackingArea` with `.activeAlways` (and `.inVisibleRect`, so it follows the view's size),
/// `.mouseEnteredAndExited` and `.mouseMoved`: it reports in a window that is not key and in an app
/// that is not active, which SwiftUI's `onHover` and `onContinuousHover` do not. The view returns nil
/// from `hitTest`, so every click still goes to the button under it. `onChange` is called with true
/// on every move over the view and with false when the pointer leaves, and also with false when the
/// view leaves its window or the window closes or stops being key or main (the pointer cannot be
/// reported as over a view nobody can see, and a tracking area sends no exit for any of those).
/// `onRoomAbove` is told how much room there is above the view inside what clips it.
struct HoverTracker: NSViewRepresentable {
    let onChange: @MainActor (Bool) -> Void
    var onRoomAbove: (@MainActor (CGFloat) -> Void)?

    func makeNSView(context: Context) -> HoverTrackingView {
        let view = HoverTrackingView()
        view.onChange = onChange
        view.onRoomAbove = onRoomAbove
        return view
    }

    func updateNSView(_ view: HoverTrackingView, context: Context) {
        view.onChange = onChange
        view.onRoomAbove = onRoomAbove
    }
}

final class HoverTrackingView: NSView {
    var onChange: (@MainActor (Bool) -> Void)?
    var onRoomAbove: (@MainActor (CGFloat) -> Void)?
    private var area: NSTrackingArea?

    /// Never the target of a click: the button under this view gets it.
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let area { removeTrackingArea(area) }
        let fresh = NSTrackingArea(
            rect: .zero,
            options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways, .inVisibleRect],
            owner: self,
            userInfo: nil)
        addTrackingArea(fresh)
        area = fresh
        // A pointer already over the view (the popover opened under it) sends no entry; ask where it is.
        if let window, window.isVisible {
            let point = convert(window.mouseLocationOutsideOfEventStream, from: nil)
            if bounds.contains(point) { pointerIsOver() }
        }
    }

    override func mouseEntered(with event: NSEvent) { pointerIsOver() }
    override func mouseMoved(with event: NSEvent) { pointerIsOver() }
    override func mouseExited(with event: NSEvent) { onChange?(false) }

    private func pointerIsOver() {
        reportRoomAbove()
        onChange?(true)
    }

    // MARK: - Leaving the window

    override func viewWillMove(toWindow newWindow: NSWindow?) {
        // Going to another window, or to none (the view was removed): the pointer is no longer over
        // it. Told on the next turn of the run loop, not now, because SwiftUI may be updating.
        if window != nil, let report = onChange { Task { @MainActor in report(false) } }
        NotificationCenter.default.removeObserver(self)
        super.viewWillMove(toWindow: newWindow)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window else { return }
        // The window closing, or no longer being key or main, ends the hover: nothing sends an exit for those.
        for name in [NSWindow.willCloseNotification, NSWindow.didResignKeyNotification, NSWindow.didResignMainNotification] {
            NotificationCenter.default.addObserver(self, selector: #selector(windowStoppedShowing(_:)), name: name, object: window)
        }
        reportRoomAbove()
    }

    @objc private func windowStoppedShowing(_ notification: Notification) { onChange?(false) }

    override func layout() {
        super.layout()
        reportRoomAbove()
    }

    // MARK: - Room above

    /// How far this view's top edge is below the top of what clips it: the nearest enclosing scroll
    /// view's visible area (a Settings list ends right above its first row), else the window's content.
    /// Infinite while it is not in a window.
    var roomAbove: CGFloat {
        guard window != nil else { return .infinity }
        var container = superview
        while let view = container, !(view is NSClipView) { container = view.superview }
        let top: CGFloat
        if let clip = container {
            top = clip.convert(clip.bounds, to: nil).maxY
        } else if let content = window?.contentView {
            top = content.convert(content.bounds, to: nil).maxY
        } else {
            return .infinity
        }
        // Window coordinates run upward whatever the views' own flipping, so a larger number is higher up.
        return top - convert(bounds, to: nil).maxY
    }

    private func reportRoomAbove() {
        guard let report = onRoomAbove else { return }
        let room = roomAbove
        // Not now: this can run inside SwiftUI's layout, which must not be asked to change state.
        Task { @MainActor in report(room) }
    }
}

/// Shows `HoverLabelBubble` above the view once the pointer has rested on it for `HoverLabelMetrics.delay`
/// (below it when there is no room above, see `HoverTrackingView.roomAbove`).
///
/// It listens with `HoverTracker` (an always-active tracking area), which reports every move and not
/// only the entry, so a pointer that was already over the icon when the popover opened (no entry
/// event) still gets its label, and which works while the popover is not the key window.
/// Pressing the icon hides the label until the pointer has left (`dismissTrigger` changes), so a bar
/// the press opens is never covered by it; opening any menu does the same. The label also goes when
/// the window closes or stops being key or main, and when the view leaves its window (the tracker
/// reports "outside" for those).
///
/// `onHover` hands the same pointer reports to the caller, so an icon's own highlight follows the
/// tracker too and not SwiftUI's `onHover` (which reports only in the key window of the active app).
///
/// `State` is written out instead of with `@State`, like `SettingsAccountRow`: the offscreen renders
/// compile this file without Xcode's macro plugins. It is the same storage.
struct HoverLabelModifier: ViewModifier {
    let text: String
    var anchor: HoverLabelAnchor = .center
    var maxWidth: CGFloat = 240
    /// Space between the label and the view's edge; negative tucks it into the view's own padding.
    var gap: CGFloat = 0
    /// Change this to hide the label now (the icon was pressed).
    var dismissTrigger = 0
    /// Held open, for the screenshot harnesses: a still has no pointer.
    var alwaysShown = false
    /// Told whether the pointer is over the view, from the same tracking area.
    var onHover: (@MainActor (Bool) -> Void)?

    private let shownStorage = State<Bool>(initialValue: false)
    private let waitStorage = State<Task<Void, Never>?>(initialValue: nil)
    private let quietStorage = State<Bool>(initialValue: false)
    private let roomStorage = State<CGFloat>(initialValue: .infinity)
    private var shown: Bool {
        get { shownStorage.wrappedValue }
        nonmutating set { shownStorage.wrappedValue = newValue }
    }
    private var wait: Task<Void, Never>? {
        get { waitStorage.wrappedValue }
        nonmutating set { waitStorage.wrappedValue = newValue }
    }
    /// The icon was pressed and the pointer has not left since: no label until it does.
    private var quiet: Bool {
        get { quietStorage.wrappedValue }
        nonmutating set { quietStorage.wrappedValue = newValue }
    }
    /// Room above the view inside what clips it, as the tracker last measured it.
    private var roomAbove: CGFloat {
        get { roomStorage.wrappedValue }
        nonmutating set { if roomStorage.wrappedValue != newValue { roomStorage.wrappedValue = newValue } }
    }

    /// Below the view when the label would not fit above it.
    private var below: Bool {
        HoverLabelMetrics.showsBelow(roomAbove: roomAbove, text: text, maxWidth: maxWidth, gap: gap)
    }

    func body(content: Content) -> some View {
        content
            // Over the view, never in front of a click: the tracking view returns nil from hitTest and
            // SwiftUI is told to skip it too.
            .overlay {
                HoverTracker(onChange: { over in
                    onHover?(over)
                    if over {
                        if !quiet { arm() }
                    } else {
                        quiet = false
                        disarm()
                    }
                }, onRoomAbove: { room in roomAbove = room })
                .allowsHitTesting(false)
            }
            .onChange(of: dismissTrigger) { _, _ in
                quiet = true
                disarm()
            }
            .onDisappear {
                quiet = false
                disarm()
            }
            // Switching to another app with the pointer still on the icon sends no exit: hide it anyway.
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
                quiet = false
                disarm()
            }
            // A menu opening (the icon may be a menu itself) covers or follows the label: hide it until the pointer leaves.
            .onReceive(NotificationCenter.default.publisher(for: NSMenu.didBeginTrackingNotification)) { _ in
                quiet = true
                disarm()
            }
            // Above: a zero-height strip along the view's top edge, with the label hung from its bottom
            // edge, so it rises above the view, `gap` clear of it, without a layout guide.
            .overlay(alignment: .top) {
                Color.clear
                    .frame(height: 0)
                    .overlay(alignment: Alignment(horizontal: anchor.horizontal, vertical: .bottom)) {
                        if (shown || alwaysShown) && !text.isEmpty && !below {
                            HoverLabelBubble(text: text, maxWidth: maxWidth)
                                .offset(y: -gap)
                                .allowsHitTesting(false)
                                .transition(.opacity)
                        }
                    }
            }
            // Below: the same, hung from the view's bottom edge, when there is no room above it.
            .overlay(alignment: .bottom) {
                Color.clear
                    .frame(height: 0)
                    .overlay(alignment: Alignment(horizontal: anchor.horizontal, vertical: .top)) {
                        if (shown || alwaysShown) && !text.isEmpty && below {
                            HoverLabelBubble(text: text, maxWidth: maxWidth)
                                .offset(y: gap)
                                .allowsHitTesting(false)
                                .transition(.opacity)
                        }
                    }
            }
            // Over its neighbours while it shows.
            .zIndex(shown || alwaysShown ? 1 : 0)
    }

    /// Starts the pause. Called on every move over the icon; only the first one starts it.
    private func arm() {
        guard !shown, wait == nil else { return }
        wait = Task { @MainActor in
            try? await Task.sleep(for: HoverLabelMetrics.delay)
            guard !Task.isCancelled else { return }
            wait = nil
            withAnimation(.easeOut(duration: 0.12)) { shown = true }
        }
    }

    /// Cancels a pause that has not finished and hides the label.
    private func disarm() {
        wait?.cancel()
        wait = nil
        if shown { withAnimation(.easeIn(duration: 0.08)) { shown = false } }
    }
}

extension View {
    /// A label the app draws over this view after the pointer has rested on it for 0.3 s. Put it on a
    /// plain wrapper around a button, not on the button: a disabled control gets no pointer events.
    /// `onHover` receives the pointer reports of the same tracking area (for an icon's own highlight).
    func hoverLabel(_ text: String, anchor: HoverLabelAnchor = .center, maxWidth: CGFloat = 240, gap: CGFloat = 0,
                    dismissTrigger: Int = 0, alwaysShown: Bool = false, onHover: (@MainActor (Bool) -> Void)? = nil) -> some View {
        modifier(HoverLabelModifier(text: text, anchor: anchor, maxWidth: maxWidth, gap: gap,
                                    dismissTrigger: dismissTrigger, alwaysShown: alwaysShown, onHover: onHover))
    }
}
