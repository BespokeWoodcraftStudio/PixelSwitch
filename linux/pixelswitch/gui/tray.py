"""``pixelswitch-tray``: the GNOME top-bar icon (spec 5.1).

A StatusNotifierItem whose label is the active account's session percentage and whose menu lists
every account in priority order (``P1 address · S 42% · W 61%``, the active one checked, a short form of
its message under it), click to switch, then Auto-switch on/off, Refresh, Settings, Sign in another account
and Quit tray. It is a client of the daemon's socket and lives off ``events.subscribe``.

Rules (spec 5.1): the tray never sends ``usage.refresh`` by itself (only the user's Refresh does), never
execs ``pixelswitchd`` (it only asks systemd to start the service, through the control client), and
re-executes itself when the daemon runs another version or build than its own (a build-only update, 2.0 build 31
to 2.0 build 32, counts), once per version and build so a daemon that stays on the old one cannot loop it. One tray per user (a lock in the state folder).

Needs PyGObject. The backend is chosen by ``sni.pick_backend`` (AyatanaAppIndicator3 only if importable).
"""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
from typing import Mapping

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .. import fsio, version as version_module  # noqa: E402
from ..paths import Paths  # noqa: E402
from . import model, sni  # noqa: E402
from .link import DaemonLink  # noqa: E402

REEXEC_ENV = "PIXELSWITCH_TRAY_REEXEC"


def launcher_path(name: str, home: str) -> str | None:
    """``~/.local/bin/<name>`` when it exists (it follows ``current``), else the launcher beside this
    version's package."""
    candidate = os.path.join(home, ".local", "bin", name)
    if os.path.exists(candidate):
        return candidate
    sibling = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), "bin", name)
    return sibling if os.path.exists(sibling) else None


