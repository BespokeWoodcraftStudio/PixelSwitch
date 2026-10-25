"""The usage-request guard, an exact port of PixelSwitch/Services/UsageRequestGuard.swift
(1.9) plus UsageRequestError.swift.

Why it exists (2026-10-16, docs/investigations/2026-10-16-usage-429.md):
Anthropic answers roughly the fourth refused (401) call on one access token within
about an hour with 429 and ``Retry-After: 3600``, on ``/api/oauth/usage`` and
``/api/oauth/profile`` alike. PixelSwitch kept sending tokens it could know were
dead, so it earned those 429s itself. Healthy tokens were never refused. The rules:

* a. A token that got a 401 is never sent again until the account's stored or live
  token changes.
* b. A token whose ``expiresAt`` is known and is past, or within 60 seconds, is
  never sent. A missing ``expiresAt`` is allowed (rule a stops it after one 401).
* c. A 429 park holds only while the account still has the token it was earned on.
* d. After a 401 or 429 on a token, nothing else is sent with it for 20 minutes,
  however many accounts are being polled.
* e. An account the server refused a refresh grant for sends nothing until its
  token changes.
* f. The live login is never dropped: it waits for Claude Code, and is read the
  moment its token changes.

Pure logic: no network, no clock of its own (``now`` is POSIX seconds, passed in,
default ``time.time()``). One instance per process; it takes a lock because the
daemon consults it from more than one thread. Nothing here is ever persisted
except account ids flagged needs-reauth (the caller does that).

``decision`` / ``may_use`` / ``record_*`` correspond one to one with the Swift
methods of the same names (camelCase there, snake_case here).
"""
from __future__ import annotations

import enum
import errno
import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Hashable, Iterable

from .usage_model import swift_double

# --- fingerprint (UsageRequestGuard.swift:7-14) -------------------------------------


def fingerprint_of(access_token: str) -> str:
    """SHA-256 of the token's UTF-8 bytes, first 8 bytes as 16 lowercase hex characters."""
    return hashlib.sha256(access_token.encode("utf-8")).digest()[:8].hex()


def fingerprint_for_log(fingerprint: str) -> str:
    """The part of a fingerprint that may appear in a log line (first 8 hex)."""
    return fingerprint[:8]


# --- what the app may do (:17-34) ---------------------------------------------------


@dataclass(frozen=True)
class UsageDecision:
    """What the app may do with an account's access token right now.

    ``kind`` is one of ``request``, ``refreshFirst``, ``waitForClaudeCode``,
    ``needsReauth``, ``parked`` (with ``until``) or ``coolingDown`` (with ``until``).
    """

    kind: str
    until: float | None = None


REQUEST = UsageDecision("request")                      # send the usage request with this token
REFRESH_FIRST = UsageDecision("refreshFirst")           # a saved login that is expired or refused: renew, then ask
WAIT_FOR_CLAUDE_CODE = UsageDecision("waitForClaudeCode")  # the LIVE login is expired or refused: send nothing
NEEDS_REAUTH = UsageDecision("needsReauth")             # the grant is refused or lost: nothing until a new sign-in


def parked(until: float) -> UsageDecision:
    """The server answered 429 for this very token; leave it alone until then."""
    return UsageDecision("parked", until)


def cooling_down(until: float) -> UsageDecision:
    """A 401 or 429 was just seen for this very token; leave it alone for a while."""
    return UsageDecision("coolingDown", until)


@dataclass
class _Stamp:
    until: float
    fingerprint: str


@dataclass
class _State:
    #: The access token that got a 401 from /usage or /profile.
    rejected_fingerprint: str | None = None
    last_auth_failure: float | None = None
    park: _Stamp | None = None
    cooldown: _Stamp | None = None
    needs_reauth: bool = False
    #: The token the account held when it was flagged. None for a flag restored
    #: after a relaunch, which adopts the first token it sees.
    needs_reauth_fingerprint: str | None = None


def _now(now: float | None) -> float:
    return time.time() if now is None else now


