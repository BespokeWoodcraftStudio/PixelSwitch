"""The auto-switch engine, ported from PixelSwitch/Services/AutoSwitchEngine.swift
(1.9) and extended with the 2.0 rules of docs/superpowers/specs/2026-10-24-2.0-spec.md
sections 2.1 to 2.3.

Pure functions only: no clock of their own (``now`` is POSIX seconds and is
passed in), no I/O, no settings store. The conformance vectors in
``Tests/conformance/autoswitch-vectors.json`` drive this module and the Swift
engine alike.

Swift references in docstrings are ``AutoSwitchEngine.swift:<lines>`` unless
another file is named. Where 2.0 changes a rule the docstring cites the spec
rule number (2.3 rule N) instead.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .finite import finite_number
from .usage_model import (
    FABLE_MODEL_NAME,
    UsageResponse,
    UsageWindow,
)

# --- constants (AutoSwitchEngine.swift:42-68, 475-490) -------------------------------

HYSTERESIS = 1.0                 # :51, a target needs one point of room (1.8)
DRAIN_MINIMUM_ROOM = 1.0         # :68
COOLDOWN_SECONDS = 300.0         # AppState.swift:130
THRESHOLD_RANGE = (50.0, 100.0)  # :475, the global defaults only
ACCOUNT_THRESHOLD_RANGE = (0.0, 100.0)  # :477, 0 = Manual only
ACCOUNT_STEPPER_RANGE = (1.0, 100.0)    # :480
MANUAL_ONLY_THRESHOLD = 0.0      # :483
DRAIN_HOURS_RANGE = (1.0, 72.0)  # :485
FALLBACK_THRESHOLD = 90.0        # :488
FALLBACK_DRAIN_WITHIN_HOURS = 24.0  # :490


class Strategy(str, enum.Enum):
    """:5-15. ``my_order`` is shown as "Priority (lowest number first)" in 2.0 (D4)."""

    MOST_ROOM = "mostRoom"
    MY_ORDER = "myOrder"
    RESETS_SOONEST = "resetsSoonest"


class Limit(str, enum.Enum):
    """:34-39. The 5-hour and 7-day windows together, or the weekly Fable allowance."""

    WINDOWS = "windows"
    FABLE = "fable"


class Trigger(str, enum.Enum):
    """:447."""

    THRESHOLD = "threshold"
    DRAIN_EARLY = "drainEarly"
    NO_SUBSCRIPTION = "noSubscription"


# --- small numeric helpers ------------------------------------------------------------


def round_half_away(value: float) -> float:
    """Swift ``Double.rounded()``: halves round away from zero, exactly."""
    magnitude = abs(value)
    whole = math.floor(magnitude)
    if magnitude - whole >= 0.5:
        whole += 1
    return float(math.copysign(whole, value)) if whole else 0.0


def _finite_number(value: Any) -> float | None:
    """A JSON number as a finite float, else None. An integer of 309 or more digits is a valid Python int that
    ``float()`` refuses with OverflowError; it reads as not a number here, never as a crash (finite.py)."""
    return finite_number(value)


def ceiling(threshold: float, room: float) -> float | None:
    """:58-61. The highest use at which an account may be switched TO.

    ``room`` under the threshold, or half the threshold when that is smaller.
    None for Manual only (0), a negative value or NaN: the one place that
    excludes an account.
    """
    if not (threshold > MANUAL_ONLY_THRESHOLD):  # also False for NaN
        return None
    return threshold - min(room, threshold / 2)


# --- thresholds: normalisation and the 2.2 resolution ---------------------------------


def clamped_threshold(value: Any) -> float:
    """:518-521. A global default kept within 50-100; not a number reads as 90."""
    number = _finite_number(value)
    if number is None:
        return FALLBACK_THRESHOLD
    return min(max(number, THRESHOLD_RANGE[0]), THRESHOLD_RANGE[1])


def normalized_account_threshold(value: Any) -> float | None:
    """:526-530. nil for nil or not-a-number, else a whole number within 0-100."""
    number = _finite_number(value)
    if number is None:
        return None
    whole = round_half_away(number)
    if whole <= ACCOUNT_THRESHOLD_RANGE[0]:
        return 0.0  # never -0
    return min(whole, ACCOUNT_THRESHOLD_RANGE[1])


def is_manual_only(own: Any) -> bool:
    """:533-535."""
    return normalized_account_threshold(own) == MANUAL_ONLY_THRESHOLD


def window_value(value: Any) -> float | None:
    """2.2 ``window(v)``: not a finite number -> nil; else round half away from zero;
    a result <= 0 -> nil (follow the next layer); above 100 -> 100."""
    number = _finite_number(value)
    if number is None:
        return None
    whole = round_half_away(number)
    if whole <= 0:
        return None
    return min(whole, 100.0)


@dataclass(frozen=True)
class Defaults:
    """The effective global defaults (2.2). ``base`` is ``autoSwitchThreshold``."""

    base: float
    session: float
    weekly: float


def resolve_base(raw: Any) -> float:
    """2.2 ``base``: unset or 0 -> 90, else clamp 50..100 (:494-497)."""
    number = _finite_number(raw)
    if number is None or number == 0:
        return FALLBACK_THRESHOLD
    return clamped_threshold(number)


def global_window_is_set(raw: Any) -> bool:
    """2.1: a global window key counts as set only when present and a finite
    number; absent, null, 0 or non-finite all mean follow the base."""
    number = _finite_number(raw)
    return number is not None and number != 0


def resolve_defaults(base: Any = None, session: Any = None, weekly: Any = None) -> Defaults:
    """2.2. Arguments are the RAW stored values (None = unset)."""
    resolved_base = resolve_base(base)
    return Defaults(
        base=resolved_base,
        session=clamped_threshold(session) if global_window_is_set(session) else resolved_base,
        weekly=clamped_threshold(weekly) if global_window_is_set(weekly) else resolved_base,
    )


@dataclass(frozen=True)
class Rule:
    """One account's resolved rule (2.2). Manual only is 0 / 0 with ``manual_only``."""

    session: float
    weekly: float
    manual_only: bool = False


