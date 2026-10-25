"""Fixtures for lane LC's tests: a scripted daemon on a real Unix socket (so the CLI, the MCP
server and the faces are driven through the real client), and builders for the control payloads."""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import unittest
from typing import Any, Callable

from pixelswitch import protocol as p
from pixelswitch.protocol import ControlError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LINUX_ROOT = os.path.join(REPO_ROOT, "linux")


def status_payload(**overrides: Any) -> dict:
    """A 2.0 ``status.get`` result."""
    auto = {
        "enabled": True, "defaultThreshold": 90, "defaultSessionThreshold": 90, "defaultWeeklyThreshold": 90,
        "onFable": True, "strategy": "mostRoom", "drainEarly": True, "drainWithinHours": 24,
    }
    auto.update(overrides.pop("autoSwitch", {}))
    status: dict[str, Any] = {
        "appVersion": "2.0", "protocolVersion": 2, "protocolRevision": 3, "features": ["priority", "splitThresholds"],
        "platform": "linux", "accountCount": 2, "claudeAvailable": True,
        "activeAccountId": "11111111-1111-1111-1111-111111111111", "activeAccountEmail": "alice@work.com",
        "autoSwitch": auto,
    }
    status.update(overrides)
    return {k: v for k, v in status.items() if v is not None}


def account_payload(account_id: str = "1", email: str = "alice@work.com", position: int = 1, **overrides: Any) -> dict:
    """An ``AccountInfo`` (equal windows at 90 unless overridden). ``None`` values are dropped."""
    info: dict[str, Any] = {
        "id": account_id, "email": email, "isActive": False, "isSwitchable": True, "position": position,
        "effectiveThreshold": 90, "effectiveSessionThreshold": 90, "effectiveWeeklyThreshold": 90, "manualOnly": False,
    }
    info.update(overrides)
    return {k: v for k, v in info.items() if v is not None}


class FakeDaemon:
    """Answers each method with ``results[method]`` (a value, or a callable ``(params, request)``
    that returns a value or raises ``ControlError``). ``status.get`` defaults to a 2.0 daemon.
    ``requests`` records every request; ``events`` are pushed to a connection after ``events.subscribe``."""

    def __init__(self, path: str, results: dict[str, Any] | None = None, events: list[str] | None = None) -> None:
        self.path = path
        self.results = {"status.get": status_payload(), "events.subscribe": {"subscribed": True}}
        self.results.update(results or {})
        self.events = list(events or [])
        self.requests: list[p.Request] = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(path)
        self.listener.listen(8)
        self.stop_flag = threading.Event()
        self.connections: list[socket.socket] = []
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def methods_asked(self) -> list[str]:
        return [r.method for r in self.requests]

    def last_params(self, method: str) -> Any:
        for request in reversed(self.requests):
            if request.method == method:
                return request.params
        return None

    def _accept(self) -> None:
        self.listener.settimeout(0.1)
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.listener.accept()
            except (socket.timeout, OSError):
                continue
            self.connections.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        def send(text: str) -> None:
            conn.sendall(text.encode("utf-8") + b"\n")

        buffer = b""
        conn.settimeout(0.1)
        try:
            while not self.stop_flag.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    request = p.decode_request(line)
                    self.requests.append(request)
                    self._answer(request, send)
        except OSError:
            return

    def _answer(self, request: p.Request, send: Callable[[str], None]) -> None:
        handler = self.results.get(request.method)
        if handler is None:
            send(p.failure_line(request.id, p.method_not_found(request.method)))
            return
        try:
            result = handler(request.params, request) if callable(handler) else handler
        except ControlError as error:
            send(p.failure_line(request.id, error))
            return
        send(p.success_line(request.id, result))
        if request.method == "events.subscribe":
            for event in self.events:
                send(event)

    def close(self) -> None:
        self.stop_flag.set()
        self.thread.join(timeout=2)
        for conn in self.connections:
            try:
                conn.close()
            except OSError:
                pass
        self.listener.close()


class DaemonTestCase(unittest.TestCase):
    """Starts fake daemons in a short temp folder (a Unix socket path is limited to 107 bytes)."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="pslc", dir="/tmp")
        self.path = os.path.join(self.dir, "c.sock")
        self.daemons: list[FakeDaemon] = []

    def tearDown(self) -> None:
        for daemon in self.daemons:
            daemon.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, results: dict[str, Any] | None = None, events: list[str] | None = None) -> FakeDaemon:
        daemon = FakeDaemon(self.path, results, events)
        self.daemons.append(daemon)
        return daemon

    def env(self) -> dict[str, str]:
        return {"PIXELSWITCH_CONTROL_SOCKET": self.path, "PIXELSWITCH_NO_LAUNCH": "1", "HOME": self.dir}
