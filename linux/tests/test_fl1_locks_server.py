"""Lane FL1: lock ownership (birth time as well as inode), the leftover-lock message, the control
server's subscribe ordering and socket ownership, and the control API's notFound for a vanished account."""
from __future__ import annotations

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
        real = dirlock.birth_time_ms
        with mock.patch.object(dirlock, "birth_time_ms", lambda path: real(path) + 5000):   # recreated later
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
        real = dirlock.birth_time_ms
        with mock.patch.object(dirlock, "birth_time_ms", lambda path: real(path) + 5000):
            self.assertTrue(wait_until(lambda: lock.compromised))
        lock.release()

    def test_without_a_birth_time_the_mtime_we_last_set_is_the_proof(self):
        with mock.patch.object(dirlock, "birth_time_ms", lambda path: None):
            lock = self.lock()
            lock.try_acquire()
            lock.check()                                      # ours: mtime is the one we set
            os.utime(self.path, (1.0, 1.0))                   # someone else touched it
            with self.assertRaises(LockLost):
                lock.check()
            lock.release()
            self.assertTrue(os.path.isdir(self.path), "not removed: it is not provably ours")

    def test_without_a_birth_time_our_own_directory_is_released(self):
        with mock.patch.object(dirlock, "birth_time_ms", lambda path: None):
            lock = self.lock()
            lock.try_acquire()
            lock.release()
            self.assertFalse(os.path.exists(self.path))

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
