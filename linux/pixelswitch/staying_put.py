"""Rule 10's wording choice as one pure function: a port of ``AutoSwitchEngine.stayingPutKind``
(AutoSwitchEngine.swift:396-409, used at AppState.swift:1667-1674).

The weekly-overflow sentence ("staying until the session limit") applies only when overflow
holds AND the active account is still under its RELAXED limits. Past them (always the case with
equal windows, where relaxed equals strict) the account is not "staying until the session
limit": it already passed it, and the true reason is that nobody has room.
"""
from __future__ import annotations

from . import engine
from .usage_model import UsageResponse

NOT_AT_LIMIT = "notAtLimit"
NO_ROOM = "noRoom"
WAITING_FOR_SESSION_LIMIT = "waitingForSessionLimit"


def _reached(usage: UsageResponse | None, leave: engine.LeaveAt, watch_fable: bool, now: float, strict: bool) -> bool:
    if engine.fired_window(usage, leave, now, strict) is not None:
        return True
    if watch_fable:
        fable = engine.fable_reading(usage, now, strict)
        return fable is not None and fable >= leave.weekly
    return False


def staying_put_kind(active_usage: UsageResponse | None, active_rule: engine.Rule, defaults: engine.Defaults,
                     active_sampled_this_cycle: bool, watch_fable: bool, now: float) -> str:
    """``notAtLimit`` when neither a window nor (when watched) Fable has reached the STRICT
    limit; ``noRoom`` when only Fable did, or when the active account is also at its relaxed
    limits (the 1.9 sentence); ``waitingForSessionLimit`` when it is at its strict limit but still
    under its relaxed ones (the overflow sentence)."""
    require_known_window = not active_sampled_this_cycle
    strict = engine.leave_at(active_rule, defaults)
    strict_fire = engine.fired_window(active_usage, strict, now, require_known_window) is not None
    fable = engine.fable_reading(active_usage, now, require_known_window) if watch_fable else None
    fable_fire = fable is not None and fable >= strict.weekly
    if not (strict_fire or fable_fire):
        return NOT_AT_LIMIT
    if not strict_fire:  # a Fable-only trigger never makes overflow hold
        return NO_ROOM
    relaxed = engine.leave_at(engine.relaxed_rule(active_rule), engine.relaxed_defaults(defaults))
    relaxed_fire = _reached(active_usage, relaxed, watch_fable, now, require_known_window)
    return NO_ROOM if relaxed_fire else WAITING_FOR_SESSION_LIMIT
