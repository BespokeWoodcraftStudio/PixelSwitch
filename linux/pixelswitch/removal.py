"""The pure rules behind removing an account and moving Claude Code off it (spec D20), the
"already live" shortcut of a switch, and what a switch does when no account is active: a port of
``PixelSwitch/Services/AccountRemoval.swift``.

``Coordinator`` applies them; the unit tests run them without a daemon.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .logins import Login, login_from_credential, same_grant
from .usage_guard import fingerprint_of

# --- RemovalFallback: try the candidates in order, stop when the situation no longer holds -------------


@dataclass(frozen=True)
class Attempt:
    """What one try of one candidate came to: ``switched``, ``busy`` (another switch or a sign-in is
    running; nothing was tried), ``failed`` (tried, or refused before writing; ``message`` is the
    sentence the user would be shown, when there is one) or ``gone`` (no longer on the list)."""

    kind: str
    message: str | None = None


SWITCHED = Attempt("switched")
GONE = Attempt("gone")


def busy(message: str) -> Attempt:
    return Attempt("busy", message)


def failed(message: str | None) -> Attempt:
    return Attempt("failed", message)


@dataclass(frozen=True)
class FallbackResult:
    """How the loop ended: ``switched`` (``account_id``), ``stoodDown`` (something else chose an
    account, or emptied the list, while the loop ran), ``busyGaveUp`` (a switch or sign-in kept
    running for the whole wait; ``message`` is its sentence) or ``noneWorked`` (``message`` is the
    last failure's sentence, if any)."""

    kind: str
    account_id: str | None = None
    message: str | None = None


STOOD_DOWN = FallbackResult("stoodDown")

#: How many one-second waits a busy switch or sign-in is given in all. A sign-in can take minutes
#: in the browser, and nothing is lost by waiting.
DEFAULT_MAX_BUSY_WAITS = 150


def run_fallback(
    candidates: Sequence[str],
    *,
    still_needed: Callable[[], bool],
    attempt: Callable[[str], Attempt],
    pause: Callable[[], None],
    note: Callable[[str], None],
    max_busy_waits: int = DEFAULT_MAX_BUSY_WAITS,
) -> FallbackResult:
    """``RemovalFallback.run``. ``still_needed`` is checked before every candidate and after every
    wait; false as soon as anything else made an account active or emptied the list."""
    last_failure: str | None = None
    busy_waits = 0
    for account_id in candidates:
        while True:
            if not still_needed():
                note("Another account became active (or none are left); stopping the fallback")
                return STOOD_DOWN
            outcome = attempt(account_id)
            if outcome.kind == "switched":
                return FallbackResult("switched", account_id)
            if outcome.kind == "gone":
                note(f"Candidate {account_id} is no longer on the list; skipping it")
                break
            if outcome.kind == "failed":
                if outcome.message:
                    last_failure = outcome.message
                note(f"Switch to {account_id} failed ({outcome.message or 'no reason given'}); trying the next account")
                break
            busy_waits += 1
            if busy_waits > max_busy_waits:
                note(f"A switch or sign-in is still running after {max_busy_waits} s; giving up without trying "
                     "anything or clearing the active account")
                return FallbackResult("busyGaveUp", message=outcome.message)
            pause()
    if not still_needed():
        note("Another account became active (or none are left); nothing more to do")
        return STOOD_DOWN
    return FallbackResult("noneWorked", message=last_failure)


# --- the "already live" shortcut ---------------------------------------------------------------------------


def already_live(live_owner_id: str | None, believed_active_id: str | None, target_id: str) -> bool:
    """SwitchShortcut.alreadyLive: the live login is proven to be the target's, or it is unproven and
    the target is the account believed active. With no active account and no proven owner nothing is
    "already live" here; :func:`decide_disposal` decides first whether the switch may go ahead."""
    if live_owner_id is not None:
        return live_owner_id == target_id
    return believed_active_id == target_id


# --- LiveLoginDisposal ------------------------------------------------------------------------------------------

#: What the API said about the live access token.
COULD_NOT_SAY = "couldNotSay"                          # expired, parked or refused token, network
NAMES_NO_REMAINING_ACCOUNT = "namesNoRemainingAccount"  # an identity that names nobody on the list
NAMES_REMAINING_ACCOUNT = "namesRemainingAccount"        # names a remaining account without settling on exactly one

#: Why going ahead is safe.
NOTHING_TO_LOSE = "nothingToLose"
API_NAMES_NO_REMAINING_ACCOUNT = "apiNamesNoRemainingAccount"
SAME_GRANT_AS_REMOVED_ACCOUNT = "sameGrantAsRemovedAccount"

#: The sentence shown when the switch is refused.
REFUSAL_SENTENCE = "PixelSwitch cannot tell whose login Claude Code holds right now. Use Claude Code once, then try again."


@dataclass(frozen=True)
class Disposal:
    """``proceed`` (the login being replaced is disposable; ``basis`` says why), ``treatAsActive``
    (the login is the same grant as exactly this remaining account's saved login: treat that account
    as the believed active one) or ``refuse``."""

    kind: str
    basis: str | None = None
    account_id: str | None = None

    def __str__(self) -> str:
        return self.kind + (f"({self.basis})" if self.basis else "") + (f"({self.account_id})" if self.account_id else "")


REFUSE = Disposal("refuse")


def decide_disposal(*, live_login_usable: bool, api: str, remaining_same_grant: "Sequence[str] | None",
                    same_grant_as_removed_login: bool) -> Disposal:
    """``LiveLoginDisposal.decide`` (spec D20 revision 4), in the same order. The live login is
    overwritten without being saved anywhere, so it may only be, in order of certainty: absent or
    blanked (nothing to lose); named by the API as someone who is not on the list; or the same grant
    as a removed account's login and as no remaining account's. The API naming a remaining account
    without settling on exactly one is contradictory data: refuse. Exactly one remaining account
    sharing the grant becomes the believed active one; several are ambiguous and refuse."""
    if not live_login_usable:
        return Disposal("proceed", NOTHING_TO_LOSE)
    if api == NAMES_NO_REMAINING_ACCOUNT:
        return Disposal("proceed", API_NAMES_NO_REMAINING_ACCOUNT)
    if api == NAMES_REMAINING_ACCOUNT:
        return REFUSE
    if remaining_same_grant is not None:
        if len(remaining_same_grant) == 1:
            return Disposal("treatAsActive", account_id=remaining_same_grant[0])
        if len(remaining_same_grant) == 0 and same_grant_as_removed_login:
            return Disposal("proceed", SAME_GRANT_AS_REMOVED_ACCOUNT)
    return REFUSE


# --- RemovedLogins -------------------------------------------------------------------------------------------------

#: The most fingerprints kept (a handful of removals, two or three logins each).
REMOVED_LOGINS_LIMIT = 16


def fingerprints_of_credentials(credentials: Sequence["str | None"]) -> list[str]:
    """Fingerprints of the refresh tokens in these credentials. One that is absent or has no
    refresh token adds nothing. Never a token."""
    out: list[str] = []
    for credential in credentials:
        login = login_from_credential(credential) if credential else None
        if login is not None and login.refresh_token is not None:
            out.append(fingerprint_of(login.refresh_token))
    return out


@dataclass(frozen=True)
class ProvenLiveLogin:
    """A live login the API proved belongs to one account, kept by that token's refresh-token
    fingerprint (never the token), in memory, from the last proven owner lookup."""

    account_id: str
    refresh_fingerprint: str


def proof_for(owner: str, credential: "str | None") -> ProvenLiveLogin | None:
    """The proof for ``credential`` being ``owner``'s; None if it has no refresh token."""
    login = login_from_credential(credential) if credential else None
    if login is None or login.refresh_token is None:
        return None
    return ProvenLiveLogin(owner, fingerprint_of(login.refresh_token))


def fingerprints_to_record(removed_account: str, saved_credential: "str | None", live_credential: "str | None",
                           proof: ProvenLiveLogin | None) -> list[str]:
    """What to remember when ``removed_account`` is removed: only logins that are provably its own.
    Its saved login's refresh token always. The live login's only when it is the same grant as that
    saved login, or the API proved it is the removed account's. Belief that the account was active is
    not proof: until the next lookup the live login can be another account's (one signed in by
    hand), and recording it as the removed account's would let a later switch discard it."""
    recorded = fingerprints_of_credentials([saved_credential])
    live = login_from_credential(live_credential) if live_credential else None
    if live is None or live.refresh_token is None:
        return recorded
    saved = login_from_credential(saved_credential) if saved_credential else None
    same_grant_as_saved = saved is not None and same_grant(saved, live)
    fingerprint = fingerprint_of(live.refresh_token)
    proven_removed = proof is not None and proof.account_id == removed_account and proof.refresh_fingerprint == fingerprint
    if (same_grant_as_saved or proven_removed) and fingerprint not in recorded:
        recorded.append(fingerprint)
    return recorded


def remembering(new: Sequence[str], existing: Sequence[str]) -> list[str]:
    """``existing`` plus ``new``, without repeats, the oldest dropped past the limit."""
    everything = list(existing)
    for fingerprint in new:
        if fingerprint not in everything:
            everything.append(fingerprint)
    return everything[-REMOVED_LOGINS_LIMIT:]


def is_removed_login(login: Login, fingerprints: Sequence[str]) -> bool:
    """Whether this live login is the same grant as a removed account's login: its refresh token is
    one that was recorded. (An access token is never recorded, so a renewed login that rotated its
    refresh token no longer matches; the API's answer covers that case.)"""
    return login.refresh_token is not None and fingerprint_of(login.refresh_token) in fingerprints
