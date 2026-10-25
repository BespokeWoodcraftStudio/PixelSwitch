"""The Linux CLI against the Mac CLI's golden outputs (ControlTests.swift and ControlSplitTests.swift
ported), then end to end against a scripted daemon on a real socket: parser, output formats, exit
codes, ``--json`` shapes, the check against an old daemon, sign-in, watch, and the Linux differences."""
from __future__ import annotations

import io
import os
import time
import unittest

from pixelswitch import cli, protocol as p, settings_catalog
from pixelswitch.cli import FOLLOW, CliUsageError, Command, Invocation, Output, Runner, parse
from pixelswitch.client import ControlClient
from pixelswitch.protocol import ControlError

from .lc_support import DaemonTestCase, account_payload, status_payload


def cmd(line: str) -> Command | None:
    try:
        return parse(line.split()).command
    except CliUsageError:
        return None


def refused(line: str) -> str | None:
    try:
        parse(line.split())
        return None
    except CliUsageError as error:
        return error.message


class ParserTests(unittest.TestCase):
    def test_no_arguments_shows_help(self):
        self.assertEqual(parse([]).command, Command("help"))

    def test_status_and_the_account_list(self):
        self.assertEqual(cmd("status"), Command("status"))
        self.assertEqual(cmd("accounts"), Command("listAccounts"))
        self.assertEqual(cmd("accounts list"), Command("listAccounts"))

    def test_switch_needs_exactly_one_account(self):
        self.assertEqual(cmd("switch 2"), Command("switchAccount", ("2",)))
        self.assertIsNotNone(refused("switch"))
        self.assertIsNotNone(refused("switch 1 2"))

    def test_sign_in_options(self):
        self.assertEqual(cmd("accounts add-current"), Command("addCurrent"))
        self.assertEqual(cmd("accounts sign-in"), Command("signIn", (None, None, False)))
        self.assertEqual(cmd("accounts sign-in --open --wait"), Command("signIn", (None, "default", True)))
        self.assertEqual(cmd("accounts sign-in --open-in Firefox"), Command("signIn", (None, "Firefox", False)))
        self.assertIsNotNone(refused("accounts sign-in --open-in"))
        self.assertEqual(cmd("accounts reauth bob@home.com --open-in Safari"), Command("signIn", ("bob@home.com", "Safari", False)))
        self.assertEqual(cmd("accounts sign-in code abc#def"), Command("signInCode", ("abc#def",)))
        self.assertEqual(cmd("accounts sign-in cancel"), Command("signInCancel"))
        self.assertEqual(cmd("accounts sign-in status"), Command("signInStatus"))

    def test_remove_refuses_without_yes_and_says_so(self):
        self.assertEqual(cmd("accounts remove 2 --yes"), Command("remove", ("2",)))
        self.assertIn("--yes", refused("accounts remove 2"))

    def test_label_can_have_spaces_and_clear(self):
        self.assertEqual(cmd("accounts label 2 Side project"), Command("label", ("2", "Side project")))
        self.assertEqual(cmd("accounts label 2 --clear"), Command("label", ("2", None)))

    def test_a_threshold_is_a_number_default_or_manual(self):
        self.assertEqual(cmd("accounts threshold 2 75%"), Command("threshold", ("2", 75.0)))
        self.assertEqual(cmd("accounts threshold 2 default"), Command("threshold", ("2", None)))
        self.assertIsNotNone(refused("accounts threshold 2 high"))
        for text in ("manual", "MANUAL", "0", "0%"):
            self.assertEqual(cmd(f"accounts threshold 2 {text}"), Command("threshold", ("2", 0.0)), text)
        self.assertEqual(cmd("accounts threshold 2 35"), Command("threshold", ("2", 35.0)))
        for word in ("off", "never", "high"):
            self.assertIn("manual", refused(f"accounts threshold 2 {word}"), word)

    def test_order_lists_the_accounts(self):
        self.assertEqual(cmd("accounts order 1 4 2"), Command("order", ("1", "4", "2")))
        self.assertIsNotNone(refused("accounts order"))

    def test_usage_for_all_or_one(self):
        self.assertEqual(cmd("usage"), Command("usage", (None,)))
        self.assertEqual(cmd("usage 2"), Command("usage", ("2",)))

    def test_settings_get_and_set(self):
        self.assertEqual(cmd("settings"), Command("settingsGet", (None,)))
        self.assertEqual(cmd("settings autoSwitch.enabled"), Command("settingsGet", ("autoSwitch.enabled",)))
        self.assertEqual(cmd("settings set menuBar.modules account weeklyBar"),
                         Command("settingsSet", ("menuBar.modules", "account weeklyBar")))

    def test_the_simple_commands(self):
        for word, kind in (("refresh", "refresh"), ("watch", "watch"), ("update-check", "updateCheck"), ("quit", "quit"), ("mcp", "mcp")):
            self.assertEqual(cmd(word), Command(kind), word)

    def test_json_and_timeout_work_anywhere(self):
        self.assertEqual(parse(["--json", "usage", "--timeout", "90"]), Invocation(Command("usage", (None,)), True, 90.0))

    def test_a_bad_timeout_and_an_unknown_command_are_refused(self):
        self.assertIsNotNone(refused("usage --timeout soon"))
        self.assertIsNotNone(refused("usage --timeout"))
        self.assertIsNotNone(refused("usage --timeout 0"))
        self.assertIsNotNone(refused("teleport"))

    def test_help_and_version_anywhere(self):
        self.assertEqual(parse(["status", "--help"]).command, Command("help"))
        self.assertEqual(parse(["status", "-h"]).command, Command("help"))
        self.assertEqual(parse(["--version"]).command, Command("version"))
        self.assertEqual(parse(["version"]).command, Command("version"))

    def test_a_setting_value_is_json_when_it_is_json_otherwise_text(self):
        self.assertIs(cli.value_from("true"), True)
        self.assertEqual(cli.value_from("90"), 90)
        self.assertEqual(cli.value_from('["account","weeklyBar"]'), ["account", "weeklyBar"])
        self.assertEqual(cli.value_from("resetsSoonest"), "resetsSoonest")
        self.assertEqual(cli.value_from("NaN"), "NaN")

    # -- 2.0 --

    def test_session_and_weekly_flags(self):
        self.assertEqual(cmd("accounts threshold 2 --session 90 --weekly 50"), Command("windowThresholds", ("2", 90.0, 50.0)))
        self.assertEqual(cmd("accounts threshold 2 --weekly 50"), Command("windowThresholds", ("2", None, 50.0)))
        self.assertEqual(cmd("accounts threshold --session 75% 2"), Command("windowThresholds", ("2", 75.0, None)))
        self.assertEqual(cmd("accounts threshold 2 --session default --weekly DEFAULT"), Command("windowThresholds", ("2", FOLLOW, FOLLOW)))

    def test_the_old_threshold_syntax_is_unchanged(self):
        self.assertEqual(cmd("accounts threshold 2 75"), Command("threshold", ("2", 75.0)))
        self.assertEqual(cmd("accounts threshold 2 default"), Command("threshold", ("2", None)))
        self.assertEqual(cmd("accounts threshold 2 manual"), Command("threshold", ("2", 0.0)))

    def test_bad_flag_values_a_missing_account_and_stray_words_are_refused(self):
        for line in ("accounts threshold 2 --session", "accounts threshold 2 --session x", "accounts threshold 2 --session 101",
                     "accounts threshold 2 --session 0", "accounts threshold --session 80", "accounts threshold 2 3 --session 80",
                     "accounts threshold 2 --bogus 80"):
            self.assertIsNotNone(refused(line), line)

    def test_manual_for_one_window_points_at_the_whole_account_command(self):
        self.assertIn("pixelswitch accounts threshold <account> manual", refused("accounts threshold 2 --session manual"))

    def test_priority_takes_1_to_99_or_none(self):
        self.assertEqual(cmd("accounts priority 2 1"), Command("priority", ("2", 1)))
        self.assertEqual(cmd("accounts priority bob@home.com 99"), Command("priority", ("bob@home.com", 99)))
        self.assertEqual(cmd("accounts priority 2 none"), Command("priority", ("2", None)))
        self.assertEqual(cmd("accounts priority 2 NONE"), Command("priority", ("2", None)))

    def test_a_priority_outside_1_to_99_with_a_p_or_fractional_is_refused(self):
        for word in ("0", "100", "P1", "-3", "1.5", "high"):
            self.assertIn("1 to 99", refused(f"accounts priority 2 {word}"), word)
        self.assertIsNotNone(refused("accounts priority 2"))
        self.assertIsNotNone(refused("accounts priority"))

    def test_the_help_names_the_new_commands(self):
        text = cli.HELP_TEXT
        self.assertIn("--session", text)
        self.assertIn("accounts priority <account> <1-99|none>", text)
        self.assertIn("<account> is an email address, a label, an id, or a position", text)
        self.assertIn("never a priority number", text)


