"""The daemon process: --self-check (read-only, beside a live daemon), the daemon.lock flock, test-mode
refusals, the control socket's files and modes, the umask, SIGTERM and SIGINT (a sign-in cancelled, an
in-flight renewal finished), app.quit and the rollback marker.

These start the real ``pixelswitchd`` as a subprocess in a scratch HOME under ``~/.cache/pixelswitch-dev/``
(spec 5.7), with the mock API and the stub claude, and remove everything they started.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
import uuid

from pixelswitch import daemon as daemon_module, fsio, paths as paths_module, testmode
from pixelswitch.client import ControlClient

from . import network_guard
from .lb_support import Rig, read_file, tmpdir, wait_for
from .mock_api import R, fabricated

LINUX_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = "import sys; sys.path.insert(0, %r); from pixelswitch.daemon import main; sys.exit(main(sys.argv[1:]))" % LINUX_DIR


def scratch_root(case) -> str:
    """A fresh folder under the REAL home's ``~/.cache/pixelswitch-dev/`` (spec 5.7: the test-mode daemon
    refuses any other HOME, and ``testmode.scratch_root()`` reads the password database, so overriding
    $HOME does not move it). The parent is made 0700 when this run creates it."""
    parent = testmode.scratch_root()
    if not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)
        os.chmod(parent, 0o700)
    path = os.path.join(parent, "ut-" + uuid.uuid4().hex[:10])
    os.makedirs(path, mode=0o700)
    case.addCleanup(shutil.rmtree, path, True)
    return path


def guarded_environment(case) -> dict:
    """Environment entries that load the test network guard into a daemon subprocess; the test fails at
    cleanup if the daemon tried to reach anthropic.com, claude.com or github.com."""
    directory = network_guard.make_child_dir()
    case.addCleanup(shutil.rmtree, directory, True)
    entries = network_guard.child_environment(directory)

    def check():
        found = network_guard.read_child_log(entries[network_guard.ENV_LOG])
        if found:
            raise AssertionError(f"the daemon under test tried to reach a production host: {found}")

    case.addCleanup(check)
    return entries


class Proc:
    def __init__(self, case, rig: Rig, extra_env=None, args=()):
        self.case = case
        self.rig = rig
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": rig.home, "XDG_RUNTIME_DIR": os.path.join(rig.tmp, "run"),
            "PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": rig.api.base, "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.env.update(guarded_environment(case))
        self.env.update(extra_env or {})
        os.makedirs(self.env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)
        self.args = list(args)
        self.process: subprocess.Popen | None = None
        self.paths = paths_module.Paths(rig.home, self.env["XDG_RUNTIME_DIR"])
        case.addCleanup(self.kill)

    def start(self, wait=True):
        self.process = subprocess.Popen([sys.executable, "-c", CODE, *self.args], env=self.env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if wait:
            assert wait_for(lambda: self._answers() or self.process.poll() is not None, 30), "the daemon never answered"
            assert self.process.poll() is None, self.process.communicate()
            # settled: the pointer to the socket is written right after the bind, so wait for it too
            assert wait_for(lambda: self._pointer_is_current(), 10), "socket-path was not written"
        return self

    def _pointer_is_current(self) -> bool:
        try:
            with open(self.paths.socket_path_file, encoding="utf-8") as handle:
                return handle.read().strip() == self.paths.socket
        except OSError:
            return False

    def _answers(self) -> bool:
        import socket as socket_module

        sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        try:
            sock.settimeout(1)
            sock.connect(self.paths.socket)
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def client(self) -> ControlClient:
        client = ControlClient(self.paths.socket)
        client.connect(launch=False)
        self.case.addCleanup(client.disconnect)
        return client

    def finish(self, timeout=30):
        out, err = self.process.communicate(timeout=timeout)
        return self.process.returncode, out, err

    def kill(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.communicate()


class SelfCheckTests(unittest.TestCase):
    def snapshot(self, root):
        found = {}
        for directory, _, names in os.walk(root):
            for name in names + [""]:
                path = os.path.join(directory, name)
                try:
                    status = os.lstat(path)
                    found[path] = (status.st_mtime_ns, status.st_size)
                except OSError:
                    pass
        return found

    def run_check(self, home, env_extra=None, run_dir=None):
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-I", "-c", CODE, "--self-check"], env=env, capture_output=True, text=True, timeout=60)

    def test_it_prints_the_version_and_build_and_exits_0(self):
        home = tmpdir(self)
        done = self.run_check(home)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout.strip(), r"^pixelswitchd [0-9]+\.[0-9]+ \(build [0-9]+\)$")

    def test_it_writes_nothing_creates_nothing_takes_no_lock_and_binds_no_socket(self):
        home = tmpdir(self)
        before = self.snapshot(home)
        self.assertEqual(self.run_check(home, {"XDG_RUNTIME_DIR": home}).returncode, 0)
        self.assertEqual(os.listdir(home), [], "not even the state folder was created")
        self.assertEqual(self.snapshot(home), before)

    def test_it_reads_the_stores_it_finds_and_leaves_them_untouched(self):
        rig = Rig(self)
        rig.seed_account("a@example.invalid", active=True)
        rig.build()
        before = self.snapshot(rig.home)
        done = self.run_check(rig.home)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual({k: v for k, v in self.snapshot(rig.home).items() if "stub" not in k}, {k: v for k, v in before.items() if "stub" not in k})

    def test_a_damaged_store_fails_it_without_moving_the_file_aside(self):
        rig = Rig(self)
        rig.build()
        for path, text in ((rig.paths.backups, "garbage"), (rig.paths.accounts, '{"a": 1}'), (rig.paths.settings, "[1]")):
            fsio.atomic_write(path, text)
            done = self.run_check(rig.home)
            self.assertEqual(done.returncode, 1, (path, done.stdout))
            self.assertIn("self-check failed", done.stderr)
            self.assertEqual(read_file(path), text)
            self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if "unreadable" in n], [])
            os.unlink(path)

    def test_it_runs_beside_a_live_daemon_holding_the_lock(self):
        home = tmpdir(self)
        os.makedirs(os.path.join(home, ".local", "state", "pixelswitch"), mode=0o700)
        lock = os.path.join(home, ".local", "state", "pixelswitch", "daemon.lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = self.snapshot(home)
        done = self.run_check(home)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.snapshot(home), before)

    def test_test_mode_rules_apply_to_it_too(self):
        home = tmpdir(self)  # not under ~/.cache/pixelswitch-dev/
        done = self.run_check(home, {"PIXELSWITCH_TEST_MODE": "1"})
        self.assertEqual(done.returncode, 1)
        self.assertIn("HOME under", done.stderr)

    def test_every_module_imports(self):
        from pixelswitch.daemon import import_every_module

        names = import_every_module()
        for expected in ("coordinator", "usagecycle", "server", "updater", "signin", "claude_login", "stores", "rollback"):
            self.assertIn(expected, names)


class StartRulesTests(unittest.TestCase):
    def test_test_mode_with_a_home_outside_the_scratch_root_is_refused_and_touches_nothing(self):
        home = tmpdir(self)
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "PIXELSWITCH_TEST_MODE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run([sys.executable, "-c", CODE], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 78)
        self.assertIn("PIXELSWITCH_TEST_MODE needs HOME under", done.stderr)
        self.assertEqual(os.listdir(home), [])

    def test_test_mode_under_systemd_is_refused(self):
        rig = Rig(self, root=scratch_root(self), keep_log=False)
        env = dict(Proc(self, rig).env, INVOCATION_ID="0123456789abcdef")
        done = subprocess.run([sys.executable, "-c", CODE], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 78)
        self.assertIn("systemd", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(rig.home, ".local", "state")), "nothing of the daemon's was created")

    def test_the_test_api_base_is_ignored_outside_test_mode_in_the_daemon_that_is_built(self):
        """No process, no socket: the daemon is built (not started) outside test mode with the test base in its
        environment, and every URL it would use is the production one."""
        from pixelswitch.api import ApiBases, TOKEN_URL, USAGE_URL, PROFILE_URL
        from pixelswitch.update_check import FEED_URL, RELEASE_BASE

        home = tmpdir(self)
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "XDG_RUNTIME_DIR": os.path.join(home, "run"),
               "PIXELSWITCH_TEST_API_BASE": "http://127.0.0.1:9", "PIXELSWITCH_TEST_RELEASE_BASE": "http://127.0.0.1:9/releases",
               "PIXELSWITCH_TEST_PUBLIC_KEY": "AAAA"}
        paths = paths_module.Paths.from_env(env)
        fd = daemon_module.acquire_daemon_lock(paths.daemon_lock)
        self.addCleanup(os.close, fd)
        daemon = daemon_module.build(env, fd, paths, threading.Event())
        self.addCleanup(daemon.log.close)
        self.assertFalse(daemon.test_mode)
        bases = daemon.coordinator.service._bases
        self.assertEqual((bases.usage, bases.profile, bases.token), (USAGE_URL, PROFILE_URL, TOKEN_URL))
        self.assertEqual(bases, ApiBases())
        self.assertEqual(daemon.updater._feed_url, FEED_URL)
        self.assertEqual(daemon.updater._release_base, RELEASE_BASE)
        self.assertNotEqual(daemon.updater._public_key, "AAAA")
        self.assertTrue(daemon.updater._scheduled)
        self.assertTrue(daemon.updater._transport.follow_redirects)
        self.assertFalse(daemon.coordinator.service._http.follow_redirects, "the token transport never follows a redirect")
        self.assertIsNot(daemon.updater._transport, daemon.coordinator.service._http)

    def test_the_test_api_base_is_ignored_outside_test_mode_in_a_real_daemon_with_nothing_to_send(self):
        """A real daemon process outside test mode. It has no account, no saved login and no Claude Code
        login, so there is nothing it could send anywhere; the network guard is loaded into it and fails this
        test if it opens a socket to a production host."""
        rig = Rig(self)  # no seed_account: nothing to send
        env = {"PATH": "/usr/bin:/bin", "HOME": rig.home, "XDG_RUNTIME_DIR": os.path.join(rig.tmp, "run"),
               "PIXELSWITCH_TEST_API_BASE": rig.api.base, "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(guarded_environment(self))
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        process = subprocess.Popen([sys.executable, "-c", CODE], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        socket_path = os.path.join(env["XDG_RUNTIME_DIR"], "pixelswitch", "control.sock")
        self.assertTrue(wait_for(lambda: os.path.exists(socket_path), 30))
        client = ControlClient(socket_path)
        client.connect(launch=False)
        self.addCleanup(client.disconnect)
        status = client.call("status.get")
        self.assertNotIn("testMode", status)
        self.assertEqual(status["accountCount"], 0)
        time.sleep(0.5)
        self.assertEqual(rig.api.calls, [], "the base was not honoured")
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0)
        self.assertEqual([line for line in read_file(rig.paths.log).splitlines() if "getUsageLimits" in line or "accountIdentity" in line], [])


class RunningDaemonTests(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self, root=scratch_root(self))
        self.a = self.rig.seed_account("a@example.invalid", active=True)
        self.b = self.rig.seed_account("b@example.invalid")
        self.rig.build()
        self.proc = Proc(self, self.rig).start()

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_files_modes_and_the_socket_pointer(self):
        paths = self.proc.paths
        self.assertTrue(wait_for(lambda: os.path.exists(paths.socket_path_file), 10), "socket-path is written right after the bind")
        self.assertEqual(self.mode(paths.socket), 0o600)
        self.assertEqual(self.mode(os.path.dirname(paths.socket)), 0o700)
        self.assertEqual(read_file(paths.socket_path_file).strip(), paths.socket)
        self.assertEqual(self.mode(paths.daemon_lock), 0o600)
        self.assertEqual(self.mode(paths.log), 0o600)
        self.assertEqual(read_file(paths.daemon_lock).strip(), str(self.proc.process.pid))
        for path in (paths.backups, paths.accounts, paths.settings):
            self.assertEqual(self.mode(path), 0o600, path)
        self.assertEqual(self.mode(paths.state_dir), 0o700)

    def test_status_says_test_mode_and_the_protocol(self):
        status = self.proc.client().call("status.get")
        self.assertIs(status["testMode"], True)
        self.assertEqual((status["protocolVersion"], status["protocolRevision"], status["platform"]), (2, 3, "linux"))
        self.assertTrue(status["claudeAvailable"])
        self.assertEqual(status["accountCount"], 2)

    def test_the_first_cycle_runs_by_itself_and_writes_the_rollback_marker(self):
        from pixelswitch.version import read_version

        build = read_version().build
        self.assertTrue(wait_for(lambda: os.path.exists(self.proc.paths.good_marker(build)), 30))
        self.assertTrue(wait_for(lambda: self.rig.api.calls_to("usage"), 10))
        report = self.proc.client().call("usage.get")
        self.assertTrue(any("usage" in a for a in report["accounts"]))

    def test_a_second_daemon_exits_0_with_already_running_and_touches_nothing(self):
        before = {n: os.stat(os.path.join(self.proc.paths.state_dir, n)).st_mtime_ns for n in os.listdir(self.proc.paths.state_dir)
                  if n != "pixelswitch.log"}
        second = Proc(self, self.rig).start(wait=False)
        code, out, err = second.finish()
        self.assertEqual(code, 0)
        self.assertIn("already running", err)
        after = {n: os.stat(os.path.join(self.proc.paths.state_dir, n)).st_mtime_ns for n in os.listdir(self.proc.paths.state_dir)
                 if n != "pixelswitch.log"}
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)
        self.proc.client().call("status.get")  # the first is undisturbed

    def test_self_check_runs_beside_the_live_daemon_without_disturbing_it(self):
        state_dir = self.proc.paths.state_dir
        before = {n: os.stat(os.path.join(state_dir, n)).st_mtime_ns for n in os.listdir(state_dir) if n != "pixelswitch.log"}
        done = subprocess.run([sys.executable, "-I", "-c", CODE, "--self-check"], env=self.proc.env, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout.strip(), r"^pixelswitchd [0-9]+\.[0-9]+ \(build [0-9]+\)$")
        after = {n: os.stat(os.path.join(state_dir, n)).st_mtime_ns for n in os.listdir(state_dir) if n != "pixelswitch.log"}
        self.assertEqual({k: v for k, v in after.items() if k in before}, before, "it touched nothing")
        self.assertEqual(self.proc.client().call("status.get")["accountCount"], 2, "and the daemon still answers")

    def test_sigterm_exits_0_and_cleans_up(self):
        paths = self.proc.paths
        client = self.proc.client()
        client.call("status.get")
        self.proc.process.send_signal(signal.SIGTERM)
        code, out, err = self.proc.finish()
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(paths.socket))
        self.assertFalse(os.path.exists(paths.socket_path_file))
        fd = os.open(paths.daemon_lock, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released
        self.assertIn("Stopped", read_file(paths.log))

    def test_sigint_is_the_same(self):
        self.proc.process.send_signal(signal.SIGINT)
        code, out, err = self.proc.finish()
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self.proc.paths.socket))

    def test_app_quit_exits_0_a_moment_after_the_reply(self):
        reply = self.proc.client().call("app.quit")
        self.assertEqual(reply, {"ok": True})
        code, out, err = self.proc.finish()
        self.assertEqual(code, 0, err)

    def test_a_sigkilled_daemon_leaves_a_stale_socket_the_next_one_replaces(self):
        self.proc.process.kill()
        self.proc.finish()
        self.assertTrue(os.path.exists(self.proc.paths.socket))
        again = Proc(self, self.rig).start()
        self.assertEqual(again.client().call("status.get")["accountCount"], 2)

    def test_a_daemon_that_finds_the_socket_taken_exits_0_and_does_not_remove_it(self):
        # A second daemon (another HOME, so the flock does not stop it) sharing the first one's runtime folder:
        # the control server answers "already running" and the second daemon's stop must leave the socket alone.
        other = Rig(self, root=scratch_root(self))
        other.seed_account("c@example.invalid", active=True)
        other.build()
        second = Proc(self, other, extra_env={"XDG_RUNTIME_DIR": os.path.dirname(os.path.dirname(self.proc.paths.socket))}).start(wait=False)
        code, out, err = second.finish()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(self.proc.paths.socket), "the running daemon's socket is still there")
        self.assertEqual(self.proc.client().call("status.get")["accountCount"], 2, "and it still answers")

    def test_new_files_are_made_with_umask_077(self):
        client = self.proc.client()
        client.call("accounts.setLabel", {"account": "1", "label": "x"})
        for path in (self.proc.paths.accounts, self.proc.paths.log):
            self.assertEqual(self.mode(path) & 0o077, 0, path)
        client.call("settings.set", {"key": "autoSwitch.enabled", "value": True})
        self.assertEqual(self.mode(self.proc.paths.settings) & 0o077, 0)

    def test_a_switch_through_the_socket(self):
        client = self.proc.client()
        result = client.call("accounts.switch", {"account": "b@example.invalid"}, )
        self.assertTrue(result["account"]["isActive"])
        self.assertEqual(self.rig.live_credential()["claudeAiOauth"]["accessToken"], self.b.access)
        self.assertEqual(self.mode(self.rig.paths.credentials), 0o600)

    def test_the_log_is_in_the_mac_format_without_emails_or_tokens(self):
        client = self.proc.client()
        client.call("accounts.switch", {"account": "b@example.invalid"})
        text = read_file(self.proc.paths.log)
        self.assertRegex(text.splitlines()[0], r"^====== PixelSwitch launched \d{4}-\d\d-\d\dT[\d:.]+Z ======$")
        self.assertRegex(text.splitlines()[1], r"^\[\d{4}-\d\d-\d\dT[\d:.]+Z\] \[(INFO|WARN|ERROR|DEBUG)\] \[[A-Za-z]+\] ")
        for secret in (self.a.email, self.b.email, self.a.access, self.b.access, self.a.refresh, self.b.refresh):
            self.assertNotIn(secret, text)


class ShutdownWorkTests(unittest.TestCase):
    def test_sigterm_cancels_a_running_sign_in_and_stops_the_cli(self):
        rig = Rig(self, root=scratch_root(self))
        rig.seed_account("a@example.invalid", active=True)
        rig.build()
        proc = Proc(self, rig).start()
        client = proc.client()
        info = client.call("accounts.signIn.start")["signIn"]
        self.assertTrue(wait_for(lambda: client.call("accounts.signIn.status")["signIn"]["state"] == "waitingForUser", 30))
        proc.process.send_signal(signal.SIGTERM)
        code, out, err = proc.finish(timeout=40)
        self.assertEqual(code, 0, err)
        time.sleep(0.3)
        listing = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
        self.assertNotIn(os.path.join(rig.tmp, "stubbin", "claude"), listing, "the stub claude was stopped")
        self.assertFalse(os.path.exists(os.path.join(rig.tmp, "run", "pixelswitch", parse_prefix(info))), "capture folder gone")
        self.assertFalse(os.path.exists(os.path.join(rig.paths.claude_dir, ".credentials.json.bak")))

    def test_an_in_flight_renewal_finishes_its_post_and_its_save_before_the_daemon_exits(self):
        rig = Rig(self, root=scratch_root(self))
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)  # expired: the first cycle renews it
        new_access, new_refresh = fabricated("oat01"), fabricated("ort01")
        rig.api.script("token", b.refresh, R(200, {"access_token": new_access, "refresh_token": new_refresh, "expires_in": 3600}, delay=1.5))
        rig.api.add_account(new_access, b.uuid, b.email)
        rig.build()
        proc = Proc(self, rig).start()
        self.assertTrue(wait_for(lambda: rig.api.calls_to("token"), 30), "the renewal began")
        proc.process.send_signal(signal.SIGTERM)
        code, out, err = proc.finish(timeout=60)
        self.assertEqual(code, 0, err)
        saved = json.loads(rig.backup_of(b).token)["claudeAiOauth"]
        self.assertEqual(saved["refreshToken"], new_refresh, "the rotated refresh token was saved, not lost")


class IdleTests(unittest.TestCase):
    """Spec 5.1 and 5.4: a switch, a sign-in, a saved-login renewal or a store write blocks the updater's restart."""

    def setUp(self):
        self.rig = Rig(self)
        self.rig.seed_account("a@example.invalid", active=True)
        self.coordinator = self.rig.build()

    def holding(self, lock):
        """Holds ``lock`` from another thread until the returned event is set (an RLock is re-entrant, so the
        test thread could not make it look busy)."""
        held, release = threading.Event(), threading.Event()

        def hold():
            with lock:
                held.set()
                release.wait(10)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.assertTrue(held.wait(5))
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        return release

    def test_idle_when_nothing_is_going_on(self):
        self.assertTrue(daemon_module.is_idle(self.coordinator))

    def test_a_switch_a_sign_in_a_capture_a_removal_or_a_reconcile_blocks_it(self):
        for kind in ("switch", "signin", "capture", "remove", "reconcile", "audit"):
            self.assertTrue(self.coordinator.gate.try_begin(kind), kind)
            self.assertFalse(daemon_module.is_idle(self.coordinator), kind)
            self.coordinator.gate.end(kind)
            self.assertTrue(daemon_module.is_idle(self.coordinator), kind)

    def test_a_renewal_in_flight_blocks_it(self):
        with self.coordinator._renewal_lock:
            self.coordinator._renewals["some-account"] = object()
        self.assertEqual(self.coordinator.renewals_in_flight(), 1)
        self.assertFalse(daemon_module.is_idle(self.coordinator))
        with self.coordinator._renewal_lock:
            self.coordinator._renewals.clear()
        self.assertTrue(daemon_module.is_idle(self.coordinator))

    def test_a_write_to_the_saved_logins_the_accounts_or_the_settings_blocks_it(self):
        for name in ("backups", "accounts_file", "settings"):
            store = getattr(self.coordinator, name)
            lock = store._lock
            release = self.holding(lock)
            self.assertFalse(daemon_module.is_idle(self.coordinator), name)
            release.set()
            self.assertTrue(wait_for(lambda: daemon_module.is_idle(self.coordinator), 5), name)

    def test_the_updater_the_daemon_builds_waits_on_it(self):
        home = tmpdir(self)
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "XDG_RUNTIME_DIR": os.path.join(home, "run")}
        paths = paths_module.Paths.from_env(env)
        fd = daemon_module.acquire_daemon_lock(paths.daemon_lock)
        self.addCleanup(os.close, fd)
        daemon = daemon_module.build(env, fd, paths, threading.Event())
        self.addCleanup(daemon.log.close)
        self.assertTrue(daemon.updater._idle())
        self.assertTrue(daemon.coordinator.gate.try_begin("switch"))
        self.assertFalse(daemon.updater._idle(), "a switch in progress holds the restart back")
        daemon.coordinator.gate.end("switch")
        self.assertTrue(daemon.updater._idle())


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class StopBudgetTests(unittest.TestCase):
    """SIGTERM: the whole stop fits inside TimeoutStopSec=90, and a daemon that never bound the socket does
    not remove the one that did."""

    def make(self, case_clock, *, begin_takes=0.0, finish_takes=0.0):
        events = []
        clock = case_clock

        class Coordinator:
            def begin_shutdown(self_inner):
                events.append(("begin_shutdown",))
                clock.now += begin_takes

            def finish_shutdown(self_inner, timeout):
                events.append(("finish_shutdown", timeout))
                clock.now += min(finish_takes, timeout)
                return finish_takes <= timeout

        class Server:
            def stop_accepting(self_inner):
                events.append(("stop_accepting",))

            def stop(self_inner):
                events.append(("server.stop",))

        class Updater:
            def stop(self_inner, timeout=5.0):
                events.append(("updater.stop", timeout))
                clock.now += min(timeout, 1.0)

        from pixelswitch.logfile import Log

        home = tmpdir(self)
        paths = paths_module.Paths(home, None)
        fd = os.open(os.devnull, os.O_RDONLY)
        daemon = daemon_module.Daemon(paths, Log(None), Coordinator(), Server(), None, Updater(), fd,
                                      threading.Event(), None, True)
        return daemon, events, paths

    def test_the_budget_is_under_the_units_timeout(self):
        self.assertLess(daemon_module.STOP_BUDGET_SECONDS, 90)
        self.assertEqual(daemon_module.RENEWAL_WAIT_SECONDS, 80.0)
        unit = read_file(os.path.join(LINUX_DIR, "packaging", "pixelswitch.service"))
        self.assertIn("TimeoutStopSec=90", unit)

    def test_the_updater_is_told_to_stop_before_the_wait_and_the_wait_is_bounded(self):
        clock = FakeClock()
        daemon, events, _ = self.make(clock, finish_takes=500.0)
        began = clock()
        daemon.stop(clock=clock)
        names = [event[0] for event in events]
        self.assertEqual(names, ["begin_shutdown", "updater.stop", "stop_accepting", "finish_shutdown", "updater.stop",
                                 "server.stop"])
        self.assertEqual(events[1], ("updater.stop", 0.0), "the flag first: no download, self-check or restart starts now")
        self.assertEqual(events[3], ("finish_shutdown", 80.0))
        self.assertLessEqual(clock() - began, daemon_module.STOP_BUDGET_SECONDS)

    def test_a_slow_start_to_the_shutdown_shortens_the_wait_so_the_total_still_fits(self):
        clock = FakeClock()
        daemon, events, _ = self.make(clock, begin_takes=20.0, finish_takes=500.0)
        began = clock()
        daemon.stop(clock=clock)
        finish = [event for event in events if event[0] == "finish_shutdown"][0]
        self.assertLessEqual(finish[1], daemon_module.STOP_BUDGET_SECONDS - 20.0)
        self.assertLessEqual(clock() - began, daemon_module.STOP_BUDGET_SECONDS)
        self.assertLess(clock() - began, 90)

    def test_an_exhausted_budget_still_stops_everything_without_waiting(self):
        clock = FakeClock()
        daemon, events, _ = self.make(clock, begin_takes=100.0, finish_takes=500.0)
        daemon.stop(clock=clock)
        self.assertIn(("finish_shutdown", 0.0), events)
        self.assertIn(("server.stop",), events)

    def test_a_daemon_whose_server_never_bound_leaves_the_running_daemons_socket_and_pointer_alone(self):
        clock = FakeClock()
        daemon, events, paths = self.make(clock)
        fsio.atomic_write(paths.socket_path_file, paths.socket + "\n")
        daemon.stop(server_started=False, clock=clock)
        self.assertNotIn(("server.stop",), events)
        self.assertTrue(os.path.exists(paths.socket_path_file), "the pointer belongs to the daemon that is running")

    def test_a_daemon_that_bound_it_removes_its_own_pointer(self):
        clock = FakeClock()
        daemon, events, paths = self.make(clock)
        fsio.atomic_write(paths.socket_path_file, paths.socket + "\n")
        daemon.stop(clock=clock)
        self.assertIn(("server.stop",), events)
        self.assertFalse(os.path.exists(paths.socket_path_file))


def parse_prefix(info):
    return "pixelswitch-signin-" + info["id"]


if __name__ == "__main__":
    unittest.main()
