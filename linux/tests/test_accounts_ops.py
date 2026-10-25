"""Accounts: add-current, capture (the guards against saving one account's token under another), the
launch audit, reconcile, removal (spec D20), labels, thresholds, priority and order, and the events."""
from __future__ import annotations

import json
import os
import threading
import unittest

from pixelswitch import fsio
from pixelswitch.accounts import ABSENT
from pixelswitch.coordinator import ActionError, BUSY_MESSAGE
from pixelswitch.protocol import ControlError
from pixelswitch.stores import BackupStore

from .lb_support import Rig, credential_text, read_json, wait_for
from .mock_api import R, fabricated


class AddCurrentTests(unittest.TestCase):
    def live_only(self, email="a@example.invalid", **kw):
        """Claude Code is signed in as `email`, PixelSwitch knows nothing yet."""
        rig = Rig(self)
        seed = rig.seed_account(email, active=False, saved=False, **kw)
        rig.seeds.clear()
        rig.write_live(seed)
        return rig, seed

    def test_the_first_account_is_saved_active_with_its_login(self):
        rig, seed = self.live_only(subscription="max", display="Acme")
        app = rig.build()
        account_id = app.add_current_account()
        account = app.account_by_id(account_id)
        self.assertEqual((account.email, account.is_active, account.org_name, account.subscription_type),
                         (seed.email, True, "Acme", "max"))
        self.assertEqual(app.active_account().id, account_id)
        backup = rig.backup_of(type("S", (), {"id": account_id}))
        self.assertEqual(backup.token, seed.credential)
        self.assertEqual(backup.oauth_account, seed.oauth_account)
        self.assertEqual(read_json(rig.paths.accounts)[0]["email"], seed.email)
        self.assertEqual(rig.events_of("accountsChanged")[-1]["accounts"][0]["email"], seed.email)

    def test_a_second_account_is_saved_but_not_active_and_the_list_stays_sorted(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, priority=1)
        b = rig.seed_account("b@example.invalid", saved=False)
        rig.seeds.remove(b)
        app = rig.build()
        rig.write_live(b)
        b_id = app.add_current_account()
        self.assertFalse(app.account_by_id(b_id).is_active)
        self.assertEqual([x.id for x in app.accounts], [a.id, b_id], "no number sorts after every numbered account")

    def test_refusals_and_their_texts(self):
        rig, seed = self.live_only()
        app = rig.build()
        rig.settings_seed = {}
        app.settings.replace({})
        app.cli._base_env["PATH"] = "/nonexistent"
        with self.assertRaises(ActionError) as raised:
            app.add_current_account()
        self.assertEqual((raised.exception.kind, raised.exception.message), ("claudeUnavailable", "Claude CLI not found"))
        self.assertFalse(app.gate.busy)

        rig, seed = self.live_only()
        app = rig.build()
        os.unlink(rig.paths.credentials)
        with self.assertRaises(ActionError) as raised:
            app.add_current_account()
        self.assertEqual(raised.exception.message, "Not logged in to Claude. Run 'claude auth login' first.")

        rig, seed = self.live_only()
        app = rig.build()
        app.cli._base_env["STUB_STATUS_SHADOW"] = "1"
        with self.assertRaises(ActionError) as raised:
            app.add_current_account()
        self.assertIn("Claude CLI is authenticating via apiKey instead of a stored claude.ai login", raised.exception.message)

        rig, seed = self.live_only()
        app = rig.build()
        app.add_current_account()
        with self.assertRaises(ActionError) as raised:
            app.add_current_account()
        self.assertEqual((raised.exception.kind, raised.exception.message), ("failed", "Account already exists"))

    def test_a_capture_that_is_refused_adds_nothing(self):
        rig, seed = self.live_only()
        rig.api.add_account(seed.access, "someone-elses-uuid", "other@example.invalid")  # the API says it is another account
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.add_current_account()
        self.assertEqual(raised.exception.message, "Could not capture the current Claude Code login")
        self.assertEqual(app.accounts, [])
        self.assertFalse(os.path.exists(rig.paths.backups))
        self.assertFalse(app.gate.busy)


