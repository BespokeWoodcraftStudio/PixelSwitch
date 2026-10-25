"""The settings catalog: every key, its accepted values and error texts, and the read/write rules
(Tests/UnitTests/ControlTests.swift runControlSettingsCatalogTests, AutoSwitchRulesTests.swift
autoSwitchSettingsTests, SettingsStore.swift), with the 2.0 keys of spec section 4."""
from __future__ import annotations

import unittest

from pixelswitch import settings_catalog as sc
from pixelswitch.engine import Strategy
from pixelswitch.protocol import ControlError


def norm(name, value):
    try:
        return sc.normalize(sc.named(name), value)
    except ControlError:
        return "REFUSED"


class KeyListTests(unittest.TestCase):
    def test_the_twenty_two_keys_of_1_9_in_the_macs_order(self):
        self.assertEqual(list(sc.KEYS_1_9), [
            "refreshInterval", "transcriptLookbackHours", "autoSwitch.enabled", "autoSwitch.threshold",
            "autoSwitch.onFable", "autoSwitch.strategy", "autoSwitch.drainEarly", "autoSwitch.drainWithinHours",
            "maskEmailAddresses", "colorCodeAccounts", "appLanguage", "launchAtLogin", "menuBar.showsHeadIcon",
            "menuBar.modules", "menuBar.customizesLimitBarColors", "menuBar.color.session", "menuBar.color.weekly",
            "menuBar.color.fable", "menuBar.color.lowRemaining", "menuBar.lowRemainingWarningThreshold",
            "claude.binaryPath", "updates.automatic"])
        self.assertEqual(len(sc.KEYS_1_9), 22)

    def test_the_two_new_keys_follow_the_threshold(self):
        self.assertEqual(len(sc.KEYS), 24)
        self.assertEqual(sc.NAMES[3:6], ("autoSwitch.threshold", "autoSwitch.sessionThreshold", "autoSwitch.weeklyThreshold"))
        self.assertEqual(len(set(sc.NAMES)), 24)

    def test_keys_with_no_effect_on_linux(self):
        self.assertEqual(sorted(sc.NO_EFFECT_ON_LINUX), sorted(
            ["menuBar.showsHeadIcon", "menuBar.modules", "menuBar.customizesLimitBarColors", "menuBar.color.session",
             "menuBar.color.weekly", "menuBar.color.fable", "menuBar.color.lowRemaining",
             "menuBar.lowRemainingWarningThreshold", "appLanguage", "transcriptLookbackHours"]))
        # ...and their validation is exactly the Mac's
        self.assertEqual(norm("menuBar.color.session", "34c759"), "#34C759")
        self.assertEqual(norm("appLanguage", "es"), "REFUSED")
        for name in ("autoSwitch.enabled", "autoSwitch.threshold", "launchAtLogin", "updates.automatic", "claude.binaryPath"):
            self.assertNotIn(name, sc.NO_EFFECT_ON_LINUX)


