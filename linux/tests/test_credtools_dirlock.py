"""credtools (re-read-before-write, renewal expiry, the three D12 gates) and dirlock (the proper-lockfile
style directory locks Claude Code uses, which PixelSwitch honours and never breaks)."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest

from pixelswitch import credtools
from pixelswitch.dirlock import DirLock, LockBusy, LockLedger, LockLost, birth_time_ms, owner_record, proc_start
from pixelswitch.logfile import Log

from .lb_support import credential_text, read_json, tmpdir

LOG = Log(None).category("test")


def cred(access="a", refresh="r", **kw):
    return credential_text(access, refresh, **kw)


class LiveLoginChangedTests(unittest.TestCase):
    """The compare behind "re-read before write": the Mac's CredentialOwnership.liveLoginChanged."""

    def test_same_text_is_no_change_even_when_unreadable(self):
        self.assertFalse(credtools.live_login_changed(None, None))
        self.assertFalse(credtools.live_login_changed("junk", "junk"))
        self.assertFalse(credtools.live_login_changed(cred(), cred()))

    def test_a_different_access_or_refresh_token_is_a_change(self):
        base = cred("a1", "r1")
        self.assertTrue(credtools.live_login_changed(base, cred("a2", "r1")))
        self.assertTrue(credtools.live_login_changed(base, cred("a1", "r2")))
        self.assertTrue(credtools.live_login_changed(base, cred("a2", "r2")))

    def test_other_keys_and_expiry_are_not_a_change(self):
        a = cred("a1", "r1", top={"mcpOAuth": {"s": 1}})
        b = cred("a1", "r1", top={"mcpOAuth": {"s": 2}}, hours=1)
        self.assertFalse(credtools.live_login_changed(a, b))

    def test_one_side_unreadable_is_a_change(self):
        self.assertTrue(credtools.live_login_changed(cred(), None))
        self.assertTrue(credtools.live_login_changed(None, cred()))
        self.assertTrue(credtools.live_login_changed(cred(), "junk"))
        self.assertTrue(credtools.live_login_changed(cred(), json.dumps({"claudeAiOauth": "x"})))
        self.assertTrue(credtools.live_login_changed("junk", "other junk"))


class RenewalExpiryTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def test_a_positive_refresh_token_expires_in_sets_the_key(self):
        oauth = {"accessToken": "a", "refreshTokenExpiresAt": 5, "scopes": ["s"]}
        result = credtools.applying_renewal_expiry(oauth, 7776000, self.NOW)
        self.assertEqual(result["refreshTokenExpiresAt"], int(self.NOW * 1000) + 7776000 * 1000)
        self.assertEqual(result["scopes"], ["s"])
        self.assertEqual(oauth["refreshTokenExpiresAt"], 5, "the input is not mutated")

    def test_no_value_and_a_stored_value_in_the_past_removes_the_key(self):
        result = credtools.applying_renewal_expiry({"refreshTokenExpiresAt": int(self.NOW * 1000) - 1, "x": 1}, None, self.NOW)
        self.assertNotIn("refreshTokenExpiresAt", result)
        self.assertEqual(result["x"], 1)
        result = credtools.applying_renewal_expiry({"refreshTokenExpiresAt": int(self.NOW * 1000)}, None, self.NOW)
        self.assertNotIn("refreshTokenExpiresAt", result, "now itself counts as past")

    def test_no_value_and_a_stored_value_in_the_future_keeps_it(self):
        future = int(self.NOW * 1000) + 5000
        self.assertEqual(credtools.applying_renewal_expiry({"refreshTokenExpiresAt": future}, None, self.NOW)["refreshTokenExpiresAt"], future)
        self.assertNotIn("refreshTokenExpiresAt", credtools.applying_renewal_expiry({}, None, self.NOW))

    def test_zero_negative_or_junk_responses_do_not_set_it(self):
        for value in (0, -5, "100", True, float("inf"), None):
            result = credtools.applying_renewal_expiry({"refreshTokenExpiresAt": 1}, value, self.NOW)
            self.assertNotIn("refreshTokenExpiresAt", result, value)


