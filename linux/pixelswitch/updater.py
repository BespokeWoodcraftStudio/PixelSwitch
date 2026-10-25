"""The Linux updater's I/O (spec 5.4), on top of lane LA's checks in :mod:`update_check`.

Schedule: a check two minutes after start and every 6 hours (a 404 is "no update", quietly).
For one update, in this order, each step failing closed with nothing installed:

1. fetch ``latest-linux.json`` and validate it (``update_check.validate_feed``: nothing in it is trusted);
2. download the tarball into a fresh 0700 staging folder beside ``app/``, cut off at the feed's ``size``;
3. verify size, SHA-256 and the Ed25519 signature BEFORE anything is extracted;
4. extract with the safe extractor, find the folder holding ``VERSION``, and check ``VERSION`` against the feed;
5. rename it to ``app/<version>-<build>``;
6. run ``python3 -I <new>/bin/pixelswitchd --self-check`` (no lock, no socket, no writes); it must exit 0
   and print the feed's version and build;
7. wait until no switch, sign-in, saved-login renewal or store write is in progress;
8. ONLY THEN, immediately before the restart, point ``current`` at the new version (``previous`` at the old one);
9. restart: ``systemctl --user restart pixelswitch.service``.

The previous version's folder is kept; only versions older than it are removed (after the new daemon
has written its ``good-<build>`` marker). If the new build then fails to start twice without that
marker, ``rollback.py`` flips ``current`` back (see there).

In test mode (spec 5.7) the release URL and the public key come from ``PIXELSWITCH_TEST_RELEASE_BASE``
and ``PIXELSWITCH_TEST_PUBLIC_KEY``, the scheduled check is off unless a test release base is given,
and the restart is never run: a scratch run must not touch ``systemctl --user``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import fsio
from .api import Transport, TransportError
from .logfile import CategoryLog
from .paths import Paths
from .update_check import (
    FEED_URL, RELEASE_BASE, SU_PUBLIC_ED_KEY, UpdateError, check_extracted_version, parse_feed, safe_extract,
    validate_feed, verify_download,
)
from .version import VersionInfo

FIRST_CHECK_DELAY = 120.0
CHECK_INTERVAL = 6 * 3600.0
FEED_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0
SELF_CHECK_TIMEOUT = 60.0
IDLE_WAIT_LIMIT = 30 * 60.0
SERVICE_NAME = "pixelswitch.service"
VERSION_DIR = re.compile(r"([0-9]{1,6}\.[0-9]{1,6})-([0-9]{1,9})\Z")
SELF_CHECK_LINE = re.compile(r"pixelswitchd ([0-9]+\.[0-9]+) \(build ([0-9]+)\)")


@dataclass(frozen=True)
class UpdateOutcome:
    """``kind``: ``none`` (nothing newer, or the feed is not there), ``installed`` (current flipped
    and the restart asked for), ``waiting`` (installed on disk but the daemon was busy; the next
    check finishes the flip), ``failed`` (a check refused it: ``reason``)."""

    kind: str
    reason: str = ""
    version: str = ""
    build: int = 0


def version_dir_name(version: str, build: int) -> str:
    return f"{version}-{build}"


def parse_version_dir(name: str) -> tuple[str, int] | None:
    match = VERSION_DIR.match(name)
    return (match.group(1), int(match.group(2))) if match else None


def read_link_name(path: str) -> str | None:
    try:
        return os.path.basename(os.readlink(path))
    except OSError:
        return None


def flip_symlink(link: str, target_name: str) -> None:
    """Atomically points ``link`` at ``target_name`` (a relative name): a temp symlink renamed over it."""
    temp = f"{link}.new-{os.getpid()}"
    try:
        os.unlink(temp)
    except FileNotFoundError:
        pass
    os.symlink(target_name, temp)
    try:
        os.replace(temp, link)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    fsio.fsync_dir(os.path.dirname(link))


def strip_group_other_write(root: str) -> None:
    """Removes group and other write access from everything under ``root`` (and ``root``), whatever
    the umask of the process was: a directory the archive did not list gets its mode from the
    umask, and an installed version must never be writable by anyone else. Symlinks are left alone."""
    for directory, names, files in os.walk(root):
        for name in [""] + names + files:
            path = os.path.join(directory, name) if name else directory
            try:
                status = os.lstat(path)
            except OSError:
                continue
            if os.path.islink(path):
                continue
            mode = status.st_mode & 0o7777
            if mode & 0o022:
                os.chmod(path, mode & ~0o022)


def prune_old_versions(app_dir: str, log: CategoryLog | None = None) -> list[str]:
    """Deletes version folders OLDER than the previous version; keeps ``current`` and ``previous``
    (and anything newer than either). Returns the names removed."""
    keep: set[str] = set()
    builds: list[int] = []
    for link in ("current", "previous"):
        name = read_link_name(os.path.join(app_dir, link))
        parsed = parse_version_dir(name) if name else None
        if parsed:
            keep.add(name)  # type: ignore[arg-type]
            builds.append(parsed[1])
    if not builds:
        return []
    floor = min(builds)
    removed: list[str] = []
    try:
        names = sorted(os.listdir(app_dir))
    except OSError:
        return []
    for name in names:
        parsed = parse_version_dir(name)
        path = os.path.join(app_dir, name)
        if parsed is None or name in keep or parsed[1] >= floor or os.path.islink(path) or not os.path.isdir(path):
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(name)
        if log is not None:
            log.info(f"[update] Removed old version {name}")
    return removed


class Updater:
    def __init__(
        self,
        *,
        paths: Paths,
        log: CategoryLog,
        running: VersionInfo,
        transport: Transport,
        automatic: Callable[[], bool],
        idle: Callable[[], bool],
        restart: Callable[[], None],
        feed_url: str = FEED_URL,
        release_base: str = RELEASE_BASE,
        public_key: str = SU_PUBLIC_ED_KEY,
        scheduled: bool = True,
        first_delay: float = FIRST_CHECK_DELAY,
        interval: float = CHECK_INTERVAL,
        python: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.paths = paths
        self._log = log
        self.running = running
        self._transport = transport
        self._automatic = automatic
        self._idle = idle
        self._restart = restart
        self._feed_url = feed_url
        self._release_base = release_base
        self._public_key = public_key
        self._scheduled = scheduled
        self._first_delay = first_delay
        self._interval = interval
        self._python = python or sys.executable or "python3"
        self._sleep = sleep
        self._clock = clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._manual = False
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()

    # -- the schedule ------------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="updater", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def set_automatic(self, on: bool) -> None:
        """The setting is read when a scheduled check comes due, so nothing needs waking."""
        self._log.info(f"[update] Automatic updates {'on' if on else 'off'}")

    def check_now(self) -> None:
        """``app.checkForUpdates``: a check right away, whatever the automatic setting says."""
        self._manual = True
        self._wake.set()

    def _loop(self) -> None:
        delay: float | None = self._first_delay if self._scheduled else None
        while not self._stop.is_set():
            triggered = self._wake.wait(timeout=delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            manual, self._manual = self._manual, False
            if manual or (not triggered and self._automatic()):
                try:
                    self.check_and_install()
                except Exception as error:  # an updater bug must never take the daemon down
                    self._log.error(f"[update] Check failed unexpectedly ({type(error).__name__})")
            if not triggered or self._scheduled:
                delay = self._interval if self._scheduled else None

    # -- one check ------------------------------------------------------------------------------------------------------

    def check_and_install(self) -> UpdateOutcome:
        if not self._busy.acquire(blocking=False):
            return UpdateOutcome("none", "a check is already running")
        staging = os.path.join(self.paths.data_dir, f".update-{os.getpid()}-{int(time.time())}")
        try:
            return self._check(staging)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            self._busy.release()

    def _check(self, staging: str) -> UpdateOutcome:
        try:
            response = self._transport.request("GET", self._feed_url, headers={"Accept": "application/json"},
                                               timeout=FEED_TIMEOUT)
        except TransportError:
            self._log.info("[update] The update feed could not be reached")
            return UpdateOutcome("none", "unreachable")
        if response.status == 404:
            return UpdateOutcome("none", "no feed")  # no release yet: quietly "no update"
        if response.status != 200:
            self._log.info(f"[update] The update feed answered HTTP {response.status}")
            return UpdateOutcome("none", f"HTTP {response.status}")
        try:
            feed = validate_feed(parse_feed(response.body), self.running.build, release_base=self._release_base)
        except UpdateError as error:
            if error.reason == "older":
                return UpdateOutcome("none", "up to date")
            self._log.warning(f"[update] The feed was refused ({error.reason})")
            return UpdateOutcome("failed", error.reason)
        name = version_dir_name(feed.version, feed.build)
        target = os.path.join(self.paths.app_dir, name)
        if not os.path.isdir(target):
            outcome = self._download_and_unpack(feed, staging, target)
            if outcome is not None:
                return outcome
        return self._finish(feed, target, name)

    def _download_and_unpack(self, feed, staging: str, target: str) -> UpdateOutcome | None:
        try:
            fsio.ensure_private_dir(staging)
            tarball = os.path.join(staging, "update.tar.gz")
            self._log.info(f"[update] Downloading version {feed.version} (build {feed.build})")
            try:
                status, written, exceeded = self._transport.download(feed.url, tarball, limit=feed.size,
                                                                     timeout=DOWNLOAD_TIMEOUT)
            except TransportError:
                self._log.info("[update] The download failed; will try again at the next check")
                return UpdateOutcome("none", "download failed")
            if status == 404:
                return UpdateOutcome("none", "no download")
            if status != 200:
                return UpdateOutcome("none", f"HTTP {status}")
            if exceeded:
                raise UpdateError("size", "The download is larger than the feed promised.")
            verify_download(tarball, feed, self._public_key)
            extracted = os.path.join(staging, "extracted")
            safe_extract(tarball, extracted)
            root = self._find_root(extracted)
            check_extracted_version(root, feed, self.running.build)
            strip_group_other_write(root)
            fsio.ensure_private_dir(self.paths.app_dir)
            os.rename(root, target)
            fsio.fsync_dir(self.paths.app_dir)
            return None
        except UpdateError as error:
            self._log.warning(f"[update] Update refused ({error.reason}): {error.message}")
            return UpdateOutcome("failed", error.reason)
        except OSError as error:
            self._log.warning(f"[update] Update could not be unpacked ({type(error).__name__})")
            return UpdateOutcome("failed", "unpack")

    @staticmethod
    def _find_root(extracted: str) -> str:
        """The folder holding ``VERSION``: the extraction root itself, or its only top-level folder."""
        if os.path.isfile(os.path.join(extracted, "VERSION")):
            return extracted
        entries = [e for e in os.listdir(extracted)]
        if len(entries) == 1 and os.path.isdir(os.path.join(extracted, entries[0])) \
                and not os.path.islink(os.path.join(extracted, entries[0])):
            return os.path.join(extracted, entries[0])
        raise UpdateError("version-file", "The update has no VERSION file at its top.")

    def _finish(self, feed, target: str, name: str) -> UpdateOutcome:
        if not self._self_check(feed, target):
            shutil.rmtree(target, ignore_errors=True)  # it is ours, and it does not start
            return UpdateOutcome("failed", "self-check", feed.version, feed.build)
        deadline = self._clock() + IDLE_WAIT_LIMIT
        while not self._idle():
            if self._stop.is_set() or self._clock() >= deadline:
                self._log.info("[update] Installed, but PixelSwitch stayed busy; the flip waits for the next check")
                return UpdateOutcome("waiting", "busy", feed.version, feed.build)
            self._sleep(5.0)
        # Flip `current` only now, immediately before the restart.
        previous = read_link_name(os.path.join(self.paths.app_dir, "current"))
        try:
            if previous and previous != name:
                flip_symlink(os.path.join(self.paths.app_dir, "previous"), previous)
            flip_symlink(os.path.join(self.paths.app_dir, "current"), name)
        except OSError as error:
            self._log.error(f"[update] Could not point current at the new version ({type(error).__name__})")
            return UpdateOutcome("failed", "flip", feed.version, feed.build)
        self._log.info(f"[update] Version {feed.version} (build {feed.build}) is current; restarting")
        self._restart()
        return UpdateOutcome("installed", "", feed.version, feed.build)

    def _self_check(self, feed, target: str) -> bool:
        """``python3 -I <new>/bin/pixelswitchd --self-check``: exit 0 and the feed's version and build."""
        script = os.path.join(target, "bin", "pixelswitchd")
        if not os.path.isfile(script):
            self._log.warning("[update] The update has no bin/pixelswitchd")
            return False
        try:
            completed = subprocess.run([self._python, "-I", script, "--self-check"], stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, timeout=SELF_CHECK_TIMEOUT,
                                       env=self._self_check_env())
        except (OSError, subprocess.SubprocessError):
            self._log.warning("[update] The update's self-check could not run")
            return False
        match = SELF_CHECK_LINE.search(completed.stdout)
        if completed.returncode != 0 or match is None or match.group(1) != feed.version or int(match.group(2)) != feed.build:
            self._log.warning(f"[update] The update's self-check failed (exit {completed.returncode})")
            return False
        return True

    @staticmethod
    def _self_check_env() -> dict[str, str]:
        keep = ("HOME", "PATH", "LANG", "LC_ALL", "PIXELSWITCH_TEST_MODE", "XDG_RUNTIME_DIR")
        return {key: os.environ[key] for key in keep if key in os.environ}


def restart_service() -> None:
    """``systemctl --user restart pixelswitch.service`` (never in test mode; the daemon passes a no-op there)."""
    subprocess.Popen(["systemctl", "--user", "restart", SERVICE_NAME], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
