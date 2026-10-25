"""Lane FL1: the switch, removal, shutdown and state-race fixes of the LB review, each against the real
coordinator in a scratch HOME with the mock API. Every test names the finding and the Mac code it ports."""
from __future__ import annotations

import json
import os
import stat
import threading
import time
import unittest
from dataclasses import replace

from pixelswitch import coordinator as coordinator_module
from pixelswitch import fsio, removal
from pixelswitch.accounts import Account
from pixelswitch.coordinator import (
    ActionError, BUSY_MESSAGE, LIVE_CHANGED_MESSAGE, LIVE_NOT_SAVED_MESSAGE, LIVE_UNREADABLE_MESSAGE,
    NO_ACCOUNTS_MESSAGE, SHUTTING_DOWN_MESSAGE,
)
from pixelswitch.stores import BackupStore
from pixelswitch.usage_guard import fingerprint_of

from .lb_support import Rig, credential_text, read_file, read_json
from .mock_api import R, fabricated

SENTENCE = removal.REFUSAL_SENTENCE
BLANK = json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}})


def base_config(rig, oauth_account=None):
    """A ~/.claude.json (a switch rewrites it, so it must exist)."""
    config = {"numStartups": 3, "projects": {"/x": {}}, "theme": "dark"}
    if oauth_account is not None:
        config["oauthAccount"] = oauth_account
    fsio.atomic_write(rig.paths.claude_json, json.dumps(config))


def unowned(case, n=2, **kw):
    """n saved accounts, none active, nothing live yet (the state a removal whose fallbacks all failed leaves)."""
    rig = Rig(case)
    seeds = [rig.seed_account(f"{name}@example.invalid", **kw) for name in "abcdef"[:n]]
    app = rig.build()
    return (rig, app, *seeds)


def write_live_text(rig, text, oauth_account=None):
    fsio.atomic_write(rig.paths.credentials, text)
    base_config(rig, oauth_account)


def files(rig):
    return read_file(rig.paths.credentials) if os.path.exists(rig.paths.credentials) else None, read_file(rig.paths.claude_json)


