"""The HTTP transport for the three requests that carry an OAuth token, with the Mac's URLs,
headers, bodies and timeouts (``usage.md`` section 1.1, ``credentials.md`` 5.3).

====================  =====================================================  =============================
request               URL                                                    headers set
====================  =====================================================  =============================
usage (GET)           https://api.anthropic.com/api/oauth/usage              Authorization, anthropic-beta
profile (GET)         https://api.anthropic.com/api/oauth/profile            Authorization, anthropic-beta
refresh (POST)        https://console.anthropic.com/v1/oauth/token           Content-Type: application/json
====================  =====================================================  =============================

``anthropic-beta`` is the literal ``oauth-2025-04-20`` and goes on both GETs, not on the POST.
Timeouts are the Mac's: 60 s for usage and for the refresh (URLSession's default), 15 s for the
profile lookup. The Mac sends URLSession's default User-Agent; Linux sends an explicit
``PixelSwitch/<version>`` (``usage.md`` 6.14). Nothing is cached, no cookies are kept, and a
redirect is never followed (so a bearer token can never be sent to a second host).

Base URLs are overridable only in test mode (``testmode.py``: ``PIXELSWITCH_TEST_MODE=1`` plus
``PIXELSWITCH_TEST_API_BASE``), and in test mode a request to an ``anthropic.com`` or
``claude.com`` host is refused whatever sets the URL. This module never logs a token or a body.
"""
from __future__ import annotations

import http.client
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from . import testmode

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
#: Claude Code's public OAuth client id (a PKCE public client: not a secret).
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_BETA = "oauth-2025-04-20"

USAGE_TIMEOUT = 60.0
PROFILE_TIMEOUT = 15.0
TOKEN_TIMEOUT = 60.0
MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ApiBases:
    usage: str = USAGE_URL
    profile: str = PROFILE_URL
    token: str = TOKEN_URL

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ApiBases":
        """The production URLs, or -- in test mode only -- one test base for all three."""
        base = testmode.api_base(env)
        if base is None:
            return cls()
        return cls(usage=base + "/api/oauth/usage", profile=base + "/api/oauth/profile", token=base + "/v1/oauth/token")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]  # keys lower-cased
    body: bytes

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class TransportError(Exception):
    """No reply at all (offline, DNS, TLS, timeout) or a refused request. ``cause`` is the
    original exception, which ``UsageRequestError.from_transport_error`` classifies."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(type(cause).__name__)
        self.cause = cause


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # the 3xx is returned to the caller as an unexpected status


class Transport:
    """Blocking HTTP with an explicit timeout per request. ``test_mode`` refuses blocked hosts and
    ignores any proxy in the environment (a scratch run talks to localhost)."""

    def __init__(self, *, user_agent: str, test_mode: bool = False) -> None:
        self.user_agent = user_agent
        self.test_mode = test_mode
        handlers: list = [_NoRedirect(), urllib.request.HTTPSHandler(), urllib.request.HTTPHandler()]
        handlers.append(urllib.request.ProxyHandler({}) if test_mode else urllib.request.ProxyHandler())
        self._opener = urllib.request.build_opener(*handlers)
        # build_opener keeps its own default handlers unless ours are subclasses; make sure no
        # cookie or cache layer exists (urllib has none by default).

    def _check_test_mode(self, url: str) -> None:
        """Test mode refuses any request to an anthropic.com or claude.com host (spec 5.7 rule 2)."""
        if not self.test_mode:
            return
        try:
            testmode.check_url(url)
        except testmode.TestModeError as error:
            raise TransportError(error) from None

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: bytes | None = None, timeout: float) -> HttpResponse:
        self._check_test_mode(url)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise TransportError(ValueError("not an http(s) URL"))
        merged = {"User-Agent": self.user_agent, "Accept": "application/json", "Accept-Encoding": "identity"}
        merged.update(headers or {})
        request = urllib.request.Request(url, data=body, method=method, headers=merged)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResponse(response.status, _lower(response.headers), response.read(MAX_BODY_BYTES))
        except urllib.error.HTTPError as error:
            try:
                data = error.read(MAX_BODY_BYTES)
            except Exception:
                data = b""
            finally:
                error.close()
            return HttpResponse(error.code, _lower(error.headers), data)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, http.client.HTTPException,
                OSError, ValueError) as error:
            raise TransportError(error) from None


    def download(self, url: str, destination: str, *, limit: int, timeout: float,
                 headers: Mapping[str, str] | None = None) -> tuple[int, int, bool]:
        """Streams ``url`` into a NEW file ``destination`` (0600, ``O_EXCL``), cutting the transfer off
        at ``limit`` bytes. Returns ``(status, bytes written, exceeded)``; on a non-200 status or
        when the limit is passed the file is removed. Raises :class:`TransportError`."""
        self._check_test_mode(url)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise TransportError(ValueError("not an http(s) URL"))
        merged = {"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Encoding": "identity"}
        merged.update(headers or {})
        request = urllib.request.Request(url, headers=merged)
        written = 0
        created = False
        try:
            try:
                response = self._opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                error.close()
                return error.code, 0, False
            with response:
                if response.status != 200:
                    return response.status, 0, False
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                created = True
                try:
                    while True:
                        chunk = response.read(min(1 << 20, limit + 1 - written))
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > limit:
                            break
                        os.write(fd, chunk)
                finally:
                    os.close(fd)
                if written > limit:
                    os.unlink(destination)
                    return 200, written, True
                return 200, written, False
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, http.client.HTTPException,
                OSError, ValueError) as error:
            if created:
                try:
                    os.unlink(destination)
                except OSError:
                    pass
            raise TransportError(error) from None

def _lower(headers) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()} if headers is not None else {}
