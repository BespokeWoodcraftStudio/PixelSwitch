"""2.3 rule 8, the hard test: with equal session and weekly rules the 2.0 engine reproduces
every 1.9 decision. These are the cases of Tests/UnitTests/AutoSwitchRulesTests.swift and
Tests/UnitTests/main.swift (plan, ceiling, strategies, early drain, Manual only, low
thresholds, no subscription, switch-at-your-number) run through the two-window engine with
one threshold per account (mapped to equal windows by ``resolve_rule``)."""
from __future__ import annotations

import unittest

from pixelswitch import engine
from pixelswitch.engine import Limit, Strategy, Trigger
from tests.support import NOW, acct, describe, run_plan, sample

S = Strategy


def plan(active, candidates, usage, **kwargs):
    return describe(run_plan(active, candidates, usage, **kwargs))


class CeilingTests(unittest.TestCase):
    def test_hysteresis_is_one_point(self):
        self.assertEqual(engine.HYSTERESIS, 1)
        self.assertEqual(engine.DRAIN_MINIMUM_ROOM, 1.0)

    def test_ceiling_values(self):
        c = engine.ceiling
        self.assertEqual([c(100, 10), c(50, 10), c(20, 10), c(19, 10), c(10, 10), c(5, 10), c(1, 10)],
                         [90, 40, 10, 9.5, 5, 2.5, 0.5])
        self.assertEqual([c(90, 1), c(2, 1), c(1, 1)], [89, 1, 0.5])

    def test_manual_only_has_none(self):
        c = engine.ceiling
        self.assertIsNone(c(0, 10))
        self.assertIsNone(c(0, 1))
        self.assertIsNone(c(-5, 10))
        self.assertIsNone(c(float("nan"), 10))

    def test_every_whole_threshold_leaves_room(self):
        prev = (0.0, 0.0)
        for t in range(1, 101):
            c, d = engine.ceiling(float(t), 10), engine.ceiling(float(t), 1)
            self.assertTrue(0 < c < t and c <= d < t and c >= prev[0] and d >= prev[1], t)
            prev = (c, d)

    def test_identical_to_1_4_ranges(self):
        self.assertTrue(all(engine.ceiling(float(t), 10) == t - 10 for t in range(20, 101)))
        self.assertTrue(all(engine.ceiling(float(t), 1) == t - 1 for t in range(2, 101)))


