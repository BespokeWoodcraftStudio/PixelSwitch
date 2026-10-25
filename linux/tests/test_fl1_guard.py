"""Lane FL1: the guard's account-less 429 store (spec D20 revision 4), the offline mapping of a
resolver failure, and the two guard branches no test reached. Ports UsageGuardTests.swift's
"429 with no account" cases (UsageRequestGuard.swift:94-103, 135-138, 153, 203-216)."""
from __future__ import annotations

import socket
import unittest
import urllib.error

from pixelswitch import usage_guard as ug
from pixelswitch.usage_guard import (
    REFRESH_FIRST, REQUEST, WAIT_FOR_CLAUDE_CODE, UsageRequestError, UsageRequestGuard, cooling_down, parked,
)

from .test_guard import HOUR, MINUTE, T0, login


class AccountLessRateLimitTests(unittest.TestCase):
    def test_a_429_with_no_account_parks_the_token_for_every_caller(self):
        g = UsageRequestGuard()
        until = g.record_rate_limited(None, "T", 600, T0)
        self.assertEqual(until, T0 + 600)
        self.assertFalse(g.may_use("T", None, T0 + MINUTE))
        self.assertFalse(g.may_use("T", None, T0 + 11 * MINUTE))   # past the park, inside the cool-down
        self.assertTrue(g.may_use("T", None, T0 + 21 * MINUTE))
        self.assertTrue(g.may_use("U", None, T0 + MINUTE))

    def test_decision_answers_parked_then_cooling_down_after_the_accounts_own_checks(self):
        g = UsageRequestGuard()
        g.record_rate_limited(None, "T", 600, T0)
        valid = login("T", 6 * HOUR)
        self.assertEqual(g.decision("id", False, valid, T0 + MINUTE), parked(T0 + 600))
        self.assertEqual(g.decision("id", False, valid, T0 + 11 * MINUTE), cooling_down(T0 + 20 * MINUTE))
        self.assertEqual(g.decision("id", False, valid, T0 + 21 * MINUTE), REQUEST)

    def test_the_accounts_own_park_comes_first(self):
        g = UsageRequestGuard()
        g.record_rate_limited(None, "T", 60, T0)
        g.record_rate_limited("id", "T", 3000, T0)
        self.assertEqual(g.decision("id", False, login("T", 6 * HOUR), T0 + MINUTE), parked(T0 + 3000))

    def test_a_dead_live_login_still_waits_for_claude_code_before_the_account_less_park(self):
        g = UsageRequestGuard()
        g.record_rate_limited(None, "T", 600, T0)
        g.record_rejected("id", "T", T0)
        self.assertEqual(g.decision("id", True, login("T", 6 * HOUR), T0 + MINUTE), WAIT_FOR_CLAUDE_CODE)

    def test_a_saved_login_that_is_expired_is_still_parked_not_renewed_inside_the_park(self):
        g = UsageRequestGuard()
        g.record_rate_limited(None, "T", 600, T0)
        # parked first, as for an account-owned park (the renewed token carries no park)
        self.assertEqual(g.decision("id", False, login("T", 30), T0 + MINUTE).kind, "parked")
        self.assertEqual(g.decision("id", False, login("T", 30), T0 + 21 * MINUTE), REFRESH_FIRST)

    def test_the_park_is_clamped_like_an_accounts(self):
        g = UsageRequestGuard()
        self.assertEqual(g.record_rate_limited(None, "A", 999999, T0), T0 + 3600)
        self.assertEqual(g.record_rate_limited(None, "B", 1, T0), T0 + 60)
        self.assertEqual(g.record_rate_limited(None, "C", None, T0), T0 + 120)
        self.assertEqual(g.record_rate_limited(None, "D", float("inf"), T0), T0 + 3600)

    def test_at_most_64_tokens_the_earliest_cooldown_goes_first(self):
        g = UsageRequestGuard()
        for i in range(64):
            g.record_rate_limited(None, f"t{i}", 3600, T0 + i)
        self.assertEqual(len(g._rate_limited_without_account), 64)
        g.record_rate_limited(None, "new", 3600, T0 + 100)
        self.assertEqual(len(g._rate_limited_without_account), 64)
        self.assertTrue(g.may_use("t0", None, T0 + 101))          # the oldest was dropped
        self.assertFalse(g.may_use("t1", None, T0 + 101))
        self.assertFalse(g.may_use("new", None, T0 + 101))

    def test_ended_entries_are_pruned_on_the_next_record(self):
        g = UsageRequestGuard()
        g.record_rate_limited(None, "old", 60, T0)
        g.record_rate_limited(None, "new", 60, T0 + 2 * HOUR)
        self.assertEqual(set(g._rate_limited_without_account), {ug.fingerprint_of("new")})


class RefusedTokenBranchTests(unittest.TestCase):
    """LA review, tests P2 (mutants G11 and G12): two guard branches no test reached."""

    def test_a_token_refused_for_an_account_is_not_sent_by_a_caller_with_no_account_after_the_cooldown(self):
        g = UsageRequestGuard()
        g.record_rejected("id", "T", T0)
        self.assertFalse(g.may_use("T", None, T0 + 21 * MINUTE))
        self.assertTrue(g.may_use("U", None, T0 + 21 * MINUTE))

    def test_a_live_login_that_is_parked_and_refused_waits_for_claude_code(self):
        g = UsageRequestGuard()
        g.record_rate_limited("id", "T", 3600, T0)
        g.record_rejected("id", "T", T0)
        self.assertEqual(g.decision("id", True, login("T", 5 * HOUR), T0 + MINUTE), WAIT_FOR_CLAUDE_CODE)


class OfflineMappingTests(unittest.TestCase):
    def test_a_resolver_that_cannot_answer_is_offline(self):
        error = urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"))
        self.assertTrue(UsageRequestError.from_transport_error(error).offline)
        self.assertTrue(UsageRequestError.from_transport_error(
            socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")).offline)

    def test_an_unknown_host_is_not_offline(self):
        error = urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
        self.assertFalse(UsageRequestError.from_transport_error(error).offline)


class RetryAfterWordTests(unittest.TestCase):
    def test_inf_reads_as_infinity_and_parks_for_the_longest(self):
        self.assertEqual(ug.parse_retry_after("inf"), float("inf"))
        self.assertEqual(ug.parse_retry_after(" Infinity "), float("inf"))
        self.assertEqual(ug.parse_retry_after("60"), 60.0)
        self.assertIsNone(ug.parse_retry_after("soon"))
        self.assertEqual(UsageRequestError.from_status(429, "", "inf").retry_after, float("inf"))


if __name__ == "__main__":
    unittest.main()
