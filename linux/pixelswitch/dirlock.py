"""Directory locks in the `proper-lockfile` style that Claude Code itself uses (spec 5.3).

CONFIRMED read-only from the installed Claude Code 2.1.310 on the laptop (see ``linux/README.md``,
"Claude Code's locks"): Claude Code serialises its work on the login with lock DIRECTORIES made
by ``mkdir``. A lock is held while its directory exists; the holder keeps its mtime fresh with
``utimes`` (every 5 s for the refresh lock) and removes it with ``rmdir``; a contender that finds
one answers ``ELOCKED`` and, if the directory's mtime is older than the ``stale`` limit, may
remove it and take it over. PixelSwitch follows the same protocol with one deliberate
difference: it NEVER removes, breaks or takes over a lock it did not create (spec 5.3). A busy
lock is waited for, up to a limit, and then the caller fails with nothing written.

Locks PixelSwitch holds, in the order a switch takes them:

* ``~/.claude/.oauth_refresh.lock`` (stale 60 s, touched every 5 s) with its holder record
  ``~/.claude/.oauth_refresh.lock.owner``, and the legacy ``~/.claude.lock`` (an older Claude
  Code takes only that one) around reading the live login, merging and writing it;
* ``~/.claude/.storage-write`` (stale 15 s) around the write of ``.credentials.json`` itself,
  which is how Claude Code serialises its own read-modify-write of that file;
* ``~/.claude.json.lock`` (stale 10 s, touched every 5 s) around the write of ``~/.claude.json``.

Ownership is decided on exact values (see :meth:`DirLock._is_ours`): the inode, the creation time in whole
nanoseconds where the filesystem keeps one, and the mtime PixelSwitch last set with ``os.utime(ns=...)`` (a
random sub-millisecond part, read back). A lock PixelSwitch cannot prove it made is never removed.

A small ledger of the locks this process created (``held-locks.json`` in the state folder) lets
a restarted daemon remove ITS OWN leftover after a crash, and only that.
"""
from __future__ import annotations

import ctypes
import os
import random
import socket
import stat
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import fsio
from .logfile import CategoryLog


class LockBusy(Exception):
    """The lock is held by someone else and stayed so for the whole wait. ``age`` is the
    seconds since its directory was last touched (None when unknown)."""

    def __init__(self, path: str, age: float | None) -> None:
        super().__init__(f"{os.path.basename(path)} is held by another process")
        self.path = path
        self.age = age


class LockLost(Exception):
    """The lock was held longer than allowed, or its directory changed under us."""


# --- birth time (the owner record names it) ------------------------------------------------------

_STATX_BTIME = 0x800
_AT_FDCWD = -100
_AT_SYMLINK_NOFOLLOW = 0x100


