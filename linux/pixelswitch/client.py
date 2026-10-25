"""The Linux control client: one connection to the daemon's Unix socket, blocking calls with
a timeout. Used by the CLI, the MCP server and the faces (tray, settings window).

Ports PixelSwitchCLI/ControlClient.swift (connect, call, nextEvent, the 1 MiB line limit) and
the exit-code mapping of CLIRunner.swift:3-20, with the Linux socket resolution of spec 5.2:

1. ``PIXELSWITCH_CONTROL_SOCKET`` (when set) is the only path tried.
2. Otherwise, in order: the path written in ``~/.local/state/pixelswitch/socket-path``,
   ``$XDG_RUNTIME_DIR/pixelswitch/control.sock``, ``/run/user/<uid>/pixelswitch/control.sock``,
   and ``~/.local/state/pixelswitch/control.sock``.

When none answers the client runs ``systemctl --user start pixelswitch.service`` ONCE and polls
every 250 ms for up to ``wait`` seconds, unless ``PIXELSWITCH_NO_LAUNCH=1``. Faces never exec
``pixelswitchd`` themselves, and nothing here sends ``usage.refresh`` by itself.

A line longer than 1 MiB closes the connection (``ClientError`` ``closed``). ``send`` uses
``MSG_NOSIGNAL`` so a vanished daemon never raises SIGPIPE.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from .protocol import (
    MAX_LINE_BYTES,
    PROTOCOL_VERSION,
    ControlError,
    decode_event,
    decode_response,
    request_line,
)

SERVICE_NAME = "pixelswitch.service"
ENV_SOCKET = "PIXELSWITCH_CONTROL_SOCKET"
ENV_NO_LAUNCH = "PIXELSWITCH_NO_LAUNCH"

# Exit codes, as ``pixelswitch help`` lists them (CLIRunner.swift:3-20).
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_BUSY = 3
EXIT_NOT_FOUND = 4
EXIT_UNREACHABLE = 5

#: Calls that wait ``max(timeout, 120)`` seconds (the CLI's "slow" calls; the MCP server gives
#: ``usage.refresh`` and ``accounts.switch`` the same 120 s, and ``accounts.addCurrent`` too).
SLOW_METHODS = frozenset({"accounts.switch", "accounts.addCurrent", "usage.refresh"})
SLOW_CALL_FLOOR_SECONDS = 120.0
DEFAULT_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 5.0

#: What ``status.features`` must contain before the CLI sends a 2.0 method, and the words for the
#: refusal against an older PixelSwitch ("This PixelSwitch (<version>) cannot <action>; update it.").
FEATURE_ACTIONS = {
    "splitThresholds": "set separate thresholds",
    "priority": "set priorities",
}


class ClientError(Exception):
    """A transport problem (as opposed to ``ControlError``, an error reply).

    ``kind``: ``unreachable`` (PixelSwitch is not running and could not be started, or its socket
    is missing), ``timedOut``, ``closed``.
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(self.description)

    @property
    def description(self) -> str:
        if self.kind == "unreachable":
            return f"PixelSwitch is not reachable: {self.detail}"
        if self.kind == "timedOut":
            return f"PixelSwitch did not answer {self.detail} in time."
        return "PixelSwitch closed the connection."


def exit_code_for(error: BaseException) -> int:
    """The CLI's exit code for an error (CLIRunner.swift:12-19 and 64-79): busy 3; no such or
    ambiguous account 4; an invalid value or parameters 2; unreachable 5; anything else 1."""
    if isinstance(error, ControlError):
        if error.kind == "busy":
            return EXIT_BUSY
        if error.kind in ("notFound", "ambiguous"):
            return EXIT_NOT_FOUND
        if error.kind in ("invalidValue", "invalidParams"):
            return EXIT_USAGE
        return EXIT_FAILED
    if isinstance(error, ClientError):
        return EXIT_UNREACHABLE if error.kind == "unreachable" else EXIT_FAILED
    return EXIT_FAILED