class GateTests(unittest.TestCase):
    def test_save_decision(self):
        self.assertTrue(credtools.gate_save(cred()).allowed)
        refused = credtools.gate_save(cred(access=""))
        self.assertFalse(refused.allowed)
        self.assertIn("accessToken", refused.reason)

    def test_target_problem(self):
        self.assertIsNone(credtools.gate_target_problem(cred()))
        self.assertIn("refreshToken", credtools.gate_target_problem(cred(refresh=" ")))

    def test_switchable(self):
        self.assertTrue(credtools.gate_is_switchable(cred(), False, False))
        self.assertFalse(credtools.gate_is_switchable(None, False, False))
        self.assertFalse(credtools.gate_is_switchable(cred(access=""), False, False))
        self.assertFalse(credtools.gate_is_switchable(cred(), True, False))
        self.assertFalse(credtools.gate_is_switchable(cred(), False, True))


class DirLockTests(unittest.TestCase):
    def setUp(self):
        self.dir = tmpdir(self)
        self.path = os.path.join(self.dir, ".oauth_refresh.lock")

    def lock(self, **kw):
        kw.setdefault("stale", 60.0)
        kw.setdefault("update", None)
        return DirLock(self.path, **kw)

    def test_acquire_makes_a_0700_directory_and_release_removes_it(self):
        lock = self.lock()
        self.assertTrue(lock.try_acquire())
        self.assertTrue(os.path.isdir(self.path))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o700)
        self.assertTrue(lock.held)
        lock.release()
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(lock.held)

    def test_a_held_lock_is_busy_for_another(self):
        first, second = self.lock(), self.lock()
        self.assertTrue(first.try_acquire())
        self.assertFalse(second.try_acquire())
        first.release()
        self.assertTrue(second.try_acquire())
        second.release()

    def test_acquire_waits_then_raises_busy_with_the_age(self):
        slept = []
        clock = [1000.0]
        holder = self.lock()
        holder.try_acquire()
        os.utime(self.path, (900.0, 900.0))
        waiter = DirLock(self.path, stale=60.0, update=None, clock=lambda: clock[0],
                         sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
        with self.assertRaises(LockBusy) as raised:
            waiter.acquire(3.0, poll=(0.5, 0.5))
        self.assertGreaterEqual(len(slept), 5)
        self.assertGreater(raised.exception.age, 60.0)
        holder.release()

    def test_a_waiter_gets_the_lock_when_the_holder_lets_go(self):
        holder = self.lock()
        holder.try_acquire()
        waiter = self.lock()
        threading.Timer(0.15, holder.release).start()
        started = time.monotonic()
        waiter.acquire(5.0, poll=(0.02, 0.05))
        self.assertGreaterEqual(time.monotonic() - started, 0.1)
        waiter.release()

    def test_a_stale_lock_we_did_not_create_is_never_broken(self):
        os.mkdir(self.path)
        os.utime(self.path, (1.0, 1.0))  # older than any stale limit
        lock = self.lock(stale=60.0)
        with self.assertRaises(LockBusy):
            lock.acquire(0.05, poll=(0.01, 0.01))
        self.assertTrue(os.path.isdir(self.path), "the foreign lock is still there")
        self.assertFalse(lock.held)

    def test_release_leaves_a_lock_that_was_replaced(self):
        lock = self.lock()
        lock.try_acquire()
        os.rmdir(self.path)
        os.mkdir(self.path)  # someone else's, a new inode
        lock.release()
        self.assertTrue(os.path.isdir(self.path), "not ours any more: left alone")

    def test_the_touch_thread_keeps_the_mtime_fresh(self):
        lock = self.lock(update=0.05)
        lock.try_acquire()
        before = os.stat(self.path).st_mtime
        time.sleep(0.4)
        # (Not set back to an old time by hand: macOS then rewrites the birth time too, which the
        # ownership check rightly reads as a different directory.)
        self.assertGreater(os.stat(self.path).st_mtime, before)
        self.assertTrue(lock.held)
        lock.check()
        lock.release()

    def test_check_enforces_the_hold_limit_and_detects_a_taken_lock(self):
        clock = [0.0]
        lock = DirLock(self.path, stale=60.0, update=None, clock=lambda: clock[0], max_hold=10.0)
        lock.try_acquire()
        lock.check()
        clock[0] = 11.0
        with self.assertRaises(LockLost):
            lock.check()
        lock.release()
        other = self.lock()
        other.try_acquire()
        os.rmdir(self.path)
        os.mkdir(self.path)
        with self.assertRaises(LockLost):
            other.check()
        self.assertTrue(other.compromised)

    def test_the_context_manager_releases(self):
        with self.lock() as lock:
            self.assertTrue(lock.try_acquire())
        self.assertFalse(os.path.exists(self.path))

    def test_birth_time_is_positive_milliseconds_where_known(self):
        os.mkdir(self.path)
        birth = birth_time_ms(self.path)
        if birth is not None:
            self.assertAlmostEqual(birth / 1000.0, time.time(), delta=60)
        self.assertIsNone(birth_time_ms(os.path.join(self.dir, "missing")))

    def test_owner_record_has_the_fields_claude_code_reads(self):
        record = owner_record(1_700_000_000_123.0, 1_700_000_000_456.0)
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["lockBirthtimeMs"], 1_700_000_000_123.0)
        self.assertEqual(record["legacyLockBirthtimeMs"], 1_700_000_000_456.0)
        self.assertIsInstance(record["pidDomain"], str)
        self.assertTrue(record["pidDomain"].startswith("linux:"))
        self.assertIn(record["pidSpace"].split("#")[0], record["pidSpace"])
        self.assertLessEqual(len(record["pidDomain"]), 512)
        self.assertLessEqual(len(record["pidSpace"]), 512)
        if "procStart" in record:
            self.assertLessEqual(len(record["procStart"]), 64)
        self.assertNotIn("legacyLockBirthtimeMs", owner_record(5.0))
        if os.path.exists("/proc/self/stat"):
            self.assertTrue(proc_start().isdigit())


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tmpdir(self)
        self.ledger_path = os.path.join(self.dir, "state", "held-locks.json")
        self.lock_path = os.path.join(self.dir, ".oauth_refresh.lock")
        self.ledger = LockLedger(self.ledger_path)

    def test_a_held_lock_is_listed_and_unlisted_on_release(self):
        lock = DirLock(self.lock_path, stale=60.0, update=None, ledger=self.ledger)
        lock.try_acquire()
        entry = read_json(self.ledger_path)[self.lock_path]
        self.assertEqual(entry["pid"], os.getpid())
        lock.release()
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_reap_removes_only_our_own_dead_leftover(self):
        os.mkdir(self.lock_path)
        os.utime(self.lock_path, (1.0, 1.0))
        dead_pid = 999_999_999 if os.name == "posix" else 1
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        with open(self.ledger_path, "w") as handle:
            json.dump({self.lock_path: {"pid": dead_pid, "birthtimeMs": birth_time_ms(self.lock_path)}}, handle)
        self.assertEqual(self.ledger.reap(LOG, {self.lock_path: 60.0}), 1)
        self.assertFalse(os.path.exists(self.lock_path))
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_reap_leaves_a_foreign_lock_that_is_not_in_the_ledger(self):
        os.mkdir(self.lock_path)
        os.utime(self.lock_path, (1.0, 1.0))
        self.assertEqual(self.ledger.reap(LOG, {self.lock_path: 60.0}), 0)
        self.assertTrue(os.path.isdir(self.lock_path))

    def test_reap_leaves_a_lock_whose_creator_is_alive_or_fresh(self):
        os.mkdir(self.lock_path)
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        with open(self.ledger_path, "w") as handle:
            json.dump({self.lock_path: {"pid": os.getpid(), "birthtimeMs": None}}, handle)
        os.utime(self.lock_path, (1.0, 1.0))
        self.assertEqual(self.ledger.reap(LOG, {}), 0, "our own live process")
        with open(self.ledger_path, "w") as handle:
            json.dump({self.lock_path: {"pid": 999_999_999, "birthtimeMs": None}}, handle)
        os.utime(self.lock_path, None)
        self.assertEqual(self.ledger.reap(LOG, {}), 0, "touched a moment ago: not stale")
        self.assertTrue(os.path.isdir(self.lock_path))

    def test_reap_ignores_a_ledger_entry_whose_lock_was_recreated_by_someone_else(self):
        os.mkdir(self.lock_path)
        os.utime(self.lock_path, (1.0, 1.0))
        birth = birth_time_ms(self.lock_path)
        if birth is None:
            self.skipTest("no birth time on this filesystem")
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        with open(self.ledger_path, "w") as handle:
            json.dump({self.lock_path: {"pid": 999_999_999, "birthtimeMs": birth - 60_000}}, handle)
        self.assertEqual(self.ledger.reap(LOG, {}), 0)
        self.assertTrue(os.path.isdir(self.lock_path))


if __name__ == "__main__":
    unittest.main()