def _birth_parts(path: str) -> tuple[int, int] | None:
    """The creation time of ``path`` as (whole seconds, nanoseconds), or None where the platform or
    filesystem keeps none. Linux: ``statx``; macOS: ``st_birthtime``."""
    try:
        status = os.lstat(path)
    except OSError:
        return None
    birth = getattr(status, "st_birthtime", None)
    if birth:
        whole = int(birth // 1)
        return whole, int(round((birth - whole) * 1e9))
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statx = libc.statx
        statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
        statx.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(256)
        if statx(_AT_FDCWD, os.fsencode(path), _AT_SYMLINK_NOFOLLOW, _STATX_BTIME, buffer) != 0:
            return None
        mask = struct.unpack_from("<I", buffer, 0)[0]
        if not mask & _STATX_BTIME:
            return None
        seconds, nanos = struct.unpack_from("<qI", buffer, 80)
        if seconds * 10**9 + nanos <= 0:
            return None
        return seconds, nanos
    except (OSError, AttributeError, struct.error):
        return None


def birth_time_ns(path: str) -> int | None:
    """The creation time of ``path`` in WHOLE nanoseconds (``seconds * 10**9 + nanos``), or None. Ownership
    of a lock is decided on this value compared with ``==`` and never with a tolerance: a directory removed
    and re-made within a millisecond of ours (ext4 reuses the inode number at once) has a different one."""
    parts = _birth_parts(path)
    return None if parts is None else parts[0] * 10**9 + parts[1]


def birth_time_ms(path: str) -> float | None:
    """The creation time of ``path`` in milliseconds as Node's ``birthtimeMs`` (a float), for the owner
    record Claude Code reads, or None where the platform or filesystem keeps none."""
    parts = _birth_parts(path)
    if parts is None:
        return None
    value = parts[0] * 1000.0 + parts[1] / 1e6
    return value if value > 0 else None


# --- the ledger of locks this daemon created --------------------------------------------------------


class LockLedger:
    """``held-locks.json``: ``{path: {"pid": n, "birthtimeMs": x, "birthtimeNs": y}}`` for every lock a PixelSwitch
    process created and has not yet released. Only a lock listed here, whose creator is no longer
    running, is ever removed by :meth:`reap` -- never one Claude Code made."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if self.path is None:
            return {}
        try:
            text = fsio.read_text(self.path, limit=1 << 20)
            value = fsio.loads_strict(text) if text is not None else {}
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, data: dict) -> None:
        if self.path is None:
            return
        try:
            if data:
                fsio.atomic_write(self.path, fsio.dumps_compact(data))
            elif os.path.lexists(self.path):
                os.unlink(self.path)
        except OSError:
            pass

    def add(self, lock_path: str, birth_ms: float | None, birth_ns: int | None = None) -> None:
        with self._lock:
            data = self._read()
            data[lock_path] = {"pid": os.getpid(), "birthtimeMs": birth_ms, "birthtimeNs": birth_ns}
            self._write(data)

    def remove(self, lock_path: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(lock_path, None) is not None:
                self._write(data)

    def reap(self, log: CategoryLog, stale: dict[str, float], now: float | None = None) -> int:
        """At daemon start: removes the locks a dead PixelSwitch process left behind. A lock is
        removed only if it is in the ledger, its recorded pid is not alive, its directory still
        has the recorded birth time, and it has not been touched for longer than its stale limit
        (``stale`` maps a lock path to that limit; unknown paths use 60 s). Returns the count."""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            data = self._read()
            for lock_path, entry in list(data.items()):
                keep = True
                try:
                    pid = int(entry.get("pid", 0))
                except (TypeError, ValueError):
                    pid = 0
                alive = pid > 0 and _pid_alive(pid)
                try:
                    status = os.lstat(lock_path)
                except FileNotFoundError:
                    keep = False
                except OSError:
                    status = None
                else:
                    # Only when both birth times are known and EXACTLY equal (whole nanoseconds where the
                    # entry has them): a freed inode is reused at once on ext4, so the inode alone cannot
                    # tell a lock we made from one made since, and a tolerance would let a lock re-made a
                    # millisecond later pass. Without a birth time the leftover is left to Claude Code's
                    # own 60 s staleness rule.
                    recorded_ns = entry.get("birthtimeNs")
                    recorded = entry.get("birthtimeMs")
                    if isinstance(recorded_ns, int) and not isinstance(recorded_ns, bool):
                        same = birth_time_ns(lock_path) == recorded_ns
                    else:
                        birth = birth_time_ms(lock_path)
                        same = (isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
                                and birth is not None and recorded == birth)
                    old = now - status.st_mtime > stale.get(lock_path, 60.0)
                    if not alive and same and old and stat.S_ISDIR(status.st_mode):
                        try:
                            os.rmdir(lock_path)
                            removed += 1
                            keep = False
                            log.warning(f"[lock] Removed {os.path.basename(lock_path)}, left behind by an earlier "
                                        "PixelSwitch process that is gone")
                        except OSError:
                            pass
                if not keep:
                    data.pop(lock_path, None)
            self._write(data)
        return removed


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# --- the lock -------------------------------------------------------------------------------------------


@dataclass
class _Held:
    inode: int
    #: The creation time in whole nanoseconds when the filesystem keeps a trustworthy one (not a whole
    #: second). None where there is none, and the mtime below is the proof.
    birth_ns: int | None
    #: The creation time as Node's float, for the owner record Claude Code reads.
    birth_ms: float | None
    since: float
    #: The directory's mtime in nanoseconds, read back after the last ``utime`` PixelSwitch did (the filesystem
    #: may keep less than we set): half of the proof of ownership, all of it where there is no birth time.
    #: None: unknown, and then nothing is provably ours.
    mtime_ns: int | None


_COARSE_LOGGED = False


def _note_coarse(log: "CategoryLog | None") -> None:
    """Logs once per process that this filesystem cannot keep the nanoseconds PixelSwitch sets."""
    global _COARSE_LOGGED
    if _COARSE_LOGGED:
        return
    _COARSE_LOGGED = True
    if log is not None:
        log.warning("[lock] This filesystem keeps file times coarser than a millisecond and has no creation time, "
                    "so a lock directory can only be told apart at that coarseness; PixelSwitch removes a lock "
                    "only while its inode and time still match what it set")


def _stamp(path: str, when: float, expect_inode: int | None = None) -> tuple[os.stat_result, int, bool] | None:
    """Sets ``path``'s mtime to ``when`` plus a random sub-millisecond nanosecond part and returns the status
    read back from the SAME directory, the nanoseconds asked for, and whether the filesystem kept them. The
    directory is opened first and its time set through the descriptor, so a lock that is replaced between the
    check and the write is never touched. None when the directory is gone, is not a directory, or (when
    ``expect_inode`` is given) is no longer the one with that inode number."""
    asked = int(max(0.0, when) * 1000.0) * 1_000_000 + random.randrange(1, 1_000_000)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except (FileNotFoundError, NotADirectoryError):
        return None
    try:
        if expect_inode is not None and os.fstat(fd).st_ino != expect_inode:
            return None
        if os.utime in os.supports_fd:
            os.utime(fd, ns=(asked, asked))
        else:
            os.utime(path, ns=(asked, asked))
        status = os.fstat(fd)
    finally:
        os.close(fd)
    return status, asked, status.st_mtime_ns == asked


class DirLock:
    """One lock directory. ``acquire`` waits (never forces); ``release`` removes it if it is still
    ours. While held, a background thread touches the directory every ``update`` seconds."""

    def __init__(
        self,
        path: str,
        *,
        stale: float,
        update: float | None,
        ledger: LockLedger | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        log: CategoryLog | None = None,
        max_hold: float | None = None,
    ) -> None:
        self.path = path
        self.stale = stale
        self.update = update
        self.max_hold = max_hold
        self._ledger = ledger
        self._clock = clock
        self._sleep = sleep
        self._log = log
        self._held: _Held | None = None
        #: Serialises the touch loop's write of ``held.mtime_ns`` with every ownership check.
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.compromised = False

    # -- state -------------------------------------------------------------------------------

    @property
    def held(self) -> bool:
        return self._held is not None

    @property
    def birth_ms(self) -> float | None:
        return self._held.birth_ms if self._held else None

    def age(self) -> float | None:
        """Seconds since the directory was last touched, None if there is none."""
        try:
            return max(0.0, self._clock() - os.lstat(self.path).st_mtime)
        except OSError:
            return None

    def remaining(self) -> float | None:
        """Seconds of ``max_hold`` left (never below 0), or None when this lock has no limit or is not held."""
        held = self._held
        if held is None or self.max_hold is None:
            return None
        return max(0.0, self.max_hold - (self._clock() - held.since))

    def _is_ours(self, status: os.stat_result, held: _Held) -> bool:
        """Whether the directory at our path is still the one we made, decided on EXACT values, never a
        tolerance. The inode alone is not enough: ext4 reuses a freed inode at once, so a lock directory
        Claude Code recreated after taking over a stale one can carry the same number. So:

        * the inode must be ours;
        * where the filesystem keeps a trustworthy creation time it must equal ours to the nanosecond
          (a tolerance of milliseconds passed a directory re-made a millisecond later, CI run 40162811377);
        * the directory's mtime must equal, to the nanosecond, the one we last set and read back. It carries
          a random sub-millisecond part nobody else could have chosen, which tells our directory from one
          re-made within the same kernel clock tick (older kernels stamp creation times with a coarse clock,
          so two directories made a millisecond apart can share a creation time) and is the only proof where
          the filesystem keeps no creation time. On a filesystem that cannot keep the nanoseconds we set, it
          is the mtime as that filesystem reads it back.

        Unknown means no: a lock that cannot be proved ours is never removed."""
        if status.st_ino != held.inode:
            return False
        if held.birth_ns is not None and birth_time_ns(self.path) != held.birth_ns:
            return False
        return held.mtime_ns is not None and status.st_mtime_ns == held.mtime_ns

    def check(self) -> None:
        """Raises :class:`LockLost` if the lock was held past ``max_hold`` or was taken from us."""
        held = self._held
        if held is None:
            raise LockLost("the lock is not held")
        if self.compromised:
            raise LockLost(f"{os.path.basename(self.path)} was taken over")
        if self.max_hold is not None and self._clock() - held.since > self.max_hold:
            raise LockLost(f"{os.path.basename(self.path)} was held longer than {self.max_hold:.0f} s")
        try:
            with self._guard:
                ours = self._is_ours(os.lstat(self.path), held)
            if not ours:
                self.compromised = True
                raise LockLost(f"{os.path.basename(self.path)} changed under us")
        except FileNotFoundError:
            self.compromised = True
            raise LockLost(f"{os.path.basename(self.path)} disappeared") from None

    # -- acquiring ---------------------------------------------------------------------------

    def try_acquire(self) -> bool:
        if self._held is not None:
            raise RuntimeError("the lock is already held by this object")
        try:
            os.mkdir(self.path, 0o700)
        except FileExistsError:
            return False
        now = self._clock()
        try:
            stamped = _stamp(self.path, now)
        except OSError:
            try:
                os.rmdir(self.path)
            except OSError:
                pass
            raise
        if stamped is None:
            return False   # removed by someone else the moment it was made: not ours, nothing to release
        status, _asked, exact = stamped
        birth_ns = birth_time_ns(self.path)
        birth = birth_time_ms(self.path)
        # A creation time that is a whole second is a filesystem that keeps no better: not a proof of anything.
        reliable_birth = birth_ns is not None and birth_ns % 10**9 != 0
        if not reliable_birth and not exact:
            _note_coarse(self._log)
        self._held = _Held(status.st_ino, birth_ns if reliable_birth else None, birth, now, status.st_mtime_ns)
        self.compromised = False
        if self._ledger is not None:
            self._ledger.add(self.path, birth, birth_ns)
        if self.update:
            self._stop.clear()
            self._thread = threading.Thread(target=self._touch_loop, name=f"lock-touch-{os.path.basename(self.path)}",
                                            daemon=True)
            self._thread.start()
        return True

    def acquire(self, timeout: float, poll: tuple[float, float] = (0.2, 0.5)) -> None:
        """Takes the lock, waiting up to ``timeout`` seconds for another holder. Raises
        :class:`LockBusy`. A stale lock is waited for like any other: PixelSwitch does not break it."""
        deadline = self._clock() + timeout
        while True:
            if self.try_acquire():
                return
            if self._clock() >= deadline:
                raise LockBusy(self.path, self.age())
            low, high = poll
            self._sleep(min(random.uniform(low, high), max(0.01, deadline - self._clock())))

    def release(self) -> None:
        held, self._held = self._held, None
        if held is None:
            return
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            with self._guard:
                ours = self._is_ours(os.lstat(self.path), held)
                if ours:
                    os.rmdir(self.path)
            if not ours and self._log is not None:
                self._log.warning(f"[lock] {os.path.basename(self.path)} was replaced while held; leaving it alone")
        except FileNotFoundError:
            pass
        except OSError as error:
            if self._log is not None:
                self._log.warning(f"[lock] Could not remove {os.path.basename(self.path)} ({type(error).__name__})")
        if self._ledger is not None:
            self._ledger.remove(self.path)

    def _touch_loop(self) -> None:
        assert self.update
        while not self._stop.wait(self.update):
            held = self._held
            if held is None:
                return
            try:
                with self._guard:
                    if not self._is_ours(os.lstat(self.path), held):
                        self.compromised = True
                        return
                    stamped = _stamp(self.path, self._clock(), held.inode)
                    if stamped is None:
                        self.compromised = True
                        return
                    held.mtime_ns = stamped[0].st_mtime_ns
            except OSError:
                self.compromised = True
                return

    def __enter__(self) -> "DirLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# --- the holder record Claude Code reads ------------------------------------------------------------------

OWNER_NAME = ".oauth_refresh.lock.owner"


def _read_first(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(256).strip()
    except OSError:
        return ""


def pid_namespace() -> str:
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return ""


def proc_start(pid: int | None = None) -> str | None:
    """Field 22 of ``/proc/<pid>/stat`` (the start time in ticks) as text, as Claude Code records it."""
    try:
        with open(f"/proc/{pid or os.getpid()}/stat", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        fields = text[text.rindex(")") + 2:].split(" ")
        return fields[19]
    except (OSError, ValueError, IndexError):
        return None


def owner_record(lock_birth_ms: float, legacy_birth_ms: float | None = None) -> dict:
    """The JSON Claude Code 2.1.310 writes beside its refresh lock (and reads to decide whether the
    holder is gone): ``pid``, ``procStart`` (optional), ``pidDomain``, ``pidSpace``,
    ``lockBirthtimeMs`` and ``legacyLockBirthtimeMs`` (optional). Written only when the lock's
    birth time is known; a record that does not describe a live process is never read as a reason
    to take the lock over, because the pid is ours and alive."""
    namespace = pid_namespace()
    machine_id = _read_first("/etc/machine-id")
    record: dict = {
        "pid": os.getpid(),
        "pidDomain": f"linux:{machine_id}:{namespace}",
        "pidSpace": f"{socket.gethostname()}{'#' + namespace if namespace else ''}",
        "lockBirthtimeMs": lock_birth_ms,
    }
    start = proc_start()
    if start is not None:
        record["procStart"] = start
    if legacy_birth_ms is not None and legacy_birth_ms > 0:
        record["legacyLockBirthtimeMs"] = legacy_birth_ms
    return record
