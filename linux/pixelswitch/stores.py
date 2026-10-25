"""PixelSwitch's own stores: ``settings.json``, ``accounts.json``, ``backups.json`` (saved
logins) and the needs-reauth ids. Ports the role of ``UserDefaults`` (settings, accounts,
needs-reauth) and ``KeychainService``'s backup store (``KeychainService.swift:213-300``).

``BackupStore`` is the ONE place a login is saved, so spec D12 is enforced inside its single
save function and every path (capture, reconcile, the switch snapshot, sign-in, add-current,
renewal) inherits it: a credential that is not a usable login is never saved. It also keeps the
Mac's three-way load (``loaded`` / ``empty`` / ``failed``): a save or a removal REFUSES when the
store could not be read, because writing a mutated copy back would replace every account's
login with just one.

Every write is atomic (``fsio.atomic_write``): a temp file in the same directory, ``fsync``,
``os.replace``, directory ``fsync``, permissions never widened (0600 when new).
"""
from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import fsio
from .accounts import Account, AccountsFormatError, dump_accounts, load_accounts
from .logfile import CategoryLog
from .logins import login_problem


# --- settings.json -------------------------------------------------------------------------------


class SettingsFile:
    """``settings.json``: one JSON object keyed by the store keys of ``settings_catalog``.
    A file that cannot be read as an object is moved aside (``.unreadable-<epoch>``, 0600) and
    the defaults apply; nothing is ever deleted."""

    def __init__(self, path: str, log: CategoryLog, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._log = log
        self._clock = clock
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            text = fsio.read_text(self.path)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            self._log.error(f"[settings] Could not read settings.json ({type(error).__name__}); using the defaults")
            return {}
        if text is None:
            return {}
        try:
            value = fsio.loads_strict(text)
        except (ValueError, RecursionError):
            value = None
        if not isinstance(value, dict):
            self._set_aside("not a JSON object")
            return {}
        return value

    def _set_aside(self, why: str) -> None:
        target = f"{self.path}.unreadable-{int(self._clock())}"
        try:
            os.replace(self.path, target)
            self._log.error(f"[settings] settings.json is {why}; moved it aside and using the defaults")
        except OSError:
            self._log.error(f"[settings] settings.json is {why}; using the defaults")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def replace(self, data: dict[str, Any]) -> None:
        """Stores ``data`` (the whole object) and writes it. On a write failure the in-memory
        copy is left as it was and the error propagates."""
        with self._lock:
            fsio.atomic_write(self.path, fsio.dumps_pretty(data))
            self._data = copy.deepcopy(data)

    def update(self, change: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """One read-modify-write under the file's lock: ``change`` gets a copy of the stored object and
        returns the whole object to store. Two writers (the tray and the settings window, each on its
        own connection) can no longer both read the same snapshot and lose one change. On a write
        failure the in-memory copy is left as it was and the error propagates."""
        with self._lock:
            data = change(copy.deepcopy(self._data))
            fsio.atomic_write(self.path, fsio.dumps_pretty(data))
            self._data = copy.deepcopy(data)
            return copy.deepcopy(data)


# --- accounts.json ----------------------------------------------------------------------------------


class AccountsFile:
    """``accounts.json``: a list of account records, kept sorted by priority on load and after
    every change (the caller sorts; :meth:`load` sorts again). A damaged file is moved aside,
    never half-read and then overwritten."""

    def __init__(self, path: str, log: CategoryLog, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._log = log
        self._clock = clock
        self._lock = threading.RLock()

    def load(self) -> list[Account]:
        with self._lock:
            try:
                text = fsio.read_text(self.path)
            except (OSError, UnicodeDecodeError, ValueError) as error:
                self._log.error(f"[accounts] Could not read accounts.json ({type(error).__name__}); starting with none")
                return []
            if text is None:
                return []
            try:
                return load_accounts(fsio.loads_strict(text))
            except (AccountsFormatError, ValueError, RecursionError):
                target = f"{self.path}.unreadable-{int(self._clock())}"
                try:
                    os.replace(self.path, target)
                except OSError:
                    pass
                self._log.error("[accounts] accounts.json is damaged; moved it aside and starting with none")
                return []

    def save(self, accounts: Iterable[Account]) -> None:
        with self._lock:
            fsio.atomic_write(self.path, fsio.dumps_pretty(dump_accounts(accounts)))


# --- needs-reauth ids ---------------------------------------------------------------------------------


class NeedsReauthFile:
    """Account ids whose refresh grant the server refused (ids only, never a token), sorted.
    Written only when the set changed (``AppState.persistNeedsReauth``)."""

    def __init__(self, path: str, log: CategoryLog) -> None:
        self.path = path
        self._log = log
        self._last: set[str] | None = None

    def load(self) -> set[str]:
        try:
            text = fsio.read_text(self.path, limit=1 << 20)
            value = fsio.loads_strict(text) if text is not None else []
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            self._log.warning("[needsReauth] Could not read the needs-reauth file; assuming none")
            value = []
        ids = {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()
        self._last = set(ids)
        return ids

    def save(self, ids: set[str]) -> None:
        if self._last is not None and ids == self._last:
            return
        fsio.atomic_write(self.path, fsio.dumps_pretty(sorted(ids)))
        self._last = set(ids)


# --- removed-logins.json ------------------------------------------------------------------------------


class RemovedLoginsFile:
    """Fingerprints (never tokens) of the refresh tokens of the logins of accounts the user removed
    that are PROVABLY the removed account's (``removal.fingerprints_to_record``), so a switch made with
    no active account can recognise the live login as a removed account's and so disposable (spec D20
    revision 4). The Mac keeps them in ``UserDefaults``; here they are a 0600 file in the state folder,
    at most 16, forgotten when an account is added, signed in, adopted as the active one, or switched to."""

    def __init__(self, path: str, log: CategoryLog) -> None:
        self.path = path
        self._log = log
        self._lock = threading.Lock()

    def load(self) -> list[str]:
        with self._lock:
            return self._load()

    def _load(self) -> list[str]:
        try:
            text = fsio.read_text(self.path, limit=1 << 20)
            value = fsio.loads_strict(text) if text is not None else []
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            self._log.warning("[removedLogins] Could not read the removed-logins file; assuming none")
            return []
        return [item for item in value if isinstance(item, str)][-16:] if isinstance(value, list) else []

    def save(self, fingerprints: Iterable[str]) -> None:
        with self._lock:
            items = list(fingerprints)[-16:]
            if items:
                fsio.atomic_write(self.path, fsio.dumps_pretty(items))
            elif os.path.lexists(self.path):
                os.unlink(self.path)

    def forget(self) -> None:
        """Removes the record, if there is one."""
        with self._lock:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            except OSError as error:
                self._log.warning(f"[removedLogins] Could not clear the removed-logins file ({type(error).__name__})")


# --- backups.json -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountBackup:
    """``AccountBackup`` (KeychainService.swift:7-10): the WHOLE credential JSON string and the
    ``oauthAccount`` object from ``~/.claude.json``."""

    token: str
    oauth_account: dict


@dataclass(frozen=True)
class StoreLoad:
    """``loaded`` (with ``backups``), ``empty`` (the file does not exist) or ``failed`` (it MAY
    exist but could not be read or understood: never write after this)."""

    kind: str
    backups: dict[str, AccountBackup]
    reason: str = ""


class BackupStore:
    """The saved logins, ``{uuid: {"token": <credential JSON string>, "oauthAccount": {...}}}``."""

    FOUND, MISSING, UNAVAILABLE = "found", "missing", "storeUnavailable"

    def __init__(self, path: str, log: CategoryLog) -> None:
        self.path = path
        self._log = log
        self._lock = threading.RLock()

    # -- reading ----------------------------------------------------------------------------

    def _load(self) -> StoreLoad:
        try:
            data = fsio.read_bytes(self.path)
        except (OSError, ValueError) as error:
            return StoreLoad("failed", {}, f"the file could not be read ({type(error).__name__})")
        if data is None:
            return StoreLoad("empty", {})
        try:
            value = fsio.loads_strict(data.decode("utf-8"))
        except (ValueError, RecursionError, UnicodeDecodeError):
            return StoreLoad("failed", {}, "the file is not valid JSON")
        if not isinstance(value, dict):
            return StoreLoad("failed", {}, "the file is not a JSON object")
        backups: dict[str, AccountBackup] = {}
        for key, entry in value.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("token"), str) \
                    or not isinstance(entry.get("oauthAccount"), dict):
                return StoreLoad("failed", {}, "an entry is not in the expected shape")
            backups[key] = AccountBackup(entry["token"], entry["oauthAccount"])
        return StoreLoad("loaded", backups)

    def _write(self, backups: dict[str, AccountBackup]) -> bool:
        payload = {key: {"token": b.token, "oauthAccount": b.oauth_account} for key, b in sorted(backups.items())}
        try:
            fsio.atomic_write(self.path, fsio.dumps_compact(payload))
            return True
        except (OSError, ValueError, TypeError) as error:
            self._log.error(f"[saveBackup] Write failed ({type(error).__name__})")
            return False

    def lookup(self, account_id: str) -> tuple[str, AccountBackup | None]:
        """``lookupAccountBackup``: (``found``, backup), (``missing``, None) or
        (``storeUnavailable``, None). 'Does not exist' and 'could not be read right now' are
        different answers with different remedies."""
        with self._lock:
            load = self._load()
            if load.kind == "failed":
                self._log.error(f"[getBackup] Store unreadable ({load.reason}); {account_id} unavailable until it recovers")
                return self.UNAVAILABLE, None
            backup = load.backups.get(account_id)
            if backup is None:
                self._log.error(f"[getBackup] No backup for accountId={account_id}")
                return self.MISSING, None
            return self.FOUND, backup

    def get(self, account_id: str) -> AccountBackup | None:
        """Where 'missing' and 'unavailable' demand the same behaviour."""
        kind, backup = self.lookup(account_id)
        return backup if kind == self.FOUND else None

    def all(self) -> dict[str, AccountBackup] | None:
        """Every backup in one read, or None when the store is unreadable right now (so callers
        never mistake 'unreadable' for 'no backups')."""
        with self._lock:
            load = self._load()
            if load.kind == "failed":
                self._log.error(f"[allBackups] Store unreadable ({load.reason})")
                return None
            return dict(load.backups)

    def is_idle(self) -> bool:
        """False while a read or write of the store is in progress (the updater restarts the daemon
        only when no store write is running)."""
        if self._lock.acquire(blocking=False):
            self._lock.release()
            return True
        return False

    def readable(self) -> tuple[bool, str]:
        """For ``--self-check``: whether the store can be read, with the reason when it cannot."""
        with self._lock:
            load = self._load()
            return load.kind != "failed", load.reason

    # -- writing ----------------------------------------------------------------------------

    def save(self, token: str, oauth_account: dict, account_id: str) -> bool:
        """The ONE function every path saves a login through. Refuses a credential that is not a
        usable login (spec D12) and refuses when the store is unreadable. Returns whether it saved."""
        problem = login_problem(token)
        if problem is not None:
            self._log.error(f"[saveBackup] REFUSED for {account_id}: not a usable login ({problem}); "
                            "the saved login is left as it is")
            return False
        if not isinstance(oauth_account, dict):
            self._log.error(f"[saveBackup] REFUSED for {account_id}: the identity details are not an object")
            return False
        with self._lock:
            load = self._load()
            if load.kind == "failed":
                self._log.error(f"[saveBackup] REFUSED: store unreadable ({load.reason}); writing now would "
                                "destroy the other accounts' backups")
                return False
            backups = dict(load.backups)
            backups[account_id] = AccountBackup(token, copy.deepcopy(oauth_account))
            saved = self._write(backups)
            self._log.info(f"[saveBackup] Saved for {account_id}: {saved}")
            return saved

    def remove(self, account_id: str) -> bool:
        with self._lock:
            load = self._load()
            if load.kind == "empty":
                return True
            if load.kind == "failed":
                self._log.error(f"[removeBackup] REFUSED: store unreadable ({load.reason}); writing now would "
                                "destroy the other accounts' backups")
                return False
            if account_id not in load.backups:
                return True
            backups = dict(load.backups)
            del backups[account_id]
            return self._write(backups)