class CaptureTests(unittest.TestCase):
    def setup(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        return rig, rig.build(), a

    def test_the_api_confirms_owner_and_details_then_it_saves(self):
        rig, app, a = self.setup()
        rotated = fabricated("oat01")
        rig.api.add_account(rotated, a.uuid, a.email)
        rig.write_live(a, credential_text(rotated, fabricated("ort01")))
        self.assertTrue(app.capture_current_credentials(app.account_by_id(a.id)))
        self.assertEqual(json.loads(rig.backup_of(a).token)["claudeAiOauth"]["accessToken"], rotated)

    def test_refused_when_the_live_login_belongs_to_someone_else(self):
        rig, app, a = self.setup()
        other = fabricated("oat01")
        rig.api.add_account(other, "u-other", "other@example.invalid")
        rig.write_live(a, credential_text(other, fabricated("ort01")))
        before = rig.backup_of(a).token
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))
        self.assertEqual(rig.backup_of(a).token, before)

    def test_refused_when_claude_json_describes_another_account(self):
        rig, app, a = self.setup()
        config = rig.live_config()
        config["oauthAccount"] = {"accountUuid": "u-other", "emailAddress": "other@example.invalid"}
        fsio.atomic_write(rig.paths.claude_json, json.dumps(config))
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))

    def test_without_the_api_the_file_must_at_least_name_the_account(self):
        rig, app, a = self.setup()
        rig.api.script("profile", a.access, R(500, "down"))
        self.assertTrue(app.capture_current_credentials(app.account_by_id(a.id)))
        config = rig.live_config()
        config["oauthAccount"] = {"accountUuid": "u-other", "emailAddress": "other@example.invalid"}
        fsio.atomic_write(rig.paths.claude_json, json.dumps(config))
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))

    def test_without_the_api_a_different_grant_never_replaces_the_saved_one(self):
        rig, app, a = self.setup()
        new_access = fabricated("oat01")
        rig.write_live(a, credential_text(new_access, fabricated("ort01")))  # a new grant, unknown to the API
        rig.api.script("profile", None, R(500, "down"))
        before = rig.backup_of(a).token
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))
        self.assertEqual(rig.backup_of(a).token, before)

    def test_a_blank_live_login_is_not_captured_even_when_the_file_still_names_the_account(self):
        """R4: with the API unable to answer, the old code saved the blank credential."""
        rig, app, a = self.setup()
        rig.write_live(a, json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}}))
        before = rig.backup_of(a).token
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))
        self.assertEqual(rig.backup_of(a).token, before)

    def test_no_files_no_capture(self):
        rig, app, a = self.setup()
        os.unlink(rig.paths.claude_json)
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))
        os.unlink(rig.paths.credentials)
        self.assertFalse(app.capture_current_credentials(app.account_by_id(a.id)))


