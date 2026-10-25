#!/usr/bin/env python3
"""Scratch-tree run of the Linux faces on the laptop (spec 5.7; lane LC's third check).

It builds the release tarball, installs it with ``install.sh --from <tarball> --no-systemd --no-autostart`` into a
scratch HOME, starts the INSTALLED daemon in test mode against the mock API and a stub ``claude`` (fabricated logins
only), runs ``Tests/integration/remote-control.sh`` with the installed CLI, then starts the installed tray ONCE against
that daemon on the desktop's session bus to prove it registers with ``org.kde.StatusNotifierWatcher``, stops it, and
proves the registration is gone. It never starts the settings window, never runs ``systemctl --user``, and compares
the real home's PixelSwitch places before and after.

Refuses to run unless this file lives under ``~/.cache/pixelswitch-dev/`` (copy ``linux/``, ``Tests/`` and
``project.yml`` there first). Exit 0 only if every check passed. Usage (from the copied tree):

    DESKTOP_BUS=unix:path=/run/user/1000/bus python3 linux/tests/lc_scratch_run.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
LINUX = os.path.dirname(HERE)
TREE = os.path.dirname(LINUX)
sys.path.insert(0, LINUX)

REAL_HOME = os.path.expanduser("~")
SCRATCH_ROOT = os.path.join(REAL_HOME, ".cache", "pixelswitch-dev")
if not os.path.abspath(__file__).startswith(SCRATCH_ROOT + os.sep):
    sys.exit(f"lc_scratch_run: refusing to run outside {SCRATCH_ROOT} (copy the tree there first)")

from pixelswitch import paths as paths_module  # noqa: E402
from pixelswitch.client import ControlClient  # noqa: E402

from tests.integration_scratch import real_home_signature, wait_for, write_accounts  # noqa: E402
from tests.lb_support import Rig  # noqa: E402
from tests.mock_api import usage_body  # noqa: E402

passed = failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}{': ' + detail if detail else ''}")
    return bool(condition)


def gdbus(bus: str, *args: str) -> str:
    done = subprocess.run(["gdbus", "call", "--session", *args], capture_output=True, text=True, timeout=20,
                          env={"PATH": "/usr/bin:/bin", "DBUS_SESSION_BUS_ADDRESS": bus})
    return done.stdout.strip() if done.returncode == 0 else ""


def registered_items(bus: str) -> str:
    return gdbus(bus, "--dest", "org.kde.StatusNotifierWatcher", "--object-path", "/StatusNotifierWatcher", "--method",
                 "org.freedesktop.DBus.Properties.Get", "org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems")


def main() -> int:
    bus = os.environ.get("DESKTOP_BUS", f"unix:path=/run/user/{os.getuid()}/bus")
    before = real_home_signature(REAL_HOME)
    # Unique per run: two runs at once (or a leftover from a crashed one) must never share a folder.
    work = os.path.join(SCRATCH_ROOT, f"lc-run-{uuid.uuid4().hex[:8]}", "work")
    os.makedirs(work, exist_ok=True)
    rig = Rig(None, keep_log=False, root=os.path.join(work, "rig"))
    daemon = tray = None
    try:
        now = time.time()
        rig.seed_account("alice@example.invalid", active=True, usage=usage_body(42, 61, now=now), priority=1, label="Alice")
        rig.seed_account("bob@example.invalid", usage=usage_body(20, 20, now=now), priority=2)
        write_accounts(rig)
        with open(rig.paths.settings, "w", encoding="utf-8") as handle:
            json.dump({"claudeBinaryPathPreference": rig.stub}, handle)

        # 1. build and install the tarball
        dist = os.path.join(work, "dist")
        done = subprocess.run(["bash", os.path.join(LINUX, "build-release.sh"), "--out", dist], capture_output=True, text=True)
        check("build-release.sh builds the tarball", done.returncode == 0, done.stderr + done.stdout)
        tarballs = [f for f in os.listdir(dist) if f.endswith(".tar.gz")]
        if not check("... and install-linux.sh", len(tarballs) == 1 and os.path.exists(os.path.join(dist, "install-linux.sh"))):
            return 1
        install_env = {"HOME": rig.home, "PATH": "/usr/bin:/bin"}
        done = subprocess.run(["bash", os.path.join(LINUX, "install.sh"), "--from", os.path.join(dist, tarballs[0]),
                               "--no-systemd", "--no-autostart"], capture_output=True, text=True, env=install_env)
        check("install.sh --from <tarball> --no-systemd --no-autostart installs (real dependency checks on)", done.returncode == 0,
              done.stderr + done.stdout)
        print("      " + "\n      ".join((done.stdout + done.stderr).strip().splitlines()[:3]))
        bin_dir = os.path.join(rig.home, ".local", "bin")
        for name in ("pixelswitch", "pixelswitchd", "pixelswitch-tray", "pixelswitch-settings"):
            check(f"~/.local/bin/{name} is a working link", os.access(os.path.join(bin_dir, name), os.X_OK))
        check("nothing was written to the real systemd or autostart folders",
              real_home_signature(REAL_HOME)[".config/systemd/user"] == before[".config/systemd/user"]
              and real_home_signature(REAL_HOME)[".config/autostart"] == before[".config/autostart"])

        # 2. the installed daemon, in test mode
        xdg = os.path.join(rig.tmp, "xdg")
        for sub in ("run", "config", "data", "state", "cache"):
            os.makedirs(os.path.join(xdg, sub), mode=0o700, exist_ok=True)
        env = {
            "PATH": "/usr/bin:/bin", "HOME": rig.home, "XDG_RUNTIME_DIR": os.path.join(xdg, "run"),
            "XDG_CONFIG_HOME": os.path.join(xdg, "config"), "XDG_DATA_HOME": os.path.join(xdg, "data"),
            "XDG_STATE_HOME": os.path.join(xdg, "state"), "XDG_CACHE_HOME": os.path.join(xdg, "cache"),
            "PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": rig.api.base, "PYTHONDONTWRITEBYTECODE": "1",
        }
        assert not any(k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_") for k in env)
        paths = paths_module.Paths(rig.home, env["XDG_RUNTIME_DIR"])
        daemon = subprocess.Popen([os.path.join(bin_dir, "pixelswitchd")], env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        up = wait_for(lambda: os.path.exists(paths.socket), 40)
        if not check("the installed daemon starts and binds its socket", up and daemon.poll() is None):
            return 1
        client = ControlClient(paths.socket)
        client.connect(launch=False)
        status = client.call("status.get")
        check("status.get reports testMode true", status.get("testMode") is True, str(status))
        client.disconnect()

        # 3. remote-control.sh with the installed CLI
        script_env = {"PATH": "/usr/bin:/bin", "HOME": rig.home, "PIXELSWITCH_CLI": os.path.join(bin_dir, "pixelswitch"),
                      "PIXELSWITCH_CONTROL_SOCKET": paths.socket, "PIXELSWITCH_NO_LAUNCH": "1"}
        script = os.path.join(TREE, "Tests", "integration", "remote-control.sh")
        done = subprocess.run(["bash", script], capture_output=True, text=True, env=script_env, timeout=300)
        print("      " + "\n      ".join(done.stdout.strip().splitlines()))
        summary = re.findall(r"(\d+) passed, (\d+) failed", done.stdout)
        check("remote-control.sh ran against the scratch daemon", bool(summary), done.stdout + done.stderr)
        if summary:
            check("remote-control.sh: every check passed", summary[-1][1] == "0" and done.returncode == 0,
                  "see the FAIL lines above")
        # The same script with the one line that needs the Mac's cost dashboard (usage ... machine todayCost) left out.
        text = open(script, encoding="utf-8").read()
        relaxed = text.replace('echo "$usage" | field machine todayCost >/dev/null', 'true')
        relaxed_path = os.path.join(work, "remote-control-no-machine.sh")
        with open(relaxed_path, "w", encoding="utf-8") as handle:
            handle.write(relaxed)
        done2 = subprocess.run(["bash", relaxed_path], capture_output=True, text=True, env=script_env, timeout=300)
        summary2 = re.findall(r"(\d+) passed, (\d+) failed", done2.stdout)
        check("remote-control.sh without its Mac-only cost line passes in full", bool(summary2) and summary2[-1][1] == "0" and done2.returncode == 0,
              done2.stdout)
        if summary2:
            print(f"      {summary2[-1][0]} passed, {summary2[-1][1]} failed (cost line left out)")

        # 4. the tray, once, on the desktop's bus
        before_items = registered_items(bus)
        check("the desktop's StatusNotifierWatcher answers", before_items.startswith("("), before_items)
        tray_env = dict(env, DBUS_SESSION_BUS_ADDRESS=bus, WAYLAND_DISPLAY="wayland-0", PIXELSWITCH_CONTROL_SOCKET=paths.socket,
                        PIXELSWITCH_NO_LAUNCH="1")
        tray = subprocess.Popen([os.path.join(bin_dir, "pixelswitch-tray")], env=tray_env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        name = f"org.kde.StatusNotifierItem-{tray.pid}-1"
        seen = wait_for(lambda: name in registered_items(bus), 30)
        check("the tray registers with org.kde.StatusNotifierWatcher", seen, registered_items(bus))
        print(f"      RegisteredStatusNotifierItems has {name}/StatusNotifierItem: {name in registered_items(bus)}")
        item = ["--dest", name, "--object-path", "/StatusNotifierItem", "--method", "org.freedesktop.DBus.Properties.Get", "org.kde.StatusNotifierItem"]
        label_ok = wait_for(lambda: "42%" in gdbus(bus, *item, "XAyatanaLabel"), 20)
        check("its label is the active account's session percentage, read from the daemon", label_ok, gdbus(bus, *item, "XAyatanaLabel"))
        layout = gdbus(bus, "--dest", name, "--object-path", "/MenuBar", "--method", "com.canonical.dbusmenu.GetLayout", "--", "0", "-1", "[]")
        check("its menu lists the accounts in priority order", "P1 Alice · S 42%" in layout and "P2 bob@example.invalid" in layout, layout[:300])
        check("... and the fixed items", all(text in layout for text in ("Auto-switch", "Refresh", "Settings…", "Quit tray")))
        check("the tray did not start the daemon or the settings window",
              daemon.poll() is None and subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "pixelswitch-settings"], capture_output=True).returncode != 0)
        tray.send_signal(signal.SIGTERM)
        try:
            code = tray.wait(timeout=20)
        except subprocess.TimeoutExpired:
            tray.kill()
            code = tray.wait()
        check("SIGTERM stops the tray cleanly", code == 0, tray.stderr.read() if tray.stderr else "")
        gone = wait_for(lambda: name not in registered_items(bus), 20)
        check("the same call shows it gone", gone, registered_items(bus))
        print(f"      RegisteredStatusNotifierItems after: {registered_items(bus)[:200]}")
        tray = None

        # 5. the daemon stops cleanly, nothing of ours is left running
        daemon.send_signal(signal.SIGTERM)
        code = daemon.wait(timeout=60)
        check("the daemon stops on SIGTERM with exit 0", code == 0)
        daemon = None
        left = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", os.path.join(rig.home, ".local")], capture_output=True, text=True)
        check("no process of this run is left", left.returncode != 0, left.stdout)
        check("the real home's PixelSwitch places are unchanged", real_home_signature(REAL_HOME) == before)
    finally:
        for process in (tray, daemon):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
        rig.close()
        shutil.rmtree(os.path.dirname(work), ignore_errors=True)  # this run's own folder, and only that
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
