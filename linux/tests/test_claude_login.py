"""Claude Code's login files: ``~/.claude/.credentials.json`` and ``~/.claude.json`` (spec 5.2, 5.3)."""
from __future__ import annotations

import json
import os
import stat
import threading
import time
import unittest

from pixelswitch import fsio
from pixelswitch.claude_login import (
    ClaudeLogin, LiveLoginBusy, LoginWriteError, RENEWING_MESSAGE, LEFTOVER_MESSAGE,
)
from pixelswitch.dirlock import LockLost, OWNER_NAME
from pixelswitch.logfile import Log
from pixelswitch.paths import Paths

from .lb_support import FakeClock, credential_text, read_json, tmpdir

LOG = Log(None).category("test")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir(self)
        self.paths = Paths(self.home, None)
        self.clock = FakeClock()
        self.login = ClaudeLogin(self.paths, LOG, clock=self.clock, sleep=self.clock.sleep)
        os.makedirs(self.paths.claude_dir, mode=0o700)

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def write_creds(self, text, mode=0o600):
        with open(self.paths.credentials, "w") as handle:
            handle.write(text)
        os.chmod(self.paths.credentials, mode)

    def write_config(self, value, mode=0o600):
        with open(self.paths.claude_json, "w") as handle:
            json.dump(value, handle)
        os.chmod(self.paths.claude_json, mode)


class ReadTests(Base):
    def test_reads_trimmed_text_or_none(self):
        self.assertIsNone(self.login.read_credential())
        self.write_creds("  " + credential_text("a", "r") + "\n")
        self.assertEqual(self.login.read_credential(), credential_text("a", "r"))
        self.write_creds("   \n")
        self.assertIsNone(self.login.read_credential())

    def test_oauth_account_is_a_copy_or_none(self):
        self.assertIsNone(self.login.read_oauth_account())
        self.write_config({"oauthAccount": {"emailAddress": "a@x.test", "n": None}})
        found = self.login.read_oauth_account()
        self.assertEqual(found, {"emailAddress": "a@x.test", "n": None})
        found["emailAddress"] = "changed"
        self.assertEqual(self.login.read_oauth_account()["emailAddress"], "a@x.test")
        self.write_config({"oauthAccount": "nope"})
        self.assertIsNone(self.login.read_oauth_account())
        with open(self.paths.claude_json, "w") as handle:
            handle.write("{broken")
        self.assertIsNone(self.login.read_oauth_account())


class WriteCredentialTests(Base):
    def test_writes_the_whole_credential_atomically_at_0600(self):
        text = credential_text("a1", "r1", top={"mcpOAuth": {"srv": {"t": 1}}})
        self.login.write_credential(text)
        self.assertEqual(read_json(self.paths.credentials), json.loads(text))
        self.assertEqual(self.mode(self.paths.credentials), 0o600)
        self.assertEqual([n for n in os.listdir(self.paths.claude_dir) if n.endswith(".tmp")], [])

    def test_a_replaced_file_keeps_its_mode_and_is_never_widened(self):
        self.write_creds(credential_text("old", "old"), 0o640)
        self.login.write_credential(credential_text("new", "new"))
        self.assertEqual(self.mode(self.paths.credentials), 0o640)
        self.write_creds(credential_text("old", "old"), 0o400)
        self.login.write_credential(credential_text("new2", "new2"))
        self.assertEqual(self.mode(self.paths.credentials), 0o400)

    def test_the_mtime_always_differs_from_the_old_one(self):
        """A running Claude Code stats this file and re-reads only when mtimeMs changed."""
        self.write_creds(credential_text("old", "old"))
        old_ns = os.stat(self.paths.credentials).st_mtime_ns
        original_stat = os.stat
        # Force the new file to land in the SAME clock tick as the old one.
        real_replace = os.replace

        def replace_then_pin(src, dst):
            real_replace(src, dst)
            os.utime(dst, ns=(old_ns, old_ns))

        os.replace, replaced = replace_then_pin, os.replace
        try:
            self.login.write_credential(credential_text("new", "new"))
        finally:
            os.replace = replaced
        self.assertNotEqual(os.stat(self.paths.credentials).st_mtime_ns, old_ns)
        del original_stat

    def test_a_symlinked_credentials_file_is_refused(self):
        real = os.path.join(self.home, "real.json")
        with open(real, "w") as handle:
            handle.write("keep")
        os.symlink(real, self.paths.credentials)
        with self.assertRaises(LoginWriteError):
            self.login.write_credential(credential_text("a", "r"))
        with open(real) as handle:
            self.assertEqual(handle.read(), "keep")

    def test_a_hard_linked_credentials_file_is_refused(self):
        self.write_creds("keep")
        os.link(self.paths.credentials, os.path.join(self.home, "other"))
        with self.assertRaises(LoginWriteError):
            self.login.write_credential(credential_text("a", "r"))

    def test_the_storage_write_lock_is_taken_and_released(self):
        seen = []
        original = fsio.atomic_write

        def watch(path, data, **kw):
            seen.append(os.path.isdir(os.path.join(self.paths.claude_dir, ".storage-write")))
            return original(path, data, **kw)

        fsio.atomic_write, restore = watch, fsio.atomic_write
        try:
            self.login.write_credential(credential_text("a", "r"))
        finally:
            fsio.atomic_write = restore
        self.assertEqual(seen, [True])
        self.assertFalse(os.path.exists(os.path.join(self.paths.claude_dir, ".storage-write")))

    def test_a_busy_storage_lock_fails_the_write_with_nothing_written(self):
        os.mkdir(os.path.join(self.paths.claude_dir, ".storage-write"))
        self.write_creds(credential_text("old", "old"))
        with self.assertRaises(LoginWriteError) as raised:
            self.login.write_credential(credential_text("new", "new"))
        self.assertIn("Claude Code is saving its login", str(raised.exception))
        self.assertEqual(read_json(self.paths.credentials)["claudeAiOauth"]["accessToken"], "old")
        self.assertTrue(os.path.isdir(os.path.join(self.paths.claude_dir, ".storage-write")), "not broken")

    def test_nothing_to_write_is_an_error(self):
        with self.assertRaises(LoginWriteError):
            self.login.write_credential("")

    def test_the_claude_dir_is_created_0700_if_missing(self):
        os.rmdir(self.paths.claude_dir)
        self.login.write_credential(credential_text("a", "r"))
        self.assertEqual(self.mode(self.paths.claude_dir), 0o700)


