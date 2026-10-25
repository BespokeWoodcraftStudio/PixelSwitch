"""The control socket server (spec 5.2; ``ControlServer.swift`` and recon ``control.md`` 1.1-1.2, 5.1).

* The socket lives at ``$XDG_RUNTIME_DIR/pixelswitch/control.sock`` (else the state folder), in a
  0700 directory, created 0600 (``umask`` 0077 around ``bind``). Only the user that owns it may
  connect: ``SO_PEERCRED`` uid check, the connection closed at once otherwise.
* Framing: UTF-8, one JSON object per line. A trailing ``\\r`` is stripped and an empty or
  blank line is skipped. A line over 1 MiB closes the connection.
* Each connection has its own thread and its requests run strictly one at a time in arrival
  order; a long ``accounts.switch`` on one connection never blocks another connection.
* A line that cannot be fully written within 2 s closes the connection (a subscriber that stops
  reading is dropped at its first stalled write).
* The caller holds ``daemon.lock``: only while holding it may this server unlink a stale socket
  (one nothing answers on). A socket something does answer on means another copy runs and this
  one does not serve.

Events are written by one dispatcher thread, so a stalled subscriber can delay the others by
2 s at most once and never delays the code that emitted the event.
"""
from __future__ import annotations

import os
import queue
import select
import socket
import struct
import sys
import threading
import time
from typing import Callable

from . import fsio
from .protocol import MAX_LINE_BYTES, MAX_SOCKET_PATH_BYTES, event_line

WRITE_TIMEOUT = 2.0
BACKLOG = 16
_NOSIGNAL = getattr(socket, "MSG_NOSIGNAL", 0)


class StartError(Exception):
    """The server did not start; the daemon keeps running without remote control only if the
    caller decides so. ``already_running`` means another copy answers on the socket."""

    def __init__(self, message: str, already_running: bool = False) -> None:
        super().__init__(message)
        self.already_running = already_running


def peer_uid(sock: socket.socket) -> int | None:
    """The uid of the process on the other end, or None when it cannot be told."""
    option = getattr(socket, "SO_PEERCRED", None)
    try:
        if option is not None:
            _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i")))
            return uid
        if sys.platform == "darwin":  # LOCAL_PEERCRED, struct xucred: version, uid, ngroups, groups[16]
            data = sock.getsockopt(0, 0x001, 76)
            return struct.unpack_from("II", data, 0)[1]
    except (OSError, struct.error):
        return None
    return None