class NoActiveAccountTests(unittest.TestCase):
    """D20 revision 4 on Linux: a switch with accounts but none active
    (AppState.performSwitch :1041-1101, unownedLiveLoginDecision :1252)."""

    def test_the_list_is_what_refuses_not_the_active_account(self):
        rig, app, a, b = unowned(self)
        self.assertIsNone(app.active_account())
        base_config(rig)
        app.perform_switch(app.account_by_id(b.id))        # no credentials file at all: nothing to lose
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(app.active_account().id, b.id)

    def test_a_blanked_live_login_has_nothing_to_lose(self):
        rig, app, a, b = unowned(self)
        write_live_text(rig, BLANK)
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(rig.backup_of(a).token, a.credential, "nothing was saved over any account's login")

    def test_a_login_the_api_says_is_nobodys_on_the_list_is_replaced_and_saved_nowhere(self):
        rig, app, a, b = unowned(self)
        token = fabricated("oat01")
        rig.api.add_account(token, "stranger-uuid", "stranger@example.invalid")
        write_live_text(rig, credential_text(token, fabricated("ort01")))
        before = (rig.backup_of(a).token, rig.backup_of(b).token)
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual((rig.backup_of(a).token, rig.backup_of(b).token), before)
        self.assertEqual(len(rig.api.calls_to("profile", token)), 1)

    def test_contradictory_api_data_refuses(self):
        rig, app, a, b = unowned(self)
        token = fabricated("oat01")
        rig.api.add_account(token, a.uuid, b.email)       # the id says A, the email says B: no single owner
        write_live_text(rig, credential_text(token, fabricated("ort01")))
        self.assertIsNone(app.proven_owner(read_file(rig.paths.credentials), None))
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, SENTENCE)
        self.assertEqual(files(rig), before)

    def test_the_same_grant_as_exactly_one_remaining_account_is_treated_as_the_active_one(self):
        rig, app, a, b = unowned(self)
        rig.write_live(a)
        rig.api.script("profile", a.access, R(500, "down"))      # the API cannot say
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(rig.backup_of(a).token, a.credential, "nothing is saved under an unproven owner")

    def test_switching_to_that_one_account_adopts_it_and_writes_nothing(self):
        rig, app, a, b = unowned(self)
        rig.write_live(a)
        rig.api.script("profile", a.access, R(500, "down"))
        before = files(rig)
        app.perform_switch(app.account_by_id(a.id))
        self.assertEqual(app.active_account().id, a.id)
        self.assertEqual(files(rig), before)

    def test_the_removed_accounts_login_is_disposable(self):
        rig = Rig(self)
        x = rig.seed_account("x@example.invalid")
        a = rig.seed_account("a@example.invalid")
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        rig.write_live(x)
        app.remove_account(x.id)                          # x was not active: nothing else changes
        self.assertIsNone(app.active_account())
        rig.api.script("profile", x.access, R(500, "down"))
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertFalse(os.path.exists(os.path.join(rig.paths.state_dir, "removed-logins.json")),
                         "the record is cleared by the switch")

    def test_a_login_nobody_can_name_is_refused_with_the_macs_sentence_and_nothing_changes(self):
        rig, app, a, b = unowned(self)
        token = fabricated("oat01")
        write_live_text(rig, credential_text(token, fabricated("ort01")))
        rig.api.script("profile", token, R(500, "down"))
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual((raised.exception.kind, raised.exception.message, raised.exception.silent), ("failed", SENTENCE, False))
        self.assertEqual(files(rig), before)
        self.assertIsNone(app.active_account())
        self.assertFalse(app.gate.busy)

    def test_a_grant_shared_by_two_remaining_accounts_is_ambiguous(self):
        rig, app, a, b = unowned(self)
        store = BackupStore(rig.paths.backups, rig.log.category("t"))
        store.save(a.credential, a.oauth_account, b.id)    # B's saved login is A's
        data = read_json(rig.paths.backups)
        data[b.id]["oauthAccount"] = b.oauth_account
        fsio.atomic_write(rig.paths.backups, json.dumps(data))
        rig.write_live(a)
        rig.api.script("profile", a.access, R(500, "down"))
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, SENTENCE)
        self.assertEqual(files(rig), before)

    def test_the_decision_is_bound_to_the_login_it_judged(self):
        """AppState passes JudgedLogin; if a real login appears after the judgment, nothing is written."""
        rig, app, a, b = unowned(self)
        base_config(rig)                                  # no credentials file when it is judged
        real_read = app.login.read_credential
        calls = []

        def read():
            calls.append(1)
            if len(calls) == 2:                            # step 1 of the switch: Claude Code signed in meanwhile
                token = fabricated("oat01")
                rig.api.add_account(token, "late-uuid", "late@example.invalid")
                fsio.atomic_write(rig.paths.credentials, credential_text(token, fabricated("ort01")))
            return real_read()

        app.login.read_credential = read
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_CHANGED_MESSAGE)
        self.assertNotEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)

    def test_an_empty_list_keeps_the_old_sentence(self):
        rig = Rig(self)
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(Account(id="X", email="x@example.invalid"))
        self.assertEqual((raised.exception.message, raised.exception.silent), (NO_ACCOUNTS_MESSAGE, True))


