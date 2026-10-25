"""The faces' live link to the daemon: standard library only, no GTK.

Two connections, each used by one thread (``ControlClient`` is not thread-safe):

* the **event thread** connects, checks the protocol, sends ``events.subscribe`` and reads the pushed
  events. On any event it re-reads ``status.get``, ``accounts.list`` (and ``settings.get`` when asked)
  after a short quiet moment, so a burst of events is one read. When the daemon goes away it publishes a
  disconnected snapshot and reconnects with a growing wait (1 s to 30 s). It starts the service
  (``systemctl --user start pixelswitch.service``, through the client) at most once, on the first
  connect, and never execs the daemon.
* the **call worker** runs the user's actions (``accounts.switch``, ``settings.set`` and so on) one at a
  time, so the interface never waits on the socket.

Everything the faces get back is handed to ``post`` (in the GTK faces ``GLib.idle_add``, in tests a plain
call), so widgets are only touched on the main thread. Nothing here sends ``usage.refresh`` by itself.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Mapping

from ..client import ClientError, ControlClient, check_protocol, launch_allowed
from ..protocol import ControlError
from . import model

QUIET_SECONDS = 0.25
MAX_BACKOFF = 30.0
POLL_SLICE_SECONDS = 0.2


class DaemonLink:
    def __init__(
        self,
        post: Callable[[Callable[[], None]], None],
        on_snapshot: Callable[[model.Snapshot], None],
        *,
        env: Mapping[str, str] | None = None,
        want_settings: bool = False,
        client_factory: Callable[[], ControlClient] | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self._post = post
        self._on_snapshot = on_snapshot
        self._on_event = on_event
        self._env = env
        self._want_settings = want_settings
        self._factory = client_factory or (lambda: ControlClient(env=env))
        self._stop = threading.Event()
        self._reread = threading.Event()
        self._calls: "queue.Queue[tuple | None]" = queue.Queue()
        self._event_client: ControlClient | None = None
        self._call_client: ControlClient | None = None
        self._threads: list[threading.Thread] = []
        self.snapshot = model.Snapshot()

    # --- lifecycle -------------------------------------------------------------------------------------

    def start(self) -> None:
        for target, name in ((self._event_loop, "link-events"), (self._call_loop, "link-calls")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._reread.set()
        self._calls.put(None)
        for client in (self._event_client, self._call_client):
            if client is not None:
                client.disconnect()
        for thread in self._threads:
            thread.join(timeout=2.0)

    def request_snapshot(self) -> None:
        """Read everything again now (no ``usage.refresh``: this only reads)."""
        self._reread.set()

    # --- calls (the user's actions) ----------------------------------------------------------------------

    def call(self, method: str, params: Any = None, done: Callable[[Any, Exception | None], None] | None = None,
             timeout: float = 30.0) -> None:
        """Runs ``method`` on the worker; ``done(result, error)`` is posted to the main thread."""
        self._calls.put((method, params, done, timeout))

    def _call_loop(self) -> None:
        while not self._stop.is_set():
            item = self._calls.get()
            if item is None or self._stop.is_set():
                return
            method, params, done, timeout = item
            result: Any = None
            error: Exception | None = None
            try:
                result = self._call_once(method, params, timeout)
            except (ControlError, ClientError) as caught:
                error = caught
            except Exception as caught:  # a bug here must not end the worker
                error = caught
            if done is not None:
                self._post(lambda done=done, result=result, error=error: done(result, error))

    def _call_once(self, method: str, params: Any, timeout: float) -> Any:
        for attempt in (1, 2):
            client = self._call_client
            if client is None or not client.connected:
                client = self._call_client = self._factory()
                client.connect(launch=False)
            try:
                return client.call(method, params, timeout)
            except ClientError as error:
                client.disconnect()
                if error.kind != "closed" or attempt == 2:
                    raise
        raise ClientError("closed")  # unreachable

    # --- events and snapshots ----------------------------------------------------------------------------

    def _publish(self, snapshot: model.Snapshot) -> None:
        self.snapshot = snapshot
        self._post(lambda snapshot=snapshot: self._on_snapshot(snapshot))

    def _read(self, client: ControlClient) -> model.Snapshot:
        status = client.call("status.get")
        accounts = client.call("accounts.list")
        settings = client.call("settings.get") if self._want_settings else None
        return model.snapshot_from_replies(status, accounts, settings)

    def _event_loop(self) -> None:
        backoff = 1.0
        first = True
        while not self._stop.is_set():
            client = self._event_client = self._factory()
            try:
                client.connect(launch=first and launch_allowed(self._env), wait=10.0)
                first = False
                check_protocol(_as_dict(client.call("status.get")))
                client.call("events.subscribe")
                self._publish(self._read(client))
                backoff = 1.0
                self._read_events(client)
            except (ClientError, ControlError) as error:
                first = False
                if self._stop.is_set():
                    break
                self._publish(model.Snapshot(connected=False, error=_error_text(error)))
            except Exception as error:  # keep the faces alive whatever the daemon sends
                first = False
                if self._stop.is_set():
                    break
                self._publish(model.Snapshot(connected=False, error=f"{type(error).__name__}: {error}"))
            finally:
                client.disconnect()
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, MAX_BACKOFF)

    def _read_events(self, client: ControlClient) -> None:
        dirty = False
        last_event = 0.0
        while not self._stop.is_set():
            event = client.next_event(timeout=POLL_SLICE_SECONDS)
            now = time.monotonic()
            if event is not None:
                if self._on_event is not None:
                    self._post(lambda event=event: self._on_event(event))  # type: ignore[misc]
                dirty = True
                last_event = now
                continue
            if self._reread.is_set():
                self._reread.clear()
                dirty = True
                last_event = now - QUIET_SECONDS
            if dirty and now - last_event >= QUIET_SECONDS:
                self._publish(self._read(client))
                dirty = False


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _error_text(error: Exception) -> str:
    if isinstance(error, ClientError):
        return error.description
    if isinstance(error, ControlError):
        return error.message
    return str(error)