class ReconcileTests(unittest.TestCase):
    def pair(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        return rig, rig.build(), a, b

    def test_it_follows_the_live_login_to_its_proven_owner(self):
        rig, app, a, b = self.pair()
        rig.write_live(b)  # the user ran `claude auth login` as B outside PixelSwitch
        app.reconcile_live_login()
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual([x.id for x in app.accounts if x.is_active], [b.id])
        self.assertEqual(rig.events_of("activeAccountChanged")[-1]["accountId"], b.id)

    def test_it_keeps_the_owners_backup_current_when_claude_code_rotates(self):
        rig, app, a, b = self.pair()
        rotated = fabricated("oat01")
        rig.api.add_account(rotated, a.uuid, a.email)
        text = credential_text(rotated, fabricated("ort01"))
        rig.write_live(a, text)
        app.reconcile_live_login()
        self.assertEqual(json.loads(rig.backup_of(a).token)["claudeAiOauth"]["accessToken"], rotated)
        self.assertIn(a.id, app.read_soon, "the Claude token changed, so the account is read next")

    def test_a_change_only_in_mcp_logins_saves_but_does_not_queue_a_read(self):
        rig, app, a, b = self.pair()
        rig.write_live(a, credential_text(a.access, a.refresh, top={"mcpOAuth": {"s": {"n": 1}}}))
        app.reconcile_live_login()
        self.assertIn("mcpOAuth", json.loads(rig.backup_of(a).token))
        self.assertNotIn(a.id, app.read_soon)

    def test_formatting_alone_is_not_a_change(self):
        rig, app, a, b = self.pair()
        rig.write_live(a, json.dumps(json.loads(a.credential), indent=4))
        mtime = os.stat(rig.paths.backups).st_mtime_ns
        app.reconcile_live_login()
        self.assertEqual(os.stat(rig.paths.backups).st_mtime_ns, mtime, "no write")

    def test_it_restores_the_owners_identity_when_a_session_left_another_in_claude_json(self):
        rig, app, a, b = self.pair()
        config = rig.live_config()
        config["oauthAccount"] = b.oauth_account  # a running session rewrote it from memory
        fsio.atomic_write(rig.paths.claude_json, json.dumps(config))
        app.reconcile_live_login()
        self.assertEqual(rig.live_config()["oauthAccount"], a.oauth_account)
        self.assertEqual(rig.backup_of(b).oauth_account, b.oauth_account, "B's backup was not touched")

    def test_an_unproven_owner_changes_nothing(self):
        rig, app, a, b = self.pair()
        rig.api.script("profile", b.access, R(500, "down"))  # the API cannot say whose the live (B's) token is
        rig.write_live(b)
        before = rig.backup_of(a).token, rig.backup_of(b).token
        app.reconcile_live_login()
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual((rig.backup_of(a).token, rig.backup_of(b).token), before)

    def test_a_blank_live_login_is_never_saved(self):
        rig, app, a, b = self.pair()
        rig.write_live(a, json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}}))
        before = rig.backup_of(a).token
        app.reconcile_live_login()
        self.assertEqual(rig.backup_of(a).token, before)
        self.assertEqual(rig.api.calls_to("profile"), [], "a blank token is not a login to ask about")

    def test_it_stands_down_while_a_switch_or_sign_in_holds_the_mutex(self):
        rig, app, a, b = self.pair()
        rig.write_live(b)
        self.assertTrue(app.gate.try_begin("switch"))
        app.reconcile_live_login()
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual(rig.api.calls_to("profile"), [], "it did not even ask")
        app.gate.end("switch")

    def test_it_stands_down_if_a_switch_starts_while_it_asks(self):
        rig, app, a, b = self.pair()
        rig.write_live(b)
        real = app.service.account_identity

        def asking(*args, **kwargs):
            result = real(*args, **kwargs)
            app.gate.try_begin("switch")  # a switch begins during the network call
            return result

        app.service.account_identity = asking
        app.reconcile_live_login()
        self.assertEqual(app.active_account().id, a.id, "nothing was adopted under a running switch")
        app.gate.end("switch")

    def test_it_stands_down_if_the_login_changes_while_it_asks(self):
        rig, app, a, b = self.pair()
        real = app.service.account_identity

        def asking(*args, **kwargs):
            result = real(*args, **kwargs)
            rig.write_live(b)  # Claude Code (or the user) changed the login during the call
            return result

        app.service.account_identity = asking
        before = rig.backup_of(a).token
        app.reconcile_live_login()
        self.assertEqual(rig.backup_of(a).token, before)
        self.assertEqual(app.active_account().id, a.id)