class RemovedLoginsRecordTests(unittest.TestCase):
    def test_removing_the_active_account_records_fingerprints_never_tokens_at_0600(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        app.reconcile_live_login()                         # the API proves the live login is A's
        os.mkdir(os.path.join(rig.paths.claude_dir, ".oauth_refresh.lock"))   # so the fallback switch cannot finish
        app._spawn = lambda *a_, **k: None
        app.remove_account(a.id)
        path = os.path.join(rig.paths.state_dir, "removed-logins.json")
        prints = read_json(path)
        self.assertEqual(prints, [fingerprint_of(a.refresh)], "the saved login and the proven live login share a refresh token")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        text = read_file(path)
        self.assertNotIn(a.refresh, text)
        self.assertNotIn(a.access, text)

    def test_a_live_login_that_was_only_believed_to_be_the_removed_accounts_is_not_recorded(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        rig.seed_account("b@example.invalid")
        app = rig.build()
        stranger = credential_text(fabricated("oat01"), fabricated("ort01"))
        fsio.atomic_write(rig.paths.credentials, stranger)  # signed in by hand; never proven
        app._spawn = lambda *a_, **k: None
        app.remove_account(a.id)
        prints = read_json(os.path.join(rig.paths.state_dir, "removed-logins.json"))
        self.assertEqual(prints, [fingerprint_of(a.refresh)])

    def test_the_record_is_bounded_and_cleared_by_add_current_and_by_a_sign_in_style_adopt(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        path = os.path.join(rig.paths.state_dir, "removed-logins.json")
        app.removed_logins.save([f"f{i}" for i in range(30)])
        self.assertEqual(len(read_json(path)), 16)
        app.adopt_active(app.account_by_id(b.id))
        self.assertFalse(os.path.exists(path))


class RemovalFallbackTests(unittest.TestCase):
    """AppState.switchAfterRemoval / RemovalFallback.run, driven directly so the timing is exact."""

    def setup_three(self):
        rig = Rig(self)
        x = rig.seed_account("x@example.invalid", active=True, priority=1)
        a = rig.seed_account("a@example.invalid", priority=2)
        b = rig.seed_account("b@example.invalid", priority=3)
        app = rig.build()
        spawned = []
        app._spawn = lambda target, name, *args: spawned.append((target, args))
        app.remove_account(x.id)
        self.assertEqual(len(spawned), 1)
        return rig, app, x, a, b, spawned[0]

    def test_a_switch_the_user_finished_during_the_wait_is_not_undone(self):
        rig, app, x, a, b, (target, args) = self.setup_three()
        app.gate.try_begin("signin")                      # the first attempt is busy and the fallback waits
        tried = []
        real = app.perform_switch
        app.perform_switch = lambda account: (tried.append(account.id), real(account))[1]
        pauses = []

        def fake_sleep(seconds):
            pauses.append(seconds)
            app.gate.end("signin")
            app._mark_active(b.id)                         # the user chose B meanwhile
        app.sleep = fake_sleep
        target(*args)
        self.assertEqual(tried, [a.id], "one busy attempt; after the wait it stood down")
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], x.access, "nothing was switched")

    def test_a_sign_in_that_finished_during_the_wait_is_not_switched_away(self):
        rig, app, x, a, b, (target, args) = self.setup_three()
        app.gate.try_begin("signin")
        new = app.accounts[0]
        app.sleep = lambda seconds: (app.gate.end("signin"), app._mark_active(new.id))[0]
        tried = []
        real = app.perform_switch
        app.perform_switch = lambda account: (tried.append(account.id), real(account))[1]
        target(*args)
        self.assertEqual(tried, [a.id])
        self.assertEqual(app.active_account().id, new.id)

    def test_a_gate_busy_for_the_whole_wait_tries_nothing_else_clears_the_active_account_and_says_why(self):
        rig, app, x, a, b, (target, args) = self.setup_three()
        app.gate.try_begin("signin")
        tried = []
        real = app.perform_switch
        app.perform_switch = lambda account: (tried.append(account.id), real(account))[1]
        target(*args)
        self.assertEqual(set(tried), {a.id}, "the same candidate is retried; the others are not tried")
        self.assertEqual(len(tried), 151)
        self.assertEqual(len(rig.clock.slept), 150)
        self.assertIsNone(app.active_account())
        self.assertEqual(app.error_message, BUSY_MESSAGE)
        self.assertEqual(rig.events_of("error")[-1]["message"], BUSY_MESSAGE)
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], x.access)

    def test_when_none_works_the_last_failure_is_shown(self):
        rig, app, x, a, b, (target, args) = self.setup_three()
        os.unlink(rig.paths.backups)                       # nothing to switch to
        target(*args)
        self.assertIsNone(app.active_account())
        expected = f"No stored credentials for {b.email}. Use re-authenticate to fix."
        self.assertEqual(app.error_message, expected, "the LAST candidate's sentence")
        self.assertEqual(rig.events_of("error")[-1]["message"], expected)

    def test_a_candidate_that_works_takes_over_with_no_error(self):
        rig, app, x, a, b, (target, args) = self.setup_three()
        target(*args)
        self.assertEqual(app.active_account().id, a.id)
        self.assertIsNone(app.error_message)


