"""The tray: its StatusNotifierItem and Dbusmenu over a private session bus with a fake watcher (a subprocess, so the
desktop's own bus is never touched), and its small rules (one tray per user, re-exec once per daemon version)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "gui_sni_probe.py")


def have_gi_with(*namespaces: tuple[str, str]) -> bool:
    code = "import gi\n" + "".join(f"gi.require_version({n!r}, {v!r})\n" for n, v in namespaces)
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


HAVE_SNI = shutil.which("dbus-run-session") is not None and have_gi_with(("Gio", "2.0"), ("Dbusmenu", "0.4"))
HAVE_GI = have_gi_with(("Gio", "2.0"))


@unittest.skipUnless(HAVE_SNI, "needs PyGObject, gir1.2-dbusmenu-glib-0.4 and dbus-run-session")
class StatusNotifierItemTests(unittest.TestCase):
    def test_item_registers_exports_its_menu_and_goes_away(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        done = subprocess.run(["dbus-run-session", "--", sys.executable, PROBE], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("OK", done.stdout.splitlines()[-1])
        for line in ("the item registered with the watcher", "the registration is gone after the item stops",
                     "clicking Quit tray reaches the tray", "the active account is checked"):
            self.assertIn(line, done.stdout)


@unittest.skipUnless(HAVE_GI, "needs PyGObject")
class TrayRuleTests(unittest.TestCase):
    def setUp(self):
        from pixelswitch.gui import tray

        self.tray = tray
        self.dir = tempfile.mkdtemp(prefix="pstray", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_one_tray_per_user(self):
        from pixelswitch.paths import Paths

        paths = Paths(home=self.dir, runtime_dir=None)
        first = self.tray.take_lock(paths)
        self.assertIsNotNone(first)
        self.assertIsNone(self.tray.take_lock(paths))
        os.close(first)
        again = self.tray.take_lock(paths)
        self.assertIsNotNone(again)
        os.close(again)

    def test_launcher_path_prefers_the_link_in_local_bin(self):
        link = os.path.join(self.dir, ".local", "bin", "pixelswitch-tray")
        os.makedirs(os.path.dirname(link))
        open(link, "w").close()
        self.assertEqual(self.tray.launcher_path("pixelswitch-tray", self.dir), link)
        sibling = self.tray.launcher_path("pixelswitch-settings", self.dir)
        self.assertTrue(sibling.endswith(os.path.join("bin", "pixelswitch-settings")))
        self.assertIsNone(self.tray.launcher_path("nope", self.dir))

    def make_tray(self, own, env=None, own_build=None, installed=None):
        from pixelswitch.gui import model

        obj = object.__new__(self.tray.Tray)
        obj.env = env if env is not None else {}
        obj.own_version = own
        obj.own_build = own_build
        if installed is not None:  # the VERSION of app/current, as the updater leaves it
            current = os.path.join(self.dir, ".local", "share", "pixelswitch", "app", "current")
            os.makedirs(current, exist_ok=True)
            with open(os.path.join(current, "VERSION"), "w") as handle:
                handle.write(installed)
        obj._reexec_done = False
        obj.paths = type("P", (), {"home": self.dir})()
        stopped = []
        obj.link = type("L", (), {"stop": lambda s: stopped.append("link")})()
        obj.backend = type("B", (), {"stop": lambda s: stopped.append("backend")})()
        launcher = os.path.join(self.dir, ".local", "bin", "pixelswitch-tray")
        os.makedirs(os.path.dirname(launcher), exist_ok=True)
        open(launcher, "w").close()
        return obj, launcher, stopped, model

    def test_it_starts_itself_again_once_when_the_daemon_reports_another_version(self):
        obj, launcher, stopped, model = self.make_tray("1.9")
        calls = []
        original = os.execv
        os.execv = lambda path, args: calls.append((path, args))
        self.addCleanup(setattr, os, "execv", original)
        self.addCleanup(os.environ.pop, self.tray.REEXEC_ENV, None)
        same = model.Snapshot(connected=True, status={"appVersion": "1.9"})
        obj.maybe_reexec(same)
        self.assertEqual(calls, [])
        newer = model.Snapshot(connected=True, status={"appVersion": "2.0"})
        obj.maybe_reexec(newer)
        self.assertEqual(calls, [(launcher, [launcher])])
        self.assertEqual(stopped, ["link", "backend"])
        self.assertEqual(os.environ[self.tray.REEXEC_ENV], "2.0")

    def test_a_tray_that_already_re_executed_for_that_version_stays(self):
        obj, launcher, stopped, model = self.make_tray("1.9", env={self.tray.REEXEC_ENV: "2.0"})
        calls = []
        original = os.execv
        os.execv = lambda path, args: calls.append(path)
        self.addCleanup(setattr, os, "execv", original)
        obj.maybe_reexec(model.Snapshot(connected=True, status={"appVersion": "2.0"}))
        self.assertEqual(calls, [])  # no loop if the launcher still resolves to the old version

    def test_a_build_only_update_restarts_the_tray_too_once(self):
        # 2.0 build 31 to 2.0 build 32: the marketing version is the same, so only the build tells.
        obj, launcher, stopped, model = self.make_tray("2.0", own_build=31, installed="version=2.0\nbuild=32\n")
        calls = []
        original = os.execv
        os.execv = lambda path, args: calls.append((path, args))
        self.addCleanup(setattr, os, "execv", original)
        self.addCleanup(os.environ.pop, self.tray.REEXEC_ENV, None)
        obj.maybe_reexec(model.Snapshot(connected=True, status={"appVersion": "2.0"}))
        self.assertEqual(calls, [(launcher, [launcher])])
        self.assertEqual(os.environ[self.tray.REEXEC_ENV], "2.0-32")

    def test_the_same_build_or_a_tray_that_already_restarted_for_it_stays(self):
        calls = []
        original = os.execv
        os.execv = lambda path, args: calls.append(path)
        self.addCleanup(setattr, os, "execv", original)
        same, _, _, model = self.make_tray("2.0", own_build=32, installed="version=2.0\nbuild=32\n")
        same.maybe_reexec(model.Snapshot(connected=True, status={"appVersion": "2.0"}))
        again, _, _, _ = self.make_tray("2.0", env={self.tray.REEXEC_ENV: "2.0-32"}, own_build=31, installed="version=2.0\nbuild=32\n")
        again.maybe_reexec(model.Snapshot(connected=True, status={"appVersion": "2.0"}))
        unreadable, _, _, _ = self.make_tray("2.0", own_build=31, installed="garbage")  # no build to compare: the version decides
        unreadable.maybe_reexec(model.Snapshot(connected=True, status={"appVersion": "2.0"}))
        self.assertEqual(calls, [])

    def test_nothing_while_the_daemon_is_not_answering(self):
        obj, launcher, stopped, model = self.make_tray("1.9")
        calls = []
        original = os.execv
        os.execv = lambda path, args: calls.append(path)
        self.addCleanup(setattr, os, "execv", original)
        obj.maybe_reexec(model.Snapshot(connected=False))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