class NormalisationTests(unittest.TestCase):
    def test_booleans(self):
        for truthy in ("on", "ON", "yes", "true", "1", 1, 1.0, True):
            self.assertIs(norm("autoSwitch.enabled", truthy), True, truthy)
        for falsy in ("NO", "off", "false", "0", 0, False):
            self.assertIs(norm("autoSwitch.enabled", falsy), False, falsy)
        for bad in ("maybe", 2, None, [], " true", "yes ", {}, 0.5):
            self.assertEqual(norm("autoSwitch.enabled", bad), "REFUSED", bad)

    def test_the_refresh_interval_takes_only_the_windows_choices(self):
        self.assertEqual(norm("refreshInterval", "60"), 60)
        self.assertEqual(norm("refreshInterval", 300), 300)
        self.assertEqual(norm("refreshInterval", 45), "REFUSED")
        self.assertEqual(norm("refreshInterval", 7), "REFUSED")
        self.assertEqual(norm("refreshInterval", "fast"), "REFUSED")
        self.assertEqual(norm("refreshInterval", True), "REFUSED")

    def test_zero_all_history_is_a_lookback_choice(self):
        self.assertEqual(norm("transcriptLookbackHours", 0), 0)
        self.assertEqual(norm("transcriptLookbackHours", "168"), 168)

    def test_the_strategy_is_case_insensitive_and_stored_canonically(self):
        self.assertEqual(norm("autoSwitch.strategy", "RESETSSOONEST"), "resetsSoonest")
        self.assertEqual(norm("autoSwitch.strategy", "mostroom"), "mostRoom")
        self.assertEqual(norm("autoSwitch.strategy", "fastest"), "REFUSED")
        self.assertEqual(norm("autoSwitch.strategy", 3), "REFUSED")

    def test_the_priority_alias_stores_my_order(self):
        self.assertEqual(norm("autoSwitch.strategy", "priority"), "myOrder")
        self.assertEqual(norm("autoSwitch.strategy", "PRIORITY"), "myOrder")
        self.assertEqual(norm("autoSwitch.strategy", "myOrder"), "myOrder")
        # the alias is for the strategy only
        self.assertEqual(norm("appLanguage", "priority"), "REFUSED")
        # and it changes no error text
        with self.assertRaises(ControlError) as caught:
            sc.normalize(sc.named("autoSwitch.strategy"), "fastest")
        self.assertEqual(caught.exception.message,
                         'autoSwitch.strategy must be one of "mostRoom", "myOrder", "resetsSoonest".')

    def test_the_default_threshold_is_a_whole_number_from_50_to_100(self):
        self.assertEqual(norm("autoSwitch.threshold", "72.4"), 72)
        self.assertEqual(norm("autoSwitch.threshold", 100), 100)
        self.assertEqual(norm("autoSwitch.threshold", 49), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", 49.6), 50)
        self.assertEqual(norm("autoSwitch.threshold", float("inf")), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", float("nan")), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", 0), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", 100.4), 100)
        self.assertEqual(norm("autoSwitch.threshold", 100.6), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", "ninety"), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", None), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", True), "REFUSED")

    def test_the_early_switch_window_is_one_to_72_hours(self):
        self.assertEqual(norm("autoSwitch.drainWithinHours", 0.4), "REFUSED")
        self.assertEqual(norm("autoSwitch.drainWithinHours", 72), 72)
        self.assertEqual(norm("autoSwitch.drainWithinHours", 0.5), 1)
        self.assertEqual(norm("autoSwitch.drainWithinHours", 73), "REFUSED")

    def test_the_low_remaining_threshold_keeps_a_fraction(self):
        self.assertEqual(norm("menuBar.lowRemainingWarningThreshold", 33.5), 33.5)
        self.assertEqual(norm("menuBar.lowRemainingWarningThreshold", 101), "REFUSED")
        self.assertEqual(norm("menuBar.lowRemainingWarningThreshold", 0), 0)

    def test_menu_bar_modules(self):
        self.assertEqual(norm("menuBar.modules", "account, weeklyBar,ACCOUNT"), ["account", "weeklyBar"])
        self.assertEqual(norm("menuBar.modules", ["SessionBar", "dailyCost"]), ["sessionBar", "dailyCost"])
        self.assertEqual(norm("menuBar.modules", ["nope"]), "REFUSED")
        self.assertEqual(norm("menuBar.modules", [1]), "REFUSED")
        self.assertEqual(norm("menuBar.modules", ""), [])
        self.assertEqual(norm("menuBar.modules", 5), "REFUSED")

    def test_colours(self):
        self.assertEqual(norm("menuBar.color.session", "34c759"), "#34C759")
        self.assertEqual(norm("menuBar.color.session", " #0a84ff "), "#0A84FF")
        self.assertEqual(norm("menuBar.color.session", "#12345"), "REFUSED")
        self.assertEqual(norm("menuBar.color.session", "#12345G"), "REFUSED")
        self.assertEqual(norm("menuBar.color.session", 123456), "REFUSED")

    def test_the_claude_path_is_auto_or_absolute(self):
        self.assertEqual(norm("claude.binaryPath", "auto"), "")
        self.assertEqual(norm("claude.binaryPath", "AUTO"), "")
        self.assertEqual(norm("claude.binaryPath", "  "), "")
        self.assertEqual(norm("claude.binaryPath", "/opt/bin/claude"), "/opt/bin/claude")
        self.assertEqual(norm("claude.binaryPath", "claude"), "REFUSED")
        self.assertEqual(norm("claude.binaryPath", 5), "REFUSED")

    def test_only_the_apps_languages_or_auto(self):
        self.assertEqual(norm("appLanguage", "ja"), "ja")
        self.assertEqual(norm("appLanguage", "ZH-HANS"), "zh-Hans")
        self.assertEqual(norm("appLanguage", "es"), "REFUSED")

    def test_automatic_updates_is_an_on_off_setting(self):
        self.assertEqual(sc.named("updates.automatic").name, "updates.automatic")
        self.assertIs(norm("updates.automatic", "on"), True)
        self.assertEqual(norm("updates.automatic", "sometimes"), "REFUSED")


class WindowDefaultKeyTests(unittest.TestCase):
    """autoSwitch.sessionThreshold and autoSwitch.weeklyThreshold (spec section 4)."""

    KEYS = ("autoSwitch.sessionThreshold", "autoSwitch.weeklyThreshold")

    def test_fifty_to_hundred_whole_numbers(self):
        for name in self.KEYS:
            self.assertEqual(norm(name, 90), 90)
            self.assertEqual(norm(name, "72.4"), 72)
            self.assertEqual(norm(name, 50), 50)
            self.assertEqual(norm(name, 100), 100)
            self.assertEqual(norm(name, 49), "REFUSED")
            self.assertEqual(norm(name, 0), "REFUSED")
            self.assertEqual(norm(name, 101), "REFUSED")
            self.assertEqual(norm(name, float("nan")), "REFUSED")
            self.assertEqual(norm(name, True), "REFUSED")

    def test_null_or_default_clears_to_follow_the_base(self):
        for name in self.KEYS:
            self.assertIsNone(norm(name, None))
            self.assertIsNone(norm(name, "default"))
            self.assertIsNone(norm(name, "DEFAULT"))
            self.assertIsNone(norm(name, " default "))
            self.assertEqual(norm(name, "defaults"), "REFUSED")
            self.assertEqual(norm(name, ""), "REFUSED")

    def test_the_error_text(self):
        for name in self.KEYS:
            with self.assertRaises(ControlError) as caught:
                sc.normalize(sc.named(name), 10)
            self.assertEqual(caught.exception.kind, "invalidValue")
            self.assertEqual(caught.exception.message, f'{name} must be 50–100, whole numbers, or "default".')

    def test_the_base_threshold_does_not_accept_default(self):
        self.assertEqual(norm("autoSwitch.threshold", "default"), "REFUSED")
        self.assertEqual(norm("autoSwitch.threshold", None), "REFUSED")

    def test_window_values_are_not_negative_zero_or_text_floats(self):
        self.assertEqual(norm("autoSwitch.weeklyThreshold", "50.0"), 50)
        self.assertIsInstance(norm("autoSwitch.weeklyThreshold", 50.0), int)


class NamedAndErrorTests(unittest.TestCase):
    def test_keys_are_found_case_insensitively(self):
        self.assertEqual(sc.named("AUTOSWITCH.ENABLED").name, "autoSwitch.enabled")
        self.assertEqual(sc.named("  autoswitch.sessionthreshold ").name, "autoSwitch.sessionThreshold")

    def test_an_unknown_key_lists_the_real_ones(self):
        with self.assertRaises(ControlError) as caught:
            sc.named("volume")
        self.assertEqual(caught.exception.kind, "notFound")
        self.assertEqual(caught.exception.message, 'There is no setting called "volume".')
        self.assertEqual(caught.exception.candidates, list(sc.NAMES))
        self.assertEqual(len(caught.exception.candidates), len(sc.KEYS))

    def test_a_refused_value_says_what_is_allowed(self):
        with self.assertRaises(ControlError) as caught:
            sc.normalize(sc.named("autoSwitch.threshold"), 10)
        self.assertEqual(caught.exception.message, "autoSwitch.threshold must be 50–100, whole numbers.")

    def test_the_summaries(self):
        expected = {
            "autoSwitch.enabled": "true or false",
            "refreshInterval": "one of 15, 30, 60, 300, 600",
            "transcriptLookbackHours": "one of 24, 72, 168, 720, 0",
            "appLanguage": 'one of "auto", "en", "zh-Hans", "ja", "de", "fr"',
            "autoSwitch.drainWithinHours": "1–72, whole numbers",
            "menuBar.lowRemainingWarningThreshold": "0–100",
            "menuBar.modules": "a list drawn from account, sessionBar, sessionBarPlain, weeklyBar, weeklyBarPlain, "
                               "fableBar, dailyCost, sessionReset, weeklyReset",
            "menuBar.color.fable": "a colour as #RRGGBB",
            "claude.binaryPath": '"" to auto-detect, or an absolute path',
        }
        for name, text in expected.items():
            self.assertEqual(sc.summary(sc.named(name)), text, name)

    def test_the_claude_path_check(self):
        sc.validate_claude_binary_path("", lambda path: False)
        sc.validate_claude_binary_path("/bin/ls", lambda path: True)
        with self.assertRaises(ControlError) as caught:
            sc.validate_claude_binary_path("/nope/claude", lambda path: False)
        self.assertEqual((caught.exception.kind, caught.exception.message),
                         ("invalidValue", "No executable file at /nope/claude."))


class ReadingTests(unittest.TestCase):
    def test_untouched_settings_read_as_the_defaults(self):
        cfg = sc.read_auto_switch({})
        self.assertEqual((cfg.defaults.base, cfg.defaults.session, cfg.defaults.weekly), (90, 90, 90))
        self.assertEqual((cfg.enabled, cfg.on_fable, cfg.strategy, cfg.drain_early, cfg.drain_within_hours),
                         (False, True, Strategy.MOST_ROOM, True, 24))

    def test_every_key_has_a_default_that_normalises_to_itself(self):
        for key in sc.KEYS:
            value = sc.read_setting({}, key)
            if key.name in (sc.SESSION_THRESHOLD_KEY, sc.WEEKLY_THRESHOLD_KEY):
                self.assertEqual(value, 90)
            else:
                self.assertEqual(sc.normalize(key, value), value, key.name)

    def test_defaults(self):
        got = sc.read_all({})
        self.assertEqual(got["refreshInterval"], 300)
        self.assertEqual(got["transcriptLookbackHours"], 24)
        self.assertIs(got["autoSwitch.enabled"], False)
        self.assertEqual(got["autoSwitch.threshold"], 90)
        self.assertIs(got["autoSwitch.onFable"], True)
        self.assertEqual(got["autoSwitch.strategy"], "mostRoom")
        self.assertEqual(got["autoSwitch.drainWithinHours"], 24)
        self.assertEqual(got["menuBar.modules"], ["account", "sessionBarPlain", "weeklyBarPlain"])
        self.assertEqual(got["menuBar.color.lowRemaining"], "#FF3B30")
        self.assertEqual(got["claude.binaryPath"], "")
        self.assertIs(got["updates.automatic"], True)
        self.assertIs(got["launchAtLogin"], False)
        self.assertEqual(len(got), 24)

    def test_with_only_the_base_stored_both_window_keys_read_it(self):
        """Spec 2.1 test: only autoSwitchThreshold = 98 stored."""
        stored = {"autoSwitchThreshold": 98}
        got = sc.read_all(stored)
        self.assertEqual((got["autoSwitch.threshold"], got["autoSwitch.sessionThreshold"],
                          got["autoSwitch.weeklyThreshold"]), (98, 98, 98))

    def test_window_keys_read_effective_numbers_never_null(self):
        stored = {"autoSwitchThreshold": 90, "autoSwitchWeeklyThreshold": 50}
        got = sc.read_all(stored)
        self.assertEqual((got["autoSwitch.sessionThreshold"], got["autoSwitch.weeklyThreshold"]), (90, 50))

    def test_a_saved_default_of_zero_still_reads_90(self):
        self.assertEqual(sc.read_auto_switch({"autoSwitchThreshold": 0}).defaults.base, 90)
        self.assertEqual(sc.read_auto_switch({"autoSwitchThreshold": 30}).defaults.base, 50)
        self.assertEqual(sc.read_auto_switch({"autoSwitchThreshold": 99}).defaults.base, 99)
        self.assertEqual(sc.read_auto_switch({"autoSwitchThreshold": 100}).defaults.base, 100)

    def test_an_unknown_strategy_falls_back_to_most_room(self):
        self.assertEqual(sc.read_auto_switch({"autoSwitchStrategy": "resetsSoonest"}).strategy, Strategy.RESETS_SOONEST)
        self.assertEqual(sc.read_auto_switch({"autoSwitchStrategy": "fastest"}).strategy, Strategy.MOST_ROOM)
        self.assertEqual(sc.read_auto_switch({"autoSwitchStrategy": 5}).strategy, Strategy.MOST_ROOM)

    def test_the_drain_window_is_kept_within_one_to_72(self):
        read = lambda v: sc.read_auto_switch({"autoSwitchDrainWithinHours": v}).drain_within_hours  # noqa: E731
        self.assertEqual([read(0.5), read(500), read(12), read("x"), read(float("nan"))], [1, 72, 12, 24, 24])

    def test_booleans_are_read_strictly(self):
        cfg = sc.read_auto_switch({"autoSwitchEnabled": True, "autoSwitchOnFable": False, "autoSwitchDrainEarly": "no"})
        self.assertEqual((cfg.enabled, cfg.on_fable, cfg.drain_early), (True, False, True))

    def test_an_invalid_stored_value_reads_as_the_default(self):
        stored = {"refreshInterval": 7, "appLanguage": "es", "menuBarModules": ["bogus"], "autoSwitchEnabled": "yes"}
        got = sc.read_all(stored)
        self.assertEqual((got["refreshInterval"], got["appLanguage"]), (300, "auto"))
        self.assertEqual(got["menuBar.modules"], ["account", "sessionBarPlain", "weeklyBarPlain"])
        self.assertIs(got["autoSwitch.enabled"], False)

    def test_stored_values_are_read_back(self):
        stored = {"refreshInterval": 60, "claudeBinaryPathPreference": "/x/claude", "updatesAutomatic": False,
                  "sessionLimitBarColor": "#112233", "menuBarModules": ["dailyCost"], "lowRemainingWarningThreshold": 12.5}
        got = sc.read_all(stored)
        self.assertEqual((got["refreshInterval"], got["claude.binaryPath"], got["updates.automatic"]), (60, "/x/claude", False))
        self.assertEqual((got["menuBar.color.session"], got["menuBar.modules"]), ("#112233", ["dailyCost"]))
        self.assertEqual(got["menuBar.lowRemainingWarningThreshold"], 12.5)


class WritingTests(unittest.TestCase):
    def test_a_write_returns_a_copy(self):
        stored = {"autoSwitchThreshold": 80}
        updated = sc.write_setting(stored, sc.named("autoSwitch.enabled"), True)
        self.assertEqual(updated, {"autoSwitchThreshold": 80, "autoSwitchEnabled": True})
        self.assertEqual(stored, {"autoSwitchThreshold": 80})

    def test_a_legacy_write_of_the_threshold_clears_the_two_window_keys(self):
        """D8: settings.set autoSwitch.threshold means both windows."""
        stored = {"autoSwitchThreshold": 90, "autoSwitchSessionThreshold": 85, "autoSwitchWeeklyThreshold": 60, "x": 1}
        updated = sc.write_setting(stored, sc.named("autoSwitch.threshold"), 80)
        self.assertEqual(updated, {"autoSwitchThreshold": 80, "x": 1})
        cfg = sc.read_auto_switch(updated)
        self.assertEqual((cfg.defaults.session, cfg.defaults.weekly), (80, 80))

    def test_a_window_write_touches_only_its_own_key(self):
        stored = {"autoSwitchThreshold": 98}
        updated = sc.write_setting(stored, sc.named("autoSwitch.weeklyThreshold"), 50)
        self.assertEqual(updated, {"autoSwitchThreshold": 98, "autoSwitchWeeklyThreshold": 50})
        cfg = sc.read_auto_switch(updated)
        self.assertEqual((cfg.defaults.base, cfg.defaults.session, cfg.defaults.weekly), (98, 98, 50))

    def test_clearing_a_window_key_removes_it(self):
        stored = {"autoSwitchThreshold": 90, "autoSwitchSessionThreshold": 70}
        updated = sc.write_setting(stored, sc.named("autoSwitch.sessionThreshold"), None)
        self.assertEqual(updated, {"autoSwitchThreshold": 90})

    def test_a_normalised_value_round_trips_through_the_store(self):
        stored = {}
        for key in sc.KEYS:
            if key.name in (sc.SESSION_THRESHOLD_KEY, sc.WEEKLY_THRESHOLD_KEY):
                continue
            value = sc.normalize(key, sc.read_setting({}, key))
            stored = sc.write_setting(stored, key, value)
            self.assertEqual(sc.read_setting(stored, key), value, key.name)

    def test_a_strategy_alias_is_stored_as_my_order(self):
        key = sc.named("autoSwitch.strategy")
        updated = sc.write_setting({}, key, sc.normalize(key, "priority"))
        self.assertEqual(updated, {"autoSwitchStrategy": "myOrder"})
        self.assertEqual(sc.read_setting(updated, key), "myOrder")


if __name__ == "__main__":
    unittest.main()
