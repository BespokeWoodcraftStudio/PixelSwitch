"""Claude Code's login on Linux: ``~/.claude/.credentials.json`` (``claudeAiOauth`` and any other
top key such as ``mcpOAuth``) and ``~/.claude.json`` (``oauthAccount``, the identity).

Ports the Linux half of ``KeychainService.swift`` (``readClaudeToken`` / ``writeClaudeToken`` /
``readOAuthAccount`` / ``writeOAuthAccount``, lines 112-209) and ``ClaudeCredentialsFile.swift``.
On the Mac the live login is a Keychain item; here the file IS the store (``credentials.md`` 7.1).
``CLAUDE_CONFIG_DIR`` and ``CLAUDE_SECURESTORAGE_CONFIG_DIR`` are not honoured, exactly as on the
Mac (``ClaudeCredentialsFile.swift:22-27``).

Writes, per spec 5.2: an atomic temp-file write in the target's directory (0600, ``fsync``,
``os.replace``, directory ``fsync``); a replaced file keeps its permissions and is never made
wider; a symlinked or hard-linked target is refused; the whole of ``~/.claude.json`` is rewritten
with every other key and its permissions preserved. After a credential write the file is read
back and compared (the Mac's write-then-read-back contract, ``ClaudeTokenWriter.swift``).

Locks (confirmed against Claude Code 2.1.310, see ``dirlock.py`` and ``linux/README.md``):
:meth:`refresh_lock` is the lock around a whole switch; :meth:`write_credential` takes
Claude Code's ``.storage-write`` lock around the file write and :meth:`write_oauth_account` its
``~/.claude.json.lock`` around the identity write. A busy lock is waited for and never broken.

How a running Claude Code notices the rewrite (confirmed in the same binary): on Linux it
``stat``s ``~/.claude/.credentials.json`` and compares ``mtimeMs`` with the value it saw last; a
different value clears its credential cache and re-reads the file. An atomic rename gives the
new file a new mtime, and :meth:`write_credential` guarantees it differs from the old one.
"""
from __future__ import annotations

import contextlib
import copy
import os
import time
from dataclasses import dataclass
from typing import Callable, Iterator

from . import fsio
from .dirlock import OWNER_NAME, DirLock, LockBusy, LockLedger, LockLost, owner_record
from .logfile import CategoryLog
from .paths import Paths

#: How long a switch waits for Claude Code to finish renewing (its own refresh timeout is 30 s).
REFRESH_WAIT_SECONDS = 35.0
#: How long PixelSwitch holds the refresh lock at most.
REFRESH_HOLD_SECONDS = 10.0
#: How long the two short write locks are waited for.
WRITE_LOCK_WAIT_SECONDS = 10.0

RENEWING_MESSAGE = "Claude Code is renewing this login; try again in a minute."
LEFTOVER_MESSAGE_LEGACY = ("A lock left by a Claude Code process that is gone is blocking this. It clears the next time "
                           "Claude Code renews its login; you can also remove {path}.")
LEFTOVER_MESSAGE = ("A lock left in ~/.claude by a Claude Code process that is gone is blocking this. It clears "
                    "the next time Claude Code renews its login; you can also remove ~/.claude/.oauth_refresh.lock.")


class LoginWriteError(Exception):
    """Writing Claude Code's login files failed; nothing is claimed to be written. The text names
    no token."""