def call_timeout(method: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> float:
    """How long to wait for ``method``: ``timeout``, but at least 120 s for the slow calls."""
    return max(timeout, SLOW_CALL_FLOOR_SECONDS) if method in SLOW_METHODS else timeout


# --- socket resolution (spec 5.2) --------------------------------------------------------------


def _read_socket_path_file(state_dir: str) -> str | None:
    try:
        with open(os.path.join(state_dir, "socket-path"), encoding="utf-8") as handle:
            text = handle.read(4096).strip().splitlines()[0:1]
    except (OSError, UnicodeDecodeError):
        return None
    if text and os.path.isabs(text[0]):
        return text[0]
    return None


def socket_candidates(env: Mapping[str, str] | None = None, uid: int | None = None) -> list[str]:
    """The socket paths to try, in order (spec 5.2). ``env`` defaults to ``os.environ``."""
    env = os.environ if env is None else env
    override = env.get(ENV_SOCKET)
    if override:
        return [override]
    home = env.get("HOME") or os.path.expanduser("~")
    state_dir = os.path.join(home, ".local", "state", "pixelswitch")
    paths: list[str] = []
    recorded = _read_socket_path_file(state_dir)
    if recorded:
        paths.append(recorded)
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        paths.append(os.path.join(runtime, "pixelswitch", "control.sock"))
    user_id = os.getuid() if uid is None else uid
    paths.append(f"/run/user/{user_id}/pixelswitch/control.sock")
    paths.append(os.path.join(state_dir, "control.sock"))
    seen: set[str] = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def launch_allowed(env: Mapping[str, str] | None = None) -> bool:
    """``PIXELSWITCH_NO_LAUNCH=1`` stops the client starting the service (integration scripts)."""
    return (os.environ if env is None else env).get(ENV_NO_LAUNCH) != "1"


def start_service() -> int:
    """``systemctl --user start pixelswitch.service``. Returns the exit status; -1 when systemctl
    cannot be run at all. Never ``pixelswitchd`` itself."""
    try:
        return subprocess.run(
            ["systemctl", "--user", "start", SERVICE_NAME],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=60, check=False,
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


# --- the client --------------------------------------------------------------------------------

_NOSIGNAL = getattr(socket, "MSG_NOSIGNAL", 0)


def _server_is_us(sock: socket.socket) -> bool:
    """On Linux, the peer of a connected Unix socket must run as this user (``SO_PEERCRED``).
    Where the option does not exist (not Linux) there is nothing to check."""
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        return True
    try:
        # struct ucred is { pid_t pid; uid_t uid; gid_t gid; }: a signed int, then two UNSIGNED ones. Read as
        # signed, a uid of 2**31 or more is negative and the client would refuse its own daemon.
        creds = sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("iII"))
        _pid, uid, _gid = struct.unpack("iII", creds)
    except (OSError, struct.error):
        return False
    return uid == os.getuid()


class ControlClient:
    """One connection to the daemon. Not thread-safe: one caller at a time."""

    def __init__(
        self,
        path: str | None = None,
        *,
        env: Mapping[str, str] | None = None,
        launcher: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._env = os.environ if env is None else env
        self._explicit_path = path
        self._launcher = launcher or start_service
        self._clock = clock
        self._sock: socket.socket | None = None
        self._buffer = bytearray()
        self._next_id = 1
        #: Pushed events that ``call`` skipped while it waited for its reply (a face that reads on an event
        #: reads again when this rose during a read, so a change that landed mid-read is not lost).
        self.skipped_events = 0
        #: The path the current connection was made to.
        self.path: str | None = None

    # --- connection --------------------------------------------------------------------------

    def candidates(self) -> list[str]:
        return [self._explicit_path] if self._explicit_path else socket_candidates(self._env)

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def _try_connect(self, path: str) -> bool:
        self.disconnect()
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError:
            return False
        try:
            sock.settimeout(CONNECT_TIMEOUT_SECONDS)  # a full backlog must not hang the caller
            sock.connect(path)
            sock.settimeout(None)
        except (OSError, ValueError):
            sock.close()
            return False
        if not _server_is_us(sock):
            sock.close()
            return False
        no_sigpipe = getattr(socket, "SO_NOSIGPIPE", None)
        if no_sigpipe is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, no_sigpipe, 1)
            except OSError:
                pass
        self._sock = sock
        self.path = path
        return True

    def _try_all(self) -> bool:
        return any(self._try_connect(path) for path in self.candidates())

    def connect(self, launch: bool = True, wait: float = 10.0) -> None:
        """Connects; if nothing answers and ``launch`` is allowed (and ``PIXELSWITCH_NO_LAUNCH`` is
        not 1) starts the service once and polls for up to ``wait`` seconds."""
        if self._try_all():
            return
        paths = self.candidates()
        if not launch or not launch_allowed(self._env):
            raise ClientError("unreachable", f"nothing is listening at {paths[0]}")
        status = self._launcher()
        if status != 0:
            raise ClientError("unreachable", "PixelSwitch is not installed, or its service could not be started")
        deadline = self._clock() + wait
        while self._clock() < deadline:
            if self._try_all():
                return
            time.sleep(0.25)
        raise ClientError("unreachable", f"PixelSwitch started but did not open its socket within {int(wait)} seconds")

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buffer.clear()

    def __enter__(self) -> "ControlClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    # --- calls -------------------------------------------------------------------------------

    def call(
        self, method: str, params: Any = None, timeout: float = DEFAULT_TIMEOUT_SECONDS, *, slow_floor: bool = True
    ) -> Any:
        """Sends one request and waits for its reply. Returns the ``result`` (None for a null
        result). Raises ``ControlError`` for an error reply and ``ClientError`` for a transport
        problem. Pushed events and replies to other ids are skipped.

        The slow calls (``accounts.switch``, ``accounts.addCurrent``, ``usage.refresh``) wait at
        least 120 s whatever ``timeout`` says (CLIRunner.swift:73-75); pass ``slow_floor=False``
        for an exact timeout (tests)."""
        if slow_floor:
            timeout = call_timeout(method, timeout)
        request_id = self._next_id
        self._next_id += 1
        deadline = self._clock() + timeout
        self._send(request_line(request_id, method, params) + "\n", deadline, method)
        while True:
            line = self._read_line(deadline)
            if line is None:
                raise ClientError("timedOut", method)
            response = decode_response(line)
            if response is None or response.id != request_id:
                if decode_event(line) is not None:
                    self.skipped_events += 1  # a pushed event that arrived while waiting; the faces read again
                continue  # a pushed event, or another reply
            if response.error is not None:
                raise ControlError.from_rpc(response.error)
            return response.result

    def next_event(self, timeout: float | None = None, *, deadline: float | None = None) -> dict | None:
        """The ``params`` (``at``, ``data``, ``type``) of the next pushed event, or None at the
        deadline. For ``watch`` and the faces, after ``events.subscribe``."""
        if deadline is None:
            deadline = self._clock() + (3600.0 if timeout is None else timeout)
        while True:
            line = self._read_line(deadline)
            if line is None:
                return None
            params = decode_event(line)
            if params is not None:
                return params

    # --- transport -----------------------------------------------------------------------------

    def _send(self, text: str, deadline: float, method: str) -> None:
        sock = self._sock
        if sock is None:
            raise ClientError("closed")
        data = text.encode("utf-8")
        offset = 0
        while offset < len(data):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise ClientError("timedOut", method)
            try:
                _, writable, _ = select.select([], [sock], [], min(remaining, 3600))
            except (OSError, ValueError):
                self.disconnect()
                raise ClientError("closed") from None
            if not writable:
                continue
            try:
                written = sock.send(data[offset:], _NOSIGNAL)
            except BlockingIOError:
                continue
            except OSError:
                self.disconnect()
                raise ClientError("closed") from None
            if written <= 0:
                self.disconnect()
                raise ClientError("closed")
            offset += written

    def _read_line(self, deadline: float) -> bytes | None:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                return line
            sock = self._sock
            if sock is None:
                raise ClientError("closed")
            remaining = deadline - self._clock()
            if remaining <= 0:
                return None
            try:
                ready, _, _ = select.select([sock], [], [], min(remaining, 3600))
            except (OSError, ValueError):
                self.disconnect()
                raise ClientError("closed") from None
            if not ready:
                return None
            try:
                chunk = sock.recv(64 * 1024)
            except InterruptedError:
                continue
            except OSError:
                self.disconnect()
                raise ClientError("closed") from None
            if not chunk:
                self.disconnect()
                raise ClientError("closed")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_LINE_BYTES:
                self.disconnect()
                raise ClientError("closed")


# --- what the CLI checks on the first reply (CLIRunner.swift:62-68, spec section 4) ------------------


def check_protocol(status: Mapping[str, Any]) -> None:
    """Refuses a PixelSwitch that speaks another protocol version (an old one still running
    after an update): ``protocolVersion`` must equal 2 exactly."""
    version = status.get("protocolVersion")
    if version != PROTOCOL_VERSION or isinstance(version, bool):
        raise ControlError(
            "protocolMismatch",
            f"PixelSwitch {status.get('appVersion', 'unknown')} speaks remote-control protocol {version}, "
            f"but this pixelswitch speaks {PROTOCOL_VERSION}. Quit and reopen PixelSwitch, then run the command again.",
        )


def has_feature(status: Mapping[str, Any], feature: str) -> bool:
    features = status.get("features")
    return isinstance(features, list) and feature in features


def require_feature(status: Mapping[str, Any], feature: str) -> None:
    """Before sending a 2.0 method the CLI checks ``status.features``; against an older app it
    stops with "This PixelSwitch (<version>) cannot <action>; update it." (exit 1)."""
    if has_feature(status, feature):
        return
    action = FEATURE_ACTIONS.get(feature, f"use {feature}")
    raise ControlError("failed", f"This PixelSwitch ({status.get('appVersion', 'unknown')}) cannot {action}; update it.")


def candidates_text(candidates: Sequence[str]) -> str:
    """The ``  choices: a, b, c`` line the CLI prints on stderr after an error that lists choices."""
    return "  choices: " + ", ".join(candidates)
