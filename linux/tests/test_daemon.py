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
import time
import unittest
import uuid

from pixelswitch import fsio, paths as paths_module, testmode
from pixelswitch.client import ControlClient

from .lb_support import Rig, read_file, tmpdir, wait_for
from .mock_api import R, fabricated

LINUX_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = "import sys; sys.path.insert(0, %r); from pixelswitch.daemon import main; sys.exit(main(sys.argv[1:]))" % LINUX_DIR


def scratch_root(case) -> str:
    path = os.path.join(testmode.scratch_root(), "ut-" + uuid.uuid4().hex[:10])
    os.makedirs(path, mode=0o700)
    case.addCleanup(shutil.rmtree, path, True)
    return path


class Proc:
    def __init__(self, case, rig: Rig, extra_env=None, args=()):
        self.case = case
        self.rig = rig
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": rig.home, "XDG_RUNTIME_DIR": os.path.join(rig.tmp, "run"),
            "PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": rig.api.base, "PYTHONDONTWRITEBYTECODE": "1",
        }
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
        return self

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

    def test_the_test_api_base_is_ignored_outside_test_mode(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        rig.build()
        env = {"PATH": "/usr/bin:/bin", "HOME": rig.home, "XDG_RUNTIME_DIR": os.path.join(rig.tmp, "run"),
               "PIXELSWITCH_TEST_API_BASE": rig.api.base, "PYTHONDONTWRITEBYTECODE": "1"}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        process = subprocess.Popen([sys.executable, "-c", CODE], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        socket_path = os.path.join(env["XDG_RUNTIME_DIR"], "pixelswitch", "control.sock")
        self.assertTrue(wait_for(lambda: os.path.exists(socket_path), 30))
        client = ControlClient(socket_path)
        client.connect(launch=False)
        self.assertNotIn("testMode", client.call("status.get"))
        time.sleep(1.0)
        self.assertEqual(rig.api.calls, [], "the base was not honoured: the daemon would have gone to the real hosts")
        client.disconnect()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)


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


def parse_prefix(info):
    return "pixelswitch-signin-" + info["id"]


if __name__ == "__main__":
    unittest.main()