def take_lock(paths: Paths):
    """One tray per user. Returns the open lock file (keep it open), or None when another tray holds it."""
    fsio.ensure_private_dir(paths.state_dir)
    fd = os.open(os.path.join(paths.state_dir, "tray.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


class Tray:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = os.environ if env is None else env
        self.paths = Paths.from_env(self.env)
        self.loop = GLib.MainLoop()
        self.snapshot = model.Snapshot()
        try:
            own = version_module.read_version()
            self.own_version, self.own_build = own.version, own.build
        except version_module.VersionUnavailable:
            self.own_version, self.own_build = None, None
        icons = os.path.join(self.paths.home, ".local", "share", "icons")
        self.backend = sni.pick_backend(self.on_action, icon_theme_path=icons if os.path.isdir(icons) else "",
                                        forced=self.env.get("PIXELSWITCH_TRAY_BACKEND"))
        self.link = DaemonLink(lambda fn: GLib.idle_add(self._run_once, fn), self.on_snapshot, env=self.env)
        self._reexec_done = False
        self._entries = model.build_tray_menu(self.snapshot)
        self.backend.set_menu(self._entries)

    @staticmethod
    def _run_once(fn) -> bool:
        fn()
        return False  # remove the idle source

    # --- the daemon -> the tray --------------------------------------------------------------------------

    def on_snapshot(self, snapshot: model.Snapshot) -> None:
        self.snapshot = snapshot
        self.backend.set_label(model.tray_label(snapshot.accounts) if snapshot.connected else "")
        self.backend.set_tooltip(model.tray_tooltip(snapshot.accounts) if snapshot.connected else "PixelSwitch is not running")
        entries = model.build_tray_menu(snapshot)
        if entries != self._entries:  # a menu that did not change is not redrawn (it would flicker open)
            self._entries = entries
            self.backend.set_menu(entries)
        self.maybe_reexec(snapshot)

    def installed_build(self, version: str) -> int | None:
        """The build of the installed version (``app/current/VERSION``) when it is ``version``, else None. The
        daemon reports only the marketing version, and a build-only update keeps it."""
        path = os.path.join(self.paths.home, ".local", "share", "pixelswitch", "app", "current", "VERSION")
        try:
            with open(path, encoding="utf-8") as handle:
                installed = version_module.parse_version_file(handle.read())
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        return installed.build if installed.version == version else None

    def maybe_reexec(self, snapshot: model.Snapshot) -> None:
        """The daemon runs another version or build than this tray: start the tray again through the launcher,
        which resolves ``current``. Once per version and build: if the new process still differs, it stays."""
        daemon_version = snapshot.app_version
        if self._reexec_done or not snapshot.connected or not daemon_version or not self.own_version:
            return
        build = self.installed_build(daemon_version)
        own_build = getattr(self, "own_build", None)
        key = f"{daemon_version}-{build}" if build is not None else daemon_version
        differs = daemon_version != self.own_version or (build is not None and own_build is not None and build != own_build)
        if not differs or self.env.get(REEXEC_ENV) == key:
            return
        launcher = launcher_path("pixelswitch-tray", self.paths.home)
        if launcher is None:
            return
        self._reexec_done = True
        os.environ[REEXEC_ENV] = key
        self.link.stop()
        self.backend.stop()
        os.execv(launcher, [launcher])

    # --- the user -> the daemon ----------------------------------------------------------------------------

    def on_action(self, action: str) -> None:
        if action == "quit":
            self.loop.quit()
        elif action == "settings":
            self.open_settings(None)
        elif action == "sign-in":
            self.open_settings("sign-in")
        elif action == "refresh":
            self.link.call("usage.refresh", None, self._done("Refresh"))
        elif action == "auto-switch":
            self.link.call("settings.set", {"key": "autoSwitch.enabled", "value": not self.snapshot.auto_switch_on},
                           self._done("Auto-switch"))
        elif action == "start-service":
            self.start_service()
        elif action.startswith("switch:"):
            self.link.call("accounts.switch", {"account": action.split(":", 1)[1]}, self._done("Switch"), timeout=120.0)

    def _done(self, what: str):
        def done(_result, error) -> None:
            if error is not None:
                message = getattr(error, "message", None) or getattr(error, "description", None) or str(error)
                self.notify(f"PixelSwitch: {what} did not work", message)
            self.link.request_snapshot()

        return done

    def open_settings(self, page: str | None) -> None:
        launcher = launcher_path("pixelswitch-settings", self.paths.home)
        if launcher is None:
            self.notify("PixelSwitch", "The settings window is not installed.")
            return
        args = [launcher] + (["--page", page] if page else [])
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError as error:
            self.notify("PixelSwitch", f"The settings window could not start ({error.strerror}).")

    def start_service(self) -> None:
        try:
            subprocess.Popen(["systemctl", "--user", "start", "pixelswitch.service"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.notify("PixelSwitch", "systemd could not be run to start the service.")
            return
        # Look again when it is up, so "PixelSwitch is not running" does not outlast the start by a reconnect wait.
        GLib.timeout_add(1500, lambda: self.link.request_snapshot() or False)

    def notify(self, summary: str, body: str) -> None:
        """A desktop notification over org.freedesktop.Notifications; silently nothing when there is none."""
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            connection.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                            "Notify", GLib.Variant("(susssasa{sv}i)", ("PixelSwitch", 0, sni.ICON_NAME, summary, body, [], {}, 8000)),
                            None, Gio.DBusCallFlags.NONE, 2000, None, None)
        except GLib.Error:
            print(f"{summary}: {body}", file=sys.stderr, flush=True)

    # --- run --------------------------------------------------------------------------------------------------

    def run(self) -> int:
        for sig in (signal.SIGTERM, signal.SIGINT):
            sni.unix_signal_add(sig, self._quit_signal)
        self.backend.start()
        self.link.start()
        try:
            self.loop.run()
        finally:
            self.link.stop()
            self.backend.stop()
        return 0

    def _quit_signal(self) -> bool:
        self.loop.quit()
        return False


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    os.umask(0o077)
    paths = Paths.from_env(os.environ if env is None else env)
    lock = take_lock(paths)
    if lock is None:
        print("pixelswitch-tray: already running", file=sys.stderr)
        return 0
    return Tray(env).run()


if __name__ == "__main__":
    sys.exit(main())
