"""``pixelswitch-settings``: the settings window (GTK 4 and libadwaita 1.5 or newer; spec 5.1).

Four pages in a header view switcher: Accounts, General, Sign in and About. Everything the window shows comes
from the daemon over its socket (``events.subscribe``, so it follows changes made elsewhere, by the CLI or the
tray), and every change it makes is a control call. Rules (spec 5.1): the window never sends ``usage.refresh``
by itself (only Retry does), never execs ``pixelswitchd`` (its banner starts the service with
``systemctl --user start pixelswitch.service`` and nothing else), and nothing opens a browser unless Open is
pressed (``Gio.AppInfo.launch_default_for_uri``).

A second launch raises the window that is already open (the application id is unique), and
``--page accounts|general|sign-in|about`` picks the page.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Callable, Mapping

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import version as version_module  # noqa: E402
from ..cli import Output  # noqa: E402
from ..paths import Paths  # noqa: E402
from . import model  # noqa: E402
from .link import DaemonLink  # noqa: E402
from .settings_accounts import AccountsPage  # noqa: E402
from .widgets import Debouncer, caption, load_css, plain_row  # noqa: E402

APP_ID = "ai.pixelventures.pixelswitch.settings"
PAGES = ("accounts", "general", "sign-in", "about")
SETTING_PAUSE_MS = 500


def error_text(error: Exception) -> str:
    return getattr(error, "message", None) or getattr(error, "description", None) or str(error)


# --- General -----------------------------------------------------------------------------------------------


class GeneralPage(Adw.PreferencesPage):
    def __init__(self, window: "SettingsWindow") -> None:
        super().__init__()
        self.window = window
        self.set_title("General")
        self.set_icon_name("preferences-system-symbolic")
        self._updating = False

        # The switch and the two defaults are one group whose description is the overflow sentence (a caption added
        # with group.add() lands after the whole list, so it would sit under the strategy picker instead).
        defaults = Adw.PreferencesGroup()
        defaults.set_title("Auto-switch")
        defaults.set_description(model.TEXT_OVERFLOW)
        self.defaults_group = defaults
        auto = defaults
        self.enabled = Adw.SwitchRow()
        self.enabled.set_title("Switch account before hitting the limit")
        self.enabled.set_use_markup(False)
        self.enabled.connect("notify::active", lambda *_a: self._set("autoSwitch.enabled", self.enabled.get_active()))
        auto.add(self.enabled)

        self.session_default = self._percent_row("Session (5-hour) default", "autoSwitch.sessionThreshold")
        self.weekly_default = self._percent_row("Weekly default", "autoSwitch.weeklyThreshold")
        auto.add(self.session_default)
        auto.add(self.weekly_default)
        auto.add(caption("Any account can have its own threshold in Settings → Accounts, from 1% to 100%, or be Manual only so "
                         "auto-switch never moves you to it. 100% uses an account until it is empty."))
        self.add(defaults)

        auto = Adw.PreferencesGroup()

        self.on_fable = Adw.SwitchRow()
        self.on_fable.set_title("Also switch when Fable runs out")
        self.on_fable.set_subtitle(model.TEXT_FABLE)
        self.on_fable.set_use_markup(False)
        self.on_fable.connect("notify::active", lambda *_a: self._set("autoSwitch.onFable", self.on_fable.get_active()))
        auto.add(self.on_fable)

        self.strategy = Adw.ComboRow()
        self.strategy.set_title("Choose the next account by")
        self.strategy.set_use_markup(False)
        self.strategy.set_model(Gtk.StringList.new([label for _, label in model.STRATEGIES]))
        self.strategy.connect("notify::selected", self._strategy_chosen)
        auto.add(self.strategy)
        # The D6 line, with its button, right under the strategy it is about: numbers do nothing unless this chooses Priority.
        self.priority_note = plain_row(model.TEXT_PRIORITY_NOTE)
        self.priority_note.set_title_lines(0)
        use_priority = Gtk.Button(label="Use priority")
        use_priority.set_valign(Gtk.Align.CENTER)
        use_priority.connect("clicked", lambda _b: self.window.run("settings.set", {"key": "autoSwitch.strategy", "value": "priority"}))
        self.priority_note.add_suffix(use_priority)
        auto.add(self.priority_note)

        self.drain_early = Adw.SwitchRow()
        self.drain_early.set_title("Switch early to use quota before it resets")
        self.drain_early.set_use_markup(False)
        self.drain_early.connect("notify::active", lambda *_a: self._set("autoSwitch.drainEarly", self.drain_early.get_active()))
        auto.add(self.drain_early)
        self.drain_hours = Adw.SpinRow.new_with_range(model.DRAIN_HOURS_RANGE[0], model.DRAIN_HOURS_RANGE[1], 1)
        self.drain_hours.set_title("Within this many hours")
        self.drain_hours.set_subtitle("Switches before the active account reaches its threshold when another account's weekly quota "
                                      "resets within this many hours, sooner than the active account's, and that account still has room.")
        self.drain_hours.set_use_markup(False)
        self._drain_debounce = Debouncer(SETTING_PAUSE_MS, lambda: self._set("autoSwitch.drainWithinHours", int(self.drain_hours.get_value())))
        self.drain_hours.connect("notify::value", lambda *_a: None if self._updating else self._drain_debounce.trigger())
        auto.add(self.drain_hours)
        self.how_it_works = caption(
            "When the active account's 5-hour, weekly or Fable usage reaches its threshold, PixelSwitch switches to another account "
            "that is under its own threshold (at least a point under) and is not Manual only. Turn off the Fable switch above to "
            "leave Fable as a reading only, while the 5-hour and weekly limits keep switching. Checked on every refresh; a 5-minute "
            "cooldown prevents rapid flip-flopping.")
        auto.add(self.how_it_works)
        auto.add(caption("If the account you're using has no active subscription, PixelSwitch moves you right away to another "
                         "account that still has room and is not Manual only."))
        self.add(auto)

        refresh = Adw.PreferencesGroup()
        refresh.set_title("Refresh")
        self.interval = Adw.ComboRow()
        self.interval.set_title("Auto-refresh interval")
        self.interval.set_use_markup(False)
        self.interval.set_model(Gtk.StringList.new([label for _, label in model.REFRESH_CHOICES]))
        self.interval.connect("notify::selected", self._interval_chosen)
        refresh.add(self.interval)
        self.add(refresh)

        self._rows_for_auto = (self.session_default, self.weekly_default, self.on_fable, self.strategy, self.priority_note,
                               self.drain_early, self.drain_hours)

    def flush(self) -> None:
        """Sends any number typed in the last half second (the window is closing)."""
        self.session_default.debounce.flush()
        self.weekly_default.debounce.flush()
        self._drain_debounce.flush()

    def _percent_row(self, title: str, key: str) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(model.DEFAULT_RANGE[0], model.DEFAULT_RANGE[1], 1)
        row.set_title(title)
        row.set_subtitle("Percent used")
        row.set_use_markup(False)
        row.debounce = Debouncer(SETTING_PAUSE_MS, lambda: self._set(key, int(row.get_value())))
        row.connect("notify::value", lambda *_a: None if self._updating else row.debounce.trigger())
        return row

    def _set(self, key: str, value: Any) -> None:
        if not self._updating:
            self.window.run("settings.set", {"key": key, "value": value})

    def _strategy_chosen(self, *_args) -> None:
        if self._updating:
            return
        index = self.strategy.get_selected()
        value = model.STRATEGIES[index][0]
        self._set("autoSwitch.strategy", value)

    def _interval_chosen(self, *_args) -> None:
        if not self._updating:
            self._set("refreshInterval", model.REFRESH_CHOICES[self.interval.get_selected()][0])

    def update(self, snapshot: model.Snapshot) -> None:
        settings = snapshot.settings
        self._updating = True
        try:
            enabled = bool(settings.get("autoSwitch.enabled", snapshot.auto_switch_on))
            self.enabled.set_active(enabled)
            session, weekly = snapshot.defaults
            session = float(settings.get("autoSwitch.sessionThreshold", session))
            weekly = float(settings.get("autoSwitch.weeklyThreshold", weekly))
            if not self.session_default.debounce.pending:
                self.session_default.set_value(session)
            if not self.weekly_default.debounce.pending:
                self.weekly_default.set_value(weekly)
            self.on_fable.set_active(bool(settings.get("autoSwitch.onFable", snapshot.auto_switch.get("onFable", True))))
            strategy = settings.get("autoSwitch.strategy", snapshot.strategy)
            keys = [key for key, _ in model.STRATEGIES]
            self.strategy.set_selected(keys.index(strategy) if strategy in keys else 0)
            self.strategy.set_subtitle(model.STRATEGY_HELP.get(strategy, ""))
            self.priority_note.set_visible(model.priority_note_visible(strategy, snapshot.accounts))
            early = bool(settings.get("autoSwitch.drainEarly", snapshot.auto_switch.get("drainEarly", True)))
            self.drain_early.set_active(early)
            hours = settings.get("autoSwitch.drainWithinHours", snapshot.auto_switch.get("drainWithinHours", 24))
            if not self._drain_debounce.pending:
                self.drain_hours.set_value(float(hours))
            soonest = strategy == "resetsSoonest"
            self.drain_early.set_visible(soonest)
            self.drain_hours.set_visible(soonest and early)
            interval = settings.get("refreshInterval", 300)
            seconds = [s for s, _ in model.REFRESH_CHOICES]
            self.interval.set_selected(seconds.index(interval) if interval in seconds else 3)
            for row in self._rows_for_auto:
                row.set_sensitive(enabled or row is self.enabled)
        finally:
            self._updating = False


# --- Sign in ------------------------------------------------------------------------------------------------


class SignInPage(Adw.PreferencesPage):
    def __init__(self, window: "SettingsWindow") -> None:
        super().__init__()
        self.window = window
        self.set_title("Sign in")
        self.set_icon_name("list-add-symbolic")
        self._session: dict | None = None

        start = Adw.PreferencesGroup()
        start.set_title("Sign in another account")
        start.set_description("Claude Code gives two links. Nothing opens a browser until you press Open.")
        self.start_row = plain_row("Add a new account", "You sign in to Anthropic in a browser; PixelSwitch saves the login.")
        self.start_row.set_subtitle_lines(0)
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.start_row.add_suffix(self.spinner)
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.set_valign(Gtk.Align.CENTER)
        self.cancel_button.connect("clicked", lambda _b: window.run("accounts.signIn.cancel", None))
        self.start_row.add_suffix(self.cancel_button)
        self.start_button = Gtk.Button(label="Start")
        self.start_button.add_css_class("suggested-action")
        self.start_button.set_valign(Gtk.Align.CENTER)
        self.start_button.connect("clicked", lambda _b: self.begin(None))
        self.start_row.add_suffix(self.start_button)
        start.add(self.start_row)
        self.status_row = plain_row("")
        self.status_row.set_title_lines(0)
        self.status_row.set_subtitle_lines(0)
        start.add(self.status_row)
        self.add(start)

        self.links = Adw.PreferencesGroup()
        self.links.set_title("Links")
        self.auto_row = self._link_row("On this computer", "Open it in any browser on this computer. It finishes by itself.")
        self.manual_row = self._link_row("On another device",
                                         "Open it on the other device and sign in. The page then shows a code: paste it below.")
        self.links.add(self.auto_row)
        self.links.add(self.manual_row)
        self.code_row = Adw.EntryRow()
        self.code_row.set_title("Code from the sign-in page")
        self.code_row.set_show_apply_button(True)
        self.code_row.connect("apply", self._code_applied)
        self.links.add(self.code_row)
        self.code_hint = caption("Copy all of the code, including the part after #.")
        self.links.add(self.code_hint)
        self.add(self.links)
        self.links.set_visible(False)

    def _link_row(self, title: str, subtitle: str) -> Adw.ActionRow:
        row = plain_row(title, subtitle)
        row.set_subtitle_lines(0)
        copy = Gtk.Button(label="Copy")
        copy.set_valign(Gtk.Align.CENTER)
        copy.connect("clicked", lambda _b, row=row: self.window.copy_text(getattr(row, "link", ""), "Link copied"))
        open_button = Gtk.Button(label="Open")
        open_button.add_css_class("suggested-action")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.connect("clicked", lambda _b, row=row: self.window.open_uri(getattr(row, "link", "")))
        row.add_suffix(copy)
        row.add_suffix(open_button)
        row.link = ""
        return row

    def begin(self, account: str | None, email: str | None = None) -> None:
        params: dict[str, Any] = {}
        if account is not None:
            params["account"] = account
        self.window.run("accounts.signIn.start", params or None)

    def _code_applied(self, _row) -> None:
        code = self.code_row.get_text().strip()
        if code:
            self.window.run("accounts.signIn.submitCode", {"code": code})

    def update(self, snapshot: model.Snapshot) -> None:
        session = snapshot.sign_in
        self._session = session
        active = session is not None and session.get("state") in ("starting", "waitingForUser", "completing")
        self.start_button.set_visible(not active)
        self.cancel_button.set_visible(active)
        self.spinner.set_visible(active)
        self.spinner.set_spinning(active)
        if session is None:
            self.status_row.set_visible(False)
            self.links.set_visible(False)
            return
        words = {
            "starting": "Getting the sign-in link…",
            "waitingForUser": "Checking the code…" if session.get("codeSubmitted") else "Waiting for you to sign in in a browser…",
            "completing": "Saving the account…",
            "succeeded": "Signed in.",
            "failed": session.get("message") or "The sign-in failed.",
            "cancelled": "Cancelled. Nothing was changed.",
        }.get(session.get("state", ""), Output.state_words(session) if session.get("state") else "")
        what = f"Re-sign {session.get('email') or 'account'}" if session.get("purpose") == "reauthenticate" else "New account"
        self.status_row.set_title(what)
        self.status_row.set_subtitle(words + (f"\n{session['notice']}" if session.get("notice") else ""))
        self.status_row.set_visible(True)
        automatic, manual = session.get("automaticLink"), session.get("manualLink")
        self.auto_row.link = automatic or ""
        self.manual_row.link = manual or ""
        self.auto_row.set_visible(bool(automatic))
        self.manual_row.set_visible(bool(manual))
        self.code_row.set_visible(bool(manual) and active)
        self.code_hint.set_visible(bool(manual) and active)
        self.links.set_visible(bool(automatic or manual) and active)


# --- About ----------------------------------------------------------------------------------------------------


class AboutPage(Adw.PreferencesPage):
    def __init__(self, window: "SettingsWindow") -> None:
        super().__init__()
        self.window = window
        self.set_title("About")
        self.set_icon_name("help-about-symbolic")
        self._updating = False
        paths = Paths.from_env(os.environ)

        head = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name("pixelswitch")
        image.set_pixel_size(96)
        box.append(image)
        title = Gtk.Label(label="PixelSwitch")
        title.add_css_class("title-1")
        box.append(title)
        subtitle = Gtk.Label(label="Claude Code Account Switcher")
        subtitle.add_css_class("dim-label")
        box.append(subtitle)
        head.add(box)
        self.add(head)

        info = Adw.PreferencesGroup()
        try:
            own = version_module.read_version()
            own_text = f"{own.version} (build {own.build})"
        except version_module.VersionUnavailable:
            own_text = "unknown"
        self.version_row = plain_row("Version", own_text)
        self.version_row.set_subtitle_selectable(True)
        info.add(self.version_row)
        self.daemon_row = plain_row("Background service", "not running")
        info.add(self.daemon_row)
        self.log_row = plain_row("Log file", paths.log)
        self.log_row.set_subtitle_selectable(True)
        copy = Gtk.Button(label="Copy path")
        copy.set_valign(Gtk.Align.CENTER)
        copy.connect("clicked", lambda _b: window.copy_text(paths.log, "Path copied"))
        self.log_row.add_suffix(copy)
        info.add(self.log_row)
        self.add(info)

        updates = Adw.PreferencesGroup()
        updates.set_title("Updates")
        self.automatic = Adw.SwitchRow()
        self.automatic.set_title("Update automatically")
        self.automatic.set_subtitle("Checks every 6 hours and installs new versions by itself, restarting PixelSwitch when no switch "
                                    "or sign-in is running. Off: it only installs when you check.")
        self.automatic.set_use_markup(False)
        self.automatic.connect("notify::active", self._automatic_toggled)
        updates.add(self.automatic)
        check_row = plain_row("Check for updates", "Looks for a newer version now.")
        check = Gtk.Button(label="Check now")
        check.set_valign(Gtk.Align.CENTER)
        check.connect("clicked", lambda _b: window.run("app.checkForUpdates", None, ok=lambda _r: window.toast("Checking for updates…")))
        check_row.add_suffix(check)
        updates.add(check_row)
        self.add(updates)

        credit = Adw.PreferencesGroup()
        credit.add(caption("© 2026 Pixel Ventures. Includes code © Xueshi Qiao and contributors."))
        self.add(credit)

    def _automatic_toggled(self, *_args) -> None:
        if not self._updating:
            self.window.run("settings.set", {"key": "updates.automatic", "value": self.automatic.get_active()})

    def mark_stopped(self) -> None:
        """The daemon is not answering: say so, and leave the rest as it was last read."""
        self.daemon_row.set_subtitle("not running")

    def update(self, snapshot: model.Snapshot) -> None:
        self._updating = True
        try:
            self.automatic.set_active(bool(snapshot.settings.get("updates.automatic", True)))
            version = snapshot.app_version
            self.daemon_row.set_subtitle(f"version {version}" if snapshot.connected and version else "not running")
        finally:
            self._updating = False


# --- The window -----------------------------------------------------------------------------------------------


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, link, *, env: Mapping[str, str] | None = None) -> None:
        super().__init__(application=app)
        self.env = os.environ if env is None else env
        self.link = link
        self.snapshot = model.Snapshot()
        self.set_title("PixelSwitch Settings")
        self.set_default_size(820, 860)
        load_css()

        self.accounts_page = AccountsPage(self)
        self.general_page = GeneralPage(self)
        self.sign_in_page = SignInPage(self)
        self.about_page = AboutPage(self)
        self.stack = Adw.ViewStack()
        for name, page, title, icon in (
            ("accounts", self.accounts_page, "Accounts", "system-users-symbolic"),
            ("general", self.general_page, "General", "preferences-system-symbolic"),
            ("sign-in", self.sign_in_page, "Sign in", "list-add-symbolic"),
            ("about", self.about_page, "About", "help-about-symbolic"),
        ):
            self.stack.add_titled_with_icon(page, name, title, icon)

        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        self.banner = Adw.Banner()
        self.banner.set_title("PixelSwitch is not running.")
        self.banner.set_button_label("Start PixelSwitch")
        self.banner.connect("button-clicked", lambda _b: self.start_service())
        self.banner.set_revealed(False)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        view.set_content(self.stack)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        self.connect("close-request", self._closing)
        # "Updated N ago" counts up by itself (D15); no socket call is needed to redraw it.
        self._tick_source = GLib.timeout_add_seconds(30, self._tick)
        self.connect("destroy", self._destroyed)

    # --- the daemon -> the window ----------------------------------------------------------------------------

    def apply_snapshot(self, snapshot: model.Snapshot) -> None:
        self.banner.set_revealed(not snapshot.connected)
        if not snapshot.connected:
            # Keep the last connected state on screen, greyed out. Drawing the empty disconnected snapshot would tear
            # down every card, show "No Accounts" with a live Add button and invent defaults, on every daemon restart
            # (each auto-update included).
            self.banner.set_title("PixelSwitch is not running. " + snapshot.error if snapshot.error else "PixelSwitch is not running.")
            self.stack.set_sensitive(False)
            self.about_page.mark_stopped()
            return
        self.snapshot = snapshot
        self.stack.set_sensitive(True)
        self.banner.set_title("PixelSwitch is not running.")
        self.accounts_page.update(snapshot)
        self.general_page.update(snapshot)
        self.sign_in_page.update(snapshot)
        self.about_page.update(snapshot)

    # --- the user -> the daemon ------------------------------------------------------------------------------------

    def run(self, method: str, params: Any = None, ok: Callable[[Any], None] | None = None, timeout: float = 30.0) -> None:
        """A control call. A failure is a toast with the daemon's own sentence; either way the window reads again."""
        def done(result: Any, error: Exception | None) -> None:
            if error is not None:
                self.toast(error_text(error))
            elif ok is not None:
                ok(result)
            self.link.request_snapshot()

        self.link.call(method, params, done, timeout)

    def toast(self, text: str) -> None:
        toast = Adw.Toast.new(text)
        toast.set_timeout(5)
        self.toasts.add_toast(toast)

    def show_page(self, name: str) -> None:
        if name in PAGES:
            self.stack.set_visible_child_name(name)

    def start_resign(self, info: Mapping) -> None:
        self.show_page("sign-in")
        self.sign_in_page.begin(info["id"], info.get("email"))

    def start_service(self) -> None:
        """The only way the window starts the daemon: ask systemd."""
        try:
            subprocess.Popen(["systemctl", "--user", "start", "pixelswitch.service"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.toast("systemd could not be run to start the service.")
            return
        GLib.timeout_add(1500, lambda: self.link.request_snapshot() or False)

    def copy_text(self, text: str, message: str) -> None:
        if not text:
            return
        clipboard = self.get_clipboard()
        if hasattr(clipboard, "set_text"):
            clipboard.set_text(text)
        else:
            clipboard.set(text)
        self.toast(message)

    def open_uri(self, uri: str) -> None:
        """Only here, only when Open is pressed."""
        if not uri:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            self.toast("The link could not be opened. Use Copy and paste it into a browser.")

    def _tick(self) -> bool:
        if self.snapshot.connected:
            self.accounts_page.update(self.snapshot)
        return True

    def _destroyed(self, *_args) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0

    def _closing(self, *_args) -> bool:
        self.accounts_page.flush()
        self.general_page.flush()
        return False


# --- The application ---------------------------------------------------------------------------------------------


class SettingsApp(Adw.Application):
    def __init__(self, env: Mapping[str, str] | None = None, link_factory: Callable[["SettingsApp"], Any] | None = None) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.env = os.environ if env is None else env
        self.link_factory = link_factory
        self.window: SettingsWindow | None = None
        self.link = None
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Page to show: accounts, general, sign-in or about", "NAME")

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict().end().unpack()
        self.activate()
        page = options.get("page")
        if page and self.window is not None:
            self.window.show_page(page)
        return 0

    def do_activate(self) -> None:
        if self.window is None:
            if self.link_factory is not None:
                self.link = self.link_factory(self)
            else:
                self.link = DaemonLink(lambda fn: GLib.idle_add(lambda: fn() or False), self._on_snapshot, env=self.env, want_settings=True)
            self.window = SettingsWindow(self, self.link, env=self.env)
            self.link.start()
        self.window.present()

    def _on_snapshot(self, snapshot: model.Snapshot) -> None:
        if self.window is not None:
            self.window.apply_snapshot(snapshot)

    def do_shutdown(self) -> None:
        if self.link is not None:
            self.link.stop()
        Adw.Application.do_shutdown(self)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    os.umask(0o077)
    app = SettingsApp(env)
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
