"""What the tray and the settings window say: every sentence, number and menu line, from fixture data (no GTK)."""
from __future__ import annotations

import time
import unittest

from pixelswitch.gui import model
from pixelswitch.gui.model import MenuEntry

from . import gui_fixtures as fx

NOW = 1_800_000_000.0


class FormatTests(unittest.TestCase):
    def test_percent_text(self):
        self.assertEqual((model.percent_text(None), model.percent_text(42.4), model.percent_text(99.6), model.percent_text(2.5)),
                         ("—", "42%", "100%", "3%"))

    def test_reset_words(self):
        def at(seconds):
            return fx.iso(NOW, seconds)

        self.assertEqual(model.reset_words(at(30), NOW), "resets in under a minute")
        self.assertEqual(model.reset_words(at(42 * 60), NOW), "resets in 42 min")
        self.assertEqual(model.reset_words(at(3 * 3600 + 12 * 60), NOW), "resets in 3 h 12 min")
        self.assertEqual(model.reset_words(at(5 * 3600), NOW), "resets in 5 h")
        self.assertTrue(model.reset_words(at(3 * 86400), NOW).startswith("resets "))
        self.assertNotIn(" in ", model.reset_words(at(3 * 86400), NOW))
        self.assertEqual(model.reset_words(at(-5), NOW), "")
        self.assertEqual(model.reset_words(None, NOW), "")
        self.assertEqual(model.reset_words("garbage", NOW), "")

    def test_updated_ago_only_when_stale(self):
        self.assertIsNone(model.updated_ago(fx.iso(NOW, -60), NOW))
        self.assertEqual(model.updated_ago(fx.iso(NOW, -90), NOW), "Updated 1 minute ago")
        self.assertEqual(model.updated_ago(fx.iso(NOW, -600), NOW), "Updated 10 minutes ago")
        self.assertEqual(model.updated_ago(fx.iso(NOW, -7200), NOW), "Updated 2 hours ago")
        self.assertEqual(model.updated_ago(fx.iso(NOW, -3 * 86400), NOW), "Updated 3 days ago")
        self.assertIsNone(model.updated_ago(None, NOW))

    def test_usage_tone(self):
        self.assertEqual([model.usage_tone(v) for v in (None, 10, 74, 75, 89, 90, 100)],
                         ["ok", "ok", "ok", "warn", "warn", "high", "high"])


class AccountTextTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c = fx.fixture_accounts(NOW)

    def test_titles_and_priority(self):
        self.assertEqual((model.account_title(self.a), model.account_subtitle(self.a)), ("Personal", "ahamade@gmail.com"))
        self.assertEqual((model.account_title(self.b), model.account_subtitle(self.b)), ("ahmed@pixelventures.ai", None))
        self.assertEqual((model.priority_text(self.a), model.priority_text(self.c)), ("P1", None))

    def test_tray_line(self):
        self.assertEqual(model.tray_account_text(self.a), "P1 Personal · S 42% · W 61%")
        self.assertEqual(model.tray_account_text(self.c), "Studio · S — · W —")
        plain = dict(self.a, label=None)
        self.assertEqual(model.tray_account_text(plain), "P1 ahamade@gmail.com · S 42% · W 61%")

    def test_tray_label_is_the_active_session_percentage(self):
        self.assertEqual(model.tray_label([self.a, self.b, self.c]), "42%")
        self.assertEqual(model.tray_label([self.b, self.c]), "")
        no_reading = dict(self.a, usage={})
        self.assertEqual(model.tray_label([no_reading]), "")
        self.assertIn("session 42%", model.tray_tooltip([self.a]))
        self.assertEqual(model.tray_tooltip([self.b]), "PixelSwitch: no active account")

    def test_messages(self):
        self.assertIn("isn't responding", model.message_of(self.b))
        self.assertTrue(model.can_retry(self.b))
        self.assertFalse(model.needs_resign(self.b))
        self.assertEqual(model.message_of(self.c), "Session expired. Re-authenticate (↻) to fix.")
        self.assertTrue(model.needs_resign(self.c))
        self.assertFalse(model.can_retry(self.c))
        self.assertIsNone(model.message_of(self.a))
        silent = {"email": "x@y.z", "isSwitchable": False, "usage": {}}
        self.assertEqual(model.message_of(silent), model.TEXT_RESIGN_MESSAGE)
        waiting = {"email": "x", "isSwitchable": True, "usage": {"error": "Waiting for Claude Code to renew this login. It renews the next time a session runs."}}
        self.assertFalse(model.can_retry(waiting))
        self.assertFalse(model.needs_resign(waiting))
        # The daemon's own sentence for an unexpected failure offers Retry too (usagecycle: canRetry)
        unexpected = {"email": "x", "isSwitchable": True, "usage": {"error": "Could not fetch usage: JSONDecodeError"}}
        self.assertTrue(model.can_retry(unexpected))
        self.assertFalse(model.needs_resign(unexpected))

    def test_an_account_that_follows_split_defaults_gets_the_two_window_sentence(self):
        # Session 90, weekly 50, and the account has no limit of its own. Even if its effective values were read a moment
        # before the defaults changed (they say 90 and 90), the snapshot's defaults decide.
        stale = {"email": "x", "isActive": True, "effectiveThreshold": 90, "effectiveSessionThreshold": 90, "effectiveWeeklyThreshold": 90}
        text = model.limit_help(stale, (90.0, 50.0))
        self.assertIn("at 90% of its session or 50% of its week", text)
        self.assertIn("89% or less of its session and 49% or less of its week", text)
        self.assertNotIn("moves you to it only while it is at 89% or less.", text)
        # Equal defaults still give the one-limit sentence.
        self.assertIn("at 90%, and moves you to it only while it is at 89% or less.", model.limit_help(stale, (90.0, 90.0)))
        # An account's own limit is used as the daemon resolved it.
        own = {"email": "x", "weeklyThreshold": 40, "effectiveWeeklyThreshold": 40, "effectiveSessionThreshold": 90}
        self.assertIn("at 90% of its session or 40% of its week", model.limit_help(own, (90.0, 50.0)))

    def test_the_remove_text_depends_on_whether_claude_code_will_be_switched(self):
        active, other = {"id": "1", "isActive": True}, {"id": "2", "isActive": False}
        self.assertIn("switch Claude Code to the next account by priority", model.remove_body(active, [active, other]))
        self.assertIn("every Claude Code on this computer uses", model.remove_body(active, [active, other]))
        for info, accounts in ((other, [active, other]), (active, [active])):
            body = model.remove_body(info, accounts)
            self.assertIn("Claude Code's own login is not touched.", body)
            self.assertNotIn("switch Claude Code", body)

    def test_underscores_are_doubled_for_dbusmenu(self):
        self.assertEqual(model.dbusmenu_label("ahmed_h@example.com"), "ahmed__h@example.com")
        self.assertEqual(model.dbusmenu_label("side_project"), "side__project")
        self.assertEqual(model.dbusmenu_label("no underscore"), "no underscore")

    def test_short_message(self):
        self.assertEqual(model.short_message("Session expired. Re-authenticate (↻) to fix."), "Session expired.")
        long = model.short_message("Anthropic's usage service isn't responding at all right now, and has not for a while (HTTP 503). Your account is fine.")
        self.assertLessEqual(len(long), 60)
        self.assertTrue(long.endswith("…"))
        self.assertEqual(model.short_message("Short one."), "Short one.")

    def test_reset_note(self):
        self.assertEqual(model.reset_note(self.c), "Weekly reset not known yet")
        self.assertTrue(model.reset_note(self.a, time.time()).startswith("Weekly resets"))