@dataclass(frozen=True)
class LeaveAt:
    """Where auto-switch moves the user OFF an account while it is active (2.2 ``leaveAt``)."""

    session: float
    weekly: float


def resolve_rule(switch_threshold: Any, session_threshold: Any, weekly_threshold: Any, defaults: Defaults) -> Rule:
    """2.2 ``rule(account)``: account window value, then the account both-windows
    value (``switchThreshold``), then the global window default."""
    both = normalized_account_threshold(switch_threshold)
    if both == MANUAL_ONLY_THRESHOLD:
        return Rule(0.0, 0.0, True)
    session = window_value(session_threshold)
    weekly = window_value(weekly_threshold)
    return Rule(
        session=session if session is not None else (both if both is not None else defaults.session),
        weekly=weekly if weekly is not None else (both if both is not None else defaults.weekly),
        manual_only=False,
    )


def leave_at(rule: Rule, defaults: Defaults) -> LeaveAt:
    """2.2 ``leaveAt``: Manual only leaves at the defaults (:546-548)."""
    return LeaveAt(
        session=rule.session if rule.session > 0 else defaults.session,
        weekly=rule.weekly if rule.weekly > 0 else defaults.weekly,
    )


def relaxed_rule(rule: Rule) -> Rule:
    """2.3 rule 9: the weekly rule raised to ``max(weekly, session)``; Manual only stays."""
    if rule.manual_only:
        return rule
    return Rule(rule.session, max(rule.weekly, rule.session), False)


def relaxed_defaults(defaults: Defaults) -> Defaults:
    """2.3 rule 9: ``defaults.weekly`` raised the same way."""
    return Defaults(defaults.base, defaults.session, max(defaults.weekly, defaults.session))


