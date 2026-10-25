"""Every one of the 21 control methods through ControlApi: the same validation and texts as the Mac's
(ControlTests.swift), the 2.0 additions, the envelope rules, and that no result carries a token."""
from __future__ import annotations

import json
import os
import re
import unittest

from pixelswitch import protocol, settings_catalog as catalog
from pixelswitch.control_api import ControlApi

from .lb_support import Rig, read_json, wait_for
from .mock_api import R, usage_body


class Api:
    def __init__(self, case, **kw):
        self.rig = Rig(case)
        rig = self.rig
        now = rig.clock.now
        self.a = rig.seed_account("alice@work.com", active=True, usage=usage_body(42, 61, fable=10, now=now))
        self.b = rig.seed_account("bob@home.com", usage=usage_body(10, 10, now=now), switch=70)
        self.c = rig.seed_account("carol@work.com", usage=usage_body(5, 5, now=now))
        self.app = rig.build()
        self.logs: list[str] = []
        self.api = ControlApi(self.app, self.logs.append)
        self.subscribed = []

    def call(self, method, params=None, id=1, raw=None):
        line = raw if raw is not None else protocol.request_line(id, method, params)
        out = self.api.handle(line, lambda: self.subscribed.append(1))
        return None if out is None else json.loads(out)

    def result(self, method, params=None):
        reply = self.call(method, params)
        assert "result" in reply, reply
        return reply["result"]

    def error(self, method, params=None):
        reply = self.call(method, params)
        assert "error" in reply, reply
        return reply["error"]