# --- output -------------------------------------------------------------------------------------------


def info(identifier, email, position, active=False, threshold=None, session=None, weekly=None, effective=(90, 90),
         manual=False, priority=None, switchable=True, label=None, usage=None):
    return account_payload(
        identifier, email, position, label=label, isActive=active, isSwitchable=switchable, threshold=threshold,
        sessionThreshold=session, weeklyThreshold=weekly, effectiveThreshold=min(effective), manualOnly=manual,
        priority=priority, effectiveSessionThreshold=effective[0], effectiveWeeklyThreshold=effective[1], usage=usage,
    )


def old_info(identifier, email, position, **kw):
    """A 1.9 AccountInfo: none of the 2.0 fields."""
    base = info(identifier, email, position, **kw)
    for key in ("priority", "sessionThreshold", "weeklyThreshold", "effectiveSessionThreshold", "effectiveWeeklyThreshold"):
        base.pop(key, None)
    return base


class OutputTests(unittest.TestCase):
    def setUp(self):
        os.environ["TZ"] = "UTC"
        time.tzset()

    def test_the_table_with_a_priority_column(self):
        usage = {"session": {"utilization": 42.4}, "weekly": {"utilization": 61}}
        accounts = [info("1", "alice@work.com", 1, active=True, label="Work", usage=usage, priority=1),
                    info("2", "bob@home.com", 2, threshold=70, effective=(70, 70), switchable=False)]
        lines = Output.accounts(accounts).split("\n")
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("#  Account") and lines[0].endswith("Switches at    Priority"))
        self.assertTrue(lines[1].startswith("*  1  alice@work.com (Work)"))
        self.assertIn("42%", lines[1])
        self.assertIn("90% (default)", lines[1])
        self.assertTrue(lines[2].startswith("!  2  bob@home.com"))
        self.assertIn("70%", lines[2])
        self.assertNotIn("default", lines[2])
        self.assertIn("add-current", Output.accounts([]))

    def test_equal_windows_are_1_9s_table_plus_the_last_column(self):
        equal = [info("1", "alice@work.com", 1, active=True, priority=1), info("2", "bob@home.com", 2, threshold=70, effective=(70, 70))]
        lines = Output.accounts(equal).split("\n")
        self.assertEqual(lines[0], "#  Account         Session  Weekly  Fable  Switches at    Priority")
        self.assertEqual("\n".join(lines[1:3]),
                         "*  1  alice@work.com  —        —       —      90% (default)  P1\n"
                         "2  bob@home.com    —        —       —      70%            —")
        self.assertEqual(lines[-1], "* active   ! cannot be switched to until it is re-signed (pixelswitch accounts reauth <account>)")

    def test_manual_only_shows_where_it_is_left_and_the_legend(self):
        carol = info("3", "carol@work.com", 3, threshold=0, manual=True)
        lines = Output.accounts([info("1", "alice@work.com", 1, active=True), carol]).split("\n")
        self.assertTrue(any("carol@work.com" in l and "90% (manual only)" in l for l in lines))
        self.assertTrue(any(l.startswith("manual only:") for l in lines))

    def test_threshold_set_sentences(self):
        carol = info("3", "carol@work.com", 3, threshold=0, manual=True)
        carol_active = info("3", "carol@work.com", 3, active=True, threshold=0, manual=True)
        self.assertEqual(Output.threshold_set(carol),
                         "carol@work.com is now manual only: auto-switch never switches to it. You can still switch to it yourself.")
        self.assertEqual(Output.threshold_set(carol_active),
                         "carol@work.com is now manual only: auto-switch never switches to it. You can still switch to it yourself. "
                         "You're using it now: auto-switch moves you off it at 90% and won't move you back to it.")
        self.assertEqual(Output.threshold_set(info("1", "alice@work.com", 1)), "alice@work.com now follows the default threshold (90%).")
        self.assertEqual(Output.threshold_set(info("2", "bob@home.com", 2, threshold=70, effective=(70, 70))), "bob@home.com now switches at 70%.")
        self.assertEqual(Output.threshold_set(None), "Threshold set.")

    def test_switched_to_a_manual_only_account_says_where_it_is_left(self):
        carol_active = info("3", "carol@work.com", 3, active=True, threshold=0, manual=True)
        self.assertEqual(Output.switched(carol_active),
                         "Switched to carol@work.com. It is manual only: auto-switch moves you off it at 90% and won't move you back to it.")
        self.assertEqual(Output.switched(info("2", "bob@home.com", 2)), "Switched to bob@home.com.")

    def test_percentages_are_whole_numbers_half_away_from_zero(self):
        self.assertEqual(Output.percent(99.6), "100%")
        self.assertEqual(Output.percent(None), "—")
        self.assertEqual(Output.percent(0.5), "1%")
        self.assertEqual(Output.percent(2.5), "3%")
        self.assertEqual(Output.percent(42.4), "42%")

    def test_status_spells_out_auto_switch(self):
        status = status_payload(autoSwitch={"onFable": False, "strategy": "resetsSoonest", "drainWithinHours": 12})
        text = Output.status(status)
        self.assertIn("Active:        alice@work.com", text)
        self.assertIn("on, default 90%, next by resets soonest, Fable ignored, switches early within 12 h", text)

    def test_status_full_layout(self):
        status = status_payload(lastRefresh="2026-10-24T17:08:00Z")
        self.assertEqual(Output.status(status), "\n".join([
            "PixelSwitch 2.0 (remote-control protocol 2)",
            "Active:        alice@work.com",
            "Accounts:      2",
            "Auto-switch:   on, default 90%, next by most room left",
            "Claude CLI:    found",
            "Last refresh:  2026-10-24 17:08",
        ]))

    def test_status_with_auto_switch_off_and_nothing_active(self):
        status = status_payload(activeAccountId=None, activeAccountEmail=None, claudeAvailable=False, accountCount=0,
                                autoSwitch={"enabled": False})
        text = Output.status(status)
        self.assertIn("Active:        none", text)
        self.assertIn("Auto-switch:   off", text)
        self.assertIn("Claude CLI:    not found", text)
        self.assertIn("Last refresh:  —", text)

    def test_status_prints_the_daemons_warnings_one_line_each(self):
        status = status_payload(warnings=["CLAUDE_CONFIG_DIR is set in the daemon's environment; PixelSwitch ignores it."])
        self.assertTrue(Output.status(status).endswith("Warning:       CLAUDE_CONFIG_DIR is set in the daemon's environment; PixelSwitch ignores it."))
        two = status_payload(warnings=["one", "two"])
        self.assertTrue(Output.status(two).endswith("Warning:       one\nWarning:       two"))

    def test_sign_in_shows_both_links_and_what_to_do_with_the_code(self):
        sign_in = {"id": "S", "purpose": "reauthenticate", "accountId": "2", "email": "bob@home.com", "state": "waitingForUser",
                   "automaticLink": "https://a", "manualLink": "https://m", "codeSubmitted": False, "startedAt": "2026-10-24T17:00:00Z"}
        text = Output.sign_in(sign_in)
        self.assertTrue(text.startswith("Re-sign bob@home.com: waiting for someone to sign in in a browser"))
        self.assertIn("  https://a", text)
        self.assertIn("On the computer running PixelSwitch, open this in any browser", text)
        self.assertIn("pixelswitch accounts sign-in code <code>", text)

    def test_state_words(self):
        base = {"purpose": "newAccount", "codeSubmitted": False}
        for state, words in (("starting", "getting the sign-in link"), ("completing", "saving the account"),
                             ("succeeded", "signed in"), ("cancelled", "cancelled")):
            self.assertEqual(Output.state_words({**base, "state": state}), words)
        self.assertEqual(Output.state_words({**base, "state": "waitingForUser", "codeSubmitted": True}), "checking the code")
        self.assertEqual(Output.state_words({**base, "state": "failed"}), "failed: unknown reason")
        self.assertEqual(Output.state_words({**base, "state": "failed", "message": "no"}), "failed: no")

    def test_settings_print_in_the_windows_order_unknown_keys_after(self):
        self.assertEqual(Output.settings({"refreshInterval": 300, "autoSwitch.enabled": True}), "refreshInterval = 300\nautoSwitch.enabled = true")
        self.assertEqual(Output.settings({"zzz": 1, "aaa": 2, "autoSwitch.enabled": False}), "autoSwitch.enabled = false\naaa = 2\nzzz = 1")

    def test_the_tools_list_of_keys_matches_the_daemons(self):
        self.assertEqual(list(cli.SETTING_KEY_NAMES), list(settings_catalog.NAMES))

    def test_usage_has_no_machine_line_on_linux(self):
        report = {"accounts": [info("1", "alice@work.com", 1, active=True, usage={
            "session": {"utilization": 42, "resetsAt": "2026-09-25T11:00:00Z"}, "weekly": {"utilization": 61, "resetsAt": "2026-09-30T02:00:00.000000+00:00"},
            "fable": {"utilization": 10}, "error": "Session expired.", "sampledAt": "2026-09-25T12:00:00Z"}), info("2", "b@x.com", 2)]}
        self.assertEqual(Output.usage(report), "\n".join([
            "1. alice@work.com  [active]",
            "   Session 42% (resets 2026-09-25 11:00)",
            "   Weekly  61% (resets 2026-09-30 02:00)",
            "   Fable   10%",
            "   Note: Session expired.",
            "   Read at 2026-09-25 12:00",
            "2. b@x.com",
            "   No reading yet.",
        ]))
        self.assertNotIn("This Mac", Output.usage(report))
        self.assertEqual(Output.usage(None), "No usage yet.")

    # -- 2.0 --

    def test_split_windows_read_5h_and_7d_in_switches_at(self):
        split = [info("1", "alice@work.com", 1, active=True, session=90, weekly=50, effective=(90, 50), priority=1),
                 info("2", "bob@home.com", 2, effective=(90, 50), priority=3),
                 info("3", "carol@work.com", 3, threshold=0, effective=(90, 50), manual=True)]
        cells = Output.accounts(split).split("\n")
        self.assertIn("5h 90% / 7d 50% ", cells[1])
        self.assertTrue(cells[1].endswith("P1"))
        self.assertNotIn("(default)", cells[1])
        self.assertIn("5h 90% / 7d 50% (default) ", cells[2])
        self.assertTrue(cells[2].endswith("P3"))
        self.assertIn("5h 90% / 7d 50% (manual only)", cells[3])
        self.assertTrue(cells[3].endswith("—"))
        self.assertEqual(Output.switches_at(split[0]), "5h 90% / 7d 50%")
        self.assertEqual(Output.switches_at(info("1", "a@x.com", 1, active=True)), "90% (default)")
        self.assertEqual(Output.priority_cell(split[1]), "P3")
        self.assertEqual(Output.priority_cell(split[2]), "—")

    def test_threshold_set_in_words_for_a_split(self):
        split = info("1", "alice@work.com", 1, active=True, session=90, weekly=50, effective=(90, 50))
        self.assertEqual(Output.threshold_set(split), "alice@work.com now switches at 90% of its session or 50% of its week.")
        both_own = info("4", "dan@x.com", 4, session=80, weekly=80, effective=(80, 80))
        self.assertEqual(Output.threshold_set(both_own), "dan@x.com now switches at 80%.")
        manual_active = info("5", "eve@x.com", 5, active=True, threshold=0, effective=(90, 50), manual=True)
        self.assertTrue(Output.threshold_set(manual_active).endswith(
            "You're using it now: auto-switch moves you off it at 90% of its session or 50% of its week and won't move you back to it."))
        self.assertEqual(Output.switched(manual_active),
                         "Switched to eve@x.com. It is manual only: auto-switch moves you off it at 90% of its session or 50% of its week and won't move you back to it.")

    def test_status_default_words(self):
        def auto(**kw):
            base = {"enabled": True, "defaultThreshold": 98, "onFable": True, "strategy": "mostRoom", "drainEarly": True, "drainWithinHours": 24}
            base.update(kw)
            return {k: v for k, v in base.items() if v is not None}

        def status(a):
            return {"appVersion": "2.0", "protocolVersion": 2, "accountCount": 0, "claudeAvailable": True, "autoSwitch": a}

        split = auto(defaultThreshold=90, strategy="myOrder", defaultSessionThreshold=90, defaultWeeklyThreshold=50)
        self.assertIn("Auto-switch:   on, default 5h 90% / 7d 50%, next by priority", Output.status(status(split)))
        equal = auto(defaultSessionThreshold=98, defaultWeeklyThreshold=98)
        old = auto()
        for a in (equal, old):
            self.assertIn("Auto-switch:   on, default 98%, next by most room left", Output.status(status(a)))
        moved = auto(defaultSessionThreshold=95, defaultWeeklyThreshold=95)
        lower = auto(defaultThreshold=90, defaultSessionThreshold=80, defaultWeeklyThreshold=80)
        self.assertIn("default 95%, next by most room left", Output.status(status(moved)))
        self.assertIn("default 80%, next by most room left", Output.status(status(lower)))
        self.assertEqual((Output.default_words(moved), Output.default_words(old), Output.default_words(split)), ("95%", "98%", "5h 90% / 7d 50%"))
        self.assertEqual((Output.strategy_words("myOrder"), Output.strategy_words("resetsSoonest"), Output.strategy_words("mostRoom")),
                         ("priority", "resets soonest", "most room left"))

    def test_priority_and_order_sentences(self):
        equal = [info("1", "alice@work.com", 1, active=True, priority=1), info("2", "bob@home.com", 2, threshold=70, effective=(70, 70))]
        text = Output.priority_set(equal, "mostRoom")
        self.assertTrue(text.startswith(Output.accounts(equal)))
        self.assertIn("Auto-switch follows these numbers only when it chooses by Priority.", text)
        self.assertIn("pixelswitch settings set autoSwitch.strategy priority", text)
        self.assertEqual(Output.priority_set(equal, "myOrder"), Output.accounts(equal))
        numbered = Output.order_set(equal, True)
        self.assertTrue(numbered.startswith(Output.accounts(equal)))
        self.assertIn("numbered from 1", numbered)
        self.assertIn("replacing any priority numbers", numbered)
        self.assertEqual(Output.order_set(equal, False), Output.accounts(equal))

    def test_a_1_9_accounts_payload_decodes_and_prints_old_numbers(self):
        payload = [old_info("1", "alice@work.com", 1, active=True, label="Work", effective=(98, 98)),
                   old_info("2", "bob@home.com", 2, threshold=0, effective=(98, 98), manual=True)]
        for a in payload:
            a["effectiveThreshold"] = 98
        decoded = cli.decode_accounts(payload)
        self.assertEqual(len(decoded), 2)
        table = Output.accounts(decoded)
        self.assertIn("98% (default)", table)
        self.assertIn("98% (manual only)", table)
        self.assertTrue(table.startswith("#  Account"))
        self.assertIn("Priority", table)

    def test_an_account_list_that_does_not_decode_is_no_accounts(self):
        self.assertEqual(cli.decode_accounts([{"email": "x"}]), [])
        self.assertEqual(cli.decode_accounts("nope"), [])

    def test_pretty_json_matches_foundation(self):
        value = {"a": [], "b": {}, "c": 90.0, "d": "a/b é", "e": ["x"]}
        self.assertEqual(cli.pretty(value),
                         '{\n  "a" : [\n\n  ],\n  "b" : {\n\n  },\n  "c" : 90,\n  "d" : "a/b é",\n  "e" : [\n    "x"\n  ]\n}')


