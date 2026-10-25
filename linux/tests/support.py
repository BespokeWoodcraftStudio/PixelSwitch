"""Shared fixtures for the engine tests: the Swift suite's ``sample`` helper and a fixed clock."""
from __future__ import annotations

from typing import Callable

from pixelswitch import engine
from pixelswitch.accounts import Account
from pixelswitch.usage_model import UsageResponse, parse_iso8601

#: The fixed clock every case uses (AutoSwitchRulesTests.swift Rules.now).
NOW = parse_iso8601("2026-09-22T17:00:00Z")
assert NOW is not None


def iso(hours: float) -> str:
    """An ISO 8601 time ``hours`` from NOW (negative is in the past), whole seconds."""
    from pixelswitch.protocol import iso8601_utc

    return iso8601_utc(NOW + hours * 3600)


def sample(
    session,
    weekly,
    weekly_resets_in=100,
    session_resets_in=2,
    fable=None,
    fable_resets_in=100,
) -> UsageResponse:
    """A usage reading: session and weekly use in percent (None is JSON null), every
    ``*_resets_in`` in hours from NOW (negative is in the past, None leaves ``resets_at`` out),
    and an optional Fable allowance."""

    def window(util, resets_in):
        out = {"utilization": util}
        if resets_in is not None:
            out["resets_at"] = iso(resets_in)
        return out

    body = {"five_hour": window(session, session_resets_in), "seven_day": window(weekly, weekly_resets_in)}
    if fable is not None:
        limit = {
            "kind": "weekly_scoped", "group": "weekly", "percent": fable,
            "scope": {"model": {"id": None, "display_name": "Fable"}},
        }
        if fable_resets_in is not None:
            limit["resets_at"] = iso(fable_resets_in)
        body["limits"] = [limit]
    return UsageResponse.from_json(body)


_counter = [0]


def acct(name: str, switch=None, session=None, weekly=None, active=False, priority=None, set_order=None) -> Account:
    return Account(
        id=name, email=f"{name.lower()}@x.com", display_name=name, is_active=active,
        switch_threshold=switch, session_threshold=session, weekly_threshold=weekly,
        priority=priority, priority_set_order=set_order,
    )


def defaults_of(base=None, session=None, weekly=None) -> engine.Defaults:
    return engine.resolve_defaults(base, session, weekly)


def run_plan(
    active: Account,
    candidates,
    usage: dict,
    default_threshold=90,
    strategy=engine.Strategy.MOST_ROOM,
    drain_early=False,
    drain_within_hours=24,
    watch_fable=True,
    switchable: Callable[[Account], bool] = lambda a: True,
    sampled=True,
    no_subscription=(),
    session_default=None,
    weekly_default=None,
):
    """The engine as the coordinator calls it. ``usage`` is keyed by account NAME (id)."""
    defaults = engine.resolve_defaults(default_threshold, session_default, weekly_default)
    return engine.plan(
        active=active,
        candidates=list(candidates),
        usage_by_account=usage,
        is_switchable=switchable,
        is_unusable=lambda a: a.id in no_subscription,
        active_sampled_this_cycle=sampled,
        rule_of=lambda a: engine.resolve_rule(a.switch_threshold, a.session_threshold, a.weekly_threshold, defaults),
        defaults=defaults,
        hysteresis=engine.HYSTERESIS,
        watch_fable=watch_fable,
        strategy=strategy,
        drain_early=drain_early,
        drain_within=drain_within_hours * 3600,
        now=NOW,
    )


def describe(plan) -> str:
    """"stay", or "<limit>/<trigger>: <targets in order>" (+ window / relaxed markers for 2.0)."""
    if plan is None:
        return "stay"
    text = f"{plan.limit.value}/{plan.trigger.value}: " + ",".join(a.display_name for a in plan.targets)
    return text
