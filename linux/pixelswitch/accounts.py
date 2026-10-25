"""The account record, the account order and the priority helpers.

``Account`` carries the fields of PixelSwitch/Models/Account.swift plus the 2.0
additions (``sessionThreshold``, ``weeklyThreshold``, ``priority`` and
``prioritySetOrder``). It round-trips through JSON with unknown keys preserved,
so an older or newer build's extra fields are never lost on a save.

Order rules (spec D2, D3 and section 3): ``priority`` is a stored whole number
1-99, lower first; accounts with none come after every numbered one; among equal
numbers the account whose number was set most recently comes first (a higher
``prioritySetOrder``), otherwise the existing relative order. The accounts array
is always kept sorted by priority, so list order, the CLI position column and the
Priority strategy are one order.

Dates (``lastUsed``) are kept exactly as stored: the Mac writes a Swift ``Date`` as
seconds since 2001-01-01, and nothing in the core reads it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from .engine import (
    is_manual_only,
    normalized_account_threshold,
    round_half_away,
    split_single_threshold,
    window_value,
)
from .finite import finite_number

PRIORITY_RANGE = (1, 99)


class _Absent:
    """The type of :data:`ABSENT`."""

    def __repr__(self) -> str:
        return "ABSENT"

    def __bool__(self) -> bool:
        return False


#: Argument value meaning "this key was not given" (as opposed to ``None`` = clear).
ABSENT: Any = _Absent()

_KNOWN_KEYS = (
    "id", "email", "displayName", "provider", "orgName", "subscriptionType", "isActive", "lastUsed",
    "customLabel", "switchThreshold", "sessionThreshold", "weeklyThreshold", "priority", "prioritySetOrder",
)


class AccountsFormatError(ValueError):
    """accounts.json holds something that is not a list of account records."""


def _json_number(value: float) -> int | float:
    """Whole numbers are written without a fraction (``70``, not ``70.0``)."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    return value


