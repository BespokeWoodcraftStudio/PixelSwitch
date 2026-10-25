"""A mock of the three Anthropic endpoints PixelSwitch calls, with scripted responses, for the unit
tests and for the scratch-HOME integration run (spec 5.7: base URLs are overridable only in test
mode, and never point at a real host).

* ``GET  /api/oauth/usage``    bearer token -> usage JSON, or 401 / 403 / 429 (+ ``Retry-After``) / 5xx
* ``GET  /api/oauth/profile``  bearer token -> ``{"account": {"uuid", "email"}}``, or an error
* ``POST /v1/oauth/token``     ``refresh_token`` -> ``{"access_token", "refresh_token", "expires_in", ...}``
  (a rotation), or ``invalid_grant`` (400), or a transient failure

Every request is recorded (:class:`Call`) so a test can assert what was SENT: that a token which got a
401 is never sent again, which headers each endpoint receives, the body of the refresh. Scripts are
queues: ``script("usage", token, R(...), R(...))`` answers the first request with the first
response and so on; the LAST response repeats. Without a script an endpoint answers from the
account table (``add_account``) and says 401 for an unknown token.

Stdlib only. Binds ``127.0.0.1`` on a free port. Run it standalone with ``python3 mock_api.py`` to
print the base URL (a fabricated account is registered for manual poking).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

USAGE_PATH = "/api/oauth/usage"
PROFILE_PATH = "/api/oauth/profile"
TOKEN_PATH = "/v1/oauth/token"


@dataclass
class R:
    """One scripted response. ``body`` is a dict (sent as JSON) or a string (sent as is)."""

    status: int = 200
    body: Any = None
    headers: dict = field(default_factory=dict)
    delay: float = 0.0


@dataclass
class Call:
    method: str
    path: str
    headers: dict  # lower-cased names
    body: bytes
    token: str | None  # the bearer token, or the refresh_token of a token request
    at: float


def usage_body(session=0.0, weekly=0.0, session_resets_in=3600.0, weekly_resets_in=3 * 86400.0, fable=None,
               now: float | None = None) -> dict:
    """A usage response. ``*_resets_in`` are seconds from ``now``."""
    from pixelswitch.protocol import iso8601_utc

    now = time.time() if now is None else now
    body: dict = {
        "five_hour": {"utilization": session, "resets_at": iso8601_utc(now + session_resets_in) if session_resets_in is not None else None},
        "seven_day": {"utilization": weekly, "resets_at": iso8601_utc(now + weekly_resets_in) if weekly_resets_in is not None else None},
    }
    if fable is not None:
        body["limits"] = [{"kind": "weekly_scoped", "group": "weekly", "percent": fable,
                           "scope": {"model": {"id": None, "display_name": "Fable"}},
                           "resets_at": iso8601_utc(now + weekly_resets_in)}]
    return body


class MockApi:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._lock = threading.Lock()
        self._scripts: dict[tuple[str, str | None], list[R]] = {}
        self._accounts: dict[str, dict] = {}       # access token -> {"uuid", "email", "usage": body}
        self._refresh: dict[str, dict] = {}        # refresh token -> {"access", "refresh", "expires_in", "refresh_expires_in"}
        self._server: ThreadingHTTPServer | None = None
        self.base = ""

    # -- setting up ---------------------------------------------------------------------------------

    def add_account(self, access_token: str, uuid: str, email: str, usage: dict | None = None) -> None:
        """Registers an access token as belonging to an account (profile) with a usage body."""
        self._accounts[access_token] = {"uuid": uuid, "email": email, "usage": usage if usage is not None else usage_body()}

    def set_usage(self, access_token: str, usage: dict) -> None:
        self._accounts[access_token]["usage"] = usage

    def add_refresh(self, refresh_token: str, new_access: str, new_refresh: str | None, expires_in: float = 28800.0,
                    refresh_expires_in: float | None = None) -> None:
        """A refresh token that rotates: the next token request returns ``new_access`` (and
        ``new_refresh``). The refresh token is then spent: a second use is ``invalid_grant``."""
        self._refresh[refresh_token] = {"access": new_access, "refresh": new_refresh, "expires_in": expires_in,
                                        "refresh_expires_in": refresh_expires_in}

    def script(self, endpoint: str, key: str | None, *responses: R) -> None:
        """``endpoint``: ``usage``, ``profile`` or ``token``; ``key``: the bearer token (usage and
        profile) or the refresh token (token), or None for every request to that endpoint."""
        self._scripts[(endpoint, key)] = list(responses)

    # -- inspecting ------------------------------------------------------------------------------------------

    def calls_to(self, endpoint: str, key: str | None = None) -> list[Call]:
        path = {"usage": USAGE_PATH, "profile": PROFILE_PATH, "token": TOKEN_PATH}[endpoint]
        with self._lock:
            return [c for c in self.calls if c.path == path and (key is None or c.token == key)]

    def clear_calls(self) -> None:
        with self._lock:
            self.calls.clear()

    # -- serving -------------------------------------------------------------------------------------------------

    def _next(self, endpoint: str, key: str | None) -> R | None:
        with self._lock:
            for scripted_key in ((endpoint, key), (endpoint, None)):
                queue = self._scripts.get(scripted_key)
                if queue:
                    return queue.pop(0) if len(queue) > 1 else queue[0]
        return None

    def _answer(self, endpoint: str, token: str | None, refresh_token: str | None) -> R:
        scripted = self._next(endpoint, refresh_token if endpoint == "token" else token)
        if scripted is not None:
            return scripted
        if endpoint == "usage":
            account = self._accounts.get(token or "")
            return R(200, account["usage"]) if account else R(401, {"error": {"type": "authentication_error"}})
        if endpoint == "profile":
            account = self._accounts.get(token or "")
            if not account:
                return R(401, {"error": {"type": "authentication_error"}})
            return R(200, {"account": {"uuid": account["uuid"], "email": account["email"]}})
        grant = self._refresh.pop(refresh_token or "", None)
        if grant is None:
            return R(400, {"error": "invalid_grant", "error_description": "Refresh token not found or invalid"})
        body = {"access_token": grant["access"], "expires_in": grant["expires_in"], "token_type": "Bearer"}
        if grant["refresh"]:
            body["refresh_token"] = grant["refresh"]
        if grant["refresh_expires_in"]:
            body["refresh_token_expires_in"] = grant["refresh_expires_in"]
        return R(200, body)

    def start(self) -> str:
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                endpoint = {USAGE_PATH: "usage", PROFILE_PATH: "profile", TOKEN_PATH: "token"}.get(self.path)
                token = None
                refresh_token = None
                auth = headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    token = auth[7:]
                if endpoint == "token":
                    try:
                        refresh_token = json.loads(body.decode("utf-8")).get("refresh_token")
                    except (ValueError, AttributeError):
                        refresh_token = None
                with api._lock:
                    api.calls.append(Call(method, self.path, headers, body, refresh_token if endpoint == "token" else token,
                                          time.time()))
                if endpoint is None or (endpoint == "token") != (method == "POST"):
                    response = R(404, {"error": "not_found"})
                else:
                    response = api._answer(endpoint, token, refresh_token)
                if response.delay:
                    time.sleep(response.delay)
                payload = response.body if isinstance(response.body, str) else json.dumps(response.body if response.body is not None else {})
                data = payload.encode("utf-8")
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in response.headers.items():
                    self.send_header(name, str(value))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, name="mock-api", daemon=True).start()
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self.base

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "MockApi":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def fabricated(prefix: str) -> str:
    """A fabricated random token (never a real login)."""
    return f"sk-ant-{prefix}-{secrets.token_hex(12)}"


if __name__ == "__main__":  # pragma: no cover - manual use
    server = MockApi()
    access = fabricated("oat01")
    server.add_account(access, "00000000-0000-4000-8000-000000000001", "fabricated@example.invalid")
    print(server.start())
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
