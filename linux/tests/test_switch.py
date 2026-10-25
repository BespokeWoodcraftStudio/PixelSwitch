"""The switch (credentials.md section 3 mapped to Linux), under the one mutex, with the usable-login
rule (D12), re-read-before-write, Claude Code's refresh lock and a verified result."""
from __future__ import annotations

import json
import os
import stat
import threading
import time
import unittest

from pixelswitch import claude_cli, fsio
from pixelswitch.claude_login import RENEWING_MESSAGE
from pixelswitch.coordinator import ActionError, BUSY_MESSAGE, LIVE_CHANGED_MESSAGE
from pixelswitch.stores import BackupStore

from .lb_support import Rig, credential_text, read_file, read_json, wait_for
from .mock_api import R, fabricated, usage_body


def two_accounts(case, **kw):
    rig = Rig(case)
    a = rig.seed_account("a@example.invalid", active=True, live_extra_top={"mcpOAuth": {"srv": {"live": "token"}}}, **kw)
    b = rig.seed_account("b@example.invalid")
    coordinator = rig.build()
    return rig, coordinator, a, b


def write_raw_backups(rig, mapping):
    """backups.json with whatever shape is given (the store refuses to save an unusable login, so a
    test that needs one on disk writes the file itself)."""
    fsio.atomic_write(rig.paths.backups, json.dumps(mapping))


