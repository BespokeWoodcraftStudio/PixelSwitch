"""Credentials: what counts as a usable login (spec D12), the credential merge a switch
writes, and the parsing used to tell whose grant a login is.

Ports: ``PixelSwitch/Services/ClaudeCredentialMerge.swift`` and
``PixelSwitch/Services/CredentialOwnership.swift`` (Login, sameGrant, lineageMatches,
sharedLogins, detailsBelong). Pure: strings and dicts in, values out. No function here
reads a file, and no message or exception text ever carries a token.

"token" in this code means the WHOLE credential JSON, as it does in the Swift (it holds
``claudeAiOauth`` and may hold ``mcpOAuth`` and anything else Claude Code stored).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .protocol import trim_whitespace_and_newlines
from .usage_model import is_number


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def loads_object(text: "str | bytes | bytearray | None") -> dict | None:
    """The JSON object in ``text``, or None when it is not one (a JSON array, a scalar,
    non-JSON, or JSON with NaN / Infinity, which Foundation refuses too)."""
    if text is None:
        return None
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _as_object(credential: "str | bytes | Mapping | None") -> dict | None:
    if isinstance(credential, Mapping):
        return dict(credential)
    return loads_object(credential)


# --- D12: the usable-login rule ----------------------------------------------------------


def login_problem(credential: "str | bytes | Mapping | None") -> str | None:
    """Why ``credential`` is NOT a usable login, as a short fixed phrase, or None when it is.

    Spec D12: a credential is a usable login only if it parses as a JSON object whose
    ``claudeAiOauth`` is an object with ``accessToken`` a non-empty string, ``refreshToken``
    a non-empty string, and ``expiresAt`` (when present) a number greater than 0. Missing,
    null or wrongly typed values count as blank: an ``expiresAt`` that is present but null
    or not a number is therefore NOT usable (only an absent one is allowed). A bool is not
    a number. A token made only of whitespace or newlines is blank too, exactly as the Mac's
    ``CredentialOwnership.unusableReason`` trims ``.whitespacesAndNewlines`` (lane LB fix,
    2026-10-24). The phrases name the field, never a value.
    """
    obj = _as_object(credential)
    if obj is None:
        return "the credential is not a JSON object"
    oauth = obj.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return "the credential has no claudeAiOauth object"
    access = oauth.get("accessToken")
    if not isinstance(access, str) or trim_whitespace_and_newlines(access) == "":
        return "accessToken is missing or blank"
    refresh = oauth.get("refreshToken")
    if not isinstance(refresh, str) or trim_whitespace_and_newlines(refresh) == "":
        return "refreshToken is missing or blank"
    if "expiresAt" in oauth:
        expires = oauth["expiresAt"]
        if not is_number(expires) or not math.isfinite(float(expires)) or not float(expires) > 0:
            return "expiresAt is not a positive number"
    return None


def usable_login(credential: "str | bytes | Mapping | None") -> bool:
    """The usable-login rule of spec D12 as one pure function.

    It is the one test behind: the store-save function (a credential that is not a usable
    login is never saved), target validation before a switch (never written to Claude Code's
    live store; the re-sign message instead) and ``isSwitchable`` (not switchable).
    """
    return login_problem(credential) is None


# --- CredentialOwnership.swift -------------------------------------------------------------


@dataclass(frozen=True)
class Login:
    """:12-27. The account-identifying part of a Claude Code credential."""

    access_token: str
    refresh_token: str | None = None
    #: Access-token expiry in milliseconds since 1970, as Claude Code stores it.
    expires_at: float | None = None

    def is_expired(self, now: float | None = None) -> bool:
        """True when the access token has expired or will within a minute."""
        if self.expires_at is None:
            return False
        return self.expires_at / 1000 < (time.time() if now is None else now) + 60


def login_from_credential(credential: "str | bytes | Mapping | None") -> Login | None:
    """:30-37. The ``claudeAiOauth`` login inside a credential JSON, or None.

    ``accessToken`` must be a non-empty string; an empty ``refreshToken`` reads as absent;
    ``expiresAt`` is read as a number (Foundation's ``NSNumber``, so a JSON boolean reads
    as 1 or 0, exactly as the Swift does). This is NOT the usable-login rule: it is how a
    credential is matched to a grant.
    """
    obj = _as_object(credential)
    if obj is None:
        return None
    oauth = obj.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    access = oauth.get("accessToken")
    if not isinstance(access, str) or access == "":
        return None
    refresh = oauth.get("refreshToken")
    refresh_token = refresh if isinstance(refresh, str) and refresh != "" else None
    expires = oauth.get("expiresAt")
    expires_at: float | None = None
    if isinstance(expires, bool):
        expires_at = 1.0 if expires else 0.0
    elif is_number(expires) and math.isfinite(float(expires)):
        expires_at = float(expires)
    return Login(access, refresh_token, expires_at)


def same_grant(a: Login, b: Login) -> bool:
    """:43-47. True when two logins come from the same OAuth grant: the same refresh token,
    or the same access token. A refreshed login shares neither with its older self once the
    refresh token rotates, so a miss is "unknown", never "different"."""
    if a.access_token == b.access_token:
        return True
    return a.refresh_token is not None and b.refresh_token is not None and a.refresh_token == b.refresh_token


def lineage_matches(login: Login, saved: Mapping[str, Login]) -> list[str]:
    """:50-52. Ids of the saved logins that are the same grant as ``login``, sorted."""
    return sorted(key for key, other in saved.items() if same_grant(other, login))


def shared_logins(saved: Mapping[str, Login]) -> list[list[str]]:
    """:56-67. Groups of account ids whose saved logins are the same grant. Every group
    holds at most one account's real login; the others are wrong."""
    groups: list[list[str]] = []
    assigned: set[str] = set()
    for key in sorted(saved):
        if key in assigned:
            continue
        group = lineage_matches(saved[key], saved)
        if len(group) > 1:
            groups.append(group)
            assigned.update(group)
    return groups