def split_single_threshold(
    switch_threshold: Any, session_threshold: Any, weekly_threshold: Any
) -> tuple[Any, Any, Any]:
    """2.1 "Splitting the single value", the pure helper.

    Before any per-window write: if ``switchThreshold`` is 1-100, copy it into
    each of ``sessionThreshold`` / ``weeklyThreshold`` that is nil and set
    ``switchThreshold`` to nil. Manual only (0) and nil are left as they are.
    Returns ``(switch, session, weekly)``.
    """
    both = normalized_account_threshold(switch_threshold)
    if both is None or both <= 0:
        return switch_threshold, session_threshold, weekly_threshold
    return (
        None,
        session_threshold if session_threshold is not None else both,
        weekly_threshold if weekly_threshold is not None else both,
    )


@dataclass(frozen=True)
class Explanation:
    """The numbers Settings -> Accounts states for an account (:555-564, two windows)."""

    leave_at_session: float
    leave_at_weekly: float
    arrive_at_session: float
    arrive_at_weekly: float
    #: The weekly number when no account under its weekly limit can take you (rule 9).
    overflow_arrive_at_weekly: float
    #: Only stated when it says something (never since 1.8: both rooms are 1).
    drain_arrive_at_session: float | None
    drain_arrive_at_weekly: float | None


def explanation(
    rule: Rule, defaults: Defaults, strategy: Strategy | str = Strategy.MOST_ROOM, drain_early: bool = True
) -> Explanation | None:
    """:555-564 for two windows. None for Manual only."""
    if rule.manual_only:
        return None
    leave = leave_at(rule, defaults)
    arrive_s = ceiling(rule.session, HYSTERESIS)
    arrive_w = ceiling(rule.weekly, HYSTERESIS)
    if arrive_s is None or arrive_w is None:
        return None
    overflow_w = ceiling(max(rule.weekly, rule.session), HYSTERESIS)
    drain_on = Strategy(strategy) == Strategy.RESETS_SOONEST and drain_early
    drain_s = drain_w = None
    if drain_on:
        ds = ceiling(rule.session, DRAIN_MINIMUM_ROOM)
        dw = ceiling(rule.weekly, DRAIN_MINIMUM_ROOM)
        drain_s = ds if ds is not None and ds > arrive_s else None
        drain_w = dw if dw is not None and dw > arrive_w else None
    return Explanation(leave.session, leave.weekly, arrive_s, arrive_w,
                       overflow_w if overflow_w is not None else arrive_w, drain_s, drain_w)


# --- readings (the expiry rule), :74-153, 232-238 -------------------------------------


def reading(window: UsageWindow | None, now: float, require_known_window: bool = False) -> float | None:
    """:232-238. One window's utilization, or None if it has none or its window has reset.

    A window whose ``resets_at`` is in the past is discarded. When ``resets_at``
    is absent or unparseable the reading is kept if lenient (candidates) and
    discarded if ``require_known_window`` (the active side, when its sample was
    not taken by the current cycle).
    """
    if window is None or window.utilization is None:
        return None
    resets = window.resets_at_date
    if resets is None:
        return None if require_known_window else window.utilization
    return None if resets < now else window.utilization


def session_reading(usage: UsageResponse | None, now: float, require_known_window: bool = False) -> float | None:
    """The 5-hour window's reading under the expiry rule (:232-238 on ``usage.fiveHour``)."""
    return reading(usage.five_hour, now, require_known_window) if usage is not None else None


def weekly_reading(usage: UsageResponse | None, now: float, require_known_window: bool = False) -> float | None:
    """The 7-day window's reading under the expiry rule (:232-238 on ``usage.sevenDay``)."""
    return reading(usage.seven_day, now, require_known_window) if usage is not None else None


def fable_window(usage: UsageResponse | None) -> UsageWindow | None:
    """The Fable allowance as a window (:112-115, :148): the ``limits[]`` entry named "Fable"."""
    if usage is None:
        return None
    limit = usage.model_weekly_limit(FABLE_MODEL_NAME)
    return limit.window if limit is not None else None


def fable_reading(usage: UsageResponse | None, now: float, require_known_window: bool = False) -> float | None:
    """The Fable allowance under the same expiry rule (:112-115)."""
    return reading(fable_window(usage), now, require_known_window)


