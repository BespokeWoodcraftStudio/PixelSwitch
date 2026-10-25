"""Sign-in: the parser (ported from SignInTests.swift), the rules, the capture folder, the ``claude``
lookup and environment, and a whole session against the stub ``claude`` (links, code box, cancel,
the limits, and NO 10-second fallback)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
import unittest
from dataclasses import dataclass

from pixelswitch import signin, signin_parse as parse
from pixelswitch.claude_cli import AuthStatus, ClaudeCli, ClaudeError, parse_auth_status, process_environment
from pixelswitch.logfile import Log
from pixelswitch.signin import Launch, Purpose, SignInSession

from . import stub_claude
from .lb_support import read_file, tmpdir, wait_for

MANUAL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code"
          "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile"
          "+user%3Ainference&code_challenge=TESTCHALLENGE123&code_challenge_method=S256&state=TESTSTATE456")
AUTOMATIC = MANUAL.replace("https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback", "http%3A%2F%2Flocalhost%3A54545%2Fcallback")
OBSERVED = f"Opening browser to sign in…\nIf the browser didn't open, visit: {MANUAL}\nPaste code here if prompted > "


class ParserTests(unittest.TestCase):
    def test_the_exact_output_of_claude_code(self):
        self.assertEqual(parse.manual_link(OBSERVED), MANUAL)

    def test_osc8_colour_labels_and_rewording_are_tolerated(self):
        osc8 = f"Opening browser\nIf the browser didn't open, visit: \x1b]8;;{MANUAL}\x07{MANUAL}\x1b]8;;\x07\nPaste code > "
        self.assertEqual(parse.manual_link(osc8), MANUAL)
        labelled = f"If the browser didn't open, visit: \x1b]8;;{MANUAL}\x1b\\this link\x1b]8;;\x1b\\\nPaste code > "
        self.assertEqual(parse.manual_link(labelled), MANUAL)
        coloured = f"\x1b[1mOpening\x1b[0m\nIf the browser didn't open, visit: \x1b[4;36m{MANUAL}\x1b[0m\n\x1b[2mPaste\x1b[0m"
        self.assertEqual(parse.manual_link(coloured), MANUAL)
        self.assertEqual(parse.manual_link(f"Sign in with this address: {MANUAL}\nThen paste the code > "), MANUAL)
        noisy = f"Checking https://docs.claude.com/en/docs/claude-code for news\nWarning\nIf the browser didn't open, visit: {MANUAL}\n"
        self.assertEqual(parse.manual_link(noisy), MANUAL)

    def test_no_link_the_wrong_link_and_the_automatic_link(self):
        self.assertIsNone(parse.manual_link("Opening browser to sign in…\nPaste code here if prompted > "))
        self.assertIsNone(parse.manual_link("If the browser didn't open, visit: https://docs.claude.com/en/sign-in\n"))
        self.assertIsNone(parse.manual_link(f"If the browser didn't open, visit: {AUTOMATIC}\n"))
        redrawn = f"⠋ Opening\r⠙ Opening\rIf the browser didn't open, visit: {MANUAL}\r\n"
        self.assertEqual(parse.manual_link(redrawn), MANUAL)

    def test_the_automatic_link_comes_from_the_capture_file(self):
        self.assertEqual(parse.automatic_link(AUTOMATIC + "\n"), AUTOMATIC)
        several = f"https://claude.com/help\n{MANUAL}\n{AUTOMATIC}\n{AUTOMATIC.replace('54545', '60000')}\n"
        self.assertEqual(parse.automatic_link(several), AUTOMATIC)
        self.assertIsNone(parse.automatic_link(""))
        self.assertIsNone(parse.automatic_link(MANUAL + "\n"))
        self.assertEqual(parse.automatic_link(AUTOMATIC), AUTOMATIC)
        v4 = AUTOMATIC.replace("localhost", "127.0.0.1")
        self.assertEqual(parse.automatic_link(v4), v4)

    def test_escapes_are_stripped(self):
        self.assertEqual(parse.strip_terminal_escapes("\x1b[31mred\x1b[0m \x1b]0;title\x07text\x07"), "red text")

    def test_the_loggable_form_carries_no_query_value(self):
        manual = parse.redacted(MANUAL)
        self.assertNotIn("TESTSTATE456", manual)
        self.assertNotIn("TESTCHALLENGE123", manual)
        self.assertNotIn("=", manual)
        self.assertEqual(manual, "https://claude.com/cai/oauth/authorize (returns to platform.claude.com/oauth/code/callback)")
        self.assertEqual(parse.redacted(AUTOMATIC),
                         "https://claude.com/cai/oauth/authorize (returns to localhost:54545/callback)")


class RulesTests(unittest.TestCase):
    def test_code_shape(self):
        self.assertEqual(parse.normalized_code("abc123#state456"), "abc123#state456")
        self.assertEqual(parse.normalized_code("  abc123#state456 \n"), "abc123#state456")
        for bad in ("", "   ", "abc123", "abc123#", "#state456", "a#b#c", "abc 123#state", "abc123#sta\nte", "abc\x001#x"):
            self.assertIsNone(parse.normalized_code(bad), repr(bad))

    def test_gate(self):
        d = parse.gate_decide
        self.assertEqual(d(False, True, True, False), "claudeUnavailable")
        self.assertEqual(d(True, False, False, None), "allowed")
        self.assertEqual(d(True, True, False, None), "busy")
        self.assertEqual(d(True, False, True, None), "busy")
        self.assertEqual(d(True, False, False, False), "busy")
        self.assertEqual(d(True, False, False, True), "allowed")

    def test_results(self):
        @dataclass
        class A:
            id: str
            email: str

        def status(logged_in, email, method="claude.ai"):
            return AuthStatus(logged_in, method, None, email)

        a, b = A("1", "a@x.com"), A("2", "b@x.com")
        self.assertEqual(parse.classify_new_account(status(False, None), [a]), ("notLoggedIn", None))
        self.assertEqual(parse.classify_new_account(status(True, None, "apiKeyHelper"), [a]), ("noIdentity", None))
        self.assertEqual(parse.classify_new_account(status(True, "b@x.com"), [a, b]), ("existing", "2"))
        self.assertEqual(parse.classify_new_account(status(True, "c@x.com"), [a, b]), ("new", "c@x.com"))
        self.assertEqual(parse.classify_new_account(status(True, "B@x.com"), [a, b]), ("new", "B@x.com"), "emails compare exactly")
        self.assertEqual(parse.classify_reauthentication(status(True, "a@x.com"), "a@x.com"), ("matches", None))
        self.assertEqual(parse.classify_reauthentication(status(True, "b@x.com"), "a@x.com"), ("wrongAccount", "b@x.com"))
        self.assertEqual(parse.classify_reauthentication(status(False, None), "a@x.com"), ("notLoggedIn", None))
        self.assertEqual(parse.classify_reauthentication(status(True, None, "oauthToken"), "a@x.com"), ("noIdentity", None))


class CaptureFolderTests(unittest.TestCase):
    def test_modes_and_the_helper_runs_as_claude_runs_it(self):
        parent = tmpdir(self)
        capture = parse.LinkCapture.create(parent, "SESSION-1")
        mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
        self.assertEqual(mode(capture.directory), 0o700)
        self.assertEqual(mode(capture.helper), 0o700)
        self.assertEqual(mode(capture.capture_file), 0o600)
        env = dict(os.environ)
        env.update(capture.environment_overrides)
        completed = subprocess.run([capture.environment_overrides["BROWSER"], AUTOMATIC], env=env)
        self.assertEqual(completed.returncode, 0)
        subprocess.run([capture.helper, "https://second.example/x"], env=env)
        self.assertEqual(capture.read().splitlines(), [AUTOMATIC, "https://second.example/x"])
        self.assertEqual(parse.automatic_link(capture.read()), AUTOMATIC)
        capture.remove()
        self.assertFalse(os.path.exists(capture.directory))

    def test_the_helper_falls_back_to_its_own_folder_without_the_variable(self):
        parent = tmpdir(self)
        capture = parse.LinkCapture.create(parent, "S2")
        env = {k: v for k, v in os.environ.items() if k != "PIXELSWITCH_SIGNIN_CAPTURE_FILE"}
        subprocess.run([capture.helper, "https://x.example/"], env=env, check=True)
        self.assertIn("https://x.example/", capture.read())

    def test_stale_folders_are_removed_and_nothing_else(self):
        parent = tmpdir(self)
        old = os.path.join(parent, parse.FOLDER_PREFIX + "OLD")
        fresh = os.path.join(parent, parse.FOLDER_PREFIX + "FRESH")
        other = os.path.join(parent, "unrelated")
        for path in (old, fresh, other):
            os.mkdir(path)
        os.utime(old, (1.0, 1.0))
        os.utime(other, (1.0, 1.0))
        parse.remove_stale(parent)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(other))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir(self)
        self.stub = stub_claude.install(os.path.join(self.home, "bin"))
        self.setting = [""]
        self.log = Log(None).category("t")

    def cli(self, **kw):
        return ClaudeCli(home=self.home, base_env=kw.pop("env", {"PATH": "/usr/bin:/bin"}), log=self.log,
                         binary_path_setting=lambda: self.setting[0], **kw)

    def test_the_setting_wins_if_it_names_an_executable_file(self):
        self.setting[0] = self.stub
        self.assertEqual(self.cli().path(), self.stub)
        self.setting[0] = os.path.join(self.home, "missing")
        self.assertIsNone(self.cli(env={"PATH": "/nonexistent"}).path())

    def test_then_local_bin_then_path(self):
        local = stub_claude.install(os.path.join(self.home, ".local", "bin"))
        self.assertEqual(self.cli().path(), local)
        os.unlink(local)
        self.assertEqual(self.cli(env={"PATH": os.path.dirname(self.stub)}).path(), self.stub)

    def test_a_native_update_replacing_the_binary_is_seen_at_once(self):
        local = stub_claude.install(os.path.join(self.home, ".local", "bin"))
        cli = self.cli()
        self.assertEqual(cli.path(), local)
        os.unlink(local)
        self.assertIsNone(self.cli(env={"PATH": "/nonexistent"}).path())

    def test_test_mode_never_searches_path_and_needs_the_stub_under_the_scratch_root(self):
        local = stub_claude.install(os.path.join(self.home, ".local", "bin"))
        self.assertEqual(local, self.cli(test_mode=False).path())
        self.assertIsNone(self.cli(test_mode=True).path(), "no setting: the real claude on PATH is never found")
        self.setting[0] = self.stub  # a stub outside ~/.cache/pixelswitch-dev/
        self.assertIsNone(self.cli(test_mode=True).path())

    def test_the_environment_drops_the_two_config_dir_variables(self):
        env = process_environment({"PATH": "/usr/bin", "HOME": "/wrong", "CLAUDE_CONFIG_DIR": "/x",
                                   "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/y", "KEEP": "1"}, self.stub, "/home/u")
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("CLAUDE_SECURESTORAGE_CONFIG_DIR", env)
        self.assertEqual(env["KEEP"], "1")
        self.assertEqual(env["HOME"], "/home/u")
        self.assertTrue(env["PATH"].startswith(os.path.dirname(os.path.realpath(self.stub)) + ":"))
        self.assertIn("/home/u/.local/bin", env["PATH"].split(":"))
        self.assertTrue(env["PATH"].endswith(":/usr/bin"))

    def test_a_warning_when_its_own_environment_sets_either(self):
        self.assertIsNone(self.cli().env_warning())
        warning = self.cli(env={"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/x"}).env_warning()
        self.assertIn("CLAUDE_CONFIG_DIR", warning)
        both = self.cli(env={"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/x", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/y"}).env_warning()
        self.assertIn("CLAUDE_SECURESTORAGE_CONFIG_DIR", both)

    def test_the_stub_never_sees_the_variables(self):
        self.setting[0] = self.stub
        os.makedirs(os.path.join(self.home, ".claude"))
        cli = self.cli(env={"PATH": "/usr/bin:/bin", "CLAUDE_CONFIG_DIR": "/x", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/y"})
        cli.auth_status()
        call = json.loads(read_file(os.path.join(self.home, "stub-calls.log")).splitlines()[-1])
        self.assertFalse(call["CLAUDE_CONFIG_DIR"])
        self.assertFalse(call["CLAUDE_SECURESTORAGE_CONFIG_DIR"])

    def test_auth_status_reads_what_the_files_say(self):
        self.setting[0] = self.stub
        cli = self.cli()
        status = cli.auth_status()
        self.assertFalse(status.logged_in)
        os.makedirs(os.path.join(self.home, ".claude"))
        with open(os.path.join(self.home, ".claude", ".credentials.json"), "w") as handle:
            json.dump({"claudeAiOauth": {"accessToken": "a", "subscriptionType": "max"}}, handle)
        with open(os.path.join(self.home, ".claude.json"), "w") as handle:
            json.dump({"oauthAccount": {"emailAddress": "a@x.test", "organizationName": "Org"}}, handle)
        status = cli.auth_status()
        self.assertEqual((status.logged_in, status.email, status.org_name, status.subscription_type),
                         (True, "a@x.test", "Org", "max"))
        self.assertIsNone(status.shadowing_auth_method)

    def test_a_cli_that_exits_1_when_logged_out_still_answers_with_its_status(self):
        self.setting[0] = self.stub
        os.environ["STUB_STATUS_EXIT1"] = "1"
        self.addCleanup(os.environ.pop, "STUB_STATUS_EXIT1", None)
        cli = self.cli(env={**os.environ, "PATH": "/usr/bin:/bin"})
        self.assertFalse(cli.auth_status().logged_in)

    def test_a_shadowing_source_has_no_email(self):
        self.setting[0] = self.stub
        cli = self.cli(env={"PATH": "/usr/bin:/bin", "STUB_STATUS_SHADOW": "1"})
        status = cli.auth_status()
        self.assertTrue(status.logged_in)
        self.assertIsNone(status.email)
        self.assertEqual(status.shadowing_auth_method, "apiKey")

    def test_a_cli_that_never_answers_times_out_and_is_stopped(self):
        self.setting[0] = self.stub
        cli = self.cli(env={"PATH": "/usr/bin:/bin", "STUB_STATUS_HANG": "1"})
        started = time.monotonic()
        with self.assertRaises(ClaudeError) as raised:
            cli.run(["auth", "status"], timeout=0.5)
        self.assertEqual(raised.exception.kind, "timedOut")
        self.assertLess(time.monotonic() - started, 8)

    def test_unavailable_launch_failure_and_bad_output(self):
        with self.assertRaises(ClaudeError) as raised:
            self.cli(env={"PATH": "/nonexistent"}).auth_status()
        self.assertEqual(raised.exception.kind, "unavailable")
        broken = os.path.join(self.home, "bin", "broken")
        with open(broken, "w") as handle:
            handle.write("#!/bin/sh\necho 'not json'\n")
        os.chmod(broken, 0o755)
        self.setting[0] = broken
        with self.assertRaises(ClaudeError) as raised:
            self.cli().auth_status()
        self.assertEqual(raised.exception.kind, "invalidOutput")
        failing = os.path.join(self.home, "bin", "failing")
        with open(failing, "w") as handle:
            handle.write("#!/bin/sh\nexit 3\n")
        os.chmod(failing, 0o755)
        self.setting[0] = failing
        with self.assertRaises(ClaudeError) as raised:
            self.cli().auth_status()
        self.assertEqual((raised.exception.kind, raised.exception.message), ("cliError", "Claude CLI error: exit 3"))

    def test_status_parsing_tolerates_noise_around_the_json(self):
        self.assertTrue(parse_auth_status('warning: x\n{"loggedIn": true, "email": "a@x.test"}\n').logged_in)
        self.assertIsNone(parse_auth_status("nothing"))
        self.assertIsNone(parse_auth_status('{"loggedIn": "yes"}'))


class SessionHarness(unittest.TestCase):
    """A session against the stub claude in a scratch HOME."""

    def setUp(self):
        self.home = tmpdir(self)
        self.stub = stub_claude.install(os.path.join(self.home, "bin"))
        self.parent = os.path.join(self.home, "run")
        self.states: list[str] = []
        self.finished = threading.Event()
        self.completed = []
        self.logs: list[str] = []
        self.env = {"PATH": "/usr/bin:/bin", "HOME": self.home}

    def session(self, purpose=None, complete=None, env=None) -> SignInSession:
        def done(s):
            self.completed.append(s.state)
            return ("succeeded", None, "ACCOUNT-1") if complete is None else complete(s)

        environment = dict(self.env)
        environment.update(env or {})
        session = SignInSession(
            purpose or Purpose.new_account(), Launch(self.stub, environment), self.parent,
            log=self.logs.append, complete=done, on_finish=lambda s: self.finished.set(),
            on_change=lambda s: self.states.append(s.state))
        self.addCleanup(self._stop, session)
        return session

    @staticmethod
    def _stop(session):
        session.cancel()
        session.join(10)

    def approve(self):
        with open(os.path.join(self.home, "stub-login-approve"), "w") as handle:
            handle.write("1")


class SessionTests(SessionHarness):
    def test_both_links_are_read_and_nothing_opens_by_itself(self):
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.manual_link and session.automatic_link, 15))
        self.assertEqual(session.manual_link, MANUAL)
        self.assertEqual(session.automatic_link, AUTOMATIC)
        self.assertEqual(session.state, "waitingForUser")
        self.assertIsNone(session.notice)
        call = json.loads(read_file(os.path.join(self.home, "stub-calls.log")).splitlines()[0])
        self.assertEqual(call["args"], ["auth", "login"])
        self.assertTrue(call["BROWSER"].endswith("capture-sign-in-link"), "BROWSER is the private helper")

    def test_the_cli_exiting_zero_runs_the_save_steps_and_succeeds(self):
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        self.approve()
        self.assertTrue(self.finished.wait(20))
        self.assertEqual(session.state, "succeeded")
        self.assertEqual(session.result_account_id, "ACCOUNT-1")
        self.assertEqual(self.completed, ["completing"])
        self.assertEqual([s for i, s in enumerate(self.states) if i == 0 or s != self.states[i - 1]],
                         ["waitingForUser", "completing", "succeeded"])
        self.assertFalse(os.path.exists(os.path.join(self.parent, parse.FOLDER_PREFIX + session.id)), "capture folder removed")

    def test_a_reauthentication_passes_the_email(self):
        session = self.session(Purpose.reauthenticate("ID-1", "a@x.test"))
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        call = json.loads(read_file(os.path.join(self.home, "stub-calls.log")).splitlines()[0])
        self.assertEqual(call["args"], ["auth", "login", "--email", "a@x.test"])

    def test_the_code_box_hands_one_whole_code_to_the_cli(self):
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        self.assertFalse(session.submit_code("only-half"))
        self.assertFalse(session.submit_code("two#parts#too many"))
        self.assertFalse(session.code_submitted)
        self.assertTrue(session.submit_code("CODE#TESTSTATE456"))
        self.assertTrue(session.code_submitted)
        self.assertFalse(session.submit_code("CODE#TESTSTATE456"), "a second code is refused")
        self.assertTrue(self.finished.wait(20))
        self.assertEqual(session.state, "succeeded")

    def test_a_code_is_refused_before_the_session_waits_and_after_it_finishes(self):
        session = self.session()
        self.assertFalse(session.submit_code("CODE#TESTSTATE456"), "not started")
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        session.cancel()
        self.assertTrue(self.finished.wait(20))
        self.assertFalse(session.submit_code("CODE#TESTSTATE456"))

    def test_cancel_stops_the_cli_and_the_session_ends_cancelled(self):
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        session.cancel()
        self.assertTrue(self.finished.wait(20))
        self.assertEqual(session.state, "cancelled")
        self.assertEqual(self.completed, [], "nothing was saved")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", ".credentials.json")))

    def test_cancel_before_the_cli_starts_launches_nothing(self):
        session = self.session()
        session.cancel()
        self.assertEqual(session.state, "cancelled")
        session.start()
        time.sleep(0.2)
        self.assertFalse(os.path.exists(os.path.join(self.home, "stub-calls.log")))

    def test_cancel_is_ignored_while_the_account_is_being_saved(self):
        release = threading.Event()
        entered = threading.Event()

        def complete(s):
            entered.set()
            release.wait(10)
            return ("succeeded", None, "ID")

        session = self.session(complete=complete)
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        self.approve()
        self.assertTrue(entered.wait(20))
        self.assertEqual(session.state, "completing")
        session.cancel()
        self.assertEqual(session.state, "completing")
        release.set()
        self.assertTrue(self.finished.wait(10))
        self.assertEqual(session.state, "succeeded")

    def test_a_cli_that_exits_non_zero_fails_with_its_status(self):
        session = self.session(env={"STUB_LOGIN_EXIT": "7"})
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        self.approve()
        self.assertTrue(self.finished.wait(20))
        # exit 7 after approval but the stub exits before writing; the state is a failure
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.message, "Claude Code's sign-in stopped (exit status 7).")

    def test_a_missing_binary_fails_cleanly(self):
        session = SignInSession(Purpose.new_account(), Launch(os.path.join(self.home, "nope"), self.env), self.parent,
                                complete=lambda s: ("succeeded", None, None), on_finish=lambda s: self.finished.set())
        session.start()
        self.assertTrue(self.finished.wait(10))
        self.assertEqual(session.state, "failed")
        self.assertTrue(session.message.startswith("Couldn't start Claude Code:"))

    def test_the_overall_limit_ends_a_sign_in_nobody_finishes(self):
        original = signin.OVERALL_LIMIT
        signin.OVERALL_LIMIT = 0.8
        self.addCleanup(setattr, signin, "OVERALL_LIMIT", original)
        session = self.session()
        session.start()
        self.assertTrue(self.finished.wait(20))
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.message, "Sign-in timed out after 15 minutes. Start it again when you're ready.")

    def test_the_cli_is_in_its_own_process_group_and_cancel_signals_the_group(self):
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        pid = session._process.pid
        self.assertEqual(os.getpgid(pid), pid)
        self.assertNotEqual(os.getpgid(pid), os.getpgrp())

    def test_no_link_means_a_notice_and_a_wait_not_a_fallback(self):
        original = signin.LINK_WAIT
        signin.LINK_WAIT = 0.6
        self.addCleanup(setattr, signin, "LINK_WAIT", original)
        session = self.session(env={"STUB_LOGIN_NO_LINKS": "1"})
        session.start()
        self.assertTrue(wait_for(lambda: session.notice is not None, 15))
        self.assertEqual(session.notice, signin.NO_LINK_NOTICE)
        self.assertEqual(session.state, "starting")
        self.assertIsNone(session.manual_link)
        time.sleep(0.5)
        calls = read_file(os.path.join(self.home, "stub-calls.log")).splitlines()
        self.assertEqual(len(calls), 1, "the CLI was NOT started a second time without BROWSER (no fallback)")
        self.assertTrue(json.loads(calls[0])["BROWSER"])

    def test_only_the_manual_link_still_shows_the_code_box(self):
        script = os.path.join(self.home, "bin", "manual_only")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\nprintf 'visit: %s\\n> ' '%s'\nsleep 30\n" % (MANUAL, MANUAL))
        os.chmod(script, 0o755)
        self.stub = script
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.manual_link is not None, 15))
        self.assertEqual(session.state, "waitingForUser")
        self.assertIsNone(session.automatic_link)

    def test_the_log_never_carries_a_query_value(self):
        session = self.session()
        session.start()
        self.assertTrue(wait_for(lambda: session.automatic_link and session.manual_link, 15))
        session.cancel()
        self.assertTrue(self.finished.wait(20))
        text = "\n".join(self.logs)
        self.assertNotIn("TESTSTATE456", text)
        self.assertNotIn("TESTCHALLENGE123", text)
        self.assertIn("Manual link read from the output", text)

    def test_a_crash_in_the_save_steps_does_not_leave_the_sign_in_busy(self):
        def explode(s):
            raise RuntimeError("bug")

        session = self.session(complete=explode)
        session.start()
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 15))
        self.approve()
        self.assertTrue(self.finished.wait(20))
        self.assertEqual(session.state, "failed")


if __name__ == "__main__":
    unittest.main()