class LimitTests(unittest.TestCase):
    defaults = (90.0, 50.0)

    def test_menu_labels(self):
        a, b, c = fx.fixture_accounts(NOW)
        self.assertEqual(model.limit_menu_label(a, "session", self.defaults), "Default (90%)")
        self.assertEqual(model.limit_menu_label(a, "weekly", self.defaults), "Default (50%)")
        self.assertEqual(model.limit_menu_label(b, "session", self.defaults), "Switch at 90%")
        self.assertEqual(model.limit_menu_label(b, "weekly", self.defaults), "Switch at 50%")
        self.assertEqual(model.limit_menu_label(c, "session", self.defaults), "Manual only")

    def test_a_both_windows_value_reads_as_the_windows_own(self):
        both = {"threshold": 70, "manualOnly": False}
        self.assertEqual(model.own_limit(both, "session"), 70.0)
        self.assertEqual(model.own_limit({"threshold": 70, "weeklyThreshold": 40}, "weekly"), 40.0)
        self.assertEqual(model.own_limit({"threshold": 70, "weeklyThreshold": 40}, "session"), 70.0)
        self.assertIsNone(model.own_limit({"threshold": 0, "manualOnly": True}, "session"))

    def test_help_for_equal_windows(self):
        a = fx.fixture_accounts(NOW)[0]
        self.assertEqual(model.limit_help(a, (90.0, 90.0)),
                         "Auto-switch moves you off this account at 90%, and moves you to it only while it is at 89% or less.")

    def test_help_for_split_windows_with_the_overflow_clause(self):
        b = fx.fixture_accounts(NOW)[1]
        self.assertEqual(model.limit_help(b, self.defaults),
                         "Auto-switch moves you off this account at 90% of its session or 50% of its week, and moves you to it only "
                         "while it is at 89% or less of its session and 49% or less of its week (up to 89% of its week when no account "
                         "under its weekly limit can take you).")

    def test_help_without_the_clause_when_the_weekly_limit_is_the_higher_one(self):
        info = {"effectiveSessionThreshold": 50, "effectiveWeeklyThreshold": 90, "manualOnly": False}
        self.assertEqual(model.limit_help(info, (50.0, 90.0)),
                         "Auto-switch moves you off this account at 50% of its session or 90% of its week, and moves you to it only "
                         "while it is at 49% or less of its session and 89% or less of its week.")

    def test_help_for_manual_only(self):
        c = fx.fixture_accounts(NOW)[2]
        self.assertIn("never moves you to this account", model.limit_help(c, (90.0, 90.0)))
        self.assertIn("at the default threshold (90%).", model.limit_help(c, (90.0, 90.0)))
        self.assertIn("default thresholds (90% of its session or 50% of its week).", model.limit_help(c, self.defaults))

    def test_manual_note_only_for_the_active_manual_only_account(self):
        info = {"manualOnly": True, "isActive": True}
        self.assertEqual(model.manual_note(info, (90.0, 90.0)), "You're using it now. Auto-switch moves you off it at 90% and won't move you back to it.")
        self.assertIn("90% of its session or 50% of its week", model.manual_note(info, self.defaults))
        self.assertIsNone(model.manual_note({"manualOnly": True, "isActive": False}, self.defaults))

    def test_nowhere_to_go_and_the_priority_line(self):
        a, b, c = fx.fixture_accounts(NOW)
        only_manual = [a, dict(b, manualOnly=True), c]
        self.assertTrue(model.nowhere_to_go(only_manual, True))
        self.assertFalse(model.nowhere_to_go(only_manual, False))
        self.assertFalse(model.nowhere_to_go([a, b, c], True))
        self.assertTrue(model.priority_note_visible("mostRoom", [a]))
        self.assertFalse(model.priority_note_visible("myOrder", [a]))
        self.assertFalse(model.priority_note_visible("mostRoom", []))

    def test_the_fixed_sentences(self):
        self.assertEqual(model.TEXT_OVERFLOW, "When no account under its weekly limit can take you, the session limit decides.")
        self.assertEqual(model.TEXT_FABLE, "Fable uses the weekly limit.")
        self.assertEqual(model.TEXT_PRIORITY_NOTE, "Auto-switch follows these numbers only when it chooses by Priority.")
        self.assertEqual(model.STRATEGIES[1], ("myOrder", "Priority (lowest number first)"))
        self.assertEqual(model.LIMIT_CHOICES, (10, 20, 30, 40, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100))

    def test_defaults_follow_the_base_when_the_windows_are_not_sent(self):
        self.assertEqual(model.defaults_of({"autoSwitch": {"defaultThreshold": 98}}), (98.0, 98.0))
        self.assertEqual(model.defaults_of({"autoSwitch": {"defaultThreshold": 90, "defaultSessionThreshold": 90, "defaultWeeklyThreshold": 50}}), (90.0, 50.0))
        self.assertEqual(model.defaults_of(None), (90.0, 90.0))