def binding_utilization(usage: UsageResponse | None, now: float, require_known_window: bool = False) -> float | None:
    """:89-98. The higher of the two windows (kept for logs and display)."""
    values = [v for v in (session_reading(usage, now, require_known_window),
                          weekly_reading(usage, now, require_known_window)) if v is not None]
    return max(values) if values else None


def weekly_reset(usage: UsageResponse | None, limit: Limit, now: float) -> float | None:
    """:144-153. The weekly reset "Resets soonest" ranks by; None unless that window
    has a reading AND a reset time still in the future."""
    if usage is None:
        return None
    window = usage.seven_day if limit == Limit.WINDOWS else fable_window(usage)
    if window is None or window.utilization is None:
        return None
    resets = window.resets_at_date
    if resets is None or not resets > now:
        return None
    return resets


# --- eligibility (2.3 rules 3, 4, 5) --------------------------------------------------


@dataclass(frozen=True)
class Eligible:
    """The readings a candidate was found eligible on (None = no usable reading)."""

    session: float | None
    weekly: float | None
    fable: float | None = None


def _eligible_on(usage: UsageResponse | None, limit: Limit, cs: float, cw: float, now: float) -> Eligible | None:
    """Rule 3 (windows) and rule 4 (Fable): the candidate needs at least one usable
    window reading and every usable window reading under its ceiling; for Fable the
    Fable reading must also be known and ``<= cw``."""
    s = session_reading(usage, now)
    w = weekly_reading(usage, now)
    if s is None and w is None:
        return None
    if s is not None and s > cs:
        return None
    if w is not None and w > cw:
        return None
    if limit == Limit.FABLE:
        f = fable_reading(usage, now)
        if f is None or f > cw:
            return None
        return Eligible(s, w, f)
    return Eligible(s, w)


def drain_eligible_utilization(
    usage: UsageResponse | None,
    rule: Rule,
    active_weekly_reset: float,
    drain_within: float,
    watch_fable: bool,
    now: float,
) -> Eligible | None:
    """:165-180 with rule 5 (a window each, at least ``DRAIN_MINIMUM_ROOM`` under it;
    the Fable check uses the weekly ceiling). All of these must hold: its weekly
    window has a known reset within ``drain_within`` of now, that reset is strictly
    earlier than the active account's, and it sits at least a point under its own
    limits (never for Manual only)."""
    cs = ceiling(rule.session, DRAIN_MINIMUM_ROOM)
    cw = ceiling(rule.weekly, DRAIN_MINIMUM_ROOM)
    if cs is None or cw is None:
        return None
    resets = weekly_reset(usage, Limit.WINDOWS, now)
    if resets is None or not resets < active_weekly_reset or not (resets - now) <= drain_within:
        return None
    found = _eligible_on(usage, Limit.WINDOWS, cs, cw, now)
    if found is None:
        return None
    if watch_fable:
        f = fable_reading(usage, now)
        if f is not None and f > cw:
            return None
    return found


def eligible_utilization(
    usage: UsageResponse | None,
    limit: Limit,
    trigger: Trigger,
    rule: Rule,
    *,
    hysteresis: float = HYSTERESIS,
    active_weekly_reset: float | None = None,
    drain_within: float = 24 * 3600.0,
    watch_fable: bool = True,
    now: float,
    relaxed: bool = False,
) -> Eligible | None:
    """:197-220. The one rule that decides whether a candidate may be switched to,
    for the rule that fired. ``plan`` ranks with it and the coordinator verifies the
    chosen account's fresh reading with it (2.3 rule 10), in the mode (strict or
    ``relaxed``) the plan was made in, so ranking and verification cannot disagree.

    ``rule`` is the CANDIDATE's own resolved rule. A rule of 0 (Manual only) is
    never eligible, by any rule.
    """
    if relaxed:
        rule = relaxed_rule(rule)
    if trigger == Trigger.THRESHOLD:
        cs = ceiling(rule.session, hysteresis)
        cw = ceiling(rule.weekly, hysteresis)
        if cs is None or cw is None:
            return None
        return _eligible_on(usage, limit, cs, cw, now)
    if trigger == Trigger.DRAIN_EARLY:
        if limit != Limit.WINDOWS or active_weekly_reset is None:
            return None
        return drain_eligible_utilization(usage, rule, active_weekly_reset, drain_within, watch_fable, now)
    # NO_SUBSCRIPTION
    if limit != Limit.WINDOWS:
        return None
    cs = ceiling(rule.session, DRAIN_MINIMUM_ROOM)
    cw = ceiling(rule.weekly, DRAIN_MINIMUM_ROOM)
    if cs is None or cw is None:
        return None
    return _eligible_on(usage, Limit.WINDOWS, cs, cw, now)


