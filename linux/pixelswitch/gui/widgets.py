"""Small GTK 4 and libadwaita widgets the settings window is built from (libadwaita 1.5 or newer, no newer API).

The Priority control and the Session and Weekly limit controls are the Mac row's controls (a menu plus a
number for any whole value). They report only what the user did: setting them from a daemon reply never
calls back (``_updating``), and a number typed with the spin button is saved after a short pause, so the
list (which sorts by priority) does not move under the pointer between two clicks. While the pointer is over a
control the save is held (the pause starts over), and it is sent when the pointer leaves or the number loses focus,
as the Mac's priority stepper holds its save while the pointer is over the row.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import model  # noqa: E402

PRIORITY_PAUSE_MS = 800
LIMIT_PAUSE_MS = 600

CSS = """
progressbar.usage trough { min-height: 8px; min-width: 150px; }
progressbar.usage progress { min-height: 8px; }
progressbar.usage-ok progress { background-color: @success_bg_color; }
progressbar.usage-warn progress { background-color: @warning_bg_color; }
progressbar.usage-high progress { background-color: @error_bg_color; }
label.active-badge {
  background-color: alpha(@success_bg_color, 0.2);
  color: @success_color;
  border-radius: 99px;
  padding: 2px 10px;
  font-weight: bold;
  font-size: 0.85em;
}
.account-help { opacity: 0.75; }
"""

_css_loaded = False


def load_css() -> None:
    """Loads the window's small stylesheet once per process."""
    global _css_loaded
    if _css_loaded:
        return
    from gi.repository import Gdk

    provider = Gtk.CssProvider()
    if hasattr(provider, "load_from_string"):
        provider.load_from_string(CSS)
    else:
        provider.load_from_data(CSS.encode("utf-8"))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_loaded = True


class Debouncer:
    """Runs ``callback(*args)`` once, ``delay_ms`` after the last ``trigger``."""

    def __init__(self, delay_ms: int, callback: Callable[..., None]) -> None:
        self._delay = delay_ms
        self._callback = callback
        self._source = 0
        self._args: tuple = ()

    def trigger(self, *args) -> None:
        self._args = args
        if self._source:
            GLib.source_remove(self._source)
        self._source = GLib.timeout_add(self._delay, self._fire)

    def _fire(self) -> bool:
        self._source = 0
        self._callback(*self._args)
        return False

    def flush(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
            self._callback(*self._args)

    def cancel(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

    @property
    def pending(self) -> bool:
        return bool(self._source)


def selected_text(dropdown: Gtk.DropDown) -> str | None:
    item = dropdown.get_selected_item()
    return item.get_string() if item is not None else None


def set_items(dropdown: Gtk.DropDown, strings: list[str], selected: int) -> None:
    """Replaces a drop-down's items only when they changed (so an open menu is not reset), then selects one."""
    model_ = dropdown.get_model()
    current = [model_.get_string(i) for i in range(model_.get_n_items())] if model_ is not None else []
    if current != strings:
        dropdown.set_model(Gtk.StringList.new(strings))
    if dropdown.get_selected() != selected:
        dropdown.set_selected(selected)


class _HoldsWhilePointed:
    """What the number controls share: a pointer over the control holds the pending save back, and leaving it (or the
    number losing keyboard focus) sends it. Needs ``self.spin``, ``self._debounce`` and ``self._commit_now``."""

    def _watch_pointer(self) -> None:
        self._pointer_inside = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._hold_begin())
        motion.connect("leave", lambda *_a: self._hold_end())
        self.add_controller(motion)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_a: self._release())
        self.spin.add_controller(focus)

    def _hold_begin(self) -> None:
        self._pointer_inside = True

    def _hold_end(self) -> None:
        self._pointer_inside = False
        self._release()

    def _release(self) -> None:
        """Sends a pending number now (the pointer left, or focus did)."""
        if self._debounce.pending:
            self._debounce.cancel()
            self._commit_now()

    def _commit(self) -> None:
        """The pause ran out: send the number, unless the pointer is still over the control (then wait another pause)."""
        if self._pointer_inside:
            self._debounce.trigger()
            return
        self._commit_now()

    def flush(self) -> None:
        self._release()