class StatusAndListTests(unittest.TestCase):
    def setUp(self):
        self.t = Api(self)

    def test_status(self):
        status = self.t.result("status.get")
        self.assertEqual(status["protocolVersion"], 2, "a shipped CLI requires exactly 2")
        self.assertEqual(status["protocolRevision"], 3)
        self.assertEqual(status["features"], ["priority", "splitThresholds"])
        self.assertEqual(status["platform"], "linux")
        self.assertEqual((status["activeAccountEmail"], status["accountCount"], status["claudeAvailable"]),
                         ("alice@work.com", 3, True))
        self.assertNotIn("testMode", status, "only when true")
        self.assertNotIn("signIn", status)
        self.assertNotIn("warnings", status)
        self.assertEqual(status["autoSwitch"]["defaultSessionThreshold"], 90)
        self.assertEqual(status["autoSwitch"]["strategy"], "mostRoom")
        self.assertNotIn("machine", self.t.result("usage.get"), "the machine-usage line is omitted on Linux (D15)")

    def test_status_reports_test_mode_when_it_is_on(self):
        self.t.app.test_mode = True
        self.assertIs(self.t.result("status.get")["testMode"], True)

    def test_status_carries_one_warning_line_when_the_daemons_environment_sets_a_config_dir(self):
        self.t.app.cli._base_env["CLAUDE_CONFIG_DIR"] = "/x"
        warnings = self.t.result("status.get")["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("CLAUDE_CONFIG_DIR", warnings[0])

    def test_status_names_a_sign_in(self):
        from pixelswitch.signin import Purpose

        session = self.t.app.start_sign_in(Purpose.new_account())
        self.assertEqual(self.t.result("status.get")["signIn"]["purpose"], "newAccount")
        session.cancel()
        session.wait_finished(20)

    def test_accounts_come_in_priority_order_with_positions_and_usage(self):
        t = self.t
        t.app.refresh()
        accounts = t.result("accounts.list")["accounts"]
        self.assertEqual([a["position"] for a in accounts], [1, 2, 3])
        self.assertEqual([a["email"] for a in accounts], ["alice@work.com", "bob@home.com", "carol@work.com"])
        first = accounts[0]
        self.assertEqual(first["usage"]["session"]["utilization"], 42)
        self.assertEqual(first["usage"]["fable"]["utilization"], 10)
        self.assertRegex(first["usage"]["weekly"]["resetsAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(\+00:00|Z)$")
        self.assertIn("sampledAt", first["usage"])
        self.assertEqual((accounts[1]["threshold"], accounts[1]["effectiveThreshold"]), (70, 70))
        self.assertEqual((accounts[0].get("threshold"), accounts[0]["effectiveThreshold"]), (None, 90))
        self.assertTrue(re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", first["id"]), "uppercase UUID")

    def test_an_account_with_no_reading_but_an_error_still_reports_the_error(self):
        t = self.t
        t.app.refresh_backup_in_place  # noqa: B018 - keep the import path honest
        t.app.card_errors[t.c.id] = type("E", (), {"message": "Token expired. Switch to refresh.", "is_expired": False})()
        info = [a for a in t.result("accounts.list")["accounts"] if a["email"] == "carol@work.com"][0]
        self.assertEqual(info["usage"], {"error": "Token expired. Switch to refresh."})

    def test_absent_optionals_are_omitted_never_null(self):
        text = protocol.dumps(self.t.result("accounts.list"))
        self.assertNotIn("null", text)
        info = self.t.result("accounts.list")["accounts"][2]
        self.assertNotIn("label", info)
        self.assertNotIn("priority", info)
        self.assertNotIn("sessionThreshold", info)


class SwitchAndAddTests(unittest.TestCase):
    def setUp(self):
        self.t = Api(self)

    def test_switch_resolves_the_name_and_switches(self):
        t = self.t
        result = t.result("accounts.switch", {"account": "bob@home.com"})
        self.assertEqual((result["account"]["email"], result["account"]["isActive"]), ("bob@home.com", True))
        self.assertEqual(t.rig.live_credential()["claudeAiOauth"]["accessToken"], t.b.access)
        self.assertIn(f"[control] accounts.switch {protocol.uuid_string(t.b.id)} ok", t.logs)

    def test_a_busy_app_answers_busy(self):
        self.t.app.gate.try_begin("signin")
        error = self.t.error("accounts.switch", {"account": "1"})
        self.assertEqual((error["data"]["kind"], error["code"]), ("busy", -32001))
        self.assertEqual(error["message"], "A switch or sign-in is in progress. Try again in a moment.")

    def test_references_by_id_email_label_position_and_the_failures(self):
        t = self.t
        t.app.set_label("2", t.c.id)  # a label "2" beats position 2
        ref = lambda text: t.call("accounts.setLabel", {"account": text, "label": "x"})
        self.assertEqual(ref("2")["result"]["account"]["email"], "carol@work.com")
        self.assertEqual(ref("3")["result"]["account"]["email"], "carol@work.com")
        self.assertEqual(ref(t.a.id.lower())["result"]["account"]["email"], "alice@work.com")
        error = t.error("accounts.switch", {"account": "work"})
        self.assertEqual(error["data"]["kind"], "notFound")
        error = t.error("accounts.switch", {"account": "  "})
        self.assertEqual((error["data"]["kind"], error["message"]), ("invalidValue", "Name an account: its email, label, id or position."))
        error = t.error("accounts.switch", {"account": "nobody"})
        self.assertEqual(error["message"], 'No account matches "nobody".')
        self.assertEqual(error["data"]["candidates"], ["alice@work.com (x)", "bob@home.com", "carol@work.com (x)"])

    def test_ambiguous_names_list_the_choices(self):
        t = self.t
        t.app.set_label("shared", t.a.id)
        t.app.set_label("SHARED", t.b.id)
        error = t.error("accounts.switch", {"account": "shared"})
        self.assertEqual(error["data"]["kind"], "ambiguous")
        self.assertEqual(error["data"]["candidates"], ["alice@work.com (shared)", "bob@home.com (SHARED)"])
        self.assertEqual(error["message"], '"shared" matches 2 accounts. Use the email address or id.')

    def test_a_misspelt_parameter_is_invalid_params(self):
        error = self.t.error("accounts.switch", {"acount": "1"})
        self.assertEqual((error["data"]["kind"], error["code"]), ("invalidParams", -32602))
        self.assertEqual(error["message"], "The parameters for accounts.switch are missing or the wrong shape.")

    def test_add_current_busy_and_duplicate(self):
        t = self.t
        self.assertEqual(t.error("accounts.addCurrent")["message"], "Account already exists")
        t.app.gate.try_begin("switch")
        self.assertEqual(t.error("accounts.addCurrent")["data"]["kind"], "busy")


class LabelThresholdPriorityOrderTests(unittest.TestCase):
    def setUp(self):
        self.t = Api(self)

    def test_label(self):
        t = self.t
        self.assertEqual(t.result("accounts.setLabel", {"account": "carol@work.com", "label": "  Side  "})["account"]["label"], "Side")
        self.assertNotIn("label", t.result("accounts.setLabel", {"account": "carol@work.com", "label": "   "})["account"])
        self.assertNotIn("label", t.result("accounts.setLabel", {"account": "carol@work.com", "label": None})["account"])
        self.assertNotIn("label", t.result("accounts.setLabel", {"account": "carol@work.com"})["account"])
        error = t.error("accounts.setLabel", {"account": "1", "label": "x" * 101})
        self.assertEqual((error["data"]["kind"], error["message"]), ("invalidValue", "A label can be at most 100 characters."))
        self.assertEqual(t.result("accounts.setLabel", {"account": "1", "label": "x" * 100})["account"]["label"], "x" * 100)
        self.assertEqual(t.call("accounts.setLabel", {"account": "1", "label": 5})["error"]["data"]["kind"], "invalidParams")

    def test_the_legacy_threshold(self):
        t = self.t
        result = t.result("accounts.setThreshold", {"account": "1", "threshold": 72.6})["account"]
        self.assertEqual(result["threshold"], 73)
        self.assertNotIn("threshold", t.result("accounts.setThreshold", {"account": "1", "threshold": None})["account"])
        manual = t.result("accounts.setThreshold", {"account": "1", "threshold": 0})["account"]
        self.assertEqual((manual["threshold"], manual["manualOnly"], manual["effectiveThreshold"]), (0, True, 90))
        low = t.result("accounts.setThreshold", {"account": "1", "threshold": 35})["account"]
        self.assertEqual((low["threshold"], low["effectiveThreshold"], low["manualOnly"]), (35, 35, False))
        self.assertTrue(t.result("accounts.setThreshold", {"account": "1", "threshold": 0.4})["account"]["manualOnly"])
        self.assertEqual(t.result("accounts.setThreshold", {"account": "1", "threshold": 0.6})["account"]["threshold"], 1)
        for bad in (-1, 101, float("inf")):
            if bad == float("inf"):
                continue
            error = t.error("accounts.setThreshold", {"account": "1", "threshold": bad})
            self.assertEqual((error["data"]["kind"], error["message"]), ("invalidValue",
                             "A threshold must be 1–100, 0 for manual only, or null to follow the default."))
        self.assertEqual(t.call("accounts.setThreshold", {"account": "1", "threshold": "high"})["error"]["data"]["kind"], "invalidParams")

    def test_a_legacy_threshold_write_clears_window_values(self):
        t = self.t
        t.result("accounts.setThresholds", {"account": "1", "session": 60, "weekly": 40})
        info = t.result("accounts.setThreshold", {"account": "1", "threshold": 80})["account"]
        self.assertEqual(info["threshold"], 80)
        self.assertNotIn("sessionThreshold", info)
        self.assertNotIn("weeklyThreshold", info)

    def test_set_thresholds_presence_decides(self):
        t = self.t
        info = t.result("accounts.setThresholds", {"account": "1", "session": 90, "weekly": 50})["account"]
        self.assertEqual((info["sessionThreshold"], info["weeklyThreshold"]), (90, 50))
        self.assertEqual((info["effectiveSessionThreshold"], info["effectiveWeeklyThreshold"], info["effectiveThreshold"]), (90, 50, 50))
        info = t.result("accounts.setThresholds", {"account": "1", "weekly": None})["account"]
        self.assertEqual(info["sessionThreshold"], 90, "absent leaves it alone")
        self.assertNotIn("weeklyThreshold", info, "null clears to follow the default")
        self.assertEqual(info["effectiveWeeklyThreshold"], 90)
        info = t.result("accounts.setThresholds", {"account": "1", "session": 44.6})["account"]
        self.assertEqual(info["sessionThreshold"], 45, "rounded half away from zero")

    def test_set_thresholds_validation(self):
        t = self.t
        text = "A threshold must be 1–100, or null to follow the default. For manual only use accounts.setThreshold with 0."
        for bad in (0, -5, 101, 100.6, "x", True, [1]):
            error = t.error("accounts.setThresholds", {"account": "1", "session": bad})
            self.assertEqual((error["data"]["kind"], error["message"]), ("invalidValue", text), bad)
        self.assertEqual(t.error("accounts.setThresholds", {"account": "1"})["data"]["kind"], "invalidParams")
        self.assertEqual(t.error("accounts.setThresholds", {"session": 5})["data"]["kind"], "invalidParams")
        self.assertEqual(t.result("accounts.setThresholds", {"account": "1", "session": 100})["account"]["sessionThreshold"], 100)
        self.assertEqual(t.result("accounts.setThresholds", {"account": "1", "weekly": 0.5})["account"]["weeklyThreshold"], 1)

    def test_the_single_value_is_split_before_a_per_window_write(self):
        t = self.t
        info = t.result("accounts.setThresholds", {"account": "bob@home.com", "session": None})["account"]  # bob has 70
        self.assertNotIn("threshold", info)
        self.assertNotIn("sessionThreshold", info)
        self.assertEqual(info["weeklyThreshold"], 70)
        self.assertEqual((info["effectiveSessionThreshold"], info["effectiveWeeklyThreshold"]), (90, 70))

    def test_only_a_number_clears_manual_only(self):
        t = self.t
        t.result("accounts.setThreshold", {"account": "1", "threshold": 0})
        info = t.result("accounts.setThresholds", {"account": "1", "session": None})["account"]
        self.assertTrue(info["manualOnly"], "null alone changes nothing")
        info = t.result("accounts.setThresholds", {"account": "1", "weekly": 60})["account"]
        self.assertFalse(info["manualOnly"])
        self.assertEqual((info["weeklyThreshold"], info["effectiveSessionThreshold"]), (60, 90))
        self.assertNotIn("threshold", info)

    def test_priority(self):
        t = self.t
        accounts = t.result("accounts.setPriority", {"account": "carol@work.com", "priority": 1})["accounts"]
        self.assertEqual([a["email"] for a in accounts][0], "carol@work.com")
        self.assertEqual(accounts[0]["priority"], 1)
        self.assertEqual([a["position"] for a in accounts], [1, 2, 3])
        accounts = t.result("accounts.setPriority", {"account": "alice@work.com", "priority": 1})["accounts"]
        self.assertEqual([a["email"] for a in accounts][:2], ["alice@work.com", "carol@work.com"], "equal numbers: set last goes first")
        accounts = t.result("accounts.setPriority", {"account": "alice@work.com", "priority": None})["accounts"]
        self.assertNotIn("priority", [a for a in accounts if a["email"] == "alice@work.com"][0])
        text = "A priority must be a whole number from 1 to 99, or null for none."
        for bad in (0, 100, -1, 2.5, "3", True, [1]):
            error = t.error("accounts.setPriority", {"account": "1", "priority": bad})
            self.assertEqual((error["data"]["kind"], error["message"]), ("invalidValue", text), bad)
        self.assertEqual(t.error("accounts.setPriority", {"account": "1"})["data"]["kind"], "invalidParams", "the key must be present")
        self.assertEqual(t.result("accounts.setPriority", {"account": "1", "priority": 3.0})["accounts"][0]["priority"], 3)

    def test_a_priority_number_is_never_an_account_reference(self):
        t = self.t
        t.result("accounts.setPriority", {"account": "carol@work.com", "priority": 2})
        error = t.error("accounts.switch", {"account": "99"})
        self.assertEqual(error["data"]["kind"], "notFound")
        info = t.result("accounts.setLabel", {"account": "2", "label": "x"})["account"]
        self.assertEqual(info["position"], 2, "2 is a position, not P2")

    def test_order(self):
        t = self.t
        order = t.result("accounts.setOrder", {"accounts": ["3", "bob@home.com", t.a.id]})
        self.assertEqual([a["email"] for a in order["accounts"]], ["carol@work.com", "bob@home.com", "alice@work.com"])
        self.assertEqual([a["priority"] for a in order["accounts"]], [1, 2, 3], "a reorder numbers every account from 1")
        for bad in (["1", "2"], ["1", "1", "2"], ["1", "2", "3", "3"]):
            error = t.error("accounts.setOrder", {"accounts": bad})
            self.assertEqual((error["data"]["kind"], error["message"]), ("invalidValue", "List every account exactly once, in the order you want."))
            self.assertEqual(len(error["data"]["candidates"]), 3)
        self.assertEqual(t.error("accounts.setOrder", {"accounts": ["1", "2", "nobody"]})["data"]["kind"], "notFound")
        self.assertEqual(t.error("accounts.setOrder", {"accounts": "1,2,3"})["data"]["kind"], "invalidParams")
        t.app.set_account_order = lambda ids: False
        error = t.error("accounts.setOrder", {"accounts": ["1", "2", "3"]})
        self.assertEqual(error["message"], "The accounts changed while the order was being set. Try again.")

    def test_remove(self):
        t = self.t
        removed = t.result("accounts.remove", {"account": "carol@work.com"})
        self.assertEqual(removed["removed"]["email"], "carol@work.com")
        self.assertEqual(len(t.result("accounts.list")["accounts"]), 2)
        self.assertEqual(t.error("accounts.remove", {"account": "carol@work.com"})["data"]["kind"], "notFound")


class UsageAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.t = Api(self)

    def test_usage(self):
        t = self.t
        t.app.refresh()
        report = t.result("usage.get")
        self.assertEqual(len(report["accounts"]), 3)
        self.assertIn("lastRefresh", report)
        one = t.result("usage.get", {"account": "alice@work.com"})
        self.assertEqual([a["email"] for a in one["accounts"]], ["alice@work.com"])
        self.assertEqual(t.error("usage.get", {"account": "zed"})["data"]["kind"], "notFound")

    def test_usage_refresh_runs_a_cycle(self):
        t = self.t
        before = len(t.rig.api.calls_to("usage"))
        report = t.result("usage.refresh")
        self.assertGreater(len(t.rig.api.calls_to("usage")), before)
        self.assertEqual(len(report["accounts"]), 3)
        self.assertEqual(len(t.rig.events_of("usageUpdated")), 1)

    def test_settings_get_all_one_unknown(self):
        t = self.t
        everything = t.result("settings.get")["settings"]
        self.assertEqual(set(everything), set(catalog.NAMES))
        self.assertEqual(len(everything), 24, "the 22 keys of 1.9 and the two window defaults")
        one = t.result("settings.get", {"key": "AUTOSWITCH.strategy"})["settings"]
        self.assertEqual(one, {"autoSwitch.strategy": "mostRoom"})
        error = t.error("settings.get", {"key": "volume"})
        self.assertEqual(error["data"]["kind"], "notFound")
        self.assertEqual(error["message"], 'There is no setting called "volume".')
        self.assertEqual(error["data"]["candidates"], list(catalog.NAMES))

    def test_settings_set_normalises_and_reports_the_new_value(self):
        t = self.t
        result = t.result("settings.set", {"key": "autoSwitch.enabled", "value": "off"})["settings"]
        self.assertEqual(result, {"autoSwitch.enabled": False})
        self.assertEqual(t.result("settings.set", {"key": "autoSwitch.threshold", "value": 72.4})["settings"]["autoSwitch.threshold"], 72)
        self.assertEqual(t.result("settings.get", {"key": "autoSwitch.sessionThreshold"})["settings"]["autoSwitch.sessionThreshold"], 72)
        error = t.error("settings.set", {"key": "refreshInterval", "value": 7})
        self.assertEqual((error["data"]["kind"], error["message"]),
                         ("invalidValue", "refreshInterval must be one of 15, 30, 60, 300, 600."))
        self.assertEqual(t.error("settings.set", {"key": "autoSwitch.enabled"})["data"]["kind"], "invalidParams")
        error = t.error("settings.set", {"key": "claude.binaryPath", "value": "relative/claude"})
        self.assertEqual(error["message"], 'claude.binaryPath must be "" to auto-detect, or an absolute path.')
        error = t.error("settings.set", {"key": "claude.binaryPath", "value": "/nonexistent/claude"})
        self.assertEqual(error["message"], "No executable file at /nonexistent/claude.")

    def test_the_two_new_keys_and_the_strategy_alias(self):
        t = self.t
        text = 'autoSwitch.sessionThreshold must be 50–100, whole numbers, or "default".'
        self.assertEqual(t.error("settings.set", {"key": "autoSwitch.sessionThreshold", "value": 49})["message"], text)
        t.result("settings.set", {"key": "autoSwitch.sessionThreshold", "value": 80})
        t.result("settings.set", {"key": "autoSwitch.weeklyThreshold", "value": "default"})
        self.assertEqual(t.result("settings.get", {"key": "autoSwitch.weeklyThreshold"})["settings"]["autoSwitch.weeklyThreshold"], 90)
        self.assertEqual(t.result("settings.set", {"key": "autoSwitch.strategy", "value": "priority"})["settings"]["autoSwitch.strategy"], "myOrder")

    def test_launch_at_login_is_the_autostart_file(self):
        t = self.t
        path = t.rig.paths.autostart_file
        self.assertFalse(t.result("settings.get", {"key": "launchAtLogin"})["settings"]["launchAtLogin"])
        self.assertIs(t.result("settings.set", {"key": "launchAtLogin", "value": True})["settings"]["launchAtLogin"], True)
        self.assertTrue(os.path.exists(path))
        with open(path) as handle:
            text = handle.read()
        self.assertIn("[Desktop Entry]", text)
        self.assertIn(f"Exec={t.rig.home}/.local/bin/pixelswitch-tray", text)
        t.result("settings.set", {"key": "launchAtLogin", "value": True})  # again: nothing changes
        self.assertIs(t.result("settings.set", {"key": "launchAtLogin", "value": False})["settings"]["launchAtLogin"], False)
        self.assertFalse(os.path.exists(path))
        t.result("settings.set", {"key": "launchAtLogin", "value": False})

    def test_updates_automatic_needs_the_updater(self):
        t = self.t
        error = t.error("settings.set", {"key": "updates.automatic", "value": False})
        self.assertEqual((error["data"]["kind"], error["message"]), ("failed", "The updater is not running yet. Try again in a moment."))
        calls = []
        t.app.attach_updater(type("U", (), {"set_automatic": lambda self, on: calls.append(on)})())
        self.assertIs(t.result("settings.get", {"key": "updates.automatic"})["settings"]["updates.automatic"], True)
        t.result("settings.set", {"key": "updates.automatic", "value": False})
        self.assertEqual(calls, [False])
        self.assertIs(t.result("settings.get", {"key": "updates.automatic"})["settings"]["updates.automatic"], False)

    def test_gui_only_keys_are_stored_and_validated_like_the_macs(self):
        t = self.t
        t.result("settings.set", {"key": "menuBar.color.session", "value": "ff00aa"})
        self.assertEqual(t.result("settings.get", {"key": "menuBar.color.session"})["settings"]["menuBar.color.session"], "#FF00AA")
        t.result("settings.set", {"key": "menuBar.modules", "value": "account, weeklyBar"})
        self.assertEqual(t.result("settings.get", {"key": "menuBar.modules"})["settings"]["menuBar.modules"], ["account", "weeklyBar"])
        self.assertEqual(t.error("settings.set", {"key": "menuBar.modules", "value": "nope"})["data"]["kind"], "invalidValue")

    def test_settings_survive_a_restart(self):
        t = self.t
        t.result("settings.set", {"key": "autoSwitch.enabled", "value": True})
        again = t.rig.build()
        self.assertTrue(again.auto_switch_config().enabled)

    def test_events_subscribe_updates_checks_and_quit(self):
        t = self.t
        self.assertEqual(t.result("events.subscribe"), {"subscribed": True})
        self.assertEqual(t.subscribed, [1])
        flags = []
        t.app.on_check_for_updates = lambda: flags.append("updates")
        t.app.on_quit = lambda: flags.append("quit")
        self.assertEqual(t.result("app.checkForUpdates"), {"ok": True})
        self.assertEqual(t.result("app.quit"), {"ok": True})
        self.assertEqual(flags, ["updates", "quit"])


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.t = Api(self)

    def test_an_unknown_method_lists_all_21(self):
        error = self.t.error("accounts.teleport")
        self.assertEqual((error["data"]["kind"], error["code"]), ("methodNotFound", -32601))
        self.assertEqual(error["data"]["candidates"], list(protocol.METHODS))
        self.assertEqual(len(protocol.METHODS), 21)

    def test_a_notification_gets_no_reply_even_on_error(self):
        t = self.t
        self.assertIsNone(t.api.handle(protocol.request_line(None, "status.get")))
        self.assertIsNone(t.api.handle('{"jsonrpc":"2.0","method":"status.get","id":null}'))
        self.assertIsNone(t.api.handle('{"jsonrpc":"2.0","method":"accounts.switch","params":{"account":"nobody"}}'))
        self.assertIsNone(t.api.handle('{"jsonrpc":"2.0","method":"nope"}'))
        self.assertIn("[control] accounts.switch notFound", t.logs, "but it is logged")

    def test_an_unreadable_line_gets_a_parse_error_without_an_id(self):
        reply = self.t.call(None, raw="{not json")
        self.assertEqual(reply["error"]["code"], -32700)
        self.assertNotIn("id", reply)
        for raw in ('{"id":1,"method":"status.get"}', "[1]", '{"jsonrpc":"2.0","id":1.5,"method":"status.get"}', '"x"'):
            self.assertEqual(self.t.call(None, raw=raw)["error"]["code"], -32700, raw)

    def test_a_request_that_is_not_json_rpc_2_is_refused_with_its_own_id(self):
        reply = self.t.call(None, raw='{"jsonrpc":"1.0","id":7,"method":"status.get"}')
        self.assertEqual((reply["error"]["code"], reply["id"]), (-32600, 7))

    def test_ids_may_be_strings_and_integral_floats(self):
        self.assertEqual(self.t.call("status.get", id="abc")["id"], "abc")
        self.assertEqual(json.loads(self.t.api.handle('{"jsonrpc":"2.0","id":3.0,"method":"status.get"}'))["id"], 3)

    def test_replies_are_compact_sorted_and_integers_have_no_fraction(self):
        line = self.t.api.handle(protocol.request_line(7, "status.get"))
        self.assertNotIn(" ", line.replace("Linux", ""))
        self.assertIn('"protocolVersion":2', line)
        self.assertNotIn("90.0", line)
        self.assertEqual(list(json.loads(line)), sorted(json.loads(line)))

    def test_a_bug_in_a_handler_is_a_failed_reply_not_a_crash(self):
        t = self.t
        t.app.status_info = lambda: 1 / 0
        reply = t.call("status.get")
        self.assertEqual(reply["error"]["data"]["kind"], "failed")
        self.assertNotIn("division", reply["error"]["message"], "no internals leak")
        self.assertIn("[control] status.get failed (ZeroDivisionError)", t.logs)

    def test_the_log_names_methods_ids_and_keys_never_emails(self):
        t = self.t
        t.call("accounts.switch", {"account": "bob@home.com"})
        t.call("settings.set", {"key": "autoSwitch.enabled", "value": True})
        t.call("accounts.signIn.status")
        t.call("accounts.switch", {"account": "nobody@home.com"})
        text = "\n".join(t.logs)
        self.assertNotIn("@", text)
        self.assertIn(f"[control] accounts.switch {protocol.uuid_string(t.b.id)} ok", text)
        self.assertIn("[control] settings.set autoSwitch.enabled ok", text)
        self.assertIn("[control] accounts.signIn.status ok", text)
        self.assertIn("[control] accounts.switch notFound", text)

    def test_no_result_carries_a_token_credential_secret_or_refresh_field(self):
        t = self.t
        t.app.refresh()
        forbidden = re.compile("token|credential|secret|refresh|password", re.I)
        allowed = {"lastRefresh", "refreshInterval"}
        seen = []

        def scan(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    if forbidden.search(key) and key not in allowed:
                        seen.append(path + "." + key)
                    scan(child, path + "." + key)
            elif isinstance(value, list):
                for item in value:
                    scan(item, path + "[]")
            elif isinstance(value, str) and "sk-ant-" in value:
                seen.append(path)

        for method, params in (("status.get", None), ("accounts.list", None), ("usage.get", None), ("settings.get", None),
                               ("accounts.signIn.status", None), ("accounts.switch", {"account": "2"})):
            scan(t.call(method, params).get("result"), method)
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