class FirstPassTests(unittest.TestCase):
    """LiveLoginBackup.afterFirstPass on the real switch (LB review, coordinator.py:1273, P0)."""

    def renewed_a(self, rig, a, prove=True):
        access, refresh = fabricated("oat01"), fabricated("ort01")
        text = credential_text(access, refresh, top={"mcpOAuth": {"srv": {"live": "token"}}})
        if prove:
            rig.api.add_account(access, a.uuid, a.email)
        rig.write_live(a, text)
        return text

    def two(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, live_extra_top={"mcpOAuth": {"srv": {"live": "token"}}})
        b = rig.seed_account("b@example.invalid")
        return rig, rig.build(), a, b

    def test_a_store_that_refuses_the_save_of_a_renewed_login_writes_nothing(self):
        rig, app, a, b = self.two()
        self.renewed_a(rig, a)
        app.backups.save = lambda *args, **kw: False       # hard-linked, full, or owned by root
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_NOT_SAVED_MESSAGE)
        self.assertEqual(files(rig), before, "A's renewed refresh token is not thrown away")
        self.assertEqual(app.active_account().id, a.id)

    def test_a_store_that_refuses_the_save_of_an_unchanged_login_writes_nothing(self):
        rig, app, a, b = self.two()
        app.backups.save = lambda *args, **kw: False
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_NOT_SAVED_MESSAGE)
        self.assertEqual(files(rig), before)

    def test_a_working_store_still_switches(self):
        rig, app, a, b = self.two()
        self.renewed_a(rig, a)
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)

    def test_a_live_login_that_cannot_be_read_at_step_one_writes_nothing(self):
        rig, app, a, b = self.two()
        real = app.login.read_credential
        calls = []

        def read():
            calls.append(1)
            return None if len(calls) == 2 else real()     # the call after the proof: step 1's read

        app.login.read_credential = read
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_UNREADABLE_MESSAGE)
        self.assertEqual(files(rig), before)

    def test_a_live_login_that_vanishes_before_the_write_and_stays_gone_writes_nothing(self):
        rig, app, a, b = self.two()
        real = app.login.read_credential
        calls = []

        def read():
            calls.append(1)
            return None if len(calls) >= 3 else real()    # step 1 reads it; the re-read and the retry read nothing

        app.login.read_credential = read
        before = files(rig)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, LIVE_UNREADABLE_MESSAGE)
        self.assertEqual(files(rig), before)