def cooldown_holds(last_switch_at: float | None, cooldown: float, active_unusable: bool, now: float) -> bool:
    """:226-229. Less than ``cooldown`` since the last automatic switch; never for an
    active account with no subscription."""
    if active_unusable or last_switch_at is None:
        return False
    return now - last_switch_at < cooldown


# --- ranking, :363-437 and rule 6 -----------------------------------------------------


@dataclass(frozen=True)
class _Ranked:
    account: Any
    position: int
    room: float
    keeps_fable: bool
    reset_minute: float | None


def _room(rule: Rule, limit: Limit, found: Eligible) -> float:
    """2.3 rule 6. On windows ``min(rule.session - s, rule.weekly - w)`` over the
    windows with a reading; on Fable ``rule.weekly - f``."""
    if limit == Limit.FABLE:
        assert found.fable is not None
        return rule.weekly - found.fable
    rooms = []
    if found.session is not None:
        rooms.append(rule.session - found.session)
    if found.weekly is not None:
        rooms.append(rule.weekly - found.weekly)
    return min(rooms)


def ranked_targets(
    *,
    active: Any,
    candidates: Sequence[Any],
    usage_by_account: dict[Any, UsageResponse],
    is_switchable: Callable[[Any], bool],
    is_unusable: Callable[[Any], bool] = lambda account: False,
    rule_of: Callable[[Any], Rule],
    hysteresis: float = HYSTERESIS,
    limit: Limit,
    trigger: Trigger,
    strategy: Strategy,
    active_weekly_reset: float | None,
    drain_within: float,
    watch_fable: bool,
    now: float,
    relaxed: bool = False,
) -> list[Any]:
    """Rank the accounts worth switching to for one limit and rule, best first (:363-437).

    The result is a list of *proposals*: it is computed from whatever samples the
    caller holds, which round-robin polling can leave several cycles old. The
    coordinator verifies the first candidates with a fresh reading (rule 10).

    An account with no active subscription is never eligible, and a candidate with
    no usable reading is not eligible. ``candidates`` are in the priority order;
    that order is the tie-break in every strategy and is the ranking itself under
    ``myOrder``.
    """
    ranked: list[_Ranked] = []
    for position, candidate in enumerate(candidates):
        usage = usage_by_account.get(candidate.id)
        rule = rule_of(candidate)
        if relaxed:
            rule = relaxed_rule(rule)
        # The usage check comes first: is_switchable reads the saved logins.
        if candidate.id == active.id or is_unusable(candidate):
            continue
        found = eligible_utilization(
            usage, limit, trigger, rule, hysteresis=hysteresis, active_weekly_reset=active_weekly_reset,
            drain_within=drain_within, watch_fable=watch_fable, now=now,
        )
        if found is None or not is_switchable(candidate):
            continue
        keeps_fable = False
        f = fable_reading(usage, now)
        c = ceiling(rule.weekly, hysteresis)
        if f is not None and c is not None:
            keeps_fable = f <= c
        reset = weekly_reset(usage, limit, now)
        reset_minute = math.floor(reset / 60) if reset is not None else None
        ranked.append(_Ranked(candidate, position, _room(rule, limit, found), keeps_fable, reset_minute))

    if strategy == Strategy.MOST_ROOM:
        # Fable to spare first, then most room under each account's own limits, then
        # the user's order so a tie is stable.
        ranked.sort(key=lambda r: (0 if r.keeps_fable else 1, -r.room, r.position))
    elif strategy == Strategy.MY_ORDER:
        pass  # already in the user's (priority) order: always from the top
    else:
        # Earliest weekly reset (whole minutes) first, unknown last, ties by room then order.
        ranked.sort(key=lambda r: (
            0 if r.reset_minute is not None else 1,
            r.reset_minute if r.reset_minute is not None else 0,
            -r.room,
            r.position,
        ))
    return [r.account for r in ranked]