class UsageRequestGuard:
    """:60-285. Decides, before any request carrying an OAuth access token is made,
    whether the token may be sent."""

    COOLDOWN = 20 * 60.0          # rule d
    EXPIRY_SLACK = 60.0           # rule b
    LONGEST_PARK = 3600.0
    SHORTEST_PARK = 60.0
    DEFAULT_PARK = 120.0          # the park when the server sent no Retry-After
    REFUSED_WITHOUT_ACCOUNT_LIMIT = 64

    def __init__(self, needs_reauth: Iterable[Hashable] = ()) -> None:
        self._lock = threading.RLock()
        self._states: dict[Hashable, _State] = {}
        #: Tokens refused where no account was known (a 401 on the live login
        #: before its owner was proven). Oldest forgotten first.
        self._refused_without_account: list[str] = []
        self.restore_needs_reauth(needs_reauth)

    # -- asking (:99-159) ------------------------------------------------------------

    def decision(self, account: Hashable, is_active: bool, login: Any, now: float | None = None) -> UsageDecision:
        """:103-128. May this account's token be sent now? Also forgets anything that
        was earned on a different token (rules a, c, d and e all end with a new
        token). ``login`` needs ``access_token`` and ``expires_at`` (milliseconds)."""
        now = _now(now)
        with self._lock:
            fp = fingerprint_of(login.access_token)
            state = self._states.get(account) or _State()
            # The live login is Claude Code's own, never the saved login a flag was
            # raised about. A flag that holds no token must not adopt the live token as
            # the dead one: it goes, and the live login is judged on its own.
            if is_active and state.needs_reauth and state.needs_reauth_fingerprint is None:
                state.needs_reauth = False
            self._drop_what_is_not_about_this_token(state, fp)
            self._states[account] = state

            if state.needs_reauth:
                return NEEDS_REAUTH
            is_dead = (
                self.is_expired(login.expires_at, now)
                or state.rejected_fingerprint == fp
                or fp in self._refused_without_account
            )
            # The live login that is expired or was refused waits for Claude Code,
            # whatever else is known about it (the honest message is "waiting").
            if is_active and is_dead:
                return WAIT_FOR_CLAUDE_CODE
            if state.park is not None and state.park.until > now:
                return parked(state.park.until)
            if state.cooldown is not None and state.cooldown.until > now:
                return cooling_down(state.cooldown.until)
            # A saved login that is expired or was refused is renewed first.
            if is_dead:
                return REFRESH_FIRST
            return REQUEST

    def may_use(self, access_token: str, expires_at: float | None = None, now: float | None = None) -> bool:
        """:133-144. May a token be sent to /api/oauth/profile (or /usage) by a caller
        that has no account to hand? True unless the token is expired, was refused,
        or is inside a park or cool-down earned on it by any account."""
        now = _now(now)
        with self._lock:
            if self.is_expired(expires_at, now):
                return False
            fp = fingerprint_of(access_token)
            if fp in self._refused_without_account:
                return False
            for state in self._states.values():
                if state.rejected_fingerprint == fp:
                    return False
                if state.park is not None and state.park.fingerprint == fp and state.park.until > now:
                    return False
                if state.cooldown is not None and state.cooldown.fingerprint == fp and state.cooldown.until > now:
                    return False
            return True

    def was_rejected(self, account: Hashable, access_token: str) -> bool:
        """:149-152. Whether this exact token already got a 401 for this account."""
        with self._lock:
            state = self._states.get(account)
            return state is not None and state.rejected_fingerprint == fingerprint_of(access_token)

    @classmethod
    def is_expired(cls, expires_at: float | None, now: float) -> bool:
        """:156-159. Rule b: an ``expiresAt`` (milliseconds since 1970) that is known
        and past, or within a minute. Missing means "not known to be expired"."""
        if expires_at is None:
            return False
        return expires_at / 1000 < now + cls.EXPIRY_SLACK

    # -- recording what the server said (:161-195) -----------------------------------

    def record_rejected(self, account: Hashable | None, access_token: str, now: float | None = None) -> None:
        """:164-180. The server answered 401 for this exact token, from /usage or /profile."""
        now = _now(now)
        with self._lock:
            fp = fingerprint_of(access_token)
            if account is None:
                if fp not in self._refused_without_account:
                    self._refused_without_account.append(fp)
                    if len(self._refused_without_account) > self.REFUSED_WITHOUT_ACCOUNT_LIMIT:
                        self._refused_without_account.pop(0)
                return
            state = self._states.get(account) or _State()
            self._drop_what_is_not_about_this_token(state, fp)
            state.rejected_fingerprint = fp
            state.last_auth_failure = now
            state.cooldown = _Stamp(now + self.COOLDOWN, fp)
            self._states[account] = state

    def record_rate_limited(
        self, account: Hashable, access_token: str, retry_after: float | None, now: float | None = None
    ) -> float:
        """:184-195. The server answered 429 for this exact token. Returns the park's end."""
        now = _now(now)
        with self._lock:
            fp = fingerprint_of(access_token)
            state = self._states.get(account) or _State()
            self._drop_what_is_not_about_this_token(state, fp)
            park_for = min(max(self.DEFAULT_PARK if retry_after is None else retry_after, self.SHORTEST_PARK),
                           self.LONGEST_PARK)
            until = now + park_for
            state.park = _Stamp(until, fp)
            state.cooldown = _Stamp(now + self.COOLDOWN, fp)
            self._states[account] = state
            return until

    def mark_needs_reauth(self, account: Hashable, access_token: str | None = None) -> None:
        """:200-206. The server refused this account's refresh grant (``invalid_grant``),
        or its saved login is lost or was someone else's. ``access_token`` is the one
        the account holds now, so a different token later clears the flag."""
        with self._lock:
            state = self._states.get(account) or _State()
            state.needs_reauth = True
            state.needs_reauth_fingerprint = fingerprint_of(access_token) if access_token is not None else None
            self._states[account] = state

    def credential_changed(self, account: Hashable, access_token: str | None) -> bool:
        """:212-227. The account's stored or live credential changed (a sign-in, a
        renewal, a switch). Forgets everything earned on any other token; returns
        whether anything was forgotten. The SAME token does not lift its park."""
        with self._lock:
            state = self._states.get(account)
            if state is None:
                return False
            before = self._snapshot(state)
            if access_token is not None:
                # A flag restored without a token has nothing to compare; the caller
                # says the credential changed, so it goes.
                if state.needs_reauth and state.needs_reauth_fingerprint is None:
                    state.needs_reauth = False
                self._drop_what_is_not_about_this_token(state, fingerprint_of(access_token))
            else:
                state = _State()
            self._states[account] = state
            return before != self._snapshot(state)

    def forget(self, account: Hashable) -> None:
        """:230-233. Drops everything about a removed account."""
        with self._lock:
            self._states.pop(account, None)

    # -- needsReauth, persisted by the caller (:235-262) -----------------------------

    def is_needs_reauth(self, account: Hashable) -> bool:
        with self._lock:
            state = self._states.get(account)
            return state is not None and state.needs_reauth

    @property
    def needs_reauth_accounts(self) -> set[Hashable]:
        """The accounts to persist (ids only)."""
        with self._lock:
            return {account for account, state in self._states.items() if state.needs_reauth}

    def restore_needs_reauth(self, accounts: Iterable[Hashable]) -> None:
        """:249-256. Re-applies a persisted set after a relaunch."""
        with self._lock:
            for account in accounts:
                state = self._states.get(account) or _State()
                state.needs_reauth = True
                self._states[account] = state

    def last_auth_failure(self, account: Hashable) -> float | None:
        with self._lock:
            state = self._states.get(account)
            return state.last_auth_failure if state is not None else None

    # -- internals (:264-284) --------------------------------------------------------

    @staticmethod
    def _snapshot(state: _State) -> tuple:
        return (
            state.rejected_fingerprint,
            state.park.fingerprint if state.park else None,
            state.cooldown.fingerprint if state.cooldown else None,
            state.needs_reauth,
        )

    @staticmethod
    def _drop_what_is_not_about_this_token(state: _State, fingerprint: str) -> None:
        """Rules a, c, d, e: whatever was earned on another token no longer applies."""
        if state.rejected_fingerprint is not None and state.rejected_fingerprint != fingerprint:
            state.rejected_fingerprint = None
            state.last_auth_failure = None
        if state.park is not None and state.park.fingerprint != fingerprint:
            state.park = None
        if state.cooldown is not None and state.cooldown.fingerprint != fingerprint:
            state.cooldown = None
        if state.needs_reauth:
            if state.needs_reauth_fingerprint is not None:
                if state.needs_reauth_fingerprint != fingerprint:
                    state.needs_reauth = False
                    state.needs_reauth_fingerprint = None
            else:
                state.needs_reauth_fingerprint = fingerprint


