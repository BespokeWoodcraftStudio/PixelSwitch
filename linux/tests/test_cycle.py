"""The usage cycle behind the guard, the renewal of saved logins, and the auto-switch cycle.

Everything runs against the mock API with a clock that moves only when the code sleeps, so the 1.5 s
spacing, the 20-minute cool-down and the 300 s auto-switch cooldown are asserted exactly.
"""
from __future__ import annotations

import json
import os
import threading
import unittest

from pixelswitch import fsio
from pixelswitch import settings_catalog as catalog
from pixelswitch.stores import BackupStore

from .lb_support import Rig, credential_text, read_file, read_json, wait_for
from .mock_api import R, fabricated, usage_body


def setting(app, name, value):
    key = catalog.named(name)
    app.set_setting(key, catalog.normalize(key, value))


def trio(case, a_usage=None, b_usage=None, c_usage=None, **kw):
    rig = Rig(case)
    a = rig.seed_account("a@example.invalid", active=True, usage=a_usage or usage_body(10, 10, now=rig.clock.now))
    b = rig.seed_account("b@example.invalid", usage=b_usage or usage_body(10, 10, now=rig.clock.now))
    c = rig.seed_account("c@example.invalid", usage=c_usage or usage_body(10, 10, now=rig.clock.now))
    return rig, rig.build(**kw), a, b, c


class RoundRobinTests(unittest.TestCase):
    def test_each_cycle_reads_the_active_account_plus_one_other_in_turn(self):
        rig, app, a, b, c = trio(self)
        asked = []
        for _ in range(4):
            rig.api.clear_calls()
            app.refresh()
            asked.append(sorted(call.token for call in rig.api.calls_to("usage")))
        self.assertEqual(asked, [sorted([a.access, b.access]), sorted([a.access, c.access]),
                                 sorted([a.access, b.access]), sorted([a.access, c.access])])

    def test_the_active_account_reads_the_live_login_and_the_others_their_saved_one(self):
        rig, app, a, b, c = trio(self)
        rotated = fabricated("oat01")
        rig.api.add_account(rotated, a.uuid, a.email, usage_body(33, 44, now=rig.clock.now))
        rig.write_live(a, credential_text(rotated, fabricated("ort01")))  # Claude Code renewed it; the saved copy is older
        app.refresh()
        self.assertEqual(len(rig.api.calls_to("usage", rotated)), 1)
        self.assertEqual(rig.api.calls_to("usage", a.access), [])
        self.assertEqual(app.usage[a.id].five_hour.utilization, 33)

    def test_the_two_requests_are_spaced_1_5_seconds_apart(self):
        rig, app, a, b, c = trio(self)
        app.refresh()
        self.assertEqual(rig.clock.slept.count(1.5), 1)
        rig.clock.slept.clear()
        app.fetch_all_account_usage()
        self.assertEqual(rig.clock.slept.count(1.5), 1, "one gap between two requests, never before the first")

    def test_an_account_whose_credential_just_changed_goes_first_in_line(self):
        rig, app, a, b, c = trio(self)
        app.note_credential_changed(app.account_by_id(c.id))
        app.refresh()
        self.assertEqual(len(rig.api.calls_to("usage", c.access)), 1)
        self.assertEqual(rig.api.calls_to("usage", b.access), [])
        self.assertNotIn(c.id, app.read_soon)
        self.assertEqual(app.fetch_cursor, 0, "the cursor advances only on the round-robin path")

    def test_a_cycle_sets_the_last_refresh_and_emits_usage_updated_and_runs_the_hook(self):
        rig, app, a, b, c = trio(self)
        hooks = []
        app.on_cycle_complete = lambda: hooks.append(1)
        self.assertIsNone(app.last_usage_refresh)
        app.refresh()
        self.assertEqual(app.last_usage_refresh, rig.clock.now)
        self.assertEqual(rig.events_of("usageUpdated"), [{}])
        self.assertEqual(hooks, [1])
        self.assertEqual(app.cycles_completed, 1)

    def test_a_refresh_is_skipped_while_a_sign_in_runs_or_another_refresh_is_running(self):
        rig, app, a, b, c = trio(self)
        app.gate.try_begin("signin")
        self.assertFalse(app.refresh())
        self.assertEqual(rig.api.calls, [])
        app.gate.end("signin")
        app._refresh_lock.acquire()
        self.assertFalse(app.refresh())
        app._refresh_lock.release()
        self.assertTrue(app.refresh())

    def test_a_manual_refresh_waits_for_the_one_running_instead_of_starting_a_second(self):
        rig, app, a, b, c = trio(self)
        entered, release = threading.Event(), threading.Event()
        real = app.fetch_all_account_usage

        def slow():
            entered.set()
            release.wait(10)
            real()

        app.fetch_all_account_usage = slow
        first = threading.Thread(target=app.refresh)
        first.start()
        self.assertTrue(entered.wait(10))
        done = []
        second = threading.Thread(target=lambda: (app.refresh_usage(), done.append(1)))
        second.start()
        self.assertFalse(wait_for(lambda: done, 0.3), "it is waiting")
        release.set()
        first.join(10)
        second.join(10)
        self.assertEqual(done, [1])
        self.assertEqual(len(rig.api.calls_to("usage", a.access)), 1, "one cycle, not two")

    def test_the_interval_setting_comes_from_settings_and_a_change_restarts_the_timer(self):
        rig, app, a, b, c = trio(self)
        self.assertEqual(app.refresh_interval(), 300.0)
        generation = app._interval_generation
        setting(app, "refreshInterval", 600)
        self.assertEqual(app.refresh_interval(), 600.0)
        self.assertEqual(app._interval_generation, generation + 1)
        self.assertEqual(rig.api.calls, [], "changing it does not refresh at once")