class PriorityControl(_HoldsWhilePointed, Gtk.Box):
    """None or P1 to P9 in a menu, and a number 1 to 99 beside it once there is one."""

    def __init__(self, on_change: Callable[[int | None], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        self._on_change = on_change
        self._updating = False
        self.dropdown = Gtk.DropDown.new_from_strings(["None"])
        self.dropdown.set_valign(Gtk.Align.CENTER)
        self.dropdown.connect("notify::selected", self._menu_chosen)
        self.spin = Gtk.SpinButton.new_with_range(model.PRIORITY_RANGE[0], model.PRIORITY_RANGE[1], 1)
        self.spin.set_valign(Gtk.Align.CENTER)
        self.spin.set_width_chars(3)
        self.spin.set_visible(False)
        self.spin.connect("value-changed", self._spin_changed)
        self._debounce = Debouncer(PRIORITY_PAUSE_MS, self._commit)
        self._pending: int | None = None
        self.append(self.dropdown)
        self.append(self.spin)
        self._watch_pointer()

    def update(self, priority: int | None) -> None:
        self._updating = True
        try:
            numbers = list(model.PRIORITY_MENU)
            if priority is not None and priority not in numbers:
                numbers.append(priority)
            strings = ["None"] + [f"P{n}" for n in numbers]
            shown = self._pending if self._pending is not None else priority
            set_items(self.dropdown, strings, 0 if shown is None else (numbers.index(shown) + 1 if shown in numbers else 0))
            self.spin.set_visible(shown is not None)
            if shown is not None and int(self.spin.get_value()) != shown:
                self.spin.set_value(shown)
        finally:
            self._updating = False

    def _menu_chosen(self, *_args) -> None:
        if self._updating:
            return
        self._debounce.cancel()
        self._pending = None
        text = selected_text(self.dropdown)
        self._on_change(None if text in (None, "None") else int(text[1:]))

    def _spin_changed(self, _spin) -> None:
        if self._updating:
            return
        self._pending = int(self.spin.get_value())
        self._debounce.trigger()

    def _commit_now(self) -> None:
        value, self._pending = self._pending, None
        if value is not None:
            self._on_change(value)


class LimitControl(_HoldsWhilePointed, Gtk.Box):
    """The Session or Weekly menu of one account: Default (N%), Manual only, 10 to 100, and a number."""

    def __init__(self, window: str, on_choice: Callable[[str, float | None], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        self.window = window
        self._on_choice = on_choice
        self._updating = False
        self._numbers: list[int] = list(model.LIMIT_CHOICES)
        self.dropdown = Gtk.DropDown.new_from_strings(["Default"])
        self.dropdown.set_valign(Gtk.Align.CENTER)
        self.dropdown.connect("notify::selected", self._menu_chosen)
        self.spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.spin.set_valign(Gtk.Align.CENTER)
        self.spin.set_width_chars(4)
        self.spin.set_visible(False)
        self.spin.connect("value-changed", self._spin_changed)
        self._debounce = Debouncer(LIMIT_PAUSE_MS, self._commit)
        self._pending: int | None = None
        self.append(self.dropdown)
        self.append(self.spin)
        self._watch_pointer()

    def update(self, info: dict, defaults: tuple[float, float]) -> None:
        self._updating = True
        try:
            own = model.own_limit(info, self.window)
            own_whole = None if own is None else int(model.round_half_away(own))
            numbers = sorted(set(model.LIMIT_CHOICES) | ({own_whole} if own_whole is not None else set()))
            self._numbers = numbers
            default_value = int(model.round_half_away(defaults[0 if self.window == "session" else 1]))
            strings = [f"Default ({default_value}%)", "Manual only"] + [f"{n}%" for n in numbers]
            if info.get("manualOnly"):
                selected = 1
            elif own_whole is None:
                selected = 0
            else:
                selected = numbers.index(own_whole) + 2
            set_items(self.dropdown, strings, selected)
            shown_spin = own_whole is not None and not info.get("manualOnly")
            self.spin.set_visible(shown_spin)
            if shown_spin and self._pending is None and int(self.spin.get_value()) != own_whole:
                self.spin.set_value(own_whole)
        finally:
            self._updating = False

    def _menu_chosen(self, *_args) -> None:
        if self._updating:
            return
        self._debounce.cancel()
        self._pending = None
        index = self.dropdown.get_selected()
        if index == 0:
            self._on_choice("default", None)
        elif index == 1:
            self._on_choice("manual", None)
        else:
            self._on_choice("percent", float(self._numbers[index - 2]))

    def _spin_changed(self, _spin) -> None:
        if self._updating:
            return
        self._pending = int(self.spin.get_value())
        self._debounce.trigger()

    def _commit_now(self) -> None:
        value, self._pending = self._pending, None
        if value is not None:
            self._on_choice("percent", float(value))


def usage_bar() -> Gtk.ProgressBar:
    bar = Gtk.ProgressBar()
    bar.add_css_class("usage")
    bar.add_css_class("usage-ok")
    bar.set_valign(Gtk.Align.CENTER)
    bar.set_size_request(150, -1)
    return bar


def set_usage(bar: Gtk.ProgressBar, percent: float | None) -> None:
    for tone in ("usage-ok", "usage-warn", "usage-high"):
        bar.remove_css_class(tone)
    bar.add_css_class("usage-" + model.usage_tone(percent))
    bar.set_fraction(0.0 if percent is None else max(0.0, min(1.0, percent / 100.0)))
    bar.set_visible(percent is not None)


def plain_row(title: str, subtitle: str | None = None) -> Adw.ActionRow:
    """An action row whose text is shown as written (no markup)."""
    row = Adw.ActionRow()
    row.set_use_markup(False)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def caption(text: str) -> Gtk.Label:
    """A wrapped, dimmed line of small text under a group."""
    label = Gtk.Label(label=text, wrap=True, xalign=0.0)
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    label.set_margin_top(6)
    label.set_margin_start(4)
    label.set_margin_end(4)
    return label


def confirm_dialog(parent: Gtk.Window, heading: str, body: str, confirm_label: str, on_confirm: Callable[[], None],
                   *, destructive: bool = True) -> None:
    """A confirmation: ``Adw.AlertDialog`` (libadwaita 1.5) where it exists, else ``Adw.MessageDialog``."""
    appearance = Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED
    if hasattr(Adw, "AlertDialog"):
        dialog = Adw.AlertDialog.new(heading, body)
        shown = lambda: dialog.present(parent)  # noqa: E731
    else:
        dialog = Adw.MessageDialog.new(parent, heading, body)
        shown = dialog.present
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", confirm_label)
    dialog.set_response_appearance("confirm", appearance)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def responded(_dialog, response: str) -> None:
        if response == "confirm":
            on_confirm()

    dialog.connect("response", responded)
    shown()
