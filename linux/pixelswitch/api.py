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
redirect is never followed by the token transport (so a bearer token can never be sent to a second host).

The UPDATER has its own transport (``Transport(follow_redirects=True)``): GitHub answers a release
download with redirects, so it follows at most :data:`MAX_REDIRECTS` of them, to https URLs on
:data:`RELEASE_REDIRECT_HOSTS` only, and it never sends an Authorization or Cookie header (it has no
token). Both transports are the same class; only the redirect policy differs.

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
from typing import Callable, Mapping
from urllib.parse import urlsplit

from . import testmode

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
#: Claude Code's public OAuth client id (a PKCE public client: not a secret).
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_BETA = "oauth-2025-04-20"

#: Where a release download may be redirected to. Observed with ``curl -sI`` on 2026-10-24 for
#: ``https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/appcast.xml``:
#:   302 github.com  /releases/latest/download/<asset>  ->  github.com  /releases/download/v<tag>/<asset>
#:   302 github.com  /releases/download/v<tag>/<asset>  ->  release-assets.githubusercontent.com/...  (200)
#: GitHub served assets from ``objects.githubusercontent.com`` until 2025; it is kept in case a download
#: is still routed there. Any other host is refused.
RELEASE_REDIRECT_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_REDIRECTS = 5
#: Never forwarded to, or sent for, an update download.
_CREDENTIAL_HEADERS = ("authorization", "proxy-authorization", "cookie")

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


class RedirectRefused(ValueError):
    """A redirect the updater's transport will not follow (too many, or to a place it may not go)."""


def check_release_url(url: str, *, test_mode: bool) -> None:
    """The updater's rule for the URL it asks for and for every redirect target. Outside test mode:
    ``https`` only, no user name or password, the default port, and a host on
    :data:`RELEASE_REDIRECT_HOSTS`. In test mode (a local release server over http) the host list does
    not apply, but a blocked host (anthropic.com, claude.com) is still refused. Raises
    :class:`RedirectRefused`."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
    except ValueError:
        raise RedirectRefused("unreadable URL") from None
    if test_mode:
        if parts.scheme not in ("http", "https") or not host:
            raise RedirectRefused("not an http(s) URL")
        try:
            testmode.check_url(url)
        except testmode.TestModeError as error:
            raise RedirectRefused(str(error)) from None
        return
    if parts.scheme != "https":
        raise RedirectRefused("only https is followed")
    if parts.username is not None or parts.password is not None or port not in (None, 443):
        raise RedirectRefused("user info or a port is not allowed")
    if host not in RELEASE_REDIRECT_HOSTS:
        raise RedirectRefused("host is not a GitHub release host")


class _FollowReleaseRedirects(urllib.request.HTTPRedirectHandler):
    """At most :data:`MAX_REDIRECTS` hops, each checked by :func:`check_release_url`, GET only, and
    the new request carries none of the credential headers of the old one."""

    max_redirections = MAX_REDIRECTS

    def __init__(self, test_mode: bool) -> None:
        self._test_mode = test_mode

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        if req.get_method() not in ("GET", "HEAD") or req.data is not None:
            return None
        hops = getattr(req, "_ps_hops", 0) + 1
        if hops > MAX_REDIRECTS:
            raise RedirectRefused("too many redirects")
        check_release_url(newurl, test_mode=self._test_mode)
        kept = {key: value for key, value in req.headers.items() if key.lower() not in _CREDENTIAL_HEADERS}
        new = urllib.request.Request(newurl, headers=kept, method=req.get_method(), unverifiable=True)
        new._ps_hops = hops  # type: ignore[attr-defined]
        return new


class Transport:
    """Blocking HTTP with an explicit timeout per request. ``test_mode`` refuses blocked hosts and
    ignores any proxy in the environment (a scratch run talks to localhost). ``follow_redirects`` is
    for the updater only (see the module docstring); the default never follows one."""

    def __init__(self, *, user_agent: str, test_mode: bool = False, follow_redirects: bool = False) -> None:
        self.user_agent = user_agent
        self.test_mode = test_mode
        self.follow_redirects = follow_redirects
        redirect = _FollowReleaseRedirects(test_mode) if follow_redirects else _NoRedirect()
        handlers: list = [redirect, urllib.request.HTTPSHandler(), urllib.request.HTTPHandler()]
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

    def _check_first_url(self, url: str) -> None:
        self._check_test_mode(url)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise TransportError(ValueError("not an http(s) URL"))
        if self.follow_redirects:
            try:
                check_release_url(url, test_mode=self.test_mode)
            except RedirectRefused as error:
                raise TransportError(error) from None

    def _merge_headers(self, defaults: dict[str, str], headers: Mapping[str, str] | None) -> dict[str, str]:
        merged = dict(defaults)
        merged.update(headers or {})
        if self.follow_redirects:  # the updater has no token to send, whatever a caller passes
            merged = {key: value for key, value in merged.items() if key.lower() not in _CREDENTIAL_HEADERS}
        return merged

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: bytes | None = None, timeout: float) -> HttpResponse:
        self._check_first_url(url)
        merged = self._merge_headers(
            {"User-Agent": self.user_agent, "Accept": "application/json", "Accept-Encoding": "identity"}, headers)
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
                 headers: Mapping[str, str] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> tuple[int, int, bool]:
        """Streams ``url`` into a NEW file ``destination`` (0600, ``O_EXCL``), cutting the transfer off
        at ``limit`` bytes (counted on the FINAL response, after any redirects, and refused up front
        when its Content-Length already exceeds ``limit``). Returns ``(status, bytes written,
        exceeded)``; on a non-200 status or when the limit is passed the file is removed.
        ``should_stop`` is polled between chunks; when it answers True the transfer is abandoned with a
        :class:`TransportError`. Raises :class:`TransportError`."""
        self._check_first_url(url)
        merged = self._merge_headers({"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Encoding": "identity"},
                                     headers)
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
                read_some = getattr(response, "read1", response.read)
                declared = response.headers.get("Content-Length")
                if declared is not None and declared.strip().isdigit() and int(declared) > limit:
                    return 200, int(declared), True
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                created = True
                try:
                    while True:
                        if should_stop is not None and should_stop():
                            raise InterruptedError("stopped")
                        # read1: whatever has arrived (up to 64 KiB), so a slow server cannot hold the
                        # stop check for the time it takes to fill a megabyte.
                        chunk = read_some(min(1 << 16, limit + 1 - written))
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > limit:
                            break
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(fd, view):]
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