class PerAccountThresholdTests(unittest.TestCase):
    def setUp(self):
        self.a70 = acct("A", switch=70, active=True)
        self.a_default = acct("A", active=True)
        self.a100 = acct("A", switch=100, active=True)
        self.b70 = acct("B", switch=70)
        self.c = acct("C")
        self.d100 = acct("D", switch=100)
        self.fresh = acct("E")

    def test_active_own_threshold_decides(self):
        self.assertEqual(plan(self.a70, [self.c], {"A": sample(72, 40), "C": sample(10, 10)}), "windows/threshold: C")
        self.assertEqual(plan(self.a_default, [self.c], {"A": sample(72, 40), "C": sample(10, 10)}), "stay")
        self.assertEqual(plan(self.a70, [self.c], {"A": sample(69, 40), "C": sample(10, 10)}), "stay")

    def test_candidate_judged_against_its_own_threshold(self):
        mixed = {"A": sample(95, 40), "B": sample(70, 20), "C": sample(75, 20)}
        self.assertEqual(plan(self.a_default, [self.b70, self.c], mixed), "windows/threshold: C")
        self.assertEqual(plan(self.a_default, [self.b70], {"A": sample(95, 40), "B": sample(69, 20)}),
                         "windows/threshold: B")

    def test_fable_triggers_on_active_threshold(self):
        usage = {"A": sample(10, 20, fable=75), "C": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.a70, [self.c], usage), "fable/threshold: C")

    def test_threshold_100(self):
        self.assertEqual(plan(self.a100, [self.c], {"A": sample(99, 50), "C": sample(10, 10)}), "stay")
        self.assertEqual(plan(self.a100, [self.c], {"A": sample(100, 50), "C": sample(10, 10)}), "windows/threshold: C")
        self.assertEqual(plan(self.a_default, [self.d100], {"A": sample(95, 40), "D": sample(99, 90)}),
                         "windows/threshold: D")
        self.assertEqual(plan(self.a_default, [self.d100], {"A": sample(95, 40), "D": sample(100, 20)}), "stay")

    def test_no_sample_and_nobody_eligible(self):
        self.assertEqual(plan(self.a_default, [self.fresh], {"A": sample(95, 40)}), "stay")
        nobody = {"A": sample(95, 40), "B": sample(70, 20), "C": sample(90, 20)}
        self.assertIsNone(run_plan(self.a_default, [self.b70, self.c], nobody))


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.b, self.c, self.d, self.e = acct("B"), acct("C"), acct("D"), acct("E")

    def p(self, usage, strategy, active=None, candidates=None):
        return plan(active or self.a, candidates or [self.b, self.c, self.d], usage, strategy=strategy)

    def test_no_sample_never_chosen(self):
        fresh = acct("F")
        for strategy in S:
            self.assertEqual(self.p({"A": sample(95, 40)}, strategy, candidates=[fresh]), "stay", strategy)

    def test_three_eligible(self):
        three = {
            "A": sample(95, 40), "B": sample(10, 50),
            "C": sample(10, 20, weekly_resets_in=30), "D": sample(10, 40, weekly_resets_in=10),
        }
        self.assertEqual(self.p(three, S.MOST_ROOM), "windows/threshold: C,D,B")
        self.assertEqual(self.p(three, S.MY_ORDER), "windows/threshold: B,C,D")
        self.assertEqual(self.p(three, S.RESETS_SOONEST), "windows/threshold: D,C,B")

    def test_resets_soonest_ties_and_unknown(self):
        ties = {
            "A": sample(95, 40),
            "B": sample(10, 60, weekly_resets_in=30),
            "C": sample(10, 30, weekly_resets_in=30 + 20.0 / 3600),
            "D": sample(5, 5, weekly_resets_in=None),
            "E": sample(5, 5, weekly_resets_in=-3),
        }
        self.assertEqual(self.p(ties, S.RESETS_SOONEST, candidates=[self.b, self.c, self.d, self.e]),
                         "windows/threshold: C,B,D,E")
        self.assertIsNone(engine.weekly_reset(ties["E"], Limit.WINDOWS, NOW))

    def test_my_order_starts_from_the_top(self):
        from_top = {"A": sample(10, 10), "C": sample(95, 40), "B": sample(10, 10), "D": sample(10, 5)}
        self.assertEqual(self.p(from_top, S.MY_ORDER, active=self.c, candidates=[self.a, self.b, self.d]),
                         "windows/threshold: A,B,D")
        freed = {"D": sample(95, 40), "A": sample(99, 99), "B": sample(30, 30), "C": sample(5, 5)}
        self.assertEqual(self.p(freed, S.MY_ORDER, active=self.d, candidates=[self.a, self.b, self.c]),
                         "windows/threshold: B,C")

    def test_fable_preference(self):
        usage = {"A": sample(95, 40, fable=50), "B": sample(10, 10, fable=100), "C": sample(20, 20, fable=10)}
        self.assertEqual(self.p(usage, S.MOST_ROOM, candidates=[self.b, self.c]), "windows/threshold: C,B")
        self.assertEqual(self.p(usage, S.MY_ORDER, candidates=[self.b, self.c]), "windows/threshold: B,C")

    def test_fable_switch_resets_soonest_uses_fable_reset(self):
        usage = {
            "A": sample(10, 20, fable=95),
            "B": sample(5, 10, weekly_resets_in=5, fable=10, fable_resets_in=50),
            "C": sample(5, 10, fable=30, fable_resets_in=20),
        }
        self.assertEqual(self.p(usage, S.RESETS_SOONEST, candidates=[self.b, self.c]), "fable/threshold: C,B")
        self.assertEqual(self.p(usage, S.MOST_ROOM, candidates=[self.b, self.c]), "fable/threshold: B,C")

    def test_equal_use_keeps_list_order(self):
        usage = {"A": sample(95, 40), "B": sample(10, 30), "C": sample(10, 30)}
        self.assertEqual(self.p(usage, S.MOST_ROOM, candidates=[self.c, self.b]), "windows/threshold: C,B")

    def test_room_under_own_threshold(self):
        x, y = acct("X", switch=70), acct("Y", switch=100)
        usage = {"A": sample(95, 40), "X": sample(10, 60), "Y": sample(10, 65)}
        self.assertEqual(self.p(usage, S.MOST_ROOM, candidates=[x, y]), "windows/threshold: Y,X")
        tie = {"A": sample(95, 40), "X": sample(10, 50, weekly_resets_in=30), "Y": sample(10, 60, weekly_resets_in=30)}
        self.assertEqual(self.p(tie, S.RESETS_SOONEST, candidates=[x, y]), "windows/threshold: Y,X")


class EarlyDrainTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.y = acct("Y")
        self.y100 = acct("Y", switch=100)
        self.z = acct("Z")
        self.active_a = sample(30, 40)

    def drain(self, usage, active=None, candidates=None, strategy=S.RESETS_SOONEST, drain_early=True, within=24,
              watch_fable=True, switchable=lambda a: True):
        return plan(active or self.a, candidates or [self.y], usage, strategy=strategy, drain_early=drain_early,
                    drain_within_hours=within, watch_fable=watch_fable, switchable=switchable)

    def test_founders_example(self):
        self.assertEqual(self.drain({"A": self.active_a, "Y": sample(10, 95, weekly_resets_in=12)},
                                    candidates=[self.y100]), "windows/drainEarly: Y")
        self.assertEqual(self.drain({"A": self.active_a, "Y": sample(10, 95, weekly_resets_in=12)}), "stay")

    def test_basic_conditions(self):
        a, y_in_12 = self.active_a, sample(10, 80, weekly_resets_in=12)
        d = self.drain
        self.assertEqual(d({"A": a, "Y": y_in_12}), "windows/drainEarly: Y")
        self.assertEqual(d({"A": a, "Y": sample(10, 80, weekly_resets_in=30)}), "stay")
        self.assertEqual(d({"A": a, "Y": sample(10, 80, weekly_resets_in=30)}, within=48), "windows/drainEarly: Y")
        self.assertEqual(d({"A": sample(30, 40, weekly_resets_in=10), "Y": y_in_12}), "stay")
        self.assertEqual(d({"A": sample(30, 40, weekly_resets_in=12), "Y": y_in_12}), "stay")
        self.assertEqual(d({"A": a, "Y": sample(10, 89.5, weekly_resets_in=12)}), "stay")
        self.assertEqual(d({"A": a, "Y": sample(10, 89, weekly_resets_in=12)}), "windows/drainEarly: Y")
        self.assertEqual(d({"A": a, "Y": sample(95, 20, weekly_resets_in=12)}), "stay")
        self.assertEqual(d({"A": a, "Y": y_in_12}, strategy=S.MOST_ROOM), "stay")
        self.assertEqual(d({"A": a, "Y": y_in_12}, strategy=S.MY_ORDER), "stay")
        self.assertEqual(d({"A": a, "Y": y_in_12}, drain_early=False), "stay")
        self.assertEqual(d({"A": a, "Y": y_in_12}, switchable=lambda x: x.id != "Y"), "stay")
        self.assertEqual(d({"A": sample(30, 40, weekly_resets_in=None), "Y": y_in_12}), "stay")
        self.assertEqual(d({"A": sample(30, 40, weekly_resets_in=-1), "Y": y_in_12}), "stay")
        self.assertEqual(d({"A": a, "Y": sample(10, 5, weekly_resets_in=-1)}), "stay")
        self.assertEqual(d({"A": a}), "stay")

    def test_fable(self):
        a = self.active_a
        self.assertEqual(self.drain({"A": a, "Y": sample(10, 20, fable=10, fable_resets_in=5)}), "stay")
        out_of_fable = {"A": a, "Y": sample(10, 20, weekly_resets_in=12, fable=95)}
        self.assertEqual(self.drain(out_of_fable), "stay")
        self.assertEqual(self.drain(out_of_fable, watch_fable=False), "windows/drainEarly: Y")

    def test_two_targets(self):
        usage = {"A": self.active_a, "Y": sample(10, 80, weekly_resets_in=12), "Z": sample(10, 50, weekly_resets_in=6)}
        self.assertEqual(self.drain(usage, candidates=[self.y, self.z]), "windows/drainEarly: Z,Y")

    def test_threshold_reached_blocks_drain(self):
        self.assertEqual(self.drain({"A": sample(92, 40), "Y": sample(10, 90, weekly_resets_in=12)}), "stay")
        usage = {"A": sample(92, 40), "Y": sample(10, 90, weekly_resets_in=12), "Z": sample(10, 70, weekly_resets_in=50)}
        self.assertEqual(self.drain(usage, candidates=[self.y, self.z]), "windows/threshold: Z")

    def test_ping_pong(self):
        y_full = {"Y": sample(10, 90, weekly_resets_in=12), "A": self.active_a}
        y_active = acct("Y", active=True)
        a_idle = acct("A")
        self.assertEqual(self.drain(y_full, active=y_active, candidates=[a_idle]), "windows/threshold: A")
        self.assertEqual(self.drain(y_full, active=self.a, candidates=[self.y]), "stay")
        session_reset = {"Y": sample(90, 50, weekly_resets_in=12, session_resets_in=-0.1), "A": self.active_a}
        self.assertEqual(self.drain(session_reset, active=self.a, candidates=[self.y]), "windows/drainEarly: Y")

    def test_verification_rule_is_shared(self):
        fresh = sample(10, 85, weekly_resets_in=12)
        reset = NOW + 100 * 3600
        rule = engine.Rule(90, 90)
        common = dict(hysteresis=10.0, drain_within=24 * 3600, watch_fable=True, now=NOW)
        as_drain = engine.eligible_utilization(fresh, Limit.WINDOWS, Trigger.DRAIN_EARLY, rule,
                                               active_weekly_reset=reset, **common)
        as_threshold = engine.eligible_utilization(fresh, Limit.WINDOWS, Trigger.THRESHOLD, rule,
                                                   active_weekly_reset=None, **common)
        no_active_reset = engine.eligible_utilization(fresh, Limit.WINDOWS, Trigger.DRAIN_EARLY, rule,
                                                      active_weekly_reset=None, **common)
        on_fable = engine.eligible_utilization(fresh, Limit.FABLE, Trigger.DRAIN_EARLY, rule,
                                               active_weekly_reset=reset, **common)
        self.assertIsNotNone(as_drain)
        self.assertEqual(as_drain.weekly, 85)
        self.assertIsNone(as_threshold)  # 85 is past the 10-point ceiling (80)
        self.assertIsNone(no_active_reset)
        self.assertIsNone(on_fable)


class ManualOnlyTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.m0 = acct("M", switch=0)
        self.m_active = acct("M", switch=0, active=True)
        self.b = acct("B")
        self.b_active = acct("B", active=True)
        self.c = acct("C")
        self.y = acct("Y")

    def test_never_a_target(self):
        for s in S:
            self.assertEqual(plan(self.a, [self.m0], {"A": sample(95, 40), "M": sample(0, 0)}, strategy=s), "stay", s)
            self.assertEqual(plan(self.a, [self.m0, self.c],
                                  {"A": sample(95, 40), "M": sample(0, 0), "C": sample(50, 50)}, strategy=s),
                             "windows/threshold: C", s)
        self.assertEqual(plan(self.a, [self.m0], {"A": sample(10, 20, fable=95), "M": sample(0, 0, fable=0)}), "stay")
        usage = {"A": sample(10, 20, fable=95), "M": sample(0, 0, fable=0), "C": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.a, [self.m0, self.c], usage), "fable/threshold: C")
        drain = {"A": sample(30, 40), "M": sample(10, 80, weekly_resets_in=12), "Y": sample(10, 80, weekly_resets_in=12)}
        self.assertEqual(plan(self.a, [self.m0], drain, strategy=S.RESETS_SOONEST, drain_early=True), "stay")
        self.assertEqual(plan(self.a, [self.y], drain, strategy=S.RESETS_SOONEST, drain_early=True),
                         "windows/drainEarly: Y")

    def test_verification_refuses_it(self):
        rule = engine.resolve_rule(0, None, None, engine.resolve_defaults(90))
        self.assertTrue(rule.manual_only)
        common = dict(hysteresis=10.0, drain_within=24 * 3600, watch_fable=True, now=NOW)
        self.assertIsNone(engine.eligible_utilization(sample(0, 0), Limit.WINDOWS, Trigger.THRESHOLD, rule, **common))
        self.assertIsNone(engine.eligible_utilization(sample(0, 0, weekly_resets_in=12), Limit.WINDOWS,
                                                      Trigger.DRAIN_EARLY, rule,
                                                      active_weekly_reset=NOW + 100 * 3600, **common))

    def test_never_checked_in_the_store(self):
        reads = []
        run_plan(self.a, [self.m0], {"A": sample(95, 40), "M": sample(0, 0)},
                 switchable=lambda x: reads.append(x) or True)
        self.assertEqual(reads, [])

    def test_most_room_never_ranks_it(self):
        usage = {"A": sample(95, 40, fable=50), "M": sample(0, 0, fable=0), "B": sample(50, 50, fable=100)}
        self.assertEqual(plan(self.a, [self.m0, self.b], usage), "windows/threshold: B")

    def test_active_manual_only_leaves_at_the_default(self):
        c = self.c
        self.assertEqual(plan(self.m_active, [c], {"M": sample(89, 40), "C": sample(50, 50)}), "stay")
        self.assertEqual(plan(self.m_active, [c], {"M": sample(90, 40), "C": sample(50, 50)}), "windows/threshold: C")
        self.assertEqual(plan(self.m_active, [c], {"M": sample(72, 40), "C": sample(50, 50)}, default_threshold=70),
                         "windows/threshold: C")
        self.assertEqual(plan(self.m_active, [c], {"M": sample(69, 40), "C": sample(50, 50)}, default_threshold=70),
                         "stay")
        fable = {"M": sample(10, 20, fable=90), "C": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.m_active, [c], fable), "fable/threshold: C")
        self.assertEqual(plan(self.m_active, [c], fable, watch_fable=False), "stay")

    def test_active_manual_only_is_never_drained(self):
        usage = {"M": sample(30, 40), "A": sample(30, 40), "Y": sample(10, 80, weekly_resets_in=12)}
        self.assertEqual(plan(self.m_active, [self.y], usage, strategy=S.RESETS_SOONEST, drain_early=True), "stay")
        self.assertEqual(plan(self.a, [self.y], usage, strategy=S.RESETS_SOONEST, drain_early=True),
                         "windows/drainEarly: Y")

    def test_active_manual_only_edge_cases(self):
        c = self.c
        self.assertEqual(plan(self.m_active, [c], {"C": sample(50, 50)}), "stay")
        retained = {"M": sample(95, 95, weekly_resets_in=None, session_resets_in=None), "C": sample(50, 50)}
        self.assertEqual(plan(self.m_active, [c], retained, sampled=False), "stay")
        self.assertIsNone(run_plan(self.m_active, [c], {"M": sample(95, 40), "C": sample(90, 90)}))

    def test_nothing_brings_you_back(self):
        for s in S:
            self.assertEqual(plan(self.b_active, [self.m0], {"B": sample(95, 40), "M": sample(0, 0)}, strategy=s),
                             "stay", s)
        self.assertEqual(plan(self.b_active, [self.m0], {"B": sample(30, 40), "M": sample(10, 10, weekly_resets_in=12)},
                              strategy=S.RESETS_SOONEST, drain_early=True), "stay")


class LowThresholdTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)

    def one(self, b, usage):
        return plan(self.a, [b], {"A": sample(95, 40), b.id: usage})

    def test_low_candidates(self):
        self.assertEqual(self.one(acct("B", switch=20), sample(19, 10)), "windows/threshold: B")
        self.assertEqual(self.one(acct("B", switch=20), sample(20, 5)), "stay")
        self.assertEqual(self.one(acct("B", switch=10), sample(9, 5)), "windows/threshold: B")
        self.assertEqual(self.one(acct("B", switch=10), sample(10, 0)), "stay")
        self.assertEqual(self.one(acct("B", switch=1), sample(0.5, 0)), "windows/threshold: B")
        self.assertEqual(self.one(acct("B", switch=1), sample(1, 0)), "stay")

    def test_low_active(self):
        a5, c = acct("A", switch=5, active=True), acct("C")
        self.assertEqual(plan(a5, [c], {"A": sample(5, 1), "C": sample(0, 0)}), "windows/threshold: C")
        self.assertEqual(plan(a5, [c], {"A": sample(4, 1), "C": sample(0, 0)}), "stay")
        b_active, a6 = acct("B", active=True), acct("A", switch=6)
        self.assertEqual(plan(b_active, [a6], {"B": sample(95, 40), "A": sample(5.5, 0)}), "stay")
        self.assertEqual(plan(b_active, [a6], {"B": sample(95, 40), "A": sample(5, 0)}), "windows/threshold: A")

    def test_two_five_percent_accounts_never_ping_pong(self):
        a5, b5 = acct("A", switch=5, active=True), acct("B", switch=5)
        b5_active, a5_idle = acct("B", switch=5, active=True), acct("A", switch=5)
        self.assertEqual(plan(a5, [b5], {"A": sample(5, 0), "B": sample(2, 0)}), "windows/threshold: B")
        self.assertEqual(plan(b5_active, [a5_idle], {"B": sample(5, 0), "A": sample(5, 0)}), "stay")

    def test_low_drain(self):
        a_default = acct("A", active=True)

        def drain(y, usage):
            return plan(a_default, [y], {"A": sample(30, 40), "Y": usage}, strategy=S.RESETS_SOONEST, drain_early=True)

        y1, y10 = acct("Y", switch=1), acct("Y", switch=10)
        self.assertEqual(drain(y1, sample(0.5, 0.5, weekly_resets_in=12)), "windows/drainEarly: Y")
        self.assertEqual(drain(y1, sample(1, 0, weekly_resets_in=12)), "stay")
        self.assertEqual(drain(y10, sample(9, 9, weekly_resets_in=12)), "windows/drainEarly: Y")

    def test_fable_preference_uses_the_same_room(self):
        x, y = acct("X", switch=10), acct("Y", switch=10)
        usage = {"A": sample(95, 40, fable=50), "X": sample(1, 1, fable=4), "Y": sample(0, 0, fable=9.5)}
        self.assertEqual(plan(self.a, [x, y], usage), "windows/threshold: X,Y")

    def test_room_under_own_threshold(self):
        x10, y90 = acct("X", switch=10), acct("Y", switch=90)
        usage = {"A": sample(95, 40), "X": sample(2, 2), "Y": sample(70, 70)}
        self.assertEqual(plan(self.a, [x10, y90], usage), "windows/threshold: Y,X")


class NoSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.a = acct("A", active=True)
        self.b, self.c = acct("B"), acct("C")
        self.m0 = acct("M", switch=0)
        self.m_active = acct("M", switch=0, active=True)

    def test_left_with_no_reading_at_all(self):
        for s in S:
            self.assertEqual(plan(self.a, [self.b], {"B": sample(50, 50)}, strategy=s, no_subscription={"A"}),
                             "windows/noSubscription: B", s)
        self.assertEqual(plan(self.a, [self.b], {"A": sample(13, 3), "B": sample(0, 26)}, no_subscription={"A"}),
                         "windows/noSubscription: B")
        self.assertEqual(plan(self.a, [self.b], {"B": sample(50, 50)}), "stay")
        self.assertEqual(plan(self.a, [self.b], {"B": sample(50, 50)}, strategy=S.RESETS_SOONEST, drain_early=True,
                              no_subscription={"A"}), "windows/noSubscription: B")

    def test_room_needed(self):
        def one(u):
            return plan(self.a, [self.b], {"B": u}, no_subscription={"A"})

        self.assertEqual(one(sample(85, 85)), "windows/noSubscription: B")
        self.assertEqual(one(sample(89, 0)), "windows/noSubscription: B")
        self.assertEqual(one(sample(89.5, 0)), "stay")
        self.assertEqual(one(sample(90, 10)), "stay")
        self.assertEqual(one(sample(100, 100)), "stay")

    def test_manual_only(self):
        self.assertEqual(plan(self.m_active, [self.c], {"C": sample(50, 50)}, no_subscription={"M"}),
                         "windows/noSubscription: C")
        self.assertEqual(plan(self.a, [self.m0], {"M": sample(0, 0)}, no_subscription={"A"}), "stay")
        self.assertEqual(plan(self.a, [self.m0, self.c], {"M": sample(0, 0), "C": sample(50, 50)},
                              no_subscription={"A"}), "windows/noSubscription: C")

    def test_dead_candidates_and_missing_pieces(self):
        usage = {"A": sample(95, 40), "B": sample(0, 0), "C": sample(50, 50)}
        self.assertEqual(plan(self.a, [self.b, self.c], usage, no_subscription={"B"}), "windows/threshold: C")
        self.assertEqual(plan(self.a, [self.b], {"A": sample(95, 40), "B": sample(0, 0)}, no_subscription={"B"}), "stay")
        self.assertEqual(plan(self.a, [self.b], {"A": sample(30, 40), "B": sample(10, 10, weekly_resets_in=12)},
                              strategy=S.RESETS_SOONEST, drain_early=True, no_subscription={"B"}), "stay")
        self.assertEqual(plan(self.a, [self.b], {"B": sample(0, 0)}, no_subscription={"A", "B"}), "stay")
        self.assertEqual(plan(self.a, [self.b], {"B": sample(0, 0)}, switchable=lambda x: x.id != "B",
                              no_subscription={"A"}), "stay")
        self.assertEqual(plan(self.a, [self.c], {}, no_subscription={"A"}), "stay")

    def test_targets_follow_the_strategy(self):
        ranked = {"B": sample(80, 80, weekly_resets_in=12), "C": sample(20, 20)}
        self.assertEqual(plan(self.a, [self.b, self.c], ranked, strategy=S.MOST_ROOM, no_subscription={"A"}),
                         "windows/noSubscription: C,B")
        self.assertEqual(plan(self.a, [self.b, self.c], ranked, strategy=S.MY_ORDER, no_subscription={"A"}),
                         "windows/noSubscription: B,C")
        self.assertEqual(plan(self.a, [self.c, self.b], ranked, strategy=S.RESETS_SOONEST, no_subscription={"A"}),
                         "windows/noSubscription: B,C")
        fable = {"B": sample(10, 10, fable=100), "C": sample(40, 40, fable=20)}
        self.assertEqual(plan(self.a, [self.b, self.c], fable, no_subscription={"A"}), "windows/noSubscription: C,B")
        self.assertEqual(plan(self.a, [self.b], {"B": sample(10, 10, fable=100)}, no_subscription={"A"}),
                         "windows/noSubscription: B")

    def test_verification_rule(self):
        defaults = engine.resolve_defaults(90)
        rule = engine.resolve_rule(None, None, None, defaults)
        manual = engine.resolve_rule(0, None, None, defaults)
        common = dict(hysteresis=10.0, drain_within=24 * 3600, watch_fable=True, now=NOW, active_weekly_reset=None)

        def verify(u, r=rule, limit=Limit.WINDOWS):
            return engine.eligible_utilization(u, limit, Trigger.NO_SUBSCRIPTION, r, **common)

        self.assertIsNotNone(verify(sample(89, 0)))
        self.assertIsNone(verify(sample(89.5, 0)))
        self.assertIsNone(verify(sample(0, 0), manual))
        self.assertIsNone(verify(sample(0, 0, fable=0), limit=Limit.FABLE))
        self.assertEqual(Trigger.NO_SUBSCRIPTION.value, "noSubscription")

    def test_cooldown(self):
        hold = engine.cooldown_holds
        self.assertTrue(hold(NOW - 60, 300, False, NOW))
        self.assertFalse(hold(NOW - 301, 300, False, NOW))
        self.assertFalse(hold(None, 300, False, NOW))
        self.assertFalse(hold(NOW - 60, 300, True, NOW))  # never on a dead account


