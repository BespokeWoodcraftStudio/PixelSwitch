"""The settings window's widgets against fixture data and a stand-in link: what each page shows, and exactly which
control call each control makes. Needs PyGObject with GTK 4 and libadwaita and a display (CI and the laptop run
the suite under xvfb-run); skipped otherwise. Nothing here opens a browser, a socket or a real window."""
from __future__ import annotations

import time
import unittest

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk

    HAVE_DISPLAY = Gtk.is_initialized() or Gtk.init_check()
except (ImportError, ValueError):
    HAVE_DISPLAY = False

from . import gui_fixtures as fx

A, B, C = (a["id"] for a in fx.fixture_accounts())


def pump(milliseconds: int = 50) -> None:
    deadline = time.monotonic() + milliseconds / 1000.0
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.iteration(False):
            pass
        time.sleep(0.005)


@unittest.skipUnless(HAVE_DISPLAY, "needs PyGObject, GTK 4, libadwaita and a display (run under xvfb-run)")
class WindowTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        from pixelswitch.gui import settings

        cls.settings = settings
        cls.app = Adw.Application(application_id="ai.pixelventures.pixelswitch.test", flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def make(self, snapshot=None, results=None):
        holder = {}
        link = fx.FixtureLink(lambda fn: fn(), lambda s: holder["w"].apply_snapshot(s), snapshot or fx.fixture_snapshot(), results)
        window = self.settings.SettingsWindow(self.app, link)
        holder["w"] = window
        self.addCleanup(window.destroy)
        link.start()
        pump()
        return window, link

    def card(self, window, account_id):
        return window.accounts_page._cards[account_id]

    # -- what the pages show --

    def test_the_cards_show_the_accounts_in_order_with_their_state(self):
        window, link = self.make()
        page = window.accounts_page
        self.assertEqual(page._order, [A, B, C])
        a, b, c = (self.card(window, i) for i in (A, B, C))
        self.assertEqual(a.get_title(), "Personal")
        self.assertIn("ahamade@gmail.com", a.get_description())
        self.assertIn("Updated 10 minutes ago", b.get_description())
        self.assertTrue(a.badge.get_visible())
        self.assertFalse(b.badge.get_visible())
        self.assertFalse(a.switch_button.get_visible())
        self.assertTrue(b.switch_button.get_visible())
        self.assertFalse(c.switch_button.get_visible())  # not switchable until it is re-signed
        self.assertTrue(a.fable_row.get_visible())
        self.assertFalse(b.fable_row.get_visible())
        self.assertFalse(a.message_row.get_visible())
        self.assertTrue(b.retry_button.get_visible() and not b.resign_button.get_visible())
        self.assertTrue(c.resign_button.get_visible() and not c.retry_button.get_visible())
        self.assertIn("42% used", a.session_row.get_subtitle())
        self.assertIn("resets in 3 h", a.session_row.get_subtitle())
        self.assertEqual(c.session_row.get_subtitle(), "No reading yet.")
        self.assertEqual(a.session_limit_row.get_subtitle(), "Default (90%)")
        self.assertEqual(b.weekly_limit_row.get_subtitle(), "Switch at 50%")
        self.assertEqual(c.session_limit_row.get_subtitle(), "Manual only")
        self.assertIn("moves you to it only while it is at 89% or less.", a.help_label.get_label())
        self.assertIn("(up to 89% of its week", b.help_label.get_label())
        self.assertEqual(a.label_row.get_text(), "Personal")
        self.assertEqual(link.calls, [])  # building and showing it sent nothing, usage.refresh least of all

    def test_the_priority_and_limit_controls_show_the_account_values(self):
        window, _ = self.make()
        a, b, c = (self.card(window, i) for i in (A, B, C))
        self.assertEqual((a.priority_control.dropdown.get_selected(), a.priority_control.spin.get_visible()), (1, True))
        self.assertEqual((c.priority_control.dropdown.get_selected(), c.priority_control.spin.get_visible()), (0, False))
        self.assertEqual(b.priority_control.spin.get_value(), 3)
        self.assertEqual(a.session_limit.dropdown.get_selected(), 0)
        self.assertEqual(b.session_limit.dropdown.get_selected(), 2 + b.session_limit._numbers.index(90))
        self.assertEqual(b.session_limit.spin.get_value(), 90)
        self.assertEqual(c.weekly_limit.dropdown.get_selected(), 1)
        self.assertFalse(c.weekly_limit.spin.get_visible())

    def test_a_priority_above_nine_and_a_limit_off_the_menu_are_shown(self):
        snap = fx.fixture_snapshot()
        snap.accounts[0].update(priority=23, threshold=73, effectiveThreshold=73, effectiveSessionThreshold=73, effectiveWeeklyThreshold=73)
        window, link = self.make(snap)
        a = self.card(window, A)
        self.assertEqual(a.priority_control.dropdown.get_selected(), 10)
        self.assertEqual(a.priority_control.spin.get_value(), 23)
        self.assertEqual(a.session_limit.spin.get_value(), 73)
        self.assertIn(73, a.session_limit._numbers)
        self.assertEqual(link.calls, [])

    def test_general_shows_the_settings_and_sends_nothing(self):
        window, link = self.make()
        g = window.general_page
        self.assertTrue(g.enabled.get_active())
        self.assertEqual((g.session_default.get_value(), g.weekly_default.get_value()), (90, 50))
        self.assertEqual(g.strategy.get_selected(), 0)
        self.assertFalse(g.drain_early.get_visible())
        self.assertEqual(g.interval.get_selected(), 3)
        self.assertEqual(g.on_fable.get_subtitle(), "Fable uses the weekly limit.")
        self.assertEqual(link.calls, [])

    def test_the_early_switching_rows_show_only_for_resets_soonest(self):
        snap = fx.fixture_snapshot()
        snap.settings["autoSwitch.strategy"] = "resetsSoonest"
        window, _ = self.make(snap)
        self.assertTrue(window.general_page.drain_early.get_visible())
        self.assertTrue(window.general_page.drain_hours.get_visible())
        self.assertEqual(window.general_page.drain_hours.get_value(), 24)

    def test_the_priority_line_and_use_priority(self):
        window, link = self.make()
        page = window.accounts_page
        self.assertTrue(page.use_priority_row.get_visible())
        self.assertFalse(page.warning_row.get_visible())
        snap = fx.fixture_snapshot()
        snap.status["autoSwitch"]["strategy"] = "myOrder"
        window.apply_snapshot(snap)
        self.assertFalse(page.use_priority_row.get_visible())

    # -- what each control sends --

    def test_limit_menus(self):
        window, link = self.make()
        a, b, c = (self.card(window, i) for i in (A, B, C))
        a.session_limit.dropdown.set_selected(1)  # Manual only
        self.assertEqual(link.last("accounts.setThreshold"), {"account": A, "threshold": 0})
        a.weekly_limit.dropdown.set_selected(2 + a.weekly_limit._numbers.index(70))
        self.assertEqual(link.last("accounts.setThresholds"), {"account": A, "weekly": 70.0})
        b.session_limit.dropdown.set_selected(0)  # Default
        self.assertEqual(link.last("accounts.setThresholds"), {"account": B, "session": None})
        c.session_limit.dropdown.set_selected(0)  # Default on a Manual only account clears Manual only
        self.assertEqual(link.last("accounts.setThreshold"), {"account": C, "threshold": None})
        self.assertGreaterEqual(link.requested, 4)  # each change asks for a fresh read, never usage.refresh
        self.assertNotIn("usage.refresh", [m for m, _ in link.calls])

    def test_a_number_typed_in_the_spin_is_saved_after_a_pause(self):
        window, link = self.make()
        b = self.card(window, B)
        b.session_limit.spin.set_value(77)
        pump(200)
        self.assertIsNone(link.last("accounts.setThresholds"))
        pump(900)
        self.assertEqual(link.last("accounts.setThresholds"), {"account": B, "session": 77.0})
        self.assertEqual(len(link.calls), 1)

    def test_priority_menu_and_number(self):
        window, link = self.make()
        a, c = self.card(window, A), self.card(window, C)
        a.priority_control.dropdown.set_selected(0)
        self.assertEqual(link.last("accounts.setPriority"), {"account": A, "priority": None})
        c.priority_control.dropdown.set_selected(5)
        self.assertEqual(link.last("accounts.setPriority"), {"account": C, "priority": 5})
        b = self.card(window, B)
        b.priority_control.spin.set_value(4)
        b.priority_control.spin.set_value(5)
        pump(1200)
        self.assertEqual(link.last("accounts.setPriority"), {"account": B, "priority": 5})
        self.assertEqual([p for m, p in link.calls if m == "accounts.setPriority" and p["account"] == B], [{"account": B, "priority": 5}])

    def test_buttons(self):
        window, link = self.make()
        a, b, c = (self.card(window, i) for i in (A, B, C))
        b.switch_button.emit("clicked")
        self.assertEqual(link.last("accounts.switch"), {"account": B})
        b.retry_button.emit("clicked")
        self.assertEqual(link.last("usage.refresh"), None)
        self.assertIn("usage.refresh", [m for m, _ in link.calls])
        c.resign_button.emit("clicked")
        self.assertEqual(window.stack.get_visible_child_name(), "sign-in")
        self.assertEqual(link.last("accounts.signIn.start"), {"account": C})

    def test_remove_asks_first(self):
        from pixelswitch.gui import settings_accounts

        window, link = self.make()
        asked = []
        original = settings_accounts.confirm_dialog
        settings_accounts.confirm_dialog = lambda parent, heading, body, label, on_confirm, **kw: asked.append((heading, label, on_confirm))
        self.addCleanup(setattr, settings_accounts, "confirm_dialog", original)
        self.card(window, B).remove_button.emit("clicked")
        self.assertEqual(link.calls, [])
        self.assertEqual(asked[0][:2], ("Remove ahmed@pixelventures.ai?", "Remove"))
        asked[0][2]()
        self.assertEqual(link.last("accounts.remove"), {"account": B})

    def test_the_real_confirmation_dialog_builds(self):
        from pixelswitch.gui import widgets

        window, _ = self.make()
        confirmed = []
        widgets.confirm_dialog(window, "Remove x?", "body", "Remove", lambda: confirmed.append(1))
        pump(100)
        self.assertEqual(confirmed, [])

    def test_label(self):
        window, link = self.make()
        b = self.card(window, B)
        b.label_row.set_text("Side project")
        b.label_row.emit("apply")
        self.assertEqual(link.last("accounts.setLabel"), {"account": B, "label": "Side project"})
        self.card(window, A).label_row.set_text("")
        self.card(window, A).label_row.emit("apply")
        self.assertEqual(link.last("accounts.setLabel"), {"account": A, "label": None})

    def test_general_controls(self):
        window, link = self.make()
        g = window.general_page
        g.enabled.set_active(False)
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.enabled", "value": False})
        g.strategy.set_selected(1)
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.strategy", "value": "myOrder"})
        g.interval.set_selected(0)
        self.assertEqual(link.last("settings.set"), {"key": "refreshInterval", "value": 15})
        g.session_default.set_value(80)
        pump(900)
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.sessionThreshold", "value": 80})
        g.on_fable.set_active(False)
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.onFable", "value": False})

    def test_use_priority_sets_the_strategy(self):
        window, link = self.make()
        window.accounts_page.use_priority_row.get_last_child()  # the row is built
        button = self._find_button(window.accounts_page.use_priority_row, "Use priority")
        button.emit("clicked")
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.strategy", "value": "priority"})

    def _find_button(self, widget, label):
        queue = [widget]
        while queue:
            item = queue.pop()
            if isinstance(item, Gtk.Button) and item.get_label() == label:
                return item
            child = item.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
        raise AssertionError(f"no button {label!r}")

    def test_errors_become_a_toast_and_the_window_reads_again(self):
        from pixelswitch.protocol import ControlError

        def refuse(params):
            raise ControlError("busy", "A switch is in progress.")

        window, link = self.make(results={"accounts.switch": refuse})
        shown = []
        window.toast = shown.append
        self.card(window, B).switch_button.emit("clicked")
        self.assertEqual(shown, ["A switch is in progress."])
        self.assertGreaterEqual(link.requested, 1)

    # -- sign in --

    def test_sign_in_page_shows_both_links_and_opens_nothing_by_itself(self):
        opened = []
        original = Gio.AppInfo.launch_default_for_uri
        Gio.AppInfo.launch_default_for_uri = lambda uri, ctx=None: opened.append(uri)
        self.addCleanup(setattr, Gio.AppInfo, "launch_default_for_uri", original)
        snap = fx.fixture_snapshot(sign_in=fx.fixture_sign_in())
        window, link = self.make(snap)
        page = window.sign_in_page
        self.assertFalse(page.start_button.get_visible())
        self.assertTrue(page.cancel_button.get_visible())
        self.assertTrue(page.links.get_visible())
        self.assertTrue(page.auto_row.get_visible() and page.manual_row.get_visible() and page.code_row.get_visible())
        self.assertEqual(opened, [])  # nothing opens a browser unless Open is pressed
        self._find_button(page.auto_row, "Open").emit("clicked")
        self.assertEqual(opened, [snap.sign_in["automaticLink"]])
        copied = []
        window.copy_text = lambda text, message: copied.append(text)
        self._find_button(page.manual_row, "Copy").emit("clicked")
        self.assertEqual(copied, [snap.sign_in["manualLink"]])
        page.code_row.set_text("abc#def")
        page.code_row.emit("apply")
        self.assertEqual(link.last("accounts.signIn.submitCode"), {"code": "abc#def"})
        page.cancel_button.emit("clicked")
        self.assertIn("accounts.signIn.cancel", [m for m, _ in link.calls])

    def test_start_sends_no_open_parameter(self):
        window, link = self.make()
        self.assertTrue(window.sign_in_page.start_button.get_visible())
        self.assertFalse(window.sign_in_page.links.get_visible())
        window.sign_in_page.start_button.emit("clicked")
        self.assertEqual(link.last("accounts.signIn.start"), None)
        self.assertIn("accounts.signIn.start", [m for m, _ in link.calls])

    # -- about, and the banner --

    def test_about_and_the_banner(self):
        window, link = self.make()
        self.assertFalse(window.banner.get_revealed())
        self.assertEqual(window.about_page.daemon_row.get_subtitle(), "version 2.0")
        self.assertIn("pixelswitch.log", window.about_page.log_row.get_subtitle())
        window.about_page.automatic.set_active(False)
        self.assertEqual(link.last("settings.set"), {"key": "updates.automatic", "value": False})
        from pixelswitch.gui import model

        window.apply_snapshot(model.Snapshot(connected=False, error="nothing is listening"))
        self.assertTrue(window.banner.get_revealed())
        self.assertEqual(window.about_page.daemon_row.get_subtitle(), "not running")

    def test_the_cards_are_rebuilt_only_when_the_accounts_change(self):
        window, _ = self.make()
        before = self.card(window, A)
        window.apply_snapshot(fx.fixture_snapshot())
        self.assertIs(self.card(window, A), before)
        snap = fx.fixture_snapshot()
        snap.accounts = snap.accounts[:2]
        window.apply_snapshot(snap)
        self.assertEqual(window.accounts_page._order, [A, B])
        self.assertNotIn(C, window.accounts_page._cards)
        snap.accounts = []
        window.apply_snapshot(snap)
        self.assertEqual(window.accounts_page._order, [])


if __name__ == "__main__":
    unittest.main()
