"""Every file PixelSwitch writes goes through here (spec 5.2).

``atomic_write`` is the one write path for ``.credentials.json``, ``~/.claude.json``,
``backups.json``, ``settings.json``, ``accounts.json`` and the state files:

* a temp file in the target's own directory, opened ``O_CREAT|O_EXCL|O_NOFOLLOW`` at 0600;
* written, ``fsync``'d, then ``os.replace``'d over the target, then the directory ``fsync``'d;
* a replaced file keeps its permissions (never wider than it had; 0600 when new) and its owner;
* a symlinked or hard-linked target is refused, and so is anything that is not a regular file;
* directories PixelSwitch creates are 0700 (existing ones are never touched).

Nothing in an error message carries file content.
"""
from __future__ import annotations

import errno
import json
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class FileSafetyError(OSError):
    """A write was refused because the target is not a plain file we may replace."""


def ensure_private_dir(path: str) -> None:
    """Creates ``path`` and any missing parents, each new directory 0700 whatever the umask.
    Directories that already exist are left exactly as they are."""
    path = os.path.abspath(path)
    missing: list[str] = []
    current = path
    while current and not os.path.lexists(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory, PRIVATE_DIR_MODE)
        except FileExistsError:
            continue
        os.chmod(directory, PRIVATE_DIR_MODE)  # mkdir's mode is masked by the umask
    if not os.path.isdir(path):
        raise NotADirectoryError(errno.ENOTDIR, "not a directory", path)


def fsync_dir(directory: str) -> None:
    """``fsync`` of a directory, so a rename inside it survives a crash. Best effort where the
    platform or filesystem refuses (the data is already safe in the file itself)."""
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class TargetState:
    """What the target is before a write. ``exists`` False means it will be created."""

    exists: bool
    mode: int = PRIVATE_FILE_MODE
    uid: int | None = None
    gid: int | None = None
    mtime_ns: int | None = None


def inspect_target(path: str) -> TargetState:
    """Refuses a symlink, a hard link (``st_nlink > 1``) and anything that is not a regular file."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return TargetState(False)
    if stat.S_ISLNK(status.st_mode):
        raise FileSafetyError(errno.ELOOP, "refusing to replace a symbolic link", path)
    if not stat.S_ISREG(status.st_mode):
        raise FileSafetyError(errno.EINVAL, "refusing to replace something that is not a regular file", path)
    if status.st_nlink > 1:
        raise FileSafetyError(errno.EMLINK, "refusing to replace a hard-linked file", path)
    return TargetState(True, stat.S_IMODE(status.st_mode), status.st_uid, status.st_gid, status.st_mtime_ns)


def atomic_write(
    path: str,
    data: "bytes | str",
    *,
    default_mode: int = PRIVATE_FILE_MODE,
    make_parent: bool = True,
    before_replace: Callable[[str], None] | None = None,
) -> TargetState:
    """Writes ``data`` to ``path`` atomically (see the module docstring). Returns the target's
    state BEFORE the write. Raises :class:`FileSafetyError` for a refused target and ``OSError``
    for anything the filesystem refuses; the temp file never survives a failure."""
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    directory = os.path.dirname(os.path.abspath(path))
    if make_parent:
        ensure_private_dir(directory)
    before = inspect_target(path)
    mode = before.mode if before.exists else default_mode
    if before.exists and before.uid is not None and before.uid != os.geteuid():
        # os.replace gives the file to us; keeping its owner is only possible as root.
        if os.geteuid() != 0:
            raise FileSafetyError(errno.EPERM, "refusing to replace a file owned by someone else", path)

    base = os.path.basename(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    temp = ""
    fd = -1
    for _ in range(8):
        candidate = os.path.join(directory, f".{base}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        try:
            fd = os.open(candidate, flags, PRIVATE_FILE_MODE)
        except FileExistsError:
            continue
        temp = candidate
        break
    if fd < 0:
        raise FileExistsError(errno.EEXIST, "could not create a temporary file", directory)
    try:
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fchmod(fd, mode)
            if before.exists and before.uid is not None and before.uid != os.geteuid():
                os.fchown(fd, before.uid, before.gid if before.gid is not None else -1)  # root only
            elif before.exists and before.gid is not None:
                try:
                    os.fchown(fd, -1, before.gid)  # keep the group when we may; otherwise leave ours
                except OSError:
                    pass
            os.fsync(fd)
        finally:
            os.close(fd)
        if before_replace is not None:
            before_replace(temp)
        os.replace(temp, path)
        temp = ""
        fsync_dir(directory)
    finally:
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass
    return before


def read_bytes(path: str, limit: int = 8 * 1024 * 1024) -> bytes | None:
    """The file's bytes, or None when it is absent. Raises ``OSError`` for anything else and
    ``ValueError`` for a file over ``limit``. Follows a symlink (reading is harmless)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", path)
        if status.st_size > limit:
            raise ValueError("file is too large")
        chunks = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(1 << 20, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise ValueError("file is too large")
        return data
    finally:
        os.close(fd)


def read_text(path: str, limit: int = 8 * 1024 * 1024) -> str | None:
    """The file as UTF-8 text, None when absent. Raises ``UnicodeDecodeError`` for bad text."""
    data = read_bytes(path, limit)
    return None if data is None else data.decode("utf-8")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def loads_strict(text: "str | bytes") -> Any:
    """``json.loads`` that refuses NaN and Infinity, as Foundation does."""
    return json.loads(text, parse_constant=_reject_constant)


def dumps_compact(value: Any) -> str:
    """Compact JSON, key order kept, non-ASCII kept, NaN refused."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def dumps_pretty(value: Any) -> str:
    """Two-space JSON for the files people may read (settings, accounts)."""
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def dumps_indented_no_newline(value: Any) -> str:
    """Two-space JSON with no trailing newline: what ``JSON.stringify(value, null, 2)`` gives, the
    form Claude Code writes ``~/.claude.json`` in."""
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def tighten_permissions(path: str) -> bool:
    """Removes group and other access from one of OUR files if it has any (a file made by hand
    or an older tool). Never widens. True when it changed the mode."""
    try:
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink > 1:
            return False
        mode = stat.S_IMODE(status.st_mode)
        if mode & 0o077:
            os.chmod(path, mode & 0o700)
            return True
    except OSError:
        pass
    return False


def make_scratch_dir(prefix: str, parent: str) -> str:
    """A fresh 0700 directory under ``parent`` (created if needed)."""
    ensure_private_dir(parent)
    return tempfile.mkdtemp(prefix=prefix, dir=parent)