class MenuTests(unittest.TestCase):
    def test_the_menu_of_spec_5_1(self):
        entries = model.build_tray_menu(fx.fixture_snapshot(NOW))
        labels = [(e.kind, e.label.strip(), e.action, e.enabled, e.checked) for e in entries]
        self.assertEqual(labels[0], ("check", "P1 Personal · S 42% · W 61%", None, True, True))
        self.assertEqual(labels[1][:2], ("check", "P3 ahmed@pixelventures.ai · S 15% · W 48%"))
        self.assertEqual(labels[1][2], "switch:22222222-2222-2222-2222-222222222222")
        self.assertEqual(labels[2][0:2], ("item", "Anthropic's usage service isn't responding (HTTP 503)."))
        self.assertFalse(labels[2][3])
        # The Manual only, not-switchable account: shown, not clickable, its message under it.
        studio = next(i for i, e in enumerate(entries) if e.label.startswith("Studio"))
        self.assertIsNone(entries[studio].action)
        self.assertFalse(entries[studio].enabled)
        self.assertEqual(entries[studio + 1].label.strip(), "Session expired.")
        tail = [e.label for e in entries if e.kind != "separator"][-5:]
        self.assertEqual(tail, ["Auto-switch", "Refresh", "Settings…", "Sign in another account…", "Quit tray"])
        auto = next(e for e in entries if e.action == "auto-switch")
        self.assertEqual((auto.kind, auto.checked), ("check", True))
        self.assertEqual([e.action for e in entries if e.action in ("refresh", "settings", "sign-in", "quit")],
                         ["refresh", "settings", "sign-in", "quit"])

    def test_auto_switch_unchecked_when_off(self):
        snap = fx.fixture_snapshot(NOW)
        snap.status["autoSwitch"]["enabled"] = False
        self.assertFalse(next(e for e in model.build_tray_menu(snap) if e.action == "auto-switch").checked)

    def test_no_accounts(self):
        snap = model.Snapshot(connected=True, status={"autoSwitch": {}})
        self.assertEqual(model.build_tray_menu(snap)[0], MenuEntry(label="No accounts yet", enabled=False))

    def test_not_running(self):
        entries = model.build_tray_menu(model.Snapshot())
        self.assertEqual([(e.label, e.action) for e in entries if e.kind != "separator"],
                         [("PixelSwitch is not running", None), ("Start PixelSwitch", "start-service"), ("Quit tray", "quit")])

    def test_entries_compare_equal_so_an_unchanged_menu_is_not_redrawn(self):
        self.assertEqual(model.build_tray_menu(fx.fixture_snapshot(NOW)), model.build_tray_menu(fx.fixture_snapshot(NOW)))


class SnapshotTests(unittest.TestCase):
    def test_from_replies_ignores_what_is_malformed(self):
        snap = model.snapshot_from_replies({"appVersion": "2.0", "autoSwitch": {"enabled": True, "strategy": "myOrder"}},
                                           {"accounts": [{"email": "a@b.c", "id": "1"}, "junk", {"nope": 1}]},
                                           {"settings": {"refreshInterval": 60}})
        self.assertTrue(snap.connected)
        self.assertEqual([a["email"] for a in snap.accounts], ["a@b.c"])
        self.assertEqual((snap.strategy, snap.auto_switch_on, snap.app_version, snap.settings), ("myOrder", True, "2.0", {"refreshInterval": 60}))
        self.assertTrue(model.snapshot_from_replies(None, None).connected)


if __name__ == "__main__":
    unittest.main()