# --- what a card says while the app is NOT asking (:287-327) ------------------------


class NoticeCurrent(str, enum.Enum):
    """What the card says now, for ``choose_notice``."""

    NOTHING = "nothing"
    #: A message that says nothing about the login itself (paused, waiting, a server or
    #: network error, a retry hint).
    TRANSIENT = "transient"
    #: The re-sign message, or "its saved login belonged to another account".
    RESIGN = "resign"
    NO_SUBSCRIPTION = "noSubscription"


@dataclass(frozen=True)
class UsageNotice:
    """``keepCurrent``, ``paused`` (with ``until``), ``waitingForClaudeCode`` or ``resign``."""

    kind: str
    until: float | None = None


KEEP_CURRENT = UsageNotice("keepCurrent")
WAITING_FOR_CLAUDE_CODE = UsageNotice("waitingForClaudeCode")
RESIGN = UsageNotice("resign")


def paused(until: float) -> UsageNotice:
    return UsageNotice("paused", until)


def choose_notice(decision: UsageDecision, current: NoticeCurrent) -> UsageNotice:
    """:312-326. The honest message for a decision that sends nothing. An expired or
    re-sign message is never overwritten by a pause (the 2026-10-16 banner that said
    "API Rate Limited" over "Session expired")."""
    keeps_what_it_says = current in (NoticeCurrent.RESIGN, NoticeCurrent.NO_SUBSCRIPTION)
    if decision.kind == "needsReauth":
        # Say it once; a message already saying so (it may be the more specific "its
        # saved login belonged to another account") stays.
        return KEEP_CURRENT if current == NoticeCurrent.RESIGN else RESIGN
    if decision.kind == "parked":
        assert decision.until is not None
        return KEEP_CURRENT if keeps_what_it_says else paused(decision.until)
    if decision.kind == "waitForClaudeCode":
        return KEEP_CURRENT if keeps_what_it_says else WAITING_FOR_CLAUDE_CODE
    return KEEP_CURRENT  # coolingDown, request, refreshFirst


