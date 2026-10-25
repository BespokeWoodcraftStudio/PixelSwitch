"""Lane FL4: the findings of the re-review of FL1 and FL2 (rereview-fl1-fl2-2026-10-24.json), each with a
test that fails without its fix. The dirlock ownership tests (O1 and the whole-second filesystem P2) are in
test_fl1_locks_server.py beside the FL1 ownership tests, and the network guard's in test_network_guard.py."""
from __future__ import annotations

import io
import json
import os
import signal
import threading
import time
import unittest
import urllib.request
from unittest import mock

from pixelswitch import api, daemon as daemon_module, fsio, removal, updater as updater_module
from pixelswitch.coordinator import ActionError, LIVE_CHANGED_MESSAGE
from pixelswitch.paths import Paths
from pixelswitch.signin import Purpose
from pixelswitch.updater import read_bad_builds
from pixelswitch.version import VersionInfo

from .lb_support import Rig, credential_text, read_file, read_json, wait_for
from .mock_api import R, fabricated
from .test_fl1_switch import base_config, files, unowned
from .test_signin_flow import approve, configure_login, run_to_end
from .test_updater import SELF_CHECK_OK, UpdaterCase, UpdaterLogCase, good_files, tarball


class CredentialWrittenThenHoldRanOutTests(unittest.TestCase):
    """P1 coordinator.py:1610. Once `.credentials.json` is written the switch finishes or rolls back, whatever
    the refresh lock's 10 s hold limit says (the Mac never leaves the token switched without trying the
    identity write)."""

    def rig(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        real_write = app.login.write_credential

        def slow_write(text, held=None):
            real_write(text, held)
            rig.clock.advance(11)                             # the credential write used the whole 10 s and more

        app.login.write_credential = slow_write
        return rig, app, a, b

    def test_the_identity_is_still_written_and_the_switch_succeeds(self):
        rig, app, a, b = self.rig()
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(rig.live_config()["oauthAccount"], b.oauth_account, "never B's login under A's identity")
        self.assertEqual(app.active_account().id, b.id)

    def test_when_the_identity_cannot_be_written_the_login_goes_back_to_the_previous_account(self):
        rig, app, a, b = self.rig()
        os.mkdir(rig.paths.claude_json + ".lock")             # Claude Code holds the config lock
        before = rig.live_credential()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "Failed to write oauthAccount to ~/.claude.json")
        self.assertEqual(rig.live_credential(), before, "rolled back even though the hold limit had passed")
        self.assertEqual(rig.live_config()["oauthAccount"], a.oauth_account)
        self.assertEqual(app.active_account().id, a.id)

    def test_a_hold_that_ran_out_before_the_write_still_writes_nothing(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        real_save = app.backups.save

        def slow_save(*args, **kw):
            rig.clock.advance(11)                             # step 1 used the whole hold
            return real_save(*args, **kw)

        app.backups.save = slow_save
        before = rig.live_credential()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("could not finish within the time it may hold", raised.exception.message)
        self.assertEqual(rig.live_credential(), before)


class TreatAsActiveJudgedLoginTests(unittest.TestCase):
    """P2 coordinator.py:1268 (Mac lane FE, AppState.performSwitch): the treatAsActive branch binds the switch
    to the login it judged, like the proceed branch."""

    def test_a_renewal_after_the_judgment_writes_nothing(self):
        rig, app, a, b = unowned(self)
        rig.write_live(a)
        rig.api.script("profile", a.access, R(500, "down"))   # the API cannot say: the same grant as A's backup decides
        real_read = app.login.read_credential
        calls = []
        renewed = credential_text(fabricated("oat01"), fabricated("ort01"))

        def read():
            calls.append(1)
            if len(calls) == 2:                                # step 1: Claude Code renewed A's login since the judgment
                fsio.atomic_write(rig.paths.credentials, renewed)
            return real_read()

        app.login.read_credential = read
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_CHANGED_MESSAGE)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["refreshToken"],
                         json.loads(renewed)["claudeAiOauth"]["refreshToken"], "the renewed login is still live")
        self.assertEqual(rig.backup_of(a).token, a.credential, "and A's saved login was not touched")

    def test_an_unchanged_login_still_switches(self):
        rig, app, a, b = unowned(self)
        rig.write_live(a)
        rig.api.script("profile", a.access, R(500, "down"))
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(app.active_account().id, b.id)