def _opt_str(obj: Mapping[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) else None


def _opt_num(obj: Mapping[str, Any], key: str) -> float | None:
    return finite_number(obj.get(key))


def _opt_int(obj: Mapping[str, Any], key: str) -> int | None:
    value = obj.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


@dataclass(frozen=True)
class Account:
    """One saved account. Immutable: use ``dataclasses.replace`` to change one."""

    id: str
    email: str
    display_name: str = ""
    provider: str = "Claude Code"
    org_name: str | None = None
    subscription_type: str | None = None
    is_active: bool = False
    last_used: Any = None
    custom_label: str | None = None
    #: 0...100; 0 = Manual only; None = follow the default. Means "both windows".
    switch_threshold: float | None = None
    #: 1...100 or None = follow the next layer.
    session_threshold: float | None = None
    weekly_threshold: float | None = None
    #: Whole number 1...99 (lower is tried first) or None.
    priority: int | None = None
    #: Larger = the number was set more recently. Breaks ties between equal numbers.
    priority_set_order: int | None = None
    #: Keys this build does not know, kept as read.
    extras: dict = field(default_factory=dict, compare=True)

    # --- derived ---------------------------------------------------------------------

    @property
    def display_subscription_type(self) -> str | None:
        """Subscription type with the first letter capitalised ("max" -> "Max")."""
        if self.subscription_type is None:
            return None
        return self.subscription_type[:1].upper() + self.subscription_type[1:]

    @property
    def label(self) -> str | None:
        """The custom label when set and non-empty (the control API never sends an empty one)."""
        return self.custom_label if self.custom_label else None

    @property
    def manual_only(self) -> bool:
        return is_manual_only(self.switch_threshold)

    # --- JSON ------------------------------------------------------------------------

    @classmethod
    def from_json(cls, obj: Any) -> "Account":
        if not isinstance(obj, dict):
            raise AccountsFormatError("an account record is not an object")
        account_id = obj.get("id")
        email = obj.get("email")
        if not isinstance(account_id, str) or not account_id or not isinstance(email, str):
            raise AccountsFormatError("an account record needs an id and an email")
        active = obj.get("isActive")
        return cls(
            id=account_id,
            email=email,
            display_name=_opt_str(obj, "displayName") or email,
            provider=_opt_str(obj, "provider") or "Claude Code",
            org_name=_opt_str(obj, "orgName"),
            subscription_type=_opt_str(obj, "subscriptionType"),
            is_active=active if isinstance(active, bool) else False,
            last_used=obj.get("lastUsed"),
            custom_label=_opt_str(obj, "customLabel"),
            switch_threshold=_opt_num(obj, "switchThreshold"),
            session_threshold=_opt_num(obj, "sessionThreshold"),
            weekly_threshold=_opt_num(obj, "weeklyThreshold"),
            priority=_opt_int(obj, "priority"),
            priority_set_order=_opt_int(obj, "prioritySetOrder"),
            extras={key: value for key, value in obj.items() if key not in _KNOWN_KEYS},
        )

    def to_json(self) -> dict:
        """The JSON object for accounts.json: absent optionals are omitted, unknown keys kept."""
        out: dict[str, Any] = dict(self.extras)
        out["id"] = self.id
        out["email"] = self.email
        out["displayName"] = self.display_name or self.email
        out["provider"] = self.provider
        out["isActive"] = self.is_active
        optional = {
            "orgName": self.org_name,
            "subscriptionType": self.subscription_type,
            "lastUsed": self.last_used,
            "customLabel": self.custom_label,
            "switchThreshold": None if self.switch_threshold is None else _json_number(self.switch_threshold),
            "sessionThreshold": None if self.session_threshold is None else _json_number(self.session_threshold),
            "weeklyThreshold": None if self.weekly_threshold is None else _json_number(self.weekly_threshold),
            "priority": self.priority,
            "prioritySetOrder": self.priority_set_order,
        }
        for key, value in optional.items():
            if value is not None:
                out[key] = value
        return out


def load_accounts(data: Any) -> list[Account]:
    """The accounts in a decoded accounts.json (a list of records), sorted by priority
    ("Sorting also happens on load"). Raises :class:`AccountsFormatError` for anything
    else, so a damaged file is never half-read and then overwritten."""
    if not isinstance(data, list):
        raise AccountsFormatError("accounts.json is not a list")
    return sorted_by_priority([Account.from_json(item) for item in data])


def dump_accounts(accounts: Iterable[Account]) -> list[dict]:
    return [account.to_json() for account in accounts]


# --- priority (spec section 3) ----------------------------------------------------------


def valid_priority(value: Any) -> bool:
    """A priority is a whole number 1-99 (a bool is not one)."""
    return isinstance(value, int) and not isinstance(value, bool) and PRIORITY_RANGE[0] <= value <= PRIORITY_RANGE[1]


def sorted_by_priority(accounts: Iterable[Account], last_set: Mapping[str, int] | None = None) -> list[Account]:
    """Stable sort: numbered accounts by ascending number, the account set most recently
    first among equal numbers (``last_set`` maps id -> set order, overriding the
    account's own ``priority_set_order``), then every unnumbered account in its
    existing relative order."""
    items = list(accounts)

    def key(account: Account) -> tuple:
        # A stored number outside 1-99 counts as none (AccountOrder.validPriority).
        if not valid_priority(account.priority):
            return (1, 0, 0)
        order = (last_set or {}).get(account.id, account.priority_set_order)
        return (0, account.priority, -(order if order is not None else -1))

    return sorted(items, key=key)


def next_priority_set_order(accounts: Iterable[Account]) -> int:
    orders = [a.priority_set_order for a in accounts if a.priority_set_order is not None]
    return (max(orders) + 1) if orders else 1


def setting_priority(accounts: Sequence[Account], account_id: str, priority: int | None) -> list[Account] | None:
    """The accounts with ``account_id`` given ``priority`` (None clears it), re-sorted.

    None for an unknown id or a value outside 1-99 other than None. Setting a number
    stamps it as the most recently set; clearing drops the stamp. Setting the number an
    account already has changes nothing, not even its recency (AccountOrder.swift:53).
    """
    if priority is not None and not valid_priority(priority):
        return None
    current = next((a for a in accounts if a.id == account_id), None)
    if current is None:
        return None
    if current.priority == priority:
        return sorted_by_priority(accounts)
    stamp = next_priority_set_order(accounts) if priority is not None else None
    changed = [
        replace(a, priority=priority, priority_set_order=stamp) if a.id == account_id else a for a in accounts
    ]
    return sorted_by_priority(changed)


def numbered_in_order(accounts: Iterable[Account]) -> list[Account]:
    """Every account numbered 1...N in the given order (a whole-list reorder or a drag
    replaces typed numbers). Set-order stamps are dropped: no number is shared."""
    return [replace(a, priority=min(index, PRIORITY_RANGE[1]), priority_set_order=None)
            for index, a in enumerate(accounts, start=1)]


# --- order (AccountOrder.swift) ----------------------------------------------------------


def reordered(accounts: Sequence[Account], ordered_ids: Sequence[str]) -> list[Account] | None:
    """AccountOrder.swift:12-20. ``accounts`` in the order ``ordered_ids`` gives, or None
    unless it names every current account exactly once and nothing else."""
    current = [a.id for a in accounts]
    if (len(ordered_ids) != len(current) or len(set(ordered_ids)) != len(ordered_ids)
            or len(set(current)) != len(current) or set(ordered_ids) != set(current)):
        return None
    by_id: dict[str, Account] = {}
    for account in accounts:
        by_id.setdefault(account.id, account)
    return [by_id[i] for i in ordered_ids]


def set_order(accounts: Sequence[Account], ordered_ids: Sequence[str]) -> list[Account] | None:
    """``accounts.setOrder`` / a drag (D3): reorder, then number every account 1...N."""
    ordered = reordered(accounts, ordered_ids)
    return numbered_in_order(ordered) if ordered is not None else None


def moving(items: Sequence[Any], from_offsets: Iterable[int], to_offset: int) -> list[Any]:
    """AccountOrder.swift:26-36. ``items`` with the elements at ``from_offsets`` moved to
    ``to_offset``, with the meaning SwiftUI's ``onMove`` gives them: the destination is an
    index in the list BEFORE the move. Offsets outside the list are ignored and a
    destination outside it is clamped."""
    valid = sorted({i for i in from_offsets if 0 <= i < len(items)})
    if not valid:
        return list(items)
    moved = [items[i] for i in valid]
    valid_set = set(valid)
    kept = [item for index, item in enumerate(items) if index not in valid_set]
    clamped = min(max(to_offset, 0), len(items))
    insert_at = clamped - sum(1 for i in valid if i < clamped)
    return kept[:insert_at] + moved + kept[insert_at:]


def fallback_order(remaining: Sequence[Account]) -> list[Account]:
    """AccountOrder.fallbackOrder (AccountOrder.swift:109-113): where to go when the active
    account is removed, best first: the accounts that are not Manual only in priority order,
    then the Manual only ones. The caller tries them in turn until a switch succeeds (D20)."""
    ordered = sorted_by_priority(remaining)
    return [a for a in ordered if not a.manual_only] + [a for a in ordered if a.manual_only]


def fallback(remaining: Sequence[Account]) -> Account | None:
    """AccountOrder.swift:40-42 with D3: where to go when the active account is removed
    is the first account, in priority order, that is not Manual only, else the first."""
    ordered = fallback_order(remaining)
    return ordered[0] if ordered else None


# --- threshold writes (spec 2.1, D8) -------------------------------------------------------


def with_legacy_threshold(account: Account, value: Any) -> Account:
    """D8: the legacy write (``accounts.setThreshold``, ``accounts threshold <a> <n|default|manual>``,
    the Manual only menu item) means "both windows" and clears that account's window
    values, even when ``switchThreshold`` is unchanged. ``value`` None = follow the default,
    0 = Manual only."""
    return replace(account, switch_threshold=normalized_account_threshold(value),
                   session_threshold=None, weekly_threshold=None)


def thresholds_triple(account: Account) -> tuple[float | None, float | None, float | None]:
    """The triple a setter's no-op check compares (D8)."""
    return account.switch_threshold, account.session_threshold, account.weekly_threshold


def with_window_thresholds(account: Account, session: Any = ABSENT, weekly: Any = ABSENT) -> Account:
    """``accounts.setThresholds``. Per window: a number sets it (already validated to a
    whole 1-100 by the caller), ``None`` clears it to follow the default, ``ABSENT``
    leaves it alone. Both ABSENT returns the account unchanged (the caller answers
    invalidParams).

    2.1: before any per-window write the single value is split (a ``switchThreshold`` of
    1-100 is copied into each window that is nil, ``switchThreshold`` becomes nil). Only a
    number clears Manual only: it clears Manual only, sets that window, and the other
    window follows its default; ``None`` alone on a Manual only account changes nothing.
    """
    if session is ABSENT and weekly is ABSENT:
        return account
    switch, s, w = account.switch_threshold, account.session_threshold, account.weekly_threshold
    if is_manual_only(switch):
        if not any(v is not ABSENT and v is not None for v in (session, weekly)):
            return account
        switch, s, w = None, None, None
    else:
        switch, s, w = split_single_threshold(switch, s, w)
    if session is not ABSENT:
        s = None if session is None else float(session)
    if weekly is not ABSENT:
        w = None if weekly is None else float(weekly)
    return replace(account, switch_threshold=switch, session_threshold=s, weekly_threshold=w)


def with_default_choice(account: Account, window: str) -> Account:
    """Settings -> Accounts, "Default" chosen in the Session or Weekly menu. Unlike the
    wire's ``null``, this clears Manual only on a Manual only account and both windows
    follow their defaults (1.9's Default); otherwise it is the ``None`` write for that window."""
    if window not in ("session", "weekly"):
        raise ValueError("window must be 'session' or 'weekly'")
    if account.manual_only:
        return replace(account, switch_threshold=None, session_threshold=None, weekly_threshold=None)
    if window == "session":
        return with_window_thresholds(account, session=None)
    return with_window_thresholds(account, weekly=None)


def whole_threshold(value: Any) -> float | None:
    """A wire threshold number rounded half away from zero (``accounts.setThresholds``);
    None when it is not a finite number."""
    number = finite_number(value)
    if number is None:
        return None
    return round_half_away(number)


def own_window_values(account: Account) -> tuple[float | None, float | None]:
    """The account's own session and weekly values as the wire sends them: absent when the
    stored value is not a usable window number (so it follows the next layer)."""
    return window_value(account.session_threshold), window_value(account.weekly_threshold)