class SwitchAtYourNumberTests(unittest.TestCase):
    """Beeco: default 98%, Most room left (founder, 2026-09-29)."""

    def setUp(self):
        self.vk = acct("VK", active=True)
        self.racer, self.blarg, self.gobeeco = acct("Racer"), acct("Blarg"), acct("Gobeeco")
        self.pv, self.ahamade = acct("PV"), acct("Ahamade")
        self.others = [self.racer, self.blarg, self.gobeeco, self.pv, self.ahamade]

    def beeco(self, active):
        return {"VK": active, "Racer": sample(0, 96, fable=11), "Blarg": sample(0, 98, fable=34),
                "Gobeeco": sample(0, 96, fable=19), "PV": sample(0, 92, fable=0), "Ahamade": sample(0, 99, fable=2)}

    def test_beeco_cases(self):
        self.assertEqual(plan(self.vk, self.others, self.beeco(sample(98, 90, fable=0)), default_threshold=98),
                         "windows/threshold: PV,Racer,Gobeeco")
        self.assertEqual(plan(self.vk, self.others, self.beeco(sample(99, 90, fable=0)), default_threshold=99),
                         "windows/threshold: PV,Racer,Gobeeco,Blarg")
        self.assertEqual(plan(self.vk, self.others, self.beeco(sample(97, 90, fable=0)), default_threshold=98), "stay")
        self.assertEqual(plan(self.vk, self.others, self.beeco(sample(40, 98, fable=0)), default_threshold=98),
                         "windows/threshold: PV,Racer,Gobeeco")

    def test_one_point_under(self):
        def one(u, own=None):
            t = acct("T", switch=own)
            return plan(self.vk, [t], {"VK": sample(98, 60), "T": u}, default_threshold=98)

        self.assertEqual(one(sample(0, 97)), "windows/threshold: T")
        self.assertEqual(one(sample(97, 0)), "windows/threshold: T")
        self.assertEqual(one(sample(0, 97.5)), "stay")
        self.assertEqual(one(sample(0, 98)), "stay")
        self.assertEqual(one(sample(100, 0)), "stay")
        self.assertEqual(one(sample(79, 0), own=80), "windows/threshold: T")
        self.assertEqual(one(sample(80, 0), own=80), "stay")
        self.assertEqual(one(sample(0, 0), own=0), "stay")

    def test_unusable_unswitchable_unread(self):
        usage = {"VK": sample(98, 60), "PV": sample(0, 92)}
        self.assertEqual(plan(self.vk, [self.pv], usage, default_threshold=98, no_subscription={"PV"}), "stay")
        self.assertEqual(plan(self.vk, [self.pv], usage, default_threshold=98, switchable=lambda a: a.id != "PV"), "stay")
        self.assertEqual(plan(self.vk, [self.pv], {"VK": sample(98, 60)}, default_threshold=98), "stay")

    def test_fable(self):
        ok = {"VK": sample(10, 10, fable=98), "PV": sample(0, 92, fable=95)}
        self.assertEqual(plan(self.vk, [self.pv], ok, default_threshold=98), "fable/threshold: PV")
        bad = {"VK": sample(10, 10, fable=98), "PV": sample(0, 98, fable=50)}
        self.assertEqual(plan(self.vk, [self.pv], bad, default_threshold=98), "stay")

    def test_two_accounts_at_the_number_never_swap(self):
        a, b = acct("A", active=True), acct("B")
        self.assertEqual(plan(a, [b], {"A": sample(98, 50), "B": sample(98, 50)}, default_threshold=98), "stay")

    def test_exhausted_trigger_is_gone(self):
        with self.assertRaises(ValueError):
            Trigger("exhausted")


