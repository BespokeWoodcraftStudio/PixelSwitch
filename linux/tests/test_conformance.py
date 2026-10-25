"""The shared conformance vectors: every case of Tests/conformance/autoswitch-vectors.json through
engine.py and accounts.py, following Tests/conformance/README.md exactly.

The file is the contract between the Swift engine (macOS) and this one (Linux). Nobody edits it
from here: a case believed wrong is reported with the reasoning.

Where the file is: the environment variable PIXELSWITCH_VECTORS if set (an error when it points at
nothing), else <repo root>/Tests/conformance/autoswitch-vectors.json. A missing file FAILS the
tests (it never skips), so a misplaced file cannot pass silently in CI.
"""
from __future__ import annotations

import json
import math
import os
import re
import unittest

from pixelswitch import engine
from pixelswitch.accounts import Account, sorted_by_priority
from pixelswitch.usage_model import UsageResponse, parse_iso8601

DEFAULT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "Tests", "conformance", "autoswitch-vectors.json"))


def vectors_path() -> str:
    return os.environ.get("PIXELSWITCH_VECTORS") or DEFAULT_PATH


def load_vectors():
    path = vectors_path()
    if not os.path.isfile(path):
        if os.environ.get("PIXELSWITCH_VECTORS"):
            raise FileNotFoundError(f"PIXELSWITCH_VECTORS points at {path}, which does not exist")
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --- the harness, README "The harness procedure" ---------------------------------------------------


def build_usage(raw: dict) -> UsageResponse:
    """A vector's ``usage`` block as the API response the engine reads. ``fable`` becomes the
    ``limits[]`` entry that ``model_weekly_limit("Fable")`` finds."""
    body: dict = {}
    for source, target in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
        if source in raw:
            body[target] = raw[source]
    if "fable" in raw:
        window = raw["fable"]
        entry = {"kind": "weekly_scoped", "group": "weekly", "percent": window.get("utilization"),
                 "scope": {"model": {"id": None, "display_name": "Fable"}}}
        if "resets_at" in window:
            entry["resets_at"] = window["resets_at"]
        body["limits"] = [entry]
    return UsageResponse.from_json(body)


def build_account(raw: dict) -> Account:
    return Account(
        id=raw["id"], email=f"{raw['id'].lower()}@x.com", display_name=raw["id"],
        switch_threshold=raw.get("switchThreshold"),
        session_threshold=raw.get("sessionThreshold"),
        weekly_threshold=raw.get("weeklyThreshold"),
        priority=raw.get("priority"),
        priority_set_order=raw.get("prioritySetOrder"),
    )


def number_equal(a, b) -> bool:
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
        and not isinstance(b, bool) and math.isclose(a, b, rel_tol=0, abs_tol=1e-9)