# --- how renewing a saved login ended (:329-380) ------------------------------------


@dataclass(frozen=True)
class BackupRefreshOutcome:
    """How renewing a saved login in place ended, and what that says about the account.

    ``kind``: ``refreshed`` (``detail`` = the new credential JSON), ``grantRejected``,
    ``sameGrantAsAnotherLogin``, ``unreachable``, ``noBackup``, ``storeUnavailable``,
    ``belongsToAnotherAccount`` (``detail`` = that account's email), ``rotationLost``.
    """

    kind: str
    detail: str | None = None

    @property
    def requires_reauth(self) -> bool:
        """Whether the account must sign in again, so nothing more is sent for it.
        Only the server's refusal, a lost rotation, and a login that was someone
        else's. Never PixelSwitch's own same-grant guard."""
        return self.kind in ("grantRejected", "rotationLost", "belongsToAnotherAccount")

    @staticmethod
    def local_refusal(is_live: bool, shares_lineage: bool) -> "BackupRefreshOutcome | None":
        """:377-379. PixelSwitch's own refusal to spend a refresh token, decided before
        any request: the saved login is the same grant as the live login or as another
        account's. Never a server verdict, so never ``requires_reauth``."""
        return SAME_GRANT_AS_ANOTHER_LOGIN if (is_live or shares_lineage) else None


def refreshed(credential: str) -> BackupRefreshOutcome:
    return BackupRefreshOutcome("refreshed", credential)


def belongs_to_another_account(email: str) -> BackupRefreshOutcome:
    return BackupRefreshOutcome("belongsToAnotherAccount", email)


GRANT_REJECTED = BackupRefreshOutcome("grantRejected")
SAME_GRANT_AS_ANOTHER_LOGIN = BackupRefreshOutcome("sameGrantAsAnotherLogin")
UNREACHABLE = BackupRefreshOutcome("unreachable")
NO_BACKUP = BackupRefreshOutcome("noBackup")
STORE_UNAVAILABLE = BackupRefreshOutcome("storeUnavailable")
ROTATION_LOST = BackupRefreshOutcome("rotationLost")


# --- why a usage request failed (UsageRequestError.swift) ---------------------------

STATUS_PAGE = "https://status.claude.com"

_OFFLINE_ERRNOS = {
    errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,        # notConnectedToInternet
    errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE,            # networkConnectionLost
}


