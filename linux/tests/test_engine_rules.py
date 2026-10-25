"""Spec 2.1 and 2.2: the layered threshold resolution, the split rule, and the settings readers
of AutoSwitchSettings (kept pure: values in, values out)."""
from __future__ import annotations

import math
import unittest

from pixelswitch import engine
from pixelswitch.engine import Defaults, Rule, resolve_defaults, resolve_rule


class NormalisationTests(unittest.TestCase):
    def test_clamped_threshold(self):
        c = engine.clamped_threshold
        self.assertEqual([c(49), c(101), c(75), c(float("nan")), c(None), c("x")], [50, 100, 75, 90, 90, 90])

    def test_normalized_account_threshold(self):
        n = engine.normalized_account_threshold
        self.assertEqual([n(-5), n(0), n(0.4), n(0.6), n(35), n(72.6), n(120)], [0, 0, 0, 1, 35, 73, 100])
        self.assertIsNone(n(None))
        self.assertIsNone(n(float("nan")))
        self.assertIsNone(n(True))
        self.assertEqual(math.copysign(1, n(-0.3)), 1.0)  # never -0

    def test_manual_only(self):
        m = engine.is_manual_only
        self.assertTrue(m(0) and m(0.4))
        self.assertFalse(m(None) or m(1) or m(50) or m(100))

    def test_round_half_away(self):
        r = engine.round_half_away
        self.assertEqual([r(0.5), r(1.5), r(2.5), r(-0.5), r(-1.5), r(0.49999999999999994)], [1, 2, 3, -1, -2, 0])

    def test_window_value(self):
        w = engine.window_value
        self.assertIsNone(w(0))
        self.assertIsNone(w(-5))
        self.assertIsNone(w(0.4))
        self.assertEqual(w(0.5), 1)
        self.assertEqual(w(100.4), 100)
        self.assertEqual(w(250), 100)
        self.assertEqual(w(72.5), 73)
        self.assertIsNone(w(None))
        self.assertIsNone(w(float("inf")))
        self.assertIsNone(w("90"))
        self.assertIsNone(w(True))


class DefaultsTests(unittest.TestCase):
    def test_unset_reads_90(self):
        d = resolve_defaults()
        self.assertEqual((d.base, d.session, d.weekly), (90, 90, 90))

    def test_base_only_feeds_both_windows(self):
        """The Beeco Mac: only autoSwitchThreshold = 98 stored."""
        d = resolve_defaults(98)
        self.assertEqual((d.base, d.session, d.weekly), (98, 98, 98))

    def test_base_zero_reads_90_and_clamps(self):
        self.assertEqual(resolve_defaults(0).base, 90)
        self.assertEqual(resolve_defaults(30).base, 50)
        self.assertEqual(resolve_defaults(150).base, 100)
        self.assertEqual(resolve_defaults(float("nan")).base, 90)

    def test_window_keys_override_the_base(self):
        d = resolve_defaults(98, session=90, weekly=50)
        self.assertEqual((d.base, d.session, d.weekly), (98, 90, 50))
        d = resolve_defaults(80, weekly=60)
        self.assertEqual((d.session, d.weekly), (80, 60))

    def test_window_key_counts_as_set_only_when_a_finite_nonzero_number(self):
        for raw in (None, 0, 0.0, float("nan"), float("inf"), "70", True):
            d = resolve_defaults(75, session=raw, weekly=raw)
            self.assertEqual((d.session, d.weekly), (75, 75), raw)

    def test_window_default_is_clamped_to_50_100(self):
        d = resolve_defaults(90, session=20, weekly=500)
        self.assertEqual((d.session, d.weekly), (50, 100))


class RuleResolutionTests(unittest.TestCase):
    """2.2: account window -> account both -> global window -> global base -> 90."""

    def setUp(self):
        self.d = resolve_defaults(90, session=85, weekly=60)

    def test_each_layer(self):
        r = resolve_rule
        self.assertEqual(r(None, None, None, self.d), Rule(85, 60))                 # global windows
        self.assertEqual(r(70, None, None, self.d), Rule(70, 70))                   # account both
        self.assertEqual(r(70, 40, None, self.d), Rule(40, 70))                     # account window over both
        self.assertEqual(r(70, None, 55, self.d), Rule(70, 55))
        self.assertEqual(r(None, 30, 20, self.d), Rule(30, 20))                     # windows, no both
        self.assertEqual(r(None, 30, None, self.d), Rule(30, 60))                   # window, then global weekly
        self.assertEqual(r(None, None, None, resolve_defaults()), Rule(90, 90))     # base 90

    def test_manual_only_wins_over_window_values(self):
        self.assertEqual(resolve_rule(0, 40, 30, self.d), Rule(0, 0, True))
        self.assertEqual(resolve_rule(0.4, None, None, self.d), Rule(0, 0, True))
        self.assertEqual(resolve_rule(-5, None, None, self.d), Rule(0, 0, True))

    def test_window_values_that_are_not_usable_follow_the_next_layer(self):
        for bad in (0, -5, 0.4, float("nan"), None):
            self.assertEqual(resolve_rule(None, bad, bad, self.d), Rule(85, 60), bad)
        self.assertEqual(resolve_rule(None, 100.4, 100.4, self.d), Rule(100, 100))
        self.assertEqual(resolve_rule(70, -5, 0, self.d), Rule(70, 70))

    def test_both_value_is_rounded(self):
        self.assertEqual(resolve_rule(72.6, None, None, self.d), Rule(73, 73))
        self.assertEqual(resolve_rule(150, None, None, self.d), Rule(100, 100))

    def test_leave_at(self):
        d = self.d
        self.assertEqual(engine.leave_at(Rule(70, 50), d), engine.LeaveAt(70, 50))
        self.assertEqual(engine.leave_at(Rule(0, 0, True), d), engine.LeaveAt(85, 60))  # Manual only leaves at defaults

    def test_relaxed_rule_and_defaults(self):
        self.assertEqual(engine.relaxed_rule(Rule(90, 50)), Rule(90, 90))
        self.assertEqual(engine.relaxed_rule(Rule(70, 95)), Rule(70, 95))
        self.assertEqual(engine.relaxed_rule(Rule(0, 0, True)), Rule(0, 0, True))
        self.assertEqual(engine.relaxed_defaults(Defaults(90, 90, 50)), Defaults(90, 90, 90))
        self.assertEqual(engine.relaxed_defaults(Defaults(90, 70, 95)), Defaults(90, 70, 95))


