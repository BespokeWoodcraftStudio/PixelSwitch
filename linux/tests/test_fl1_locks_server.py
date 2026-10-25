"""Lane FL1: lock ownership (birth time as well as inode), the leftover-lock message, the control
server's subscribe ordering and socket ownership, and the control API's notFound for a vanished account."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import tempfile
import time
import unittest
from unittest import mock

from pixelswitch import dirlock, protocol
from pixelswitch.claude_login import ClaudeLogin, LEFTOVER_MESSAGE, LiveLoginBusy, RENEWING_MESSAGE
from pixelswitch.dirlock import DirLock, LockLedger, LockLost
from pixelswitch.logfile import Log
from pixelswitch.paths import Paths
from pixelswitch.server import ControlServer, StartError

from .lb_support import FakeClock, tmpdir
from .test_control_api import Api

LOG = Log(None).category("test")


def no_birth_time():
    """A filesystem that keeps no creation time (ext4 with 128-byte inodes, ext3, some network mounts)."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(dirlock, "birth_time_ns", lambda path: None))
    stack.enter_context(mock.patch.object(dirlock, "birth_time_ms", lambda path: None))
    return stack


class OwnershipTests(unittest.TestCase):
    """LB review, dirlock.py:314: ext4 reuses a freed inode at once, so the inode alone is not proof."""

    def setUp(self):
        self.dir = tmpdir(self)
        self.path = os.path.join(self.dir, "x.lock")

    def lock(self, **kw):
        return DirLock(self.path, stale=60.0, update=None, **kw)

    def test_a_directory_with_our_inode_but_another_birth_time_is_not_ours(self):
        lock = self.lock()
        lock.try_acquire()
        if lock.birth_ms is None:
            self.skipTest("no birth time on this filesystem")
        real = dirlock.birth_time_ns
        with mock.patch.object(dirlock, "birth_time_ns", lambda path: real(path) + 5_000_000_000):   # recreated later
            with self.assertRaises(LockLost):
                lock.check()
            lock.release()
        self.assertTrue(os.path.isdir(self.path), "release leaves a lock it did not create")

    def test_the_touch_loop_stops_touching_a_directory_that_is_not_ours(self):
        lock = self.lock(clock=time.time)
        lock.update = 0.05
        lock.try_acquire()
        if lock.birth_ms is None:
            lock.release()
            self.skipTest("no birth time on this filesystem")
        real = dirlock.birth_time_ns
        with mock.patch.object(dirlock, "birth_time_ns", lambda path: real(path) + 5_000_000_000):
            self.assertTrue(wait_until(lambda: lock.compromised))
        lock.release()

    def test_without_a_birth_time_the_mtime_we_last_set_is_the_proof(self):
        with no_birth_time():
            lock = self.lock()
            lock.try_acquire()
            lock.check()                                      # ours: mtime is the one we set
            os.utime(self.path, (1.0, 1.0))                   # someone else touched it
            with self.assertRaises(LockLost):
                lock.check()
            lock.release()
            self.assertTrue(os.path.isdir(self.path), "not removed: it is not provably ours")

    def test_without_a_birth_time_our_own_directory_is_released(self):
        with no_birth_time():
            lock = self.lock()
            lock.try_acquire()
            lock.release()
            self.assertFalse(os.path.exists(self.path))

    def test_a_birth_time_one_millisecond_away_with_the_same_inode_is_not_ours(self):
        """CI run 40162811377 (Ubuntu 24.04): a directory removed and re-made within 2 ms kept the old inode
        and passed the 2 ms tolerance. Ownership is exact now, so a millisecond (or a nanosecond) off is foreign."""
        for offset in (1_000_000, 1, -1):
            with self.subTest(offset_ns=offset):
                lock = self.lock()
                lock.try_acquire()
                if lock.birth_ms is None:
                    lock.release()
                    self.skipTest("no birth time on this filesystem")
                real = dirlock.birth_time_ns
                inode = os.lstat(self.path).st_ino
                with mock.patch.object(dirlock, "birth_time_ns", lambda path, real=real, offset=offset: real(path) + offset):
                    self.assertEqual(os.lstat(self.path).st_ino, inode, "the same inode number")
                    with self.assertRaises(LockLost):
                        lock.check()
                    self.assertTrue(lock.compromised)
                    lock.release()
                self.assertTrue(os.path.isdir(self.path), "release left the lock it did not create")
                os.rmdir(self.path)

    def test_a_remake_in_the_same_clock_tick_is_not_ours_even_with_the_same_inode_and_birth_time(self):
        """Older kernels (Ubuntu 24.04's 6.8) stamp a new directory's creation time with a coarse clock, so a
        directory re-made a millisecond after ours can carry the very same birth time as well as the same
        inode. What differs is the mtime: ours carries a random sub-millisecond part."""
        lock = self.lock()
        lock.try_acquire()
        os.rmdir(self.path)
        os.mkdir(self.path)                                    # someone else's
        lock._held.inode = os.lstat(self.path).st_ino          # the freed inode number was handed out again
        if lock._held.birth_ns is None:
            lock._held.birth_ns = 1_700_000_000_123_456_789    # and the creation time came out the same
        same_birth = lock._held.birth_ns
        with mock.patch.object(dirlock, "birth_time_ns", lambda path: same_birth):
            with self.assertRaises(LockLost):
                lock.check()
            lock.release()
        self.assertTrue(os.path.isdir(self.path), "not removed: it is not ours")

    def test_without_a_birth_time_an_mtime_one_millisecond_away_is_not_ours(self):
        with no_birth_time():
            lock = self.lock()
            lock.try_acquire()
            lock.check()
            ours = os.lstat(self.path).st_mtime_ns
            os.utime(self.path, ns=(ours + 1_000_000, ours + 1_000_000))   # same inode, 1 ms later
            with self.assertRaises(LockLost):
                lock.check()
            lock.release()
            self.assertTrue(os.path.isdir(self.path), "not provably ours: left alone")

    def test_the_mtime_pixelswitch_sets_carries_a_random_sub_millisecond_part(self):
        seen = set()
        for index in range(20):
            path = os.path.join(self.dir, f"m{index}")
            os.mkdir(path)
            status, asked, exact = dirlock._stamp(path, 1_800_000_000.0)
            self.assertEqual(asked // 1_000_000, 1_800_000_000_000)
            self.assertGreater(asked % 1_000_000, 0)
            seen.add(asked % 1_000_000)
            if exact:
                self.assertEqual(status.st_mtime_ns, asked)
        self.assertGreater(len(seen), 1, "not a constant")

    def test_a_touch_never_lands_on_a_directory_that_is_not_the_one_we_checked(self):
        os.mkdir(self.path)
        os.utime(self.path, ns=(5_000_000_000, 5_000_000_000))
        self.assertIsNone(dirlock._stamp(self.path, time.time(), os.lstat(self.path).st_ino + 1))
        self.assertEqual(os.lstat(self.path).st_mtime_ns, 5_000_000_000, "a foreign directory is not touched")
        self.assertIsNone(dirlock._stamp(os.path.join(self.dir, "missing"), time.time()))

    def test_without_a_birth_time_the_touch_loop_keeps_our_lock_ours(self):
        with no_birth_time():
            lock = self.lock(clock=time.time)
            lock.update = 0.03
            lock.try_acquire()
            first = os.lstat(self.path).st_mtime_ns
            self.assertTrue(wait_until(lambda: os.lstat(self.path).st_mtime_ns != first))
            for _ in range(20):
                lock.check()
                time.sleep(0.01)
            self.assertFalse(lock.compromised)
            lock.release()
            self.assertFalse(os.path.exists(self.path), "our own lock is removed")

    def test_a_whole_second_creation_time_is_not_taken_as_proof(self):
        whole = 1_700_000_000 * 10**9
        with mock.patch.object(dirlock, "birth_time_ns", lambda path: whole):
            lock = self.lock()
            lock.try_acquire()
            lock.check()
            os.utime(self.path, ns=(7_000_000_001, 7_000_000_001))
            with self.assertRaises(LockLost):
                lock.check()   # the mtime decides, since the creation time cannot tell directories apart
            lock.release()
            self.assertTrue(os.path.isdir(self.path))

    def test_a_filesystem_with_whole_second_times_judges_at_its_granularity_and_logs_once(self):
        real = os.utime

        def whole_seconds(target, times=None, *, ns=None, **kw):
            if ns is None:
                return real(target, times, **kw)
            return real(target, ns=(ns[0] // 10**9 * 10**9, ns[1] // 10**9 * 10**9), **kw)

        class Capture:
            def __init__(self):
                self.lines = []

            def warning(self, text):
                self.lines.append(text)

        log = Capture()
        before = dirlock._COARSE_LOGGED
        dirlock._COARSE_LOGGED = False
        try:
            with no_birth_time(), mock.patch.object(os, "utime", whole_seconds):
                for _ in range(2):
                    lock = self.lock(log=log)
                    self.assertTrue(lock.try_acquire())
                    lock.check()                                # our own lock is never misjudged as foreign
                    lock.release()
                    self.assertFalse(os.path.exists(self.path), "and is removed on release")
                self.assertEqual(len([line for line in log.lines if "coarser" in line]), 1, "logged once")
                lock = self.lock(log=log)
                lock.try_acquire()
                whole_seconds(self.path, ns=(9 * 10**9, 9 * 10**9))   # someone else's, another second
                with self.assertRaises(LockLost):
                    lock.check()
                lock.release()
                self.assertTrue(os.path.isdir(self.path), "a foreign one is never removed")
        finally:
            dirlock._COARSE_LOGGED = before

    def test_a_lock_whose_mtime_cannot_be_read_back_is_never_removed(self):
        lock = self.lock()
        with no_birth_time():
            lock.try_acquire()
            lock._held.mtime_ns = None
            with self.assertRaises(LockLost):
                lock.check()
            lock.release()
        self.assertTrue(os.path.isdir(self.path))

    def test_reap_compares_the_birth_time_to_the_nanosecond(self):
        ledger_path = os.path.join(self.dir, "held-locks.json")
        ledger = LockLedger(ledger_path)
        os.mkdir(self.path)
        os.utime(self.path, (1.0, 1.0))
        birth = dirlock.birth_time_ns(self.path)
        if birth is None:
            self.skipTest("no birth time on this filesystem")
        for recorded, removed in ((birth + 1_000_000, 0), (birth - 1, 0), (birth, 1)):
            with open(ledger_path, "w") as handle:
                json.dump({self.path: {"pid": 999_999_999, "birthtimeMs": birth / 1e6, "birthtimeNs": recorded}}, handle)
            self.assertEqual(ledger.reap(LOG, {self.path: 60.0}), removed, recorded - birth)
            if removed == 0:
                self.assertTrue(os.path.isdir(self.path))

    def test_reap_needs_both_birth_times(self):
        ledger_path = os.path.join(self.dir, "held-locks.json")
        ledger = LockLedger(ledger_path)
        os.mkdir(self.path)
        os.utime(self.path, (1.0, 1.0))
        dead = 999_999_999
        for recorded in (None,):
            with open(ledger_path, "w") as handle:
                json.dump({self.path: {"pid": dead, "birthtimeMs": recorded}}, handle)
            self.assertEqual(ledger.reap(LOG, {self.path: 60.0}), 0, "an unknown recorded birth time is not a match")
            self.assertTrue(os.path.isdir(self.path))
        birth = dirlock.birth_time_ms(self.path)
        if birth is None:
            self.skipTest("no birth time on this filesystem")
        with open(ledger_path, "w") as handle:
            json.dump({self.path: {"pid": dead, "birthtimeMs": birth}}, handle)
        with mock.patch.object(dirlock, "birth_time_ms", lambda path: None):
            self.assertEqual(ledger.reap(LOG, {self.path: 60.0}), 0, "an unknown current birth time is not a match")
        self.assertEqual(ledger.reap(LOG, {self.path: 60.0}), 1, "both known and equal: removed")

    def test_remaining_reports_the_hold_left(self):
        clock = [0.0]
        lock = DirLock(self.path, stale=60.0, update=None, clock=lambda: clock[0], max_hold=10.0)
        self.assertIsNone(lock.remaining())
        lock.try_acquire()
        clock[0] = 4.0
        self.assertAlmostEqual(lock.remaining(), 6.0)
        clock[0] = 99.0
        self.assertEqual(lock.remaining(), 0.0)
        lock.release()
        self.assertIsNone(self.lock().remaining())


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class LeftoverMessageTests(unittest.TestCase):
    """LB review, claude_login.py:195."""

    def setUp(self):
        self.home = tmpdir(self)
        self.paths = Paths(self.home, None)
        self.clock = FakeClock()
        self.login = ClaudeLogin(self.paths, LOG, clock=self.clock, sleep=self.clock.sleep)
        os.makedirs(self.paths.claude_dir, mode=0o700)

    def test_a_stuck_legacy_lock_is_named_not_the_one_just_released(self):
        legacy = self.login.legacy_lock_path
        os.mkdir(legacy)
        os.utime(legacy, (self.clock.now - 120, self.clock.now - 120))
        with self.assertRaises(LiveLoginBusy) as raised:
            with self.login.refresh_lock(timeout=1.0):
                pass
        message = raised.exception.message
        self.assertIn("~/.claude.lock", message)
        self.assertNotIn(".oauth_refresh.lock", message)
        self.assertFalse(os.path.exists(self.login.refresh_lock_path), "the new lock was let go")

    def test_a_stuck_new_lock_keeps_its_message(self):
        os.mkdir(self.login.refresh_lock_path)
        os.utime(self.login.refresh_lock_path, (self.clock.now - 120, self.clock.now - 120))
        with self.assertRaises(LiveLoginBusy) as raised:
            with self.login.refresh_lock(timeout=1.0):
                pass
        self.assertEqual(raised.exception.message, LEFTOVER_MESSAGE)
        self.assertIn("~/.claude/.oauth_refresh.lock", LEFTOVER_MESSAGE)

    def test_a_young_legacy_lock_is_just_renewing(self):
        os.mkdir(self.login.legacy_lock_path)
        with self.assertRaises(LiveLoginBusy) as raised:
            with self.login.refresh_lock(timeout=1.0):
                pass
        self.assertEqual(raised.exception.message, RENEWING_MESSAGE)


class SubscribeOrderTests(unittest.TestCase):
    """LB review, control_api.py:152: the reply to events.subscribe is the first line on the wire."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ps-", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "run", "control.sock")

    def test_no_event_is_written_ahead_of_the_subscribe_reply(self):
        server_box = []

        def handler(line, connection):
            request = json.loads(line)
            connection.mark_subscribed()
            for index in range(5):                            # events keep being emitted during the handler
                server_box[0].publish("usageUpdated", {})
                time.sleep(0.02)
            return protocol.success_line(request["id"], {"subscribed": True})

        server = ControlServer(self.path, handler)
        server_box.append(server)
        server.start()
        self.addCleanup(server.stop)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(self.path)
        self.addCleanup(sock.close)
        sock.sendall((protocol.request_line(1, "events.subscribe") + "\n").encode())
        buffer = b""
        deadline = time.monotonic() + 3
        while buffer.count(b"\n") < 1 and time.monotonic() < deadline:
            buffer += sock.recv(65536)
        first = json.loads(buffer.decode().splitlines()[0])
        self.assertEqual(first.get("result"), {"subscribed": True}, "the reply, not an event, comes first")
        # and the connection IS a subscriber afterwards
        server.publish("usageUpdated", {})
        deadline = time.monotonic() + 3
        while buffer.count(b"\n") < 2 and time.monotonic() < deadline:
            buffer += sock.recv(65536)
        second = json.loads(buffer.decode().splitlines()[1])
        self.assertEqual(second["method"], "event")


class SocketOwnershipTests(unittest.TestCase):
    """LB review, server.py:230."""

    def test_a_server_refused_as_already_running_does_not_delete_the_socket_of_the_one_answering(self):
        directory = tempfile.mkdtemp(prefix="ps-", dir="/tmp")
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "run", "control.sock")
        first = ControlServer(path, lambda line, conn: None)
        first.start()
        self.addCleanup(first.stop)
        second = ControlServer(path, lambda line, conn: None)
        with self.assertRaises(StartError) as raised:
            second.start()
        self.assertTrue(raised.exception.already_running)
        second.stop()
        self.assertTrue(os.path.exists(path), "the live daemon's socket is still there")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(2)
        probe.connect(path)
        probe.close()

    def test_the_server_that_bound_it_removes_it(self):
        directory = tempfile.mkdtemp(prefix="ps-", dir="/tmp")
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "run", "control.sock")
        server = ControlServer(path, lambda line, conn: None)
        server.start()
        self.assertTrue(os.path.exists(path))
        server.stop()
        self.assertFalse(os.path.exists(path))

    def test_a_socket_someone_else_put_there_since_is_left_alone(self):
        directory = tempfile.mkdtemp(prefix="ps-", dir="/tmp")
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "run", "control.sock")
        server = ControlServer(path, lambda line, conn: None)
        server.start()
        os.unlink(path)
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        replacement.bind(path)
        self.addCleanup(replacement.close)
        server.stop()
        self.assertTrue(os.path.exists(path))


class VanishedAccountTests(unittest.TestCase):
    """LB review, control_api.py:119: notFound, as the Mac answers (ControlAPI.swift:183-185, 207-209)."""

    def test_thresholds_and_priority_and_label_answer_not_found_when_the_account_is_gone(self):
        t = Api(self)
        for name in ("set_label", "set_threshold", "set_thresholds", "set_priority"):
            setattr(t.app, name, lambda *a, **k: False)
        calls = [("accounts.setLabel", {"account": "1", "label": "x"}),
                 ("accounts.setThreshold", {"account": "1", "threshold": 80}),
                 ("accounts.setThresholds", {"account": "1", "session": 80}),
                 ("accounts.setPriority", {"account": "1", "priority": 3})]
        for method, params in calls:
            with self.subTest(method):
                error = t.error(method, params)
                self.assertEqual(error["data"]["kind"], "notFound")
                self.assertEqual(error["message"], "That account no longer exists.")

    def test_a_valid_priority_for_a_real_account_still_works_and_an_invalid_one_is_still_invalid(self):
        t = Api(self)
        self.assertIn("accounts", t.result("accounts.setPriority", {"account": "1", "priority": 3}))
        error = t.error("accounts.setPriority", {"account": "1", "priority": 100})
        self.assertEqual(error["data"]["kind"], "invalidValue")


if __name__ == "__main__":
    unittest.main()