class FableAndExpiryTests(unittest.TestCase):
    """Tests/UnitTests/main.swift plan cases (Fable follows the same expiry rule)."""

    def setUp(self):
        self.active = acct("ACT", active=True)
        self.e = acct("E")

    def test_retained_fable_without_reset_never_triggers(self):
        retained = {"ACT": sample(10, 50, fable=99, fable_resets_in=None), "E": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.active, [self.e], retained, sampled=False), "stay")
        self.assertEqual(plan(self.active, [self.e], retained, sampled=True), "fable/threshold: E")

    def test_expired_fable_never_triggers(self):
        expired = {"ACT": sample(10, 50, fable=100, fable_resets_in=-1), "E": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.active, [self.e], expired), "stay")

    def test_no_allowance_no_trigger(self):
        self.assertEqual(plan(self.active, [self.e], {"ACT": sample(10, 50), "E": sample(5, 10, fable=20)}), "stay")

    def test_fable_switch_off(self):
        usage = {"ACT": sample(10, 50, fable=99), "E": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.active, [self.e], usage, watch_fable=True), "fable/threshold: E")
        self.assertEqual(plan(self.active, [self.e], usage, watch_fable=False), "stay")
        windows = {"ACT": sample(99, 50, fable=99), "E": sample(5, 10, fable=20)}
        self.assertEqual(plan(self.active, [self.e], windows, watch_fable=False), "windows/threshold: E")


if __name__ == "__main__":
    unittest.main()
