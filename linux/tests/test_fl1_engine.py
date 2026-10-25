"""Lane FL1: rule 10's wording (AutoSwitchEngine.stayingPutKind, Tests/UnitTests/RemovalAndBackupTests.swift
381-404 are the Mac's cases) and two engine cases no test reached (LA review, tests P2)."""
from __future__ import annotations

import unittest

from pixelswitch import engine, staying_put
from pixelswitch.engine import Strategy
from pixelswitch.staying_put import NO_ROOM, NOT_AT_LIMIT, WAITING_FOR_SESSION_LIMIT, staying_put_kind
from tests.support import NOW, acct, describe, sample
from tests.test_engine_split import go, line


def kind(usage, defaults=(90, 50), rule=(None, None, None), fable=False, sampled=True):
    d = engine.resolve_defaults(None, *defaults) if defaults[0] != defaults[1] else engine.resolve_defaults(defaults[0])
    r = engine.resolve_rule(*rule, d)
    return staying_put_kind(usage, r, d, sampled, fable, NOW)


class StayingPutKindTests(unittest.TestCase):
    def test_not_at_a_limit(self):
        self.assertEqual(kind(sample(60, 40)), NOT_AT_LIMIT)
        self.assertEqual(kind(None), NOT_AT_LIMIT)

    def test_at_the_strict_weekly_limit_but_under_the_relaxed_ones_waits_for_the_session_limit(self):
        self.assertEqual(kind(sample(60, 70)), WAITING_FOR_SESSION_LIMIT)

    def test_at_the_session_limit_is_past_the_relaxed_limits_too(self):
        self.assertEqual(kind(sample(95, 20)), NO_ROOM)
        self.assertEqual(kind(sample(95, 60)), NO_ROOM)

    def test_equal_windows_are_always_no_room_once_a_limit_is_reached(self):
        self.assertEqual(kind(sample(98, 10), defaults=(98, 98)), NO_ROOM)
        self.assertEqual(kind(sample(10, 98), defaults=(98, 98)), NO_ROOM)
        self.assertEqual(kind(sample(10, 50), defaults=(98, 98)), NOT_AT_LIMIT)

    def test_a_fable_only_trigger_is_no_room_never_overflow(self):
        self.assertEqual(kind(sample(10, 10, fable=95), fable=True), NO_ROOM)
        self.assertEqual(kind(sample(10, 10, fable=95), fable=False), NOT_AT_LIMIT)

    def test_fable_past_the_relaxed_weekly_limit_with_the_weekly_window_at_its_strict_limit(self):
        # weekly strict fired (70 >= 50); relaxed weekly is 90; fable 95 is past it
        self.assertEqual(kind(sample(10, 70, fable=95), fable=True), NO_ROOM)
        self.assertEqual(kind(sample(10, 70, fable=60), fable=True), WAITING_FOR_SESSION_LIMIT)

    def test_a_manual_only_account_judges_at_the_defaults(self):
        self.assertEqual(kind(sample(60, 70), rule=(0, None, None)), WAITING_FOR_SESSION_LIMIT)

    def test_a_window_without_a_known_reset_counts_only_when_sampled_this_cycle(self):
        stale = sample(60, 70, weekly_resets_in=None)
        self.assertEqual(kind(stale, sampled=True), WAITING_FOR_SESSION_LIMIT)
        self.assertEqual(kind(stale, sampled=False), NOT_AT_LIMIT)

    def test_the_names_are_the_macs(self):
        self.assertEqual((staying_put.NOT_AT_LIMIT, staying_put.NO_ROOM, staying_put.WAITING_FOR_SESSION_LIMIT),
                         ("notAtLimit", "noRoom", "waitingForSessionLimit"))


class ManualOnlyRaisedDefaultsTests(unittest.TestCase):
    """Rule 9: a Manual only active account leaves at the RAISED defaults in the relaxed pass
    (LA review, test_engine_split.py:245, mutant E5)."""

    def setUp(self):
        self.m = acct("M", switch=0, active=True)
        self.y = acct("Y")

    def test_it_stays_at_sixty_percent_of_its_week(self):
        self.assertIsNone(go(self.m, [self.y], {"M": sample(20, 60), "Y": sample(20, 70)}))

    def test_it_moves_when_it_reaches_the_session_default_and_relaxed(self):
        self.assertEqual(line(go(self.m, [self.y], {"M": sample(90, 60), "Y": sample(20, 70)})),
                         "windows/threshold: Y [session] relaxed")


class ResetsSoonestWholeMinutesTests(unittest.TestCase):
    """LA review, test_engine_split.py:319 (mutant E7): exact seconds would give B,C."""

    def test_whole_minutes_not_exact_seconds(self):
        a, b, c = acct("A", active=True), acct("B"), acct("C")
        usage = {"A": sample(95, 10), "B": sample(10, 30, weekly_resets_in=30 + 10 / 3600),
                 "C": sample(10, 20, weekly_resets_in=30 + 50 / 3600)}
        self.assertEqual(describe(go(a, [b, c], usage, strategy=Strategy.RESETS_SOONEST)), "windows/threshold: C,B")


if __name__ == "__main__":
    unittest.main()