def details_belong(uuid: str | None, email: str | None, owner_uuid: str | None, owner_email: str | None) -> bool:
    """:81-85. Whether saved account details (an ``oauthAccount`` block's ``accountUuid`` and
    ``emailAddress``) describe the proven owner of a token. The permanent id decides when
    both sides have one; otherwise the email does. Missing information on both sides is
    never a match."""
    if uuid is not None and owner_uuid is not None:
        return uuid == owner_uuid
    if email is not None and owner_email is not None:
        return email.casefold() == owner_email.casefold()
    return False


def same_oauth_tokens(a: "str | bytes | Mapping | None", b: "str | bytes | Mapping | None") -> bool:
    """Spec 5.3 "re-read before write": do two credentials hold the same
    ``claudeAiOauth.accessToken`` AND ``refreshToken``? Anything unreadable on either side
    is a difference (so the caller does not write)."""
    left, right = login_from_credential(a), login_from_credential(b)
    if left is None or right is None:
        return False
    return left.access_token == right.access_token and left.refresh_token == right.refresh_token


# --- ClaudeCredentialMerge.swift -------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    """``merged``: the target's ``claudeAiOauth`` over the live item's other keys
    (``kept_keys`` sorted). ``targetOnly``: could not merge, the target verbatim (the
    behaviour before 1.0.2), with a ``reason``."""

    kind: str
    credential: str
    kept_keys: tuple[str, ...] = ()
    reason: str = ""


def credential_for_switch(live: "str | bytes | None", target: str) -> MergeResult:
    """:20-36. Builds the credential PixelSwitch writes on a switch.

    Claude Code keeps two unrelated things in one credential: ``claudeAiOauth``, the Claude
    account login, and ``mcpOAuth``, the logins for every MCP server on the machine. Only
    the first belongs to an account. A switch takes ``claudeAiOauth`` from the target
    account's backup and keeps every other key from the live item, so MCP logins made or
    renewed since that backup was taken are not replaced by older copies.
    """
    target_object = loads_object(target)
    target_login = target_object.get("claudeAiOauth") if target_object is not None else None
    if not isinstance(target_login, dict):
        return MergeResult("targetOnly", target, reason="target backup has no readable claudeAiOauth")
    live_object = loads_object(live) if live is not None else None
    if live_object is None:
        return MergeResult("targetOnly", target, reason="live credential missing or not JSON")
    live_object["claudeAiOauth"] = target_login
    try:
        merged = json.dumps(live_object, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return MergeResult("targetOnly", target, reason="merged credential did not serialize")
    kept = tuple(sorted(key for key in live_object if key != "claudeAiOauth"))
    return MergeResult("merged", merged, kept)


def credential_keys(credential: "str | bytes | Mapping | None") -> Iterable[str]:
    """The top-level keys of a credential (for logs: key names only, never values)."""
    obj = _as_object(credential)
    return sorted(obj) if obj is not None else []