class WriteOAuthAccountTests(Base):
    def test_only_oauth_account_changes_every_other_key_and_the_mode_survive(self):
        original = {"numStartups": 3, "projects": {"/a": {"x": [1, 2]}}, "oauthAccount": {"emailAddress": "old@x.test"},
                    "theme": "dark", "nested": {"k": None, "n": 1.5, "u": "é"}}
        self.write_config(original, 0o640)
        self.assertTrue(self.login.write_oauth_account({"emailAddress": "new@x.test", "accountUuid": "u"}))
        after = read_json(self.paths.claude_json)
        expected = dict(original)
        expected["oauthAccount"] = {"emailAddress": "new@x.test", "accountUuid": "u"}
        self.assertEqual(after, expected)
        self.assertEqual(list(after), list(original), "key order is kept")
        self.assertEqual(self.mode(self.paths.claude_json), 0o640)
        with open(self.paths.claude_json, encoding="utf-8") as handle:
            self.assertFalse(handle.read().endswith("\n"), "JSON.stringify(x, null, 2) has no trailing newline")

    def test_a_missing_or_unreadable_file_is_refused(self):
        self.assertFalse(self.login.write_oauth_account({"emailAddress": "a@x.test"}))
        self.assertFalse(os.path.exists(self.paths.claude_json))
        with open(self.paths.claude_json, "w") as handle:
            handle.write("[1,2]")
        self.assertFalse(self.login.write_oauth_account({"emailAddress": "a@x.test"}))
        with open(self.paths.claude_json) as handle:
            self.assertEqual(handle.read(), "[1,2]")

    def test_symlink_and_hard_link_are_refused(self):
        real = os.path.join(self.home, "real.json")
        with open(real, "w") as handle:
            json.dump({"oauthAccount": {}}, handle)
        os.symlink(real, self.paths.claude_json)
        self.assertFalse(self.login.write_oauth_account({"emailAddress": "a@x.test"}))
        os.unlink(self.paths.claude_json)
        self.write_config({"oauthAccount": {}})
        os.link(self.paths.claude_json, os.path.join(self.home, "other.json"))
        self.assertFalse(self.login.write_oauth_account({"emailAddress": "a@x.test"}))

    def test_claude_codes_config_lock_is_honoured_and_never_broken(self):
        self.write_config({"oauthAccount": {"emailAddress": "old@x.test"}})
        os.mkdir(self.paths.claude_json + ".lock")
        self.assertFalse(self.login.write_oauth_account({"emailAddress": "new@x.test"}))
        self.assertEqual(read_json(self.paths.claude_json)["oauthAccount"]["emailAddress"], "old@x.test")
        self.assertTrue(os.path.isdir(self.paths.claude_json + ".lock"))
        os.rmdir(self.paths.claude_json + ".lock")
        self.assertTrue(self.login.write_oauth_account({"emailAddress": "new@x.test"}))
        self.assertFalse(os.path.exists(self.paths.claude_json + ".lock"), "released")