class ReauthenticationForgetsRemovedLoginsTests(unittest.TestCase):
    """P2 coordinator.py:939 (Mac lane FE, AppState.finishReauthentication)."""

    def test_the_record_of_removed_logins_is_cleared(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        app.removed_logins.save(["f" * 16, "e" * 16])
        configure_login(rig, "b@example.invalid", b.uuid)
        rig.api.script("profile", None, R(200, {"account": {"uuid": b.uuid, "email": b.email}}))
        session = app.start_sign_in(Purpose.reauthenticate(b.id, b.email))
        run_to_end(rig, app, session)
        self.assertEqual((session.state, session.result_account_id), ("succeeded", b.id))
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(app.removed_logins.load(), [])

    def test_a_wrong_account_leaves_the_record_alone(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        app.removed_logins.save(["f" * 16])
        configure_login(rig, "c@example.invalid", "uuid-c")
        session = app.start_sign_in(Purpose.reauthenticate(b.id, b.email))
        run_to_end(rig, app, session)
        self.assertEqual(session.state, "failed")
        self.assertEqual(app.removed_logins.load(), ["f" * 16])


class RemoveAccountStateTests(unittest.TestCase):
    """P2 coordinator.py:1009 and :993."""

    def rig(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        c = rig.seed_account("c@example.invalid")
        app = rig.build()
        spawned = []
        app._spawn = lambda *args, **kw: spawned.append(args)
        return rig, app, a, b, c, spawned

    def test_a_change_made_while_the_removal_runs_is_kept(self):
        rig, app, a, b, c, spawned = self.rig()
        real = app.persist_needs_reauth
        done = []

        def persist():
            if not done:
                done.append(app.set_label("Work", b.id))      # another connection, after the removal's snapshot
            real()

        app.persist_needs_reauth = persist
        app.remove_account(a.id)
        self.assertEqual(done, [True])
        self.assertEqual(app.account_by_id(b.id).custom_label, "Work")
        self.assertIn('"Work"', read_file(rig.paths.accounts), "and it is in accounts.json")
        self.assertIsNone(app.account_by_id(a.id))

    def test_a_removal_is_judged_from_the_state_after_the_gate_wait(self):
        rig, app, a, b, c, spawned = self.rig()
        real_begin = app.gate.begin
        adopted = []

        def begin(*args, **kw):
            took = real_begin(*args, **kw)
            if took and not adopted:
                adopted.append(1)                              # the reconcile that held the gate adopted B meanwhile
                app._mark_active(b.id)
            return took

        app.gate.begin = begin
        app.remove_account(a.id)
        self.assertEqual(adopted, [1])
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual([x.id for x in app.accounts if x.is_active], [b.id], "B is still marked active in the list")
        self.assertEqual(spawned, [], "A was not the active account any more: no fallback switch")
        saved = [x for x in read_json(rig.paths.accounts) if x.get("isActive")] \
            if isinstance(read_json(rig.paths.accounts), list) else None
        if saved is not None:
            self.assertEqual(len(saved), 1)

    def test_an_account_removed_while_waiting_is_not_found(self):
        rig, app, a, b, c, spawned = self.rig()
        real_begin = app.gate.begin

        def begin(*args, **kw):
            took = real_begin(*args, **kw)
            with app._state_lock:
                app._accounts = [x for x in app._accounts if x.id != a.id]
            return took

        app.gate.begin = begin
        from pixelswitch.protocol import ControlError
        with self.assertRaises(ControlError) as raised:
            app.remove_account(a.id)
        self.assertEqual(raised.exception.kind, "notFound")
        self.assertFalse(app.gate.busy, "the gate was let go")

    def test_removing_the_active_account_still_hands_over_to_a_fallback(self):
        rig, app, a, b, c, spawned = self.rig()
        app.remove_account(a.id)
        self.assertEqual(len(spawned), 1)
        self.assertEqual([x.id for x in app.accounts if x.is_active], [], "none marked active until the switch succeeds")
        self.assertIsNone(app.account_by_id(a.id))


class SelfCheckVerdictTests(UpdaterLogCase):
    """P2 updater.py:468: `bad` is permanent, so only the build's own faults earn it."""

    def publish_script(self, script):
        self.server.publish(tarball(good_files(self_check=script)))

    def test_a_self_check_killed_by_a_signal_is_not_held_against_the_build(self):
        self.publish_script("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("failed", "self-check"))
        self.assertEqual(read_bad_builds(self.paths), set())
        self.assertFalse(os.path.exists(updater_module.bad_builds_path(self.paths)))
        self.assert_untouched()
        self.publish_script(SELF_CHECK_OK)                     # the same build, a quieter machine
        self.assertEqual(self.updater().check_and_install().kind, "installed")

    def test_a_self_check_that_could_not_read_the_users_files_is_not_held_against_the_build(self):
        for error in ("JSONDecodeError", "ValueError", "OSError", "PermissionError", "AccountsFormatError"):
            with self.subTest(error=error):
                self.publish_script("import sys\nif '--self-check' in sys.argv:\n"
                                    f"    print('pixelswitchd self-check failed: {error}: settings.json is not valid', file=sys.stderr)\n"
                                    "    sys.exit(1)\n")
                outcome = self.updater().check_and_install()
                self.assertEqual((outcome.kind, outcome.reason), ("failed", "self-check"))
                self.assertEqual(read_bad_builds(self.paths), set())

    def test_the_builds_own_faults_are_remembered(self):
        for failure in ("ImportError: No module named 'pixelswitch.x'", "VersionUnavailable: no VERSION", "SyntaxError: bad",
                        "something nobody expected"):
            with self.subTest(failure=failure):
                self.publish_script("import sys\nif '--self-check' in sys.argv:\n"
                                    f"    print('pixelswitchd self-check failed: {failure}', file=sys.stderr)\n"
                                    "    sys.exit(1)\n")
                self.assertEqual(self.updater().check_and_install().reason, "self-check")
                self.assertEqual(read_bad_builds(self.paths), {32}, failure)
                os.unlink(updater_module.bad_builds_path(self.paths))

    def test_what_the_real_self_check_prints_for_a_damaged_settings_file_is_classified_as_the_users(self):
        home = os.path.join(self.home, "scratch-home")
        paths = Paths(home, None)
        os.makedirs(os.path.dirname(paths.settings), exist_ok=True)
        with open(paths.settings, "w") as handle:
            handle.write("{ this is not json")
        out, err = io.StringIO(), io.StringIO()
        # (A scratch copy of linux/ alone has no VERSION file or project.yml; the version is not what is tested.)
        with mock.patch("pixelswitch.version.read_version", lambda *a, **k: VersionInfo("2.1", 32)):
            code = daemon_module.self_check({"HOME": home, "PATH": "/usr/bin:/bin"}, out, err)
        self.assertEqual(code, daemon_module.EXIT_FAILED)
        found = updater_module.SELF_CHECK_FAILURE.search(err.getvalue())
        self.assertIsNotNone(found, err.getvalue())
        self.assertIn(found.group(1).rsplit(".", 1)[-1], updater_module.SELF_CHECK_ENVIRONMENT_ERRORS)

    def test_a_clean_run_that_prints_the_wrong_version_is_still_bad(self):
        self.publish_script("print('pixelswitchd 9.9 (build 99)')\n")
        self.assertEqual(self.updater().check_and_install().reason, "self-check")
        self.assertEqual(read_bad_builds(self.paths), {32})


class StopRestartGapTests(UpdaterLogCase):
    """P2 updater.py:422/434: a stop that lands between the last stop check and the restart request must not
    become a restart."""

    def test_a_stop_during_the_flip_asks_for_no_restart(self):
        self.server.publish(tarball(good_files()))
        updater = self.updater()
        real_flip = updater_module.flip_symlink
        flips = []

        def flip(link, target):
            real_flip(link, target)
            flips.append(os.path.basename(link))
            if os.path.basename(link) == "current":
                updater._stop.set()                           # SIGTERM lands right after `current` moved

        with mock.patch.object(updater_module, "flip_symlink", flip):
            outcome = updater.check_and_install()
        self.assertEqual(flips, ["previous", "current"])
        self.assertEqual((outcome.kind, outcome.reason), ("waiting", "stopping"))
        self.assertEqual(self.restarts, [], "the user's stop is not turned into a restart")
        self.assertEqual(self.current(), "2.1-32", "the new version runs at the next start")

    def test_stop_waits_for_a_restart_that_is_already_under_way(self):
        self.server.publish(tarball(good_files()))
        entered, release = threading.Event(), threading.Event()

        def slow_restart():
            self.restarts.append(("asked",))
            entered.set()
            release.wait(10)

        updater = self.updater(restart=slow_restart)
        worker = threading.Thread(target=updater.check_and_install, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(15))
        stopped = []
        stopper = threading.Thread(target=lambda: (updater.stop(5.0), stopped.append(time.monotonic())), daemon=True)
        began = time.monotonic()
        stopper.start()
        time.sleep(0.4)
        self.assertEqual(stopped, [], "stop() does not return while the restart is being asked for")
        release.set()
        stopper.join(10)
        worker.join(10)
        self.assertEqual(len(stopped), 1)
        self.assertGreaterEqual(stopped[0] - began, 0.3)
        self.assertEqual(self.restarts, [("asked",)])

    def test_stop_before_the_flip_leaves_current_alone(self):
        self.server.publish(tarball(good_files()))
        updater = self.updater()
        updater._idle = lambda: (updater.stop(0.0), True)[1]
        outcome = updater.check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("waiting", "stopping"))
        self.assertEqual(self.current(), "2.0-31")
        self.assertEqual(self.restarts, [])

    def test_stop_returns_at_once_when_nothing_is_flipping(self):
        updater = self.updater()
        began = time.monotonic()
        updater.stop(5.0)
        self.assertLess(time.monotonic() - began, 1.0)

    def test_the_restart_request_itself_may_call_stop_without_waiting_on_itself(self):
        self.server.publish(tarball(good_files()))
        holder = []
        updater = self.updater(restart=lambda: (holder[0].stop(2.0), self.restarts.append(("ok",))))
        holder.append(updater)
        began = time.monotonic()
        updater.check_and_install()
        self.assertLess(time.monotonic() - began, 5.0)
        self.assertEqual(self.restarts, [("ok",)])


class SpyBody:
    def __init__(self, size=10_000_000):
        self.asked = []
        self.closed = False
        self.size = size

    def read(self, amt=None):
        self.asked.append(amt)
        return b"x" * (self.size if amt is None else min(amt, self.size))

    def close(self):
        self.closed = True


class RedirectBodyLimitTests(unittest.TestCase):
    """P2 api.py:150: urllib read a 3xx body with no limit before following the redirect."""

    def handler(self):
        handler = api._FollowReleaseRedirects(False)
        handler.parent = mock.Mock()
        handler.parent.open.return_value = "followed"
        return handler

    def request(self):
        request = urllib.request.Request("https://github.com/a")
        request.timeout = 30.0                                 # the opener sets it on every request it sends
        return request

    def test_the_body_of_a_redirect_is_not_read(self):
        handler, body = self.handler(), SpyBody()
        result = handler.http_error_302(self.request(), body, 302, "Found", {"location": "https://release-assets.githubusercontent.com/x"})
        self.assertEqual(result, "followed")
        self.assertEqual(sum(a for a in body.asked if a), 0, "no byte of it was read")
        self.assertNotIn(None, body.asked, "and never an unbounded read")
        self.assertTrue(body.closed)

    def test_every_redirect_status_is_covered(self):
        for code in (301, 302, 303, 307, 308):
            handler, body = self.handler(), SpyBody()
            getattr(handler, f"http_error_{code}")(self.request(), body, code, "x", {"location": "https://github.com/b"})
            self.assertNotIn(None, body.asked, code)
            self.assertTrue(body.closed, code)

    def test_a_refused_hop_closes_the_response(self):
        handler, body = self.handler(), SpyBody()
        with self.assertRaises(api.RedirectRefused):
            handler.http_error_302(self.request(), body, 302, "Found", {"location": "https://evil.example/x"})
        self.assertTrue(body.closed)

    def test_the_cap_applies_to_whatever_reads_the_body(self):
        capped = api._CappedBody(SpyBody(), 5)
        self.assertEqual(len(capped.read()), 5)
        self.assertEqual(capped.read(), b"")
        self.assertEqual(capped.read(100), b"")
        self.assertEqual(api._CappedBody(SpyBody(), 0).read(), b"")
        self.assertEqual(api.REDIRECT_BODY_LIMIT, 0)


if __name__ == "__main__":
    unittest.main()