class SwitchTests(unittest.TestCase):
    def test_a_switch_between_two_accounts_rewrites_both_files_and_keeps_everything_else(self):
        rig, app, a, b = two_accounts(self)
        creds_before_mode = rig.mode_of(rig.paths.credentials)
        app.perform_switch(app.account_by_id(b.id))
        live = rig.live_credential()
        self.assertEqual(live["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(live["claudeAiOauth"]["refreshToken"], b.refresh)
        self.assertEqual(live["mcpOAuth"], {"srv": {"live": "token"}}, "every other top key is kept from the live file")
        self.assertEqual(rig.mode_of(rig.paths.credentials), 0o600)
        self.assertEqual(creds_before_mode, 0o600)
        config = rig.live_config()
        self.assertEqual(config["oauthAccount"], b.oauth_account)
        self.assertEqual(config["numStartups"], 3)
        self.assertEqual(config["projects"], {"/x": {}})
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(rig.mode_of(rig.paths.claude_json), 0o600)
        self.assertEqual([x for x in os.listdir(rig.paths.claude_dir) if x.endswith(".tmp")], [])
        self.assertEqual([x for x in os.listdir(rig.home) if x.endswith(".tmp")], [])
        self.assertEqual(os.listdir(rig.paths.claude_dir), [".credentials.json"], "no lock is left behind")
        self.assertFalse(os.path.exists(rig.paths.claude_json + ".lock"))

    def test_the_leaving_login_is_saved_under_its_proven_owner(self):
        rig, app, a, b = two_accounts(self)
        rotated = credential_text(fabricated("oat01"), fabricated("ort01"), top={"mcpOAuth": {"srv": {"live": "token"}}})
        rig.write_live(a, rotated)  # Claude Code renewed A's login since PixelSwitch last saved it
        rotated_access = json.loads(rotated)["claudeAiOauth"]["accessToken"]
        rig.api.add_account(rotated_access, a.uuid, a.email)
        app.perform_switch(app.account_by_id(b.id))
        saved = rig.backup_of(a)
        self.assertEqual(json.loads(saved.token)["claudeAiOauth"]["accessToken"], rotated_access,
                         "the newest login is what switching back will restore")
        self.assertEqual(saved.oauth_account, a.oauth_account)

    def test_the_state_after_a_switch(self):
        rig, app, a, b = two_accounts(self)
        before = rig.clock.now
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual([x.id for x in app.accounts if x.is_active], [b.id])
        self.assertEqual(app.last_auto_switch_at, before, "any switch restarts the auto-switch cooldown")
        self.assertIn(a.id, app.usage_sampled_at, "the refresh that ends a switch read the account that left ...")
        self.assertIn(b.id, app.usage_sampled_at, "... and the one that arrived")
        self.assertEqual(rig.events_of("activeAccountChanged")[-1]["accountId"], b.id)
        self.assertTrue(rig.events_of("accountsChanged"))
        stored = json.loads(read_file(rig.paths.accounts))
        self.assertEqual([r["id"] for r in stored if r["isActive"]], [b.id])
        self.assertIn("lastUsed", [k for r in stored if r["id"] == b.id for k in r])

    def test_the_cli_confirms_the_target_afterwards(self):
        rig, app, a, b = two_accounts(self)
        app.perform_switch(app.account_by_id(b.id))
        calls = [json.loads(l) for l in read_file(os.path.join(rig.home, "stub-calls.log")).splitlines()]
        self.assertIn(["auth", "status"], [c["args"] for c in calls])

    def test_already_live_is_no_switch(self):
        rig, app, a, b = two_accounts(self)
        before = read_file(rig.paths.credentials)
        app.perform_switch(app.account_by_id(a.id))
        self.assertEqual(read_file(rig.paths.credentials), before)
        self.assertEqual(rig.api.calls_to("token"), [])

    def test_a_live_login_that_belongs_to_the_target_is_adopted(self):
        rig, app, a, b = two_accounts(self)
        rig.write_live(b)  # the user ran `claude auth login` by hand as B
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)

    def test_no_active_account_is_a_quiet_failure(self):
        rig = Rig(self)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertTrue(raised.exception.silent)
        self.assertIn("nothing to switch from", raised.exception.message)

    def test_an_unproven_live_owner_skips_the_snapshot_but_the_switch_goes_on(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, hours=-1)  # the live access token has expired
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        backup_before = rig.backup_of(a).token
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(rig.backup_of(a).token, backup_before, "an unproven login is never saved over a backup")
        self.assertEqual(rig.api.calls_to("profile", a.access), [], "the expired token was not sent")


class UsableLoginTests(unittest.TestCase):
    """D12 on a switch: an unusable saved login is never written to Claude Code's store."""

    SHAPES = {
        "blank accessToken": {"accessToken": "", "refreshToken": "r", "expiresAt": 9_999_999_999_999},
        "whitespace accessToken": {"accessToken": " \n", "refreshToken": "r", "expiresAt": 9_999_999_999_999},
        "blank refreshToken": {"accessToken": "a", "refreshToken": "", "expiresAt": 9_999_999_999_999},
        "whitespace refreshToken": {"accessToken": "a", "refreshToken": "\t", "expiresAt": 9_999_999_999_999},
        "null accessToken": {"accessToken": None, "refreshToken": "r", "expiresAt": 9_999_999_999_999},
        "null refreshToken": {"accessToken": "a", "refreshToken": None, "expiresAt": 9_999_999_999_999},
        "expiresAt zero": {"accessToken": "a", "refreshToken": "r", "expiresAt": 0},
        "expiresAt null": {"accessToken": "a", "refreshToken": "r", "expiresAt": None},
    }

    def with_bad_target(self, oauth):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", saved=False)
        token = json.dumps({"claudeAiOauth": oauth} if oauth is not None else {"mcpOAuth": {}})
        write_raw_backups(rig, {a.id: {"token": a.credential, "oauthAccount": a.oauth_account},
                                b.id: {"token": token, "oauthAccount": b.oauth_account}})
        return rig, rig.build(), a, b

    def test_every_unusable_shape_fails_the_switch_with_the_resign_message_and_writes_nothing(self):
        for name, oauth in list(self.SHAPES.items()) + [("missing claudeAiOauth", None)]:
            with self.subTest(name):
                rig, app, a, b = self.with_bad_target(oauth)
                creds, config = read_file(rig.paths.credentials), read_file(rig.paths.claude_json)
                with self.assertRaises(ActionError) as raised:
                    app.perform_switch(app.account_by_id(b.id))
                self.assertEqual(raised.exception.message, "The saved login for b@example.invalid has expired and could "
                                 "not be renewed. Re-authenticate b@example.invalid to fix it.")
                self.assertEqual(read_file(rig.paths.credentials), creds)
                self.assertEqual(read_file(rig.paths.claude_json), config)
                self.assertEqual(rig.api.calls_to("token"), [], "nothing to renew with")
                self.assertFalse(app.is_switchable(app.account_by_id(b.id)), "and it is not switchable")
                self.assertFalse(app.account_info(app.account_by_id(b.id), 2)["isSwitchable"])

    def test_the_switch_function_itself_is_the_last_line_of_defence(self):
        rig, app, a, b = two_accounts(self)
        from pixelswitch.coordinator import SwitchFailure
        from pixelswitch.stores import AccountBackup

        bad = AccountBackup(json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "r"}}), b.oauth_account)
        creds = read_file(rig.paths.credentials)
        with self.assertRaises(SwitchFailure):
            app._switch_account(app.account_by_id(a.id), None, a.credential, app.account_by_id(b.id), bad)
        self.assertEqual(read_file(rig.paths.credentials), creds)

    def test_a_blank_live_login_is_never_saved_over_a_good_backup(self):
        """R4: Claude Code blanks a dead login on disk and leaves the file in place."""
        rig, app, a, b = two_accounts(self)
        blank = json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}})
        rig.write_live(a, blank)
        good = rig.backup_of(a).token
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.backup_of(a).token, good)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)


