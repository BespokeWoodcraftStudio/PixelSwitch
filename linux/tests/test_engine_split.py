"""Spec 2.3: separate session and weekly limits, Fable on the weekly rule, the early drain with
split values, and weekly overflow (rule 9) including the cases that must MOVE and the cases
that must STAY."""
from __future__ import annotations

import unittest

from pixelswitch import engine
from pixelswitch.engine import Limit, Strategy, Trigger
from tests.support import NOW, acct, describe, run_plan, sample

S = Strategy


def go(active, candidates, usage, **kwargs):
    """The plan with the founder's defaults (session 90, weekly 50) unless overridden."""
    kwargs.setdefault("default_threshold", 90)
    kwargs.setdefault("weekly_default", 50)
    return run_plan(active, candidates, usage, **kwargs)


def line(plan):
    if plan is None:
        return "stay"
    tail = f" [{plan.window}]" if plan.window else ""
    if plan.relaxed:
        tail += " relaxed"
    return describe(plan) + tail


class FoundersExampleTests(unittest.TestCase):
    """90% for the 5-hour limit, 50% for the weekly (the founder's words, 2026-10-24)."""

    def setUp(self):
        self.a = acct("A", active=True)
        self.b, self.c = acct("B"), acct("C")

    def test_leaves_at_the_weekly_limit_with_a_calm_session(self):
        usage = {"A": sample(20, 50), "B": sample(20, 40)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "windows/threshold: B [weekly]")

    def test_leaves_at_the_session_limit_with_a_calm_week(self):
        usage = {"A": sample(90, 10), "B": sample(20, 40)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "windows/threshold: B [session]")

    def test_stays_just_under_both(self):
        usage = {"A": sample(89, 49), "B": sample(20, 10)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")

    def test_session_is_named_first_when_both_fire(self):
        usage = {"A": sample(95, 60), "B": sample(20, 10)}
        self.assertEqual(go(self.a, [self.b], usage).window, "session")

    def test_candidate_needs_both_windows_under_their_own_ceilings(self):
        base = {"A": sample(90, 10)}
        for s, w, ok in [(89, 49, True), (90, 10, False), (10, 50, False), (89, 10, True), (0, 49, True)]:
            plan = go(self.a, [self.b], {**base, "B": sample(s, w)}, strategy=S.MOST_ROOM)
            # strict only: a candidate over its weekly limit may be taken in overflow, so compare the strict result
            strict = plan if plan is not None and not plan.relaxed else None
            self.assertEqual(strict is not None, ok, (s, w))

    def test_a_single_window_reading_is_enough_but_none_is_not(self):
        base = {"A": sample(90, 10)}
        self.assertEqual(line(go(self.a, [self.b], {**base, "B": sample(20, None)})), "windows/threshold: B [session]")
        self.assertEqual(line(go(self.a, [self.b], {**base, "B": sample(None, 20)})), "windows/threshold: B [session]")
        self.assertEqual(line(go(self.a, [self.b], {**base, "B": sample(None, None)})), "stay")

    def test_a_missing_window_cannot_trigger(self):
        usage = {"A": sample(None, 49), "B": sample(1, 1)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")
        usage = {"A": sample(89, None), "B": sample(1, 1)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")

    def test_room_is_the_smaller_gap(self):
        # B: gaps 90-30=60 session, 50-40=10 weekly -> room 10. C: gaps 90-60=30 and 50-5=45 -> 30.
        usage = {"A": sample(90, 10), "B": sample(30, 40), "C": sample(60, 5)}
        self.assertEqual(line(go(self.a, [self.b, self.c], usage)), "windows/threshold: C,B [session]")


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.b = acct("B")

    def test_expired_active_window_is_dropped(self):
        usage = {"A": sample(20, 60, weekly_resets_in=-1), "B": sample(5, 5)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")
        usage = {"A": sample(95, 60, session_resets_in=-1, weekly_resets_in=-1), "B": sample(5, 5)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")

    def test_one_expired_window_leaves_the_other_working(self):
        usage = {"A": sample(95, 10, session_resets_in=-1), "B": sample(5, 5)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")
        usage = {"A": sample(20, 55, session_resets_in=-1), "B": sample(5, 5)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "windows/threshold: B [weekly]")

    def test_strict_on_the_active_side_lenient_for_candidates(self):
        retained = {"A": sample(20, 55, weekly_resets_in=None), "B": sample(5, 5)}
        self.assertEqual(line(go(self.a, [self.b], retained, sampled=False)), "stay")
        self.assertEqual(line(go(self.a, [self.b], retained, sampled=True)), "windows/threshold: B [weekly]")
        usage = {"A": sample(95, 10), "B": sample(5, 30, weekly_resets_in=None)}
        self.assertEqual(line(go(self.a, [self.b], usage, sampled=False)), "windows/threshold: B [session]")

    def test_expired_candidate_window_is_dropped(self):
        # B's weekly reading (80) is from a week that ended: only its session counts.
        usage = {"A": sample(95, 10), "B": sample(5, 80, weekly_resets_in=-3)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "windows/threshold: B [session]")
        usage = {"A": sample(95, 10), "B": sample(95, 5, session_resets_in=-1)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "windows/threshold: B [session]")


class FableOnTheWeeklyRuleTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.b = acct("B")

    def test_fable_triggers_at_the_weekly_limit(self):
        usage = {"A": sample(10, 10, fable=50), "B": sample(5, 5, fable=20)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "fable/threshold: B")
        usage = {"A": sample(10, 10, fable=49), "B": sample(5, 5, fable=20)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")

    def test_fable_candidate_must_be_under_the_weekly_ceiling_on_fable_and_both_windows(self):
        usage = {"A": sample(10, 10, fable=60), "B": sample(5, 5, fable=49)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "fable/threshold: B")
        usage = {"A": sample(10, 10, fable=60), "B": sample(5, 5, fable=50)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")
        usage = {"A": sample(10, 10, fable=60), "B": sample(5, 60, fable=20)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")  # Fable-only trigger: no overflow

    def test_keeps_fable_with_no_fable_reading_is_false(self):
        c = acct("C")
        usage = {"A": sample(90, 10), "B": sample(30, 30), "C": sample(40, 40, fable=10)}
        # C keeps Fable (10 <= 49) so it leads despite less room; B has no Fable reading.
        self.assertEqual(line(go(self.a, [self.b, c], usage)), "windows/threshold: C,B [session]")

    def test_strict_fable_expiry_on_the_active_side(self):
        usage = {"A": sample(10, 10, fable=99, fable_resets_in=None), "B": sample(5, 5, fable=20)}
        self.assertEqual(line(go(self.a, [self.b], usage, sampled=False)), "stay")
        self.assertEqual(line(go(self.a, [self.b], usage, sampled=True)), "fable/threshold: B")
        usage = {"A": sample(10, 10, fable=99, fable_resets_in=-2), "B": sample(5, 5, fable=20)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")


class DrainWithSplitValuesTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.y = acct("Y")

    def drain(self, y_usage, **kw):
        usage = {"A": sample(30, 20), "Y": y_usage}
        return line(go(self.a, [self.y], usage, strategy=S.RESETS_SOONEST, drain_early=True, **kw))

    def test_each_window_needs_a_point_under_its_own_limit(self):
        self.assertEqual(self.drain(sample(10, 49, weekly_resets_in=12)), "windows/drainEarly: Y")
        self.assertEqual(self.drain(sample(10, 49.5, weekly_resets_in=12)), "stay")
        self.assertEqual(self.drain(sample(89, 10, weekly_resets_in=12)), "windows/drainEarly: Y")
        self.assertEqual(self.drain(sample(89.5, 10, weekly_resets_in=12)), "stay")

    def test_drain_follows_the_weekly_limit_not_the_raised_one(self):
        # 95% of a week under a 50% weekly limit is not a drain target, even though overflow
        # would raise the weekly limit: the active account is under both of its limits.
        self.assertEqual(self.drain(sample(10, 95, weekly_resets_in=12)), "stay")

    def test_drain_fable_check_uses_the_weekly_ceiling(self):
        self.assertEqual(self.drain(sample(10, 20, weekly_resets_in=12, fable=49)), "windows/drainEarly: Y")
        self.assertEqual(self.drain(sample(10, 20, weekly_resets_in=12, fable=50)), "stay")

    def test_no_subscription_with_split_values(self):
        usage = {"B": sample(89, 49)}
        a, b = acct("A", active=True), acct("B")
        self.assertEqual(line(go(a, [b], usage, no_subscription={"A"})), "windows/noSubscription: B")
        usage = {"B": sample(89, 49.5)}
        # 49.5 is past the weekly ceiling (49) but the active account has no subscription: overflow holds
        self.assertEqual(line(go(a, [b], usage, no_subscription={"A"})), "windows/noSubscription: B relaxed")
        usage = {"B": sample(89.5, 10)}
        self.assertEqual(line(go(a, [b], usage, no_subscription={"A"})), "stay")  # session over its ceiling: no mode helps


class WeeklyOverflowMoveTests(unittest.TestCase):
    """Rule 9: cases that must MOVE (relaxed)."""

    def setUp(self):
        self.a = acct("A", active=True)
        self.b = acct("B")

    def test_active_under_its_weekly_limit_at_session_90(self):
        # A's session is at its 90% limit; the week is at 10%. Every other account is past 50% of its week.
        usage = {"A": sample(90, 10), "B": sample(20, 70)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "windows/threshold: B [session] relaxed")

    def test_candidate_under_its_weekly_limit_with_session_95(self):
        a = acct("A", active=True)
        usage = {"A": sample(100, 10), "B": sample(95, 40)}
        plan = go(a, [self.b], usage, default_threshold=100, weekly_default=50, session_default=100)
        self.assertEqual(line(plan), "windows/threshold: B [session]")
        self.assertFalse(plan.relaxed)  # 95 <= 99 and 40 <= 49: an ordinary strict move

    def test_candidate_past_its_weekly_limit_with_session_95(self):
        usage = {"A": sample(100, 10), "B": sample(95, 70)}
        plan = go(self.a, [self.b], usage, session_default=100)
        # the relaxed weekly ceiling is max(50, 100) = 100 -> 99; the session ceiling is 99: 95 passes
        self.assertEqual(line(plan), "windows/threshold: B [session] relaxed")

    def test_candidate_with_no_reading_stays(self):
        usage = {"A": sample(90, 10)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")
        usage = {"A": sample(90, 10), "B": sample(None, None)}
        self.assertEqual(line(go(self.a, [self.b], usage)), "stay")

    def test_no_subscription_active_with_a_candidate_past_its_weekly_limit(self):
        usage = {"B": sample(20, 70)}
        plan = go(self.a, [self.b], usage, no_subscription={"A"})
        self.assertEqual(line(plan), "windows/noSubscription: B relaxed")

    def test_session_70_weekly_95_override_as_a_candidate(self):
        c = acct("C", session=70, weekly=95)
        usage = {"A": sample(90, 10), "C": sample(60, 90)}
        # C's own weekly limit is 95, so 90 is over 94 only if > 94: C is an ordinary strict candidate.
        plan = go(self.a, [c], usage)
        self.assertEqual(line(plan), "windows/threshold: C [session]")
        usage = {"A": sample(90, 10), "C": sample(70, 20)}
        self.assertEqual(line(go(self.a, [c], usage)), "stay")  # 70 > ceiling 69 on C's session, relaxed or not

    def test_session_70_weekly_95_override_as_the_active_account(self):
        a = acct("A", session=70, weekly=95, active=True)
        # session 70 >= 70 fires; B is past its weekly 50 limit -> relaxed weekly 90
        usage = {"A": sample(70, 10), "B": sample(20, 70)}
        self.assertEqual(line(go(a, [self.b], usage)), "windows/threshold: B [session] relaxed")
        # weekly 94 < 95: A has not reached its own weekly limit and session is calm -> stays
        usage = {"A": sample(60, 94), "B": sample(20, 70)}
        self.assertEqual(line(go(a, [self.b], usage)), "stay")

    def test_manual_only_candidate_is_never_taken_even_in_overflow(self):
        m = acct("M", switch=0)
        usage = {"A": sample(90, 10), "M": sample(0, 0)}
        self.assertEqual(line(go(self.a, [m], usage)), "stay")

    def test_equal_rules_change_nothing(self):
        """When the two limits are equal rule 9 is a no-op: relaxed == strict."""
        usage = {"A": sample(95, 40), "B": sample(95, 60)}
        self.assertEqual(line(go(self.a, [self.b], usage, weekly_default=90)), "stay")


class WeeklyOverflowStayTests(unittest.TestCase):
    """Rule 9: cases that must STAY."""

    def setUp(self):
        self.a = acct("A", active=True)
        self.y = acct("Y")

    def test_a_drain_from_an_account_under_both_limits(self):
        # A is under both of its limits; Y is past its weekly limit but would pass the raised one.
        usage = {"A": sample(30, 20), "Y": sample(10, 70, weekly_resets_in=12)}
        plan = go(self.a, [self.y], usage, strategy=S.RESETS_SOONEST, drain_early=True)
        self.assertIsNone(plan)

    def test_a_fable_only_trigger_from_an_account_under_both_limits(self):
        usage = {"A": sample(30, 20, fable=60), "Y": sample(10, 70, fable=10)}
        self.assertIsNone(go(self.a, [self.y], usage))

    def test_no_return_within_the_cooldown(self):
        """A moved to B by overflow; B is past its weekly limit, but A is not a target again:
        in the relaxed rules B has not reached its (raised) limit, and in the strict rules A's
        session is still over its ceiling."""
        a_idle = acct("A")
        b_active = acct("B", active=True)
        usage = {"B": sample(10, 60), "A": sample(90, 10)}
        self.assertIsNone(go(b_active, [a_idle], usage))
        # even with A's session calmer than before, B (weekly 60 >= 50) goes back only when A is
        # really under both of its limits - which then is the ordinary strict rule, not a ping-pong
        usage = {"B": sample(10, 60), "A": sample(40, 30)}
        self.assertEqual(line(go(b_active, [a_idle], usage)), "windows/threshold: A [weekly]")

    def test_overflow_needs_the_active_account_at_a_strict_limit(self):
        usage = {"A": sample(89, 49), "Y": sample(10, 70)}
        self.assertIsNone(go(self.a, [self.y], usage))

    def test_expired_active_reading_is_not_a_reached_limit(self):
        usage = {"A": sample(95, 60, session_resets_in=-1, weekly_resets_in=-1), "Y": sample(10, 70)}
        self.assertIsNone(go(self.a, [self.y], usage))
        usage = {"A": sample(95, 10, weekly_resets_in=None), "Y": sample(10, 70)}
        self.assertIsNone(go(self.a, [self.y], usage, sampled=False, session_default=100))


class RelaxedEntryPointTests(unittest.TestCase):
    """Rule 10: verification in a given mode."""

    def test_a_target_past_its_weekly_limit_is_eligible_only_in_relaxed_mode(self):
        defaults = engine.resolve_defaults(90, None, 50)
        rule = engine.resolve_rule(None, None, None, defaults)
        reading = sample(20, 70)
        common = dict(hysteresis=1.0, drain_within=86400.0, watch_fable=True, now=NOW)
        strict = engine.eligible_utilization(reading, Limit.WINDOWS, Trigger.THRESHOLD, rule, **common)
        relaxed = engine.eligible_utilization(reading, Limit.WINDOWS, Trigger.THRESHOLD, rule, relaxed=True, **common)
        self.assertIsNone(strict)
        self.assertIsNotNone(relaxed)

    def test_the_plan_says_which_mode_it_was_made_in(self):
        a, b = acct("A", active=True), acct("B")
        plan = go(a, [b], {"A": sample(90, 10), "B": sample(20, 70)})
        self.assertTrue(plan.relaxed)
        self.assertEqual((plan.limit, plan.trigger, plan.window), (Limit.WINDOWS, Trigger.THRESHOLD, "session"))


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)

    def test_tie_break_is_the_priority_order_in_every_strategy(self):
        b, c = acct("B"), acct("C")
        usage = {"A": sample(95, 10), "B": sample(10, 10, weekly_resets_in=30), "C": sample(10, 10, weekly_resets_in=30)}
        for strategy in S:
            self.assertEqual(describe(go(self.a, [b, c], usage, strategy=strategy)),
                             "windows/threshold: B,C", strategy)
            self.assertEqual(describe(go(self.a, [c, b], usage, strategy=strategy)),
                             "windows/threshold: C,B", strategy)

    def test_resets_soonest_uses_whole_minutes(self):
        b, c = acct("B"), acct("C")
        usage = {"A": sample(95, 10), "B": sample(10, 30, weekly_resets_in=30 + 30 / 3600),
                 "C": sample(10, 20, weekly_resets_in=30)}
        # both reset in the same minute -> tie -> more room first
        self.assertEqual(describe(go(self.a, [b, c], usage, strategy=S.RESETS_SOONEST)), "windows/threshold: C,B")

    def test_my_order_is_the_candidate_order(self):
        b, c, d = acct("B"), acct("C"), acct("D")
        usage = {"A": sample(95, 10), "B": sample(40, 40), "C": sample(5, 5), "D": sample(20, 20)}
        self.assertEqual(describe(go(self.a, [d, b, c], usage, strategy=S.MY_ORDER)), "windows/threshold: D,B,C")


if __name__ == "__main__":
    unittest.main()