# --- the plan, :285-347 and rules 1, 2, 9 ---------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What to do: which limit, by which rule, and where to go (best first).

    ``window`` names the window that fired for a windows trigger (``"session"``
    first if both); it is None for Fable, early-drain and no-subscription plans.
    ``relaxed`` is True when the plan was made under weekly overflow (rule 9).
    """

    limit: Limit
    trigger: Trigger
    window: str | None
    relaxed: bool
    targets: list[Any]


def fired_window(
    usage: UsageResponse | None, leave: LeaveAt, now: float, require_known_window: bool
) -> str | None:
    """2.3 rule 1 (replaces the single ``activeUtil >= activeThreshold`` of :323-325). ``"session"`` if ``s >= leave.session``, else ``"weekly"`` if
    ``w >= leave.weekly``, else None. A missing window cannot trigger."""
    s = session_reading(usage, now, require_known_window)
    if s is not None and s >= leave.session:
        return "session"
    w = weekly_reading(usage, now, require_known_window)
    if w is not None and w >= leave.weekly:
        return "weekly"
    return None


def _plan_once(
    *,
    active: Any,
    candidates: Sequence[Any],
    usage_by_account: dict[Any, UsageResponse],
    is_switchable: Callable[[Any], bool],
    is_unusable: Callable[[Any], bool],
    active_sampled_this_cycle: bool,
    rule_of: Callable[[Any], Rule],
    defaults: Defaults,
    hysteresis: float,
    watch_fable: bool,
    strategy: Strategy,
    drain_early: bool,
    drain_within: float,
    now: float,
    relaxed: bool,
) -> Plan | None:
    def ranked(limit: Limit, trigger: Trigger, active_weekly_reset: float | None) -> list[Any]:
        """:363-437 with this plan's arguments."""
        return ranked_targets(
            active=active, candidates=candidates, usage_by_account=usage_by_account,
            is_switchable=is_switchable, is_unusable=is_unusable, rule_of=rule_of,
            hysteresis=hysteresis, limit=limit, trigger=trigger, strategy=strategy,
            active_weekly_reset=active_weekly_reset, drain_within=drain_within,
            watch_fable=watch_fable, now=now, relaxed=relaxed,
        )

    # Rule 0: no subscription. An active account that cannot be used at all is left
    # at once, whatever its rule (:301-309).
    if is_unusable(active):
        targets = ranked(Limit.WINDOWS, Trigger.NO_SUBSCRIPTION, None)
        return Plan(Limit.WINDOWS, Trigger.NO_SUBSCRIPTION, None, relaxed, targets) if targets else None

    active_rule = rule_of(active)
    if relaxed:
        active_rule = relaxed_rule(active_rule)
    leave = leave_at(active_rule, relaxed_defaults(defaults) if relaxed else defaults)
    active_usage = usage_by_account.get(active.id)
    strict_expiry = not active_sampled_this_cycle  # :323-324
    threshold_reached = False

    # Rule 1: the windows.
    window = fired_window(active_usage, leave, now, strict_expiry)
    if window is not None:
        threshold_reached = True
        targets = ranked(Limit.WINDOWS, Trigger.THRESHOLD, None)
        if targets:
            return Plan(Limit.WINDOWS, Trigger.THRESHOLD, window, relaxed, targets)

    # Rule 2: Fable follows the weekly limit.
    if watch_fable:
        f = fable_reading(active_usage, now, strict_expiry)
        if f is not None and f >= leave.weekly:
            threshold_reached = True
            targets = ranked(Limit.FABLE, Trigger.THRESHOLD, None)
            if targets:
                return Plan(Limit.FABLE, Trigger.THRESHOLD, None, relaxed, targets)

    # Early drain (:336-346): only when no limit was reached at all, the active
    # account is not Manual only, the strategy is Resets soonest and early
    # switching is on, and the active account has a future weekly reset.
    if threshold_reached or active_rule.manual_only or strategy != Strategy.RESETS_SOONEST or not drain_early:
        return None
    active_weekly_reset = weekly_reset(active_usage, Limit.WINDOWS, now)
    if active_weekly_reset is None:
        return None
    targets = ranked(Limit.WINDOWS, Trigger.DRAIN_EARLY, active_weekly_reset)
    return Plan(Limit.WINDOWS, Trigger.DRAIN_EARLY, None, relaxed, targets) if targets else None