class TargetValidationTests(unittest.TestCase):
    def test_a_missing_backup_and_an_unreadable_store_are_different_messages(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", saved=False)
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "No stored credentials for b@example.invalid. Use re-authenticate to fix.")
        with open(rig.paths.backups, "w") as handle:
            handle.write("garbage")
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "Credential storage is temporarily unavailable. Try again shortly.")

    def test_a_target_login_that_belongs_to_another_account_is_refused(self):
        rig, app, a, b = two_accounts(self)
        rig.api.add_account(b.access, a.uuid, a.email)  # the API says B's saved token is really A's
        creds = read_file(rig.paths.credentials)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "The login saved for b@example.invalid belongs to a@example.invalid. "
                         "Re-authenticate b@example.invalid to fix it.")
        self.assertEqual(read_file(rig.paths.credentials), creds)

    def test_saved_details_that_belong_to_another_account_are_refused(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", saved=False)
        BackupStore(rig.paths.backups, rig.log.category("t")).save(a.credential, a.oauth_account, a.id)
        BackupStore(rig.paths.backups, rig.log.category("t")).save(b.credential, a.oauth_account, b.id)  # A's details with B's token
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("The saved details for b@example.invalid belong to another account.", raised.exception.message)

    def test_a_login_also_saved_under_another_account_is_refused_when_the_api_cannot_say(self):
        rig, app, a, b = two_accounts(self)
        rig.api.script("profile", a.access, R(500, "down"))  # B's saved login IS A's, and the API cannot say
        BackupStore(rig.paths.backups, rig.log.category("t")).save(a.credential, a.oauth_account, b.id)
        with open(rig.paths.backups) as handle:
            data = json.load(handle)
        data[b.id]["oauthAccount"] = b.oauth_account
        fsio.atomic_write(rig.paths.backups, json.dumps(data))
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("is also saved under another account", raised.exception.message)

    def test_an_expired_target_is_renewed_first_and_the_new_login_is_written(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        new_access, new_refresh = fabricated("oat01"), fabricated("ort01")
        rig.api.add_refresh(b.refresh, new_access, new_refresh)
        rig.api.add_account(new_access, b.uuid, b.email)
        app = rig.build()
        app.perform_switch(app.account_by_id(b.id))
        live = rig.live_credential()["claudeAiOauth"]
        self.assertEqual((live["accessToken"], live["refreshToken"]), (new_access, new_refresh))
        self.assertEqual(len(rig.api.calls_to("token")), 1)
        self.assertEqual(json.loads(rig.backup_of(b).token)["claudeAiOauth"]["refreshToken"], new_refresh)

    def test_a_dead_target_grant_fails_the_switch_and_flags_the_account(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)  # no refresh script: the mock says invalid_grant
        app = rig.build()
        creds = read_file(rig.paths.credentials)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("has expired and could not be renewed", raised.exception.message)
        self.assertTrue(app.guard.is_needs_reauth(b.id))
        self.assertEqual(read_json(rig.paths.needs_reauth), [b.id])
        self.assertEqual(read_file(rig.paths.credentials), creds)

    def test_a_renewal_that_cannot_reach_the_server_lets_the_switch_go_ahead(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        rig.api.script("token", b.refresh, R(503, "down"))
        app = rig.build()
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)


class ReReadBeforeWriteTests(unittest.TestCase):
    """Claude Code renews the live login while PixelSwitch is switching (spec 5.3)."""

    def rotate_live(self, rig, seed, prove=True):
        new_access, new_refresh = fabricated("oat01"), fabricated("ort01")
        text = credential_text(new_access, new_refresh, top={"mcpOAuth": {"srv": {"live": "token"}}})
        if prove:
            rig.api.add_account(new_access, seed.uuid, seed.email)
        return text, new_access

    def test_a_renewal_before_the_lock_is_reproved_outside_it_and_the_newest_login_is_saved(self):
        rig, app, a, b = two_accounts(self)
        text, new_access = self.rotate_live(rig, a)
        login = app.login
        real = login.refresh_lock
        calls = []

        def hooked(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                rig.write_live(a, text)  # lands after the proof of the old token, before the lock is taken
            return real(*args, **kwargs)

        login.refresh_lock = hooked
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(len(calls), 2, "the lock was taken, left for the proof, then taken again")
        self.assertEqual(json.loads(rig.backup_of(a).token)["claudeAiOauth"]["accessToken"], new_access)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(len(rig.api.calls_to("profile", new_access)), 1, "proved exactly once, and not under the lock")

    def test_the_proof_of_the_new_login_never_happens_while_the_lock_is_held(self):
        rig, app, a, b = two_accounts(self)
        text, new_access = self.rotate_live(rig, a)
        held_during_profile = []
        service = app.service
        real_identity = service.account_identity

        def watching(access_token, *args, **kwargs):
            if access_token == new_access:
                held_during_profile.append(os.path.exists(app.login.refresh_lock_path))
            return real_identity(access_token, *args, **kwargs)

        service.account_identity = watching
        real = app.login.refresh_lock
        once = []

        def hooked(*a_, **kw):
            if not once:
                once.append(1)
                rig.write_live(a, text)
            return real(*a_, **kw)

        app.login.refresh_lock = hooked
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(held_during_profile, [False])

    def test_a_renewal_that_cannot_be_proved_aborts_with_nothing_written(self):
        rig, app, a, b = two_accounts(self)
        text, new_access = self.rotate_live(rig, a, prove=False)  # the API does not know the new token
        real = app.login.refresh_lock
        once = []

        def hooked(*args, **kwargs):
            if not once:
                once.append(1)
                rig.write_live(a, text)
            return real(*args, **kwargs)

        app.login.refresh_lock = hooked
        backup_before = rig.backup_of(a).token
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_CHANGED_MESSAGE)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], new_access, "the live login is untouched")
        self.assertEqual(rig.backup_of(a).token, backup_before)
        self.assertEqual(rig.live_config()["oauthAccount"], a.oauth_account)

    def test_a_change_between_the_snapshot_and_the_write_is_caught_by_the_reread(self):
        rig, app, a, b = two_accounts(self)
        text, new_access = self.rotate_live(rig, a)
        reads = []
        real_read = app.login.read_credential

        def read_and_rotate():
            reads.append(1)
            if len(reads) == 2:  # the second read is the re-read right before the write: it already differs
                rig.write_live(a, text)
            return real_read()

        app.login.read_credential = read_and_rotate
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(json.loads(rig.backup_of(a).token)["claudeAiOauth"]["accessToken"], new_access,
                         "the retry backed the newest login up before writing")

    def test_a_login_that_keeps_changing_aborts(self):
        rig, app, a, b = two_accounts(self)
        counter = [0]
        real_read = app.login.read_credential

        def always_new():
            counter[0] += 1
            access = fabricated("oat01")
            rig.api.add_account(access, a.uuid, a.email)
            rig.write_live(a, credential_text(access, fabricated("ort01")))
            return real_read()

        app.login.read_credential = always_new
        original = rig.live_credential()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_CHANGED_MESSAGE)
        self.assertNotEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        del original