class StorageWriteLockTests(unittest.TestCase):
    """The re-read and the merge happen inside Claude Code's .storage-write lock (LB review, coordinator.py:1267)."""

    def test_a_save_claude_code_finishes_while_the_switch_waits_is_kept(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True, live_extra_top={"mcpOAuth": {"srv": {"old": 1}}})
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        lock = os.path.join(rig.paths.claude_dir, ".storage-write")
        os.mkdir(lock)                                      # Claude Code is in the middle of its save
        real_sleep = app.login._sleep
        done = []

        def claude_code_finishes(seconds):
            if not done:
                done.append(1)
                live = read_json(rig.paths.credentials)
                live["mcpOAuth"] = {"srv": {"rotated": "refresh-token-2"}}   # the rotated MCP login
                fsio.atomic_write(rig.paths.credentials, json.dumps(live))
                os.rmdir(lock)
            real_sleep(seconds)

        app.login._sleep = claude_code_finishes
        app.perform_switch(app.account_by_id(b.id))
        live = rig.live_credential()
        self.assertEqual(live["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(live["mcpOAuth"], {"srv": {"rotated": "refresh-token-2"}},
                         "the MCP login Claude Code rotated while the switch waited is not overwritten")

    def test_a_login_claude_code_saves_under_the_lock_is_seen_by_the_reread(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        lock = os.path.join(rig.paths.claude_dir, ".storage-write")
        os.mkdir(lock)
        real_sleep = app.login._sleep
        new_access = fabricated("oat01")
        rig.api.add_account(new_access, a.uuid, a.email)
        done = []

        def claude_code_logs_in_again(seconds):
            if not done:
                done.append(1)
                fsio.atomic_write(rig.paths.credentials, credential_text(new_access, fabricated("ort01")))
                os.rmdir(lock)
            real_sleep(seconds)

        app.login._sleep = claude_code_logs_in_again
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], b.access)
        self.assertEqual(json.loads(rig.backup_of(a).token)["claudeAiOauth"]["accessToken"], new_access,
                         "the newer login was backed up before it was replaced")

    def test_the_lock_is_not_held_while_the_identity_is_written(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        storage = os.path.join(rig.paths.claude_dir, ".storage-write")
        real = app.login.write_oauth_account
        seen = {}

        def watching(account, **kw):
            seen["storage_held"] = os.path.isdir(storage)
            return real(account, **kw)

        app.login.write_oauth_account = watching
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(seen, {"storage_held": False})
        self.assertFalse(os.path.exists(storage))

    def test_the_rollback_compares_and_writes_under_the_lock(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        os.mkdir(rig.paths.claude_json + ".lock")           # the identity write fails
        storage = os.path.join(rig.paths.claude_dir, ".storage-write")
        real_write = app.login.write_credential
        held_during = []

        def write(text, held=None):
            held_during.append(os.path.isdir(storage))
            return real_write(text, held)

        app.login.write_credential = write
        before = read_json(rig.paths.credentials)
        with self.assertRaises(ActionError):
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(held_during, [True, True], "the switch's write and the rollback's both hold .storage-write")
        self.assertEqual(read_json(rig.paths.credentials), before)


class HoldBudgetTests(unittest.TestCase):
    """Every wait under the refresh lock is capped by what is left of its 10 s (LB review, coordinator.py:1305)."""

    def test_a_busy_storage_lock_is_waited_for_only_what_is_left(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        os.mkdir(os.path.join(rig.paths.claude_dir, ".storage-write"))     # Claude Code never lets go
        real_save = app.backups.save

        def slow_save(*args, **kw):
            rig.clock.advance(6)                              # step 1 took 6 of the 10 seconds
            return real_save(*args, **kw)

        app.backups.save = slow_save
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("Claude Code is saving its login", raised.exception.message)
        self.assertLessEqual(sum(rig.clock.slept), 4.5, "not the full 10 s on top of the 6 already spent")
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], a.access)

    def test_a_busy_config_lock_is_waited_for_only_what_is_left_and_the_login_goes_back(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        os.mkdir(rig.paths.claude_json + ".lock")
        real_write = app.login.write_credential

        def slow_write(text, held=None):
            real_write(text, held)
            rig.clock.advance(7)                              # the credential write used 7 of the 10 seconds

        app.login.write_credential = slow_write
        before = read_json(rig.paths.credentials)
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.message, "Failed to write oauthAccount to ~/.claude.json")
        self.assertLessEqual(sum(rig.clock.slept), 3.5)
        self.assertEqual(read_json(rig.paths.credentials), before, "rolled back")

    def test_held_refresh_reports_what_is_left(self):
        rig = Rig(self)
        with rig.build().login.refresh_lock() as held:
            self.assertAlmostEqual(held.remaining(), 10.0, delta=0.01)
            rig.clock.advance(4)
            self.assertAlmostEqual(held.remaining(), 6.0, delta=0.01)
            rig.clock.advance(60)
            self.assertEqual(held.remaining(), 0.0)


class RenewalDuringSwitchTests(unittest.TestCase):
    """LB review, usagecycle.py:259."""

    def test_a_renewal_that_finishes_during_the_switch_is_what_gets_written(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        new_access, new_refresh = fabricated("oat01"), fabricated("ort01")
        renewed = credential_text(new_access, new_refresh)
        real_save = app.backups.save
        fired = []

        def save(token, oauth_account, account_id):
            result = real_save(token, oauth_account, account_id)
            if account_id == a.id and not fired:
                fired.append(1)                                # the cycle renews B's saved login (RT1 -> RT2)
                real_save(renewed, b.oauth_account, b.id)
            return result

        app.backups.save = save
        app.perform_switch(app.account_by_id(b.id))
        live = rig.live_credential()["claudeAiOauth"]
        self.assertEqual((live["accessToken"], live["refreshToken"]), (new_access, new_refresh),
                         "not the refresh token the renewal just spent")

    def test_the_cycle_may_not_start_a_renewal_for_the_account_a_switch_is_moving_to(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        app = rig.build()
        app._switching_to = b.id
        outcome = app.refresh_backup_in_place(app.account_by_id(b.id))
        self.assertEqual(outcome.kind, "unreachable")
        self.assertEqual(rig.api.calls_to("token"), [])
        # the switch itself may
        rig.api.add_refresh(b.refresh, fabricated("oat01"), fabricated("ort01"))
        self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id), by_switch=True).kind, "refreshed")