class LiveLoginBusy(Exception):
    """Claude Code holds one of its locks. ``message`` is for the person."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class HeldRefresh:
    """The refresh lock(s) while held; ``check()`` before the write enforces the 10 s limit."""

    new: DirLock
    legacy: DirLock | None

    def check(self) -> None:
        self.new.check()
        if self.legacy is not None:
            self.legacy.check()

    def remaining(self) -> float:
        """Seconds of the hold limit left (spec 5.3: hold it at most 10 s). Every wait taken while
        this lock is held (the ``.storage-write`` lock, ``~/.claude.json.lock``, the rollback) is
        capped by it, so the hold cannot run to the sum of those waits: Claude Code gives up on a
        lock held longer than a few seconds."""
        left = [lock.remaining() for lock in (self.new, self.legacy) if lock is not None]
        known = [value for value in left if value is not None]
        return min(known) if known else REFRESH_HOLD_SECONDS


class ClaudeLogin:
    def __init__(
        self,
        paths: Paths,
        log: CategoryLog,
        *,
        ledger: LockLedger | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.paths = paths
        self._log = log
        self._ledger = ledger
        self._clock = clock
        self._sleep = sleep

    # -- lock paths ---------------------------------------------------------------------------

    @property
    def refresh_lock_path(self) -> str:
        return os.path.join(self.paths.claude_dir, ".oauth_refresh.lock")

    @property
    def legacy_lock_path(self) -> str:
        """``<realpath of ~/.claude>.lock``, the lock an older Claude Code takes instead."""
        return os.path.realpath(self.paths.claude_dir) + ".lock"

    @property
    def storage_lock_path(self) -> str:
        return os.path.join(self.paths.claude_dir, ".storage-write")

    @property
    def config_lock_path(self) -> str:
        return self.paths.claude_json + ".lock"

    def lock_stale_limits(self) -> dict[str, float]:
        return {self.refresh_lock_path: 60.0, self.legacy_lock_path: 60.0, self.storage_lock_path: 15.0,
                self.config_lock_path: 10.0}

    def _lock(self, path: str, stale: float, update: float | None, max_hold: float | None = None) -> DirLock:
        return DirLock(path, stale=stale, update=update, ledger=self._ledger, clock=self._clock,
                       sleep=self._sleep, log=self._log, max_hold=max_hold)

    # -- reading ------------------------------------------------------------------------------

    def read_credential(self) -> str | None:
        """The whole credential JSON text (trimmed), or None when the file is absent, unreadable
        or empty. The Mac's ``readClaudeToken``."""
        try:
            text = fsio.read_text(self.paths.credentials, limit=4 * 1024 * 1024)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            self._log.error(f"[readCredential] Could not read the credentials file ({type(error).__name__})")
            return None
        if text is None:
            return None
        text = text.strip()
        return text or None

    def credential_mtime_ns(self) -> int | None:
        try:
            return os.stat(self.paths.credentials).st_mtime_ns
        except OSError:
            return None

    def read_oauth_account(self) -> dict | None:
        """``oauthAccount`` from ``~/.claude.json`` (a deep copy), or None."""
        try:
            text = fsio.read_text(self.paths.claude_json, limit=64 * 1024 * 1024)
            value = fsio.loads_strict(text) if text is not None else None
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
            self._log.error(f"[readOAuthAccount] Failed to read ~/.claude.json ({type(error).__name__})")
            return None
        entry = value.get("oauthAccount") if isinstance(value, dict) else None
        if not isinstance(entry, dict):
            self._log.error("[readOAuthAccount] Failed to read oauthAccount from ~/.claude.json")
            return None
        return copy.deepcopy(entry)

    # -- the refresh lock ------------------------------------------------------------------------

    @contextlib.contextmanager
    def refresh_lock(self, timeout: float = REFRESH_WAIT_SECONDS, max_hold: float = REFRESH_HOLD_SECONDS
                     ) -> Iterator[HeldRefresh]:
        """Claude Code's refresh lock (and the legacy one), taken the way Claude Code takes them:
        the new lock first, then the legacy lock (if that one is busy the new lock is let go and
        both are tried again). Waits up to ``timeout`` seconds, then raises :class:`LiveLoginBusy`
        with nothing written. Hold time is bounded by ``max_hold`` (``HeldRefresh.check``)."""
        try:
            fsio.ensure_private_dir(self.paths.claude_dir)
        except OSError as error:
            raise LiveLoginBusy("PixelSwitch could not prepare ~/.claude.") from error
        deadline = self._clock() + timeout
        new = self._lock(self.refresh_lock_path, 60.0, 5.0, max_hold)
        legacy: DirLock | None = None
        try:
            while True:
                try:
                    new.acquire(max(0.0, deadline - self._clock()), poll=(0.5, 1.0))
                except LockBusy as busy:
                    raise LiveLoginBusy(self._busy_message(busy.age, self.refresh_lock_path)) from None
                except OSError as error:
                    raise LiveLoginBusy(f"PixelSwitch could not take Claude Code's refresh lock in ~/.claude "
                                        f"({error.strerror or type(error).__name__}). Nothing was changed.") from None
                candidate = self._lock(self.legacy_lock_path, 60.0, 5.0, max_hold)
                try:
                    got = candidate.try_acquire()
                except OSError as error:
                    # Claude Code ignores a legacy lock it cannot make (a read-only home, say).
                    self._log.warning(f"[lock] Legacy refresh lock unavailable ({type(error).__name__}); going without it")
                    got = None
                if got is None:
                    break
                if got:
                    legacy = candidate
                    break
                new.release()  # the legacy lock is busy: let go of the new one and try both again
                if self._clock() >= deadline:
                    raise LiveLoginBusy(self._busy_message(candidate.age(), candidate.path))
                self._sleep(0.5)
            self._write_owner(new, legacy)
            try:
                yield HeldRefresh(new, legacy)
            finally:
                self._remove_owner(new)
        finally:
            if legacy is not None:
                legacy.release()
            new.release()

    def _busy_message(self, age: float | None, lock_path: str | None = None) -> str:
        """The leftover message names the lock that is actually stuck: ``~/.claude/.oauth_refresh.lock``
        for the new lock, ``~/.claude.lock`` for the legacy one."""
        if age is None or age <= 60.0:
            return RENEWING_MESSAGE
        if lock_path is None or lock_path == self.refresh_lock_path:
            return LEFTOVER_MESSAGE
        return LEFTOVER_MESSAGE_LEGACY.format(path=self._display(lock_path))

    def _display(self, path: str) -> str:
        home = self.paths.home.rstrip("/")
        return "~" + path[len(home):] if path.startswith(home + "/") else path

    def _owner_path(self) -> str:
        return os.path.join(self.paths.claude_dir, OWNER_NAME)

    def _write_owner(self, new: DirLock, legacy: DirLock | None) -> None:
        birth = new.birth_ms
        if birth is None or birth <= 0:
            return  # no birth time on this filesystem: no record (Claude Code then relies on staleness)
        try:
            record = owner_record(birth, legacy.birth_ms if legacy is not None else None)
            fsio.atomic_write(self._owner_path(), fsio.dumps_compact(record))
        except (OSError, ValueError, TypeError) as error:
            self._log.warning(f"[lock] Owner record not written ({type(error).__name__})")

    def _remove_owner(self, new: DirLock) -> None:
        """Removes the owner record only if it is the one we wrote (our pid and this lock's birth)."""
        path = self._owner_path()
        try:
            text = fsio.read_text(path, limit=4096)
            record = fsio.loads_strict(text) if text is not None else None
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            return
        if not isinstance(record, dict) or record.get("pid") != os.getpid():
            return
        birth = new.birth_ms
        if birth is not None and record.get("lockBirthtimeMs") not in (birth, None):
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    # -- writing -------------------------------------------------------------------------------------

    @contextlib.contextmanager
    def storage_write_lock(self, budget: float | None = None) -> Iterator[DirLock]:
        """Claude Code's ``.storage-write`` lock (stale 15 s), the one it takes around its own
        read-modify-write of ``.credentials.json``. A switch holds it from the re-read of the live
        login through the write and the read-back, so a save Claude Code finishes while the switch
        waits is read, not overwritten (spec 5.3). The wait is ``WRITE_LOCK_WAIT_SECONDS`` at most and
        never longer than ``budget`` (what is left of the refresh lock's hold limit). A busy lock is
        waited for and never broken: :class:`LoginWriteError` when it stays busy."""
        try:
            fsio.ensure_private_dir(self.paths.claude_dir)
        except OSError as error:
            raise LoginWriteError("PixelSwitch could not prepare ~/.claude.") from error
        wait = WRITE_LOCK_WAIT_SECONDS if budget is None else max(0.0, min(WRITE_LOCK_WAIT_SECONDS, budget))
        lock = self._lock(self.storage_lock_path, 15.0, 7.5)
        try:
            lock.acquire(wait)
        except LockBusy:
            raise LoginWriteError("Claude Code is saving its login; try again in a moment.") from None
        except OSError as error:
            raise LoginWriteError(f"Could not take the credentials lock ({type(error).__name__}).") from error
        try:
            yield lock
        finally:
            lock.release()

    def write_credential(self, text: str, held: DirLock | None = None) -> None:
        """Replaces ``~/.claude/.credentials.json`` with ``text`` (the whole credential JSON).
        Takes Claude Code's ``.storage-write`` lock (unless the caller already holds it and passes
        it as ``held``), writes atomically, makes sure the file's mtime differs from the old one,
        and reads the file back. Raises :class:`LoginWriteError`."""
        if not isinstance(text, str) or not text:
            raise LoginWriteError("There is no credential to write.")
        if held is None:
            with self.storage_write_lock() as lock:
                self.write_credential(text, lock)
            return
        if not held.held:
            raise LoginWriteError("The credentials lock is not held.")
        try:
            before = fsio.atomic_write(self.paths.credentials, text)
        except fsio.FileSafetyError as error:
            raise LoginWriteError(f"PixelSwitch will not replace this file: {error.strerror}.") from None
        except OSError as error:
            raise LoginWriteError(f"Failed to write the credentials file ({type(error).__name__}).") from error
        self._make_mtime_differ(before.mtime_ns)
        try:
            back = fsio.read_bytes(self.paths.credentials, limit=4 * 1024 * 1024)
        except (OSError, ValueError):
            back = None
        if back != text.encode("utf-8"):
            raise LoginWriteError("The credentials file did not read back as written.")

    def _make_mtime_differ(self, previous_ns: int | None) -> None:
        """A running Claude Code compares the file's mtime with the one it saw last: the new file's
        must differ. It does unless two writes land in the same clock tick, so this moves it on."""
        if previous_ns is None:
            return
        try:
            current = os.stat(self.paths.credentials).st_mtime_ns
            if current == previous_ns:
                bumped = previous_ns + 1_000_000
                os.utime(self.paths.credentials, ns=(bumped, bumped))
        except OSError:
            pass

    def write_oauth_account(self, oauth_account: dict, budget: float | None = None) -> bool:
        """Replaces ``oauthAccount`` in ``~/.claude.json``: the whole file is rewritten atomically
        with every other key (and its permissions) preserved, under Claude Code's own config lock.
        False when the file cannot be read as a JSON object, the lock stays busy or the write fails.
        The wait for the lock is ``WRITE_LOCK_WAIT_SECONDS`` at most and never longer than ``budget``
        (what is left of the refresh lock's hold limit, when the caller holds it)."""
        if not isinstance(oauth_account, dict):
            return False
        lock = self._lock(self.config_lock_path, 10.0, 5.0)
        try:
            lock.acquire(WRITE_LOCK_WAIT_SECONDS if budget is None else max(0.0, min(WRITE_LOCK_WAIT_SECONDS, budget)))
        except LockBusy:
            self._log.error("[writeOAuthAccount] ~/.claude.json is locked by Claude Code; not written")
            return False
        except OSError as error:
            self._log.error(f"[writeOAuthAccount] Could not take the config lock ({type(error).__name__})")
            return False
        try:
            try:
                text = fsio.read_text(self.paths.claude_json, limit=64 * 1024 * 1024)
                value = fsio.loads_strict(text) if text is not None else None
            except (OSError, UnicodeDecodeError, ValueError, RecursionError):
                value = None
            if not isinstance(value, dict):
                self._log.error("[writeOAuthAccount] Failed to read ~/.claude.json")
                return False
            value["oauthAccount"] = copy.deepcopy(oauth_account)
            try:
                fsio.atomic_write(self.paths.claude_json, fsio.dumps_indented_no_newline(value))
            except (OSError, ValueError, TypeError) as error:
                self._log.error(f"[writeOAuthAccount] Failed ({type(error).__name__})")
                return False
            return True
        finally:
            lock.release()


__all__ = [
    "ClaudeLogin", "HeldRefresh", "LiveLoginBusy", "LockLost", "LoginWriteError", "RENEWING_MESSAGE",
    "REFRESH_HOLD_SECONDS", "REFRESH_WAIT_SECONDS",
]