class RefreshLockOnSwitchTests(unittest.TestCase):
    def test_claude_code_renewing_blocks_the_switch_after_the_wait_with_nothing_written(self):
        rig, app, a, b = two_accounts(self)
        os.mkdir(os.path.join(rig.paths.claude_dir, ".oauth_refresh.lock"))  # Claude Code is renewing
        creds, config = read_file(rig.paths.credentials), read_file(rig.paths.claude_json)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, RENEWING_MESSAGE)
        self.assertGreaterEqual(sum(rig.clock.slept), 34.99, "it waited the 35 seconds")
        self.assertEqual(read_file(rig.paths.credentials), creds)
        self.assertEqual(read_file(rig.paths.claude_json), config)
        self.assertTrue(os.path.isdir(os.path.join(rig.paths.claude_dir, ".oauth_refresh.lock")), "its lock is untouched")
        self.assertFalse(app.gate.busy, "and the mutex is free again")

    def test_the_lock_is_held_around_the_whole_snapshot_merge_write_and_readback(self):
        rig, app, a, b = two_accounts(self)
        held = {}
        real_save = app.backups.save
        real_write_cred = app.login.write_credential
        real_write_acct = app.login.write_oauth_account
        lock = os.path.join(rig.paths.claude_dir, ".oauth_refresh.lock")

        def save(*args, **kw):
            held.setdefault("snapshot", os.path.isdir(lock))
            return real_save(*args, **kw)

        def write_cred(text):
            held["credential"] = os.path.isdir(lock)
            return real_write_cred(text)

        def write_acct(account):
            held["identity"] = os.path.isdir(lock)
            return real_write_acct(account)

        app.backups.save, app.login.write_credential, app.login.write_oauth_account = save, write_cred, write_acct
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(held, {"snapshot": True, "credential": True, "identity": True})
        self.assertFalse(os.path.exists(lock))

    def test_the_lock_is_released_when_the_write_fails(self):
        rig, app, a, b = two_accounts(self)
        os.mkdir(os.path.join(rig.paths.claude_dir, ".storage-write"))  # Claude Code is saving its login
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("Claude Code is saving its login", raised.exception.message)
        self.assertFalse(os.path.exists(os.path.join(rig.paths.claude_dir, ".oauth_refresh.lock")))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], a.access)

    def test_holding_the_lock_past_the_limit_aborts_before_the_write(self):
        rig, app, a, b = two_accounts(self)
        real_save = app.backups.save

        def slow_save(*args, **kw):
            rig.clock.advance(11)  # the snapshot took too long
            return real_save(*args, **kw)

        app.backups.save = slow_save
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("could not finish within the time it may hold Claude Code's lock", raised.exception.message)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], a.access)
        self.assertFalse(os.path.exists(os.path.join(rig.paths.claude_dir, ".oauth_refresh.lock")))


