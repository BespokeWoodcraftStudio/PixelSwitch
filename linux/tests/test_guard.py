"""The usage-request guard: which access tokens may be sent to Anthropic, and what a card says
when one is not. A case-by-case port of Tests/UnitTests/UsageGuardTests.swift.

Background (docs/investigations/2026-10-16-usage-429.md): Anthropic answers roughly the fourth
refused (401) call on one access token within about an hour with 429 and Retry-After 3600.
PixelSwitch kept sending tokens it could know were dead, so it earned those 429s itself.
"""
from __future__ import annotations

import unittest

from pixelswitch import usage_guard as ug
from pixelswitch.logins import Login
from pixelswitch.usage_guard import (
    NEEDS_REAUTH,
    REFRESH_FIRST,
    REQUEST,
    WAIT_FOR_CLAUDE_CODE,
    BackupRefreshOutcome,
    NoticeCurrent,
    UsageRequestGuard,
    choose_notice,
    cooling_down,
    parked,
)

T0 = 1_790_000_000.0  # a fixed "now"
HOUR = 3600.0
MINUTE = 60.0


def ms(moment: float) -> float:
    return moment * 1000


def login(token, expires_in, from_now=T0):
    """A login whose access token is ``token``, expiring ``expires_in`` seconds after ``from_now``
    (None = the credential carries no expiresAt)."""
    return Login(access_token=token, refresh_token=f"refresh-{token}",
                 expires_at=None if expires_in is None else ms(from_now + expires_in))