def run_case(case: dict, testcase: unittest.TestCase) -> None:
    settings = case["settings"]
    now = parse_iso8601(case["now"])
    testcase.assertIsNotNone(now, "the clock must parse")

    raw_accounts = case["accounts"]
    accounts = [build_account(raw) for raw in raw_accounts]
    usage_raw = {raw["id"]: raw.get("usage") for raw in raw_accounts}
    switchable = {raw["id"]: raw["switchable"] for raw in raw_accounts}
    unusable = {raw["id"]: raw["unusable"] for raw in raw_accounts}
    usage = {account_id: build_usage(block) for account_id, block in usage_raw.items() if block is not None}

    # 1. sort with sortedByPriority, passing prioritySetOrder as lastSet
    last_set = {raw["id"]: raw["prioritySetOrder"] for raw in raw_accounts if raw.get("prioritySetOrder") is not None}
    ordered = sorted_by_priority(accounts, last_set=last_set)
    testcase.assertEqual([a.id for a in ordered], case["expect"]["order"], "sortedByPriority")

    # 3. apply spec 2.2 to the raw stored values
    defaults_raw = settings.get("defaults") or {}
    defaults = engine.resolve_defaults(defaults_raw.get("base"), defaults_raw.get("session"), defaults_raw.get("weekly"))
    rules = {a.id: engine.resolve_rule(a.switch_threshold, a.session_threshold, a.weekly_threshold, defaults) for a in ordered}
    expected_rules = case["expect"]["rules"]
    testcase.assertEqual(sorted(rules), sorted(expected_rules), "rules cover every account")
    for account_id, expected in expected_rules.items():
        rule = rules[account_id]
        leave = engine.leave_at(rule, defaults)
        got = {"session": rule.session, "weekly": rule.weekly, "manualOnly": rule.manual_only,
               "leaveSession": leave.session, "leaveWeekly": leave.weekly}
        for key, want in expected.items():
            if key == "manualOnly":
                testcase.assertIs(got[key], want, f"rules[{account_id}].{key}")
            else:
                testcase.assertTrue(number_equal(got[key], want), f"rules[{account_id}].{key}: {got[key]} != {want}")

    # 2. the candidates are every account except the active one, in that sorted order
    active = next(a for a in ordered if a.id == case["active"])
    candidates = [a for a in ordered if a.id != active.id]

    # 4. the plan: strict ?? relaxed (engine.plan implements rule 9)
    result = engine.plan(
        active=active,
        candidates=candidates,
        usage_by_account=usage,
        is_switchable=lambda account: switchable[account.id],
        is_unusable=lambda account: unusable[account.id],
        active_sampled_this_cycle=case["activeSampledThisCycle"],
        rule_of=lambda account: rules[account.id],
        defaults=defaults,
        hysteresis=settings.get("hysteresis", 1),
        watch_fable=settings["watchFable"],
        strategy=settings["strategy"],
        drain_early=settings["drainEarly"],
        drain_within=settings["drainWithinHours"] * 3600,
        now=now,
    )
    expected = case["expect"]["plan"]
    if expected is None:
        testcase.assertIsNone(result, f"expected to stay put, got {_describe(result)}")
        return
    testcase.assertIsNotNone(result, f"expected {expected}, got to stay put")
    testcase.assertEqual(result.limit.value, expected["limit"], "limit")
    testcase.assertEqual(result.trigger.value, expected["trigger"], "trigger")
    testcase.assertIs(result.relaxed, expected["relaxed"], "relaxed")
    testcase.assertEqual(result.window, expected.get("window"), "window (absent means None)")
    testcase.assertEqual([a.id for a in result.targets], expected["targets"], "targets in ranked order")


def _describe(plan) -> str:
    if plan is None:
        return "stay"
    return (f"{plan.limit.value}/{plan.trigger.value} window={plan.window} relaxed={plan.relaxed} "
            f"targets={[a.id for a in plan.targets]}")


# --- one test per case -----------------------------------------------------------------------------


class ConformanceFileTests(unittest.TestCase):
    def test_the_file_shape_and_constants(self):
        data = load_vectors()
        self.assertIsNotNone(data, f"the conformance vectors are missing: expected {DEFAULT_PATH}")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["hysteresisDefault"], engine.HYSTERESIS)
        self.assertEqual(data["drainMinimumRoom"], engine.DRAIN_MINIMUM_ROOM)
        names = [case["name"] for case in data["cases"]]
        self.assertEqual(len(names), len(set(names)), "case names are unique")
        self.assertGreaterEqual(len(names), 150, "the Mac harness requires at least 150 cases too")

    def test_every_case_has_every_expect_field(self):
        data = load_vectors()
        self.assertIsNotNone(data, f"the conformance vectors are missing: expected {DEFAULT_PATH}")
        for case in data["cases"]:
            expect = case["expect"]
            self.assertEqual(sorted(expect), ["order", "plan", "rules"], case["name"])
            for rule in expect["rules"].values():
                self.assertEqual(sorted(rule), ["leaveSession", "leaveWeekly", "manualOnly", "session", "weekly"], case["name"])


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()[:80]


def _install(vectors) -> None:
    if not vectors:
        return
    for index, case in enumerate(vectors["cases"]):
        def test(self, case=case):
            run_case(case, self)

        test.__doc__ = case["name"]
        setattr(ConformanceCases, f"test_{index:03d}_{_slug(case['name'])}", test)


class ConformanceCases(unittest.TestCase):
    """One test method per vector case: ``test_<index>_<name>``."""


try:
    _vectors = load_vectors()
    if _vectors is None:
        # Lane LB: a misplaced or missing file must FAIL, never skip, so CI cannot pass silently.
        def _missing_default(self):
            self.fail(f"the conformance vectors are missing: expected {DEFAULT_PATH}")

        ConformanceCases.test_vectors_file_exists = _missing_default
    else:
        _install(_vectors)
except FileNotFoundError as error:  # an explicit PIXELSWITCH_VECTORS that does not exist
    def _missing(self, error=error):
        raise error

    ConformanceCases.test_vectors_file_exists = _missing


if __name__ == "__main__":
    unittest.main()