class FailureHandlingTests(unittest.TestCase):
    def test_a_failed_identity_write_puts_the_previous_login_back(self):
        rig, app, a, b = two_accounts(self)
        os.mkdir(rig.paths.claude_json + ".lock")  # ~/.claude.json is locked: the identity write fails
        before = read_file(rig.paths.credentials)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "Failed to write oauthAccount to ~/.claude.json")
        self.assertEqual(json.loads(read_file(rig.paths.credentials)), json.loads(before),
                         "Claude Code is not left on one account with another's identity")
        self.assertEqual(app.active_account().id, a.id)

    def test_the_cli_naming_the_wrong_account_fails_the_switch(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        b.oauth_account["emailAddress"] = "someone-else@example.invalid"  # same uuid, other email
        BackupStore(rig.paths.backups, rig.log.category("t")).save(b.credential, b.oauth_account, b.id)
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "Switch failed: expected b@example.invalid but got "
                         "someone-else@example.invalid. Try removing and re-adding the account.")

    def test_a_shadowed_cli_is_verified_against_the_stores_and_warned_about(self):
        rig, app, a, b = two_accounts(self)
        app.cli._base_env["STUB_STATUS_SHADOW"] = "1"
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(app.error_message, "Switched to b@example.invalid, but the Claude CLI is authenticating via "
                         "apiKey instead of the stored login, so it will not use this account.")
        self.assertEqual(rig.events_of("error")[-1]["message"], app.error_message)

    def test_a_cli_that_does_not_answer_is_checked_against_the_stores(self):
        rig, app, a, b = two_accounts(self)
        app.cli._base_env["STUB_STATUS_HANG"] = "1"
        original = claude_cli.STATUS_TIMEOUT
        claude_cli.STATUS_TIMEOUT = 0.6
        self.addCleanup(setattr, claude_cli, "STATUS_TIMEOUT", original)
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(app.active_account().id, b.id)

    def test_a_missing_cli_is_checked_against_the_stores_too(self):
        rig, app, a, b = two_accounts(self)
        app.settings.replace({"claudeBinaryPathPreference": ""})
        app.cli._base_env["PATH"] = "/nonexistent"
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(app.active_account().id, b.id)

    def test_the_switch_checks_the_stores_and_fails_when_they_do_not_match(self):
        rig, app, a, b = two_accounts(self)
        app.cli._base_env["STUB_STATUS_HANG"] = "1"
        original = claude_cli.STATUS_TIMEOUT
        claude_cli.STATUS_TIMEOUT = 0.5
        self.addCleanup(setattr, claude_cli, "STATUS_TIMEOUT", original)
        real = app.login.write_oauth_account
        app.login.write_oauth_account = lambda account: (real(dict(account, emailAddress="other@example.invalid")), True)[1]
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "Account switch verification failed")