class FingerprintTests(unittest.TestCase):
    def test_sixteen_hex_characters(self):
        fp = ug.fingerprint_of("sk-ant-oat01-SECRETSECRETSECRET")
        self.assertEqual(len(fp), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_stable_distinct_and_holds_none_of_the_token(self):
        fp = ug.fingerprint_of("sk-ant-oat01-SECRETSECRETSECRET")
        self.assertNotIn("SECRET", fp)
        self.assertEqual(fp, ug.fingerprint_of("sk-ant-oat01-SECRETSECRETSECRET"))
        self.assertNotEqual(fp, ug.fingerprint_of("sk-ant-oat01-SECRETSECRETSECRET2"))

    def test_log_gets_at_most_eight(self):
        self.assertEqual(len(ug.fingerprint_for_log(ug.fingerprint_of("x"))), 8)

    def test_known_value(self):
        # SHA-256("abc") starts ba7816bf8f01cfea
        self.assertEqual(ug.fingerprint_of("abc"), "ba7816bf8f01cfea")


class ExpiryTests(unittest.TestCase):
    def test_expired_live_waits_and_expired_saved_renews(self):
        g = UsageRequestGuard()
        self.assertEqual(g.decision("live", True, login("A", -10 * MINUTE), T0), WAIT_FOR_CLAUDE_CODE)
        self.assertEqual(g.decision("backup", False, login("B", -10 * MINUTE), T0), REFRESH_FIRST)

    def test_good_for_hours_is_asked(self):
        g = UsageRequestGuard()
        self.assertEqual(g.decision("backup", False, login("B2", 2 * HOUR), T0), REQUEST)
        self.assertEqual(g.decision("live", True, login("A2", 2 * HOUR), T0), REQUEST)

    def test_within_sixty_seconds_counts_as_expired(self):
        g = UsageRequestGuard()
        self.assertEqual(g.decision("backup", False, login("B3", 59), T0), REFRESH_FIRST)
        self.assertEqual(g.decision("live", True, login("A3", 59), T0), WAIT_FOR_CLAUDE_CODE)

    def test_sixty_one_seconds_left_is_still_asked(self):
        g = UsageRequestGuard()
        self.assertEqual(g.decision("backup", False, login("B4", 61), T0), REQUEST)

    def test_missing_expires_at_is_allowed(self):
        g = UsageRequestGuard()
        self.assertEqual(g.decision("backup", False, login("B5", None), T0), REQUEST)
        self.assertEqual(g.decision("live", True, login("A5", None), T0), REQUEST)

    def test_is_expired_boundaries(self):
        self.assertFalse(UsageRequestGuard.is_expired(None, T0))
        self.assertFalse(UsageRequestGuard.is_expired(ms(T0 + 60), T0))     # strict: exactly 60 s left is not expired
        self.assertTrue(UsageRequestGuard.is_expired(ms(T0 + 59.999), T0))
        self.assertTrue(UsageRequestGuard.is_expired(ms(T0 + 59), T0))
        self.assertFalse(UsageRequestGuard.is_expired(ms(T0 + 61), T0))


class RefusedTokenTests(unittest.TestCase):
    """Rule a: a refused token is never sent again, until the token changes."""

    def test_never_asked_again(self):
        for is_active in (False, True):
            g = UsageRequestGuard()
            dead = login("DEAD", None)  # revoked before its expiry, so no expiresAt to warn us
            self.assertEqual(g.decision("id", is_active, dead, T0), REQUEST)
            g.record_rejected("id", "DEAD", T0)
            after = [g.decision("id", is_active, dead, T0 + dt)
                     for dt in (1.0, 5 * MINUTE, 19 * MINUTE, 21 * MINUTE, 2 * HOUR, 3 * 24 * HOUR)]
            self.assertTrue(all(d != REQUEST for d in after), (is_active, after))
            self.assertEqual(after[-1], WAIT_FOR_CLAUDE_CODE if is_active else REFRESH_FIRST)
            renewed = login("FRESH", 8 * HOUR, T0 + 30 * MINUTE)
            self.assertEqual(g.decision("id", is_active, renewed, T0 + 30 * MINUTE), REQUEST)

    def test_a_401_with_no_account_still_blocks_that_token(self):
        g = UsageRequestGuard()
        g.record_rejected(None, "ORPHAN", T0)
        self.assertEqual(g.decision("id", True, login("ORPHAN", None), T0 + HOUR), WAIT_FOR_CLAUDE_CODE)
        self.assertFalse(g.may_use("ORPHAN", now=T0 + HOUR))
        self.assertTrue(g.may_use("OTHER", now=T0 + HOUR))

    def test_account_less_refusals_are_a_fifo_of_64(self):
        g = UsageRequestGuard()
        for number in range(70):
            g.record_rejected(None, f"T{number}", T0)
        self.assertTrue(g.may_use("T0", now=T0))      # forgotten: the oldest went first
        self.assertTrue(g.may_use("T5", now=T0))
        self.assertFalse(g.may_use("T6", now=T0))
        self.assertFalse(g.may_use("T69", now=T0))
        g.record_rejected(None, "T69", T0)             # a repeat is not stored twice
        self.assertFalse(g.may_use("T6", now=T0))


class ParkTests(unittest.TestCase):
    """Rule c: a 429 park belongs to its token."""

    def test_park_follows_its_token(self):
        g = UsageRequestGuard()
        until = g.record_rate_limited("id", "T1", 3600, T0)
        self.assertEqual(until, T0 + 3600)
        self.assertEqual(g.decision("id", False, login("T1", 5 * HOUR), T0 + 30 * MINUTE), parked(until))
        self.assertEqual(g.decision("id", False, login("T2", 5 * HOUR), T0 + 30 * MINUTE), REQUEST)
        # the old park does not come back: it was forgotten with the old token
        self.assertEqual(g.decision("id", False, login("T1", 5 * HOUR), T0 + 30 * MINUTE), REQUEST)

    def test_park_clamps(self):
        g = UsageRequestGuard()
        self.assertEqual(g.record_rate_limited("id", "T3", None, T0), T0 + 120)
        self.assertEqual(g.record_rate_limited("id", "T3", 0, T0), T0 + 60)
        self.assertEqual(g.record_rate_limited("id", "T3", 999_999, T0), T0 + 3600)


class CooldownTests(unittest.TestCase):
    """Rule d: 20 minutes after a 401 or 429, whatever the number of accounts."""

    def test_even_after_a_short_retry_after(self):
        g = UsageRequestGuard()
        g.record_rate_limited("id", "T", 0, T0)
        valid = login("T", 6 * HOUR)
        self.assertEqual(g.decision("id", False, valid, T0 + 2 * MINUTE), cooling_down(T0 + 20 * MINUTE))

    def test_ends_at_twenty_minutes(self):
        g = UsageRequestGuard()
        g.record_rate_limited("id", "T", 0, T0)
        valid = login("T", 6 * HOUR)
        self.assertNotEqual(g.decision("id", False, valid, T0 + 20 * MINUTE - 1), REQUEST)
        self.assertEqual(g.decision("id", False, valid, T0 + 20 * MINUTE + 1), REQUEST)

    def test_profile_lookup_is_held_by_the_same_cooldown(self):
        g = UsageRequestGuard()
        g.record_rate_limited("id", "T", 0, T0)
        self.assertFalse(g.may_use("T", now=T0 + MINUTE))
        self.assertTrue(g.may_use("T", now=T0 + 21 * MINUTE))

    def test_round_robin_speed_up_never_sends_a_429d_token_inside_its_cooldown(self):
        """A visit EVERY MINUTE, at any number of accounts, must not send a 429'd token inside its 20 minutes."""
        for others in range(1, 6):
            g = UsageRequestGuard()
            accounts = [f"acct{i}" for i in range(others)]
            victim = accounts[0]
            g.record_rate_limited(victim, "VICTIM", 60, T0)
            within = after = 0
            for minute_index in range(30):
                visited = accounts[minute_index % len(accounts)]
                token = "VICTIM" if visited == victim else f"OK-{visited}"
                now = T0 + minute_index * MINUTE + 1
                if g.decision(visited, False, login(token, 6 * HOUR), now) == REQUEST and visited == victim:
                    if now < T0 + 20 * MINUTE:
                        within += 1
                    else:
                        after += 1
            self.assertEqual(within, 0, others)
            self.assertGreater(after, 0, others)


class TokenChangeTests(unittest.TestCase):
    """R3: a token change clears everything."""

    def test_credential_changed_clears_all(self):
        g = UsageRequestGuard()
        g.record_rejected("id", "OLD", T0)
        g.record_rate_limited("id", "OLD", 3600, T0)
        g.mark_needs_reauth("id", "OLD")
        self.assertEqual(g.decision("id", False, login("OLD", None), T0 + MINUTE), NEEDS_REAUTH)
        cleared = g.credential_changed("id", "NEW")
        self.assertTrue(cleared)
        self.assertFalse(g.is_needs_reauth("id"))
        self.assertEqual(g.needs_reauth_accounts, set())
        self.assertEqual(g.decision("id", False, login("NEW", 8 * HOUR), T0 + 2 * MINUTE), REQUEST)
        self.assertFalse(g.credential_changed("id", "NEW"))

    def test_the_same_token_does_not_lift_its_park(self):
        g = UsageRequestGuard()
        g.record_rate_limited("id", "SAME", 3600, T0)
        g.credential_changed("id", "SAME")
        self.assertEqual(g.decision("id", False, login("SAME", 5 * HOUR), T0 + MINUTE), parked(T0 + 3600))

    def test_a_decision_with_another_token_forgets_by_itself(self):
        g = UsageRequestGuard()
        g.mark_needs_reauth("id", "DEADGRANT")
        self.assertEqual(g.decision("id", False, login("DEADGRANT", -HOUR), T0), NEEDS_REAUTH)
        self.assertEqual(g.decision("id", False, login("DEADGRANT", -HOUR), T0 + 7 * 24 * HOUR), NEEDS_REAUTH)
        later = T0 + 8 * 24 * HOUR
        self.assertEqual(g.decision("id", True, login("SIGNEDINAGAIN", 8 * HOUR, later), later), REQUEST)

    def test_a_flag_restored_after_a_relaunch(self):
        g = UsageRequestGuard(needs_reauth=["id"])
        self.assertTrue(g.is_needs_reauth("id"))
        self.assertEqual(g.decision("id", False, login("X", -HOUR), T0), NEEDS_REAUTH)
        g.credential_changed("id", "Y")
        self.assertFalse(g.is_needs_reauth("id"))
        g2 = UsageRequestGuard()
        g2.restore_needs_reauth(["id"])
        g2.mark_needs_reauth("id", "DEAD")
        self.assertEqual(g2.decision("id", False, login("OTHER", 5 * HOUR), T0), REQUEST)

    def test_credential_changed_for_an_unknown_account_changes_nothing(self):
        self.assertFalse(UsageRequestGuard().credential_changed("nobody", "T"))

    def test_credential_changed_with_no_token_resets_the_account(self):
        g = UsageRequestGuard()
        g.record_rejected("id", "T", T0)
        self.assertTrue(g.credential_changed("id", None))
        self.assertEqual(g.decision("id", False, login("T", 5 * HOUR), T0), REQUEST)

    def test_forget(self):
        g = UsageRequestGuard()
        g.mark_needs_reauth("id", "T")
        g.forget("id")
        self.assertFalse(g.is_needs_reauth("id"))


class NeedsReauthIsForTheServersWordTests(unittest.TestCase):
    """R4."""

    def test_only_the_servers_refusal_a_lost_rotation_and_another_accounts_login(self):
        flags = [
            (ug.GRANT_REJECTED, True), (ug.ROTATION_LOST, True), (ug.belongs_to_another_account("x@y.z"), True),
            (ug.SAME_GRANT_AS_ANOTHER_LOGIN, False), (ug.UNREACHABLE, False), (ug.NO_BACKUP, False),
            (ug.STORE_UNAVAILABLE, False), (ug.refreshed("{}"), False),
        ]
        self.assertTrue(all(outcome.requires_reauth == expected for outcome, expected in flags))
        self.assertFalse(ug.SAME_GRANT_AS_ANOTHER_LOGIN.requires_reauth)

    def test_an_expired_saved_login_is_renewable_not_flagged(self):
        g = UsageRequestGuard()
        self.assertEqual(g.decision("id", False, login("EXPIRED", -HOUR), T0), REFRESH_FIRST)
        self.assertFalse(g.is_needs_reauth("id"))


class FlagWithNoTokenTests(unittest.TestCase):
    """A flag with no token never takes the live login (review finding P1, 2026-10-16)."""

    def test_a_no_token_flag_does_not_latch_onto_the_active_account(self):
        g = UsageRequestGuard()
        g.mark_needs_reauth("id", None)
        self.assertEqual(g.decision("id", True, login("LIVE", 8 * HOUR), T0), REQUEST)
        self.assertFalse(g.is_needs_reauth("id"))
        self.assertEqual(g.decision("id", True, login("LIVE", 8 * HOUR), T0 + 4 * HOUR), REQUEST)

    def test_the_same_after_a_relaunch(self):
        r = UsageRequestGuard(needs_reauth=["id"])
        self.assertEqual(r.decision("id", True, login("LIVE", 8 * HOUR), T0), REQUEST)

    def test_a_saved_login_still_adopts_the_first_token_it_sees(self):
        b = UsageRequestGuard(needs_reauth=["id"])
        self.assertEqual(b.decision("id", False, login("DEAD", -HOUR), T0), NEEDS_REAUTH)
        self.assertEqual(b.decision("id", False, login("NEW", 8 * HOUR), T0), REQUEST)

    def test_a_flag_tied_to_a_token_keeps_holding_the_active_account_on_it(self):
        t = UsageRequestGuard()
        t.mark_needs_reauth("id", "LIVE")
        self.assertEqual(t.decision("id", True, login("LIVE", 8 * HOUR), T0), NEEDS_REAUTH)


class LocalRefusalTests(unittest.TestCase):
    def test_same_grant_is_refused_locally(self):
        refuse = BackupRefreshOutcome.local_refusal
        self.assertEqual(refuse(True, False), ug.SAME_GRANT_AS_ANOTHER_LOGIN)
        self.assertEqual(refuse(False, True), ug.SAME_GRANT_AS_ANOTHER_LOGIN)
        self.assertIsNone(refuse(False, False))
        self.assertFalse(refuse(True, True).requires_reauth)
        self.assertFalse(ug.UNREACHABLE.requires_reauth)

    def test_the_switch_can_tell_a_token_was_refused(self):
        g = UsageRequestGuard()
        g.record_rejected("id", "T", T0)
        self.assertTrue(g.was_rejected("id", "T"))
        self.assertFalse(g.was_rejected("id", "U"))
        self.assertFalse(g.was_rejected("other", "T"))
        # decision() answers cooling down first, which is why the switch asks was_rejected
        self.assertNotEqual(g.decision("id", False, login("T", 5 * HOUR), T0), REFRESH_FIRST)


class MeasuredHistoryTests(unittest.TestCase):
    def test_dead_grant_over_three_days(self):
        """ahmed@pixelventures.ai, 2026-10-12: a saved login whose refresh grant was revoked. It used to
        cost a usage 401 plus a refresh POST failing invalid_grant on EVERY round-robin visit, for days."""
        g = UsageRequestGuard()
        saved = login("PV-SAVED", None)  # not expired by its expiresAt, revoked server-side
        usage_requests = refresh_posts = 0
        now = T0
        for visit in range(3 * 24 * 12):  # every 5 minutes for 3 days
            now = T0 + visit * 5 * MINUTE
            decision = g.decision("id", False, saved, now)
            if decision == REQUEST:
                usage_requests += 1
                g.record_rejected("id", "PV-SAVED", now)       # the one 401
                refresh_posts += 1
                g.mark_needs_reauth("id", "PV-SAVED")          # invalid_grant: the server refused the grant
            elif decision == REFRESH_FIRST:
                refresh_posts += 1
                g.mark_needs_reauth("id", "PV-SAVED")
        self.assertEqual((usage_requests, refresh_posts), (1, 1))
        self.assertEqual(g.decision("id", False, saved, now), NEEDS_REAUTH)
        g.credential_changed("id", "PV-REAUTH")
        self.assertEqual(g.decision("id", False, login("PV-REAUTH", 8 * HOUR, now), now), REQUEST)

    def test_stale_live_login_over_a_day(self):
        """claude@pixelventures.ai, 2026-10-13: the live login expired while Claude Code sat idle. It
        used to send a usage request AND a profile lookup every 5 minutes for 24 hours."""
        g = UsageRequestGuard()
        stale = login("CPV-STALE", 0, T0)  # expiresAt = T0
        sent = profile_lookups = 0
        for tick in range(1, 24 * 12):
            now = T0 + tick * 5 * MINUTE
            if g.decision("id", True, stale, now) == REQUEST:
                sent += 1
            if g.may_use("CPV-STALE", stale.expires_at, now):
                profile_lookups += 1
        self.assertEqual((sent, profile_lookups), (0, 0))
        renewed_at = T0 + 24 * HOUR
        renewed = login("CPV-RENEWED", 8 * HOUR, renewed_at)
        self.assertEqual(g.decision("id", True, renewed, renewed_at), REQUEST)
        self.assertTrue(g.may_use("CPV-RENEWED", renewed.expires_at, renewed_at))


class NoticeTests(unittest.TestCase):
    """R6: the card never lies."""

    def setUp(self):
        self.until = T0 + HOUR

    def test_pause_never_overwrites_resign_or_no_subscription(self):
        decision = parked(self.until)
        self.assertEqual(choose_notice(decision, NoticeCurrent.RESIGN), ug.KEEP_CURRENT)
        self.assertEqual(choose_notice(decision, NoticeCurrent.NO_SUBSCRIPTION), ug.KEEP_CURRENT)

    def test_pause_shows_until_when(self):
        decision = parked(self.until)
        self.assertEqual(choose_notice(decision, NoticeCurrent.NOTHING), ug.paused(self.until))
        self.assertEqual(choose_notice(decision, NoticeCurrent.TRANSIENT), ug.paused(self.until))

    def test_waiting_for_claude_code(self):
        self.assertEqual(choose_notice(WAIT_FOR_CLAUDE_CODE, NoticeCurrent.NOTHING), ug.WAITING_FOR_CLAUDE_CODE)
        self.assertEqual(choose_notice(WAIT_FOR_CLAUDE_CODE, NoticeCurrent.TRANSIENT), ug.WAITING_FOR_CLAUDE_CODE)
        self.assertEqual(choose_notice(WAIT_FOR_CLAUDE_CODE, NoticeCurrent.RESIGN), ug.KEEP_CURRENT)

    def test_needs_reauth_shows_resign_once(self):
        for current in (NoticeCurrent.NOTHING, NoticeCurrent.TRANSIENT, NoticeCurrent.NO_SUBSCRIPTION):
            self.assertEqual(choose_notice(NEEDS_REAUTH, current), ug.RESIGN, current)
        self.assertEqual(choose_notice(NEEDS_REAUTH, NoticeCurrent.RESIGN), ug.KEEP_CURRENT)

    def test_cooldown_or_an_ask_changes_nothing(self):
        self.assertEqual(choose_notice(cooling_down(self.until), NoticeCurrent.NOTHING), ug.KEEP_CURRENT)
        self.assertEqual(choose_notice(REQUEST, NoticeCurrent.RESIGN), ug.KEEP_CURRENT)
        self.assertEqual(choose_notice(REFRESH_FIRST, NoticeCurrent.TRANSIENT), ug.KEEP_CURRENT)


class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_decisions_and_records_do_not_corrupt_state(self):
        import threading

        g = UsageRequestGuard()
        errors = []

        def worker(index):
            try:
                for step in range(200):
                    token = f"T{index}-{step % 3}"
                    g.decision(f"acct{index}", index % 2 == 0, login(token, 5 * HOUR), T0)
                    g.record_rejected(f"acct{index}", token, T0)
                    g.may_use(token, None, T0)
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
