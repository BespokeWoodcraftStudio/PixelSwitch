"""The pure decisions behind step 1 of a switch (save the live login under its owner) and the
re-read before the write (spec 5.3): a port of ``PixelSwitch/Services/LiveLoginBackup.swift``.

``Coordinator._switch_account`` gathers the facts (what the login file returned, what the API
proved, whether the store took the save) and asks these functions what they mean, so the rules are
testable without a file or a network.

The rule they exist for: a read that returned nothing, or nothing usable, never counts as
"saved". Only a credential that is there, parses, and holds a blanked login counts as "nothing
worth saving" (spec D12), because Claude Code leaves the file in place when it blanks a dead login.
"""
from __future__ import annotations

from dataclasses import dataclass

from .logins import Login, live_login_changed, loads_object, login_problem, same_grant

# --- NotSavedReason: why the live login was not saved under its owner (logged, never shown) ----------

UNREADABLE = "unreadable"          # the read returned nothing, or text that is not a credential object
NO_OWNER = "noOwner"               # no account is the proven owner, so nothing may be saved for anyone
UNPROVEN = "unproven"              # the API could not say the login is still the owner's
IDENTITY_MISMATCH = "identityMismatch"   # no identity details on record match the owner
STORE_REFUSED = "storeRefused"     # the backup store refused or failed the write


@dataclass(frozen=True)
class BackupResult:
    """What step 1 came to: ``saved``, ``nothingWorthSaving`` or ``notSaved`` (with a reason)."""

    kind: str
    reason: str | None = None

    @property
    def is_settled(self) -> bool:
        """True when nothing that matters was lost by not saving."""
        return self.kind in ("saved", "nothingWorthSaving")

    def __str__(self) -> str:
        return self.kind if self.reason is None else f"{self.kind}({self.reason})"


SAVED = BackupResult("saved")
NOTHING_WORTH_SAVING = BackupResult("nothingWorthSaving")


def not_saved(reason: str) -> BackupResult:
    return BackupResult("notSaved", reason)


def is_credential_object(text: "str | None") -> bool:
    """True when ``text`` parses as a JSON object (whatever it holds). Text that is not a
    credential at all, or a partial read, is false."""
    return text is not None and loads_object(text) is not None


def result(fresh: "str | None", *, owner_known: bool, proven: bool, identity_matches: bool,
           store_accepted: bool) -> BackupResult:
    """The outcome of step 1 from the facts, in the order they apply (LiveLoginBackup.result)."""
    if fresh is None or not is_credential_object(fresh):
        return not_saved(UNREADABLE)
    if login_problem(fresh) is not None:
        return NOTHING_WORTH_SAVING
    if not owner_known:
        return not_saved(NO_OWNER)
    if not proven:
        return not_saved(UNPROVEN)
    if not identity_matches:
        return not_saved(IDENTITY_MISMATCH)
    return SAVED if store_accepted else not_saved(STORE_REFUSED)


# --- the re-read before the write -------------------------------------------------------------------


@dataclass(frozen=True)
class Abort:
    """Why a switch stopped with nothing written: ``stillChanging`` (Claude Code is still renewing the
    login), ``unreadable`` (the live login was read a moment ago and cannot be read now) or
    ``notSaved`` (the renewed login could not be saved for its owner; ``reason`` says why)."""

    kind: str
    reason: str | None = None


STILL_CHANGING = Abort("stillChanging")
UNREADABLE_ABORT = Abort("unreadable")


@dataclass(frozen=True)
class FirstPassStep:
    """``proceed``, ``retry`` (the live login changed since step 1 saw it: run step 1 again, with
    proof) or ``abort`` (with ``abort`` set)."""

    kind: str
    abort: Abort | None = None


PROCEED = FirstPassStep("proceed")
RETRY = FirstPassStep("retry")


def after_first_pass(seen: "str | None", live_now: "str | None", owner_known: bool, step: BackupResult) -> FirstPassStep:
    """What to do after the first pass of step 1 and the re-read before the write.

    ``seen`` is the live credential step 1 looked at; ``live_now`` the one read immediately before
    the write. With a proven owner and nothing read at step 1 the login vanished or the read
    flaked: stop, because writing now could overwrite a login that is only unreadable. With the login
    unchanged and an owner known, a save that could not be proven (``unproven``) or that the store
    refused (``storeRefused``) means the owner's backup does not hold the newest login, so writing
    now would lose it: stop. A login that changed goes to ``retry`` first, which asks again with
    proof. Other reasons (``noOwner``, ``identityMismatch``, ``unreadable``) never stop an unchanged
    login here."""
    if owner_known and seen is None:
        return FirstPassStep("abort", UNREADABLE_ABORT)
    if live_login_changed(seen, live_now):
        return RETRY
    if owner_known and step.kind == "notSaved" and step.reason in (UNPROVEN, STORE_REFUSED):
        return FirstPassStep("abort", Abort("notSaved", step.reason))
    return PROCEED


@dataclass(frozen=True)
class JudgedLogin:
    """The live credential a switch with no active account judged disposable (``LiveLoginDisposal``,
    spec D20 revision 4) before it went ahead. ``credential`` is None when that read returned
    nothing: a None judged read is still a judgment, and any real login that shows up afterwards was
    never judged."""

    credential: str | None


def after_judged_login(judged: JudgedLogin | None, seen: "str | None", live_now: "str | None") -> Abort | None:
    """Binds the no-active-account decision to the login it judged. With no proven owner nothing is
    compared against a saved login, so without this the login that gets overwritten could be one
    that was never judged. ``still changing`` (nothing written) when the login step 1 looked at, or
    the one read just before the write, differs from the judged one. ``judged`` is None for every
    other switch, which keeps 1.9's rules as they were."""
    if judged is None:
        return None
    if live_login_changed(judged.credential, seen) or live_login_changed(judged.credential, live_now):
        return STILL_CHANGING
    return None


SAME_GRANT = "sameGrant"
NEEDS_API = "needsAPI"
NO_PROOF = "none"


def fresh_login_proof(verified: Login | None, fresh: Login | None) -> str:
    """How step 1 knows the login it is about to save is still the owner's: ``sameGrant`` (the fresh
    login is the same grant as the one the API already proved), ``needsAPI`` (it differs: Claude Code
    renewed it since, so the API must vouch for it) or ``none`` (nothing to compare)."""
    if verified is None or fresh is None:
        return NO_PROOF
    return SAME_GRANT if same_grant(verified, fresh) else NEEDS_API


def after_retry(first_seen: "str | None", seen: "str | None", step: BackupResult, live_now: "str | None",
                owner_known: bool) -> Abort | None:
    """The verdict after step 1 ran a second time; None means go ahead and write.

    Two reads of nothing agree with each other, but when the first pass saw a login this is an
    unreadable login, not a settled one."""
    if live_login_changed(seen, live_now):
        return STILL_CHANGING
    if live_now is None and first_seen is not None:
        return UNREADABLE_ABORT
    if owner_known and step.kind == "notSaved":
        return Abort("notSaved", step.reason)
    return None