class GuardInTheCycleTests(unittest.TestCase):
    def test_a_401_on_the_live_login_waits_for_claude_code_and_the_token_is_never_sent_again(self):
        rig, app, a, b, c = trio(self)
        rig.api.script("usage", a.access, R(401, {"error": "expired"}))
        app.refresh()
        self.assertEqual(app.card_errors[a.id].message, "Waiting for Claude Code to renew this login. It renews the next time "
                         "a session runs.")
        self.assertTrue(app.card_errors[a.id].is_rate_limited)
        self.assertEqual(rig.api.calls_to("token"), [], "the live login is never refreshed by PixelSwitch")
        for _ in range(5):
            rig.clock.advance(300)
            app.refresh()
        self.assertEqual(len(rig.api.calls_to("usage", a.access)), 1, "one 401, then never that token again")

    def test_the_live_login_is_read_again_the_moment_its_token_changes(self):
        rig, app, a, b, c = trio(self)
        rig.api.script("usage", a.access, R(401, {"error": "expired"}))
        app.refresh()
        renewed = fabricated("oat01")
        rig.api.add_account(renewed, a.uuid, a.email, usage_body(40, 41, now=rig.clock.now))
        rig.write_live(a, credential_text(renewed, fabricated("ort01")))
        app.refresh()
        self.assertEqual(len(rig.api.calls_to("usage", renewed)), 1)
        self.assertEqual(app.usage[a.id].five_hour.utilization, 40)
        self.assertNotIn(a.id, app.card_errors)

    def test_an_expired_live_login_sends_nothing_at_all(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, hours=-2)
        app = rig.build()
        app.refresh()
        self.assertEqual(rig.api.calls, [], "no usage and no profile request for an expired live token")
        self.assertIn("Waiting for Claude Code", app.card_errors[a.id].message)

    def test_a_401_on_a_saved_login_renews_it_once_then_asks_once_with_the_new_token(self):
        rig, app, a, b, c = trio(self)
        new_access = fabricated("oat01")
        rig.api.script("usage", b.access, R(401, {"error": "revoked early"}))
        rig.api.add_refresh(b.refresh, new_access, fabricated("ort01"))
        rig.api.add_account(new_access, b.uuid, b.email, usage_body(21, 22, now=rig.clock.now))
        app.refresh()
        self.assertEqual(len(rig.api.calls_to("token")), 1)
        self.assertEqual(len(rig.api.calls_to("usage", b.access)), 1)
        self.assertEqual(len(rig.api.calls_to("usage", new_access)), 1)
        self.assertEqual(app.usage[b.id].five_hour.utilization, 21)
        self.assertEqual(json.loads(rig.backup_of(b).token)["claudeAiOauth"]["accessToken"], new_access)

    def test_a_401_on_a_token_that_was_just_renewed_stops_there(self):
        rig, app, a, b, c = trio(self)
        new_access = fabricated("oat01")
        rig.api.script("usage", b.access, R(401, {"error": "x"}))
        rig.api.script("usage", new_access, R(401, {"error": "x"}))
        rig.api.add_refresh(b.refresh, new_access, fabricated("ort01"))
        app.refresh()
        self.assertEqual(app.card_errors[b.id].message, "Session expired.")
        self.assertFalse(app.card_errors[b.id].is_expired, "not the re-sign message: the account stays switchable")
        self.assertEqual(len(rig.api.calls_to("token")), 1)
        self.assertEqual(len(rig.api.calls_to("usage", new_access)), 1)

    def test_an_expired_saved_login_is_renewed_before_it_is_asked(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        new_access = fabricated("oat01")
        rig.api.add_refresh(b.refresh, new_access, fabricated("ort01"))
        rig.api.add_account(new_access, b.uuid, b.email, usage_body(5, 6, now=rig.clock.now))
        app = rig.build()
        app.refresh()
        self.assertEqual(rig.api.calls_to("usage", b.access), [], "the expired token was never sent")
        self.assertEqual(len(rig.api.calls_to("usage", new_access)), 1)

    def test_a_dead_grant_costs_one_refresh_then_nothing_ever_again_until_the_login_changes(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)  # the mock answers invalid_grant
        app = rig.build()
        for _ in range(6):
            app.refresh()
            rig.clock.advance(3 * 3600)
        self.assertEqual(len(rig.api.calls_to("token")), 1)
        self.assertEqual(rig.api.calls_to("usage", b.access), [])
        self.assertTrue(app.guard.is_needs_reauth(b.id))
        self.assertEqual(app.card_errors[b.id].message, "Session expired. Re-authenticate (↻) to fix.")
        self.assertTrue(app.card_errors[b.id].is_expired)
        self.assertFalse(app.is_switchable(app.account_by_id(b.id)))
        self.assertEqual(read_json(rig.paths.needs_reauth), [b.id], "ids only, never a token")
        self.assertNotIn("sk-ant", read_file(rig.paths.needs_reauth))

    def test_the_flag_survives_a_restart_and_clears_when_the_saved_token_changes(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        app = rig.build()
        app.refresh()
        self.assertTrue(app.guard.is_needs_reauth(b.id))
        again = rig.build()
        self.assertTrue(again.guard.is_needs_reauth(b.id), "restored from the needs-reauth file")
        again.backups.save(credential_text(fabricated("oat01"), fabricated("ort01")), b.oauth_account, b.id)  # re-signed
        again.note_credential_changed(again.account_by_id(b.id))
        self.assertFalse(again.guard.is_needs_reauth(b.id))

    def test_a_restored_flag_never_captures_a_healthy_live_login(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, saved=False)  # no saved login, so the flag holds no token
        rig.seed_account("b@example.invalid")
        fsio.atomic_write(rig.paths.needs_reauth, json.dumps([a.id]))
        app = rig.build()
        self.assertTrue(app.guard.is_needs_reauth(a.id))
        app.refresh()
        self.assertFalse(app.guard.is_needs_reauth(a.id))
        self.assertEqual(len(rig.api.calls_to("usage", a.access)), 1)

    def test_a_403_means_no_subscription_and_clears_the_reading(self):
        rig, app, a, b, c = trio(self)
        app.refresh()
        self.assertIn(a.id, app.usage)
        rig.api.script("usage", a.access, R(403, {"error": {"type": "permission_error"}}))
        app.refresh()
        self.assertTrue(app.card_errors[a.id].is_no_subscription)
        self.assertEqual(app.card_errors[a.id].message, "No active subscription on this account (OAuth not allowed).")
        self.assertNotIn(a.id, app.usage)

    def test_a_429_parks_the_token_for_retry_after_and_the_card_says_when(self):
        rig, app, a, b, c = trio(self)
        rig.api.script("usage", a.access, R(429, "slow", {"Retry-After": "3600"}))
        app.refresh()
        self.assertTrue(app.card_errors[a.id].message.startswith("Usage reading paused until "))
        self.assertTrue(app.card_errors[a.id].message.endswith(". Claude Code itself is not affected."))
        self.assertTrue(app.card_errors[a.id].is_rate_limited)
        rig.api.clear_calls()
        for _ in range(3):
            rig.clock.advance(600)
            app.refresh()
        self.assertEqual(rig.api.calls_to("usage", a.access), [], "parked: not sent again")
        rig.clock.advance(1900)  # past the 3600 s park and the 20 minute cool-down
        app.refresh()
        self.assertEqual(len(rig.api.calls_to("usage", a.access)), 1)

    def test_a_429_never_overwrites_the_resign_message(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        app.flag_needs_reauth(app.account_by_id(b.id))
        message = app.card_errors[b.id].message
        app.refresh()
        self.assertEqual(app.card_errors[b.id].message, message)

    def test_server_and_network_failures_keep_the_last_reading_and_say_so(self):
        rig, app, a, b, c = trio(self)
        app.refresh()
        first = app.usage[a.id]
        rig.api.script("usage", a.access, R(503, "down"))
        app.refresh()
        self.assertIs(app.usage[a.id], first)
        self.assertEqual(app.card_errors[a.id].message, "Anthropic's usage service isn't responding (HTTP 503). Your "
                         "account is fine; PixelSwitch keeps trying.")
        self.assertTrue(app.card_errors[a.id].can_retry)
        self.assertTrue(app.card_errors[a.id].offers_status_page)
        rig.api.script("usage", a.access, R(418, "tea"))
        app.refresh()
        self.assertEqual(app.card_errors[a.id].message, "Unexpected reply from Anthropic (HTTP 418).")
        self.assertNotIn(a.id, app.usage, "an unexpected reply clears the reading")

    def test_the_card_retry_reads_one_account_through_the_guard(self):
        rig, app, a, b, c = trio(self)
        app.retry_usage(app.account_by_id(b.id))
        self.assertEqual(len(rig.api.calls_to("usage", b.access)), 1)
        self.assertIn(b.id, app.usage)

    def test_the_log_names_ids_never_emails_or_tokens(self):
        rig, app, a, b, c = trio(self)
        app.refresh()
        text = rig.log_text()
        for secret in (a.access, a.refresh, b.access, a.email, b.email):
            self.assertNotIn(secret, text)
        self.assertIn(a.id, text)


class RenewalTests(unittest.TestCase):
    def pair(self, **kw):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", **kw)
        return rig, rig.build(), a, b

    def test_a_rotation_replaces_the_saved_login_and_notes_the_change(self):
        rig, app, a, b = self.pair(hours=-1)
        new_access, new_refresh = fabricated("oat01"), fabricated("ort01")
        rig.api.add_refresh(b.refresh, new_access, new_refresh, refresh_expires_in=7776000)
        rig.api.add_account(new_access, b.uuid, b.email)
        outcome = app.refresh_backup_in_place(app.account_by_id(b.id))
        self.assertEqual(outcome.kind, "refreshed")
        saved = json.loads(rig.backup_of(b).token)["claudeAiOauth"]
        self.assertEqual((saved["accessToken"], saved["refreshToken"]), (new_access, new_refresh))
        self.assertAlmostEqual(saved["refreshTokenExpiresAt"], (rig.clock.now + 7776000) * 1000, delta=5000)
        self.assertEqual(rig.backup_of(b).oauth_account, b.oauth_account)

    def test_the_store_is_proven_writable_before_the_refresh_token_is_spent(self):
        rig, app, a, b = self.pair(hours=-1)
        rig.api.add_refresh(b.refresh, fabricated("oat01"), fabricated("ort01"))
        app.backups.save = lambda *args, **kw: False
        outcome = app.refresh_backup_in_place(app.account_by_id(b.id))
        self.assertEqual(outcome.kind, "storeUnavailable")
        self.assertEqual(rig.api.calls_to("token"), [], "nothing was spent")
        self.assertFalse(app.guard.is_needs_reauth(b.id), "and it is not the server's verdict")

    def test_a_rotation_that_cannot_be_saved_twice_is_lost_and_the_account_must_sign_in_again(self):
        rig, app, a, b = self.pair(hours=-1)
        new_access = fabricated("oat01")
        rig.api.add_refresh(b.refresh, new_access, fabricated("ort01"))
        rig.api.add_account(new_access, b.uuid, b.email)
        real = app.backups.save
        calls = []

        def flaky(token, oauth, account_id):
            calls.append(1)
            return real(token, oauth, account_id) if len(calls) == 1 else False  # the probe passes, then every save fails

        app.backups.save = flaky
        outcome = app.refresh_backup_in_place(app.account_by_id(b.id))
        self.assertEqual(outcome.kind, "rotationLost")
        self.assertEqual(len(calls), 3, "the probe, the save, and one retry after a second")
        self.assertIn(1.0, rig.clock.slept)
        self.assertTrue(app.guard.is_needs_reauth(b.id))

    def test_a_login_that_is_the_same_grant_as_the_live_one_is_never_refreshed(self):
        rig, app, a, b = self.pair(hours=-1)
        BackupStore(rig.paths.backups, rig.log.category("t")).save(credential_text(a.access, a.refresh, hours=-1), b.oauth_account, b.id)
        rig.write_live(a, credential_text(a.access, a.refresh))
        outcome = app.refresh_backup_in_place(app.account_by_id(b.id))
        self.assertEqual(outcome.kind, "sameGrantAsAnotherLogin")
        self.assertEqual(rig.api.calls_to("token"), [])
        self.assertFalse(app.guard.is_needs_reauth(b.id), "PixelSwitch's own refusal is not a verdict")

    def test_a_login_that_is_the_same_grant_as_another_accounts_is_never_refreshed(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        c = rig.seed_account("c@example.invalid")
        BackupStore(rig.paths.backups, rig.log.category("t")).save(b.credential, c.oauth_account, c.id)
        app = rig.build()
        self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id)).kind, "sameGrantAsAnotherLogin")
        self.assertEqual(rig.api.calls_to("token"), [])

    def test_a_renewed_login_that_belongs_to_another_account_is_removed(self):
        rig, app, a, b = self.pair(hours=-1)
        new_access = fabricated("oat01")
        rig.api.add_refresh(b.refresh, new_access, fabricated("ort01"))
        rig.api.add_account(new_access, a.uuid, a.email)  # the renewed token turns out to be A's
        outcome = app.refresh_backup_in_place(app.account_by_id(b.id))
        self.assertEqual((outcome.kind, outcome.detail), ("belongsToAnotherAccount", a.email))
        self.assertIsNone(rig.backup_of(b))
        self.assertTrue(app.guard.is_needs_reauth(b.id))
        self.assertIn("belonged to another account", app.card_errors[b.id].message)

    def test_transient_failures_neither_spend_nor_flag(self):
        rig, app, a, b = self.pair(hours=-1)
        for response in (R(503, "down"), R(429, "slow"), R(400, {"error": "invalid_request"})):
            rig.api.script("token", b.refresh, response)
            self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id)).kind, "unreachable")
        self.assertFalse(app.guard.is_needs_reauth(b.id))

    def test_missing_and_unreadable_backups(self):
        rig, app, a, b = self.pair()
        app.backups.remove(b.id)
        self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id)).kind, "noBackup")
        with open(rig.paths.backups, "w") as handle:
            handle.write("junk")
        self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id)).kind, "storeUnavailable")

    def test_an_unusable_saved_login_is_a_rejected_grant_without_a_request(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", saved=False)
        fsio.atomic_write(rig.paths.backups, json.dumps({
            a.id: {"token": a.credential, "oauthAccount": a.oauth_account},
            b.id: {"token": json.dumps({"claudeAiOauth": {"accessToken": "x", "refreshToken": "  ", "expiresAt": 1}}),
                   "oauthAccount": b.oauth_account}}))
        app = rig.build()
        self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id)).kind, "grantRejected")
        self.assertEqual(rig.api.calls_to("token"), [])
        self.assertTrue(app.guard.is_needs_reauth(b.id))

    def test_concurrent_renewals_of_one_account_join_a_single_request(self):
        rig, app, a, b = self.pair(hours=-1)
        new_access = fabricated("oat01")
        rig.api.add_refresh(b.refresh, new_access, fabricated("ort01"))
        rig.api.add_account(new_access, b.uuid, b.email)
        rig.api.script("token", b.refresh, R(200, {"access_token": new_access, "refresh_token": fabricated("ort01"),
                                                    "expires_in": 3600}, delay=0.4))
        results = []
        threads = [threading.Thread(target=lambda: results.append(app.refresh_backup_in_place(app.account_by_id(b.id))))
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(len(rig.api.calls_to("token")), 1)
        self.assertEqual([r.kind for r in results], ["refreshed"] * 4)
        self.assertEqual(app.renewals_in_flight(), 0)


class AutoSwitchTests(unittest.TestCase):
    def setUp(self):
        self.rig = None

    def build(self, a_usage, b_usage=None, c_usage=None, **kw):
        rig = Rig(self)
        now = rig.clock.now
        a = rig.seed_account("a@example.invalid", active=True, usage=a_usage(now), **kw.get("a", {}))
        b = rig.seed_account("b@example.invalid", usage=(b_usage or (lambda n: usage_body(10, 10, now=n)))(now), **kw.get("b", {}))
        c = rig.seed_account("c@example.invalid", usage=(c_usage or (lambda n: usage_body(20, 20, now=n)))(now), **kw.get("c", {}))
        app = rig.build()
        self.rig = rig
        return rig, app, a, b, c

    def prime(self, app):
        """Reads every account once, with auto-switch still off, so a decision sees every account."""
        app.fetch_all_account_usage()
        app.fetch_all_account_usage()
        app.fetch_all_account_usage()

    def full_cycles(self, app, n=2):
        for _ in range(n):  # two cycles read every account once (round-robin)
            app.refresh()

    def test_off_by_default_nothing_switches_even_at_100_percent(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(100, 100, now=n))
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual(rig.events_of("autoSwitched"), [])

    def test_the_active_account_at_its_session_limit_moves_to_the_account_with_most_room(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, b.id, "most room left: B (10%) beats C (20%)")
        event = rig.events_of("autoSwitched")[-1]
        self.assertEqual((event["from"], event["to"], event["limit"], event["trigger"], event["window"]),
                         (a.id, b.id, "windows", "threshold", "session"))
        self.assertIs(event["relaxed"], False)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access, "the real files were switched")
        self.assertEqual(app.last_auto_switch.window, "session")

    def test_the_weekly_limit_names_its_window(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(10, 80, now=n))
        setting(app, "autoSwitch.enabled", True)
        setting(app, "autoSwitch.weeklyThreshold", 70)
        self.full_cycles(app)
        self.assertEqual(rig.events_of("autoSwitched")[-1]["window"], "weekly")
        self.assertIn("(weekly window)", rig.log_text())

    def test_weekly_overflow_lets_the_session_limit_decide_and_says_so(self):
        # session 90 / weekly 50, every account past its weekly limit: strict plan nil, relaxed plan moves on.
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 60, now=n), lambda n: usage_body(10, 70, now=n),
                                       lambda n: usage_body(10, 55, now=n))
        self.prime(app)
        setting(app, "autoSwitch.enabled", True)
        setting(app, "autoSwitch.sessionThreshold", 90)
        setting(app, "autoSwitch.weeklyThreshold", 50)
        app.refresh()
        self.assertEqual(app.active_account().id, c.id, "most room under the raised weekly limit: C")
        event = rig.events_of("autoSwitched")[-1]
        self.assertEqual((event["to"], event["relaxed"], event["window"]), (c.id, True, "session"))
        self.assertIn("no account under its weekly limit can take you, so the session limit decides", rig.log_text())
        self.assertTrue(app.last_auto_switch.relaxed)

    def test_weekly_overflow_does_not_apply_while_the_active_account_is_under_both_limits(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(10, 10, now=n), lambda n: usage_body(10, 70, now=n),
                                       lambda n: usage_body(10, 55, now=n))
        setting(app, "autoSwitch.enabled", True)
        setting(app, "autoSwitch.sessionThreshold", 90)
        setting(app, "autoSwitch.weeklyThreshold", 50)
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual(rig.events_of("autoSwitched"), [])

    def test_priority_order_picks_the_lowest_number_first_under_the_priority_strategy(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n), b={"priority": 3}, c={"priority": 1})
        setting(app, "autoSwitch.enabled", True)
        setting(app, "autoSwitch.strategy", "priority")
        self.assertEqual(app.auto_switch_config().strategy.value, "myOrder")
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, c.id, "C is P1; B is P3 even with more room")

    def test_the_strategy_priority_alias_is_stored_as_my_order(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(10, 10, now=n))
        setting(app, "autoSwitch.strategy", "Priority")
        self.assertEqual(read_json(rig.paths.settings)["autoSwitchStrategy"], "myOrder")

    def test_a_candidate_whose_fresh_reading_fails_verification_is_not_switched_to(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        now = rig.clock.now
        app.last_cycle_start = now  # a cycle that sampled only A
        app.usage = {a.id: usage_for(95, 20, now), b.id: usage_for(10, 10, now), c.id: usage_for(20, 20, now)}
        app.usage_sampled_at = {a.id: now, b.id: now - 3600, c.id: now - 3600}
        rig.api.set_usage(b.access, usage_body(99, 99, now=now))  # B filled up from another device
        rig.api.clear_calls()
        app.evaluate_auto_switch()
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual(len(rig.api.calls_to("usage")), 1, "at most ONE fresh verification request per evaluation")
        self.assertEqual(len(rig.api.calls_to("usage", b.access)), 1)
        self.assertIn("failed verification", rig.log_text())

    def test_a_candidate_sampled_by_this_cycle_needs_no_fresh_request(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        at_switch = []
        real = app.perform_switch

        def watching(target):
            at_switch.append(len(rig.api.calls_to("usage", b.access)))
            return real(target)

        app.perform_switch = watching
        app.refresh()  # samples A and B, then decides
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(at_switch, [1], "the cycle's own sample of B counted as verified: no second request before the switch")

    def test_the_cooldown_holds_for_300_seconds(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        app.refresh()
        self.assertEqual(app.active_account().id, b.id)
        switched_at = app.last_auto_switch_at

        def at(seconds_after):
            """B (now active) is full; A and C are empty and were sampled by 'this cycle'."""
            now = switched_at + seconds_after
            rig.clock.now = now
            app.last_cycle_start = now
            app.usage = {b.id: usage_for(97, 20, now), a.id: usage_for(5, 5, now), c.id: usage_for(5, 5, now)}
            app.usage_sampled_at = {b.id: now, a.id: now, c.id: now}
            app.evaluate_auto_switch()

        at(299)
        self.assertEqual(app.active_account().id, b.id, "inside the 300 s cooldown")
        at(301)
        self.assertNotEqual(app.active_account().id, b.id, "after it, the rule applies again")

    def test_a_manual_switch_restarts_the_cooldown(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(10, 10, now=n), lambda n: usage_body(96, 96, now=n))
        setting(app, "autoSwitch.enabled", True)
        app.perform_switch(app.account_by_id(b.id))  # the user knowingly picks an account at 96%
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, b.id, "not auto-switched away seconds later")

    def test_staying_put_is_logged_once_per_change(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n), lambda n: usage_body(96, 96, now=n),
                                       lambda n: usage_body(97, 97, now=n))
        setting(app, "autoSwitch.enabled", True)
        # Equal windows (the defaults): the active account is already AT its session limit, so the
        # overflow sentence ("staying until the session limit") would be false. Rule 10 revision 4:
        # the 1.9 sentence (AutoSwitchEngine.stayingPutKind; LB review, usagecycle.py:616).
        note = "but no other account has room under its own limits; staying put"
        self.full_cycles(app, 4)
        self.assertEqual(rig.log_text().count(note), 1)
        self.assertNotIn("staying until the session limit", rig.log_text())
        rig.api.set_usage(a.access, usage_body(98, 20, now=rig.clock.now))
        self.full_cycles(app, 2)
        self.assertEqual(rig.log_text().count(note), 2, "a changed reading is a changed note")

    def test_split_limits_with_the_active_account_past_both_say_no_room(self):
        """Limits 90 / 50 and the active account at 95% session, 60% week: it is past the relaxed
        limits (90 / 90) too, so it is not 'staying until 90%' (AutoSwitchEngine.stayingPutKind)."""
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 60, now=n), lambda n: usage_body(96, 96, now=n),
                                       lambda n: usage_body(97, 97, now=n))
        setting(app, "autoSwitch.enabled", True)
        setting(app, "autoSwitch.sessionThreshold", 90)
        setting(app, "autoSwitch.weeklyThreshold", 50)
        self.full_cycles(app, 2)
        self.assertIn("but no other account has room under its own limits; staying put", rig.log_text())
        self.assertNotIn("staying until the session limit", rig.log_text())

    def test_a_fable_only_limit_with_nowhere_to_go_logs_the_plain_message(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(10, 10, fable=95, now=n),
                                       lambda n: usage_body(10, 10, fable=96, now=n),
                                       lambda n: usage_body(10, 10, fable=97, now=n))
        setting(app, "autoSwitch.enabled", True)
        self.full_cycles(app, 3)
        self.assertEqual(rig.log_text().count("but no other account has room under its own limits; staying put"), 1)
        self.assertIn("95% on fable", rig.log_text())

    def test_when_overflow_holds_and_nobody_can_take_you_it_says_it_stays_until_the_session_limit(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(60, 70, now=n), lambda n: usage_body(95, 95, now=n),
                                       lambda n: usage_body(96, 96, now=n))
        setting(app, "autoSwitch.enabled", True)
        setting(app, "autoSwitch.sessionThreshold", 90)
        setting(app, "autoSwitch.weeklyThreshold", 50)
        self.full_cycles(app, 2)
        self.assertIn("no account under its weekly limit can take you; staying until the session limit (90%)", rig.log_text())

    def test_manual_only_accounts_are_never_switched_to(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n), b={"switch": 0})
        setting(app, "autoSwitch.enabled", True)
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, c.id)

    def test_an_active_manual_only_account_leaves_at_the_defaults(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n), a={"switch": 0})
        setting(app, "autoSwitch.enabled", True)
        self.full_cycles(app)
        self.assertNotEqual(app.active_account().id, a.id)

    def test_an_active_account_with_no_subscription_is_left_without_waiting_for_the_cooldown(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(10, 10, now=n))
        setting(app, "autoSwitch.enabled", True)
        app.last_auto_switch_at = rig.clock.now  # a switch just happened
        rig.api.script("usage", a.access, R(403, {"error": {"type": "permission_error"}}))
        self.full_cycles(app)
        self.assertNotEqual(app.active_account().id, a.id)
        self.assertEqual(rig.events_of("autoSwitched")[-1]["trigger"], "noSubscription")

    def test_it_stands_down_if_a_sign_in_or_switch_starts_during_verification(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        now = rig.clock.now
        app.last_cycle_start = now
        app.usage = {a.id: usage_for(95, 20, now), b.id: usage_for(10, 10, now), c.id: usage_for(20, 20, now)}
        app.usage_sampled_at = {a.id: now, b.id: now - 3600, c.id: now - 3600}
        real = app.read_usage

        def reading(account):
            result = real(account)
            app.gate.try_begin("signin")  # a login begins during the verification request
            return result

        app.read_usage = reading
        app.evaluate_auto_switch()
        self.assertEqual(app.active_account().id, a.id)
        self.assertIn("State changed during verification; standing down", rig.log_text())

    def test_a_failed_auto_switch_sets_the_error_message(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        os.mkdir(os.path.join(rig.paths.claude_dir, ".storage-write"))  # Claude Code is saving: the write is refused
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, a.id)
        self.assertIn("Claude Code is saving its login", app.error_message)
        self.assertEqual(rig.events_of("autoSwitched"), [])
        self.assertEqual(rig.events_of("error")[-1]["message"], app.error_message)

    def test_an_account_that_must_sign_in_again_is_never_a_target(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        app.flag_needs_reauth(app.account_by_id(b.id))
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, c.id)

    def test_nothing_switches_while_shutting_down(self):
        rig, app, a, b, c = self.build(lambda n: usage_body(95, 20, now=n))
        setting(app, "autoSwitch.enabled", True)
        app.begin_shutdown()
        self.full_cycles(app)
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual(rig.api.calls, [], "no new cycle once shutdown has begun")


def usage_for(session, weekly, now):
    from pixelswitch.usage_model import UsageResponse

    return UsageResponse.from_json(usage_body(session, weekly, now=now))


if __name__ == "__main__":
    unittest.main()