class ShutdownTests(unittest.TestCase):
    """Nothing new starts after SIGTERM (spec 5.1)."""

    def build(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid", hours=-1)
        return rig, rig.build(), a, b

    def test_no_renewal_starts(self):
        rig, app, a, b = self.build()
        saves = []
        real_save = app.backups.save
        app.backups.save = lambda *args, **kw: (saves.append(1), real_save(*args, **kw))[1]
        app.begin_shutdown()
        self.assertEqual(app.refresh_backup_in_place(app.account_by_id(b.id)).kind, "unreachable")
        self.assertEqual(rig.api.calls_to("token"), [])
        self.assertEqual(saves, [], "not even the writability probe before a renewal")

    def test_no_usage_request_starts(self):
        rig, app, a, b = self.build()
        app.begin_shutdown()
        self.assertIsNone(app.read_usage(app.account_by_id(a.id)))
        self.assertIsNone(app.read_usage(app.account_by_id(b.id)))
        self.assertEqual(rig.api.calls_to("usage"), [])
        self.assertEqual(rig.api.calls_to("token"), [])

    def test_a_usage_request_that_was_under_way_and_fails_with_401_sends_no_token_request(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        from pixelswitch.usage_guard import UsageRequestError

        def slow_then_401(access_token):
            app.begin_shutdown()                             # SIGTERM arrives while the GET is slow
            raise UsageRequestError("expired")

        app.service.get_usage_limits = slow_then_401
        self.assertIsNone(app.read_usage(app.account_by_id(b.id)))
        self.assertEqual(rig.api.calls_to("token"), [])

    def test_every_entry_point_refuses_and_changes_nothing(self):
        rig, app, a, b = self.build()
        c = rig.seeds[0]
        before = (read_file(rig.paths.credentials), read_file(rig.paths.claude_json), read_file(rig.paths.backups))
        app.begin_shutdown()
        from pixelswitch.signin import Purpose

        calls = {
            "perform_switch": lambda: app.perform_switch(app.account_by_id(b.id)),
            "add_current_account": app.add_current_account,
            "start_sign_in": lambda: app.start_sign_in(Purpose.new_account()),
            "remove_account": lambda: app.remove_account(b.id),
        }
        for name, call in calls.items():
            with self.subTest(name):
                with self.assertRaises(ActionError) as raised:
                    call()
                self.assertEqual(raised.exception.message, SHUTTING_DOWN_MESSAGE)
        after = (read_file(rig.paths.credentials), read_file(rig.paths.claude_json), read_file(rig.paths.backups))
        self.assertEqual(after, before)
        self.assertIsNotNone(app.account_by_id(b.id))
        del c

    def test_finish_shutdown_waits_for_the_cycle_thread(self):
        rig, app, a, b = self.build()
        release = threading.Event()
        started = threading.Event()

        def cycle():
            started.set()
            release.wait(5)

        app._cycle_thread = threading.Thread(target=cycle, daemon=True)
        app._cycle_thread.start()
        started.wait(2)
        app.begin_shutdown()
        threading.Timer(0.3, release.set).start()
        t0 = time.monotonic()
        self.assertTrue(app.finish_shutdown(timeout=5))
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)


class StateRaceTests(unittest.TestCase):
    """Every change to the accounts list is one critical section (LB review, coordinator.py:978)."""

    def test_a_label_change_and_a_switch_of_the_active_account_never_overwrite_each_other(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        c = rig.seed_account("c@example.invalid")
        app = rig.build()
        errors = []

        def labels():
            try:
                for i in range(150):
                    app.set_label(f"L{i}", c.id)
            except Exception as error:                      # pragma: no cover
                errors.append(error)

        def switching():
            try:
                for i in range(150):
                    app._mark_active(a.id if i % 2 else b.id)
            except Exception as error:                      # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=labels), threading.Thread(target=switching)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(app.account_by_id(c.id).custom_label, "L149", "the last label survived")
        flagged = [x.id for x in app.accounts if x.is_active]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0], app.active_account().id, "the list's flag always matches the active account")
        stored = read_json(rig.paths.accounts)
        self.assertEqual([r["id"] for r in stored if r.get("isActive")], flagged)
        self.assertEqual(next(r for r in stored if r["id"] == c.id)["customLabel"], "L149")

    def test_a_sign_in_that_adds_an_account_is_not_lost_to_a_label_change(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        started = threading.Event()
        real = app._save_accounts

        def slow_save():
            started.set()
            time.sleep(0.05)
            real()

        newcomer = Account(id="NEW", email="new@example.invalid", display_name="N", is_active=True)
        app._save_accounts = slow_save
        adder = threading.Thread(target=lambda: app._modify_accounts(
            lambda current: ([replace(x, is_active=False) for x in current] + [newcomer], newcomer)))
        adder.start()
        started.wait(2)
        app.set_label("mine", b.id)                         # arrives while the add is installing
        adder.join(5)
        ids = [x.id for x in app.accounts]
        self.assertIn("NEW", ids)
        self.assertEqual(app.account_by_id(b.id).custom_label, "mine")
        self.assertEqual(app.active_account().id, "NEW")
        self.assertEqual([r["id"] for r in read_json(rig.paths.accounts) if r.get("isActive")], ["NEW"])


class ReadModifyWriteTests(unittest.TestCase):
    def test_a_change_that_reads_the_list_cannot_be_overtaken_by_another_writer(self):
        """The list is read, changed and installed under ONE lock: while the label change is still
        working on the list it read, a sign-in's new account waits, and neither is lost."""
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        inside = threading.Event()

        def slow_label(current):
            inside.set()
            time.sleep(0.3)                                   # still holding the list it read
            return [replace(x, custom_label="mine") if x.id == b.id else x for x in current], True

        newcomer = Account(id="NEW", email="new@example.invalid", display_name="N")
        first = threading.Thread(target=lambda: app._modify_accounts(slow_label))
        first.start()
        inside.wait(2)
        second = threading.Thread(target=lambda: app._modify_accounts(lambda current: ([*current, newcomer], True)))
        second.start()
        first.join(5)
        second.join(5)
        self.assertEqual(app.account_by_id(b.id).custom_label, "mine")
        self.assertIsNotNone(app.account_by_id("NEW"), "the new account was not overwritten by the older list")
        self.assertEqual({r["id"] for r in read_json(rig.paths.accounts)}, {a.id, b.id, "NEW"})


class GateWaitTests(unittest.TestCase):
    """The cycle's reconcile and audit are not a switch (LB review, coordinator.py:1394)."""

    def test_a_switch_waits_briefly_for_the_cycles_reconcile(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        self.assertTrue(app.gate.try_begin("reconcile"))
        threading.Timer(0.3, lambda: app.gate.end("reconcile")).start()
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(app.active_account().id, b.id)

    def test_it_still_answers_busy_when_a_background_step_outlasts_the_wait(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        original = coordinator_module.USER_ACTION_GATE_WAIT_SECONDS
        coordinator_module.USER_ACTION_GATE_WAIT_SECONDS = 0.2
        self.addCleanup(setattr, coordinator_module, "USER_ACTION_GATE_WAIT_SECONDS", original)
        app.gate.try_begin("audit")
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.kind, "busy")

    def test_a_real_switch_in_progress_is_never_waited_for(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        app.gate.try_begin("switch")
        t0 = time.monotonic()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.kind, "busy")
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_a_sign_in_waits_for_a_background_step_and_a_gate_helper_is_exact(self):
        gate = coordinator_module.MutationGate()
        self.assertTrue(gate.begin("switch"))
        self.assertFalse(gate.begin("switch", ("reconcile",), 0.05))
        gate.end("switch")
        self.assertTrue(gate.try_begin("reconcile"))
        threading.Timer(0.1, lambda: gate.end("reconcile")).start()
        self.assertTrue(gate.begin("signin", ("reconcile", "audit"), 2.0))
        self.assertEqual(gate.kind, "signin")


class NoAccountReconcileTests(unittest.TestCase):
    """A 429 on the live token with no active account is recorded once (LB review, claude_service.py:113)."""

    def test_two_cycles_after_a_429_send_one_profile_request(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid")
        b = rig.seed_account("b@example.invalid")
        token = fabricated("oat01")
        write_live_text(rig, credential_text(token, fabricated("ort01")))
        rig.api.script("profile", token, R(429, "slow", {"Retry-After": "3600"}))
        app = rig.build()
        self.assertIsNone(app.active_account())
        app.reconcile_live_login()
        rig.clock.advance(300)
        app.reconcile_live_login()
        rig.clock.advance(300)
        app.reconcile_live_login()
        self.assertEqual(len(rig.api.calls_to("profile", token)), 1)


class SettingsRaceTests(unittest.TestCase):
    def test_two_keys_set_from_two_connections_both_survive(self):
        rig = Rig(self)
        rig.seed_account("a@example.invalid", active=True)
        app = rig.build()
        from pixelswitch import settings_catalog as catalog

        errors = []

        def setter(name, values):
            try:
                for value in values:
                    app.set_setting(catalog.named(name), value)
            except Exception as error:                      # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=setter, args=("autoSwitch.weeklyThreshold", [50 + i % 40 for i in range(80)])),
                   threading.Thread(target=setter, args=("autoSwitch.drainWithinHours", [1 + i % 10 for i in range(80)]))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        stored = read_json(rig.paths.settings)
        self.assertEqual(stored["autoSwitchWeeklyThreshold"], 50 + 79 % 40)
        self.assertEqual(stored["autoSwitchDrainWithinHours"], 1 + 79 % 10)

    def test_update_is_one_read_modify_write(self):
        rig = Rig(self)
        rig.seed_account("a@example.invalid", active=True)
        app = rig.build()
        seen = []
        app.settings.update(lambda current: (seen.append(dict(current)), {**current, "x": 1})[1])
        app.settings.update(lambda current: (seen.append(dict(current)), {**current, "y": 2})[1])
        self.assertEqual(seen[1].get("x"), 1)
        self.assertEqual(app.settings.snapshot()["y"], 2)
        self.assertEqual(read_json(rig.paths.settings)["x"], 1)


class LogHasNoEmailsTests(unittest.TestCase):
    def test_a_failed_switch_logs_ids_not_addresses(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        b.oauth_account["emailAddress"] = "someone-else@example.invalid"
        BackupStore(rig.paths.backups, rig.log.category("t")).save(b.credential, b.oauth_account, b.id)
        app = rig.build()
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertIn("someone-else@example.invalid", raised.exception.message)
        text = rig.log_text()
        self.assertNotIn("someone-else@example.invalid", text)
        self.assertNotIn("b@example.invalid", text)
        self.assertIn(f"Switch to {b.id} failed", text)


class CurrentActiveUnderTheGateTests(unittest.TestCase):
    """LB review, coordinator.py:1035: current_active is read after the gate is taken."""

    def test_a_switch_that_waited_sees_the_account_the_other_switch_made_active(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        # A's request starts after B's switch finished: with a stale current_active (A) and a live owner
        # that cannot be proven, the shortcut would answer "no switch needed" while Claude Code is on B.
        app.perform_switch(app.account_by_id(b.id))
        rig.api.script("profile", b.access, R(500, "down"))
        before = files(rig)
        app.perform_switch(app.account_by_id(a.id))
        self.assertEqual(rig.live_credential()["claudeAiOauth"]["accessToken"], a.access)
        self.assertNotEqual(files(rig), before)

    def test_the_active_account_is_read_after_the_gate_is_taken(self):
        rig = Rig(self)
        a = rig.seed_account("a@example.invalid", active=True)
        b = rig.seed_account("b@example.invalid")
        app = rig.build()
        order = []
        real_active, real_begin = app.active_account, app.gate.begin
        app.active_account = lambda: (order.append("active"), real_active())[1]
        app.gate.begin = lambda *args, **kw: (order.append("gate"), real_begin(*args, **kw))[1]
        app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(order[:2], ["gate", "active"])


if __name__ == "__main__":
    unittest.main()
