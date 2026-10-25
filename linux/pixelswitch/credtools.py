"""Pure credential decisions the coordinator and the stores share. Ports the rest of
``CredentialOwnership.swift`` that lane LA's ``logins`` module does not hold: ``LoginGate`` (the
three places spec D12 is enforced), ``liveLoginChanged`` (the re-read-before-write compare, spec
5.3) and ``applyingRenewalExpiry`` (spec 5.3, "Renewal keeps ``refreshTokenExpiresAt`` honest").

No file is read here and no message or exception carries a token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .finite import finite_number
from .logins import live_login_changed, login_problem


def unusable_reason(credential: "str | None") -> str | None:
    """CredentialOwnership.swift:101. Why a credential is not a usable login (spec D12), or None."""
    return login_problem(credential)


def is_usable_login(credential: "str | None") -> bool:
    return unusable_reason(credential) is None


# --- LoginGate (:128-155) -------------------------------------------------------------------------


@dataclass(frozen=True)
class SaveDecision:
    allowed: bool
    reason: str = ""


def gate_save(token: str) -> SaveDecision:
    """(a) Whether a credential may be saved as an account's login. Asked by the one store-save function."""
    problem = unusable_reason(token)
    return SaveDecision(True) if problem is None else SaveDecision(False, problem)


def gate_target_problem(backup_token: str) -> str | None:
    """(b) The reason a saved login must not be written to Claude Code's live store, or None."""
    return unusable_reason(backup_token)


def gate_is_switchable(backup_token: str | None, needs_reauth: bool, card_says_login_expired: bool) -> bool:
    """(c) A saved login that is usable, whose refresh grant the server has not refused, and whose
    card does not say the login expired."""
    if backup_token is None or not is_usable_login(backup_token):
        return False
    return not needs_reauth and not card_says_login_expired


# --- re-read before write (:159-187) ------------------------------------------------------------------


# ``live_login_changed`` lives in ``logins`` (it is the one function the coordinator and
# ``same_oauth_tokens`` share) and is re-exported here under its original name.


# --- renewal keeps refreshTokenExpiresAt honest (:191-207) ---------------------------------------------------


def applying_renewal_expiry(oauth: Mapping[str, Any], refresh_token_expires_in: Any, now: float) -> dict:
    """``oauth`` (a credential's ``claudeAiOauth``) after a successful renewal. A positive
    ``refresh_token_expires_in`` in the response sets ``refreshTokenExpiresAt`` to now plus that,
    in milliseconds. A response without one removes a stored ``refreshTokenExpiresAt`` that is
    already in the past (Claude Code treats a past value as signed out, so a renewed login must
    not carry it). Nothing else changes; ``scopes`` is untouched."""
    result = dict(oauth)
    now_ms = now * 1000
    seconds = finite_number(refresh_token_expires_in)
    if seconds is not None and seconds > 0:
        result["refreshTokenExpiresAt"] = int(now_ms) + int(seconds * 1000)
    else:
        stored = finite_number(result.get("refreshTokenExpiresAt"))
        if stored is not None and stored <= now_ms:
            result.pop("refreshTokenExpiresAt", None)
    return result