@dataclass(eq=True)
class UsageRequestError(Exception):
    """Why a usage request (``/api/oauth/usage``) failed, in words the account's card
    can show as they are, and what the app does about it.

    ``kind``: ``expired`` (401, or a body naming ``token_expired``), ``network``
    (``offline`` tells no connection from other failures), ``server`` (5xx, with
    ``status``), ``unexpected`` (any other status), ``decode`` (a 200 whose body could
    not be read), ``rateLimited`` (with ``retry_after``), ``forbidden`` (403, with the
    ``body``: no active subscription).
    """

    kind: str
    status: int = 0
    offline: bool = False
    retry_after: float | None = None
    body: str = ""

    # constructors ---------------------------------------------------------------

    @classmethod
    def from_status(cls, status: int, body: str, retry_after: str | None) -> "UsageRequestError":
        """:28-36. The error for a reply other than 200. Order matters: a 401, or ANY
        status whose body contains ``token_expired``, is ``expired`` first."""
        if status == 401 or "token_expired" in body:
            return cls("expired")
        if status == 429:
            return cls("rateLimited", retry_after=swift_double(retry_after))
        if status == 403:
            return cls("forbidden", body=body)
        if 500 <= status <= 599:
            return cls("server", status=status)
        return cls("unexpected", status=status)

    @classmethod
    def from_transport_error(cls, error: BaseException) -> "UsageRequestError":
        """:39-43. The error for a request that got no reply at all. On Linux the Mac's
        URLError codes map to errno values: no route or connection lost -> offline;
        a timeout, DNS or TLS failure -> not offline."""
        cause: Any = error
        reason = getattr(cause, "reason", None)
        if isinstance(reason, BaseException):
            cause = reason
        number = getattr(cause, "errno", None)
        return cls("network", offline=number in _OFFLINE_ERRNOS)

    @classmethod
    def decode_failure(cls, why: str = "") -> "UsageRequestError":
        return cls("decode", body=why)

    # behaviour ------------------------------------------------------------------

    @property
    def keeps_last_reading(self) -> bool:
        """Whether the account's last reading stays on its card (a 5xx or a network failure)."""
        return self.kind in ("server", "network")

    @property
    def can_retry(self) -> bool:
        """Whether the card offers Retry. Not when only signing in again helps (expired,
        no subscription), nor for a rate limit, whose deadline the app waits out."""
        return self.kind in ("server", "network", "unexpected", "decode")

    @property
    def offers_status_page(self) -> bool:
        return self.kind == "server"

    def message(self, now: float | None = None) -> str:
        """The sentence the account's card shows (English)."""
        if self.kind == "expired":
            return "Session expired."
        if self.kind == "network":
            if self.offline:
                return "Can't reach Anthropic. Check your internet connection; PixelSwitch keeps trying."
            return "Can't reach Anthropic right now; PixelSwitch keeps trying."
        if self.kind == "server":
            return (f"Anthropic's usage service isn't responding (HTTP {self.status}). "
                    "Your account is fine; PixelSwitch keeps trying.")
        if self.kind == "unexpected":
            return f"Unexpected reply from Anthropic (HTTP {self.status})."
        if self.kind == "decode":
            return "Anthropic sent usage data PixelSwitch couldn't read."
        if self.kind == "rateLimited":
            # Never "API Rate Limited. Try again later.": a 429 on the usage lookup says
            # nothing about Claude Code, and the card says when the app will ask again.
            wait = min(max(UsageRequestGuard.DEFAULT_PARK if self.retry_after is None else self.retry_after,
                           UsageRequestGuard.SHORTEST_PARK), UsageRequestGuard.LONGEST_PARK)
            return paused_message(_now(now) + wait)
        return "No active subscription on this account (OAuth not allowed)."

    def __str__(self) -> str:
        return self.message()


def paused_message(until: float, tz: Any = None) -> str:
    """:97-100. What the card says while the app waits out a 429 on the usage lookup;
    ``until`` is shown in the reader's local short time (``3:45 PM``)."""
    moment = datetime.fromtimestamp(until, tz) if tz is not None else datetime.fromtimestamp(until)
    hour12 = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"Usage reading paused until {hour12}:{moment.minute:02d} {suffix}. Claude Code itself is not affected."