class SplitRuleTests(unittest.TestCase):
    """2.1 "Splitting the single value"."""

    def test_split_copies_into_nil_windows_and_clears_the_single_value(self):
        self.assertEqual(engine.split_single_threshold(70, None, None), (None, 70, 70))
        self.assertEqual(engine.split_single_threshold(70, 40, None), (None, 40, 70))
        self.assertEqual(engine.split_single_threshold(70, None, 55), (None, 70, 55))
        self.assertEqual(engine.split_single_threshold(70, 40, 55), (None, 40, 55))

    def test_split_leaves_manual_only_and_nil_alone(self):
        self.assertEqual(engine.split_single_threshold(0, None, None), (0, None, None))
        self.assertEqual(engine.split_single_threshold(None, 40, None), (None, 40, None))

    def test_split_uses_the_whole_number(self):
        self.assertEqual(engine.split_single_threshold(72.6, None, None), (None, 73, 73))


class ExplanationTests(unittest.TestCase):
    def test_default_account(self):
        d = resolve_defaults(90)
        e = engine.explanation(resolve_rule(None, None, None, d), d, engine.Strategy.MOST_ROOM, True)
        self.assertEqual((e.leave_at_session, e.leave_at_weekly), (90, 90))
        self.assertEqual((e.arrive_at_session, e.arrive_at_weekly), (89, 89))
        self.assertIsNone(e.drain_arrive_at_session)

    def test_split_numbers_and_overflow_number(self):
        d = resolve_defaults(90, weekly=50)
        e = engine.explanation(resolve_rule(None, None, None, d), d)
        self.assertEqual((e.leave_at_session, e.leave_at_weekly), (90, 50))
        self.assertEqual((e.arrive_at_session, e.arrive_at_weekly), (89, 49))
        self.assertEqual(e.overflow_arrive_at_weekly, 89)

    def test_low_threshold_numbers(self):
        d = resolve_defaults(90)
        e = engine.explanation(resolve_rule(10, None, None, d), d, engine.Strategy.RESETS_SOONEST, True)
        self.assertEqual((e.leave_at_session, e.arrive_at_session), (10, 9))
        self.assertIsNone(e.drain_arrive_at_session)  # equals arriveAt since 1.8

    def test_manual_only_has_none(self):
        d = resolve_defaults(90)
        self.assertIsNone(engine.explanation(resolve_rule(0, None, None, d), d))

    def test_out_of_range_default_reads_as_the_engine_reads_it(self):
        d = resolve_defaults(30)
        self.assertEqual(engine.explanation(resolve_rule(None, None, None, d), d).leave_at_session, 50)


class ReadingTests(unittest.TestCase):
    def test_expiry_rule(self):
        from pixelswitch.usage_model import UsageWindow
        from tests.support import NOW, iso

        w = engine.reading
        future = UsageWindow(40, iso(2))
        past = UsageWindow(40, iso(-1))
        unknown = UsageWindow(40, None)
        garbage = UsageWindow(40, "tomorrow")
        self.assertEqual(w(future, NOW), 40)
        self.assertIsNone(w(past, NOW))
        self.assertEqual(w(unknown, NOW), 40)               # lenient: a candidate
        self.assertIsNone(w(unknown, NOW, True))            # strict: the retained active side
        self.assertIsNone(w(garbage, NOW, True))
        self.assertEqual(w(garbage, NOW), 40)
        self.assertIsNone(w(UsageWindow(None, iso(2)), NOW))
        self.assertIsNone(w(None, NOW))

    def test_binding_utilization(self):
        from tests.support import NOW, sample

        self.assertEqual(engine.binding_utilization(sample(30, 55), NOW), 55)
        self.assertEqual(engine.binding_utilization(sample(None, 55), NOW), 55)
        self.assertIsNone(engine.binding_utilization(sample(None, None), NOW))
        self.assertIsNone(engine.binding_utilization(None, NOW))


if __name__ == "__main__":
    unittest.main()