class OneMutexTests(unittest.TestCase):
    def test_a_second_switch_while_one_runs_is_busy(self):
        rig, app, a, b = two_accounts(self)
        c = rig.seed_account("c@example.invalid")
        app = rig.build()
        entered, release = threading.Event(), threading.Event()
        real = app.cli.auth_status

        def slow_status():
            entered.set()
            release.wait(10)
            return real()

        app.cli.auth_status = slow_status
        results = []
        thread = threading.Thread(target=lambda: results.append(app.perform_switch(app.account_by_id(b.id))))
        thread.start()
        self.assertTrue(entered.wait(10))
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(c.id))
        self.assertEqual((raised.exception.kind, raised.exception.message), ("busy", BUSY_MESSAGE))
        with self.assertRaises(ActionError) as raised:
            app.add_current_account()
        self.assertEqual(raised.exception.kind, "busy")
        with self.assertRaises(ActionError) as raised:
            app.remove_account(c.id)
        self.assertEqual(raised.exception.kind, "busy")
        with self.assertRaises(ActionError) as raised:
            from pixelswitch.signin import Purpose

            app.start_sign_in(Purpose.new_account())
        self.assertEqual(raised.exception.kind, "busy")
        release.set()
        thread.join(20)
        self.assertEqual(app.active_account().id, b.id)
        self.assertFalse(app.gate.busy)

    def test_a_failed_switch_frees_the_mutex(self):
        rig, app, a, b = two_accounts(self)
        os.mkdir(os.path.join(rig.paths.claude_dir, ".storage-write"))
        with self.assertRaises(ActionError):
            app.perform_switch(app.account_by_id(b.id))
        self.assertFalse(app.gate.busy)


if __name__ == "__main__":
    unittest.main()