def overflow_holds(
    *,
    active: Any,
    usage_by_account: dict[Any, UsageResponse],
    is_unusable: Callable[[Any], bool],
    active_sampled_this_cycle: bool,
    rule_of: Callable[[Any], Rule],
    defaults: Defaults,
    now: float,
) -> bool:
    """Rule 9, the condition (given that the strict plan is nil): the active account
    has no subscription, or has reached its strict limit on the windows by rule 1
    (under rule 1's expiry rule). A Fable-only trigger does not count."""
    if is_unusable(active):
        return True
    leave = leave_at(rule_of(active), defaults)
    return fired_window(usage_by_account.get(active.id), leave, now, not active_sampled_this_cycle) is not None


def plan(
    *,
    active: Any,
    candidates: Sequence[Any],
    usage_by_account: dict[Any, UsageResponse],
    is_switchable: Callable[[Any], bool],
    is_unusable: Callable[[Any], bool] = lambda account: False,
    active_sampled_this_cycle: bool,
    rule_of: Callable[[Any], Rule],
    defaults: Defaults,
    hysteresis: float = HYSTERESIS,
    watch_fable: bool = True,
    strategy: Strategy | str = Strategy.MOST_ROOM,
    drain_early: bool = False,
    drain_within: float = 24 * 3600.0,
    now: float,
) -> Plan | None:
    """Which limit to act on, by which rule, and where to go; None to stay put.

    Mirrors ``AutoSwitchEngine.plan`` (:285-347) with the 2.0 rules. Arguments:

    * ``active`` / ``candidates``: objects with an ``id``; ``candidates`` are every
      other account of the same provider IN THE PRIORITY ORDER (``myOrder`` ranks by
      it and it breaks every tie).
    * ``usage_by_account``: latest usage per account id.
    * ``rule_of(account)``: that account's resolved :class:`Rule` (2.2).
    * ``defaults``: the effective global defaults pair; where an active Manual only
      account is left.
    * ``active_sampled_this_cycle``: fresh readings are trusted even without a
      parseable ``resets_at``; only retained readings need the strict expiry check.

    Rule 9 (weekly overflow): when the strict plan is None and overflow holds, the
    result is the same plan with every weekly rule raised to ``max(weekly, session)``
    and ``Plan.relaxed`` set.
    """
    strategy = Strategy(strategy)
    common = dict(
        active=active, candidates=candidates, usage_by_account=usage_by_account,
        is_switchable=is_switchable, is_unusable=is_unusable,
        active_sampled_this_cycle=active_sampled_this_cycle, rule_of=rule_of, defaults=defaults,
        hysteresis=hysteresis, watch_fable=watch_fable, strategy=strategy, drain_early=drain_early,
        drain_within=drain_within, now=now,
    )
    strict = _plan_once(relaxed=False, **common)
    if strict is not None:
        return strict
    if not overflow_holds(
        active=active, usage_by_account=usage_by_account, is_unusable=is_unusable,
        active_sampled_this_cycle=active_sampled_this_cycle, rule_of=rule_of, defaults=defaults, now=now,
    ):
        return None
    return _plan_once(relaxed=True, **common)

