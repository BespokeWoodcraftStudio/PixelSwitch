"""The rollback script (spec 5.4): a small standard-library module that lane LC installs OUTSIDE the
versioned directories (``~/.local/share/pixelswitch/rollback.py``) and the systemd unit runs as
``ExecStartPre=python3 -I %h/.local/share/pixelswitch/rollback.py``. It imports nothing from the
``pixelswitch`` package, so a broken new version can never stop it from running.

The rule: a build that has started twice WITHOUT writing its ``good-<build>`` marker (the marker a
daemon writes after its first complete cycle) is rolled back. On every run, for the build ``current``
points at:

* a ``good-<build>`` marker exists: the build is proven; its start counter is reset;
* otherwise this start is an attempt at an unproven build. If two earlier attempts at it already
  left no marker and a ``previous`` version exists, ``current`` is flipped back to ``previous``
  (atomically) and the counter starts over for that version;
* else the attempt is counted and the daemon is allowed to start.

Against ``StartLimitBurst=4`` that is: the boot start (the old, proven build), the update restart
(attempt 1 of the new build, no marker), the automatic restart 120 s later (attempt 2, no marker),
and then this script rolls back, so the third start of the new build never happens and the
rolled-back start is the fourth. A unit test plays exactly that sequence.

State: ``~/.local/state/pixelswitch/rollback.json`` (``{"build": n, "attempts": k}``). Exit code 0
always (a failing ``ExecStartPre`` would stop the daemon from starting at all); what it did is
printed on one line.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

MAX_UNPROVEN_ATTEMPTS = 2
_VERSION_DIR = re.compile(r"([0-9]{1,6}\.[0-9]{1,6})-([0-9]{1,9})\Z")


def _layout(home: str) -> tuple[str, str, str]:
    app_dir = os.path.join(home, ".local", "share", "pixelswitch", "app")
    state_dir = os.path.join(home, ".local", "state", "pixelswitch")
    return app_dir, state_dir, os.path.join(state_dir, "rollback.json")


def _link_name(path: str) -> str | None:
    try:
        return os.path.basename(os.readlink(path))
    except OSError:
        return None


def _build_of(name: str | None) -> int | None:
    match = _VERSION_DIR.match(name or "")
    return int(match.group(2)) if match else None


def _read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".rollback.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass


def _flip(link: str, name: str) -> None:
    temp = f"{link}.rollback-{os.getpid()}"
    try:
        os.unlink(temp)
    except FileNotFoundError:
        pass
    os.symlink(name, temp)
    os.replace(temp, link)


def run(home: str) -> str:
    """One ExecStartPre run. Returns a short description of what it did; never raises."""
    try:
        app_dir, state_dir, state_file = _layout(home)
        current = _link_name(os.path.join(app_dir, "current"))
        build = _build_of(current)
        if current is None or build is None:
            return "no current version; nothing to check"
        if os.path.exists(os.path.join(state_dir, f"good-{build}")):
            _write_state(state_file, {"build": build, "attempts": 0})
            return f"build {build} is proven"
        state = _read_state(state_file)
        attempts = state.get("attempts", 0) if state.get("build") == build and isinstance(state.get("attempts"), int) else 0
        previous = _link_name(os.path.join(app_dir, "previous"))
        previous_build = _build_of(previous)
        if attempts >= MAX_UNPROVEN_ATTEMPTS and previous and previous != current and previous_build is not None \
                and os.path.isdir(os.path.join(app_dir, previous)):
            _flip(os.path.join(app_dir, "current"), previous)
            _write_state(state_file, {"build": previous_build, "attempts": 0})
            return f"build {build} failed to start {attempts} times without a good marker; rolled back to {previous}"
        _write_state(state_file, {"build": build, "attempts": attempts + 1})
        return f"build {build}: attempt {attempts + 1} without a good marker"
    except Exception as error:  # an ExecStartPre that fails would keep the daemon from starting
        return f"rollback check skipped ({type(error).__name__})"


def main(argv: list[str] | None = None) -> int:
    home = os.environ.get("HOME") or os.path.expanduser("~")
    print(f"pixelswitch-rollback: {run(home)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
