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
        self.assertTrue(b.retry_button.get_visible())
        self.assertFalse(c.retry_button.get_visible())
        # Re-sign is on every card (the Mac's re-authenticate button is on every row), highlighted only where it is the fix
        for card in (a, b, c):
            self.assertTrue(card.resign_button.get_visible())
        self.assertEqual([x.resign_button.has_css_class("suggested-action") for x in (a, b, c)], [False, False, True])
        self.assertEqual(c.resign_button.get_label(), "↻ Re-sign")
        self.assertIn("42% used", a.session_row.get_subtitle())
        self.assertIn("resets in 3 h", a.session_row.get_subtitle())
        self.assertEqual(c.session_row.get_subtitle(), "No reading yet.")
        self.assertEqual(a.session_limit_row.get_subtitle(), "Default (90%)")
        self.assertEqual(b.weekly_limit_row.get_subtitle(), "Switch at 50%")
        self.assertEqual(c.session_limit_row.get_subtitle(), "Manual only")
        # A follows split defaults (session 90, weekly 50): the two-window sentence, not the equal-windows one
        self.assertIn("at 90% of its session or 50% of its week", a.help_label.get_label())
        self.assertNotIn("moves you to it only while it is at 89% or less.", a.help_label.get_label())
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

    def test_the_remove_text_tells_the_truth_about_the_active_account(self):
        from pixelswitch.gui import settings_accounts

        window, link = self.make()
        bodies = []
        original = settings_accounts.confirm_dialog
        settings_accounts.confirm_dialog = lambda parent, heading, body, label, on_confirm, **kw: bodies.append(body)
        self.addCleanup(setattr, settings_accounts, "confirm_dialog", original)
        self.card(window, B).remove_button.emit("clicked")  # not the active one
        self.assertIn("Claude Code's own login is not touched.", bodies[-1])
        # The active account, with others left: Claude Code is switched to the next one, and the text says so.
        active = self.card(window, A)
        active.remove_button.emit("clicked")
        self.assertIn("This is the account Claude Code is using now.", bodies[-1])
        self.assertIn("switch Claude Code to the next account by priority", bodies[-1])
        self.assertIn("every Claude Code on this computer uses", bodies[-1])
        self.assertNotIn("is not touched", bodies[-1])
        # The only account: nothing to switch to, so the first text stands.
        snap = fx.fixture_snapshot()
        snap.accounts = snap.accounts[:1]
        window.apply_snapshot(snap)
        self.card(window, A).remove_button.emit("clicked")
        self.assertIn("is not touched", bodies[-1])

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

    def test_a_daemon_restart_leaves_the_accounts_on_screen_greyed_out(self):
        from pixelswitch.gui import model

        window, link = self.make()
        before = self.card(window, A)
        window.apply_snapshot(model.Snapshot(connected=False, error="closed"))
        self.assertIs(self.card(window, A), before)  # no "No Accounts" page, no rebuilt cards, no invented defaults
        self.assertEqual(window.accounts_page._order, [A, B, C])
        self.assertFalse(window.accounts_page.empty.get_parent() is not None)
        self.assertFalse(window.stack.get_sensitive())
        self.assertEqual(window.general_page.weekly_default.get_value(), 50)
        window.apply_snapshot(fx.fixture_snapshot())
        self.assertTrue(window.stack.get_sensitive())
        self.assertFalse(window.banner.get_revealed())

    def test_updated_n_ago_counts_up_without_a_new_snapshot(self):
        import time as time_module
        from pixelswitch.gui import model

        now = time_module.time()
        snap = fx.fixture_snapshot(now)
        snap.accounts[0]["usage"]["sampledAt"] = fx.iso(now, -60)  # a fresh reading says nothing
        window, _ = self.make(snap)
        a = self.card(window, A)
        self.assertNotIn("Updated", a.get_description())
        original = model.time.time
        model.time.time = lambda: now + 600
        self.addCleanup(setattr, model.time, "time", original)
        self.assertTrue(window._tick())
        self.assertIn("Updated 11 minutes ago", a.get_description())

    def test_closing_sends_a_number_typed_in_the_last_half_second(self):
        window, link = self.make()
        window.general_page.weekly_default.set_value(60)  # starts the 500 ms pause
        self.assertEqual(link.calls, [])
        window.emit("close-request")
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.weeklyThreshold", "value": 60})
        window.general_page.drain_hours.set_value(12)
        window.emit("close-request")
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.drainWithinHours", "value": 12})

    def test_general_has_the_priority_line_under_the_strategy_and_the_overflow_sentence_under_the_defaults(self):
        from pixelswitch.gui import model

        window, link = self.make()
        general = window.general_page
        self.assertEqual(general.defaults_group.get_description(), model.TEXT_OVERFLOW)
        self.assertTrue(general.priority_note.get_visible())  # the strategy is "Most room left"
        self.assertEqual(general.priority_note.get_title(), model.TEXT_PRIORITY_NOTE)
        self.assertIsNot(general.priority_note.get_ancestor(Adw.PreferencesGroup), general.defaults_group)
        self.assertIs(general.priority_note.get_ancestor(Adw.PreferencesGroup), general.strategy.get_ancestor(Adw.PreferencesGroup))
        found = []  # the button is somewhere inside the row: find it by its label

        def walk(widget):
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Button) and child.get_label() == "Use priority":
                    found.append(child)
                walk(child)
                child = child.get_next_sibling()

        walk(general.priority_note)
        self.assertEqual(len(found), 1)
        found[0].emit("clicked")
        self.assertEqual(link.last("settings.set"), {"key": "autoSwitch.strategy", "value": "priority"})
        snap = fx.fixture_snapshot()
        snap.settings["autoSwitch.strategy"] = "myOrder"
        window.apply_snapshot(snap)
        self.assertFalse(general.priority_note.get_visible())

    def test_a_number_waits_while_the_pointer_is_over_the_control_and_is_sent_when_it_leaves(self):
        window, link = self.make()
        b = self.card(window, B)
        control = b.priority_control
        control._hold_begin()  # the pointer enters
        control.spin.set_value(7)
        pump(control._debounce._delay + 300)  # longer than the pause
        self.assertEqual([m for m, _ in link.calls if m == "accounts.setPriority"], [])  # held: the list must not move under it
        self.assertTrue(control._debounce.pending)  # the pause started over
        control._hold_end()  # the pointer leaves
        self.assertEqual(link.last("accounts.setPriority"), {"account": B, "priority": 7})
        limit = b.weekly_limit
        limit._hold_begin()
        limit.spin.set_value(66)
        pump(limit._debounce._delay + 300)
        self.assertEqual(link.last("accounts.setThresholds"), None)
        limit._release()  # focus leaving sends it too
        self.assertEqual(link.last("accounts.setThresholds"), {"account": B, "weekly": 66})
        # Without the pointer the pause alone sends it, as before.
        control._hold_end()
        control.spin.set_value(8)
        pump(control._debounce._delay + 300)
        self.assertEqual(link.last("accounts.setPriority"), {"account": B, "priority": 8})

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