class Connection:
    """One client. ``send_line`` is safe from any thread."""

    def __init__(self, sock: socket.socket, server: "ControlServer") -> None:
        self.sock = sock
        self.server = server
        self.subscribed = False
        self._write_lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def mark_subscribed(self) -> None:
        self.subscribed = True

    def send_line(self, text: str) -> bool:
        """Writes ``text`` and a newline, within 2 s. False (and the connection closed) if it could not.
        The socket is non-blocking, so a client that stops reading costs at most the 2 s once."""
        data = text.encode("utf-8") + b"\n"
        with self._write_lock:
            if self._closed:
                return False
            deadline = time.monotonic() + WRITE_TIMEOUT
            view = memoryview(data)
            try:
                while view:
                    try:
                        sent = self.sock.send(view, _NOSIGNAL)
                        view = view[sent:]
                    except (BlockingIOError, InterruptedError):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError from None
                        poller = select.poll()
                        poller.register(self.sock.fileno(), select.POLLOUT)
                        poller.poll(min(remaining, 0.25) * 1000)
            except (OSError, TimeoutError, ValueError):
                self.close()
                return False
        return True

    def read(self, size: int = 65536) -> bytes | None:
        """The next chunk, b"" at end of stream, or None if the connection was closed here. Waits
        for data without busy-looping, and wakes every second to notice a close."""
        poller = select.poll()
        try:
            poller.register(self.sock.fileno(), select.POLLIN)
        except (OSError, ValueError):
            return None
        while not self._closed:
            try:
                events = poller.poll(1000)
            except (OSError, ValueError):
                return None
            if not events:
                continue
            try:
                return self.sock.recv(size)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return b""
        return None

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class ControlServer:
    def __init__(
        self,
        path: str,
        handler: Callable[[str, Connection], str | None],
        log: Callable[[str], None] = lambda message: None,
        *,
        uid: int | None = None,
        max_line: int = MAX_LINE_BYTES,
    ) -> None:
        self.path = path
        self._handler = handler
        self._log = log
        self._uid = os.getuid() if uid is None else uid
        self._max_line = max_line
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._connections: set[Connection] = set()
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._events: "queue.Queue[tuple[str, dict, float] | None]" = queue.Queue()
        self._dispatcher: threading.Thread | None = None

    # -- starting and stopping -----------------------------------------------------------------------

    def start(self) -> None:
        if len(os.fsencode(self.path)) > MAX_SOCKET_PATH_BYTES:
            raise StartError(f"The control socket path is longer than {MAX_SOCKET_PATH_BYTES} bytes.")
        directory = os.path.dirname(self.path)
        try:
            fsio.ensure_private_dir(directory)
            status = os.stat(directory)
            if status.st_uid != os.getuid():
                raise StartError("The control socket folder belongs to another user.")
            if status.st_mode & 0o077:
                os.chmod(directory, status.st_mode & 0o700)
        except OSError as error:
            raise StartError(f"The control socket folder could not be prepared ({type(error).__name__}).") from None
        if os.path.lexists(self.path):
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(2.0)
                probe.connect(self.path)
            except OSError:
                try:
                    os.unlink(self.path)  # stale: nothing answers; the daemon lock proves it is ours to remove
                except OSError as error:
                    raise StartError(f"A stale control socket could not be removed ({type(error).__name__}).") from None
            else:
                raise StartError("Another PixelSwitch is already answering on the control socket.", already_running=True)
            finally:
                probe.close()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)
        try:
            listener.bind(self.path)
        except OSError as error:
            listener.close()
            raise StartError(f"The control socket could not be bound ({type(error).__name__}).") from None
        finally:
            os.umask(previous)
        os.chmod(self.path, 0o600)
        listener.listen(BACKLOG)
        listener.settimeout(0.1)
        self._listener = listener
        self._accept_thread = threading.Thread(target=self._accept_loop, name="control-accept", daemon=True)
        self._accept_thread.start()
        self._dispatcher = threading.Thread(target=self._dispatch_events, name="control-events", daemon=True)
        self._dispatcher.start()

    def stop_accepting(self) -> None:
        self._stopping.set()

    def stop(self) -> None:
        self._stopping.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self._events.put(None)
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            connection.close()
        for thread in (self._accept_thread, self._dispatcher):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=3.0)
        try:
            if os.path.lexists(self.path) and not os.path.isdir(self.path):
                os.unlink(self.path)
        except OSError:
            pass

    # -- accepting --------------------------------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            uid = peer_uid(client)
            wrong_user = uid != self._uid if uid is not None else sys.platform.startswith("linux")
            if wrong_user:
                self._log("[control] Refused a connection from another user")
                client.close()
                continue
            client.setblocking(False)
            connection = Connection(client, self)
            with self._lock:
                self._connections.add(connection)
            threading.Thread(target=self._serve, args=(connection,), name="control-conn", daemon=True).start()

    # -- one connection ----------------------------------------------------------------------------------------------

    def _serve(self, connection: Connection) -> None:
        buffer = bytearray()
        try:
            while not connection.closed:
                chunk = connection.read()
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    line = bytes(buffer[:newline])
                    del buffer[:newline + 1]
                    if len(line) > self._max_line:
                        return
                    if not self._process(connection, line):
                        return
                if len(buffer) > self._max_line:
                    return  # a client that never sends a newline is cut off; others keep working
        finally:
            connection.close()
            with self._lock:
                self._connections.discard(connection)

    def _process(self, connection: Connection, raw: bytes) -> bool:
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw.strip(b" \t"):
            return True  # blank lines are skipped
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")  # the protocol layer answers with a parse error
        try:
            response = self._handler(text, connection)
        except Exception:
            self._log("[control] A handler failed unexpectedly")
            return True
        if response is None:
            return True
        return connection.send_line(response)

    # -- events -----------------------------------------------------------------------------------------------------------

    def subscribers(self) -> list[Connection]:
        with self._lock:
            return [c for c in self._connections if c.subscribed and not c.closed]

    def publish(self, event_type: str, data: dict, at: float | None = None) -> None:
        """Queues an event for every subscriber; returns at once."""
        self._events.put((event_type, data, time.time() if at is None else at))

    def _dispatch_events(self) -> None:
        while True:
            item = self._events.get()
            if item is None:
                return
            event_type, data, at = item
            try:
                line = event_line(event_type, at, data)
            except (TypeError, ValueError):
                continue
            for connection in self.subscribers():
                connection.send_line(line)