class AuditTests(unittest.TestCase):
    def test_a_saved_login_that_provably_belongs_to_another_account_is_removed_and_flagged(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", saved=False)
        store = BackupStore(rig.paths.backups, rig.log.category("t"))
        store.save(a.credential, a.oauth_account, a.id)
        store.save(a.credential, a.oauth_account, b.id)  # 2026-09-21: A's login saved under B
        rig.api.add_account(a.access, a.uuid, a.email)
        app = rig.build()
        app.audit_saved_logins()
        self.assertIsNone(rig.backup_of(b), "B's wrong backup was deleted")
        self.assertIsNotNone(rig.backup_of(a))
        self.assertEqual(app.card_errors[b.id].message, "Its saved login belonged to another account and was removed. "
                         "Re-authenticate (↻) to fix.")
        self.assertTrue(app.card_errors[b.id].is_expired)

    def test_a_healthy_set_is_checked_once_per_launch_and_then_only_shared_grants(self):
        rig = Rig(self)
        rig.seed_account("a@example.invalid", active=True)
        rig.seed_account("b@example.invalid")
        app = rig.build()
        app.audit_saved_logins()
        first = len(rig.api.calls_to("profile"))
        self.assertEqual(first, 2, "every saved login once")
        app.audit_saved_logins()
        self.assertEqual(len(rig.api.calls_to("profile")), first, "after that only logins that share a grant")

    def test_it_does_not_ask_about_an_account_that_must_sign_in_again(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        app.flag_needs_reauth(app.account_by_id(b.id))
        app.audit_saved_logins()
        self.assertEqual(rig.api.calls_to("profile", b.access), [])

    def test_it_acts_only_if_the_account_still_holds_the_login_it_checked(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", saved=False)
        store = BackupStore(rig.paths.backups, rig.log.category("t"))
        store.save(a.credential, a.oauth_account, a.id)
        store.save(a.credential, a.oauth_account, b.id)
        app = rig.build()
        real = app.service.account_identity

        def asking(*args, **kwargs):
            result = real(*args, **kwargs)
            fresh = credential_text(fabricated("oat01"), fabricated("ort01"))
            store.save(fresh, b.oauth_account, b.id)  # B signed in again during the call
            return result

        app.service.account_identity = asking
        app.audit_saved_logins()
        self.assertIsNotNone(rig.backup_of(b), "the new login was not deleted")


class RemoveTests(unittest.TestCase):
    def three(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, priority=1)
        b = rig.seed_account("b@example.invalid", priority=2)
        c = rig.seed_account("c@example.invalid", priority=3)
        return rig, rig.build(), a, b, c

    def wait_idle(self, app):
        return wait_for(lambda: not app.gate.busy and not any(t.name == "switch-after-removal" for t in threading.enumerate()), 20)

    def test_removing_an_inactive_account_deletes_its_login_and_caches(self):
        rig, app, a, b, c = self.three()
        app.usage[b.id] = object()  # type: ignore[assignment]
        app.card_errors[b.id] = object()  # type: ignore[assignment]
        app.remove_account(b.id)
        self.assertIsNone(app.account_by_id(b.id))
        self.assertIsNone(rig.backup_of(b))
        self.assertNotIn(b.id, app.usage)
        self.assertNotIn(b.id, app.card_errors)
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual([x["id"] for x in read_json(rig.paths.accounts)], [a.id, c.id])

    def test_removing_the_active_account_marks_nothing_active_until_the_switch_has_succeeded(self):
        rig, app, a, b, c = self.three()
        seen = []
        real = app.perform_switch

        def watching(target):
            seen.append(([x.id for x in app.accounts if x.is_active], app.active_account().id))
            return real(target)

        app.perform_switch = watching
        app.remove_account(a.id)
        self.assertTrue(self.wait_idle(app))
        self.assertEqual(seen, [([], a.id)], "while the switch ran, no remaining account was marked active; "
                         "the removed one is still what the switch leaves")
        self.assertEqual(app.active_account().id, b.id, "the first fallback in priority order")
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(rig.live_config()["oauthAccount"], b.oauth_account)
        self.assertEqual([x.id for x in app.accounts if x.is_active], [b.id])

    def test_the_fallback_prefers_accounts_that_are_not_manual_only(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, priority=1)
        b = rig.seed_account("b@example.invalid", priority=2, switch=0)
        c = rig.seed_account("c@example.invalid", priority=3)
        app = rig.build()
        app.remove_account(a.id)
        self.assertTrue(self.wait_idle(app))
        self.assertEqual(app.active_account().id, c.id)

    def test_a_failed_fallback_tries_the_next_candidate(self):
        rig, app, a, b, c = self.three()
        rig.api.script("profile", b.access, R(401, "no"))
        os.unlink(rig.paths.backups)
        store = BackupStore(rig.paths.backups, rig.log.category("t"))
        store.save(b.credential, b.oauth_account, b.id)
        store.save(c.credential, c.oauth_account, c.id)
        # B's login is refused by the API and cannot be renewed (no refresh scripted): that switch fails.
        app.remove_account(a.id)
        self.assertTrue(self.wait_idle(app))
        self.assertEqual(app.active_account().id, c.id)

    def test_when_no_candidate_works_no_account_is_active(self):
        rig, app, a, b, c = self.three()
        os.unlink(rig.paths.backups)  # nothing to switch to
        app.remove_account(a.id)
        self.assertTrue(self.wait_idle(app))
        self.assertIsNone(app.active_account())
        self.assertEqual([x for x in app.accounts if x.is_active], [])
        self.assertEqual(rig.events_of("activeAccountChanged")[-1]["accountId"], None)

    def test_removing_the_last_account_clears_the_active_account_and_does_not_recreate_it(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        app = rig.build()
        app.remove_account(a.id)
        self.assertIsNone(app.active_account())
        self.assertEqual(app.accounts, [])
        app.refresh()
        app.reconcile_live_login()
        self.assertEqual(app.accounts, [], "the live login is not turned back into an account")
        self.assertEqual(rig.events_of("activeAccountChanged")[-1]["accountId"], None)

    def test_a_busy_removal_changes_nothing(self):
        rig, app, a, b, c = self.three()
        app.gate.try_begin("signin")
        with self.assertRaises(ActionError) as raised:
            app.remove_account(b.id)
        self.assertEqual((raised.exception.kind, raised.exception.message), ("busy", BUSY_MESSAGE))
        self.assertIsNotNone(app.account_by_id(b.id))
        self.assertIsNotNone(rig.backup_of(b))

    def test_an_unknown_account_is_not_found(self):
        rig, app, a, b, c = self.three()
        with self.assertRaises(ControlError) as raised:
            app.remove_account("nope")
        self.assertEqual(raised.exception.kind, "notFound")

    def test_a_removed_account_comes_back_without_its_old_readings_or_park(self):
        rig, app, a, b, c = self.three()
        app.guard.record_rejected(b.id, b.access)
        app.remove_account(b.id)
        self.assertFalse(app.guard.was_rejected(b.id, b.access))


class SettersAndEventsTests(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.a = self.rig.seed_account("a@example.invalid", active=True)
        self.b = self.rig.seed_account("b@example.invalid")
        self.c = self.rig.seed_account("c@example.invalid")
        self.app = self.rig.build()

    def accounts_changed(self):
        return len(self.rig.events_of("accountsChanged"))

    def test_label_threshold_priority_and_order_each_save_and_announce(self):
        app, rig = self.app, self.rig
        before = self.accounts_changed()
        app.set_label("Work", self.b.id)
        app.set_threshold(70, self.b.id)
        app.set_thresholds(self.b.id, session=60, weekly=ABSENT)
        self.assertTrue(app.set_priority(self.c.id, 1))
        self.assertTrue(app.set_account_order([self.b.id, self.a.id, self.c.id]))
        self.assertEqual(self.accounts_changed(), before + 5)
        stored = {r["id"]: r for r in read_json(rig.paths.accounts)}
        self.assertEqual([r["id"] for r in read_json(rig.paths.accounts)], [self.b.id, self.a.id, self.c.id])
        self.assertEqual(stored[self.b.id]["customLabel"], "Work")
        self.assertEqual([stored[i]["priority"] for i in (self.b.id, self.a.id, self.c.id)], [1, 2, 3],
                         "a whole-list reorder numbers every account from 1")

    def test_a_no_change_write_is_not_announced(self):
        before = self.accounts_changed()
        self.app.set_label(None, self.b.id)
        self.assertEqual(self.accounts_changed(), before)

    def test_the_legacy_threshold_write_clears_window_values_even_when_unchanged(self):
        app = self.app
        app.set_threshold(80, self.b.id)
        app.set_thresholds(self.b.id, session=60)  # splits: session 60, weekly 80, switchThreshold nil
        account = app.account_by_id(self.b.id)
        self.assertEqual((account.switch_threshold, account.session_threshold, account.weekly_threshold), (None, 60, 80))
        app.set_threshold(80, self.b.id)
        account = app.account_by_id(self.b.id)
        self.assertEqual((account.switch_threshold, account.session_threshold, account.weekly_threshold), (80, None, None))

    def test_priority_numbers_sort_the_list_and_the_one_set_last_goes_first_among_equals(self):
        app = self.app
        app.set_priority(self.a.id, 3)
        app.set_priority(self.b.id, 1)
        self.assertEqual([x.id for x in app.accounts][:2], [self.b.id, self.a.id])
        app.set_priority(self.c.id, 1)
        self.assertEqual([x.id for x in app.accounts][:2], [self.c.id, self.b.id], "equal numbers: the most recently set first")
        self.assertFalse(app.set_priority(self.a.id, 100))
        self.assertFalse(app.set_priority("unknown", 1))

    def test_an_order_that_does_not_name_every_account_once_is_refused(self):
        self.assertFalse(self.app.set_account_order([self.a.id, self.b.id]))
        self.assertFalse(self.app.set_account_order([self.a.id, self.a.id, self.b.id]))
        self.assertFalse(self.app.set_account_order([self.a.id, self.b.id, "x"]))

    def test_account_info_carries_the_2_0_fields(self):
        app = self.app
        app.set_thresholds(self.b.id, session=90, weekly=50)
        app.set_priority(self.b.id, 1)
        info = app.info_for(self.b.id)
        self.assertEqual((info["sessionThreshold"], info["weeklyThreshold"], info["priority"]), (90, 50, 1))
        self.assertEqual((info["effectiveSessionThreshold"], info["effectiveWeeklyThreshold"], info["effectiveThreshold"]),
                         (90, 50, 50))
        self.assertEqual(info["position"], 1)
        self.assertNotIn("token", json.dumps(info).lower())
        self.assertFalse(any("sk-ant" in json.dumps(i) for i in app.account_infos()))

    def test_the_threshold_settings_announce_accounts_changed(self):
        from pixelswitch import settings_catalog as catalog

        before = self.accounts_changed()
        self.app.set_setting(catalog.named("autoSwitch.sessionThreshold"), 80)
        self.app.set_setting(catalog.named("autoSwitch.weeklyThreshold"), 60)
        self.app.set_setting(catalog.named("autoSwitch.threshold"), 95)
        self.assertEqual(self.accounts_changed(), before + 3)
        self.app.set_setting(catalog.named("autoSwitch.drainEarly"), False)
        self.assertEqual(self.accounts_changed(), before + 3)
        info = self.app.account_info(self.app.account_by_id(self.b.id), 2)
        self.assertEqual((info["effectiveSessionThreshold"], info["effectiveWeeklyThreshold"]), (95, 95),
                         "settings.set autoSwitch.threshold clears both window defaults")


if __name__ == "__main__":
    unittest.main()


class NeedsReauthAnnouncementTests(unittest.TestCase):
    def test_a_flag_set_or_cleared_is_an_accounts_change_because_is_switchable_changed(self):
        rig = Rig(self)
        rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        before = len(rig.events_of("accountsChanged"))
        app.flag_needs_reauth(app.account_by_id(b.id))
        events = rig.events_of("accountsChanged")
        self.assertEqual(len(events), before + 1)
        info = [x for x in events[-1]["accounts"] if x["email"] == "b@example.invalid"][0]
        self.assertFalse(info["isSwitchable"])
        app.persist_needs_reauth()
        self.assertEqual(len(rig.events_of("accountsChanged")), before + 1, "unchanged: not announced again")
        app.guard.credential_changed(b.id, None)
        app.persist_needs_reauth()
        self.assertEqual(len(rig.events_of("accountsChanged")), before + 2)

    def test_the_backup_store_says_when_it_is_idle(self):
        rig = Rig(self)
        rig.seed_account("a@example.invalid", active=True)
        app = rig.build()
        self.assertTrue(app.backups.is_idle())
        entered, release = threading.Event(), threading.Event()

        def hold():
            with app.backups._lock:
                entered.set()
                release.wait(10)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(entered.wait(5))
        self.assertFalse(app.backups.is_idle())
        release.set()
        thread.join(5)
        self.assertTrue(app.backups.is_idle())