# --- end to end ----------------------------------------------------------------------------------------


class Captured:
    def __init__(self):
        self.out: list[str] = []
        self.err: list[str] = []


class RunnerTestCase(DaemonTestCase):
    def run_cli(self, *arguments, env=None):
        captured = Captured()
        client = ControlClient(env=env or self.env())
        runner = Runner(client, out=captured.out.append, err=captured.err.append, poll_interval=0.01,
                        sleep=lambda s: time.sleep(min(s, 0.01)), version_text=lambda: "2.0")
        try:
            code = runner.run(parse(list(arguments)))
        finally:
            client.disconnect()
        return code, "\n".join(captured.out), "\n".join(captured.err)


class RunnerTests(RunnerTestCase):
    def test_help_and_version_need_no_daemon(self):
        code, out, _ = self.run_cli("help")
        self.assertEqual((code, out), (0, cli.HELP_TEXT))
        code, out, _ = self.run_cli("--version")
        self.assertEqual((code, out), (0, "pixelswitch 2.0 (PixelSwitch remote-control protocol 2)"))

    def test_nothing_listening_is_exit_5(self):
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 5)
        self.assertTrue(err.startswith("error: PixelSwitch is not reachable: nothing is listening at "))

    def test_status_text_and_json(self):
        self.serve()
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("PixelSwitch 2.0 (remote-control protocol 2)"))
        code, out, _ = self.run_cli("--json", "status")
        self.assertEqual(code, 0)
        self.assertEqual(p.loads(out), status_payload())

    def test_a_daemon_speaking_another_protocol_is_refused_with_what_to_do(self):
        self.serve({"status.get": status_payload(protocolVersion=99, appVersion="1.1")})
        code, _, err = self.run_cli("accounts")
        self.assertEqual(code, 1)
        self.assertIn("PixelSwitch 1.1 speaks remote-control protocol 99", err)
        self.assertIn("Quit and reopen PixelSwitch", err)

    def test_error_exit_codes_and_choices(self):
        for kind, code in (("busy", 3), ("notFound", 4), ("ambiguous", 4), ("invalidValue", 2), ("invalidParams", 2), ("failed", 1)):
            def raise_it(params, request, kind=kind):
                raise ControlError(kind, f"{kind} text", ["a@x.com", "b@x.com"] if kind == "notFound" else [])

            self.serve({"accounts.switch": raise_it})
            result, _, err = self.run_cli("switch", "nobody")
            self.assertEqual(result, code, kind)
            self.assertTrue(err.startswith(f"error: {kind} text"), err)
            if kind == "notFound":
                self.assertIn("  choices: a@x.com, b@x.com", err)
            self.daemons.pop().close()
            os.unlink(self.path)

    def test_switch_prints_the_account(self):
        self.serve({"accounts.switch": {"account": info("2", "bob@home.com", 2, active=True)}})
        code, out, _ = self.run_cli("switch", "2")
        self.assertEqual((code, out), (0, "Switched to bob@home.com."))
        self.assertEqual(self.daemons[0].last_params("accounts.switch"), {"account": "2"})

    def test_the_slow_calls_wait_at_least_120_seconds(self):
        # The floor is the client's; the CLI passes --timeout through. A fast daemon proves nothing
        # about waiting, so check the rule itself.
        from pixelswitch.client import call_timeout

        self.assertEqual(call_timeout("accounts.switch", 30), 120)
        self.assertEqual(call_timeout("usage.refresh", 200), 200)
        self.assertEqual(call_timeout("status.get", 30), 30)

    def test_usage_is_not_slow_and_prints_no_machine_line(self):
        self.serve({"usage.get": {"accounts": [info("1", "alice@work.com", 1, active=True)]}})
        code, out, _ = self.run_cli("usage")
        self.assertEqual(code, 0)
        self.assertNotIn("This Mac", out)
        self.assertIn("1. alice@work.com  [active]", out)

    def test_remove_label_and_the_other_one_liners(self):
        self.serve({
            "accounts.remove": {"removed": info("2", "bob@home.com", 2, label="Home")},
            "accounts.setLabel": {"account": info("2", "bob@home.com", 2, label="Side")},
            "accounts.addCurrent": {"account": info("3", "c@x.com", 3)},
            "accounts.signIn.submitCode": {"signIn": None}, "accounts.signIn.cancel": {"signIn": None},
            "app.checkForUpdates": {"ok": True}, "app.quit": {"ok": True},
        })
        self.assertEqual(self.run_cli("accounts", "remove", "2", "--yes")[1], "Removed bob@home.com (Home).")
        self.assertEqual(self.run_cli("accounts", "label", "2", "Side")[1], "Label set: bob@home.com (Side).")
        self.assertEqual(self.run_cli("accounts", "label", "2", "--clear")[1], "Label cleared.")
        self.assertEqual(self.run_cli("accounts", "add-current")[1], "Saved c@x.com.")
        self.assertEqual(self.run_cli("accounts", "sign-in", "code", "abc#d")[1], "Code handed to Claude Code; it is checking it now.")
        self.assertEqual(self.run_cli("accounts", "sign-in", "cancel")[1], "Sign-in cancelled.")
        self.assertEqual(self.run_cli("update-check")[1], "PixelSwitch is checking for updates; a newer version, if there is one, installs by itself.")
        self.assertEqual(self.run_cli("quit")[1], "PixelSwitch is quitting.")
        self.assertEqual(self.daemons[0].last_params("accounts.setLabel"), {"account": "2", "label": None})

    def test_settings_get_and_set_send_json_values(self):
        self.serve({"settings.get": {"settings": {"autoSwitch.enabled": True}}, "settings.set": {"settings": {"refreshInterval": 60}}})
        self.assertEqual(self.run_cli("settings", "autoSwitch.enabled")[1], "autoSwitch.enabled = true")
        self.assertEqual(self.run_cli("settings", "set", "refreshInterval", "60")[1], "refreshInterval = 60")
        self.assertEqual(self.daemons[0].last_params("settings.set"), {"key": "refreshInterval", "value": 60})

    # -- sign-in --

    def sign_in(self, state, **extra):
        base = {"id": "S", "purpose": "newAccount", "state": state, "codeSubmitted": False, "startedAt": "2026-10-24T17:00:00Z"}
        base.update(extra)
        return base

    def test_sign_in_json_is_unwrapped_and_status_json_is_wrapped(self):
        waiting = self.sign_in("waitingForUser", automaticLink="https://a", manualLink="https://m")
        self.serve({"accounts.signIn.start": {"signIn": waiting}, "accounts.signIn.status": {"signIn": waiting}})
        code, out, _ = self.run_cli("--json", "accounts", "sign-in")
        self.assertEqual(code, 0)
        self.assertEqual(p.loads(out)["state"], "waitingForUser")
        code, out, _ = self.run_cli("--json", "accounts", "sign-in", "status")
        self.assertEqual(p.loads(out)["signIn"]["state"], "waitingForUser")

    def test_sign_in_waits_for_the_links_then_prints_them(self):
        answers = iter([self.sign_in("starting")])
        self.serve({
            "accounts.signIn.start": lambda params, request: {"signIn": next(answers)},
            "accounts.signIn.status": {"signIn": self.sign_in("waitingForUser", automaticLink="https://a", manualLink="https://m")},
        })
        code, out, _ = self.run_cli("accounts", "sign-in", "--open")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("New account: waiting for someone to sign in in a browser"))
        self.assertEqual(self.daemons[0].last_params("accounts.signIn.start"), {"open": "default"})

    def test_sign_in_wait_follows_the_states_and_exits_by_the_outcome(self):
        states = iter(["waitingForUser", "completing", "succeeded"])
        self.serve({
            "accounts.signIn.start": {"signIn": self.sign_in("waitingForUser", automaticLink="https://a")},
            "accounts.signIn.status": lambda params, request: {"signIn": self.sign_in(next(states))},
        })
        code, out, _ = self.run_cli("accounts", "reauth", "bob@home.com", "--wait")
        self.assertEqual(code, 0)
        lines = out.split("\n")
        self.assertIn("… saving the account", lines)
        self.assertEqual(lines[-1], "… signed in")
        self.assertEqual(self.daemons[0].last_params("accounts.signIn.start"), {"account": "bob@home.com"})

    def test_sign_in_wait_fails_with_exit_1_when_the_sign_in_fails(self):
        self.serve({
            "accounts.signIn.start": {"signIn": self.sign_in("waitingForUser")},
            "accounts.signIn.status": {"signIn": self.sign_in("failed", message="No.")},
        })
        code, out, _ = self.run_cli("accounts", "sign-in", "--wait")
        self.assertEqual(code, 1)
        self.assertIn("… failed: No.", out)

    def test_sign_in_status_with_none_started(self):
        self.serve({"accounts.signIn.status": {"signIn": None}})
        self.assertEqual(self.run_cli("accounts", "sign-in", "status")[1], "No sign-in has been started.")

    # -- watch --

    def test_watch_prints_each_events_params_and_ends_when_the_daemon_goes(self):
        events = [p.event_line("usageUpdated", 1_000_000, {"x": 1}), p.event_line("autoSwitched", 1_000_001, {"from": "A", "to": "B"})]
        daemon = self.serve(events=events)
        captured = Captured()
        client = ControlClient(env=self.env())
        runner = Runner(client, out=captured.out.append, err=captured.err.append)
        import threading

        threading.Timer(0.5, daemon.close).start()
        code = runner.run(parse(["watch"]))
        client.disconnect()
        self.assertEqual(code, 1)
        self.assertEqual(captured.err[0], "Watching PixelSwitch. Press Control-C to stop.")
        self.assertEqual(captured.err[-1], "error: PixelSwitch closed the connection.")
        self.assertEqual(captured.out, [
            '{"at":"1970-01-12T13:46:40Z","data":{"x":1},"type":"usageUpdated"}',
            '{"at":"1970-01-12T13:46:41Z","data":{"from":"A","to":"B"},"type":"autoSwitched"}',
        ])

    # -- the 2.0 commands against a 2.0 daemon --

    def test_each_flag_puts_only_what_was_named_on_the_wire(self):
        two = {"account": info("1", "alice@work.com", 1, active=True, session=90, weekly=50, effective=(90, 50))}
        self.serve({"accounts.setThresholds": two, "accounts.setPriority": {"accounts": [info("1", "alice@work.com", 1, active=True, priority=5)]}})
        daemon = self.daemons[0]
        self.assertEqual(self.run_cli("accounts", "threshold", "1", "--weekly", "50")[0], 0)
        self.assertEqual(daemon.last_params("accounts.setThresholds"), {"account": "1", "weekly": 50})
        self.run_cli("accounts", "threshold", "1", "--session", "80")
        self.assertEqual(daemon.last_params("accounts.setThresholds"), {"account": "1", "session": 80})
        self.run_cli("accounts", "threshold", "1", "--session", "default")
        self.assertEqual(daemon.last_params("accounts.setThresholds"), {"account": "1", "session": None})
        code, out, _ = self.run_cli("accounts", "threshold", "1", "--session", "90", "--weekly", "50")
        self.assertEqual((code, out), (0, "alice@work.com now switches at 90% of its session or 50% of its week."))
        self.run_cli("accounts", "priority", "1", "none")
        self.assertEqual(daemon.last_params("accounts.setPriority"), {"account": "1", "priority": None})

    def test_priority_says_the_numbers_do_nothing_unless_the_strategy_is_priority(self):
        self.serve({"accounts.setPriority": {"accounts": [info("1", "alice@work.com", 1, active=True, priority=5)]}})
        code, out, _ = self.run_cli("accounts", "priority", "1", "5")
        self.assertEqual(code, 0)
        self.assertIn("Auto-switch follows these numbers only when it chooses by Priority.", out)
        self.daemons.pop().close()
        os.unlink(self.path)
        self.serve({"status.get": status_payload(autoSwitch={"strategy": "myOrder"}),
                    "accounts.setPriority": {"accounts": [info("1", "alice@work.com", 1, active=True, priority=6)]}})
        code, out, _ = self.run_cli("accounts", "priority", "1", "6")
        self.assertTrue(out.startswith("#  Account"))
        self.assertNotIn("Auto-switch follows", out)

    def test_order_says_it_numbered_every_account_from_1(self):
        self.serve({"accounts.setOrder": {"accounts": [info("1", "a@x.com", 1, priority=1)]}})
        code, out, _ = self.run_cli("accounts", "order", "1")
        self.assertIn("numbered from 1", out)

    # -- against an old daemon --

    def old_daemon(self):
        status = {"accountCount": 2, "activeAccountEmail": "alice@work.com", "activeAccountId": "11111111-1111-1111-1111-111111111111",
                  "appVersion": "1.9", "claudeAvailable": True, "protocolVersion": 2,
                  "autoSwitch": {"defaultThreshold": 98, "drainEarly": True, "drainWithinHours": 24, "enabled": True, "onFable": True, "strategy": "myOrder"}}
        accounts = {"accounts": [old_info("1", "alice@work.com", 1, active=True, label="Work", effective=(98, 98)),
                                 old_info("2", "bob@home.com", 2, threshold=0, effective=(98, 98), manual=True)]}
        for a in accounts["accounts"]:
            a["effectiveThreshold"] = 98
        return self.serve({"status.get": status, "accounts.list": accounts, "accounts.setOrder": accounts})

    def test_the_new_commands_stop_before_they_are_sent_to_an_old_daemon(self):
        daemon = self.old_daemon()
        code, out, _ = self.run_cli("accounts")
        self.assertEqual(code, 0)
        self.assertIn("alice@work.com (Work)", out)
        self.assertIn("98% (default)", out)
        code, _, err = self.run_cli("accounts", "threshold", "1", "--session", "90")
        self.assertEqual((code, err), (1, "error: This PixelSwitch (1.9) cannot set separate thresholds; update it."))
        code, _, err = self.run_cli("accounts", "priority", "1", "1")
        self.assertEqual((code, err), (1, "error: This PixelSwitch (1.9) cannot set priorities; update it."))
        self.assertNotIn("accounts.setThresholds", daemon.methods_asked())
        self.assertNotIn("accounts.setPriority", daemon.methods_asked())
        code, out, _ = self.run_cli("accounts", "order", "2", "1")
        self.assertEqual(code, 0)
        self.assertNotIn("numbered from 1", out)
        code, _, _ = self.run_cli("accounts", "threshold", "1", "80")
        self.assertEqual(code, 1)  # the stand-in has no such method: methodNotFound
        self.assertIn("accounts.setThreshold", daemon.methods_asked())


class MainTests(unittest.TestCase):
    def test_a_usage_error_is_exit_2_on_stderr(self):
        import contextlib

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["teleport"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), 'error: Unknown command "teleport". Run `pixelswitch help`.\n')


if __name__ == "__main__":
    unittest.main()
