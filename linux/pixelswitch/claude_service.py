"""The requests that carry a login, behind the usage guard exactly as the Mac's ``ClaudeService``
does them (``ClaudeService.swift:227-433``): usage, the profile lookup (whose login is this?)
and the refresh of a saved login.

Every request with an access token is gated by one :class:`UsageRequestGuard` and nothing here
can bypass it: the profile lookup asks ``may_use`` itself and records a 401 or a 429; the usage
request is gated by the coordinator's ``decision`` before it is made and records nothing itself
(the coordinator reads the error and records, as ``AppState.requestUsage`` does). A token that
was refused or has expired is never sent. Neither a token nor a response body is ever logged.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from . import fsio
from .api import (
    OAUTH_BETA, OAUTH_CLIENT_ID, PROFILE_TIMEOUT, TOKEN_TIMEOUT, USAGE_TIMEOUT, ApiBases, Transport, TransportError,
)
from .credtools import applying_renewal_expiry
from .logfile import CategoryLog
from .logins import loads_object
from .usage_guard import (
    UsageRequestError, UsageRequestGuard, fingerprint_for_log, fingerprint_of, parse_retry_after,
)
from .usage_model import UsageDecodeError, UsageResponse, is_number

#: ClaudeService.swift:394, the OAuth error codes that may appear in a log line.
KNOWN_OAUTH_ERRORS = ("invalid_grant", "invalid_request", "invalid_client", "unauthorized_client",
                      "unsupported_grant_type", "invalid_scope")


@dataclass(frozen=True)
class Identity:
    """Who an access token belongs to, per the API (ClaudeService.swift:282)."""

    uuid: str | None
    email: str | None


@dataclass(frozen=True)
class RefreshResult:
    """``success`` (``credential`` = the renewed credential JSON), ``rejected`` (the grant can
    never work: no refresh token stored, or ``invalid_grant``) or ``transient`` (says nothing
    about the token)."""

    kind: str
    credential: str | None = None


class ClaudeService:
    def __init__(self, transport: Transport, bases: ApiBases, guard: UsageRequestGuard, log: CategoryLog,
                 clock: Callable[[], float] = time.time) -> None:
        self._http = transport
        self._bases = bases
        self.guard = guard
        self._log = log
        self._clock = clock

    # -- usage (:234-277) ------------------------------------------------------------------------

    def get_usage_limits(self, access_token: str) -> UsageResponse:
        """``GET /api/oauth/usage``. Raises :class:`UsageRequestError`."""
        headers = {"Authorization": f"Bearer {access_token}", "anthropic-beta": OAUTH_BETA}
        try:
            response = self._http.request("GET", self._bases.usage, headers=headers, timeout=USAGE_TIMEOUT)
        except TransportError as error:
            self._log.error(f"[getUsageLimits] No reply: {error}")
            raise UsageRequestError.from_transport_error(error.cause) from None
        if response.status != 200:
            body = response.body.decode("utf-8", "replace")
            self._log.error(f"[getUsageLimits] HTTP {response.status}")
            failure = UsageRequestError.from_status(response.status, body, response.header("retry-after"))
            if failure.kind == "rateLimited":
                wait = "none" if failure.retry_after is None else f"{failure.retry_after:.0f}"
                self._log.warning(f"[getUsageLimits] 429, Retry-After: {wait}s")
            elif failure.kind == "forbidden":
                self._log.warning("[getUsageLimits] 403 permission error")
            raise failure
        try:
            usage = UsageResponse.from_json(fsio.loads_strict(response.body.decode("utf-8")))
        except (UsageDecodeError, ValueError, RecursionError, UnicodeDecodeError) as error:
            self._log.error(f"[getUsageLimits] Decode Error: {type(error).__name__}")
            raise UsageRequestError.decode_failure(type(error).__name__) from None
        fable = usage.model_weekly_limit("Fable")
        session = usage.five_hour.utilization if usage.five_hour and usage.five_hour.utilization is not None else -1
        weekly = usage.seven_day.utilization if usage.seven_day and usage.seven_day.utilization is not None else -1
        fable_percent = fable.percent if fable is not None and fable.percent is not None else -1
        self._log.info(f"[getUsageLimits] session={session}%, weekly={weekly}%, fable={fable_percent}%")
        return usage

    # -- whose login is this? (:297-334) ------------------------------------------------------------

    def account_identity(self, access_token: str, expires_at: float | None = None,
                         account: str | None = None) -> Identity | None:
        """The account an access token belongs to, from ``/api/oauth/profile``, or None if the
        token is expired, was refused, is cooling down, or the call fails. This is the only proof
        of ownership. A 401 or a 429 is recorded for ``account``, and against the token alone when
        ``account`` is None (the owner is not yet known): the guard keeps an account-less list for
        each, so a lookup with no active account cannot repeat (spec D20 revision 4)."""
        if not self.guard.may_use(access_token, expires_at, self._clock()):
            self._log.info(f"[accountIdentity] Not asking: token {fingerprint_for_log(fingerprint_of(access_token))} "
                           "is expired, was refused, or is cooling down")
            return None
        headers = {"Authorization": f"Bearer {access_token}", "anthropic-beta": OAUTH_BETA}
        try:
            response = self._http.request("GET", self._bases.profile, headers=headers, timeout=PROFILE_TIMEOUT)
        except TransportError as error:
            self._log.warning(f"[accountIdentity] Profile lookup failed: {error}")
            return None
        status = response.status
        if status == 401:
            self.guard.record_rejected(account, access_token, self._clock())
        elif status == 429:
            # Recorded whether or not an account is known (spec D20 revision 4,
            # ClaudeService.swift:313-318): a lookup with no active account must not be able to repeat.
            retry = parse_retry_after(response.header("retry-after"))
            self.guard.record_rate_limited(account, access_token, retry, self._clock())
        if status != 200:
            self._log.warning(f"[accountIdentity] Profile lookup failed (HTTP {status})")
            return None
        try:
            body = loads_object(response.body.decode("utf-8"))
        except UnicodeDecodeError:
            body = None
        profile = body.get("account") if body is not None else None
        if not isinstance(profile, dict):
            self._log.warning("[accountIdentity] Profile lookup failed (HTTP 200)")
            return None
        uuid = profile.get("uuid") if isinstance(profile.get("uuid"), str) else None
        email = profile.get("email") if isinstance(profile.get("email"), str) else None
        if email is None and isinstance(profile.get("email_address"), str):
            email = profile["email_address"]
        if uuid is None and email is None:
            self._log.warning("[accountIdentity] Profile response names no account")
            return None
        return Identity(uuid, email)

    # -- refreshing a saved login (:360-433) ------------------------------------------------------------

    def refresh_oauth_credentials(self, credentials_json: str) -> RefreshResult:
        """Refreshes a credential JSON with its refresh token by a direct POST to the OAuth token
        endpoint. Touches no live file: safe for a non-active account while Claude Code sessions
        work, because only PixelSwitch holds these saved logins (rotation cannot race anyone).

        The rotated refresh token is kept only if the response has a non-empty one; ``scopes`` is
        deliberately NOT rewritten from the response (the CLI decides it recognises a login by
        its stored ``scopes`` containing ``user:inference``); ``refreshTokenExpiresAt`` follows
        spec 5.3."""
        root = loads_object(credentials_json)
        oauth = root.get("claudeAiOauth") if root is not None else None
        refresh_token = oauth.get("refreshToken") if isinstance(oauth, dict) else None
        if root is None or not isinstance(oauth, dict) or not isinstance(refresh_token, str) or refresh_token == "":
            self._log.warning("[refreshOAuth] Stored credential has no refresh token")
            return RefreshResult("rejected")
        body = json.dumps({"grant_type": "refresh_token", "refresh_token": refresh_token,
                           "client_id": OAUTH_CLIENT_ID}, separators=(",", ":")).encode("utf-8")
        try:
            response = self._http.request("POST", self._bases.token, headers={"Content-Type": "application/json"},
                                          body=body, timeout=TOKEN_TIMEOUT)
        except TransportError as error:
            self._log.warning(f"[refreshOAuth] Refresh failed: {error}")
            return RefreshResult("transient")
        reply = None
        if response.status == 200 or response.body:
            try:
                reply = loads_object(response.body.decode("utf-8"))
            except UnicodeDecodeError:
                reply = None
        access = reply.get("access_token") if reply is not None else None
        expires_in = reply.get("expires_in") if reply is not None else None
        if response.status != 200 or not isinstance(access, str) or access.strip() == "" or not is_number(expires_in):
            # NEVER log the body: a 200 that failed our parse is a successful token response.
            oauth_error = reply.get("error") if reply is not None else None
            shown = ("unrecognized" if oauth_error not in KNOWN_OAUTH_ERRORS else oauth_error) \
                if isinstance(oauth_error, str) else "none"
            self._log.warning(f"[refreshOAuth] Refresh not applied (HTTP {response.status}, error={shown}, "
                              f"{len(response.body)} bytes)")
            if response.status == 200:
                return RefreshResult("transient")  # a 200 that failed OUR parse is a schema change
            return RefreshResult("rejected" if oauth_error == "invalid_grant" else "transient")
        now = self._clock()
        renewed = dict(oauth)
        renewed["accessToken"] = access
        renewed["expiresAt"] = int(now * 1000) + int(float(expires_in) * 1000)
        new_refresh = reply.get("refresh_token") if reply is not None else None
        if isinstance(new_refresh, str) and new_refresh != "":
            renewed["refreshToken"] = new_refresh
        renewed = applying_renewal_expiry(renewed, reply.get("refresh_token_expires_in"), now)
        root["claudeAiOauth"] = renewed
        hours, minutes = int(float(expires_in) // 3600), int((float(expires_in) % 3600) // 60)
        self._log.info(f"[refreshOAuth] Access token refreshed, valid for {hours}h {minutes}m")
        return RefreshResult("success", fsio.dumps_compact(root))