class RefreshLockTests(Base):
    refresh = property(lambda self: os.path.join(self.paths.claude_dir, ".oauth_refresh.lock"))
    owner = property(lambda self: os.path.join(self.paths.claude_dir, OWNER_NAME))
    legacy = property(lambda self: os.path.realpath(self.paths.claude_dir) + ".lock")

    def test_both_locks_are_held_inside_and_gone_after(self):
        with self.login.refresh_lock() as held:
            self.assertTrue(os.path.isdir(self.refresh))
            self.assertTrue(os.path.isdir(self.legacy))
            held.check()
        self.assertFalse(os.path.exists(self.refresh))
        self.assertFalse(os.path.exists(self.legacy))
        self.assertFalse(os.path.exists(self.owner))

    def test_the_holder_record_describes_this_process_and_is_removed(self):
        with self.login.refresh_lock():
            if not os.path.exists(self.owner):
                self.skipTest("this filesystem keeps no birth time, so no holder record is written")
            record = read_json(self.owner)
            self.assertEqual(record["pid"], os.getpid())
            self.assertGreater(record["lockBirthtimeMs"], 0)
            self.assertIn("pidDomain", record)
            self.assertIn("pidSpace", record)
            self.assertEqual(self.mode(self.owner), 0o600)
        self.assertFalse(os.path.exists(self.owner))

    def test_the_locks_are_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with self.login.refresh_lock():
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(self.refresh))
        self.assertFalse(os.path.exists(self.legacy))

    def test_waits_for_claude_code_then_gives_up_with_nothing_taken(self):
        os.mkdir(self.refresh)  # Claude Code is renewing
        with self.assertRaises(LiveLoginBusy) as raised:
            with self.login.refresh_lock(timeout=2.0):
                self.fail("must not be entered")
        self.assertEqual(raised.exception.message, RENEWING_MESSAGE)
        self.assertEqual(RENEWING_MESSAGE, "Claude Code is renewing this login; try again in a minute.")
        self.assertTrue(os.path.isdir(self.refresh), "Claude Code's lock was not touched")
        self.assertFalse(os.path.exists(self.legacy))
        self.assertGreaterEqual(sum(self.clock.slept), 1.99, "it waited")

    def test_a_lock_that_looks_abandoned_gets_its_own_message_and_is_still_not_broken(self):
        os.mkdir(self.refresh)
        os.utime(self.refresh, (self.clock.now - 120, self.clock.now - 120))
        with self.assertRaises(LiveLoginBusy) as raised:
            with self.login.refresh_lock(timeout=1.0):
                pass
        self.assertEqual(raised.exception.message, LEFTOVER_MESSAGE)
        self.assertTrue(os.path.isdir(self.refresh))

    def test_a_busy_legacy_lock_lets_go_of_the_new_one_and_fails_after_the_wait(self):
        os.makedirs(os.path.dirname(self.legacy), exist_ok=True)
        os.mkdir(self.legacy)
        with self.assertRaises(LiveLoginBusy):
            with self.login.refresh_lock(timeout=1.5):
                pass
        self.assertFalse(os.path.exists(self.refresh), "the new lock was let go, not kept")
        self.assertTrue(os.path.isdir(self.legacy))

    def test_the_wait_for_claude_code_ends_when_it_lets_go(self):
        os.mkdir(self.refresh)
        login = ClaudeLogin(self.paths, LOG)  # real clock and sleep

        def release():
            time.sleep(0.2)
            os.rmdir(self.refresh)

        threading.Thread(target=release).start()
        with login.refresh_lock(timeout=10.0) as held:
            held.check()

    def test_the_hold_limit_is_enforced(self):
        with self.login.refresh_lock(max_hold=10.0) as held:
            self.clock.advance(11)
            with self.assertRaises(LockLost):
                held.check()

    def test_the_default_limits_are_the_specs(self):
        from pixelswitch import claude_login

        self.assertEqual(claude_login.REFRESH_WAIT_SECONDS, 35.0)
        self.assertEqual(claude_login.REFRESH_HOLD_SECONDS, 10.0)


if __name__ == "__main__":
    unittest.main()
