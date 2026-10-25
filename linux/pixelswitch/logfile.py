"""The PixelSwitch log, in the Mac log's line format and without emails.

Ports ``PixelSwitch/Services/FileLogger.swift``: ``[<ISO 8601 UTC with ms>] [LEVEL] [Category]
message``, a ``====== PixelSwitch launched <ts> ======`` header per start, rotation at 5 MB with
two prior files kept (``.1`` and ``.2``). The file is opened 0600 in a 0700 folder.

The Mac log never carries a token, and on Linux it carries no email either (spec 5.2): callers
log account ids, and as a second line of defence every line is scrubbed of anything that looks
like an email address or an OAuth token before it is written.
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from .fsio import ensure_private_dir

MAX_BYTES = 5 * 1024 * 1024
KEEP_ROTATED = 2

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]+=*")

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


def scrub(text: str) -> str:
    """``text`` with email addresses, ``sk-ant-`` tokens and bearer values replaced."""
    text = _TOKEN.sub("<token>", text)
    text = _BEARER.sub("Bearer <token>", text)
    return _EMAIL.sub("<email>", text)


def timestamp(now: float) -> str:
    moment = datetime.fromtimestamp(now, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class Log:
    """One log file. Thread-safe. ``category(name)`` gives the per-module handle callers use."""

    def __init__(
        self,
        path: str | None,
        *,
        clock: Callable[[], float] = time.time,
        max_bytes: int = MAX_BYTES,
        keep: int = KEEP_ROTATED,
        mirror: Callable[[str], None] | None = None,
    ) -> None:
        self.path = path
        self._clock = clock
        self._max_bytes = max_bytes
        self._keep = keep
        self._mirror = mirror
        self._lock = threading.Lock()
        self._fd = -1
        self._written = 0
        if path is not None:
            self._open()
            self._raw(f"====== PixelSwitch launched {timestamp(self._clock())} ======\n")

    # --- file handling ---------------------------------------------------------------------

    def _open(self) -> None:
        assert self.path is not None
        ensure_private_dir(os.path.dirname(self.path))
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        self._fd = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(self._fd, os.fstat(self._fd).st_mode & 0o600)  # never wider than 0600
        except OSError:
            pass
        self._written = os.fstat(self._fd).st_size

    def _raw(self, text: str) -> None:
        if self._fd < 0:
            return
        data = text.encode("utf-8", "replace")
        try:
            os.write(self._fd, data)
        except OSError:
            return
        self._written += len(data)
        if self._written >= self._max_bytes:
            self._rotate()

    def _rotate(self) -> None:
        assert self.path is not None
        os.close(self._fd)
        self._fd = -1
        oldest = f"{self.path}.{self._keep}"
        try:
            os.unlink(oldest)
        except OSError:
            pass
        for index in range(self._keep, 1, -1):
            source, target = f"{self.path}.{index - 1}", f"{self.path}.{index}"
            if os.path.exists(source):
                try:
                    os.replace(source, target)
                except OSError:
                    pass
        try:
            os.replace(self.path, f"{self.path}.1")
        except OSError:
            pass
        self._open()
        self._raw(f"====== rotated at {timestamp(self._clock())} ======\n")

    def close(self) -> None:
        with self._lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1

    # --- writing ---------------------------------------------------------------------------

    def write(self, level: str, category: str, message: str) -> None:
        line = f"[{timestamp(self._clock())}] [{level}] [{category}] {scrub(message)}\n"
        with self._lock:
            self._raw(line)
        if self._mirror is not None:
            try:
                self._mirror(line.rstrip("\n"))
            except Exception:
                pass

    def category(self, name: str) -> "CategoryLog":
        return CategoryLog(self, name)


class CategoryLog:
    """``FileLog("Category")``: ``log.info(msg)`` and friends."""

    def __init__(self, log: Log, name: str) -> None:
        self._log = log
        self.name = name

    def debug(self, message: str) -> None:
        self._log.write("DEBUG", self.name, message)

    def info(self, message: str) -> None:
        self._log.write("INFO", self.name, message)

    def warning(self, message: str) -> None:
        self._log.write("WARN", self.name, message)

    def error(self, message: str) -> None:
        self._log.write("ERROR", self.name, message)


NULL_LOG = Log(None)
